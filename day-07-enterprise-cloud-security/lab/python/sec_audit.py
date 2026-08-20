#!/usr/bin/env python3
"""
sec_audit.py — Day 07 cloud security auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits detection, evidence, credential hygiene and — the part this day is
actually about — the automated response path built on top of them.

The bias this tool has, stated up front: it is far more interested in
AUTOMATION THAT WILL DO THE WRONG THING than in detection that is missing. A
team with no GuardDuty knows it has no GuardDuty. A team whose responder
triggers on `severity >= 7` believes it has automated containment, and what it
actually has is a system that will isolate a production instance the next time
somebody runs a penetration test.

What it checks
--------------
    SEC-001  GuardDuty not enabled         nothing is watching            CRITICAL
    SEC-002  Security Hub / no standards   findings with nowhere to land  HIGH
    SEC-003  Findings nobody triaged       a backlog is not detection     MEDIUM
    SEC-004  Publishing frequency 6h       updates arrive after the fact  LOW
    SEC-005  Response triggers on severity impact is not confidence       CRITICAL
    SEC-006  Trail is single-region        ap-south-1 is equally easy     HIGH
    SEC-007  No log file validation        logging, not evidence          HIGH
    SEC-008  Responder can tamper          it can delete its own trail    CRITICAL
    SEC-009  Trail bucket unprotected      no rollback from an overwrite  HIGH
    SEC-010  Secret with no rotation       nobody came back to it         MEDIUM
    SEC-011  Rotation configured, never ran the console says green        HIGH
    SEC-012  Containment is destructive    a decision nobody can undo     CRITICAL
    SEC-013  Long-lived access key         a copyable string              MEDIUM
    SEC-014  No runtime kill switch        stopping it needs a deploy     HIGH
    SEC-015  Response rule DISABLED        automation nobody knows is off MEDIUM
    SEC-016  Response target has no DLQ    a detection that vanished      MEDIUM

Two things carried over from Day 06, deliberately
-------------------------------------------------
**One signature.** Every check takes `(stack: Dict, region: str)` and returns
`List[Finding]`. Several need cross-resource context to be correct — SEC-008
must read a role's Deny statements before calling an Allow dangerous, SEC-014
only applies to functions that can actually take an action — and a
one-resource signature makes that impossible without a global.

**Time is injected, not read.** `stack["now"]` is set once by `collect()`.
Three checks here are age-based (SEC-003, SEC-011, SEC-013) and a check that
calls `datetime.now()` itself is a check whose tests are non-deterministic and
whose behaviour depends on when CI happened to run. Injecting the clock costs
one dictionary key and makes SEC-013's whole lesson testable: the same account
passes today and fails in ninety-one days with nothing changed.

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

Usage
-----
    sec_audit.py --profile bootcamp --region us-east-1
    sec_audit.py --format json --quiet > findings.json
    sec_audit.py --format csv --min-severity HIGH
    sec_audit.py --fail-on CRITICAL   # exit 1 on any CRITICAL

Requires: boto3. Nothing else, on any day of this bootcamp.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
# Identical to Days 03 through 06 on purpose. By Day 10 you will have five of
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

    check_id     Stable identifier (SEC-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken. resource_id is the thing you
                 would type into the console or the CLI to look at it.
    title        One line, imperative, readable in a table.
    detail       What was actually observed. Include the real values.
    remediation  What to do about it, concretely.
    evidence     Raw values so the finding is auditable without re-querying.
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
# An account with 400 roles is completely ordinary, and an audit that reports
# on the first 50 is worse than no audit, because it produces a clean report
# you believe.
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
    """IAM policy documents use a string where a list of one would do.

    `"Action": "iam:*"` and `"Action": ["iam:*"]` are the same document. Every
    policy parser that forgets this has a wildcard-detection bug, because the
    single-string form is exactly the form `Resource: "*"` takes.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_policy(document: Any) -> Dict[str, Any]:
    """Return a policy document as a dict.

    IAM hands them back URL-encoded JSON strings, EventBridge hands event
    patterns back as plain JSON strings, and our own tests hand them back as
    dicts. Accept all three rather than making every caller remember which.
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

# Actions that let a principal destroy or rewrite the audit trail, escalate its
# own privileges, or disable its own brake. A responder holding any of these
# without a matching Deny is check SEC-008.
TAMPER_ACTIONS: Dict[str, str] = {
    "cloudtrail:stoplogging": "stop the audit trail",
    "cloudtrail:deletetrail": "delete the audit trail",
    "cloudtrail:updatetrail": "rewrite what the audit trail records",
    "cloudtrail:puteventselectors": "narrow what the audit trail records",
    "cloudtrail:*": "do anything at all to the audit trail",
    "iam:*": "modify its own role and escalate",
    "iam:putrolepolicy": "modify its own role and escalate",
    "iam:attachrolepolicy": "modify its own role and escalate",
    "iam:createpolicyversion": "modify its own role and escalate",
    "ssm:putparameter": "disable its own kill switch",
    "ssm:*": "disable its own kill switch",
}

# Actions that make a Lambda capable of changing the account in response to a
# finding. Only functions holding one of these are subject to SEC-012 and
# SEC-014 — a read-only enrichment Lambda does not need a brake, and flagging
# it would train people to ignore the check.
CONTAINMENT_ACTIONS: Set[str] = {
    "ec2:modifyinstanceattribute",
    "ec2:terminateinstances",
    "ec2:stopinstances",
    "ec2:*",
    "iam:deleteaccesskey",
    "iam:updateaccesskey",
    "iam:putuserpolicy",
    "*",
}

REVERSIBLE_MODES: Set[str] = {"dry-run", "isolate", "quarantine", "tag", "notify"}

ENV_CONTAINMENT_MODE = "CONTAINMENT_MODE"
ENV_KILL_SWITCH = "KILL_SWITCH_PARAM"
ENV_ALLOW_LIST = "RESPOND_TO_TYPES"
ENV_SEVERITY_THRESHOLD = "SEVERITY_THRESHOLD"

GUARDDUTY_EVENT_SOURCE = "aws.guardduty"


###############################################################################
# Shared derivations
###############################################################################


def _env(function: Dict[str, Any]) -> Dict[str, str]:
    return (function.get("Environment") or {}).get("Variables") or {}


def _now(stack: Dict[str, Any]) -> datetime:
    """The clock, injected rather than read.

    A check that calls datetime.now() itself is a check whose tests are
    non-deterministic and whose behaviour depends on when CI happened to run.
    Injecting it costs one dictionary key and makes SEC-013's entire lesson
    testable: the same account passes today and fails in ninety-one days with
    nothing changed by anybody.
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


def policy_allows(document: Any, actions: Set[str]) -> List[str]:
    """Allowed actions from `actions` present in a policy document.

    Matches literal actions and the wildcard forms that subsume them, because
    `iam:*` and `*` are how the dangerous grant usually arrives — nobody writes
    `iam:PutRolePolicy` on a responder role by hand.
    """
    policy = parse_policy(document)
    found: List[str] = []
    for statement in as_list(policy.get("Statement")):
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        for action in as_list(statement.get("Action")):
            lowered = str(action).lower()
            if lowered in actions:
                found.append(lowered)
                continue
            if lowered == "*":
                found.append("*")
                continue
            if lowered.endswith(":*"):
                service = lowered.split(":", 1)[0]
                if any(candidate.startswith(service + ":") for candidate in actions):
                    found.append(lowered)
    return sorted(set(found))


def policy_denies(document: Any, actions: Set[str]) -> Set[str]:
    """Actions explicitly Denied.

    An explicit Deny cannot be overridden by any Allow, in any policy, ever.
    That property is why the responder role in main.tf section 7 uses Denies
    rather than merely omitting the dangerous actions, and it is why this
    check has to read them before calling an Allow a fault.
    """
    policy = parse_policy(document)
    denied: Set[str] = set()
    for statement in as_list(policy.get("Statement")):
        if not isinstance(statement, dict) or statement.get("Effect") != "Deny":
            continue
        for action in as_list(statement.get("Action")):
            lowered = str(action).lower()
            denied.add(lowered)
            if lowered.endswith(":*"):
                service = lowered.split(":", 1)[0]
                denied.update(a for a in actions if a.startswith(service + ":"))
            if lowered == "*":
                denied.update(actions)
    return denied


def responder_functions(stack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every Lambda whose role lets it CHANGE something in response to a finding.

    Identified by permission, not by name. Name matching survives a lab and
    nothing else; in a real account the function that contains threats is
    called `sec-auto-v2`.

    This is the set SEC-005, SEC-008, SEC-012 and SEC-014 operate on. A
    read-only enrichment Lambda subscribed to the same findings is correctly
    excluded — it needs no kill switch, because it cannot do anything.
    """
    role_policies = stack.get("role_policies") or {}
    found: List[Dict[str, Any]] = []
    for function in stack.get("lambda_functions") or []:
        role_name = (function.get("Role") or "").rsplit("/", 1)[-1]
        allows: List[str] = []
        for document in role_policies.get(role_name, []):
            allows += policy_allows(document, CONTAINMENT_ACTIONS)
        if allows:
            enriched = dict(function)
            enriched["_role_name"] = role_name
            enriched["_containment_actions"] = sorted(set(allows))
            found.append(enriched)
    return found


def guardduty_rules(stack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """EventBridge rules whose pattern matches GuardDuty findings."""
    matching = []
    for rule in stack.get("event_rules") or []:
        pattern = parse_policy(rule.get("EventPattern"))
        sources = [str(s) for s in as_list(pattern.get("source"))]
        if GUARDDUTY_EVENT_SOURCE in sources:
            matching.append(rule)
    return matching


def trail_bucket_for(stack: Dict[str, Any], trail: Dict[str, Any]) -> Dict[str, Any]:
    return (stack.get("buckets") or {}).get(trail.get("S3BucketName", ""), {})


###############################################################################
# Checks
#
# Every one is a pure function: (stack: Dict, region: str) -> List[Finding].
# No boto3, no network, no credentials, no clock. That is what lets
# tests/test_checks.py run 47 assertions in under a second with no AWS account.
###############################################################################


def check_guardduty_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-001 — nothing is watching.

    A region with no GuardDuty detector, or a detector that exists and is
    disabled. The second is more common and more interesting: somebody turned
    it off during a cost review, or a detector was created by a stack that has
    since been destroyed, and the console still shows GuardDuty on the service
    list.

    Fires once per region, not once per resource, because there is one thing
    missing rather than N.
    """
    detectors = stack.get("guardduty_detectors") or []
    enabled = [d for d in detectors if str(d.get("Status", "")).upper() == "ENABLED"]
    if enabled:
        return []

    disabled = [d.get("DetectorId") for d in detectors]
    return [
        Finding(
            check_id="SEC-001",
            severity="CRITICAL",
            resource_type="AWS::GuardDuty::Detector",
            resource_id=f"(region {region or 'unknown'})",
            title="GuardDuty is not enabled in this region",
            detail=(
                f"No enabled GuardDuty detector in {region or 'this region'}"
                + (f"; {len(disabled)} detector(s) exist but are disabled: {disabled}"
                   if disabled else " and no detector exists at all")
                + ". Nothing is analysing CloudTrail, VPC flow logs or DNS queries "
                "here, which means nothing downstream — Security Hub, the "
                "responder, the notification topic — has anything to act on. Every "
                "other control in this account is unaffected and every one of them "
                "is now the only thing you have."
            ),
            remediation=(
                "Enable it: `aws guardduty create-detector --enable`. Then do the "
                "thing everyone skips and enable it in EVERY region, including the "
                "ones you do not use — an attacker with credentials will happily "
                "operate in ap-south-1, and a region with no detector is a region "
                "with no evidence. There is a 30-day free trial per account per "
                "region; set a budget alarm for day 31 rather than discovering the "
                "steady-state cost on an invoice."
            ),
            evidence={"detectors": detectors, "enabled_count": 0},
            region=region,
        )
    ]


def check_security_hub_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-002 — findings with nowhere to land.

    Security Hub disabled, or enabled with no standards subscribed. The second
    is the sneakier half: the service is on, the console renders, and it is
    doing nothing but forwarding other services' findings. Nobody is running
    compliance checks against anything.

    Fires once per region.
    """
    if not stack.get("securityhub_enabled"):
        return [
            Finding(
                check_id="SEC-002",
                severity="HIGH",
                resource_type="AWS::SecurityHub::Hub",
                resource_id=f"(region {region or 'unknown'})",
                title="Security Hub is not enabled",
                detail=(
                    f"Security Hub is not enabled in {region or 'this region'}. "
                    "GuardDuty, Inspector and Macie findings have no aggregation "
                    "point, no compliance standard is being evaluated, and there is "
                    "no single place for anyone to look."
                ),
                remediation=(
                    "Enable it, then subscribe to ONE standard — "
                    "aws-foundational-security-best-practices. Enabling every "
                    "standard on day one produces several thousand failed controls "
                    "across overlapping sets and a compliance percentage nobody "
                    "will ever drive to zero, which teaches the whole team to "
                    "scroll past the dashboard. Budget ~$0.0010 per security check, "
                    "counted per control per resource per day."
                ),
                evidence={"securityhub_enabled": False},
                region=region,
            )
        ]

    ready = [
        s for s in (stack.get("securityhub_standards") or [])
        if str(s.get("StandardsStatus", "")).upper() in ("READY", "PENDING", "INCOMPLETE")
    ]
    if ready:
        return []

    return [
        Finding(
            check_id="SEC-002",
            severity="HIGH",
            resource_type="AWS::SecurityHub::Hub",
            resource_id=f"(region {region or 'unknown'})",
            title="Security Hub is enabled with no standards subscribed",
            detail=(
                "Security Hub is on and no standard is subscribed. It is "
                "forwarding other services' findings and evaluating nothing of its "
                "own. This is the state an account reaches when somebody enabled "
                "the service to satisfy a checklist and stopped there — the console "
                "renders, the service appears in the bill, and no control has ever "
                "been checked."
            ),
            remediation=(
                "Subscribe to aws-foundational-security-best-practices and nothing "
                "else to begin with. Work the failures down over a few weeks, "
                "suppressing the controls that genuinely do not apply WITH A "
                "WRITTEN REASON, and only then consider adding CIS or PCI."
            ),
            evidence={"standards": stack.get("securityhub_standards")},
            region=region,
        )
    ]


def check_stale_findings(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-003 — a backlog is not detection.

    Findings still in an active workflow state, older than the configured
    threshold. The most common failure of a detection programme is not missing
    detections. It is a backlog nobody triages, which is operationally
    indistinguishable from having no detection at all — and considerably more
    expensive, because you are paying for the detection.

    ONE finding for the region, with the count and the oldest in evidence. One
    finding per stale finding would produce a backlog of findings about the
    backlog, which is funny once and useless twice.

    THIS CHECK READS RUNTIME STATE, not configuration. It is silent
    immediately after `apply` and fires once the lab generates findings — which
    is the difference between Day 07's contract and Day 06's, where static and
    live were identical.
    """
    threshold = float(stack.get("stale_finding_age_days", 7))
    now = _now(stack)

    stale = []
    for finding in stack.get("guardduty_findings") or []:
        service = finding.get("Service") or {}
        if service.get("Archived"):
            continue
        workflow = (finding.get("Workflow") or {}).get("Status", "NEW")
        if str(workflow).upper() in ("RESOLVED", "SUPPRESSED"):
            continue
        age = _age_days(finding.get("UpdatedAt") or finding.get("CreatedAt"), now)
        if age is not None and age > threshold:
            stale.append({"id": finding.get("Id"), "type": finding.get("Type"), "age_days": round(age, 1)})

    if not stale:
        return []

    oldest = max(stale, key=lambda f: f["age_days"])
    return [
        Finding(
            check_id="SEC-003",
            severity="MEDIUM",
            resource_type="AWS::GuardDuty::Finding",
            resource_id=f"(region {region or 'unknown'})",
            title=f"{len(stale)} GuardDuty finding(s) untriaged for over {threshold:.0f} days",
            detail=(
                f"{len(stale)} finding(s) are still in an active workflow state and "
                f"have not been updated for more than {threshold:.0f} days. The "
                f"oldest is {oldest['type']} at {oldest['age_days']} days. A "
                f"detection nobody has looked at is not a detection; it is a "
                f"subscription."
            ),
            remediation=(
                "Triage them, and then fix the reason there was a backlog rather "
                "than just clearing it. Usually one of three things: the finding "
                "types are noisy and need suppression rules with written reasons; "
                "nobody owns the queue; or there is no route from a finding to a "
                "person. Archive what you have decided about — "
                "`aws guardduty archive-findings` — so the number means something "
                "next week."
            ),
            evidence={"stale_count": len(stale), "threshold_days": threshold, "oldest": oldest},
            region=region,
        )
    ]


def check_publishing_frequency(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-004 — updates arrive after the incident.

    `finding_publishing_frequency` controls how often GuardDuty publishes
    UPDATES to existing findings. It does not delay the first notification of a
    NEW finding — those arrive in about five minutes regardless — which is why
    this is LOW rather than HIGH and why people reasonably ignore it.

    It still matters, because "this is now happening on four more instances" is
    exactly the update you want inside fifteen minutes rather than six hours,
    and by six hours the incident is over one way or another.

    FIFTEEN_MINUTES costs nothing extra. There is no argument for the default.
    """
    findings: List[Finding] = []
    for detector in stack.get("guardduty_detectors") or []:
        frequency = str(detector.get("FindingPublishingFrequency", "SIX_HOURS"))
        if frequency == "FIFTEEN_MINUTES":
            continue
        findings.append(
            Finding(
                check_id="SEC-004",
                severity="LOW",
                resource_type="AWS::GuardDuty::Detector",
                resource_id=str(detector.get("DetectorId", "unknown")),
                title="GuardDuty finding updates are published slowly",
                detail=(
                    f"Detector {detector.get('DetectorId')} publishes finding "
                    f"updates every {frequency}. New findings still arrive in about "
                    f"five minutes, but updates — including 'this is now occurring "
                    f"on additional resources' — wait for the next window. On a "
                    f"{frequency} cadence that update lands after most incidents "
                    f"have already been decided."
                ),
                remediation=(
                    f"`aws guardduty update-detector --detector-id "
                    f"{detector.get('DetectorId')} --finding-publishing-frequency "
                    f"FIFTEEN_MINUTES`. It costs nothing extra."
                ),
                evidence={"FindingPublishingFrequency": frequency},
                region=region,
            )
        )
    return findings


def check_response_trigger_style(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-005 — the response triggers on severity rather than on finding type.

    THE MOST IMPORTANT CHECK IN THIS FILE.

    GuardDuty severity scores IMPACT — how bad this would be if it is real. It
    does not score CONFIDENCE. Those are different questions and conflating
    them is the root of almost every bad automated-response design.

    A HIGH finding is routinely your own penetration test, a vulnerability
    scanner your security team runs on a schedule, a researcher probing a
    public endpoint, or a developer who ran something odd from a coffee shop.
    All four produce the same severity as a genuine compromise. `severity >= 7`
    contains all of them — four outages you caused, for one real detection.

    What correlates with confidence is the finding TYPE.
    `CryptoCurrencyMining:EC2/BitcoinTool.B!DNS` is rarely a false positive.
    `UnauthorizedAccess:EC2/SSHBruteForce` against anything internet-facing is
    near-constant background noise.

    So automation belongs on an allow-list of specific types you have decided
    about individually, and adding an entry should get the same review as a
    deploy.
    """
    findings: List[Finding] = []
    for function in responder_functions(stack):
        env = _env(function)
        threshold = env.get(ENV_SEVERITY_THRESHOLD)
        try:
            allow_list = json.loads(env.get(ENV_ALLOW_LIST, "[]"))
        except ValueError:
            allow_list = []

        if not threshold and allow_list:
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="SEC-005",
                severity="CRITICAL",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Automated response triggers on severity, not on finding type",
                detail=(
                    f"{name} can change this account in response to a finding, and "
                    + (
                        f"decides using {ENV_SEVERITY_THRESHOLD}={threshold}"
                        if threshold
                        else f"has an empty {ENV_ALLOW_LIST} and no other stated rule"
                    )
                    + ". GuardDuty severity scores IMPACT, not CONFIDENCE — a "
                    "severity-7 finding is as likely to be your own penetration "
                    "test, your own scanner or a researcher as it is to be a "
                    "compromise. This rule cannot tell them apart, so every one of "
                    "them gets contained."
                ),
                remediation=(
                    f"Replace the threshold with an allow-list of finding TYPES in "
                    f"{ENV_ALLOW_LIST}. Start with the types that are rarely false "
                    f"positives — cryptomining, known-malicious-IP callers, "
                    f"command-and-control DNS — and add entries one at a time, each "
                    f"with a written reason. Run in dry-run for a week first; that "
                    f"week always changes the list."
                ),
                evidence={
                    "FunctionName": name,
                    ENV_SEVERITY_THRESHOLD: threshold,
                    "allow_list_size": len(allow_list),
                    "containment_actions": function.get("_containment_actions"),
                },
                region=region,
            )
        )
    return findings


def check_trail_coverage(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-006 — the trail does not cover where the attacker will be.

    A single-region trail, or one without global service events.

    An attacker with credentials does not politely operate in your primary
    region. Creating an instance in ap-south-1 is exactly as easy for them as
    in us-east-1, and a single-region trail records none of it. Global service
    events are the other half: IAM, STS and CloudFront emit in us-east-1
    regardless of where you are, so a trail outside us-east-1 without them
    records no IAM activity at all — which is the activity you most want.

    Multi-region costs nothing extra: the FIRST trail delivering management
    events is free per account, including multi-region.
    """
    findings: List[Finding] = []
    for trail in stack.get("trails") or []:
        problems = []
        if not trail.get("IsMultiRegionTrail"):
            problems.append("it is single-region")
        if not trail.get("IncludeGlobalServiceEvents"):
            problems.append("it excludes global service events, so no IAM or STS activity")
        if not problems:
            continue

        name = trail.get("Name", "unknown")
        findings.append(
            Finding(
                check_id="SEC-006",
                severity="HIGH",
                resource_type="AWS::CloudTrail::Trail",
                resource_id=name,
                title="Trail does not cover the whole account",
                detail=(
                    f"Trail {name} has gaps: {'; '.join(problems)}. Activity in "
                    f"regions this trail does not watch is not recorded anywhere, "
                    f"and 'we have CloudTrail' will be said in the postmortem by "
                    f"somebody who believed it."
                ),
                remediation=(
                    f"`aws cloudtrail update-trail --name {name} --is-multi-region-trail "
                    f"--include-global-service-events`, or set is_multi_region_trail "
                    f"and include_global_service_events in the Terraform so it does "
                    f"not regress. The first trail delivering management events is "
                    f"free per account, multi-region included — there is no cost "
                    f"argument for a partial trail."
                ),
                evidence={
                    "Name": name,
                    "IsMultiRegionTrail": trail.get("IsMultiRegionTrail"),
                    "IncludeGlobalServiceEvents": trail.get("IncludeGlobalServiceEvents"),
                    "HomeRegion": trail.get("HomeRegion"),
                },
                region=region,
            )
        )
    return findings


def check_trail_validation(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-007 — logging, not evidence.

    Log file validation makes CloudTrail sign an hourly digest listing the
    files delivered and their hashes, so `aws cloudtrail validate-logs` can
    prove no file was modified or deleted since delivery.

    That distinction matters exactly once, and then completely: during an
    incident where the question is whether an attacker with S3 write access
    edited the trail to remove their own activity. Without validation you
    cannot answer it. With it you can, and the answer holds up.

    It is free. There is no argument for turning it off.
    """
    findings: List[Finding] = []
    for trail in stack.get("trails") or []:
        if trail.get("LogFileValidationEnabled"):
            continue
        name = trail.get("Name", "unknown")
        findings.append(
            Finding(
                check_id="SEC-007",
                severity="HIGH",
                resource_type="AWS::CloudTrail::Trail",
                resource_id=name,
                title="Trail has no log file validation",
                detail=(
                    f"Trail {name} does not sign its log files, so there is no way "
                    f"to prove any of them is intact. Anyone who can write to "
                    f"{trail.get('S3BucketName')} can replace a log file with a "
                    f"shorter one and nothing in AWS will ever notice. This is "
                    f"logging; it is not evidence."
                ),
                remediation=(
                    f"`aws cloudtrail update-trail --name {name} "
                    f"--enable-log-file-validation`. It is free. Then RUN the "
                    f"validation once, now, so you know the command works before "
                    f"the day you need it: `aws cloudtrail validate-logs "
                    f"--trail-arn <arn> --start-time <iso8601>`."
                ),
                evidence={"Name": name, "LogFileValidationEnabled": False,
                          "S3BucketName": trail.get("S3BucketName")},
                region=region,
            )
        )
    return findings


def check_responder_role_scope(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-008 — the responder can destroy the evidence of what it did.

    A principal that can change your account without a human is the single
    most valuable thing in that account to compromise — more valuable than most
    human roles, because it acts at machine speed and its actions look normal
    in CloudTrail.

    Three things it must never be able to do:
      1. Stop or modify the trail. An attacker who reaches this role should not
         also be able to delete the record of having done so.
      2. Modify its own role or policy. Otherwise every other scope is
         advisory, one line of privilege escalation away.
      3. Change its own kill switch. The brake must not be reachable by the
         thing it brakes.

    THIS CHECK READS DENY STATEMENTS, and that is what makes it more than a
    wildcard grep. An explicit Deny cannot be overridden by any Allow in any
    policy, ever — so a role that Allows `ec2:*` and Denies the four
    destructive actions is correctly configured, and a check that flags it
    trains people to ignore the check.
    """
    findings: List[Finding] = []
    role_policies = stack.get("role_policies") or {}
    seen: Set[str] = set()

    for function in responder_functions(stack):
        role_name = function.get("_role_name", "")
        if role_name in seen:
            continue

        allowed: List[str] = []
        denied: Set[str] = set()
        for document in role_policies.get(role_name, []):
            allowed += policy_allows(document, set(TAMPER_ACTIONS))
            denied |= policy_denies(document, set(TAMPER_ACTIONS))

        undenied = sorted(set(allowed) - denied)
        if not undenied:
            continue

        seen.add(role_name)
        capabilities = sorted({TAMPER_ACTIONS[a] for a in undenied if a in TAMPER_ACTIONS})
        findings.append(
            Finding(
                check_id="SEC-008",
                severity="CRITICAL",
                resource_type="AWS::IAM::Role",
                resource_id=role_name,
                title="Automated responder can tamper with the trail or with itself",
                detail=(
                    f"Role {role_name}, used by {function.get('FunctionName')}, is "
                    f"allowed {undenied} with no matching Deny. In practice that "
                    f"means it can: {'; '.join(capabilities) if capabilities else 'escalate'}. "
                    f"An automated responder is a principal that changes your "
                    f"account without a human — it is the most valuable thing here "
                    f"to compromise, and this one can erase the record of its own "
                    f"actions."
                ),
                remediation=(
                    "Scope the Allow statements to exactly what containment needs — "
                    "usually ec2:ModifyInstanceAttribute and ec2:CreateTags on "
                    "instances — and add EXPLICIT DENY statements for "
                    "cloudtrail:StopLogging/DeleteTrail/UpdateTrail, for iam:*, and "
                    "for writing the kill-switch parameter. Denies, not omissions: "
                    "an omission is one careless policy attachment away from not "
                    "being an omission, and an explicit Deny cannot be overridden."
                ),
                evidence={
                    "role": role_name,
                    "FunctionName": function.get("FunctionName"),
                    "allowed_tamper_actions": undenied,
                    "explicitly_denied": sorted(denied),
                },
                region=region,
            )
        )
    return findings


def check_trail_bucket_protection(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-009 — the evidence has no rollback and may be readable.

    A trail bucket without versioning, or without a public access block.

    Versioning is not a nice-to-have on a trail bucket, it is the rollback
    path. An attacker with s3:PutObject can otherwise replace a log file with a
    shorter one, and with no previous version there is nothing to compare
    against — log file validation tells you a file changed, versioning is what
    lets you see what it said before.

    The public access block is the other half, and its absence is worse than it
    sounds: a publicly readable trail bucket is a complete map of your
    account's control plane, including every principal that has touched it.
    """
    findings: List[Finding] = []
    for trail in stack.get("trails") or []:
        bucket_name = trail.get("S3BucketName", "")
        bucket = trail_bucket_for(stack, trail)
        if not bucket_name:
            continue

        problems = []
        versioning = (bucket.get("Versioning") or {}).get("Status")
        if versioning != "Enabled":
            problems.append(f"versioning is {versioning or 'not enabled'}")

        pab = bucket.get("PublicAccessBlock") or {}
        missing_blocks = [
            key for key in ("BlockPublicAcls", "BlockPublicPolicy",
                            "IgnorePublicAcls", "RestrictPublicBuckets")
            if not pab.get(key)
        ]
        if missing_blocks:
            problems.append(f"public access block incomplete: {missing_blocks}")

        if not problems:
            continue

        findings.append(
            Finding(
                check_id="SEC-009",
                severity="HIGH",
                resource_type="AWS::S3::Bucket",
                resource_id=bucket_name,
                title="Trail bucket is not protected as evidence",
                detail=(
                    f"{bucket_name} receives CloudTrail deliveries for trail "
                    f"{trail.get('Name')} and {'; '.join(problems)}. Without "
                    f"versioning there is no previous copy of an overwritten log "
                    f"file; without a complete public access block the bucket is one "
                    f"policy edit away from publishing your account's control plane."
                ),
                remediation=(
                    f"`aws s3api put-bucket-versioning --bucket {bucket_name} "
                    f"--versioning-configuration Status=Enabled` and "
                    f"`aws s3api put-public-access-block --bucket {bucket_name} "
                    f"--public-access-block-configuration "
                    f"BlockPublicAcls=true,BlockPublicPolicy=true,"
                    f"IgnorePublicAcls=true,RestrictPublicBuckets=true`. Then add a "
                    f"lifecycle rule expiring NONCURRENT versions, or versioning "
                    f"quietly retains every overwrite forever."
                ),
                evidence={
                    "bucket": bucket_name,
                    "trail": trail.get("Name"),
                    "versioning": versioning,
                    "public_access_block": pab,
                },
                region=region,
            )
        )
    return findings


def check_secret_rotation_configured(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-010 — a secret nobody came back to.

    Rotation is not configured at all. Created once, during a migration, by
    somebody who meant to return to it. Every audit finds several and every
    team is surprised by the count.

    Distinct from SEC-011, deliberately: this is "nobody decided", that is "it
    says it rotates and it does not". Different remediations, different owners,
    and a secret with no rotation produces ONE finding rather than two.
    """
    findings: List[Finding] = []
    for secret in stack.get("secrets") or []:
        if secret.get("RotationEnabled"):
            continue
        name = secret.get("Name", secret.get("ARN", "unknown"))
        findings.append(
            Finding(
                check_id="SEC-010",
                severity="MEDIUM",
                resource_type="AWS::SecretsManager::Secret",
                resource_id=str(name),
                title="Secret has no rotation configured",
                detail=(
                    f"{name} has no rotation schedule. Whatever credential it holds "
                    f"has been the same value since it was created, and every copy "
                    f"of it that has ever leaked — into a log group, a CI variable, "
                    f"a laptop — is still valid."
                ),
                remediation=(
                    "Decide, and record the decision. Either configure rotation with "
                    "a Lambda that genuinely applies the new value downstream, or "
                    "document why this secret cannot rotate and what compensates for "
                    "it. 'We will do it next quarter' has been the answer for several "
                    "quarters in most accounts. Note that rotating does not help if "
                    "the old value is sitting in an unretained log group — check "
                    "both."
                ),
                evidence={"Name": name, "RotationEnabled": False},
                region=region,
            )
        )
    return findings


def check_secret_rotation_ran(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-011 — rotation is configured and has never actually run.

    THE FAILURE MODE THAT LOOKS LIKE SUCCESS.

    Rotation is configured, the rotation Lambda throws on every invocation, and
    the console shows a schedule with a next-rotation date that keeps moving.
    Nothing is red. `RotationEnabled` is `true`. And the credential has not
    changed since March.

    The tell is `LastRotatedDate`: absent, or older than
    `AutomaticallyAfterDays` implies it should be. That is the only field that
    means rotation ran.

    This is the check most likely to fire in a real account that believes it is
    fine — and it fires immediately after `terraform apply` too, because a
    freshly created secret with `rotate_immediately = false` has genuinely
    never rotated. That is correct rather than a false positive: the schedule
    exists and has not yet been proven.
    """
    findings: List[Finding] = []
    now = _now(stack)

    for secret in stack.get("secrets") or []:
        if not secret.get("RotationEnabled"):
            continue  # SEC-010 owns it

        interval = (secret.get("RotationRules") or {}).get("AutomaticallyAfterDays")
        last = secret.get("LastRotatedDate")
        age = _age_days(last, now)
        name = secret.get("Name", secret.get("ARN", "unknown"))

        never = last is None or age is None
        overdue = (
            age is not None and interval is not None and age > float(interval) * 1.5
        )
        if not never and not overdue:
            continue

        findings.append(
            Finding(
                check_id="SEC-011",
                severity="HIGH",
                resource_type="AWS::SecretsManager::Secret",
                resource_id=str(name),
                title="Rotation is configured but has not run",
                detail=(
                    f"{name} has RotationEnabled=true with an interval of "
                    f"{interval} day(s), and "
                    + ("has never rotated (LastRotatedDate is absent)."
                       if never else
                       f"last rotated {age:.0f} days ago, well past that interval.")
                    + " The schedule exists, the console is green, and the "
                    "credential is unchanged. A secret that reports rotation it is "
                    "not performing is worse than one that reports none, because "
                    "the dashboard says it is handled."
                ),
                remediation=(
                    f"Force one and watch it: `aws secretsmanager rotate-secret "
                    f"--secret-id {name}`, then read the rotation Lambda's log group "
                    f"for the four steps — createSecret, setSecret, testSecret, "
                    f"finishSecret. The usual cause is setSecret stubbed out, which "
                    f"makes every rotation 'succeed' while the downstream service "
                    f"keeps the old credential. Fix that before you trust the "
                    f"schedule."
                ),
                evidence={
                    "Name": name,
                    "LastRotatedDate": last,
                    "AutomaticallyAfterDays": interval,
                    "age_days": None if age is None else round(age, 1),
                },
                region=region,
            )
        )
    return findings


def check_containment_reversible(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-012 — the automation is configured to do something it cannot undo.

    Every action automation can take on a probabilistic finding, with nobody
    watching, must be undoable by one documented command. Isolate, do not
    terminate. Detach, do not delete. Snapshot first.

    Not because destructive actions are never correct — because they are
    decisions a human makes on Monday, with the finding in front of them and
    somebody to ask, not decisions a Lambda makes at 03:00.

    THIS FIRES ON CONFIGURED INTENT, NOT ON OBSERVED BEHAVIOUR. The responder
    in this lab refuses an unrecognised containment mode and changes nothing,
    which is correct and does not make the configuration acceptable — the next
    person to "fix" the responder will implement what the configuration asks
    for.
    """
    findings: List[Finding] = []
    for function in responder_functions(stack):
        mode = str(_env(function).get(ENV_CONTAINMENT_MODE, "")).strip().lower()
        if mode in REVERSIBLE_MODES:
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="SEC-012",
                severity="CRITICAL",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Automated containment is configured to be irreversible",
                detail=(
                    f"{name} is configured with {ENV_CONTAINMENT_MODE}="
                    f"{mode or '(unset)'}, which is not one of the reversible modes "
                    f"{sorted(REVERSIBLE_MODES)}. Its role additionally allows "
                    f"{function.get('_containment_actions')}. An automated action "
                    f"taken on a probabilistic detection at 03:00, that a human "
                    f"cannot undo with one command, is not containment — it is an "
                    f"outage with a security justification."
                ),
                remediation=(
                    "Set it to isolate — replace the instance's security groups with "
                    "a pre-created quarantine group, RECORDING the previous groups "
                    "so the rollback command can be printed in the notification. "
                    "Start in dry-run for a week and read what it would have done. "
                    "If somebody genuinely needs terminate, that belongs behind a "
                    "human approval step in a state machine, not in an environment "
                    "variable."
                ),
                evidence={
                    "FunctionName": name,
                    ENV_CONTAINMENT_MODE: mode or None,
                    "containment_actions": function.get("_containment_actions"),
                },
                region=region,
            )
        )
    return findings


def check_access_key_age(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-013 — a long-lived, copyable credential.

    An active IAM access key older than the configured threshold.

    The problem is not the age and it is not the permissions. It is that a
    long-lived key is a string that can be copied, and once copied there is
    nothing about its use that looks different from legitimate use. Every other
    credential in a modern AWS account — instance profiles, IRSA, OIDC
    federation from CI — is short-lived and bound to a workload identity. This
    one lives in somebody's environment.

    Age is reported because age is the only thing measurable from outside. A
    30-day-old key that has leaked is worse than a 400-day-old one that has
    not, and no check can tell you which you have.

    A NOTE ON WHY THIS MAY BE SILENT. This check finds nothing on a
    freshly-applied stack, because the key it exists to find is hours old.
    NOTHING HAS TO CHANGE for that to stop being true — in ninety-one days the
    same unchanged account fails the same unchanged check. That makes this the
    clearest argument in the repo for running an auditor on a schedule rather
    than at merge time.
    """
    threshold = float(stack.get("max_access_key_age_days", 90))
    now = _now(stack)
    findings: List[Finding] = []

    for key in stack.get("access_keys") or []:
        if str(key.get("Status", "")).upper() != "ACTIVE":
            continue
        age = _age_days(key.get("CreateDate"), now)
        if age is None or age <= threshold:
            continue

        key_id = key.get("AccessKeyId", "unknown")
        findings.append(
            Finding(
                check_id="SEC-013",
                severity="MEDIUM",
                resource_type="AWS::IAM::AccessKey",
                resource_id=f"{key.get('UserName', '?')}/{key_id}",
                title=f"Active access key is {age:.0f} days old",
                detail=(
                    f"Access key {key_id} belonging to {key.get('UserName')} is "
                    f"active and {age:.0f} days old, past the {threshold:.0f}-day "
                    f"threshold. A long-lived access key is a copyable string, and "
                    f"once copied its use is indistinguishable from legitimate use. "
                    f"Age is what is measurable from outside; it is not the actual "
                    f"problem."
                ),
                remediation=(
                    "Replace the credential, not the key. If it is a build server, "
                    "use OIDC federation from your CI provider; if it is a workload "
                    "in AWS, use an instance profile or IRSA; if it is a human, use "
                    "identity-centre credentials. Rotating the key resets the clock "
                    "and changes nothing about the failure mode. When you do delete "
                    "it, deactivate first and wait — deleting immediately is how you "
                    "find out what was using it, during an outage."
                ),
                evidence={
                    "AccessKeyId": key_id,
                    "UserName": key.get("UserName"),
                    "CreateDate": key.get("CreateDate"),
                    "age_days": round(age, 1),
                    "threshold_days": threshold,
                },
                region=region,
            )
        )
    return findings


def check_kill_switch(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-014 — stopping the automation requires a deploy.

    A responder with no runtime disable.

    An apply-time toggle is the right shape for a considered decision and
    useless at 03:00, when the automation is making things worse and somebody
    needs it to stop NOW. A pull request is not a brake.

    The kill switch has to be read at RUNTIME, on every invocation, from
    somewhere a human can change with one command — an SSM parameter, a feature
    flag, a DynamoDB item. And it has to fail safe: if the responder cannot
    read the switch, it must take no action, because automation that keeps
    containing production while its own control plane is broken is worse than
    automation that stops.

    Scoped to functions that can actually take an action. A read-only
    enrichment Lambda does not need a brake, and flagging it would train people
    to ignore the check.
    """
    findings: List[Finding] = []
    for function in responder_functions(stack):
        if _env(function).get(ENV_KILL_SWITCH):
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="SEC-014",
                severity="HIGH",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Automated responder has no runtime kill switch",
                detail=(
                    f"{name} can change this account in response to a finding "
                    f"({function.get('_containment_actions')}) and has no "
                    f"{ENV_KILL_SWITCH}. The only way to stop it is to disable the "
                    f"EventBridge rule or redeploy — both of which need a pipeline, "
                    f"and neither of which is available to the person holding the "
                    f"pager at 03:00 on a Sunday."
                ),
                remediation=(
                    "Add an SSM parameter the function reads on EVERY invocation, "
                    "with no caching, and have it fail safe when the parameter is "
                    "unreadable. Then flip it once, deliberately, and confirm the "
                    "responder stops — a kill switch nobody has ever flipped is a "
                    "hypothesis. Deny the responder's own role permission to write "
                    "the parameter, or the brake is reachable by the thing it "
                    "brakes."
                ),
                evidence={
                    "FunctionName": name,
                    ENV_KILL_SWITCH: None,
                    "containment_actions": function.get("_containment_actions"),
                },
                region=region,
            )
        )
    return findings


def check_response_rule_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-015 — automation everybody believes is running.

    An EventBridge rule matching GuardDuty findings, created or left in the
    DISABLED state.

    This is the more insidious half of the automation story. It looks
    completely normal in the console, produces no errors, costs nothing, and
    everybody believes the responder is running. It is Day 04's CMP-014 wearing
    a security hat, and the only way to find it is to look — or to run a check
    that looks for you.

    Disabling deliberately is legitimate. Disabling deliberately and not
    telling anyone is the finding.
    """
    findings: List[Finding] = []
    for rule in guardduty_rules(stack):
        if str(rule.get("State", "ENABLED")).upper() != "DISABLED":
            continue
        name = rule.get("Name", "unknown")
        targets = [t.get("Arn") for t in (rule.get("Targets") or [])]
        findings.append(
            Finding(
                check_id="SEC-015",
                severity="MEDIUM",
                resource_type="AWS::Events::Rule",
                resource_id=name,
                title="GuardDuty response rule is DISABLED",
                detail=(
                    f"Rule {name} matches GuardDuty findings and is DISABLED. Its "
                    f"{len(targets)} target(s) — {targets} — have never been invoked "
                    f"and will not be. Nothing about this state is visibly different "
                    f"from a working rule unless you look at exactly this field."
                ),
                remediation=(
                    f"`aws events enable-rule --name {name}` if it should be running. "
                    f"If it is disabled on purpose, say so where a human will read "
                    f"it — the rule description is the right place — and set an "
                    f"expiry date on the decision. A rule that has been 'temporarily' "
                    f"disabled since a deploy in March is the shape this always takes."
                ),
                evidence={"Name": name, "State": rule.get("State"), "targets": targets},
                region=region,
            )
        )
    return findings


def check_response_target_dlq(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """SEC-016 — a detection that vanished.

    An EventBridge target on a GuardDuty rule with no dead-letter queue.

    An asynchronous invocation that fails its retries and disappears is
    indistinguishable from a finding that was correctly ignored. Both produce
    silence. On Day 04 that argument was about compliance reports; here it is
    about a detection nobody ever saw, in a system whose entire purpose is not
    missing things.

    Reports on the TARGET, not the rule, because each target is a separate path
    a detection can vanish down. One rule with three targets and no DLQ is
    three findings and three things to fix.
    """
    findings: List[Finding] = []
    for rule in guardduty_rules(stack):
        for target in rule.get("Targets") or []:
            if (target.get("DeadLetterConfig") or {}).get("Arn"):
                continue
            rule_name = rule.get("Name", "unknown")
            target_id = target.get("Id", "unknown")
            findings.append(
                Finding(
                    check_id="SEC-016",
                    severity="MEDIUM",
                    resource_type="AWS::Events::Target",
                    resource_id=f"{rule_name}/{target_id}",
                    title="Response target has no dead-letter queue",
                    detail=(
                        f"Target {target_id} on rule {rule_name} ({target.get('Arn')}) "
                        f"has no dead-letter queue"
                        + ("" if target.get("RetryPolicy") else " and no retry policy")
                        + ". A finding that fails to reach it is dropped silently, "
                        "and a dropped detection looks exactly like a detection that "
                        "was correctly ignored."
                    ),
                    remediation=(
                        "Attach an SQS dead-letter queue to the target and a retry "
                        "policy, then put an alarm on the queue's depth — a DLQ "
                        "nobody watches is a slightly better-organised silence. Give "
                        "the queue a resource policy allowing events.amazonaws.com "
                        "with an aws:SourceArn condition, or the DLQ itself fails."
                    ),
                    evidence={
                        "rule": rule_name,
                        "target_id": target_id,
                        "target_arn": target.get("Arn"),
                        "DeadLetterConfig": target.get("DeadLetterConfig"),
                        "RetryPolicy": target.get("RetryPolicy"),
                    },
                    region=region,
                )
            )
    return findings


###############################################################################
# Check registry
###############################################################################

CHECKS = [
    ("SEC-001", check_guardduty_enabled),
    ("SEC-002", check_security_hub_enabled),
    ("SEC-003", check_stale_findings),
    ("SEC-004", check_publishing_frequency),
    ("SEC-005", check_response_trigger_style),
    ("SEC-006", check_trail_coverage),
    ("SEC-007", check_trail_validation),
    ("SEC-008", check_responder_role_scope),
    ("SEC-009", check_trail_bucket_protection),
    ("SEC-010", check_secret_rotation_configured),
    ("SEC-011", check_secret_rotation_ran),
    ("SEC-012", check_containment_reversible),
    ("SEC-013", check_access_key_age),
    ("SEC-014", check_kill_switch),
    ("SEC-015", check_response_rule_enabled),
    ("SEC-016", check_response_target_dlq),
]

LIVE_CHECKS = [check_id for check_id, _ in CHECKS]

# Checks that read RUNTIME state rather than configuration. Listed separately
# because their answer depends on when you ran the tool, and that is worth
# saying out loud rather than discovering from a confusing diff.
RUNTIME_CHECKS = ["SEC-003", "SEC-011", "SEC-013"]


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.

    Expect zero against this lab's stack with create_insecure_examples = true.
    Four CRITICAL findings are 100 points on their own. That is the intended
    shock. Set create_insecure_examples = false, let rotation run once, and the
    same tool with the same checks returns 100/100.
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
    w(colour("  CLOUD SECURITY AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 07 · Enterprise Cloud Security\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('detectors', 0)} detector(s) · "
        f"{stats.get('findings_open', 0)} open finding(s) · "
        f"{stats.get('trails', 0)} trail(s) · "
        f"{stats.get('secrets', 0)} secret(s) · "
        f"{stats.get('access_keys', 0)} access key(s) · "
        f"{stats.get('functions', 0)} function(s) · "
        f"{stats.get('rules', 0)} response rule(s)\n\n"
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
        "audit": "sec_audit",
        "day": "07",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
        # Named in the payload because a consumer diffing two runs needs to
        # know which checks could legitimately change without anybody touching
        # the account.
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


class SecurityAuditor:
    """Collects one normalised snapshot of the region, then runs pure checks.

    Everything that touches AWS happens in collect(); everything that decides
    anything happens in a function that takes a dict. That is why
    tests/test_checks.py needs no credentials — and it is the structural idea
    worth stealing from all five of these tools.
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        prefix: Optional[str] = None,
        max_access_key_age_days: int = 90,
        stale_finding_age_days: int = 7,
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.prefix = prefix
        self.max_access_key_age_days = max_access_key_age_days
        self.stale_finding_age_days = stale_finding_age_days
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stack: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "detectors": 0,
            "findings_open": 0,
            "trails": 0,
            "secrets": 0,
            "access_keys": 0,
            "functions": 0,
            "rules": 0,
        }

        self.session: Any = None
        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.guardduty = self.session.client("guardduty")
            self.securityhub = self.session.client("securityhub")
            self.cloudtrail = self.session.client("cloudtrail")
            self.s3 = self.session.client("s3")
            self.secretsmanager = self.session.client("secretsmanager")
            self.iam = self.session.client("iam")
            self.lambda_ = self.session.client("lambda")
            self.events = self.session.client("events")
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
            "InvalidAccessException",
            "AccessDeniedException",
            "NoSuchPublicAccessBlockConfiguration",
            "NoSuchBucketPolicy",
            "NoSuchEntity",
            "BadRequestException",
        ):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    def _in_scope(self, name: str) -> bool:
        return not self.prefix or str(name).startswith(self.prefix)

    def collect(self) -> Dict[str, Any]:
        stack: Dict[str, Any] = {
            "region": self.region,
            # The clock, set ONCE, here. See _now().
            "now": datetime.now(timezone.utc),
            "max_access_key_age_days": self.max_access_key_age_days,
            "stale_finding_age_days": self.stale_finding_age_days,
            "guardduty_detectors": [],
            "guardduty_findings": [],
            "securityhub_enabled": False,
            "securityhub_standards": [],
            "trails": [],
            "buckets": {},
            "secrets": [],
            "access_keys": [],
            "lambda_functions": [],
            "role_policies": {},
            "event_rules": [],
        }
        if not self.session:
            return stack

        self.log("  · guardduty detectors and findings")
        for detector_id in paginate(self.guardduty, "list_detectors", "DetectorIds"):
            try:
                detail = self.guardduty.get_detector(DetectorId=detector_id)
                detail["DetectorId"] = detector_id
                stack["guardduty_detectors"].append(detail)
            except ClientError as exc:
                self._swallow("get_detector", detector_id, exc)
                continue

            finding_ids = paginate(
                self.guardduty, "list_findings", "FindingIds", DetectorId=detector_id
            )
            for batch in chunked(finding_ids, 50):
                try:
                    stack["guardduty_findings"].extend(
                        self.guardduty.get_findings(
                            DetectorId=detector_id, FindingIds=batch
                        ).get("Findings", [])
                    )
                except ClientError as exc:
                    self._swallow("get_findings", detector_id, exc)

        self.log("  · security hub")
        try:
            self.securityhub.describe_hub()
            stack["securityhub_enabled"] = True
            stack["securityhub_standards"] = paginate(
                self.securityhub, "get_enabled_standards", "StandardsSubscriptions"
            )
        except ClientError as exc:
            self._swallow("describe_hub", "account", exc)

        self.log("  · cloudtrail trails and their buckets")
        for trail in self.cloudtrail.describe_trails(includeShadowTrails=False).get(
            "trailList", []
        ):
            if not self._in_scope(trail.get("Name", "")):
                continue
            stack["trails"].append(trail)
            bucket = trail.get("S3BucketName")
            if bucket and bucket not in stack["buckets"]:
                stack["buckets"][bucket] = self._describe_bucket(bucket)

        self.log("  · secrets")
        for secret in paginate(self.secretsmanager, "list_secrets", "SecretList"):
            if not self._in_scope(secret.get("Name", "")):
                continue
            stack["secrets"].append(secret)

        self.log("  · iam access keys")
        for user in paginate(self.iam, "list_users", "Users"):
            if not self._in_scope(user.get("UserName", "")):
                continue
            for key in paginate(
                self.iam, "list_access_keys", "AccessKeyMetadata",
                UserName=user["UserName"],
            ):
                stack["access_keys"].append(key)

        self.log("  · lambda functions and their role policies")
        for function in paginate(self.lambda_, "list_functions", "Functions"):
            if not self._in_scope(function.get("FunctionName", "")):
                continue
            try:
                stack["lambda_functions"].append(
                    self.lambda_.get_function_configuration(
                        FunctionName=function["FunctionName"]
                    )
                )
            except ClientError as exc:
                self._swallow("get_function_configuration", function["FunctionName"], exc)

        for function in stack["lambda_functions"]:
            role_name = (function.get("Role") or "").rsplit("/", 1)[-1]
            if role_name and role_name not in stack["role_policies"]:
                stack["role_policies"][role_name] = self._role_policies(role_name)

        self.log("  · eventbridge rules and targets")
        for rule in paginate(self.events, "list_rules", "Rules"):
            if not self._in_scope(rule.get("Name", "")):
                continue
            try:
                rule["Targets"] = paginate(
                    self.events, "list_targets_by_rule", "Targets", Rule=rule["Name"]
                )
            except ClientError as exc:
                self._swallow("list_targets_by_rule", rule["Name"], exc)
                rule["Targets"] = []
            stack["event_rules"].append(rule)

        self.stack = stack
        open_findings = [
            f for f in stack["guardduty_findings"]
            if not (f.get("Service") or {}).get("Archived")
        ]
        self.stats.update(
            {
                "detectors": len(stack["guardduty_detectors"]),
                "findings_open": len(open_findings),
                "trails": len(stack["trails"]),
                "secrets": len(stack["secrets"]),
                "access_keys": len(stack["access_keys"]),
                "functions": len(stack["lambda_functions"]),
                "rules": len(stack["event_rules"]),
            }
        )
        return stack

    def _describe_bucket(self, name: str) -> Dict[str, Any]:
        bucket: Dict[str, Any] = {"Name": name}
        try:
            bucket["Versioning"] = self.s3.get_bucket_versioning(Bucket=name)
        except ClientError as exc:
            self._swallow("get_bucket_versioning", name, exc)
            bucket["Versioning"] = {}
        try:
            bucket["PublicAccessBlock"] = self.s3.get_public_access_block(
                Bucket=name
            ).get("PublicAccessBlockConfiguration", {})
        except ClientError as exc:
            self._swallow("get_public_access_block", name, exc)
            bucket["PublicAccessBlock"] = {}
        return bucket

    def _role_policies(self, role_name: str) -> List[Any]:
        """Inline and attached policy documents. Both, because a wildcard grant
        is exactly as dangerous in a managed policy as in an inline one."""
        documents: List[Any] = []
        try:
            for policy_name in paginate(
                self.iam, "list_role_policies", "PolicyNames", RoleName=role_name
            ):
                documents.append(
                    self.iam.get_role_policy(
                        RoleName=role_name, PolicyName=policy_name
                    ).get("PolicyDocument")
                )
        except ClientError as exc:
            self._swallow("list_role_policies", role_name, exc)

        try:
            for attached in paginate(
                self.iam, "list_attached_role_policies", "AttachedPolicies",
                RoleName=role_name,
            ):
                arn = attached.get("PolicyArn")
                policy = self.iam.get_policy(PolicyArn=arn)["Policy"]
                version = self.iam.get_policy_version(
                    PolicyArn=arn, VersionId=policy["DefaultVersionId"]
                )
                documents.append(version["PolicyVersion"]["Document"])
        except ClientError as exc:
            self._swallow("list_attached_role_policies", role_name, exc)

        return documents

    def run(self) -> List[Finding]:
        if not self.session:
            print(
                "No AWS credentials. Every check on this day reads AWS. Try "
                "--profile bootcamp, or run `aws configure --profile bootcamp`.",
                file=sys.stderr,
            )
            sys.exit(2)

        self.log("Collecting security configuration...")
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
        prog="sec_audit.py",
        description=(
            "Audit detection, evidence, credential hygiene and the automated "
            "response path built on them — missing detectors, trails that are "
            "not evidence, responders that trigger on severity, containment "
            "nobody can undo, and automation with no brake."
        ),
        epilog=(
            "Examples:\n"
            "  sec_audit.py --profile bootcamp --region us-east-1\n"
            "  sec_audit.py --prefix cbc-day07     # only this lab's resources\n"
            "  sec_audit.py --format json --quiet > findings.json\n"
            "  sec_audit.py --min-severity HIGH --format csv\n"
            "  sec_audit.py --fail-on CRITICAL     # exit 1 on any CRITICAL\n"
            "\n"
            "Three checks read RUNTIME state (SEC-003, SEC-011, SEC-013), so their\n"
            "answer depends on when you ran this. Run it on a schedule, not only at\n"
            "merge time.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--profile", default=None,
                        help="AWS CLI named profile. Day 01 created 'bootcamp'.")
    parser.add_argument("--region", default="us-east-1",
                        help="AWS region to audit (default: us-east-1).")
    parser.add_argument(
        "--prefix", default=None,
        help=(
            "Only examine resources whose name starts with this prefix, e.g. "
            "cbc-day07. Omit to audit everything in the region — which is the run "
            "worth doing, and the one that finds the access key from 2019."
        ),
    )
    parser.add_argument(
        "--max-access-key-age-days", type=int, default=90,
        help="Age at which an active access key becomes a finding (default: 90).",
    )
    parser.add_argument(
        "--stale-finding-age-days", type=int, default=7,
        help="Age at which an untriaged GuardDuty finding becomes a finding of its own (default: 7).",
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

    auditor = SecurityAuditor(
        profile=args.profile,
        region=args.region,
        prefix=args.prefix,
        max_access_key_age_days=args.max_access_key_age_days,
        stale_finding_age_days=args.stale_finding_age_days,
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
