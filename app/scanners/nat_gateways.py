"""
NAT Gateway Idle Scanner
NAT Gateways cost ~$32/month in base charges alone, before any data
processing fees. A forgotten one in an old VPC is pure waste.
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings

SCANNER_NAME = "nat_gateways"
SCANNER_LABEL = "Idle NAT Gateways"

NAT_HOURLY = 0.045


def scan_region(region: str) -> list[dict]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
        cw = boto3.client("cloudwatch", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=settings.idle_lookback_days)
    findings = []

    paginator = ec2.get_paginator("describe_nat_gateways")
    for page in paginator.paginate(Filters=[{"Name": "state", "Values": ["available"]}]):
        for nat in page["NatGateways"]:
            nat_id = nat["NatGatewayId"]
            resp = cw.get_metric_statistics(
                Namespace="AWS/NATGateway", MetricName="BytesOutToDestination",
                Dimensions=[{"Name": "NatGatewayId", "Value": nat_id}],
                StartTime=start, EndTime=end, Period=86400, Statistics=["Sum"],
            )
            total_bytes = sum(p["Sum"] for p in resp.get("Datapoints", []))
            if total_bytes >= settings.nat_gateway_bytes_threshold:
                continue
            findings.append({
                "resource_type": "nat_gateway",
                "resource_id": nat_id,
                "region": region,
                "reason": (
                    f"NAT Gateway processed only {total_bytes:.0f} bytes "
                    f"over {settings.idle_lookback_days} days - likely idle"
                ),
                "estimated_monthly_cost_usd": round(NAT_HOURLY * 730, 2),
                "details": {
                    "vpc_id": nat.get("VpcId", ""),
                    "subnet_id": nat.get("SubnetId", ""),
                    "total_bytes_out": int(total_bytes),
                },
            })
    return findings
