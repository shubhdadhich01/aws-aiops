#!/usr/bin/env python3
"""
dr_audit.py — Day 08 high availability and disaster recovery auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits failure domains, backup, replication, DNS failover and the automated
recovery path built on top of them.

The bias this tool has, stated up front: it is far more interested in
RECOVERY PATHS NOBODY HAS RUN than in redundancy nobody has built. A team with
one Availability Zone knows it has one Availability Zone. A team with a
cross-region backup vault, a documented RTO of fifteen minutes and no restore
job in the account's entire history believes it has disaster recovery, and
what it actually has is a hypothesis with a runbook.

An untested recovery path is a hypothesis, and RTO is a claim about a
procedure nobody has run.

What it checks
--------------
    DR-001  Compute in a single AZ        one failure domain             CRITICAL
    DR-002  Single-AZ NAT dependency      green dashboard, no egress     HIGH
    DR-003  ASG health check type EC2     a dead app is a healthy host   HIGH
    DR-004  No health check grace period  a boot loop that looks like AZ HIGH/MEDIUM
    DR-005  RDS not Multi-AZ              an AZ failure is a restore     CRITICAL
    DR-006  RDS retention 0 or 1 day      backups for the wrong failure  HIGH
    DR-007  DynamoDB without PITR         RPO is your last manual backup MEDIUM
    DR-008  No recovery point within RPO  the number is decaying now     HIGH
    DR-009  Backup vault with no lock     deletable by whoever got in    MEDIUM
    DR-010  No backup ever restored       a backup is a file until then  CRITICAL
    DR-011  DR copy in the source region  same blast radius              HIGH
    DR-012  S3 bucket without versioning  no rollback, and no CRR either MEDIUM
    DR-013  Replication lag unmeasurable  an RPO you cannot state        HIGH
    DR-014  Failover record, no health chk the trigger is disconnected   HIGH
    DR-015  Failover with no brake        nobody decides, nobody can stop CRITICAL
    DR-016  Failover never executed       the thesis of the entire day   CRITICAL

Three things carried over, deliberately
---------------------------------------
**One signature.** Every check takes `(stack: Dict, region: str)` and returns
`List[Finding]`. Several need cross-resource context to be correct — DR-002
must relate route tables to NAT gateways to subnets to AZs before it can say
anything, DR-011 must compare a replication destination's region against its
source's — and a one-resource signature makes that impossible without a global.

**Time is injected, not read.** `stack["now"]` is set once by `collect()`.
Day 07 introduced this and Day 08 needs it more: backup age, restore recency
and last-tested-failover are all age-based, and a check that calls
`datetime.now()` is a check whose tests depend on when CI ran. It also makes
this day's central claim testable — that the same unchanged account passes at
14:00 and fails at 15:01.

**Two regions, one audit.** `--dr-region` is not optional decoration. A
single-region audit of a multi-region DR posture is not an audit: it cannot
see whether the copy vault exists, whether it holds anything, or whether it is
in the region it is supposed to be in. Everything collected from the DR region
is tagged with its region in the stack, and every Finding carries the region
of the resource it is about rather than the region you invoked with.

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

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

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
# Severity weights
#
# Identical to Days 03 through 07 on purpose. By Day 10 you will have six of
# these tools and one mental model for reading their output.
#
# Day 08 uses only CRITICAL, HIGH and MEDIUM. There are no LOW or INFO checks
# here, and that is a decision rather than an oversight: on this day every
# fault either costs you data or costs you time during an outage, and a thing
# that does not matter when the region is on fire does not belong in an audit
# whose whole subject is the hour the region is on fire.
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

    check_id     Stable identifier (DR-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken. resource_id is the thing you
                 would type into the console or the CLI to look at it.
    title        One line, imperative, readable in a table.
    detail       What was actually observed. Include the real values.
    remediation  What to do about it, concretely.
    evidence     Raw values so the finding is auditable without re-querying.
    region       The region of the RESOURCE, not of the invocation. Two of
                 this day's checks report on resources in the DR region and a
                 finding that lies about where a thing lives is worse than no
                 finding.
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
# Paginator helpers
#
# An account with 400 snapshots is completely ordinary, and an audit that
# reports on the first 50 is worse than no audit, because it produces a clean
# report you believe.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Any]:
    """Collect every page of a paginated boto3 operation into one list."""
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
                f"  ! Access denied calling {operation}. Skipping the checks that "
                f"depend on it. Attach SecurityAudit or ReadOnlyAccess to fix.",
                file=sys.stderr,
            )
            return []
        raise
    return items


def chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    """Yield fixed-size chunks. Several AWS APIs cap the number of IDs per call."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def as_list(value: Any) -> List[Any]:
    """AWS APIs use a string where a list of one would do.

    A Step Functions `Retry` block's `ErrorEquals` is a list; a policy
    document's `Action` may be either. Every parser that forgets this has a
    wildcard-detection bug, because the single-string form is exactly the form
    the interesting value takes.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_policy(document: Any) -> Dict[str, Any]:
    """Return a JSON document as a dict.

    IAM hands policies back URL-encoded, Step Functions hands state machine
    definitions back as plain JSON strings, and our own tests hand them back as
    dicts. Accept all three rather than making every caller remember which.

    On this day the important caller is DR-015, which reads a state machine
    DEFINITION. That is the one place in this repo where an audit reasons about
    a program rather than about a configuration, and getting the parse wrong
    means silently concluding that every workflow is safe.
    """
    if document is None:
        return {}
    if isinstance(document, dict):
        return document
    if isinstance(document, (bytes, bytearray)):
        document = document.decode("utf-8", "replace")
    if isinstance(document, str):
        text = document.strip()
        if not text:
            return {}
        if text.startswith("%7B") or text.startswith("%7b"):
            from urllib.parse import unquote

            text = unquote(text)
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


###############################################################################
# Constants the checks reason about
###############################################################################

# The minimum number of Availability Zones that makes the phrase "highly
# available" true for a compute tier. Two. Not because two is generous, but
# because one is not a number of failure domains.
MIN_AZS = 2

# A grace period below this is a boot loop waiting for a slow morning. The
# stack's own application is ready in about sixty seconds; the floor is
# deliberately lower than that, because the check is looking for values that
# cannot possibly be right rather than for values that are merely tight.
MIN_GRACE_SECONDS = 30

# RDS backup retention at or below this is not a backup posture. Zero disables
# automated backups and point-in-time restore entirely; one day cannot recover
# from corruption discovered after a weekend.
MIN_RDS_RETENTION_DAYS = 2

# Step Functions state machine actions that cannot be undone by doing nothing.
# A workflow that can invoke one of these without a brake is DR-015.
IRREVERSIBLE_ACTIONS: Set[str] = {
    "failover",
    "failback",
    "promote",
    "promote_replica",
    "delete",
    "restore",
}

# Markers in a state machine definition that constitute a brake. Any ONE of
# these is enough for DR-015 to stay silent, which is deliberate: the check is
# looking for workflows with NO gate at all, not for workflows that chose a
# different gate from the one this repo happens to use.
GATE_MARKERS: Set[str] = {
    "waitfortasktoken",
    "check_kill_switch",
    "kill_switch",
    "approval",
}

# Resource types, spelled once. A typo in one of these produces a finding that
# is correct and unsearchable.
RT_ASG = "AWS::AutoScaling::AutoScalingGroup"
RT_VPC = "AWS::EC2::VPC"
RT_RDS = "AWS::RDS::DBInstance"
RT_TABLE = "AWS::DynamoDB::Table"
RT_VAULT = "AWS::Backup::BackupVault"
RT_ACCOUNT = "AWS::Account"
RT_BUCKET = "AWS::S3::Bucket"
RT_RECORD = "AWS::Route53::RecordSet"
RT_SFN = "AWS::StepFunctions::StateMachine"


###############################################################################
# Shared derivations
###############################################################################


def _now(stack: Dict[str, Any]) -> datetime:
    """The clock, injected rather than read.

    Day 07 introduced this. Day 08 leans on it harder than any other day,
    because three checks here are age-based and one of them — DR-008 — exists
    to demonstrate that an unchanged account's audit result changes with the
    clock alone. That lesson is only demonstrable if the clock is a value.
    """
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


def _age_minutes(value: Any, now: datetime) -> Optional[float]:
    """Age in MINUTES rather than days, unlike Days 06 and 07.

    The unit is the lesson. An RPO expressed in days is a decision you have
    already made about how much data you are willing to lose; expressing these
    ages in minutes keeps the comparison against rpo_minutes honest, and stops
    a 23-hour-old recovery point rounding to "1" and looking fine.
    """
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 60.0


def _age_days(value: Any, now: datetime) -> Optional[float]:
    minutes = _age_minutes(value, now)
    return None if minutes is None else minutes / 1440.0


def _region_of_arn(arn: Any) -> str:
    """The region field of an ARN, or "" if it is not one or has none.

    DR-011 is built on this. An ARN's region field is the only reliable way to
    know where a copy target actually lives — the vault NAME in a copy rule
    tells you nothing, and a DR vault called "dr-vault" in us-east-1 is exactly
    the fault the check exists for.
    """
    text = str(arn or "")
    parts = text.split(":")
    if len(parts) < 4 or parts[0] != "arn":
        return ""
    return parts[3]


def _humanise_minutes(minutes: Optional[float]) -> str:
    if minutes is None:
        return "never"
    if minutes < 90:
        return f"{minutes:.0f} minutes"
    if minutes < 2880:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.1f} days"


def _azs_of_subnets(stack: Dict[str, Any], subnet_ids: Iterable[str]) -> List[str]:
    """Map subnet ids to their AZ names, dropping ones we could not resolve.

    Resolving through subnets rather than trusting an ASG's own
    `AvailabilityZones` field is deliberate. That field is populated by AWS
    from the subnets and is usually right, but it is also settable, and a stack
    that lists three AZs while every subnet is in one is a real shape — it is
    what an ASG looks like after somebody deleted two subnets and left the
    group alone.
    """
    subnets = stack.get("subnets") or {}
    out: List[str] = []
    for subnet_id in subnet_ids:
        info = subnets.get(subnet_id)
        if info and info.get("AvailabilityZone"):
            out.append(info["AvailabilityZone"])
    return out


def state_machine_states(definition: Any) -> Dict[str, Any]:
    """Every state in a definition, including nested Parallel/Map branches.

    Nesting matters and is the obvious way to defeat a naive version of
    DR-015: put the failover inside a Parallel branch and a top-level scan
    finds nothing interesting. This walks the whole tree.
    """
    parsed = parse_policy(definition)
    out: Dict[str, Any] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        states = node.get("States")
        if isinstance(states, dict):
            for name, body in states.items():
                if isinstance(body, dict):
                    out[str(name)] = body
                    for branch in as_list(body.get("Branches")):
                        walk(branch)
                    if isinstance(body.get("Iterator"), dict):
                        walk(body["Iterator"])
                    if isinstance(body.get("ItemProcessor"), dict):
                        walk(body["ItemProcessor"])

    walk(parsed)
    return out


def workflow_gates(definition: Any) -> Set[str]:
    """Which brakes a state machine definition contains, if any.

    Deliberately a substring search over the serialised definition rather than
    a structural one. A gate can be expressed as a Task resource
    (`...waitForTaskToken`), as a Lambda action string (`check_kill_switch`),
    or as a state name (`ApprovalGate`), and a structural matcher that
    understood only this repo's shape would report every other team's
    perfectly good workflow as ungated. False negatives here are much cheaper
    than false positives: a check that flags correct workflows is a check
    people switch off.
    """
    text = json.dumps(parse_policy(definition), default=str).lower()
    return {marker for marker in GATE_MARKERS if marker in text}


def workflow_irreversible_actions(definition: Any) -> List[str]:
    """The irreversible actions a definition can invoke, with their state names."""
    found: List[str] = []
    for name, body in state_machine_states(definition).items():
        params = body.get("Parameters")
        if not isinstance(params, dict):
            continue
        action = str(params.get("action", "")).strip().lower()
        if action in IRREVERSIBLE_ACTIONS:
            found.append(f"{name}:{action}")
    return sorted(found)


def workflow_forces_live(definition: Any) -> bool:
    """True if the definition hardcodes dry_run=false on an irreversible step.

    The distinction this draws is the whole of DR-015's judgement. A workflow
    whose dry_run comes from a REFERENCE (`"dry_run.$": "$.something"`) has a
    rehearsal mode: somebody can run it safely. A workflow with a literal
    `false` compiled into the definition has none, and there is no way to
    exercise it without causing the thing it exists to respond to.
    """
    for body in state_machine_states(definition).values():
        params = body.get("Parameters")
        if not isinstance(params, dict):
            continue
        action = str(params.get("action", "")).strip().lower()
        if action not in IRREVERSIBLE_ACTIONS:
            continue
        if "dry_run.$" in params:
            continue
        if params.get("dry_run") is False:
            return True
    return False


###############################################################################
# Checks
#
# Every one is a pure function of (stack, region). Nothing here calls AWS,
# nothing here reads the clock, and nothing here has an opinion that is not
# visible in its arguments. That is why tests/test_checks.py needs no
# credentials, and it is the structural idea worth stealing from all six of
# these tools.
###############################################################################


def check_single_az_compute(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-001 — an Auto Scaling group whose subnets are all in one AZ.

    The most basic failure and the one that survives longest, because nothing
    about it is visible while the AZ is healthy. The ASG works. The instances
    launch. The dashboard is green. The architecture diagram — which was drawn
    from intent rather than from state — shows two zones.

    It usually arrives one of three ways, none of which is carelessness:
    somebody deleted a subnet during a CIDR reshuffle and the ASG kept the
    survivor; somebody pinned an ASG to one AZ to chase a cross-AZ data
    transfer bill; or somebody copied a working single-AZ stack and scaled it
    up without revisiting the subnet list.

    Note that this resolves AZs through the SUBNETS rather than trusting the
    group's own AvailabilityZones field. That field is usually right and is
    also settable, and an ASG listing three zones while every subnet is in one
    is a real shape.
    """
    findings: List[Finding] = []

    for group in stack.get("asgs") or []:
        name = group.get("AutoScalingGroupName", "unknown")
        subnet_ids = [s for s in str(group.get("VPCZoneIdentifier", "")).split(",") if s]
        azs = sorted(set(_azs_of_subnets(stack, subnet_ids)))
        if not azs:
            azs = sorted(set(as_list(group.get("AvailabilityZones"))))
        if len(azs) >= MIN_AZS:
            continue

        findings.append(
            Finding(
                check_id="DR-001",
                severity="CRITICAL",
                resource_type=RT_ASG,
                resource_id=str(name),
                title="Auto Scaling group is confined to a single Availability Zone",
                detail=(
                    f"{name} spans {len(azs)} Availability Zone(s) "
                    f"({', '.join(azs) or 'none resolvable'}) across "
                    f"{len(subnet_ids)} subnet(s). Everything this group runs shares "
                    f"one failure domain. When that zone has a power, network or "
                    f"cooling event, the group does not degrade — it stops, and the "
                    f"load balancer has nowhere to send traffic. Nothing about this "
                    f"is visible while the zone is healthy, which is why it survives "
                    f"for years in accounts that believe they are multi-AZ."
                ),
                remediation=(
                    f"Add subnets in at least one more AZ and set them on the group: "
                    f"`aws autoscaling update-auto-scaling-group "
                    f"--auto-scaling-group-name {name} --vpc-zone-identifier "
                    f"subnet-aaa,subnet-bbb`. Then size for the loss: two AZs at "
                    f"N+1 means running at 50% utilisation, because the survivor "
                    f"takes 100% of the load it was sized for at 50%. Multi-AZ and "
                    f"multi-AZ with enough headroom to survive losing an AZ are "
                    f"different architectures, and the second one is what you "
                    f"promised."
                ),
                evidence={
                    "AutoScalingGroupName": name,
                    "subnets": subnet_ids,
                    "availability_zones": azs,
                    "DesiredCapacity": group.get("DesiredCapacity"),
                },
                region=region,
            )
        )
    return findings


def check_single_az_nat(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-002 — multi-AZ private subnets routed through a single-AZ NAT gateway.

    THE MOST COMMON REAL HIGH-AVAILABILITY DEFECT IN PRODUCTION AWS ACCOUNTS,
    and it is produced by a COST OPTIMISATION rather than by carelessness.

    A NAT gateway is zonal: one subnet, one AZ, ~$32.85/month plus processing.
    The correct architecture is one per AZ. So somebody, entirely reasonably,
    deletes one and points both private route tables at the survivor. The bill
    halves. Every test passes. The diagram is unchanged.

    The failure mode is not an outage, which is exactly what makes it
    dangerous. AZ-a goes away; instances in AZ-b keep running, pass EC2 status
    checks, and pass ALB health checks — because a target group health check is
    an HTTP GET from inside the VPC and does not traverse NAT. The dashboard
    stays green. Every outbound call fails: the payment provider, the OAuth
    endpoint, the package repository during a deploy, S3 if there is no gateway
    endpoint.

    You get an incident that reads as "third-party API is down" for the first
    twenty minutes.

    Reported once per VPC rather than once per route table, because the fault
    is a property of the topology and three findings for one missing gateway
    trains people to skim.
    """
    findings: List[Finding] = []

    nat_az: Dict[str, str] = {}
    for gateway in stack.get("nat_gateways") or []:
        if str(gateway.get("State", "available")) not in ("available", "pending"):
            continue
        az = (stack.get("subnets") or {}).get(gateway.get("SubnetId"), {}).get(
            "AvailabilityZone"
        )
        if az:
            nat_az[str(gateway.get("NatGatewayId"))] = az

    by_vpc: Dict[str, Dict[str, Any]] = {}
    for table in stack.get("route_tables") or []:
        nat_ids = [
            str(route.get("NatGatewayId"))
            for route in table.get("Routes") or []
            if route.get("NatGatewayId")
        ]
        if not nat_ids:
            continue
        vpc_id = str(table.get("VpcId", "unknown"))
        bucket = by_vpc.setdefault(vpc_id, {"nat_ids": set(), "azs": set(), "tables": []})
        bucket["nat_ids"].update(nat_ids)
        bucket["tables"].append(str(table.get("RouteTableId")))
        subnet_ids = [
            str(assoc.get("SubnetId"))
            for assoc in table.get("Associations") or []
            if assoc.get("SubnetId")
        ]
        bucket["azs"].update(_azs_of_subnets(stack, subnet_ids))

    for vpc_id, bucket in sorted(by_vpc.items()):
        serving_azs = sorted({nat_az[n] for n in bucket["nat_ids"] if n in nat_az})
        subnet_azs = sorted(bucket["azs"])

        if len(subnet_azs) < MIN_AZS or len(serving_azs) >= MIN_AZS:
            continue

        findings.append(
            Finding(
                check_id="DR-002",
                severity="HIGH",
                resource_type=RT_VPC,
                resource_id=vpc_id,
                title="Private subnets in several AZs depend on one AZ's NAT gateway",
                detail=(
                    f"{vpc_id} has private subnets in {len(subnet_azs)} AZ(s) "
                    f"({', '.join(subnet_azs)}) whose default route points at NAT "
                    f"gateway(s) living in only {', '.join(serving_azs) or 'an AZ we could not resolve'}. "
                    f"That is a single-AZ dependency inside an architecture that is "
                    f"otherwise multi-AZ. If that zone fails, instances elsewhere "
                    f"keep running and keep passing every health check you have, and "
                    f"lose all outbound internet access. Route tables involved: "
                    f"{', '.join(sorted(bucket['tables']))}."
                ),
                remediation=(
                    "Create one NAT gateway per AZ and point each private route "
                    "table at the gateway in its own zone (~$32.85/month each, plus "
                    "~$0.045/GB processed). Before you do, add the free VPC gateway "
                    "endpoints for S3 and DynamoDB — they cost nothing, remove the "
                    "largest share of NAT traffic in most stacks, and remove an AZ "
                    "dependency from your data path. Once you need three or four "
                    "interface endpoints (~$7.30/month each per AZ), per-AZ NAT is "
                    "cheaper than endpoints; below that, endpoints win on both cost "
                    "and availability."
                ),
                evidence={
                    "VpcId": vpc_id,
                    "subnet_azs": subnet_azs,
                    "nat_gateway_azs": serving_azs,
                    "nat_gateway_ids": sorted(bucket["nat_ids"]),
                    "route_tables": sorted(bucket["tables"]),
                },
                region=region,
            )
        )
    return findings


def check_asg_health_check_type(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-003 — the ASG replaces instances EC2 says are dead, not ones your app says are.

    THE MOST EXPENSIVE MISSING LINE IN AWS: `health_check_type = "ELB"`.

    The default is "EC2". With it, the ASG replaces an instance when the
    hypervisor loses it or its status checks fail. It knows nothing about your
    application.

    Now picture the failure that actually happens. The process deadlocks, or
    the JVM is in permanent full GC, or the container exited and nothing
    restarted it. The instance is running. The OS answers. EC2 status checks
    pass. What follows:

      - the target group health check fails, correctly
      - the ALB deregisters that target, correctly
      - traffic goes to the healthy instances: THE SERVICE IS FINE
      - the ASG does nothing, because EC2 says the instance is alive
      - you pay for an instance serving zero requests, indefinitely
      - your effective capacity is silently N-1, and nothing alarms

    This state survives for months and is discovered during the next incident,
    when the spare capacity that was supposed to absorb an AZ failure turns out
    to have been dead since March.

    This fires on ANY group with a non-ELB health check, including groups with
    no load balancer at all. That is deliberate and not an over-reach: a group
    with no load-balancer health check has no application-level health signal
    whatsoever, which is strictly worse than having one and ignoring it.
    """
    findings: List[Finding] = []

    for group in stack.get("asgs") or []:
        check_type = str(group.get("HealthCheckType", "EC2")).upper()
        if check_type == "ELB":
            continue

        name = group.get("AutoScalingGroupName", "unknown")
        target_groups = as_list(group.get("TargetGroupARNs"))
        load_balancers = as_list(group.get("LoadBalancerNames"))
        attached = bool(target_groups or load_balancers)

        findings.append(
            Finding(
                check_id="DR-003",
                severity="HIGH",
                resource_type=RT_ASG,
                resource_id=str(name),
                title="Auto Scaling group health check type is EC2, not ELB",
                detail=(
                    f"{name} uses HealthCheckType={check_type}. "
                    + (
                        "It is attached to a load balancer whose health check it "
                        "ignores, so an instance can fail your application's health "
                        "check, be removed from rotation, and never be replaced."
                        if attached
                        else "It has no load balancer at all, so there is no "
                        "application-level health signal anywhere — which is worse "
                        "than ignoring one, not better."
                    )
                    + " The result is an instance you pay for that serves nothing, "
                    "permanently, with your effective capacity silently reduced and "
                    "nothing alarming, because from the service's point of view "
                    "everything is fine."
                ),
                remediation=(
                    f"`aws autoscaling update-auto-scaling-group "
                    f"--auto-scaling-group-name {name} --health-check-type ELB "
                    f"--health-check-grace-period 300`. SET BOTH, in the same "
                    f"command. Turning on ELB health checks without an adequate "
                    f"grace period converts a silent capacity leak into a loud boot "
                    f"loop: the ASG starts terminating instances for being slow to "
                    f"start, and their replacements for the same reason, forever. "
                    f"If this group has no load balancer, the equivalent is a custom "
                    f"health check calling `set-instance-health` from something that "
                    f"knows what healthy means."
                ),
                evidence={
                    "AutoScalingGroupName": name,
                    "HealthCheckType": check_type,
                    "TargetGroupARNs": target_groups,
                    "LoadBalancerNames": load_balancers,
                },
                region=region,
            )
        )
    return findings


def check_health_check_grace_period(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-004 — a grace period too short for the application to start.

    The boot-loop variable. Set it shorter than your application takes to
    become ready and you get: instance launches, health check fails because the
    app is still starting, ASG terminates it, ASG launches a replacement,
    replacement fails identically, forever. The activity history shows a tidy
    launch/terminate loop that looks like an AZ problem and is not.

    It is SELF-CONCEALING DURING A REAL INCIDENT, which is the part worth
    carrying away. Under load an application boots slower — cold caches,
    contended disks, a database already struggling — so a grace period that was
    adequate on a quiet Tuesday is inadequate on the one day it matters, and
    the ASG responds to your outage by killing every instance that tries to
    help.

    Severity is HIGH rather than MEDIUM when the group actually honours ELB
    health checks, because that is the combination that loops. With
    HealthCheckType=EC2 a short grace period is mostly harmless, and reporting
    it at the same severity would train people to ignore both.
    """
    findings: List[Finding] = []
    floor = int(stack.get("min_grace_seconds", MIN_GRACE_SECONDS))

    for group in stack.get("asgs") or []:
        grace = group.get("HealthCheckGracePeriod")
        if grace is None or int(grace) >= floor:
            continue

        name = group.get("AutoScalingGroupName", "unknown")
        honours_elb = str(group.get("HealthCheckType", "EC2")).upper() == "ELB"

        findings.append(
            Finding(
                check_id="DR-004",
                severity="HIGH" if honours_elb else "MEDIUM",
                resource_type=RT_ASG,
                resource_id=str(name),
                title="Health check grace period is too short to survive a slow boot",
                detail=(
                    f"{name} has HealthCheckGracePeriod={int(grace)}s, below the "
                    f"{floor}s floor this audit uses. "
                    + (
                        "The group honours ELB health checks, so an instance that is "
                        "still starting when the grace period expires is terminated "
                        "and replaced by another instance that will be terminated for "
                        "the same reason. That is a boot loop, and it presents as an "
                        "AZ problem."
                        if honours_elb
                        else "The group uses EC2 health checks, so this is currently "
                        "mostly harmless — but it becomes a boot loop the moment "
                        "somebody correctly sets HealthCheckType=ELB, which is check "
                        "DR-003's remediation. Fix both together."
                    )
                ),
                remediation=(
                    f"Time a boot to the first passing health check, then double it: "
                    f"`aws autoscaling update-auto-scaling-group "
                    f"--auto-scaling-group-name {name} --health-check-grace-period "
                    f"300`. Measure it under load rather than on an idle account — "
                    f"the number you need is the one from a bad day, not a good one."
                ),
                evidence={
                    "AutoScalingGroupName": name,
                    "HealthCheckGracePeriod": grace,
                    "HealthCheckType": group.get("HealthCheckType"),
                    "floor_seconds": floor,
                },
                region=region,
            )
        )
    return findings


def check_rds_multi_az(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-005 — an RDS instance running in a single Availability Zone.

    Multi-AZ gives you a synchronous standby in another zone and an automatic
    DNS failover in typically 60-120 seconds. Every commit is acknowledged by
    the standby before the primary returns, so your RPO for an AZ failure is
    zero. Without it, an AZ failure is a RESTORE — measured in tens of minutes
    at best, and bounded below by however long it takes to provision new
    storage and replay logs.

    It costs exactly double. There is no partial version.

    And the misconception this check exists to kill: THE STANDBY SERVES NO
    TRAFFIC. It is not a read replica. You cannot query it. It does not improve
    read throughput, write throughput or latency. Teams enable it expecting
    read scaling and are then confused that nothing got faster. (Read replicas
    are a different feature, asynchronous, promotable manually, billed
    separately. Most people who need one need both.)

    One more thing worth knowing before you claim zero downtime: a Multi-AZ
    failover drops every connection and rolls back every in-flight transaction.
    An application with a connection pool and no retry logic experiences it as
    an outage whose length is set by the pool's TCP timeout, which is very
    often longer than the failover it was supposed to hide.
    """
    findings: List[Finding] = []

    for db in stack.get("db_instances") or []:
        if db.get("MultiAZ"):
            continue

        identifier = db.get("DBInstanceIdentifier", "unknown")
        findings.append(
            Finding(
                check_id="DR-005",
                severity="CRITICAL",
                resource_type=RT_RDS,
                resource_id=str(identifier),
                title="RDS instance is single-AZ",
                detail=(
                    f"{identifier} ({db.get('Engine', 'unknown')} on "
                    f"{db.get('DBInstanceClass', 'unknown')}) has MultiAZ=false. "
                    f"An Availability Zone failure is therefore a restore rather "
                    f"than a failover: you lose everything since the last recovery "
                    f"point and you wait for provisioning and log replay. Every "
                    f"stateless tier in front of this database can be as multi-AZ as "
                    f"you like — the RTO of the whole system is the RTO of its data "
                    f"tier."
                ),
                remediation=(
                    f"`aws rds modify-db-instance --db-instance-identifier "
                    f"{identifier} --multi-az --apply-immediately`. Budget the time "
                    f"as well as the money: the conversion takes a snapshot, builds "
                    f"a standby and syncs it, so 15-25 minutes, and it costs exactly "
                    f"double from then on. If doubling the bill is not justifiable "
                    f"for this workload, that is a legitimate answer — write it "
                    f"down, and write down the restore-based RTO it implies, rather "
                    f"than leaving the DR document claiming a number this "
                    f"configuration cannot produce."
                ),
                evidence={
                    "DBInstanceIdentifier": identifier,
                    "MultiAZ": db.get("MultiAZ"),
                    "AvailabilityZone": db.get("AvailabilityZone"),
                    "Engine": db.get("Engine"),
                },
                region=region,
            )
        )
    return findings


def check_rds_backup_retention(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-006 — automated backups disabled, or retained for a day.

    ZERO DISABLES AUTOMATED BACKUPS ENTIRELY, and with them point-in-time
    restore. Your RPO becomes "the last manual snapshot somebody remembered to
    take", which is a number you find out during the incident.

    One day of retention is technically backups and practically not. Note WHICH
    failure backups are for: AZ failure is handled by Multi-AZ, hardware
    failure by the storage layer. Backups exist for the case where the DATA WAS
    WRONG and nobody noticed immediately — a bad migration, a truncating bug, a
    delete with the wrong WHERE clause. Those are discovered in hours or days,
    not seconds. Corruption noticed on Friday afternoon that began Thursday
    morning is unrecoverable with one day of retention, and that is the exact
    scenario the retention exists for.

    Seven days is the minimum that survives a weekend plus a Monday of nobody
    looking.
    """
    findings: List[Finding] = []

    for db in stack.get("db_instances") or []:
        retention = db.get("BackupRetentionPeriod")
        if retention is None or int(retention) >= MIN_RDS_RETENTION_DAYS:
            continue

        identifier = db.get("DBInstanceIdentifier", "unknown")
        disabled = int(retention) == 0
        findings.append(
            Finding(
                check_id="DR-006",
                severity="HIGH",
                resource_type=RT_RDS,
                resource_id=str(identifier),
                title=(
                    "Automated backups are disabled"
                    if disabled
                    else "Automated backup retention is one day"
                ),
                detail=(
                    f"{identifier} has BackupRetentionPeriod={int(retention)}. "
                    + (
                        "Zero disables automated backups AND point-in-time restore "
                        "entirely. There is no recovery point other than whatever "
                        "manual snapshot somebody happens to have taken, and the "
                        "date of that snapshot is a number you will find out during "
                        "the incident."
                        if disabled
                        else "Point-in-time restore covers the last 24 hours only. "
                        "Corruption discovered on Friday that began on Thursday is "
                        "unrecoverable - and corruption is the failure backups exist "
                        "for, since AZ failure is Multi-AZ's job and hardware failure "
                        "is the storage layer's."
                    )
                ),
                remediation=(
                    f"`aws rds modify-db-instance --db-instance-identifier "
                    f"{identifier} --backup-retention-period 7 --apply-immediately`. "
                    f"Backup storage up to the size of the database is free; beyond "
                    f"that it is ~$0.095/GB-month, so seven days of a 20 GiB database "
                    f"costs approximately nothing. Then confirm the backup window "
                    f"does not overlap your peak, and go and restore one."
                ),
                evidence={
                    "DBInstanceIdentifier": identifier,
                    "BackupRetentionPeriod": retention,
                    "PreferredBackupWindow": db.get("PreferredBackupWindow"),
                },
                region=region,
            )
        )
    return findings


def check_dynamodb_pitr(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-007 — a DynamoDB table without point-in-time recovery.

    PITR is continuous backup with restore to any second in the last 35 days,
    for ~$0.20 per GB-month. On most tables that is the cheapest RPO
    improvement available anywhere in AWS: from "the last manual backup
    somebody took" to roughly five minutes, with no schedule to maintain and no
    backup job to fail silently.

    THE PART THAT CATCHES PEOPLE, and which belongs in your RTO rather than
    your RPO: a PITR restore creates a NEW TABLE. It cannot restore in place
    and it cannot restore into an existing table. So the procedure is not
    "restore the table" — it is "restore to a new table, then repoint every
    consumer at a different name, then decide what to do about whatever wrote
    to the old table in the meantime". That is application work performed under
    pressure, and it is where the RTO of a data restore actually goes.

    The usual justification for leaving it off is that the table holds
    something regenerable — sessions, caches, derived data. That is often true
    on the day the table is created and much less often true two years later,
    when the sessions table also holds the shopping cart.
    """
    findings: List[Finding] = []

    for table in stack.get("tables") or []:
        status = str(table.get("PointInTimeRecoveryStatus", "DISABLED")).upper()
        if status == "ENABLED":
            continue

        name = table.get("TableName", "unknown")
        replicas = [r.get("RegionName") for r in as_list(table.get("Replicas"))]

        findings.append(
            Finding(
                check_id="DR-007",
                severity="MEDIUM",
                resource_type=RT_TABLE,
                resource_id=str(name),
                title="DynamoDB table has no point-in-time recovery",
                detail=(
                    f"{name} has PointInTimeRecovery={status}. Its RPO is whatever "
                    f"on-demand backup somebody last took, and there is no way to "
                    f"roll back to a moment before a bad write. "
                    + (
                        f"It does replicate to {', '.join(str(r) for r in replicas)}, "
                        f"which protects against losing a region and does nothing at "
                        f"all against corruption — global tables replicate the bad "
                        f"write faithfully, in under a second."
                        if replicas
                        else "It has no replica either, so a regional event loses it "
                        "entirely."
                    )
                ),
                remediation=(
                    f"`aws dynamodb update-continuous-backups --table-name {name} "
                    f"--point-in-time-recovery-specification "
                    f"PointInTimeRecoveryEnabled=true` (~$0.20/GB-month). Then "
                    f"rehearse the restore, and time the part after the restore: the "
                    f"new table has a different name, and every consumer has to be "
                    f"repointed before the data is usable. Enable PITR on each "
                    f"replica separately — it is per replica and billed per replica, "
                    f"and a replica without it is a DR copy you cannot roll back."
                ),
                evidence={
                    "TableName": name,
                    "PointInTimeRecoveryStatus": status,
                    "Replicas": replicas,
                },
                region=region,
            )
        )
    return findings


def check_recovery_point_age(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-008 — no recovery point inside the RPO you declared.

    THIS IS THE CHECK THAT MAKES THE DAY'S POINT, so read the whole docstring.

    It compares the age of the newest recovery point in each backup vault
    against `rpo_minutes`. An empty vault fires. A vault whose newest recovery
    point is older than the stated RPO fires.

    AND IT CHANGES ANSWER WITH NOTHING BUT THE CLOCK. Take a backup, run the
    audit, pass. Change nothing — no deploy, no console click, no merge — wait
    until the recovery point is older than the RPO, run the audit again, fail.

    That is not a defect. It is the difference between a configuration audit
    and a recovery audit. RTO and RPO are not properties of a configuration;
    they are claims about a PROCEDURE, and a claim about a procedure decays
    continuously from the last time the procedure ran. A merge-time-only audit
    certifies the account as it was on the day somebody last changed it, and
    that is not the property a DR posture needs to have.

    The practical consequence: with a schedule slower than your stated RPO,
    this check SAWTOOTHS — silent for the minutes after each successful job,
    firing again as the recovery point ages. Two runs one minute apart give
    different answers and both are correct. If that is uncomfortable, the fix
    is not a looser check. It is a schedule that is actually faster than the
    RPO you claimed, or an RPO you can actually defend.

    Reported per vault, including vaults in the DR region, because a fresh
    recovery point in the region that just failed is not a recovery point.
    """
    findings: List[Finding] = []
    now = _now(stack)
    rpo = float(stack.get("rpo_minutes", 60))

    for vault in stack.get("vaults") or []:
        name = vault.get("BackupVaultName", "unknown")
        vault_region = str(vault.get("Region") or region)
        latest = vault.get("LatestRecoveryPointTime")
        age = _age_minutes(latest, now)
        count = vault.get("NumberOfRecoveryPoints", 0)

        if age is not None and age <= rpo:
            continue

        empty = latest is None
        findings.append(
            Finding(
                check_id="DR-008",
                severity="HIGH",
                resource_type=RT_VAULT,
                resource_id=str(name),
                title=(
                    "Backup vault holds no recovery point at all"
                    if empty
                    else "Newest recovery point is older than the stated RPO"
                ),
                detail=(
                    f"{name} in {vault_region} holds {count} recovery point(s) and "
                    f"its newest is {_humanise_minutes(age)} old, against a declared "
                    f"RPO of {rpo:.0f} minutes. "
                    + (
                        "An empty vault is a plan that has never produced anything. "
                        "The schedule may be correct and the first job may simply not "
                        "have run yet — which is exactly the state a new DR posture is "
                        "in on the day somebody writes the RPO into a document."
                        if empty
                        else "Everything written since that recovery point is "
                        "currently unrecoverable from this vault. Note that this "
                        "answer changes with the clock alone: it will be true again "
                        "an hour from now whether or not anybody touches this "
                        "account."
                    )
                ),
                remediation=(
                    f"Make the schedule faster than the RPO, or change the RPO to one "
                    f"the schedule can support. Check what the plan actually does: "
                    f"`aws backup list-recovery-points-by-backup-vault "
                    f"--backup-vault-name {name} --region {vault_region} "
                    f"--query 'RecoveryPoints[0].CreationDate'`. If the vault is "
                    f"empty, run one now and watch it complete: `aws backup "
                    f"start-backup-job ...`. A daily schedule cannot support an "
                    f"hourly RPO, and no amount of replication elsewhere changes what "
                    f"THIS vault can restore."
                ),
                evidence={
                    "BackupVaultName": name,
                    "Region": vault_region,
                    "LatestRecoveryPointTime": latest,
                    "NumberOfRecoveryPoints": count,
                    "age_minutes": None if age is None else round(age, 1),
                    "rpo_minutes": rpo,
                },
                region=vault_region,
            )
        )
    return findings


def check_vault_lock(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-009 — a backup vault with no vault lock.

    Vault lock makes recovery points immutable: nobody, including the account
    root, can shorten retention or delete a recovery point before it expires.
    It is the control that survives a compromised administrator, which is the
    threat model backups are actually for once you take ransomware seriously.
    An attacker who has your credentials and wants your recovery options
    limited does not need a zero-day; they need `delete-recovery-point`.

    Two modes, and the difference is not a detail. GOVERNANCE can be removed by
    a principal with `backup:DeleteBackupVaultLockConfiguration` — protects
    against accident and process failure, not against admin. COMPLIANCE cannot
    be removed by anyone, ever, and the vault cannot be deleted while it holds
    recovery points, so you pay for that storage until the longest retention
    expires.

    The mode is selected by the PRESENCE of `ChangeableForDays` rather than by
    a value, which is a genuinely poor API and has produced real, unrecoverable
    bills. Start with governance.

    Reported per vault and deliberately NOT deduplicated up to the plan. A
    locked primary vault beside an unlocked DR copy vault is a real and common
    asymmetry, and it is exactly backwards: the DR vault is the one an attacker
    who has already compromised the primary account will reach for, because it
    is the copy that survives everything they just did.
    """
    findings: List[Finding] = []

    for vault in stack.get("vaults") or []:
        if vault.get("Locked"):
            continue

        name = vault.get("BackupVaultName", "unknown")
        vault_region = str(vault.get("Region") or region)
        is_dr = vault_region != str(stack.get("region") or region)

        findings.append(
            Finding(
                check_id="DR-009",
                severity="MEDIUM",
                resource_type=RT_VAULT,
                resource_id=str(name),
                title="Backup vault has no vault lock",
                detail=(
                    f"{name} in {vault_region} has no lock configuration, so every "
                    f"recovery point in it can be deleted by anyone holding "
                    f"backup:DeleteRecoveryPoint. "
                    + (
                        "This is the DR-region copy vault — the one that survives "
                        "everything an attacker does in the primary account, and "
                        "therefore the one they will go for next. An unlocked DR "
                        "vault behind a locked primary is exactly backwards."
                        if is_dr
                        else "Backups protect you from a bad migration and from an "
                        "administrator having a bad day. Only a lock protects you "
                        "from an administrator whose credentials somebody else is "
                        "using."
                    )
                ),
                remediation=(
                    f"Start with governance mode: `aws backup "
                    f"put-backup-vault-lock-configuration --backup-vault-name {name} "
                    f"--region {vault_region} --min-retention-days 7 "
                    f"--max-retention-days 35`. DO NOT add --changeable-for-days "
                    f"unless you mean compliance mode: its presence is what selects "
                    f"the irreversible variant, and after the cooling-off window "
                    f"nobody — including AWS Support — can remove it or delete the "
                    f"vault while it holds recovery points."
                ),
                evidence={
                    "BackupVaultName": name,
                    "Region": vault_region,
                    "Locked": bool(vault.get("Locked")),
                    "is_dr_region": is_dr,
                },
                region=vault_region,
            )
        )
    return findings


def check_never_restored(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-010 — nobody in this account has ever restored a backup.

    A BACKUP NOBODY HAS RESTORED IS A FILE.

    This is one finding for the whole account, deliberately. It is not attached
    to a vault or a table, because it is not a statement about a resource — it
    is a statement about the ORGANISATION. Attaching it to a resource id
    invites somebody to close it by deleting the resource.

    Everything a backup report can tell you is about whether the file exists.
    The failure modes that matter are all invisible there and all obvious after
    one restore:

      - the KMS key the snapshot was encrypted with has been rotated or deleted
      - the AMI the recovery point references no longer exists
      - the instance type is not available in the DR region
      - the database engine version has been deprecated and cannot be launched
      - the IAM role has backup permissions and not restore permissions
      - the restore works, and takes nine hours

    That last one is not a failure. It is an RTO, discovered.

    Note the interaction with DR-008: a vault full of fresh, correctly
    retained, cross-region-copied recovery points that has never had a single
    restore performed against it scores zero on DR-008 and twenty-five here.
    That is the normal state of most organisations.
    """
    jobs = stack.get("restore_jobs") or []
    completed = [j for j in jobs if str(j.get("Status", "")).upper() == "COMPLETED"]
    if completed:
        return []

    now = _now(stack)
    window = int(stack.get("restore_window_days", 365))
    account = str(stack.get("account_id") or "this account")
    attempted = len(jobs)

    return [
        Finding(
            check_id="DR-010",
            severity="CRITICAL",
            resource_type=RT_ACCOUNT,
            resource_id=account,
            title="No backup has ever been successfully restored in this account",
            detail=(
                f"AWS Backup reports {attempted} restore job(s) in the last "
                f"{window} days and none of them completed successfully. Every "
                f"recovery point in this account is therefore untested: you know "
                f"the files exist and you do not know that any of them can be "
                f"turned back into a running system. The failure modes that stop "
                f"a restore — a rotated KMS key, a missing AMI, an instance type "
                f"unavailable in the DR region, a deprecated engine version, a role "
                f"with backup permissions and not restore permissions — are all "
                f"invisible in a backup report and all obvious after one restore. "
                f"So is the one that is not a failure at all: a restore that works "
                f"and takes nine hours, which is an RTO you have just discovered "
                f"rather than declared."
                + (
                    ""
                    if attempted == 0
                    else " Note that restores were ATTEMPTED and did not complete, "
                    "which is a stronger signal than none having been tried."
                )
            ),
            remediation=(
                "Restore something this week, time it, and write the number down "
                "next to the RTO in your DR document. Restore into an isolated "
                "target so it cannot affect production, and restore into the DR "
                "REGION at least once — that is where the KMS key, the AMI and the "
                "instance type problems live. Then put it in the calendar: a "
                "restore test more than a quarter old is describing an "
                "architecture that has since changed. `aws backup "
                "start-restore-job --recovery-point-arn ... --iam-role-arn ... "
                "--metadata ...`"
            ),
            evidence={
                "restore_jobs_seen": attempted,
                "completed_restore_jobs": 0,
                "window_days": window,
                "checked_at": now.isoformat(),
            },
            region=str(stack.get("region") or region),
        )
    ]


def check_same_region_dr_target(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-011 — a replication or backup copy target in its source's region.

    SILENT BY DESIGN against this lab's stack, and the reason is worth stating
    because it is the difference between a check that means something and a
    check people learn to ignore.

    No shipped default and no typo in this repo's Terraform can produce this
    fault: `dr_region` carries a validation refusing `dr_region == aws_region`,
    the S3 replica bucket is created under `provider = aws.dr`, and the AWS
    Backup copy rule targets the DR vault or does not exist. There is no path
    through that configuration that puts a DR copy in the primary region, so
    the plan refuses to produce one.

    It is not a hypothetical fault in the wider world. S3 Same-Region
    Replication is a real and legitimate feature — compliance separation, log
    aggregation, cross-account isolation — and an AWS Backup copy rule will
    happily target a vault in the source region. Both get pressed into service
    as "DR" by people who were solving a different problem last week, and both
    produce a second copy inside the same blast radius.

    Note how the two halves resolve region differently, which is itself a
    lesson: a backup vault ARN carries its region in field four, and an S3
    bucket ARN does not carry a region AT ALL. The bucket half therefore has to
    resolve the destination through the collected bucket inventory, and a
    destination bucket we have never seen is reported as unknown rather than
    assumed safe.
    """
    findings: List[Finding] = []
    bucket_region = {
        str(b.get("Name")): str(b.get("Region") or "") for b in stack.get("buckets") or []
    }

    for bucket in stack.get("buckets") or []:
        name = str(bucket.get("Name", "unknown"))
        source_region = str(bucket.get("Region") or "")
        replication = bucket.get("Replication") or {}
        for rule in as_list(replication.get("Rules")):
            destination = (rule.get("Destination") or {}).get("Bucket", "")
            dest_name = str(destination).split(":::")[-1].split("/")[0]
            dest_region = bucket_region.get(dest_name, "")
            if not dest_region or not source_region or dest_region != source_region:
                continue

            findings.append(
                Finding(
                    check_id="DR-011",
                    severity="HIGH",
                    resource_type=RT_BUCKET,
                    resource_id=name,
                    title="Replication destination is in the source's own region",
                    detail=(
                        f"{name} replicates to {dest_name}, and both are in "
                        f"{source_region}. Same-Region Replication is a real feature "
                        f"with real uses — compliance separation, log aggregation, "
                        f"cross-account isolation — and disaster recovery is not one "
                        f"of them. Every event that removes the source removes the "
                        f"copy: a regional control-plane failure, a bad config push, "
                        f"an account-wide credential compromise. You are paying "
                        f"transfer and double storage for a copy inside the same "
                        f"blast radius."
                    ),
                    remediation=(
                        f"Decide which problem this replication is solving. If it is "
                        f"DR, create the destination bucket in another region and "
                        f"repoint the rule. If it is compliance or account "
                        f"separation, keep it and stop describing it as DR in the "
                        f"recovery plan — the plan is what somebody reads at 03:00."
                    ),
                    evidence={
                        "source_bucket": name,
                        "destination_bucket": dest_name,
                        "region": source_region,
                        "rule_id": rule.get("ID"),
                    },
                    region=source_region,
                )
            )

    for plan in stack.get("backup_plans") or []:
        plan_name = plan.get("BackupPlanName", "unknown")
        for rule in as_list(plan.get("Rules")):
            source_region = _region_of_arn(rule.get("TargetBackupVaultArn")) or str(
                stack.get("region") or region
            )
            for copy_action in as_list(rule.get("CopyActions")):
                dest_arn = copy_action.get("DestinationBackupVaultArn")
                dest_region = _region_of_arn(dest_arn)
                if not dest_region or dest_region != source_region:
                    continue

                findings.append(
                    Finding(
                        check_id="DR-011",
                        severity="HIGH",
                        resource_type="AWS::Backup::BackupPlan",
                        resource_id=str(plan_name),
                        title="Backup copy target is in the source vault's own region",
                        detail=(
                            f"Rule {rule.get('RuleName', '?')} of {plan_name} copies "
                            f"recovery points to a vault in {dest_region}, which is "
                            f"the same region as the vault it is copying from. The "
                            f"copy is billed twice and survives nothing the original "
                            f"does not. A copy rule pointed at the source region is "
                            f"usually a placeholder somebody meant to change."
                        ),
                        remediation=(
                            f"Create a vault in another region and repoint the copy "
                            f"action at it: `aws backup update-backup-plan "
                            f"--backup-plan-id {plan.get('BackupPlanId', '<id>')} "
                            f"...`. Then verify by ARN rather than by name — a vault "
                            f"called 'dr-vault' in the primary region is exactly the "
                            f"fault this check exists for."
                        ),
                        evidence={
                            "BackupPlanName": plan_name,
                            "RuleName": rule.get("RuleName"),
                            "TargetBackupVaultArn": rule.get("TargetBackupVaultArn"),
                            "DestinationBackupVaultArn": dest_arn,
                            "region": dest_region,
                        },
                        region=source_region,
                    )
                )

    return findings


def check_bucket_versioning(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-012 — an S3 bucket without versioning.

    Versioning is the difference between a bucket you can roll back and a
    bucket where an overwrite is final. It is also a HARD PRECONDITION for
    cross-region replication: the API rejects a replication rule on an
    unversioned bucket outright.

    That constraint produces a specific and very recognisable artefact — a
    bucket created for a DR requirement, named accordingly, with no replication
    rule, because the first attempt failed with a versioning error on a Friday
    and nobody came back to it. The bucket exists. It has "dr" or "backup" or
    "archive" in the name. It contains nothing that will ever leave the region,
    and it appears in the DR document as though it does.

    The cost objection is real and is handled by a lifecycle rule, not by
    leaving versioning off: with versioning on, deletes stop deleting, and
    every overwrite retains the old version and bills for it until something
    expires it. A versioned, replicated bucket with no noncurrent-version
    lifecycle rule is the most reliable way to grow a storage bill in a region
    nobody looks at.
    """
    findings: List[Finding] = []

    for bucket in stack.get("buckets") or []:
        status = str(bucket.get("Versioning", "Disabled") or "Disabled")
        if status == "Enabled":
            continue

        name = str(bucket.get("Name", "unknown"))
        bucket_region = str(bucket.get("Region") or region)
        suspended = status == "Suspended"
        looks_like_dr = any(
            token in name.lower() for token in ("dr", "backup", "archive", "replica")
        )

        findings.append(
            Finding(
                check_id="DR-012",
                severity="MEDIUM",
                resource_type=RT_BUCKET,
                resource_id=name,
                title=(
                    "Bucket versioning is suspended"
                    if suspended
                    else "Bucket has no versioning"
                ),
                detail=(
                    f"{name} in {bucket_region} has versioning {status}. An overwrite "
                    f"or a delete is final, there is no rollback from a bad deploy or "
                    f"a bad script, and cross-region replication cannot be configured "
                    f"on it at all — the API rejects a replication rule on an "
                    f"unversioned bucket."
                    + (
                        " The name suggests this bucket exists for disaster recovery, "
                        "which makes the absence of versioning worth checking against "
                        "whatever the DR document claims about it."
                        if looks_like_dr
                        else ""
                    )
                    + (
                        " Suspended is the more alarming of the two states: versioning "
                        "was on, somebody turned it off, and the versions created "
                        "before that are still there and still billing."
                        if suspended
                        else ""
                    )
                ),
                remediation=(
                    f"`aws s3api put-bucket-versioning --bucket {name} "
                    f"--versioning-configuration Status=Enabled`, then IMMEDIATELY "
                    f"add a lifecycle rule expiring noncurrent versions "
                    f"(`NoncurrentVersionExpiration`) — versioning without one is "
                    f"how a bucket grows without bound. Enabling versioning is not "
                    f"retroactive: it protects objects written from now on."
                ),
                evidence={
                    "Name": name,
                    "Region": bucket_region,
                    "Versioning": status,
                    "name_suggests_dr": looks_like_dr,
                },
                region=bucket_region,
            )
        )
    return findings


def check_replication_measurable(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-013 — replication is configured and its lag cannot be measured.

    THE ONLY CHECK IN THIS SET THAT FIRES ON SOMETHING WHICH IS NOT BROKEN.

    The rule works. Objects replicate. What is absent is the METRIC.

    S3 cross-region replication is asynchronous and, by default, has NO SLA and
    NO metrics. Most objects arrive in seconds; some take minutes; under a
    large burst some take considerably longer. "Usually fast" is true and is
    not an RPO. An RPO is a number you can defend, and without replication
    metrics you cannot answer "what is my current replication lag" at all —
    which means any RPO you state for this bucket is an adjective wearing a
    number's clothes.

    Replication Time Control costs ~$0.015/GB on top of transfer and storage,
    and buys two things: a contractual 99.99%-within-15-minutes SLA, and
    CloudWatch metrics. The metrics are the part that matters. This is the
    clearest example in the repo of paying money for OBSERVABILITY rather than
    for capability: the data replicates either way, and what the money buys is
    the ability to say a true sentence about it.

    Day 06's argument in new clothes. A summary you cannot check is worse than
    no summary; an RPO you cannot measure is worse than no RPO, because you
    will quote it.

    A DISABLED rule is reported by the same check at the same severity, and
    that is deliberate rather than lazy: both are states where the DR document
    says "replicated" and the account cannot support the claim. The detail
    tells them apart.
    """
    findings: List[Finding] = []

    for bucket in stack.get("buckets") or []:
        replication = bucket.get("Replication") or {}
        rules = as_list(replication.get("Rules"))
        if not rules:
            continue

        name = str(bucket.get("Name", "unknown"))
        bucket_region = str(bucket.get("Region") or region)

        for rule in rules:
            rule_id = str(rule.get("ID", "unnamed"))
            disabled = str(rule.get("Status", "Enabled")) != "Enabled"
            destination = rule.get("Destination") or {}
            metrics = (destination.get("Metrics") or {}).get("Status")
            rtc = (destination.get("ReplicationTime") or {}).get("Status")
            measurable = str(metrics) == "Enabled" or str(rtc) == "Enabled"

            if not disabled and measurable:
                continue

            findings.append(
                Finding(
                    check_id="DR-013",
                    severity="HIGH",
                    resource_type=RT_BUCKET,
                    resource_id=name,
                    title=(
                        "Replication rule is disabled"
                        if disabled
                        else "Replication lag cannot be measured"
                    ),
                    detail=(
                        f"Rule {rule_id} on {name} "
                        + (
                            "has Status=Disabled. Nothing is replicating and nothing "
                            "says so — a disabled rule looks identical to an enabled "
                            "one in every summary view, and the DR document still "
                            "says this bucket is replicated."
                            if disabled
                            else "has neither Replication Time Control nor replication "
                            "metrics enabled. Objects do replicate, asynchronously, "
                            "with no SLA and no published lag. There is no API call "
                            "that will tell you how far behind the destination "
                            "currently is, which means the RPO you have written down "
                            "for this bucket cannot be verified by anyone, including "
                            "you."
                        )
                    ),
                    remediation=(
                        f"For the measurement: enable metrics, and Replication Time "
                        f"Control if you want the SLA (~$0.015/GB on top of transfer "
                        f"and storage). `aws s3api put-bucket-replication --bucket "
                        f"{name} --replication-configuration ...` with "
                        f"`Destination.Metrics.Status=Enabled` and "
                        f"`Destination.ReplicationTime.Status=Enabled`. Then alarm on "
                        f"the OperationsPendingReplication and ReplicationLatency "
                        f"metrics — the point of paying for them is that somebody "
                        f"finds out before the incident, not during it. Remember also "
                        f"that replication is not retroactive: run S3 Batch "
                        f"Replication if the bucket had objects before the rule."
                    ),
                    evidence={
                        "Name": name,
                        "rule_id": rule_id,
                        "Status": rule.get("Status"),
                        "Metrics": metrics,
                        "ReplicationTime": rtc,
                        "Destination": destination.get("Bucket"),
                    },
                    region=bucket_region,
                )
            )
    return findings


def check_failover_record_health(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-014 — a Route 53 failover record with no health check attached.

    This is worse than useless, and the reason is specific: Route 53 treats a
    PRIMARY failover record with no health check as PERMANENTLY HEALTHY. It
    never fails over. You have built the mechanism, wired the DNS, drawn it in
    the diagram, and disconnected the trigger.

    It is a configuration that passes every review that looks for the existence
    of things. The record set is there. Its type is PRIMARY. There is a
    SECONDARY. Only a review that asks "what makes this switch" catches it, and
    only a test proves it.

    An alias record with `evaluate_target_health = true` is a legitimate
    alternative — Route 53 uses the target's own health instead — so this check
    exempts those. That exemption is the thing to check by hand if you are
    reading a real account: alias plus evaluate-target-health works for ALBs
    and does not exist for a plain A record, and the two look similar in the
    console.
    """
    findings: List[Finding] = []

    for record in stack.get("route53_records") or []:
        failover = str(record.get("Failover", "") or "").upper()
        if failover not in ("PRIMARY", "SECONDARY"):
            continue
        if record.get("HealthCheckId"):
            continue
        if (record.get("AliasTarget") or {}).get("EvaluateTargetHealth"):
            continue

        name = str(record.get("Name", "unknown"))
        set_id = str(record.get("SetIdentifier", ""))

        findings.append(
            Finding(
                check_id="DR-014",
                severity="HIGH",
                resource_type=RT_RECORD,
                resource_id=f"{name} [{set_id}]" if set_id else name,
                title="Failover record has no health check",
                detail=(
                    f"{name} is a {failover} failover record with no HealthCheckId "
                    f"and no alias evaluate-target-health. Route 53 treats a PRIMARY "
                    f"record with no health signal as permanently healthy, so this "
                    f"configuration never fails over. Every part of the mechanism "
                    f"exists except the one that makes it move, and nothing in the "
                    f"console distinguishes it from a working one."
                ),
                remediation=(
                    f"Attach a health check to the record, or use an alias to a "
                    f"resource with `EvaluateTargetHealth=true`. A Route 53 health "
                    f"check is ~$0.50/month against an AWS endpoint. Then TEST IT by "
                    f"inverting the health check — `aws route53 update-health-check "
                    f"--health-check-id <id> --inverted` — and watching DNS actually "
                    f"move. Remember to un-invert it afterwards: while it is "
                    f"inverted you have no health signal at all from the primary."
                ),
                evidence={
                    "Name": name,
                    "SetIdentifier": set_id,
                    "Failover": failover,
                    "TTL": record.get("TTL"),
                    "Type": record.get("Type"),
                },
                region=region,
            )
        )
    return findings


def check_recovery_workflow_brakes(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-015 — a workflow that can execute an irreversible failover with no brake.

    Day 07's argument, carried forward and made heavier. An automated response
    is a decision you are making now, to be executed later, by nobody, on
    evidence that might be wrong. Day 07's automation contained a threat. This
    one declares a region dead.

    And the evidence it acts on is health checks, which lie during exactly the
    network conditions that make you want to fail over. An automated regional
    failover triggered by a transient partition is how you get split brain, and
    split brain in a last-writer-wins data store is silent, permanent data
    loss.

    The check reads the state machine DEFINITION — the one place in this repo
    where an audit reasons about a program rather than a configuration — and
    asks two questions:

      1. can it invoke an irreversible action?
      2. does it contain ANY gate: a kill switch, an approval wait, or a
         dry-run mode that is a reference rather than a hardcoded false?

    Any one gate is enough for it to stay silent. That is deliberate: the check
    is looking for workflows with NO brake at all, not for workflows that chose
    a different brake from this repo's. A check that flags other people's
    perfectly good designs is a check people switch off, and a switched-off
    check finds nothing.

    Nested Parallel and Map branches are walked, because putting the failover
    inside a branch is the obvious way to defeat a top-level scan and would
    make this check reassuring rather than useful.
    """
    findings: List[Finding] = []

    for machine in stack.get("state_machines") or []:
        definition = machine.get("definition")
        actions = workflow_irreversible_actions(definition)
        if not actions:
            continue

        gates = workflow_gates(definition)
        forced_live = workflow_forces_live(definition)
        if gates and not forced_live:
            continue

        name = machine.get("name", "unknown")
        machine_region = str(machine.get("Region") or region)

        findings.append(
            Finding(
                check_id="DR-015",
                severity="CRITICAL",
                resource_type=RT_SFN,
                resource_id=str(name),
                title="Recovery workflow can execute an irreversible action with no brake",
                detail=(
                    f"{name} can invoke {', '.join(actions)} and has "
                    + (
                        "no kill switch, no approval gate and no dry-run mode."
                        if not gates
                        else f"gate(s) {sorted(gates)}, but "
                    )
                    + (
                        "the irreversible step has dry_run hardcoded to false, so "
                        "there is no way to rehearse it — the only way to exercise "
                        "this workflow is to cause the thing it responds to. "
                        if forced_live
                        else ""
                    )
                    + "Nothing decides whether the evidence is good, nothing stops it "
                    "once it has started, and nothing verifies that what it did "
                    "worked. Health checks fail during network partitions, bad "
                    "deploys and expired certificates as readily as during regional "
                    "outages, and this workflow cannot tell those apart."
                ),
                remediation=(
                    "Add three things, in this order of value. A KILL SWITCH read as "
                    "the first state, from SSM, failing closed if it cannot be read — "
                    "so somebody can stop it from a phone without a deploy. An "
                    "APPROVAL GATE (`waitForTaskToken`) before anything irreversible; "
                    "in-AZ recovery does not need one because it is reversible by "
                    "doing nothing, and a regional failover does. A DRY-RUN mode "
                    "passed by reference rather than hardcoded, so the workflow can "
                    "be rehearsed. Then add a VERIFY step that can fail the "
                    "execution — a workflow ending at 'executed' reports success when "
                    "the API call succeeded and the outcome did not."
                ),
                evidence={
                    "name": name,
                    "irreversible_actions": actions,
                    "gates_found": sorted(gates),
                    "dry_run_hardcoded_false": forced_live,
                    "state_count": len(state_machine_states(definition)),
                },
                region=machine_region,
            )
        )
    return findings


def check_failover_never_tested(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """DR-016 — the recovery workflow has never executed successfully.

    THE THESIS OF THE ENTIRE DAY, expressed as five lines of code.

    An untested recovery path is a hypothesis. The failover path is the only
    code in the system that runs exclusively during your worst hour, which
    makes it the least exercised code you own and the most confidently
    described. Every number in a DR plan — RTO, RPO, "we can fail over in
    fifteen minutes" — is a measurement or it is a wish, and almost all of them
    are wishes.

    A DRY RUN COUNTS. That is generous on purpose. A dry-run execution proves
    the state machine's paths, its IAM, its inputs and its notification route,
    which is most of what goes wrong; it does not prove the failover, and no
    audit can tell the difference from the outside. The honest position is that
    this check is a floor, not a certificate. If it is silent, somebody has run
    the thing at least once. That is a much lower bar than "we have DR" and a
    much higher one than most accounts clear.

    Reported per state machine, so an ungated workflow that has also never been
    executed produces both this and DR-015. Those are genuinely different
    faults with different remediations and neither fixes the other: adding an
    approval gate does not test it, and testing it does not add a gate.
    """
    findings: List[Finding] = []
    now = _now(stack)
    max_age = float(stack.get("failover_test_max_age_days", 90))

    for machine in stack.get("state_machines") or []:
        definition = machine.get("definition")
        if not workflow_irreversible_actions(definition):
            continue

        name = machine.get("name", "unknown")
        machine_region = str(machine.get("Region") or region)
        executions = as_list(machine.get("executions"))
        succeeded = [
            e for e in executions if str(e.get("status", "")).upper() == "SUCCEEDED"
        ]

        newest_age = None
        if succeeded:
            ages = [
                a
                for a in (_age_days(e.get("stopDate"), now) for e in succeeded)
                if a is not None
            ]
            newest_age = min(ages) if ages else None
            if newest_age is not None and newest_age <= max_age:
                continue
            if newest_age is None:
                continue

        never = not succeeded
        findings.append(
            Finding(
                check_id="DR-016",
                severity="CRITICAL",
                resource_type=RT_SFN,
                resource_id=str(name),
                title=(
                    "Recovery workflow has never been executed successfully"
                    if never
                    else "Recovery workflow has not been executed recently"
                ),
                detail=(
                    f"{name} is a recovery workflow capable of failing over, and "
                    + (
                        f"there is no successful execution in its history "
                        f"({len(executions)} execution(s) seen). It has never run. "
                        f"Whatever RTO is written next to it is a wish."
                        if never
                        else f"its most recent successful execution was "
                        f"{newest_age:.0f} days ago, past the {max_age:.0f}-day "
                        f"freshness window. A DR test more than a quarter old is "
                        f"describing an architecture that has since changed."
                    )
                    + " The failover path is the only code in this system that runs "
                    "exclusively during the worst hour you will have, which makes it "
                    "the least exercised code you own and the most confidently "
                    "described."
                ),
                remediation=(
                    f"Run it. Dry run first: `aws stepfunctions start-execution "
                    f"--state-machine-arn <arn> --region {machine_region}`, then read "
                    f"the execution history — the per-step timestamps ARE your RTO "
                    f"measurement, which is the reason to use a state machine rather "
                    f"than a Lambda. Then run it for real, in a maintenance window, "
                    f"WITH A STOPWATCH, and write the measured number next to the "
                    f"declared one. Then rehearse the FAILBACK in the same session: "
                    f"every exercise that ends at 'we failed over successfully' has "
                    f"tested half a procedure and measured a third of an RTO."
                ),
                evidence={
                    "name": name,
                    "Region": machine_region,
                    "executions_seen": len(executions),
                    "successful_executions": len(succeeded),
                    "days_since_last_success": None
                    if newest_age is None
                    else round(newest_age, 1),
                    "max_age_days": max_age,
                },
                region=machine_region,
            )
        )
    return findings


CHECKS = [
    ("DR-001", check_single_az_compute),
    ("DR-002", check_single_az_nat),
    ("DR-003", check_asg_health_check_type),
    ("DR-004", check_health_check_grace_period),
    ("DR-005", check_rds_multi_az),
    ("DR-006", check_rds_backup_retention),
    ("DR-007", check_dynamodb_pitr),
    ("DR-008", check_recovery_point_age),
    ("DR-009", check_vault_lock),
    ("DR-010", check_never_restored),
    ("DR-011", check_same_region_dr_target),
    ("DR-012", check_bucket_versioning),
    ("DR-013", check_replication_measurable),
    ("DR-014", check_failover_record_health),
    ("DR-015", check_recovery_workflow_brakes),
    ("DR-016", check_failover_never_tested),
]

LIVE_CHECKS = [check_id for check_id, _ in CHECKS]

# Checks that read RUNTIME state rather than configuration. Listed separately
# because their answer depends on WHEN you ran the tool, and on this day that
# is not a footnote — DR-008 exists specifically to demonstrate that an
# unchanged account's audit result changes with the clock alone.
RUNTIME_CHECKS = ["DR-008", "DR-010", "DR-016"]


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.

    Expect zero against this lab's stack with create_insecure_examples = true.
    Five CRITICAL findings are 125 points on their own. That is the intended
    shock. Do the lab, set create_insecure_examples = false, take one backup,
    perform one restore and run the workflow once, and the same tool with the
    same checks returns 100/100.
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
    w(colour("  DISASTER RECOVERY AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 08 · High Availability & Disaster Recovery\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('asgs', 0)} scaling group(s) · "
        f"{stats.get('vpcs', 0)} VPC(s) · "
        f"{stats.get('databases', 0)} database(s) · "
        f"{stats.get('tables', 0)} table(s) · "
        f"{stats.get('vaults', 0)} vault(s) · "
        f"{stats.get('recovery_points', 0)} recovery point(s) · "
        f"{stats.get('buckets', 0)} bucket(s) · "
        f"{stats.get('records', 0)} failover record(s) · "
        f"{stats.get('workflows', 0)} workflow(s)\n\n"
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
        w(f"  {'SEVERITY':<10} {'CHECK':<9} {'RESOURCE':<34} {'FINDING':<40}\n")
        w("  " + "-" * 96 + "\n")

        ordered = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER.index(f.severity), f.check_id, f.resource_id),
        )

        for f in ordered:
            sev = colour(f"{f.severity:<10}", f.severity, use_colour)
            w(
                f"  {sev} {f.check_id:<9} "
                f"{_truncate(f.resource_id, 33):<34} "
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
        "audit": "dr_audit",
        "day": "08",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
        # Named in the payload because a consumer diffing two runs needs to
        # know which checks could legitimately change without anybody touching
        # the account. On this day that is not a nicety: DR-008 changes answer
        # with the clock alone, and a dashboard that alerts on the delta will
        # page somebody hourly if it does not know that.
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


class DisasterRecoveryAuditor:
    """Collects one normalised snapshot of TWO regions, then runs pure checks.

    Everything that touches AWS happens in collect(); everything that decides
    anything happens in a function that takes a dict. That is why
    tests/test_checks.py needs no credentials — and it is the structural idea
    worth stealing from all six of these tools.

    The Day 08 addition is the second region. A single-region audit of a
    multi-region DR posture is not an audit: it cannot see whether the copy
    vault exists, whether it holds anything, or whether it is in the region it
    was supposed to be in. Every DR-region object collected here is tagged with
    its own region, and findings carry the region of the RESOURCE rather than
    the region you invoked with.
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        dr_region: str = "us-west-2",
        prefix: Optional[str] = None,
        rpo_minutes: int = 60,
        rto_minutes: int = 30,
        min_grace_seconds: int = MIN_GRACE_SECONDS,
        failover_test_max_age_days: int = 90,
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.dr_region = dr_region
        self.prefix = prefix
        self.rpo_minutes = rpo_minutes
        self.rto_minutes = rto_minutes
        self.min_grace_seconds = min_grace_seconds
        self.failover_test_max_age_days = failover_test_max_age_days
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stack: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "asgs": 0,
            "vpcs": 0,
            "databases": 0,
            "tables": 0,
            "vaults": 0,
            "recovery_points": 0,
            "buckets": 0,
            "records": 0,
            "workflows": 0,
        }

        self.session: Any = None
        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.autoscaling = self.session.client("autoscaling")
            self.ec2 = self.session.client("ec2")
            self.rds = self.session.client("rds")
            self.dynamodb = self.session.client("dynamodb")
            self.backup = self.session.client("backup")
            self.s3 = self.session.client("s3")
            self.route53 = self.session.client("route53")
            self.sfn = self.session.client("stepfunctions")
            self.sts = self.session.client("sts")
            # The DR region gets its own clients for the services whose state
            # actually lives there. Forgetting this is how a DR audit reports a
            # healthy backup posture while the copy vault is empty.
            self.backup_dr = self.session.client("backup", region_name=dr_region)
            self.dynamodb_dr = self.session.client("dynamodb", region_name=dr_region)
        except (BotoCoreError, NoCredentialsError) as exc:
            self.log(f"  ! No AWS session ({exc}).")
            self.session = None

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def _swallow(self, operation: str, resource: str, exc: ClientError) -> None:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in (
            "ResourceNotFoundException",
            "ResourceNotFound",
            "AccessDeniedException",
            "NoSuchBucket",
            "NoSuchLifecycleConfiguration",
            "ReplicationConfigurationNotFoundError",
            "InvalidParameterValueException",
            "DBInstanceNotFound",
            "StateMachineDoesNotExist",
        ):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    def _in_scope(self, name: str) -> bool:
        return not self.prefix or str(name).startswith(self.prefix)

    # -- collectors ---------------------------------------------------------

    def _collect_compute(self, stack: Dict[str, Any]) -> None:
        groups = paginate(
            self.autoscaling, "describe_auto_scaling_groups", "AutoScalingGroups"
        )
        stack["asgs"] = [
            g for g in groups if self._in_scope(g.get("AutoScalingGroupName", ""))
        ]

        # Subnets, route tables and NAT gateways are collected WITHOUT the
        # prefix filter, deliberately. They are topology rather than resources:
        # DR-002 has to relate a route table to a NAT gateway to a subnet to an
        # AZ, and a prefix filter applied halfway through that chain produces a
        # confident answer about a graph with holes in it.
        subnets = paginate(self.ec2, "describe_subnets", "Subnets")
        stack["subnets"] = {s["SubnetId"]: s for s in subnets}
        stack["route_tables"] = paginate(self.ec2, "describe_route_tables", "RouteTables")
        stack["nat_gateways"] = paginate(self.ec2, "describe_nat_gateways", "NatGateways")

        self.stats["asgs"] = len(stack["asgs"])
        self.stats["vpcs"] = len({s.get("VpcId") for s in subnets if s.get("VpcId")})

    def _collect_data(self, stack: Dict[str, Any]) -> None:
        stack["db_instances"] = [
            db
            for db in paginate(self.rds, "describe_db_instances", "DBInstances")
            if self._in_scope(db.get("DBInstanceIdentifier", ""))
        ]
        self.stats["databases"] = len(stack["db_instances"])

        tables: List[Dict[str, Any]] = []
        for name in paginate(self.dynamodb, "list_tables", "TableNames"):
            if not self._in_scope(name):
                continue
            entry: Dict[str, Any] = {"TableName": name}
            try:
                described = self.dynamodb.describe_table(TableName=name)["Table"]
                entry["TableArn"] = described.get("TableArn")
                entry["Replicas"] = described.get("Replicas") or []
            except ClientError as exc:
                self._swallow("describe_table", name, exc)
            try:
                backups = self.dynamodb.describe_continuous_backups(TableName=name)
                entry["PointInTimeRecoveryStatus"] = (
                    backups.get("ContinuousBackupsDescription", {})
                    .get("PointInTimeRecoveryDescription", {})
                    .get("PointInTimeRecoveryStatus", "DISABLED")
                )
            except ClientError as exc:
                self._swallow("describe_continuous_backups", name, exc)
                entry.setdefault("PointInTimeRecoveryStatus", "DISABLED")
            tables.append(entry)
        stack["tables"] = tables
        self.stats["tables"] = len(tables)

    def _collect_vaults(self, stack: Dict[str, Any]) -> None:
        vaults: List[Dict[str, Any]] = []
        total_points = 0

        for client, client_region in (
            (self.backup, self.region),
            (self.backup_dr, self.dr_region),
        ):
            for vault in paginate(client, "list_backup_vaults", "BackupVaultList"):
                name = vault.get("BackupVaultName", "")
                if not self._in_scope(name):
                    continue
                entry = dict(vault)
                entry["Region"] = client_region
                try:
                    described = client.describe_backup_vault(BackupVaultName=name)
                    entry["Locked"] = bool(described.get("Locked"))
                    entry["MinRetentionDays"] = described.get("MinRetentionDays")
                    entry["MaxRetentionDays"] = described.get("MaxRetentionDays")
                    entry["NumberOfRecoveryPoints"] = described.get(
                        "NumberOfRecoveryPoints", 0
                    )
                except ClientError as exc:
                    self._swallow("describe_backup_vault", name, exc)
                    entry.setdefault("Locked", False)

                points = paginate(
                    client,
                    "list_recovery_points_by_backup_vault",
                    "RecoveryPoints",
                    BackupVaultName=name,
                )
                dates = [p.get("CreationDate") for p in points if p.get("CreationDate")]
                entry["LatestRecoveryPointTime"] = max(dates) if dates else None
                entry["NumberOfRecoveryPoints"] = entry.get(
                    "NumberOfRecoveryPoints"
                ) or len(points)
                total_points += len(points)
                vaults.append(entry)

        stack["vaults"] = vaults
        self.stats["vaults"] = len(vaults)
        self.stats["recovery_points"] = total_points

        stack["restore_jobs"] = paginate(self.backup, "list_restore_jobs", "RestoreJobs")

        plans: List[Dict[str, Any]] = []
        for summary in paginate(self.backup, "list_backup_plans", "BackupPlansList"):
            plan_id = summary.get("BackupPlanId")
            if not plan_id or not self._in_scope(summary.get("BackupPlanName", "")):
                continue
            try:
                detail = self.backup.get_backup_plan(BackupPlanId=plan_id)
                plan = dict(detail.get("BackupPlan") or {})
                plan["BackupPlanId"] = plan_id
                plans.append(plan)
            except ClientError as exc:
                self._swallow("get_backup_plan", str(plan_id), exc)
        stack["backup_plans"] = plans

    def _collect_buckets(self, stack: Dict[str, Any]) -> None:
        buckets: List[Dict[str, Any]] = []
        try:
            listed = self.s3.list_buckets().get("Buckets", [])
        except ClientError as exc:
            self._swallow("list_buckets", "account", exc)
            listed = []

        for bucket in listed:
            name = bucket.get("Name", "")
            if not self._in_scope(name):
                continue
            entry: Dict[str, Any] = {"Name": name}
            try:
                location = self.s3.get_bucket_location(Bucket=name).get(
                    "LocationConstraint"
                )
                # us-east-1 is reported as None. This has caught out every
                # generation of AWS tooling since 2006 and it will catch out
                # yours: a bucket whose region reads as empty compares unequal
                # to everything, which is exactly the wrong answer for DR-011.
                entry["Region"] = location or "us-east-1"
            except ClientError as exc:
                self._swallow("get_bucket_location", name, exc)
                entry["Region"] = self.region
            try:
                entry["Versioning"] = self.s3.get_bucket_versioning(Bucket=name).get(
                    "Status", "Disabled"
                )
            except ClientError as exc:
                self._swallow("get_bucket_versioning", name, exc)
                entry["Versioning"] = "Disabled"
            try:
                entry["Replication"] = self.s3.get_bucket_replication(Bucket=name).get(
                    "ReplicationConfiguration", {}
                )
            except ClientError as exc:
                self._swallow("get_bucket_replication", name, exc)
                entry["Replication"] = {}
            buckets.append(entry)

        stack["buckets"] = buckets
        self.stats["buckets"] = len(buckets)

    def _collect_dns(self, stack: Dict[str, Any]) -> None:
        records: List[Dict[str, Any]] = []
        try:
            zones = paginate(self.route53, "list_hosted_zones", "HostedZones")
        except ClientError as exc:
            self._swallow("list_hosted_zones", "account", exc)
            zones = []

        for zone in zones:
            zone_id = str(zone.get("Id", "")).split("/")[-1]
            try:
                sets = paginate(
                    self.route53,
                    "list_resource_record_sets",
                    "ResourceRecordSets",
                    HostedZoneId=zone_id,
                )
            except ClientError as exc:
                self._swallow("list_resource_record_sets", zone_id, exc)
                continue
            for record in sets:
                if not record.get("Failover"):
                    continue
                entry = dict(record)
                entry["ZoneId"] = zone_id
                records.append(entry)

        stack["route53_records"] = records
        stack["health_checks"] = paginate(
            self.route53, "list_health_checks", "HealthChecks"
        )
        self.stats["records"] = len(records)

    def _collect_workflows(self, stack: Dict[str, Any]) -> None:
        machines: List[Dict[str, Any]] = []
        for summary in paginate(self.sfn, "list_state_machines", "stateMachines"):
            name = summary.get("name", "")
            if not self._in_scope(name):
                continue
            arn = summary.get("stateMachineArn")
            entry: Dict[str, Any] = {
                "name": name,
                "stateMachineArn": arn,
                "Region": self.region,
                "definition": {},
                "executions": [],
            }
            try:
                described = self.sfn.describe_state_machine(stateMachineArn=arn)
                entry["definition"] = parse_policy(described.get("definition"))
            except ClientError as exc:
                self._swallow("describe_state_machine", str(name), exc)
            try:
                entry["executions"] = paginate(
                    self.sfn, "list_executions", "executions", stateMachineArn=arn
                )
            except ClientError as exc:
                self._swallow("list_executions", str(name), exc)
            machines.append(entry)

        stack["state_machines"] = machines
        self.stats["workflows"] = len(machines)

    def collect(self) -> Dict[str, Any]:
        account_id = ""
        try:
            account_id = self.sts.get_caller_identity().get("Account", "")
        except (ClientError, BotoCoreError):
            pass

        stack: Dict[str, Any] = {
            "region": self.region,
            "dr_region": self.dr_region,
            "account_id": account_id,
            # The clock, once, here. Every age-based check reads this rather
            # than datetime.now(), which is what makes DR-008's lesson
            # reproducible in a test instead of dependent on when CI ran.
            "now": datetime.now(timezone.utc),
            "rpo_minutes": self.rpo_minutes,
            "rto_minutes": self.rto_minutes,
            "min_grace_seconds": self.min_grace_seconds,
            "failover_test_max_age_days": self.failover_test_max_age_days,
            "restore_window_days": 365,
        }

        self.log("  · compute and network topology")
        self._collect_compute(stack)
        self.log("  · data tier")
        self._collect_data(stack)
        self.log(f"  · backup vaults in {self.region} and {self.dr_region}")
        self._collect_vaults(stack)
        self.log("  · buckets and replication")
        self._collect_buckets(stack)
        self.log("  · DNS failover records")
        self._collect_dns(stack)
        self.log("  · recovery workflows")
        self._collect_workflows(stack)

        self.stack = stack
        return stack

    def run(self) -> List[Finding]:
        if not self.session:
            print(
                "No AWS credentials. Every check on this day reads AWS. Try "
                "--profile bootcamp, or run `aws configure --profile bootcamp`.",
                file=sys.stderr,
            )
            sys.exit(2)

        self.log("Collecting recovery configuration...")
        stack = self.collect()

        self.log("Running checks...")
        findings: List[Finding] = []
        for _check_id, check in CHECKS:
            findings += check(stack, self.region)

        self.findings = findings
        return findings


###############################################################################
# CLI
###############################################################################


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dr_audit.py",
        description=(
            "Audit failure domains, backup, replication, DNS failover and the "
            "automated recovery path built on them — compute in one AZ, NAT "
            "gateways that make multi-AZ a fiction, backups nobody has "
            "restored, replication whose lag cannot be measured, and failover "
            "workflows nobody has ever run."
        ),
        epilog=(
            "Examples:\n"
            "  dr_audit.py --profile bootcamp --region us-east-1 --dr-region us-west-2\n"
            "  dr_audit.py --prefix cbc-day08          # only this lab's resources\n"
            "  dr_audit.py --rpo-minutes 15            # audit against a tighter claim\n"
            "  dr_audit.py --format json --quiet > dr-findings.json\n"
            "  dr_audit.py --fail-on CRITICAL          # exit 1 on any CRITICAL\n"
            "\n"
            "Three checks read RUNTIME state (DR-008, DR-010, DR-016), so their\n"
            "answer depends on when you ran this. DR-008 in particular will change\n"
            "answer on an UNCHANGED account as recovery points age past your stated\n"
            "RPO. That is correct: RTO and RPO are claims about a procedure, and a\n"
            "claim about a procedure decays from the last time somebody ran it.\n"
            "Run this on a schedule, not only at merge time.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--profile", default=None,
                        help="AWS CLI named profile. Day 01 created 'bootcamp'.")
    parser.add_argument("--region", default="us-east-1",
                        help="Primary region to audit (default: us-east-1).")
    parser.add_argument(
        "--dr-region", default="us-west-2",
        help=(
            "Disaster-recovery region. NOT optional decoration: a single-region "
            "audit of a multi-region DR posture cannot see whether the copy vault "
            "exists, holds anything, or is where you think it is (default: us-west-2)."
        ),
    )
    parser.add_argument(
        "--prefix", default=None,
        help=(
            "Only examine resources whose name starts with this prefix, e.g. "
            "cbc-day08. Omit to audit everything in the region — which is the run "
            "worth doing, and the one that finds the vault nobody has restored from."
        ),
    )
    parser.add_argument(
        "--rpo-minutes", type=int, default=60,
        help=(
            "The RPO you CLAIM, in minutes. DR-008 measures recovery point age "
            "against it (default: 60). Setting it to the number in your DR document "
            "rather than the number you hope for is the whole exercise."
        ),
    )
    parser.add_argument(
        "--rto-minutes", type=int, default=30,
        help="The RTO you CLAIM, in minutes. Carried in the stack for context (default: 30).",
    )
    parser.add_argument(
        "--min-grace-seconds", type=int, default=MIN_GRACE_SECONDS,
        help="Health check grace period below which DR-004 fires (default: 30).",
    )
    parser.add_argument(
        "--failover-test-max-age-days", type=int, default=90,
        help="Age at which a previously-successful failover test stops counting (default: 90).",
    )
    parser.add_argument("--min-severity", choices=SEVERITY_ORDER, default="INFO",
                        help="Only report findings at this severity or worse (default: INFO).")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table",
                        help="Output format (default: table).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output on stderr. Use when piping stdout.")
    parser.add_argument(
        "--fail-on", choices=SEVERITY_ORDER, default=None,
        help="Exit with code 1 if any finding is at this severity or worse. Use in CI to block a merge.",
    )
    parser.add_argument("--no-colour", "--no-color", dest="no_colour", action="store_true",
                        help="Disable ANSI colour even on a TTY.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    use_colour = sys.stdout.isatty() and not args.no_colour and args.format == "table"

    auditor = DisasterRecoveryAuditor(
        profile=args.profile,
        region=args.region,
        dr_region=args.dr_region,
        prefix=args.prefix,
        rpo_minutes=args.rpo_minutes,
        rto_minutes=args.rto_minutes,
        min_grace_seconds=args.min_grace_seconds,
        failover_test_max_age_days=args.failover_test_max_age_days,
        quiet=args.quiet,
    )

    try:
        all_findings = auditor.run()
    except NoCredentialsError:
        print(
            "No AWS credentials found. Try --profile bootcamp, or run "
            "`aws configure --profile bootcamp`.",
            file=sys.stderr,
        )
        return 2
    except ClientError as exc:
        print(f"AWS API error: {exc}", file=sys.stderr)
        return 2

    # The score always reflects EVERY finding, regardless of --min-severity.
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
                    f"Failing: at least one finding at severity {args.fail_on} "
                    f"or worse.",
                    file=sys.stderr,
                )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
