#!/usr/bin/env python3
"""
obs_audit.py — Day 06 observability auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits CloudWatch observability — log groups, metric filters, alarms,
dashboards — and the AI incident-analysis path built on top of it, for the
faults that make a monitoring stack look healthy while measuring nothing.

Day 05's auditor read files. This one reads AWS, because none of what it looks
for is visible in the Terraform: whether an alarm has ever had data, whether a
dashboard points at a metric that exists, whether the model your Lambda is
permitted to invoke is the one you scoped it to. All sixteen checks need
credentials, and read-only ones are enough.

The bias this tool has, stated up front: it is much more interested in
observability that is SILENTLY BROKEN than in observability that is missing.
A team with no alarms knows it has no alarms. A team with an alarm stuck in
INSUFFICIENT_DATA since a field rename four months ago believes it is covered,
and that belief is the thing that costs them.

What it checks
--------------
    OBS-001  Log group with no retention   ingested once, stored forever   HIGH
    OBS-002  Write-only log group          paid for, never read            MEDIUM
    OBS-003  Unbounded dimension           undeletable metrics, 15 months  CRITICAL
    OBS-004  Alarm notifies nobody         red in a console nobody opens   HIGH
    OBS-005  treat_missing_data default    the outage that goes grey       MEDIUM
    OBS-006  Alarm on a raw count          means different things by hour  MEDIUM
    OBS-007  Composite alarm cannot fire   green, billed, unsatisfiable    HIGH
    OBS-008  Dashboard metric missing      a flat line reads as good news  MEDIUM
    OBS-009  No liveness alarm anywhere    nothing detects silence         HIGH
    OBS-010  Single-datapoint alarm        pages on one unlucky minute     LOW
    OBS-011  Raw log text in the prompt    secrets you did not know you had CRITICAL
    OBS-012  No token budget               one flap away from four figures HIGH
    OBS-013  Log data leaves the region    a residency question, unasked   HIGH
    OBS-014  bedrock:InvokeModel on "*"    a blank cheque against any model CRITICAL
    OBS-015  Analyser log group unretained the observability tool that is
                                           not observable                  MEDIUM
    OBS-016  No model invocation logging   nothing records what you sent   MEDIUM

One deliberate difference from iac_audit.py
-------------------------------------------
Every check here takes the SAME argument: a normalised `stack` dict and a
region. Day 05 mixed per-directory and per-resource signatures because its
checks genuinely operated on different things.

Day 06's do not. Half of these checks need cross-resource context to be
correct — OBS-004 has to resolve composite alarm rules before it can call an
actionless alarm a fault, OBS-001 has to know which functions invoke Bedrock
before it can hand a log group to OBS-015 — and a signature that only passes
one resource makes that context impossible without a global. One shape, one
argument, and every check remains a pure function: dict in, list of Finding
out, no credentials, no network, testable in a millisecond.

=============================================================================
DAY 06 FINDING CONTRACT — LOCKED AT CP2
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
lab/python/obs_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03, 04 and 05:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, enable_bedrock_invocation_logging = false),
before anything has been invoked.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  OBS-001  HIGH      10   1   10  aws_cloudwatch_log_group.unretained
  OBS-002  MEDIUM     4   2    8  aws_cloudwatch_log_group.unretained
                                  aws_cloudwatch_log_group.write_only
  OBS-003  CRITICAL  25   1   25  aws_cloudwatch_log_metric_filter.high_cardinality
  OBS-004  HIGH      10   1   10  aws_cloudwatch_metric_alarm.orphan
  OBS-005  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-006  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-007  HIGH      10   1   10  aws_cloudwatch_composite_alarm.impossible
  OBS-008  MEDIUM     4   1    4  aws_cloudwatch_dashboard.broken
  OBS-009  HIGH      10   0    0  none — SILENT BY SITUATION, see below
  OBS-010  LOW        1   1    1  aws_cloudwatch_metric_alarm.orphan
  OBS-011  CRITICAL  25   1   25  aws_lambda_function.naive_analyser
  OBS-012  HIGH      10   1   10  aws_lambda_function.naive_analyser
  OBS-013  HIGH      10   0    0  none — SILENT BY DESIGN, see below
  OBS-014  CRITICAL  25   1   25  aws_iam_role_policy.naive_analyser
  OBS-015  MEDIUM     4   1    4  aws_cloudwatch_log_group.naive_analyser
  OBS-016  MEDIUM     4   1    4  account-level Bedrock invocation logging
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    15  144

  FIFTEEN findings from SIXTEEN checks. Check count and finding count are not
  the same number and never will be: OBS-002 fires twice, and OBS-009 and
  OBS-013 do not fire at all. If you are reconciling this table against a real
  run, reconcile the N column, not the number of rows.

  Score: 100 - 144 = -44, floored to 0/100. Grade F.

THE THREE STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  Static: after apply, before anything runs          15     144    0/100      F
  Live: after lab steps 1-6 — incident
    generated, alarms transitioned, composite
    proven, both analysers run                       15     144    0/100      F
  After lab step 8 — bedrock_region pointed at
    another region, and the no-telemetry
    alarm's treat_missing_data changed to
    notBreaching outside Terraform                   18     174    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  Reference build: create_insecure_examples =
    false AND enable_bedrock_invocation_logging
    = true                                            0       0  100/100      A

  STATIC AND LIVE ARE IDENTICAL, AND THAT IS THE POINT. obs_audit.py audits
  CONFIGURATION, not runtime. Generating a real incident, watching three
  alarms transition, paging yourself and running both analysers changes
  nothing in its output. A
  configuration auditor and a monitoring system answer different questions,
  and treating either one as the other is the category error this day exists
  to prevent.

  Setting create_insecure_examples = false on its own leaves exactly one
  finding — OBS-016 — for 4 points and 96/100, grade A. Both toggles are
  needed for 100/100, and turning invocation logging on obliges you to set
  retention and a resource policy on its destination log group. That is
  stated in the variable description and it is not optional.

  Step 8 adds THREE findings, not two: OBS-009 once, and OBS-013 twice.
  bedrock_region is a single variable feeding BOTH analysers, so pointing it
  at another region moves the good one's log data as well as the naive one's.
  That is worth noticing — the misconfiguration is in a shared setting, and a
  shared setting does not care which of your functions was carefully written.

SILENT BY DESIGN — OBS-013, log data crossing a region boundary to reach the
model. bedrock_region defaults to the empty string, which resolves to
aws_region, and the model ARN in the analyser's IAM policy is built from that
same resolved value. No combination of shipped defaults can put the logs and
the model in different regions. The check fires only if you edit a variable on
purpose, which lab step 8 asks you to do. A check that stays silent because
the stack cannot produce the misconfiguration is evidence that the auditor
does not cry wolf.

SILENT BY SITUATION — OBS-009, no liveness alarm anywhere in the region. This
is silent only because aws_cloudwatch_metric_alarm.no_telemetry happens to
exist with treat_missing_data set to breaching. Nothing structural prevents it
firing. One attribute on one alarm, changed in the console in thirty seconds,
and it fires — which is exactly what lab step 8 does.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor. Silent by situation tells you nothing about the auditor and
everything about today's configuration. Never read the second as the first: a
check that is silent by situation must be re-run, never assumed.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  OBS-001 skips the log group of any function that holds bedrock:InvokeModel;
  OBS-015 owns those. An unretained analyser log group is ONE finding, not
  two.

  OBS-002 skips log groups under /aws/lambda/. A function's own execution log
  is a diagnostic artefact, not a data feed, and having no metric filter on it
  is correct rather than negligent.

  OBS-004 exempts alarms referenced by a composite alarm's rule. The three
  metric alarms in main.tf section 5 have no actions and are correct, because
  the composite in section 7 notifies on their behalf.

  OBS-006 exempts liveness alarms — treat_missing_data set to breaching with
  a LessThan comparison. A dead-man's switch is legitimately a raw count, and
  flagging it would be the auditor crying wolf about the best alarm in the
  stack.

  OBS-004's composite exemption only counts a composite that notifies AND
  whose rule can actually fire. An alarm watched solely by an unsatisfiable
  composite is exactly as silent as an orphan — and worse, because a reviewer
  scanning for orphans sees the reference and moves on. So OBS-007 firing on a
  composite also makes OBS-004 fire on its children. In this stack that is
  precisely what happens: the orphan alarm IS referenced, by the deliberately
  impossible composite, and is still reported as notifying nobody. Cause and
  consequence, not duplicates — fixing the rule clears both.
=============================================================================
This block is reproduced identically in five places. Change one, change all
five: README.md, lab/README.md, lab/terraform/outputs.tf (next_steps),
lab/python/obs_audit.py (module docstring), lab/python/tests/test_checks.py.

Weights are the repo-wide ones, identical to Days 03, 04 and 05:
CRITICAL 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0. Score is 100 minus the sum,
floored at 0. Grades: 90+ A, 75+ B, 60+ C, 40+ D, below that F.

STATIC STATE — after terraform apply with the shipped defaults
(create_insecure_examples = true, enable_bedrock_invocation_logging = false),
before anything has been invoked.

  ID       SEVERITY   W   N  PTS  SOURCE RESOURCE
  -------  --------  --  --  ---  ------------------------------------------
  OBS-001  HIGH      10   1   10  aws_cloudwatch_log_group.unretained
  OBS-002  MEDIUM     4   2    8  aws_cloudwatch_log_group.unretained
                                  aws_cloudwatch_log_group.write_only
  OBS-003  CRITICAL  25   1   25  aws_cloudwatch_log_metric_filter.high_cardinality
  OBS-004  HIGH      10   1   10  aws_cloudwatch_metric_alarm.orphan
  OBS-005  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-006  MEDIUM     4   1    4  aws_cloudwatch_metric_alarm.orphan
  OBS-007  HIGH      10   1   10  aws_cloudwatch_composite_alarm.impossible
  OBS-008  MEDIUM     4   1    4  aws_cloudwatch_dashboard.broken
  OBS-009  HIGH      10   0    0  none — SILENT BY SITUATION, see below
  OBS-010  LOW        1   1    1  aws_cloudwatch_metric_alarm.orphan
  OBS-011  CRITICAL  25   1   25  aws_lambda_function.naive_analyser
  OBS-012  HIGH      10   1   10  aws_lambda_function.naive_analyser
  OBS-013  HIGH      10   0    0  none — SILENT BY DESIGN, see below
  OBS-014  CRITICAL  25   1   25  aws_iam_role_policy.naive_analyser
  OBS-015  MEDIUM     4   1    4  aws_cloudwatch_log_group.naive_analyser
  OBS-016  MEDIUM     4   1    4  account-level Bedrock invocation logging
  -------  --------  --  --  ---  ------------------------------------------
  TOTALS                    16  144

  Sixteen findings from sixteen checks is a COINCIDENCE, not a mapping.
  OBS-002 fires twice; OBS-009 and OBS-013 do not fire at all.

  Score: 100 - 144 = -44, floored to 0/100. Grade F.

THE THREE STATES

  STATE                                        FINDINGS  POINTS    SCORE  GRADE
  -------------------------------------------  --------  ------  -------  -----
  Static: after apply, before anything runs          16     144    0/100      F
  Live: after lab steps 1-6 — incident
    generated, alarms transitioned, composite
    proven, both analysers run                       16     144    0/100      F
  After lab step 8 — bedrock_region pointed at
    another region, and the no-telemetry
    alarm's treat_missing_data changed to
    notBreaching outside Terraform                   18     164    0/100      F
  -------------------------------------------  --------  ------  -------  -----
  Reference build: create_insecure_examples =
    false AND enable_bedrock_invocation_logging
    = true                                            0       0  100/100      A

  STATIC AND LIVE ARE IDENTICAL, AND THAT IS THE POINT. obs_audit.py audits
  CONFIGURATION, not runtime. Generating a real incident, watching three
  alarms transition, paging yourself and running both analysers changes
  nothing in its output. A
  configuration auditor and a monitoring system answer different questions,
  and treating either one as the other is the category error this day exists
  to prevent.

  Setting create_insecure_examples = false on its own leaves exactly one
  finding — OBS-016 — for 4 points and 96/100, grade A. Both toggles are
  needed for 100/100, and turning invocation logging on obliges you to set
  retention and a resource policy on its destination log group. That is
  stated in the variable description and it is not optional.

SILENT BY DESIGN — OBS-013, log data crossing a region boundary to reach the
model. bedrock_region defaults to the empty string, which resolves to
aws_region, and the model ARN in the analyser's IAM policy is built from that
same resolved value. No combination of shipped defaults can put the logs and
the model in different regions. The check fires only if you edit a variable on
purpose, which lab step 8 asks you to do. A check that stays silent because
the stack cannot produce the misconfiguration is evidence that the auditor
does not cry wolf.

SILENT BY SITUATION — OBS-009, no liveness alarm anywhere in the region. This
is silent only because aws_cloudwatch_metric_alarm.no_telemetry happens to
exist with treat_missing_data set to breaching. Nothing structural prevents it
firing. One attribute on one alarm, changed in the console in thirty seconds,
and it fires — which is exactly what lab step 8 does.

THE DIFFERENCE MATTERS. Silent by design tells you something about the
auditor. Silent by situation tells you nothing about the auditor and
everything about today's configuration. Never read the second as the first: a
check that is silent by situation must be re-run, never assumed.

CHECK INTERACTIONS THAT ARE DELIBERATE, NOT BUGS

  OBS-001 skips the log group of any function that holds bedrock:InvokeModel;
  OBS-015 owns those. An unretained analyser log group is ONE finding, not
  two.

  OBS-002 skips log groups under /aws/lambda/. A function's own execution log
  is a diagnostic artefact, not a data feed, and having no metric filter on it
  is correct rather than negligent.

  OBS-004 exempts alarms referenced by a composite alarm's rule. The three
  metric alarms in main.tf section 5 have no actions and are correct, because
  the composite in section 7 notifies on their behalf.

  OBS-006 exempts liveness alarms — treat_missing_data set to breaching with
  a LessThan comparison. A dead-man's switch is legitimately a raw count, and
  flagging it would be the auditor crying wolf about the best alarm in the
  stack.
=============================================================================

Usage
-----
    obs_audit.py --profile bootcamp --region us-east-1
    obs_audit.py --format json --quiet > findings.json
    obs_audit.py --format csv --min-severity HIGH
    obs_audit.py --fail-on CRITICAL   # exit 1 on any CRITICAL

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


def check_log_retention(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-001 — a log group with no retention policy.

    "Never expire" is not a setting anyone chose. It is the default for every
    log group AWS creates on your behalf — Lambda, ECS, API Gateway, EKS, RDS —
    and the failure is an ABSENCE, so it never appears in a diff and never gets
    reviewed.

    Ingestion is $0.50/GB once. Storage is $0.03/GB-month forever. Neither
    number is large; "forever" is what makes it. A service logging 1 GB/day
    costs $15/month to ingest and, a year later, $110/month to store, still
    climbing, invisible in the console unless you go looking, and folded into a
    single "CloudWatch" line in Cost Explorer.

    Skips the log groups of Bedrock-invoking functions. OBS-015 owns those, so
    an unretained analyser log group is one finding rather than two.
    """
    findings: List[Finding] = []
    owned_by_obs_015 = bedrock_log_group_names(stack)

    for group in stack.get("log_groups") or []:
        name = group.get("logGroupName", "")
        if name in owned_by_obs_015:
            continue
        if group.get("retentionInDays"):
            continue

        stored = group.get("storedBytes", 0) or 0
        findings.append(
            Finding(
                check_id="OBS-001",
                severity="HIGH",
                resource_type="AWS::Logs::LogGroup",
                resource_id=name,
                title="Log group has no retention policy",
                detail=(
                    f"{name} has retention set to Never expire and currently holds "
                    f"{stored:,} bytes. Storage bills at $0.03/GB-month with no end "
                    f"date, for data nobody will read after this week. Nothing "
                    f"in the console flags this and nothing in a `terraform plan` "
                    f"shows it, because the fault is a missing attribute."
                ),
                remediation=(
                    f"`aws logs put-retention-policy --log-group-name {name} "
                    f"--retention-in-days 30`, and set retention_in_days in the "
                    f"Terraform so it does not come back. If you need the data "
                    f"longer than 90 days, move it to S3 with a subscription "
                    f"filter at $0.023/GB-month rather than keeping it in "
                    f"CloudWatch Logs at $0.03."
                ),
                evidence={
                    "logGroupName": name,
                    "retentionInDays": group.get("retentionInDays"),
                    "storedBytes": stored,
                    "logGroupClass": group.get("logGroupClass", "STANDARD"),
                },
                region=region,
            )
        )
    return findings


def check_write_only_log_group(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-002 — a log group nothing ever reads.

    No metric filter, no subscription filter, no alarm derived from it. Data
    goes in at $0.50/GB and nothing ever comes out.

    This is the most common form of expensive theatre in observability. The
    team is definitely logging. The compliance checkbox is ticked. No signal
    from this group has ever reached a human, and the first time anyone opens
    it will be during an incident, when they will discover the field they need
    was never logged.

    The fix is usually not "add a filter". It is usually "stop logging this".

    Skips /aws/lambda/ groups. A function's own execution log is a diagnostic
    artefact, not a data feed; having no metric filter on it is correct rather
    than negligent, and flagging every Lambda in the account would bury the
    findings that matter.
    """
    findings: List[Finding] = []

    with_filters = {
        f.get("logGroupName") for f in stack.get("metric_filters") or []
    }
    with_subscriptions = {
        s.get("logGroupName") for s in stack.get("subscription_filters") or []
    }

    for group in stack.get("log_groups") or []:
        name = group.get("logGroupName", "")
        if name.startswith(LAMBDA_LOG_PREFIX):
            continue
        if name in with_filters or name in with_subscriptions:
            continue

        findings.append(
            Finding(
                check_id="OBS-002",
                severity="MEDIUM",
                resource_type="AWS::Logs::LogGroup",
                resource_id=name,
                title="Log group is write-only — nothing reads it",
                detail=(
                    f"{name} has no metric filter and no subscription filter. "
                    f"Every byte written to it is billed at $0.50/GB of ingestion "
                    f"and $0.03/GB-month of storage, and no alarm, dashboard or "
                    f"downstream system consumes any of it. It is a backup nobody "
                    f"tests, paid for monthly."
                ),
                remediation=(
                    f"Decide which it is. If the data matters, attach a metric "
                    f"filter so something can alarm on it, or a subscription "
                    f"filter so it reaches a system that will. If it does not "
                    f"matter — which is the usual answer — turn the logging off "
                    f"at the source rather than paying to store it. Deleting the "
                    f"group alone just means it gets recreated on the next write, "
                    f"without retention."
                ),
                evidence={
                    "logGroupName": name,
                    "metricFilterCount": 0,
                    "subscriptionFilterCount": 0,
                    "retentionInDays": group.get("retentionInDays"),
                },
                region=region,
            )
        )
    return findings


def check_metric_filter_cardinality(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-003 — a metric filter dimension whose cardinality is unbounded.

    The single most expensive mistake in this file, and one that looks helpful
    in review. `dimensions = { RequestId = "$.request_id" }` reads as "break
    the metric down by request so we can trace it", and the person writing it
    is trying to be thorough.

    What it does is create ONE CUSTOM METRIC PER UNIQUE VALUE, at $0.30 per
    metric per month. Forty thousand requests in an afternoon is forty thousand
    custom metrics: $12,000/month.

    And it cannot be undone. There is no DeleteMetric API. A custom metric ages
    out fifteen months after its last datapoint and not one day sooner — no
    console button, no support ticket, no exception. You will be paying for a
    single line of Terraform into the year after next.

    The check is a name heuristic and says so in its evidence. The rule it
    encodes is not: a dimension value must come from a set you could write down
    on a napkin.
    """
    findings: List[Finding] = []

    for filt in stack.get("metric_filters") or []:
        for transform in filt.get("metricTransformations") or []:
            dimensions = transform.get("dimensions") or {}
            risky = {
                key: value
                for key, value in dimensions.items()
                if any(
                    hint in str(value).lower() or hint in str(key).lower()
                    for hint in HIGH_CARDINALITY_HINTS
                )
            }
            if not risky:
                continue

            findings.append(
                Finding(
                    check_id="OBS-003",
                    severity="CRITICAL",
                    resource_type="AWS::Logs::MetricFilter",
                    resource_id=filt.get("filterName", "unknown"),
                    title="Metric filter dimension has unbounded cardinality",
                    detail=(
                        f"Filter {filt.get('filterName')} on {filt.get('logGroupName')} "
                        f"publishes {transform.get('metricNamespace')}/"
                        f"{transform.get('metricName')} with dimensions {risky}. Each "
                        f"distinct value creates a separate custom metric at "
                        f"$0.30/month, and custom metrics CANNOT BE DELETED — they "
                        f"age out fifteen months after their last datapoint. A "
                        f"moderately busy service can create tens of thousands of "
                        f"them in an afternoon."
                    ),
                    remediation=(
                        "Delete this metric filter now — every hour it runs adds "
                        "metrics you will pay for until the year after next. Then "
                        "re-add it with a dimension whose values come from a small "
                        "fixed set (status code, error type, environment), and keep "
                        "the identifier in the log line where Logs Insights can "
                        "query it for $0.005/GB scanned and nothing accumulates."
                    ),
                    evidence={
                        "filterName": filt.get("filterName"),
                        "logGroupName": filt.get("logGroupName"),
                        "dimensions": dimensions,
                        "matched_hints": sorted(risky.keys()),
                        "note": (
                            "Name heuristic. A field this check does not recognise "
                            "can still be unbounded; review every dimension you add."
                        ),
                    },
                    region=region,
                )
            )
    return findings


def check_alarm_without_action(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-004 — an alarm that notifies nobody.

    It evaluates. It transitions. It turns red in a console nobody has open. It
    tells no one, ever. The usual origin is somebody building an alarm during
    an incident to watch something, meaning to wire the topic up afterwards,
    and not.

    EXEMPTS alarms referenced by a composite alarm's rule. That exemption is
    why this check is more than a null test: the three metric alarms in
    main.tf section 5 have no actions and are CORRECT, because the composite in
    section 7 notifies on their behalf. An auditor that cannot tell those apart
    from a genuine orphan trains people to ignore it.

    Also treats ActionsEnabled = false as no action, because it is. An alarm
    with a topic attached and actions disabled is the same silence with better
    camouflage.

    The exemption only counts composites that ARE real cover — one that
    notifies and whose rule can actually fire. An alarm watched only by an
    unsatisfiable composite is exactly as silent as an orphan, and worse,
    because a reviewer scanning for orphans sees the reference and moves on.
    That is why OBS-007 firing on a composite also makes OBS-004 fire on its
    children: cause and consequence, not duplicates. Fixing the rule clears
    both.
    """
    findings: List[Finding] = []
    referenced = alarms_referenced_by_composites(stack)

    for alarm in stack.get("metric_alarms") or []:
        name = alarm.get("AlarmName", "")
        if _has_notification(alarm):
            continue
        if name in referenced:
            continue

        disabled = alarm.get("ActionsEnabled") is False
        findings.append(
            Finding(
                check_id="OBS-004",
                severity="HIGH",
                resource_type="AWS::CloudWatch::Alarm",
                resource_id=name,
                title="Alarm notifies nobody",
                detail=(
                    f"{name} has "
                    + (
                        "actions configured but ActionsEnabled is false"
                        if disabled
                        else "no alarm, OK or insufficient-data actions"
                    )
                    + ", and no composite alarm references it. When it "
                    f"transitions, the console turns red and nothing else happens. "
                    f"It is currently in state {alarm.get('StateValue', 'UNKNOWN')}."
                ),
                remediation=(
                    f"Either give it an SNS action — `aws cloudwatch put-metric-alarm "
                    f"--alarm-name {name} --alarm-actions <topic-arn>` — or make it "
                    f"an input to a composite alarm that does notify, which is the "
                    f"better pattern when several signals describe one incident. If "
                    f"it is genuinely diagnostic and nobody should ever be paged for "
                    f"it, delete it: an alarm nobody acts on is $0.10/month of "
                    f"decoration and it dilutes the ones that matter."
                ),
                evidence={
                    "AlarmName": name,
                    "AlarmActions": alarm.get("AlarmActions"),
                    "OKActions": alarm.get("OKActions"),
                    "ActionsEnabled": alarm.get("ActionsEnabled"),
                    "referenced_by_composite": False,
                },
                region=region,
            )
        )
    return findings


def check_treat_missing_data(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-005 — treat_missing_data left at the default.

    The default is "missing": periods with no datapoint are ignored, and
    CloudWatch looks further back for enough real data to decide. If it never
    finds any, the alarm goes to INSUFFICIENT_DATA and STAYS THERE. It will not
    notify, it is not red, and on a dashboard it is a polite grey.

    That is how an alarm on a metric that stopped being published — because the
    service died, or a deploy renamed the field its metric filter matched, or
    an IAM change revoked logs:PutLogEvents — becomes permanently, silently
    useless. The alarm is fine. The thing it was watching is gone.

    AN HONEST LIMITATION: the CloudWatch API returns "missing" both for an
    alarm that never set the attribute and for one that set it deliberately.
    They are indistinguishable from outside. This check flags both, and the
    remediation says what to do if you meant it.
    """
    findings: List[Finding] = []

    for alarm in stack.get("metric_alarms") or []:
        setting = alarm.get("TreatMissingData")
        if setting and setting != "missing":
            continue

        name = alarm.get("AlarmName", "")
        findings.append(
            Finding(
                check_id="OBS-005",
                severity="MEDIUM",
                resource_type="AWS::CloudWatch::Alarm",
                resource_id=name,
                title="Alarm leaves treat_missing_data at the default",
                detail=(
                    f"{name} treats missing data as {setting or 'missing (unset)'}. "
                    f"Periods with no datapoint are ignored, so if this metric ever "
                    f"stops being published the alarm drifts to INSUFFICIENT_DATA "
                    f"and stays there — quiet, grey, and indistinguishable on a "
                    f"dashboard from healthy. The most common cause is a deploy "
                    f"renaming a field a metric filter matched on."
                ),
                remediation=(
                    "Choose deliberately. notBreaching when absence means health "
                    "(an error count with no datapoints). breaching when silence is "
                    "the bad news — that is a dead-man's switch and every stack "
                    "needs at least one. ignore to hold the last state on a bursty "
                    "metric. If you genuinely meant 'missing', set it explicitly and "
                    "say why in the alarm description, because from outside nobody "
                    "can tell the choice from the default."
                ),
                evidence={
                    "AlarmName": name,
                    "TreatMissingData": setting,
                    "StateValue": alarm.get("StateValue"),
                },
                region=region,
            )
        )
    return findings


def check_alarm_on_raw_count(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-006 — an alarm thresholded on a raw count instead of a rate.

    "More than 50 errors in 5 minutes" is a different statement at 03:00 than
    at midday. It fires on a traffic spike where nothing is wrong, and it goes
    quiet during the outage where traffic collapsed — which is the failure that
    matters, because that is the alarm you were relying on.

    5% of requests failing is 5% of requests failing at every hour of the day,
    and the number means the same thing to whoever reads the page as it did to
    whoever set it.

    EXEMPTS metric-math alarms, because an alarm built on an expression is
    almost always computing a rate or a ratio, and EXEMPTS liveness alarms —
    treat_missing_data breaching with a LessThan comparison. A dead-man's
    switch is legitimately a raw count; that is the entire idea.
    """
    findings: List[Finding] = []

    for alarm in stack.get("metric_alarms") or []:
        if _uses_metric_math(alarm):
            continue
        if _is_liveness_alarm(alarm):
            continue

        statistic = alarm.get("Statistic")
        comparison = str(alarm.get("ComparisonOperator", ""))
        if statistic not in ("Sum", "SampleCount"):
            continue
        if not comparison.startswith("GreaterThan"):
            continue

        name = alarm.get("AlarmName", "")
        findings.append(
            Finding(
                check_id="OBS-006",
                severity="MEDIUM",
                resource_type="AWS::CloudWatch::Alarm",
                resource_id=name,
                title="Alarm thresholds on a raw count, not a rate",
                detail=(
                    f"{name} alarms when the {statistic} of "
                    f"{alarm.get('Namespace')}/{alarm.get('MetricName')} is "
                    f"{comparison} {alarm.get('Threshold')}. A fixed count is a "
                    f"different statement at every traffic level: it fires on growth "
                    f"where nothing is wrong, and it falls silent during an outage "
                    f"that collapses traffic — exactly when you needed it."
                ),
                remediation=(
                    "Rebuild it as a rate using metric math over two metrics — the "
                    "error count and the request count — with an expression like "
                    "IF(r > 0, 100 * e / r, 0). See the error-rate alarm in main.tf "
                    "section 5a for the exact five-block shape, including the FILL() "
                    "calls that stop a quiet period being decided by "
                    "treat_missing_data instead of by your expression."
                ),
                evidence={
                    "AlarmName": name,
                    "Statistic": statistic,
                    "Threshold": alarm.get("Threshold"),
                    "ComparisonOperator": comparison,
                    "MetricName": alarm.get("MetricName"),
                },
                region=region,
            )
        )
    return findings


def check_composite_alarm_satisfiable(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-007 — a composite alarm whose rule can never fire.

    The rule language accepts anything syntactically valid, including rules
    that are logically impossible. `ALARM(x) AND OK(x)` is accepted, created,
    billed at $0.50/month, shows a reassuring green in the console, and cannot
    transition under any circumstances.

    The version that ships in real repositories is subtler: an AND across two
    conditions that never co-occur, written by somebody reducing noise, tested
    by nobody, discovered eighteen months later during a postmortem.

    Two ways to be unfireable, and this finds both:
      * A contradiction — the same alarm required to be in two states at once
        within an AND chain.
      * A dangling reference — the rule names an alarm that does not exist, so
        the composite sits in INSUFFICIENT_DATA permanently.

    Neither is caught by validation, a plan, or a review. The only proof that a
    composite alarm works is forcing a child into ALARM with
    `aws cloudwatch set-alarm-state` and watching the parent. That takes ninety
    seconds and it is Step 5 of the lab.
    """
    findings: List[Finding] = []
    known = known_alarm_names(stack)

    for composite in stack.get("composite_alarms") or []:
        rule = composite.get("AlarmRule", "") or ""
        name = composite.get("AlarmName", "")
        refs = _ALARM_STATE_REF.findall(rule)
        problems = composite_rule_problems(rule, known)

        if not problems:
            continue

        findings.append(
            Finding(
                check_id="OBS-007",
                severity="HIGH",
                resource_type="AWS::CloudWatch::CompositeAlarm",
                resource_id=name,
                title="Composite alarm rule can never fire",
                detail=(
                    f"{name} has rule `{rule}`, which {'; '.join(problems)}. "
                    f"CloudWatch accepts it, creates it, bills $0.50/month for it "
                    f"and displays a comforting green OK. It has never transitioned "
                    f"and it never will."
                ),
                remediation=(
                    f"Fix the rule, then PROVE it: `aws cloudwatch set-alarm-state "
                    f"--alarm-name <a-child-alarm> --state-value ALARM --state-reason "
                    f"test` and confirm {name} follows within seconds. Nothing else "
                    f"— not a plan, not a review, not validation — distinguishes a "
                    f"composite alarm that works from one that cannot."
                ),
                evidence={
                    "AlarmName": name,
                    "AlarmRule": rule,
                    "problems": problems,
                    "referenced_alarms": sorted({r[1].strip() for r in refs}),
                },
                region=region,
            )
        )
    return findings


def check_dashboard_metrics_exist(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-008 — a dashboard widget referencing a metric that does not exist.

    CloudWatch dashboards do not validate metric references. A widget naming a
    namespace and metric that were never published renders as an empty graph
    with a legend, which looks exactly like "nothing has happened yet".

    That is how a dashboard survives a refactor. Somebody renames a metric, the
    widget keeps rendering, the line is flat, and for four months everyone
    reads the flat line as good news. The dashboard is not wrong in any way a
    human notices — it is answering a question about a metric nobody publishes.

    The check is mechanical: enumerate what the widgets reference, compare
    against ListMetrics, report the difference. It belongs in CI.

    Note it deliberately reports ONE finding per dashboard rather than one per
    missing metric. A dashboard with nine dead widgets is one thing to fix.
    """
    findings: List[Finding] = []
    existing = {
        (m.get("Namespace"), m.get("MetricName"))
        for m in stack.get("existing_metrics") or []
    }

    for dashboard in stack.get("dashboards") or []:
        name = dashboard.get("DashboardName", "")
        refs = _dashboard_metric_refs(dashboard.get("DashboardBody"))
        missing = sorted({ref for ref in refs if ref not in existing})
        if not missing:
            continue

        findings.append(
            Finding(
                check_id="OBS-008",
                severity="MEDIUM",
                resource_type="AWS::CloudWatch::Dashboard",
                resource_id=name,
                title="Dashboard references metrics that do not exist",
                detail=(
                    f"{name} has {len(missing)} widget metric(s) with no datapoints "
                    f"ever published: "
                    + ", ".join(f"{ns}/{mn}" for ns, mn in missing[:5])
                    + (" and others" if len(missing) > 5 else "")
                    + f". Those widgets render as empty graphs with a legend, which "
                    f"is visually indistinguishable from a healthy flat line."
                ),
                remediation=(
                    "Fix or delete the widgets. Then add this check to CI, because "
                    "the failure mode is a rename in one repository silently "
                    "blanking a dashboard in another, and there is no signal at all "
                    "when it happens. If a metric is genuinely not published yet, "
                    "say so in a text widget beside it rather than leaving an empty "
                    "graph for someone to misread during an incident."
                ),
                evidence={
                    "DashboardName": name,
                    "missing_metrics": [f"{ns}/{mn}" for ns, mn in missing],
                    "referenced_total": len(refs),
                },
                region=region,
            )
        )
    return findings


def check_liveness_alarm_exists(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-009 — nothing in this region detects silence.

    Every other alarm in a typical stack answers "is the data bad". None of
    them answers "is there any data", and those are different questions with
    different failure modes.

    When a service crashes on boot, a log driver breaks, a deploy renames the
    field a metric filter matched, or an IAM change revokes logs:PutLogEvents,
    the metrics simply stop. Error-rate alarms see no errors. Latency alarms
    see no slow requests. Both sit in a comfortable OK — or drift to
    INSUFFICIENT_DATA and go grey — while the service is dark.

    treat_missing_data = "breaching" inverts that: missing data IS the breach.
    It is the one place where the setting people are warned away from is
    exactly right.

    A NOTE ON WHY THIS CHECK MAY BE SILENT. This is a whole-stack check, and it
    is silent on the lab's stack because one alarm happens to be built
    correctly. That is SILENT BY SITUATION, not by design — nothing structural
    prevents it firing, and one attribute changed in the console brings it
    back. Do not read its silence as coverage; re-run it.

    Fires once, not once per alarm: there is one thing missing, not N.
    """
    alarms = stack.get("metric_alarms") or []
    if not alarms:
        # An account with no alarms at all has a bigger problem than this
        # check, and reporting "no liveness alarm" would be noise on top of it.
        return []

    if any(_is_liveness_alarm(alarm) for alarm in alarms):
        return []

    return [
        Finding(
            check_id="OBS-009",
            severity="HIGH",
            resource_type="AWS::CloudWatch::Alarm",
            resource_id=f"(region {region or 'unknown'})",
            title="No alarm anywhere detects silence",
            detail=(
                f"None of the {len(alarms)} alarm(s) in this region uses "
                f"treat_missing_data = breaching with a LessThan comparison. Every "
                f"alarm here asks whether the data is bad; none asks whether there "
                f"is any data. A service that stops emitting entirely — crashed on "
                f"boot, broken log driver, revoked PutLogEvents, renamed field — "
                f"triggers none of them."
            ),
            remediation=(
                "Add one dead-man's switch per service: alarm on the Sum of your "
                "request-count metric being LessThanThreshold 1, with "
                "treat_missing_data = breaching and a longer evaluation window than "
                "your other alarms. See the no-telemetry alarm in main.tf section "
                "5c. Set the threshold from your genuinely quietest period — one "
                "that flaps every Sunday at 04:00 gets muted, and a muted "
                "dead-man's switch is worse than none because it looks like cover."
            ),
            evidence={
                "alarms_examined": len(alarms),
                "alarms_with_breaching_missing_data": 0,
                "note": (
                    "Whole-stack check. It fires once for the region, not once per "
                    "alarm, because there is one thing missing rather than N."
                ),
            },
            region=region,
        )
    ]


def check_single_datapoint_alarm(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-010 — an alarm that evaluates a single datapoint.

    evaluation_periods = 1 with datapoints_to_alarm unset or 1. One unlucky
    minute pages somebody, and the alarm resolves itself before they have found
    a laptop. Do that twice and the team has learned to ignore the topic, which
    is a far more expensive outcome than the alarm you were trying to build.

    3 of 5 means at least three of the last five minutes breached. A single bad
    minute does not page anyone; a genuinely degraded five minutes does.

    Do not reach for a longer averaging period instead — averaging hides the
    shape. Three catastrophic minutes and two perfect ones average out to
    "slightly elevated" and may not cross the threshold at all. M-of-N sees the
    three bad minutes for what they are.

    LOW, deliberately. It is a tuning fault, not a blindness fault, and rating
    it higher would drown OBS-001 and OBS-004 in noise.
    """
    findings: List[Finding] = []

    for alarm in stack.get("metric_alarms") or []:
        if alarm.get("EvaluationPeriods") != 1:
            continue
        if (alarm.get("DatapointsToAlarm") or 1) != 1:
            continue

        name = alarm.get("AlarmName", "")
        period = alarm.get("Period", 60)
        findings.append(
            Finding(
                check_id="OBS-010",
                severity="LOW",
                resource_type="AWS::CloudWatch::Alarm",
                resource_id=name,
                title="Alarm fires on a single datapoint",
                detail=(
                    f"{name} evaluates 1 of 1 datapoints over a {period}-second "
                    f"period, so a single breaching interval transitions it. On any "
                    f"metric with normal variance this pages somebody for a blip "
                    f"that has already resolved by the time they read the message."
                ),
                remediation=(
                    f"Set datapoints_to_alarm and evaluation_periods to something "
                    f"like 3 and 5 — `--datapoints-to-alarm 3 --evaluation-periods "
                    f"5` — so the alarm describes a degraded few minutes rather than "
                    f"one unlucky one. Keep the period at {period}; widening the "
                    f"period to smooth the noise hides the shape of the incident "
                    f"instead."
                ),
                evidence={
                    "AlarmName": name,
                    "EvaluationPeriods": alarm.get("EvaluationPeriods"),
                    "DatapointsToAlarm": alarm.get("DatapointsToAlarm"),
                    "Period": period,
                },
                region=region,
            )
        )
    return findings


def check_prompt_redaction(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-011 — raw log text goes into a model prompt with no redaction.

    A CloudWatch log line from a real system contains, routinely and without
    anyone intending it: bearer tokens, session cookies, connection strings,
    email addresses, full request bodies, and stack traces with local variables
    still in them. Nobody put them there on purpose. That is the point — you
    cannot decide not to send data you do not know you are logging.

    When a function interpolates that text straight into a prompt, all of it
    leaves for another service, and lands in the model invocation log if that
    is enabled, and appears in nobody's data-flow diagram.

    Redaction is not a solution and this check does not claim it is. A regex
    catches key shapes and token shapes; it does not catch a customer's name in
    a free-text field or a session identifier your framework invented. The real
    control is upstream — do not log the secret. Redaction is the seatbelt.

    Detected from configuration rather than code, because configuration is what
    actually ships. The good and bad analysers in this lab run the IDENTICAL
    zip file and differ in this one environment variable.
    """
    findings: List[Finding] = []

    for function in bedrock_functions(stack):
        env = _env(function)
        if str(env.get(ENV_REDACT, "")).lower() == "true":
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="OBS-011",
                severity="CRITICAL",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Log text reaches the model with no redaction",
                detail=(
                    f"{name} holds bedrock:InvokeModel and has {ENV_REDACT}="
                    f"{env.get(ENV_REDACT, '(unset)')}. Log lines are being placed "
                    f"in prompts verbatim. Whatever the application printed — "
                    f"tokens, connection strings, request bodies, customer data — "
                    f"is leaving for another service, and is recorded in the model "
                    f"invocation log if that is on."
                ),
                remediation=(
                    f"Set {ENV_REDACT}=true so obvious secret shapes are stripped "
                    f"before the prompt is built, and treat that as the seatbelt "
                    f"rather than the brakes. The durable fix is upstream: audit "
                    f"what the application logs, remove the secrets at the source, "
                    f"and write down which log groups are allowed to reach a model "
                    f"at all. Then decide who can read the invocation log, because "
                    f"that group now holds the same content."
                ),
                evidence={
                    "FunctionName": name,
                    ENV_REDACT: env.get(ENV_REDACT),
                    "role": function.get("_role_name"),
                },
                region=region,
            )
        )
    return findings


def check_token_budget(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-012 — a model-invoking function with no token budget.

    "The model can take 200,000 tokens" and "you should send it 200,000 tokens"
    are different claims and only the first is true.

      COST     At roughly $0.0008/1K input, one 200,000-token invocation is
               $0.16. Behind an alarm that flaps forty times an hour overnight
               that is $77 by breakfast, on a system that is not broken.
      LATENCY  Time-to-first-token scales with input. A summary that arrives
               after the incident is over is a postmortem, not a tool.
      QUALITY  The one people do not expect. Recall degrades over very long
               context: a specific line buried in 180,000 tokens is genuinely
               harder for a model to use than the same line in 12,000. Sending
               everything makes the answer worse, not just slower.

    Unbounded input also means unbounded spend from a single alarm transition,
    with no ceiling anywhere in the system. That is the shape of every
    surprise-bill story involving a model.
    """
    findings: List[Finding] = []

    for function in bedrock_functions(stack):
        env = _env(function)
        raw = env.get(ENV_MAX_TOKENS)
        try:
            budget = int(raw) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            budget = 0
        if budget > 0:
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="OBS-012",
                severity="HIGH",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Model invocation has no token budget",
                detail=(
                    f"{name} holds bedrock:InvokeModel and has {ENV_MAX_TOKENS}="
                    f"{raw if raw is not None else '(unset)'}. Prompt size is bounded "
                    f"only by how much the log window happens to contain, which is "
                    f"bounded only by how bad the incident is. Reserved concurrency "
                    f"is {function.get('ReservedConcurrentExecutions', 'unset')}, so "
                    f"nothing caps how many of these run at once either."
                ),
                remediation=(
                    f"Set {ENV_MAX_TOKENS} to a real ceiling — 12,000 is roughly 175 "
                    f"log lines and is enough for a whole incident when it is sampled "
                    f"properly — and make the function report how much it dropped, so "
                    f"a summary built from 4% of the evidence says so. Pair it with "
                    f"reserved concurrency and an idempotency window; a budget alone "
                    f"still lets a flapping alarm invoke you all night."
                ),
                evidence={
                    "FunctionName": name,
                    ENV_MAX_TOKENS: raw,
                    "ReservedConcurrentExecutions": function.get(
                        "ReservedConcurrentExecutions"
                    ),
                },
                region=region,
            )
        )
    return findings


def check_cross_region_model(stack: Dict[str, Any], region: str = "") -> List[Finding]:
    """OBS-013 — log data crosses a region boundary to reach the model.

    A data-residency question before it is a latency one, and the honest
    version of the answer is that most people who did this did not know they
    had. It arrives two ways:

      * Someone sets the client's region to wherever the model they wanted has
        capacity.
      * Someone switches to a cross-region inference profile — model IDs
        starting "us.", "eu." — which by design may route the request to
        another region in the group. That door is much less visible: the
        configuration says one region and the traffic goes to several.

    The data in question is application log content. Whatever your contracts,
    your privacy notice or your regulator say about where that may be
    processed, this is the line where it happens, and it is one environment
    variable deep.

    SILENT BY DESIGN against this lab's stack. bedrock_region defaults to empty
    and resolves to aws_region, and the model ARN in the analyser's IAM policy
    is built from that same resolved value, so no shipped default can separate
    them. The check fires only if you edit a variable on purpose, which Step 8
    of the lab asks you to do. A check that stays quiet because the stack
    cannot produce the fault is evidence the auditor does not cry wolf.
    """
    findings: List[Finding] = []

    for function in bedrock_functions(stack):
        env = _env(function)
        model_region = env.get(ENV_BEDROCK_REGION)
        if not model_region or not region:
            continue
        if model_region == region:
            continue

        name = function.get("FunctionName", "")
        findings.append(
            Finding(
                check_id="OBS-013",
                severity="HIGH",
                resource_type="AWS::Lambda::Function",
                resource_id=name,
                title="Log data leaves the region to reach the model",
                detail=(
                    f"{name} reads logs in {region} and invokes a model in "
                    f"{model_region}. Application log content — which contains "
                    f"whatever the application printed — is being processed outside "
                    f"the region it was collected in. Nothing in CloudWatch, the "
                    f"console or a plan surfaces this; it is one environment "
                    f"variable."
                ),
                remediation=(
                    f"Either set {ENV_BEDROCK_REGION} to {region} and request model "
                    f"access there, or get an explicit, written decision that log "
                    f"content may be processed in {model_region} and record it "
                    f"where an auditor will find it. Check the model ID too: an "
                    f"inference profile beginning 'us.' or 'eu.' may route to other "
                    f"regions in its group regardless of what this variable says."
                ),
                evidence={
                    "FunctionName": name,
                    "log_region": region,
                    ENV_BEDROCK_REGION: model_region,
                },
                region=region,
            )
        )
    return findings


def check_bedrock_resource_scope(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-014 — bedrock:InvokeModel granted on Resource "*".

    A blank cheque. The function may invoke ANY model in the account: ones
    nobody evaluated, ones with different data-handling terms, ones costing
    forty times more per token. A bug or a compromise turns that into spend
    with no ceiling and no allow-list.

    The origin is almost always the same, and it is not carelessness. The
    correctly-scoped ARN did not match, the error said AccessDeniedException
    and nothing else, twenty minutes went by, and "*" made it work. It shipped
    on a Friday and nobody revisited it.

    The reason it did not match is worth knowing, because it is the single most
    common Bedrock IAM mistake:

        WRONG  arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic...
        RIGHT  arn:aws:bedrock:us-east-1::foundation-model/anthropic...
                                        ^^ empty. The model is not yours.

    Foundation models are owned by the provider, so the account field is empty.
    Cross-region inference profiles ARE yours and do carry an account — and if
    you use one you need both ARNs, the profile and the foundation model in
    every region the profile can route to.
    """
    findings: List[Finding] = []
    role_policies = stack.get("role_policies") or {}
    seen: Set[str] = set()

    for function in bedrock_functions(stack):
        role_name = function.get("_role_name", "")
        if role_name in seen:
            continue
        for document in role_policies.get(role_name, []):
            granted, resources = policy_grants_bedrock_invoke(document)
            if not granted:
                continue
            wildcards = [r for r in resources if r == "*" or r.endswith(":*")]
            if not wildcards:
                continue
            seen.add(role_name)
            findings.append(
                Finding(
                    check_id="OBS-014",
                    severity="CRITICAL",
                    resource_type="AWS::IAM::Role",
                    resource_id=role_name,
                    title="bedrock:InvokeModel granted on a wildcard resource",
                    detail=(
                        f"Role {role_name}, used by "
                        f"{function.get('FunctionName')}, allows bedrock:InvokeModel "
                        f"on {wildcards}. That permits every model in the account, "
                        f"including ones nobody has evaluated and ones costing "
                        f"substantially more per token. There is no ceiling and no "
                        f"allow-list."
                    ),
                    remediation=(
                        "Scope the Resource to the exact model ARN — note the EMPTY "
                        "account field: arn:aws:bedrock:REGION::foundation-model/"
                        "MODEL-ID. Writing your account ID there is why the policy "
                        "did not match in the first place. If you use a cross-region "
                        "inference profile, list the profile ARN (which does carry "
                        "your account) AND the foundation-model ARN in every region "
                        "the profile can route to, or it fails intermittently."
                    ),
                    evidence={
                        "role": role_name,
                        "FunctionName": function.get("FunctionName"),
                        "resources": resources,
                    },
                    region=region,
                )
            )
            break
    return findings


def check_analyser_log_retention(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-015 — the observability tool that is not observable.

    The log group of a Bedrock-invoking function has no retention, or does not
    exist in Terraform at all so Lambda will create it on first invocation with
    "Never expire".

    A small irony and a real problem. When the summariser starts producing
    nonsense, its own logs are where you find out why — which prompt it built,
    how much it sampled, what the model returned before parsing failed. Here
    they accumulate forever at $0.03/GB-month, because nobody thinks of the
    tooling as a workload with an owner.

    A SPECIALISATION OF OBS-001, and OBS-001 deliberately skips the groups this
    check owns, so one unretained analyser log group is ONE finding rather than
    two. That is a contract decision written into the finding contract, and
    there is a test asserting it.

    MEDIUM rather than HIGH because the volume is small — this is a function
    that runs on alarm transitions, not a request path. The cost is real but
    slow; the diagnostic loss is the part that hurts.
    """
    findings: List[Finding] = []
    by_name = {g.get("logGroupName"): g for g in stack.get("log_groups") or []}

    for function in bedrock_functions(stack):
        name = function.get("FunctionName", "")
        group_name = LAMBDA_LOG_PREFIX + name
        group = by_name.get(group_name)

        if group is not None and group.get("retentionInDays"):
            continue

        missing = group is None
        findings.append(
            Finding(
                check_id="OBS-015",
                severity="MEDIUM",
                resource_type="AWS::Logs::LogGroup",
                resource_id=group_name,
                title="Model-invoking function's log group has no retention",
                detail=(
                    f"{group_name} "
                    + (
                        "does not exist, so Lambda will create it on first "
                        "invocation with retention Never expire and no tags"
                        if missing
                        else "exists with retention Never expire"
                    )
                    + f". These are the logs you need when {name} produces a "
                    f"confident, wrong summary — the prompt it built, how much it "
                    f"sampled, what came back. They will accumulate at "
                    f"$0.03/GB-month indefinitely."
                ),
                remediation=(
                    f"Create the group in Terraform BEFORE the function, with "
                    f"retention: an aws_cloudwatch_log_group named exactly "
                    f"{group_name}, plus a depends_on from the function. Then Lambda "
                    f"finds yours already there and writes into it. See main.tf "
                    f"section 3 — it is the pattern to copy into every Lambda you "
                    f"ever write."
                ),
                evidence={
                    "logGroupName": group_name,
                    "exists": not missing,
                    "retentionInDays": (group or {}).get("retentionInDays"),
                    "FunctionName": name,
                },
                region=region,
            )
        )
    return findings


def check_model_invocation_logging(
    stack: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """OBS-016 — nothing records what was sent to the model.

    The prompt is gone the moment the Lambda returns. When a summary turns out
    to be confidently wrong — and the first month it will — the first question
    is "what did the model actually see", and without invocation logging there
    is no way to answer it. You cannot reproduce it, because the log window has
    moved on and the sample was chosen from data that has since expired.

    The finding is not "you did something stupid". It is "nothing here can tell
    you what you sent".

    AND THE REMEDIATION CREATES A NEW PROBLEM, which is why the default in this
    lab is off rather than on. Turning it on writes every prompt and every
    completion to a CloudWatch log group in your account. That group then holds
    the log content you were careful about, in full, readable by anyone with
    CloudWatch read access — a much wider audience, in most organisations, than
    the people who can read the original application logs.

    Enable it AND set retention AND put a resource policy on the destination.
    Both halves or neither.

    Only fires when something in the account actually invokes a model. An
    account with no model-invoking functions does not need an invocation log,
    and reporting one would be the auditor inventing work.
    """
    if not bedrock_functions(stack):
        return []

    config = stack.get("bedrock_logging") or {}
    destinations = config.get("loggingConfig") or config
    has_destination = bool(
        destinations.get("cloudWatchConfig") or destinations.get("s3Config")
    )
    if has_destination:
        return []

    return [
        Finding(
            check_id="OBS-016",
            severity="MEDIUM",
            resource_type="AWS::Bedrock::LoggingConfiguration",
            resource_id=f"(account, region {region or 'unknown'})",
            title="Bedrock model invocation logging is not enabled",
            detail=(
                f"{len(bedrock_functions(stack))} function(s) in this account hold "
                f"bedrock:InvokeModel and no model invocation logging destination is "
                f"configured for {region or 'this region'}. There is no record of "
                f"any prompt sent or any completion received. When a generated "
                f"summary is wrong, there is nothing to reproduce it from."
            ),
            remediation=(
                "Enable it — aws_bedrock_model_invocation_logging_configuration "
                "with a cloudwatch_config — AND set retention on the destination log "
                "group AND put a resource policy on it. The destination will contain "
                "full prompts, which means full log content; enabling logging "
                "without restricting who can read it fixes an audit gap by opening a "
                "data-access one. Note the setting is account-level and "
                "region-singleton, so `terraform destroy` turns it off for "
                "everything in the region."
            ),
            evidence={
                "bedrock_logging": config or None,
                "bedrock_invoking_functions": [
                    fn.get("FunctionName") for fn in bedrock_functions(stack)
                ],
            },
            region=region,
        )
    ]


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
