import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
CPU_THRESHOLD = float(os.environ.get("IDLE_CPU_THRESHOLD", "5.0"))
NETWORK_THRESHOLD = float(os.environ.get("IDLE_NETWORK_THRESHOLD", "5000000"))
LOOKBACK_DAYS = int(os.environ.get("IDLE_LOOKBACK_DAYS", "7"))


def _is_idle(cw, instance_id, start, end):
    cpu = cw.get_metric_statistics(
        Namespace="AWS/EC2", MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start, EndTime=end, Period=3600, Statistics=["Average"],
    )["Datapoints"]
    avg_cpu = sum(p["Average"] for p in cpu) / len(cpu) if cpu else 0.0

    net_total = 0.0
    for metric in ("NetworkIn", "NetworkOut"):
        points = cw.get_metric_statistics(
            Namespace="AWS/EC2", MetricName=metric,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start, EndTime=end, Period=3600, Statistics=["Sum"],
        )["Datapoints"]
        net_total += sum(p["Sum"] for p in points)

    return avg_cpu < CPU_THRESHOLD and net_total < NETWORK_THRESHOLD


def handler(event, context):
    ec2 = boto3.client("ec2", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    resp = ec2.describe_instances(Filters=[
        {"Name": "instance-state-name", "Values": ["running"]},
        {"Name": "tag:Environment", "Values": ["dev"]},
        {"Name": "tag:AutoStop", "Values": ["true"]},
    ])

    stopped = []
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            instance_id = inst["InstanceId"]
            if _is_idle(cw, instance_id, start, end):
                ec2.stop_instances(InstanceIds=[instance_id])
                stopped.append(instance_id)
                logger.info("Stopped idle dev instance %s", instance_id)
            else:
                logger.info("Instance %s tagged for autostop but not idle, leaving it", instance_id)

    logger.info("Run complete, stopped %d instance(s): %s", len(stopped), stopped)
    return {"stopped": stopped}
