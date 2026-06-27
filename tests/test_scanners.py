import os

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.scanners import ebs_unattached, ec2_idle, snapshots  # noqa: E402


@mock_aws
def test_running_instance_with_no_metrics_is_idle():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.run_instances(ImageId="ami-12345678", InstanceType="t3.micro", MinCount=1, MaxCount=1)

    findings = ec2_idle.scan()

    # no datapoints in mock CloudWatch means zero usage, so it must be flagged
    assert len(findings) == 1
    assert findings[0].resource_type == "ec2_instance"
    assert findings[0].details["instance_type"] == "t3.micro"


@mock_aws
def test_unattached_volume_is_flagged():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=50, VolumeType="gp3")

    findings = ebs_unattached.scan()

    assert len(findings) == 1
    assert findings[0].details["size_gb"] == 50
    assert findings[0].estimated_monthly_cost_usd == 4.0


@mock_aws
def test_fresh_snapshot_is_not_flagged():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol = ec2.create_volume(AvailabilityZone="us-east-1a", Size=10)
    ec2.create_snapshot(VolumeId=vol["VolumeId"], Description="made just now")

    findings = snapshots.scan()

    # snapshot created seconds ago is inside the age cutoff
    assert findings == []
