variable "aws_region" {
  description = "Primary AWS region where Lambda functions and supporting infra are deployed"
  default     = "us-east-1"
}

variable "alert_email" {
  description = "Email address for SNS alerts and SES weekly reports — never commit a real email to source"
  type        = string
}

variable "scan_regions" {
  description = "Comma-separated regions to scan, or ALL for automatic active-region discovery"
  type        = string
  default     = "ALL"
}

variable "idle_cpu_threshold" {
  description = "CPU percent below which an EC2 instance is considered idle"
  type        = string
  default     = "5.0"
}

variable "idle_network_threshold" {
  description = "Network bytes (in+out) below which an EC2 instance is considered idle"
  type        = string
  default     = "5000000"
}

variable "idle_lookback_days" {
  description = "Number of days of CloudWatch history analysed for idle detection"
  type        = string
  default     = "7"
}

variable "snapshot_max_age_days" {
  description = "EBS snapshots older than this many days are flagged"
  type        = string
  default     = "90"
}

variable "bedrock_model_id" {
  description = "Bedrock model ID for AI recommendations — must be enabled in Bedrock console"
  type        = string
  default     = "anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "weekly_report_timeout_seconds" {
  description = "Lambda timeout for the weekly report function. Parallel scanning keeps this well under typical AWS Lambda max (900s), but raise it if you add many more scanners."
  type        = number
  default     = 300
}

variable "weekly_report_memory_mb" {
  description = "Memory for the weekly report Lambda. Higher memory also increases CPU allocation, which helps with the thread pool used for parallel scanning."
  type        = number
  default     = 512
}
