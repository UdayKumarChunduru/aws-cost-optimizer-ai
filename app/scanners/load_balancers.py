"""
Empty Load Balancer Scanner
Application/Network Load Balancers cost ~$16-22/month base price
even with zero traffic, plus LCU charges. An ALB with no healthy
targets in any target group is serving nothing and pure waste.
"""
import boto3
import botocore

SCANNER_NAME = "load_balancers"
SCANNER_LABEL = "Empty Load Balancers"

ALB_HOURLY = 0.0225  # Application Load Balancer base price
NLB_HOURLY = 0.0225  # Network Load Balancer base price


def scan_region(region: str) -> list[dict]:
    try:
        elbv2 = boto3.client("elbv2", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    findings = []
    try:
        paginator = elbv2.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page["LoadBalancers"]:
                lb_arn = lb["LoadBalancerArn"]
                lb_type = lb.get("Type", "application")

                tg_resp = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
                target_groups = tg_resp.get("TargetGroups", [])

                has_healthy_target = False
                for tg in target_groups:
                    health_resp = elbv2.describe_target_health(
                        TargetGroupArn=tg["TargetGroupArn"]
                    )
                    for target in health_resp.get("TargetHealthDescriptions", []):
                        if target.get("TargetHealth", {}).get("State") == "healthy":
                            has_healthy_target = True
                            break
                    if has_healthy_target:
                        break

                if has_healthy_target:
                    continue

                hourly = ALB_HOURLY if lb_type == "application" else NLB_HOURLY
                findings.append({
                    "resource_type": "load_balancer",
                    "resource_id": lb["LoadBalancerName"],
                    "region": region,
                    "reason": (
                        f"{lb_type.upper()} has {len(target_groups)} target group(s) "
                        f"but zero healthy targets - serving no traffic"
                    ),
                    "estimated_monthly_cost_usd": round(hourly * 730, 2),
                    "details": {
                        "type": lb_type,
                        "arn": lb_arn,
                        "target_group_count": len(target_groups),
                    },
                })
    except botocore.exceptions.ClientError as exc:
        # ELB API not available or no permission in this region -
        # skip silently rather than crash the whole scan.
        if "AccessDenied" not in str(exc):
            pass
    return findings
