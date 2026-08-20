#!/usr/bin/env python3
"""
cost_audit_challenge.py — Day 09 cost auditor, for you to finish.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

GENERATED FROM cost_audit.py. Identical imports, identical Finding, identical
helpers, identical renderers, identical collector, identical CLI. Sixteen check
bodies have been removed and their DOCSTRINGS LEFT IN PLACE, because the
docstring is the specification. Read it before you write anything.

    cd lab/python
    COST_AUDIT_MODULE=cost_audit_challenge PYTHONPATH=challenge \\
      python3 -m unittest discover -s tests -v

47 tests. They need no AWS credentials, because every check is a pure function
over a plain dict. Aim for all 47 green; get there one CHECKPOINT at a time.

Roughly TWO hours if you work through it in order. The long ones are COST-004
(the tag coverage arithmetic), COST-008 (parsing StateTransitionReason to get
the stopped-at timestamp), COST-012 (the NAT/endpoint graph across VPCs), and
COST-016 (the CRITICAL check that is the day's thesis and whose logic is the
smallest, cleanly separating the mechanism from the message).

-----------------------------------------------------------------------------
WHICH CHECKS ARE NOT INDEPENDENT
-----------------------------------------------------------------------------
Six relationships. Writing them down is what stops them reading as bugs when a
test fails for a reason that is not in the check you just wrote.

  COST-001 AND COST-002 LOOK LIKE THE SAME CHECK. They are not. COST-001 asks
  whether ANY budget exists. COST-002 asks whether an EXISTING budget carries
  a notification with subscribers. Fixing COST-001 by creating a budget with
  zero notifications is exactly what people do, and it is the transition
  COST-002 exists to catch. On this stack COST-002 is silent by design and
  must stay silent against every fixture state that includes a
  Terraform-shaped budget.

  COST-003 AND COST-016 ARE THE SAME PATTERN AT TWO LAYERS. COST-003 asks
  "does the anomaly detector exist"; COST-016 asks "does anybody read what it
  says". Both cite the same console URL in their remediation. Neither
  remediates the other.

  COST-005 AND COST-006 ARE THE SAME IDEA AT DIFFERENT PRICE POINTS.
  Unattached volume, unassociated EIP — both bill for nothing, both accumulate
  in the same way, both fire per resource. Do not deduplicate to "the account
  has orphaned resources". Two vaults means two DR-008 findings on Day 08 and
  two volumes means two COST-005 findings here.

  COST-009 AND COST-010 FIRE ON THE SAME PREVIOUS-GENERATION INSTANCE and are
  not duplicates. Family (COST-009) is one remediation and root-volume type
  (COST-010) is another. Same resource, potentially different owners, both
  findings correct.

  COST-013 FIRES ONCE PER LOG GROUP, DELIBERATELY NOT DEDUPLICATED. Each log
  group is a separate line item and a separate owner. If your COST-013 returns
  one finding for an account with 40 unbounded log groups, you deduplicated.

  COST-004 IS SILENT BY DESIGN against this stack and must stay silent against
  every fixture that uses the base_stack() fixture. If it fires, either you
  read Tags in a shape that is not what boto3 returns, or you set the
  threshold above 100%.

-----------------------------------------------------------------------------
FILE LAYOUT
-----------------------------------------------------------------------------
Above the check functions: imports, Finding, paginate/as_list helpers, the
constants (RT_*, SEVERITY_*, DEFAULT_PREVIOUS_GEN_FAMILIES), the shared
derivations (_now, _parse_time, _age_days, _humanise_days, _tags_to_dict).
YOU DO NOT NEED TO CHANGE ANY OF THIS. They are complete.

Below the check functions: CHECKS registry, RUNTIME_CHECKS list, scoring
functions, renderers, CostAuditor collector, CLI. All complete.

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
    # =======================================================================
    # TODO 1 of 16 — COST-001
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-001 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-001"
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

def check_budget_no_notification(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-002 - a budget exists but carries no notification thresholds.

    SILENT BY DESIGN against this stack; the budget_notifications variable's
    validation refuses an empty list. Fires readily against budgets created
    via the console click-through wizard or by a Terraform module that made
    notifications optional.
    """
    # =======================================================================
    # TODO 2 of 16 — COST-002
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-002 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-002"
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

def check_no_anomaly_monitor(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-003 - the account has no Cost Anomaly Detection monitor.

    Cost Anomaly Detection is entirely free. It uses ML on the AWS side to
    learn the account's spend pattern and flag departures. The number of
    accounts that do not have it enabled is one of the quietest cost
    governance facts in the industry - not because people looked at it and
    declined; because the console has 400 links and this one is not on any
    of them by default.
    """
    # =======================================================================
    # TODO 3 of 16 — COST-003
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-003 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-003"
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
    # =======================================================================
    # TODO 4 of 16 — COST-004
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-004 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-004"
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
    # =======================================================================
    # TODO 5 of 16 — COST-005
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-005 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-005"
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

def check_orphan_eips(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-006 - Elastic IPs that are not associated with any resource.

    Since February 2024 unattached EIPs (and public IPv4 addresses generally)
    bill at $0.005/hour, roughly $3.60/month EACH. An account with 20
    forgotten EIPs across regions is $70+/month for nothing.

    Note that the EC2 API does not carry a 'CreateTime' on addresses, so
    there is no age-based filter here - every unassociated EIP is a
    finding. If that produces too much noise in your account, use tags.
    """
    # =======================================================================
    # TODO 6 of 16 — COST-006
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-006 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-006"
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
    # =======================================================================
    # TODO 7 of 16 — COST-007
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-007 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-007"
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
    # =======================================================================
    # TODO 8 of 16 — COST-008
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-008 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-008"
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

def check_previous_gen_instance(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-009 - EC2 instances of previous-generation families.

    Fires per instance whose family (m5, c4, t2, etc.) is in the
    previous_gen_families list. Not automatically wrong - an m5 running on a
    3-year Reserved Instance is exactly what it should be - but nearly
    always UNEXAMINED, and the check flags it for consideration rather than
    for automatic replacement.
    """
    # =======================================================================
    # TODO 9 of 16 — COST-009
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-009 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-009"
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

def check_gp2_volumes(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-010 - EBS volumes of type gp2 (or any type in
    SUPERSEDED_VOLUME_TYPES).

    LOW severity. gp3 is cheaper AND faster in every dimension. There is no
    workload for which gp2 is preferable to gp3 today, but migration is
    manual and nobody has a reason to do it during a quiet week - which is
    exactly the argument for the check.
    """
    # =======================================================================
    # TODO 10 of 16 — COST-010
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-010 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-010"
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

def check_classic_elb(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-011 - Classic Load Balancers still in use.

    Superseded by ALB (HTTP/HTTPS, layer 7) and NLB (TCP/UDP, layer 4) since
    2016. Classic ELB carries the same per-hour price as ALB (~$16.20/month)
    but supports fewer features and does not benefit from newer AWS work.
    The reason it still exists in an account is usually organisational
    (nobody has been staffed to rebuild the stack), not technical.
    """
    # =======================================================================
    # TODO 11 of 16 — COST-011
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-011 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-011"
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
    # =======================================================================
    # TODO 12 of 16 — COST-012
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-012 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-012"
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

def check_unbounded_log_groups(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-013 - CloudWatch log groups whose retention is not set.

    Retention 'Never Expire' at $0.03/GB/month is the slowest reliably-quiet
    cost decay in the account. It starts at nothing. Twelve months later it
    is the third line on the bill and nobody remembers creating the log
    groups (Lambda functions do it automatically, and every Lambda that
    never sets retention leaves a permanent trail).

    Fires per log group, not per account, because each is separately owned.
    """
    # =======================================================================
    # TODO 13 of 16 — COST-013
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-013 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-013"
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

def check_bucket_no_lifecycle(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """COST-014 - S3 buckets with no lifecycle configuration.

    A bucket without lifecycle stays STANDARD storage forever, at
    $0.023/GB/month. STANDARD-IA is $0.0125, Glacier Instant Retrieval is
    $0.004, Glacier Deep Archive is $0.00099. The bucket a Lambda writes
    into and never reads from is the archetype: 300 GB over three years at
    STANDARD is $22.90/month in perpetuity for data nothing has touched
    since it was written.
    """
    # =======================================================================
    # TODO 14 of 16 — COST-014
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-014 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-014"
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
    # =======================================================================
    # TODO 15 of 16 — COST-015
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-015 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-015"
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
    # =======================================================================
    # TODO 16 of 16 — COST-016
    # =======================================================================
    #
    # READ the docstring above. It is the specification for COST-016 —
    # what the finding must say, which stack keys to read, what the
    # severity is, and why the check exists.
    #
    # Look at the reference finder in cost_audit.py's other checks for
    # the shape:
    #     check_id="COST-016"
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
