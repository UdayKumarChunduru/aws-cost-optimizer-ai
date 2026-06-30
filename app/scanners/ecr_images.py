"""
ECR Untagged Image Scanner
Every CI/CD build that doesn't get a release tag leaves an untagged
image layer behind. These accumulate fast in active pipelines and
each one still counts toward ECR storage charges ($0.10/GB-month).
"""
from datetime import datetime, timedelta, timezone

import boto3
import botocore

from app.config import settings

SCANNER_NAME = "ecr_images"
SCANNER_LABEL = "ECR Untagged Images"

ECR_PRICE_PER_GB_MONTH = 0.10


def scan_region(region: str) -> list[dict]:
    try:
        ecr = boto3.client("ecr", region_name=region)
    except botocore.exceptions.ClientError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ecr_untagged_age_days)
    findings = []

    repo_paginator = ecr.get_paginator("describe_repositories")
    for repo_page in repo_paginator.paginate():
        for repo in repo_page["repositories"]:
            repo_name = repo["repositoryName"]
            try:
                image_paginator = ecr.get_paginator("describe_images")
                for image_page in image_paginator.paginate(repositoryName=repo_name):
                    for image in image_page["imageDetails"]:
                        if image.get("imageTags"):
                            continue  # has at least one tag, not orphaned
                        pushed_at = image.get("imagePushedAt")
                        if pushed_at is None or pushed_at > cutoff:
                            continue
                        size_bytes = image.get("imageSizeInBytes", 0)
                        size_gb = size_bytes / (1024 ** 3)
                        age_days = (datetime.now(timezone.utc) - pushed_at).days
                        findings.append({
                            "resource_type": "ecr_image",
                            "resource_id": (
                                f"{repo_name}@{image.get('imageDigest', '')[:19]}"
                            ),
                            "region": region,
                            "reason": (
                                f"Untagged image, {age_days} days old — "
                                f"orphaned from a CI build with no release tag"
                            ),
                            "estimated_monthly_cost_usd": round(
                                size_gb * ECR_PRICE_PER_GB_MONTH, 4
                            ),
                            "details": {
                                "repository": repo_name,
                                "size_gb": round(size_gb, 4),
                                "age_days": age_days,
                            },
                        })
            except botocore.exceptions.ClientError:
                continue
    return findings
