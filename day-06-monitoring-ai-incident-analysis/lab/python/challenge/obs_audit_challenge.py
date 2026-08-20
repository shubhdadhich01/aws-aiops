#!/usr/bin/env python3
"""
obs_audit_challenge.py — build the Day 06 observability auditor yourself.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

===============================================================================
  HOW THIS WORKS
===============================================================================

All the boring parts are done for you: the CLI, the Finding dataclass, the
scoring, all three output renderers, the boto3 collector, and — most
importantly — every shared derivation. `bedrock_functions`,
`alarms_referenced_by_composites`, `composite_rule_problems`,
`_dashboard_metric_refs`, `_is_liveness_alarm` and `_uses_metric_math` are
complete and tested. You will not be writing a CloudWatch dashboard parser
today.

What is missing is the part that matters: the sixteen checks.

There are 16 TODOs. Each one has:
    * a time estimate
    * the exact fields you need, with their types and their absent cases
    * a hint if you are stuck
    * a CHECKPOINT so you know whether it worked before moving on

Total: roughly 130 minutes if you have not done this before.

Every check on this day reads AWS, so unlike Day 05 you cannot run the tool
itself without credentials. That makes the offline feedback loop below more
important here, not less — use it.

    python3 obs_audit_challenge.py --profile bootcamp --region us-east-1

You are done when your output matches the reference at ../obs_audit.py:

    15 findings, 144 points, compliance score 0/100, against the lab stack
     0 findings,   0 points, compliance score 100/100, against the reference
                   build (create_insecure_examples = false and
                   enable_bedrock_invocation_logging = true)

Do not read the reference first. You will learn nothing, and the checks are
the whole exercise.

===============================================================================
  THE OFFLINE FEEDBACK LOOP — USE THIS
===============================================================================

The 47 unit tests in ../tests/test_checks.py can be pointed at YOUR file:

    cd ..
    OBS_AUDIT_MODULE=obs_audit_challenge python3 -m unittest discover -s tests -v

No credentials, no account, no network, under a second. Every check has one
test proving it FIRES on bad input and one proving it stays SILENT on good
input. Run them after every TODO and you will know immediately which half you
broke.

The silent half is the half people skip, and it is the half that decides
whether anyone keeps using your tool. A check that flags every log group in
the account gets muted in week two, at which point it does nothing at all.

===============================================================================
  FOUR CHECKS THAT ARE NOT INDEPENDENT
===============================================================================

Most of these sixteen can be written in any order. Four cannot, because they
have to agree with each other, and the tests enforce it:

    OBS-001 defers to OBS-015 on the log group of any model-invoking function.
            One unretained analyser log group is ONE finding, not two.
    OBS-004 exempts alarms covered by a composite — but only a composite that
            notifies AND whose rule can actually fire. So OBS-007 firing on a
            composite also makes OBS-004 fire on its children.
    OBS-006 exempts liveness alarms, which are legitimately raw counts.
    OBS-002 skips /aws/lambda/ groups, which legitimately have no filters.

Read the finding contract in ../obs_audit.py's docstring before TODO 1. Those
four interactions are written down there, they are not implementation details,
and getting them wrong is the difference between a tool people run and a tool
people mute.

===============================================================================
  THE CHECKS YOU ARE IMPLEMENTING
===============================================================================

    TODO 1    OBS-001  log group with no retention policy              HIGH     ~ 8 min
    TODO 2    OBS-002  log group nothing ever reads                    MEDIUM   ~ 8 min
    TODO 3    OBS-003  metric filter dimension, unbounded cardinality  CRITICAL ~10 min
    TODO 4    OBS-004  alarm that notifies nobody                      HIGH     ~12 min
    TODO 5    OBS-005  treat_missing_data left at the default          MEDIUM   ~ 5 min
    TODO 6    OBS-006  alarm thresholded on a raw count, not a rate    MEDIUM   ~10 min
    TODO 7    OBS-007  composite alarm rule can never fire             HIGH     ~10 min
    TODO 8    OBS-008  dashboard references a metric that is gone      MEDIUM   ~10 min
    TODO 9    OBS-009  nothing in the region detects silence           HIGH     ~ 6 min
    TODO 10   OBS-010  alarm evaluates a single datapoint              LOW      ~ 5 min
    TODO 11   OBS-011  raw log text reaches the model unredacted       CRITICAL ~ 8 min
    TODO 12   OBS-012  model invocation with no token budget           HIGH     ~ 6 min
    TODO 13   OBS-013  log data leaves the region to reach the model   HIGH     ~ 6 min
    TODO 14   OBS-014  bedrock:InvokeModel granted on Resource "*"     CRITICAL ~10 min
    TODO 15   OBS-015  model-invoking function's log group unretained  MEDIUM   ~ 8 min
    TODO 16   OBS-016  nothing records what was sent to the model      MEDIUM   ~ 6 min

                       TOTAL                                                    ~128 min

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
# Identical to Days 03, 04 and 05 on purpose. By Day 10 you will have five of
# these tools and one mental model for reading their output; changing the
# arithmetic per tool would make the numbers incomparable for no gain.
###############################################################################

SEVERITY_WEIGHTS: Dict[str, int] = {
    "CRITICAL": 25,
    "HIGH": 10,
    "MEDIUM": 4,
    "LOW": 1,
    "INFO": 0,
}

SEVERITY_ORDER: List[str] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

# ANSI colours. Disabled automatically when stdout is not a TTY, so piping to a
# file or into `jq` does not produce escape-code soup.
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

    check_id     Stable identifier (OBS-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken. resource_id is the thing you
                 would type into the console or the CLI to look at it: a log
                 group name, an alarm name, a function name.
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
# boto3 paginators are the correct way to do this. The wrong way — calling
# describe_* once and trusting the first page — silently misses everything past
# the first 50-100 items, which is exactly the situation where an audit
# matters. An account with 400 log groups is completely ordinary, and an audit
# that reports on the first 50 is worse than no audit, because it produces a
# clean report you believe.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Any]:
    """Collect every page of a paginated boto3 operation into one list.

    Falls back to a single direct call for operations that have no paginator
    registered (cloudwatch:ListDashboards is like this in some botocore
    versions).
    """
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

    `"Action": "bedrock:InvokeModel"` and `"Action": ["bedrock:InvokeModel"]`
    are the same document. Every policy parser that forgets this has a
    wildcard-detection bug, because the single-string form is exactly the form
    `Resource: "*"` takes.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_policy(document: Any) -> Dict[str, Any]:
    """Return a policy document as a dict.

    IAM hands them back URL-encoded JSON strings, CloudWatch hands dashboard
    bodies back as plain JSON strings, and our own tests hand them back as
    dicts. Accept all three rather than making every caller remember which is
    which.
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

# Dimension values whose cardinality is unbounded. Every distinct value of one
# of these becomes a separate custom metric at $0.30/month that CANNOT be
# deleted — it ages out fifteen months after its last datapoint and not one day
# sooner.
#
# The list is a heuristic and it is honest about being one. A field called
# `tenant` is bounded in one company and unbounded in the next. What is NOT a
# heuristic is the rule behind it: a dimension value must come from a set you
# could write down on a napkin.
HIGH_CARDINALITY_HINTS: List[str] = [
    "request_id",
    "requestid",
    "trace",
    "span",
    "session",
    "correlation",
    "user",
    "customer",
    "account_id",
    "order",
    "uuid",
    "guid",
    "path",
    "url",
    "uri",
    "email",
    "ip",
    "host",
    "instance_id",
    "token",
    "message_id",
]

# Environment variable names the AI-path checks read. These are the analyser's
# contract with its operator, and the reason OBS-011/012/013 can be answered
# without reading a line of the function's code.
ENV_REDACT = "REDACT_LOGS"
ENV_MAX_TOKENS = "MAX_INPUT_TOKENS"
ENV_BEDROCK_REGION = "BEDROCK_REGION"

BEDROCK_INVOKE_ACTIONS: Set[str] = {
    "bedrock:invokemodel",
    "bedrock:invokemodelwithresponsestream",
    "bedrock:converse",
    "bedrock:conversestream",
    "bedrock:*",
    "*",
}

LAMBDA_LOG_PREFIX = "/aws/lambda/"

_ALARM_STATE_REF = re.compile(
    r"\b(ALARM|OK|INSUFFICIENT_DATA)\s*\(\s*\"?([^\")]+?)\"?\s*\)"
)


###############################################################################
# Shared derivations
#
# Several checks need the same three answers. Computing them once, here, keeps
# every check a pure function of `stack` while making the expensive reasoning
# happen only in one place — and, more importantly, makes the check
# interactions in the finding contract explicit rather than emergent.
###############################################################################


def _env(function: Dict[str, Any]) -> Dict[str, str]:
    return (function.get("Environment") or {}).get("Variables") or {}


def policy_grants_bedrock_invoke(document: Any) -> Tuple[bool, List[str]]:
    """True if a policy document allows bedrock:InvokeModel, plus the resources.

    Returns the resource list too, because OBS-014 needs to know whether it was
    scoped and OBS-011/012/013 only need to know that it was granted at all.
    """
    policy = parse_policy(document)
    resources: List[str] = []
    granted = False
    for statement in as_list(policy.get("Statement")):
        if not isinstance(statement, dict):
            continue
        if statement.get("Effect") != "Allow":
            continue
        actions = [str(a).lower() for a in as_list(statement.get("Action"))]
        if any(action in BEDROCK_INVOKE_ACTIONS for action in actions):
            granted = True
            resources.extend(str(r) for r in as_list(statement.get("Resource")))
    return granted, resources


def bedrock_functions(stack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every Lambda whose role grants bedrock:InvokeModel.

    This is the set OBS-011 through OBS-015 operate on, and the set OBS-001
    hands over to OBS-015. Identifying it by PERMISSION rather than by name is
    the only approach that survives contact with a real account, where the
    function that talks to a model is called `summarizer-v2-final`.
    """
    role_policies = stack.get("role_policies") or {}
    found: List[Dict[str, Any]] = []
    for function in stack.get("lambda_functions") or []:
        role_name = (function.get("Role") or "").rsplit("/", 1)[-1]
        for document in role_policies.get(role_name, []):
            granted, resources = policy_grants_bedrock_invoke(document)
            if granted:
                enriched = dict(function)
                enriched["_bedrock_resources"] = resources
                enriched["_role_name"] = role_name
                found.append(enriched)
                break
    return found


def bedrock_log_group_names(stack: Dict[str, Any]) -> Set[str]:
    """The /aws/lambda/ log group names belonging to Bedrock-invoking functions.

    OBS-001 skips these and OBS-015 owns them, so an unretained analyser log
    group produces ONE finding rather than two. That is a contract decision,
    not an implementation detail — it is written down in the finding contract
    and there is a test asserting it.
    """
    return {
        LAMBDA_LOG_PREFIX + str(fn.get("FunctionName", ""))
        for fn in bedrock_functions(stack)
    }


def composite_rule_problems(rule: str, known: Set[str]) -> List[str]:
    """Why a composite alarm rule can never fire, or an empty list.

    Factored out of OBS-007 because OBS-004 needs the same answer. Two ways to
    be unfireable:

      * A contradiction — the same alarm required to be in two states at once
        inside an AND chain. With an OR anywhere in the rule that is perfectly
        satisfiable, so the check only applies to pure AND chains.
      * A dangling reference — the rule names an alarm that does not exist, so
        the composite sits in INSUFFICIENT_DATA permanently.
    """
    refs = _ALARM_STATE_REF.findall(rule or "")
    problems: List[str] = []

    if refs and " OR " not in (rule or "").upper():
        states_by_alarm: Dict[str, Set[str]] = {}
        for state, alarm_name in refs:
            states_by_alarm.setdefault(alarm_name.strip(), set()).add(state)
        for alarm_name, states in sorted(states_by_alarm.items()):
            if len(states) > 1:
                problems.append(
                    f"requires {alarm_name} to be in {' and '.join(sorted(states))} "
                    f"simultaneously"
                )

    for _state, alarm_name in refs:
        if alarm_name.strip() not in known:
            problems.append(f"references unknown alarm {alarm_name.strip()}")

    return problems


def known_alarm_names(stack: Dict[str, Any]) -> Set[str]:
    names = {a.get("AlarmName") for a in stack.get("metric_alarms") or []}
    names |= {a.get("AlarmName") for a in stack.get("composite_alarms") or []}
    return {n for n in names if n}


def alarms_referenced_by_composites(stack: Dict[str, Any]) -> Set[str]:
    """Alarm names watched by a composite alarm that actually works.

    An alarm with no actions is not a fault if a composite is watching it and
    the composite notifies. Resolving that is the difference between a check
    that finds real problems and one that flags the correctly-designed stack in
    main.tf section 5.

    But the composite has to be REAL cover, and two kinds are not:

      * A composite with no notification action of its own. Then the chain is
        silent all the way up and you have added a layer, not a page.
      * A composite whose rule can never fire. An alarm whose only reader is an
        unsatisfiable composite is exactly as silent as one with no reader at
        all — and it is worse, because a reviewer scanning for orphaned alarms
        sees the reference and moves on.

    That second exclusion is why OBS-007 firing on a composite also makes
    OBS-004 fire on its children. The two findings are not duplicates; they are
    the cause and the consequence, and fixing the rule clears both.
    """
    known = known_alarm_names(stack)
    referenced: Set[str] = set()

    for composite in stack.get("composite_alarms") or []:
        if not _has_notification(composite):
            continue
        if composite_rule_problems(composite.get("AlarmRule", "") or "", known):
            continue
        for _state, name in _ALARM_STATE_REF.findall(composite.get("AlarmRule", "") or ""):
            referenced.add(name.strip())
    return referenced


def _has_notification(alarm: Dict[str, Any]) -> bool:
    if alarm.get("ActionsEnabled") is False:
        return False
    return bool(
        alarm.get("AlarmActions")
        or alarm.get("OKActions")
        or alarm.get("InsufficientDataActions")
    )


def _is_liveness_alarm(alarm: Dict[str, Any]) -> bool:
    """A dead-man's switch: treats missing data as breaching, fires on a floor.

    OBS-006 exempts these. A liveness alarm is legitimately a raw count — the
    whole point is "fewer than N events happened" — and flagging it would be
    the auditor crying wolf about the best alarm in the stack.
    """
    return (
        alarm.get("TreatMissingData") == "breaching"
        and str(alarm.get("ComparisonOperator", "")).startswith("LessThan")
    )


def _uses_metric_math(alarm: Dict[str, Any]) -> bool:
    for query in alarm.get("Metrics") or []:
        if query.get("Expression"):
            return True
    return False


def _dashboard_metric_refs(body: Any) -> List[Tuple[str, str]]:
    """Every (namespace, metric_name) a dashboard body references.

    Dashboard metric arrays are positional and support a `"..."` shorthand that
    repeats the previous entry's namespace and metric. Handling that shorthand
    is not optional: a widget that uses it is the common case, and a parser
    that ignores it reports every such widget as broken.
    """
    document = parse_policy(body)
    refs: List[Tuple[str, str]] = []
    last: Optional[Tuple[str, str]] = None

    for widget in document.get("widgets", []) or []:
        if not isinstance(widget, dict) or widget.get("type") != "metric":
            continue
        for series in (widget.get("properties") or {}).get("metrics", []) or []:
            if not isinstance(series, list) or not series:
                continue
            head = series[0]
            if head == "...":
                if last:
                    refs.append(last)
                continue
            if not isinstance(head, str) or len(series) < 2:
                # A pure metric-math entry: [{"expression": ...}]. It
                # references other series by id, not a metric by name.
                continue
            if not isinstance(series[1], str):
                continue
            last = (head, series[1])
            refs.append(last)
    return refs


###############################################################################
# Checks
#
# Every one is a pure function: (stack: Dict, region: str) -> List[Finding].
# No boto3, no network, no credentials, no clock. That is what lets
# tests/test_checks.py run 47 assertions in under a second on a laptop with no
# AWS account at all — and it is why the check bodies are the part of this file
# worth reading twice.
###############################################################################


# =============================================================================
# TODO 1 — OBS-001: log group with no retention policy            (~8 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["log_groups"][n]["logGroupName"]   str
#     stack["log_groups"][n]["retentionInDays"] int, ABSENT when Never expire
#     stack["log_groups"][n]["storedBytes"]      int
#
# Logic:
#     retentionInDays present and truthy -> no finding
#     otherwise                          -> HIGH
#     SKIP any group returned by bedrock_log_group_names(stack); OBS-015
#     owns those, and double-reporting one resource inflates the score.
#
# HINT: the fault is a MISSING key, not a wrong value. `if group.get("retentionInDays")`
#       is the whole test, and that is exactly why nobody catches it in review.
#
# CHECKPOINT: the legacy-app group -> 1 HIGH. The naive analyser's group -> 0 here
#             (it belongs to OBS-015). Every retained group -> 0.
# =============================================================================
def check_log_retention(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 1: implement the logic described above.

    return findings


# =============================================================================
# TODO 2 — OBS-002: log group nothing ever reads                  (~8 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["log_groups"][n]["logGroupName"]         str
#     stack["metric_filters"][n]["logGroupName"]      str
#     stack["subscription_filters"][n]["logGroupName"] str
#
# Logic:
#     group has a metric filter OR a subscription filter -> no finding
#     group name starts with /aws/lambda/                -> no finding
#     otherwise                                          -> MEDIUM
#
# HINT: build the two sets of log group names ONCE before the loop. Doing it
#       inside the loop is O(n*m) and this runs against accounts with hundreds
#       of groups.
#
# CHECKPOINT: legacy-app and write-only -> 1 MEDIUM each, so this check fires TWICE
#             on the lab stack. The workload group has four metric filters -> 0.
#             The two /aws/lambda/ groups -> 0.
# =============================================================================
def check_write_only_log_group(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 2: implement the logic described above.

    return findings


# =============================================================================
# TODO 3 — OBS-003: metric filter dimension, unbounded cardinality  (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     stack["metric_filters"][n]["filterName"]                     str
#     stack["metric_filters"][n]["metricTransformations"][m]
#         ["dimensions"]  dict of {DimensionName: "$.json_field"}
#     HIGH_CARDINALITY_HINTS  module-level list, already written for you
#
# Logic:
#     any dimension whose KEY or VALUE contains one of the hints -> CRITICAL
#     no dimensions, or only bounded ones                        -> no finding
#     one finding per transformation, not per dimension
#
# HINT: match case-insensitively and check both the key and the value —
#       {"RequestId": "$.rid"} and {"Id": "$.request_id"} are the same mistake
#       and only one of them is caught if you only look at one side.
#
# CHECKPOINT: the per-request filter -> 1 CRITICAL. The errors-by-type filter,
#             whose dimension takes four values from a tuple in the source, -> 0.
# =============================================================================
def check_metric_filter_cardinality(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 3: implement the logic described above.

    return findings


# =============================================================================
# TODO 4 — OBS-004: alarm that notifies nobody                   (~12 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["metric_alarms"][n]["AlarmName"]      str
#         ["AlarmActions"] / ["OKActions"] / ["InsufficientDataActions"]  lists
#         ["ActionsEnabled"]  bool
#     alarms_referenced_by_composites(stack)  already written for you
#     _has_notification(alarm)                already written for you
#
# Logic:
#     any action list non-empty AND ActionsEnabled is not False -> no finding
#     name appears in alarms_referenced_by_composites(stack)    -> no finding
#     otherwise                                                 -> HIGH
#
# HINT: read alarms_referenced_by_composites before you write this. It only
#       counts a composite that NOTIFIES and whose rule CAN FIRE — an alarm
#       watched solely by an unsatisfiable composite is exactly as silent as an
#       orphan. That is the OBS-007/OBS-004 interaction in the finding contract.
#
# CHECKPOINT: the orphan alarm -> 1 HIGH, even though the impossible composite
#             references it. The three alarms in main.tf section 5 have no actions
#             and -> 0, because the working composite covers them.
# =============================================================================
def check_alarm_without_action(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 4: implement the logic described above.

    return findings


# =============================================================================
# TODO 5 — OBS-005: treat_missing_data left at the default        (~5 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["metric_alarms"][n]["TreatMissingData"]
#         "missing" | "notBreaching" | "breaching" | "ignore" | ABSENT
#
# Logic:
#     set to something other than "missing" -> no finding
#     absent, or "missing"                  -> MEDIUM
#
# HINT: the API returns "missing" both for an alarm that never set the
#       attribute and one that set it deliberately. They are indistinguishable
#       from outside, so flag both and say so in the remediation. Pretending you
#       can tell them apart is worse than admitting you cannot.
#
# CHECKPOINT: the orphan alarm -> 1 MEDIUM. The other three set it explicitly -> 0.
# =============================================================================
def check_treat_missing_data(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 5: implement the logic described above.

    return findings


# =============================================================================
# TODO 6 — OBS-006: alarm thresholded on a raw count, not a rate  (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["metric_alarms"][n]["Statistic"]           "Sum" | "Average" | ...
#         ["ComparisonOperator"]  "GreaterThanThreshold" | "LessThanThreshold" | ...
#         ["Metrics"]             present when the alarm uses metric math
#     _uses_metric_math(alarm) / _is_liveness_alarm(alarm)  already written
#
# Logic:
#     alarm uses metric math                       -> no finding
#     alarm is a liveness alarm (see helper)       -> no finding
#     Statistic in (Sum, SampleCount) AND comparison starts GreaterThan -> MEDIUM
#
# HINT: the liveness exemption is not politeness. A dead-man's switch is
#       LEGITIMATELY a raw count — "fewer than N events happened" is the entire
#       idea — and flagging it would be the auditor crying wolf about the best
#       alarm in the stack.
#
# CHECKPOINT: the orphan alarm -> 1 MEDIUM. The no-telemetry alarm, which also
#             sums a raw count, -> 0. The percentile and metric-math alarms -> 0.
# =============================================================================
def check_alarm_on_raw_count(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 6: implement the logic described above.

    return findings


# =============================================================================
# TODO 7 — OBS-007: composite alarm rule can never fire          (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["composite_alarms"][n]["AlarmName"] / ["AlarmRule"]  str
#     composite_rule_problems(rule, known)  already written for you
#     known_alarm_names(stack)              already written for you
#     _ALARM_STATE_REF                      compiled regex, already written
#
# Logic:
#     composite_rule_problems returns [] -> no finding
#     otherwise                          -> HIGH, with the problems in evidence
#
# HINT: the helper is written; your job is the Finding. Put the rule itself in
#       the detail — the reader needs to see the string to believe it, and
#       `ALARM(x) AND OK(x)` is far more convincing than any description of it.
#
# CHECKPOINT: the impossible composite -> 1 HIGH with a problem mentioning
#             "simultaneously". The service-degraded composite, an OR of three real
#             alarms, -> 0.
# =============================================================================
def check_composite_alarm_satisfiable(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 7: implement the logic described above.

    return findings


# =============================================================================
# TODO 8 — OBS-008: dashboard references a metric that is gone   (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["dashboards"][n]["DashboardName"] / ["DashboardBody"]
#     stack["existing_metrics"][n]["Namespace"] / ["MetricName"]
#     _dashboard_metric_refs(body)  already written — it handles the
#                                   "..." shorthand, which you need
#
# Logic:
#     every referenced (namespace, metric) exists -> no finding
#     any missing                                 -> MEDIUM
#     ONE finding per dashboard, not one per missing metric
#
# HINT: one finding per dashboard is a judgement, not laziness: a dashboard
#       with nine dead widgets is one thing to fix, and nine findings for it would
#       drown the fifteen other things that are wrong.
#
# CHECKPOINT: the broken dashboard -> 1 MEDIUM listing CheckoutSuccessRate and
#             CareerByteCode/Day05 DriftDetected. The operations dashboard -> 0.
# =============================================================================
def check_dashboard_metrics_exist(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 8: implement the logic described above.

    return findings


# =============================================================================
# TODO 9 — OBS-009: nothing in the region detects silence         (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     stack["metric_alarms"]  the whole list
#     _is_liveness_alarm(alarm)  already written for you
#
# Logic:
#     no alarms at all           -> no finding (bigger problem, not this one)
#     any liveness alarm exists  -> no finding
#     otherwise                  -> exactly ONE HIGH for the region
#
# HINT: this is a WHOLE-STACK check. It fires once, not once per alarm,
#       because there is one thing missing rather than N. resource_id should say
#       so — something like "(region us-east-1)".
#
# CHECKPOINT: the lab stack -> 0, because the no-telemetry alarm exists. This is
#             SILENT BY SITUATION, not by design: change that one alarm's
#             treat_missing_data and it fires. Read the contract on the difference.
# =============================================================================
def check_liveness_alarm_exists(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 9: implement the logic described above.

    return findings


# =============================================================================
# TODO 10 — OBS-010: alarm evaluates a single datapoint           (~5 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   LOW
#
# Fields:
#     stack["metric_alarms"][n]["EvaluationPeriods"]  int
#         ["DatapointsToAlarm"]  int, often ABSENT (means: same as N)
#         ["Period"]             int seconds
#
# Logic:
#     EvaluationPeriods != 1                       -> no finding
#     DatapointsToAlarm present and > 1            -> no finding
#     otherwise                                    -> LOW
#
# HINT: `(alarm.get("DatapointsToAlarm") or 1) != 1` handles both the absent
#       case and an explicit 1 in one expression. LOW is deliberate — this is a
#       tuning fault, not a blindness fault, and rating it higher would drown
#       OBS-001 and OBS-004 in noise.
#
# CHECKPOINT: the orphan alarm -> 1 LOW. The 3-of-5 and 10-of-10 alarms -> 0.
# =============================================================================
def check_single_datapoint_alarm(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 10: implement the logic described above.

    return findings


# =============================================================================
# TODO 11 — OBS-011: raw log text reaches the model unredacted    (~8 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     bedrock_functions(stack)  already written — returns every Lambda
#                               whose ROLE grants bedrock:InvokeModel
#     _env(function)            already written — the env var dict
#     ENV_REDACT                module constant
#
# Logic:
#     env[ENV_REDACT] == "true" (case-insensitive) -> no finding
#     anything else, including absent              -> CRITICAL
#
# HINT: identify model-invoking functions by PERMISSION, not by name — which
#       bedrock_functions already does. Name matching survives a lab and nothing
#       else; in a real account the function that talks to a model is called
#       summarizer-v2-final.
#
# CHECKPOINT: the naive analyser -> 1 CRITICAL. The good analyser, same zip file,
#             REDACT_LOGS=true, -> 0.
# =============================================================================
def check_prompt_redaction(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 11: implement the logic described above.

    return findings


# =============================================================================
# TODO 12 — OBS-012: model invocation with no token budget        (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     bedrock_functions(stack) / _env(function)  already written
#     ENV_MAX_TOKENS  module constant
#     function["ReservedConcurrentExecutions"]  useful in the detail
#
# Logic:
#     int(env[ENV_MAX_TOKENS]) > 0 -> no finding
#     absent, empty, 0, or unparseable -> HIGH
#
# HINT: wrap the int() in try/except. An env var is a string somebody typed,
#       and a ValueError here would abort the whole audit over one typo.
#
# CHECKPOINT: the naive analyser (MAX_INPUT_TOKENS=0) -> 1 HIGH. The good
#             analyser (12000) -> 0.
# =============================================================================
def check_token_budget(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 12: implement the logic described above.

    return findings


# =============================================================================
# TODO 13 — OBS-013: log data leaves the region to reach the model  (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     bedrock_functions(stack) / _env(function)  already written
#     ENV_BEDROCK_REGION  module constant
#     region  the function argument — the region the LOGS are in
#
# Logic:
#     env value absent, or region argument empty -> no finding
#     env value == region                        -> no finding
#     they differ                                -> HIGH
#
# HINT: mention inference profiles in the remediation. A model ID beginning
#       "us." or "eu." may route to other regions in its group regardless of what
#       this variable says, and that door is much less visible than this one.
#
# CHECKPOINT: the lab stack -> 0. This is SILENT BY DESIGN: one Terraform variable
#             feeds both the client region and the model ARN, so they cannot
#             diverge by accident. Lab step 8 makes it fire, TWICE, because that
#             variable is shared by both analysers.
# =============================================================================
def check_cross_region_model(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 13: implement the logic described above.

    return findings


# =============================================================================
# TODO 14 — OBS-014: bedrock:InvokeModel granted on Resource "*"  (~10 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     stack["role_policies"]  {role_name: [policy_document, ...]}
#     policy_grants_bedrock_invoke(doc) -> (granted: bool, resources: list)
#     bedrock_functions(stack)  gives you _role_name on each function
#
# Logic:
#     any resource == "*" or ending ":*" -> CRITICAL, once per ROLE
#     every resource a concrete model ARN -> no finding
#
# HINT: de-duplicate by role. Two functions sharing one bad role is one
#       problem to fix, and reporting it twice makes the remediation read as if
#       there were two policies to edit.
#
# CHECKPOINT: the naive analyser's role -> 1 CRITICAL. The good analyser's role,
#             scoped to one foundation-model ARN, -> 0.
# =============================================================================
def check_bedrock_resource_scope(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 14: implement the logic described above.

    return findings


# =============================================================================
# TODO 15 — OBS-015: model-invoking function's log group unretained  (~8 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     bedrock_functions(stack)  already written
#     stack["log_groups"]  index it by logGroupName first
#     LAMBDA_LOG_PREFIX  module constant
#
# Logic:
#     group exists AND has retentionInDays -> no finding
#     group exists without retention       -> MEDIUM
#     group does not exist at all          -> MEDIUM (Lambda will create it
#                                             on first invocation, without)
#
# HINT: the missing-group case is the more common one and the worse one, so
#       say which it is in the detail. A group that does not exist yet will exist
#       tomorrow, with Never expire, and nobody will ever look at it.
#
# CHECKPOINT: the naive analyser's group -> 1 MEDIUM. The good analyser's group,
#             created in Terraform with retention, -> 0. And note OBS-001 must NOT
#             also fire on the naive group: one resource, one finding.
# =============================================================================
def check_analyser_log_retention(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 15: implement the logic described above.

    return findings


# =============================================================================
# TODO 16 — OBS-016: nothing records what was sent to the model   (~6 minutes)
# =============================================================================
#
# Signature:  (stack: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     stack["bedrock_logging"]  the get_model_invocation_logging_configuration
#                               response, or None when never configured
#         ["loggingConfig"]["cloudWatchConfig"] / ["s3Config"]
#
# Logic:
#     no bedrock-invoking functions at all -> no finding
#     a cloudWatchConfig or s3Config exists -> no finding
#     otherwise                             -> exactly ONE MEDIUM
#
# HINT: accept both the wrapped {"loggingConfig": {...}} and a bare {...}.
#       And write the remediation carefully: turning this on writes every prompt
#       to a log group readable by everyone with CloudWatch access, so the fix is
#       "enable it AND restrict the destination", both halves or neither.
#
# CHECKPOINT: the lab default -> 1 MEDIUM. With enable_bedrock_invocation_logging
#             = true -> 0, and that plus create_insecure_examples = false is the
#             only combination that scores 100/100.
# =============================================================================
def check_model_invocation_logging(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 16: implement the logic described above.

    return findings


###############################################################################
# Check registry
#
# Every check needs credentials, because everything this tool looks for is
# invisible in the Terraform. That is the inverse of Day 05, where twelve of
# sixteen ran with nothing but a filesystem — and the difference is worth
# noticing rather than glossing over: a static auditor tells you what you
# WROTE, a live one tells you what you HAVE. You want both, and by Day 10 you
# will have both.
###############################################################################

CHECKS = [
    ("OBS-001", check_log_retention),
    ("OBS-002", check_write_only_log_group),
    ("OBS-003", check_metric_filter_cardinality),
    ("OBS-004", check_alarm_without_action),
    ("OBS-005", check_treat_missing_data),
    ("OBS-006", check_alarm_on_raw_count),
    ("OBS-007", check_composite_alarm_satisfiable),
    ("OBS-008", check_dashboard_metrics_exist),
    ("OBS-009", check_liveness_alarm_exists),
    ("OBS-010", check_single_datapoint_alarm),
    ("OBS-011", check_prompt_redaction),
    ("OBS-012", check_token_budget),
    ("OBS-013", check_cross_region_model),
    ("OBS-014", check_bedrock_resource_scope),
    ("OBS-015", check_analyser_log_retention),
    ("OBS-016", check_model_invocation_logging),
]

LIVE_CHECKS = [check_id for check_id, _ in CHECKS]


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.

    Expect zero against this lab's stack with create_insecure_examples = true.
    Three CRITICAL findings are 75 points on their own and the weights total
    144. That is the intended shock. Set create_insecure_examples = false and
    enable_bedrock_invocation_logging = true and the same tool with the same
    checks returns 100/100, which is the more useful demonstration.
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
    w(colour("  OBSERVABILITY AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 06 · Monitoring & AI Incident Analysis\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('log_groups', 0)} log group(s) · "
        f"{stats.get('metric_filters', 0)} metric filter(s) · "
        f"{stats.get('alarms', 0)} alarm(s) · "
        f"{stats.get('composite_alarms', 0)} composite alarm(s) · "
        f"{stats.get('dashboards', 0)} dashboard(s) · "
        f"{stats.get('functions', 0)} function(s) · "
        f"{stats.get('custom_metrics', 0)} custom metric(s)\n\n"
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
        "audit": "obs_audit",
        "day": "06",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compliance_score": score,
        "grade": score_grade(score),
        "scanned": stats,
        "summary": counts,
        "finding_count": len(findings),
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


class ObservabilityAuditor:
    """Collects one normalised snapshot of the region, then runs pure checks.

    The split is deliberate and it is the reason tests/test_checks.py needs no
    credentials: everything that touches AWS happens in collect(), everything
    that decides anything happens in a function that takes a dict.

    If you only take one structural idea from these five auditors, take that
    one. A check that calls boto3 itself cannot be tested without either an
    account or a mocking library elaborate enough to have its own bugs, and the
    checks are the part where the reasoning lives.
    """

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        prefix: Optional[str] = None,
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.prefix = prefix
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stack: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "log_groups": 0,
            "metric_filters": 0,
            "alarms": 0,
            "composite_alarms": 0,
            "dashboards": 0,
            "functions": 0,
            "custom_metrics": 0,
        }

        self.session: Any = None
        self.logs: Any = None
        self.cloudwatch: Any = None
        self.lambda_: Any = None
        self.iam: Any = None
        self.bedrock: Any = None

        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.logs = self.session.client("logs")
            self.cloudwatch = self.session.client("cloudwatch")
            self.lambda_ = self.session.client("lambda")
            self.iam = self.session.client("iam")
            self.bedrock = self.session.client("bedrock")
        except (BotoCoreError, NoCredentialsError) as exc:
            self.log(f"  ! No AWS session ({exc}).")
            self.session = None

    # -- logging ------------------------------------------------------------

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def _swallow(self, operation: str, resource: str, exc: ClientError) -> None:
        """Log an API error that should not abort the whole audit.

        ResourceNotFoundException is the NORMAL answer to several of these —
        Bedrock returns it when invocation logging has never been configured,
        which is precisely the condition OBS-016 exists to report — so it is
        handled by the caller and not logged as a problem.
        """
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in (
            "ResourceNotFoundException",
            "ValidationException",
            "NoSuchEntity",
            "AccessDeniedException",
        ):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    # -- collection ---------------------------------------------------------

    def _in_scope(self, name: str) -> bool:
        return not self.prefix or str(name).startswith(self.prefix)

    def collect(self) -> Dict[str, Any]:
        """One snapshot of the region, in the shape every check expects."""
        stack: Dict[str, Any] = {
            "region": self.region,
            "log_groups": [],
            "metric_filters": [],
            "subscription_filters": [],
            "metric_alarms": [],
            "composite_alarms": [],
            "dashboards": [],
            "existing_metrics": [],
            "lambda_functions": [],
            "role_policies": {},
            "bedrock_logging": None,
        }
        if not self.session:
            return stack

        self.log("  · log groups, metric filters, subscription filters")
        stack["log_groups"] = [
            g
            for g in paginate(self.logs, "describe_log_groups", "logGroups")
            if self._in_scope(g.get("logGroupName", ""))
        ]
        stack["metric_filters"] = [
            f
            for f in paginate(self.logs, "describe_metric_filters", "metricFilters")
            if self._in_scope(f.get("logGroupName", ""))
        ]
        for group in stack["log_groups"]:
            try:
                stack["subscription_filters"].extend(
                    paginate(
                        self.logs,
                        "describe_subscription_filters",
                        "subscriptionFilters",
                        logGroupName=group["logGroupName"],
                    )
                )
            except ClientError as exc:
                self._swallow(
                    "describe_subscription_filters", group["logGroupName"], exc
                )

        self.log("  · alarms and composite alarms")
        stack["metric_alarms"] = [
            a
            for a in paginate(self.cloudwatch, "describe_alarms", "MetricAlarms")
            if self._in_scope(a.get("AlarmName", ""))
        ]
        stack["composite_alarms"] = [
            a
            for a in paginate(self.cloudwatch, "describe_alarms", "CompositeAlarms")
            if self._in_scope(a.get("AlarmName", ""))
        ]

        self.log("  · dashboards")
        for entry in paginate(
            self.cloudwatch, "list_dashboards", "DashboardEntries"
        ):
            name = entry.get("DashboardName", "")
            if not self._in_scope(name):
                continue
            try:
                body = self.cloudwatch.get_dashboard(DashboardName=name)
                stack["dashboards"].append(
                    {"DashboardName": name, "DashboardBody": body.get("DashboardBody")}
                )
            except ClientError as exc:
                self._swallow("get_dashboard", name, exc)

        self.log("  · published metrics (for the dashboard check)")
        namespaces = {
            t.get("metricNamespace")
            for f in stack["metric_filters"]
            for t in f.get("metricTransformations") or []
        }
        namespaces |= {a.get("Namespace") for a in stack["metric_alarms"]}
        namespaces |= {
            ns
            for d in stack["dashboards"]
            for ns, _mn in _dashboard_metric_refs(d.get("DashboardBody"))
        }
        for namespace in sorted(n for n in namespaces if n):
            stack["existing_metrics"].extend(
                paginate(
                    self.cloudwatch, "list_metrics", "Metrics", Namespace=namespace
                )
            )

        self.log("  · lambda functions and their role policies")
        for function in paginate(self.lambda_, "list_functions", "Functions"):
            if not self._in_scope(function.get("FunctionName", "")):
                continue
            try:
                detail = self.lambda_.get_function_configuration(
                    FunctionName=function["FunctionName"]
                )
                stack["lambda_functions"].append(detail)
            except ClientError as exc:
                self._swallow(
                    "get_function_configuration", function["FunctionName"], exc
                )

        for function in stack["lambda_functions"]:
            role_name = (function.get("Role") or "").rsplit("/", 1)[-1]
            if not role_name or role_name in stack["role_policies"]:
                continue
            stack["role_policies"][role_name] = self._role_policies(role_name)

        self.log("  · bedrock model invocation logging")
        try:
            stack["bedrock_logging"] = (
                self.bedrock.get_model_invocation_logging_configuration()
            )
        except ClientError as exc:
            # ResourceNotFoundException here IS the finding, not an error.
            self._swallow("get_model_invocation_logging_configuration", "account", exc)
            stack["bedrock_logging"] = None

        self.stack = stack
        self.stats.update(
            {
                "log_groups": len(stack["log_groups"]),
                "metric_filters": len(stack["metric_filters"]),
                "alarms": len(stack["metric_alarms"]),
                "composite_alarms": len(stack["composite_alarms"]),
                "dashboards": len(stack["dashboards"]),
                "functions": len(stack["lambda_functions"]),
                "custom_metrics": len(stack["existing_metrics"]),
            }
        )
        return stack

    def _role_policies(self, role_name: str) -> List[Any]:
        """Inline and attached policy documents for a role, as parsed dicts.

        Both, because a wildcard grant is just as dangerous in a managed policy
        as in an inline one, and teams that have been told not to use inline
        policies put it in a customer-managed one instead.
        """
        documents: List[Any] = []
        try:
            for policy_name in paginate(
                self.iam, "list_role_policies", "PolicyNames", RoleName=role_name
            ):
                response = self.iam.get_role_policy(
                    RoleName=role_name, PolicyName=policy_name
                )
                documents.append(response.get("PolicyDocument"))
        except ClientError as exc:
            self._swallow("list_role_policies", role_name, exc)

        try:
            for attached in paginate(
                self.iam,
                "list_attached_role_policies",
                "AttachedPolicies",
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

    # -- running ------------------------------------------------------------

    def run(self) -> List[Finding]:
        if not self.session:
            print(
                "No AWS credentials. Every check on this day reads AWS — unlike "
                "Day 05, none of this is visible in the Terraform. Try --profile "
                "bootcamp, or run `aws configure --profile bootcamp`.",
                file=sys.stderr,
            )
            sys.exit(2)

        self.log("Collecting observability configuration...")
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
        prog="obs_audit.py",
        description=(
            "Audit CloudWatch observability and the AI incident-analysis path "
            "built on it — unretained log groups, alarms that notify nobody, "
            "composite alarms that cannot fire, dashboards pointing at metrics "
            "that do not exist, and model invocations with no budget, no "
            "redaction and no audit trail."
        ),
        epilog=(
            "Examples:\n"
            "  obs_audit.py --profile bootcamp --region us-east-1\n"
            "  obs_audit.py --prefix cbc-day06   # only this lab's resources\n"
            "  obs_audit.py --format json --quiet > findings.json\n"
            "  obs_audit.py --min-severity HIGH --format csv\n"
            "  obs_audit.py --fail-on CRITICAL   # exit 1 on any CRITICAL\n"
            "\n"
            "All sixteen checks read AWS. Read-only credentials are enough.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI named profile. Day 01 created 'bootcamp'.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region to audit (default: us-east-1).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help=(
            "Only examine resources whose name starts with this prefix, e.g. "
            "cbc-day06. Omit to audit everything in the region — which is the "
            "run worth doing, and the one that finds the log group from 2019."
        ),
    )
    parser.add_argument(
        "--min-severity",
        choices=SEVERITY_ORDER,
        default="INFO",
        help="Only report findings at this severity or worse (default: INFO).",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output on stderr. Use when piping stdout.",
    )
    parser.add_argument(
        "--fail-on",
        choices=SEVERITY_ORDER,
        default=None,
        help=(
            "Exit with code 1 if any finding is at this severity or worse. "
            "Use in CI to block a merge."
        ),
    )
    parser.add_argument(
        "--no-colour",
        "--no-color",
        dest="no_colour",
        action="store_true",
        help="Disable ANSI colour even on a TTY.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    use_colour = sys.stdout.isatty() and not args.no_colour and args.format == "table"

    auditor = ObservabilityAuditor(
        profile=args.profile,
        region=args.region,
        prefix=args.prefix,
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
    # Filtering the display should never flatter the score; otherwise people
    # "improve" their posture by passing --min-severity CRITICAL.
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
