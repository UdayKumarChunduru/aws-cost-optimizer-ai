from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "ALL" triggers automatic active-region discovery via Cost Explorer
    # (falling back to a resource probe). Comma-separated list scans
    # exactly those regions with zero discovery overhead.
    aws_regions: str = "ALL"

    # Scanner thresholds - every number a scanner uses to decide
    # "is this idle" lives here, never inline in scanner code.
    idle_cpu_threshold: float = 5.0
    idle_network_threshold: int = 5_000_000
    idle_lookback_days: int = 7
    snapshot_max_age_days: int = 90
    nat_gateway_bytes_threshold: int = 1_000_000
    rds_connection_lookback_days: int = 7
    cloudwatch_log_no_retention_min_age_days: int = 30
    secrets_unused_days: int = 90
    ecr_untagged_age_days: int = 30

    # Ollama - local FastAPI development only, never used by Lambda
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"

    # Alerting - must be set per-deployment, never hardcoded in source
    alert_email: str = ""
    sns_topic_arn: str = ""

    # Bedrock - model ID is fully configurable, code never assumes a
    # specific model exists
    bedrock_model_id: str = "anthropic.claude-haiku-4-5-20251001-v1:0"

    # DynamoDB
    dynamodb_table: str = "cost-optimizer-findings"

    # Concurrency control for multi-region scanning — bounds total
    # thread count so a sequential region-by-region, scanner-by-scanner
    # scan never risks running long enough to hit the Lambda timeout.
    # Tune down on accounts with many regions if Lambda memory becomes a constraint.
    max_parallel_regions: int = 10
    max_parallel_scanners_per_region: int = 5

    # Per-scanner timeout in seconds. If a single scanner call to a
    # single region hangs (e.g. AWS API degradation), it gets abandoned
    # rather than blocking the whole Lambda toward its 300s limit.
    scanner_timeout_seconds: int = 25

    class Config:
        env_file = ".env"


settings = Settings()
