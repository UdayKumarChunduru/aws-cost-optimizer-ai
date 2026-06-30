from app.pricing.aws_pricing import PricingCache, REGION_TO_LOCATION


def test_ebs_pricing_is_static_lookup_no_api_call():
    cache = PricingCache()
    assert cache.get_ebs_price_per_gb("gp3") == 0.08
    assert cache.get_ebs_price_per_gb("io2") == 0.125
    # Unknown volume types fall back to gp3 pricing rather than crashing
    assert cache.get_ebs_price_per_gb("totally-made-up-type") == 0.08


def test_ec2_price_is_cached_after_first_call(monkeypatch):
    """
    Verifies the cache actually prevents repeat API calls. Without
    this cache, calling the Pricing API once per EC2 instance instead
    of once per (instance_type, region) triggers ThrottlingException
    on any account with more than a handful of running instances.
    """
    cache = PricingCache()
    call_count = {"count": 0}

    def fake_fetch(instance_type, region):
        call_count["count"] += 1
        return 0.0104

    monkeypatch.setattr(cache, "_fetch_ec2_price", fake_fetch)

    # Simulate 50 instances of the same type - should only hit the
    # "fetch" function once, with the other 49 served from cache.
    for _ in range(50):
        price = cache.get_ec2_hourly_price("t3.micro", "us-east-1")
        assert price == 0.0104

    assert call_count["count"] == 1, (
        f"Expected exactly 1 API call for 50 identical lookups, "
        f"got {call_count['count']} - caching is broken"
    )


def test_region_to_location_map_covers_common_regions():
    required_regions = [
        "us-east-1", "us-west-2", "ap-south-1", "eu-west-1", "ap-southeast-1",
    ]
    for region in required_regions:
        assert region in REGION_TO_LOCATION, f"Missing pricing location for {region}"
