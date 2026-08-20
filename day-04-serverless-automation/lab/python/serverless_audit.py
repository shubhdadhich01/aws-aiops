#!/usr/bin/env python3
"""
serverless_audit.py — Day 04 serverless compliance auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits Lambda functions, their execution roles and log groups, plus the SNS
topics, SQS queues and EventBridge rules they are wired to, for the
misconfigurations that turn "it works in the console" into a silent outage or
an audit finding.

Serverless fails differently from EC2. There is no host to SSH into, no
process list, no disk to inspect. When an asynchronous invocation fails its
last retry the event is simply gone — the only trace is an Errors metric
ticking up by one. Most of the checks below exist because of that: they are
about whether failure leaves EVIDENCE, not merely whether it happens.

What it checks
--------------
    CMP-001  No dead letter queue        failed async events vanish        CRITICAL
    CMP-002  Plaintext secrets in env    readable by any read-only caller  CRITICAL
    CMP-003  Env vars not CMK-encrypted  no revocable, auditable key       MEDIUM
    CMP-004  Wildcard role policy        Action "*" on Resource "*"        CRITICAL
    CMP-005  Log group missing / kept    "Never expire" bills you forever  MEDIUM
    CMP-006  3-second default timeout    the AWS default, almost never right MEDIUM
    CMP-007  Unreserved concurrency      a runaway loop bills at machine speed MEDIUM
    CMP-008  Deprecated runtime          no security patches, forced migration HIGH
    CMP-009  X-Ray tracing disabled      no distributed trace when it matters LOW
    CMP-010  SNS topic not encrypted     messages at rest in the clear     MEDIUM
    CMP-011  SNS wildcard principal      anyone in AWS may publish         CRITICAL
    CMP-012  SQS queue not encrypted     payloads at rest in the clear     MEDIUM
    CMP-013  SQS queue has no DLQ        a poison message is retried forever MEDIUM
    CMP-014  EventBridge rule DISABLED   looks correct, never runs         MEDIUM
    CMP-015  Target has no retry / DLQ   delivery failures discarded silently LOW
    CMP-016  Function is public          function URL or policy open to *  CRITICAL

Usage
-----
    python3 serverless_audit.py --profile bootcamp --region us-east-1
    python3 serverless_audit.py --format json --quiet > findings.json
    python3 serverless_audit.py --min-severity HIGH --format csv
    python3 serverless_audit.py --fail-on CRITICAL     # non-zero exit for CI

Note on --min-severity: it filters the DISPLAY only. The score always reflects
every finding. Otherwise anyone could "improve" their compliance posture by
passing --min-severity CRITICAL, which is not an improvement, it is a habit.

Required IAM permissions (all read-only):
    lambda:ListFunctions
    lambda:GetFunctionConcurrency
    lambda:GetFunctionUrlConfig
    lambda:GetPolicy
    iam:ListRolePolicies
    iam:GetRolePolicy
    iam:ListAttachedRolePolicies
    iam:GetPolicy
    iam:GetPolicyVersion
    logs:DescribeLogGroups
    sns:ListTopics
    sns:GetTopicAttributes
    sqs:ListQueues
    sqs:GetQueueAttributes
    events:ListRules
    events:ListTargetsByRule

The SecurityAudit or ReadOnlyAccess managed policy covers all of these.
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
        "boto3 is not installed. Run:  pip install -r requirements.txt",
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


def check_dead_letter_queue(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-001 — the function has no dead letter queue configured.

    Asynchronous invocations (EventBridge, SNS, S3 notifications) are retried
    twice by default and then DISCARDED. Not logged with the payload, not held
    anywhere — discarded. The event that broke your function is the one piece
    of evidence you need to fix it, and by default AWS throws it away.

    Two mechanisms satisfy this and they are not the same thing:
      * DeadLetterConfig — the older per-function setting, SNS or SQS target.
      * Destinations (OnFailure) — the newer, richer one, configured with
        put-function-event-invoke-config, which also carries the response.

    Either is fine. Neither is a finding.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")

    dlq_target = (function.get("DeadLetterConfig") or {}).get("TargetArn")
    # Injected by the collector from get_function_event_invoke_config, because
    # list_functions does not return destination configuration.
    on_failure = (
        (function.get("EventInvokeConfig") or {}).get("DestinationConfig", {}) or {}
    ).get("OnFailure", {}) or {}
    destination = on_failure.get("Destination")

    if dlq_target or destination:
        return findings

    findings.append(
        Finding(
            check_id="CMP-001",
            severity="CRITICAL",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title="No dead letter queue configured",
            detail=(
                f"{name} has neither a DeadLetterConfig target nor an OnFailure "
                f"destination. Every asynchronous event that fails all of its "
                f"retries is discarded with no record of the payload. You will "
                f"see the Errors metric increment and have nothing to replay."
            ),
            remediation=(
                "Add an SQS queue as a dead letter target: dead_letter_config "
                "{ target_arn = aws_sqs_queue.dlq.arn } in Terraform, or "
                "`aws lambda update-function-configuration --function-name "
                f"{name} --dead-letter-config TargetArn=<queue-arn>`. Give the "
                "execution role sqs:SendMessage on that queue or the delivery "
                "fails silently, which is the same problem one layer down."
            ),
            evidence={
                "DeadLetterConfig": function.get("DeadLetterConfig"),
                "OnFailureDestination": destination,
            },
            region=region,
        )
    )
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


def check_plaintext_secrets(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-002 — secret-shaped values sitting in plaintext environment variables.

    Environment variables are encrypted at rest by AWS. That is not the point.
    The point is that anyone holding lambda:GetFunctionConfiguration — which
    ReadOnlyAccess grants, and which is handed out freely — reads them back in
    plaintext through the API, and they appear in the console, in
    `terraform show`, and in your state file.

    The fix is not "encrypt them harder". It is to keep the SECRET somewhere
    designed for secrets and keep the POINTER in the environment variable.

    One finding per function, not per variable, listing every offending key.
    Ten findings for one function distorts the score and tells you nothing the
    single finding did not.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    variables = (function.get("Environment") or {}).get("Variables") or {}

    offenders: Dict[str, str] = {}

    for key, value in variables.items():
        value_reason = _looks_like_secret_value(value)
        if value_reason:
            offenders[key] = value_reason
            continue

        name_match = _looks_like_secret_name(key)
        if name_match and isinstance(value, str) and not _NON_SECRET_VALUE.match(value):
            offenders[key] = f"name contains {name_match}"

    if not offenders:
        return findings

    listed = ", ".join(sorted(offenders))
    findings.append(
        Finding(
            check_id="CMP-002",
            severity="CRITICAL",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title="Secret-shaped values in plaintext environment variables",
            detail=(
                f"{name} carries {len(offenders)} environment variable(s) that "
                f"look like credentials: {listed}. Anyone with "
                f"lambda:GetFunctionConfiguration reads these in plaintext, and "
                f"they are also visible in the console and in Terraform state."
            ),
            remediation=(
                "Move the value into Secrets Manager or SSM Parameter Store "
                "(SecureString) and put only the ARN in the environment "
                "variable, then fetch it at cold start and cache it in a module "
                "global. Rotate anything that has already been sitting there — "
                "it is in your state file and probably in git."
            ),
            evidence={"variables": offenders},
            region=region,
        )
    )
    return findings


def check_env_encryption(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-003 — environment variables not encrypted with a customer-managed key.

    Lambda always encrypts environment variables at rest. With no KMSKeyArn it
    uses the default service key, which you do not control.

    Three things a customer-managed key buys you that the default key does not:
      1. You own the key policy, so you decide who may Decrypt.
      2. Every Decrypt lands in CloudTrail naming the caller.
      3. You can revoke access to the data by revoking the grant, without
         touching the data.

    MEDIUM, not CRITICAL: the data is encrypted either way. What is missing is
    control and evidence. A function with no environment variables at all is
    not a finding — there is nothing to encrypt.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    variables = (function.get("Environment") or {}).get("Variables") or {}

    if not variables:
        return findings
    if function.get("KMSKeyArn"):
        return findings

    findings.append(
        Finding(
            check_id="CMP-003",
            severity="MEDIUM",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title="Environment variables use the default service key",
            detail=(
                f"{name} defines {len(variables)} environment variable(s) and "
                f"has no KMSKeyArn, so they are encrypted with the AWS-managed "
                f"default Lambda key. You cannot write its key policy, you get "
                f"no per-caller Decrypt trail, and you cannot revoke access."
            ),
            remediation=(
                "Create a customer-managed key and set kms_key_arn on the "
                "function. Grant the execution role kms:Decrypt on that key "
                "only — omit it and the function fails at cold start with an "
                "unhelpful KMSAccessDeniedException. Cost is about $1/month "
                "for the key."
            ),
            evidence={
                "variable_count": len(variables),
                "variable_names": sorted(variables),
                "KMSKeyArn": None,
            },
            region=region,
        )
    )
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


def check_execution_role(
    function: Dict[str, Any],
    policies: List[Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    """CMP-004 — the execution role grants wildcard Action on wildcard Resource.

    `policies` is a list of {"name": str, "type": "inline"|"managed",
    "document": dict}. The collector assembles it from the role's inline
    policies and its customer-managed attachments.

    AWS-managed policies are excluded upstream. Flagging AdministratorAccess
    as a wildcard policy is technically true and operationally useless — you
    would get the finding on every account and stop reading the check. What
    matters is the wildcard somebody wrote themselves, usually as a temporary
    measure during a debugging session that ended two years ago.

    One finding per role, listing every offending statement.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    role_arn = function.get("Role", "")
    role_name = role_arn.rsplit("/", 1)[-1] if role_arn else "unknown"

    offenders: List[Dict[str, Any]] = []

    for policy in policies or []:
        document = parse_policy(policy.get("document"))
        for statement in as_list(document.get("Statement")):
            if not isinstance(statement, dict):
                continue
            problem = _statement_grants_wildcard(statement)
            if problem:
                problem["policy"] = policy.get("name", "unnamed")
                problem["policy_type"] = policy.get("type", "inline")
                offenders.append(problem)

    if not offenders:
        return findings

    names = sorted({str(o["policy"]) for o in offenders})
    findings.append(
        Finding(
            check_id="CMP-004",
            severity="CRITICAL",
            resource_type="AWS::IAM::Role",
            resource_id=role_name,
            title="Execution role grants wildcard Action on wildcard Resource",
            detail=(
                f"The execution role for {name} has {len(offenders)} statement(s) "
                f"granting unrestricted access, in policy/policies: "
                f"{', '.join(names)}. This is administrator access with extra "
                f"steps. Any code execution in the function — including a "
                f"compromised dependency — inherits it."
            ),
            remediation=(
                "Replace the wildcard with the specific actions the handler "
                "actually calls. Read them off CloudTrail or IAM Access "
                "Analyzer's policy generation, which builds a least-privilege "
                "policy from observed activity. Scope every statement to "
                "concrete ARNs. Expect this to take twenty minutes and to "
                "surface at least one permission nobody knew was being used."
            ),
            evidence={
                "function": name,
                "role_arn": role_arn,
                "statements": offenders[:10],
            },
            region=region,
        )
    )
    return findings


def check_log_group(
    function: Dict[str, Any],
    log_groups: Dict[str, Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    """CMP-005 — the log group is missing, or retains logs forever.

    ⚠️ This is one of Day 04's two silent cost-growth traps.

    If you never create the log group, Lambda creates it for you on first
    invocation with retention set to "Never expire". Three consequences, in
    increasing order of annoyance:

      1. Ingestion is $0.50/GB and storage $0.03/GB-month, forever, for logs
         nobody will ever read.
      2. Terraform does not know the group exists, so `terraform destroy`
         leaves it behind. You believe the lab is torn down. It is not.
      3. The bill arrives months later with no obvious cause, because a log
         group has no tags, no owner and no name that means anything.

    `log_groups` is keyed by log group name so the check stays pure.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    group_name = (function.get("LoggingConfig") or {}).get(
        "LogGroup"
    ) or f"/aws/lambda/{name}"

    group = log_groups.get(group_name)

    if group is None:
        findings.append(
            Finding(
                check_id="CMP-005",
                severity="MEDIUM",
                resource_type="AWS::Logs::LogGroup",
                resource_id=group_name,
                title="Log group does not exist yet — Lambda will create it untracked",
                detail=(
                    f"No log group named {group_name} exists. On the first "
                    f"invocation of {name}, Lambda creates it with retention "
                    f'"Never expire" and outside Terraform state, so it '
                    f"survives `terraform destroy` and bills quietly forever."
                ),
                remediation=(
                    "Declare the group explicitly with a retention period and "
                    "make the function depend on it: aws_cloudwatch_log_group "
                    'with name = "/aws/lambda/<fn>" and retention_in_days = 7, '
                    "plus depends_on. If Lambda already created one, import it "
                    "or delete it — there is no third option that ends well."
                ),
                evidence={"function": name, "expected_log_group": group_name},
                region=region,
            )
        )
        return findings

    retention = group.get("retentionInDays")
    if retention in (None, 0):
        findings.append(
            Finding(
                check_id="CMP-005",
                severity="MEDIUM",
                resource_type="AWS::Logs::LogGroup",
                resource_id=group_name,
                title='Log group retention is "Never expire"',
                detail=(
                    f"{group_name} has no retention period set, so every log "
                    f"line {name} has ever written is stored indefinitely at "
                    f"$0.03/GB-month. Stored bytes: "
                    f"{group.get('storedBytes', 'unknown')}."
                ),
                remediation=(
                    "Set retention_in_days (7 for labs, 30-90 for production, "
                    "longer only where a regulator says so) or `aws logs "
                    f"put-retention-policy --log-group-name {group_name} "
                    "--retention-in-days 7`. Then sweep the whole account: "
                    "`aws logs describe-log-groups --query "
                    "'logGroups[?!retentionInDays].logGroupName'`."
                ),
                evidence={
                    "function": name,
                    "retentionInDays": retention,
                    "storedBytes": group.get("storedBytes"),
                },
                region=region,
            )
        )

    return findings


def check_timeout(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-006 — the function is still on the 3-second default timeout.

    Three seconds is the AWS default and it is almost never a deliberate
    choice. Any handler that makes a paginated API call, cold-starts a boto3
    client and does real work will exceed it, and the failure mode is a
    timeout with no exception and no stack trace: the log ends mid-sentence.

    The instinct to keep timeouts tight to control cost is backwards. You are
    billed for duration ACTUALLY USED, not for the timeout. A generous timeout
    costs nothing; it only bounds your worst case.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    timeout = function.get("Timeout")

    if timeout is None or timeout > DEFAULT_LAMBDA_TIMEOUT:
        return findings

    findings.append(
        Finding(
            check_id="CMP-006",
            severity="MEDIUM",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title=f"Timeout is {timeout}s — the AWS default",
            detail=(
                f"{name} times out after {timeout} second(s). Anything that "
                f"makes an AWS API call from a cold start can exceed this, and "
                f"a timeout produces no exception and no stack trace — the log "
                f"simply stops. This is the hardest Lambda failure to diagnose "
                f"precisely because it looks like nothing happened."
            ),
            remediation=(
                "Set the timeout from measured p99 duration plus headroom "
                "(60s is comfortable for an auditing function). You are billed "
                "for duration used, not for the timeout, so raising it costs "
                "nothing. Pair it with a CloudWatch alarm on Duration so you "
                "notice when the real work starts creeping upward."
            ),
            evidence={"Timeout": timeout, "default": DEFAULT_LAMBDA_TIMEOUT},
            region=region,
        )
    )
    return findings


def check_reserved_concurrency(
    function: Dict[str, Any],
    concurrency: Optional[int] = None,
    region: str = "",
) -> List[Finding]:
    """CMP-007 — no reserved concurrency, so the function can consume the account.

    ⚠️ This is Day 04's second silent cost-growth trap, and the expensive one.

    Unreserved means this function may scale to the whole account concurrency
    limit — 1,000 by default. That is fine until the function is part of a
    loop: it writes to the bucket that triggers it, or an EventBridge rule
    fires on an API call the function itself makes. Then it is an infinite
    loop running a thousand copies in parallel, billing at machine speed,
    while you sleep. People have woken to five-figure bills from two lines.

    Reserved concurrency is the only control that makes that PHYSICALLY
    impossible rather than merely unlikely. It also protects every other
    function in the account, because concurrency is a shared pool: one runaway
    function throttles everything else you run.

    `concurrency` comes from get_function_concurrency; None means unreserved.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")

    if concurrency is None:
        concurrency = function.get("ReservedConcurrentExecutions")

    if concurrency is not None and concurrency >= 0:
        return findings

    findings.append(
        Finding(
            check_id="CMP-007",
            severity="MEDIUM",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title="No reserved concurrency — unbounded scale-out",
            detail=(
                f"{name} has no reserved concurrency, so it can scale to the "
                f"account limit (1,000 concurrent executions by default). A "
                f"recursive trigger — the function writing to whatever invokes "
                f"it — becomes a thousand parallel copies billing continuously, "
                f"and throttles every other function in the account with it."
            ),
            remediation=(
                "Set reserved_concurrent_executions to the smallest number that "
                "meets the requirement (2 is plenty for a scheduled auditor). "
                "Add a CloudWatch alarm on ConcurrentExecutions and an AWS "
                "Budget with an actual notification target. Note that 0 is a "
                "valid value and means fully throttled — useful as a kill "
                "switch, catastrophic as a typo."
            ),
            evidence={"ReservedConcurrentExecutions": concurrency},
            region=region,
        )
    )
    return findings


def check_runtime(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-008 — the function runs a deprecated or out-of-support runtime.

    Deprecated means AWS has stopped patching the runtime. First you lose
    security updates, then Lambda blocks configuration updates, then it blocks
    invocations. The migration is not optional, only deferred — and it is
    always deferred to the worst possible week.

    Container-image and custom-runtime functions have no Runtime field at all;
    that is not a finding, it is a different packaging model with its own
    patching responsibility.

    Note this check stays SILENT on the Day 04 Terraform stack, which pins
    python3.12 everywhere. That is deliberate: a check that only ever fires is
    indistinguishable from a check that is hard-coded to fire, and you want at
    least one clean control in the set. tests/test_checks.py asserts zero
    findings for CMP-008 against the whole stack.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    runtime = function.get("Runtime")

    if not runtime:
        return findings
    if runtime not in DEPRECATED_RUNTIMES:
        return findings

    findings.append(
        Finding(
            check_id="CMP-008",
            severity="HIGH",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title=f"Deprecated runtime: {runtime}",
            detail=(
                f"{name} runs {runtime}, which AWS has deprecated or scheduled "
                f"for end of support. Deprecated runtimes stop receiving "
                f"security patches first, then lose the ability to be updated, "
                f"then lose the ability to be invoked."
            ),
            remediation=(
                "Move to a currently supported runtime and test properly — "
                "major-version jumps in Python and Node break on removed stdlib "
                "and on transitive dependencies more often than the release "
                "notes suggest. Check the current support table at "
                "docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html and "
                "put the next EOL date in a calendar, because nobody else will."
            ),
            evidence={"Runtime": runtime},
            region=region,
        )
    )
    return findings


def check_tracing(function: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-009 — X-Ray active tracing is disabled.

    LOW severity, and honestly so: nothing is insecure and nothing is broken.
    What you lose is the ability to answer "where did the 4 seconds go" without
    adding print statements and redeploying into an incident.

    PassThrough — the default — means the function only participates in a trace
    if something upstream already started one. For an EventBridge-triggered
    function, nothing upstream ever does, so PassThrough means no tracing at
    all in practice.

    Free at this scale: the first 100,000 traces a month are permanently free,
    and this stack produces a few hundred.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    mode = (function.get("TracingConfig") or {}).get("Mode", "PassThrough")

    if mode == "Active":
        return findings

    findings.append(
        Finding(
            check_id="CMP-009",
            severity="LOW",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title=f"X-Ray tracing is {mode}, not Active",
            detail=(
                f"{name} has tracing mode {mode}. It will only appear in a "
                f"trace if an upstream caller already started one, which for an "
                f"event-driven function means never. When this function is slow "
                f"you will have no breakdown of where the time went."
            ),
            remediation=(
                'Set tracing_config { mode = "Active" } and attach '
                "AWSXRayDaemonWriteAccess to the execution role — the mode "
                "alone does nothing without the permission, which is the usual "
                "reason people think they enabled tracing and see no traces. "
                "First 100,000 traces per month are free."
            ),
            evidence={"TracingConfig": function.get("TracingConfig")},
            region=region,
        )
    )
    return findings


###############################################################################
# Messaging checks — SNS and SQS
###############################################################################


def check_sns_encryption(topic: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-010 — the SNS topic is not encrypted at rest.

    `topic` is the Attributes dict from get_topic_attributes, with TopicArn
    included. Unlike S3, SNS has no default encryption: a topic with no
    KmsMasterKeyId stores message bodies unencrypted between publish and
    delivery. For a compliance-findings topic, those bodies are a list of
    exactly which of your resources are misconfigured.

    alias/aws/sns — the AWS-managed key — satisfies this check. It is a lesser
    control than a customer-managed key (see CMP-003) but it is encryption,
    and grading it as absent would be wrong.
    """
    findings: List[Finding] = []
    arn = topic.get("TopicArn", "unknown")
    name = arn.rsplit(":", 1)[-1] if ":" in arn else arn

    if topic.get("KmsMasterKeyId"):
        return findings

    findings.append(
        Finding(
            check_id="CMP-010",
            severity="MEDIUM",
            resource_type="AWS::SNS::Topic",
            resource_id=name,
            title="SNS topic is not encrypted at rest",
            detail=(
                f"Topic {name} has no KmsMasterKeyId. Message bodies are stored "
                f"unencrypted while in transit through SNS. On a findings topic "
                f"those bodies enumerate your misconfigurations, which is "
                f"precisely the thing you would not put in a public channel."
            ),
            remediation=(
                'Set kms_master_key_id — "alias/aws/sns" is free and immediate, '
                "a customer-managed key costs ~$1/month and gives you key "
                "policy control and a Decrypt audit trail. Remember to grant "
                "kms:GenerateDataKey to every publisher, or publishes start "
                "failing with an authorisation error that names KMS, not SNS."
            ),
            evidence={"TopicArn": arn, "KmsMasterKeyId": None},
            region=region,
        )
    )
    return findings


def check_sns_topic_policy(topic: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-011 — the topic policy allows a wildcard principal.

    `Principal: "*"` with no condition means every AWS principal on Earth may
    perform the listed actions. On a topic with an email subscription that is
    a spam relay wearing your alerting pipeline's name — messages arrive from
    your own topic, look legitimate, and there is no way for the reader to
    tell.

    The default SNS topic policy is already account-scoped, so this only
    happens when somebody widens it deliberately, usually to make a
    cross-account publish work.

    A wildcard principal narrowed by a Condition on AWS:SourceAccount,
    AWS:SourceOwner, AWS:SourceArn or aws:PrincipalOrgID is NOT a finding —
    that is the normal, correct way to write these policies. The Day 04 stack's
    own findings topic does exactly that and this check must stay silent on it.
    """
    findings: List[Finding] = []
    arn = topic.get("TopicArn", "unknown")
    name = arn.rsplit(":", 1)[-1] if ":" in arn else arn
    document = parse_policy(topic.get("Policy"))

    offenders: List[Dict[str, Any]] = []

    for statement in as_list(document.get("Statement")):
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue

        principal = statement.get("Principal")
        wildcard = False
        if principal == "*":
            wildcard = True
        elif isinstance(principal, dict):
            for value in principal.values():
                if any(str(v) == "*" for v in as_list(value)):
                    wildcard = True

        if not wildcard:
            continue

        # A condition that pins the source account, owner, ARN or org is the
        # accepted way to write a scoped policy with a wildcard principal.
        condition = statement.get("Condition") or {}
        scoped = False
        for operator_values in condition.values():
            if not isinstance(operator_values, dict):
                continue
            for condition_key in operator_values:
                if str(condition_key).lower() in (
                    "aws:sourceaccount",
                    "aws:sourceowner",
                    "aws:sourcearn",
                    "aws:principalorgid",
                    "aws:principalaccount",
                    "aws:principalarn",
                ):
                    scoped = True

        if scoped:
            continue

        offenders.append(
            {
                "Sid": statement.get("Sid"),
                "Action": [str(a) for a in as_list(statement.get("Action"))],
                "Principal": principal,
            }
        )

    if not offenders:
        return findings

    actions = sorted({a for o in offenders for a in o["Action"]})
    findings.append(
        Finding(
            check_id="CMP-011",
            severity="CRITICAL",
            resource_type="AWS::SNS::Topic",
            resource_id=name,
            title="Topic policy allows a wildcard principal",
            detail=(
                f"Topic {name} has {len(offenders)} unconditioned Allow "
                f"statement(s) with Principal \"*\", granting: "
                f"{', '.join(actions) or 'unspecified actions'}. Any AWS "
                f"account in the world can use this topic. If it has an email "
                f"subscription, strangers can send mail that appears to come "
                f"from your own alerting pipeline."
            ),
            remediation=(
                "Name the principals explicitly, or keep the wildcard and add "
                'Condition { StringEquals = { "AWS:SourceAccount" = '
                "<your-account-id> } }. For cross-account publishing, list the "
                "specific account ARNs — an org-wide condition on "
                "aws:PrincipalOrgID is the next best thing if the list is long."
            ),
            evidence={"TopicArn": arn, "statements": offenders[:10]},
            region=region,
        )
    )
    return findings


def check_sqs_encryption(queue: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-012 — the SQS queue is not encrypted at rest.

    `queue` is the Attributes dict from get_queue_attributes with QueueUrl
    added. Two mutually exclusive ways to satisfy this:
      * SqsManagedSseEnabled — SSE-SQS, free, zero configuration.
      * KmsMasterKeyId — SSE-KMS, key policy control, costs per request.

    Setting both is a validation error, so the check accepts either.

    This matters more on a dead letter queue than on a working queue. A DLQ
    holds precisely the payloads that broke — the malformed record, the
    unexpected field, the customer data your parser choked on — and it holds
    them for up to fourteen days.
    """
    findings: List[Finding] = []
    url = queue.get("QueueUrl", "")
    arn = queue.get("QueueArn", "")
    name = (arn.rsplit(":", 1)[-1] if arn else url.rsplit("/", 1)[-1]) or "unknown"

    sse_sqs = str(queue.get("SqsManagedSseEnabled", "false")).lower() == "true"
    if sse_sqs or queue.get("KmsMasterKeyId"):
        return findings

    findings.append(
        Finding(
            check_id="CMP-012",
            severity="MEDIUM",
            resource_type="AWS::SQS::Queue",
            resource_id=name,
            title="SQS queue is not encrypted at rest",
            detail=(
                f"Queue {name} has neither SqsManagedSseEnabled nor a "
                f"KmsMasterKeyId, so message bodies sit unencrypted for the "
                f"whole retention period. On a dead letter queue that is up to "
                f"fourteen days of exactly the payloads that failed to process."
            ),
            remediation=(
                "Set sqs_managed_sse_enabled = true (free, instant, no key "
                "policy to maintain) or kms_master_key_id for customer-managed "
                "control. They are mutually exclusive — setting both is a "
                "plan-time error. Add a queue policy denying "
                "aws:SecureTransport = false as well; SQS accepts plain HTTP "
                "and there is no toggle for it."
            ),
            evidence={
                "QueueUrl": url,
                "SqsManagedSseEnabled": queue.get("SqsManagedSseEnabled"),
                "KmsMasterKeyId": None,
            },
            region=region,
        )
    )
    return findings


def check_sqs_redrive(
    queue: Dict[str, Any],
    known_dlq_arns: Optional[Set[str]] = None,
    region: str = "",
) -> List[Finding]:
    """CMP-013 — the queue has no redrive policy, so it has no dead letter queue.

    Without one, a message that cannot be processed is returned to the queue
    after every visibility timeout and retried until it expires. Two costs:
    you pay for the retries, and the poison message sits at the head of the
    queue delaying everything behind it.

    A dead letter queue having no dead letter queue of its own is CORRECT, not
    a finding, and the check must not fire on one. Two ways to recognise one,
    both used here:
      * It is the redrive target of another queue, or the DLQ target of a
        Lambda function — the collector passes those ARNs in as known_dlq_arns.
      * Its name says so. Naming is not evidence, but a queue called
        `orders-dlq` in an account whose source queue lives in another stack
        would otherwise generate a finding nobody can action.

    This exemption is why the Day 04 stack's own scanner DLQ produces no
    finding while the broken queue does.
    """
    findings: List[Finding] = []
    url = queue.get("QueueUrl", "")
    arn = queue.get("QueueArn", "")
    name = (arn.rsplit(":", 1)[-1] if arn else url.rsplit("/", 1)[-1]) or "unknown"

    if queue.get("RedrivePolicy"):
        return findings

    if known_dlq_arns and arn and arn in known_dlq_arns:
        return findings

    if _DLQ_NAME_HINT.search(name):
        return findings

    findings.append(
        Finding(
            check_id="CMP-013",
            severity="MEDIUM",
            resource_type="AWS::SQS::Queue",
            resource_id=name,
            title="Queue has no redrive policy — no dead letter queue",
            detail=(
                f"Queue {name} has no RedrivePolicy. A message that fails "
                f"processing is retried until it expires "
                f"({queue.get('MessageRetentionPeriod', 'unknown')} seconds of "
                f"retention), holding up everything behind it and billing for "
                f"every attempt. Nothing captures it for inspection."
            ),
            remediation=(
                "Create a second queue as the dead letter target and set "
                "redrive_policy with maxReceiveCount (5 is a reasonable "
                "starting point). Give the DLQ the maximum 14-day retention — "
                "a DLQ that expires before a human reads it is decoration — "
                "and alarm on ApproximateNumberOfMessagesVisible > 0, because "
                "a DLQ nobody watches is a folder of unread incidents."
            ),
            evidence={"QueueUrl": url, "RedrivePolicy": None},
            region=region,
        )
    )
    return findings


###############################################################################
# EventBridge checks
###############################################################################


def check_rule_state(rule: Dict[str, Any], region: str = "") -> List[Finding]:
    """CMP-014 — the rule exists but is DISABLED.

    This is the outage that survives a code review, an architecture review and
    a screenshot in the runbook. The rule is there. The target is wired. The
    permission is correct. The diagram is accurate. Nothing runs, and nothing
    anywhere reports an error, because a disabled rule is not an error — it is
    a configuration.

    The usual origin is somebody disabling it during an incident and never
    re-enabling it. Six months later the scheduled compliance scan has not run
    once and nobody noticed, because the absence of findings looks identical to
    the absence of problems.

    A rule with ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS state is enabled
    and must not fire this check.
    """
    findings: List[Finding] = []
    name = rule.get("Name", "unknown")
    state = rule.get("State", "ENABLED")

    if str(state).upper().startswith("ENABLED"):
        return findings

    findings.append(
        Finding(
            check_id="CMP-014",
            severity="MEDIUM",
            resource_type="AWS::Events::Rule",
            resource_id=name,
            title="EventBridge rule is DISABLED",
            detail=(
                f"Rule {name} exists with state {state} and will never fire. "
                f"Schedule/pattern: "
                f"{rule.get('ScheduleExpression') or rule.get('EventPattern') or 'none'}. "
                f"Nothing reports this as an error — a disabled rule produces "
                f"silence, and silence looks exactly like success."
            ),
            remediation=(
                "Re-enable it (`aws events enable-rule --name <name>`) or "
                "delete it. A permanently disabled rule is worse than no rule, "
                "because it makes the diagram look right. If the schedule "
                "matters, alarm on the rule's Invocations metric being zero "
                "over a window longer than its interval — that turns silence "
                "back into a signal."
            ),
            evidence={
                "State": state,
                "ScheduleExpression": rule.get("ScheduleExpression"),
                "EventPattern": rule.get("EventPattern"),
            },
            region=region,
        )
    )
    return findings


def check_rule_targets(
    rule: Dict[str, Any],
    targets: List[Dict[str, Any]],
    region: str = "",
) -> List[Finding]:
    """CMP-015 — a target has neither a retry policy nor a dead letter queue.

    EventBridge delivery and Lambda execution are different failure domains and
    both need configuring. The Lambda-side retry config governs what happens
    once the function has been invoked and failed. This governs what happens
    when EventBridge cannot deliver the event at all — a throttle, a
    permissions change, the function being temporarily unavailable.

    With neither set, EventBridge retries for 24 hours on defaults and then
    discards the event, and the only evidence is a FailedInvocations metric
    with no payload attached.

    One finding per offending target, because in a fan-out rule one target
    being unprotected while the others are fine is precisely the detail worth
    surfacing.
    """
    findings: List[Finding] = []
    rule_name = rule.get("Name", "unknown")

    for target in targets or []:
        target_id = target.get("Id", "unknown")
        has_retry = bool(target.get("RetryPolicy"))
        has_dlq = bool((target.get("DeadLetterConfig") or {}).get("Arn"))

        if has_retry or has_dlq:
            continue

        findings.append(
            Finding(
                check_id="CMP-015",
                severity="LOW",
                resource_type="AWS::Events::Target",
                resource_id=f"{rule_name}/{target_id}",
                title="Target has no retry policy and no dead letter queue",
                detail=(
                    f"Target {target_id} on rule {rule_name} has neither a "
                    f"RetryPolicy nor a DeadLetterConfig. Delivery failures are "
                    f"retried on defaults for 24 hours and then discarded, "
                    f"leaving a FailedInvocations metric and no payload."
                ),
                remediation=(
                    "Add retry_policy { maximum_retry_attempts, "
                    "maximum_event_age_in_seconds } and dead_letter_config { "
                    "arn = <sqs-queue-arn> } to the target. The queue needs a "
                    "policy allowing events.amazonaws.com to SendMessage, "
                    "scoped by aws:SourceArn to this rule — without it "
                    "EventBridge silently cannot write to your DLQ, which is "
                    "the same failure one layer further down."
                ),
                evidence={
                    "rule": rule_name,
                    "target_id": target_id,
                    "target_arn": target.get("Arn"),
                    "RetryPolicy": None,
                    "DeadLetterConfig": None,
                },
                region=region,
            )
        )

    return findings


def check_public_access(
    function: Dict[str, Any],
    url_config: Optional[Dict[str, Any]] = None,
    policy: Any = None,
    region: str = "",
) -> List[Finding]:
    """CMP-016 — the function is reachable by anyone.

    Two independent routes to the same outcome:

      1. A function URL with AuthType NONE. That is a public HTTPS endpoint on
         the open internet with no authentication whatsoever, and it is two
         clicks in the console.
      2. A resource policy statement allowing Principal "*" to InvokeFunction
         with no condition narrowing it.

    The second is subtler and much more common. `lambda add-permission` with
    `--principal "*"` appears in a lot of blog posts as the quick way to make
    an integration work. Service principals like events.amazonaws.com scoped by
    SourceArn are normal and correct — this check must not fire on those, and
    on the Day 04 stack it does not fire at all.

    Like CMP-008, this stays SILENT on the Day 04 Terraform stack by design.
    tests/test_checks.py asserts zero findings for it against the whole stack:
    a check set where everything fires teaches you nothing about false
    positives, and false positives are how audit tools get ignored.
    """
    findings: List[Finding] = []
    name = function.get("FunctionName", "unknown")
    reasons: List[Dict[str, Any]] = []

    if url_config and str(url_config.get("AuthType", "")).upper() == "NONE":
        reasons.append(
            {
                "route": "function URL",
                "AuthType": url_config.get("AuthType"),
                "FunctionUrl": url_config.get("FunctionUrl"),
            }
        )

    document = parse_policy(policy)
    for statement in as_list(document.get("Statement")):
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue

        principal = statement.get("Principal")
        wildcard = principal == "*"
        if isinstance(principal, dict):
            for key, value in principal.items():
                if str(key).lower() == "service":
                    continue  # service principals are handled below
                if any(str(v) == "*" for v in as_list(value)):
                    wildcard = True

        if not wildcard:
            continue

        condition = statement.get("Condition") or {}
        scoped = False
        for operator_values in condition.values():
            if not isinstance(operator_values, dict):
                continue
            for condition_key in operator_values:
                if str(condition_key).lower() in (
                    "aws:sourcearn",
                    "aws:sourceaccount",
                    "aws:principalorgid",
                    "aws:principalaccount",
                    "lambda:functionurlauthtype",
                ):
                    scoped = True

        if scoped:
            continue

        reasons.append(
            {
                "route": "resource policy",
                "Sid": statement.get("Sid"),
                "Action": [str(a) for a in as_list(statement.get("Action"))],
                "Principal": principal,
            }
        )

    if not reasons:
        return findings

    routes = sorted({str(r["route"]) for r in reasons})
    findings.append(
        Finding(
            check_id="CMP-016",
            severity="CRITICAL",
            resource_type="AWS::Lambda::Function",
            resource_id=name,
            title="Function is publicly invokable",
            detail=(
                f"{name} is reachable without authentication via "
                f"{' and '.join(routes)}. Anyone who finds the URL or knows the "
                f"function ARN can invoke it, at your expense, with whatever "
                f"payload they choose — and it runs with the execution role's "
                f"permissions."
            ),
            remediation=(
                "Set the function URL AuthType to AWS_IAM, or put the function "
                "behind API Gateway or CloudFront with a real authorizer. For "
                "the resource policy, remove the wildcard statement (`aws "
                f"lambda remove-permission --function-name {name} --statement-id "
                "<sid>`) and re-add it scoped to the specific service principal "
                "and SourceArn. Then check CloudWatch Invocations for how long "
                "it has been open and to whom."
            ),
            evidence={"routes": reasons[:10]},
            region=region,
        )
    )
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
        prog="serverless_audit.py",
        description=(
            "Audit AWS Lambda functions, execution roles, log groups, SNS "
            "topics, SQS queues and EventBridge rules for serverless "
            "compliance and reliability misconfigurations."
        ),
        epilog=(
            "Examples:\n"
            "  serverless_audit.py --profile bootcamp --region us-east-1\n"
            "  serverless_audit.py --format json --quiet > findings.json\n"
            "  serverless_audit.py --min-severity HIGH --format csv\n"
            "  serverless_audit.py --fail-on CRITICAL   # exit 1 on any CRITICAL\n"
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
