"""
Region Discovery
ec2.describe_regions() only reports which regions are ENABLED for an
account - it says nothing about which regions actually have resources
running in them. An account could have EC2 enabled in 17 regions but
resources running in only 2.

This module uses the AWS Resource Groups Tagging API, which can list
resources across services in a single region call, combined with a
Cost Explorer check for which regions appear in recent billing data.
This mirrors the same approach AWS Cost Explorer itself uses
internally to build its per-region cost breakdown.

Two-tier strategy:
1. Cost Explorer (if available) - identifies which regions had ANY
   billing activity in the last 14 days. Fast, one API call.
2. Fallback: describe_regions() + lightweight per-region probe using
   resourcegroupstaggingapi, which is cheap and works even without
   Cost Explorer permissions (some accounts restrict it).
"""
import logging
from datetime import datetime, timedelta

import boto3
import botocore

logger = logging.getLogger(__name__)


def discover_active_regions_via_cost_explorer(lookback_days: int = 14) -> set[str] | None:
    """
    Cost Explorer's region dimension tells us exactly which regions
    generated cost in the recent past. Returns None if Cost Explorer
    is unavailable or the call fails, so the caller can fall back.
    """
    try:
        ce = boto3.client("ce", region_name="us-east-1")
        end = datetime.utcnow().date()
        start = end - timedelta(days=lookback_days)
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}],
        )
        regions = set()
        for result in resp.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                region = group["Keys"][0]
                # Cost Explorer includes "NoRegion" and "global" for
                # services like IAM, Route53, CloudFront - filter those out
                if region and region not in ("NoRegion", "global"):
                    regions.add(region)
        return regions if regions else None
    except botocore.exceptions.ClientError as exc:
        logger.warning("Cost Explorer unavailable, falling back: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Cost Explorer call failed, falling back: %s", exc)
        return None


def discover_enabled_regions() -> list[str]:
    """
    Lists every region enabled for this account (opt-in or default).
    This is the universe of regions we COULD scan, before narrowing
    down to regions that actually have activity.
    """
    ec2 = boto3.client("ec2", region_name="us-east-1")
    resp = ec2.describe_regions(
        Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
    )
    return [r["RegionName"] for r in resp["Regions"]]


def probe_region_has_resources(region: str) -> bool:
    """
    Lightweight check: does this region have ANY taggable resource?
    Used as a fallback per-region probe when Cost Explorer isn't available.
    The Resource Groups Tagging API covers most services in one call,
    which is far cheaper than calling describe_instances, describe_volumes,
    describe_db_instances, etc. separately just to check "is this empty".
    """
    try:
        client = boto3.client("resourcegroupstaggingapi", region_name=region)
        resp = client.get_resources(ResourcesPerPage=1)
        return len(resp.get("ResourceTagMappingList", [])) > 0
    except Exception as exc:
        logger.warning("Resource probe failed for %s: %s", region, exc)
        # On failure, assume the region might have resources rather than
        # silently skipping it - false positives cost a few seconds of
        # scan time, false negatives hide real waste.
        return True


def get_regions_to_scan(configured_regions: str) -> list[str]:
    """
    Main entry point. configured_regions is the raw value from settings:
    - "ALL"     -> discover active regions automatically
    - "us-east-1,eu-west-1" -> use exactly these, no discovery
    """
    if configured_regions.upper() != "ALL":
        return [r.strip() for r in configured_regions.split(",") if r.strip()]

    active = discover_active_regions_via_cost_explorer()
    if active:
        logger.info("Cost Explorer found %d active regions: %s", len(active), active)
        return sorted(active)

    logger.info("Cost Explorer unavailable - falling back to per-region resource probe")
    enabled = discover_enabled_regions()
    active_regions = []
    for region in enabled:
        if probe_region_has_resources(region):
            active_regions.append(region)
    logger.info("Resource probe found %d active regions out of %d enabled",
                len(active_regions), len(enabled))
    return active_regions
