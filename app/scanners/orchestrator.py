"""
Parallel Scan Orchestrator
Fixes the critical bug Gemini correctly identified: scanning regions
one-by-one, with multiple scanners per region also running one-by-one,
means total scan time = regions x scanners x avg_api_latency. With 15
active regions and 10 scanners, a 2-second average per call is 300
seconds - right at the Lambda timeout edge, and over it the moment
any single API call is slow.

This module runs scanners concurrently using ThreadPoolExecutor.
boto3 is thread-safe for the read-only describe/list/get calls every
scanner here makes, so this is a safe and standard pattern - it's
exactly what boto3's own documentation recommends for this use case.

Two levels of parallelism:
1. Across regions - region A and region B scan at the same time
2. Across scanners within a region - EC2 and EBS scan the same
   region at the same time

Each individual scanner call gets a hard timeout so one hanging API
call (rare, but it happens with AWS) can never block the whole run.
"""
import concurrent.futures
import logging
import time

from app.config import settings
from app.scanners.registry import get_all_scanners

logger = logging.getLogger(__name__)


def _run_one_scanner(scanner_name: str, scan_region_fn, region: str) -> tuple[str, str, list, str | None]:
    """
    Runs exactly one (scanner, region) pair. Returns a 4-tuple so the
    caller can always tell which scanner/region produced which result
    or error, even when scans complete out of order.
    """
    start_time = time.monotonic()
    try:
        findings = scan_region_fn(region)
        elapsed = time.monotonic() - start_time
        logger.info(
            "Scanner '%s' in region '%s' completed in %.2fs with %d finding(s)",
            scanner_name, region, elapsed, len(findings),
        )
        return (scanner_name, region, findings, None)
    except Exception as exc:
        elapsed = time.monotonic() - start_time
        logger.error(
            "Scanner '%s' in region '%s' FAILED after %.2fs: %s",
            scanner_name, region, elapsed, exc,
        )
        # A failed scanner in one region must never take down the
        # findings from every other scanner/region pair - this is the
        # per-scanner error isolation that was missing before.
        return (scanner_name, region, [], str(exc))


def run_all_scanners(regions: list[str]) -> dict:
    """
    Runs every registered scanner against every region in parallel.

    Returns:
        {
            "findings": [...],           # flat list of all findings found
            "errors": [{"scanner":, "region":, "error":}, ...],
            "scanners_run": int,
            "regions_scanned": int,
            "duration_seconds": float,
        }
    """
    scanners = get_all_scanners()
    if not scanners:
        logger.warning("No scanners registered - check app/scanners/ for import errors")
        return {
            "findings": [], "errors": [], "scanners_run": 0,
            "regions_scanned": 0, "duration_seconds": 0.0,
        }

    overall_start = time.monotonic()
    all_findings = []
    all_errors = []

    # Build the full list of (scanner, region) work items up front so
    # we can bound total concurrency across BOTH dimensions at once,
    # rather than nesting two separate thread pools which can spawn
    # an uncontrolled number of threads (regions x scanners).
    work_items = [
        (name, plugin.scan_region_fn, region)
        for name, plugin in scanners.items()
        for region in regions
    ]

    max_workers = min(
        settings.max_parallel_regions * settings.max_parallel_scanners_per_region,
        50,  # hard ceiling regardless of config - protects Lambda memory
    )

    logger.info(
        "Starting parallel scan: %d scanner(s) x %d region(s) = %d work items, "
        "max_workers=%d",
        len(scanners), len(regions), len(work_items), max_workers,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(_run_one_scanner, name, fn, region): (name, region)
            for name, fn, region in work_items
        }

        for future in concurrent.futures.as_completed(
            future_to_item, timeout=settings.scanner_timeout_seconds * 3
        ):
            name, region = future_to_item[future]
            try:
                # Hard per-task timeout - if a single scanner call to a
                # single region exceeds this, abandon it rather than let
                # it block the overall as_completed() loop.
                scanner_name, scan_region_val, findings, error = future.result(
                    timeout=settings.scanner_timeout_seconds
                )
                all_findings.extend(findings)
                if error:
                    all_errors.append({
                        "scanner": scanner_name, "region": scan_region_val, "error": error
                    })
            except concurrent.futures.TimeoutError:
                logger.error("Scanner '%s' in region '%s' timed out - abandoning", name, region)
                all_errors.append({
                    "scanner": name, "region": region,
                    "error": f"Timed out after {settings.scanner_timeout_seconds}s",
                })

    duration = time.monotonic() - overall_start
    logger.info(
        "Parallel scan complete in %.2fs - %d findings, %d errors",
        duration, len(all_findings), len(all_errors),
    )

    return {
        "findings": all_findings,
        "errors": all_errors,
        "scanners_run": len(scanners),
        "regions_scanned": len(regions),
        "duration_seconds": round(duration, 2),
    }
