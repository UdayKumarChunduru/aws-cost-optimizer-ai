"""
EBS Unattached Volume Scanner
"""
import boto3
import botocore

from app.pricing.aws_pricing import get_pricing_cache

SCANNER_NAME = "ebs_unattached"
SCANNER_LABEL = "EBS Unattached Volumes"


def scan_region(region: str) -> list[dict]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cache = get_pricing_cache()
    findings = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate(
        Filters=[{"Name": "status", "Values": ["available"]}]
    ):
        for vol in page["Volumes"]:
            size = vol["Size"]
            vtype = vol.get("VolumeType", "gp2")
            price_per_gb = cache.get_ebs_price_per_gb(vtype)
            findings.append({
                "resource_type": "ebs_volume",
                "resource_id": vol["VolumeId"],
                "region": region,
                "reason": f"Unattached {vtype} volume - paying for unused storage",
                "estimated_monthly_cost_usd": round(size * price_per_gb, 2),
                "details": {
                    "size_gb": size,
                    "volume_type": vtype,
                    "created": str(vol.get("CreateTime", "")),
                },
            })
    return findings
