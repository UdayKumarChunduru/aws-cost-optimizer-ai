import boto3

from app.config import settings
from app.models import Finding

GP3_PER_GB_MONTH = 0.08


def scan() -> list[Finding]:
    ec2 = boto3.client("ec2", region_name=settings.aws_region)

    findings = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate(
        Filters=[{"Name": "status", "Values": ["available"]}]
    ):
        for vol in page["Volumes"]:
            size = vol["Size"]
            findings.append(Finding(
                resource_type="ebs_volume",
                resource_id=vol["VolumeId"],
                region=settings.aws_region,
                reason="Volume is in the available state, not attached to any instance",
                estimated_monthly_cost_usd=round(size * GP3_PER_GB_MONTH, 2),
                details={
                    "size_gb": size,
                    "volume_type": vol.get("VolumeType", ""),
                    "created": str(vol.get("CreateTime", "")),
                },
            ))
    return findings
