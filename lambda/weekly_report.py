"""
Lambda 2: Weekly Cost Report
Uses the parallel orchestrator (app.scanners.orchestrator) and the
plugin registry (app.scanners.registry), so this file does NOT
hardcode a list of scanner functions to call. All 10 scanners run
automatically, and any scanner added later runs automatically too
with zero changes to this file - adding a scanner used to require
manually editing this loop to add findings.extend(scan_new_thing(region)),
which didn't scale and was easy to forget.

Region selection uses Cost Explorer-based active-region discovery
(app.regions.discovery) instead of describe_regions() alone, since
describe_regions() misses regions where EC2 is disabled but other
services (RDS, S3, etc.) are still active and billing.
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3

# Lambda's deployment package includes the app/ directory alongside
# this file (see deploy step), so this import works in both local
# testing and the real Lambda runtime without path hacks beyond this.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.regions.discovery import get_regions_to_scan  # noqa: E402
from app.scanners.orchestrator import run_all_scanners  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LAMBDA_REGION = os.environ.get("LAMBDA_REGION", "us-east-1")
SCAN_REGIONS_ENV = os.environ.get("SCAN_REGIONS", "ALL")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "cost-optimizer-findings")

# DynamoDB has a hard 25-item limit per BatchWriteItem call. Findings
# are written individually with put_item below to keep this simple
# and avoid that limit entirely, but a cap here protects against a
# pathological scan (e.g. thousands of untagged ECR images) from
# generating an excessive number of write requests in one invocation.
MAX_FINDINGS_TO_PERSIST = 500


def get_ai_recommendation(bedrock, resource_type: str, resource_id: str, reason: str) -> str:
    prompt = (
        f"You are a senior AWS DevOps and Infrastructure engineer reviewing a cost finding.\n"
        f"Resource: {resource_type} | ID: {resource_id}\n"
        f"Problem: {reason}\n"
        f"Respond in exactly 7 numbered sentences. "
        f"1. Explain why this resource is increasing costs. If historical cost or usage data is available, compare the current cost with the previous 14 days, or with the resource's lifetime if it is less than 14 days old, and indicate whether the cost trend is increasing, decreasing, or stable. If no historical data is available, explicitly state that the comparison cannot be made. "
        f"2. Describe the checks that should be performed before taking any action, such as verifying resource utilization, attached dependencies, tags, backups, snapshots, associated services, and whether the resource is actively used in production, staging, or development. If this information is unavailable, explicitly state what additional information is required. "
        f"3. Recommend the safest remediation strategy. Prefer non-destructive actions such as stopping, rightsizing, or detaching resources before permanent deletion whenever possible. Explain why the recommended approach is the safest. "
        f"4. Provide the exact AWS CLI command(s) required to inspect the resource and gather enough information to confirm whether remediation is appropriate. Include only commands relevant to the detected resource type. "
        f"5. Provide the exact AWS CLI command(s) required to stop, detach, snapshot, or permanently delete the resource, as appropriate for the resource type. If deletion is unsafe or unsupported, explicitly explain why and recommend the safest alternative. "
        f"6. Explain how to verify that the remediation was successful. Include AWS CLI commands or AWS Console checks to confirm the resource has been stopped, deleted, detached, or is no longer generating charges. If applicable, mention how long it may take for AWS billing or Cost Explorer to reflect the change. "
        f"7. Describe any risks, dependencies, recovery considerations, or situations where the resource should not be modified or deleted. Mention possible service interruptions, data loss, backup requirements, rollback options, and any compliance or security considerations relevant to the resource."
    )
    try:
        resp = bedrock.invoke_model(
            modelId=BEDROCK_MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            }),
        )
        return json.loads(resp["body"].read())["content"][0]["text"].strip()
    except Exception as exc:
        logger.warning("Bedrock failed for %s: %s", resource_id, exc)
        return "AI recommendation unavailable."


def save_to_dynamodb(dynamodb, findings: list, run_id: str):
    table = dynamodb.Table(DYNAMODB_TABLE)
    for f in findings[:MAX_FINDINGS_TO_PERSIST]:
        try:
            table.put_item(Item={
                "run_id": run_id,
                "resource_id": f["resource_id"],
                "resource_type": f["resource_type"],
                "region": f.get("region", ""),
                "reason": f["reason"],
                "recommendation": f.get("recommendation", ""),
                "estimated_monthly_cost_usd": str(f.get("estimated_monthly_cost_usd") or "0"),
                "ttl": int((datetime.now(timezone.utc) + timedelta(days=90)).timestamp()),
            })
        except Exception as exc:
            # One bad write (e.g. resource_id containing characters
            # DynamoDB rejects) must not abort the whole batch.
            logger.warning("DynamoDB write failed for %s: %s", f.get("resource_id"), exc)
    if len(findings) > MAX_FINDINGS_TO_PERSIST:
        logger.warning(
            "Truncated DynamoDB writes at %d of %d total findings",
            MAX_FINDINGS_TO_PERSIST, len(findings),
        )


def publish_metrics(cw, findings: list, regions_scanned: int, scanners_run: int, errors: list):
    total = sum(f.get("estimated_monthly_cost_usd") or 0 for f in findings)
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        cost = f.get("estimated_monthly_cost_usd") or 0
        if cost >= 50:
            by_severity["high"] += 1
        elif cost >= 10:
            by_severity["medium"] += 1
        else:
            by_severity["low"] += 1

    metric_data = [
        {"MetricName": "WeeklyFindingsCount", "Value": len(findings), "Unit": "Count",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
        {"MetricName": "EstimatedMonthlyWasteUSD", "Value": total, "Unit": "None",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
        {"MetricName": "RegionsScanned", "Value": regions_scanned, "Unit": "Count",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
        {"MetricName": "ScannersRun", "Value": scanners_run, "Unit": "Count",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
        {"MetricName": "ScannerErrors", "Value": len(errors), "Unit": "Count",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
        {"MetricName": "HighSeverityFindings", "Value": by_severity["high"], "Unit": "Count",
         "Dimensions": [{"Name": "Region", "Value": LAMBDA_REGION}]},
    ]
    cw.put_metric_data(Namespace="CostOptimizer", MetricData=metric_data)


def build_html(findings: list, total_usd: float, scan_date: str,
               regions_scanned: list, scanners_run: int, errors: list) -> str:
    # Sort highest cost first so the biggest waste is seen without scrolling.
    sorted_findings = sorted(
        findings, key=lambda f: f.get("estimated_monthly_cost_usd") or 0, reverse=True
    )
    total_inr = round(total_usd * 84)

    rows = ""
    for f in sorted_findings:
        cost = f.get("estimated_monthly_cost_usd")
        cost_str = f"${cost:.2f}/mo" if cost else "cost unknown"
        sev = "high" if (cost or 0) >= 50 else ("medium" if (cost or 0) >= 10 else "low")
        sev_color = {"high": "#e53e3e", "medium": "#dd6b20", "low": "#718096"}[sev]
        rec = f.get("recommendation", "N/A")
        rows += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;">
            <span style="background:{sev_color};color:white;padding:2px 8px;
                         border-radius:4px;font-size:11px;text-transform:uppercase;">
              {sev}</span></td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-weight:bold;">
            {f['resource_type']}</td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-family:monospace;font-size:12px;">
            {f['resource_id']}</td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#718096;">
            {f.get('region', '')}</td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;">{f['reason']}</td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-weight:bold;">
            {cost_str}</td>
          <td style="padding:10px;border-bottom:1px solid #e2e8f0;font-size:13px;">{rec}</td>
        </tr>"""

    no_issues = "" if findings else """
        <div style="text-align:center;padding:40px;color:#48bb78;">
          <h2>✅ No waste found this week!</h2>
          <p>All scanners ran clean across every active region.</p></div>"""

    table = "" if not findings else f"""
    <table style="width:100%;border-collapse:collapse;">
      <thead><tr style="background:#edf2f7;">
        <th style="padding:10px;text-align:left;">Severity</th>
        <th style="padding:10px;text-align:left;">Type</th>
        <th style="padding:10px;text-align:left;">Resource ID</th>
        <th style="padding:10px;text-align:left;">Region</th>
        <th style="padding:10px;text-align:left;">Problem</th>
        <th style="padding:10px;text-align:left;">Cost</th>
        <th style="padding:10px;text-align:left;">🤖 AI Fix</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""

    error_note = "" if not errors else f"""
    <div style="margin:0 20px 20px;padding:12px;background:#fffaf0;
                border-left:4px solid #dd6b20;border-radius:4px;font-size:12px;">
      ⚠️ {len(errors)} scanner/region combination(s) failed and were skipped —
      results above may be incomplete. Check CloudWatch Logs for details.
    </div>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f7fafc;padding:20px;">
<div style="max-width:1200px;margin:0 auto;background:white;border-radius:8px;
            overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
  <div style="background:linear-gradient(135deg,#1a365d,#2b6cb0);padding:30px;color:white;">
    <h1 style="margin:0;font-size:22px;">⚡ AWS Cloud Cost Optimizer — Weekly Report</h1>
    <p style="margin:8px 0 0;opacity:0.8;">
      {scan_date} | {scanners_run} scanners | {len(regions_scanned)} region(s):
      {', '.join(sorted(regions_scanned)[:6])}{'...' if len(regions_scanned) > 6 else ''}</p>
  </div>
  {error_note}
  <div style="display:flex;gap:16px;padding:20px;background:#edf2f7;flex-wrap:wrap;">
    <div style="flex:1;min-width:140px;background:white;padding:20px;border-radius:8px;
                text-align:center;border-left:4px solid #e53e3e;">
      <div style="font-size:26px;font-weight:bold;color:#e53e3e;">${total_usd:.2f}</div>
      <div style="color:#718096;font-size:12px;">Monthly Waste (USD)</div>
    </div>
    <div style="flex:1;min-width:140px;background:white;padding:20px;border-radius:8px;
                text-align:center;border-left:4px solid #e53e3e;">
      <div style="font-size:26px;font-weight:bold;color:#e53e3e;">₹{total_inr}</div>
      <div style="color:#718096;font-size:12px;">Monthly Waste (INR)</div>
    </div>
    <div style="flex:1;min-width:140px;background:white;padding:20px;border-radius:8px;
                text-align:center;border-left:4px solid #3182ce;">
      <div style="font-size:26px;font-weight:bold;color:#3182ce;">{len(findings)}</div>
      <div style="color:#718096;font-size:12px;">Issues Found</div>
    </div>
    <div style="flex:1;min-width:140px;background:white;padding:20px;border-radius:8px;
                text-align:center;border-left:4px solid #38a169;">
      <div style="font-size:26px;font-weight:bold;color:#38a169;">{len(regions_scanned)}</div>
      <div style="color:#718096;font-size:12px;">Regions Scanned</div>
    </div>
  </div>
  <div style="padding:20px;">{no_issues}{table}</div>
  <div style="padding:16px;background:#edf2f7;text-align:center;color:#718096;font-size:12px;">
    <p>AWS Cost Optimizer AI | 10 scanners | Bedrock: {BEDROCK_MODEL}</p>
    <p>Runs every Monday 6 AM UTC</p>
  </div>
</div></body></html>"""


def send_ses_email(ses, subject: str, html_body: str):
    if not ALERT_EMAIL:
        logger.warning("ALERT_EMAIL not set — skipping email")
        return
    try:
        ses.send_email(
            Source=ALERT_EMAIL,
            Destination={"ToAddresses": [ALERT_EMAIL]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
        logger.info("Email sent to %s", ALERT_EMAIL)
    except Exception as exc:
        logger.error("SES failed: %s", exc)


def handler(event, context):
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("Weekly report started — run_id=%s", run_id)

    regions = get_regions_to_scan(SCAN_REGIONS_ENV)
    logger.info("Resolved %d region(s) to scan: %s", len(regions), regions)

    # This single call replaces the old sequential 6-scanner,
    # region-by-region loop. All 10 scanners x all active regions run
    # concurrently here, bounded by settings.max_parallel_regions and
    # settings.max_parallel_scanners_per_region.
    scan_result = run_all_scanners(regions)
    findings = scan_result["findings"]
    errors = scan_result["errors"]

    logger.info(
        "Scan complete in %.2fs - %d findings, %d errors, %d scanners, %d regions",
        scan_result["duration_seconds"], len(findings), len(errors),
        scan_result["scanners_run"], scan_result["regions_scanned"],
    )

    bedrock = boto3.client("bedrock-runtime", region_name=LAMBDA_REGION)
    for f in findings:
        f["recommendation"] = get_ai_recommendation(
            bedrock, f["resource_type"], f["resource_id"], f["reason"]
        )

    dynamodb = boto3.resource("dynamodb", region_name=LAMBDA_REGION)
    save_to_dynamodb(dynamodb, findings, run_id)

    cw = boto3.client("cloudwatch", region_name=LAMBDA_REGION)
    publish_metrics(cw, findings, len(regions), scan_result["scanners_run"], errors)

    total_usd = sum(f.get("estimated_monthly_cost_usd") or 0 for f in findings)
    subject = (
        f"[Cost Optimizer] {len(findings)} issue(s) across {len(regions)} region(s) — "
        f"${total_usd:.2f}/mo | {scan_date[:10]}"
        if findings else
        f"[Cost Optimizer] ✅ Clean — 0 issues across {len(regions)} region(s) | {scan_date[:10]}"
    )
    html = build_html(findings, total_usd, scan_date, regions, scan_result["scanners_run"], errors)

    ses = boto3.client("ses", region_name=LAMBDA_REGION)
    send_ses_email(ses, subject, html)

    logger.info("Run complete — run_id=%s", run_id)
    return {
        "run_id": run_id,
        "regions_scanned": len(regions),
        "scanners_run": scan_result["scanners_run"],
        "findings_count": len(findings),
        "errors_count": len(errors),
        "total_waste_usd": round(total_usd, 2),
        "duration_seconds": scan_result["duration_seconds"],
    }
