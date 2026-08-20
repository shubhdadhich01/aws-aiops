#!/usr/bin/env python3
"""
serverless_audit_challenge.py — build the Day 04 serverless auditor yourself.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

===============================================================================
  HOW THIS WORKS
===============================================================================

All the boring parts are done for you: the CLI, the Finding dataclass, the
paginator helpers, the secret-detection patterns, the collector that talks to
AWS, the scoring and all three output renderers. What is missing is the part
that matters — the sixteen checks.

There are 12 TODOs plus 1 stretch. Each one has:
    * a time estimate
    * the exact AWS API fields you need
    * a hint if you are stuck
    * a CHECKPOINT so you know whether it worked before moving on

Total: roughly 100–120 minutes if you have not done this before.

Run it after each TODO:

    python3 serverless_audit_challenge.py --profile bootcamp --region us-east-1

You are done when your output matches the reference implementation at
../serverless_audit.py — 14 findings and a compliance score of 0/100 against
the stack with create_insecure_examples = true. Do not read that file first.
You will learn nothing, and the checks are the whole exercise.

Test as you go without an AWS account:

    cd .. && python3 -m unittest discover -s tests -v

The 47 tests in ../tests/test_checks.py run against synthetic dictionaries, so
they work offline. Every check has one test proving it FIRES on bad input and
one proving it stays SILENT on good input. The second half is the half people
skip, and it is the half that decides whether anyone keeps using your tool.

===============================================================================
  THE CHECKS YOU ARE IMPLEMENTING
===============================================================================

    TODO 1    CMP-001  No dead letter queue           CRITICAL   ~8 min
    TODO 2    CMP-002  Plaintext secrets in env       CRITICAL  ~12 min
    TODO 3    CMP-003  Env vars not CMK-encrypted     MEDIUM     ~6 min
    TODO 4    CMP-004  Wildcard role policy           CRITICAL  ~12 min
    TODO 5    CMP-005  Log group missing / forever    MEDIUM    ~12 min
    TODO 6    CMP-006  3-second default timeout       MEDIUM     ~6 min
    TODO 7    CMP-007  Unreserved concurrency         MEDIUM     ~8 min
    TODO 8    CMP-008  Deprecated runtime             HIGH       ~6 min
    TODO 9    CMP-009  X-Ray tracing disabled         LOW        ~6 min
    TODO 10a  CMP-010  SNS topic not encrypted        MEDIUM     ~5 min
    TODO 10b  CMP-011  SNS wildcard principal         CRITICAL  ~14 min
    TODO 11a  CMP-012  SQS queue not encrypted        MEDIUM     ~5 min
    TODO 11b  CMP-013  SQS queue has no DLQ           MEDIUM    ~12 min
    TODO 12a  CMP-014  EventBridge rule DISABLED      MEDIUM     ~6 min
    TODO 12b  CMP-015  Target has no retry / DLQ      LOW        ~8 min
    STRETCH   CMP-016  Function is public             CRITICAL  ~15 min

Two of these — CMP-008 and CMP-016 — must produce ZERO findings against the
Day 04 stack. That is not a mistake in the lab. Write them anyway, and make
them silent for the right reason rather than by accident.

===============================================================================
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # pragma: no cover
    print(
        "boto3 is not installed. Run:  pip install -r ../requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


###############################################################################
# Severity model
#
# The score starts at 100 and each finding subtracts its severity weight.
# Floor is 0 — a score cannot go negative, because "how much worse than
# completely broken is this" is not a useful question.
#
# Identical weights to Day 03's ha_audit.py on purpose. By Day 10 you will have
# five of these tools and one mental model for reading their output; changing
# the arithmetic per tool would make the numbers incomparable for no gain.
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
    """Wrap text in an ANSI colour, or return it unchanged."""
    if not enabled or key not in _COLOURS:
        return text
    return f"{_COLOURS[key]}{text}{_COLOURS['RESET']}"


###############################################################################
# Finding
###############################################################################


@dataclass
class Finding:
    """A single audit finding.

    check_id     Stable identifier (CMP-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken.
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
# list_* once and trusting the first page — silently misses everything past the
# first 50-100 items, which is exactly the situation where an audit matters.
# An account with 400 Lambda functions is not unusual; an audit that reports on
# the first 50 and says nothing about the rest is worse than no audit, because
# it produces a clean report you believe.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Any]:
    """Collect every page of a paginated boto3 operation into one list.

    Falls back to a single direct call for operations that have no paginator
    registered (several events and sqs operations are like this).
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

    `"Action": "s3:GetObject"` and `"Action": ["s3:GetObject"]` are the same
    document. Every policy parser that forgets this has a wildcard-detection
    bug, because the single-string form is exactly the form `Action: "*"` takes.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_policy(document: Any) -> Dict[str, Any]:
    """Return a policy document as a dict.

    IAM hands them back URL-encoded JSON strings, SNS and SQS hand them back
    as plain JSON strings, and our own tests hand them back as dicts. Accept
    all three rather than making every caller remember which is which.
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
# Constants the checks are built on
###############################################################################

# Runtimes AWS has already deprecated or has published an end-of-support date
# for. Deprecated means: no security patches, and Lambda eventually blocks
# updates and then invocations. Migration is not optional, only deferred.
#
# This list ages. Check the current table before trusting it:
#   https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html
DEPRECATED_RUNTIMES: Set[str] = {
    "python2.7",
    "python3.6",
    "python3.7",
    "python3.8",
    "nodejs",
    "nodejs4.3",
    "nodejs6.10",
    "nodejs8.10",
    "nodejs10.x",
    "nodejs12.x",
    "nodejs14.x",
    "nodejs16.x",
    "ruby2.5",
    "ruby2.7",
    "java8",
    "go1.x",
    "dotnetcore1.0",
    "dotnetcore2.0",
    "dotnetcore2.1",
    "dotnetcore3.1",
    "dotnet5.0",
    "dotnet7",
    "provided",
}

# Environment variable NAMES that suggest a secret regardless of the value.
# Deliberately conservative: matching on "KEY" alone would flag SORT_KEY and
# PARTITION_KEY on every DynamoDB function in the account, and a check that
# cries wolf gets suppressed, which is worse than not having it.
SECRET_KEY_PATTERNS: List[str] = [
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "API_KEY",
    "APIKEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "TOKEN",
    "CREDENTIAL",
    "PASSPHRASE",
    "AUTH",
]

# Names that LOOK like the patterns above but are routine and safe. Checked
# first, so TOKEN_TTL_SECONDS does not become a CRITICAL finding.
SECRET_KEY_ALLOWLIST: List[str] = [
    "TOKEN_TTL",
    "TOKEN_EXPIRY",
    "TOKEN_URL",
    "AUTH_URL",
    "AUTH_ENDPOINT",
    "AUTH_TYPE",
    "SECRET_ARN",
    "SECRET_NAME",
    "SECRET_ID",
    "SECRETS_MANAGER",
    "KMS_KEY_ARN",
    "PUBLIC_KEY",
]

# Value shapes that are a secret no matter what the variable is called. An
# AKIA-prefixed string in an environment variable is a live access key with no
# plausible innocent explanation.
SECRET_VALUE_PATTERNS: List[tuple] = [
    (re.compile(r"^AKIA[0-9A-Z]{16}$"), "AWS access key ID"),
    (re.compile(r"^ASIA[0-9A-Z]{16}$"), "AWS temporary access key ID"),
    (re.compile(r"^sk-[A-Za-z0-9\-_]{16,}$"), "API secret key (sk- prefix)"),
    (re.compile(r"^ghp_[A-Za-z0-9]{20,}$"), "GitHub personal access token"),
    (re.compile(r"^xox[baprs]-[A-Za-z0-9\-]{10,}$"), "Slack token"),
    (re.compile(r"^-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PEM private key"),
    (
        re.compile(r"^(postgres|postgresql|mysql|mongodb(\+srv)?)://[^:/@]+:[^@/]+@"),
        "database URI with an embedded password",
    ),
]

# Values that match a secret-shaped NAME but are obviously not a secret. An
# empty string or an ARN pointing at Secrets Manager is the correct pattern,
# not a violation of it.
_NON_SECRET_VALUE = re.compile(
    r"^\s*$|^arn:aws[a-z\-]*:(secretsmanager|ssm|kms):|^/[A-Za-z0-9/_\-]+$",
)

# The AWS default Lambda timeout. Not a rounded-down opinion: it is literally
# 3 seconds, and it is the wrong answer for anything that makes an API call.
DEFAULT_LAMBDA_TIMEOUT = 3

# Names that mark a queue as being a dead letter queue itself. A DLQ having no
# DLQ of its own is correct, not a finding — see check_sqs_redrive.
_DLQ_NAME_HINT = re.compile(r"(^|[-_])(dlq|deadletter|dead-letter|dead_letter)([-_]|$)", re.I)


###############################################################################
# Check implementations
#
# Every check is a pure function: dict in, list[Finding] out. No AWS calls, no
# printing. That means they can be unit-tested against synthetic fixtures with
# no credentials and no account, which is exactly what tests/test_checks.py
# does — 47 tests that run in under a second on a laptop on a train.
#
# It also means each check can be read on its own. When someone disputes a
# finding in a review, you open one 40-line function and settle it.
###############################################################################


# =============================================================================
# TODO 1 — CMP-001: no dead letter queue                           (~8 minutes)
# =============================================================================
#
# Fields:
#     function["FunctionName"]                       str
#     function["DeadLetterConfig"]["TargetArn"]      str, absent if unset
#     function["EventInvokeConfig"]["DestinationConfig"]["OnFailure"]["Destination"]
#
# Logic:
#     either mechanism present  -> no finding
#     neither present           -> CRITICAL
#
# Two different AWS features satisfy the same requirement and you must accept
# both. DeadLetterConfig is the older per-function setting. Destinations
# (OnFailure) is the newer one and carries the response as well as the event.
# A function using Destinations and flagged for "no DLQ" is a false positive,
# and false positives are how audit tools get switched off.
#
# HINT: (function.get("DeadLetterConfig") or {}).get("TargetArn") — the `or {}`
#       matters, because AWS returns the key with a null value, not absent.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 CRITICAL.
#             cbc-day04-compliance-scanner -> 0.
# =============================================================================


def check_dead_letter_queue(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 1: implement the logic described above.

    return findings


def _looks_like_secret_name(key: str) -> Optional[str]:
    """Return the matched pattern if the variable NAME suggests a secret."""
    upper = key.upper()
    for safe in SECRET_KEY_ALLOWLIST:
        if safe in upper:
            return None
    for pattern in SECRET_KEY_PATTERNS:
        if pattern in upper:
            return pattern
    return None


def _looks_like_secret_value(value: Any) -> Optional[str]:
    """Return a description if the VALUE is unmistakably a credential."""
    if not isinstance(value, str):
        return None
    for pattern, description in SECRET_VALUE_PATTERNS:
        if pattern.match(value.strip()):
            return description
    return None


# =============================================================================
# TODO 2 — CMP-002: plaintext secrets in environment variables    (~12 minutes)
# =============================================================================
#
# Fields:
#     function["Environment"]["Variables"]    dict[str, str]
#
# Given to you above: SECRET_KEY_PATTERNS, SECRET_KEY_ALLOWLIST,
# SECRET_VALUE_PATTERNS, _NON_SECRET_VALUE, and the two helpers
# _looks_like_secret_name() and _looks_like_secret_value().
#
# Logic:
#     for each variable:
#         value matches a secret VALUE pattern              -> offender
#         name matches a secret NAME pattern
#             AND the value is not obviously a pointer      -> offender
#     no offenders                                          -> no finding
#     any offenders                                         -> ONE CRITICAL
#
# Emit one finding per FUNCTION listing every offending key, not one per
# variable. Ten findings for one function distorts the score by 250 points and
# tells you nothing the single finding did not.
#
# The allowlist is the interesting part. Matching "KEY" alone flags SORT_KEY and
# PARTITION_KEY on every DynamoDB function in the account. Spend a moment
# thinking about which of those two failure modes you would rather explain.
#
# HINT: build `offenders: Dict[str, str]` mapping variable name -> reason, then
#       test `if not offenders: return findings`.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 CRITICAL naming API_KEY and
#             DB_PASSWORD but NOT DB_HOST.
#             cbc-day04-compliance-scanner -> 0 (SNS_TOPIC_ARN is an ARN).
# =============================================================================


def check_plaintext_secrets(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 2: implement the logic described above.

    return findings


# =============================================================================
# TODO 3 — CMP-003: env vars not encrypted with a customer key     (~6 minutes)
# =============================================================================
#
# Fields:
#     function["Environment"]["Variables"]    dict
#     function["KMSKeyArn"]                   str, absent when using the default key
#
# Logic:
#     no variables at all      -> no finding (nothing to encrypt)
#     KMSKeyArn present        -> no finding
#     variables, no KMSKeyArn  -> MEDIUM
#
# Not CRITICAL: the data IS encrypted either way, by the default service key.
# What is missing is control — your own key policy, a per-caller Decrypt trail
# in CloudTrail, and the ability to revoke access without touching the data.
#
# The "no variables" case is the one people get wrong. A function with an empty
# environment has nothing to encrypt and must not be flagged.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 MEDIUM.
#             cbc-day04-compliance-scanner -> 0 when enable_kms_encryption is
#             true. Set it false, re-apply, and this becomes 1 — that is the
#             check working, not the check breaking.
# =============================================================================


def check_env_encryption(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 3: implement the logic described above.

    return findings


def _statement_grants_wildcard(statement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return details if an Allow statement is wildcard on both action and resource.

    Deliberately requires BOTH to be wildcards. `Action: "*"` scoped to one
    bucket is broad but bounded; `s3:*` on `Resource: "*"` is broad but at
    least one service. `"*"` on `"*"` is administrator access spelled
    differently, and it is the finding people actually need to see.

    A NotAction or NotResource statement is flagged too. `NotAction: ["iam:*"]`
    with `Effect: Allow` grants every action in AWS except IAM, which is not
    what anyone thinks they are writing.
    """
    if statement.get("Effect") != "Allow":
        return None

    actions = [str(a) for a in as_list(statement.get("Action"))]
    resources = [str(r) for r in as_list(statement.get("Resource"))]
    not_actions = as_list(statement.get("NotAction"))
    not_resources = as_list(statement.get("NotResource"))

    if not_actions or not_resources:
        return {
            "reason": "NotAction/NotResource in an Allow statement",
            "NotAction": [str(a) for a in not_actions] or None,
            "NotResource": [str(r) for r in not_resources] or None,
            "Sid": statement.get("Sid"),
        }

    action_wild = any(a == "*" for a in actions)
    resource_wild = any(r == "*" for r in resources)

    if action_wild and resource_wild:
        return {
            "reason": 'Action "*" on Resource "*"',
            "Action": actions,
            "Resource": resources,
            "Sid": statement.get("Sid"),
        }
    return None


# =============================================================================
# TODO 4 — CMP-004: wildcard Action on wildcard Resource         (~12 minutes)
# =============================================================================
#
# Arguments:
#     policies    list of {"name", "type", "document"} assembled by the collector
#                 from the role's inline policies and customer-managed
#                 attachments. AWS-managed policies are already excluded.
#
# Given to you above: _statement_grants_wildcard() does the per-statement
# judgement, including the NotAction/NotResource case. as_list() normalises the
# string-or-list problem, and parse_policy() handles the URL-encoded JSON that
# IAM returns.
#
# Logic:
#     for each policy, for each statement in the document:
#         _statement_grants_wildcard(statement) returns a dict -> offender
#     no offenders -> no finding
#     offenders    -> ONE CRITICAL per role, listing them
#
# Read _statement_grants_wildcard before you use it and make sure you agree with
# it. It requires BOTH Action and Resource to be "*". `s3:*` on `"*"` is broad
# but bounded to one service; `"*"` on `"*"` is AdministratorAccess spelled
# differently, and only the second is worth waking someone up for.
#
# HINT: statements may be a single dict rather than a list. as_list() again.
#
# CHECKPOINT: cbc-day04-broken-role -> 1 CRITICAL.
#             cbc-day04-scanner-role -> 0, even though scanner_read grants
#             Resource "*" — read that policy and be sure you know why.
# =============================================================================


def check_execution_role(
    function: Dict[str, Any],
    policies: List[Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 4: implement the logic described above.

    return findings


# =============================================================================
# TODO 5 — CMP-005: log group missing or retained forever        (~12 minutes)
# =============================================================================
#
# Arguments:
#     log_groups   dict keyed by log group name, values from describe_log_groups
#                  ({"logGroupName", "retentionInDays", "storedBytes"})
#
# Fields:
#     function["LoggingConfig"]["LogGroup"]   set only when overridden
#     default group name: "/aws/lambda/<FunctionName>"
#
# Logic:
#     group absent from the dict          -> MEDIUM ("Lambda will create it")
#     group present, retentionInDays None or 0 -> MEDIUM ("Never expire")
#     group present with a retention      -> no finding
#
# ⚠️ This is one of Day 04's two silent cost-growth traps. A log group Lambda
# created for itself is not in Terraform state, so `terraform destroy` leaves it
# behind and it bills at $0.03/GB-month forever, with no tag and no owner.
#
# HINT: `retention in (None, 0)` — do not write `if not retention`, which is the
#       same thing here but stops being the same thing the moment somebody adds
#       a legitimate retention of 0 days to the API.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 MEDIUM (group absent).
#             cbc-day04-compliance-scanner -> 0 (Terraform declares it with
#             retention_in_days).
# =============================================================================


def check_log_group(
    function: Dict[str, Any],
    log_groups: Dict[str, Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 5: implement the logic described above.

    return findings


# =============================================================================
# TODO 6 — CMP-006: still on the 3-second default timeout          (~6 minutes)
# =============================================================================
#
# Fields:
#     function["Timeout"]    int, seconds
#
# Given: DEFAULT_LAMBDA_TIMEOUT = 3
#
# Logic:
#     Timeout is None or > 3   -> no finding
#     Timeout <= 3             -> MEDIUM
#
# Why this is worth a check at all: a timeout produces no exception and no stack
# trace. The log simply stops mid-sentence. It is the hardest Lambda failure to
# diagnose precisely because it looks like nothing happened.
#
# And the instinct it corrects is a real one — people run tight timeouts to
# "save money". You are billed for duration ACTUALLY USED. A generous timeout
# costs nothing and only bounds your worst case.
#
# CHECKPOINT: cbc-day04-broken-function (timeout 3, handler sleeps 5) -> 1
#             MEDIUM. cbc-day04-compliance-scanner (60) -> 0.
# =============================================================================


def check_timeout(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 6: implement the logic described above.

    return findings


# =============================================================================
# TODO 7 — CMP-007: no reserved concurrency                        (~8 minutes)
# =============================================================================
#
# Arguments:
#     concurrency   from get_function_concurrency(); None means unreserved
#
# Logic:
#     concurrency is not None and >= 0   -> no finding
#     otherwise                          -> MEDIUM
#
# ⚠️ Day 04's second silent cost-growth trap, and the expensive one. Unreserved
# means this function can scale to the whole account limit — 1,000 concurrent
# executions. Fine, until the function is part of a loop: it writes to the
# bucket that triggers it, or a rule fires on an API call the function itself
# makes. Then it is an infinite loop running a thousand copies in parallel,
# billing at machine speed, while you sleep.
#
# Note that 0 is a VALID reserved value meaning "throttled to a complete stop" —
# a useful kill switch and a catastrophic typo. Your condition must treat 0 as
# configured, not as missing. `if concurrency:` is wrong here; that is the whole
# point of the TODO.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 MEDIUM.
#             cbc-day04-compliance-scanner (reserved 2) -> 0.
# =============================================================================


def check_reserved_concurrency(
    function: Dict[str, Any],
    concurrency: Optional[int] = None,
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 7: implement the logic described above.

    return findings


# =============================================================================
# TODO 8 — CMP-008: deprecated runtime                             (~6 minutes)
# =============================================================================
#
# Fields:
#     function["Runtime"]    str, ABSENT for container-image functions
#
# Given: DEPRECATED_RUNTIMES
#
# Logic:
#     no Runtime key                    -> no finding (container image)
#     Runtime not in DEPRECATED_RUNTIMES -> no finding
#     Runtime in DEPRECATED_RUNTIMES     -> HIGH
#
# ⚠️ This check stays SILENT on the entire Day 04 stack. Both functions pin
# python3.12. That is deliberate and tests/test_checks.py asserts it: a check
# set where everything fires teaches you nothing about false positives, and
# false positives are how audit tools get ignored.
#
# To convince yourself it works, temporarily set lambda_runtime = "python3.8" in
# terraform.tfvars, apply, re-run, and put it back.
#
# CHECKPOINT: whole stack -> 0 findings. A synthetic function with
#             Runtime "python3.8" -> 1 HIGH.
# =============================================================================


def check_runtime(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 8: implement the logic described above.

    return findings


# =============================================================================
# TODO 9 — CMP-009: X-Ray active tracing disabled                  (~6 minutes)
# =============================================================================
#
# Fields:
#     function["TracingConfig"]["Mode"]    "Active" or "PassThrough"
#
# Logic:
#     Mode == "Active"   -> no finding
#     otherwise          -> LOW
#
# Default when the key is missing entirely is "PassThrough", which means the
# function only joins a trace something upstream already started. For an
# EventBridge-triggered function nothing upstream ever does, so PassThrough
# means no tracing at all in practice.
#
# LOW, and honestly so — nothing is insecure and nothing is broken. What you
# lose is the ability to answer "where did the four seconds go" without adding
# print statements and redeploying into an incident.
#
# CHECKPOINT: cbc-day04-broken-function -> 1 LOW.
#             cbc-day04-compliance-scanner -> 0.
# =============================================================================


def check_tracing(function: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 9: implement the logic described above.

    return findings


###############################################################################
# Messaging checks — SNS and SQS
###############################################################################


# =============================================================================
# TODO 10a — CMP-010: SNS topic not encrypted at rest              (~5 minutes)
# =============================================================================
#
# Arguments:
#     topic    the Attributes dict from get_topic_attributes, with TopicArn added
#
# Logic:
#     KmsMasterKeyId present   -> no finding
#     absent                   -> MEDIUM
#
# "alias/aws/sns" counts as encrypted. It is a lesser control than a
# customer-managed key — see CMP-003 for the difference — but grading it as
# absent would be wrong, and a check that disagrees with reality gets ignored.
#
# CHECKPOINT: cbc-day04-broken-topic -> 1 MEDIUM.
#             cbc-day04-findings -> 0.
# =============================================================================


def check_sns_encryption(topic: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 10a: implement the logic described above.

    return findings


# =============================================================================
# TODO 10b — CMP-011: SNS topic policy allows Principal "*"      (~14 minutes)
# =============================================================================
#
# Arguments:
#     topic["Policy"]    a JSON STRING, not a dict. parse_policy() handles it.
#
# Logic:
#     for each Allow statement:
#         Principal == "*"  OR  Principal["AWS"] contains "*"   -> candidate
#         candidate narrowed by a Condition on one of:
#             AWS:SourceAccount, AWS:SourceOwner, AWS:SourceArn,
#             aws:PrincipalOrgID, aws:PrincipalAccount, aws:PrincipalArn
#                                                              -> NOT a finding
#     any unconditioned candidate                              -> ONE CRITICAL
#
# The condition logic is the whole exercise and it is not optional. The Day 04
# stack's own findings topic uses Principal "*" narrowed by AWS:SourceAccount —
# which is the normal, correct way to write these policies. Flag it and your
# auditor cries wolf on the reference architecture it ships with.
#
# HINT: condition keys are case-inconsistent across AWS docs and real policies.
#       Lowercase both sides before comparing.
#
# CHECKPOINT: cbc-day04-broken-topic -> 1 CRITICAL.
#             cbc-day04-findings -> 0.
# =============================================================================


def check_sns_topic_policy(topic: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 10b: implement the logic described above.

    return findings


# =============================================================================
# TODO 11a — CMP-012: SQS queue not encrypted at rest              (~5 minutes)
# =============================================================================
#
# Arguments:
#     queue    Attributes dict from get_queue_attributes(AttributeNames=["All"])
#
# Fields:
#     queue["SqsManagedSseEnabled"]   the STRING "true"/"false", not a bool
#     queue["KmsMasterKeyId"]         str, absent unless SSE-KMS
#
# Logic:
#     either present/true   -> no finding
#     neither               -> MEDIUM
#
# Every value in a queue Attributes dict is a string. `if
# queue.get("SqsManagedSseEnabled"):` is true for the string "false", which
# means your check silently passes every unencrypted queue in the account. This
# is the single most common bug in home-grown SQS auditors.
#
# CHECKPOINT: cbc-day04-broken-queue -> 1 MEDIUM.
#             cbc-day04-scanner-dlq -> 0.
# =============================================================================


def check_sqs_encryption(queue: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 11a: implement the logic described above.

    return findings


# =============================================================================
# TODO 11b — CMP-013: queue has no dead letter queue              (~12 minutes)
# =============================================================================
#
# Arguments:
#     queue["RedrivePolicy"]   JSON string, absent when there is no DLQ
#     known_dlq_arns           set of ARNs already used as a DLQ by something
#
# Logic:
#     RedrivePolicy present                        -> no finding
#     this queue's own ARN is in known_dlq_arns    -> no finding
#     name matches the DLQ naming hint             -> no finding
#     otherwise                                    -> MEDIUM
#
# A dead letter queue having no dead letter queue of its own is CORRECT. Getting
# this exemption right is the difference between an auditor people run and an
# auditor people mute. The collector builds known_dlq_arns from Lambda
# DeadLetterConfig targets, Lambda OnFailure destinations, EventBridge target
# DLQs and other queues' redrive policies — everything that already treats a
# queue as a DLQ.
#
# The name heuristic (_DLQ_NAME_HINT, given above) is the fallback for the real
# case where the source queue lives in a different stack or account and nothing
# in this region points at it.
#
# CHECKPOINT: cbc-day04-broken-queue -> 1 MEDIUM.
#             cbc-day04-scanner-dlq -> 0 by BOTH routes. Comment out the
#             known_dlq_arns branch and confirm the name hint still catches it.
# =============================================================================


def check_sqs_redrive(
    queue: Dict[str, Any],
    known_dlq_arns: Optional[Set[str]] = None,
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 11b: implement the logic described above.

    return findings


###############################################################################
# EventBridge checks
###############################################################################


# =============================================================================
# TODO 12a — CMP-014: EventBridge rule is DISABLED                 (~6 minutes)
# =============================================================================
#
# Fields:
#     rule["State"]   "ENABLED", "DISABLED", or
#                     "ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS"
#
# Logic:
#     State starts with "ENABLED"   -> no finding
#     otherwise                     -> MEDIUM
#
# Test the PREFIX, not equality. The third value above is a real state on
# enabled rules and an equality test flags it as disabled — an auditor that
# reports your working rules as broken.
#
# This is the outage that survives a code review, an architecture review and a
# screenshot in the runbook. The rule is there, the target is wired, the
# permission is correct, the diagram is accurate, and nothing runs. Nothing
# reports an error either, because a disabled rule is not an error, it is a
# configuration.
#
# CHECKPOINT: cbc-day04-broken-rule -> 1 MEDIUM.
#             cbc-day04-scheduled-scan and -reactive-scan -> 0.
# =============================================================================


def check_rule_state(rule: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 12a: implement the logic described above.

    return findings


# =============================================================================
# TODO 12b — CMP-015: target has no retry policy and no DLQ        (~8 minutes)
# =============================================================================
#
# Arguments:
#     targets    list from list_targets_by_rule
#
# Fields:
#     target["RetryPolicy"]              dict, absent by default
#     target["DeadLetterConfig"]["Arn"]  str, absent by default
#
# Logic:
#     per target: either present -> skip
#                 neither        -> LOW, one finding PER TARGET
#
# Per target, not per rule, on purpose: in a fan-out rule with five targets,
# one being unprotected while the others are fine is exactly the detail worth
# surfacing, and a rule-level finding would hide which one.
#
# EventBridge delivery and Lambda execution are different failure domains. The
# Lambda-side retry config governs what happens after the function was invoked
# and failed. This governs what happens when EventBridge cannot deliver at all.
#
# CHECKPOINT: cbc-day04-broken-rule/BrokenTargetNoDlq -> 1 LOW.
#             both good rules -> 0.
# =============================================================================


def check_rule_targets(
    rule: Dict[str, Any],
    targets: List[Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 12b: implement the logic described above.

    return findings


# =============================================================================
# STRETCH — CMP-016: function is publicly invokable               (~15 minutes)
# =============================================================================
#
# Arguments:
#     url_config   from get_function_url_config(); {} when there is no URL
#     policy       from get_policy(); a JSON string or None
#
# Logic:
#     url_config["AuthType"] == "NONE"                        -> public
#     resource policy Allow statement with a wildcard AWS
#         principal and no narrowing Condition                -> public
#     Principal {"Service": "events.amazonaws.com"} with a
#         SourceArn condition                                 -> NOT public
#     any reason found                                        -> ONE CRITICAL
#
# ⚠️ Like CMP-008, this stays SILENT on the whole Day 04 stack, and
# tests/test_checks.py asserts that. The stack's aws_lambda_permission resources
# grant a service principal with source_arn, which is the correct pattern. An
# auditor that flags them is an auditor nobody runs twice.
#
# Marked STRETCH because the resource-policy half needs care, not because it is
# optional in real life — a function URL with AuthType NONE is a public HTTPS
# endpoint with no authentication, two clicks away in the console, running with
# your execution role's permissions.
#
# To see it fire, create a function URL by hand with auth type NONE, run the
# auditor, then delete it. Do not leave it there.
#
# CHECKPOINT: whole stack -> 0. Synthetic function with AuthType "NONE" -> 1
#             CRITICAL.
# =============================================================================


def check_public_access(
    function: Dict[str, Any],
    url_config: Optional[Dict[str, Any]] = None,
    policy: Any = None,
    region: str = "",
) -> List[Finding]:
    findings: List[Finding] = []

    # STRETCH: implement the logic described above.

    return findings


###############################################################################
# Collection
#
# The auditor object does all the I/O and none of the judgement. Every AWS call
# lives here; every decision lives in the pure check functions above. That
# split is what makes 47 unit tests possible without a single credential.
###############################################################################


class ServerlessAuditor:
    """Collects AWS state and runs every check against it."""

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        quiet: bool = False,
    ) -> None:
        self.region = region
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.stats: Dict[str, int] = {
            "functions": 0,
            "roles": 0,
            "log_groups": 0,
            "topics": 0,
            "queues": 0,
            "rules": 0,
        }

        # Role policy documents are cached by role ARN. Several functions
        # sharing one execution role is normal, and IAM has a low request rate
        # limit that a naive audit hits immediately in a busy account.
        self._role_cache: Dict[str, List[Dict[str, Any]]] = {}

        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.lambda_client = self.session.client("lambda")
            self.iam = self.session.client("iam")
            self.logs = self.session.client("logs")
            self.sns = self.session.client("sns")
            self.sqs = self.session.client("sqs")
            self.events = self.session.client("events")
        except (BotoCoreError, NoCredentialsError) as exc:
            print(f"Could not create an AWS session: {exc}", file=sys.stderr)
            sys.exit(2)

    # -- logging ------------------------------------------------------------

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def _swallow(self, operation: str, resource: str, exc: ClientError) -> None:
        """Log an API error that should not abort the whole audit.

        ResourceNotFoundException is the normal answer to "does this function
        have a URL / a policy / an invoke config", so it is not worth a line of
        output. Everything else is.
        """
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in ("ResourceNotFoundException", "NoSuchEntity", "NotFound"):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    # -- collection ---------------------------------------------------------

    def collect_functions(self) -> List[Dict[str, Any]]:
        return paginate(self.lambda_client, "list_functions", "Functions")

    def collect_concurrency(self, name: str) -> Optional[int]:
        """None means unreserved, which is the AWS default and check CMP-007."""
        try:
            response = self.lambda_client.get_function_concurrency(FunctionName=name)
        except ClientError as exc:
            self._swallow("get_function_concurrency", name, exc)
            return None
        return response.get("ReservedConcurrentExecutions")

    def collect_event_invoke_config(self, name: str) -> Dict[str, Any]:
        """OnFailure destinations live here, not in list_functions output."""
        try:
            return self.lambda_client.get_function_event_invoke_config(
                FunctionName=name
            )
        except ClientError as exc:
            self._swallow("get_function_event_invoke_config", name, exc)
            return {}

    def collect_url_config(self, name: str) -> Dict[str, Any]:
        try:
            return self.lambda_client.get_function_url_config(FunctionName=name)
        except ClientError as exc:
            self._swallow("get_function_url_config", name, exc)
            return {}

    def collect_function_policy(self, name: str) -> Any:
        try:
            return self.lambda_client.get_policy(FunctionName=name).get("Policy")
        except ClientError as exc:
            self._swallow("get_policy", name, exc)
            return None

    def collect_role_policies(self, role_arn: str) -> List[Dict[str, Any]]:
        """Inline policies plus customer-managed attachments for one role.

        AWS-managed policies (arn:aws:iam::aws:policy/...) are skipped
        deliberately. See check_execution_role for why: flagging
        AdministratorAccess as a wildcard policy is true and useless.
        """
        if role_arn in self._role_cache:
            return self._role_cache[role_arn]

        role_name = role_arn.rsplit("/", 1)[-1]
        policies: List[Dict[str, Any]] = []

        for policy_name in paginate(self.iam, "list_role_policies", "PolicyNames", RoleName=role_name):
            try:
                document = self.iam.get_role_policy(
                    RoleName=role_name, PolicyName=policy_name
                ).get("PolicyDocument")
            except ClientError as exc:
                self._swallow("get_role_policy", f"{role_name}/{policy_name}", exc)
                continue
            policies.append(
                {"name": policy_name, "type": "inline", "document": document}
            )

        for attached in paginate(
            self.iam, "list_attached_role_policies", "AttachedPolicies", RoleName=role_name
        ):
            policy_arn = attached.get("PolicyArn", "")
            if ":iam::aws:policy/" in policy_arn:
                continue
            try:
                version_id = (
                    self.iam.get_policy(PolicyArn=policy_arn)
                    .get("Policy", {})
                    .get("DefaultVersionId")
                )
                document = (
                    self.iam.get_policy_version(
                        PolicyArn=policy_arn, VersionId=version_id
                    )
                    .get("PolicyVersion", {})
                    .get("Document")
                )
            except ClientError as exc:
                self._swallow("get_policy_version", policy_arn, exc)
                continue
            policies.append(
                {
                    "name": attached.get("PolicyName", policy_arn),
                    "type": "managed",
                    "document": document,
                }
            )

        self._role_cache[role_arn] = policies
        return policies

    def collect_log_groups(self) -> Dict[str, Dict[str, Any]]:
        groups = paginate(
            self.logs,
            "describe_log_groups",
            "logGroups",
            logGroupNamePrefix="/aws/lambda/",
        )
        return {g.get("logGroupName", ""): g for g in groups}

    def collect_topics(self) -> List[Dict[str, Any]]:
        topics = paginate(self.sns, "list_topics", "Topics")
        collected: List[Dict[str, Any]] = []
        for topic in topics:
            arn = topic.get("TopicArn")
            if not arn:
                continue
            try:
                attributes = self.sns.get_topic_attributes(TopicArn=arn).get(
                    "Attributes", {}
                )
            except ClientError as exc:
                self._swallow("get_topic_attributes", arn, exc)
                continue
            attributes.setdefault("TopicArn", arn)
            collected.append(attributes)
        return collected

    def collect_queues(self) -> List[Dict[str, Any]]:
        urls = paginate(self.sqs, "list_queues", "QueueUrls")
        collected: List[Dict[str, Any]] = []
        for url in urls:
            try:
                attributes = self.sqs.get_queue_attributes(
                    QueueUrl=url, AttributeNames=["All"]
                ).get("Attributes", {})
            except ClientError as exc:
                self._swallow("get_queue_attributes", url, exc)
                continue
            attributes["QueueUrl"] = url
            collected.append(attributes)
        return collected

    def collect_rules(self) -> List[Dict[str, Any]]:
        return paginate(self.events, "list_rules", "Rules")

    def collect_targets(self, rule_name: str) -> List[Dict[str, Any]]:
        return paginate(
            self.events, "list_targets_by_rule", "Targets", Rule=rule_name
        )

    # -- orchestration ------------------------------------------------------

    def run(self) -> List[Finding]:
        """Collect everything, then run every check. Returns all findings."""
        self.log(f"\n  Auditing serverless resources in {self.region} ...\n")

        # --- Lambda functions ------------------------------------------------
        functions = self.collect_functions()
        self.stats["functions"] = len(functions)
        self.log(f"  Lambda functions    : {len(functions)}")

        log_groups = self.collect_log_groups()
        self.stats["log_groups"] = len(log_groups)
        self.log(f"  Lambda log groups   : {len(log_groups)}")

        # Every queue that something already treats as a dead letter target.
        # Collected before CMP-013 runs so a real DLQ is never flagged for not
        # having a DLQ of its own.
        known_dlq_arns: Set[str] = set()

        for function in functions:
            name = function.get("FunctionName")
            if not name:
                continue

            dlq_arn = (function.get("DeadLetterConfig") or {}).get("TargetArn")
            if dlq_arn and ":sqs:" in dlq_arn:
                known_dlq_arns.add(dlq_arn)

            invoke_config = self.collect_event_invoke_config(name)
            if invoke_config:
                function["EventInvokeConfig"] = invoke_config
                destination = (
                    (invoke_config.get("DestinationConfig") or {}).get("OnFailure", {})
                    or {}
                ).get("Destination")
                if destination and ":sqs:" in destination:
                    known_dlq_arns.add(destination)

            self.findings += check_dead_letter_queue(function, self.region)
            self.findings += check_plaintext_secrets(function, self.region)
            self.findings += check_env_encryption(function, self.region)
            self.findings += check_log_group(function, log_groups, self.region)
            self.findings += check_timeout(function, self.region)
            self.findings += check_reserved_concurrency(
                function, self.collect_concurrency(name), self.region
            )
            self.findings += check_runtime(function, self.region)
            self.findings += check_tracing(function, self.region)
            self.findings += check_public_access(
                function,
                self.collect_url_config(name),
                self.collect_function_policy(name),
                self.region,
            )

            role_arn = function.get("Role")
            if role_arn:
                policies = self.collect_role_policies(role_arn)
                self.findings += check_execution_role(function, policies, self.region)

        self.stats["roles"] = len(self._role_cache)
        self.log(f"  Execution roles     : {self.stats['roles']}")

        # --- SNS -------------------------------------------------------------
        topics = self.collect_topics()
        self.stats["topics"] = len(topics)
        self.log(f"  SNS topics          : {len(topics)}")

        for topic in topics:
            self.findings += check_sns_encryption(topic, self.region)
            self.findings += check_sns_topic_policy(topic, self.region)

        # --- EventBridge -----------------------------------------------------
        # Runs before SQS so that any queue used as a target DLQ is already in
        # known_dlq_arns by the time CMP-013 evaluates it.
        rules = self.collect_rules()
        self.stats["rules"] = len(rules)
        self.log(f"  EventBridge rules   : {len(rules)}")

        for rule in rules:
            rule_name = rule.get("Name")
            if not rule_name:
                continue
            targets = self.collect_targets(rule_name)
            for target in targets:
                target_dlq = (target.get("DeadLetterConfig") or {}).get("Arn")
                if target_dlq and ":sqs:" in target_dlq:
                    known_dlq_arns.add(target_dlq)

            self.findings += check_rule_state(rule, self.region)
            self.findings += check_rule_targets(rule, targets, self.region)

        # --- SQS -------------------------------------------------------------
        queues = self.collect_queues()
        self.stats["queues"] = len(queues)
        self.log(f"  SQS queues          : {len(queues)}")

        for queue in queues:
            redrive = parse_policy(queue.get("RedrivePolicy"))
            target_arn = redrive.get("deadLetterTargetArn")
            if target_arn:
                known_dlq_arns.add(target_arn)

        for queue in queues:
            self.findings += check_sqs_encryption(queue, self.region)
            self.findings += check_sqs_redrive(queue, known_dlq_arns, self.region)

        self.log("")
        return self.findings


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.

    Expect zero on a fresh Day 04 stack with create_insecure_examples = true.
    Four CRITICAL findings alone are 100 points. That is the intended shock;
    Step 6 of the lab is fixing them one at a time and watching it climb.
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
    w(colour("  SERVERLESS COMPLIANCE AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 04 · Serverless Automation\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('functions', 0)} function(s) · "
        f"{stats.get('roles', 0)} role(s) · "
        f"{stats.get('log_groups', 0)} log group(s) · "
        f"{stats.get('topics', 0)} topic(s) · "
        f"{stats.get('queues', 0)} queue(s) · "
        f"{stats.get('rules', 0)} rule(s)\n\n"
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
        "audit": "serverless_audit",
        "day": "04",
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
# CLI
###############################################################################


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serverless_audit_challenge.py",
        description=(
            "Audit AWS Lambda functions, execution roles, log groups, SNS "
            "topics, SQS queues and EventBridge rules for serverless "
            "compliance and reliability misconfigurations."
        ),
        epilog=(
            "Examples:\n"
            "  serverless_audit_challenge.py --profile bootcamp --region us-east-1\n"
            "  serverless_audit_challenge.py --format json --quiet > findings.json\n"
            "  serverless_audit_challenge.py --min-severity HIGH --format csv\n"
            "  serverless_audit_challenge.py --fail-on CRITICAL   # exit 1 on any CRITICAL\n"
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

    auditor = ServerlessAuditor(
        profile=args.profile, region=args.region, quiet=args.quiet
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
