"""
EBS Snapshot Age Scanner
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings

SCANNER_NAME = "ebs_snapshots"
SCANNER_LABEL = "EBS Old Snapshots"

SNAPSHOT_PER_GB_MONTH = 0.05


def scan_region(region: str) -> list[dict]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.snapshot_max_age_days)
    findings = []
    paginator = ec2.get_paginator("describe_snapshots")
    for page in paginator.paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            if snap["StartTime"] > cutoff:
                continue
            age_days = (datetime.now(timezone.utc) - snap["StartTime"]).days
            size = snap.get("VolumeSize", 0)
            findings.append({
                "resource_type": "ebs_snapshot",
                "resource_id": snap["SnapshotId"],
                "region": region,
                "reason": (
                    f"Snapshot is {age_days} days old — "
                    f"past the {settings.snapshot_max_age_days}-day retention policy"
                ),
                "estimated_monthly_cost_usd": round(size * SNAPSHOT_PER_GB_MONTH, 2),
                "details": {
                    "volume_size_gb": size,
                    "age_days": age_days,
                    "description": snap.get("Description", ""),
                },
            })
    return findings
