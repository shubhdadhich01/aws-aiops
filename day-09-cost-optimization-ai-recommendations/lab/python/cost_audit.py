#!/usr/bin/env python3
"""
cost_audit.py — Day 09 cost optimisation and cost anomaly auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits the shape of an AWS account's cost posture: the guardrails that catch
overspend before the finance team does (Budgets, Cost Anomaly Detection, cost
allocation tags), the waste that accumulates when nobody looks (unattached
EBS, unassociated EIPs, old snapshots, stopped instances), the shape of
resources that has been superseded (previous-generation EC2, gp2 volumes,
Classic Load Balancers), the free architectural fixes that stop bills before
they start (VPC gateway endpoints, log group retention, S3 lifecycle), and
the one thing that makes the whole day's argument concrete: cost anomalies
that fired, produced emails, and were never read.

The bias this tool has, stated up front: it is more interested in
UNREAD ALERTS than in expensive resources. A team with a $10,000/month EC2
bill knows it has a $10,000/month EC2 bill. A team with a working Cost
Anomaly Detection monitor, a confirmed SNS subscription, and 47 open
anomalies with no feedback from the last 90 days believes it has cost
monitoring, and what it actually has is a machine speaking to itself.

An unread anomaly is a bill that keeps growing. Cost is a lagging measure
of a decision nobody re-examined.

What it checks
--------------
    COST-001  No AWS Budget defined                account has no ceiling      HIGH
    COST-002  Budget without notification          decorative guardrail        HIGH
    COST-003  No Cost Anomaly Detection monitor    no ML watching spend        HIGH
    COST-004  Tag coverage below threshold         bill without a stakeholder  MEDIUM
    COST-005  Unattached EBS volume                storage billing for nothing HIGH
    COST-006  Unassociated Elastic IP              $3.60/month, EACH, forever  MEDIUM
    COST-007  Old EBS snapshot                     backup that outlived its use MEDIUM
    COST-008  EC2 stopped for extended period      EBS bills, instance doesn't MEDIUM
    COST-009  Previous-generation instance family  strict successor exists     MEDIUM
    COST-010  gp2 EBS volume                       gp3 is cheaper AND faster   LOW
    COST-011  Classic Load Balancer                deprecated in favour of ALB MEDIUM
    COST-012  NAT gateway, no S3/DDB endpoints     $0.045/GB avoidable         MEDIUM
    COST-013  CloudWatch log group unbounded       $0.03/GB/month forever      MEDIUM
    COST-014  S3 bucket without lifecycle          STANDARD tier, forever      MEDIUM
    COST-015  Long-running EC2, no Savings Plan    ~30% left on the table      MEDIUM
    COST-016  Cost anomaly untriaged               the day's thesis            CRITICAL

Three things carried over, deliberately
---------------------------------------
**One signature.** Every check takes `(stack: Dict, region: str)` and returns
`List[Finding]`. Several need cross-resource context to be correct — COST-004
needs every tagged resource in the account to compute a coverage percentage,
COST-012 needs to relate route tables to NAT gateways to VPC endpoints, and
COST-015 needs to reconcile instance uptime with account-wide Savings Plan
coverage — and a one-resource signature makes that impossible without a
global.

**Time is injected, not read.** `stack["now"]` is set once by `collect()`.
On this day it carries three separate age-based checks (COST-007, COST-008,
COST-015) and the day's central check (COST-016), all of which produce
"unchanged account, different day, different result" outputs. A check that
calls `datetime.now()` is a check whose tests depend on when CI ran, and on
this day it is also a check that cannot demonstrate the day's thesis.

**Global-first, region-second.** Unlike Day 08 which added a second region,
Day 09 recognises that the interesting APIs on this day (Budgets, Cost
Explorer, Cost Anomaly Detection, Savings Plans) are all hosted at us-east-1
regardless of where the resources they describe live. The auditor pins
us-east-1 for those clients and takes --region for the regional resources
(EC2, EBS, log groups, ELB). A common failure mode is a Cost Explorer client
instantiated in the wrong region; it fails immediately with
UnrecognizedClientException, which is expensive debugging.

=============================================================================
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
=============================================================================
"""

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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
# Identical to Days 03 through 08 on purpose. By Day 10 you will have seven of
# these tools and one mental model for reading their output.
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

    check_id     Stable identifier (COST-001 ...). Never renumber these -
                 people write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken. resource_id is the thing you
                 would type into the console or the CLI to look at it.
    title        One line, imperative, readable in a table.
    detail       What was actually observed. Include the real values.
    remediation  What to do about it, concretely.
    evidence     Raw values so the finding is auditable without re-querying.
    region       The region of the RESOURCE. Cost checks against global APIs
                 (Budgets, Cost Explorer) report region "" (account-scope),
                 which is deliberate and worth reading correctly.
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
# Same shape as Day 08. Cost APIs paginate, ec2 paginates, s3 does not.
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
                f"  ! Access denied calling {operation}. Skipping the checks "
                f"that depend on it. Attach SecurityAudit or ReadOnlyAccess "
                f"to fix, plus AWSBillingReadOnlyAccess for the cost APIs.",
                file=sys.stderr,
            )
            return []
        raise
    return items


def as_list(value: Any) -> List[Any]:
    """AWS APIs use a string where a list of one would do."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


###############################################################################
# Constants the checks reason about
###############################################################################

# Instance families the auditor treats as previous-generation. Passed in via
# stack["previous_gen_families"] so the default can be overridden by the
# Terraform variable.
DEFAULT_PREVIOUS_GEN_FAMILIES: Set[str] = {
    "t2", "m3", "m4", "m5", "c3", "c4", "c5", "r3", "r4",
}

# Volume types the auditor treats as superseded. gp2 has a strict successor
# in gp3 that is cheaper AND faster. No other type on this list today.
SUPERSEDED_VOLUME_TYPES: Set[str] = {"gp2"}

# Critical cost allocation tags. Coverage of ALL of these across resources is
# what COST-004 measures. Owner because the bill needs a stakeholder; Project
# because the bill needs a rollup.
CRITICAL_TAGS: Set[str] = {"Owner", "Project"}

# Resource types, spelled once. A typo here produces a finding that is correct
# and unsearchable.
RT_ACCOUNT = "AWS::Account"
RT_BUDGET = "AWS::Budgets::Budget"
RT_ANOMALY_MONITOR = "AWS::CE::AnomalyMonitor"
RT_ANOMALY = "AWS::CE::Anomaly"
RT_EBS_VOLUME = "AWS::EC2::Volume"
RT_EBS_SNAPSHOT = "AWS::EC2::Snapshot"
RT_EIP = "AWS::EC2::EIP"
RT_INSTANCE = "AWS::EC2::Instance"
RT_VPC = "AWS::EC2::VPC"
RT_LOG_GROUP = "AWS::Logs::LogGroup"
RT_ELB_CLASSIC = "AWS::ElasticLoadBalancing::LoadBalancer"
RT_BUCKET = "AWS::S3::Bucket"


###############################################################################
# Shared derivations
###############################################################################


def _now(stack: Dict[str, Any]) -> datetime:
    """The clock, injected rather than read.

    On Day 09 four checks are age-based and one of them (COST-016) is the
    day's central check. That lesson - unchanged account, different day,
    different result - is only demonstrable in a test if the clock is a
    value.
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


def _tags_to_dict(tags: Any) -> Dict[str, str]:
    """AWS returns tags as [{Key:..., Value:...}] EVERYWHERE except S3 where
    they come back the same way but from a different call, and DynamoDB where
    they come back as {TagKey:...}.  Normalise to a dict once."""
    if isinstance(tags, dict):
        return {str(k): str(v) for k, v in tags.items()}
    out: Dict[str, str] = {}
    for tag in as_list(tags):
        if not isinstance(tag, dict):
            continue
        key = tag.get("Key") or tag.get("TagKey") or tag.get("key")
        value = tag.get("Value") or tag.get("TagValue") or tag.get("value")
        if key is None:
            continue
        out[str(key)] = "" if value is None else str(value)
    return out


def _instance_family(instance_type: Any) -> str:
    """Extract the family prefix from an instance type string.

    m5.xlarge -> m5. Anything without a dot is left as-is; anything with a
    hyphenated family (m5n, m6gd) keeps the whole part before the dot.
    """
    text = str(instance_type or "")
    if "." in text:
        return text.split(".", 1)[0]
    return text


def _resource_has_all_tags(tag_dict: Dict[str, str], required: Set[str]) -> bool:
    """True if every required key is present AND has a non-empty value."""
    for key in required:
        value = tag_dict.get(key)
        if not value or not value.strip():
            return False
    return True


###############################################################################
# CHECKS
#
# Each check takes (stack, region) and returns List[Finding]. The stack is a
# normalised dict produced by CostAuditor.collect(); the region argument is
# the value used to invoke the auditor. Findings carry the region of the
# RESOURCE where that differs from the invocation region (Budgets and
# Cost Explorer are account-global, so they carry "").
###############################################################################


def check_no_budget(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-001 - the account has no AWS Budget at all.

    First-line cost governance. AWS gives every account two Zero Spend
    budgets and two additional budgets in the free tier, so the reason an
    account has no budget is not cost - it is that nobody configured one,
    and the failure mode is that nothing tells the account holder when the
    monthly bill has doubled until the credit card statement arrives.

    A budget is not the finish line. It is the trigger that starts the
    conversation. An account with a budget alarm at 80% and 100% of a
    number somebody actually thought about is a governance posture; an
    account with no budget is a Zoom link waiting to happen.
    """
    findings: List[Finding] = []
    budgets = stack.get("budgets") or []
    account_id = stack.get("account_id", "unknown")

    if budgets:
        return findings

    findings.append(
        Finding(
            check_id="COST-001",
            severity="HIGH",
            resource_type=RT_ACCOUNT,
            resource_id=f"account/{account_id}",
            title="Account has no AWS Budget defined",
            detail=(
                f"Account {account_id} has zero AWS Budgets configured. "
                f"AWS Budgets is free at this scale (2 cost/usage budgets "
                f"and 2 Zero Spend budgets per account), and its absence is "
                f"the first-line cost governance defect: no ceiling means "
                f"no alarm, and no alarm means the finance team surfaces "
                f"the incident from the monthly close rather than from the "
                f"cloud team."
            ),
            remediation=(
                f"Create a monthly cost budget with actual-cost thresholds "
                f"at 80% and 100%, plus a forecasted-cost threshold at "
                f"100%. Wire notifications to an email address that "
                f"somebody reads and to an SNS topic subscribed by the "
                f"team channel. In Terraform: `aws_budgets_budget` with a "
                f"`dynamic \"notification\"` block over a list. See this "
                f"lab's variables.tf for a worked example - the validation "
                f"on budget_notifications requiring a non-empty list is "
                f"what makes COST-002 silent by design against this stack."
            ),
            evidence={
                "budgets_seen": len(budgets),
                "account_id": account_id,
            },
            region="",
        )
    )
    return findings


def check_budget_no_notification(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-002 - a budget exists but carries no notification thresholds.

    SILENT BY DESIGN against this stack; the budget_notifications variable's
    validation refuses an empty list. Fires readily against budgets created
    via the console click-through wizard or by a Terraform module that made
    notifications optional.
    """
    findings: List[Finding] = []
    for budget in stack.get("budgets") or []:
        name = budget.get("BudgetName", "unknown")
        notifications = budget.get("Notifications") or []

        # A notification without any subscribers is the same fault as no
        # notification - the threshold fires and there is nowhere for the
        # message to land. Count only notifications that have at least one
        # subscriber.
        actionable = [
            n for n in notifications
            if (n.get("Subscribers") or [])
        ]

        if actionable:
            continue

        findings.append(
            Finding(
                check_id="COST-002",
                severity="HIGH",
                resource_type=RT_BUDGET,
                resource_id=str(name),
                title="Budget has no notification threshold or subscriber",
                detail=(
                    f"Budget '{name}' exists but has "
                    f"{len(notifications)} notification(s) with actionable "
                    f"subscribers. A budget without a notification is a "
                    f"decorative object: the ceiling is set, the threshold "
                    f"is crossed, and nobody is told. This is a common "
                    f"outcome when a budget was created for a one-off CSV "
                    f"report and never revisited."
                ),
                remediation=(
                    f"Add at least one notification with an email or SNS "
                    f"subscriber. Two-tier thresholds (80% actual, 100% "
                    f"actual) are a reasonable minimum; add a 100% "
                    f"forecasted threshold to be warned before the ceiling "
                    f"is crossed rather than at the moment it is. AWS CLI: "
                    f"`aws budgets create-notification --budget-name {name} "
                    f"--notification <json> --subscribers <json>`."
                ),
                evidence={
                    "BudgetName": name,
                    "notification_count": len(notifications),
                    "actionable_count": len(actionable),
                },
                region="",
            )
        )
    return findings


def check_no_anomaly_monitor(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-003 - the account has no Cost Anomaly Detection monitor.

    Cost Anomaly Detection is entirely free. It uses ML on the AWS side to
    learn the account's spend pattern and flag departures. The number of
    accounts that do not have it enabled is one of the quietest cost
    governance facts in the industry - not because people looked at it and
    declined; because the console has 400 links and this one is not on any
    of them by default.
    """
    findings: List[Finding] = []
    monitors = stack.get("cost_anomaly_monitors") or []
    account_id = stack.get("account_id", "unknown")

    if monitors:
        return findings

    findings.append(
        Finding(
            check_id="COST-003",
            severity="HIGH",
            resource_type=RT_ACCOUNT,
            resource_id=f"account/{account_id}",
            title="Account has no Cost Anomaly Detection monitor",
            detail=(
                f"Account {account_id} has zero Cost Anomaly Detection "
                f"monitors. Cost Anomaly Detection is entirely free and its "
                f"absence means there is no ML watching the account's spend "
                f"pattern. The failure mode is quiet: an unexpected spike "
                f"in NAT gateway processing, or a Lambda misconfigured to "
                f"invoke itself, is billing at $30/day for a week before "
                f"the monthly review looks at the dashboard nobody had a "
                f"reason to open."
            ),
            remediation=(
                f"Create at least one Cost Anomaly Detection monitor, "
                f"typically DIMENSIONAL on SERVICE for account-wide "
                f"coverage. Wire a subscription to an SNS topic (which "
                f"forwards to email) with a threshold expression set to "
                f"an ABSOLUTE dollar amount you want to know about (see "
                f"cost_anomaly_threshold_usd in this stack's variables.tf). "
                f"NOTE: the monitor needs ~10 days of baseline before "
                f"producing its first anomaly - enable it now, not on the "
                f"day of the incident."
            ),
            evidence={
                "monitors_seen": len(monitors),
                "account_id": account_id,
            },
            region="",
        )
    )
    return findings


def check_tag_coverage(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-004 - the account's cost allocation tag coverage is below the
    configured threshold.

    ACCOUNT-LEVEL FINDING rather than per-resource, deliberately. A finding
    of "40 resources are missing Owner tags" is a finding nobody knows how
    to remediate; a finding of "Owner+Project coverage is at 61% (target
    90%)" is a governance conversation with a metric attached.

    Counts EC2 instances, EBS volumes, S3 buckets and CloudWatch log groups.
    Not exhaustive - SNS topics, SQS queues, Lambda functions, RDS instances
    all have tags too - but representative of the kinds of resource whose
    cost accumulates. Extend it in production.
    """
    findings: List[Finding] = []
    threshold = float(stack.get("tag_coverage_threshold_percent") or 90)

    resources: List[Tuple[str, str, Dict[str, str]]] = []

    for instance in stack.get("instances") or []:
        resources.append((
            "EC2 instance",
            str(instance.get("InstanceId")),
            _tags_to_dict(instance.get("Tags")),
        ))

    for volume in stack.get("volumes") or []:
        resources.append((
            "EBS volume",
            str(volume.get("VolumeId")),
            _tags_to_dict(volume.get("Tags")),
        ))

    for bucket in stack.get("buckets") or []:
        resources.append((
            "S3 bucket",
            str(bucket.get("Name")),
            _tags_to_dict(bucket.get("Tags")),
        ))

    for log_group in stack.get("log_groups") or []:
        resources.append((
            "CloudWatch log group",
            str(log_group.get("logGroupName")),
            _tags_to_dict(log_group.get("Tags")),
        ))

    total = len(resources)
    if total == 0:
        return findings

    covered = sum(
        1 for _, _, tags in resources
        if _resource_has_all_tags(tags, CRITICAL_TAGS)
    )
    coverage = (covered / total) * 100.0

    if coverage >= threshold:
        return findings

    # Sample a few uncovered resources for the evidence, so the finding
    # points at something concrete rather than at a percentage.
    uncovered_sample = [
        f"{kind} {res_id}"
        for kind, res_id, tags in resources
        if not _resource_has_all_tags(tags, CRITICAL_TAGS)
    ][:5]

    findings.append(
        Finding(
            check_id="COST-004",
            severity="MEDIUM",
            resource_type=RT_ACCOUNT,
            resource_id="account (tag coverage)",
            title=f"Cost allocation tag coverage is {coverage:.1f}% (target {threshold:.0f}%)",
            detail=(
                f"Of {total} resources examined (EC2 instances, EBS "
                f"volumes, S3 buckets, log groups), only {covered} carry "
                f"all critical tags ({', '.join(sorted(CRITICAL_TAGS))}). "
                f"Coverage is {coverage:.1f}%, below the configured target "
                f"of {threshold:.0f}%. A bill line whose Owner tag is "
                f"empty is a bill line without a stakeholder; a bill line "
                f"whose Project tag is empty is a bill line nothing rolls "
                f"up into. Both are findings the finance team will send at "
                f"the END of the month rather than during it. "
                f"Sample uncovered: {', '.join(uncovered_sample) or 'none'}."
            ),
            remediation=(
                f"Set default_tags on the AWS provider (see this stack's "
                f"providers.tf) so every resource that Terraform creates "
                f"carries these keys automatically. For resources created "
                f"outside Terraform: `aws ec2 create-tags --resources "
                f"<id> --tags Key=Owner,Value=<name> Key=Project,Value="
                f"<name>`. THEN activate the tags as cost allocation tags "
                f"in the Billing console (this step is account-wide, "
                f"one-way and manual, and is the reason many tags that "
                f"'should work' don't group in Cost Explorer)."
            ),
            evidence={
                "total_resources": total,
                "covered_resources": covered,
                "coverage_percent": round(coverage, 1),
                "threshold_percent": threshold,
                "required_tags": sorted(CRITICAL_TAGS),
                "uncovered_sample": uncovered_sample,
            },
            region="",
        )
    )
    return findings


def check_orphan_volumes(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-005 - EBS volumes in 'available' state older than the threshold.

    An 'available' volume is one that is not attached to anything. It bills
    at $0.08/GB/month for gp3 (higher for other types), forever, and it
    accumulates naturally as stacks are half-destroyed, snapshots are
    restored to new volumes that supersede old ones, and manual tests leave
    debris.

    Fires PER VOLUME rather than once per account, because each is a
    separately-owned line item and remediation may involve tracking down a
    different person for each.
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("volume_orphan_days") or 7)

    for volume in stack.get("volumes") or []:
        state = str(volume.get("State", "")).lower()
        if state != "available":
            continue

        create_time = volume.get("CreateTime")
        age = _age_days(create_time, now)
        if age is None or age < threshold_days:
            continue

        volume_id = str(volume.get("VolumeId", "unknown"))
        size_gb = int(volume.get("Size") or 0)
        vol_type = str(volume.get("VolumeType", "unknown"))
        # gp3 baseline; other types would be slightly different. Not worth
        # a lookup table for a message that already says "approximately".
        est_monthly = size_gb * 0.08

        findings.append(
            Finding(
                check_id="COST-005",
                severity="HIGH",
                resource_type=RT_EBS_VOLUME,
                resource_id=volume_id,
                title="EBS volume is unattached and older than the retention window",
                detail=(
                    f"Volume {volume_id} ({size_gb} GB, type {vol_type}) has "
                    f"been in state 'available' for "
                    f"{_humanise_days(age)}, which is above the "
                    f"{threshold_days:.0f}-day threshold. Estimated cost: "
                    f"~${est_monthly:.2f}/month, indefinitely, for storage "
                    f"nothing is reading. This is one of the top three "
                    f"quiet cost decays in mature accounts - a stack that "
                    f"was destroyed but had `prevent_destroy = true` on the "
                    f"volume; a snapshot restored to a new volume that "
                    f"superseded this one; a manual test that 'we'll clean "
                    f"up later'."
                ),
                remediation=(
                    f"First, verify nothing wants it. Snapshot it (~$0.05/GB "
                    f"one-time) with a description explaining what it was: "
                    f"`aws ec2 create-snapshot --volume-id {volume_id} "
                    f"--description 'ORPHAN candidate, decommission "
                    f"YYYY-MM-DD, ticket #NNN'`. Then delete: "
                    f"`aws ec2 delete-volume --volume-id {volume_id}`. In "
                    f"an account where this pattern is common, add a Config "
                    f"rule (ec2-volume-inuse-check) that reports it "
                    f"continuously rather than waiting on the next audit."
                ),
                evidence={
                    "VolumeId": volume_id,
                    "Size": size_gb,
                    "VolumeType": vol_type,
                    "State": state,
                    "CreateTime": str(create_time),
                    "age_days": round(age, 1),
                    "estimated_monthly_usd": round(est_monthly, 2),
                    "threshold_days": threshold_days,
                },
                region=region,
            )
        )
    return findings


def check_orphan_eips(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-006 - Elastic IPs that are not associated with any resource.

    Since February 2024 unattached EIPs (and public IPv4 addresses generally)
    bill at $0.005/hour, roughly $3.60/month EACH. An account with 20
    forgotten EIPs across regions is $70+/month for nothing.

    Note that the EC2 API does not carry a 'CreateTime' on addresses, so
    there is no age-based filter here - every unassociated EIP is a
    finding. If that produces too much noise in your account, use tags.
    """
    findings: List[Finding] = []

    for address in stack.get("elastic_ips") or []:
        # AssociationId is present when the EIP is attached to an ENI or
        # instance. Absent means unassociated.
        if address.get("AssociationId"):
            continue

        public_ip = str(address.get("PublicIp", "unknown"))
        allocation_id = str(address.get("AllocationId", "unknown"))
        est_monthly = 3.60

        findings.append(
            Finding(
                check_id="COST-006",
                severity="MEDIUM",
                resource_type=RT_EIP,
                resource_id=allocation_id,
                title="Elastic IP is unassociated and billing continuously",
                detail=(
                    f"EIP {public_ip} (allocation {allocation_id}) is not "
                    f"associated with any resource. Since February 2024, "
                    f"unattached Elastic IPs bill at $0.005/hour "
                    f"(~${est_monthly:.2f}/month), regardless of use. The "
                    f"'how did that get there' answer is usually 'an old "
                    f"NAT gateway from a stack that half-destroyed'. Two "
                    f"forgotten EIPs is a coffee a month; ten across "
                    f"regions is a small vendor invoice."
                ),
                remediation=(
                    f"Release it: `aws ec2 release-address --allocation-id "
                    f"{allocation_id} --region {region}`. If you need it "
                    f"reserved for a future NAT gateway, tag it "
                    f"'Reserved-For=<purpose>' and add the tag to a "
                    f"suppression list. Silent noise is expensive."
                ),
                evidence={
                    "PublicIp": public_ip,
                    "AllocationId": allocation_id,
                    "estimated_monthly_usd": est_monthly,
                },
                region=region,
            )
        )
    return findings


def check_old_snapshots(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-007 - EBS snapshots older than snapshot_retention_days.

    Fires per snapshot. This is the slow accumulator: every automated backup
    rule keeps writing, and unless a companion rule ages the old ones out,
    yesterday's backup exists forever. $0.05/GB/month for standard EBS
    snapshots, slightly less for Archive tier. Ten years of daily 100-GB
    snapshots is ~$182,000/month.

    Silent by situation in STATE A because a fresh terraform apply produces
    no snapshots at all.
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("snapshot_retention_days") or 90)

    for snapshot in stack.get("snapshots") or []:
        start_time = snapshot.get("StartTime")
        age = _age_days(start_time, now)
        if age is None or age < threshold_days:
            continue

        snap_id = str(snapshot.get("SnapshotId", "unknown"))
        size_gb = int(snapshot.get("VolumeSize") or 0)
        est_monthly = size_gb * 0.05
        description = str(snapshot.get("Description") or "")[:60]

        findings.append(
            Finding(
                check_id="COST-007",
                severity="MEDIUM",
                resource_type=RT_EBS_SNAPSHOT,
                resource_id=snap_id,
                title="EBS snapshot older than the retention window",
                detail=(
                    f"Snapshot {snap_id} ({size_gb} GB) is "
                    f"{_humanise_days(age)} old, which exceeds the "
                    f"{threshold_days:.0f}-day retention window. Estimated "
                    f"cost: ~${est_monthly:.2f}/month for standard EBS "
                    f"snapshot storage. Description: "
                    f"{description or '(none)'}. In a mature account this "
                    f"check is where the largest quiet finding usually "
                    f"lives - not because any single snapshot matters, but "
                    f"because thousands of them do."
                ),
                remediation=(
                    f"If snapshots are managed by AWS Backup or a DLM "
                    f"lifecycle policy, extend the DELETE rule so future "
                    f"snapshots age out. Delete this one: `aws ec2 "
                    f"delete-snapshot --snapshot-id {snap_id} --region "
                    f"{region}`. For unmanaged snapshots, consider moving "
                    f"to Amazon Data Lifecycle Manager which is designed "
                    f"for exactly this problem."
                ),
                evidence={
                    "SnapshotId": snap_id,
                    "VolumeSize": size_gb,
                    "StartTime": str(start_time),
                    "age_days": round(age, 1),
                    "threshold_days": threshold_days,
                    "estimated_monthly_usd": round(est_monthly, 2),
                },
                region=region,
            )
        )
    return findings


def check_stopped_instances(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-008 - EC2 instances stopped for longer than the threshold.

    A stopped instance does not bill for compute. It DOES bill for every EBS
    volume attached to it, for any Elastic IP still allocated to it, and for
    the private-IP address post-October-2024 if it has a persistent public
    one. 'We only need this during month-end' is nearly always the wrong
    reason to leave one stopped - a snapshot-and-relaunch pattern is
    cheaper.

    The state transition time is read from StateTransitionReason, which
    contains 'User initiated (YYYY-MM-DD HH:MM:SS GMT)'. Parsing is best-
    effort; when it fails, the check skips rather than reporting an unknown
    age.
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("instance_stopped_days") or 30)

    for instance in stack.get("instances") or []:
        state = str(instance.get("State", {}).get("Name", "")).lower()
        if state != "stopped":
            continue

        # StateTransitionReason format: 'User initiated (YYYY-MM-DD HH:MM:SS GMT)'
        reason = str(instance.get("StateTransitionReason", ""))
        stopped_at: Optional[datetime] = None
        if "(" in reason and ")" in reason:
            inner = reason[reason.index("(") + 1:reason.index(")")]
            inner_iso = inner.replace(" GMT", "").replace(" ", "T")
            stopped_at = _parse_time(inner_iso)

        # Fall back to LaunchTime as the earliest possible stop time.
        launch_time = instance.get("LaunchTime")
        reference = stopped_at or _parse_time(launch_time)
        age = _age_days(reference, now) if reference else None
        if age is None or age < threshold_days:
            continue

        instance_id = str(instance.get("InstanceId", "unknown"))
        instance_type = str(instance.get("InstanceType", "unknown"))

        # Rough estimate: sum root+attached volume sizes if we can, at
        # gp3 rates. This is a floor; a t3.large with a 500 GB gp3 root
        # is $40/month of storage alone.
        volume_gb = 0
        for mapping in instance.get("BlockDeviceMappings") or []:
            ebs = mapping.get("Ebs") or {}
            volume_gb += int(ebs.get("VolumeSize") or 0)
        est_monthly = volume_gb * 0.08

        findings.append(
            Finding(
                check_id="COST-008",
                severity="MEDIUM",
                resource_type=RT_INSTANCE,
                resource_id=instance_id,
                title="EC2 instance has been stopped for an extended period",
                detail=(
                    f"Instance {instance_id} ({instance_type}) has been in "
                    f"state 'stopped' for {_humanise_days(age)}, exceeding "
                    f"the {threshold_days:.0f}-day threshold. Compute is "
                    f"not billing while stopped, but every attached EBS "
                    f"volume is - estimated ~${est_monthly:.2f}/month for "
                    f"{volume_gb} GB of storage. Add any attached EIP at "
                    f"$3.60/month. 'We only need this at month-end' has "
                    f"cheaper answers than 'stopped for 27 days out of "
                    f"every 30'."
                ),
                remediation=(
                    f"If the workload is genuinely intermittent, take an "
                    f"AMI (`aws ec2 create-image --instance-id "
                    f"{instance_id} --name <name>`), then terminate the "
                    f"instance. Re-launch from the AMI when needed - the "
                    f"AMI is a snapshot at $0.05/GB/month rather than "
                    f"full-volume storage at $0.08/GB/month. If the "
                    f"workload is not intermittent, either run it or "
                    f"decommission it."
                ),
                evidence={
                    "InstanceId": instance_id,
                    "InstanceType": instance_type,
                    "StateTransitionReason": reason,
                    "age_days": round(age, 1),
                    "threshold_days": threshold_days,
                    "attached_gb": volume_gb,
                    "estimated_monthly_usd": round(est_monthly, 2),
                },
                region=region,
            )
        )
    return findings


def check_previous_gen_instance(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-009 - EC2 instances of previous-generation families.

    Fires per instance whose family (m5, c4, t2, etc.) is in the
    previous_gen_families list. Not automatically wrong - an m5 running on a
    3-year Reserved Instance is exactly what it should be - but nearly
    always UNEXAMINED, and the check flags it for consideration rather than
    for automatic replacement.
    """
    findings: List[Finding] = []
    families: Set[str] = set(
        stack.get("previous_gen_families") or DEFAULT_PREVIOUS_GEN_FAMILIES
    )

    for instance in stack.get("instances") or []:
        state = str(instance.get("State", {}).get("Name", "")).lower()
        if state not in ("running", "pending", "stopped"):
            continue

        instance_type = str(instance.get("InstanceType", ""))
        family = _instance_family(instance_type)
        if family not in families:
            continue

        instance_id = str(instance.get("InstanceId", "unknown"))
        # Map to the strict successor for the message. Not comprehensive.
        successor_map = {
            "t2": "t3 (Nitro, cheaper baseline)",
            "m3": "m6i or m7i",
            "m4": "m6i or m7i",
            "m5": "m6i, m7i or Graviton m7g",
            "c3": "c6i or c7i",
            "c4": "c6i or c7i",
            "c5": "c6i, c7i or Graviton c7g",
            "r3": "r6i or r7i",
            "r4": "r6i or r7i",
        }
        successor = successor_map.get(family, "the current-generation equivalent")

        findings.append(
            Finding(
                check_id="COST-009",
                severity="MEDIUM",
                resource_type=RT_INSTANCE,
                resource_id=instance_id,
                title=f"Instance is of previous-generation family '{family}'",
                detail=(
                    f"Instance {instance_id} is a {instance_type} - the "
                    f"'{family}' family is superseded. Same size on the "
                    f"successor generation is typically the same price or "
                    f"cheaper AND has a substantially higher baseline. "
                    f"Consider {successor}. NOTE: previous-gen is not "
                    f"always wrong; an instance on a Reserved Instance you "
                    f"already paid for is correct until the RI expires. "
                    f"But it is nearly always UNEXAMINED, which is what "
                    f"this check flags."
                ),
                remediation=(
                    f"Rebuild the instance from an AMI on the successor "
                    f"family. If it is behind a load balancer, blue/green: "
                    f"launch new, register with target group, deregister "
                    f"old, terminate old. If it is standalone, snapshot "
                    f"and relaunch (measure boot time; user-data on a "
                    f"different generation can behave differently). If it "
                    f"is on an RI, wait for RI expiry or exchange the RI "
                    f"if it is a Standard RI on Convertible terms."
                ),
                evidence={
                    "InstanceId": instance_id,
                    "InstanceType": instance_type,
                    "family": family,
                    "suggested_successor": successor,
                },
                region=region,
            )
        )
    return findings


def check_gp2_volumes(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-010 - EBS volumes of type gp2 (or any type in
    SUPERSEDED_VOLUME_TYPES).

    LOW severity. gp3 is cheaper AND faster in every dimension. There is no
    workload for which gp2 is preferable to gp3 today, but migration is
    manual and nobody has a reason to do it during a quiet week - which is
    exactly the argument for the check.
    """
    findings: List[Finding] = []

    for volume in stack.get("volumes") or []:
        vol_type = str(volume.get("VolumeType", ""))
        if vol_type not in SUPERSEDED_VOLUME_TYPES:
            continue

        volume_id = str(volume.get("VolumeId", "unknown"))
        size_gb = int(volume.get("Size") or 0)
        gp2_cost = size_gb * 0.10
        gp3_cost = size_gb * 0.08
        savings = gp2_cost - gp3_cost

        findings.append(
            Finding(
                check_id="COST-010",
                severity="LOW",
                resource_type=RT_EBS_VOLUME,
                resource_id=volume_id,
                title=f"EBS volume type is {vol_type}, superseded by gp3",
                detail=(
                    f"Volume {volume_id} ({size_gb} GB) is {vol_type}. gp3 "
                    f"is cheaper ($0.08/GB/month vs $0.10) AND faster "
                    f"(3000 IOPS baseline regardless of size). Estimated "
                    f"saving on this volume alone: ~${savings:.2f}/month. "
                    f"Migration is in-place and does not require a stop or "
                    f"a snapshot."
                ),
                remediation=(
                    f"`aws ec2 modify-volume --volume-id {volume_id} "
                    f"--volume-type gp3 --region {region}`. The volume "
                    f"stays online during the modification; the state "
                    f"goes to 'modifying' then back to 'in-use' over a "
                    f"few minutes. No workload impact."
                ),
                evidence={
                    "VolumeId": volume_id,
                    "Size": size_gb,
                    "VolumeType": vol_type,
                    "estimated_saving_monthly_usd": round(savings, 2),
                },
                region=region,
            )
        )
    return findings


def check_classic_elb(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-011 - Classic Load Balancers still in use.

    Superseded by ALB (HTTP/HTTPS, layer 7) and NLB (TCP/UDP, layer 4) since
    2016. Classic ELB carries the same per-hour price as ALB (~$16.20/month)
    but supports fewer features and does not benefit from newer AWS work.
    The reason it still exists in an account is usually organisational
    (nobody has been staffed to rebuild the stack), not technical.
    """
    findings: List[Finding] = []

    for elb in stack.get("classic_elbs") or []:
        name = str(elb.get("LoadBalancerName", "unknown"))
        est_monthly = 16.20

        findings.append(
            Finding(
                check_id="COST-011",
                severity="MEDIUM",
                resource_type=RT_ELB_CLASSIC,
                resource_id=name,
                title="Classic Load Balancer (ELBv1) is in use",
                detail=(
                    f"Load balancer '{name}' is a Classic (v1) ELB, "
                    f"superseded by ALB (layer 7) and NLB (layer 4) since "
                    f"2016. Base cost is ~${est_monthly:.2f}/month plus "
                    f"$0.008/GB processed, roughly the same as ALB - so "
                    f"the migration argument is about features and "
                    f"support horizon, not immediate cost. But the moment "
                    f"you need path-based routing, WebSocket, HTTP/2, or "
                    f"any target-group-based mechanism (Fargate, Lambda "
                    f"targets, weighted routing), you are rebuilding "
                    f"anyway."
                ),
                remediation=(
                    f"Replace with an ALB or NLB. AWS provides a Migration "
                    f"Wizard in the EC2 console (Load Balancers > Migrate "
                    f"to ALB) that produces a Terraform-equivalent config "
                    f"you can adopt. Run both in parallel behind Route 53 "
                    f"weighted routing while you validate; then switch "
                    f"traffic and delete the Classic."
                ),
                evidence={
                    "LoadBalancerName": name,
                    "estimated_monthly_usd": est_monthly,
                },
                region=region,
            )
        )
    return findings


def check_nat_without_endpoints(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-012 - a VPC has a NAT gateway but no S3 or DynamoDB gateway
    endpoints.

    Gateway endpoints for S3 and DynamoDB are FREE. Interface endpoints (for
    KMS, Secrets Manager, ECR, etc.) cost ~$7.30/month per AZ per endpoint,
    which is a real trade-off. The gateway endpoints are not - there is no
    argument for not having them in any VPC that also has a NAT gateway.

    NAT processes traffic at $0.045/GB. S3 gateway endpoint traffic bypasses
    NAT at $0.00/GB. On a busy stack this is often the largest cost that a
    five-minute config change removes.
    """
    findings: List[Finding] = []

    # Group NAT gateways and endpoints by VPC.
    nat_by_vpc: Dict[str, List[str]] = {}
    for gateway in stack.get("nat_gateways") or []:
        state = str(gateway.get("State", "")).lower()
        if state not in ("available", "pending"):
            continue
        vpc_id = str(gateway.get("VpcId", ""))
        if not vpc_id:
            continue
        nat_by_vpc.setdefault(vpc_id, []).append(str(gateway.get("NatGatewayId")))

    endpoints_by_vpc: Dict[str, Set[str]] = {}
    for endpoint in stack.get("vpc_endpoints") or []:
        if str(endpoint.get("VpcEndpointType", "")).lower() != "gateway":
            continue
        vpc_id = str(endpoint.get("VpcId", ""))
        service = str(endpoint.get("ServiceName", ""))
        if not vpc_id:
            continue
        endpoints_by_vpc.setdefault(vpc_id, set()).add(service)

    for vpc_id, nat_ids in sorted(nat_by_vpc.items()):
        endpoints = endpoints_by_vpc.get(vpc_id, set())
        # A gateway endpoint's service name looks like
        # com.amazonaws.us-east-1.s3 - check by suffix.
        has_s3 = any(s.endswith(".s3") for s in endpoints)
        has_ddb = any(s.endswith(".dynamodb") for s in endpoints)
        if has_s3 and has_ddb:
            continue

        missing = []
        if not has_s3:
            missing.append("S3")
        if not has_ddb:
            missing.append("DynamoDB")

        findings.append(
            Finding(
                check_id="COST-012",
                severity="MEDIUM",
                resource_type=RT_VPC,
                resource_id=vpc_id,
                title="VPC has a NAT gateway but no free gateway endpoints",
                detail=(
                    f"VPC {vpc_id} has {len(nat_ids)} NAT gateway(s) "
                    f"({', '.join(nat_ids)}) and is missing the free "
                    f"gateway endpoint(s) for: {', '.join(missing)}. NAT "
                    f"processes traffic at $0.045/GB; S3 and DynamoDB "
                    f"gateway endpoints are $0.00/GB and $0.00/month. On "
                    f"a stack that reads from S3 (backup restores, log "
                    f"shipping, artifact downloads, yum/dnf via the "
                    f"AL2023 mirror), this is one of the largest cost "
                    f"lines a five-minute change removes."
                ),
                remediation=(
                    f"Create gateway endpoints in this VPC and associate "
                    f"them with the route tables of every subnet whose "
                    f"traffic reaches S3 or DynamoDB. Terraform: "
                    f"`resource \"aws_vpc_endpoint\" \"s3\" {{ "
                    f"service_name = \"com.amazonaws.{region}.s3\" "
                    f"vpc_endpoint_type = \"Gateway\" }}`. Same shape for "
                    f"DynamoDB. Free."
                ),
                evidence={
                    "VpcId": vpc_id,
                    "nat_gateway_ids": nat_ids,
                    "missing_endpoints": missing,
                },
                region=region,
            )
        )
    return findings


def check_unbounded_log_groups(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-013 - CloudWatch log groups whose retention is not set.

    Retention 'Never Expire' at $0.03/GB/month is the slowest reliably-quiet
    cost decay in the account. It starts at nothing. Twelve months later it
    is the third line on the bill and nobody remembers creating the log
    groups (Lambda functions do it automatically, and every Lambda that
    never sets retention leaves a permanent trail).

    Fires per log group, not per account, because each is separately owned.
    """
    findings: List[Finding] = []

    for group in stack.get("log_groups") or []:
        retention = group.get("retentionInDays")
        if retention is not None:
            continue

        name = str(group.get("logGroupName", "unknown"))
        stored_bytes = int(group.get("storedBytes") or 0)
        stored_gb = stored_bytes / (1024 ** 3)
        est_monthly = stored_gb * 0.03

        findings.append(
            Finding(
                check_id="COST-013",
                severity="MEDIUM",
                resource_type=RT_LOG_GROUP,
                resource_id=name,
                title="CloudWatch log group has no retention set",
                detail=(
                    f"Log group '{name}' has retention 'Never Expire'. "
                    f"Currently storing {stored_gb:.2f} GB at "
                    f"~${est_monthly:.2f}/month (storage only; ingest was "
                    f"a separate one-time $0.50/GB). This number grows "
                    f"monotonically as long as anything writes to the "
                    f"group, and there is no upper bound."
                ),
                remediation=(
                    f"Set a retention: `aws logs put-retention-policy "
                    f"--log-group-name '{name}' --retention-in-days 30 "
                    f"--region {region}`. Common values: 7 for verbose "
                    f"debug, 30 for application, 90 for compliance, 365 "
                    f"for audit. In Terraform: "
                    f"`aws_cloudwatch_log_group.name.retention_in_days = "
                    f"30`. If old data must be kept, export to S3 with a "
                    f"lifecycle rule that transitions to Glacier at day "
                    f"30 - S3 Glacier Deep Archive is $0.00099/GB vs "
                    f"CloudWatch Logs' $0.03."
                ),
                evidence={
                    "logGroupName": name,
                    "retentionInDays": None,
                    "storedBytes": stored_bytes,
                    "stored_gb": round(stored_gb, 3),
                    "estimated_monthly_usd": round(est_monthly, 2),
                },
                region=region,
            )
        )
    return findings


def check_bucket_no_lifecycle(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-014 - S3 buckets with no lifecycle configuration.

    A bucket without lifecycle stays STANDARD storage forever, at
    $0.023/GB/month. STANDARD-IA is $0.0125, Glacier Instant Retrieval is
    $0.004, Glacier Deep Archive is $0.00099. The bucket a Lambda writes
    into and never reads from is the archetype: 300 GB over three years at
    STANDARD is $22.90/month in perpetuity for data nothing has touched
    since it was written.
    """
    findings: List[Finding] = []

    for bucket in stack.get("buckets") or []:
        name = str(bucket.get("Name", "unknown"))
        rules = bucket.get("LifecycleRules") or []
        active_rules = [r for r in rules if str(r.get("Status", "")).lower() == "enabled"]

        if active_rules:
            continue

        findings.append(
            Finding(
                check_id="COST-014",
                severity="MEDIUM",
                resource_type=RT_BUCKET,
                resource_id=name,
                title="S3 bucket has no active lifecycle rule",
                detail=(
                    f"Bucket '{name}' has no enabled lifecycle rules. "
                    f"Objects remain in STANDARD storage ($0.023/GB/month) "
                    f"indefinitely. For a bucket used by a Lambda that "
                    f"writes and never reads - which is the archetype - "
                    f"this is money billed continuously for data that is "
                    f"not accessed after the first minute of its life."
                ),
                remediation=(
                    f"Attach a lifecycle rule that transitions to "
                    f"STANDARD-IA at day 30 ($0.0125/GB, ~45% cheaper), to "
                    f"Glacier Instant Retrieval at day 90 ($0.004/GB, "
                    f"~83% cheaper), and expires objects at whatever "
                    f"retention is appropriate. Terraform: "
                    f"`aws_s3_bucket_lifecycle_configuration` with a rule "
                    f"block. For usage where access patterns are unknown, "
                    f"consider S3 Intelligent-Tiering, which does the "
                    f"tiering automatically at a small per-object fee."
                ),
                evidence={
                    "BucketName": name,
                    "rule_count": len(rules),
                    "active_rule_count": len(active_rules),
                },
                region=region,
            )
        )
    return findings


def check_long_running_no_savings_plan(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-015 - EC2 instances running longer than long_running_instance_days
    without any Savings Plan or Reserved Instance coverage in the account.

    Fires ONCE per long-running instance, only when SP/RI count is zero at
    the account level. If ANY commitment exists, the check stays silent -
    the auditor cannot know from list responses whether a specific instance
    is covered, and firing on every long-running instance when a Savings
    Plan probably covers most of them would train people to ignore the
    check.

    Deliberately narrow. Fires when the question 'have we looked at this
    yet' has an unambiguous answer of no.
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("long_running_instance_days") or 30)

    savings_plans = stack.get("savings_plans") or []
    reserved_instances = stack.get("reserved_instances") or []
    active_sp = [
        sp for sp in savings_plans
        if str(sp.get("state", "")).lower() == "active"
    ]
    active_ri = [
        ri for ri in reserved_instances
        if str(ri.get("State", "")).lower() == "active"
    ]

    # If ANY commitment exists, the auditor cannot know from list responses
    # whether a specific instance is covered. Stay silent.
    if active_sp or active_ri:
        return findings

    for instance in stack.get("instances") or []:
        state = str(instance.get("State", {}).get("Name", "")).lower()
        if state != "running":
            continue

        launch_time = instance.get("LaunchTime")
        age = _age_days(launch_time, now)
        if age is None or age < threshold_days:
            continue

        instance_id = str(instance.get("InstanceId", "unknown"))
        instance_type = str(instance.get("InstanceType", "unknown"))

        findings.append(
            Finding(
                check_id="COST-015",
                severity="MEDIUM",
                resource_type=RT_INSTANCE,
                resource_id=instance_id,
                title="Long-running instance with zero Savings Plan or RI coverage",
                detail=(
                    f"Instance {instance_id} ({instance_type}) has been "
                    f"running for {_humanise_days(age)} - above the "
                    f"{threshold_days:.0f}-day threshold - and the account "
                    f"has zero active Savings Plans or Reserved Instances. "
                    f"A Compute Savings Plan for baseline consumption is "
                    f"typically ~30% off the on-demand rate, no operational "
                    f"change required. This is the largest single cost "
                    f"optimisation in most accounts and it is the one that "
                    f"leaves nothing running that was not already running."
                ),
                remediation=(
                    f"Open the Savings Plans console: "
                    f"https://console.aws.amazon.com/cost-management/home"
                    f"#/savings-plans/recommendations. Review "
                    f"recommendations, which are ML-generated from the "
                    f"last 30 days of actual usage. Compute Savings Plans "
                    f"cover ANY instance family, region and OS - so a "
                    f"1-year, No Upfront commitment at 60-70% of baseline "
                    f"usage is a low-risk starting point. 'We looked and "
                    f"decided not to' is a valid answer; suppress the "
                    f"check by tagging or by adjusting "
                    f"long_running_instance_days. Silence without a "
                    f"decision is not."
                ),
                evidence={
                    "InstanceId": instance_id,
                    "InstanceType": instance_type,
                    "uptime_days": round(age, 1),
                    "threshold_days": threshold_days,
                    "active_savings_plans": len(active_sp),
                    "active_reserved_instances": len(active_ri),
                },
                region=region,
            )
        )
    return findings


def check_untriaged_anomalies(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-016 - Cost anomalies raised more than anomaly_triage_days ago
    without Feedback provided.

    THE DAY'S THESIS. An anomaly with Feedback = None is one that fired,
    produced a notification (if a subscription existed), and was not
    acknowledged. 'Acknowledged' here does not mean 'acted on' - it means
    somebody opened the console and marked the anomaly Yes (real issue), No
    (false positive), or PlannedActivity (expected). Any of those three
    counts as triage; only the absence of all three counts as untriaged.

    This is the only CRITICAL check on this day, and it is the only check in
    the entire repo whose fault is 'the process does not work' rather than
    'a specific resource is misconfigured'. A stack where every other check
    is green and COST-016 is red is an account that has bought cost tooling
    and not yet started using it, which is the modal state of cost tooling.
    """
    findings: List[Finding] = []
    now = _now(stack)
    threshold_days = float(stack.get("anomaly_triage_days") or 7)

    for anomaly in stack.get("cost_anomalies") or []:
        # Feedback field values: YES, NO, PLANNED_ACTIVITY, or absent.
        feedback = str(anomaly.get("Feedback") or "").strip().upper()
        if feedback in ("YES", "NO", "PLANNED_ACTIVITY"):
            continue

        anomaly_start = anomaly.get("AnomalyStartDate")
        age = _age_days(anomaly_start, now)
        if age is None or age < threshold_days:
            continue

        anomaly_id = str(anomaly.get("AnomalyId", "unknown"))
        impact = anomaly.get("Impact") or {}
        total_impact = float(impact.get("TotalImpact") or 0)

        findings.append(
            Finding(
                check_id="COST-016",
                severity="CRITICAL",
                resource_type=RT_ANOMALY,
                resource_id=anomaly_id,
                title="Cost anomaly has been open without feedback",
                detail=(
                    f"Anomaly {anomaly_id} was raised "
                    f"{_humanise_days(age)} ago (threshold "
                    f"{threshold_days:.0f} days). Total impact: "
                    f"${total_impact:.2f}. Feedback field is empty, which "
                    f"means nobody has opened the anomaly in the Cost "
                    f"Anomaly Detection console. The ML flagged it. The "
                    f"notification (if a subscription exists) was sent. "
                    f"Nothing acknowledged it. THIS IS THE DAY'S CENTRAL "
                    f"FINDING: an unread anomaly is a bill that keeps "
                    f"growing, and a monitor whose messages nobody reads "
                    f"is a machine talking to itself."
                ),
                remediation=(
                    f"Open the Cost Anomaly Detection console: "
                    f"https://console.aws.amazon.com/cost-management/home"
                    f"#/anomaly-detection. For each open anomaly, click "
                    f"into it, review the root causes AWS attaches "
                    f"(specific service, region, usage type, linked "
                    f"account), and provide Feedback: Yes if it is a real "
                    f"issue you need to fix, No if it is a false positive, "
                    f"PlannedActivity if it is expected (a promo campaign, "
                    f"a scheduled batch job). ANY of the three counts. "
                    f"The check does not care WHICH answer you give - it "
                    f"cares that somebody looked. Then make anomaly "
                    f"triage a step in a weekly rota, not a heroic "
                    f"individual behaviour."
                ),
                evidence={
                    "AnomalyId": anomaly_id,
                    "AnomalyStartDate": str(anomaly_start),
                    "age_days": round(age, 1),
                    "threshold_days": threshold_days,
                    "TotalImpact": total_impact,
                    "Feedback": feedback or None,
                },
                region="",
            )
        )
    return findings


CHECKS = [
    ("COST-001", check_no_budget),
    ("COST-002", check_budget_no_notification),
    ("COST-003", check_no_anomaly_monitor),
    ("COST-004", check_tag_coverage),
    ("COST-005", check_orphan_volumes),
    ("COST-006", check_orphan_eips),
    ("COST-007", check_old_snapshots),
    ("COST-008", check_stopped_instances),
    ("COST-009", check_previous_gen_instance),
    ("COST-010", check_gp2_volumes),
    ("COST-011", check_classic_elb),
    ("COST-012", check_nat_without_endpoints),
    ("COST-013", check_unbounded_log_groups),
    ("COST-014", check_bucket_no_lifecycle),
    ("COST-015", check_long_running_no_savings_plan),
    ("COST-016", check_untriaged_anomalies),
]

LIVE_CHECKS = [check_id for check_id, _ in CHECKS]

# Checks that read RUNTIME state rather than configuration. Listed separately
# because their answer depends on WHEN you ran the tool. On this day that is
# not a footnote: COST-007, COST-008, COST-015 and COST-016 all change answer
# with the clock alone, and STATE C exists to demonstrate exactly that.
RUNTIME_CHECKS = ["COST-007", "COST-008", "COST-015", "COST-016"]


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Expect ~31/100 against this lab's stack with create_insecure_examples =
    true. Twelve findings from sixteen checks; seven are silent (two by
    design, five by situation). Set the permissive flags off, tag the
    resources, and the same tool with the same checks returns 100/100.

    Come back a month later without changing anything else, and the score
    slides down to about 67 because time passed, anomalies fired, snapshots
    aged, and nobody looked at any of it. That is STATE C, and that is the
    whole day.
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
    w(colour("  COST OPTIMISATION AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 09 · Cost Optimization & AI Recommendations\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('instances', 0)} instance(s) · "
        f"{stats.get('volumes', 0)} volume(s) · "
        f"{stats.get('snapshots', 0)} snapshot(s) · "
        f"{stats.get('elastic_ips', 0)} EIP(s) · "
        f"{stats.get('vpcs', 0)} VPC(s) · "
        f"{stats.get('log_groups', 0)} log group(s) · "
        f"{stats.get('buckets', 0)} bucket(s) · "
        f"{stats.get('classic_elbs', 0)} classic ELB(s) · "
        f"{stats.get('budgets', 0)} budget(s) · "
        f"{stats.get('cost_anomaly_monitors', 0)} anomaly monitor(s) · "
        f"{stats.get('cost_anomalies', 0)} anomaly record(s)\n\n"
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
        "audit": "cost_audit",
        "day": "09",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
        # Named in the payload so a consumer diffing two runs knows which
        # checks could legitimately change without anybody touching the
        # account. On this day four checks decay with the clock alone.
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


class CostAuditor:
    """Collects one normalised snapshot of the account, then runs pure checks.

    Everything that touches AWS happens in collect(); everything that decides
    anything happens in a function that takes a dict. That is why
    tests/test_checks.py needs no credentials.

    Global-first, region-second. The Budgets, Cost Explorer, Cost Anomaly
    Detection and Savings Plans APIs are hosted at us-east-1 regardless of
    the caller's region, so those clients are pinned. EC2, EBS, log groups,
    S3 and ELB are regional and use the region passed in.
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        prefix: Optional[str] = None,
        volume_orphan_days: int = 7,
        eip_orphan_days: int = 7,
        snapshot_retention_days: int = 90,
        instance_stopped_days: int = 30,
        long_running_instance_days: int = 30,
        anomaly_triage_days: int = 7,
        tag_coverage_threshold_percent: int = 90,
        previous_gen_families: Optional[List[str]] = None,
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.prefix = prefix
        self.volume_orphan_days = volume_orphan_days
        self.eip_orphan_days = eip_orphan_days
        self.snapshot_retention_days = snapshot_retention_days
        self.instance_stopped_days = instance_stopped_days
        self.long_running_instance_days = long_running_instance_days
        self.anomaly_triage_days = anomaly_triage_days
        self.tag_coverage_threshold_percent = tag_coverage_threshold_percent
        self.previous_gen_families = (
            set(previous_gen_families)
            if previous_gen_families
            else set(DEFAULT_PREVIOUS_GEN_FAMILIES)
        )
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stack: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "instances": 0,
            "volumes": 0,
            "snapshots": 0,
            "elastic_ips": 0,
            "vpcs": 0,
            "log_groups": 0,
            "buckets": 0,
            "classic_elbs": 0,
            "budgets": 0,
            "cost_anomaly_monitors": 0,
            "cost_anomalies": 0,
        }

        self.session: Any = None
        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.ec2 = self.session.client("ec2")
            self.logs = self.session.client("logs")
            self.s3 = self.session.client("s3")
            self.elb = self.session.client("elb")
            self.sts = self.session.client("sts")
            # Global (us-east-1) clients for the billing APIs.
            self.budgets = self.session.client("budgets", region_name="us-east-1")
            self.ce = self.session.client("ce", region_name="us-east-1")
            self.savingsplans = self.session.client("savingsplans", region_name="us-east-1")
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
            "NoSuchLifecycleConfiguration",
            "NoSuchTagSet",
            "AccessDeniedException",
            "ValidationException",
        ):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    def _in_scope(self, name: str) -> bool:
        return not self.prefix or str(name).startswith(self.prefix)

    # -- collectors ---------------------------------------------------------

    def _collect_ec2(self, stack: Dict[str, Any]) -> None:
        reservations = paginate(self.ec2, "describe_instances", "Reservations")
        instances: List[Dict[str, Any]] = []
        for reservation in reservations:
            for instance in reservation.get("Instances") or []:
                # Filter by prefix on the Name tag when a prefix is set.
                if self.prefix:
                    tags = _tags_to_dict(instance.get("Tags"))
                    if not self._in_scope(tags.get("Name", "")):
                        continue
                instances.append(instance)
        stack["instances"] = instances
        self.stats["instances"] = len(instances)

        volumes = paginate(self.ec2, "describe_volumes", "Volumes")
        if self.prefix:
            volumes = [
                v for v in volumes
                if self._in_scope(_tags_to_dict(v.get("Tags")).get("Name", ""))
            ]
        stack["volumes"] = volumes
        self.stats["volumes"] = len(volumes)

        snapshots = paginate(
            self.ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"]
        )
        if self.prefix:
            snapshots = [
                s for s in snapshots
                if self._in_scope(_tags_to_dict(s.get("Tags")).get("Name", ""))
                or self._in_scope(str(s.get("Description", "")))
            ]
        stack["snapshots"] = snapshots
        self.stats["snapshots"] = len(snapshots)

        addresses = paginate(self.ec2, "describe_addresses", "Addresses")
        stack["elastic_ips"] = addresses
        self.stats["elastic_ips"] = len(addresses)

        vpcs = paginate(self.ec2, "describe_vpcs", "Vpcs")
        stack["vpcs"] = vpcs
        self.stats["vpcs"] = len(vpcs)

        stack["nat_gateways"] = paginate(
            self.ec2, "describe_nat_gateways", "NatGateways"
        )
        stack["vpc_endpoints"] = paginate(
            self.ec2, "describe_vpc_endpoints", "VpcEndpoints"
        )

    def _collect_logs(self, stack: Dict[str, Any]) -> None:
        groups = paginate(self.logs, "describe_log_groups", "logGroups")
        if self.prefix:
            groups = [
                g for g in groups
                if self._in_scope(str(g.get("logGroupName", "")))
            ]
        # Attach tags. list_tags_for_resource requires the log group ARN;
        # older accounts also accept list_tags_log_group by name.
        for group in groups:
            arn = group.get("arn") or group.get("logGroupArn")
            try:
                if arn:
                    resp = self.logs.list_tags_for_resource(resourceArn=arn)
                    group["Tags"] = resp.get("tags") or {}
                else:
                    resp = self.logs.list_tags_log_group(
                        logGroupName=group["logGroupName"]
                    )
                    group["Tags"] = resp.get("tags") or {}
            except ClientError as exc:
                self._swallow("list_tags", group.get("logGroupName", ""), exc)
                group["Tags"] = {}
        stack["log_groups"] = groups
        self.stats["log_groups"] = len(groups)

    def _collect_s3(self, stack: Dict[str, Any]) -> None:
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
                lifecycle = self.s3.get_bucket_lifecycle_configuration(Bucket=name)
                entry["LifecycleRules"] = lifecycle.get("Rules", [])
            except ClientError as exc:
                self._swallow("get_bucket_lifecycle_configuration", name, exc)
                entry["LifecycleRules"] = []
            try:
                tags = self.s3.get_bucket_tagging(Bucket=name).get("TagSet", [])
                entry["Tags"] = tags
            except ClientError as exc:
                self._swallow("get_bucket_tagging", name, exc)
                entry["Tags"] = []
            buckets.append(entry)
        stack["buckets"] = buckets
        self.stats["buckets"] = len(buckets)

    def _collect_elb(self, stack: Dict[str, Any]) -> None:
        elbs = paginate(
            self.elb, "describe_load_balancers", "LoadBalancerDescriptions"
        )
        if self.prefix:
            elbs = [e for e in elbs if self._in_scope(str(e.get("LoadBalancerName", "")))]
        stack["classic_elbs"] = elbs
        self.stats["classic_elbs"] = len(elbs)

    def _collect_budgets(self, stack: Dict[str, Any]) -> None:
        account_id = stack.get("account_id", "")
        if not account_id:
            stack["budgets"] = []
            return
        try:
            budgets = paginate(
                self.budgets, "describe_budgets", "Budgets", AccountId=account_id
            )
        except ClientError as exc:
            self._swallow("describe_budgets", account_id, exc)
            budgets = []

        for budget in budgets:
            name = budget.get("BudgetName")
            try:
                notifications = paginate(
                    self.budgets,
                    "describe_notifications_for_budget",
                    "Notifications",
                    AccountId=account_id,
                    BudgetName=name,
                )
            except ClientError as exc:
                self._swallow("describe_notifications_for_budget", str(name), exc)
                notifications = []

            # Attach subscribers to each notification.
            for notification in notifications:
                try:
                    subscribers = paginate(
                        self.budgets,
                        "describe_subscribers_for_notification",
                        "Subscribers",
                        AccountId=account_id,
                        BudgetName=name,
                        Notification=notification,
                    )
                except ClientError as exc:
                    self._swallow(
                        "describe_subscribers_for_notification", str(name), exc
                    )
                    subscribers = []
                notification["Subscribers"] = subscribers

            budget["Notifications"] = notifications

        stack["budgets"] = budgets
        self.stats["budgets"] = len(budgets)

    def _collect_cost_anomaly(self, stack: Dict[str, Any]) -> None:
        try:
            monitors = paginate(
                self.ce, "get_anomaly_monitors", "AnomalyMonitors"
            )
        except ClientError as exc:
            self._swallow("get_anomaly_monitors", "account", exc)
            monitors = []
        stack["cost_anomaly_monitors"] = monitors
        self.stats["cost_anomaly_monitors"] = len(monitors)

        try:
            subscriptions = paginate(
                self.ce, "get_anomaly_subscriptions", "AnomalySubscriptions"
            )
        except ClientError as exc:
            self._swallow("get_anomaly_subscriptions", "account", exc)
            subscriptions = []
        stack["cost_anomaly_subscriptions"] = subscriptions

        # Query the last 90 days of anomalies. get_anomalies requires a date
        # interval; the auditor's decay demonstration cares about the ones
        # older than anomaly_triage_days but younger than 90 days.
        anomalies: List[Dict[str, Any]] = []
        now = _now(stack)
        try:
            paginator = self.ce.get_paginator("get_anomalies")
            for page in paginator.paginate(
                DateInterval={
                    "StartDate": (now.date().replace(day=1)).isoformat(),
                    "EndDate": now.date().isoformat(),
                }
            ):
                anomalies.extend(page.get("Anomalies") or [])
        except ClientError as exc:
            self._swallow("get_anomalies", "account", exc)

        stack["cost_anomalies"] = anomalies
        self.stats["cost_anomalies"] = len(anomalies)

    def _collect_commitments(self, stack: Dict[str, Any]) -> None:
        # Reserved Instances live in EC2.
        try:
            reserved = paginate(
                self.ec2, "describe_reserved_instances", "ReservedInstances"
            )
        except ClientError as exc:
            self._swallow("describe_reserved_instances", "account", exc)
            reserved = []
        stack["reserved_instances"] = reserved

        # Savings Plans live in the savingsplans service.
        try:
            plans = paginate(
                self.savingsplans, "describe_savings_plans", "savingsPlans"
            )
        except ClientError as exc:
            self._swallow("describe_savings_plans", "account", exc)
            plans = []
        stack["savings_plans"] = plans

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
            "volume_orphan_days": self.volume_orphan_days,
            "eip_orphan_days": self.eip_orphan_days,
            "snapshot_retention_days": self.snapshot_retention_days,
            "instance_stopped_days": self.instance_stopped_days,
            "long_running_instance_days": self.long_running_instance_days,
            "anomaly_triage_days": self.anomaly_triage_days,
            "tag_coverage_threshold_percent": self.tag_coverage_threshold_percent,
            "previous_gen_families": self.previous_gen_families,
        }

        self.log("  · EC2, EBS, EIPs, VPC topology")
        self._collect_ec2(stack)
        self.log("  · CloudWatch log groups")
        self._collect_logs(stack)
        self.log("  · S3 buckets and lifecycle")
        self._collect_s3(stack)
        self.log("  · Classic Load Balancers")
        self._collect_elb(stack)
        self.log("  · AWS Budgets (us-east-1)")
        self._collect_budgets(stack)
        self.log("  · Cost Anomaly Detection (us-east-1)")
        self._collect_cost_anomaly(stack)
        self.log("  · Reserved Instances and Savings Plans")
        self._collect_commitments(stack)

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

        self.log("Collecting cost posture...")
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
        prog="cost_audit.py",
        description=(
            "Audit the cost posture of an AWS account — the guardrails that "
            "catch overspend, the waste that accumulates when nobody looks, "
            "the shape of resources that has been superseded, the free "
            "architectural fixes that stop bills before they start, and the "
            "one thing that makes the whole day concrete: cost anomalies "
            "that fired, produced emails, and were never read."
        ),
        epilog=(
            "Examples:\n"
            "  cost_audit.py --profile bootcamp --region us-east-1\n"
            "  cost_audit.py --prefix cbc-day09           # only this lab's resources\n"
            "  cost_audit.py --snapshot-retention-days 30 # tighter than default\n"
            "  cost_audit.py --format json --quiet > cost-findings.json\n"
            "  cost_audit.py --fail-on CRITICAL           # exit 1 on any CRITICAL\n"
            "\n"
            "Four checks read RUNTIME state (COST-007, COST-008, COST-015,\n"
            "COST-016), so their answer depends on when you ran this. COST-016\n"
            "in particular will change answer on an UNCHANGED account as\n"
            "anomalies age past your anomaly_triage_days threshold - that is\n"
            "correct, and it is the day's central lesson. Run this on a\n"
            "schedule, not only at merge time.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--profile", default=None,
                        help="AWS CLI named profile. Day 01 created 'bootcamp'.")
    parser.add_argument("--region", default="us-east-1",
                        help="Regional resources to audit (default: us-east-1). "
                             "The billing APIs are always us-east-1 regardless.")
    parser.add_argument(
        "--prefix", default=None,
        help=(
            "Only examine resources whose Name tag or name starts with this "
            "prefix, e.g. cbc-day09. Omit to audit everything in the region - "
            "which is the run worth doing, and the one that finds the "
            "unassociated EIP nobody remembers creating."
        ),
    )
    parser.add_argument("--volume-orphan-days", type=int, default=7,
                        help="Unattached EBS volume age above which COST-005 fires (default: 7).")
    parser.add_argument("--eip-orphan-days", type=int, default=7,
                        help="Unassociated EIP age above which COST-006 fires (default: 7). "
                             "The EC2 API does not carry a CreateTime for addresses, so this "
                             "is currently informational - COST-006 fires on any unassociated EIP.")
    parser.add_argument("--snapshot-retention-days", type=int, default=90,
                        help="EBS snapshot age above which COST-007 fires (default: 90).")
    parser.add_argument("--instance-stopped-days", type=int, default=30,
                        help="EC2-stopped duration above which COST-008 fires (default: 30).")
    parser.add_argument("--long-running-instance-days", type=int, default=30,
                        help="EC2-running uptime above which COST-015 fires (default: 30).")
    parser.add_argument("--anomaly-triage-days", type=int, default=7,
                        help="Cost anomaly age without feedback above which COST-016 fires (default: 7).")
    parser.add_argument("--tag-coverage-threshold-percent", type=int, default=90,
                        help="Owner+Project tag coverage below which COST-004 fires (default: 90).")
    parser.add_argument("--min-severity", choices=SEVERITY_ORDER, default="INFO",
                        help="Only report findings at this severity or worse (default: INFO). "
                             "Filters display only; the score is always calculated from every finding.")
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

    auditor = CostAuditor(
        profile=args.profile,
        region=args.region,
        prefix=args.prefix,
        volume_orphan_days=args.volume_orphan_days,
        eip_orphan_days=args.eip_orphan_days,
        snapshot_retention_days=args.snapshot_retention_days,
        instance_stopped_days=args.instance_stopped_days,
        long_running_instance_days=args.long_running_instance_days,
        anomaly_triage_days=args.anomaly_triage_days,
        tag_coverage_threshold_percent=args.tag_coverage_threshold_percent,
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
