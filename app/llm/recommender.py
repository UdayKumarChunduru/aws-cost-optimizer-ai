import logging

import requests

from app.config import settings
from app.models import Finding

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = (
    "You are reviewing AWS cost findings for an engineer. "
    "Resource type: {resource_type}. Resource id: {resource_id}. "
    "Reason flagged: {reason}. Details: {details}. "
    "In three sentences or fewer, state the safest remediation step, "
    "what to verify first, and the AWS CLI command to do it."
)


def recommend(finding: Finding) -> Finding:
    prompt = PROMPT_TEMPLATE.format(
        resource_type=finding.resource_type,
        resource_id=finding.resource_id,
        reason=finding.reason,
        details=finding.details,
    )
    try:
        resp = requests.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        finding.recommendation = resp.json().get("response", "").strip()
    except requests.RequestException as exc:
        logger.warning("Ollama unreachable, returning finding without recommendation: %s", exc)
        finding.recommendation = "Recommendation unavailable, Ollama was not reachable during the scan."
    return finding


def recommend_all(findings: list[Finding]) -> list[Finding]:
    return [recommend(f) for f in findings]
