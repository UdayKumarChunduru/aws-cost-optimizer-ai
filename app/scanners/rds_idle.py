"""
RDS Idle Instance Scanner
RDS is frequently the single largest line item on an AWS bill.
An instance with zero connections for a week is running but unused.
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings
from app.pricing.aws_pricing import get_pricing_cache

SCANNER_NAME = "rds_idle"
SCANNER_LABEL = "RDS Idle Instances"


def scan_region(region: str) -> list[dict]:
    try:
        rds = boto3.client("rds", region_name=region)
        cw = boto3.client("cloudwatch", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cache = get_pricing_cache()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=settings.rds_connection_lookback_days)
    findings = []

    paginator = rds.get_paginator("describe_db_instances")
    for page in paginator.paginate():
        for db in page["DBInstances"]:
            if db["DBInstanceStatus"] != "available":
                continue
            db_id = db["DBInstanceIdentifier"]

            conn_resp = cw.get_metric_statistics(
                Namespace="AWS/RDS", MetricName="DatabaseConnections",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
                StartTime=start, EndTime=end, Period=86400, Statistics=["Maximum"],
            )
            points = conn_resp.get("Datapoints", [])
            max_connections = max((p["Maximum"] for p in points), default=0)
            if max_connections > 0:
                continue

            iclass = db.get("DBInstanceClass", "unknown")
            engine = db.get("Engine", "")
            hourly = cache.get_rds_hourly_price(iclass, engine, region)
            findings.append({
                "resource_type": "rds_instance",
                "resource_id": db_id,
                "region": region,
                "reason": (
                    f"Zero database connections over "
                    f"{settings.rds_connection_lookback_days} days - running but unused"
                ),
                "estimated_monthly_cost_usd": round(hourly * 730, 2) if hourly else None,
                "details": {
                    "instance_class": iclass,
                    "engine": engine,
                    "multi_az": db.get("MultiAZ", False),
                },
            })
    return findings
