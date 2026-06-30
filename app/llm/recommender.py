import logging

import requests

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = (
    "You are reviewing an AWS cost finding for a DevOps engineer. "
    "Resource type: {resource_type}. Resource ID: {resource_id}. Region: {region}. "
    "Problem: {reason}. "
    "Respond in exactly 7 numbered sentences. "
    "1. Explain why this resource is increasing costs. If historical cost or usage data is available, compare the current cost with the previous 14 days, or with the resource's lifetime if it is less than 14 days old, and indicate whether the cost trend is increasing, decreasing, or stable. If no historical data is available, explicitly state that the comparison cannot be made. "
    "2. Describe the checks that should be performed before taking any action, such as verifying resource utilization, attached dependencies, tags, backups, snapshots, associated services, and whether the resource is actively used in production, staging, or development. If this information is unavailable, explicitly state what additional information is required. "
    "3. Recommend the safest remediation strategy. Prefer non-destructive actions such as stopping, rightsizing, or detaching resources before permanent deletion whenever possible. Explain why the recommended approach is the safest. "
    "4. Provide the exact AWS CLI command(s) required to inspect the resource and gather enough information to confirm whether remediation is appropriate. Include only commands relevant to the detected resource type. "
    "5. Provide the exact AWS CLI command(s) required to stop, detach, snapshot, or permanently delete the resource, as appropriate for the resource type. If deletion is unsafe or unsupported, explicitly explain why and recommend the safest alternative. "
    "6. Explain how to verify that the remediation was successful. Include AWS CLI commands or AWS Console checks to confirm the resource has been stopped, deleted, detached, or is no longer generating charges. If applicable, mention how long it may take for AWS billing or Cost Explorer to reflect the change. "
    "7. Describe any risks, dependencies, recovery considerations, or situations where the resource should not be modified or deleted. Mention possible service interruptions, data loss, backup requirements, rollback options, and any compliance or security considerations relevant to the resource."
)


def recommend(finding: dict) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        resource_type=finding.get("resource_type", ""),
        resource_id=finding.get("resource_id", ""),
        region=finding.get("region", ""),
        reason=finding.get("reason", ""),
    )
    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        finding["recommendation"] = resp.json().get("response", "").strip()
    except requests.RequestException as exc:
        logger.warning("Ollama unreachable: %s", exc)
        finding["recommendation"] = "Ollama not reachable - run `ollama serve` locally."
    return finding


def recommend_all(findings: list[dict]) -> list[dict]:
    return [recommend(f) for f in findings]
