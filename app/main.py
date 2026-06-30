from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.llm.recommender import recommend_all
from app.regions.discovery import get_regions_to_scan
from app.scanners.orchestrator import run_all_scanners
from app.scanners.registry import get_all_scanners, get_scanner

app = FastAPI(
    title="AI Cloud Cost Optimizer",
    description=(
        "Multi-region, multi-service AWS cost waste scanner with a "
        "plug-in scanner architecture. New scanners are auto-discovered "
        "from app/scanners/ with no code changes elsewhere."
    ),
    version="4.0.0",
)


class HealthResponse(BaseModel):
    status: str
    version: str
    regions_configured: str
    scanners_registered: list[str]
    scanner_count: int


class RegionsResponse(BaseModel):
    configured: str
    resolved_regions: list[str]
    region_count: int


class ScannerInfo(BaseModel):
    name: str
    label: str


class ScannersResponse(BaseModel):
    scanners: list[ScannerInfo]


class ScanOneResponse(BaseModel):
    scanner: str
    regions_scanned: list[str]
    finding_count: int
    findings: list[dict]


class ScanAllResponse(BaseModel):
    findings: list[dict]
    errors: list[dict]
    scanners_run: int
    regions_scanned: int
    duration_seconds: float


@app.get("/health", response_model=HealthResponse)
def health():
    scanners = get_all_scanners()
    return HealthResponse(
        status="ok",
        version="4.0.0",
        regions_configured=settings.aws_regions,
        scanners_registered=sorted(scanners.keys()),
        scanner_count=len(scanners),
    )


@app.get("/regions", response_model=RegionsResponse)
def list_regions():
    """Shows exactly which regions will be scanned right now, and why."""
    regions = get_regions_to_scan(settings.aws_regions)
    return RegionsResponse(
        configured=settings.aws_regions,
        resolved_regions=regions,
        region_count=len(regions),
    )


@app.get("/scanners", response_model=ScannersResponse)
def list_scanners():
    """Lists every auto-discovered scanner, confirming the plugin system works."""
    scanners = get_all_scanners()
    return ScannersResponse(
        scanners=[
            ScannerInfo(name=name, label=plugin.label)
            for name, plugin in sorted(scanners.items())
        ]
    )


@app.get("/scan/{scanner_name}", response_model=ScanOneResponse)
def scan_one(scanner_name: str, recommend: bool = False, region: Optional[str] = None):
    plugin = get_scanner(scanner_name)
    if plugin is None:
        available = sorted(get_all_scanners().keys())
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scanner '{scanner_name}'. Available: {available}",
        )

    regions = [region] if region else get_regions_to_scan(settings.aws_regions)
    findings = []
    for r in regions:
        findings.extend(plugin.scan_region_fn(r))

    if recommend:
        findings = recommend_all(findings)

    return ScanOneResponse(
        scanner=scanner_name,
        regions_scanned=regions,
        finding_count=len(findings),
        findings=findings,
    )


@app.get("/scan/all", response_model=ScanAllResponse)
def scan_all(recommend: bool = False):
    """
    Runs every registered scanner against every active region in
    parallel via the orchestrator. This is the same code path the
    Lambda uses, so local testing and production behave identically.
    """
    regions = get_regions_to_scan(settings.aws_regions)
    result = run_all_scanners(regions)

    if recommend:
        result["findings"] = recommend_all(result["findings"])

    return ScanAllResponse(**result)
