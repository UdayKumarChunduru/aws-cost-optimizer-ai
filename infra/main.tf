terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

resource "aws_dynamodb_table" "findings" {
  name         = "cost-optimizer-findings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "resource_id"

  attribute {
    name = "run_id"
    type = "S"
  }

  attribute {
    name = "resource_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Project = "cost-optimizer" }
}

resource "aws_sns_topic" "alerts" {
  name = "cost-optimizer-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_iam_role" "lambda_role" {
  name = "cost-optimizer-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Permissions cover all 10 scanners + region discovery + pricing cache.
# Every action here maps to a specific scanner file - see the comment
# above each block. Read-only everywhere except ec2:StopInstances,
# which only the idle_shutdown Lambda's logic path actually invokes.
resource "aws_iam_role_policy" "permissions" {
  name = "cost-optimizer-permissions"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # region discovery (app/regions/discovery.py) +
        # ec2_idle, ebs_unattached, ebs_snapshots, elastic_ips, nat_gateways scanners
        Effect = "Allow"
        Action = [
          "ec2:DescribeRegions",
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:DescribeSnapshots",
          "ec2:DescribeAddresses",
          "ec2:DescribeNatGateways",
          "ec2:StopInstances",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:PutMetricData",
        ]
        Resource = "*"
      },
      {
        # region discovery fallback (resourcegroupstaggingapi probe)
        Effect   = "Allow"
        Action   = ["tag:GetResources"]
        Resource = "*"
      },
      {
        # region discovery primary path (Cost Explorer)
        Effect   = "Allow"
        Action   = ["ce:GetCostAndUsage"]
        Resource = "*"
      },
      {
        # load_balancers scanner
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
        ]
        Resource = "*"
      },
      {
        # rds_idle scanner
        Effect   = "Allow"
        Action   = ["rds:DescribeDBInstances"]
        Resource = "*"
      },
      {
        # cloudwatch_logs scanner
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        # secrets_manager scanner
        Effect   = "Allow"
        Action   = ["secretsmanager:ListSecrets"]
        Resource = "*"
      },
      {
        # ecr_images scanner
        Effect = "Allow"
        Action = [
          "ecr:DescribeRepositories",
          "ecr:DescribeImages",
        ]
        Resource = "*"
      },
      {
        # app/pricing/aws_pricing.py - Pricing API only has endpoints
        # in us-east-1/ap-south-1 but this action has no region-scoped
        # resource ARN format, so Resource must be "*"
        Effect   = "Allow"
        Action   = ["pricing:GetProducts"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.findings.arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
      },
    ]
  })
}

data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/placeholder.zip"
  source {
    content  = "def handler(e,c): return {'status':'placeholder'}"
    filename = "idle_shutdown.py"
  }
}

resource "aws_lambda_function" "idle_shutdown" {
  function_name = "cost-optimizer-idle-shutdown"
  role          = aws_iam_role.lambda_role.arn
  handler       = "idle_shutdown.handler"
  runtime       = "python3.12"
  timeout       = 120
  memory_size   = 256

  filename = data.archive_file.placeholder.output_path

  environment {
    variables = {
      LAMBDA_REGION          = var.aws_region
      SCAN_REGIONS           = var.scan_regions
      SNS_TOPIC_ARN          = aws_sns_topic.alerts.arn
      IDLE_CPU_THRESHOLD     = var.idle_cpu_threshold
      IDLE_NETWORK_THRESHOLD = var.idle_network_threshold
      IDLE_LOOKBACK_DAYS     = var.idle_lookback_days
    }
  }

  tags = { Project = "cost-optimizer" }
}

resource "aws_lambda_function" "weekly_report" {
  function_name = "cost-optimizer-weekly-report"
  role          = aws_iam_role.lambda_role.arn
  handler       = "weekly_report.handler"
  runtime       = "python3.12"

  # Parallel scanning keeps this well under the limit even with 10
  # scanners x 15+ regions - see app/scanners/orchestrator.py. Memory
  # is raised from the original 256MB because more memory also
  # increases the CPU share Lambda allocates, which the thread pool
  # benefits from directly.
  timeout     = var.weekly_report_timeout_seconds
  memory_size = var.weekly_report_memory_mb

  filename = data.archive_file.placeholder.output_path

  environment {
    variables = {
      LAMBDA_REGION          = var.aws_region
      SCAN_REGIONS           = var.scan_regions
      ALERT_EMAIL            = var.alert_email
      SNS_TOPIC_ARN          = aws_sns_topic.alerts.arn
      DYNAMODB_TABLE         = aws_dynamodb_table.findings.name
      BEDROCK_MODEL_ID       = var.bedrock_model_id
      IDLE_CPU_THRESHOLD     = var.idle_cpu_threshold
      IDLE_NETWORK_THRESHOLD = var.idle_network_threshold
      IDLE_LOOKBACK_DAYS     = var.idle_lookback_days
      SNAPSHOT_MAX_AGE_DAYS  = var.snapshot_max_age_days
    }
  }

  tags = { Project = "cost-optimizer" }
}

resource "aws_cloudwatch_event_rule" "nightly" {
  name                = "cost-optimizer-nightly"
  schedule_expression = "cron(0 20 * * ? *)"
  description         = "Triggers idle shutdown Lambda every night at 8 PM UTC"
}

resource "aws_cloudwatch_event_target" "shutdown_target" {
  rule      = aws_cloudwatch_event_rule.nightly.name
  target_id = "IdleShutdownLambda"
  arn       = aws_lambda_function.idle_shutdown.arn
}

resource "aws_lambda_permission" "allow_nightly" {
  statement_id  = "AllowEventBridgeNightly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.idle_shutdown.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.nightly.arn
}

resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "cost-optimizer-weekly"
  schedule_expression = "cron(0 6 ? * MON *)"
  description         = "Triggers weekly cost report Lambda every Monday at 6 AM UTC"
}

resource "aws_cloudwatch_event_target" "report_target" {
  rule      = aws_cloudwatch_event_rule.weekly.name
  target_id = "WeeklyReportLambda"
  arn       = aws_lambda_function.weekly_report.arn
}

resource "aws_lambda_permission" "allow_weekly" {
  statement_id  = "AllowEventBridgeWeekly"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.weekly_report.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly.arn
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "CostOptimizerDashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"; x = 0; y = 0; width = 8; height = 6
        properties = {
          title   = "Weekly Findings Count"
          metrics = [["CostOptimizer", "WeeklyFindingsCount", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region; yAxis = { left = { min = 0 } }
        }
      },
      {
        type = "metric"; x = 8; y = 0; width = 8; height = 6
        properties = {
          title   = "Estimated Monthly Waste (USD)"
          metrics = [["CostOptimizer", "EstimatedMonthlyWasteUSD", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region; yAxis = { left = { min = 0 } }
        }
      },
      {
        type = "metric"; x = 16; y = 0; width = 8; height = 6
        properties = {
          title   = "High Severity Findings (>= $50/mo)"
          metrics = [["CostOptimizer", "HighSeverityFindings", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region; yAxis = { left = { min = 0 } }
        }
      },
      {
        type = "metric"; x = 0; y = 6; width = 8; height = 6
        properties = {
          title   = "Regions Scanned"
          metrics = [["CostOptimizer", "RegionsScanned", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region
        }
      },
      {
        type = "metric"; x = 8; y = 6; width = 8; height = 6
        properties = {
          title   = "Scanners Run"
          metrics = [["CostOptimizer", "ScannersRun", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region
        }
      },
      {
        type = "metric"; x = 16; y = 6; width = 8; height = 6
        properties = {
          title   = "Scanner Errors (failed scanner/region pairs)"
          metrics = [["CostOptimizer", "ScannerErrors", "Region", var.aws_region]]
          view    = "timeSeries"; stat = "Maximum"; period = 604800
          region  = var.aws_region
        }
      },
    ]
  })
}
