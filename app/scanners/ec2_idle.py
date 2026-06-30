"""
EC2 Idle Instance Scanner
Uses the pricing cache (app.pricing.aws_pricing) instead of calling
the Pricing API per-instance, which was causing ThrottlingException
once scans covered accounts with more than a handful of instances.
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings
from app.pricing.aws_pricing import get_pricing_cache

SCANNER_NAME = "ec2_idle"
SCANNER_LABEL = "EC2 Idle Instances"


def _avg_cpu(cw, instance_id, start, end) -> float:
    resp = cw.get_metric_statistics(
        Namespace="AWS/EC2", MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start, EndTime=end, Period=3600, Statistics=["Average"],
    )
    points = resp.get("Datapoints", [])
    return sum(p["Average"] for p in points) / len(points) if points else 0.0


def _total_network(cw, instance_id, start, end) -> float:
    total = 0.0
    for metric in ("NetworkIn", "NetworkOut"):
        resp = cw.get_metric_statistics(
            Namespace="AWS/EC2", MetricName=metric,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start, EndTime=end, Period=3600, Statistics=["Sum"],
        )
        total += sum(p["Sum"] for p in resp.get("Datapoints", []))
    return total


def scan_region(region: str) -> list[dict]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
        cw = boto3.client("cloudwatch", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cache = get_pricing_cache()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=settings.idle_lookback_days)
    findings = []

    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instance_id = inst["InstanceId"]
                cpu = _avg_cpu(cw, instance_id, start, end)
                net = _total_network(cw, instance_id, start, end)
                if cpu >= settings.idle_cpu_threshold or net >= settings.idle_network_threshold:
                    continue
                itype = inst.get("InstanceType", "unknown")
                # Single cache-backed lookup - repeated instance types
                # across many instances cost zero extra API calls.
                hourly = cache.get_ec2_hourly_price(itype, region)
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                findings.append({
                    "resource_type": "ec2_instance",
                    "resource_id": instance_id,
                    "region": region,
                    "reason": (
                        f"Avg CPU {cpu:.2f}% and network {net:.0f} bytes "
                        f"over {settings.idle_lookback_days} days - both below thresholds"
                    ),
                    "estimated_monthly_cost_usd": round(hourly * 730, 2) if hourly else None,
                    "details": {
                        "instance_type": itype,
                        "avg_cpu_percent": round(cpu, 2),
                        "total_network_bytes": int(net),
                        "tags": tags,
                    },
                })
    return findings
