"""
Unattached Elastic IP Scanner
"""
import boto3
import botocore

SCANNER_NAME = "elastic_ips"
SCANNER_LABEL = "Unattached Elastic IPs"

EIP_HOURLY = 0.005


def scan_region(region: str) -> list[dict]:
    try:
        ec2 = boto3.client("ec2", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    findings = []
    resp = ec2.describe_addresses()
    for addr in resp.get("Addresses", []):
        if addr.get("AssociationId"):
            continue
        findings.append({
            "resource_type": "elastic_ip",
            "resource_id": addr.get("AllocationId", addr.get("PublicIp", "")),
            "region": region,
            "reason": "Elastic IP allocated but not attached to any running instance",
            "estimated_monthly_cost_usd": round(EIP_HOURLY * 730, 2),
            "details": {
                "public_ip": addr.get("PublicIp", ""),
                "allocation_id": addr.get("AllocationId", ""),
            },
        })
    return findings
