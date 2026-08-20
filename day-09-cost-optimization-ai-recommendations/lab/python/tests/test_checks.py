#!/usr/bin/env python3
"""
test_checks.py — Day 09 unit tests for cost_audit.py

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

    cd lab/python
    python3 -m unittest discover -s tests -v

47 tests, standard library only, NO AWS CREDENTIALS REQUIRED. That is not a
convenience — it is the whole reason the auditor separates collect() from the
checks. Every check is a pure function over a plain dict, including the clock,
which arrives as stack["now"] rather than from datetime.now().

    16 FIRE      one per check, each proving the fault is detected
    16 SILENT    one per check, each proving clean input is not flagged
    15 COVERING  whole-stack totals, the score, the injected clock, the
                 silent-by-design checks, the deliberate interactions, the
                 helpers and the three renderers

To point these at the challenge file instead of the reference:

    COST_AUDIT_MODULE=cost_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests

THE RENDERER TESTS DO NOT CALL THE CHECKS. They build findings directly from
Finding(...) via CONTRACT_SHAPE, which reproduces the static state's severity
histogram (0 CRITICAL, 3 HIGH firing plus 1 more from insecure = 4 HIGH, 6
MEDIUM, 1 LOW = 11 findings, 69 points, score 31). Day 08 does this, and the
reason is practical: renderer tests that call the checks produce ERRORS
rather than clean failures when pointed at a stubbed challenge file, and an
error tells a learner nothing about what they got wrong.

=============================================================================
# CONTRACT-BEGIN
DAY 09 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/cost_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 08:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 09 uses CRITICAL, HIGH, MEDIUM and LOW, but not INFO. There is one LOW
(COST-010, gp2 vs gp3), because the choice is real, cheap and non-urgent —
unlike anything on Day 08. There is one CRITICAL (COST-016), because it is
the day's thesis: a cost anomaly nobody triaged is a bill nobody stopped,
and that is the failure mode the whole day exists to make concrete.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, enable_budget = false,
enable_cost_anomaly_monitor = false, enable_bucket_lifecycle = false,
enable_vpc_endpoints = false, enable_nat_gateway = false), before any
anomaly has been raised, before any triage, before any Savings Plan.

  ID        SEVERITY   W   N  PTS  SOURCE RESOURCE
  --------  --------  --  --  ---  ------------------------------------------
  COST-001  HIGH      10   1   10  account - no budget exists
  COST-002  HIGH      10   0    0  none - SILENT BY DESIGN, see below
  COST-003  HIGH      10   1   10  account - no anomaly monitor
  COST-004  MEDIUM     4   0    0  none - SILENT BY DESIGN, see below
  COST-005  HIGH      10   2   20  aws_ebs_volume.orphan_a, aws_ebs_volume.orphan_b
  COST-006  MEDIUM     4   2    8  aws_eip.orphan_a, aws_eip.orphan_b
  COST-007  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-008  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-009  MEDIUM     4   1    4  aws_instance.previous_gen
  COST-010  LOW        1   1    1  aws_instance.previous_gen root volume (gp2)
  COST-011  MEDIUM     4   1    4  aws_elb.classic
  COST-012  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-013  MEDIUM     4   2    8  aws_cloudwatch_log_group.unbounded_a and _b
  COST-014  MEDIUM     4   1    4  aws_s3_bucket.artifacts - no lifecycle
  COST-015  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  COST-016  CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  --------  --------  --  --  ---  ------------------------------------------
  TOTALS                    12   69

  TWELVE findings from SIXTEEN checks. Seven checks are silent here and
  they are silent for two different reasons, which is the most useful thing
  in this table: five because this particular stack cannot currently
  produce the fault (COST-007, COST-008, COST-012, COST-015, COST-016),
  and two because NO configuration of this stack can ever produce them
  (COST-002 and COST-004).

  Score: 100 - 69 = 31/100. Grade F.

  SEVERITY HISTOGRAM of the 16 checks: 1 CRITICAL, 4 HIGH, 10 MEDIUM,
  1 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: after apply, nothing configured        12      69   31/100      F
  B  Live: guardrails on, insecure examples
     off, endpoints on, tags at 100%                  0       0  100/100      A
  C  Thirty days after B, WITH NOTHING
     CHANGED - anomalies raised, none
     triaged, snapshots aged past retention           3      33   67/100      C
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: everything in B, plus a
     Savings Plan covering baseline usage
     AND each anomaly triaged within its
     SLA AND snapshots pruned by an aging
     rule                                             0       0  100/100      A

  STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

  Between B and C, nobody deploys anything. No console click, no apply, no
  merge. Three findings appear because time passed: COST-007 fires as
  snapshots the account has been quietly accumulating age past
  snapshot_retention_days; COST-015 fires as the app instance's uptime
  crosses long_running_instance_days without a Savings Plan being
  purchased; COST-016 fires as Cost Anomaly Detection has produced at least
  one anomaly which nobody has provided Feedback on within
  anomaly_triage_days.

  An audit that passes on the 1st fails on the 31st on an unchanged account.

  That is not a defect in the auditor. It is the correct behaviour, and it
  is the difference between a configuration audit and a cost audit. Cost is
  not a property of a configuration. It is a lagging measure of a decision
  nobody re-examined, and a claim about "we watch our spend" decays
  continuously from the last time somebody looked at Cost Explorer.

  Day 08's contract had a state that decayed WITHIN AN HOUR - DR-008
  fired the minute the newest recovery point aged past a 60-minute RPO,
  and the point was that a merge-time audit is blind to that. Day 09
  makes the same argument on a monthly timescale, with three separate
  decay paths so the pattern is undeniable rather than a single quirky
  check.

SILENT BY DESIGN - COST-002 (a budget with no notification threshold) and
COST-004 (cost allocation tag coverage below threshold).

  COST-002: No shipped default and no typo can produce this fault. The
  budget_notifications variable carries a validation refusing an empty
  list, and the aws_budgets_budget resource uses `dynamic "notification"`
  over that list. There is no path through this Terraform that produces a
  budget with zero notifications, so the plan refuses to.

  It is not a hypothetical fault. Every Billing console has a "create
  budget" wizard that will let you click through to a budget with no
  notifications attached, and every account with more than about ten
  budgets has one - usually created for a specific report that generated
  the CSV, and never revisited. A budget without a notification is a
  decorative object.

  COST-004: The AWS provider carries default_tags with Project and Owner,
  which are exactly the tags this check looks for. Every resource that
  goes through this Terraform plan inherits them automatically at create
  time - a resource without them is a resource that was NOT created by
  this plan. So the check stays silent against this stack even at 100%
  target coverage, and the same check fires on the account next door
  where somebody was creating buckets from a shell script.

  A check that stays silent because the stack cannot produce the fault is
  evidence that the auditor does not cry wolf.

SILENT BY SITUATION - COST-007, COST-008, COST-012, COST-015 and COST-016.

  COST-007 is the aged-snapshot check. A fresh terraform apply produces
  no snapshots at all, and even after the lab creates one for backup
  testing, snapshot_retention_days (default 90) is a long time. In a real
  account this fires readily - every automated backup rule accumulates
  copies unless a companion rule ages them out.

  COST-008 is the stopped-instance check. The app instance defaults to
  running; nothing in the lab stops it and leaves it for 30 days. In a
  real account it fires against forgotten test boxes.

  COST-012 is the NAT-without-endpoints check. enable_nat_gateway defaults
  to false, so there is no NAT gateway for the check to fire against. The
  moment somebody sets enable_nat_gateway = true WITHOUT setting
  enable_vpc_endpoints = true, it fires immediately with 4 points.

  COST-015 is the long-running-without-Savings-Plan check. The app
  instance was created seconds ago at apply time, so uptime is not yet
  above long_running_instance_days (default 30). This one fires with the
  clock alone, without anybody changing anything, and that is exactly the
  lesson of STATE C.

  COST-016 is the untriaged-anomaly check. With enable_cost_anomaly_monitor
  = false there is no monitor and no anomalies to triage. Once the
  monitor is enabled it needs roughly 10 days of baseline before producing
  its first anomaly. Once anomalies exist, this check fires with the
  CLOCK ALONE - no configuration change required - until somebody opens
  the console and marks the anomaly with Feedback.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING SILENT except the
  passage of time.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor: it cannot fire, so its silence is a property of the tool. Silent
by situation tells you nothing about the auditor and everything about
today's account - and "we have no findings" and "we have nothing to find"
are different states that render identically in every report. Never read
the second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  COST-001 AND COST-002 LOOK LIKE THE SAME CHECK AND ARE NOT. COST-001
  fires when NO budget exists. COST-002 fires when a budget EXISTS but has
  no notification threshold. The first is a missing guardrail; the second
  is a guardrail that speaks into the void. Fixing COST-001 by creating a
  budget with zero notifications is exactly what people do, and it is the
  transition COST-002 is there to catch. On this stack COST-002 is silent
  by design; on somebody else's stack it is the second-most common cost
  governance finding, after "no budget at all".

  COST-003 AND COST-016 ARE THE SAME PATTERN AT TWO LAYERS. COST-003 asks
  "does the anomaly detector exist"; COST-016 asks "does anybody read
  what it says". A monitor without a subscription is halfway there. A
  monitor with a subscription pointed at an unconfirmed SNS topic is
  three-quarters of the way there. A monitor with a subscription pointed
  at a confirmed SNS topic whose emails nobody reads is COST-016, and that
  is the shape of most real accounts that have "cost monitoring".

  COST-005 AND COST-006 ARE THE SAME IDEA AT DIFFERENT PRICE POINTS. An
  unattached EBS volume is $0.08/GB/month. An unassociated EIP is
  $3.60/month flat, since February 2024. Both are "resources billing for
  nothing", both accumulate in the same way (a stack that half-destroyed,
  a manual test that "we'll clean up later"), and both are worth
  surfacing separately so remediation is not one giant list.

  COST-009 AND COST-010 FIRE ON THE SAME INSTANCE and are not duplicates.
  COST-009 says "the instance family is previous-generation". COST-010
  says "the root volume type is superseded". Same resource, unrelated
  remediations, potentially different owners: the platform team owns the
  instance type, and the storage or database team may own the volume type.
  Fixing one leaves the other.

  COST-013 FIRES ONCE PER LOG GROUP, DELIBERATELY NOT DEDUPLICATED. Each
  log group is billed independently and each one has a separate person or
  pipeline whose logs land there. A single finding at "account has 40
  unbounded log groups" is a finding nobody knows how to remediate,
  because there is no single owner. Per-log-group findings can be routed
  to per-log-group owners.

  COST-015 IS THE ONLY CHECK THAT DEPENDS ON A SUBJECTIVE JUDGEMENT, and
  it is deliberately narrow to compensate. "Should we buy a Savings Plan"
  is a real, difficult decision that depends on how confident the team is
  that the workload will still exist in a year. The check does not answer
  it. It only asks "has anyone LOOKED at this question for a workload
  that has been running longer than a month". A "yes we looked, decided
  not to" answer is a suppression comment, not a finding to leave open -
  and the check's remediation language reflects that.

  COST-016 AND EVERY OTHER CHECK: it is the only CRITICAL because it is
  the only one where the failure mode is "the whole cost governance
  program does not work". Every other finding is a specific missing or
  wasteful resource. COST-016 is the meta-check: the machine is running,
  the alerts are firing, nobody is reading them. A stack where every
  other check is green and COST-016 is red is an account that has bought
  cost tooling and not yet started using it, which is the modal state of
  cost tooling.
# CONTRACT-END
=============================================================================
"""

import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

# Path shim so tests can be pointed at either the reference or the challenge.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

MODULE_NAME = os.environ.get("COST_AUDIT_MODULE", "cost_audit")
ca = __import__(MODULE_NAME)


###############################################################################
# Contract shape and shared fixtures
###############################################################################

# Severity histogram of the 12 STATE A findings. Counted:
#   HIGH:   COST-001 (1) + COST-003 (1) + COST-005 x 2 = 4
#   MEDIUM: COST-006 x 2 + COST-009 (1) + COST-011 (1) + COST-013 x 2 + COST-014 (1) = 7
#   LOW:    COST-010 (1) = 1
#   Total: 4 + 7 + 1 = 12 findings, 4*10 + 7*4 + 1*1 = 40+28+1 = 69 points.
CONTRACT_SHAPE = (
    "HIGH", "HIGH", "HIGH", "HIGH",
    "MEDIUM", "MEDIUM", "MEDIUM", "MEDIUM", "MEDIUM", "MEDIUM", "MEDIUM",
    "LOW",
)
CONTRACT_FINDING_COUNT = 12
CONTRACT_POINTS = 69
CONTRACT_SCORE = 31
CONTRACT_GRADE_LETTER = "F"

# A fixed reference clock. Every test uses this so results are deterministic
# across machines and CI runs.
NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _tag(**kwargs):
    """Convenience: build a boto3-shape list of tag dicts."""
    return [{"Key": k, "Value": str(v)} for k, v in kwargs.items()]


def base_stack(now=NOW):
    """The STATIC STATE A stack — the fixture that reproduces the contract.

    Shipped defaults: create_insecure_examples=true, guardrails off. Every
    field the auditor reads is populated; unused fields default to empty
    lists so silent checks are silent for the right reason.
    """
    project_owner = _tag(Owner="platform", Project="cbc-day09")

    return {
        "region": "us-east-1",
        "account_id": "123456789012",
        "now": now,
        # Thresholds from variables.tf defaults.
        "volume_orphan_days": 7,
        "eip_orphan_days": 7,
        "snapshot_retention_days": 90,
        "instance_stopped_days": 30,
        "long_running_instance_days": 30,
        "anomaly_triage_days": 7,
        "tag_coverage_threshold_percent": 90,
        "previous_gen_families": {"t2", "m3", "m4", "m5", "c3", "c4", "c5", "r3", "r4"},

        # No budget, no anomaly monitor -> COST-001, COST-003 fire.
        "budgets": [],
        "cost_anomaly_monitors": [],
        "cost_anomaly_subscriptions": [],
        "cost_anomalies": [],
        # No commitments -> COST-015 CAN fire if instance ages, but instance
        # is fresh in STATE A so it stays silent.
        "savings_plans": [],
        "reserved_instances": [],

        # Correct app instance (fresh) + previous-gen (COST-009).
        "instances": [
            {
                "InstanceId": "i-app",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "LaunchTime": now - timedelta(minutes=5),
                "BlockDeviceMappings": [{"Ebs": {"VolumeSize": 8}}],
                "Tags": project_owner + _tag(Name="cbc-day09-app"),
            },
            {
                "InstanceId": "i-prevgen",
                "InstanceType": "t2.micro",
                "State": {"Name": "running"},
                "LaunchTime": now - timedelta(minutes=5),
                "BlockDeviceMappings": [{"Ebs": {"VolumeSize": 8}}],
                "Tags": project_owner + _tag(Name="cbc-day09-previous-gen"),
            },
        ],

        # Two orphan volumes (COST-005) + previous-gen gp2 root (COST-010).
        "volumes": [
            {
                "VolumeId": "vol-orphan-a",
                "Size": 8,
                "VolumeType": "gp3",
                "State": "available",
                "CreateTime": now - timedelta(days=10),
                "Tags": project_owner + _tag(Name="cbc-day09-orphan-a"),
            },
            {
                "VolumeId": "vol-orphan-b",
                "Size": 8,
                "VolumeType": "gp3",
                "State": "available",
                "CreateTime": now - timedelta(days=10),
                "Tags": project_owner + _tag(Name="cbc-day09-orphan-b"),
            },
            {
                "VolumeId": "vol-app-root",
                "Size": 8,
                "VolumeType": "gp3",
                "State": "in-use",
                "CreateTime": now - timedelta(minutes=5),
                "Tags": project_owner,
            },
            {
                "VolumeId": "vol-prevgen-root",
                "Size": 8,
                "VolumeType": "gp2",
                "State": "in-use",
                "CreateTime": now - timedelta(minutes=5),
                "Tags": project_owner,
            },
        ],

        # No snapshots -> COST-007 silent by situation.
        "snapshots": [],

        # Two orphan EIPs -> COST-006 fires twice.
        "elastic_ips": [
            {"PublicIp": "1.2.3.4", "AllocationId": "eipalloc-a"},
            {"PublicIp": "1.2.3.5", "AllocationId": "eipalloc-b"},
        ],

        # VPC exists but no NAT -> COST-012 silent by situation.
        "vpcs": [{"VpcId": "vpc-1"}],
        "nat_gateways": [],
        "vpc_endpoints": [],

        # One bounded + two unbounded log groups -> COST-013 fires twice.
        "log_groups": [
            {
                "logGroupName": "/aws/cbc-day09/app",
                "retentionInDays": 30,
                "storedBytes": 0,
                "arn": "arn:aws:logs:us-east-1:123:log-group:/aws/cbc-day09/app",
                "Tags": {"Owner": "platform", "Project": "cbc-day09"},
            },
            {
                "logGroupName": "/aws/cbc-day09/unbounded-a",
                "retentionInDays": None,
                "storedBytes": 0,
                "arn": "arn:aws:logs:us-east-1:123:log-group:/aws/cbc-day09/unbounded-a",
                "Tags": {"Owner": "platform", "Project": "cbc-day09"},
            },
            {
                "logGroupName": "/aws/cbc-day09/unbounded-b",
                "retentionInDays": None,
                "storedBytes": 0,
                "arn": "arn:aws:logs:us-east-1:123:log-group:/aws/cbc-day09/unbounded-b",
                "Tags": {"Owner": "platform", "Project": "cbc-day09"},
            },
        ],

        # One bucket, no lifecycle -> COST-014 fires.
        "buckets": [
            {
                "Name": "cbc-day09-artifacts-abc",
                "LifecycleRules": [],
                "Tags": _tag(Owner="platform", Project="cbc-day09"),
            },
        ],

        # One Classic ELB -> COST-011 fires.
        "classic_elbs": [
            {"LoadBalancerName": "cbc-day09-classic"},
        ],
    }


def clean_stack(now=NOW):
    """STATE B — everything a passing account looks like.

    Guardrails on, insecure examples gone, endpoints attached, tags
    complete, no orphaned or superseded resources. Every check must return
    0 findings.
    """
    project_owner = _tag(Owner="platform", Project="cbc-day09")

    return {
        "region": "us-east-1",
        "account_id": "123456789012",
        "now": now,
        "volume_orphan_days": 7,
        "eip_orphan_days": 7,
        "snapshot_retention_days": 90,
        "instance_stopped_days": 30,
        "long_running_instance_days": 30,
        "anomaly_triage_days": 7,
        "tag_coverage_threshold_percent": 90,
        "previous_gen_families": {"t2", "m3", "m4", "m5", "c3", "c4", "c5", "r3", "r4"},

        # Budget with a notification and subscribers.
        "budgets": [
            {
                "BudgetName": "cbc-day09-monthly",
                "Notifications": [
                    {
                        "Notification": {
                            "NotificationType": "ACTUAL",
                            "Threshold": 80,
                        },
                        "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "ops@example.com"}],
                    }
                ],
            }
        ],
        "cost_anomaly_monitors": [
            {"MonitorArn": "arn:aws:ce::123:anomalymonitor/m1", "MonitorName": "acct"}
        ],
        "cost_anomaly_subscriptions": [{"SubscriptionArn": "arn:aws:ce::123:anomalysubscription/s1"}],
        "cost_anomalies": [],
        "savings_plans": [],
        "reserved_instances": [],

        "instances": [
            {
                "InstanceId": "i-app",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "LaunchTime": now - timedelta(minutes=5),
                "BlockDeviceMappings": [{"Ebs": {"VolumeSize": 8}}],
                "Tags": project_owner + _tag(Name="cbc-day09-app"),
            },
        ],
        "volumes": [
            {
                "VolumeId": "vol-app-root",
                "Size": 8,
                "VolumeType": "gp3",
                "State": "in-use",
                "CreateTime": now - timedelta(minutes=5),
                "Tags": project_owner,
            },
        ],
        "snapshots": [],
        "elastic_ips": [],
        "vpcs": [{"VpcId": "vpc-1"}],
        "nat_gateways": [],
        "vpc_endpoints": [],
        "log_groups": [
            {
                "logGroupName": "/aws/cbc-day09/app",
                "retentionInDays": 30,
                "storedBytes": 0,
                "arn": "arn:aws:logs:us-east-1:123:log-group:/aws/cbc-day09/app",
                "Tags": {"Owner": "platform", "Project": "cbc-day09"},
            },
        ],
        "buckets": [
            {
                "Name": "cbc-day09-artifacts-abc",
                "LifecycleRules": [
                    {"ID": "expire", "Status": "Enabled"},
                ],
                "Tags": _tag(Owner="platform", Project="cbc-day09"),
            },
        ],
        "classic_elbs": [],
    }


def decayed_stack(now=None):
    """STATE C — clean_stack, 30 days later, with the three decay findings.

    Nobody changed the configuration. Three findings appear because time
    passed: COST-007 (old snapshot), COST-015 (uptime crossed threshold),
    COST-016 (anomaly untriaged). Expected: 3 findings, 33 points,
    67/100, grade C.
    """
    later = now or (NOW + timedelta(days=30))
    stack = clean_stack(later)

    # Age the instance so COST-015 fires (no SP, no RI).
    stack["instances"][0]["LaunchTime"] = later - timedelta(days=45)

    # Add an old snapshot so COST-007 fires.
    stack["snapshots"] = [
        {
            "SnapshotId": "snap-old",
            "VolumeSize": 8,
            "StartTime": later - timedelta(days=120),
            "Description": "backup from previous quarter",
        }
    ]

    # Add an untriaged anomaly so COST-016 fires.
    stack["cost_anomalies"] = [
        {
            "AnomalyId": "anom-1",
            "AnomalyStartDate": (later - timedelta(days=14)).isoformat(),
            "Feedback": None,
            "Impact": {"TotalImpact": 42.50},
        }
    ]
    return stack


def run_all(stack):
    """Run every registered check against a stack, in check order."""
    out = []
    for _check_id, check in ca.CHECKS:
        out += check(stack, stack["region"])
    return out


def ids(findings):
    """Sorted list of check IDs in a finding list."""
    return sorted(f.check_id for f in findings)


def shaped_findings():
    """Build Finding objects whose severities match STATE A's histogram,
    without calling any of the checks. Used by the renderer tests so they
    remain green when pointed at a stubbed challenge file."""
    out = []
    for i, severity in enumerate(CONTRACT_SHAPE, 1):
        out.append(ca.Finding(
            check_id=f"COST-{i:03d}",
            severity=severity,
            resource_type="AWS::Test::Resource",
            resource_id=f"res-{i}",
            title=f"synthetic {severity} finding",
            detail="detail",
            remediation="fix it",
        ))
    return out


###############################################################################
# TestChecksFire — one per check, each proving the fault is detected
###############################################################################


class TestChecksFire(unittest.TestCase):

    def test_cost_001_fires_when_no_budget_exists(self):
        stack = base_stack()
        result = ca.check_no_budget(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-001")
        self.assertEqual(result[0].severity, "HIGH")

    def test_cost_002_fires_on_budget_without_subscribers(self):
        # A one-off budget that made it into an account via the console
        # wizard: notification exists, subscriber list is empty.
        stack = base_stack()
        stack["budgets"] = [
            {
                "BudgetName": "console-created",
                "Notifications": [{"Notification": {"Threshold": 80}, "Subscribers": []}],
            }
        ]
        result = ca.check_budget_no_notification(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-002")

    def test_cost_003_fires_when_no_anomaly_monitor(self):
        stack = base_stack()
        result = ca.check_no_anomaly_monitor(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-003")

    def test_cost_004_fires_when_coverage_below_threshold(self):
        # Untag every resource so coverage drops to zero.
        stack = base_stack()
        for r in stack["instances"] + stack["volumes"] + stack["buckets"]:
            r["Tags"] = []
        for g in stack["log_groups"]:
            g["Tags"] = {}
        result = ca.check_tag_coverage(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-004")
        self.assertLess(result[0].evidence["coverage_percent"], 90)

    def test_cost_005_fires_per_orphan_volume(self):
        result = ca.check_orphan_volumes(base_stack(), "us-east-1")
        self.assertEqual(len(result), 2)
        for f in result:
            self.assertEqual(f.check_id, "COST-005")
            self.assertEqual(f.severity, "HIGH")

    def test_cost_006_fires_per_unassociated_eip(self):
        result = ca.check_orphan_eips(base_stack(), "us-east-1")
        self.assertEqual(len(result), 2)
        for f in result:
            self.assertEqual(f.check_id, "COST-006")

    def test_cost_007_fires_on_old_snapshot(self):
        stack = base_stack()
        stack["snapshots"] = [
            {"SnapshotId": "snap-1", "VolumeSize": 8,
             "StartTime": NOW - timedelta(days=120), "Description": "old"}
        ]
        result = ca.check_old_snapshots(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-007")

    def test_cost_008_fires_on_long_stopped_instance(self):
        stack = base_stack()
        stack["instances"].append({
            "InstanceId": "i-stopped",
            "InstanceType": "t3.large",
            "State": {"Name": "stopped"},
            "StateTransitionReason": "User initiated (2025-04-01 09:00:00 GMT)",
            "LaunchTime": NOW - timedelta(days=200),
            "BlockDeviceMappings": [{"Ebs": {"VolumeSize": 500}}],
            "Tags": _tag(Owner="platform", Project="cbc-day09", Name="forgotten"),
        })
        result = ca.check_stopped_instances(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-008")

    def test_cost_009_fires_on_previous_gen_family(self):
        result = ca.check_previous_gen_instance(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-009")
        self.assertEqual(result[0].resource_id, "i-prevgen")

    def test_cost_010_fires_on_gp2_volume(self):
        result = ca.check_gp2_volumes(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-010")
        self.assertEqual(result[0].severity, "LOW")

    def test_cost_011_fires_on_classic_elb(self):
        result = ca.check_classic_elb(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-011")

    def test_cost_012_fires_on_nat_without_endpoints(self):
        stack = base_stack()
        stack["nat_gateways"] = [
            {"NatGatewayId": "nat-1", "VpcId": "vpc-1", "State": "available"}
        ]
        # No gateway endpoints in stack["vpc_endpoints"].
        result = ca.check_nat_without_endpoints(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-012")

    def test_cost_013_fires_per_unbounded_log_group(self):
        result = ca.check_unbounded_log_groups(base_stack(), "us-east-1")
        self.assertEqual(len(result), 2)
        for f in result:
            self.assertEqual(f.check_id, "COST-013")

    def test_cost_014_fires_on_bucket_without_lifecycle(self):
        result = ca.check_bucket_no_lifecycle(base_stack(), "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-014")

    def test_cost_015_fires_on_long_running_no_savings_plan(self):
        # Clean stack with an aged instance and no commitments -> fires.
        stack = clean_stack()
        stack["instances"][0]["LaunchTime"] = NOW - timedelta(days=45)
        result = ca.check_long_running_no_savings_plan(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-015")

    def test_cost_016_fires_on_untriaged_anomaly(self):
        stack = base_stack()
        stack["cost_anomalies"] = [
            {
                "AnomalyId": "anom-x",
                "AnomalyStartDate": (NOW - timedelta(days=14)).isoformat(),
                "Feedback": None,
                "Impact": {"TotalImpact": 100.0},
            }
        ]
        result = ca.check_untriaged_anomalies(stack, "us-east-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].check_id, "COST-016")
        self.assertEqual(result[0].severity, "CRITICAL")


###############################################################################
# TestChecksSilent — one per check, each proving clean input is not flagged
###############################################################################


class TestChecksSilent(unittest.TestCase):

    def test_cost_001_silent_when_a_budget_exists(self):
        stack = clean_stack()
        self.assertEqual(ca.check_no_budget(stack, "us-east-1"), [])

    def test_cost_002_silent_on_budget_with_subscribers(self):
        stack = clean_stack()
        self.assertEqual(ca.check_budget_no_notification(stack, "us-east-1"), [])

    def test_cost_003_silent_when_monitor_exists(self):
        stack = clean_stack()
        self.assertEqual(ca.check_no_anomaly_monitor(stack, "us-east-1"), [])

    def test_cost_004_silent_at_full_coverage(self):
        # Full coverage on the base stack (default_tags analogue).
        stack = base_stack()
        self.assertEqual(ca.check_tag_coverage(stack, "us-east-1"), [])

    def test_cost_005_silent_on_attached_volume(self):
        stack = clean_stack()
        self.assertEqual(ca.check_orphan_volumes(stack, "us-east-1"), [])

    def test_cost_006_silent_on_associated_eip(self):
        stack = clean_stack()
        stack["elastic_ips"] = [
            {"PublicIp": "1.2.3.4", "AllocationId": "eipalloc-a",
             "AssociationId": "eipassoc-a"}
        ]
        self.assertEqual(ca.check_orphan_eips(stack, "us-east-1"), [])

    def test_cost_007_silent_on_fresh_snapshot(self):
        stack = clean_stack()
        stack["snapshots"] = [
            {"SnapshotId": "snap-fresh", "VolumeSize": 8,
             "StartTime": NOW - timedelta(days=3), "Description": "fresh"}
        ]
        self.assertEqual(ca.check_old_snapshots(stack, "us-east-1"), [])

    def test_cost_008_silent_on_running_instance(self):
        stack = clean_stack()
        self.assertEqual(ca.check_stopped_instances(stack, "us-east-1"), [])

    def test_cost_009_silent_on_current_gen(self):
        stack = clean_stack()  # instance is t3.micro
        self.assertEqual(ca.check_previous_gen_instance(stack, "us-east-1"), [])

    def test_cost_010_silent_on_gp3(self):
        stack = clean_stack()  # only volume is gp3
        self.assertEqual(ca.check_gp2_volumes(stack, "us-east-1"), [])

    def test_cost_011_silent_without_classic_elb(self):
        stack = clean_stack()
        self.assertEqual(ca.check_classic_elb(stack, "us-east-1"), [])

    def test_cost_012_silent_with_endpoints_present(self):
        stack = clean_stack()
        stack["nat_gateways"] = [
            {"NatGatewayId": "nat-1", "VpcId": "vpc-1", "State": "available"}
        ]
        stack["vpc_endpoints"] = [
            {"VpcId": "vpc-1", "VpcEndpointType": "Gateway",
             "ServiceName": "com.amazonaws.us-east-1.s3"},
            {"VpcId": "vpc-1", "VpcEndpointType": "Gateway",
             "ServiceName": "com.amazonaws.us-east-1.dynamodb"},
        ]
        self.assertEqual(ca.check_nat_without_endpoints(stack, "us-east-1"), [])

    def test_cost_013_silent_when_retention_set(self):
        stack = clean_stack()
        self.assertEqual(ca.check_unbounded_log_groups(stack, "us-east-1"), [])

    def test_cost_014_silent_when_lifecycle_active(self):
        stack = clean_stack()
        self.assertEqual(ca.check_bucket_no_lifecycle(stack, "us-east-1"), [])

    def test_cost_015_silent_with_active_savings_plan(self):
        # Aged instance, but a Savings Plan exists -> stays silent.
        stack = clean_stack()
        stack["instances"][0]["LaunchTime"] = NOW - timedelta(days=90)
        stack["savings_plans"] = [{"savingsPlanId": "sp-1", "state": "active"}]
        self.assertEqual(ca.check_long_running_no_savings_plan(stack, "us-east-1"), [])

    def test_cost_016_silent_when_feedback_provided(self):
        stack = clean_stack()
        stack["cost_anomalies"] = [
            {
                "AnomalyId": "anom-triaged",
                "AnomalyStartDate": (NOW - timedelta(days=30)).isoformat(),
                "Feedback": "YES",
                "Impact": {"TotalImpact": 50.0},
            }
        ]
        self.assertEqual(ca.check_untriaged_anomalies(stack, "us-east-1"), [])


###############################################################################
# TestContractTotals — STATE A, STATE B (clean), STATE C (decay)
###############################################################################


class TestContractTotals(unittest.TestCase):

    def test_static_state_a_matches_the_contract(self):
        findings = run_all(base_stack())
        by_id = {}
        for f in findings:
            by_id[f.check_id] = by_id.get(f.check_id, 0) + 1

        self.assertEqual(len(findings), CONTRACT_FINDING_COUNT,
                         f"Expected {CONTRACT_FINDING_COUNT} findings from STATE A, "
                         f"got {len(findings)}: {by_id}")

        points = sum(f.weight for f in findings)
        self.assertEqual(points, CONTRACT_POINTS,
                         f"Expected {CONTRACT_POINTS} points, got {points}")

        score = ca.calculate_score(findings)
        self.assertEqual(score, CONTRACT_SCORE)

        expected_ids = {
            "COST-001", "COST-003",
            "COST-005", "COST-006",
            "COST-009", "COST-010", "COST-011",
            "COST-013", "COST-014",
        }
        self.assertEqual(set(by_id.keys()), expected_ids)
        # Multi-fire checks.
        self.assertEqual(by_id["COST-005"], 2)
        self.assertEqual(by_id["COST-006"], 2)
        self.assertEqual(by_id["COST-013"], 2)

    def test_state_b_clean_produces_zero_findings(self):
        findings = run_all(clean_stack())
        self.assertEqual(findings, [],
                         f"clean_stack should be spotless; got {ids(findings)}")
        self.assertEqual(ca.calculate_score(findings), 100)

    def test_state_c_the_clock_alone_changes_the_answer(self):
        # This is the day's thesis in one test. Nothing about the CONFIG
        # changes between clean_stack and decayed_stack. The clock does.
        # Three findings appear.
        findings = run_all(decayed_stack())
        self.assertEqual(len(findings), 3,
                         f"STATE C should have exactly 3 findings; got {ids(findings)}")
        got = sorted(f.check_id for f in findings)
        self.assertEqual(got, ["COST-007", "COST-015", "COST-016"])

        # 4 + 4 + 25 = 33 points; 100 - 33 = 67; grade C.
        points = sum(f.weight for f in findings)
        self.assertEqual(points, 33)
        self.assertEqual(ca.calculate_score(findings), 67)
        self.assertTrue(ca.score_grade(67).startswith("C"))


###############################################################################
# TestScoring — floor, min-severity filter
###############################################################################


class TestScoring(unittest.TestCase):

    def test_score_floors_at_zero_and_grades_correctly(self):
        # Ten CRITICAL findings = 250 points; score would be -150 without
        # the floor.
        findings = [
            ca.Finding(
                check_id="COST-016", severity="CRITICAL",
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
        # The full STATE A score (31) must be independent of what
        # --min-severity is displaying.
        all_findings = run_all(base_stack())
        self.assertEqual(ca.calculate_score(all_findings), CONTRACT_SCORE)

        # HIGH+ filter drops COST-006/009/010/011/013/014 -> 4 remain
        # (COST-001, COST-003, COST-005 x 2).
        high_only = ca.filter_by_severity(all_findings, "HIGH")
        self.assertEqual(len(high_only), 4)
        # Score computed from the FULL list is still 31.
        self.assertEqual(ca.calculate_score(all_findings), CONTRACT_SCORE)


###############################################################################
# TestSilentByDesign — COST-002 and COST-004 cannot fire against this stack
###############################################################################


class TestSilentByDesign(unittest.TestCase):

    def test_cost_002_cannot_fire_against_a_terraform_produced_budget(self):
        # A budget produced by this stack always carries at least one
        # notification with at least one subscriber, because the
        # budget_notifications variable's validation refuses an empty list.
        # We simulate that here: any budget shape produced by the plan
        # keeps COST-002 silent.
        stack = base_stack()
        stack["budgets"] = [
            {
                "BudgetName": "cbc-day09-monthly",
                "Notifications": [
                    {
                        "Notification": {"Threshold": 80, "NotificationType": "ACTUAL"},
                        "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "a@b"}],
                    },
                    {
                        "Notification": {"Threshold": 100, "NotificationType": "ACTUAL"},
                        "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "a@b"}],
                    },
                ],
            }
        ]
        self.assertEqual(ca.check_budget_no_notification(stack, "us-east-1"), [])

    def test_cost_004_cannot_fire_against_default_tagged_resources(self):
        # default_tags on the provider guarantees Owner+Project on every
        # Terraform-created resource. STATE A's base stack has 100%
        # coverage; the check does not fire even against the noisy
        # insecure-examples population.
        stack = base_stack()
        result = ca.check_tag_coverage(stack, "us-east-1")
        self.assertEqual(result, [])


###############################################################################
# TestDeliberateInteractions — cross-check patterns that are not bugs
###############################################################################


class TestDeliberateInteractions(unittest.TestCase):

    def test_cost_009_and_cost_010_fire_on_the_same_instance(self):
        # Previous-generation family AND gp2 root are two different
        # remediations, potentially owned by different teams. Both must
        # fire against the shipped previous_gen instance.
        stack = base_stack()
        gen_findings = ca.check_previous_gen_instance(stack, "us-east-1")
        gp_findings = ca.check_gp2_volumes(stack, "us-east-1")
        self.assertEqual(len(gen_findings), 1)
        self.assertEqual(len(gp_findings), 1)
        # The instance and its root belong together.
        self.assertEqual(gen_findings[0].resource_id, "i-prevgen")
        self.assertEqual(gp_findings[0].resource_id, "vol-prevgen-root")

    def test_cost_005_and_cost_006_fire_at_different_price_points(self):
        # Same 'billing for nothing' idea, deliberately kept separate so
        # remediation is not one big undifferentiated list.
        stack = base_stack()
        vol_findings = ca.check_orphan_volumes(stack, "us-east-1")
        eip_findings = ca.check_orphan_eips(stack, "us-east-1")
        self.assertEqual(len(vol_findings), 2)
        self.assertEqual(len(eip_findings), 2)
        # Volumes are HIGH; EIPs are MEDIUM.
        self.assertTrue(all(f.severity == "HIGH" for f in vol_findings))
        self.assertTrue(all(f.severity == "MEDIUM" for f in eip_findings))

    def test_cost_013_fires_once_per_log_group_deliberately(self):
        # Deduplicating to "the account has N unbounded log groups" would
        # produce a finding nobody can route. Per-group findings can go to
        # per-group owners.
        stack = base_stack()
        # Add a third unbounded group.
        stack["log_groups"].append({
            "logGroupName": "/aws/lambda/some-forgotten-fn",
            "retentionInDays": None,
            "storedBytes": 0,
            "arn": "arn:aws:logs:us-east-1:123:log-group:/aws/lambda/some-forgotten-fn",
            "Tags": {"Owner": "platform", "Project": "cbc-day09"},
        })
        result = ca.check_unbounded_log_groups(stack, "us-east-1")
        self.assertEqual(len(result), 3)
        self.assertEqual(len({f.resource_id for f in result}), 3)


###############################################################################
# TestHelpers
###############################################################################


class TestHelpers(unittest.TestCase):

    def test_finding_dataclass_weight_and_severity_validation(self):
        f = ca.Finding(
            check_id="COST-001", severity="HIGH",
            resource_type="AWS::Account", resource_id="account/123",
            title="t", detail="d", remediation="r",
        )
        self.assertEqual(f.weight, 10)
        self.assertEqual(ca.SEVERITY_WEIGHTS["CRITICAL"], 25)
        self.assertEqual(ca.SEVERITY_WEIGHTS["HIGH"], 10)
        self.assertEqual(ca.SEVERITY_WEIGHTS["MEDIUM"], 4)
        self.assertEqual(ca.SEVERITY_WEIGHTS["LOW"], 1)
        self.assertEqual(ca.SEVERITY_WEIGHTS["INFO"], 0)
        with self.assertRaises(ValueError):
            ca.Finding(
                check_id="X", severity="ULTRA",
                resource_type="X", resource_id="y",
                title="t", detail="d", remediation="r",
            )

    def test_clock_helpers_and_check_registration(self):
        # _now honours stack["now"], _age_days does the arithmetic, and
        # CHECKS registers exactly 16 checks with stable IDs.
        stack = {"now": NOW}
        self.assertEqual(ca._now(stack), NOW)

        age = ca._age_days(NOW - timedelta(days=10), NOW)
        self.assertAlmostEqual(age, 10.0)

        self.assertEqual(len(ca.CHECKS), 16)
        got_ids = [cid for cid, _ in ca.CHECKS]
        expected = [f"COST-{i:03d}" for i in range(1, 17)]
        self.assertEqual(got_ids, expected)

        # RUNTIME_CHECKS names the four decay-prone checks. All must be
        # in CHECKS.
        self.assertEqual(
            set(ca.RUNTIME_CHECKS),
            {"COST-007", "COST-008", "COST-015", "COST-016"},
        )
        for cid in ca.RUNTIME_CHECKS:
            self.assertIn(cid, got_ids)


###############################################################################
# TestRenderers — three formats, none of them calling the checks
###############################################################################


class TestRenderers(unittest.TestCase):

    def test_render_table_with_and_without_findings(self):
        findings = shaped_findings()
        counts = {sev: CONTRACT_SHAPE.count(sev) for sev in set(CONTRACT_SHAPE)}
        stats = {
            "instances": 2, "volumes": 4, "snapshots": 0, "elastic_ips": 2,
            "vpcs": 1, "log_groups": 3, "buckets": 1, "classic_elbs": 1,
            "budgets": 0, "cost_anomaly_monitors": 0, "cost_anomalies": 0,
        }
        rendered = ca.render_table(findings, stats, ca.calculate_score(findings), False)
        self.assertIn("COST OPTIMISATION AUDIT", rendered)
        # Summary line shows non-zero counts of the histogram.
        for sev, n in counts.items():
            self.assertIn(f"{sev}: {n}", rendered)

        # And with an empty list.
        empty = ca.render_table([], stats, 100, False)
        self.assertIn("100/100", empty)
        self.assertIn("No findings", empty)

    def test_render_json_is_machine_readable(self):
        findings = shaped_findings()
        stats = {"instances": 2}
        payload = ca.render_json(findings, stats, ca.calculate_score(findings))
        parsed = json.loads(payload)
        self.assertEqual(parsed["audit"], "cost_audit")
        self.assertEqual(parsed["day"], "09")
        self.assertEqual(parsed["finding_count"], CONTRACT_FINDING_COUNT)
        # The JSON payload names the runtime-dependent checks, so a
        # consumer diffing two runs can distinguish clock-driven changes
        # from configuration changes.
        self.assertEqual(
            set(parsed["runtime_dependent_checks"]),
            {"COST-007", "COST-008", "COST-015", "COST-016"},
        )

    def test_render_csv_has_header_and_rows(self):
        findings = shaped_findings()
        rendered = ca.render_csv(findings)
        lines = rendered.strip().splitlines()
        # Header + 11 rows.
        self.assertEqual(len(lines), CONTRACT_FINDING_COUNT + 1)
        self.assertIn("check_id", lines[0])
        self.assertIn("severity", lines[0])
        self.assertIn("remediation", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
