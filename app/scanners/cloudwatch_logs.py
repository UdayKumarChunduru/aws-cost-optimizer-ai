"""
CloudWatch Log Group Retention Scanner
Log groups with NO retention policy keep data forever, and CloudWatch
Logs storage cost grows silently - there's no alert when this happens,
it just shows up as a slowly climbing line item months later.
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings

SCANNER_NAME = "cloudwatch_logs"
SCANNER_LABEL = "CloudWatch Logs Without Retention"

# CloudWatch Logs storage price per GB-month (ingestion is billed
# separately and isn't something this scanner can detect after the fact)
STORAGE_PRICE_PER_GB_MONTH = 0.03


def scan_region(region: str) -> list[dict]:
    try:
        logs = boto3.client("logs", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.cloudwatch_log_no_retention_min_age_days
    )
    findings = []

    paginator = logs.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for group in page["logGroups"]:
            # retentionInDays is simply absent from the dict when
            # retention is set to "Never expire" - that's the signal.
            if "retentionInDays" in group:
                continue

            created_ms = group.get("creationTime", 0)
            created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if created > cutoff:
                # Skip very recently created groups - give engineers
                # time to set retention before flagging them.
                continue

            size_bytes = group.get("storedBytes", 0)
            size_gb = size_bytes / (1024 ** 3)
            age_days = (datetime.now(timezone.utc) - created).days

            findings.append({
                "resource_type": "cloudwatch_log_group",
                "resource_id": group["logGroupName"],
                "region": region,
                "reason": (
                    f"No retention policy set ({age_days} days old) — "
                    f"logs accumulate forever and never get cleaned up"
                ),
                "estimated_monthly_cost_usd": round(size_gb * STORAGE_PRICE_PER_GB_MONTH, 2),
                "details": {
                    "size_gb": round(size_gb, 3),
                    "age_days": age_days,
                },
            })
    return findings
