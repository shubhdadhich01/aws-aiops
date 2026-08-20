#!/usr/bin/env python3
"""
iac_audit.py — Day 05 Infrastructure as Code auditor.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

Audits Terraform configuration on disk — plus, optionally, the live state
bucket behind it — for the mistakes that turn "it applied fine" into a leaked
credential, an unrecoverable state file, or a bucket nobody can attribute.

This tool is different from Days 01–04 in one important way: most of its
checks read FILES, not AWS. That is deliberate. Every finding a static check
produces is one you can fix before `terraform apply` runs, which is the only
time fixing it is cheap. By the time IAM or GuardDuty can see the problem, the
resource exists, something depends on it, and the fix has a change window.

    Static  (no credentials needed)   IAC-001 002 003 005 008 009 010 011
                                      012 013 014 016
    Live    (credentials needed)      IAC-004 006 007 015

What it checks
--------------
    IAC-001  Hardcoded secret in .tf      git history, state, and CI logs   CRITICAL
    IAC-002  Credentials in provider      the key that mines crypto for you CRITICAL
    IAC-003  .tfstate committed to git    every attribute, in plaintext     CRITICAL
    IAC-004  State bucket publicly open   your infrastructure, as a service CRITICAL
    IAC-005  No backend — local state     one laptop holds the only map     HIGH
    IAC-006  State bucket unversioned     no rollback from a corrupt write  HIGH
    IAC-007  State bucket unencrypted     secrets at rest in the clear      HIGH
    IAC-008  Output not marked sensitive  printed by every apply, kept by CI HIGH
    IAC-009  0.0.0.0/0 on an INGRESS rule scanned within minutes of existing HIGH
    IAC-010  No required_version          state format upgrades are one-way MEDIUM
    IAC-011  Provider version unpinned    a plan with changes nobody wrote  MEDIUM
    IAC-012  Lock file gitignored         reproducibility thrown away       MEDIUM
    IAC-013  Stateful, no prevent_destroy nothing between destroy and data  MEDIUM
    IAC-014  Resource missing tags        unattributable, uncleanable spend MEDIUM
    IAC-015  Deployed tags have drifted   the console won, quietly          MEDIUM
    IAC-016  count / untyped variable     position-addressed resources      LOW

Expected output against this repository's `lab/terraform/` directory:

    13 findings  with no AWS credentials at all (static checks only)
    15 findings  with credentials, once `create_insecure_examples = true`
                 has been applied (IAC-006 and IAC-007 fire on the deliberately
                 misconfigured example bucket in envs/dev)
    16 findings  after Step 6 of the lab changes the CostCentre tag on
                 aws_cloudwatch_log_group.drift_target in the console (IAC-015)

    Compliance score 0/100 in every one of those cases. The weights total 106,
    126 and 130 respectively, and the score floors at zero.

Two checks are SILENT BY DESIGN, and that is the point
------------------------------------------------------
IAC-003 and IAC-004 are fully implemented, fully tested, and find nothing in
this repository. A check set where everything fires teaches you that findings
are normal. A check set with two deliberate zeroes teaches you that a quiet
check is evidence, which is the more useful lesson.

    IAC-003  There is no committed .tfstate anywhere in the lab, and every
             .gitignore in the tree covers it. The check still runs on every
             directory; it simply has nothing to say.

    IAC-004  This repository does not ship a publicly readable S3 bucket even
             as a teaching example. Being one `terraform apply` away from
             leaking somebody's data is not a lesson worth the demonstration.
             The deliberately insecure example bucket in envs/dev has a REAL
             public access block for exactly this reason — it exists to fire
             IAC-006 and IAC-007 and nothing else.

IAC-015 is silent for a different reason, and it is worth being precise about
the difference: drift does not exist on a fresh apply, by definition. It is
not silent by design, it is silent by situation, and Step 6 of the lab makes
it fire on purpose.

Why regex and not a real HCL parser
-----------------------------------
boto3 is the only dependency. There is no python-hcl2 here, and that is a
choice, not an oversight.

A linter you can run on a locked-down bastion beats a perfect parser you
cannot install. The machines where this matters most — the jump host with no
egress, the CI runner with a locked requirements file, the laptop of the
person who has ninety seconds before the change window closes — are exactly
the machines where `pip install python-hcl2` fails. Every check below is built
on `re` and the standard library, so the tool runs anywhere Python does.

That decision has a real cost, and pretending otherwise would be worse than
paying it. What regex-based HCL parsing gets WRONG:

  * Nested heredocs. The scanner handles one level correctly and will lose
    track of a heredoc opened inside another heredoc's body.
  * Interpolated attribute names — `"${var.key}" = "value"` inside a map is
    not matched by an identifier pattern.
  * `dynamic` blocks. A `dynamic "ingress"` block that generates a wide-open
    rule from a variable is invisible here. The generated content does not
    exist until plan time.
  * Values that are expressions. `cidr_blocks = var.allowed` cannot be
    resolved without evaluating variables, so it is not flagged.
  * Modules from the registry or git. Only local `.tf` files on the scanned
    path are read; a public module's contents are never fetched.

All of those are FALSE NEGATIVES — cases where the tool stays quiet about a
real problem. That is the correct direction to fail in for a linter that runs
in CI, because a false positive gets the whole tool disabled and a false
negative gets it supplemented. But do not read a clean run as proof of
anything. Read it as "none of the sixteen specific, named faults below are
present in a form this tool can see".

Scoping decisions the reference Terraform relies on
---------------------------------------------------
These are documented in each check's docstring and in bad-examples/README.md:

  * IAC-005 respects an inline `# iac-audit: allow-local-state` marker.
  * IAC-012 flags a GITIGNORED lock file; the missing-file arm needs evidence
    that `init` was run.
  * IAC-014 evaluates root modules only and respects `default_tags`.
  * IAC-009 looks at INGRESS only. Egress to 0.0.0.0/0 is normal.
  * IAC-013 covers S3, RDS instances, RDS clusters and DynamoDB tables.
  * Only `.tf` files are parsed. `.tfvars` files are gitignored by convention
    across this repository and `.tfvars.example` files must never hold real
    values; scanning them would flag placeholders and train people to ignore
    the output.

Usage
-----
    python3 iac_audit.py --path ../terraform
    python3 iac_audit.py --path ../terraform --profile bootcamp --region us-east-1 \
        --state-bucket cbc-day05-tfstate-a1b2c3
    python3 iac_audit.py --path . --format json --quiet > findings.json
    python3 iac_audit.py --min-severity HIGH --format csv
    python3 iac_audit.py --fail-on CRITICAL     # non-zero exit for CI

Running with no --profile and no credentials is a supported, first-class mode:
the static checks run, the live ones are skipped with a note, and the exit
code still works for CI. That is the mode a pre-commit hook should use.

Note on --min-severity: it filters the DISPLAY only. The score always reflects
every finding. Otherwise anyone could "improve" their compliance posture by
passing --min-severity CRITICAL, which is not an improvement, it is a habit.

Required IAM permissions for the live checks (all read-only):
    s3:ListAllMyBuckets
    s3:GetBucketLocation
    s3:GetBucketVersioning
    s3:GetEncryptionConfiguration
    s3:GetBucketPublicAccessBlock
    s3:GetBucketPolicyStatus
    s3:GetBucketAcl
    logs:DescribeLogGroups
    logs:ListTagsForResource

The SecurityAudit or ReadOnlyAccess managed policy covers all of these.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
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
# Severity model
#
# The score starts at 100 and each finding subtracts its severity weight.
# Floor is 0 — a score cannot go negative, because "how much worse than
# completely broken is this" is not a useful question.
#
# Identical weights to Day 03's ha_audit.py and Day 04's serverless_audit.py on
# purpose. By Day 10 you will have five of these tools and one mental model for
# reading their output; changing the arithmetic per tool would make the numbers
# incomparable for no gain.
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

    check_id     Stable identifier (IAC-001 ...). Never renumber these — people
                 write suppressions and dashboards against them.
    severity     One of SEVERITY_ORDER.
    resource_type / resource_id   What is broken. For static findings the
                 resource_id is the Terraform address (module path plus
                 `type.name`) so it can be grepped for directly.
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
# An account with 400 buckets is not unusual; an audit that reports on the
# first 50 and says nothing about the rest is worse than no audit, because it
# produces a clean report you believe.
###############################################################################


def paginate(client: Any, operation: str, result_key: str, **kwargs: Any) -> List[Any]:
    """Collect every page of a paginated boto3 operation into one list.

    Falls back to a single direct call for operations that have no paginator
    registered (s3:ListBuckets is like this).
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

    IAM hands them back URL-encoded JSON strings, S3 hands them back as plain
    JSON strings, and our own tests hand them back as dicts. Accept all three
    rather than making every caller remember which is which.
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
# HCL parsing helpers
#
# Five small functions, each independently unit-tested in tests/test_checks.py.
# Every check is built on these rather than on ad-hoc regexes, so that a
# parsing bug is fixed in ONE place instead of sixteen.
#
# The scanner is deliberately single-pass and offset-preserving where it can
# be. Strings, heredocs and comments interact — a `#` inside a string is not a
# comment, a `"` inside a comment is not a string — so they cannot be handled
# by three independent passes without getting one of those cases wrong.
###############################################################################

_HEREDOC_START = re.compile(r"<<-?[ \t]*(?P<tag>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\r?\n")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def _skip_double_quoted(text: str, start: int) -> int:
    """Return the index one past the closing quote of the string at `start`."""
    i = start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        if c == "\n":
            # An unterminated string. HCL does not allow raw newlines inside
            # quoted strings, so treat the newline as the terminator rather
            # than swallowing the rest of the file.
            return i
        i += 1
    return n


def _skip_heredoc(text: str, match: "re.Match[str]") -> int:
    """Return the index one past the terminator line of a heredoc.

    Handles `<<EOT` and `<<-EOT`. Does NOT handle a heredoc opened inside
    another heredoc's body — see the module docstring.
    """
    tag = match.group("tag")
    i = match.end()
    n = len(text)
    terminator = re.compile(r"(?m)^[ \t]*" + re.escape(tag) + r"[ \t]*$")
    found = terminator.search(text, i)
    if not found:
        return n
    # Stop AT the end of the terminator word, not past the newline after it.
    # That newline has to stay visible as code, or _capture_value has no
    # terminator for an attribute whose value is a heredoc and swallows the
    # next attribute along with it.
    return found.end()


def strip_hcl_comments(text: str) -> str:
    """Remove `#`, `//` and `/* */` comments from HCL, preserving line numbers.

    String literals and heredoc bodies are left completely untouched. This
    matters more than it sounds: `envs/dev/outputs.tf` has a heredoc whose
    body contains `# should NOT exist` as instructional text, and a naive
    comment stripper would silently truncate the rest of that line.

    Newlines are preserved so that line numbers in the stripped text still
    correspond to line numbers in the original file.
    """
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]

        if c == "<" and text.startswith("<<", i):
            match = _HEREDOC_START.match(text, i)
            if match:
                end = _skip_heredoc(text, match)
                out.append(text[i:end])
                i = end
                continue

        if c == '"':
            end = _skip_double_quoted(text, i)
            out.append(text[i:end])
            i = end
            continue

        if c == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end  # leave the newline in place
            continue

        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("\n" * text.count("\n", i, end))
            i = end
            continue

        out.append(c)
        i += 1

    return "".join(out)


def _code_mask(text: str) -> bytearray:
    """1 where a character is code, 0 where it is inside a string or heredoc.

    Brace balancing has to consult this. `bucket = "a{b"` and the `${...}`
    interpolation in every heredoc in this repository both contain braces that
    are not block delimiters, and a brace counter that does not know the
    difference loses track of the block on the first one it meets.
    """
    n = len(text)
    mask = bytearray(b"\x01") * n
    i = 0
    while i < n:
        c = text[i]
        if c == "<" and text.startswith("<<", i):
            match = _HEREDOC_START.match(text, i)
            if match:
                end = _skip_heredoc(text, match)
                for j in range(i, end):
                    mask[j] = 0
                i = end
                continue
        if c == '"':
            end = _skip_double_quoted(text, i)
            for j in range(i, end):
                mask[j] = 0
            i = end
            continue
        i += 1
    return mask


def _match_brace(text: str, open_index: int, mask: bytearray) -> int:
    """Given the index of a `{`, return the index one past its matching `}`."""
    depth = 0
    i = open_index
    n = len(text)
    while i < n:
        if mask[i]:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return n


@dataclass
class HclBlock:
    """One `type "label" "label" { ... }` block.

    body is the text BETWEEN the braces, comments already stripped.
    address is the conventional Terraform address for resource/data/module
    blocks and a readable substitute for everything else.
    """

    block_type: str
    labels: List[str]
    body: str
    filename: str = ""
    line: int = 0

    @property
    def address(self) -> str:
        if self.block_type == "resource" and len(self.labels) >= 2:
            return f"{self.labels[0]}.{self.labels[1]}"
        if self.block_type == "data" and len(self.labels) >= 2:
            return f"data.{self.labels[0]}.{self.labels[1]}"
        if self.labels:
            return f"{self.block_type}.{'.'.join(self.labels)}"
        return self.block_type

    @property
    def name(self) -> str:
        return self.labels[-1] if self.labels else ""


def extract_blocks(text: str, block_type: str) -> List[HclBlock]:
    """Return every block of the given type, at any nesting depth.

    Brace-balanced and string-aware. Nesting depth is deliberately not
    restricted: `extract_blocks(body, "ingress")` finds the inline rule blocks
    inside a security group, and `extract_blocks(text, "resource")` finds top
    level resources, using the same code path.

    The caller passes comment-stripped text. Passing raw text mostly works and
    then fails on the one file where somebody wrote `# resource "x" "y" {` in
    a comment, so strip first.
    """
    blocks: List[HclBlock] = []
    mask = _code_mask(text)
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.\-])"
        + re.escape(block_type)
        + r"(?P<labels>(?:[ \t]+(?:\"[^\"\n]*\"|[A-Za-z_][A-Za-z0-9_.\-]*))*)"
        r"[ \t]*\{"
    )
    for match in pattern.finditer(text):
        if not mask[match.start()]:
            continue
        open_index = match.end() - 1
        close_index = _match_brace(text, open_index, mask)
        labels = [
            token.strip('"')
            for token in re.findall(
                r"\"[^\"\n]*\"|[A-Za-z_][A-Za-z0-9_.\-]*", match.group("labels")
            )
        ]
        blocks.append(
            HclBlock(
                block_type=block_type,
                labels=labels,
                body=text[open_index + 1 : max(open_index + 1, close_index - 1)],
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    return blocks


def _capture_value(text: str, start: int, mask: bytearray) -> Tuple[str, int]:
    """Read one attribute value beginning at `start`.

    Stops at the end of the logical line, but follows `{`, `[` and `(` to
    their matching close first, so that a multi-line map or list comes back
    whole. This is what makes `tags = { ... }` readable by the tag checks.

    Anything the mask marks as a string or heredoc body is skipped whole, so a
    newline inside a heredoc is not mistaken for the end of the attribute.
    """
    depth = 0
    i = start
    n = len(text)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        c = text[i]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in "\n,":
            # Newline ends an attribute in a block body; a comma ends one
            # inside an object literal such as
            # `aws = { source = "hashicorp/aws", version = "~> 5.80" }`.
            break
        i += 1
    return text[start:i], i


def block_attributes(body: str) -> Dict[str, str]:
    """Flatten the top-level `name = value` attributes of a block body.

    Nested blocks are SKIPPED, not descended into — use extract_blocks for
    those. So for a security group, `block_attributes` returns name,
    description and vpc_id, and the ingress rules come back separately.

    Values are returned as raw source text: `"us-east-1"` keeps its quotes,
    `var.thing` stays a reference, `{ ... }` comes back whole. Nothing is
    evaluated, because evaluating HCL is what Terraform is for.
    """
    attrs: Dict[str, str] = {}
    mask = _code_mask(body)
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if not mask[i] or c.isspace() or c in ",}])":
            i += 1
            continue
        if c == "{":
            i = _match_brace(body, i, mask)
            continue

        match = _IDENT.match(body, i)
        if not match:
            i += 1
            continue
        name = match.group(0)
        j = match.end()
        while j < n and body[j] in " \t":
            j += 1

        if j < n and body[j] == "=" and not body.startswith("==", j):
            j += 1
            while j < n and body[j] in " \t":
                j += 1
            value, j = _capture_value(body, j, mask)
            attrs[name] = value.strip()
            i = j
            continue

        # Not an assignment: either a nested block header or a bare token.
        # Skip to the end of the nested block if there is one on this line.
        k = j
        while k < n and body[k] not in "\n{":
            k += 1
        if k < n and body[k] == "{" and mask[k]:
            i = _match_brace(body, k, mask)
        else:
            i = match.end()
    return attrs


def unquote_hcl(value: str) -> str:
    """Strip the surrounding quotes from an HCL string literal, if present."""
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def object_body(value: str) -> str:
    """The inside of an HCL object literal, or the value unchanged.

    `{ source = "hashicorp/aws", version = "~> 5.80" }` -> everything between
    the braces, ready to hand to block_attributes. Anything that is not an
    object literal comes back untouched, so callers do not need to check first.
    """
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        return value[1:-1]
    return value


def is_literal_string(value: str) -> bool:
    """True if the value is a quoted literal with no interpolation in it.

    `"engineering"` is literal. `"${local.prefix}-demo"` is not, and neither
    is `var.owner`. Checks that compare values only ever compare literals,
    because everything else needs Terraform to resolve it.
    """
    value = value.strip()
    if not (len(value) >= 2 and value[0] == '"' and value[-1] == '"'):
        return False
    return "${" not in value


def find_gitignore_rules(directory: str, scan_root: str) -> List[str]:
    """Collect .gitignore patterns applying to `directory`, outermost first.

    Resolved upwards the way git does: a directory inherits every .gitignore
    above it, and the CLOSEST file wins on a conflict. Returning them
    outermost-first means a plain last-match-wins evaluation reproduces that
    precedence, including `!` negations.

    Walking stops at scan_root. Anything above the scanned tree belongs to a
    repository this tool was not pointed at, and silently reading files
    outside the path you were given is rude.
    """
    directory = os.path.abspath(directory)
    scan_root = os.path.abspath(scan_root)

    chain: List[str] = []
    current = directory
    while True:
        chain.append(current)
        if current == scan_root or os.path.dirname(current) == current:
            break
        parent = os.path.dirname(current)
        if not current.startswith(scan_root):
            break
        current = parent

    rules: List[str] = []
    for path in reversed(chain):  # outermost first
        candidate = os.path.join(path, ".gitignore")
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rules.append(line)
    return rules


def gitignore_ignores(rules: List[str], filename: str) -> bool:
    """Evaluate gitignore patterns against a bare filename, last match wins.

    Deliberately a subset of git's matcher: enough for filenames like
    `.terraform.lock.hcl` and `terraform.tfstate`, not enough for full
    pathspec semantics with `**`. The checks that use it only ever ask about
    single filenames, and a matcher that pretends to more fidelity than it has
    is how you get a check nobody trusts.
    """
    ignored = False
    for rule in rules:
        negated = rule.startswith("!")
        pattern = rule[1:] if negated else rule
        pattern = pattern.rstrip("/")
        if not pattern:
            continue
        if pattern.startswith("/"):
            pattern = pattern[1:]
        if fnmatch.fnmatch(filename, pattern):
            ignored = not negated
    return ignored


###############################################################################
# TerraformDirectory
#
# One directory of .tf files, loaded once and handed to every static check.
# Terraform's own unit of configuration is the directory, not the file: it
# concatenates every .tf file in a directory before evaluating anything, and
# the split into providers.tf / variables.tf / main.tf / outputs.tf is a
# convention with no meaning to the tool. The checks work the same way.
###############################################################################

TF_EXTENSIONS = (".tf",)
STATE_FILE_PATTERNS = ("*.tfstate", "*.tfstate.backup", "*.tfstate.*")
LOCK_FILE = ".terraform.lock.hcl"
ALLOW_LOCAL_STATE_MARKER = "iac-audit: allow-local-state"

# The marker must be a comment line of its own — `# iac-audit: allow-local-state`
# and nothing else on the line. A substring search would be suppressed by any
# file that merely TALKS about the marker, and bad-examples/providers.tf does
# exactly that in the essay explaining why it is not entitled to one. A
# suppression that can be triggered by documentation is not a suppression.
ALLOW_LOCAL_STATE_RE = re.compile(
    r"(?m)^[ \t]*(?:#|//)[ \t]*" + re.escape(ALLOW_LOCAL_STATE_MARKER) + r"[ \t]*$"
)


def has_local_state_suppression(directory: "TerraformDirectory") -> bool:
    """True if this directory carries the inline allow-local-state marker."""
    return bool(ALLOW_LOCAL_STATE_RE.search(directory.raw_text))

# Directories that are never Terraform configuration and are expensive or
# pointless to walk.
SKIP_DIRECTORIES = {".terraform", ".git", "__pycache__", ".pytest_cache", "node_modules"}


@dataclass
class TerraformDirectory:
    """Everything the static checks need about one directory, and nothing more.

    Constructed from disk by `load_directory`, or directly from a mapping of
    filename to content by `from_mapping` — which is how the unit tests build
    fixtures with no filesystem at all.

    raw_files      filename -> original text, comments and all
    files          filename -> comment-stripped text
    text           every stripped file concatenated, the way Terraform sees it
    """

    path: str
    label: str = ""
    raw_files: Dict[str, str] = field(default_factory=dict)
    gitignore_rules: List[str] = field(default_factory=list)
    has_terraform_dir: bool = False
    has_lock_file: bool = False
    state_files: List[str] = field(default_factory=list)

    files: Dict[str, str] = field(default_factory=dict, init=False)
    text: str = field(default="", init=False)

    def __post_init__(self) -> None:
        # `label` is what findings are addressed by: the directory relative to
        # the scanned root, so a resource_id reads `bad-examples::
        # aws_s3_bucket.reports` rather than repeating whatever ../../.. the
        # caller happened to type. `path` keeps the real filesystem path for
        # opening files and for evidence.
        self.label = self.label or os.path.basename(os.path.abspath(self.path))
        self.files = {
            name: strip_hcl_comments(content)
            for name, content in self.raw_files.items()
        }
        self.text = "\n".join(self.files[name] for name in sorted(self.files))

    @classmethod
    def from_mapping(
        cls,
        path: str,
        files: Dict[str, str],
        label: str = "",
        gitignore_rules: Optional[List[str]] = None,
        has_terraform_dir: bool = False,
        has_lock_file: bool = False,
        state_files: Optional[List[str]] = None,
    ) -> "TerraformDirectory":
        return cls(
            path=path,
            label=label or path,
            raw_files=dict(files),
            gitignore_rules=list(gitignore_rules or []),
            has_terraform_dir=has_terraform_dir,
            has_lock_file=has_lock_file,
            state_files=list(state_files or []),
        )

    # -- convenience --------------------------------------------------------

    def blocks(self, block_type: str) -> List[HclBlock]:
        """Every block of a type across the whole directory, filename attached."""
        found: List[HclBlock] = []
        for name in sorted(self.files):
            for block in extract_blocks(self.files[name], block_type):
                block.filename = name
                found.append(block)
        return found

    def resources(self, *types: str) -> List[HclBlock]:
        """Resource blocks, optionally filtered to specific resource types."""
        blocks = [b for b in self.blocks("resource") if len(b.labels) >= 2]
        if not types:
            return blocks
        wanted = set(types)
        return [b for b in blocks if b.labels[0] in wanted]

    @property
    def raw_text(self) -> str:
        """Every file including comments. Only the suppression marker uses this."""
        return "\n".join(self.raw_files[name] for name in sorted(self.raw_files))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TerraformDirectory {self.path} files={len(self.files)}>"


def is_root_module(directory: TerraformDirectory) -> bool:
    """True if this directory is a root module — one you would run apply in.

    The test is whether it declares a `provider` block. That is the actual
    distinction Terraform makes: a child module inherits its provider from
    whoever called it and must not declare one (see
    modules/network/versions.tf for the long version of that argument).

    This matters because several checks are meaningless for child modules.
    Asking a child module why it has no backend is asking the wrong
    directory — the caller owns the backend, and a module reused by three
    root modules cannot have one of its own.
    """
    return bool(directory.blocks("provider"))


def has_default_tags(directory: TerraformDirectory) -> bool:
    """True if any `provider "aws"` block in this directory sets default_tags.

    default_tags applies to every taggable resource the provider creates, so a
    directory with it configured cannot have an untagged resource in it. IAC-014
    checks for this first and stays quiet — flagging resources that are already
    tagged, because the tags are declared one file over, is exactly the kind of
    false positive that gets a tool removed from CI.
    """
    for provider in directory.blocks("provider"):
        if provider.labels and provider.labels[0] != "aws":
            continue
        if extract_blocks(provider.body, "default_tags"):
            return True
    return False


def _relative_label(path: str, scan_root: Optional[str]) -> str:
    """A short, stable name for a directory: its path relative to the scan root.

    The scan root itself is labelled by its own basename rather than ".", so
    that `--path .` from inside envs/dev produces findings addressed to
    `dev::...` instead of `.::...`.
    """
    if not scan_root:
        return os.path.basename(os.path.abspath(path))
    relative = os.path.relpath(os.path.abspath(path), os.path.abspath(scan_root))
    if relative == ".":
        return os.path.basename(os.path.abspath(scan_root))
    return relative


def load_directory(
    path: str, scan_root: Optional[str] = None, label: str = ""
) -> TerraformDirectory:
    """Read one directory of .tf files from disk into a TerraformDirectory."""
    scan_root = scan_root or path
    raw_files: Dict[str, str] = {}
    state_files: List[str] = []
    has_terraform_dir = False
    has_lock_file = False

    try:
        entries = sorted(os.listdir(path))
    except OSError:
        entries = []

    for entry in entries:
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            if entry == ".terraform":
                has_terraform_dir = True
            continue
        if entry == LOCK_FILE:
            has_lock_file = True
            continue
        if any(fnmatch.fnmatch(entry, pattern) for pattern in STATE_FILE_PATTERNS):
            # .terraform.tfstate.lock.info is a transient lock artefact, not a
            # committed state file. Flagging it would fire on every machine
            # that happened to be mid-apply.
            if entry != ".terraform.tfstate.lock.info":
                state_files.append(entry)
            continue
        if not entry.endswith(TF_EXTENSIONS):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                raw_files[entry] = handle.read()
        except OSError:
            continue

    return TerraformDirectory(
        path=path,
        label=label or _relative_label(path, scan_root),
        raw_files=raw_files,
        gitignore_rules=find_gitignore_rules(path, scan_root),
        has_terraform_dir=has_terraform_dir,
        has_lock_file=has_lock_file,
        state_files=state_files,
    )


def discover_directories(root: str) -> List[TerraformDirectory]:
    """Every directory under `root` that contains at least one .tf file."""
    found: List[TerraformDirectory] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRECTORIES)
        if not any(f.endswith(TF_EXTENSIONS) for f in filenames):
            continue
        found.append(load_directory(current, scan_root=root))
    return sorted(found, key=lambda d: d.path)


###############################################################################
# Constants the checks are built on
###############################################################################

# Tags every resource in this bootcamp is expected to carry, set once via
# default_tags in each root module's provider block. The list is short on
# purpose: a required-tag policy with eleven entries is a policy nobody
# complies with, and partial compliance is indistinguishable from none.
REQUIRED_TAGS: List[str] = ["Project", "Day", "ManagedBy", "Owner"]

# Resources that hold data you cannot rebuild by re-running Terraform. These
# are the ones that deserve `lifecycle { prevent_destroy = true }`.
#
# prevent_destroy takes a LITERAL. `prevent_destroy = var.protect_me` is a
# hard error, not a warning, because lifecycle is evaluated before variables
# resolve — Terraform needs to know whether destroy is permitted before it has
# a value for anything. There is no toggle. That surprises people who try to
# make it configurable per environment, and the answer is that you cannot.
STATEFUL_RESOURCE_TYPES: Set[str] = {
    "aws_s3_bucket",
    "aws_db_instance",
    "aws_rds_cluster",
    "aws_dynamodb_table",
}

# Resource types that take no tags at all. Flagging these for missing tags is
# noise; several of them are sub-resources of something already tagged.
NON_TAGGABLE_RESOURCE_TYPES: Set[str] = {
    "aws_s3_bucket_versioning",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_policy",
    "aws_s3_bucket_ownership_controls",
    "aws_s3_bucket_lifecycle_configuration",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_route_table_association",
    "aws_route",
    "aws_iam_role_policy",
    "aws_iam_role_policy_attachment",
    "aws_iam_policy_attachment",
    "aws_vpc_security_group_ingress_rule",
    "aws_vpc_security_group_egress_rule",
    "aws_flow_log",
    "random_string",
    "random_id",
    "random_password",
    "null_resource",
    "time_sleep",
}

# Attribute names that hold a secret regardless of what the value looks like.
# Deliberately conservative: matching "key" alone would flag every
# `key = "day-05/dev/terraform.tfstate"` in a backend block.
SECRET_ATTRIBUTE_PATTERNS: List[str] = [
    "PASSWORD",
    "PASSWD",
    "SECRET_KEY",
    "SECRET_ACCESS_KEY",
    "ACCESS_KEY",
    "API_KEY",
    "APIKEY",
    "PRIVATE_KEY",
    "PASSPHRASE",
    "AUTH_TOKEN",
    "CREDENTIAL",
]

# Names that LOOK like the patterns above but are routine and safe. Checked
# first, so `secret_arn` and `kms_key_arn` do not become CRITICAL findings.
SECRET_ATTRIBUTE_ALLOWLIST: List[str] = [
    "SECRET_ARN",
    "SECRET_ID",
    "SECRET_NAME",
    "KMS_KEY_ARN",
    "KMS_KEY_ID",
    "PUBLIC_KEY",
    "KEY_NAME",
    "SSH_KEY_NAME",
    "PASSWORD_LENGTH",
    "PASSWORD_POLICY",
    "ACCESS_KEY_ROTATION",
]

# Names that mark a whole block as holding a secret — used together with a
# literal `value` / `secret_string` to catch `aws_ssm_parameter "db_password"`,
# where the attribute is innocuously called `value`.
SECRET_NAME_HINTS = re.compile(
    r"(^|[-_])(password|passwd|secret|token|credential|apikey|api_key|passphrase)([-_]|$)",
    re.I,
)

# The attributes those blocks put the secret in.
SECRET_VALUE_ATTRIBUTES: Set[str] = {
    "value",
    "secret_string",
    "plaintext",
    "password",
    "master_password",
}

# Value shapes that are a credential no matter what the attribute is called.
# An AKIA-prefixed string in a .tf file has no plausible innocent explanation.
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
# empty string, or a reference to Secrets Manager, is the correct pattern
# rather than a violation of it.
_NON_SECRET_VALUE = re.compile(
    r"^\s*$|^arn:aws[a-z\-]*:(secretsmanager|ssm|kms):|^REPLACE|^CHANGE|^<.*>$",
    re.I,
)

# Output names that suggest the value is a credential.
SECRET_OUTPUT_HINTS = re.compile(
    r"(^|_)(password|passwd|secret|token|credential|private_key|access_key|api_key|passphrase)($|_)",
    re.I,
)

# Bucket names that look like they hold Terraform state, used to build the
# candidate set for the live checks when --state-bucket is not supplied.
STATE_BUCKET_PATTERNS: List[str] = ["*-tfstate-*", "*tfstate*", "*-state-*"]

# The everything-CIDRs.
OPEN_CIDRS: Set[str] = {"0.0.0.0/0", "::/0"}


###############################################################################
# Check implementations
#
# Every check is a pure function: a TerraformDirectory or a plain dict in, a
# list[Finding] out. No AWS calls, no printing, no filesystem access. That
# means they can be unit-tested against synthetic fixtures with no credentials
# and no account, which is exactly what tests/test_checks.py does — 47 tests
# that run in under a second on a laptop on a train.
#
# It also means each check can be read on its own. When someone disputes a
# finding in a review, you open one function and settle it.
###############################################################################


def _attribute_is_secret_name(name: str) -> Optional[str]:
    """Return the matched pattern if the attribute NAME implies a secret."""
    upper = name.upper()
    for safe in SECRET_ATTRIBUTE_ALLOWLIST:
        if safe in upper:
            return None
    for pattern in SECRET_ATTRIBUTE_PATTERNS:
        if pattern in upper:
            return pattern
    return None


def _value_is_credential(value: str) -> Optional[str]:
    """Return a description if the VALUE is unmistakably a credential."""
    literal = unquote_hcl(value)
    for pattern, description in SECRET_VALUE_PATTERNS:
        if pattern.match(literal.strip()):
            return description
    return None


def _looks_like_real_secret(value: str) -> bool:
    """A literal string that is not a placeholder, a reference or empty."""
    if not is_literal_string(value):
        return False
    literal = unquote_hcl(value)
    if _NON_SECRET_VALUE.match(literal):
        return False
    return len(literal.strip()) >= 6


def check_hardcoded_secrets(directory: TerraformDirectory) -> List[Finding]:
    """IAC-001 — a secret written into a .tf file as a literal string.

    This lands in THREE places at once, and people usually only think about
    the first:

      1. Git history, forever, in every clone and every fork. Deleting the
         commit does not remove it; the object still exists everywhere the
         repository has ever been.
      2. The Terraform STATE FILE, in plaintext JSON, because state records
         every attribute of every resource.
      3. Any CI log that echoed the plan output, retained for a year and
         usually readable by the whole organisation.

    Marking the value `sensitive = true` addresses none of those. It hides the
    value from CLI output and nothing else. This is the single most
    misunderstood feature in Terraform.

    Three ways to trip this check, all reported as one finding per block so
    that a resource with a secret name AND a secret value does not count
    twice:

      * an attribute whose NAME implies a secret, holding a literal
      * an attribute whose VALUE matches a known credential shape
      * a block whose NAME implies a secret, with a literal in `value`,
        `secret_string`, `plaintext`, `password` or `master_password` —
        which is how `aws_ssm_parameter "db_password"` is caught, since the
        attribute there is innocuously called `value`

    `provider` blocks are excluded deliberately. Credentials there are a
    different and more serious mistake with its own check, IAC-002, and
    reporting both would double-count one line of configuration.
    """
    findings: List[Finding] = []

    for kind in ("resource", "locals", "output", "variable", "module"):
        for block in directory.blocks(kind):
            attrs = block_attributes(block.body)
            reasons: List[str] = []
            evidence: Dict[str, Any] = {}

            block_is_secret_named = bool(SECRET_NAME_HINTS.search(block.name))

            for attr_name, raw_value in attrs.items():
                if not _looks_like_real_secret(raw_value):
                    continue
                literal = unquote_hcl(raw_value)

                credential = _value_is_credential(raw_value)
                if credential:
                    reasons.append(f"{attr_name} holds a {credential}")
                    evidence[attr_name] = _redact(literal)
                    continue

                if _attribute_is_secret_name(attr_name):
                    reasons.append(f"{attr_name} is a secret-shaped attribute name")
                    evidence[attr_name] = _redact(literal)
                    continue

                if block_is_secret_named and attr_name in SECRET_VALUE_ATTRIBUTES:
                    reasons.append(
                        f"{attr_name} on a block named {block.name!r} holds a literal"
                    )
                    evidence[attr_name] = _redact(literal)

            if not reasons:
                continue

            findings.append(
                Finding(
                    check_id="IAC-001",
                    severity="CRITICAL",
                    resource_type="Terraform::Configuration",
                    resource_id=f"{directory.label}::{block.address}",
                    title="Hardcoded secret in Terraform configuration",
                    detail=(
                        f"{block.address} in {block.filename} contains a plaintext "
                        f"credential: {'; '.join(sorted(set(reasons)))}. The value is "
                        f"now in git history, will be written to the state file in "
                        f"plaintext JSON on the next apply, and appears in any CI log "
                        f"that captured the plan."
                    ),
                    remediation=(
                        "Remove the literal and rotate the credential — it is "
                        "compromised the moment it is committed, and editing the file "
                        "does not un-commit it. Then pick one: create the secret "
                        "outside Terraform and read it with a data source "
                        "(aws_secretsmanager_secret_version); better, have the "
                        "application fetch it at runtime with its own instance role so "
                        "it never touches Terraform at all. If Terraform must generate "
                        "it, use random_password and treat state as the secrets store "
                        "it has now become."
                    ),
                    evidence={"file": block.filename, "attributes": evidence},
                )
            )
    return findings


def _redact(value: str) -> str:
    """Show enough of a secret to identify it and not enough to use it."""
    value = value.strip()
    if len(value) <= 8:
        return value[:2] + "*" * max(0, len(value) - 2)
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars)"


def check_provider_credentials(directory: TerraformDirectory) -> List[Finding]:
    """IAC-002 — access_key / secret_key written into a provider block.

    The worst version of IAC-001, and common enough that it gets its own check
    and its own remediation text.

    What actually happens: the key lands in git history, and deleting the
    commit does not remove it, because the object still exists in every clone
    and every fork. GitHub's secret scanning finds public ones in seconds; so
    do the bots that mine public repositories, and the median time from push
    to a crypto-mining instance running in your account is measured in
    MINUTES, not hours. The only remediation is rotation, immediately.

    The correct answers, in order of preference:
      1. An OIDC role in CI. No long-lived key exists to leak.
      2. An IAM role on the instance or task.
      3. `profile = "bootcamp"` and a named profile in ~/.aws/credentials.
      4. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables.
    There is no fifth answer.

    One finding per provider block, not per key, because `access_key` and
    `secret_key` are always the same mistake made once.
    """
    findings: List[Finding] = []

    for provider in directory.blocks("provider"):
        attrs = block_attributes(provider.body)
        offending = {}
        for attr_name in ("access_key", "secret_key", "token", "session_token"):
            value = attrs.get(attr_name)
            if value and is_literal_string(value) and unquote_hcl(value).strip():
                offending[attr_name] = _redact(unquote_hcl(value))

        if not offending:
            continue

        provider_name = provider.labels[0] if provider.labels else "aws"
        findings.append(
            Finding(
                check_id="IAC-002",
                severity="CRITICAL",
                resource_type="Terraform::Provider",
                resource_id=f"{directory.label}::provider.{provider_name}",
                title="Static credentials hardcoded in a provider block",
                detail=(
                    f"provider \"{provider_name}\" in {provider.filename} sets "
                    f"{', '.join(sorted(offending))} to literal values. Anyone with "
                    f"read access to this repository — including anyone who has ever "
                    f"cloned or forked it — has these credentials, and they are valid "
                    f"until somebody rotates them."
                ),
                remediation=(
                    "Delete the attributes, then ROTATE the keys before doing anything "
                    "else; the file edit does not invalidate them. Replace with an "
                    "OIDC role in CI (no long-lived key to leak), an instance or task "
                    "role on AWS, or `profile = \"bootcamp\"` locally. Then run "
                    "git-secrets or trufflehog over the history, because this is "
                    "rarely the only one."
                ),
                evidence={"file": provider.filename, "attributes": offending},
            )
        )
    return findings


def check_committed_state(directory: TerraformDirectory) -> List[Finding]:
    """IAC-003 — a .tfstate file committed alongside the configuration.

    SILENT BY DESIGN in this repository. There is no committed state file
    anywhere in the lab and every .gitignore in the tree covers the pattern,
    so this check runs on all fourteen directories and reports nothing. That
    is a result, not a gap — see the module docstring.

    Why it would be CRITICAL if it fired: state is not a cache. It is a
    plaintext JSON record of every attribute of every resource Terraform
    manages, including the ones marked sensitive, including RDS master
    passwords, including any secret that has ever passed through a resource
    argument. Committing it publishes all of that to everyone with repository
    access, permanently, in a file people stop noticing after the first week.

    It also breaks Terraform. Two people committing state produce merge
    conflicts in a file that must not be hand-merged, and resolving one
    incorrectly makes Terraform believe resources exist that do not, or the
    reverse.

    The check reports files present on disk, and separately notes whether
    .gitignore would have caught them — because a state file that is
    gitignored is a local artefact, and one that is not is in the repository.
    """
    findings: List[Finding] = []

    for filename in sorted(directory.state_files):
        gitignored = gitignore_ignores(directory.gitignore_rules, filename)
        if gitignored:
            continue
        findings.append(
            Finding(
                check_id="IAC-003",
                severity="CRITICAL",
                resource_type="Terraform::State",
                resource_id=f"{directory.label}/{filename}",
                title="Terraform state file is not gitignored",
                detail=(
                    f"{filename} exists in {directory.label} and no .gitignore rule "
                    f"covers it, so it is committed or about to be. State is "
                    f"plaintext JSON containing every attribute of every managed "
                    f"resource — including values marked sensitive, which are hidden "
                    f"from the CLI and not from the file."
                ),
                remediation=(
                    "Add `*.tfstate` and `*.tfstate.*` to .gitignore, then "
                    "`git rm --cached` the file. If it was ever pushed, treat every "
                    "secret in it as compromised and rotate them — purging git "
                    "history does not reach existing clones. Then move to a remote "
                    "backend so the question stops arising: see envs/dev/backend.tf."
                ),
                evidence={"gitignore_rules": directory.gitignore_rules[-8:]},
            )
        )
    return findings


def check_state_bucket_public(bucket: Dict[str, Any], region: str = "") -> List[Finding]:
    """IAC-004 — the Terraform state bucket is publicly accessible.

    SILENT BY DESIGN in this repository, and this is the more interesting of
    the two silent checks.

    This lab does not ship a publicly readable S3 bucket, not even as a
    teaching example. A repository that does is one `terraform apply` away
    from being a repository that leaked somebody's data — the learner runs it
    in their own account, forgets the toggle, and now there is a world
    readable bucket with their state file in it. The demonstration is not
    worth that. The deliberately insecure example bucket in envs/dev
    therefore has a REAL public access block, and exists to fire IAC-006 and
    IAC-007 only.

    The check is fully implemented and fully tested against synthetic input,
    because "we did not write it" and "it finds nothing" must be
    distinguishable. Point it at a bucket that is actually open and it fires.

    Expects the collector's shape:
        {"Name": str,
         "PublicAccessBlock": {"BlockPublicAcls": bool, ...} | None,
         "PolicyStatus": {"IsPublic": bool} | None,
         "Grants": [ {"Grantee": {"URI": ...}, "Permission": ...} ]}
    """
    findings: List[Finding] = []
    name = bucket.get("Name", "unknown")

    pab = bucket.get("PublicAccessBlock") or {}
    required = (
        "BlockPublicAcls",
        "BlockPublicPolicy",
        "IgnorePublicAcls",
        "RestrictPublicBuckets",
    )
    missing = [key for key in required if not pab.get(key)]

    policy_public = bool((bucket.get("PolicyStatus") or {}).get("IsPublic"))

    public_grants = []
    for grant in bucket.get("Grants") or []:
        uri = (grant.get("Grantee") or {}).get("URI") or ""
        if "AllUsers" in uri or "AuthenticatedUsers" in uri:
            public_grants.append(
                {"uri": uri, "permission": grant.get("Permission", "")}
            )

    if not missing and not policy_public and not public_grants:
        return findings

    reasons = []
    if missing:
        reasons.append(f"public access block incomplete ({', '.join(missing)})")
    if policy_public:
        reasons.append("bucket policy evaluates as public")
    if public_grants:
        reasons.append(f"{len(public_grants)} public ACL grant(s)")

    findings.append(
        Finding(
            check_id="IAC-004",
            severity="CRITICAL",
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            title="Terraform state bucket is publicly accessible",
            detail=(
                f"{name} holds Terraform state and is reachable publicly: "
                f"{'; '.join(reasons)}. State is plaintext JSON describing every "
                f"resource in the environment — subnet IDs, security group rules, "
                f"instance IDs, and any secret Terraform has ever touched. Published, "
                f"it is a map of the account and a credential dump in one file."
            ),
            remediation=(
                "Turn on all four public access block settings immediately: "
                f"`aws s3api put-public-access-block --bucket {name} "
                "--public-access-block-configuration "
                "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,"
                "RestrictPublicBuckets=true`. Then remove any public statement from "
                "the bucket policy, enable the account-level block so this cannot "
                "recur, and treat every secret in the state file as compromised."
            ),
            evidence={
                "public_access_block": pab,
                "missing_settings": missing,
                "policy_is_public": policy_public,
                "public_grants": public_grants,
            },
            region=region,
        )
    )
    return findings


def check_local_state(directory: TerraformDirectory) -> List[Finding]:
    """IAC-005 — a root module with no backend block, so state lives on disk.

    One laptop holds the only map between your code and your account.

    What that costs, in the order people usually discover it: your colleague
    applies, sees no state, and creates a duplicate stack. Then you both apply
    at once and interleave writes to a file with no locking. Then the laptop
    dies, and recovery is `terraform import`, one resource at a time, by hand,
    while the thing is down.

    SCOPING — two deliberate exemptions:

      * Child modules are skipped entirely. A module has no backend of its
        own; the root module that calls it owns state for everything it
        creates. Flagging modules/network for having no backend is asking the
        wrong directory.

      * A directory containing the inline marker `# iac-audit: allow-local-state`
        in any .tf file is exempt. That is not a special case bolted on for
        backend-bootstrap — it is the suppression mechanism every audit tool
        needs, and the reason it is implemented as a code marker rather than a
        config file or a CLI flag is that a suppression belongs NEXT TO the
        thing being suppressed, where the reviewer of the next change to that
        file will see it. A suppressions.yaml in the repository root is a file
        nobody reads that quietly grows.

        backend-bootstrap/ carries the marker because the backend cannot
        create itself: the bucket that holds state has to exist before any
        configuration can write state to it. That is the one legitimate use of
        local state in this repository, and it is annotated as such.

        The marker must be a comment line of its own. bad-examples/providers.tf
        discusses the marker at length in prose, and a substring search would
        let a directory suppress a finding by writing about suppression — which
        is both a bug and a fairly good joke at the tool's expense.
    """
    findings: List[Finding] = []

    if not is_root_module(directory):
        return findings

    if has_local_state_suppression(directory):
        return findings

    for terraform_block in directory.blocks("terraform"):
        if extract_blocks(terraform_block.body, "backend"):
            return findings
        if extract_blocks(terraform_block.body, "cloud"):
            return findings

    findings.append(
        Finding(
            check_id="IAC-005",
            severity="HIGH",
            resource_type="Terraform::Backend",
            resource_id=f"{directory.label}::backend",
            title="Root module has no backend — state is written to local disk",
            detail=(
                f"{directory.label} declares a provider (so it is a root module you "
                f"would run apply in) but no `backend` block, so Terraform writes "
                f"terraform.tfstate next to the configuration. There is no locking, "
                f"no versioning, no shared source of truth, and no copy of it "
                f"anywhere but one machine."
            ),
            remediation=(
                "Add an S3 backend with native locking — see envs/dev/backend.tf for "
                "the annotated version:\n"
                "  terraform { backend \"s3\" { bucket = \"...\" "
                "key = \"day-05/dev/terraform.tfstate\" region = \"us-east-1\" "
                "encrypt = true use_lockfile = true } }\n"
                "Then `terraform init -migrate-state` to move the existing file up. "
                "If this directory legitimately needs local state — a bootstrap that "
                "creates the backend itself — add the comment "
                f"`# {ALLOW_LOCAL_STATE_MARKER}` next to the terraform block, with a "
                "sentence saying why."
            ),
            evidence={
                "files": sorted(directory.files),
                "is_root_module": True,
            },
        )
    )
    return findings


def check_state_bucket_versioning(
    bucket: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """IAC-006 — the state bucket has versioning disabled.

    Versioning on a state bucket is not a nice-to-have, it is the rollback
    path. State can be corrupted by an interrupted apply, a partial write, a
    bad `state rm`, or a merge somebody resolved by hand. With versioning on,
    recovery is copying the previous version back over the current one and
    re-running plan. With it off, recovery is `terraform import`, resource by
    resource, from a screenshot of the console.

    This is one of the two checks the deliberately insecure example bucket in
    envs/dev exists to fire, and it needs credentials — you cannot tell
    whether versioning is on by reading the .tf file that was supposed to
    enable it.

    Expects: {"Name": str, "Versioning": {"Status": "Enabled"|"Suspended"|None}}
    """
    findings: List[Finding] = []
    name = bucket.get("Name", "unknown")
    status = (bucket.get("Versioning") or {}).get("Status")

    if status == "Enabled":
        return findings

    findings.append(
        Finding(
            check_id="IAC-006",
            severity="HIGH",
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            title="State bucket has no versioning",
            detail=(
                f"{name} looks like a Terraform state bucket and its versioning "
                f"status is {status or 'never enabled'}. A corrupt or truncated "
                f"state write has no rollback path: the previous good copy does not "
                f"exist. That turns a five-minute recovery into a manual import of "
                f"every resource in the environment."
            ),
            remediation=(
                f"`aws s3api put-bucket-versioning --bucket {name} "
                "--versioning-configuration Status=Enabled`, or in Terraform an "
                "aws_s3_bucket_versioning resource with status = \"Enabled\" — see "
                "backend-bootstrap/main.tf. Then add a lifecycle rule expiring "
                "noncurrent versions after 90 days, or the bucket keeps every "
                "version of every state file forever and quietly grows."
            ),
            evidence={"versioning": bucket.get("Versioning")},
            region=region,
        )
    )
    return findings


def check_state_bucket_encryption(
    bucket: Dict[str, Any], region: str = ""
) -> List[Finding]:
    """IAC-007 — the state bucket has no default encryption configured.

    State files hold every secret Terraform has ever touched, in plaintext
    JSON. `sensitive = true` does not change that, because sensitive is a
    display setting. If an RDS module generated a master password, it is in
    the state file in full.

    S3 has applied SSE-S3 by default to new buckets since January 2023, so
    this check reports on the CONFIGURATION rather than trusting the default:
    a bucket with no explicit encryption configuration is a bucket where
    nobody decided, and "nobody decided" is not the same as "encrypted with a
    key we control and can revoke". SSE-KMS with a customer-managed key is the
    meaningful upgrade — it gives you a revocable, auditable key, and
    CloudTrail records every Decrypt call against it.

    Expects: {"Name": str, "Encryption": {"Rules": [...]} | None}
    """
    findings: List[Finding] = []
    name = bucket.get("Name", "unknown")
    encryption = bucket.get("Encryption") or {}
    rules = encryption.get("Rules") or encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules") or []

    algorithms = [
        (rule.get("ApplyServerSideEncryptionByDefault") or {}).get("SSEAlgorithm")
        for rule in rules
    ]
    algorithms = [algorithm for algorithm in algorithms if algorithm]

    if algorithms:
        return findings

    findings.append(
        Finding(
            check_id="IAC-007",
            severity="HIGH",
            resource_type="AWS::S3::Bucket",
            resource_id=name,
            title="State bucket has no default encryption configured",
            detail=(
                f"{name} looks like a Terraform state bucket and has no default "
                f"server-side encryption configuration. Every secret Terraform has "
                f"ever managed is in those objects in plaintext JSON — database "
                f"passwords, generated keys, anything a module produced. Whether S3 "
                f"applied its own default is not the same as whether anyone chose a "
                f"key, and only one of those is auditable."
            ),
            remediation=(
                f"`aws s3api put-bucket-encryption --bucket {name} "
                "--server-side-encryption-configuration "
                "'{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":"
                "{\"SSEAlgorithm\":\"aws:kms\"},\"BucketKeyEnabled\":true}]}'`, or an "
                "aws_s3_bucket_server_side_encryption_configuration resource. Prefer "
                "SSE-KMS with a customer-managed key: it is revocable, and every "
                "Decrypt shows up in CloudTrail so you can see who read state. Turn "
                "on the S3 Bucket Key while you are there or KMS request charges add "
                "up on a busy state file."
            ),
            evidence={"encryption": bucket.get("Encryption")},
            region=region,
        )
    )
    return findings


def check_sensitive_outputs(directory: TerraformDirectory) -> List[Finding]:
    """IAC-008 — an output that exposes a credential without `sensitive = true`.

    `terraform output` prints it. `terraform apply` prints it at the end of
    every run. CI captures that stdout into a build log that is usually
    readable by everyone in the organisation and retained for a year.

    Adding `sensitive = true` fixes the PRINTING, and only the printing. It
    does not encrypt the value, does not remove it from state, and does not
    stop `terraform output -raw database_password` from returning it happily
    to anyone who can run Terraform in that directory.

    So the finding says to add `sensitive`, and then says the real answer:
    ask why a password is an output at all. Outputs exist to WIRE THINGS UP —
    IDs, ARNs, endpoints, names. A consumer that needs the secret should read
    it from Secrets Manager with its own IAM identity, so that access is
    granted, revocable and logged. Passing it through an output makes it none
    of those.

    Matched on the output NAME and on the VALUE expression, so that
    `output "db_creds" { value = aws_db_instance.main.password }` is caught
    even though the name does not say password.
    """
    findings: List[Finding] = []

    for output in directory.blocks("output"):
        attrs = block_attributes(output.body)
        if attrs.get("sensitive", "").strip().lower() == "true":
            continue

        name = output.name
        value = attrs.get("value", "")

        name_hit = bool(SECRET_OUTPUT_HINTS.search(name))
        value_hit = bool(SECRET_OUTPUT_HINTS.search(value)) and not is_literal_string(value)

        if not (name_hit or value_hit):
            continue

        findings.append(
            Finding(
                check_id="IAC-008",
                severity="HIGH",
                resource_type="Terraform::Output",
                resource_id=f"{directory.label}::output.{name}",
                title="Output exposes a credential without sensitive = true",
                detail=(
                    f"output \"{name}\" in {output.filename} looks like it carries a "
                    f"credential (value: {_truncate(value, 60)}) and is not marked "
                    f"sensitive. It is printed at the end of every apply and captured "
                    f"by whatever CI runs it."
                ),
                remediation=(
                    "Add `sensitive = true` as the minimum, and understand what that "
                    "buys: it stops the printing and nothing else — the value is still "
                    "in state in plaintext and still returned by "
                    f"`terraform output -raw {name}`. The real fix is to delete the "
                    "output and have the consumer read the secret from Secrets Manager "
                    "or Parameter Store with its own IAM identity, so access is "
                    "granted, revocable and logged."
                ),
                evidence={
                    "file": output.filename,
                    "value": _truncate(value, 120),
                    "matched_on": "name" if name_hit else "value expression",
                },
            )
        )
    return findings


def check_open_ingress(directory: TerraformDirectory) -> List[Finding]:
    """IAC-009 — a security group rule allowing INGRESS from 0.0.0.0/0.

    Port 22 open to the world is scanned within minutes of existing. Not
    hours: the internet-wide scanners run continuously, and a fresh public
    IPv4 gets its first credential-stuffing attempt almost immediately.

    SCOPING — INGRESS ONLY, and this is a considered decision rather than an
    oversight. Egress to 0.0.0.0/0 is normal. It is the default AWS creates
    for you, it is present in modules/network in this very repository, and
    almost every workload needs to reach the internet for package updates and
    API calls. A check that flagged every default egress rule would fire on
    essentially every security group in the account, and a check that fires
    everywhere is a check people stop reading. A check nobody reads is worse
    than no check, because it occupies the space where a real one would go.

    Locking down egress IS worth doing — it is what turns a compromised
    instance into a compromised instance that cannot phone home. But it is a
    deliberate architecture exercise with VPC endpoints and a proxy, not a
    linter finding, and pretending otherwise trains people to ignore output.

    Handles both forms:
      * inline `ingress { cidr_blocks = [...] }` inside aws_security_group
      * standalone aws_vpc_security_group_ingress_rule with cidr_ipv4

    One finding per resource, listing every offending rule, so a group with
    four wide-open ports is one thing to fix rather than four.
    """
    findings: List[Finding] = []

    def _cidrs_in(value: str) -> List[str]:
        return [c for c in re.findall(r"\"([^\"]+)\"", value) if c in OPEN_CIDRS]

    for resource in directory.resources("aws_security_group"):
        offending: List[Dict[str, Any]] = []
        for rule in extract_blocks(resource.body, "ingress"):
            attrs = block_attributes(rule.body)
            open_cidrs: List[str] = []
            for attr_name in ("cidr_blocks", "ipv6_cidr_blocks"):
                open_cidrs.extend(_cidrs_in(attrs.get(attr_name, "")))
            if open_cidrs:
                offending.append(
                    {
                        "from_port": attrs.get("from_port", "?"),
                        "to_port": attrs.get("to_port", "?"),
                        "protocol": unquote_hcl(attrs.get("protocol", "?")),
                        "cidrs": open_cidrs,
                    }
                )
        if offending:
            findings.append(_open_ingress_finding(directory, resource, offending))

    for resource in directory.resources("aws_vpc_security_group_ingress_rule"):
        attrs = block_attributes(resource.body)
        open_cidrs = [
            unquote_hcl(attrs[key])
            for key in ("cidr_ipv4", "cidr_ipv6")
            if key in attrs and unquote_hcl(attrs[key]) in OPEN_CIDRS
        ]
        if open_cidrs:
            findings.append(
                _open_ingress_finding(
                    directory,
                    resource,
                    [
                        {
                            "from_port": attrs.get("from_port", "?"),
                            "to_port": attrs.get("to_port", "?"),
                            "protocol": unquote_hcl(attrs.get("ip_protocol", "?")),
                            "cidrs": open_cidrs,
                        }
                    ],
                )
            )

    return findings


def _open_ingress_finding(
    directory: TerraformDirectory, resource: HclBlock, rules: List[Dict[str, Any]]
) -> Finding:
    ports = ", ".join(f"{r['from_port']}-{r['to_port']}/{r['protocol']}" for r in rules)
    return Finding(
        check_id="IAC-009",
        severity="HIGH",
        resource_type="AWS::EC2::SecurityGroup",
        resource_id=f"{directory.label}::{resource.address}",
        title="Security group allows ingress from 0.0.0.0/0",
        detail=(
            f"{resource.address} in {resource.filename} accepts inbound traffic from "
            f"the entire internet on {ports}. If 22 or 3389 is in that list, it will "
            f"be found by an automated scanner within minutes of the address becoming "
            f"reachable — the scanners run continuously and do not need to know you "
            f"exist."
        ),
        remediation=(
            "The fix is not 'narrow it to the office IP range'. For administrative "
            "access, remove the rule entirely and use SSM Session Manager, which "
            "needs no inbound rule, no bastion and no key — see modules/compute, "
            "which opens nothing. For genuinely public services, put an ALB or "
            "CloudFront in front and let the instance security group accept traffic "
            "only from the load balancer's security group."
        ),
        evidence={"file": resource.filename, "rules": rules},
    )


def check_required_version(directory: TerraformDirectory) -> List[Finding]:
    """IAC-010 — no `required_version` constraint in any terraform block.

    Without a floor, somebody runs this with whatever binary is on their PATH.

    In one direction that is a confusing afternoon: a 1.4 binary hits
    `optional()` in an object type and reports a syntax error, and you debug a
    version mismatch as though it were a bug in your code.

    In the other direction it is much worse. A newer binary WRITES A NEWER
    STATE FORMAT, and everyone still on the old version is locked out of the
    state file until they upgrade too. State format upgrades are one-way.
    There is no downgrade path, and the person who ran it usually did not
    notice they had a different version.

    Evaluated per DIRECTORY, not per block, because a directory can carry
    several terraform blocks — envs/dev has one in providers.tf holding
    required_version and another in backend.tf holding the backend. Between
    them the directory is correctly configured, and a per-block check would
    report a false positive on the second one.
    """
    findings: List[Finding] = []

    terraform_blocks = directory.blocks("terraform")
    if not terraform_blocks:
        return findings

    for block in terraform_blocks:
        if "required_version" in block_attributes(block.body):
            return findings

    findings.append(
        Finding(
            check_id="IAC-010",
            severity="MEDIUM",
            resource_type="Terraform::Settings",
            resource_id=f"{directory.label}::terraform.required_version",
            title="No required_version constraint",
            detail=(
                f"{directory.label} has {len(terraform_blocks)} terraform block(s) and "
                f"none of them sets required_version. Any binary on anyone's PATH will "
                f"run this, and a newer one will silently upgrade the state file "
                f"format, locking out everybody still on the old version."
            ),
            remediation=(
                "Add a floor and a ceiling to the terraform block:\n"
                "  terraform { required_version = \">= 1.10.0\" }\n"
                "The rest of this repository uses >= 1.10.0 because that is when S3 "
                "native state locking (use_lockfile) arrived. Pin the same version in "
                "CI and in the .tool-versions or .terraform-version file so the "
                "pipeline and the laptop agree."
            ),
            evidence={"terraform_blocks": len(terraform_blocks)},
        )
    )
    return findings


def check_provider_pinning(directory: TerraformDirectory) -> List[Finding]:
    """IAC-011 — a provider in required_providers with no version constraint.

    This resolves to whatever the newest provider happens to be at the moment
    `terraform init` runs. Two engineers initialising a day apart get
    different providers, and one of them sees a plan with changes nobody
    wrote — usually a new attribute with a new default.

    Major versions are worse. A 6.x AWS provider against configuration written
    for 5.x is an error at best and a silent behavioural change at worst, and
    it arrives on a Tuesday because somebody re-ran init in CI.

    The constraint style that works: `version = "~> 5.80"` allows patches and
    minors within 5.x and refuses 6.0. `>= 5.80` alone allows 6.0 and is
    therefore not a pin. `= 5.80.1` is a pin so tight that security patches
    need a code change, which is a defensible choice for regulated
    environments and an annoying one everywhere else.

    Note that this check and the lock file are different mechanisms answering
    different questions. The constraint says what is ACCEPTABLE; the lock file
    records what was SELECTED. You want both — see IAC-012.
    """
    findings: List[Finding] = []

    unpinned: List[str] = []
    for terraform_block in directory.blocks("terraform"):
        for required in extract_blocks(terraform_block.body, "required_providers"):
            for name, value in block_attributes(required.body).items():
                # Each entry is `name = { source = "...", version = "..." }`.
                if "version" not in block_attributes(object_body(value)):
                    unpinned.append(name)

    if not unpinned:
        return findings

    findings.append(
        Finding(
            check_id="IAC-011",
            severity="MEDIUM",
            resource_type="Terraform::Provider",
            resource_id=f"{directory.label}::required_providers",
            title="Provider version is not constrained",
            detail=(
                f"{directory.label} declares {', '.join(sorted(set(unpinned)))} in "
                f"required_providers with no version constraint. Every `terraform "
                f"init` resolves to whatever is newest at that moment, so two people "
                f"initialising a day apart get different providers and one of them "
                f"gets a plan full of changes nobody wrote."
            ),
            remediation=(
                "Constrain to a major version and allow patches within it:\n"
                "  aws = { source = \"hashicorp/aws\", version = \"~> 5.80\" }\n"
                "`~> 5.80` accepts 5.80.x through 5.x and refuses 6.0, which is the "
                "behaviour you want: security patches arrive, breaking changes do "
                "not. Commit .terraform.lock.hcl alongside it — the constraint says "
                "what is allowed, the lock file records what was chosen, and you need "
                "both."
            ),
            evidence={"unpinned_providers": sorted(set(unpinned))},
        )
    )
    return findings


def check_lock_file(directory: TerraformDirectory) -> List[Finding]:
    """IAC-012 — .terraform.lock.hcl is gitignored, or missing after an init.

    .terraform.lock.hcl records the EXACT provider versions Terraform selected
    and their checksums for every platform. Committing it is what makes
    `terraform init` reproducible: your laptop, your colleague's laptop and CI
    all get the same provider binary, verified against the same hashes.

    Gitignore it and every init re-resolves against the registry. The pipeline
    picks up a provider released three hours ago, the plan shows changes
    nobody wrote, and you spend the morning proving it was not your commit. It
    also throws away the supply-chain check: those hashes are how Terraform
    verifies the provider it downloaded is the provider it downloaded last
    time.

    `terraform init -upgrade` is the only thing that should ever change this
    file, and that change belongs in its own reviewed pull request where a
    human can read "aws 5.80.0 -> 5.82.2" and think about it.

    SCOPING — the two arms are not equally confident, and the check says so:

      * GITIGNORED: fires whenever a .gitignore rule resolved from this
        directory upwards matches the filename. This is a decision somebody
        made, in a file, on purpose, and it is always wrong.

      * MISSING: fires only when a `.terraform/` directory is present. A
        missing lock file in a directory nobody has ever run init in is not a
        finding, it is a directory nobody has run init in — modules/network in
        a fresh clone, for instance. The `.terraform/` directory is the
        evidence that init HAS run, which makes the absence of the lock file
        meaningful rather than merely untested.
    """
    findings: List[Finding] = []

    if gitignore_ignores(directory.gitignore_rules, LOCK_FILE):
        matching = [
            rule
            for rule in directory.gitignore_rules
            if fnmatch.fnmatch(LOCK_FILE, rule.lstrip("!").rstrip("/"))
        ]
        findings.append(
            Finding(
                check_id="IAC-012",
                severity="MEDIUM",
                resource_type="Terraform::LockFile",
                resource_id=f"{directory.label}/{LOCK_FILE}",
                title="Dependency lock file is gitignored",
                detail=(
                    f"A .gitignore rule ({', '.join(matching) or 'unknown'}) excludes "
                    f"{LOCK_FILE} from {directory.label}. Provider selection is now "
                    f"re-resolved on every init, so CI and your laptop can end up on "
                    f"different provider binaries, and the checksum verification that "
                    f"protects against a tampered provider is gone."
                ),
                remediation=(
                    f"Delete the {LOCK_FILE} line from .gitignore, run `terraform "
                    f"init`, and commit the resulting file. Changes to it should only "
                    f"ever come from `terraform init -upgrade`, in their own reviewed "
                    f"pull request. If CI runs on Linux and people develop on macOS, "
                    f"run `terraform providers lock -platform=linux_amd64 "
                    f"-platform=darwin_arm64` so the file carries hashes for both."
                ),
                evidence={"matching_rules": matching},
            )
        )
        return findings

    if directory.has_terraform_dir and not directory.has_lock_file:
        findings.append(
            Finding(
                check_id="IAC-012",
                severity="MEDIUM",
                resource_type="Terraform::LockFile",
                resource_id=f"{directory.label}/{LOCK_FILE}",
                title="Initialised directory has no dependency lock file",
                detail=(
                    f"{directory.label} contains a .terraform/ directory — so "
                    f"`terraform init` has run here — but no {LOCK_FILE}. Either it "
                    f"was deleted or it was never committed. Provider versions are "
                    f"unreproducible either way."
                ),
                remediation=(
                    f"Run `terraform init` and commit {LOCK_FILE}. Check it is not "
                    f"excluded by a .gitignore higher up the tree."
                ),
                evidence={"has_terraform_dir": True, "has_lock_file": False},
            )
        )
    return findings


def check_prevent_destroy(directory: TerraformDirectory) -> List[Finding]:
    """IAC-013 — a stateful resource with no `lifecycle { prevent_destroy }`.

    Nothing stands between `terraform destroy` and the data. Every S3 bucket,
    RDS instance, RDS cluster and DynamoDB table holding something you cannot
    rebuild from code deserves this block. It makes destroy FAIL, loudly, at
    plan time — which is annoying exactly once, in the situation where it
    saves you.

    Two things people get wrong about it:

      * `prevent_destroy = var.protect` is a HARD ERROR, not a warning.
        `lifecycle` is evaluated before variables resolve, so the argument
        must be a literal. There is no per-environment toggle. If you want dev
        to be destroyable and prod not, that is two module calls with
        different code paths, or accepting the seatbelt in both.

      * When destroy legitimately needs to happen, the answer is to remove the
        block, apply, then destroy — deliberately, as three steps. The answer
        is NOT to delete the lifecycle block in the middle of an incident
        because destroy is failing and you want it to stop failing. That is
        how production buckets go missing. See teardown-checklist.md.

    Covers aws_s3_bucket, aws_db_instance, aws_rds_cluster and
    aws_dynamodb_table. Not an exhaustive list of stateful AWS resources — it
    is the list this bootcamp actually creates, and a check that enumerates
    two hundred resource types is a check that goes stale.
    """
    findings: List[Finding] = []

    for resource in directory.resources(*sorted(STATEFUL_RESOURCE_TYPES)):
        protected = False
        for lifecycle in extract_blocks(resource.body, "lifecycle"):
            value = block_attributes(lifecycle.body).get("prevent_destroy", "")
            if value.strip().lower() == "true":
                protected = True
        if protected:
            continue

        attrs = block_attributes(resource.body)
        findings.append(
            Finding(
                check_id="IAC-013",
                severity="MEDIUM",
                resource_type=f"Terraform::{resource.labels[0]}",
                resource_id=f"{directory.label}::{resource.address}",
                title="Stateful resource has no prevent_destroy",
                detail=(
                    f"{resource.address} in {resource.filename} is a "
                    f"{resource.labels[0]} — it holds data — and carries no "
                    f"lifecycle { '{' } prevent_destroy = true { '}' } block. A "
                    f"mistyped `terraform destroy`, a workspace selected in the wrong "
                    f"terminal, or a module refactor that changes its address will "
                    f"take the data with it."
                    + (
                        " force_destroy is also true, so S3 will not even stop at a "
                        "non-empty bucket."
                        if attrs.get("force_destroy", "").strip().lower() == "true"
                        else ""
                    )
                ),
                remediation=(
                    "Add the block:\n"
                    "  lifecycle { prevent_destroy = true }\n"
                    "It takes a literal — `prevent_destroy = var.x` is a hard error, "
                    "because lifecycle is evaluated before variables resolve. When you "
                    "genuinely need to destroy, remove the block, apply, then destroy: "
                    "three deliberate steps. Do not delete it mid-incident to make an "
                    "error go away."
                ),
                evidence={
                    "file": resource.filename,
                    "force_destroy": attrs.get("force_destroy"),
                },
            )
        )
    return findings


def check_resource_tags(directory: TerraformDirectory) -> List[Finding]:
    """IAC-014 — a declared resource missing the required tags.

    Untagged resources cannot be attributed in Cost Explorer, cannot be traced
    to an owner, and cannot be safely cleaned up, because nobody can prove
    whose they are. Every long-lived AWS account has a pile of these and a
    standing agenda item about them that never closes.

    Required here: Project, Day, ManagedBy, Owner. Four, deliberately — a
    required-tag policy with eleven entries is one nobody complies with, and
    partial compliance is indistinguishable from none.

    SCOPING — two exemptions, both load-bearing for this repository:

      * ROOT MODULES ONLY. A child module has no provider block, inherits the
        caller's provider, and therefore inherits the caller's default_tags.
        Auditing modules/network for tags means auditing it without knowing
        who calls it, and the answer would be wrong for every caller that
        tags correctly. Child modules are skipped entirely.

      * default_tags COVERS THE DIRECTORY. A `provider "aws"` block with a
        default_tags block applies those tags to every taggable resource it
        creates. Reporting resources in that directory as untagged would be a
        false positive on every single one — which is precisely the kind of
        output that gets a linter removed from the pipeline in week two.

    Between them, that is why modules/*, envs/dev and envs/prod are clean and
    only bad-examples/ fires: it is a root module whose provider deliberately
    omits default_tags.

    Resource types that take no tags at all are skipped — see
    NON_TAGGABLE_RESOURCE_TYPES. Flagging aws_route_table_association for
    missing an Owner tag is noise.
    """
    findings: List[Finding] = []

    if not is_root_module(directory):
        return findings
    if has_default_tags(directory):
        return findings

    for resource in directory.resources():
        resource_type = resource.labels[0]
        if resource_type in NON_TAGGABLE_RESOURCE_TYPES:
            continue

        attrs = block_attributes(resource.body)
        tags_source = attrs.get("tags", "")
        present = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", tags_source))
        present |= {
            unquote_hcl(key) for key in re.findall(r"\"([^\"]+)\"\s*=", tags_source)
        }

        missing = [tag for tag in REQUIRED_TAGS if tag not in present]
        if not missing:
            continue

        findings.append(
            Finding(
                check_id="IAC-014",
                severity="MEDIUM",
                resource_type=f"Terraform::{resource_type}",
                resource_id=f"{directory.label}::{resource.address}",
                title="Resource is missing required tags",
                detail=(
                    f"{resource.address} in {resource.filename} is missing "
                    f"{', '.join(missing)}, and this directory's provider sets no "
                    f"default_tags to cover it. The resource will appear in the bill "
                    f"with no cost allocation, and in six months nobody will be able "
                    f"to prove whether it is safe to delete."
                ),
                remediation=(
                    "Set them once, for the whole directory, in the provider:\n"
                    "  provider \"aws\" { default_tags { tags = { "
                    "Project = \"aws-aiops-bootcamp\", Day = \"05\", "
                    "ManagedBy = \"terraform\", Owner = var.owner } } }\n"
                    "That is better than tagging each resource: it cannot be forgotten "
                    "on the next resource somebody adds. Then activate Project and "
                    "Owner as cost allocation tags in Billing, or Cost Explorer will "
                    "not group by them — activation is a separate step people miss."
                ),
                evidence={
                    "file": resource.filename,
                    "missing_tags": missing,
                    "tags_present": sorted(present),
                },
            )
        )
    return findings


def check_tag_drift(resource: Dict[str, Any], region: str = "") -> List[Finding]:
    """IAC-015 — the tags deployed in AWS no longer match the ones declared.

    Silent on a fresh apply — not by design, but by SITUATION. Drift does not
    exist until somebody changes something outside Terraform, which is a
    different thing from a check that was written to find nothing. Step 6 of
    the lab makes it fire on purpose: change the CostCentre tag on
    aws_cloudwatch_log_group.drift_target from `engineering` to `finance` in
    the console, re-run this tool, and here it is.

    Why tags specifically: they are the attribute humans edit in the console
    most often, because the console makes it a two-click operation and it
    feels like metadata rather than infrastructure. Then the next apply
    reverts it, the person who made the change does it again, and everyone
    concludes Terraform is fighting them.

    What the check does NOT do: compare every attribute of every resource.
    That is what `terraform plan` is for, and plan does it properly, with the
    provider's own diff logic and a full understanding of computed values.
    This check exists to catch drift WITHOUT running plan — from a read-only
    role, on a schedule, across accounts nobody has state access to. Different
    tool, different question.

    Only tags whose declared value is a LITERAL are compared. A tag declared
    as "${local.name_prefix}-demo" cannot be resolved without evaluating the
    configuration, and guessing at it would produce false positives on every
    resource with an interpolated Name tag — which is all of them.

    Expects: {"resource_type", "resource_id", "declared_tags": {...},
              "deployed_tags": {...}}
    """
    findings: List[Finding] = []

    declared = resource.get("declared_tags") or {}
    deployed = resource.get("deployed_tags") or {}

    differences: Dict[str, Dict[str, Any]] = {}
    for key, expected in declared.items():
        actual = deployed.get(key)
        if actual is None:
            differences[key] = {"declared": expected, "deployed": None}
        elif actual != expected:
            differences[key] = {"declared": expected, "deployed": actual}

    if not differences:
        return findings

    resource_id = resource.get("resource_id", "unknown")
    summary = ", ".join(
        f"{key}: {value['declared']!r} -> {value['deployed']!r}"
        for key, value in sorted(differences.items())
    )

    findings.append(
        Finding(
            check_id="IAC-015",
            severity="MEDIUM",
            resource_type=resource.get("resource_type", "AWS::Resource"),
            resource_id=resource_id,
            title="Deployed tags have drifted from the configuration",
            detail=(
                f"{resource_id} has tags in AWS that do not match the ones declared "
                f"in Terraform: {summary}. Something changed this outside Terraform. "
                f"The next apply will revert it, without asking, and whoever made the "
                f"change in the console will not be told."
            ),
            remediation=(
                "Decide which source of truth is right, then make it explicit — there "
                "are three correct answers and choosing accidentally is the only wrong "
                "one:\n"
                "  terraform apply                 code wins, AWS is reconciled\n"
                "  terraform plan -refresh-only    reality wins, state is updated to "
                "match and the code is then changed to agree\n"
                "  lifecycle { ignore_changes = [tags[\"CostCentre\"]] }   stop caring "
                "about this attribute, because something else legitimately owns it\n"
                "If this keeps happening, the console access that allows it is the "
                "actual finding."
            ),
            evidence={"differences": differences},
            region=region,
        )
    )
    return findings


def check_iteration_and_variables(directory: TerraformDirectory) -> List[Finding]:
    """IAC-016 — `count` where `for_each` belongs, and undeclared variables.

    Two low-severity code-quality faults sharing a check ID because they share
    a cause: writing HCL the way you would write a script instead of the way
    Terraform reads it.

    ARM 1 — count over something that is not a boolean.

    Terraform addresses count-created resources by POSITION:
    aws_s3_bucket.reports[0], [1], [2]. Delete an element from the middle of
    the list and Terraform does not see "one item removed". It sees [0]
    changed name, [1] changed name, and [2] gone — so it plans to DESTROY AND
    RECREATE all three, including the two you never touched. For an S3 bucket
    or an RDS instance, "recreate" means the data is gone.

    for_each addresses by KEY:
        for_each = toset(var.report_bucket_names)
        bucket   = each.value
    Now removing one key removes exactly one resource and nothing else in the
    plan moves.

    count is correct for exactly one shape: `count = var.enabled ? 1 : 0`, the
    conditional-creation idiom. The moment the number can exceed one, you want
    for_each. This check therefore only fires when the count expression is not
    a boolean gate — every `? 1 : 0` in this repository stays quiet.

    ARM 2 — a variable with no type or no description.

    No TYPE means Terraform infers one from whatever is supplied. Pass a
    string where the code expects a list and you do not get a plan-time error,
    you get a confusing apply-time one from deep inside a resource. `type =
    any` is not a fix; it is the same decision written down.

    No DESCRIPTION means that when a value is missing, `terraform plan`
    prompts for it with a blank line — `var.environment_name` / `Enter a
    value:` — and somebody types the wrong thing into production because
    nothing on screen said what it was for. The description is not
    documentation for the README, it is the prompt text.
    """
    findings: List[Finding] = []

    boolean_gate = re.compile(r"\?\s*1\s*:\s*0|\?\s*0\s*:\s*1|^\s*[01]\s*$")

    for resource in directory.resources() + directory.blocks("module"):
        attrs = block_attributes(resource.body)
        count_expression = attrs.get("count")
        if not count_expression:
            continue
        if boolean_gate.search(count_expression):
            continue

        findings.append(
            Finding(
                check_id="IAC-016",
                severity="LOW",
                resource_type="Terraform::Configuration",
                resource_id=f"{directory.label}::{resource.address}",
                title="count used where for_each belongs",
                detail=(
                    f"{resource.address} in {resource.filename} uses "
                    f"`count = {_truncate(count_expression, 50)}`, which can produce "
                    f"more than one instance. Those instances are addressed by "
                    f"position, so removing an element from the middle of the "
                    f"collection renumbers every instance after it and Terraform plans "
                    f"to destroy and recreate resources nobody touched."
                ),
                remediation=(
                    "Switch to for_each, keyed by something stable:\n"
                    "  for_each = toset(var.report_bucket_names)\n"
                    "  bucket   = each.value\n"
                    "Migrate existing state with `terraform state mv "
                    "'aws_s3_bucket.reports[0]' 'aws_s3_bucket.reports[\"alpha\"]'` — "
                    "or a `moved` block, which does the same thing in code where it "
                    "gets reviewed. Keep count only for `var.enabled ? 1 : 0`."
                ),
                evidence={"file": resource.filename, "count": count_expression},
            )
        )

    for variable in directory.blocks("variable"):
        attrs = block_attributes(variable.body)
        missing = [key for key in ("type", "description") if key not in attrs]
        if not missing:
            continue

        findings.append(
            Finding(
                check_id="IAC-016",
                severity="LOW",
                resource_type="Terraform::Variable",
                resource_id=f"{directory.label}::var.{variable.name}",
                title="Variable declared without type or description",
                detail=(
                    f"variable \"{variable.name}\" in {variable.filename} has no "
                    f"{' and no '.join(missing)}. Without a type, a wrong value fails "
                    f"at apply time from inside a resource instead of at plan time. "
                    f"Without a description, the interactive prompt is a blank line "
                    f"and whoever fills it in is guessing."
                ),
                remediation=(
                    f"Declare both:\n"
                    f"  variable \"{variable.name}\" {{\n"
                    f"    description = \"What this is for, and what happens if it is "
                    f"wrong.\"\n"
                    f"    type        = string\n"
                    f"  }}\n"
                    f"Add a validation block while you are there — it turns a class of "
                    f"apply-time failures into plan-time errors with a message you "
                    f"wrote."
                ),
                evidence={"file": variable.filename, "missing": missing},
            )
        )

    return findings


# Every static check, in check-ID order. The auditor iterates this rather than
# calling each one by name, so adding a check is one line and cannot be
# forgotten in the runner.
STATIC_CHECKS = [
    ("IAC-001", check_hardcoded_secrets),
    ("IAC-002", check_provider_credentials),
    ("IAC-003", check_committed_state),
    ("IAC-005", check_local_state),
    ("IAC-008", check_sensitive_outputs),
    ("IAC-009", check_open_ingress),
    ("IAC-010", check_required_version),
    ("IAC-011", check_provider_pinning),
    ("IAC-012", check_lock_file),
    ("IAC-013", check_prevent_destroy),
    ("IAC-014", check_resource_tags),
    ("IAC-016", check_iteration_and_variables),
]

# Checks that need AWS credentials. Listed separately so `--help` and the
# banner can be honest about what a credential-free run does not cover.
LIVE_CHECKS = ["IAC-004", "IAC-006", "IAC-007", "IAC-015"]


###############################################################################
# Auditor
###############################################################################


def looks_like_state_bucket(name: str) -> bool:
    """True if a bucket name matches one of the state-bucket conventions.

    Used to build the candidate set for the live checks when --state-bucket is
    not supplied, or in addition to it. This is a naming heuristic and it is
    honest about being one: a state bucket called `acme-prod-infra-2019` is
    invisible to it. Pass --state-bucket for anything that does not follow the
    convention.
    """
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in STATE_BUCKET_PATTERNS)


def literal_name_suffix(value: str) -> str:
    """The trailing literal part of a possibly-interpolated HCL string.

    `"/aws/${local.name_prefix}/drift-demo"` -> `/drift-demo`

    That suffix is enough to match a deployed resource by name without
    evaluating the configuration, which is the only way a read-only tool with
    no state access can line declared resources up against real ones.
    """
    literal = unquote_hcl(value)
    if "${" in literal:
        return literal.rsplit("}", 1)[-1]
    return literal


def declared_tag_literals(resource: HclBlock) -> Dict[str, str]:
    """Tags on a resource whose values are literal strings, as a plain dict."""
    tags_source = block_attributes(resource.body).get("tags", "")
    if not tags_source:
        return {}
    inner = object_body(tags_source)
    literals: Dict[str, str] = {}
    for key, value in block_attributes(inner).items():
        if is_literal_string(value):
            literals[unquote_hcl(key)] = unquote_hcl(value)
    for match in re.finditer(r"\"([^\"]+)\"\s*=\s*(\"[^\"]*\")", inner):
        if is_literal_string(match.group(2)):
            literals[match.group(1)] = unquote_hcl(match.group(2))
    return literals


class IaCAuditor:
    """Parses Terraform on disk and, with credentials, checks the live state.

    Two phases that are deliberately separable:

      1. STATIC — every .tf file under --path. Needs nothing but a filesystem.
         Runs in a pre-commit hook, in a PR pipeline, on a locked-down bastion.
      2. LIVE   — the state bucket and deployed tags. Needs read-only AWS
         credentials, and is skipped with a note when there are none.

    A run with no credentials is not a degraded run, it is the CI run. Twelve
    of the sixteen checks work there, and all twelve of them find problems
    while they are still cheap to fix.
    """

    def __init__(
        self,
        path: str = ".",
        profile: Optional[str] = None,
        region: str = "us-east-1",
        state_bucket: Optional[str] = None,
        state_key: Optional[str] = None,
        quiet: bool = False,
    ) -> None:
        self.path = path
        self.region = region
        self.state_bucket = state_bucket
        self.state_key = state_key
        self.quiet = quiet
        self.findings: List[Finding] = []
        self.directories: List[TerraformDirectory] = []
        self.live_enabled = False
        self.stats: Dict[str, int] = {
            "directories": 0,
            "files": 0,
            "resources": 0,
            "modules": 0,
            "variables": 0,
            "outputs": 0,
            "state_buckets": 0,
        }

        self.session: Any = None
        self.s3: Any = None
        self.logs: Any = None

        session_kwargs: Dict[str, Any] = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        try:
            self.session = boto3.Session(**session_kwargs)
            self.s3 = self.session.client("s3")
            self.logs = self.session.client("logs")
        except (BotoCoreError, NoCredentialsError) as exc:
            self.log(f"  ! No AWS session ({exc}). Static checks only.")
            self.session = None

    # -- logging ------------------------------------------------------------

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def _swallow(self, operation: str, resource: str, exc: ClientError) -> None:
        """Log an API error that should not abort the whole audit.

        NoSuchPublicAccessBlockConfiguration and friends are the NORMAL answer
        to "does this bucket have one" — S3 returns an error rather than an
        empty result — so they are handled by the caller and not logged.
        """
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in (
            "NoSuchPublicAccessBlockConfiguration",
            "ServerSideEncryptionConfigurationNotFoundError",
            "NoSuchBucketPolicy",
            "ResourceNotFoundException",
            "NoSuchEntity",
        ):
            return
        self.log(f"  ! {operation} failed for {resource}: {code}")

    # -- static -------------------------------------------------------------

    def load(self) -> List[TerraformDirectory]:
        if not os.path.isdir(self.path):
            print(
                f"--path {self.path!r} is not a directory. Point it at the "
                f"Terraform you want audited, for example --path ../terraform.",
                file=sys.stderr,
            )
            sys.exit(2)

        self.directories = discover_directories(self.path)
        self.stats["directories"] = len(self.directories)
        for directory in self.directories:
            self.stats["files"] += len(directory.files)
            self.stats["resources"] += len(directory.resources())
            self.stats["modules"] += len(directory.blocks("module"))
            self.stats["variables"] += len(directory.blocks("variable"))
            self.stats["outputs"] += len(directory.blocks("output"))
        return self.directories

    def run_static(self) -> List[Finding]:
        findings: List[Finding] = []
        for directory in self.directories:
            for _check_id, check in STATIC_CHECKS:
                findings += check(directory)
        return findings

    # -- live collection ----------------------------------------------------

    def candidate_state_buckets(self) -> List[str]:
        """The bucket named by --state-bucket, plus anything that looks like one.

        Both, not either. --state-bucket alone would miss the deliberately
        insecure example bucket the lab creates; the name heuristic alone
        would miss a state bucket named after the company. Together they find
        the ones that matter and the checks themselves are cheap.
        """
        names: List[str] = []
        if self.state_bucket:
            names.append(self.state_bucket)
        for bucket in paginate(self.s3, "list_buckets", "Buckets"):
            name = bucket.get("Name", "")
            if name and looks_like_state_bucket(name) and name not in names:
                names.append(name)
        return names

    def collect_bucket(self, name: str) -> Dict[str, Any]:
        """Everything the three live bucket checks need, in one shape."""
        bucket: Dict[str, Any] = {"Name": name}

        try:
            bucket["Versioning"] = self.s3.get_bucket_versioning(Bucket=name)
        except ClientError as exc:
            self._swallow("get_bucket_versioning", name, exc)
            bucket["Versioning"] = {}

        try:
            bucket["Encryption"] = self.s3.get_bucket_encryption(
                Bucket=name
            ).get("ServerSideEncryptionConfiguration", {})
        except ClientError as exc:
            self._swallow("get_bucket_encryption", name, exc)
            bucket["Encryption"] = {}

        try:
            bucket["PublicAccessBlock"] = self.s3.get_public_access_block(
                Bucket=name
            ).get("PublicAccessBlockConfiguration", {})
        except ClientError as exc:
            self._swallow("get_public_access_block", name, exc)
            bucket["PublicAccessBlock"] = {}

        try:
            bucket["PolicyStatus"] = self.s3.get_bucket_policy_status(
                Bucket=name
            ).get("PolicyStatus", {})
        except ClientError as exc:
            self._swallow("get_bucket_policy_status", name, exc)
            bucket["PolicyStatus"] = {}

        try:
            bucket["Grants"] = self.s3.get_bucket_acl(Bucket=name).get("Grants", [])
        except ClientError as exc:
            self._swallow("get_bucket_acl", name, exc)
            bucket["Grants"] = []

        return bucket

    def drift_candidates(self) -> List[Dict[str, Any]]:
        """Match declared CloudWatch log groups against deployed ones by name.

        Narrow on purpose. Log groups are what the lab's drift demonstration
        uses, they are free, and their tags are readable with one extra API
        call. Extending this to every taggable resource type would mean a
        describe call per service and a lot of pagination for a check whose
        job `terraform plan` already does better when you have state access.
        """
        declared: List[Dict[str, Any]] = []
        for directory in self.directories:
            if not is_root_module(directory):
                continue
            for resource in directory.resources("aws_cloudwatch_log_group"):
                tags = declared_tag_literals(resource)
                if not tags:
                    continue
                name_value = block_attributes(resource.body).get("name", "")
                suffix = literal_name_suffix(name_value)
                if not suffix:
                    continue
                declared.append(
                    {
                        "address": resource.address,
                        "suffix": suffix,
                        "declared_tags": tags,
                    }
                )

        if not declared:
            return []

        deployed = paginate(self.logs, "describe_log_groups", "logGroups")
        candidates: List[Dict[str, Any]] = []
        for entry in declared:
            for group in deployed:
                name = group.get("logGroupName", "")
                if not name.endswith(entry["suffix"]):
                    continue
                arn = group.get("arn", "").rstrip("*").rstrip(":")
                try:
                    tags = self.logs.list_tags_for_resource(
                        resourceArn=arn
                    ).get("tags", {})
                except ClientError as exc:
                    self._swallow("list_tags_for_resource", name, exc)
                    continue
                candidates.append(
                    {
                        "resource_type": "AWS::Logs::LogGroup",
                        "resource_id": name,
                        "declared_tags": entry["declared_tags"],
                        "deployed_tags": tags,
                    }
                )
        return candidates

    def run_live(self) -> List[Finding]:
        findings: List[Finding] = []
        if self.session is None:
            return findings

        try:
            self.session.client("sts").get_caller_identity()
        except (ClientError, BotoCoreError, NoCredentialsError):
            self.log(
                "  ! No usable AWS credentials. Skipping the live checks "
                f"({', '.join(LIVE_CHECKS)}). The static checks below are complete."
            )
            return findings

        self.live_enabled = True

        buckets = self.candidate_state_buckets()
        self.stats["state_buckets"] = len(buckets)
        self.log(f"  Candidate state buckets : {len(buckets)}")

        for name in buckets:
            bucket = self.collect_bucket(name)
            findings += check_state_bucket_public(bucket, self.region)
            findings += check_state_bucket_versioning(bucket, self.region)
            findings += check_state_bucket_encryption(bucket, self.region)

        drift = self.drift_candidates()
        self.log(f"  Drift candidates        : {len(drift)}")
        for resource in drift:
            findings += check_tag_drift(resource, self.region)

        return findings

    # -- orchestration ------------------------------------------------------

    def run(self) -> List[Finding]:
        self.log("")
        self.log(f"  Scanning {os.path.abspath(self.path)}")

        self.load()
        self.log(f"  Terraform directories   : {self.stats['directories']}")
        self.log(f"  .tf files               : {self.stats['files']}")
        self.log(f"  Declared resources      : {self.stats['resources']}")

        self.findings = self.run_static()
        self.log(f"  Static findings         : {len(self.findings)}")

        self.findings += self.run_live()
        if not self.live_enabled:
            self.log(
                f"  Live checks             : skipped "
                f"({', '.join(LIVE_CHECKS)} need credentials)"
            )

        self.log("")
        return self.findings


###############################################################################
# Scoring
###############################################################################


def calculate_score(findings: List[Finding]) -> int:
    """100 minus the sum of severity weights, floored at 0.

    Floored, not negative: once you are at zero there is no useful distinction
    between 'very broken' and 'even more broken'. Fix something and re-run.

    Expect zero against this repository's lab/terraform/. Two CRITICAL
    findings are 50 points on their own and the static weights total 106. That
    is the intended shock, and the bad-examples/ directory is deliberately
    ridiculous. Point the tool at envs/dev or modules/ alone and it scores
    100/100, which is the more useful demonstration: the same tool, the same
    checks, a directory somebody wrote carefully.
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
    w(colour("  INFRASTRUCTURE AS CODE AUDIT", "BOLD", use_colour))
    w("\n  CareerByteCode · Day 05 · Infrastructure as Code\n")
    w(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    w(f"{bar}\n\n")

    w("  Scanned: ")
    w(
        f"{stats.get('directories', 0)} directory(ies) · "
        f"{stats.get('files', 0)} .tf file(s) · "
        f"{stats.get('resources', 0)} resource(s) · "
        f"{stats.get('modules', 0)} module call(s) · "
        f"{stats.get('variables', 0)} variable(s) · "
        f"{stats.get('outputs', 0)} output(s) · "
        f"{stats.get('state_buckets', 0)} state bucket(s)\n\n"
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
        "audit": "iac_audit",
        "day": "05",
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
        prog="iac_audit.py",
        description=(
            "Audit Terraform configuration on disk — and optionally the live "
            "state bucket behind it — for hardcoded credentials, missing "
            "backends, unpinned providers, unprotected stateful resources and "
            "the other faults that are cheap to fix before apply and expensive "
            "afterwards."
        ),
        epilog=(
            "Examples:\n"
            "  iac_audit.py --path ../terraform\n"
            "  iac_audit.py --path ../terraform --profile bootcamp "
            "--state-bucket cbc-day05-tfstate-a1b2c3\n"
            "  iac_audit.py --path . --format json --quiet > findings.json\n"
            "  iac_audit.py --path . --min-severity HIGH --format csv\n"
            "  iac_audit.py --path . --fail-on CRITICAL   # exit 1 on any CRITICAL\n"
            "\n"
            "Twelve of the sixteen checks need no AWS credentials at all. Run it "
            "in a pre-commit hook.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--path",
        default=".",
        help=(
            "Directory of Terraform to scan, searched recursively "
            "(default: the current directory)."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI named profile. Day 01 created 'bootcamp'.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region for the live checks (default: us-east-1).",
    )
    parser.add_argument(
        "--state-bucket",
        default=None,
        help=(
            "Name of the Terraform state bucket, for IAC-004/006/007. Buckets "
            "whose names match *-tfstate-* or *-state-* are checked as well."
        ),
    )
    parser.add_argument(
        "--state-key",
        default=None,
        help=(
            "Object key of the state file within --state-bucket. Recorded in "
            "the report; reserved for the state-content checks."
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

    auditor = IaCAuditor(
        path=args.path,
        profile=args.profile,
        region=args.region,
        state_bucket=args.state_bucket,
        state_key=args.state_key,
        quiet=args.quiet,
    )

    try:
        all_findings = auditor.run()
    except NoCredentialsError:
        # Reaching here means a live call escaped run_live's guard. The static
        # findings are still valid, so say what happened rather than exiting.
        print(
            "No AWS credentials found for the live checks. Try --profile "
            "bootcamp, or run `aws configure --profile bootcamp`. The static "
            "checks do not need them.",
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
