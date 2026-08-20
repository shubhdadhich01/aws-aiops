#!/usr/bin/env python3
"""
iac_audit_challenge.py — build the Day 05 Infrastructure as Code auditor yourself.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

===============================================================================
  HOW THIS WORKS
===============================================================================

All the boring parts are done for you: the CLI, the Finding dataclass, the
scoring, all three output renderers, the boto3 collectors, and — most
importantly — the entire HCL parsing layer. `strip_hcl_comments`,
`extract_blocks`, `block_attributes`, `find_gitignore_rules`, `is_root_module`
and `has_default_tags` are complete and tested. You will not be writing a
brace-matching scanner today.

What is missing is the part that matters: the sixteen checks.

There are 16 TODOs. Each one has:
    * a time estimate
    * the exact fields, block types and regex targets you need
    * a hint if you are stuck
    * a CHECKPOINT so you know whether it worked before moving on

Total: roughly 120–140 minutes if you have not done this before.

Run it after each TODO — twelve of the sixteen checks need no AWS credentials
at all, so most of this works on a plane:

    python3 iac_audit_challenge.py --path ../../terraform

You are done when your output matches the reference implementation at
../iac_audit.py:

    13 findings, compliance score 0/100, with no credentials
    15 findings, compliance score 0/100, with credentials and the insecure
       example bucket applied

Do not read the reference first. You will learn nothing, and the checks are
the whole exercise.

===============================================================================
  THE OFFLINE FEEDBACK LOOP — USE THIS
===============================================================================

The 47 unit tests in ../tests/test_checks.py can be pointed at YOUR file:

    cd ..
    IAC_AUDIT_MODULE=iac_audit_challenge python3 -m unittest discover -s tests -v

No credentials, no account, no network, under a second. Every check has one
test proving it FIRES on bad input and one proving it stays SILENT on good
input. Run them after every TODO and you will know immediately which half you
broke.

The silent half is the half people skip, and it is the half that decides
whether anyone keeps using your tool. A check that flags every directory gets
suppressed in week two, at which point it does nothing at all.

===============================================================================
  THE CHECKS YOU ARE IMPLEMENTING
===============================================================================

    TODO 1    IAC-001  Hardcoded secret in .tf        CRITICAL  ~12 min
    TODO 2    IAC-002  Credentials in provider        CRITICAL   ~6 min
    TODO 3    IAC-003  .tfstate not gitignored        CRITICAL   ~7 min
    TODO 4    IAC-004  State bucket publicly open     CRITICAL  ~10 min
    TODO 5    IAC-005  No backend — local state       HIGH      ~10 min
    TODO 6    IAC-006  State bucket unversioned       HIGH       ~5 min
    TODO 7    IAC-007  State bucket unencrypted       HIGH       ~6 min
    TODO 8    IAC-008  Output not marked sensitive    HIGH       ~8 min
    TODO 9    IAC-009  0.0.0.0/0 on an INGRESS rule   HIGH      ~12 min
    TODO 10   IAC-010  No required_version            MEDIUM     ~5 min
    TODO 11   IAC-011  Provider version unpinned      MEDIUM     ~8 min
    TODO 12   IAC-012  Lock file gitignored           MEDIUM    ~10 min
    TODO 13   IAC-013  Stateful, no prevent_destroy   MEDIUM     ~7 min
    TODO 14   IAC-014  Resource missing tags          MEDIUM    ~10 min
    TODO 15   IAC-015  Deployed tags have drifted     MEDIUM     ~7 min
    TODO 16   IAC-016  count / untyped variable       LOW       ~10 min

THREE of these produce ZERO findings against this lab, and none of the three
is a mistake:

    IAC-003  There is no committed .tfstate anywhere and every .gitignore
             covers the pattern. Silent BY DESIGN.
    IAC-004  This repository ships no publicly readable bucket, not even as a
             teaching example. Silent BY DESIGN.
    IAC-015  Drift does not exist on a fresh apply, by definition. Silent by
             SITUATION — Step 6 of the lab makes it fire on purpose.

Write all three anyway, and make them silent for the right reason rather than
by accident. The unit tests prove the difference: each has a FIRE test against
synthetic input and a SILENT test against the real thing.

===============================================================================
  BEFORE YOU START — READ THE FIXTURE
===============================================================================

Everything the static checks find lives in ../../terraform/bad-examples/. Open
it. Every fault in there is labelled with the check ID it triggers, and the
comments explain what each one costs in production. That directory is applied
by nothing — it exists to be parsed by this file.

Then open one correct directory, ../../terraform/envs/dev/, and note that your
checks must stay completely silent on it. Both halves are the exercise.

===============================================================================
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


# =============================================================================
# TODO 1 — IAC-001: hardcoded secret in a .tf file                (~12 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   CRITICAL
#
# Blocks to walk:
#     directory.blocks("resource")   also "locals", "output", "variable", "module"
#     block_attributes(block.body)   -> Dict[str, str] of raw source values
#     block.name                     -> the last label, e.g. "db_password"
#     block.address                  -> "aws_ssm_parameter.db_password"
#
# Helpers already written for you:
#     _looks_like_real_secret(value)    literal, non-placeholder, >= 6 chars
#     _value_is_credential(value)       AKIA…, sk-…, ghp-…, PEM, db URI
#     _attribute_is_secret_name(name)   PASSWORD / SECRET_KEY / API_KEY / …
#     _redact(value)                    show enough to identify, not to use
#     SECRET_NAME_HINTS                 regex over the BLOCK name
#     SECRET_VALUE_ATTRIBUTES           {"value", "secret_string", "password", …}
#
# Logic — three ways to trip it, ONE finding per block:
#     attribute VALUE matches a credential shape          -> flag
#     attribute NAME is secret-shaped and holds a literal -> flag
#     BLOCK name is secret-shaped and `value` (or one of
#       SECRET_VALUE_ATTRIBUTES) holds a literal          -> flag
#
# That third arm is the one that catches the fixture. `aws_ssm_parameter`
# "db_password" puts the password in an attribute innocuously called `value`,
# so a purely name-based check misses it entirely.
#
# EXCLUDE provider blocks. Credentials there are IAC-002's job, and reporting
# both double-counts one line of configuration.
#
# HINT: collect `reasons: List[str]` and `evidence: Dict[str, str]` across all
#       the attributes of a block, then emit at most one Finding at the end. A
#       resource with a secret name AND a secret value is one mistake.
#
# HINT: do NOT put the raw secret in the Finding. Use _redact(). A report that
#       republishes the credential it is complaining about is a second copy of
#       the problem, and it ends up in the same CI log.
#
# CHECKPOINT: bad-examples -> 1 CRITICAL on aws_ssm_parameter.db_password.
#             envs/dev, envs/prod, modules/* -> 0.
# =============================================================================
def check_hardcoded_secrets(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 1: implement the logic described above.

    return findings


def _redact(value: str) -> str:
    """Show enough of a secret to identify it and not enough to use it."""
    value = value.strip()
    if len(value) <= 8:
        return value[:2] + "*" * max(0, len(value) - 2)
    return f"{value[:4]}...{value[-2:]} ({len(value)} chars)"


# =============================================================================
# TODO 2 — IAC-002: static credentials in a provider block         (~6 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   CRITICAL
#
# Blocks:     directory.blocks("provider")
# Attributes: access_key, secret_key, token, session_token
# Helpers:    is_literal_string(value), unquote_hcl(value), _redact(value)
#
# Logic:
#     any of those attributes set to a non-empty literal -> CRITICAL
#     ONE finding per provider block, not one per key
#
# access_key and secret_key are always the same mistake made once. Emitting two
# findings for it inflates the count and tells the reader nothing extra.
#
# HINT: `profile = "bootcamp"` and `region = var.aws_region` must NOT fire. Only
#       the four credential attributes count.
#
# CHECKPOINT: bad-examples -> exactly 1 CRITICAL naming both access_key and
#             secret_key. backend-bootstrap, envs/* -> 0 (they use `profile`).
# =============================================================================
def check_provider_credentials(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 2: implement the logic described above.

    return findings


# =============================================================================
# TODO 3 — IAC-003: state file present and not gitignored          (~7 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   CRITICAL
#
# Fields:
#     directory.state_files       List[str]  filenames matching *.tfstate*
#     directory.gitignore_rules   List[str]  resolved upwards, outermost first
#     gitignore_ignores(rules, filename) -> bool
#
# Logic:
#     state file on disk AND no .gitignore rule covers it -> CRITICAL
#     state file on disk AND gitignored                   -> nothing
#
# ** SILENT BY DESIGN. ** This finds nothing in this lab, and that is the
# correct answer. There is no committed .tfstate anywhere and every .gitignore
# in the tree covers the pattern. Write it properly anyway: the FIRE test in
# ../tests/test_checks.py proves it works, and the whole-stack test proves it
# has zero false positives. "We did not write it" and "it finds nothing" have
# to be distinguishable.
#
# HINT: the loader already excludes .terraform.tfstate.lock.info — that is a
#       transient lock artefact, not a committed state file, and flagging it
#       would fire on every machine that happened to be mid-apply.
#
# CHECKPOINT: whole lab -> 0 findings. A synthetic directory with
#             state_files=["terraform.tfstate"] and gitignore_rules=["*.tfplan"]
#             -> 1 CRITICAL.
# =============================================================================
def check_committed_state(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 3: implement the logic described above.

    return findings


# =============================================================================
# TODO 4 — IAC-004: state bucket is publicly accessible           (~10 minutes)
# =============================================================================
#
# Signature:  (bucket: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   CRITICAL
#
# This is a LIVE check — a plain dict in, so it still unit-tests with no
# credentials. The collector builds the dict for you.
#
# Fields:
#     bucket["Name"]                                     str
#     bucket["PublicAccessBlock"]["BlockPublicAcls"]     bool
#                                ["BlockPublicPolicy"]   bool
#                                ["IgnorePublicAcls"]    bool
#                                ["RestrictPublicBuckets"] bool
#     bucket["PolicyStatus"]["IsPublic"]                 bool
#     bucket["Grants"][n]["Grantee"]["URI"]              str
#     bucket["Grants"][n]["Permission"]                  str
#
# Logic — any ONE of these is a finding:
#     any of the four PAB settings missing or false
#     policy status IsPublic true
#     an ACL grant to AllUsers or AuthenticatedUsers
#
# AuthenticatedUsers means "any AWS account anywhere", not "our accounts". It
# is one of the most consistently misread strings in S3.
#
# ** SILENT BY DESIGN. ** Zero findings here too. This repository does not ship
# a publicly readable bucket even as a teaching example — being one
# `terraform apply` away from leaking somebody's data is not a lesson worth
# the demonstration. The deliberately insecure example bucket in envs/dev has
# a REAL public access block, and exists to fire IAC-006 and IAC-007 only.
#
# HINT: an absent public access block comes back as {} because S3 returns an
#       ERROR rather than an empty result. Treat {} as "all four missing".
#
# CHECKPOINT: both reference buckets -> 0. A dict with PublicAccessBlock={}
#             and PolicyStatus={"IsPublic": True} -> 1 CRITICAL.
# =============================================================================
def check_state_bucket_public(bucket: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 4: implement the logic described above.

    return findings


# =============================================================================
# TODO 5 — IAC-005: root module with no backend                   (~10 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   HIGH
#
# Helpers:
#     is_root_module(directory)              -> has a provider block
#     has_local_state_suppression(directory) -> inline marker present
#     directory.blocks("terraform")
#     extract_blocks(block.body, "backend")  and  ..., "cloud"
#
# Logic, in this order — the order is the check:
#     not a root module              -> return [] (child modules have no backend)
#     suppression marker present     -> return []
#     any terraform block contains a `backend` or `cloud` block -> return []
#     otherwise                      -> HIGH
#
# TWO SCOPING RULES, both load-bearing:
#
#   1. Child modules are skipped. A module has no backend of its own; the root
#      module that calls it owns state for everything it creates. Flagging
#      modules/network for having no backend is asking the wrong directory.
#
#   2. `# iac-audit: allow-local-state` on a comment line of its own exempts a
#      directory. This is not a special case bolted on for backend-bootstrap —
#      it is the suppression mechanism every audit tool needs, and it lives in
#      code, greppable, NEXT TO the thing being suppressed, rather than in a
#      suppressions.yaml nobody reads.
#
#      backend-bootstrap/ carries it because the backend cannot create itself.
#
# HINT: the marker must be a WHOLE comment line. bad-examples/providers.tf
#       discusses the marker at length in prose, and a substring search lets a
#       directory suppress a finding by writing about suppression. That is both
#       a bug and a fairly good joke at your tool's expense.
#       has_local_state_suppression() already handles this — use it.
#
# CHECKPOINT: bad-examples -> 1 HIGH. backend-bootstrap -> 0 (marker).
#             envs/dev, envs/prod -> 0 (backend.tf). modules/* -> 0 (not root).
# =============================================================================
def check_local_state(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 5: implement the logic described above.

    return findings


# =============================================================================
# TODO 6 — IAC-006: state bucket has no versioning                 (~5 minutes)
# =============================================================================
#
# Signature:  (bucket: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     bucket["Name"]                    str
#     bucket["Versioning"]["Status"]    "Enabled" | "Suspended" | absent
#
# Logic:
#     Status == "Enabled"  -> no finding
#     anything else        -> HIGH
#
# Versioning on a state bucket is the rollback path, not a nice-to-have. State
# gets corrupted by interrupted applies, partial writes, a bad `state rm`, or a
# merge somebody resolved by hand. With versioning: copy the previous version
# back, re-run plan, five minutes. Without: `terraform import`, resource by
# resource, from a screenshot of the console.
#
# HINT: "Suspended" is not "Enabled". A bucket that had versioning turned off
#       last year keeps the old versions and stops making new ones, which is
#       the worst of both and reads as fine on a dashboard.
#
# CHECKPOINT: cbc-day05-dev-tfstate-insecure-* -> 1 HIGH.
#             cbc-day05-tfstate-* (built by backend-bootstrap) -> 0.
# =============================================================================
def check_state_bucket_versioning(
    bucket: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 6: implement the logic described above.

    return findings


# =============================================================================
# TODO 7 — IAC-007: state bucket has no default encryption         (~6 minutes)
# =============================================================================
#
# Signature:  (bucket: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   HIGH
#
# Fields:
#     bucket["Name"]  str
#     bucket["Encryption"]["Rules"][n]
#         ["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]  "AES256"|"aws:kms"
#
# Logic:
#     at least one rule with an SSEAlgorithm -> no finding
#     no rules at all                        -> HIGH
#
# S3 has applied SSE-S3 by default to new buckets since January 2023, so this
# reports on the CONFIGURATION rather than trusting the default. A bucket with
# no explicit encryption configuration is a bucket where nobody decided, and
# "nobody decided" is not the same as "encrypted with a key we control and can
# revoke". Say so in the remediation: SSE-KMS with a customer-managed key gives
# you revocation and a CloudTrail record of every Decrypt.
#
# HINT: the collector may hand you either {"Rules": [...]} or the raw
#       {"ServerSideEncryptionConfiguration": {"Rules": [...]}}. Accept both
#       rather than making the caller remember which.
#
# CHECKPOINT: cbc-day05-dev-tfstate-insecure-* -> 1 HIGH.
#             cbc-day05-tfstate-* -> 0.
# =============================================================================
def check_state_bucket_encryption(
    bucket: Dict[str, Any], region: str = ""
) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 7: implement the logic described above.

    return findings


# =============================================================================
# TODO 8 — IAC-008: output exposes a credential                    (~8 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   HIGH
#
# Blocks:     directory.blocks("output")
# Attributes: block_attributes(output.body) -> {"description", "value", "sensitive"}
# Regex:      SECRET_OUTPUT_HINTS matches password|secret|token|credential|
#             private_key|access_key|api_key|passphrase as a whole word part
# Helper:     is_literal_string(value)
#
# Logic:
#     sensitive == "true"                            -> no finding
#     output NAME matches the hint regex             -> HIGH
#     output VALUE EXPRESSION matches the hint regex -> HIGH
#     otherwise                                      -> nothing
#
# Match on the value as well as the name so that
# `output "db_creds" { value = aws_db_instance.main.password }` is caught even
# though the name says nothing.
#
# HINT: `state_kms_key_arn` and `report_bucket_names` must NOT fire. That is
#       what the word-boundary anchors in SECRET_OUTPUT_HINTS are for — a bare
#       "key" substring would flag half the outputs in this repository.
#
# HINT: guard the value arm with `not is_literal_string(value)`. A hardcoded
#       literal is IAC-001's problem, not this one.
#
# CHECKPOINT: bad-examples -> 1 HIGH on output.database_password, and NOT on
#             output.report_bucket_names in the same file.
#             backend-bootstrap -> 0 despite having state_kms_key_arn.
# =============================================================================
def check_sensitive_outputs(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 8: implement the logic described above.

    return findings


# =============================================================================
# TODO 9 — IAC-009: 0.0.0.0/0 on an INGRESS rule                  (~12 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   HIGH
#
# TWO resource shapes, and you need both:
#
#   1. Inline blocks inside a security group:
#          directory.resources("aws_security_group")
#          extract_blocks(resource.body, "ingress")
#          block_attributes(rule.body)["cidr_blocks"]      -> '["0.0.0.0/0"]'
#                                     ["ipv6_cidr_blocks"] -> '["::/0"]'
#
#   2. Standalone rule resources (the modern form, used by modules/network):
#          directory.resources("aws_vpc_security_group_ingress_rule")
#          block_attributes(resource.body)["cidr_ipv4"] -> '"0.0.0.0/0"'
#                                         ["cidr_ipv6"]
#
# Constant:  OPEN_CIDRS == {"0.0.0.0/0", "::/0"}
#
# Logic:
#     an open CIDR on an ingress rule -> HIGH
#     ONE finding per resource, listing every offending rule
#
# ** INGRESS ONLY. ** Egress to 0.0.0.0/0 is normal, is the default AWS
# creates, and is present in modules/network in this very repository. A check
# that flagged every default egress rule would fire on essentially every
# security group in the account — and a check that fires everywhere is a check
# people stop reading. A check nobody reads is worse than no check, because it
# occupies the space where a real one would go.
#
# Locking down egress IS worth doing. It is a deliberate architecture exercise
# with VPC endpoints and a proxy, not a linter finding.
#
# HINT: `cidr_blocks` comes back as raw source text — the string
#       '["0.0.0.0/0", "10.0.0.0/8"]', brackets and all. Pull the quoted
#       strings out with re.findall(r'"([^"]+)"', value) and intersect with
#       OPEN_CIDRS. Do not try to eval it.
#
# HINT: the reference factors the Finding construction into a small helper,
#       because both shapes build the same finding from different attribute
#       names. You will want to as well.
#
# CHECKPOINT: bad-examples -> 1 HIGH on aws_security_group.wide_open, and NOT
#             on its egress block in the same resource.
#             modules/network -> 0, despite aws_vpc_security_group_egress_rule
#             with cidr_ipv4 = "0.0.0.0/0".
# =============================================================================
def check_open_ingress(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 9: implement the logic described above.

    return findings


# =============================================================================
# TODO 10 — IAC-010: no required_version constraint                (~5 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   MEDIUM
#
# Blocks:     directory.blocks("terraform")
# Attribute:  "required_version" in block_attributes(block.body)
#
# Logic:
#     no terraform block at all                       -> return [] (not a root
#                                                        module's problem)
#     ANY terraform block sets required_version       -> return []
#     terraform blocks exist and none sets it         -> MEDIUM
#
# ** EVALUATE PER DIRECTORY, NOT PER BLOCK. ** This is the part people get
# wrong. envs/dev has one terraform block in providers.tf holding
# required_version and another in backend.tf holding the backend. Between them
# the directory is correctly configured. A per-block check reports a false
# positive on backend.tf, in a directory that is doing everything right, which
# is the fastest possible way to lose your reader's trust.
#
# Terraform concatenates every .tf file in a directory before evaluating
# anything. Your check should read them the same way.
#
# HINT: `return []` early on the first block that has it, rather than
#       accumulating.
#
# CHECKPOINT: bad-examples -> 1 MEDIUM. envs/dev, envs/prod -> 0 even though
#             backend.tf has a bare terraform block. modules/* -> 0.
# =============================================================================
def check_required_version(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 10: implement the logic described above.

    return findings


# =============================================================================
# TODO 11 — IAC-011: provider version not constrained              (~8 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   MEDIUM
#
# Nesting, three deep:
#     directory.blocks("terraform")
#       -> extract_blocks(terraform_block.body, "required_providers")
#         -> block_attributes(required.body)  gives {"aws": '{ source = ... }'}
#           -> object_body(value) then block_attributes() again
#
# Logic:
#     a provider entry with no `version` key -> MEDIUM
#     ONE finding per directory, listing every unpinned provider
#
# Constraint styles worth explaining in your remediation:
#     "~> 5.80"        patches and minors within 5.x, refuses 6.0   <- want this
#     ">= 5.80"        allows 6.0, therefore not a pin
#     "= 5.80.1"       security patches need a code change
#
# HINT: object_body() strips the outer braces of `{ source = "...", version =
#       "~> 5.80" }`. block_attributes() then splits it on top-level commas as
#       well as newlines, so the single-line and multi-line forms both work.
#       Both forms appear in this repository — do not assume either.
#
# HINT: this and the lock file answer DIFFERENT questions. The constraint says
#       what is ACCEPTABLE; .terraform.lock.hcl records what was SELECTED. You
#       want both, which is why IAC-012 exists separately.
#
# CHECKPOINT: bad-examples -> 1 MEDIUM naming "aws".
#             everywhere else -> 0 (all pinned "~> 5.80" or ">= 5.80, < 6.0").
# =============================================================================
def check_provider_pinning(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 11: implement the logic described above.

    return findings


# =============================================================================
# TODO 12 — IAC-012: dependency lock file mishandled              (~10 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     directory.gitignore_rules       resolved upwards, outermost first
#     directory.has_terraform_dir     bool — a .terraform/ directory exists
#     directory.has_lock_file         bool
#     LOCK_FILE == ".terraform.lock.hcl"
#     gitignore_ignores(rules, LOCK_FILE) -> bool
#
# TWO ARMS, and they are NOT equally confident:
#
#   A. GITIGNORED -> MEDIUM, always. A .gitignore rule matching the lock file
#      is a decision somebody made, in a file, on purpose, and it is wrong.
#
#   B. MISSING -> MEDIUM only when has_terraform_dir is True. A missing lock
#      file in a directory nobody has ever run init in is not a finding, it is
#      a directory nobody has run init in — modules/network in a fresh clone,
#      for instance. The .terraform/ directory is the EVIDENCE that init has
#      run, which is what makes the absence meaningful rather than untested.
#
#      Get this wrong and the tool fires on all six clean directories in a
#      fresh checkout.
#
# Return after arm A fires — a gitignored lock file is also a missing one, and
# reporting both is one problem counted twice.
#
# HINT: gitignore rules resolve from the scanned directory UPWARDS, the way git
#       does, and the closest file wins. find_gitignore_rules() already returns
#       them outermost-first so that a plain last-match-wins loop reproduces
#       git's precedence, including `!` negations. It is already written.
#
# HINT: terraform/.gitignore in this lab deliberately does NOT list the lock
#       file, and every subdirectory inherits that. Only bad-examples/.gitignore
#       adds it. If you fire on more than one directory, your upward resolution
#       is picking up the wrong file.
#
# CHECKPOINT: bad-examples -> 1 MEDIUM. All six other directories -> 0.
# =============================================================================
def check_lock_file(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 12: implement the logic described above.

    return findings


# =============================================================================
# TODO 13 — IAC-013: stateful resource with no prevent_destroy     (~7 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   MEDIUM
#
# Resources:  directory.resources(*sorted(STATEFUL_RESOURCE_TYPES))
#             STATEFUL_RESOURCE_TYPES = aws_s3_bucket, aws_db_instance,
#                                       aws_rds_cluster, aws_dynamodb_table
# Blocks:     extract_blocks(resource.body, "lifecycle")
# Attribute:  block_attributes(lifecycle.body)["prevent_destroy"] == "true"
#
# Logic:
#     a lifecycle block with prevent_destroy = true -> no finding
#     otherwise                                     -> MEDIUM
#
# Two things to get into the remediation, because they are the two things
# people get wrong:
#
#   * prevent_destroy takes a LITERAL. `prevent_destroy = var.protect` is a
#     hard error, not a warning, because lifecycle is evaluated before
#     variables resolve — Terraform needs to know whether destroy is permitted
#     before it has a value for anything. There is no per-environment toggle.
#
#   * When destroy legitimately needs to happen: remove the block, apply, then
#     destroy. Three deliberate steps. NOT "delete the lifecycle block because
#     destroy keeps failing and I want it to stop failing", which is how
#     production buckets go missing.
#
# HINT: mention force_destroy in the detail if it is also true. An S3 bucket
#       with force_destroy = true and no prevent_destroy will not even stop at
#       a non-empty bucket.
#
# CHECKPOINT: bad-examples -> 1 MEDIUM on aws_s3_bucket.reports.
#             modules/storage -> 0 (both its bucket and table are protected).
#             envs/dev -> 0 (even the insecure example bucket has the block —
#             it exists to fire IAC-006 and IAC-007, nothing else).
# =============================================================================
def check_prevent_destroy(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 13: implement the logic described above.

    return findings


# =============================================================================
# TODO 14 — IAC-014: resource missing required tags               (~10 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   MEDIUM
#
# Helpers:    is_root_module(directory), has_default_tags(directory)
# Resources:  directory.resources()
# Constants:  REQUIRED_TAGS = ["Project", "Day", "ManagedBy", "Owner"]
#             NON_TAGGABLE_RESOURCE_TYPES
# Attribute:  block_attributes(resource.body)["tags"] -> raw '{ A = "1" ... }'
#
# Logic, in this order:
#     not a root module            -> return []
#     provider sets default_tags   -> return []
#     resource type is non-taggable -> skip it
#     any REQUIRED_TAGS key absent -> MEDIUM, one finding per resource
#
# TWO SCOPING RULES, and between them they are why only bad-examples fires:
#
#   1. ROOT MODULES ONLY. A child module has no provider block, inherits the
#      caller's provider, and therefore inherits the caller's default_tags.
#      Auditing modules/network means auditing it without knowing who calls it,
#      and the answer is wrong for every caller that tags correctly.
#
#   2. default_tags COVERS THE DIRECTORY. A `provider "aws"` block with a
#      default_tags block applies those tags to every taggable resource it
#      creates. Reporting those resources as untagged is a false positive on
#      every single one — precisely the output that gets a linter deleted from
#      the pipeline in week two.
#
# HINT: you are matching KEYS in a raw HCL map, not evaluating it. Both
#       `Project = "x"` and `"Project" = "x"` are legal, so pull identifiers
#       with re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', tags_source) and
#       quoted keys with re.findall(r'"([^"]+)"\s*=', tags_source), then union.
#
# HINT: four required tags, not eleven. A required-tag policy with eleven
#       entries is one nobody complies with, and partial compliance is
#       indistinguishable from none.
#
# CHECKPOINT: bad-examples -> exactly 2 MEDIUM (aws_s3_bucket.reports and
#             aws_security_group.wide_open) and NOT aws_ssm_parameter
#             .db_password, which is tagged.
#             Everywhere else -> 0.
# =============================================================================
def check_resource_tags(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 14: implement the logic described above.

    return findings


# =============================================================================
# TODO 15 — IAC-015: deployed tags have drifted                    (~7 minutes)
# =============================================================================
#
# Signature:  (resource: Dict[str, Any], region: str = "") -> List[Finding]
# Severity:   MEDIUM
#
# Fields:
#     resource["resource_type"]   "AWS::Logs::LogGroup"
#     resource["resource_id"]     "/aws/cbc-day05-dev/drift-demo"
#     resource["declared_tags"]   {"CostCentre": "engineering"}  from the HCL
#     resource["deployed_tags"]   {"CostCentre": "finance"}      from the API
#
# Logic:
#     for each DECLARED tag:
#         missing from deployed        -> difference
#         present with a different value -> difference
#     any differences -> MEDIUM
#
# ** ONE DIRECTION ONLY. ** Extra tags in AWS that Terraform does not declare
# are NOT drift. default_tags adds them, account tag policies add them, and
# AWS itself adds some. Comparing both directions makes this fire on every
# resource in the account on the first run.
#
# ** SILENT BY SITUATION, not by design. ** Be precise about the difference —
# drift does not exist on a fresh apply, by definition. This is not a check
# written to find nothing; it is a check with nothing to find yet. Step 6 of
# the lab changes the CostCentre tag on aws_cloudwatch_log_group.drift_target
# in the console, from "engineering" to "finance", and then it fires.
#
# Give all three remediations, because there are three correct answers and
# choosing accidentally is the only wrong one:
#     terraform apply                 code wins, AWS is reconciled
#     terraform plan -refresh-only    reality wins, state updated to match
#     lifecycle { ignore_changes = [tags["CostCentre"]] }   stop caring
#
# HINT: only tags whose DECLARED value is a literal can be compared. A tag
#       declared as "${local.name_prefix}-demo" cannot be resolved without
#       evaluating the configuration. declared_tag_literals() already filters
#       for you — but understand why, or you will produce a false positive on
#       every resource with an interpolated Name tag, which is all of them.
#
# CHECKPOINT: fresh apply -> 0. After Step 6 -> 1 MEDIUM, taking the total to
#             16 findings.
# =============================================================================
def check_tag_drift(resource: Dict[str, Any], region: str = "") -> List[Finding]:
    findings: List[Finding] = []

    # TODO 15: implement the logic described above.

    return findings


# =============================================================================
# TODO 16 — IAC-016: count misuse and undeclared variables        (~10 minutes)
# =============================================================================
#
# Signature:  (directory: TerraformDirectory) -> List[Finding]
# Severity:   LOW (weight 1)
#
# TWO ARMS under one check ID, because they share a cause: writing HCL the way
# you would write a script instead of the way Terraform reads it.
#
# ARM 1 — count over something that is not a boolean:
#     for resource in directory.resources() + directory.blocks("module"):
#         block_attributes(resource.body).get("count")
#     Fire UNLESS the expression is a boolean gate:
#         re.compile(r"\?\s*1\s*:\s*0|\?\s*0\s*:\s*1|^\s*[01]\s*$")
#
#     Terraform addresses count-created resources by POSITION —
#     aws_s3_bucket.reports[0], [1], [2]. Delete an element from the middle of
#     the list and Terraform does not see "one item removed". It sees [0]
#     changed name, [1] changed name, [2] gone, and plans to DESTROY AND
#     RECREATE all three, including the two you never touched. For an S3
#     bucket, "recreate" means the data is gone.
#
#     for_each addresses by KEY, so removing one key removes exactly one
#     resource and nothing else in the plan moves.
#
#     count is correct for exactly one shape: `count = var.enabled ? 1 : 0`.
#     The moment the number can exceed one, you want for_each. There are
#     sixteen `count` expressions in this lab and fifteen of them are that
#     shape — your regex has to let all fifteen through.
#
# ARM 2 — a variable with no type or no description:
#     for variable in directory.blocks("variable"):
#         missing = [k for k in ("type", "description")
#                    if k not in block_attributes(variable.body)]
#
#     No TYPE means Terraform infers one from whatever is supplied, so a wrong
#     value fails at apply time from deep inside a resource instead of at plan
#     time. `type = any` is not a fix; it is the same decision written down.
#
#     No DESCRIPTION means the interactive prompt is a blank line —
#     `var.environment_name` / `Enter a value:` — and somebody types the wrong
#     thing into production because nothing on screen said what it was for.
#     The description is not documentation for the README, it is the prompt.
#
# HINT: mention `moved` blocks in the count remediation. Migrating from count
#       to for_each without destroying anything is either `terraform state mv`
#       at the CLI or a `moved` block in code — and the code version gets
#       reviewed, which the CLI version does not.
#
# CHECKPOINT: bad-examples -> exactly 2 LOW: aws_s3_bucket.reports (count over
#             a list) and var.environment_name (no type, no description).
#             Everywhere else -> 0. If you fire on backend-bootstrap or
#             modules/network, your boolean-gate regex is too strict.
# =============================================================================
def check_iteration_and_variables(directory: TerraformDirectory) -> List[Finding]:
    findings: List[Finding] = []

    # TODO 16: implement the logic described above.

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
    w(colour("  INFRASTRUCTURE AS CODE AUDIT (challenge build)", "BOLD", use_colour))
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
        "audit": "iac_audit_challenge",
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
        prog="iac_audit_challenge.py",
        description=(
            "Audit Terraform configuration on disk — and optionally the live "
            "state bucket behind it — for hardcoded credentials, missing "
            "backends, unpinned providers, unprotected stateful resources and "
            "the other faults that are cheap to fix before apply and expensive "
            "afterwards."
        ),
        epilog=(
            "Examples:\n"
            "  iac_audit_challenge.py --path ../terraform\n"
            "  iac_audit_challenge.py --path ../terraform --profile bootcamp "
            "--state-bucket cbc-day05-tfstate-a1b2c3\n"
            "  iac_audit_challenge.py --path . --format json --quiet > findings.json\n"
            "  iac_audit_challenge.py --path . --min-severity HIGH --format csv\n"
            "  iac_audit_challenge.py --path . --fail-on CRITICAL   # exit 1 on any CRITICAL\n"
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
