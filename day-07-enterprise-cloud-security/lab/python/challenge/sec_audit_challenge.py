#!/usr/bin/env python3
"""
sec_audit_challenge.py — build the Day 07 cloud security auditor yourself.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

===============================================================================
  HOW THIS WORKS
===============================================================================

All the boring parts are done for you: the CLI, the Finding dataclass, the
scoring, all three output renderers, the boto3 collector, and every shared
derivation. `responder_functions`, `policy_allows`, `policy_denies`,
`guardduty_rules`, `trail_bucket_for`, `_now`, `_parse_time` and `_age_days`
are complete and tested. You will not be writing an IAM policy evaluator or a
timestamp parser today.

What is missing is the part that matters: the sixteen checks.

There are 16 TODOs. Each one has:
    * a time estimate
    * the exact fields you need, with their types and their absent cases
    * a hint if you are stuck
    * a CHECKPOINT so you know whether it worked before moving on

Total: roughly 120 minutes if you have not done this before.

    python3 sec_audit_challenge.py --profile bootcamp --region us-east-1

You are done when your output matches the reference at ../sec_audit.py:

    11 findings, 137 points, compliance score 0/100, against the lab stack
                 immediately after apply
    11 findings, 131 points, compliance score 0/100, after lab steps 1-5 —
                 SAME COUNT, DIFFERENT SET. See below.
     0 findings,   0 points, compliance score 100/100, against the reference
                 build once rotation has run

Do not read the reference first. You will learn nothing, and the checks are
the whole exercise.

===============================================================================
  THE CLOCK IS AN ARGUMENT, NOT A GLOBAL
===============================================================================

Three of these checks are age-based: SEC-003, SEC-011 and SEC-013.

Read the clock from `_now(stack)`, never from `datetime.now()`. It is already
written and it reads `stack["now"]`, which `collect()` sets once.

This is not style. A check that reads the clock itself is a check whose tests
pass or fail depending on when CI happened to run, and it makes SEC-013's
entire lesson untestable — the lesson being that the same account, unchanged
in every respect, passes today and fails in ninety-one days.

===============================================================================
  STATIC AND LIVE ARE NOT THE SAME, AND THAT IS DELIBERATE
===============================================================================

On Day 06 the auditor read configuration only, so running the lab changed
nothing in its output. Day 07 has three checks that read RUNTIME state, and two
of them move in opposite directions between a fresh apply and a worked lab:

    SEC-011  FIRES at static, goes SILENT live.  Rotation is configured and
             has genuinely never run, because rotate_immediately is false.
    SEC-003  SILENT at static, FIRES live.       It reads the age of untriaged
             findings, and there are none until you generate them.

Eleven findings before, eleven after, six points apart, different problem.
If your implementation makes those two behave the same way, one of them is
reading configuration when it should be reading state.

===============================================================================
  THE OFFLINE FEEDBACK LOOP — USE THIS
===============================================================================

The 47 unit tests in ../tests/test_checks.py can be pointed at YOUR file:

    cd ..
    SEC_AUDIT_MODULE=sec_audit_challenge python3 -m unittest discover -s tests -v

No credentials, no account, no network, under a second. Every check has one
test proving it FIRES on bad input and one proving it stays SILENT on good
input. Run them after every TODO and you will know immediately which half you
broke.

The silent half is the half people skip, and it is the half that decides
whether anyone keeps using your tool. A check that flags every log group in
the account gets muted in week two, at which point it does nothing at all.

===============================================================================
  FIVE CHECKS THAT ARE NOT INDEPENDENT
===============================================================================

Most of these sixteen can be written in any order. Five cannot, because they
have to agree with each other, and the tests enforce it:

    SEC-010 and SEC-011 split one subject. A secret with no rotation at all is
            SEC-010 ONLY. A secret whose rotation is configured and has not run
            is SEC-011 ONLY. Never both on one secret.
    SEC-008 must read DENY statements before calling an Allow a fault. A role
            that allows ec2:* and denies the four destructive actions is
            correctly configured.
    SEC-005, SEC-012 and SEC-014 apply only to functions that can actually
            take an action — which is what responder_functions() is for. A
            read-only Lambda needs no kill switch.

Read the finding contract in ../sec_audit.py's docstring before TODO 1. Those
interactions are written down there, they are not implementation details, and
getting them wrong is the difference between a tool people run and a tool
people mute.

===============================================================================
  THE CHECKS YOU ARE IMPLEMENTING
===============================================================================

    TODO 1    SEC-001  GuardDuty is not enabled in this region         CRITICAL ~ 6 min
    TODO 2    SEC-002  Security Hub off, or on with no standards       HIGH     ~ 7 min
    TODO 3    SEC-003  findings nobody has triaged                     MEDIUM   ~10 min
    TODO 4    SEC-004  finding updates are published slowly            LOW      ~ 4 min
    TODO 5    SEC-005  response triggers on severity, not finding type CRITICAL ~12 min
    TODO 6    SEC-006  trail does not cover the whole account          HIGH     ~ 7 min
    TODO 7    SEC-007  trail has no log file validation                HIGH     ~ 5 min
    TODO 8    SEC-008  responder can tamper with the trail or with itselfCRITICAL ~14 min
    TODO 9    SEC-009  trail bucket is not protected as evidence       HIGH     ~ 9 min
    TODO 10   SEC-010  secret has no rotation configured               MEDIUM   ~ 5 min
    TODO 11   SEC-011  rotation is configured and has never run        HIGH     ~10 min
    TODO 12   SEC-012  containment is configured to be irreversible    CRITICAL ~ 8 min
    TODO 13   SEC-013  long-lived, copyable access key                 MEDIUM   ~ 7 min
    TODO 14   SEC-014  responder has no runtime kill switch            HIGH     ~ 6 min
    TODO 15   SEC-015  GuardDuty response rule is DISABLED             MEDIUM   ~ 6 min
    TODO 16   SEC-016  response target has no dead-letter queue        MEDIUM   ~ 6 min

                       TOTAL                                                    ~122 min

Two of them find nothing against the lab stack, on purpose. OBS-013 is silent
BY DESIGN — the Terraform cannot produce the fault. OBS-009 is silent BY
SITUATION — one alarm happens to be built correctly today. Implement both
properly anyway; the tests check that each fires on input that deserves it.

A check set where everything fires teaches you that findings are normal. A
check set with two deliberate zeroes teaches you that a quiet check is
evidence, which is the more useful lesson.
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


# =============================================================================
# TODO 1 — SEC-001: GuardDuty is not enabled in this region       (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     stack["guardduty_detectors"][n]["DetectorId"] / ["Status"]  "ENABLED"|"DISABLED"
#
# Logic:
#     any detector with Status ENABLED -> no finding
#     otherwise                        -> exactly ONE CRITICAL for the region
#
# HINT: a detector that EXISTS and is DISABLED is the more common and more
#       interesting case than none at all - the console still lists GuardDuty.
#       Put the disabled detector ids in the detail.
#
# CHECKPOINT: the lab stack -> 0. Set Status to DISABLED -> 1 CRITICAL.
# =============================================================================
def check_guardduty_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 1: implement the logic described above.

    return findings


# =============================================================================
# TODO 2 — SEC-002: Security Hub off, or on with no standards     (~7 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["securityhub_enabled"]  bool
#     stack["securityhub_standards"][n]["StandardsStatus"]  "READY"|"PENDING"|...
#
# Logic:
#     not enabled                              -> ONE HIGH
#     enabled with no subscription in a live status -> ONE HIGH
#     enabled with at least one                -> no finding
#
# HINT: the second case is the sneakier half. The service is on, the console
#       renders, it is forwarding other services' findings and evaluating
#       nothing of its own. Say which case it is in the title.
#
# CHECKPOINT: the lab stack -> 0. Empty the standards list -> 1 HIGH.
# =============================================================================
def check_security_hub_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 2: implement the logic described above.

    return findings


# =============================================================================
# TODO 3 — SEC-003: findings nobody has triaged                  (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["guardduty_findings"][n]["Service"]["Archived"]  bool
#         ["Workflow"]["Status"]  "NEW"|"NOTIFIED"|"RESOLVED"|"SUPPRESSED"
#         ["UpdatedAt"] / ["CreatedAt"]  ISO-8601 string
#     stack["stale_finding_age_days"]  the threshold
#     _now(stack) / _age_days(value, now)  already written for you
#
# Logic:
#     archived, RESOLVED or SUPPRESSED -> skip; deciding IS triage
#     age <= threshold                 -> skip
#     any remaining                    -> exactly ONE MEDIUM for the region
#
# HINT: ONE finding with the count in evidence, not one per stale finding. A
#       backlog of findings about the backlog is funny once and useless twice.
#       Use _now(stack), never datetime.now() - see the module docstring.
#
# CHECKPOINT: the lab stack immediately after apply -> 0, because no findings exist
#             yet. This check reads RUNTIME state; it fires once lab step 2
#             generates findings and you leave them alone.
# =============================================================================
def check_stale_findings(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 3: implement the logic described above.

    return findings


# =============================================================================
# TODO 4 — SEC-004: finding updates are published slowly          (~4 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   LOW
#
# Fields:
#     stack["guardduty_detectors"][n]["FindingPublishingFrequency"]
#         "FIFTEEN_MINUTES" | "ONE_HOUR" | "SIX_HOURS"
#
# Logic:
#     FIFTEEN_MINUTES -> no finding
#     anything else   -> LOW, one per detector
#
# HINT: LOW is deliberate. This does NOT delay the first notification of a NEW
#       finding - those arrive in about five minutes regardless. It delays
#       UPDATES, and by six hours the incident is decided either way.
#
# CHECKPOINT: the lab stack -> 0. SILENT BY DESIGN: the variable defaults to
#             FIFTEEN_MINUTES and its validation accepts only three values, so
#             no typo can produce this. Lab step 8a edits it on purpose.
# =============================================================================
def check_publishing_frequency(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 4: implement the logic described above.

    return findings


# =============================================================================
# TODO 5 — SEC-005: response triggers on severity, not finding type  (~12 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     responder_functions(stack)  already written - Lambdas whose ROLE
#                                 grants a containment action
#     _env(function)              already written
#     ENV_SEVERITY_THRESHOLD / ENV_ALLOW_LIST  module constants
#
# Logic:
#     no threshold AND a non-empty allow-list -> no finding
#     a threshold set, OR an empty allow-list  -> CRITICAL
#
# HINT: THE MOST IMPORTANT CHECK IN THIS FILE. GuardDuty severity scores
#       IMPACT, not CONFIDENCE. A severity-7 finding is as likely to be your own
#       penetration test as a compromise. Put that sentence in the detail - the
#       person reading the finding needs the argument, not just the fact.
#
# CHECKPOINT: the naive responder -> 1 CRITICAL. The good one, same zip file, with
#             a four-entry type allow-list -> 0.
# =============================================================================
def check_response_trigger_style(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 5: implement the logic described above.

    return findings


# =============================================================================
# TODO 6 — SEC-006: trail does not cover the whole account        (~7 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["trails"][n]["Name"]  str
#         ["IsMultiRegionTrail"]         bool
#         ["IncludeGlobalServiceEvents"] bool
#
# Logic:
#     both true -> no finding
#     either false -> HIGH, one per trail
#
# HINT: collect BOTH problems into one finding rather than emitting two. They
#       have the same fix - one update-trail call - and two findings for one
#       command is how a report gets padded.
#
# CHECKPOINT: the shadow trail -> 1 HIGH naming both gaps. The main trail -> 0.
# =============================================================================
def check_trail_coverage(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 6: implement the logic described above.

    return findings


# =============================================================================
# TODO 7 — SEC-007: trail has no log file validation              (~5 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["trails"][n]["LogFileValidationEnabled"]  bool
#         ["S3BucketName"]  useful in the detail
#
# Logic:
#     true  -> no finding
#     false -> HIGH, one per trail
#
# HINT: the distinction to put in the detail is logging versus EVIDENCE.
#       Validation is what lets you prove nobody edited the trail to remove
#       their own activity, and it matters exactly once, completely.
#
# CHECKPOINT: the shadow trail -> 1 HIGH. The main trail -> 0.
# =============================================================================
def check_trail_validation(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 7: implement the logic described above.

    return findings


# =============================================================================
# TODO 8 — SEC-008: responder can tamper with the trail or with itself  (~14 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     responder_functions(stack)  gives you _role_name on each function
#     stack["role_policies"]  {role_name: [policy_document, ...]}
#     policy_allows(doc, actions) / policy_denies(doc, actions)  already written
#     TAMPER_ACTIONS  module dict: action -> what it lets the role do
#
# Logic:
#     allowed tamper actions MINUS explicitly denied ones is empty -> no finding
#     anything left -> CRITICAL, once per ROLE
#
# HINT: READ THE DENY STATEMENTS. A role that allows ec2:* and explicitly
#       DENIES the four destructive actions is correctly configured, and a check
#       that flags it is a wildcard grep that will be muted within a fortnight.
#       An explicit Deny cannot be overridden by any Allow, ever.
#
# CHECKPOINT: the naive responder's role -> 1 CRITICAL naming cloudtrail:*, iam:*
#             and ssm:*. The good role, which allows narrowly and denies four
#             categories -> 0.
# =============================================================================
def check_responder_role_scope(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 8: implement the logic described above.

    return findings


# =============================================================================
# TODO 9 — SEC-009: trail bucket is not protected as evidence     (~9 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["trails"][n]["S3BucketName"]
#     stack["buckets"][name]["Versioning"]["Status"]  "Enabled" or absent
#     stack["buckets"][name]["PublicAccessBlock"]  4 boolean keys
#     trail_bucket_for(stack, trail)  already written
#
# Logic:
#     versioning Enabled AND all four blocks true -> no finding
#     either missing -> HIGH, one per trail
#
# HINT: versioning is the ROLLBACK path, not a nice-to-have. Log file
#       validation tells you a file changed; versioning is what lets you see what
#       it said before. Say that in the detail - the two work as a pair.
#
# CHECKPOINT: the shadow bucket -> 1 HIGH. The main trail bucket -> 0.
# =============================================================================
def check_trail_bucket_protection(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 9: implement the logic described above.

    return findings


# =============================================================================
# TODO 10 — SEC-010: secret has no rotation configured            (~5 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["secrets"][n]["Name"] / ["ARN"]
#         ["RotationEnabled"]  bool
#
# Logic:
#     RotationEnabled true -> no finding (SEC-011 owns it)
#     false or absent      -> MEDIUM
#
# HINT: this is 'nobody decided'. SEC-011 is 'it says it rotates and it does
#       not'. Different remediation, usually a different owner, and a secret with
#       no rotation must produce ONE finding rather than two.
#
# CHECKPOINT: the legacy secret -> 1 MEDIUM. The app secret -> 0.
# =============================================================================
def check_secret_rotation_configured(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 10: implement the logic described above.

    return findings


# =============================================================================
# TODO 11 — SEC-011: rotation is configured and has never run    (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["secrets"][n]["RotationEnabled"]  bool
#         ["RotationRules"]["AutomaticallyAfterDays"]  int
#         ["LastRotatedDate"]  datetime or ABSENT
#     _now(stack) / _age_days(value, now)  already written
#
# Logic:
#     RotationEnabled false -> skip; SEC-010 owns it
#     LastRotatedDate absent -> HIGH
#     age > interval * 1.5   -> HIGH
#     otherwise              -> no finding
#
# HINT: THE FAILURE MODE THAT LOOKS LIKE SUCCESS. RotationEnabled true only
#       means a schedule exists. LastRotatedDate is the only field that means it
#       ran. The usual cause is a rotation Lambda with setSecret stubbed out,
#       which 'succeeds' forever while the credential never changes.
#
# CHECKPOINT: immediately after apply -> 1 HIGH, because rotate_immediately is
#             false and rotation genuinely has not run. Force one in lab step 5
#             and it clears. This is the other half of the static/live
#             divergence in the contract.
# =============================================================================
def check_secret_rotation_ran(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 11: implement the logic described above.

    return findings


# =============================================================================
# TODO 12 — SEC-012: containment is configured to be irreversible  (~8 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     responder_functions(stack) / _env(function)  already written
#     ENV_CONTAINMENT_MODE  module constant
#     REVERSIBLE_MODES      module set
#
# Logic:
#     mode in REVERSIBLE_MODES -> no finding
#     anything else, including unset -> CRITICAL
#
# HINT: fire on CONFIGURED INTENT, not on observed behaviour. The shared
#       responder code refuses an unknown mode and changes nothing - which is
#       correct and does not make the configuration acceptable, because the next
#       person to 'fix' the responder will implement what it asks for.
#
# CHECKPOINT: the naive responder (CONTAINMENT_MODE=terminate) -> 1 CRITICAL. The
#             good one (dry-run) -> 0, and isolate -> 0 too.
# =============================================================================
def check_containment_reversible(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 12: implement the logic described above.

    return findings


# =============================================================================
# TODO 13 — SEC-013: long-lived, copyable access key              (~7 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["access_keys"][n]["Status"]  "Active"|"Inactive"
#         ["CreateDate"]  datetime
#         ["UserName"] / ["AccessKeyId"]
#     stack["max_access_key_age_days"]  the threshold
#     _now(stack) / _age_days(value, now)  already written
#
# Logic:
#     Status not Active -> skip; deactivating IS the remediation
#     age <= threshold  -> skip
#     otherwise         -> MEDIUM, one per key
#
# HINT: age is what is measurable from outside; it is not the actual problem.
#       The problem is a copyable string whose use is indistinguishable from
#       legitimate use once copied. Say so, and make the remediation 'replace the
#       credential' rather than 'rotate the key'.
#
# CHECKPOINT: the lab stack -> 0, because the key is HOURS old. SILENT BY
#             SITUATION: nothing has to change for that to stop being true. In 91
#             days the same account fails. Lab step 8b sets the threshold to 0.
# =============================================================================
def check_access_key_age(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 13: implement the logic described above.

    return findings


# =============================================================================
# TODO 14 — SEC-014: responder has no runtime kill switch         (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     responder_functions(stack) / _env(function)  already written
#     ENV_KILL_SWITCH  module constant
#
# Logic:
#     the env var is set  -> no finding
#     absent or empty     -> HIGH
#
# HINT: scoped to functions that can ACT, which responder_functions already
#       handles. A read-only enrichment Lambda needs no brake, and demanding one
#       trains people to ignore the check.
#
# CHECKPOINT: the naive responder -> 1 HIGH. The good one -> 0. The rotator, which
#             can change nothing, is not even considered.
# =============================================================================
def check_kill_switch(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 14: implement the logic described above.

    return findings


# =============================================================================
# TODO 15 — SEC-015: GuardDuty response rule is DISABLED          (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["event_rules"][n]["Name"] / ["State"] / ["Targets"]
#     guardduty_rules(stack)  already written - matches on the event
#                             pattern SOURCE, not on the rule name
#
# Logic:
#     State is not DISABLED -> no finding
#     DISABLED -> MEDIUM, one per rule
#
# HINT: match on the pattern source, never the name. A rule called
#       'guardduty-something' that listens to aws.health is not a response rule,
#       and flagging it is a false positive somebody will remember.
#
# CHECKPOINT: the naive rule -> 1 MEDIUM. The good rule -> 0. A disabled decoy rule
#             listening to another source -> 0.
# =============================================================================
def check_response_rule_enabled(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 15: implement the logic described above.

    return findings


# =============================================================================
# TODO 16 — SEC-016: response target has no dead-letter queue     (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     guardduty_rules(stack)  already written
#     rule["Targets"][n]["Id"] / ["Arn"]
#         ["DeadLetterConfig"]["Arn"]  present when configured
#         ["RetryPolicy"]  useful in the detail
#
# Logic:
#     DeadLetterConfig.Arn present -> no finding
#     absent -> MEDIUM, one per TARGET
#
# HINT: one per TARGET, not per rule. Each target is a separate path a
#       detection can vanish down, and one rule with three undefended targets is
#       three things to fix.
#
# CHECKPOINT: the naive rule's single target -> 1 MEDIUM. The good rule's two
#             targets, both with a DLQ -> 0.
# =============================================================================
def check_response_target_dlq(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 16: implement the logic described above.

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
