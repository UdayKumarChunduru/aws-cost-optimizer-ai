from datetime import datetime, timedelta, timezone

import boto3

from app.config import settings
from app.models import Finding

SNAPSHOT_PER_GB_MONTH = 0.05


def scan() -> list[Finding]:
    ec2 = boto3.client("ec2", region_name=settings.aws_region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.snapshot_max_age_days)

    findings = []
    paginator = ec2.get_paginator("describe_snapshots")
    for page in paginator.paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            start_time = snap["StartTime"]
            if start_time > cutoff:
                continue
            age_days = (datetime.now(timezone.utc) - start_time).days
            size = snap.get("VolumeSize", 0)
            findings.append(Finding(
                resource_type="ebs_snapshot",
                resource_id=snap["SnapshotId"],
                region=settings.aws_region,
                reason=f"Snapshot is {age_days} days old, past the {settings.snapshot_max_age_days} day cutoff",
                estimated_monthly_cost_usd=round(size * SNAPSHOT_PER_GB_MONTH, 2),
                details={
                    "volume_size_gb": size,
                    "started": str(start_time),
                    "description": snap.get("Description", ""),
                },
            ))
    return findings
