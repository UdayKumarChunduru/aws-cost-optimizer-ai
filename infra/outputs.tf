output "lambda_shutdown_name" {
  value = aws_lambda_function.idle_shutdown.function_name
}

output "lambda_report_name" {
  value = aws_lambda_function.weekly_report.function_name
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "dynamodb_table" {
  value = aws_dynamodb_table.findings.name
}

output "cloudwatch_dashboard" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=CostOptimizerDashboard"
}
