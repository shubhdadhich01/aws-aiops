#!/usr/bin/env python3
"""
test_checks.py — unit tests for Day 07's sec_audit.py.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

    cd lab/python
    python3 -m unittest discover -s tests

47 tests. No credentials, no AWS account, no network, no pytest — stdlib
unittest and nothing else, in well under a second.

Composition
-----------
    16  FIRE      one per check, proving it catches the fault it exists for
    16  SILENT    one per check, proving it does NOT fire on correct input
    15  WHOLE     the stack totals, the score, the static/live divergence, the
                  injected clock, the two silent checks, the deliberate check
                  interactions, the policy helpers, and the three renderers

The fire/silent pairing is worth more than either half alone. A check with only
a fire test is a check that might flag everything, and any check that cannot be
made to shut up on correct input will be suppressed in week two — at which
point it does nothing at all.

Why the clock is a fixture argument
-----------------------------------
Three checks here are age-based: SEC-003 (untriaged findings), SEC-011 (a
rotation that has not run) and SEC-013 (an old access key). `sec_audit` reads
the clock from `stack["now"]` rather than calling `datetime.now()`, and that
one decision is what makes those three testable at all.

It also lets the suite assert the thing SEC-013 is actually about: the same
account, unchanged in every respect, passes today and fails in ninety-one days.
A test that had to wait three months for that would not exist.

=============================================================================
DAY 07 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
lab/python/sec_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 06:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true), before anything has been invoked and
before rotation has run.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  SEC-001  CRITICAL  25   0    0  none - GuardDuty is enabled
  SEC-002  HIGH      10   0    0  none - Security Hub is enabled with a standard
  SEC-003  MEDIUM     4   0    0  none - no findings exist yet. LIVE ONLY.
  SEC-004  LOW        1   0    0  none - SILENT BY DESIGN, see below
  SEC-005  CRITICAL  25   1   25  aws_lambda_function.naive_responder
  SEC-006  HIGH      10   1   10  aws_cloudtrail.shadow
  SEC-007  HIGH      10   1   10  aws_cloudtrail.shadow
  SEC-008  CRITICAL  25   1   25  aws_iam_role_policy.naive_responder
  SEC-009  HIGH      10   1   10  aws_s3_bucket.shadow
  SEC-010  MEDIUM     4   1    4  aws_secretsmanager_secret.legacy
  SEC-011  HIGH      10   1   10  aws_secretsmanager_secret.app
  SEC-012  CRITICAL  25   1   25  aws_lambda_function.naive_responder
  SEC-013  MEDIUM     4   0    0  none - SILENT BY SITUATION, see below
  SEC-014  HIGH      10   1   10  aws_lambda_function.naive_responder
  SEC-015  MEDIUM     4   1    4  aws_cloudwatch_event_rule.naive_responder
  SEC-016  MEDIUM     4   1    4  aws_cloudwatch_event_target.naive_responder
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    11  137

  ELEVEN findings from SIXTEEN checks. Five are silent at this point and they
  are silent for four different reasons, which is the most useful thing in
  this table: two because the stack is built correctly (SEC-001, SEC-002), one
  because it reads runtime state that does not exist yet (SEC-003), one
  because the stack cannot produce the fault (SEC-004), and one because not
  enough time has passed (SEC-013).

  Score: 100 - 137 = -37, floored to 0/100. Grade F.

THE THREE STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  Static: after apply, before anything runs          11     137    0/100      F
  Live: after lab steps 1-5 — sample findings
    generated and left unresolved, and one
    rotation forced                                  11     131    0/100      F
  After lab step 8 — publishing frequency set
    to SIX_HOURS, and max_access_key_age_days
    lowered to 0                                     13     136    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  Reference build: create_insecure_examples =
    false, after rotation has run at least once       0       0  100/100      A

  STATIC AND LIVE HAVE THE SAME COUNT AND A DIFFERENT SET, AND THAT IS THE
  POINT. Two checks move in opposite directions between them:

    SEC-011 FIRES at static and goes SILENT live. Rotation is configured but
            has never run, because rotate_immediately is false. Forcing one
            rotation in lab step 5 clears it.
    SEC-003 is SILENT at static and FIRES live. It reads the age of unresolved
            findings, and there are none until you generate them.

  Eleven findings before, eleven after, six points apart, and a different
  problem. NEVER DIFF ON THE COUNT. Two audit runs with the same total can
  describe completely different accounts, and a dashboard that trends the
  number without the set is worse than no dashboard.

  This is also the direct contrast with Day 06, where static and live were
  IDENTICAL because every check read configuration only. Day 07 has checks
  that read runtime state — findings, rotation history, key age — and the
  moment an auditor does that, "when you ran it" becomes part of the answer.

  Setting create_insecure_examples = false BEFORE rotation has run leaves
  exactly one finding — SEC-011 — for 10 points and 90/100, grade A. Both
  conditions are needed for 100/100.

SILENT BY DESIGN — SEC-004, GuardDuty finding publishing frequency left at
SIX_HOURS. The variable defaults to FIFTEEN_MINUTES and its validation accepts
only the three documented values, so no shipped default and no typo can
produce the fault. The check fires only if somebody edits the variable on
purpose, which lab step 8a asks you to do. A check that stays silent because
the stack cannot produce the fault is evidence that the auditor does not cry
wolf.

SILENT BY SITUATION — SEC-013, an active IAM access key older than
max_access_key_age_days. The deliberately broken example creates exactly the
credential this check exists to find, and the check does not fire, because the
key is hours old.

  NOTHING HAS TO CHANGE FOR THAT TO STOP BEING TRUE. No edit, no deploy, no
  console click. In 91 days the same unchanged account fails the same
  unchanged check. The calendar is the situation.

  That makes SEC-013 the clearest argument in this repo for running an auditor
  on a SCHEDULE rather than at merge time. A merge-time-only audit certifies
  the account as it was on the day somebody last changed it, and a
  point-in-time pass is not a property that persists.

  Lab step 8b sets max_access_key_age_days to 0 to make the point in a second
  rather than in three months.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor. Silent by situation tells you nothing about the auditor and
everything about today — and in SEC-013's case, only about today. Never read
the second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  SEC-005 and SEC-012 both fire on aws_lambda_function.naive_responder, and
  they are not duplicates. SEC-005 is about WHEN it acts (a severity threshold
  rather than a reviewed allow-list of finding types). SEC-012 is about WHAT
  it does when it acts (an intent to terminate rather than to isolate). Fixing
  one leaves the other, and they have different owners in most organisations.

  SEC-012 fires on CONFIGURED INTENT, not on observed behaviour. The shared
  responder code refuses CONTAINMENT_MODE=terminate and changes nothing, which
  is correct and does not make the configuration acceptable — the next person
  to "fix" the responder will implement what the configuration asks for.

  SEC-014 (no kill switch) is scoped to functions that can actually take an
  action. A read-only Lambda with no containment permissions does not need a
  brake, and flagging it would train people to ignore the check.

  SEC-016 reports on the TARGET, not the rule. One rule with three targets and
  no dead-letter queue is three findings, because each target is a separate
  path a detection can vanish down.

  SEC-011 requires rotation to be CONFIGURED before it can fire. A secret with
  no rotation at all is SEC-010, not SEC-011 — one finding, not two, and the
  remediations are different: SEC-010 is "decide whether this should rotate",
  SEC-011 is "it says it rotates and it does not".
=============================================================================
"""

import importlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PYTHON_DIR)
sys.path.insert(0, os.path.join(_PYTHON_DIR, "challenge"))

# Which implementation to test. Defaults to the reference; point the whole
# suite at your own work while building it:
#
#     SEC_AUDIT_MODULE=sec_audit_challenge python3 -m unittest discover -s tests
A = importlib.import_module(os.environ.get("SEC_AUDIT_MODULE", "sec_audit"))


###############################################################################
# The contract, as numbers the tests assert on
###############################################################################

EXPECTED_STATIC_FINDINGS = 11
EXPECTED_STATIC_WEIGHT = 137
EXPECTED_LIVE_FINDINGS = 11
EXPECTED_LIVE_WEIGHT = 131
EXPECTED_STEP8_FINDINGS = 13
EXPECTED_STEP8_WEIGHT = 136
EXPECTED_PARTIAL_FINDINGS = 1
EXPECTED_PARTIAL_WEIGHT = 10


###############################################################################
# Fixtures — a faithful snapshot of lab/terraform
###############################################################################

P, S, REGION = "cbc-day07", "abc123", "us-east-1"
ACCT = "111122223333"
NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)

TRAIL = f"{P}-trail-{S}"
SHADOW_TRAIL = f"{P}-shadow-trail-{S}"
TRAIL_BUCKET = f"{P}-trail-{ACCT}-{S}"
SHADOW_BUCKET = f"{P}-shadow-{ACCT}-{S}"
RESPONDER = f"{P}-responder-{S}"
NAIVE = f"{P}-naive-responder-{S}"
ROTATOR = f"{P}-rotator-{S}"
RULE = f"{P}-guardduty-to-responder-{S}"
NAIVE_RULE = f"{P}-naive-guardduty-rule-{S}"
DLQ = f"arn:aws:sqs:{REGION}:{ACCT}:{P}-responder-dlq-{S}"
KILL = f"/{P}/kill-switch"

GOOD_TYPES = json.dumps([
    "CryptoCurrencyMining:EC2/BitcoinTool.B!DNS",
    "Backdoor:EC2/C&CActivity.B!DNS",
])

GUARDDUTY_PATTERN = json.dumps({
    "source": ["aws.guardduty"],
    "detail-type": ["GuardDuty Finding"],
})


def stmt(effect, actions, resources="*"):
    return {"Effect": effect, "Action": actions, "Resource": resources}


def responder_policy():
    """The narrow role from main.tf section 7, Denies and all."""
    return {"Version": "2012-10-17", "Statement": [
        stmt("Allow", ["ec2:DescribeInstances", "ec2:DescribeSecurityGroups"]),
        stmt("Allow", ["ec2:ModifyInstanceAttribute", "ec2:CreateTags"],
             [f"arn:aws:ec2:{REGION}:{ACCT}:instance/*"]),
        stmt("Allow", ["ssm:GetParameter"], [f"arn:aws:ssm:{REGION}:{ACCT}:parameter{KILL}"]),
        stmt("Allow", ["sns:Publish"], [f"arn:aws:sns:{REGION}:{ACCT}:{P}-containment-{S}"]),
        stmt("Allow", ["logs:CreateLogStream", "logs:PutLogEvents"]),
        stmt("Deny", ["cloudtrail:StopLogging", "cloudtrail:DeleteTrail",
                      "cloudtrail:UpdateTrail", "cloudtrail:PutEventSelectors"]),
        stmt("Deny", ["iam:*", "sts:AssumeRole"]),
        stmt("Deny", ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters"]),
        stmt("Deny", ["ec2:TerminateInstances", "ec2:StopInstances",
                      "ec2:DeleteSecurityGroup", "iam:DeleteAccessKey",
                      "iam:UpdateAccessKey", "secretsmanager:DeleteSecret"]),
    ]}


def naive_policy():
    return {"Version": "2012-10-17", "Statement": [
        stmt("Allow", ["ec2:*", "cloudtrail:*", "iam:*", "ssm:*", "sns:Publish",
                       "logs:CreateLogStream", "logs:PutLogEvents"]),
    ]}


def rotator_policy():
    return {"Version": "2012-10-17", "Statement": [
        stmt("Allow", ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]),
        stmt("Allow", ["logs:CreateLogStream", "logs:PutLogEvents"]),
    ]}


def fn(name, env):
    return {
        "FunctionName": name,
        "Role": f"arn:aws:iam::{ACCT}:role/{name}",
        "Environment": {"Variables": env},
    }


def good_responder():
    return fn(RESPONDER, {
        "CONTAINMENT_MODE": "dry-run",
        "KILL_SWITCH_PARAM": KILL,
        "RESPOND_TO_TYPES": GOOD_TYPES,
        "ACT_ON_SAMPLES": "false",
    })


def target(target_id, arn, dlq=True):
    entry = {"Id": target_id, "Arn": arn}
    if dlq:
        entry["DeadLetterConfig"] = {"Arn": DLQ}
        entry["RetryPolicy"] = {"MaximumRetryAttempts": 2}
    return entry


def good_bucket(name):
    return {
        "Name": name,
        "Versioning": {"Status": "Enabled"},
        "PublicAccessBlock": {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        },
    }


def good_trail():
    return {
        "Name": TRAIL, "S3BucketName": TRAIL_BUCKET, "HomeRegion": REGION,
        "IsMultiRegionTrail": True, "IncludeGlobalServiceEvents": True,
        "LogFileValidationEnabled": True,
    }


def reference_stack(rotation_ran=True, findings=0, publishing="FIFTEEN_MINUTES",
                    key_age_threshold=90, now=NOW):
    """create_insecure_examples = false. Nothing here is wrong.

    Every SILENT test starts from this. A check that cannot stay quiet against
    it has a false positive, and a false positive is how a tool gets muted.
    """
    return {
        "region": REGION,
        "now": now,
        "max_access_key_age_days": key_age_threshold,
        "stale_finding_age_days": 7,
        "guardduty_detectors": [
            {"DetectorId": "det-1", "Status": "ENABLED",
             "FindingPublishingFrequency": publishing},
        ],
        "guardduty_findings": [
            {"Id": f"finding-{i}", "Type": "UnauthorizedAccess:EC2/SSHBruteForce",
             "Severity": 5.0, "UpdatedAt": (now - timedelta(days=20)).isoformat(),
             "Service": {"Archived": False}, "Workflow": {"Status": "NEW"}}
            for i in range(findings)
        ],
        "securityhub_enabled": True,
        "securityhub_standards": [
            {"StandardsArn": "arn:aws:securityhub:::standards/fsbp", "StandardsStatus": "READY"},
        ],
        "trails": [good_trail()],
        "buckets": {TRAIL_BUCKET: good_bucket(TRAIL_BUCKET)},
        "secrets": [{
            "Name": f"{P}/app-credentials-{S}", "ARN": "arn:aws:secretsmanager:::app",
            "RotationEnabled": True,
            "RotationRules": {"AutomaticallyAfterDays": 30},
            "LastRotatedDate": (now - timedelta(days=2)) if rotation_ran else None,
        }],
        "access_keys": [],
        "lambda_functions": [fn(ROTATOR, {"SECRETS_MANAGER_ENDPOINT": "https://x"}),
                             good_responder()],
        "role_policies": {ROTATOR: [rotator_policy()], RESPONDER: [responder_policy()]},
        "event_rules": [{
            "Name": RULE, "State": "ENABLED", "EventPattern": GUARDDUTY_PATTERN,
            "Targets": [
                target("responder", f"arn:aws:lambda:{REGION}:{ACCT}:function:{RESPONDER}"),
                target("notify", f"arn:aws:sns:{REGION}:{ACCT}:{P}-security-{S}"),
            ],
        }],
    }


def static_stack(rotation_ran=False, findings=0, publishing="FIFTEEN_MINUTES",
                 key_age_threshold=90, now=NOW):
    """create_insecure_examples = true. The lab default."""
    stack = reference_stack(rotation_ran=rotation_ran, findings=findings,
                            publishing=publishing, key_age_threshold=key_age_threshold,
                            now=now)
    stack["trails"].append({
        "Name": SHADOW_TRAIL, "S3BucketName": SHADOW_BUCKET, "HomeRegion": REGION,
        "IsMultiRegionTrail": False, "IncludeGlobalServiceEvents": False,
        "LogFileValidationEnabled": False,
    })
    stack["buckets"][SHADOW_BUCKET] = {
        "Name": SHADOW_BUCKET, "Versioning": {}, "PublicAccessBlock": {},
    }
    stack["secrets"].append({
        "Name": f"{P}/legacy-api-key-{S}", "ARN": "arn:aws:secretsmanager:::legacy",
        "RotationEnabled": False,
    })
    stack["access_keys"].append({
        "UserName": f"{P}-legacy-service-{S}", "AccessKeyId": "AKIAEXAMPLE00000001",
        "Status": "Active", "CreateDate": now - timedelta(hours=2),
    })
    stack["lambda_functions"].append(fn(NAIVE, {
        "SEVERITY_THRESHOLD": "7.0", "RESPOND_TO_TYPES": "[]",
        "CONTAINMENT_MODE": "terminate", "ACT_ON_SAMPLES": "true",
    }))
    stack["role_policies"][NAIVE] = [naive_policy()]
    stack["event_rules"].append({
        "Name": NAIVE_RULE, "State": "DISABLED", "EventPattern": GUARDDUTY_PATTERN,
        "Targets": [target("naive-responder",
                           f"arn:aws:lambda:{REGION}:{ACCT}:function:{NAIVE}", dlq=False)],
    })
    return stack


def run_all(stack, region=REGION):
    findings = []
    for check_id, check in A.CHECKS:
        for finding in check(stack, region):
            assert finding.check_id == check_id, (
                f"{check_id} produced a finding labelled {finding.check_id}"
            )
            findings.append(finding)
    return findings


def ids(findings):
    return sorted(f.check_id for f in findings)


###############################################################################
# FIRE — 16 tests, one per check
###############################################################################


class FireTests(unittest.TestCase):
    """Each check catches the fault it exists for."""

    def test_sec_001_fires_when_no_detector_is_enabled(self):
        stack = reference_stack()
        stack["guardduty_detectors"] = [{"DetectorId": "det-1", "Status": "DISABLED"}]
        found = A.check_guardduty_enabled(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_sec_002_fires_when_security_hub_has_no_standards(self):
        stack = reference_stack()
        stack["securityhub_standards"] = []
        found = A.check_security_hub_enabled(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertIn("no standards", found[0].title)

    def test_sec_003_fires_on_untriaged_findings(self):
        stack = reference_stack(findings=3)
        found = A.check_stale_findings(stack, REGION)
        # ONE finding for the region, not one per stale finding.
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].evidence["stale_count"], 3)

    def test_sec_004_fires_on_slow_publishing_frequency(self):
        found = A.check_publishing_frequency(reference_stack(publishing="SIX_HOURS"), REGION)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "LOW")

    def test_sec_005_fires_on_a_severity_threshold(self):
        found = A.check_response_trigger_style(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE])
        self.assertEqual(found[0].severity, "CRITICAL")

    def test_sec_006_fires_on_a_single_region_trail(self):
        found = A.check_trail_coverage(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [SHADOW_TRAIL])

    def test_sec_007_fires_without_log_file_validation(self):
        found = A.check_trail_validation(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [SHADOW_TRAIL])

    def test_sec_008_fires_on_a_responder_that_can_tamper(self):
        found = A.check_responder_role_scope(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE])
        self.assertIn("cloudtrail:*", found[0].evidence["allowed_tamper_actions"])

    def test_sec_009_fires_on_an_unprotected_trail_bucket(self):
        found = A.check_trail_bucket_protection(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [SHADOW_BUCKET])

    def test_sec_010_fires_on_a_secret_with_no_rotation(self):
        found = A.check_secret_rotation_configured(static_stack(), REGION)
        self.assertEqual(len(found), 1)
        self.assertIn("legacy", found[0].resource_id)

    def test_sec_011_fires_when_rotation_has_never_run(self):
        found = A.check_secret_rotation_ran(reference_stack(rotation_ran=False), REGION)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0].evidence["LastRotatedDate"])

    def test_sec_012_fires_on_destructive_containment(self):
        found = A.check_containment_reversible(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE])
        self.assertEqual(found[0].evidence["CONTAINMENT_MODE"], "terminate")

    def test_sec_013_fires_on_an_old_access_key(self):
        stack = static_stack()
        stack["access_keys"][0]["CreateDate"] = NOW - timedelta(days=400)
        found = A.check_access_key_age(stack, REGION)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0].evidence["age_days"], 90)

    def test_sec_014_fires_when_there_is_no_kill_switch(self):
        found = A.check_kill_switch(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE])

    def test_sec_015_fires_on_a_disabled_response_rule(self):
        found = A.check_response_rule_enabled(static_stack(), REGION)
        self.assertEqual([f.resource_id for f in found], [NAIVE_RULE])

    def test_sec_016_fires_on_a_target_with_no_dlq(self):
        found = A.check_response_target_dlq(static_stack(), REGION)
        self.assertEqual(len(found), 1)
        self.assertIn(NAIVE_RULE, found[0].resource_id)


###############################################################################
# SILENT — 16 tests, one per check
###############################################################################


class SilentTests(unittest.TestCase):
    """Each check stays quiet on input that is correct."""

    def test_sec_001_silent_when_a_detector_is_enabled(self):
        self.assertEqual(A.check_guardduty_enabled(reference_stack(), REGION), [])

    def test_sec_002_silent_with_one_standard_subscribed(self):
        self.assertEqual(A.check_security_hub_enabled(reference_stack(), REGION), [])

    def test_sec_003_silent_on_archived_and_resolved_findings(self):
        stack = reference_stack(findings=2)
        stack["guardduty_findings"][0]["Service"]["Archived"] = True
        stack["guardduty_findings"][1]["Workflow"] = {"Status": "RESOLVED"}
        # Deciding about a finding is triage. Only untouched ones count.
        self.assertEqual(A.check_stale_findings(stack, REGION), [])

    def test_sec_004_silent_on_fifteen_minutes(self):
        self.assertEqual(A.check_publishing_frequency(reference_stack(), REGION), [])

    def test_sec_005_silent_on_a_type_allow_list(self):
        self.assertEqual(A.check_response_trigger_style(reference_stack(), REGION), [])

    def test_sec_006_silent_on_a_multi_region_trail(self):
        self.assertEqual(A.check_trail_coverage(reference_stack(), REGION), [])

    def test_sec_007_silent_with_validation_enabled(self):
        self.assertEqual(A.check_trail_validation(reference_stack(), REGION), [])

    def test_sec_008_silent_on_a_role_that_denies_the_dangerous_actions(self):
        self.assertEqual(A.check_responder_role_scope(reference_stack(), REGION), [])

    def test_sec_009_silent_on_a_versioned_blocked_bucket(self):
        self.assertEqual(A.check_trail_bucket_protection(reference_stack(), REGION), [])

    def test_sec_010_silent_when_rotation_is_configured(self):
        self.assertEqual(A.check_secret_rotation_configured(reference_stack(), REGION), [])

    def test_sec_011_silent_when_rotation_has_actually_run(self):
        self.assertEqual(A.check_secret_rotation_ran(reference_stack(), REGION), [])

    def test_sec_012_silent_on_dry_run_and_isolate(self):
        self.assertEqual(A.check_containment_reversible(reference_stack(), REGION), [])
        stack = reference_stack()
        stack["lambda_functions"][1]["Environment"]["Variables"]["CONTAINMENT_MODE"] = "isolate"
        self.assertEqual(A.check_containment_reversible(stack, REGION), [])

    def test_sec_013_silent_on_a_new_and_on_an_inactive_key(self):
        self.assertEqual(A.check_access_key_age(static_stack(), REGION), [])
        stack = static_stack()
        stack["access_keys"][0].update({"CreateDate": NOW - timedelta(days=400),
                                        "Status": "Inactive"})
        # An inactive key cannot authenticate. Deactivating IS the remediation.
        self.assertEqual(A.check_access_key_age(stack, REGION), [])

    def test_sec_014_silent_when_a_kill_switch_is_configured(self):
        self.assertEqual(A.check_kill_switch(reference_stack(), REGION), [])

    def test_sec_015_silent_on_an_enabled_rule(self):
        self.assertEqual(A.check_response_rule_enabled(reference_stack(), REGION), [])

    def test_sec_016_silent_when_every_target_has_a_dlq(self):
        self.assertEqual(A.check_response_target_dlq(reference_stack(), REGION), [])


###############################################################################
# WHOLE STACK — 15 tests
###############################################################################


class WholeStackTests(unittest.TestCase):
    """The contract, asserted. These are the numbers five documents quote."""

    def test_static_totals_match_the_contract(self):
        findings = run_all(static_stack())
        self.assertEqual(len(findings), EXPECTED_STATIC_FINDINGS)
        self.assertEqual(sum(f.weight for f in findings), EXPECTED_STATIC_WEIGHT)
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertTrue(A.score_grade(0).startswith("F"))
        # Five checks are silent at this point, for four different reasons.
        for silent in ("SEC-001", "SEC-002", "SEC-003", "SEC-004", "SEC-013"):
            self.assertNotIn(silent, ids(findings))

    def test_every_finding_is_well_formed(self):
        for finding in run_all(static_stack()):
            with self.subTest(check=finding.check_id):
                self.assertIn(finding.severity, A.SEVERITY_ORDER)
                self.assertRegex(finding.check_id, r"^SEC-0\d\d$")
                self.assertTrue(finding.resource_type.startswith("AWS::"))
                self.assertTrue(finding.resource_id)
                self.assertGreater(len(finding.detail), 80)
                self.assertGreater(len(finding.remediation), 80)
                self.assertIsInstance(finding.evidence, dict)
                self.assertEqual(finding.region, REGION)
                json.dumps(finding.to_dict(), default=str)

    def test_reference_build_produces_zero_findings(self):
        """THE test. create_insecure_examples = false, after rotation has run.

        A tool that cannot return a clean result on clean input is a tool
        nobody can ever finish fixing things with. Every remediation in this
        file has to be reachable, and this asserts the destination exists.
        """
        findings = run_all(reference_stack(rotation_ran=True))
        self.assertEqual(findings, [])
        self.assertEqual(A.calculate_score(findings), 100)
        self.assertTrue(A.score_grade(100).startswith("A"))

    def test_the_other_contract_rows_match(self):
        step8 = run_all(static_stack(rotation_ran=True, findings=3,
                                     publishing="SIX_HOURS", key_age_threshold=0))
        self.assertEqual(len(step8), EXPECTED_STEP8_FINDINGS)
        self.assertEqual(sum(f.weight for f in step8), EXPECTED_STEP8_WEIGHT)
        self.assertIn("SEC-004", ids(step8))
        self.assertIn("SEC-013", ids(step8))

        partial = run_all(reference_stack(rotation_ran=False))
        self.assertEqual(len(partial), EXPECTED_PARTIAL_FINDINGS)
        self.assertEqual(sum(f.weight for f in partial), EXPECTED_PARTIAL_WEIGHT)
        self.assertEqual(ids(partial), ["SEC-011"])
        self.assertEqual(A.calculate_score(partial), 90)

    def test_min_severity_filters_display_but_never_the_score(self):
        findings = run_all(static_stack())
        shown = A.filter_by_severity(findings, "HIGH")
        self.assertEqual(len(shown), 8)
        self.assertLess(len(shown), len(findings))
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertTrue(all(f.severity in ("CRITICAL", "HIGH") for f in shown))


class DivergenceTests(unittest.TestCase):
    """Static and live differ, and the difference is the day's second lesson."""

    def test_static_and_live_have_the_same_count_and_a_different_set(self):
        static = run_all(static_stack())
        live = run_all(static_stack(rotation_ran=True, findings=3))

        self.assertEqual(len(live), EXPECTED_LIVE_FINDINGS)
        self.assertEqual(sum(f.weight for f in live), EXPECTED_LIVE_WEIGHT)

        # Same count.
        self.assertEqual(len(static), len(live))
        # Different set. Two checks move in OPPOSITE directions:
        #   SEC-011 clears once rotation actually runs.
        #   SEC-003 appears once there are findings to leave untriaged.
        self.assertIn("SEC-011", ids(static))
        self.assertNotIn("SEC-011", ids(live))
        self.assertNotIn("SEC-003", ids(static))
        self.assertIn("SEC-003", ids(live))
        self.assertNotEqual(set(ids(static)), set(ids(live)))

        # Eleven before, eleven after, six points apart, different problem.
        # NEVER DIFF ON THE COUNT. This is also the contrast with Day 06, where
        # static and live were identical because every check read configuration.
        self.assertNotEqual(sum(f.weight for f in static), sum(f.weight for f in live))


class ClockTests(unittest.TestCase):
    """The clock is injected, and SEC-013's whole lesson depends on it."""

    def test_the_same_account_passes_today_and_fails_in_ninety_one_days(self):
        # Not a metaphor. The stack is byte-identical apart from stack["now"].
        today = static_stack(now=NOW)
        self.assertEqual(A.check_access_key_age(today, REGION), [])

        later = static_stack(now=NOW)
        later["now"] = NOW + timedelta(days=91)
        found = A.check_access_key_age(later, REGION)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0].evidence["age_days"], 90)

        # Nobody edited anything. No deploy, no console click, no drift. A
        # point-in-time audit pass is not a property that persists, which is
        # the argument for running this on a schedule rather than at merge
        # time only.
        self.assertEqual(
            [f["FunctionName"] for f in today["lambda_functions"]],
            [f["FunctionName"] for f in later["lambda_functions"]],
        )

        # And the parsing underneath it accepts every shape AWS returns, plus
        # the ones a JSON round-trip or a fixture produces. A timestamp helper
        # that rejects one of these turns an age check into a silent no-op.
        expected = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(A._parse_time("2026-07-24T12:00:00Z"), expected)
        self.assertEqual(A._parse_time("2026-07-24T12:00:00+00:00"), expected)
        self.assertEqual(A._parse_time(expected), expected)
        # A naive datetime is assumed UTC rather than rejected — boto3 returns
        # aware ones, but a fixture or a JSON round-trip may not.
        self.assertEqual(A._parse_time(datetime(2026, 7, 24, 12, 0)), expected)
        self.assertIsNone(A._parse_time("not a date"))
        self.assertIsNone(A._parse_time(None))


class InteractionTests(unittest.TestCase):
    """The check interactions the contract calls deliberate."""

    def test_sec_010_and_sec_011_never_both_fire_on_one_secret(self):
        # No rotation at all -> SEC-010 only. "Nobody decided" is a different
        # problem from "it says it rotates and it does not", with a different
        # remediation and usually a different owner.
        stack = reference_stack()
        stack["secrets"] = [{"Name": "s", "RotationEnabled": False}]
        self.assertEqual(len(A.check_secret_rotation_configured(stack, REGION)), 1)
        self.assertEqual(A.check_secret_rotation_ran(stack, REGION), [])

        # Rotation configured but never run -> SEC-011 only.
        stack["secrets"] = [{"Name": "s", "RotationEnabled": True,
                             "RotationRules": {"AutomaticallyAfterDays": 30},
                             "LastRotatedDate": None}]
        self.assertEqual(A.check_secret_rotation_configured(stack, REGION), [])
        self.assertEqual(len(A.check_secret_rotation_ran(stack, REGION)), 1)

    def test_sec_008_reads_deny_statements_before_calling_an_allow_a_fault(self):
        # ec2:* plus explicit Denies -> correct, and silent.
        self.assertEqual(A.check_responder_role_scope(reference_stack(), REGION), [])

        # The SAME allows with the Denies removed -> fires. This is the
        # difference between a real check and a wildcard grep, and a check
        # that flagged the first case would be muted within a fortnight.
        stack = reference_stack()
        allows = [s for s in responder_policy()["Statement"] if s["Effect"] == "Allow"]
        stack["role_policies"][RESPONDER] = [
            {"Version": "2012-10-17",
             "Statement": allows + [stmt("Allow", ["cloudtrail:*", "iam:*"])]}
        ]
        found = A.check_responder_role_scope(stack, REGION)
        self.assertEqual([f.resource_id for f in found], [RESPONDER])
        self.assertEqual(found[0].evidence["explicitly_denied"], [])

    def test_response_checks_are_scoped_to_functions_that_can_actually_act(self):
        # The rotator subscribes to nothing and can change nothing. It has no
        # kill switch and no containment mode, and demanding either would train
        # people to ignore the check.
        stack = reference_stack()
        stack["lambda_functions"] = [fn(ROTATOR, {})]
        stack["role_policies"] = {ROTATOR: [rotator_policy()]}
        self.assertEqual(A.responder_functions(stack), [])
        self.assertEqual(A.check_kill_switch(stack, REGION), [])
        self.assertEqual(A.check_containment_reversible(stack, REGION), [])
        self.assertEqual(A.check_response_trigger_style(stack, REGION), [])

        # Give that same function ec2:ModifyInstanceAttribute and every one of
        # those checks now applies to it. Identified by PERMISSION, not name.
        stack["role_policies"][ROTATOR] = [
            {"Version": "2012-10-17",
             "Statement": [stmt("Allow", ["ec2:ModifyInstanceAttribute"])]}
        ]
        self.assertEqual(len(A.responder_functions(stack)), 1)
        self.assertEqual(len(A.check_kill_switch(stack, REGION)), 1)


class HelperTests(unittest.TestCase):
    """The policy helpers, which are where the subtle bugs live."""

    def test_policy_helpers_accept_every_shape_and_handle_wildcards(self):
        self.assertEqual(A.as_list("iam:*"), ["iam:*"])
        self.assertEqual(A.as_list(None), [])

        document = {"Statement": [stmt("Allow", "cloudtrail:StopLogging")]}
        self.assertEqual(A.parse_policy(document), document)
        self.assertEqual(A.parse_policy(json.dumps(document)), document)
        from urllib.parse import quote
        self.assertEqual(A.parse_policy(quote(json.dumps(document))), document)
        self.assertEqual(A.parse_policy("not json"), {})

        # A single string and a list of one are the same document — the form
        # `Action: "*"` takes, and the blind spot every naive parser has.
        self.assertEqual(A.policy_allows(document, set(A.TAMPER_ACTIONS)),
                         ["cloudtrail:stoplogging"])

        # A service wildcard subsumes the specific actions under it.
        wild = {"Statement": [stmt("Allow", ["ssm:*"])]}
        self.assertIn("ssm:*", A.policy_allows(wild, set(A.TAMPER_ACTIONS)))

        # And a Deny on the wildcard covers everything beneath it.
        denied = A.policy_denies({"Statement": [stmt("Deny", ["iam:*"])]},
                                 set(A.TAMPER_ACTIONS))
        self.assertIn("iam:putrolepolicy", denied)

        # A Deny is not a grant, however loudly it names the action.
        self.assertEqual(
            A.policy_allows({"Statement": [stmt("Deny", "cloudtrail:*")]},
                            set(A.TAMPER_ACTIONS)),
            [],
        )

    def test_guardduty_rules_matches_on_source_not_on_name(self):
        stack = reference_stack()
        stack["event_rules"].append({
            "Name": "some-scheduled-job", "State": "ENABLED",
            "EventPattern": json.dumps({"source": ["aws.ec2"]}), "Targets": [],
        })
        stack["event_rules"].append({
            "Name": "guardduty-sounding-name-but-not", "State": "DISABLED",
            "EventPattern": json.dumps({"source": ["aws.health"]}), "Targets": [],
        })
        matched = [r["Name"] for r in A.guardduty_rules(stack)]
        self.assertEqual(matched, [RULE])
        # The decoy is DISABLED and must NOT produce a SEC-015 finding — a
        # check that matched on the rule name would flag it.
        self.assertEqual(A.check_response_rule_enabled(stack, REGION), [])


class RendererTests(unittest.TestCase):
    """The three output formats. Each one is somebody's integration point."""

    # Built directly rather than by running the checks. A renderer test that
    # depends on the checks tells you nothing when a check is broken, and makes
    # this suite useless as a feedback loop for the challenge file, where the
    # checks are deliberately empty. The shape below reproduces the contract
    # independently: 4 CRITICAL, 4 HIGH, 3 MEDIUM = 11 findings, 137 points.
    CONTRACT_SHAPE = [
        ("SEC-005", "CRITICAL"), ("SEC-008", "CRITICAL"),
        ("SEC-012", "CRITICAL"), ("SEC-014", "HIGH"),
        ("SEC-006", "HIGH"), ("SEC-007", "HIGH"), ("SEC-009", "HIGH"),
        ("SEC-011", "HIGH"),
        ("SEC-010", "MEDIUM"), ("SEC-015", "MEDIUM"), ("SEC-016", "MEDIUM"),
    ]

    def setUp(self):
        self.findings = [
            A.Finding(
                check_id=check_id, severity=severity,
                resource_type="AWS::Lambda::Function",
                resource_id=f"{P}-resource-{n}",
                title=f"Synthetic finding {n}",
                detail="d" * 120, remediation="r" * 120,
                evidence={"n": n}, region=REGION,
            )
            for n, (check_id, severity) in enumerate(self.CONTRACT_SHAPE)
        ]
        self.assertEqual(len(self.findings), EXPECTED_STATIC_FINDINGS)
        self.assertEqual(sum(f.weight for f in self.findings), EXPECTED_STATIC_WEIGHT)
        self.stats = {"detectors": 1, "findings_open": 0, "trails": 2, "secrets": 2,
                      "access_keys": 1, "functions": 3, "rules": 2}

    def test_render_table_carries_the_day_banner_and_the_score(self):
        out = A.render_table(self.findings, self.stats, 0, use_colour=False)
        self.assertIn("CLOUD SECURITY AUDIT", out)
        self.assertIn("CareerByteCode · Day 07 · Enterprise Cloud Security", out)
        self.assertIn("COMPLIANCE SCORE: 0/100", out)
        self.assertIn("F — do not point this at production data", out)
        self.assertIn("2 trail(s)", out)
        self.assertNotIn("\033", out)
        self.assertLess(out.index("CRITICAL"), out.rindex("MEDIUM"))

    def test_render_json_is_valid_and_names_the_runtime_checks(self):
        payload = json.loads(A.render_json(self.findings, self.stats, 0))
        self.assertEqual(payload["audit"], "sec_audit")
        self.assertEqual(payload["day"], "07")
        self.assertEqual(payload["finding_count"], EXPECTED_STATIC_FINDINGS)
        self.assertEqual(payload["summary"]["CRITICAL"], 3)
        # A consumer diffing two runs needs to know which checks can change
        # without anybody having touched the account.
        self.assertEqual(payload["runtime_dependent_checks"],
                         ["SEC-003", "SEC-011", "SEC-013"])

    def test_render_csv_has_a_header_and_one_row_per_finding(self):
        import csv as _csv

        rows = list(_csv.reader(io.StringIO(A.render_csv(self.findings))))
        self.assertEqual(rows[0][:3], ["check_id", "severity", "weight"])
        self.assertEqual(len(rows), EXPECTED_STATIC_FINDINGS + 1)
        for row in rows[1:]:
            self.assertEqual(int(row[2]), A.SEVERITY_WEIGHTS[row[1]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
