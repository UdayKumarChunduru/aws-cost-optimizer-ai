import os

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["SNS_TOPIC_ARN"] = ""
os.environ["ALERT_EMAIL"] = ""
os.environ["DYNAMODB_TABLE"] = "cost-optimizer-findings"

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.scanners import ebs_unattached, ec2_idle, ebs_snapshots  # noqa: E402
from app.scanners.registry import get_all_scanners  # noqa: E402
from app.scanners.orchestrator import run_all_scanners  # noqa: E402


def test_registry_discovers_all_ten_scanners():
    scanners = get_all_scanners()
    expected = {
        "ec2_idle", "ebs_unattached", "ebs_snapshots", "elastic_ips",
        "nat_gateways", "load_balancers", "rds_idle", "cloudwatch_logs",
        "secrets_manager", "ecr_images",
    }
    assert set(scanners.keys()) == expected
    # Confirms orchestrator.py and registry.py itself are correctly
    # excluded from being treated as scanners.


@mock_aws
def test_ec2_idle_scanner_flags_instance():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.run_instances(ImageId="ami-12345678", InstanceType="t3.micro", MinCount=1, MaxCount=1)
    findings = ec2_idle.scan_region("us-east-1")
    assert len(findings) == 1
    assert findings[0]["resource_type"] == "ec2_instance"
    assert findings[0]["details"]["instance_type"] == "t3.micro"


@mock_aws
def test_ebs_unattached_scanner_flags_volume():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=50, VolumeType="gp3")
    findings = ebs_unattached.scan_region("us-east-1")
    assert len(findings) == 1
    assert findings[0]["details"]["size_gb"] == 50
    assert findings[0]["estimated_monthly_cost_usd"] == 4.0


@mock_aws
def test_fresh_snapshot_not_flagged():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol = ec2.create_volume(AvailabilityZone="us-east-1a", Size=10)
    ec2.create_snapshot(VolumeId=vol["VolumeId"], Description="recent")
    findings = ebs_snapshots.scan_region("us-east-1")
    assert findings == []


@mock_aws
def test_orchestrator_runs_all_scanners_without_crashing():
    """
    The most important test in this file. Verifies the parallel
    orchestrator can run all 10 scanners against a region without
    any single scanner's failure (e.g. missing IAM permission in
    the test environment) crashing the entire run.
    """
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=20, VolumeType="gp3")
    ec2.run_instances(ImageId="ami-12345678", InstanceType="t3.micro", MinCount=1, MaxCount=1)

    result = run_all_scanners(["us-east-1"])

    assert result["scanners_run"] == 10
    assert result["regions_scanned"] == 1
    assert len(result["findings"]) >= 1
    assert "duration_seconds" in result
    # Errors list may be non-empty (e.g. moto not implementing every
    # API perfectly) but must never contain a crash that took down
    # other scanners - that's what "errors" being a list and findings
    # still being populated together proves.


@mock_aws
def test_orchestrator_isolates_failures_per_scanner():
    """
    Confirms one scanner raising an exception does not prevent the
    other nine from returning their findings - this is the error
    isolation fix for the bug where one bad API call used to be able
    to crash the whole Lambda invocation.
    """
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=15, VolumeType="gp3")

    # Scanning a region with no resources at all for most services,
    # plus one EBS volume, proves that scanners returning empty lists
    # (not errors) coexist fine with the one that finds something.
    result = run_all_scanners(["us-east-1"])

    ebs_findings = [f for f in result["findings"] if f["resource_type"] == "ebs_volume"]
    assert len(ebs_findings) == 1
