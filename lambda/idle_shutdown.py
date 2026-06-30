"""
Lambda 1: Nightly Idle Shutdown
Scans every active region for tagged dev instances instead of only
the Lambda's own deployment region, since dev instances tagged for
auto-stop can exist in any region the team uses.
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.regions.discovery import get_regions_to_scan  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LAMBDA_REGION = os.environ.get("LAMBDA_REGION", "us-east-1")
SCAN_REGIONS_ENV = os.environ.get("SCAN_REGIONS", "ALL")
CPU_THRESHOLD = float(os.environ.get("IDLE_CPU_THRESHOLD", "5.0"))
NETWORK_THRESHOLD = float(os.environ.get("IDLE_NETWORK_THRESHOLD", "5000000"))
LOOKBACK_DAYS = int(os.environ.get("IDLE_LOOKBACK_DAYS", "7"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")


def _is_idle(cw, instance_id, start, end) -> bool:
    cpu = cw.get_metric_statistics(
        Namespace="AWS/EC2", MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start, EndTime=end, Period=3600, Statistics=["Average"],
    )["Datapoints"]
    avg_cpu = sum(p["Average"] for p in cpu) / len(cpu) if cpu else 0.0

    net_total = 0.0
    for metric in ("NetworkIn", "NetworkOut"):
        pts = cw.get_metric_statistics(
            Namespace="AWS/EC2", MetricName=metric,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start, EndTime=end, Period=3600, Statistics=["Sum"],
        )["Datapoints"]
        net_total += sum(p["Sum"] for p in pts)

    return avg_cpu < CPU_THRESHOLD and net_total < NETWORK_THRESHOLD


def stop_idle_in_region(region: str) -> tuple[list, list]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
        cw = boto3.client("cloudwatch", region_name=region)
    except Exception as exc:
        logger.warning("Could not create clients for %s: %s", region, exc)
        return [], []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    resp = ec2.describe_instances(Filters=[
        {"Name": "instance-state-name", "Values": ["running"]},
        {"Name": "tag:Environment", "Values": ["dev"]},
        {"Name": "tag:AutoStop", "Values": ["true"]},
    ])

    stopped, skipped = [], []
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            iid = inst["InstanceId"]
            try:
                if _is_idle(cw, iid, start, end):
                    ec2.stop_instances(InstanceIds=[iid])
                    stopped.append(f"{region}:{iid}")
                else:
                    skipped.append(f"{region}:{iid}")
            except Exception as exc:
                logger.error("Failed to evaluate/stop %s in %s: %s", iid, region, exc)
    return stopped, skipped


def handler(event, context):
    regions = get_regions_to_scan(SCAN_REGIONS_ENV)
    logger.info("Checking %d region(s) for idle dev instances: %s", len(regions), regions)

    all_stopped, all_skipped = [], []
    for region in regions:
        stopped, skipped = stop_idle_in_region(region)
        all_stopped.extend(stopped)
        all_skipped.extend(skipped)

    logger.info("Stopped %d instance(s) across %d region(s)", len(all_stopped), len(regions))

    if SNS_TOPIC_ARN and all_stopped:
        sns = boto3.client("sns", region_name=LAMBDA_REGION)
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[Cost Optimizer] Auto-stopped {len(all_stopped)} idle dev instance(s)",
            Message=(
                f"Nightly idle shutdown report\n"
                f"Regions checked: {regions}\n"
                f"Stopped: {all_stopped}\n"
                f"Skipped (not idle): {all_skipped}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            ),
        )

    return {"stopped": all_stopped, "skipped": all_skipped, "regions_checked": len(regions)}
