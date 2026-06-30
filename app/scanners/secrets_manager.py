"""
Unused Secrets Manager Secret Scanner
Each secret costs $0.40/month regardless of use. Secrets nobody has
read in 90+ days are very likely abandoned from a decommissioned
service, not actively-used credentials.
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings

SCANNER_NAME = "secrets_manager"
SCANNER_LABEL = "Unused Secrets Manager Secrets"

SECRET_MONTHLY_PRICE = 0.40


def scan_region(region: str) -> list[dict]:
    try:
        client = boto3.client("secretsmanager", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.secrets_unused_days)
    findings = []

    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for secret in page["SecretList"]:
            last_accessed = secret.get("LastAccessedDate")
            created = secret.get("CreatedDate")

            # If never accessed, use creation date as the reference point.
            reference_date = last_accessed or created
            if reference_date is None or reference_date > cutoff:
                continue

            days_unused = (datetime.now(timezone.utc) - reference_date).days
            findings.append({
                "resource_type": "secrets_manager_secret",
                "resource_id": secret["Name"],
                "region": region,
                "reason": (
                    f"Not accessed in {days_unused} days — "
                    f"likely belongs to a decommissioned service"
                ),
                "estimated_monthly_cost_usd": SECRET_MONTHLY_PRICE,
                "details": {
                    "last_accessed": str(last_accessed) if last_accessed else "never",
                    "days_unused": days_unused,
                },
            })
    return findings
