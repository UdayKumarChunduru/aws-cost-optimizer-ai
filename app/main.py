from fastapi import FastAPI

from app.config import settings
from app.llm.recommender import recommend_all
from app.models import ScanResult
from app.scanners import ebs_unattached, ec2_idle, snapshots

app = FastAPI(title="AI Cloud Cost Optimizer", version="1.0.0")

SCANNERS = {
    "ec2": ec2_idle.scan,
    "ebs": ebs_unattached.scan,
    "snapshots": snapshots.scan,
}


def _run(name: str, recommend: bool) -> ScanResult:
    findings = SCANNERS[name]()
    if recommend:
        findings = recommend_all(findings)
    return ScanResult(scanner=name, finding_count=len(findings), findings=findings)


@app.get("/health")
def health():
    return {"status": "ok", "region": settings.aws_region}


@app.get("/scan/ec2", response_model=ScanResult)
def scan_ec2(recommend: bool = True):
    return _run("ec2", recommend)


@app.get("/scan/ebs", response_model=ScanResult)
def scan_ebs(recommend: bool = True):
    return _run("ebs", recommend)


@app.get("/scan/snapshots", response_model=ScanResult)
def scan_snapshots(recommend: bool = True):
    return _run("snapshots", recommend)


@app.get("/scan/all", response_model=list[ScanResult])
def scan_all(recommend: bool = True):
    return [_run(name, recommend) for name in SCANNERS]
