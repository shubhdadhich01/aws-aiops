#!/usr/bin/env python3
"""
dr_audit_challenge.py — Day 08 auditor, for you to finish.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

GENERATED FROM dr_audit.py. Identical imports, identical Finding, identical
helpers, identical renderers, identical collector, identical CLI. Sixteen check
bodies have been removed and their DOCSTRINGS LEFT IN PLACE, because the
docstring is the specification. Read it before you write anything.

    cd lab/python
    DR_AUDIT_MODULE=dr_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests -v

47 tests. They need no AWS credentials, because every check is a pure function
over a plain dict. Aim for all 47 green; get there one CHECKPOINT at a time.

Roughly three hours if you work through it in order. The per-check estimates
below add up to 189 minutes and the two long ones (DR-002 and DR-011) are long
for different reasons — one is a graph, the other is two half-checks that
resolve regions differently.

-----------------------------------------------------------------------------
WHICH CHECKS ARE NOT INDEPENDENT
-----------------------------------------------------------------------------
Six relationships. Writing them down is what stops them reading as bugs when a
test fails for a reason that is not in the check you just wrote.

  DR-001, DR-003 and DR-004 ALL FIRE ON THE SAME SCALING GROUP, and they are
  not duplicates. WHERE it runs; WHETHER it notices an application failure;
  WHETHER a replacement can start at all. Three findings, one resource. If your
  DR-001 returns one finding and your test expects one, and the whole-stack
  total is still wrong, look at the other two before you look at DR-001.

  DR-004's SEVERITY DEPENDS ON DR-003's SUBJECT. HIGH when the group honours
  ELB health checks, MEDIUM when it does not — because a short grace period is
  nearly harmless until somebody correctly fixes DR-003, at which point it is a
  boot loop. Your DR-004 has to read HealthCheckType even though DR-003 owns it.

  DR-008 AND DR-010 LOOK LIKE THE SAME CHECK. They are not. DR-008 asks whether
  there is a RECENT ENOUGH backup; DR-010 asks whether anybody has ever proved
  a backup can be turned back into a system. A vault full of fresh recovery
  points that has never been restored from scores 0 and 25 respectively, and
  that is the normal state of most organisations. There is a test for exactly
  this, in both directions.

  DR-008 AND DR-009 BOTH ITERATE THE SAME VAULT LIST and both report per vault,
  including the DR-region one. Two vaults means two findings from each. If your
  totals are 2 low, you deduplicated.

  DR-011 IS SILENT BY DESIGN and must stay silent against every fixture state.
  If it fires, your region resolution is wrong — almost certainly because you
  used _region_of_arn() on an S3 bucket ARN, which has no region field.

  DR-015 AND DR-016 BOTH FIRE ON THE NAIVE STATE MACHINE, for genuinely
  different reasons, and neither remediates the other: adding an approval gate
  does not test it, and testing it does not add a gate. They also SHARE A
  PRECONDITION — both skip machines with no irreversible action — so a bug in
  workflow_irreversible_actions() silences both at once.

-----------------------------------------------------------------------------
THE CLOCK
-----------------------------------------------------------------------------
Three checks are age-based: DR-008, DR-010 and DR-016.

NEVER CALL datetime.now(). Call _now(stack), which reads stack["now"].

The clock is injected for a reason that is not merely testability. DR-008
exists to demonstrate that an UNCHANGED ACCOUNT'S AUDIT RESULT CHANGES WITH THE
CLOCK ALONE — the contract's STATE C is STATE B sixty-one minutes later, with
nothing deployed, and two findings appear. There is a test asserting exactly
that, and it can only exist because the clock is a value you can set.

Two more clock details that will cost you a test each:

  UNITS. DR-008 compares against stack["rpo_minutes"] and must use
  _age_minutes(). _age_days() exists and is the wrong helper there: a 23-hour-
  old recovery point would round to 1 and look fine. DR-016 is the reverse —
  a DR test's freshness is a quarterly question, so use _age_days().

  ABSENT IS NOT ZERO. _age_minutes(None, now) returns None, and None means "we
  could not date this", which is a different fact from "this is old". DR-008
  treats an absent recovery point time as a finding (there is no recovery
  point); DR-016 treats an undateable execution as a skip (it happened, we just
  cannot say when). Getting those the same way round is a test failure that
  looks like a logic error and is a units error.

-----------------------------------------------------------------------------
SEVERITY, AND WHAT GOES IN A FINDING
-----------------------------------------------------------------------------
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Day 08 uses no LOW and no INFO, deliberately: on this day every
fault either costs you data or costs you time during an outage.

Each Finding needs nine fields and the TODOs below name all nine, including
what to do when the source value is ABSENT. Two that people get wrong:

  detail       Include the REAL VALUES you observed. "The grace period is too
               short" is a complaint; "HealthCheckGracePeriod=0, below the 30s
               floor" is a finding.
  region       The region of the RESOURCE, not the region you were invoked
               with. DR-008, DR-009 and DR-016 all report on things that may
               live in the DR region.

-----------------------------------------------------------------------------
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
    # =======================================================================
    # TODO 1 of 16 — DR-001   (about 10 minutes)
    # =======================================================================
    #
    # READS: stack["asgs"], stack["subnets"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-001"
    #     severity="CRITICAL"
    #     resource_type=RT_ASG
    #     resource_id=the AutoScalingGroupName (ABSENT: use "unknown")
    #     title=one line, imperative
    #     detail=name it names the AZ count, the AZ names and the subnet count
    #     remediation=the update-auto-scaling-group command, plus the capacity point
    #     evidence={"AutoScalingGroupName", "subnets", "availability_zones", "DesiredCapacity"}
    #     region=the region argument
    #
    # HINTS:
    #   - VPCZoneIdentifier is a COMMA-SEPARATED STRING, not a list.
    #   - Resolve AZs through _azs_of_subnets() rather than trusting the group's
    #   -   own AvailabilityZones field — that field is settable, and a group
    #   -   listing three zones whose subnets are all in one is a real shape.
    #   - Fall back to AvailabilityZones only when no subnet resolves.
    #   - MIN_AZS is the constant. Do not write 2.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 2 of 16 — DR-002   (about 20 minutes)
    # =======================================================================
    #
    # READS: stack["route_tables"], stack["nat_gateways"], stack["subnets"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-002"
    #     severity="HIGH"
    #     resource_type=RT_VPC
    #     resource_id=the VpcId (ABSENT: "unknown")
    #     detail=name the subnet AZs, the NAT AZs and the route tables involved
    #     evidence={"VpcId", "subnet_azs", "nat_gateway_azs", "nat_gateway_ids", "route_tables"}
    #
    # HINTS:
    #   - THE HARDEST CHECK IN THE FILE, and it is a graph problem rather than
    #   -   a field lookup. Four hops: route table -> its NAT route -> that
    #   -   gateway's subnet -> that subnet's AZ.
    #   - Report ONCE PER VPC. Three findings for one missing gateway trains
    #   -   people to skim.
    #   - Fire only when the private subnets span >= MIN_AZS AND the serving NAT
    #   -   gateways occupy < MIN_AZS. Both conditions.
    #   - Skip route tables with no NatGatewayId route — those are public.
    #   - Skip NAT gateways whose State is not available or pending.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 3 of 16 — DR-003   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["asgs"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-003"
    #     severity="HIGH"
    #     resource_type=RT_ASG
    #     resource_id=the AutoScalingGroupName
    #     detail=BRANCHES on whether a load balancer is attached; say the harder
    #       thing when it is not
    #     evidence={"AutoScalingGroupName", "HealthCheckType", "TargetGroupARNs", "LoadBalancerNames"}
    #
    # HINTS:
    #   - Compare UPPERCASED. "elb" and "ELB" are the same answer.
    #   - Fire on ANY non-ELB group, including groups with no load balancer at
    #   -   all — that case has no application health signal whatsoever, which is
    #   -   strictly worse rather than exempt.
    #   - Default when the field is absent is EC2, which is the whole problem.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 4 of 16 — DR-004   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["asgs"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-004"
    #     severity="HIGH" when the group honours ELB health checks, "MEDIUM" when it does not
    #     resource_type=RT_ASG
    #     evidence={"AutoScalingGroupName", "HealthCheckGracePeriod", "HealthCheckType", "floor_seconds"}
    #
    # HINTS:
    #   - The floor is stack['min_grace_seconds'], defaulting to MIN_GRACE_SECONDS.
    #   -   Read it from the stack — the CLI exposes --min-grace-seconds.
    #   - ABSENT GracePeriod is NOT a finding here: None means the API did not
    #   -   report it, which is different from zero.
    #   - The severity branch is the interesting part. With EC2 health checks a
    #   -   short grace period is nearly harmless; it becomes a boot loop the
    #   -   moment somebody correctly fixes DR-003. Say that in the detail.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

    return findings


# ############################################################################
# CHECKPOINT A
#
# FAILURE DOMAINS. Run: python3 -m unittest discover -s tests -k dr_00 and
# expect DR-001 through DR-004 green, fire and silent.
# ############################################################################


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
    # =======================================================================
    # TODO 5 of 16 — DR-005   (about 6 minutes)
    # =======================================================================
    #
    # READS: stack["db_instances"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-005"
    #     severity="CRITICAL"
    #     resource_type=RT_RDS
    #     resource_id=DBInstanceIdentifier
    #     evidence={"DBInstanceIdentifier", "MultiAZ", "AvailabilityZone", "Engine"}
    #
    # HINTS:
    #   - One field. The value is in the detail and the remediation: say that
    #   -   the standby serves no traffic and is not a read replica, because that
    #   -   is the misconception this check exists to kill.
    #   - Silent by SITUATION in the shipped stack — create_rds defaults to false.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 6 of 16 — DR-006   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["db_instances"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-006"
    #     severity="HIGH"
    #     resource_type=RT_RDS
    #     title=BRANCHES: disabled entirely versus one day
    #     evidence={"DBInstanceIdentifier", "BackupRetentionPeriod", "PreferredBackupWindow"}
    #
    # HINTS:
    #   - MIN_RDS_RETENTION_DAYS is the constant.
    #   - ABSENT retention is not a finding: None is unknown, not zero.
    #   - Zero and one are different faults sharing a severity. Zero disables
    #   -   point-in-time restore entirely; one cannot survive a weekend.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 7 of 16 — DR-007   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["tables"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-007"
    #     severity="MEDIUM"
    #     resource_type=RT_TABLE
    #     resource_id=TableName
    #     detail=BRANCHES on whether the table has replicas, and says why a
    #       replica does not help against corruption
    #     evidence={"TableName", "PointInTimeRecoveryStatus", "Replicas"}
    #
    # HINTS:
    #   - PointInTimeRecoveryStatus is ENABLED or DISABLED, uppercase.
    #   - ABSENT means DISABLED — a table the collector could not describe is
    #   -   not a table you have proven has PITR.
    #   - Replicas is a list of dicts with RegionName.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

    return findings


# ############################################################################
# CHECKPOINT B
#
# DATA TIER. DR-005 through DR-007 green. Note that two of the three are
# silent against the shipped stack; the tests build the fault for them.
# ############################################################################


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
    # =======================================================================
    # TODO 8 of 16 — DR-008   (about 15 minutes)
    # =======================================================================
    #
    # READS: stack["vaults"], stack["now"], stack["rpo_minutes"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-008"
    #     severity="HIGH"
    #     resource_type=RT_VAULT
    #     resource_id=BackupVaultName
    #     title=BRANCHES: no recovery point at all versus one that is too old
    #     evidence={"BackupVaultName", "Region", "LatestRecoveryPointTime",
    #               "NumberOfRecoveryPoints", "age_minutes", "rpo_minutes"}
    #     region=THE VAULT'S OWN REGION, not the region argument
    #
    # HINTS:
    #   - THE CHECK THAT MAKES THE DAY'S POINT. Get the clock right: use
    #   -   _now(stack) and _age_minutes(), never datetime.now().
    #   - An ABSENT LatestRecoveryPointTime fires. So does one older than
    #   -   stack['rpo_minutes'].
    #   - MINUTES, not days. _age_days() exists and is the wrong helper here —
    #   -   a 23-hour-old recovery point must not round to 1 and look fine.
    #   - The region field must be the VAULT's region. A finding that lies about
    #   -   where a thing lives is worse than no finding.
    #   - _humanise_minutes() renders the age for the detail line.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 9 of 16 — DR-009   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["vaults"], stack["region"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-009"
    #     severity="MEDIUM"
    #     resource_type=RT_VAULT
    #     detail=BRANCHES on whether this is the DR-region vault
    #     evidence={"BackupVaultName", "Region", "Locked", "is_dr_region"}
    #     region=the vault's own region
    #
    # HINTS:
    #   - Report PER VAULT. Do not deduplicate up to the plan: a locked primary
    #   -   beside an unlocked DR copy vault is exactly backwards and both
    #   -   deserve saying.
    #   - is_dr_region is vault region != stack['region'].
    #   - The remediation must warn about --changeable-for-days: its PRESENCE is
    #   -   what selects the irreversible compliance mode.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 10 of 16 — DR-010   (about 10 minutes)
    # =======================================================================
    #
    # READS: stack["restore_jobs"], stack["account_id"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-010"
    #     severity="CRITICAL"
    #     resource_type=RT_ACCOUNT
    #     resource_id=stack["account_id"] (ABSENT: "this account")
    #     detail=mentions the count of ATTEMPTED jobs as well as completed ones
    #     evidence={"restore_jobs_seen", "completed_restore_jobs", "window_days", "checked_at"}
    #
    # HINTS:
    #   - RETURNS AT MOST ONE FINDING, for the whole account. Not per vault.
    #   -   It is a statement about the ORGANISATION, and attaching it to a
    #   -   resource id invites somebody to close it by deleting the resource.
    #   - Only Status == "COMPLETED" counts. Jobs that were attempted and failed
    #   -   are a STRONGER signal, not a weaker one — say so in the detail.
    #   - Early-return [] the moment a completed job exists.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

    return findings


# ############################################################################
# CHECKPOINT C
#
# BACKUP AND RESTORE. DR-008 through DR-010 green. This is the half of the
# file the day is actually about.
# ############################################################################


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
    # =======================================================================
    # TODO 11 of 16 — DR-011   (about 20 minutes)
    # =======================================================================
    #
    # READS: stack["buckets"], stack["backup_plans"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-011"
    #     severity="HIGH"
    #     resource_type=RT_BUCKET for the S3 half, "AWS::Backup::BackupPlan" for the other
    #     evidence (S3)={"source_bucket", "destination_bucket", "region", "rule_id"}
    #     evidence (plan)={"BackupPlanName", "RuleName", "TargetBackupVaultArn",
    #                      "DestinationBackupVaultArn", "region"}
    #
    # HINTS:
    #   - SILENT BY DESIGN against this stack, and it must stay that way — the
    #   -   tests assert it across every state. Build the fault yourself to
    #   -   check your logic.
    #   - TWO HALVES with DIFFERENT REGION RESOLUTION, and that is the lesson:
    #   -   a backup vault ARN carries its region in field four, and an S3 BUCKET
    #   -   ARN CARRIES NO REGION AT ALL. Use _region_of_arn() for the vault and
    #   -   the collected bucket inventory for the bucket.
    #   - A destination bucket you have never seen is UNKNOWN, not safe. Skip it
    #   -   rather than assuming.
    #   - Destination bucket ARNs look like arn:aws:s3:::name — split on ":::".
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 12 of 16 — DR-012   (about 8 minutes)
    # =======================================================================
    #
    # READS: stack["buckets"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-012"
    #     severity="MEDIUM"
    #     resource_type=RT_BUCKET
    #     title=BRANCHES: suspended versus never enabled
    #     evidence={"Name", "Region", "Versioning", "name_suggests_dr"}
    #     region=the bucket's own region
    #
    # HINTS:
    #   - Three states: "Enabled", "Suspended", and absent. Absent is "Disabled".
    #   - Suspended is the more alarming: it was on, somebody turned it off, and
    #   -   the versions from before are still there and still billing.
    #   - name_suggests_dr looks for dr / backup / archive / replica in the name,
    #   -   lowercased. It is a heuristic and belongs in evidence, not in the
    #   -   fire condition.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 13 of 16 — DR-013   (about 15 minutes)
    # =======================================================================
    #
    # READS: stack["buckets"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-013"
    #     severity="HIGH"
    #     resource_type=RT_BUCKET
    #     title=BRANCHES: rule disabled versus lag unmeasurable
    #     evidence={"Name", "rule_id", "Status", "Metrics", "ReplicationTime", "Destination"}
    #
    # HINTS:
    #   - FIRES ON A CORRECTLY-CONFIGURED RULE. The rule works, objects
    #   -   replicate, and what is missing is the METRIC. That is the point.
    #   - ONE FINDING PER RULE, not per bucket — a bucket can have several.
    #   - Skip buckets with no Replication rules entirely: no rule is DR-012's
    #   -   territory or nobody's, not this check's.
    #   - Measurable means Destination.Metrics.Status == Enabled OR
    #   -   Destination.ReplicationTime.Status == Enabled.
    #   - ABSENT Status defaults to "Enabled" — an absent field is not a disabled
    #   -   rule.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

    return findings


# ############################################################################
# CHECKPOINT D
#
# REPLICATION. DR-011 through DR-013 green. DR-011 must still be silent
# against every fixture state; if it fires, your region resolution is wrong.
# ############################################################################


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
    # =======================================================================
    # TODO 14 of 16 — DR-014   (about 10 minutes)
    # =======================================================================
    #
    # READS: stack["route53_records"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-014"
    #     severity="HIGH"
    #     resource_type=RT_RECORD
    #     resource_id=f"{name} [{set_id}]" when there is a SetIdentifier, else the name
    #     evidence={"Name", "SetIdentifier", "Failover", "TTL", "Type"}
    #
    # HINTS:
    #   - Only PRIMARY and SECONDARY records. Anything else is not a failover
    #   -   record set.
    #   - TWO exemptions, and missing the second one flags correct configurations:
    #   -   a HealthCheckId, OR an AliasTarget with EvaluateTargetHealth true.
    #   - Route 53 treats a PRIMARY record with no health signal as permanently
    #   -   healthy — it never fails over. Say that in the detail; it is the
    #   -   whole reason the severity is HIGH.
    #   - Silent by SITUATION in the shipped stack: hosted_zone_id defaults to
    #   -   empty, so there are no failover records at all.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 15 of 16 — DR-015   (about 20 minutes)
    # =======================================================================
    #
    # READS: stack["state_machines"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-015"
    #     severity="CRITICAL"
    #     resource_type=RT_SFN
    #     resource_id=the state machine name
    #     detail=BRANCHES three ways: no gates at all, gates but a hardcoded
    #       live failover, and both
    #     evidence={"name", "irreversible_actions", "gates_found",
    #               "dry_run_hardcoded_false", "state_count"}
    #
    # HINTS:
    #   - Use the three helpers you were given: workflow_irreversible_actions(),
    #   -   workflow_gates(), workflow_forces_live(). Do not reimplement them.
    #   - SKIP machines with no irreversible action. A workflow that only reads
    #   -   does not need a brake, and flagging it teaches people to ignore the
    #   -   check.
    #   - ANY ONE gate is enough to stay silent. The check is looking for
    #   -   workflows with NO brake, not for workflows that chose a different
    #   -   brake from this repo's.
    #   - The exception: gates present AND dry_run hardcoded false still fires,
    #   -   because a workflow that cannot be rehearsed has no rehearsal mode
    #   -   whatever else it has.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

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
    # =======================================================================
    # TODO 16 of 16 — DR-016   (about 15 minutes)
    # =======================================================================
    #
    # READS: stack["state_machines"], stack["now"], stack["failover_test_max_age_days"]
    #
    # RETURN a list of Finding(...) with EVERY field below:
    #     check_id="DR-016"
    #     severity="CRITICAL"
    #     resource_type=RT_SFN
    #     title=BRANCHES: never executed versus not recently
    #     evidence={"name", "Region", "executions_seen", "successful_executions",
    #               "days_since_last_success", "max_age_days"}
    #     region=the state machine's own region
    #
    # HINTS:
    #   - THE THESIS OF THE DAY. Keep it simple and keep it honest.
    #   - Only status == "SUCCEEDED" counts, uppercased.
    #   - A DRY RUN COUNTS. That is generous on purpose — a dry run proves the
    #   -   paths, the IAM, the inputs and the notification route, which is most
    #   -   of what goes wrong. Say in the docstring that this is a floor.
    #   - Use _age_days() here, not _age_minutes(): a DR test's freshness is a
    #   -   quarterly question.
    #   - An execution with no parseable stopDate is not a failure — skip it
    #   -   rather than reporting a stale test you cannot date.
    #   - SKIP machines with no irreversible action, same as DR-015.
    #
    # The docstring above is the specification. If your finding does not
    # say what the docstring says the fault is, the finding is wrong even
    # when the test passes.
    # =======================================================================

    findings: List[Finding] = []

    return findings


# ############################################################################
# CHECKPOINT E
#
# THE RECOVERY PATH. All 47 tests green: python3 -m unittest discover -s
# tests -v
# ############################################################################


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
        prog="dr_audit_challenge.py",
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
