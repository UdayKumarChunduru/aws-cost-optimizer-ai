from fastapi import FastAPI, HTTPException

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


@app.get("/health")
def health():
    scanners = get_all_scanners()
    return {
        "status": "ok",
        "version": "4.0.0",
        "regions_configured": settings.aws_regions,
        "scanners_registered": sorted(scanners.keys()),
        "scanner_count": len(scanners),
    }


@app.get("/regions")
def list_regions():
    """Shows exactly which regions will be scanned right now, and why."""
    regions = get_regions_to_scan(settings.aws_regions)
    return {
        "configured": settings.aws_regions,
        "resolved_regions": regions,
        "region_count": len(regions),
    }


@app.get("/scanners")
def list_scanners():
    """Lists every auto-discovered scanner - proves the plugin system works."""
    scanners = get_all_scanners()
    return {
        "scanners": [
            {"name": name, "label": plugin.label}
            for name, plugin in sorted(scanners.items())
        ]
    }


@app.get("/scan/{scanner_name}")
def scan_one(scanner_name: str, recommend: bool = False, region: str | None = None):
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

    return {
        "scanner": scanner_name,
        "regions_scanned": regions,
        "finding_count": len(findings),
        "findings": findings,
    }


@app.get("/scan/all")
def scan_all(recommend: bool = False):
    """
    Runs every registered scanner against every active region in
    parallel via the orchestrator - this is the same code path the
    Lambda uses, so local testing and production behave identically.
    """
    regions = get_regions_to_scan(settings.aws_regions)
    result = run_all_scanners(regions)

    if recommend:
        result["findings"] = recommend_all(result["findings"])

    return result
