#!/usr/bin/env python3
"""
capstone_audit_challenge.py — Day 10 capstone auditor, for you to finish.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

GENERATED FROM capstone_audit.py. Identical imports, identical Finding, identical
helpers, identical renderers, identical collector, identical CLI. Sixteen check
bodies have been removed and their DOCSTRINGS LEFT IN PLACE, because the
docstring is the specification. Read it before you write anything.

    cd lab/python
    CAPSTONE_AUDIT_MODULE=capstone_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests -v

47 tests. They need no AWS credentials, because every check is a pure function
over a plain dict. Aim for all 47 green; get there one CHECKPOINT at a time.

Roughly 2-3 HOURS if you work through it in order. The long ones are CAP-006
(the cross-day ARN correlation), CAP-007 (finding-normalisation drift across
consecutive reports), CAP-012 (parsing suppressions and comparing review_by
dates against the injected clock), and CAP-016 (the CRITICAL check that is
the day's thesis and whose logic is the smallest, cleanly separating the
mechanism from the message).

-----------------------------------------------------------------------------
WHICH CHECKS ARE NOT INDEPENDENT
-----------------------------------------------------------------------------
Six relationships. Writing them down is what stops them reading as bugs when a
test fails for a reason that is not in the check you just wrote.

  CAP-001 AND CAP-003 LOOK LIKE THE SAME CHECK. They are not. CAP-001 asks
  whether a schedule EXISTS AT ALL. CAP-003 asks whether an existing schedule
  has STOPPED FIRING (last report age > interval * 1.5). Fixing CAP-001 by
  creating a rule and then never verifying it fires is exactly what people
  do, and CAP-003 is the transition catcher. In STATE A CAP-001 fires and
  CAP-003 is silent; in STATE C CAP-001 is silent and CAP-003 fires. The two
  sides of the same governance failure at two different lifecycles.

  CAP-003 AND CAP-016 ARE THE SAME PATTERN AT TWO LAYERS. CAP-003 asks
  "did the runner run"; CAP-016 asks "did anyone read what it produced".
  Both are activity checks. A programme where CAP-003 is silent and CAP-016
  is firing is one where the runner is doing its job and nobody is doing
  theirs. Neither remediates the other.

  CAP-004 AND CAP-005 FIRE ON THE SAME BUCKET at different angles.
  CAP-004 (versioning) is the "can I answer WHEN did this finding first
  appear" question. CAP-005 (lifecycle) is the "will this archive cost me
  money forever" question. Same resource, different remediations, both
  toggled by separate variables in terraform.tfvars.

  CAP-008 AND CAP-012 ARE A LIFECYCLE. CAP-008 fires when there is no
  suppression file at all. CAP-012 fires when the exceptions are present
  but stale. On a mature account, CAP-008 fires briefly after the first
  audit and never again; CAP-012 fires on cadence as review_by dates
  expire. If your CAP-012 fires on a review_by that is IN THE FUTURE, you
  compared the sign wrong.

  CAP-009 AND CAP-016 ARE SYMMETRIC ACROSS LAYERS. CAP-009 is technical
  (runner errors silently, no alarm). CAP-016 is organisational (reports
  succeed, nobody reads). Both are "the loop is broken" faults. CAP-009
  is HIGH; CAP-016 is CRITICAL because the organisational failure is
  strictly worse than the technical one.

  CAP-016 FIRES ONCE PER UNREAD REPORT, DELIBERATELY NOT DEDUPLICATED.
  Each report is a separately owned acknowledgement decision (or non-
  decision). If your CAP-016 returns one finding for an archive with 47
  unread reports, you deduplicated. STATE C's expected count is FOUR (one
  per weekly report unread over a month).

  CAP-013 AND CAP-014 ARE SILENT BY DESIGN against this stack and must
  stay silent against every fixture that uses the base_stack() fixture.
  If they fire, either you read sla_days_by_severity in a shape that is
  not what the object literal produces (CAP-013), or you set
  reference_arch_enabled = True in the fixture (CAP-014). The 47 tests
  include explicit silent-by-design invariants for both.

-----------------------------------------------------------------------------
FILE LAYOUT
-----------------------------------------------------------------------------
Above the check functions: imports, Finding, paginate helper, the constants
(RT_*, SEVERITY_*, REQUIRED_SLA_KEYS), the shared derivations (_now,
_parse_time, _age_days, _humanise_days, _parse_schedule_interval_days,
_reports_by_day).
YOU DO NOT NEED TO CHANGE ANY OF THIS. They are complete.

Below the check functions: CHECKS registry, RUNTIME_CHECKS list, scoring
functions, renderers, CapstoneAuditor collector, CLI. All complete.

The sixteen check functions are the whole exercise.
"""

#!/usr/bin/env python3

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # pragma: no cover
    print(
        "boto3 is not installed. Run:  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


###############################################################################
# Severity weights — identical to Days 03 through 09
###############################################################################

SEVERITY_WEIGHTS: Dict[str, int] = {
    "CRITICAL": 25,
    "HIGH": 10,
    "MEDIUM": 4,
    "LOW": 1,
    "INFO": 0,
}

SEVERITY_ORDER: List[str] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

_COLOURS = {
    "CRITICAL": "\033[1;91m",
    "HIGH": "\033[1;31m",
    "MEDIUM": "\033[1;33m",
    "LOW": "\033[1;36m",
    "INFO": "\033[1;90m",
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "GREEN": "\033[1;32m",
}


def colour(text: str, key: str, enabled: bool = True) -> str:
    if not enabled or key not in _COLOURS:
        return text
    return f"{_COLOURS[key]}{text}{_COLOURS['RESET']}"


###############################################################################
# Finding
###############################################################################


@dataclass
class Finding:
    """A single audit finding.

    check_id     Stable identifier (CAP-001 ...). Never renumber - dashboards
                 and suppressions are written against these.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken.
    title        One line, imperative.
    detail       What was observed, with real values.
    remediation  Concrete fix.
    evidence     Raw values so the finding is auditable without re-querying.
    region       Region of the resource. Account-scoped findings carry "".
    """

    check_id: str
    severity: str
    resource_type: str
    resource_id: str
    title: str
    detail: str
    remediation: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    region: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_WEIGHTS:
            raise ValueError(
                f"{self.check_id}: unknown severity {self.severity!r}. "
                f"Expected one of {', '.join(SEVERITY_ORDER)}."
            )

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHTS[self.severity]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


###############################################################################
# Paginator helper
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Any]:
    items: List[Any] = []
    try:
        if client.can_paginate(operation):
            paginator = client.get_paginator(operation)
            for page in paginator.paginate(**kwargs):
                items.extend(page.get(result_key, []) or [])
        else:
            response = getattr(client, operation)(**kwargs)
            items.extend(response.get(result_key, []) or [])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            print(
                f"  ! Access denied calling {operation}. Skipping the checks "
                f"that depend on it.",
                file=sys.stderr,
            )
            return []
        raise
    return items


###############################################################################
# Constants
###############################################################################

RT_ACCOUNT = "AWS::Account"
RT_SCHEDULE = "AWS::Events::Rule"
RT_LAMBDA = "AWS::Lambda::Function"
RT_BUCKET = "AWS::S3::Bucket"
RT_SUPPRESSION = "Capstone::Suppression"
RT_ALARM = "AWS::CloudWatch::Alarm"
RT_DASHBOARD = "AWS::CloudWatch::Dashboard"
RT_ATHENA = "AWS::Athena::Database"
RT_REPORT = "Capstone::Report"
RT_RESOURCE = "AWS::Resource"

# Default SLA required-keys. Any of these missing in stack["sla_days_by_severity"]
# fires CAP-013.
REQUIRED_SLA_KEYS: Set[str] = {"critical", "high", "medium", "low"}


###############################################################################
# Shared derivations
###############################################################################


def _now(stack: Dict[str, Any]) -> datetime:
    value = stack.get("now")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        return _parse_time(value) or datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_days(value: Any, now: datetime) -> Optional[float]:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 86400.0


def _humanise_days(days: Optional[float]) -> str:
    if days is None:
        return "unknown"
    if days < 1:
        hours = days * 24
        return f"{hours:.1f} hours"
    if days < 90:
        return f"{days:.0f} days"
    if days < 730:
        return f"{days / 30:.1f} months"
    return f"{days / 365:.1f} years"


def _parse_schedule_interval_days(expression: Any) -> Optional[float]:
    """Parse an EventBridge rate() expression into days.

    Accepts rate(N unit) where unit is minute(s), hour(s), day(s). Anything
    else — including cron() — returns None, which propagates through
    CAP-002 as "cannot evaluate interval".
    """
    text = str(expression or "").strip().lower()
    if not text.startswith("rate(") or not text.endswith(")"):
        return None
    inner = text[5:-1].strip()
    parts = inner.split()
    if len(parts) != 2:
        return None
    try:
        n = float(parts[0])
    except ValueError:
        return None
    unit = parts[1]
    if unit.startswith("minute"):
        return n / 1440.0
    if unit.startswith("hour"):
        return n / 24.0
    if unit.startswith("day"):
        return n
    return None


def _reports_by_day(stack: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Group archived reports by day-of-audit ID (not the calendar day).

    A report envelope's `day` field is the two-digit ID of the audit
    module that produced it — "01" through "09" from the earlier bootcamp
    days. The archive can carry reports from many days across many
    invocations.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in stack.get("reports") or []:
        day = str(r.get("day") or "??")
        out.setdefault(day, []).append(r)
    return out


###############################################################################
# CHECKS
###############################################################################


def check_no_schedule(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-001 - no EventBridge schedule targets the audit-runner.

    The whole day is about ambient audit. Without a schedule, the runner
    is a Lambda function that exists and is never invoked - which is
    every organisation's default state.

    Distinct from CAP-003 (schedule exists but has silently stopped) in
    the same way COST-001 (no budget) was distinct from COST-002 (budget
    without notification): the ABSENCE vs the DECORATIVE-PRESENCE.
    """
    # =======================================================================
    # TODO 1 of 16 — CAP-001
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-001 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-001"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_schedule_too_infrequent(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-002 - schedule interval > 7 days.

    Weekly is the honest floor for most organisations. Anything longer
    means at least a week of silent regression can happen before the
    next audit reveals it. Silent by situation in STATE A because no
    schedule exists to have an interval.
    """
    # =======================================================================
    # TODO 2 of 16 — CAP-002
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-002 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-002"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_scheduler_silent(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-003 - the last audit invocation is older than interval * 1.5.

    The scheduler exists but has silently stopped firing (or is firing
    into errors that never write reports). This is the STATE C
    manifestation - it fires against an unchanged configuration when
    time passes and nobody notices.
    """
    # =======================================================================
    # TODO 3 of 16 — CAP-003
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-003 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-003"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_archive_not_versioned(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-004 - the audit-report archive bucket is not versioned.

    Without versioning, a report that gets overwritten is gone forever.
    "When did this finding first appear" becomes unanswerable.
    """
    # =======================================================================
    # TODO 4 of 16 — CAP-004
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-004 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-004"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_archive_no_lifecycle(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-005 - the audit-report archive has no lifecycle rule.

    Reports accumulate. Without a lifecycle rule they stay in STANDARD
    forever and the archive itself becomes exactly what Day 09's
    COST-014 was catching one day earlier.
    """
    # =======================================================================
    # TODO 5 of 16 — CAP-005
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-005 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-005"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_cross_cutting_risk(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-006 - the same resource ARN appears in findings from >= 2 prior days.

    A resource with a Day 03 IAM overshare AND a Day 08 no-backup finding
    is a resource with cross-cutting risk: remediating one leaves the
    other, and neither team owns the whole picture. This check surfaces
    such resources.

    Silent by situation on the reference architecture (which has 0
    findings per prior day). Silent by situation on a fresh archive
    (nothing to correlate). Fires readily on a real account.
    """
    # =======================================================================
    # TODO 6 of 16 — CAP-006
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-006 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-006"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_finding_dedup(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-007 - the same finding appears in consecutive reports with a
    different resource_id normalisation.

    Fires when the auditor's finding-emission is not stable across runs:
    the same fault reported yesterday as `i-abcd1234` and today as
    `arn:aws:ec2:us-east-1:123:instance/i-abcd1234`. Suppressions written
    against one form don't match the other. Silent by situation with
    fewer than 2 reports per day.
    """
    # =======================================================================
    # TODO 7 of 16 — CAP-007
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-007 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-007"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_no_suppressions_file(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-008 - no baseline suppression file present in the archive.

    An audit programme without a suppressions file is one where every
    ignored finding is un-tracked. There is always something the org
    has decided to accept - a specific IAM policy that will get fixed
    "next quarter", a specific bucket whose lifecycle is intentional,
    a specific Lambda whose retention will be set on the next release.
    Track those explicitly or watch them recur every audit.
    """
    # =======================================================================
    # TODO 8 of 16 — CAP-008
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-008 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-008"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_no_lambda_alarm(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-009 - no CloudWatch alarm on the runner Lambda's error metric.

    Symmetric with CAP-016 at a different layer: CAP-009 catches
    'the runner errors silently'; CAP-016 catches 'the runner works
    but nobody reads its output'. A programme where both fire is a
    programme that isn't really running.
    """
    # =======================================================================
    # TODO 9 of 16 — CAP-009
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-009 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-009"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_no_dashboard(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-010 - no CloudWatch dashboard exists for the audit programme.

    The dashboard is not analytics. It is the one URL a stakeholder
    clicks to answer 'is the audit programme working today'. Without
    it, that question requires querying CloudWatch metrics by hand, or
    knowing the API - both of which mean the answer never actually gets
    asked.
    """
    # =======================================================================
    # TODO 10 of 16 — CAP-010
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-010 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-010"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_no_athena_table(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-011 - no Athena database exists over the archive.

    Without a queryable historical layer, 'how many DR-008 findings did
    we have in Q1' requires downloading months of JSON and processing
    them locally. That query never gets run, which means historical
    trends are opaque, which means quarterly reviews devolve into
    'compare the latest score to the last one', which is not a review.
    """
    # =======================================================================
    # TODO 11 of 16 — CAP-011
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-011 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-011"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_suppressions_stale(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-012 - suppression entries older than suppression_review_days
    without a fresh review.

    An exception with a story becomes an exception without a story
    becomes an exception nobody remembers, on a schedule of about 90
    days. This check catches that transition. Silent when no
    suppressions exist (STATE A); fires as review dates pass (STATE C).
    """
    # =======================================================================
    # TODO 12 of 16 — CAP-012
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-012 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-012"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_sla_not_defined(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-013 - the SLA per severity is not fully defined.

    SILENT BY DESIGN against this stack: sla_days_by_severity is a typed
    object with all four keys required by the variable's type constraint,
    plus a validation ensuring monotonic ordering. Fires readily on
    stacks that pass a bare dict.
    """
    # =======================================================================
    # TODO 13 of 16 — CAP-013
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-013 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-013"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_reference_arch_drift(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-014 - the reference architecture no longer scores 100/100.

    Fires per prior-day audit that produces > 0 findings when pointed at
    the reference-arch resources. SILENT BY DESIGN when
    enable_reference_arch is false (nothing claims to be reference).
    """
    # =======================================================================
    # TODO 14 of 16 — CAP-014
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-014 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-014"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_reports_lack_git_remote(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-015 - reports in the archive lack a git_remote metadata field.

    A report is a claim about the account state at a moment in time. If
    it does not carry an indication of WHICH version of the auditor
    produced it, historical comparison is impossible - a finding that
    'appeared' in October may just be a new check added in October.
    LOW because the failure mode is knowledge-loss, not immediate
    breakage.
    """
    # =======================================================================
    # TODO 15 of 16 — CAP-015
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-015 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-015"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings

def check_report_unread(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-016 - the latest report has been open past report_unread_days
    without acknowledgement.

    THE DAY'S THESIS. Symmetric with COST-016 one day earlier: COST-016
    caught 'nobody reads AWS's cost anomalies', CAP-016 catches 'nobody
    reads YOUR audit reports'. A report is 'acknowledged' when the S3
    object carries an object tag `Acknowledged=true` or when a
    corresponding acknowledgement record exists in the archive.

    Silent by situation in STATE A (no reports); fires readily in
    STATE C (reports pile up unread).
    """
    # =======================================================================
    # TODO 16 of 16 — CAP-016
    # =======================================================================
    #
    # READ the docstring above. It is the specification for CAP-016 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in capstone_audit.py's other checks for
    # the shape:
    #     check_id="CAP-016"
    #     severity=... (from the docstring)
    #     resource_type=RT_...  (already defined at module scope)
    #     resource_id=the AWS id, or ARN, or name
    #     title=one imperative line
    #     detail=names the concrete values you observed
    #     remediation=exact CLI or Terraform to fix it
    #     evidence={dict of the fields you looked at}
    #     region=the region argument ("" for account-global findings)
    #
    # When the test for this check passes AND the whole-stack total is
    # still wrong, look at the checks it interacts with (see the top of
    # this file), not at this one.
    # =======================================================================

    findings: List[Finding] = []
    return findings



CHECKS = [
    ("CAP-001", check_no_schedule),
    ("CAP-002", check_schedule_too_infrequent),
    ("CAP-003", check_scheduler_silent),
    ("CAP-004", check_archive_not_versioned),
    ("CAP-005", check_archive_no_lifecycle),
    ("CAP-006", check_cross_cutting_risk),
    ("CAP-007", check_finding_dedup),
    ("CAP-008", check_no_suppressions_file),
    ("CAP-009", check_no_lambda_alarm),
    ("CAP-010", check_no_dashboard),
    ("CAP-011", check_no_athena_table),
    ("CAP-012", check_suppressions_stale),
    ("CAP-013", check_sla_not_defined),
    ("CAP-014", check_reference_arch_drift),
    ("CAP-015", check_reports_lack_git_remote),
    ("CAP-016", check_report_unread),
]

LIVE_CHECKS = [check_id for check_id, _ in CHECKS]

# Checks that read time-dependent state. Their answer changes with the
# passage of time even when nothing else changes. STATE C's three findings
# all live here.
RUNTIME_CHECKS = ["CAP-003", "CAP-012", "CAP-015", "CAP-016"]


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Expect 53/100 against this lab's stack when all enable_* toggles are
    off. Eight findings, 47 points. Turn on the six free-to-cheap
    guardrails and the score climbs to 100. Come back 30 days later
    without changing anything else and the score drops to 55 because
    three time-dependent findings appear.

    STATE C is worse than STATE A. That is the whole day.
    """
    score = 100 - sum(f.weight for f in findings)
    return max(0, score)


def score_grade(score: int) -> str:
    if score >= 90:
        return "A — production-ready"
    if score >= 75:
        return "B — solid, minor gaps"
    if score >= 60:
        return "C — real compliance gaps"
    if score >= 40:
        return "D — would fail an audit"
    return "F — do not point this at production data"


###############################################################################
# Output formats
###############################################################################


def filter_by_severity(findings: List[Finding], min_severity: str) -> List[Finding]:
    cutoff = SEVERITY_ORDER.index(min_severity)
    return [f for f in findings if SEVERITY_ORDER.index(f.severity) <= cutoff]


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "\u2026"


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            if current:
                lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def render_table(
    findings: List[Finding], stats: Dict[str, int], score: int, use_colour: bool
) -> str:
    out = io.StringIO()
    w = out.write

    bar = "=" * 100
    w(f"\n{bar}\n")
    w(colour("  CAPSTONE AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 10 · Composition & Continuity\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('schedule_rules', 0)} schedule rule(s) · "
        f"{stats.get('reports', 0)} archived report(s) · "
        f"{stats.get('suppressions', 0)} suppression entr(y/ies) · "
        f"{stats.get('cloudwatch_alarms', 0)} alarm(s) · "
        f"{stats.get('cloudwatch_dashboards', 0)} dashboard(s) · "
        f"{stats.get('athena_databases', 0)} Athena db(s)\n\n"
    )

    if not findings:
        w(
            colour(
                "  No findings. Nothing to fix at this severity level.\n\n",
                "GREEN",
                use_colour,
            )
        )
    else:
        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for f in findings:
            counts[f.severity] += 1

        w("  " + "-" * 96 + "\n")
        w(f"  {'SEVERITY':<10} {'CHECK':<10} {'RESOURCE':<33} {'FINDING':<40}\n")
        w("  " + "-" * 96 + "\n")

        ordered = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.index(f.severity), f.check_id, f.resource_id),
        )

        for f in ordered:
            sev = colour(f"{f.severity:<10}", f.severity, use_colour)
            w(
                f"  {sev} {f.check_id:<10} "
                f"{_truncate(f.resource_id, 32):<33} "
                f"{_truncate(f.title, 40):<40}\n"
            )

        w("  " + "-" * 96 + "\n\n")

        w(colour("  DETAIL\n\n", "BOLD", use_colour))
        for i, f in enumerate(ordered, 1):
            w(
                f"  {i:>2}. [{colour(f.severity, f.severity, use_colour)}] "
                f"{f.check_id} — {f.title}\n"
            )
            w(f"      Resource   : {f.resource_type} / {f.resource_id}\n")
            if f.region:
                w(f"      Region     : {f.region}\n")
            for line in _wrap(f.detail, 88):
                w(f"      {line}\n")
            w(f"      {colour('Fix', 'GREEN', use_colour)}        : ")
            fix_lines = _wrap(f.remediation, 84)
            w(f"{fix_lines[0] if fix_lines else ''}\n")
            for line in fix_lines[1:]:
                w(f"                   {line}\n")
            w("\n")

        w("  " + "-" * 96 + "\n")
        summary = "  ".join(
            f"{colour(sev, sev, use_colour)}: {counts[sev]}" for sev in SEVERITY_ORDER
        )
        w(f"  {summary}\n")

    w("  " + "-" * 96 + "\n")
    grade = score_grade(score)
    score_key = "GREEN" if score >= 75 else ("MEDIUM" if score >= 50 else "CRITICAL")
    w(
        f"  COMPLIANCE SCORE: "
        f"{colour(str(score) + '/100', score_key, use_colour)}   {grade}\n"
    )
    w(f"{bar}\n\n")

    return out.getvalue()


def render_json(findings: List[Finding], stats: Dict[str, int], score: int) -> str:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] += 1

    payload = {
        "audit": "capstone_audit",
        "day": "10",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
        "runtime_dependent_checks": RUNTIME_CHECKS,
        "findings": [f.to_dict() for f in findings],
    }
    return json.dumps(payload, indent=2, default=str)


def render_csv(findings: List[Finding]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "check_id",
            "severity",
            "weight",
            "resource_type",
            "resource_id",
            "region",
            "title",
            "detail",
            "remediation",
            "evidence",
        ]
    )
    for f in sorted(
        findings, key=lambda x: (SEVERITY_ORDER.index(x.severity), x.check_id)
    ):
        writer.writerow(
            [
                f.check_id,
                f.severity,
                f.weight,
                f.resource_type,
                f.resource_id,
                f.region,
                f.title,
                f.detail,
                f.remediation,
                json.dumps(f.evidence, default=str),
            ]
        )
    return out.getvalue()


###############################################################################
# Auditor
###############################################################################


class CapstoneAuditor:
    """Collects one normalised snapshot of the ambient audit programme, then
    runs pure checks."""

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        name_prefix: str = "cbc-day10",
        archive_bucket: Optional[str] = None,
        runner_function_name: Optional[str] = None,
        suppression_review_days: int = 90,
        report_unread_days: int = 7,
        sla_days_by_severity: Optional[Dict[str, int]] = None,
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.name_prefix = name_prefix
        self.archive_bucket = archive_bucket
        self.runner_function_name = runner_function_name or f"{name_prefix}-runner"
        self.suppression_review_days = suppression_review_days
        self.report_unread_days = report_unread_days
        self.sla_days_by_severity = sla_days_by_severity or {
            "critical": 1, "high": 3, "medium": 7, "low": 30
        }
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stack: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "schedule_rules": 0,
            "reports": 0,
            "suppressions": 0,
            "cloudwatch_alarms": 0,
            "cloudwatch_dashboards": 0,
            "athena_databases": 0,
        }

        self.session: Any = None
        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.events = self.session.client("events")
            self.lambda_ = self.session.client("lambda")
            self.s3 = self.session.client("s3")
            self.cw = self.session.client("cloudwatch")
            self.athena = self.session.client("athena")
            self.sts = self.session.client("sts")
        except (BotoCoreError, NoCredentialsError) as exc:
            self.log(f"  ! No AWS session ({exc}).")
            self.session = None

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, file=sys.stderr)

    def _swallow(self, op: str, res: str, exc: ClientError) -> None:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in (
            "ResourceNotFoundException",
            "ResourceNotFound",
            "AccessDeniedException",
            "NoSuchLifecycleConfiguration",
            "NoSuchBucket",
            "404",
            "ValidationException",
        ):
            return
        self.log(f"  ! {op} failed for {res}: {code}")

    def _collect_schedule(self, stack: Dict[str, Any]) -> None:
        try:
            rules = paginate(self.events, "list_rules", "Rules")
        except ClientError as exc:
            self._swallow("list_rules", "account", exc)
            rules = []
        matched = None
        targets: List[Dict[str, Any]] = []
        for rule in rules:
            if not str(rule.get("Name", "")).startswith(self.name_prefix):
                continue
            try:
                rule_targets = paginate(
                    self.events, "list_targets_by_rule", "Targets", Rule=rule["Name"]
                )
            except ClientError as exc:
                self._swallow("list_targets_by_rule", rule["Name"], exc)
                rule_targets = []
            # Consider a rule that targets our Lambda our schedule.
            for t in rule_targets:
                if self.runner_function_name in str(t.get("Arn", "")):
                    matched = rule
                    targets = rule_targets
                    break
            if matched:
                break
        stack["schedule_rule"] = matched
        stack["schedule_targets"] = targets
        self.stats["schedule_rules"] = 1 if matched else 0

    def _collect_archive(self, stack: Dict[str, Any]) -> None:
        if not self.archive_bucket:
            stack["archive_bucket"] = None
            stack["archive_versioning"] = "unset"
            stack["archive_lifecycle_rules"] = []
            stack["suppressions_file_present"] = False
            stack["suppressions"] = []
            stack["reports"] = []
            return

        stack["archive_bucket"] = self.archive_bucket

        try:
            v = self.s3.get_bucket_versioning(Bucket=self.archive_bucket)
            stack["archive_versioning"] = str(v.get("Status", "unset"))
        except ClientError as exc:
            self._swallow("get_bucket_versioning", self.archive_bucket, exc)
            stack["archive_versioning"] = "unset"

        try:
            lc = self.s3.get_bucket_lifecycle_configuration(Bucket=self.archive_bucket)
            stack["archive_lifecycle_rules"] = lc.get("Rules", [])
        except ClientError as exc:
            self._swallow("get_bucket_lifecycle_configuration", self.archive_bucket, exc)
            stack["archive_lifecycle_rules"] = []

        # Suppressions file.
        try:
            body = self.s3.get_object(Bucket=self.archive_bucket, Key="suppressions.yaml")["Body"].read()
            stack["suppressions_file_present"] = True
            # Very light YAML parsing: skip the yaml dep, parse only what
            # our template shape requires.
            entries = _parse_suppressions_body(body.decode("utf-8"))
            stack["suppressions"] = entries
            self.stats["suppressions"] = len(entries)
        except ClientError as exc:
            self._swallow("get_object suppressions.yaml", self.archive_bucket, exc)
            stack["suppressions_file_present"] = False
            stack["suppressions"] = []

        # Reports: list objects under reports/, download JSON, parse.
        reports: List[Dict[str, Any]] = []
        try:
            keys = paginate(
                self.s3, "list_objects_v2", "Contents", Bucket=self.archive_bucket, Prefix="reports/"
            )
        except ClientError as exc:
            self._swallow("list_objects_v2", self.archive_bucket, exc)
            keys = []

        for entry in keys:
            key = entry.get("Key")
            if not key or not str(key).endswith(".json"):
                continue
            try:
                body = self.s3.get_object(Bucket=self.archive_bucket, Key=key)["Body"].read()
                doc = json.loads(body)
                if isinstance(doc, dict):
                    doc["key"] = key
                    # Acknowledgement lives in object tags.
                    try:
                        tags = self.s3.get_object_tagging(Bucket=self.archive_bucket, Key=key).get("TagSet", [])
                        for t in tags:
                            if t.get("Key") == "Acknowledged" and str(t.get("Value")).lower() == "true":
                                doc["acknowledged"] = True
                                break
                    except ClientError:
                        pass
                    reports.append(doc)
            except (ClientError, ValueError, json.JSONDecodeError) as exc:
                self.log(f"  ! could not parse report {key}: {exc}")

        stack["reports"] = reports
        self.stats["reports"] = len(reports)

    def _collect_observability(self, stack: Dict[str, Any]) -> None:
        try:
            alarms = paginate(self.cw, "describe_alarms", "MetricAlarms")
        except ClientError as exc:
            self._swallow("describe_alarms", "account", exc)
            alarms = []
        stack["cloudwatch_alarms"] = alarms
        self.stats["cloudwatch_alarms"] = len(alarms)

        try:
            dashboards = paginate(self.cw, "list_dashboards", "DashboardEntries")
        except ClientError as exc:
            self._swallow("list_dashboards", "account", exc)
            dashboards = []
        stack["cloudwatch_dashboards"] = dashboards
        self.stats["cloudwatch_dashboards"] = len(dashboards)

        try:
            work_groups = paginate(self.athena, "list_work_groups", "WorkGroups")
            databases = []
            catalog = "AwsDataCatalog"
            try:
                databases = paginate(
                    self.athena, "list_databases", "DatabaseList", CatalogName=catalog
                )
            except ClientError as exc:
                self._swallow("list_databases", catalog, exc)
                databases = []
        except ClientError as exc:
            self._swallow("list_work_groups", "account", exc)
            databases = []
        stack["athena_databases"] = databases
        self.stats["athena_databases"] = len(databases)

    def collect(self) -> Dict[str, Any]:
        account_id = ""
        try:
            account_id = self.sts.get_caller_identity().get("Account", "")
        except (ClientError, BotoCoreError):
            pass

        stack: Dict[str, Any] = {
            "region": self.region,
            "account_id": account_id,
            "now": datetime.now(timezone.utc),
            "name_prefix": self.name_prefix,
            "runner_function_name": self.runner_function_name,
            "suppression_review_days": self.suppression_review_days,
            "report_unread_days": self.report_unread_days,
            "sla_days_by_severity": self.sla_days_by_severity,
            "reference_arch_enabled": False,
            "reference_arch_findings_by_day": {},
        }

        self.log("  · EventBridge schedule targeting the runner")
        self._collect_schedule(stack)
        self.log("  · S3 archive, suppressions, reports")
        self._collect_archive(stack)
        self.log("  · CloudWatch alarms, dashboards, Athena")
        self._collect_observability(stack)

        self.stack = stack
        return stack

    def run(self) -> List[Finding]:
        if not self.session:
            print(
                "No AWS credentials. Try --profile bootcamp, or run "
                "`aws configure --profile bootcamp`.",
                file=sys.stderr,
            )
            sys.exit(2)

        self.log("Collecting ambient-audit posture...")
        stack = self.collect()

        self.log("Running checks...")
        findings: List[Finding] = []
        for _check_id, check in CHECKS:
            findings += check(stack, self.region)

        self.findings = findings
        return findings


###############################################################################
# suppressions.yaml — minimal parser (no yaml dep)
###############################################################################


def _parse_suppressions_body(text: str) -> List[Dict[str, Any]]:
    """Parse a minimal suppressions.yaml shape.

    Accepted format:
        suppressions:
          - check_id: X
            resource_id: Y
            reason: Z
            review_by: 2025-01-01T00:00:00Z

    This is deliberately simple — the shipped template matches. If a
    stricter schema is needed, add PyYAML to requirements.txt.
    """
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    inside = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "suppressions:":
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            rest = stripped[2:]
            if ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = v.strip().strip('"').strip("'")
        elif ":" in stripped:
            k, v = stripped.split(":", 1)
            current[k.strip()] = v.strip().strip('"').strip("'")
    if current:
        entries.append(current)
    return entries


###############################################################################
# CLI
###############################################################################


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capstone_audit.py",
        description=(
            "Audit the ambient audit programme itself — whether it runs, "
            "whether its output is durable and queryable, whether the "
            "operational discipline around it exists, and whether the "
            "reports it produces are actually being read."
        ),
        epilog=(
            "Examples:\n"
            "  capstone_audit.py --profile bootcamp --region us-east-1 \\\n"
            "    --archive-bucket cbc-day10-archive-abc123\n"
            "  capstone_audit.py --format json --quiet > capstone.json\n"
            "  capstone_audit.py --fail-on CRITICAL   # for CI\n"
            "\n"
            "Four checks read RUNTIME state (CAP-003, CAP-012, CAP-015,\n"
            "CAP-016), so their answer depends on when you ran this. CAP-016\n"
            "in particular will change on an UNCHANGED programme as reports\n"
            "age past your report_unread_days threshold - that is correct,\n"
            "and it is the day's central lesson.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--profile", default=None,
                        help="AWS CLI named profile.")
    parser.add_argument("--region", default="us-east-1",
                        help="Region for regional resources (default us-east-1).")
    parser.add_argument("--name-prefix", default="cbc-day10",
                        help="Resource-name prefix from terraform.tfvars (default cbc-day10).")
    parser.add_argument("--archive-bucket", default=None,
                        help="Audit-report archive bucket name. From `terraform output archive_bucket_name`.")
    parser.add_argument("--runner-function-name", default=None,
                        help="Override runner Lambda name. Defaults to <prefix>-runner.")
    parser.add_argument("--suppression-review-days", type=int, default=90,
                        help="Suppression review threshold (default 90).")
    parser.add_argument("--report-unread-days", type=int, default=7,
                        help="Unread-report SLA threshold for CAP-016 (default 7).")
    parser.add_argument("--min-severity", choices=SEVERITY_ORDER, default="INFO",
                        help="Only report findings at this severity or worse (default INFO). Filters display only.")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table",
                        help="Output format (default table).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress on stderr.")
    parser.add_argument("--fail-on", choices=SEVERITY_ORDER, default=None,
                        help="Exit 1 if any finding is at this severity or worse.")
    parser.add_argument("--no-colour", "--no-color", dest="no_colour", action="store_true",
                        help="Disable ANSI colour.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    use_colour = sys.stdout.isatty() and not args.no_colour and args.format == "table"

    auditor = CapstoneAuditor(
        profile=args.profile,
        region=args.region,
        name_prefix=args.name_prefix,
        archive_bucket=args.archive_bucket,
        runner_function_name=args.runner_function_name,
        suppression_review_days=args.suppression_review_days,
        report_unread_days=args.report_unread_days,
        quiet=args.quiet,
    )

    try:
        all_findings = auditor.run()
    except NoCredentialsError:
        print(
            "No AWS credentials. Try --profile bootcamp.",
            file=sys.stderr,
        )
        return 2
    except ClientError as exc:
        print(f"AWS API error: {exc}", file=sys.stderr)
        return 2

    score = calculate_score(all_findings)
    shown = filter_by_severity(all_findings, args.min_severity)

    if args.format == "json":
        print(render_json(shown, auditor.stats, score))
    elif args.format == "csv":
        print(render_csv(shown), end="")
    else:
        print(render_table(shown, auditor.stats, score, use_colour))

    if args.fail_on:
        cutoff = SEVERITY_ORDER.index(args.fail_on)
        if any(SEVERITY_ORDER.index(f.severity) <= cutoff for f in all_findings):
            if not args.quiet:
                print(
                    f"Failing: at least one finding at severity {args.fail_on} or worse.",
                    file=sys.stderr,
                )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
