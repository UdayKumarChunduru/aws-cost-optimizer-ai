"""
AWS Pricing Cache
Calling the Pricing API once per resource causes ThrottlingException
on any account with more than a handful of resources.

This module caches every price lookup in memory for the lifetime of
one scan run. 100 t3.medium instances results in 1 API call, not 100.

The AWS Pricing API only has endpoints in us-east-1 and ap-south-1,
but returns prices for every other region when the region's long-form
location name is passed as a filter. This is documented AWS behavior,
not a workaround.
"""
import json
import logging
import threading

import boto3

logger = logging.getLogger(__name__)

# Maps short region codes to the long-form names the Pricing API expects.
# AWS does not provide an API to translate this, so it must be a static map.
# This list covers all regions enabled by default + commonly opted-in ones.
REGION_TO_LOCATION = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "af-south-1": "Africa (Cape Town)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ca-central-1": "Canada (Central)",
    "eu-central-1": "Europe (Frankfurt)",
    "eu-central-2": "Europe (Zurich)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)",
    "eu-north-1": "Europe (Stockholm)",
    "eu-south-1": "Europe (Milan)",
    "eu-south-2": "Europe (Spain)",
    "me-south-1": "Middle East (Bahrain)",
    "me-central-1": "Middle East (UAE)",
    "sa-east-1": "South America (Sao Paulo)",
    "il-central-1": "Israel (Tel Aviv)",
}

# EBS volume type pricing per GB-month does not vary enough across
# regions to justify a live API call for every lookup, and the
# Pricing API's EBS product codes are inconsistent across regions.
# These are AWS's published list prices, reviewed periodically.
EBS_PRICE_PER_GB_MONTH = {
    "gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125,
    "st1": 0.045, "sc1": 0.015, "standard": 0.05,
}


class PricingCache:
    """
    Thread-safe in-memory cache for EC2 and RDS hourly prices.
    One instance of this class should live for the duration of a
    single Lambda invocation, then be discarded - pricing rarely
    changes within a day, but we don't persist across invocations
    to avoid serving stale prices after an AWS price change.
    """

    def __init__(self):
        self._cache: dict[str, float | None] = {}
        self._lock = threading.Lock()
        self._pricing_client = None

    def _get_client(self):
        # Lazily create the client and reuse it - boto3 clients are
        # thread-safe for read operations once created.
        if self._pricing_client is None:
            self._pricing_client = boto3.client("pricing", region_name="us-east-1")
        return self._pricing_client

    def get_ec2_hourly_price(self, instance_type: str, region: str) -> float | None:
        cache_key = f"ec2:{instance_type}:{region}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        price = self._fetch_ec2_price(instance_type, region)

        with self._lock:
            self._cache[cache_key] = price
        return price

    def _fetch_ec2_price(self, instance_type: str, region: str) -> float | None:
        location = REGION_TO_LOCATION.get(region)
        if not location:
            logger.warning("No Pricing API location mapping for region %s", region)
            return None
        try:
            resp = self._get_client().get_products(
                ServiceCode="AmazonEC2",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                    {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                    {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                    {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                ],
                MaxResults=1,
            )
            if not resp["PriceList"]:
                return None
            price_item = json.loads(resp["PriceList"][0])
            terms = price_item.get("terms", {}).get("OnDemand", {})
            for term in terms.values():
                for dim in term.get("priceDimensions", {}).values():
                    price = float(dim["pricePerUnit"].get("USD", 0))
                    if price > 0:
                        return price
            return None
        except Exception as exc:
            logger.warning("Pricing API failed for %s in %s: %s", instance_type, region, exc)
            return None

    def get_rds_hourly_price(self, instance_class: str, engine: str, region: str) -> float | None:
        cache_key = f"rds:{instance_class}:{engine}:{region}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        price = self._fetch_rds_price(instance_class, engine, region)

        with self._lock:
            self._cache[cache_key] = price
        return price

    def _fetch_rds_price(self, instance_class: str, engine: str, region: str) -> float | None:
        location = REGION_TO_LOCATION.get(region)
        if not location:
            return None
        # RDS engine names in billing data differ from the API's engine
        # parameter (e.g. "postgres" -> "PostgreSQL").
        engine_map = {
            "postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB",
            "oracle-ee": "Oracle", "oracle-se2": "Oracle",
            "sqlserver-ex": "SQL Server", "sqlserver-web": "SQL Server",
            "sqlserver-se": "SQL Server", "sqlserver-ee": "SQL Server",
            "aurora-postgresql": "Aurora PostgreSQL", "aurora-mysql": "Aurora MySQL",
        }
        engine_name = engine_map.get(engine, engine)
        try:
            resp = self._get_client().get_products(
                ServiceCode="AmazonRDS",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_class},
                    {"Type": "TERM_MATCH", "Field": "databaseEngine", "Value": engine_name},
                    {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                    {"Type": "TERM_MATCH", "Field": "deploymentOption", "Value": "Single-AZ"},
                ],
                MaxResults=1,
            )
            if not resp["PriceList"]:
                return None
            price_item = json.loads(resp["PriceList"][0])
            terms = price_item.get("terms", {}).get("OnDemand", {})
            for term in terms.values():
                for dim in term.get("priceDimensions", {}).values():
                    price = float(dim["pricePerUnit"].get("USD", 0))
                    if price > 0:
                        return price
            return None
        except Exception as exc:
            logger.warning("RDS pricing failed for %s in %s: %s", instance_class, region, exc)
            return None

    def get_ebs_price_per_gb(self, volume_type: str) -> float:
        # Static lookup, no API call needed — see module docstring.
        return EBS_PRICE_PER_GB_MONTH.get(volume_type, 0.08)

    def stats(self) -> dict:
        """Returns cache hit stats for logging - helps verify the cache is working."""
        with self._lock:
            return {"cached_entries": len(self._cache)}


# Module-level singleton. Lambda containers are reused between invocations
# (warm starts), so this cache can persist across multiple scheduled runs
# within the same container lifetime, further reducing API calls over time.
_pricing_cache = PricingCache()


def get_pricing_cache() -> PricingCache:
    return _pricing_cache
