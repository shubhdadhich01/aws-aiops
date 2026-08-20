#!/usr/bin/env python3
"""
test_checks.py — Day 10 unit tests for capstone_audit.py

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

    cd lab/python
    python3 -m unittest discover -s tests -v

47 tests, standard library only, NO AWS CREDENTIALS REQUIRED. Same
architecture as Days 08 and 09: collect() is separated from the checks,
every check is a pure function over a plain dict, and stack["now"] is
the injected clock that makes STATE C reproducible.

    16 FIRE      one per check, each proving the fault is detected
    16 SILENT    one per check, each proving clean input is not flagged
    15 COVERING  whole-stack totals, the score, the injected clock, the
                 silent-by-design checks, the deliberate interactions, the
                 helpers and the three renderers

To point these at the challenge file instead of the reference:

    CAPSTONE_AUDIT_MODULE=capstone_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests

THE RENDERER TESTS DO NOT CALL THE CHECKS. They build findings directly
from Finding(...) via CONTRACT_SHAPE, which reproduces STATE A's severity
histogram (3 HIGH + 4 MEDIUM = 7 findings, 46 points, score 54).

=============================================================================
# CONTRACT-BEGIN
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
# CONTRACT-END
=============================================================================
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

# Path shim so tests can be pointed at either the reference or the challenge.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

MODULE_NAME = os.environ.get("CAPSTONE_AUDIT_MODULE", "capstone_audit")
ca = __import__(MODULE_NAME)


###############################################################################
# Contract shape and shared fixtures
###############################################################################

# STATE A histogram: 3 HIGH (CAP-001, CAP-004, CAP-009), 4 MEDIUM (CAP-005,
# CAP-008, CAP-010, CAP-011). Total = 7 findings, 3*10 + 4*4 = 46 pts.
CONTRACT_SHAPE = (
    "HIGH", "HIGH", "HIGH",
    "MEDIUM", "MEDIUM", "MEDIUM", "MEDIUM",
)
CONTRACT_FINDING_COUNT = 7
CONTRACT_POINTS = 46
CONTRACT_SCORE = 54
CONTRACT_GRADE_LETTER = "D"

# STATE C expected shape.
STATE_C_FINDING_COUNT = 6
STATE_C_POINTS = 120  # unfloored
STATE_C_SCORE = 0     # floored from -20
STATE_C_GRADE_LETTER = "F"

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def base_stack(now=NOW):
    """STATE A — after apply, all toggles off, no history, no suppressions."""
    return {
        "region": "us-east-1",
        "account_id": "123456789012",
        "now": now,
        "name_prefix": "cbc-day10",
        "runner_function_name": "cbc-day10-runner",
        "suppression_review_days": 90,
        "report_unread_days": 7,
        "sla_days_by_severity": {"critical": 1, "high": 3, "medium": 7, "low": 30},
        "reference_arch_enabled": False,
        "reference_arch_findings_by_day": {},

        # Nothing set up.
        "schedule_rule": None,
        "schedule_targets": [],
        "archive_bucket": "cbc-day10-archive-abc",
        "archive_versioning": "Suspended",
        "archive_lifecycle_rules": [],
        "suppressions_file_present": False,
        "suppressions": [],
        "reports": [],
        "cloudwatch_alarms": [],
        "cloudwatch_dashboards": [],
        "athena_databases": [],
        "notification_email": "ops@example.com",
    }


def clean_stack(now=NOW):
    """STATE B — everything on, one recent report, no drift."""
    stack = base_stack(now)
    stack["schedule_rule"] = {
        "Name": "cbc-day10-schedule",
        "ScheduleExpression": "rate(7 days)",
    }
    stack["schedule_targets"] = [{"Arn": f"arn:...runner:{stack['runner_function_name']}"}]
    stack["archive_versioning"] = "Enabled"
    stack["archive_lifecycle_rules"] = [{"ID": "archive-tiering", "Status": "Enabled"}]
    stack["suppressions_file_present"] = True
    stack["suppressions"] = []  # empty is fine — no stale entries
    stack["cloudwatch_alarms"] = [
        {
            "AlarmName": "cbc-day10-runner-errors",
            "MetricName": "Errors",
            "Namespace": "AWS/Lambda",
            "Dimensions": [{"Name": "FunctionName", "Value": stack["runner_function_name"]}],
        }
    ]
    stack["cloudwatch_dashboards"] = [{"DashboardName": "cbc-day10-capstone"}]
    stack["athena_databases"] = [{"Name": "cbc_day10_audits"}]
    stack["reports"] = [
        {
            "day": "09",
            "key": "reports/day=09/2025/06/01/20250601T120000Z.json",
            "invoked_at": (now - timedelta(hours=1)).isoformat(),
            "acknowledged": False,
            "git_remote": "github.com/careerbytecode/aws-bootcamp",
            "findings": [],
        }
    ]
    return stack


def decayed_stack(now=None):
    """STATE C — 30 days after clean_stack.

    Scheduler stopped 14 days ago, four weekly reports piled up unread,
    one suppression's review_by date passed 15 days ago. Expected: 6
    findings, 120 points, 0/100, grade F.
    """
    later = now or (NOW + timedelta(days=30))
    stack = clean_stack(later)

    # Reports at 14/21/28/35 days old — scheduler stopped 14 days ago
    # so newest is 14d > 10.5 (interval * 1.5) → CAP-003 fires.
    # All are > 7 days → CAP-016 fires 4 times.
    stack["reports"] = [
        {
            "day": "09",
            "key": f"reports/day=09/report-{i}.json",
            "invoked_at": (later - timedelta(days=14 + i * 7)).isoformat(),
            "acknowledged": False,
            "git_remote": "github.com/careerbytecode/aws-bootcamp",
            "findings": [],
        }
        for i in range(4)
    ]

    # One suppression that is 15 days past review_by.
    stack["suppressions"] = [
        {
            "check_id": "COST-005",
            "resource_id": "vol-orphan-a",
            "reason": "Test volume, will remove Q3.",
            "review_by": (later - timedelta(days=15)).isoformat(),
        }
    ]
    return stack


def run_all(stack):
    out = []
    for _check_id, check in ca.CHECKS:
        out += check(stack, stack["region"])
    return out


def ids(findings):
    return sorted(f.check_id for f in findings)


def shaped_findings():
    """Build Finding objects whose severities match STATE A's histogram."""
    out = []
    for i, severity in enumerate(CONTRACT_SHAPE, 1):
        out.append(ca.Finding(
            check_id=f"CAP-{i:03d}",
            severity=severity,
            resource_type="AWS::Test::Resource",
            resource_id=f"res-{i}",
            title=f"synthetic {severity} finding",
            detail="detail",
            remediation="fix it",
        ))
    return out


###############################################################################
# TestChecksFire — 16
###############################################################################


class TestChecksFire(unittest.TestCase):

    def test_cap_001_fires_when_no_schedule(self):
        result = ca.check_no_schedule(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-001")
        self.assertEqual(result[0].severity, "HIGH")

    def test_cap_002_fires_on_interval_over_seven(self):
        stack = clean_stack()
        stack["schedule_rule"]["ScheduleExpression"] = "rate(14 days)"
        result = ca.check_schedule_too_infrequent(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-002")

    def test_cap_003_fires_on_stale_newest_report(self):
        stack = clean_stack()
        # Age the report so its > interval * 1.5.
        stack["reports"][0]["invoked_at"] = (NOW - timedelta(days=15)).isoformat()
        result = ca.check_scheduler_silent(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-003")

    def test_cap_004_fires_when_versioning_off(self):
        result = ca.check_archive_not_versioned(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-004")

    def test_cap_005_fires_when_no_lifecycle(self):
        result = ca.check_archive_no_lifecycle(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-005")

    def test_cap_006_fires_on_cross_cutting_resource(self):
        # Two reports from different days, both flagging the same ARN.
        stack = clean_stack()
        stack["reports"] = [
            {
                "day": "03",
                "key": "reports/day=03/r1.json",
                "invoked_at": (NOW - timedelta(hours=2)).isoformat(),
                "acknowledged": False,
                "findings": [
                    {"check_id": "IAM-004", "resource_type": "AWS::IAM::Role",
                     "resource_id": "arn:aws:iam::123:role/critical-role"},
                ],
            },
            {
                "day": "08",
                "key": "reports/day=08/r1.json",
                "invoked_at": (NOW - timedelta(hours=1)).isoformat(),
                "acknowledged": False,
                "findings": [
                    {"check_id": "DR-010", "resource_type": "AWS::IAM::Role",
                     "resource_id": "arn:aws:iam::123:role/critical-role"},
                ],
            },
        ]
        result = ca.check_cross_cutting_risk(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-006")
        self.assertEqual(result[0].severity, "CRITICAL")

    def test_cap_007_fires_on_deduped_finding_drift(self):
        # Same day, two consecutive reports, same count but zero overlap.
        stack = clean_stack()
        stack["reports"] = [
            {
                "day": "09", "key": "r1.json",
                "invoked_at": (NOW - timedelta(days=2)).isoformat(),
                "acknowledged": False,
                "findings": [
                    {"check_id": "COST-005", "resource_type": "AWS::EC2::Volume",
                     "resource_id": "vol-abc"},
                ],
            },
            {
                "day": "09", "key": "r2.json",
                "invoked_at": (NOW - timedelta(hours=1)).isoformat(),
                "acknowledged": False,
                "findings": [
                    {"check_id": "COST-005", "resource_type": "AWS::EC2::Volume",
                     "resource_id": "arn:aws:ec2:us-east-1:123:volume/vol-abc"},
                ],
            },
        ]
        result = ca.check_finding_dedup(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-007")

    def test_cap_008_fires_when_no_suppressions_file(self):
        result = ca.check_no_suppressions_file(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-008")

    def test_cap_009_fires_when_no_lambda_error_alarm(self):
        result = ca.check_no_lambda_alarm(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-009")

    def test_cap_010_fires_when_no_dashboard(self):
        result = ca.check_no_dashboard(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-010")

    def test_cap_011_fires_when_no_athena_database(self):
        result = ca.check_no_athena_table(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-011")

    def test_cap_012_fires_on_past_review_suppression(self):
        stack = clean_stack()
        stack["suppressions"] = [
            {"check_id": "COST-005", "resource_id": "vol-x", "reason": "test",
             "review_by": (NOW - timedelta(days=15)).isoformat()},
        ]
        result = ca.check_suppressions_stale(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-012")
        self.assertEqual(result[0].severity, "HIGH")

    def test_cap_013_fires_when_sla_missing_severity(self):
        stack = clean_stack()
        stack["sla_days_by_severity"] = {"critical": 1, "high": 3}  # missing medium, low
        result = ca.check_sla_not_defined(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-013")

    def test_cap_014_fires_on_reference_arch_drift(self):
        stack = clean_stack()
        stack["reference_arch_enabled"] = True
        stack["reference_arch_findings_by_day"] = {
            "09": [{"check_id": "COST-005", "resource_id": "vol-drift"}],
        }
        result = ca.check_reference_arch_drift(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-014")

    def test_cap_015_fires_on_report_without_git_remote(self):
        stack = clean_stack()
        stack["reports"][0].pop("git_remote", None)
        result = ca.check_reports_lack_git_remote(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "CAP-015")
        self.assertEqual(result[0].severity, "LOW")

    def test_cap_016_fires_per_unread_report_past_sla(self):
        stack = clean_stack()
        # Three unread reports, each older than report_unread_days.
        stack["reports"] = [
            {"day": "09", "key": f"r{i}.json",
             "invoked_at": (NOW - timedelta(days=10 + i)).isoformat(),
             "acknowledged": False, "git_remote": "x", "findings": []}
            for i in range(3)
        ]
        result = ca.check_report_unread(stack, "us-east-1")
        self.assertEqual(len(result), 3)
        for f in result:
            self.assertEqual(f.check_id, "CAP-016")
            self.assertEqual(f.severity, "CRITICAL")


###############################################################################
# TestChecksSilent — 16
###############################################################################


class TestChecksSilent(unittest.TestCase):

    def test_cap_001_silent_when_schedule_targets_runner(self):
        self.assertEqual(ca.check_no_schedule(clean_stack(), "us-east-1"), [])

    def test_cap_002_silent_on_seven_day_interval(self):
        self.assertEqual(ca.check_schedule_too_infrequent(clean_stack(), "us-east-1"), [])

    def test_cap_003_silent_on_fresh_report(self):
        self.assertEqual(ca.check_scheduler_silent(clean_stack(), "us-east-1"), [])

    def test_cap_004_silent_when_versioning_enabled(self):
        self.assertEqual(ca.check_archive_not_versioned(clean_stack(), "us-east-1"), [])

    def test_cap_005_silent_when_lifecycle_active(self):
        self.assertEqual(ca.check_archive_no_lifecycle(clean_stack(), "us-east-1"), [])

    def test_cap_006_silent_when_no_correlation_across_days(self):
        self.assertEqual(ca.check_cross_cutting_risk(clean_stack(), "us-east-1"), [])

    def test_cap_007_silent_with_stable_finding_dedup(self):
        self.assertEqual(ca.check_finding_dedup(clean_stack(), "us-east-1"), [])

    def test_cap_008_silent_when_suppressions_file_present(self):
        self.assertEqual(ca.check_no_suppressions_file(clean_stack(), "us-east-1"), [])

    def test_cap_009_silent_when_alarm_watches_runner(self):
        self.assertEqual(ca.check_no_lambda_alarm(clean_stack(), "us-east-1"), [])

    def test_cap_010_silent_when_dashboard_exists(self):
        self.assertEqual(ca.check_no_dashboard(clean_stack(), "us-east-1"), [])

    def test_cap_011_silent_when_athena_database_exists(self):
        self.assertEqual(ca.check_no_athena_table(clean_stack(), "us-east-1"), [])

    def test_cap_012_silent_when_review_by_in_future(self):
        stack = clean_stack()
        stack["suppressions"] = [
            {"check_id": "COST-005", "resource_id": "vol-x", "reason": "test",
             "review_by": (NOW + timedelta(days=30)).isoformat()},
        ]
        self.assertEqual(ca.check_suppressions_stale(stack, "us-east-1"), [])

    def test_cap_013_silent_with_full_sla_object(self):
        # base_stack has all four severities defined via the default.
        self.assertEqual(ca.check_sla_not_defined(base_stack(), "us-east-1"), [])

    def test_cap_014_silent_when_reference_arch_disabled(self):
        # base_stack has reference_arch_enabled = False.
        self.assertEqual(ca.check_reference_arch_drift(base_stack(), "us-east-1"), [])

    def test_cap_015_silent_when_report_has_git_remote(self):
        self.assertEqual(ca.check_reports_lack_git_remote(clean_stack(), "us-east-1"), [])

    def test_cap_016_silent_on_acknowledged_reports(self):
        stack = clean_stack()
        # Age the report but acknowledge it.
        stack["reports"][0]["invoked_at"] = (NOW - timedelta(days=14)).isoformat()
        stack["reports"][0]["acknowledged"] = True
        self.assertEqual(ca.check_report_unread(stack, "us-east-1"), [])


###############################################################################
# TestContractTotals — STATE A, STATE B, STATE C
###############################################################################


class TestContractTotals(unittest.TestCase):

    def test_state_a_matches_the_contract(self):
        findings = run_all(base_stack())
        by_id = {}
        for f in findings:
            by_id[f.check_id] = by_id.get(f.check_id, 0) + 1

        self.assertEqual(len(findings), CONTRACT_FINDING_COUNT,
                         f"Expected {CONTRACT_FINDING_COUNT} findings from STATE A, "
                         f"got {len(findings)}: {by_id}")
        points = sum(f.weight for f in findings)
        self.assertEqual(points, CONTRACT_POINTS)
        self.assertEqual(ca.calculate_score(findings), CONTRACT_SCORE)
        self.assertTrue(ca.score_grade(CONTRACT_SCORE).startswith(CONTRACT_GRADE_LETTER))

        expected_ids = {
            "CAP-001", "CAP-004", "CAP-005", "CAP-008",
            "CAP-009", "CAP-010", "CAP-011",
        }
        self.assertEqual(set(by_id.keys()), expected_ids)

    def test_state_b_clean_produces_zero_findings(self):
        findings = run_all(clean_stack())
        self.assertEqual(findings, [],
                         f"clean_stack should be spotless; got {ids(findings)}")
        self.assertEqual(ca.calculate_score(findings), 100)

    def test_state_c_worse_than_state_a(self):
        """The day's central pedagogical demonstration.

        STATE A (all toggles off, informed bad posture): 54/100 D.
        STATE C (30 days after B, unchanged, silent break): 0/100 F.

        Score DROPS despite no configuration change. The scheduler
        stopped silently, reports piled up unread, one suppression
        aged past review. Every symptom is a lack of activity.
        """
        state_a = run_all(base_stack())
        state_c = run_all(decayed_stack())

        score_a = ca.calculate_score(state_a)
        score_c = ca.calculate_score(state_c)

        self.assertEqual(len(state_c), STATE_C_FINDING_COUNT)
        self.assertEqual(sum(f.weight for f in state_c), STATE_C_POINTS)
        self.assertEqual(score_c, STATE_C_SCORE)

        # The pedagogical assertion, in code.
        self.assertLess(score_c, score_a,
                        f"STATE C ({score_c}) should be worse than STATE A "
                        f"({score_a}) — that is the whole point of the day")

        # State C's exact fingerprint: CAP-003 once, CAP-012 once, CAP-016 four times.
        by_id = {}
        for f in state_c:
            by_id[f.check_id] = by_id.get(f.check_id, 0) + 1
        self.assertEqual(by_id.get("CAP-003"), 1)
        self.assertEqual(by_id.get("CAP-012"), 1)
        self.assertEqual(by_id.get("CAP-016"), 4)


###############################################################################
# TestScoring — floor, min-severity filter
###############################################################################


class TestScoring(unittest.TestCase):

    def test_score_floors_at_zero(self):
        findings = [
            ca.Finding(
                check_id="CAP-016", severity="CRITICAL",
                resource_type="X", resource_id=f"r-{i}",
                title="t", detail="d", remediation="r",
            )
            for i in range(10)
        ]
        self.assertEqual(ca.calculate_score(findings), 0)
        self.assertTrue(ca.score_grade(0).startswith("F"))
        self.assertTrue(ca.score_grade(95).startswith("A"))
        self.assertTrue(ca.score_grade(80).startswith("B"))
        self.assertTrue(ca.score_grade(65).startswith("C"))
        self.assertTrue(ca.score_grade(45).startswith("D"))

    def test_min_severity_filters_display_not_the_score(self):
        all_findings = run_all(base_stack())
        self.assertEqual(ca.calculate_score(all_findings), CONTRACT_SCORE)

        # HIGH+ filter drops the MEDIUMs.
        high_only = ca.filter_by_severity(all_findings, "HIGH")
        for f in high_only:
            self.assertIn(f.severity, ("CRITICAL", "HIGH"))
        # Score computed from the full list is unchanged.
        self.assertEqual(ca.calculate_score(all_findings), CONTRACT_SCORE)


###############################################################################
# TestSilentByDesign — CAP-013 and CAP-014
###############################################################################


class TestSilentByDesign(unittest.TestCase):

    def test_cap_013_cannot_fire_against_terraform_shaped_sla(self):
        """The sla_days_by_severity variable's type constraint requires all
        four severity keys. A stack shaped by this Terraform always has
        them, so the check stays silent."""
        stack = base_stack()  # includes the full SLA object.
        result = ca.check_sla_not_defined(stack, "us-east-1")
        self.assertEqual(result, [])

        # Also confirm firing on a bare/broken dict works — this is the
        # "off the shipped Terraform" case.
        stack["sla_days_by_severity"] = {}
        result = ca.check_sla_not_defined(stack, "us-east-1")
        self.assertNotEqual(result, [])

    def test_cap_014_cannot_fire_when_reference_arch_disabled(self):
        """enable_reference_arch defaults false. Without it, nothing claims
        to be reference and the drift check has nothing to compare."""
        stack = base_stack()
        stack["reference_arch_enabled"] = False
        # Even with drift data present, disabled means no fire.
        stack["reference_arch_findings_by_day"] = {
            "03": [{"check_id": "IAM-001", "resource_id": "role-x"}],
        }
        result = ca.check_reference_arch_drift(stack, "us-east-1")
        self.assertEqual(result, [])


###############################################################################
# TestDeliberateInteractions — cross-check patterns
###############################################################################


class TestDeliberateInteractions(unittest.TestCase):

    def test_cap_001_and_cap_003_are_different_faults(self):
        """CAP-001: no schedule at all. CAP-003: schedule exists but stopped."""
        # STATE A: only CAP-001 fires. CAP-003 needs a rule + reports.
        state_a = run_all(base_stack())
        got = {f.check_id for f in state_a}
        self.assertIn("CAP-001", got)
        self.assertNotIn("CAP-003", got)

        # STATE C: only CAP-003 fires. Schedule exists, is stale.
        state_c = run_all(decayed_stack())
        got_c = {f.check_id for f in state_c}
        self.assertNotIn("CAP-001", got_c)
        self.assertIn("CAP-003", got_c)

    def test_cap_004_and_cap_005_fire_independently_on_same_bucket(self):
        """Same bucket, different questions: versioning vs lifecycle. Neither
        finding remediates the other."""
        stack = base_stack()  # both off by default.
        v_findings = ca.check_archive_not_versioned(stack, "us-east-1")
        lc_findings = ca.check_archive_no_lifecycle(stack, "us-east-1")
        self.assertEqual(len(v_findings), 1)
        self.assertEqual(len(lc_findings), 1)
        # Both name the same bucket.
        self.assertEqual(v_findings[0].resource_id, lc_findings[0].resource_id)

    def test_cap_009_and_cap_016_are_symmetric_across_layers(self):
        """CAP-009: runner errors silently (technical failure).
        CAP-016: reports produced but nobody reads them (organisational).
        Both HIGH-ish. Both about a working programme with a broken loop."""
        stack_no_alarm = clean_stack()
        stack_no_alarm["cloudwatch_alarms"] = []
        alarm_findings = ca.check_no_lambda_alarm(stack_no_alarm, "us-east-1")
        self.assertEqual(len(alarm_findings), 1)

        stack_unread = clean_stack()
        stack_unread["reports"][0]["invoked_at"] = (NOW - timedelta(days=14)).isoformat()
        stack_unread["reports"][0]["acknowledged"] = False
        unread_findings = ca.check_report_unread(stack_unread, "us-east-1")
        self.assertEqual(len(unread_findings), 1)

        # The severities are HIGH vs CRITICAL — the org failure is worse.
        self.assertEqual(alarm_findings[0].severity, "HIGH")
        self.assertEqual(unread_findings[0].severity, "CRITICAL")


###############################################################################
# TestHelpers
###############################################################################


class TestHelpers(unittest.TestCase):

    def test_finding_dataclass_and_severity_validation(self):
        f = ca.Finding(
            check_id="CAP-001", severity="HIGH",
            resource_type="AWS::Account", resource_id="account/123",
            title="t", detail="d", remediation="r",
        )
        self.assertEqual(f.weight, 10)
        for sev, expected in [("CRITICAL", 25), ("HIGH", 10), ("MEDIUM", 4), ("LOW", 1), ("INFO", 0)]:
            self.assertEqual(ca.SEVERITY_WEIGHTS[sev], expected)
        with self.assertRaises(ValueError):
            ca.Finding(
                check_id="X", severity="ULTRA",
                resource_type="X", resource_id="y",
                title="t", detail="d", remediation="r",
            )

    def test_clock_helpers_and_check_registration(self):
        stack = {"now": NOW}
        self.assertEqual(ca._now(stack), NOW)
        age = ca._age_days(NOW - timedelta(days=10), NOW)
        self.assertAlmostEqual(age, 10.0)

        # Parse a rate() expression.
        self.assertEqual(ca._parse_schedule_interval_days("rate(7 days)"), 7.0)
        self.assertEqual(ca._parse_schedule_interval_days("rate(1 day)"), 1.0)
        self.assertAlmostEqual(ca._parse_schedule_interval_days("rate(24 hours)"), 1.0)
        self.assertIsNone(ca._parse_schedule_interval_days("cron(0 12 * * ? *)"))

        # CHECKS registers exactly 16.
        self.assertEqual(len(ca.CHECKS), 16)
        got_ids = [cid for cid, _ in ca.CHECKS]
        expected = [f"CAP-{i:03d}" for i in range(1, 17)]
        self.assertEqual(got_ids, expected)

        self.assertEqual(
            set(ca.RUNTIME_CHECKS),
            {"CAP-003", "CAP-012", "CAP-015", "CAP-016"},
        )
        for cid in ca.RUNTIME_CHECKS:
            self.assertIn(cid, got_ids)


###############################################################################
# TestRenderers — three formats
###############################################################################


class TestRenderers(unittest.TestCase):

    def test_render_table_with_and_without_findings(self):
        findings = shaped_findings()
        counts = {sev: CONTRACT_SHAPE.count(sev) for sev in set(CONTRACT_SHAPE)}
        stats = {
            "schedule_rules": 0, "reports": 0, "suppressions": 0,
            "cloudwatch_alarms": 0, "cloudwatch_dashboards": 0,
            "athena_databases": 0,
        }
        rendered = ca.render_table(findings, stats, ca.calculate_score(findings), False)
        self.assertIn("CAPSTONE AUDIT", rendered)
        for sev, n in counts.items():
            self.assertIn(f"{sev}: {n}", rendered)

        empty = ca.render_table([], stats, 100, False)
        self.assertIn("100/100", empty)
        self.assertIn("No findings", empty)

    def test_render_json_is_machine_readable(self):
        findings = shaped_findings()
        stats = {"reports": 0}
        payload = ca.render_json(findings, stats, ca.calculate_score(findings))
        parsed = json.loads(payload)
        self.assertEqual(parsed["audit"], "capstone_audit")
        self.assertEqual(parsed["day"], "10")
        self.assertEqual(parsed["finding_count"], CONTRACT_FINDING_COUNT)
        self.assertEqual(
            set(parsed["runtime_dependent_checks"]),
            {"CAP-003", "CAP-012", "CAP-015", "CAP-016"},
        )

    def test_render_csv_has_header_and_rows(self):
        findings = shaped_findings()
        rendered = ca.render_csv(findings)
        lines = rendered.strip().splitlines()
        self.assertEqual(len(lines), CONTRACT_FINDING_COUNT + 1)
        self.assertIn("check_id", lines[0])
        self.assertIn("severity", lines[0])
        self.assertIn("remediation", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
