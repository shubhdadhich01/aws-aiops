"""
runner.py — the ambient audit orchestrator.

Deployed by Day 10's Terraform as an AWS Lambda function, invoked by
EventBridge on the schedule named in variables.tf. Its job is small and
completely mechanical: import each configured day's audit module, run its
checks against the account it lives in, and write one JSON report per
invocation to the S3 archive bucket named in the environment.

    ARCHIVE_BUCKET      required. The S3 bucket the report lands in.
    REGION              required. The region the audits scan.
    ACCOUNT_PROFILE     optional. If set, boto3 uses this profile. On Lambda
                        the default (unset) is right — the runtime credentials
                        are the ones the function's IAM role provides.
    ENABLED_DAYS        optional comma-separated list. Defaults to "09" to
                        avoid making the ambient audit depend on modules that
                        may not yet be present in the deployment package.

The runner does NOT implement the checks itself. It imports each day's
`<name>_audit.py` module by convention, calls a `run_audit(region, profile)`
entry point that returns a list of Finding dicts, and merges them into one
report with the invocation timestamp.

If a module is missing, the runner logs the miss and continues. The whole
runner is under 200 lines by design: the ambient audit is a shell, and the
value is the imported modules, not the shell.

Note on scope: this file is deliberately minimal for CP1. It is a working
stub — enough for Terraform to zip it, upload it, and for the Lambda to
respond to invocations. Real audit-module imports and richer report shape
are added in CP2 alongside the finding contract and the CAP checks.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _archive_key(day: str, invoked_at: datetime) -> str:
    """Partitioned S3 key: reports/day=NN/year=YYYY/month=MM/day=DD/<ts>.json.

    Athena's default partition scheme uses key=value in the path; keeping
    the columns in the key means the external table CREATE for CAP-011 can
    use PARTITIONED BY on them without a crawler.
    """
    return (
        f"reports/day={day}/year={invoked_at.year:04d}/"
        f"month={invoked_at.month:02d}/day={invoked_at.day:02d}/"
        f"{invoked_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )


def _import_audit_module(day: str) -> Optional[Any]:
    """Import <name>_audit for the given day, if it is on sys.path.

    The Lambda deployment package can carry any subset of the Day 01–09
    audit modules under a `modules/` prefix. In CP1 this returns None for
    every request; the actual module wiring is CP2 work.
    """
    module_names = {
        "01": "day01_audit",
        "02": "day02_audit",
        "03": "day03_audit",
        "04": "day04_audit",
        "05": "day05_audit",
        "06": "day06_audit",
        "07": "day07_audit",
        "08": "dr_audit",
        "09": "cost_audit",
    }
    name = module_names.get(day)
    if not name:
        return None
    try:
        return __import__(name)
    except ImportError:
        return None


def _run_one_audit(day: str, region: str, profile: Optional[str]) -> Dict[str, Any]:
    """Run one day's audit and return a report envelope.

    The envelope keeps the metadata alongside the findings so a single
    S3 object is self-describing when read from Athena.
    """
    module = _import_audit_module(day)
    invoked_at = datetime.now(timezone.utc)

    envelope: Dict[str, Any] = {
        "runner_version": "1.0.0",
        "day": day,
        "invoked_at": invoked_at.isoformat(),
        "region": region,
        "profile": profile or "(lambda-role)",
        "module_present": module is not None,
        "findings": [],
        "score": None,
        "grade": None,
        "error": None,
    }

    if module is None:
        envelope["error"] = f"module for day {day} not present in deployment package"
        return envelope

    # CP2 wires each module's run_audit entry point in here. For CP1 we
    # simply record the module import succeeded.
    envelope["findings"] = []
    envelope["score"] = 100
    envelope["grade"] = "A — module present, checks not yet wired"
    return envelope


def _write_report(bucket: str, day: str, envelope: Dict[str, Any]) -> str:
    s3 = boto3.client("s3")
    invoked_at = datetime.fromisoformat(envelope["invoked_at"])
    key = _archive_key(day, invoked_at)
    body = json.dumps(envelope, default=str).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    return key


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda entry point.

    Event shape (from EventBridge scheduled rule):
        {}                              # scheduled invocation, run all enabled days
        {"day": "09"}                   # ad hoc: run one day only
        {"days": ["03", "08", "09"]}    # ad hoc: run several

    Returns a summary object with the S3 keys of the reports written and
    a top-level status. The summary is what CloudWatch Logs captures per
    invocation, so a log-based dashboard can read it directly.
    """
    logger.info("audit runner invocation: %s", json.dumps(event or {}, default=str))

    bucket = os.environ.get("ARCHIVE_BUCKET")
    region = os.environ.get("REGION", "us-east-1")
    profile = os.environ.get("ACCOUNT_PROFILE") or None
    enabled = os.environ.get("ENABLED_DAYS", "09").split(",")

    if not bucket:
        logger.error("ARCHIVE_BUCKET env var not set")
        return {"status": "error", "reason": "ARCHIVE_BUCKET not set"}

    days_to_run: List[str] = []
    if isinstance(event, dict) and event.get("day"):
        days_to_run = [str(event["day"])]
    elif isinstance(event, dict) and event.get("days"):
        days_to_run = [str(d) for d in event["days"]]
    else:
        days_to_run = [d.strip() for d in enabled if d.strip()]

    reports: List[Dict[str, Any]] = []
    errors: List[str] = []

    for day in days_to_run:
        try:
            envelope = _run_one_audit(day, region, profile)
            key = _write_report(bucket, day, envelope)
            reports.append({
                "day": day,
                "key": key,
                "finding_count": len(envelope.get("findings") or []),
                "score": envelope.get("score"),
                "module_present": envelope.get("module_present"),
            })
        except Exception as exc:  # noqa: BLE001 - Lambda catches everything
            logger.exception("audit for day %s failed", day)
            errors.append(f"day {day}: {exc}")

    summary = {
        "status": "ok" if not errors else "partial",
        "invoked_at": _now_iso(),
        "days_run": days_to_run,
        "reports": reports,
        "errors": errors,
    }
    logger.info("audit runner summary: %s", json.dumps(summary, default=str))
    return summary


if __name__ == "__main__":
    # For local testing: python runner.py 09
    sys.exit(0)
