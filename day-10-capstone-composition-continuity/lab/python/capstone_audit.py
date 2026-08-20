#!/usr/bin/env python3
"""
capstone_audit.py — Day 10 ambient-audit auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits the ambient audit programme itself: whether reports are being
produced on cadence (CAP-001, CAP-002, CAP-003), whether the substrate
they land on is queryable and durable (CAP-004, CAP-005, CAP-010, CAP-011),
whether the operational discipline around them exists (CAP-007, CAP-008,
CAP-012, CAP-013, CAP-015), whether the runner is observable (CAP-009),
whether the composition of prior-day findings surfaces new risk (CAP-006),
whether a reference architecture stays reference (CAP-014), and — the
day's central check — whether the reports produced by the programme are
actually being read (CAP-016).

The bias this tool has, stated up front: it is more interested in
UNREAD REPORTS than in resource-level defects. A team with a working
audit runner, a versioned archive, a wired-up dashboard and a pile of
47 unread reports believes it has cost and security governance, and
what it actually has is an automated conversation with itself.

An unread report is worse than no report. A silent break in an audit
programme is worse than never having had one. That is the day's
thesis, and it is the layer above Day 09's COST-016 (which caught the
same failure mode at the AWS-Anomaly-Detection level).

What it checks
--------------
    CAP-001  No EventBridge schedule targets runner  no automation exists      HIGH
    CAP-002  Schedule interval too infrequent        weekly is the floor       MEDIUM
    CAP-003  Last invocation silence past interval   scheduler stopped         HIGH
    CAP-004  Archive bucket not versioned            no first-appeared history HIGH
    CAP-005  Archive bucket no lifecycle rule        archive becomes COST-014  MEDIUM
    CAP-006  Cross-cutting risk across prior days    correlated attack surface CRITICAL
    CAP-007  Findings not deduplicated               resource_id drift         MEDIUM
    CAP-008  No baseline suppression file            exceptions untracked      MEDIUM
    CAP-009  No error alarm on runner Lambda         silent runner failures    HIGH
    CAP-010  No CloudWatch dashboard for programme   no visible artefact       MEDIUM
    CAP-011  No Athena table over archive            history not queryable     MEDIUM
    CAP-012  Suppressions past review date           exceptions expire         HIGH
    CAP-013  SLA per severity not defined            no ack deadline           MEDIUM
    CAP-014  Reference architecture drift            no-longer-reference       MEDIUM
    CAP-015  Reports lack git remote metadata        untraceable code          LOW
    CAP-016  Latest report unread past SLA           programme talks to itself CRITICAL

Three things carried over, deliberately
---------------------------------------
**One signature.** Every check takes `(stack: Dict, region: str)` and returns
`List[Finding]`. Several need cross-resource context to be correct — CAP-006
needs the union of findings from all archived reports, CAP-014 needs the
full reference-arch findings map — and a one-resource signature makes that
impossible without a global.

**Time is injected, not read.** `stack["now"]` is set once by `collect()`.
On this day four checks are time-based (CAP-003, CAP-012, CAP-015 via
report age, CAP-016), all of which produce "unchanged programme, different
day, different result" outputs. That is why STATE C is demonstrable
without waiting 30 days.

**The archive is the source-of-truth.** Unlike Days 01-09, whose checks
read AWS API state directly, several Day 10 checks read the ARCHIVE
(prior audit reports as JSON objects in S3). This is deliberate. The
archive is what an auditor of the audit programme has to work with; if
the archive is empty, CAP-003, CAP-006, CAP-007, CAP-015 and CAP-016
all have nothing to check, and their silence is a situation, not a
design.

=============================================================================
DAY 10 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/capstone_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 09:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 10 uses all four severities except INFO, matching Day 09. There is one
LOW (CAP-015, git remote metadata) because the finding is a housekeeping
detail that makes reports traceable but doesn't degrade the audit's
correctness. There are TWO CRITICALs (CAP-006 cross-cutting risk, CAP-016
unread report) because those are the two failure modes where the audit
programme itself is broken rather than just incomplete.

STATIC STATE — after terraform apply with the shipped defaults
(all enable_* toggles false, no suppressions file uploaded, no reports
in the archive yet).

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ---------------------------------------------
  CAP-001  HIGH      10   1   10  account - no EventBridge schedule
  CAP-002  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  CAP-003  HIGH      10   0    0  none - SILENT BY SITUATION, see below
  CAP-004  HIGH      10   1   10  aws_s3_bucket.archive - versioning suspended
  CAP-005  MEDIUM     4   1    4  aws_s3_bucket.archive - no lifecycle rule
  CAP-006  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  CAP-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  CAP-008  MEDIUM     4   1    4  account - no suppressions.yaml
  CAP-009  HIGH      10   1   10  aws_lambda_function.runner - no error alarm
  CAP-010  MEDIUM     4   1    4  account - no CloudWatch dashboard
  CAP-011  MEDIUM     4   1    4  account - no Athena table over archive
  CAP-012  HIGH      10   0    0  none - SILENT BY SITUATION, see below
  CAP-013  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  CAP-014  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  CAP-015  LOW        1   0    0  none - SILENT BY SITUATION, see below
  CAP-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  -------  --------  --  --  ---  ---------------------------------------------
  TOTALS                    7   46

  SEVEN findings from SIXTEEN checks. Nine checks are silent here —
  two by design (CAP-013, CAP-014) and seven by situation (CAP-002,
  CAP-003, CAP-006, CAP-007, CAP-012, CAP-015, CAP-016).

  Score: 100 - 46 = 54/100. Grade D.

  SEVERITY HISTOGRAM of the 16 checks: 2 CRITICAL, 5 HIGH, 8 MEDIUM,
  1 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: apply done, all toggles off,
     no history, no suppressions                     7      46   54/100      D
  B  Live: all toggles on, suppressions file
     present with review dates, git remote
     configured, at least one report written         0       0  100/100      A
  C  Thirty days after B, WITH NOTHING
     CHANGED - scheduler stopped 14 days ago,
     four weekly reports piled up unread,
     one suppression past review                     6     120    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: everything in B, plus
     weekly triage rota where each report is
     acknowledged within its SLA and
     suppressions are reviewed on cadence            0       0  100/100      A

  STATE C IS DRAMATICALLY WORSE THAN STATE A. Read that twice.

  An operator in STATE A has a score of 54/100 and knows the ambient
  audit programme has not been set up. Bad posture, but informed.

  An operator in STATE C has a score of 0/100 and believes they have
  working cost and security governance. The runner is deployed, the
  alarms are wired, the dashboard exists, the archive is populated,
  the suppressions are documented. What has silently happened is:

    - The EventBridge rule stopped firing about two weeks ago. Nobody
      notices because there is no "the scheduler didn't fire"
      notification - the absence of activity is the failure mode.
    - Four consecutive weekly reports piled up unread in the archive
      (CAP-016 fires 4 times at 25 points each = 100 points). Nobody
      triaged them because the weekly review meeting was cancelled
      for month-end.
    - One suppression's review_by date passed 15 days ago. Nobody
      revisited it (CAP-012 fires with 10 points). The exception is
      now an ignored finding without an active decision.
    - CAP-003 fires with 10 points to name the scheduler silence
      explicitly.

  Total: 120 points, floored at 0/100. Grade F.

  STATE C IS THE INFORMED VERSION OF THE STATE-A OPERATOR'S IGNORANCE,
  compounded by two weeks of accumulated debt. This is the day's
  thesis. Days 01-09 audit CONFIGURATION - the state of a resource
  at a moment in time. Day 10 audits PROCESS - whether the
  configuration auditing is still happening at all.

  A process that used to work and stopped is a worse posture than a
  process that was never started. STATE C is the shape of an
  organisation that "did FinOps" for a quarter and then stopped
  without noticing.

SILENT BY DESIGN — CAP-013 (SLA per severity not defined) and CAP-014
(reference-arch drift).

  CAP-013: The sla_days_by_severity variable's type constraint requires
  all four severity keys (critical, high, medium, low) to be present in
  the object literal. The default value provides all four. The
  validation block requires them to be monotonically non-decreasing.
  There is no path through this Terraform that produces a stack with
  an undefined-per-severity SLA. So the check stays silent against
  this stack. It will fire immediately on a deployment that imports
  the module and passes sla_days_by_severity = {} or on a real
  organisation that has "an SLA" but where the ambiguity between
  severities is where the missed acknowledgements accumulate.

  CAP-014: When enable_reference_arch = false, no resource in this
  stack claims to be a reference. The check cannot fire because it
  has nothing to compare against. When enable_reference_arch = true
  the check becomes a real comparison — does running each prior day's
  audit against the composed module produce zero findings, as the
  module claims. Answering "yes" every time is the definition of
  "reference"; the first "no" is CAP-014 firing.

  Both silent-by-design classifications are structural facts about
  this stack, not judgements about the account.

SILENT BY SITUATION — CAP-002, CAP-003, CAP-006, CAP-007, CAP-012, CAP-016.

  CAP-002 (schedule interval > 7 days): silent because no schedule
  exists in STATE A. schedule_interval_days is 7 by default; when
  enable_scheduler goes true, the rate() expression is
  rate(7 days), and CAP-002 stays silent because 7 is the boundary,
  not above it. Change schedule_interval_days to 14 and this check
  fires without touching enable_scheduler.

  CAP-003 (last invocation age > interval * 1.5): silent because no
  invocations have happened. In STATE B (after one manual invocation
  to seed the archive), the check is silent because the invocation is
  fresh. In STATE C the check fires because the last invocation is
  now older than 10.5 days (interval 7 * 1.5), and nothing has
  re-fired the scheduler.

  CAP-006 (cross-cutting risk): silent because there are no prior-day
  findings in the archive. Requires at least two report objects
  referencing the same ARN across different days. Silent forever on
  an audit-runner that only enables day 09 (the shipped default);
  becomes possible once ENABLED_DAYS is expanded.

  CAP-007 (findings not deduplicated across audits): silent because
  no reports exist yet. Once reports exist, this checks whether the
  same finding appears in consecutive reports with different resource
  IDs due to normalisation drift.

  CAP-012 (suppressions past review): silent because no suppressions
  exist. Once suppressions.yaml is uploaded and its entries have
  review_by fields, this fires as those dates pass. STATE C's
  manifestation.

  CAP-016 (report unread past SLA): silent because no reports exist.
  Once reports exist and time passes, this fires as the newest report's
  age crosses report_unread_days without an acknowledgement API call.
  This is the CRITICAL that carries the day's thesis.

  CAP-015 is the git-remote-metadata check on the newest report in
  the archive. In STATE A there are no reports at all, so the check
  has nothing to inspect - silent by situation. Once STATE B is
  reached and reports start landing, CAP-015 fires immediately if the
  runner Lambda was deployed without ENABLED_GIT_REMOTE or a
  GitRemote tag.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except
  the passage of time and the population of the archive.

THE DIFFERENCE MATTERS. Silent-by-design tells you something about
the auditor: it cannot fire, so its silence is a property of the
tool. Silent-by-situation tells you nothing about the auditor and
everything about today's account. "We have no findings" and "we
have nothing to find" are different states that render identically
in every report. Never read the second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  CAP-001 AND CAP-003 LOOK LIKE THE SAME CHECK AND ARE NOT. CAP-001
  catches the absence of a schedule at ALL. CAP-003 catches a schedule
  that EXISTS but hasn't fired. The first fault is "we never set up
  automation"; the second is "automation stopped, nobody noticed".
  On this stack CAP-001 fires in STATE A and CAP-003 fires in
  STATE C, and they are the two sides of the same governance failure
  at two different lifecycles.

  CAP-004 AND CAP-005 ARE THE SAME IDEA AT DIFFERENT ANGLES. CAP-004
  asks "can I answer when did this finding first appear" — that
  requires versioning. CAP-005 asks "will this archive itself become
  expensive over time" — that requires lifecycle. Both are S3-bucket
  properties, both fire independently, both are trivial one-line
  Terraform fixes. Bucketing them together as "S3 hygiene" would
  confuse two different questions.

  CAP-008 AND CAP-012 ARE A LIFECYCLE. CAP-008 fires when there is no
  suppression file at all — the account has never articulated the
  exceptions it wants the auditor to skip. CAP-012 fires when the
  exceptions are present but stale — the account articulated them
  once and never revisited. On a mature account, CAP-008 fires briefly
  after the first audit and then never again; CAP-012 fires on cadence
  as review dates expire. These are the two phases of "exception
  management works".

  CAP-009 AND CAP-016 ARE THE SAME PATTERN AT TWO LAYERS OF THE STACK.
  CAP-009 asks "does the Lambda have an error alarm" — a technical
  failure of the runner. CAP-016 asks "does anybody read the reports"
  — an organisational failure to consume the runner's output. A
  stack where CAP-009 is silent (alarm exists) and CAP-016 is
  firing (nobody reads reports) is technically working audit
  infrastructure that produces no organisational value. That is the
  shape of most cost programmes.

  CAP-010 AND CAP-011 ARE THE SAME QUESTION AT TWO TIME HORIZONS.
  CAP-010 (dashboard) asks "is there ONE URL a stakeholder can
  click today to see the current state". CAP-011 (Athena) asks "can
  an operator answer HISTORICAL questions about what the state used
  to be". Both are queryability questions, in tension with each
  other: dashboards give right-now, Athena gives history-back-to-
  whenever. Neither substitutes for the other.

  CAP-006 IS THE ONLY CHECK THAT LOOKS AT MORE THAN ONE DAY'S
  FINDINGS AT ONCE, and it is deliberately narrow. It only fires when
  the same ARN appears in findings from TWO OR MORE prior-day audits.
  A resource with a Day 03 IAM overshare AND a Day 08 no-backup
  finding is a resource with cross-cutting risk — remediating one
  leaves the other, and shipping either fix without the other is
  shipping a partial improvement. Ordinary within-day findings are
  not what this check is for; the whole prior-day audit surface
  already covers those.

  CAP-016 IS ONE OF TWO CRITICALS BECAUSE IT IS THE ONLY CHECK WHOSE
  FAILURE MEANS "THE WHOLE PROGRAMME HAS STOPPED WORKING". Every
  other Day 10 finding is a specific infrastructure defect. CAP-016
  is the meta-check: the machine is running, the alerts are firing,
  nobody is reading them. A stack where every other check is green
  and CAP-016 is red is an organisation that has built cost
  governance and then stopped using it, which is one of the largest
  failure modes in the industry.

  THIS IS THE SAME STRUCTURAL POINT DAY 09 MADE with COST-016, on the
  next layer up. Day 09 caught "nobody reads AWS's cost anomalies".
  Day 10 catches "nobody reads YOUR audit's reports". The same
  failure mode, two layers of the stack, two consecutive days making
  it undeniable.
=============================================================================
"""

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
    findings: List[Finding] = []
    if stack.get("schedule_rule") is None and not stack.get("schedule_targets"):
        findings.append(
            Finding(
                check_id="CAP-001",
                severity="HIGH",
                resource_type=RT_ACCOUNT,
                resource_id=f"account/{stack.get('account_id', 'unknown')}",
                title="No EventBridge schedule targets the audit-runner",
                detail=(
                    "No EventBridge rule was found that targets the "
                    "audit-runner Lambda. The runner exists but is never "
                    "invoked. This is the ambient audit programme in its "
                    "'we set it up but never turned it on' state, which "
                    "is where a large fraction of accounts stop."
                ),
                remediation=(
                    "Set enable_scheduler = true in terraform.tfvars, "
                    "apply, and verify with `aws events list-rule-names-"
                    "by-target --target-arn <runner-arn>`. The rate() "
                    "expression is derived from schedule_interval_days "
                    "(default 7). CAP-002 will fire if you pick "
                    "an interval longer than 7 days."
                ),
                evidence={
                    "schedule_rule_seen": stack.get("schedule_rule") is not None,
                    "schedule_target_count": len(stack.get("schedule_targets") or []),
                },
                region=region,
            )
        )
    return findings


def check_schedule_too_infrequent(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-002 - schedule interval > 7 days.

    Weekly is the honest floor for most organisations. Anything longer
    means at least a week of silent regression can happen before the
    next audit reveals it. Silent by situation in STATE A because no
    schedule exists to have an interval.
    """
    findings: List[Finding] = []
    rule = stack.get("schedule_rule")
    if not rule:
        return findings
    interval = _parse_schedule_interval_days(rule.get("ScheduleExpression"))
    if interval is None or interval <= 7.0:
        return findings

    findings.append(
        Finding(
            check_id="CAP-002",
            severity="MEDIUM",
            resource_type=RT_SCHEDULE,
            resource_id=str(rule.get("Name", "unknown")),
            title=f"Schedule interval is {interval:.1f} days (> 7)",
            detail=(
                f"The audit-runner fires every {interval:.1f} days "
                f"({rule.get('ScheduleExpression')}). Anything longer than "
                f"7 days lets configuration drift accumulate for more than "
                f"a week between audits. In most orgs 'weekly' is the "
                f"honest floor; daily or twice-weekly for high-stakes "
                f"resources."
            ),
            remediation=(
                "Set schedule_interval_days = 7 (or less) in "
                "terraform.tfvars and apply. The variable's validation "
                "already refuses values above 30, but 7 is the floor at "
                "which CAP-002 stays silent."
            ),
            evidence={
                "ScheduleExpression": rule.get("ScheduleExpression"),
                "interval_days": interval,
            },
            region=region,
        )
    )
    return findings


def check_scheduler_silent(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-003 - the last audit invocation is older than interval * 1.5.

    The scheduler exists but has silently stopped firing (or is firing
    into errors that never write reports). This is the STATE C
    manifestation - it fires against an unchanged configuration when
    time passes and nobody notices.
    """
    findings: List[Finding] = []
    rule = stack.get("schedule_rule")
    reports = stack.get("reports") or []
    if not rule or not reports:
        return findings

    interval = _parse_schedule_interval_days(rule.get("ScheduleExpression"))
    if interval is None:
        return findings

    now = _now(stack)
    newest_age_days = None
    for r in reports:
        age = _age_days(r.get("invoked_at"), now)
        if age is None:
            continue
        if newest_age_days is None or age < newest_age_days:
            newest_age_days = age

    if newest_age_days is None:
        return findings

    threshold = interval * 1.5
    if newest_age_days <= threshold:
        return findings

    findings.append(
        Finding(
            check_id="CAP-003",
            severity="HIGH",
            resource_type=RT_SCHEDULE,
            resource_id=str(rule.get("Name", "unknown")),
            title="Scheduled audit silence past interval",
            detail=(
                f"The most recent report in the archive is "
                f"{_humanise_days(newest_age_days)} old. The schedule is "
                f"every {interval:.1f} day(s), so the auditor should have "
                f"produced a fresh report within {threshold:.1f} days. "
                f"Either the EventBridge rule has been disabled, the "
                f"Lambda is erroring silently, or the runner ran but did "
                f"not write to the archive. Check the runner's log group "
                f"and its error metric."
            ),
            remediation=(
                "Check the Lambda's error metric first (CAP-009 exists to "
                "make this visible), then re-invoke manually to confirm "
                "the runner still works, then examine the EventBridge "
                "rule and its target for drift. If the rule is disabled, "
                "`aws events enable-rule --name <name>`."
            ),
            evidence={
                "newest_report_age_days": round(newest_age_days, 2),
                "interval_days": interval,
                "threshold_days": round(threshold, 2),
            },
            region=region,
        )
    )
    return findings


def check_archive_not_versioned(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-004 - the audit-report archive bucket is not versioned.

    Without versioning, a report that gets overwritten is gone forever.
    "When did this finding first appear" becomes unanswerable.
    """
    findings: List[Finding] = []
    bucket = stack.get("archive_bucket")
    if not bucket:
        return findings
    versioning = str(stack.get("archive_versioning", "")).lower()
    if versioning == "enabled":
        return findings

    findings.append(
        Finding(
            check_id="CAP-004",
            severity="HIGH",
            resource_type=RT_BUCKET,
            resource_id=str(bucket),
            title="Audit archive bucket is not versioned",
            detail=(
                f"Bucket '{bucket}' has versioning "
                f"{'suspended' if versioning == 'suspended' else 'unset'}. "
                f"Any overwrite - accidental or deliberate - erases the "
                f"prior version. The archive can no longer answer 'when "
                f"did this finding first appear' or 'what did the report "
                f"say six months ago'. Versioning is the substrate that "
                f"makes an audit archive durable rather than a rolling "
                f"snapshot."
            ),
            remediation=(
                "Set enable_archive_versioning = true and apply. AWS S3 "
                "versioning has no per-object cost; storage costs "
                "accumulate on non-current versions, so pair it with "
                "enable_archive_lifecycle = true which sets a noncurrent-"
                "version expiration."
            ),
            evidence={"versioning_status": versioning or "unset"},
            region=region,
        )
    )
    return findings


def check_archive_no_lifecycle(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-005 - the audit-report archive has no lifecycle rule.

    Reports accumulate. Without a lifecycle rule they stay in STANDARD
    forever and the archive itself becomes exactly what Day 09's
    COST-014 was catching one day earlier.
    """
    findings: List[Finding] = []
    bucket = stack.get("archive_bucket")
    if not bucket:
        return findings
    rules = stack.get("archive_lifecycle_rules") or []
    active = [r for r in rules if str(r.get("Status", "")).lower() == "enabled"]
    if active:
        return findings

    findings.append(
        Finding(
            check_id="CAP-005",
            severity="MEDIUM",
            resource_type=RT_BUCKET,
            resource_id=str(bucket),
            title="Audit archive bucket has no lifecycle rule",
            detail=(
                f"Bucket '{bucket}' has no active S3 lifecycle rule. "
                f"Every report will remain in STANDARD storage at "
                f"$0.023/GB/month forever, and non-current versions "
                f"(assuming CAP-004 is fixed) will do the same. On a "
                f"weekly-audit cadence at ~5 KB/report, the storage cost "
                f"stays under $1/month for years - but the drift towards "
                f"'we have five years of audit history and can't afford "
                f"to keep it' is the same shape as any other unbounded "
                f"archive."
            ),
            remediation=(
                "Set enable_archive_lifecycle = true and apply. The rule "
                "transitions to STANDARD_IA at 30 days, GLACIER_IR at 90 "
                "days, and expires at 730 days. Non-current versions "
                "expire at 365 days. Adjust the numbers in main.tf if "
                "your retention policy demands longer."
            ),
            evidence={
                "rule_count": len(rules),
                "active_rule_count": len(active),
            },
            region=region,
        )
    )
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
    findings: List[Finding] = []
    reports = stack.get("reports") or []
    if len(reports) < 2:
        return findings

    # Build resource_id -> set of check_id's across all reports.
    resource_hits: Dict[Tuple[str, str], Set[str]] = {}
    resource_days: Dict[Tuple[str, str], Set[str]] = {}
    for r in reports:
        day = str(r.get("day") or "")
        for f in r.get("findings") or []:
            key = (str(f.get("resource_type", "")), str(f.get("resource_id", "")))
            resource_hits.setdefault(key, set()).add(str(f.get("check_id", "")))
            resource_days.setdefault(key, set()).add(day)

    for (rtype, rid), days in sorted(resource_days.items()):
        if len(days) < 2:
            continue
        checks = sorted(resource_hits.get((rtype, rid), set()))
        findings.append(
            Finding(
                check_id="CAP-006",
                severity="CRITICAL",
                resource_type=rtype or RT_RESOURCE,
                resource_id=rid,
                title=f"Resource has findings across {len(days)} audit days",
                detail=(
                    f"Resource '{rid}' ({rtype}) appears in findings from "
                    f"{len(days)} different audit days: {', '.join(sorted(days))}. "
                    f"The specific checks that hit it are: "
                    f"{', '.join(checks)}. This is CROSS-CUTTING RISK: no "
                    f"single team owns the whole picture, and remediating "
                    f"one dimension leaves the others. A Day 03 IAM defect "
                    f"combined with a Day 08 no-backup defect is not two "
                    f"independent findings - it is a single risk that "
                    f"neither team's ordinary workflow will surface."
                ),
                remediation=(
                    f"Convene a cross-cutting remediation for {rid}. Each "
                    f"of the {len(checks)} check_ids has its own "
                    f"remediation guidance in the day it belongs to; the "
                    f"job here is to sequence them so the resource is left "
                    f"clean rather than partially fixed. Then re-run the "
                    f"ambient audit to confirm the correlation clears."
                ),
                evidence={
                    "days_hit": sorted(days),
                    "check_ids": checks,
                    "hit_count": len(checks),
                },
                region=region,
            )
        )
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
    findings: List[Finding] = []
    by_day = _reports_by_day(stack)

    for day, reports in sorted(by_day.items()):
        if len(reports) < 2:
            continue
        # Sort by invoked_at ascending, take last two.
        sorted_reports = sorted(reports, key=lambda x: x.get("invoked_at") or "")
        prev = sorted_reports[-2]
        curr = sorted_reports[-1]

        prev_by_check: Dict[str, Set[str]] = {}
        for f in prev.get("findings") or []:
            prev_by_check.setdefault(str(f.get("check_id")), set()).add(
                str(f.get("resource_id"))
            )

        curr_by_check: Dict[str, Set[str]] = {}
        for f in curr.get("findings") or []:
            curr_by_check.setdefault(str(f.get("check_id")), set()).add(
                str(f.get("resource_id"))
            )

        drifted: List[str] = []
        for check_id, prev_ids in prev_by_check.items():
            curr_ids = curr_by_check.get(check_id, set())
            # If both had the same COUNT but no overlap in resource_ids,
            # somebody normalised differently.
            if prev_ids and curr_ids and len(prev_ids) == len(curr_ids) and not (prev_ids & curr_ids):
                drifted.append(check_id)

        if drifted:
            findings.append(
                Finding(
                    check_id="CAP-007",
                    severity="MEDIUM",
                    resource_type=RT_REPORT,
                    resource_id=f"day={day}",
                    title=f"Finding dedup drift in day {day} between consecutive reports",
                    detail=(
                        f"For day-{day} audit, the checks {', '.join(sorted(drifted))} "
                        f"produced the same number of findings in two "
                        f"consecutive runs but zero overlap on resource_id. "
                        f"That means the auditor is emitting the same "
                        f"underlying fault under two different keys - "
                        f"typically because a normalisation rule changed. "
                        f"Suppressions written against yesterday's key no "
                        f"longer match today's."
                    ),
                    remediation=(
                        "Pick one canonical form (ARN or bare ID) in the "
                        "check's Finding(resource_id=...) construction "
                        "and stick to it. Update suppression files to use "
                        "the chosen form."
                    ),
                    evidence={
                        "drifted_check_ids": sorted(drifted),
                        "prev_invoked_at": prev.get("invoked_at"),
                        "curr_invoked_at": curr.get("invoked_at"),
                    },
                    region=region,
                )
            )
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
    findings: List[Finding] = []
    if stack.get("suppressions_file_present"):
        return findings
    findings.append(
        Finding(
            check_id="CAP-008",
            severity="MEDIUM",
            resource_type=RT_ACCOUNT,
            resource_id=f"account/{stack.get('account_id', 'unknown')}",
            title="No suppressions file present in the archive",
            detail=(
                "No suppressions.yaml (or equivalent) was found in the "
                "audit-report archive. The ambient audit is running "
                "without a documented list of exceptions. Every finding "
                "that the team has decided to accept - and there is "
                "always at least one - is being re-flagged each run, "
                "which trains the reader to ignore the report."
            ),
            remediation=(
                "Upload a suppressions.yaml to the archive bucket root. "
                "See lab/README.md step 5 for the shipped template. Each "
                "entry must carry a check_id, a resource_id, a reason, "
                "and a review_by date; CAP-012 fires as review dates pass."
            ),
            evidence={"suppressions_file_present": False},
            region=region,
        )
    )
    return findings


def check_no_lambda_alarm(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-009 - no CloudWatch alarm on the runner Lambda's error metric.

    Symmetric with CAP-016 at a different layer: CAP-009 catches
    'the runner errors silently'; CAP-016 catches 'the runner works
    but nobody reads its output'. A programme where both fire is a
    programme that isn't really running.
    """
    findings: List[Finding] = []
    runner = stack.get("runner_function_name")
    if not runner:
        return findings
    for alarm in stack.get("cloudwatch_alarms") or []:
        if str(alarm.get("MetricName", "")).lower() != "errors":
            continue
        if str(alarm.get("Namespace", "")) != "AWS/Lambda":
            continue
        for dim in alarm.get("Dimensions") or []:
            if dim.get("Name") == "FunctionName" and dim.get("Value") == runner:
                return findings
    findings.append(
        Finding(
            check_id="CAP-009",
            severity="HIGH",
            resource_type=RT_LAMBDA,
            resource_id=str(runner),
            title="No CloudWatch error alarm on the audit-runner",
            detail=(
                f"No CloudWatch alarm was found watching the Errors metric "
                f"of {runner}. If the runner errors - which it will, "
                f"eventually - nothing tells anyone. The runner keeps "
                f"getting scheduled, the reports stop being written, and "
                f"you find out only when someone re-reads a stale report "
                f"long after the fact. This is CAP-003's twin: CAP-003 "
                f"detects the ABSENCE of reports; CAP-009 detects the "
                f"absence of a mechanism to know why."
            ),
            remediation=(
                "Set enable_lambda_alarm = true and apply. The alarm "
                "publishes to the SNS topic which emails "
                f"{stack.get('notification_email', '(your address)')}. "
                "Threshold is 1 error/hour by default; adjust via "
                "lambda_error_alarm_threshold if you have a high-volume "
                "runner where a rare error is tolerable."
            ),
            evidence={
                "runner_function_name": runner,
                "alarms_checked": len(stack.get("cloudwatch_alarms") or []),
            },
            region=region,
        )
    )
    return findings


def check_no_dashboard(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-010 - no CloudWatch dashboard exists for the audit programme.

    The dashboard is not analytics. It is the one URL a stakeholder
    clicks to answer 'is the audit programme working today'. Without
    it, that question requires querying CloudWatch metrics by hand, or
    knowing the API - both of which mean the answer never actually gets
    asked.
    """
    findings: List[Finding] = []
    prefix = str(stack.get("name_prefix") or "")
    dashboards = stack.get("cloudwatch_dashboards") or []
    if any(str(d.get("DashboardName", "")).startswith(prefix) for d in dashboards):
        return findings
    findings.append(
        Finding(
            check_id="CAP-010",
            severity="MEDIUM",
            resource_type=RT_DASHBOARD,
            resource_id=f"account/{stack.get('account_id', 'unknown')}",
            title="No CloudWatch dashboard for the audit programme",
            detail=(
                "No CloudWatch dashboard with the expected name prefix "
                f"'{prefix}' was found. Without a dashboard, 'is the "
                f"programme working today' is answered by opening the "
                f"Lambda console, then the metrics tab, then the log "
                f"group, then jq'ing the latest log entry - which is "
                f"never what happens. Cost: $3.00/dashboard/month, flat. "
                f"Cheap for the visibility it produces."
            ),
            remediation=(
                "Set enable_dashboard = true and apply. The dashboard "
                "shows recent invocations, error rate, and the last "
                "runner summary from CloudWatch Logs. Extend it as your "
                "programme grows."
            ),
            evidence={
                "expected_prefix": prefix,
                "dashboards_seen": [str(d.get("DashboardName", "")) for d in dashboards],
            },
            region=region,
        )
    )
    return findings


def check_no_athena_table(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-011 - no Athena database exists over the archive.

    Without a queryable historical layer, 'how many DR-008 findings did
    we have in Q1' requires downloading months of JSON and processing
    them locally. That query never gets run, which means historical
    trends are opaque, which means quarterly reviews devolve into
    'compare the latest score to the last one', which is not a review.
    """
    findings: List[Finding] = []
    prefix = str(stack.get("name_prefix") or "").replace("-", "_")
    databases = stack.get("athena_databases") or []
    if any(str(d.get("Name", "")).startswith(prefix) for d in databases):
        return findings
    findings.append(
        Finding(
            check_id="CAP-011",
            severity="MEDIUM",
            resource_type=RT_ATHENA,
            resource_id=f"account/{stack.get('account_id', 'unknown')}",
            title="No Athena database over the audit-report archive",
            detail=(
                "No Athena database with the expected name prefix "
                f"'{prefix}' was found. Historical questions about the "
                f"audit programme cannot be answered without one. Cost: "
                f"$5/TB scanned, plus a workgroup with a result-cache "
                f"prefix. For the audit archive's size (KB-scale JSON), "
                f"queries cost less than a cent."
            ),
            remediation=(
                "Set enable_athena_table = true and apply. Then in the "
                "Athena console, run "
                f"'SELECT day, invoked_at, score FROM {prefix}_reports "
                f"ORDER BY invoked_at DESC LIMIT 20'. The reports table "
                "uses the partitioned S3 key layout the runner writes to."
            ),
            evidence={
                "expected_prefix": prefix,
                "databases_seen": [str(d.get("Name", "")) for d in databases],
            },
            region=region,
        )
    )
    return findings


def check_suppressions_stale(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-012 - suppression entries older than suppression_review_days
    without a fresh review.

    An exception with a story becomes an exception without a story
    becomes an exception nobody remembers, on a schedule of about 90
    days. This check catches that transition. Silent when no
    suppressions exist (STATE A); fires as review dates pass (STATE C).
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("suppression_review_days") or 90)
    for entry in stack.get("suppressions") or []:
        review_by = entry.get("review_by")
        review_dt = _parse_time(review_by)
        if review_dt is None:
            continue
        age_days = (now - review_dt).total_seconds() / 86400.0
        if age_days < 0:  # review_by is in the future
            continue
        # A review_by in the past means overdue.
        entry_key = str(entry.get("check_id", "unknown")) + ":" + str(entry.get("resource_id", "unknown"))
        findings.append(
            Finding(
                check_id="CAP-012",
                severity="HIGH",
                resource_type=RT_SUPPRESSION,
                resource_id=entry_key,
                title="Suppression entry is past its review date",
                detail=(
                    f"Suppression for {entry.get('check_id')} on "
                    f"{entry.get('resource_id')} was last reviewed at "
                    f"{review_by}, {_humanise_days(age_days)} ago. The "
                    f"threshold is {threshold_days:.0f} days. Reason on "
                    f"file: '{entry.get('reason', '(no reason recorded)')}'. "
                    f"An exception with a review date that has passed is an "
                    f"exception that has left the programme - the "
                    f"underlying finding is being ignored without an "
                    f"active decision to ignore it."
                ),
                remediation=(
                    "Open the suppression file, revisit the decision, and "
                    "either remove the entry (if the underlying issue is "
                    "fixed) or update review_by to a new date with an "
                    "updated reason. If you no longer remember why the "
                    "suppression exists, that is not a suppression - it is "
                    "a finding you are not treating."
                ),
                evidence={
                    "check_id": entry.get("check_id"),
                    "suppressed_resource_id": entry.get("resource_id"),
                    "review_by": review_by,
                    "days_overdue": round(age_days, 1),
                    "reason": entry.get("reason"),
                },
                region=region,
            )
        )
    return findings


def check_sla_not_defined(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-013 - the SLA per severity is not fully defined.

    SILENT BY DESIGN against this stack: sla_days_by_severity is a typed
    object with all four keys required by the variable's type constraint,
    plus a validation ensuring monotonic ordering. Fires readily on
    stacks that pass a bare dict.
    """
    findings: List[Finding] = []
    sla = stack.get("sla_days_by_severity") or {}
    missing = [k for k in REQUIRED_SLA_KEYS if k not in sla or sla.get(k) is None]
    if not missing:
        return findings
    findings.append(
        Finding(
            check_id="CAP-013",
            severity="MEDIUM",
            resource_type=RT_ACCOUNT,
            resource_id=f"account/{stack.get('account_id', 'unknown')}",
            title=f"SLA not defined for severities: {', '.join(sorted(missing))}",
            detail=(
                f"sla_days_by_severity is missing definitions for: "
                f"{', '.join(sorted(missing))}. Without a per-severity "
                f"SLA, 'when should this finding be acknowledged' is "
                f"undefined, and CAP-016 has no threshold to fire "
                f"against for those severities. An ambient audit programme "
                f"without SLAs is one where 'urgent' is whichever "
                f"finding happens to be looked at."
            ),
            remediation=(
                "Define SLA days for critical, high, medium, and low. "
                "This stack's default is 1/3/7/30. Adjust to your "
                "organisation's escalation policy. The type constraint "
                "on sla_days_by_severity requires all four keys, so "
                "this check is silent by design against a Terraform-shaped "
                "input."
            ),
            evidence={
                "missing_severities": sorted(missing),
                "defined_severities": sorted(sla.keys()),
            },
            region=region,
        )
    )
    return findings


def check_reference_arch_drift(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """CAP-014 - the reference architecture no longer scores 100/100.

    Fires per prior-day audit that produces > 0 findings when pointed at
    the reference-arch resources. SILENT BY DESIGN when
    enable_reference_arch is false (nothing claims to be reference).
    """
    findings: List[Finding] = []
    if not stack.get("reference_arch_enabled"):
        return findings

    drift_by_day = stack.get("reference_arch_findings_by_day") or {}
    for day, drift_findings in sorted(drift_by_day.items()):
        drift_list = list(drift_findings)
        if not drift_list:
            continue
        findings.append(
            Finding(
                check_id="CAP-014",
                severity="MEDIUM",
                resource_type=RT_ACCOUNT,
                resource_id=f"reference-arch/day={day}",
                title=f"Reference architecture has drifted on day {day}",
                detail=(
                    f"Day {day}'s audit against the reference architecture "
                    f"produced {len(drift_list)} finding(s). The reference "
                    f"is supposed to score 100/100; anything else means "
                    f"the reference has drifted or the audit was updated "
                    f"with a new check the reference wasn't designed for. "
                    f"Either update the reference to remain reference, or "
                    f"acknowledge that this day's check surface has "
                    f"expanded and update the reference-arch module."
                ),
                remediation=(
                    "Read day 09's cost-observations.md style template - "
                    "the same discipline applies here. Convene a review, "
                    "decide whether the reference or the check should "
                    "change, and update whichever loses the argument."
                ),
                evidence={
                    "day": day,
                    "drift_finding_count": len(drift_list),
                    "sample_check_ids": sorted({
                        str(f.get("check_id", "?")) for f in drift_list[:5]
                    }),
                },
                region=region,
            )
        )
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
    findings: List[Finding] = []
    reports = stack.get("reports") or []
    if not reports:
        return findings
    # Take the newest.
    newest = sorted(
        reports, key=lambda x: x.get("invoked_at") or "", reverse=True
    )[0]
    if newest.get("git_remote"):
        return findings
    findings.append(
        Finding(
            check_id="CAP-015",
            severity="LOW",
            resource_type=RT_REPORT,
            resource_id=str(newest.get("key", "unknown")),
            title="Latest report has no git_remote metadata",
            detail=(
                f"The newest report in the archive ({newest.get('key')}) "
                f"has no 'git_remote' field. Historical comparison is "
                f"guesswork without knowing which version of the auditor "
                f"produced each report - a finding that 'appeared' at some "
                f"date may just be a check that was added at that date."
            ),
            remediation=(
                "Populate the git_remote field by setting the "
                "ENABLED_GIT_REMOTE environment variable on the runner "
                "Lambda, or by tagging the function with GitRemote. "
                "Every subsequent report will carry it."
            ),
            evidence={
                "key": newest.get("key"),
                "invoked_at": newest.get("invoked_at"),
            },
            region=region,
        )
    )
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
    findings: List[Finding] = []
    reports = stack.get("reports") or []
    if not reports:
        return findings
    now = _now(stack)
    threshold_days = float(stack.get("report_unread_days") or 7)

    # Unacknowledged reports past the threshold.
    for r in reports:
        if r.get("acknowledged"):
            continue
        age = _age_days(r.get("invoked_at"), now)
        if age is None or age < threshold_days:
            continue
        key = str(r.get("key") or "unknown")
        day = str(r.get("day") or "unknown")
        findings.append(
            Finding(
                check_id="CAP-016",
                severity="CRITICAL",
                resource_type=RT_REPORT,
                resource_id=key,
                title="Audit report has been open without acknowledgement",
                detail=(
                    f"Report {key} (day {day}, {_humanise_days(age)} old) "
                    f"has no acknowledgement recorded. The threshold is "
                    f"{threshold_days:.0f} days. THIS IS THE DAY'S CENTRAL "
                    f"FINDING: the runner produced a report, the "
                    f"dashboard shows it, the archive holds it, and "
                    f"nobody has opened it. That is the shape of an audit "
                    f"programme that has stopped serving its purpose."
                ),
                remediation=(
                    "Open the report from the S3 console (or the "
                    "dashboard). Read it. Then tag the object: "
                    "`aws s3api put-object-tagging --bucket <archive> "
                    "--key <report-key> --tagging "
                    "'TagSet=[{Key=Acknowledged,Value=true},"
                    "{Key=Reviewer,Value=<name>}]'`. Better: automate the "
                    "acknowledgement in the review workflow. Best: make "
                    "the weekly triage rota the ONLY way an acknowledgement "
                    "gets recorded."
                ),
                evidence={
                    "key": key,
                    "day": day,
                    "age_days": round(age, 2),
                    "threshold_days": threshold_days,
                },
                region=region,
            )
        )
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
