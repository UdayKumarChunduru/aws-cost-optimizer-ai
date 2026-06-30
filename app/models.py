from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Severity(str, Enum):
    """
    Cost impact severity, not urgency - a $200/month idle RDS instance
    is HIGH even though nothing is "on fire". This lets the weekly
    email sort findings so the biggest waste is seen first instead of
    buried in a long flat list.
    """
    HIGH = "high"      # >= $50/month
    MEDIUM = "medium"  # >= $10/month
    LOW = "low"        # < $10/month


def severity_from_cost(monthly_cost_usd: float | None) -> Severity:
    if monthly_cost_usd is None:
        return Severity.LOW
    if monthly_cost_usd >= 50:
        return Severity.HIGH
    if monthly_cost_usd >= 10:
        return Severity.MEDIUM
    return Severity.LOW


class Finding(BaseModel):
    resource_type: str
    resource_id: str
    region: str
    reason: str
    estimated_monthly_cost_usd: Optional[float] = None
    severity: Severity = Severity.LOW
    details: dict = {}
    recommendation: Optional[str] = None

    def model_post_init(self, __context) -> None:
        # Auto-derive severity from cost if not explicitly set, so
        # scanner code never has to remember to set it manually.
        self.severity = severity_from_cost(self.estimated_monthly_cost_usd)


class ScanResult(BaseModel):
    scanner: str
    finding_count: int
    findings: list[Finding]
    error: Optional[str] = None  # set if this scanner failed entirely
