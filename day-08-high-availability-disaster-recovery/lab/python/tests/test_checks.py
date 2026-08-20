#!/usr/bin/env python3
"""
test_checks.py — Day 08 unit tests for dr_audit.py

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
                 silent-by-design check, the deliberate interactions, the
                 helpers and the three renderers

To point these at the challenge file instead of the reference:

    DR_AUDIT_MODULE=dr_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests

THE RENDERER TESTS DO NOT CALL THE CHECKS. They build findings directly from
Finding(...) via CONTRACT_SHAPE, which reproduces the static state's severity
histogram (5 CRITICAL, 5 HIGH, 5 MEDIUM = 15 findings, 195 points, score 0).
Days 06 and 07 both do this, and the reason is practical: renderer tests that
call the checks produce ERRORS rather than clean failures when pointed at a
stubbed challenge file, and an error tells a learner nothing about what they
got wrong.

=============================================================================
DAY 08 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (finding_contract),
lab/python/dr_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03 through 07:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

Day 08 has NO LOW AND NO INFO CHECKS, and that is a decision rather than an
oversight. On this day every fault either costs you data or costs you time
during an outage. There is no informational gap in a recovery path — a thing
that does not matter when the region is on fire does not belong in an audit
whose whole subject is the hour the region is on fire.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, nat_gateway_strategy = "single",
create_rds = false, enable_vault_lock = false,
s3_replication_time_control = false, hosted_zone_id = ""), before any backup
job has run, before any restore, before any workflow execution.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  DR-001   CRITICAL  25   1   25  aws_autoscaling_group.single_az
  DR-002   HIGH      10   1   10  aws_nat_gateway.main - strategy "single"
  DR-003   HIGH      10   1   10  aws_autoscaling_group.single_az
  DR-004   MEDIUM     4   1    4  aws_autoscaling_group.single_az
  DR-005   CRITICAL  25   0    0  none - SILENT BY SITUATION, see below
  DR-006   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-007   MEDIUM     4   1    4  aws_dynamodb_table.no_pitr
  DR-008   HIGH      10   2   20  aws_backup_vault.main, aws_backup_vault.dr
  DR-009   MEDIUM     4   2    8  aws_backup_vault.main, aws_backup_vault.dr
  DR-010   CRITICAL  25   1   25  account-level singleton - no restore, ever
  DR-011   HIGH      10   0    0  none - SILENT BY DESIGN, see below
  DR-012   MEDIUM     4   1    4  aws_s3_bucket.unversioned
  DR-013   HIGH      10   1   10  aws_s3_bucket_replication_configuration.primary
  DR-014   HIGH      10   0    0  none - SILENT BY SITUATION, see below
  DR-015   CRITICAL  25   1   25  aws_sfn_state_machine.naive
  DR-016   CRITICAL  25   2   50  both Day 08 state machines - never executed
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    15  195

  FIFTEEN findings from SIXTEEN checks. Four checks are silent here and they
  are silent for two different reasons, which is the most useful thing in this
  table: three because this particular stack cannot currently produce the
  fault (DR-005, DR-006, DR-014), and one because NO configuration of this
  stack can ever produce it (DR-011).

  Score: 100 - 195 = -95, floored to 0/100. Grade F.

  SEVERITY HISTOGRAM of the 16 checks: 5 CRITICAL, 7 HIGH, 4 MEDIUM,
  0 LOW, 0 INFO.

THE FOUR STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  A  Static: after apply, nothing run yet            15     195    0/100      F
  B  Live: after lab steps 6a, 7 and 8 - one
     on-demand backup copied to DR, one
     workflow execution succeeded, one
     restore performed                               11     125    0/100      F
  C  Sixty-one minutes after B, WITH NOTHING
     CHANGED - the recovery points have aged
     past rpo_target_minutes                         13     145    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  D  Reference build: create_insecure_examples
     = false, nat_gateway_strategy = "per_az",
     s3_replication_time_control = true,
     enable_vault_lock = true, plus a completed
     backup, a completed restore and one
     successful workflow execution                    0       0  100/100      A

  STATE C IS THE POINT OF THIS TABLE AND IT IS THE THESIS OF THE DAY.

  Between B and C, nobody deploys anything. No console click, no apply, no
  merge. Two findings appear because time passed and DR-008 measures the age
  of the newest recovery point against the RPO you declared.

  An audit that passes at 14:00 fails at 15:01 on an unchanged account.

  That is not a defect in the auditor. It is the correct behaviour, and it is
  the difference between a configuration audit and a recovery audit. RTO and
  RPO are not properties of a configuration. They are claims about a
  PROCEDURE, and a claim about a procedure decays continuously from the last
  time somebody ran it. A merge-time-only audit certifies the account as it
  was on the day somebody last changed it, and that is not the property a DR
  posture needs to have.

  With the shipped hourly backup schedule, DR-008 therefore SAWTOOTHS: silent
  for the minutes after each successful job, firing again as the recovery
  point ages past the 60-minute RPO. Two numbers that are one minute apart
  produce different audit results, and both are correct. If that is
  uncomfortable, the fix is not a looser check - it is a schedule that is
  actually faster than the RPO you claimed.

  Day 07's contract had the finding COUNT identical before and after the lab
  with a different SET. Day 08 does not repeat that trick, because forcing it
  here would have been dishonest: doing the work genuinely removes findings.
  What Day 08 has instead is a state that gets WORSE while you are asleep.

SILENT BY DESIGN — DR-011, a replication or backup copy target in the same
region as its source.

  No shipped default and no typo can produce this fault. The dr_region
  variable carries a cross-variable validation refusing dr_region ==
  aws_region; the S3 replica bucket is created under provider = aws.dr; the
  AWS Backup copy rule targets the DR vault or does not exist. There is no
  path through this Terraform that puts a DR copy in the primary region, so
  the plan refuses to produce one.

  It is not a hypothetical fault. S3 Same-Region Replication is a real and
  legitimate feature - compliance separation, log aggregation, cross-account
  isolation - and an AWS Backup copy rule will happily target a vault in the
  source region. Both get pressed into service as "DR" by people who were
  solving a different problem last week, and both produce a second copy inside
  the same blast radius.

  A check that stays silent because the stack cannot produce the fault is
  evidence that the auditor does not cry wolf.

SILENT BY SITUATION — DR-005, DR-006 and DR-014.

  DR-005 and DR-006 are the RDS checks. create_rds defaults to false, so there
  is no RDS instance to be single-AZ or to have one day of retention. The
  moment somebody sets create_rds = true with the shipped defaults, BOTH fire
  immediately, for 35 points, because rds_multi_az defaults to false and
  rds_backup_retention_days defaults to 1.

  DR-014 is the Route 53 failover-record check. The failover record sets
  require a hosted zone you own, hosted_zone_id defaults to empty, so there
  are no failover records to be missing a health check.

  NOTHING HAS TO CHANGE FOR ANY OF THESE TO STOP BEING TRUE, and in DR-005's
  case the change is one boolean typed by somebody adding a database on a
  Thursday.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor: it cannot fire, so its silence is a property of the tool. Silent by
situation tells you nothing about the auditor and everything about today's
account - and "we have no findings" and "we have nothing to find" are
different states that render identically in every report. Never read the
second as the first.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  DR-001, DR-003 and DR-004 all fire on aws_autoscaling_group.single_az and
  they are not duplicates. DR-001 is WHERE it runs - one failure domain.
  DR-003 is WHETHER IT NOTICES an application failure - health_check_type
  "EC2" means a deadlocked process is a healthy instance forever. DR-004 is
  WHETHER A REPLACEMENT CAN START AT ALL - a zero grace period is a
  termination loop. Fixing any one leaves the other two, the remediations are
  unrelated, and in most organisations they have different owners: the network
  team owns the subnets, the platform team owns the ASG, and the application
  team owns how long a boot takes.

  DR-002 IS THE ONLY CHECK THAT FIRES ON YOUR OWN CORRECTLY-INTENDED STACK
  rather than on a deliberately broken example. nat_gateway_strategy defaults
  to "single", which is a real, defensible, extremely common cost decision
  that puts a single-AZ dependency inside an architecture everybody calls
  multi-AZ. It is also the only finding in this contract that you clear by
  SPENDING MONEY rather than by fixing a mistake - roughly $36/month more.
  That is deliberate. An auditor whose findings are all strawmen teaches
  people that findings are strawmen.

  DR-008 AND DR-010 LOOK LIKE THE SAME CHECK AND ARE NOT. DR-008 asks "is
  there a recent enough backup". DR-010 asks "has anybody ever proved a backup
  can be turned back into a system". A vault full of fresh, correctly
  retained, cross-region-copied recovery points that has never had a single
  restore performed against it scores 0 on DR-008 and 25 on DR-010, and that
  is the normal state of most organisations. The failure modes DR-010 exists
  for - a rotated KMS key, a missing AMI, an instance type unavailable in the
  DR region, a deprecated engine version, a restore that works and takes nine
  hours - are all invisible in a backup report and all obvious in one restore
  test.

  DR-010 AND DR-016 ARE THE SAME IDEA ABOUT TWO DIFFERENT THINGS - restore
  versus failover - and both are reported at a level ABOVE any single
  resource. DR-010 is an account-level singleton; DR-016 is per state machine.
  Neither is attached to a data resource, deliberately: they are statements
  about the ORGANISATION, not about a bucket, and attaching them to a resource
  id invites somebody to close the finding by deleting the resource.

  DR-013 FIRES ON A CORRECTLY-CONFIGURED REPLICATION RULE. The rule works.
  Objects replicate. What is absent is the METRIC, because Replication Time
  Control is off - and without it there is no way to answer "what is my
  current replication lag", which means there is no way to state an RPO that
  is anything more than an adjective. This is the only check in the set that
  fires on something which is not broken, and it is Day 06's argument in new
  clothes: a summary you cannot check is worse than no summary, and an RPO you
  cannot measure is worse than no RPO, because you will quote it.

  DR-009 FIRES TWICE, ONCE PER VAULT, INCLUDING THE DR VAULT, and is
  deliberately not deduplicated up to the plan. A locked primary vault beside
  an unlocked DR copy vault is a real and common asymmetry, and it is exactly
  backwards: the DR vault is the one an attacker who has already compromised
  the primary account will reach for, because it is the copy that survives
  everything they just did.

  DR-016 FIRES ON THE NAIVE STATE MACHINE TOO, and after lab step 7 it is the
  only DR-016 finding left. An automated failover that has never been executed
  is untested; an automated failover that has never been executed AND has no
  kill switch, no assessment, no approval gate and no verification is untested
  in a way that will be discovered by production. DR-015 and DR-016 fire on
  the same resource for genuinely different reasons and neither remediates the
  other.
=============================================================================
"""

import importlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

A = importlib.import_module(os.environ.get("DR_AUDIT_MODULE", "dr_audit"))


NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
ACCOUNT = "123456789012"
PRIMARY = "us-east-1"
DR = "us-west-2"

VAULT_MAIN_ARN = f"arn:aws:backup:{PRIMARY}:{ACCOUNT}:backup-vault:cbc-day08-vault-ab12cd"
VAULT_DR_ARN = f"arn:aws:backup:{DR}:{ACCOUNT}:backup-vault:cbc-day08-vault-dr-ab12cd"
LAMBDA_ARN = f"arn:aws:lambda:{PRIMARY}:{ACCOUNT}:function:cbc-day08-recovery-ab12cd"

# The severity histogram of STATE A, reproduced so the renderer tests can build
# a realistic finding set without calling a single check.
#   5 CRITICAL (125) + 5 HIGH (50) + 5 MEDIUM (20) = 195 points, 15 findings.
CONTRACT_SHAPE = (
    ["CRITICAL"] * 5 + ["HIGH"] * 5 + ["MEDIUM"] * 5
)


GOOD_DEFINITION = {
    "Comment": "Day 08 recovery",
    "StartAt": "CheckKillSwitch",
    "States": {
        "CheckKillSwitch": {
            "Type": "Task",
            "Resource": LAMBDA_ARN,
            "Parameters": {"action": "check_kill_switch"},
            "Next": "Assess",
        },
        "Assess": {
            "Type": "Task",
            "Resource": LAMBDA_ARN,
            "Parameters": {"action": "assess"},
            "Next": "RequestApproval",
        },
        "RequestApproval": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sns:publish.waitForTaskToken",
            "Parameters": {"TopicArn": f"arn:aws:sns:{PRIMARY}:{ACCOUNT}:cbc-day08-dr"},
            "TimeoutSeconds": 1800,
            "Next": "ExecuteFailover",
        },
        "ExecuteFailover": {
            "Type": "Task",
            "Resource": LAMBDA_ARN,
            "Parameters": {"action": "failover", "dry_run.$": "$.kill_switch.dry_run"},
            "Next": "Verify",
        },
        "Verify": {
            "Type": "Task",
            "Resource": LAMBDA_ARN,
            "Parameters": {"action": "verify", "dry_run.$": "$.kill_switch.dry_run"},
            "Next": "Succeeded",
        },
        "Succeeded": {"Type": "Succeed"},
    },
}

NAIVE_DEFINITION = {
    "Comment": "DELIBERATELY BAD",
    "StartAt": "Failover",
    "States": {
        "Failover": {
            "Type": "Task",
            "Resource": LAMBDA_ARN,
            "Parameters": {"action": "failover", "dry_run": False, "reason": "triggered"},
            "End": True,
        }
    },
}


def base_stack(now=NOW):
    """The stack after `terraform apply` with the shipped defaults.

    create_insecure_examples = true, nat_gateway_strategy = "single",
    create_rds = false, enable_vault_lock = false,
    s3_replication_time_control = false, hosted_zone_id = "".

    This is STATE A of the finding contract, and the totals asserted against it
    below are the contract's numbers rather than whatever the code happens to
    produce.
    """
    return {
        "region": PRIMARY,
        "dr_region": DR,
        "account_id": ACCOUNT,
        "now": now,
        "rpo_minutes": 60,
        "rto_minutes": 30,
        "min_grace_seconds": 30,
        "failover_test_max_age_days": 90,
        "restore_window_days": 365,
        "subnets": {
            "subnet-pub-a": {"SubnetId": "subnet-pub-a", "AvailabilityZone": "us-east-1a", "VpcId": "vpc-day08"},
            "subnet-pub-b": {"SubnetId": "subnet-pub-b", "AvailabilityZone": "us-east-1b", "VpcId": "vpc-day08"},
            "subnet-prv-a": {"SubnetId": "subnet-prv-a", "AvailabilityZone": "us-east-1a", "VpcId": "vpc-day08"},
            "subnet-prv-b": {"SubnetId": "subnet-prv-b", "AvailabilityZone": "us-east-1b", "VpcId": "vpc-day08"},
        },
        "asgs": [
            {
                "AutoScalingGroupName": "cbc-day08-asg-ab12cd",
                "VPCZoneIdentifier": "subnet-prv-a,subnet-prv-b",
                "AvailabilityZones": ["us-east-1a", "us-east-1b"],
                "HealthCheckType": "ELB",
                "HealthCheckGracePeriod": 300,
                "TargetGroupARNs": ["arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/tg/abc"],
                "DesiredCapacity": 2,
            },
            {
                "AutoScalingGroupName": "cbc-day08-legacy-asg-ab12cd",
                "VPCZoneIdentifier": "subnet-prv-a",
                "AvailabilityZones": ["us-east-1a"],
                "HealthCheckType": "EC2",
                "HealthCheckGracePeriod": 0,
                "TargetGroupARNs": [],
                "DesiredCapacity": 0,
            },
        ],
        "nat_gateways": [
            {"NatGatewayId": "nat-0aaa", "SubnetId": "subnet-pub-a", "VpcId": "vpc-day08", "State": "available"}
        ],
        "route_tables": [
            {
                "RouteTableId": "rtb-prv-a",
                "VpcId": "vpc-day08",
                "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0aaa"}],
                "Associations": [{"SubnetId": "subnet-prv-a"}],
            },
            {
                "RouteTableId": "rtb-prv-b",
                "VpcId": "vpc-day08",
                "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0aaa"}],
                "Associations": [{"SubnetId": "subnet-prv-b"}],
            },
            {
                "RouteTableId": "rtb-pub",
                "VpcId": "vpc-day08",
                "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-0aaa"}],
                "Associations": [{"SubnetId": "subnet-pub-a"}, {"SubnetId": "subnet-pub-b"}],
            },
        ],
        "db_instances": [],
        "tables": [
            {"TableName": "cbc-day08-orders-ab12cd", "PointInTimeRecoveryStatus": "ENABLED", "Replicas": []},
            {"TableName": "cbc-day08-sessions-ab12cd", "PointInTimeRecoveryStatus": "DISABLED", "Replicas": []},
        ],
        "vaults": [
            {
                "BackupVaultName": "cbc-day08-vault-ab12cd",
                "BackupVaultArn": VAULT_MAIN_ARN,
                "Region": PRIMARY,
                "Locked": False,
                "NumberOfRecoveryPoints": 0,
                "LatestRecoveryPointTime": None,
            },
            {
                "BackupVaultName": "cbc-day08-vault-dr-ab12cd",
                "BackupVaultArn": VAULT_DR_ARN,
                "Region": DR,
                "Locked": False,
                "NumberOfRecoveryPoints": 0,
                "LatestRecoveryPointTime": None,
            },
        ],
        "restore_jobs": [],
        "backup_plans": [
            {
                "BackupPlanName": "cbc-day08-plan-ab12cd",
                "BackupPlanId": "plan-abc",
                "Rules": [
                    {
                        "RuleName": "primary",
                        "TargetBackupVaultArn": VAULT_MAIN_ARN,
                        "CopyActions": [{"DestinationBackupVaultArn": VAULT_DR_ARN}],
                    }
                ],
            }
        ],
        "buckets": [
            {
                "Name": "cbc-day08-data-123456789012-ab12cd",
                "Region": PRIMARY,
                "Versioning": "Enabled",
                "Replication": {
                    "Rules": [
                        {
                            "ID": "replicate-all-to-dr",
                            "Status": "Enabled",
                            "Destination": {"Bucket": "arn:aws:s3:::cbc-day08-data-dr-123456789012-ab12cd"},
                        }
                    ]
                },
            },
            {
                "Name": "cbc-day08-data-dr-123456789012-ab12cd",
                "Region": DR,
                "Versioning": "Enabled",
                "Replication": {},
            },
            {
                "Name": "cbc-day08-dr-archive-123456789012-ab12cd",
                "Region": PRIMARY,
                "Versioning": "Disabled",
                "Replication": {},
            },
        ],
        "route53_records": [],
        "health_checks": [{"Id": "hc-abc"}],
        "state_machines": [
            {
                "name": "cbc-day08-recovery-ab12cd",
                "stateMachineArn": f"arn:aws:states:{PRIMARY}:{ACCOUNT}:stateMachine:cbc-day08-recovery-ab12cd",
                "Region": PRIMARY,
                "definition": GOOD_DEFINITION,
                "executions": [],
            },
            {
                "name": "cbc-day08-naive-failover-ab12cd",
                "stateMachineArn": f"arn:aws:states:{PRIMARY}:{ACCOUNT}:stateMachine:cbc-day08-naive-failover-ab12cd",
                "Region": PRIMARY,
                "definition": NAIVE_DEFINITION,
                "executions": [],
            },
        ],
    }


def clean_stack():
    """STATE D — the reference build, with the work actually done.

    create_insecure_examples = false, nat_gateway_strategy = "per_az",
    s3_replication_time_control = true, enable_vault_lock = true, plus a
    completed backup, a completed restore and one successful workflow
    execution. Every check must be silent against this.
    """
    stack = base_stack()
    fresh = NOW - timedelta(minutes=10)

    stack["asgs"] = [stack["asgs"][0]]
    stack["tables"] = [stack["tables"][0]]
    stack["buckets"] = [b for b in stack["buckets"] if "dr-archive" not in b["Name"]]
    stack["state_machines"] = [stack["state_machines"][0]]

    stack["nat_gateways"].append(
        {"NatGatewayId": "nat-0bbb", "SubnetId": "subnet-pub-b", "VpcId": "vpc-day08", "State": "available"}
    )
    stack["route_tables"][1]["Routes"] = [
        {"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-0bbb"}
    ]

    for vault in stack["vaults"]:
        vault["Locked"] = True
        vault["LatestRecoveryPointTime"] = fresh
        vault["NumberOfRecoveryPoints"] = 1

    destination = stack["buckets"][0]["Replication"]["Rules"][0]["Destination"]
    destination["Metrics"] = {"Status": "Enabled"}
    destination["ReplicationTime"] = {"Status": "Enabled", "Time": {"Minutes": 15}}

    stack["restore_jobs"] = [
        {"RestoreJobId": "restore-1", "Status": "COMPLETED", "CompletionDate": fresh}
    ]
    stack["state_machines"][0]["executions"] = [
        {"name": "drill-1", "status": "SUCCEEDED", "stopDate": fresh}
    ]
    return stack


def live_stack():
    """STATE B — after lab steps 6a, 7 and 8."""
    stack = base_stack()
    fresh = NOW - timedelta(minutes=10)
    for vault in stack["vaults"]:
        vault["LatestRecoveryPointTime"] = fresh
        vault["NumberOfRecoveryPoints"] = 1
    stack["restore_jobs"] = [
        {"RestoreJobId": "restore-1", "Status": "COMPLETED", "CompletionDate": fresh}
    ]
    stack["state_machines"][0]["executions"] = [
        {"name": "drill-1", "status": "SUCCEEDED", "stopDate": fresh}
    ]
    return stack


def run_all(stack):
    findings = []
    for _check_id, check in A.CHECKS:
        findings += check(stack, stack["region"])
    return findings


def ids(findings):
    return sorted(f.check_id for f in findings)


def shaped_findings():
    """15 findings built WITHOUT calling any check, matching STATE A's histogram."""
    out = []
    for i, severity in enumerate(CONTRACT_SHAPE, 1):
        out.append(
            A.Finding(
                check_id=f"DR-{i:03d}",
                severity=severity,
                resource_type="AWS::Test::Resource",
                resource_id=f"resource-{i:02d}",
                title=f"Synthetic finding {i}",
                detail="Detail text long enough to exercise the wrapper in the table renderer, several words over the eighty-eight character width it uses.",
                remediation="Remediation text, also long enough to wrap across more than one rendered line so the continuation indent is exercised.",
                evidence={"index": i, "severity": severity},
                region=PRIMARY if i % 2 else DR,
            )
        )
    return out


###############################################################################
# FIRE — 16 tests, one per check
###############################################################################


class TestChecksFire(unittest.TestCase):
    def test_dr_001_fires_on_single_az_asg(self):
        findings = A.check_single_az_compute(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_id, "DR-001")
        self.assertEqual(findings[0].severity, "CRITICAL")
        self.assertIn("legacy", findings[0].resource_id)

    def test_dr_002_fires_on_single_nat_serving_two_azs(self):
        findings = A.check_single_az_nat(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "HIGH")
        self.assertEqual(findings[0].resource_id, "vpc-day08")
        self.assertEqual(findings[0].evidence["nat_gateway_azs"], ["us-east-1a"])

    def test_dr_003_fires_on_ec2_health_check_type(self):
        findings = A.check_asg_health_check_type(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["HealthCheckType"], "EC2")

    def test_dr_004_fires_on_zero_grace_period(self):
        findings = A.check_health_check_grace_period(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        # MEDIUM, not HIGH: the group uses EC2 health checks, so this is not
        # yet a boot loop. It becomes one the moment DR-003 is remediated.
        self.assertEqual(findings[0].severity, "MEDIUM")

    def test_dr_005_fires_on_single_az_rds(self):
        stack = base_stack()
        stack["db_instances"] = [
            {"DBInstanceIdentifier": "cbc-day08-db-ab12cd", "MultiAZ": False,
             "BackupRetentionPeriod": 7, "Engine": "postgres", "DBInstanceClass": "db.t3.micro"}
        ]
        findings = A.check_rds_multi_az(stack, PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "CRITICAL")

    def test_dr_006_fires_on_one_day_retention(self):
        stack = base_stack()
        stack["db_instances"] = [
            {"DBInstanceIdentifier": "cbc-day08-db-ab12cd", "MultiAZ": True,
             "BackupRetentionPeriod": 1, "Engine": "postgres"}
        ]
        findings = A.check_rds_backup_retention(stack, PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["BackupRetentionPeriod"], 1)

    def test_dr_007_fires_on_table_without_pitr(self):
        findings = A.check_dynamodb_pitr(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertIn("sessions", findings[0].resource_id)

    def test_dr_008_fires_on_empty_vaults_in_both_regions(self):
        findings = A.check_recovery_point_age(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 2)
        self.assertEqual(sorted(f.region for f in findings), [PRIMARY, DR])

    def test_dr_009_fires_once_per_unlocked_vault(self):
        findings = A.check_vault_lock(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 2)
        self.assertTrue(any(f.evidence["is_dr_region"] for f in findings))

    def test_dr_010_fires_once_for_the_whole_account(self):
        findings = A.check_never_restored(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].resource_type, A.RT_ACCOUNT)
        self.assertEqual(findings[0].resource_id, ACCOUNT)

    def test_dr_011_fires_on_a_same_region_copy_target(self):
        stack = base_stack()
        # Both halves of the check: an SRR bucket and a copy rule pointing home.
        stack["buckets"][0]["Replication"]["Rules"][0]["Destination"]["Bucket"] = (
            "arn:aws:s3:::cbc-day08-dr-archive-123456789012-ab12cd"
        )
        stack["backup_plans"][0]["Rules"][0]["CopyActions"] = [
            {"DestinationBackupVaultArn": VAULT_MAIN_ARN}
        ]
        findings = A.check_same_region_dr_target(stack, PRIMARY)
        self.assertEqual(len(findings), 2)
        self.assertEqual({f.severity for f in findings}, {"HIGH"})

    def test_dr_012_fires_on_unversioned_bucket(self):
        findings = A.check_bucket_versioning(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].evidence["name_suggests_dr"])

    def test_dr_013_fires_when_replication_lag_is_unmeasurable(self):
        findings = A.check_replication_measurable(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0].evidence["Metrics"])

    def test_dr_014_fires_on_failover_record_without_health_check(self):
        stack = base_stack()
        stack["route53_records"] = [
            {"Name": "app.example.com.", "Type": "A", "SetIdentifier": "primary",
             "Failover": "PRIMARY", "TTL": 60}
        ]
        findings = A.check_failover_record_health(stack, PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "HIGH")

    def test_dr_015_fires_on_workflow_with_no_brake(self):
        findings = A.check_recovery_workflow_brakes(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 1)
        self.assertIn("naive", findings[0].resource_id)
        self.assertTrue(findings[0].evidence["dry_run_hardcoded_false"])

    def test_dr_016_fires_on_every_untested_workflow(self):
        findings = A.check_failover_never_tested(base_stack(), PRIMARY)
        self.assertEqual(len(findings), 2)
        self.assertEqual({f.severity for f in findings}, {"CRITICAL"})


###############################################################################
# SILENT — 16 tests, one per check, against the reference build
###############################################################################


class TestChecksSilent(unittest.TestCase):
    def setUp(self):
        self.stack = clean_stack()

    def test_dr_001_silent_on_multi_az_asg(self):
        self.assertEqual(A.check_single_az_compute(self.stack, PRIMARY), [])

    def test_dr_002_silent_with_per_az_nat(self):
        self.assertEqual(A.check_single_az_nat(self.stack, PRIMARY), [])

    def test_dr_003_silent_on_elb_health_check_type(self):
        self.assertEqual(A.check_asg_health_check_type(self.stack, PRIMARY), [])

    def test_dr_004_silent_on_adequate_grace_period(self):
        self.assertEqual(A.check_health_check_grace_period(self.stack, PRIMARY), [])

    def test_dr_005_silent_on_multi_az_rds(self):
        self.stack["db_instances"] = [
            {"DBInstanceIdentifier": "db-1", "MultiAZ": True, "BackupRetentionPeriod": 7}
        ]
        self.assertEqual(A.check_rds_multi_az(self.stack, PRIMARY), [])

    def test_dr_006_silent_on_seven_day_retention(self):
        self.stack["db_instances"] = [
            {"DBInstanceIdentifier": "db-1", "MultiAZ": True, "BackupRetentionPeriod": 7}
        ]
        self.assertEqual(A.check_rds_backup_retention(self.stack, PRIMARY), [])

    def test_dr_007_silent_on_table_with_pitr(self):
        self.assertEqual(A.check_dynamodb_pitr(self.stack, PRIMARY), [])

    def test_dr_008_silent_on_fresh_recovery_points(self):
        self.assertEqual(A.check_recovery_point_age(self.stack, PRIMARY), [])

    def test_dr_009_silent_on_locked_vaults(self):
        self.assertEqual(A.check_vault_lock(self.stack, PRIMARY), [])

    def test_dr_010_silent_after_a_completed_restore(self):
        self.assertEqual(A.check_never_restored(self.stack, PRIMARY), [])

    def test_dr_011_silent_on_cross_region_targets(self):
        self.assertEqual(A.check_same_region_dr_target(self.stack, PRIMARY), [])

    def test_dr_012_silent_on_versioned_buckets(self):
        self.assertEqual(A.check_bucket_versioning(self.stack, PRIMARY), [])

    def test_dr_013_silent_with_replication_time_control(self):
        self.assertEqual(A.check_replication_measurable(self.stack, PRIMARY), [])

    def test_dr_014_silent_when_health_check_is_attached(self):
        self.stack["route53_records"] = [
            {"Name": "app.example.com.", "Type": "A", "SetIdentifier": "primary",
             "Failover": "PRIMARY", "TTL": 60, "HealthCheckId": "hc-abc"},
            # An alias with evaluate-target-health is the other legitimate
            # shape and must not be flagged either.
            {"Name": "app.example.com.", "Type": "A", "SetIdentifier": "secondary",
             "Failover": "SECONDARY",
             "AliasTarget": {"EvaluateTargetHealth": True, "DNSName": "alb.example."}},
        ]
        self.assertEqual(A.check_failover_record_health(self.stack, PRIMARY), [])

    def test_dr_015_silent_on_gated_workflow(self):
        self.assertEqual(A.check_recovery_workflow_brakes(self.stack, PRIMARY), [])

    def test_dr_016_silent_after_a_successful_execution(self):
        self.assertEqual(A.check_failover_never_tested(self.stack, PRIMARY), [])


###############################################################################
# COVERING — 15 tests
###############################################################################


class TestContractTotals(unittest.TestCase):
    def test_static_state_matches_the_contract(self):
        """STATE A: 15 findings, 195 points, 0/100."""
        findings = run_all(base_stack())
        self.assertEqual(len(findings), 15)
        self.assertEqual(sum(f.weight for f in findings), 195)
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertEqual(
            ids(findings),
            ["DR-001", "DR-002", "DR-003", "DR-004", "DR-007", "DR-008", "DR-008",
             "DR-009", "DR-009", "DR-010", "DR-012", "DR-013", "DR-015", "DR-016",
             "DR-016"],
        )
        # The registry itself, asserted here rather than in a test of its own:
        # sixteen checks, stable ids, in order. Renumbering these breaks every
        # suppression and dashboard anybody has written against them.
        self.assertEqual(len(A.CHECKS), 16)
        self.assertEqual(
            [check_id for check_id, _ in A.CHECKS],
            [f"DR-{i:03d}" for i in range(1, 17)],
        )

    def test_live_state_matches_the_contract(self):
        """STATE B: 11 findings, 125 points, 0/100."""
        findings = run_all(live_stack())
        self.assertEqual(len(findings), 11)
        self.assertEqual(sum(f.weight for f in findings), 125)
        self.assertEqual(A.calculate_score(findings), 0)
        self.assertNotIn("DR-010", ids(findings))
        self.assertNotIn("DR-008", ids(findings))

    def test_the_clock_alone_changes_the_answer(self):
        """STATE C: 61 minutes later, nothing changed by anybody.

        The thesis of the whole day, as an assertion. Same account, same
        configuration, same everything — two findings appear because recovery
        points aged past the declared RPO. An audit that passes at 14:00 fails
        at 15:01.

        Only possible to test because the clock is stack["now"] rather than
        datetime.now().
        """
        before = run_all(live_stack())
        later = live_stack()
        later["now"] = NOW + timedelta(minutes=61)
        after = run_all(later)

        self.assertEqual(len(before), 11)
        self.assertEqual(len(after), 13)
        self.assertEqual(sum(f.weight for f in after), 145)
        self.assertEqual(A.calculate_score(after), 0)
        self.assertEqual(
            [i for i in ids(after) if i not in ids(before)], ["DR-008", "DR-008"]
        )

    def test_clean_input_produces_zero_findings(self):
        """STATE D: the reference build scores 100/100 with an empty list.

        The single most important test in the file. A checker that never fires
        is useless; a checker that always fires is worse, because people switch
        it off and then stop reading anything it says.
        """
        findings = run_all(clean_stack())
        self.assertEqual(findings, [])
        self.assertEqual(A.calculate_score(findings), 100)
        self.assertTrue(A.score_grade(100).startswith("A"))


class TestScoring(unittest.TestCase):
    def test_score_floors_at_zero_and_grades_correctly(self):
        self.assertEqual(A.calculate_score(run_all(base_stack())), 0)
        self.assertEqual(A.SEVERITY_WEIGHTS["CRITICAL"], 25)
        self.assertEqual(A.SEVERITY_WEIGHTS["HIGH"], 10)
        self.assertEqual(A.SEVERITY_WEIGHTS["MEDIUM"], 4)
        self.assertEqual(A.SEVERITY_WEIGHTS["LOW"], 1)
        self.assertEqual(A.SEVERITY_WEIGHTS["INFO"], 0)
        for score, letter in ((100, "A"), (90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")):
            self.assertTrue(A.score_grade(score).startswith(letter), score)

    def test_min_severity_filters_display_not_the_score(self):
        findings = run_all(base_stack())
        score = A.calculate_score(findings)
        for level in ("CRITICAL", "HIGH", "MEDIUM", "INFO"):
            shown = A.filter_by_severity(findings, level)
            self.assertLessEqual(len(shown), len(findings))
            self.assertEqual(A.calculate_score(findings), score)
        self.assertEqual(len(A.filter_by_severity(findings, "CRITICAL")), 5)
        self.assertEqual(len(A.filter_by_severity(findings, "HIGH")), 10)


class TestSilentByDesign(unittest.TestCase):
    def test_dr_011_cannot_fire_from_any_state_of_this_stack(self):
        """DR-011 is silent by DESIGN, not by situation.

        The dr_region variable's cross-variable validation refuses
        dr_region == aws_region, the S3 replica is created under the aws.dr
        provider, and the backup copy rule targets the DR vault. No shipped
        default and no typo produces the fault — so the check is silent in
        every state of the contract, and its silence is a property of the
        auditor rather than of today's account.

        Distinguish this from DR-005, DR-006 and DR-014, which are silent by
        SITUATION: nothing has to change for those to start firing except
        somebody adding a database or a hosted zone.
        """
        for stack in (base_stack(), live_stack(), clean_stack()):
            self.assertEqual(A.check_same_region_dr_target(stack, PRIMARY), [])

        # And the contrast, asserted rather than described: one boolean turns
        # the silent-by-situation checks on.
        situational = base_stack()
        situational["db_instances"] = [
            {"DBInstanceIdentifier": "db-1", "MultiAZ": False, "BackupRetentionPeriod": 1}
        ]
        self.assertEqual(len(A.check_rds_multi_az(situational, PRIMARY)), 1)
        self.assertEqual(len(A.check_rds_backup_retention(situational, PRIMARY)), 1)


class TestDeliberateInteractions(unittest.TestCase):
    def test_three_checks_fire_on_one_scaling_group(self):
        """DR-001, DR-003 and DR-004 all hit the legacy ASG and are not duplicates.

        WHERE it runs, WHETHER it notices an application failure, and WHETHER a
        replacement can start at all. Fixing one leaves the other two.
        """
        stack = base_stack()
        hits = (
            A.check_single_az_compute(stack, PRIMARY)
            + A.check_asg_health_check_type(stack, PRIMARY)
            + A.check_health_check_grace_period(stack, PRIMARY)
        )
        self.assertEqual(len(hits), 3)
        self.assertEqual({f.resource_id for f in hits}, {"cbc-day08-legacy-asg-ab12cd"})
        self.assertEqual(ids(hits), ["DR-001", "DR-003", "DR-004"])

    def test_dr_008_and_dr_010_are_independent(self):
        """A vault full of fresh recovery points that nobody has ever restored.

        The normal state of most organisations: 0 points on DR-008 and 25 on
        DR-010. They look like the same check and are not.
        """
        stack = base_stack()
        for vault in stack["vaults"]:
            vault["LatestRecoveryPointTime"] = NOW - timedelta(minutes=5)
            vault["NumberOfRecoveryPoints"] = 12
        self.assertEqual(A.check_recovery_point_age(stack, PRIMARY), [])
        self.assertEqual(len(A.check_never_restored(stack, PRIMARY)), 1)

        # And the reverse: restored once, but the recovery points have aged out.
        stack["restore_jobs"] = [{"RestoreJobId": "r1", "Status": "COMPLETED"}]
        for vault in stack["vaults"]:
            vault["LatestRecoveryPointTime"] = NOW - timedelta(hours=9)
        self.assertEqual(len(A.check_recovery_point_age(stack, PRIMARY)), 2)
        self.assertEqual(A.check_never_restored(stack, PRIMARY), [])

    def test_dr_015_and_dr_016_both_fire_on_the_naive_workflow(self):
        """Untested AND ungated. Different faults, neither remediates the other."""
        stack = base_stack()
        gated = A.check_recovery_workflow_brakes(stack, PRIMARY)
        untested = A.check_failover_never_tested(stack, PRIMARY)
        naive = "cbc-day08-naive-failover-ab12cd"
        self.assertEqual([f.resource_id for f in gated], [naive])
        self.assertIn(naive, {f.resource_id for f in untested})

        # Testing it does not add a gate.
        stack["state_machines"][1]["executions"] = [
            {"name": "e1", "status": "SUCCEEDED", "stopDate": NOW - timedelta(minutes=1)}
        ]
        self.assertEqual(len(A.check_recovery_workflow_brakes(stack, PRIMARY)), 1)
        self.assertEqual(
            [f.resource_id for f in A.check_failover_never_tested(stack, PRIMARY)],
            ["cbc-day08-recovery-ab12cd"],
        )


class TestHelpers(unittest.TestCase):
    def test_finding_dataclass_and_the_list_and_policy_helpers(self):
        finding = A.Finding(
            check_id="DR-999", severity="HIGH", resource_type="T", resource_id="r",
            title="t", detail="d", remediation="r",
        )
        self.assertEqual(finding.weight, 10)
        self.assertEqual(finding.to_dict()["check_id"], "DR-999")
        with self.assertRaises(ValueError):
            A.Finding(
                check_id="DR-999", severity="SEVERE", resource_type="T",
                resource_id="r", title="t", detail="d", remediation="r",
            )

        self.assertEqual(A.as_list(None), [])
        self.assertEqual(A.as_list("one"), ["one"])
        self.assertEqual(A.as_list(["one", "two"]), ["one", "two"])
        self.assertEqual(A.parse_policy(None), {})
        self.assertEqual(A.parse_policy("not json"), {})
        self.assertEqual(A.parse_policy('{"a": 1}'), {"a": 1})
        self.assertEqual(A.parse_policy({"a": 1}), {"a": 1})
        self.assertEqual(A.parse_policy("%7B%22a%22%3A1%7D"), {"a": 1})
        self.assertEqual(list(A.chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])
        self.assertEqual(A._truncate("abcdef", 4), "abc\u2026")
        self.assertEqual(len(A._wrap("word " * 40, 20)[0]) <= 20, True)

    def test_clock_and_arn_region_helpers(self):
        stack = {"now": NOW}
        self.assertEqual(A._now(stack), NOW)
        self.assertEqual(A._now({"now": "2026-07-24T12:00:00Z"}), NOW)
        self.assertAlmostEqual(
            A._age_minutes(NOW - timedelta(minutes=90), NOW), 90.0, places=3
        )
        self.assertIsNone(A._age_minutes(None, NOW))
        self.assertAlmostEqual(A._age_days(NOW - timedelta(days=2), NOW), 2.0, places=3)
        self.assertEqual(A._region_of_arn(VAULT_DR_ARN), DR)
        self.assertEqual(A._region_of_arn("arn:aws:s3:::bucket"), "")
        self.assertEqual(A._region_of_arn(None), "")
        self.assertEqual(A._humanise_minutes(None), "never")
        self.assertIn("hours", A._humanise_minutes(600))

    def test_workflow_introspection_walks_nested_branches(self):
        """Putting the failover inside a Parallel branch must not hide it."""
        nested = {
            "StartAt": "Fan",
            "States": {
                "Fan": {
                    "Type": "Parallel",
                    "Branches": [
                        {
                            "StartAt": "Inner",
                            "States": {
                                "Inner": {
                                    "Type": "Task",
                                    "Parameters": {"action": "failover", "dry_run": False},
                                    "End": True,
                                }
                            },
                        }
                    ],
                    "End": True,
                }
            },
        }
        self.assertIn("Inner", A.state_machine_states(nested))
        self.assertEqual(A.workflow_irreversible_actions(nested), ["Inner:failover"])
        self.assertTrue(A.workflow_forces_live(nested))
        self.assertEqual(A.workflow_gates(nested), set())

        self.assertIn("waitfortasktoken", A.workflow_gates(GOOD_DEFINITION))
        self.assertIn("check_kill_switch", A.workflow_gates(GOOD_DEFINITION))
        self.assertFalse(A.workflow_forces_live(GOOD_DEFINITION))


class TestRenderers(unittest.TestCase):
    """Built from Finding(...) directly, never from the checks.

    A renderer test that calls the checks produces an ERROR rather than a clean
    failure when pointed at a stubbed challenge file, and an error tells a
    learner nothing about what they got wrong.
    """

    def setUp(self):
        self.findings = shaped_findings()
        self.stats = {"asgs": 2, "vaults": 2, "buckets": 3, "workflows": 2}
        self.score = A.calculate_score(self.findings)

    def test_render_table_with_and_without_findings(self):
        # CONTRACT_SHAPE reproduces STATE A's severity histogram, so these
        # findings total the contract's points without a check being called.
        self.assertEqual(len(self.findings), 15)
        self.assertEqual(sum(f.weight for f in self.findings), 195)
        self.assertEqual(self.score, 0)
        counts = {sev: CONTRACT_SHAPE.count(sev) for sev in set(CONTRACT_SHAPE)}
        self.assertEqual(counts, {"CRITICAL": 5, "HIGH": 5, "MEDIUM": 5})

        text = A.render_table(self.findings, self.stats, self.score, False)
        self.assertIn("DISASTER RECOVERY AUDIT", text)
        self.assertIn("Day 08", text)
        self.assertIn("COMPLIANCE SCORE", text)
        self.assertIn("0/100", text)
        for finding in self.findings:
            self.assertIn(finding.check_id, text)
        self.assertNotIn("\033[", text)

        coloured = A.render_table(self.findings, self.stats, self.score, True)
        self.assertIn("\033[", coloured)

        empty = A.render_table([], self.stats, 100, False)
        self.assertIn("No findings", empty)
        self.assertIn("100/100", empty)

    def test_render_json_and_csv_are_machine_readable(self):
        payload = json.loads(A.render_json(self.findings, self.stats, self.score))
        self.assertEqual(payload["audit"], "dr_audit")
        self.assertEqual(payload["day"], "08")
        self.assertEqual(payload["finding_count"], 15)
        self.assertEqual(payload["compliance_score"], 0)
        self.assertEqual(payload["summary"]["CRITICAL"], 5)
        self.assertEqual(payload["runtime_dependent_checks"], ["DR-008", "DR-010", "DR-016"])

        import csv as _csv

        rows = list(_csv.reader(io.StringIO(A.render_csv(self.findings))))
        self.assertEqual(rows[0][0], "check_id")
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[1][1], "CRITICAL")
        self.assertEqual(rows[1][2], "25")
        self.assertEqual(A.render_csv([]).count("\n"), 1)

        # The CLI surface, asserted alongside the machine-readable formats
        # because they are consumed together: a pipeline runs --format json
        # --quiet and needs both to be stable.
        parser = A.build_parser()
        args = parser.parse_args(["--rpo-minutes", "15", "--dr-region", "eu-west-1"])
        self.assertEqual(args.rpo_minutes, 15)
        self.assertEqual(args.dr_region, "eu-west-1")
        self.assertEqual(args.min_severity, "INFO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
