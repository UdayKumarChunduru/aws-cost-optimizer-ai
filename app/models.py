from typing import Optional

from pydantic import BaseModel


class Finding(BaseModel):
    resource_type: str
    resource_id: str
    region: str
    reason: str
    estimated_monthly_cost_usd: Optional[float] = None
    details: dict = {}
    recommendation: Optional[str] = None


class ScanResult(BaseModel):
    scanner: str
    finding_count: int
    findings: list[Finding]
