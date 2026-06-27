from datetime import datetime, timedelta, timezone

import boto3

from app.config import settings
from app.models import Finding

# rough on-demand hourly rates for cost estimates in the report
HOURLY_RATES = {
    "t2.micro": 0.0116, "t3.micro": 0.0104, "t3.small": 0.0208,
    "t3.medium": 0.0416, "m5.large": 0.096, "m5.xlarge": 0.192,
}


def _avg_cpu(cw, instance_id: str, start, end) -> float:
    resp = cw.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start, EndTime=end,
        Period=3600, Statistics=["Average"],
    )
    points = resp.get("Datapoints", [])
    if not points:
        return 0.0
    return sum(p["Average"] for p in points) / len(points)


def _total_network(cw, instance_id: str, start, end) -> float:
    total = 0.0
    for metric in ("NetworkIn", "NetworkOut"):
        resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName=metric,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start, EndTime=end,
            Period=3600, Statistics=["Sum"],
        )
        total += sum(p["Sum"] for p in resp.get("Datapoints", []))
    return total


def scan() -> list[Finding]:
    ec2 = boto3.client("ec2", region_name=settings.aws_region)
    cw = boto3.client("cloudwatch", region_name=settings.aws_region)

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

                itype = inst.get("InstanceType", "")
                hourly = HOURLY_RATES.get(itype)
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}

                findings.append(Finding(
                    resource_type="ec2_instance",
                    resource_id=instance_id,
                    region=settings.aws_region,
                    reason=(
                        f"Average CPU {cpu:.2f}% and total network {net:.0f} bytes "
                        f"over {settings.idle_lookback_days} days, both under thresholds"
                    ),
                    estimated_monthly_cost_usd=round(hourly * 730, 2) if hourly else None,
                    details={
                        "instance_type": itype,
                        "launch_time": str(inst.get("LaunchTime", "")),
                        "avg_cpu_percent": round(cpu, 2),
                        "total_network_bytes": int(net),
                        "tags": tags,
                    },
                ))
    return findings
