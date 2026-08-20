#!/usr/bin/env python3
"""
generate_challenge.py — deterministic challenge scaffold generator.

Reads capstone_audit.py, strips every check function body, keeps the docstring
in place as the specification, and writes capstone_audit_challenge.py next to
itself. Nothing else in the file is edited: same imports, same Finding, same
helpers, same CHECKS registry, same renderers, same CLI.

Usage (from lab/python/):

    python3 challenge/generate_challenge.py           # write capstone_audit_challenge.py
    python3 challenge/generate_challenge.py --check   # verify existing file matches

The point of running this rather than hand-copying is drift. When capstone_audit.py
changes signature or docstring on any check, this script re-emits the
challenge with the new specification and the tests pass again against the
reference; the challenge stays authoritative because it was regenerated from
truth rather than edited.

DESIGN NOTE. The stub bodies are deliberately uniform — one TODO comment plus
`findings: List[Finding] = []; return findings`. On Day 08 the stubs are hand-
crafted, with per-check hints. That was worth the effort at the end of the
series where the pattern was still new. By Day 09 the pattern is established:
read the docstring above, look at the shape of the reference's other checks,
write the body. The uniform stub is honest about that.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from typing import List


HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.abspath(os.path.join(HERE, "..", "capstone_audit.py"))
TARGET = os.path.join(HERE, "capstone_audit_challenge.py")


HEADER_BANNER = '''#!/usr/bin/env python3
"""
capstone_audit_challenge.py — Day 10 capstone auditor, for you to finish.

CareerByteCode · Enterprise AWS Cloud Architecture & AIOps Bootcamp

GENERATED FROM capstone_audit.py. Identical imports, identical Finding, identical
helpers, identical renderers, identical collector, identical CLI. Sixteen check
bodies have been removed and their DOCSTRINGS LEFT IN PLACE, because the
docstring is the specification. Read it before you write anything.

    cd lab/python
    CAPSTONE_AUDIT_MODULE=capstone_audit_challenge PYTHONPATH=challenge \\\\
      python3 -m unittest discover -s tests -v

47 tests. They need no AWS credentials, because every check is a pure function
over a plain dict. Aim for all 47 green; get there one CHECKPOINT at a time.

Roughly 2-3 HOURS if you work through it in order. The long ones are CAP-006
(the cross-day ARN correlation), CAP-007 (finding-normalisation drift across
consecutive reports), CAP-012 (parsing suppressions and comparing review_by
dates against the injected clock), and CAP-016 (the CRITICAL check that is
the day's thesis and whose logic is the smallest, cleanly separating the
mechanism from the message).

-----------------------------------------------------------------------------
WHICH CHECKS ARE NOT INDEPENDENT
-----------------------------------------------------------------------------
Six relationships. Writing them down is what stops them reading as bugs when a
test fails for a reason that is not in the check you just wrote.

  CAP-001 AND CAP-003 LOOK LIKE THE SAME CHECK. They are not. CAP-001 asks
  whether a schedule EXISTS AT ALL. CAP-003 asks whether an existing schedule
  has STOPPED FIRING (last report age > interval * 1.5). Fixing CAP-001 by
  creating a rule and then never verifying it fires is exactly what people
  do, and CAP-003 is the transition catcher. In STATE A CAP-001 fires and
  CAP-003 is silent; in STATE C CAP-001 is silent and CAP-003 fires. The two
  sides of the same governance failure at two different lifecycles.

  CAP-003 AND CAP-016 ARE THE SAME PATTERN AT TWO LAYERS. CAP-003 asks
  "did the runner run"; CAP-016 asks "did anyone read what it produced".
  Both are activity checks. A programme where CAP-003 is silent and CAP-016
  is firing is one where the runner is doing its job and nobody is doing
  theirs. Neither remediates the other.

  CAP-004 AND CAP-005 FIRE ON THE SAME BUCKET at different angles.
  CAP-004 (versioning) is the "can I answer WHEN did this finding first
  appear" question. CAP-005 (lifecycle) is the "will this archive cost me
  money forever" question. Same resource, different remediations, both
  toggled by separate variables in terraform.tfvars.

  CAP-008 AND CAP-012 ARE A LIFECYCLE. CAP-008 fires when there is no
  suppression file at all. CAP-012 fires when the exceptions are present
  but stale. On a mature account, CAP-008 fires briefly after the first
  audit and never again; CAP-012 fires on cadence as review_by dates
  expire. If your CAP-012 fires on a review_by that is IN THE FUTURE, you
  compared the sign wrong.

  CAP-009 AND CAP-016 ARE SYMMETRIC ACROSS LAYERS. CAP-009 is technical
  (runner errors silently, no alarm). CAP-016 is organisational (reports
  succeed, nobody reads). Both are "the loop is broken" faults. CAP-009
  is HIGH; CAP-016 is CRITICAL because the organisational failure is
  strictly worse than the technical one.

  CAP-016 FIRES ONCE PER UNREAD REPORT, DELIBERATELY NOT DEDUPLICATED.
  Each report is a separately owned acknowledgement decision (or non-
  decision). If your CAP-016 returns one finding for an archive with 47
  unread reports, you deduplicated. STATE C's expected count is FOUR (one
  per weekly report unread over a month).

  CAP-013 AND CAP-014 ARE SILENT BY DESIGN against this stack and must
  stay silent against every fixture that uses the base_stack() fixture.
  If they fire, either you read sla_days_by_severity in a shape that is
  not what the object literal produces (CAP-013), or you set
  reference_arch_enabled = True in the fixture (CAP-014). The 47 tests
  include explicit silent-by-design invariants for both.

-----------------------------------------------------------------------------
FILE LAYOUT
-----------------------------------------------------------------------------
Above the check functions: imports, Finding, paginate helper, the constants
(RT_*, SEVERITY_*, REQUIRED_SLA_KEYS), the shared derivations (_now,
_parse_time, _age_days, _humanise_days, _parse_schedule_interval_days,
_reports_by_day).
YOU DO NOT NEED TO CHANGE ANY OF THIS. They are complete.

Below the check functions: CHECKS registry, RUNTIME_CHECKS list, scoring
functions, renderers, CapstoneAuditor collector, CLI. All complete.

The sixteen check functions are the whole exercise.
"""

'''


def _extract_function_source(source: str, node: ast.FunctionDef) -> str:
    """Return the source lines for a function, from the def line through the
    last line of its docstring, inclusive of decorators if any.

    We don't use ast.get_source_segment because we want to KEEP the
    docstring literal and REPLACE the code beneath it, not extract the whole
    node."""
    lines = source.splitlines(keepends=True)
    # Find the docstring node.
    docstring_node = None
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        docstring_node = node.body[0]

    start_line = node.lineno - 1  # 0-indexed
    # Include decorator lines above.
    if node.decorator_list:
        start_line = min(d.lineno for d in node.decorator_list) - 1

    if docstring_node is not None:
        end_line = docstring_node.end_lineno  # inclusive, 1-indexed
    else:
        # No docstring; keep just the def line.
        end_line = node.lineno

    return "".join(lines[start_line:end_line])


def _stub_body(check_id: str, index: int) -> str:
    return (
        "    # " + "=" * 71 + "\n"
        f"    # TODO {index} of 16 — {check_id}\n"
        "    # " + "=" * 71 + "\n"
        "    #\n"
        f"    # READ the docstring above. It is the specification for {check_id} —\n"
        "    # what the finding must say, which stack keys to read, what the\n"
        "    # severity is, and why the check exists.\n"
        "    #\n"
        "    # Look at the reference finder in capstone_audit.py's other checks for\n"
        "    # the shape:\n"
        f"    #     check_id=\"{check_id}\"\n"
        "    #     severity=... (from the docstring)\n"
        "    #     resource_type=RT_...  (already defined at module scope)\n"
        "    #     resource_id=the AWS id, or ARN, or name\n"
        "    #     title=one imperative line\n"
        "    #     detail=names the concrete values you observed\n"
        "    #     remediation=exact CLI or Terraform to fix it\n"
        "    #     evidence={dict of the fields you looked at}\n"
        "    #     region=the region argument (\"\" for account-global findings)\n"
        "    #\n"
        "    # When the test for this check passes AND the whole-stack total is\n"
        "    # still wrong, look at the checks it interacts with (see the top of\n"
        "    # this file), not at this one.\n"
        "    # " + "=" * 71 + "\n"
        "\n"
        "    findings: List[Finding] = []\n"
        "    return findings\n"
    )


def _get_check_functions(tree: ast.Module) -> List[ast.FunctionDef]:
    """Every top-level function whose name starts with 'check_'."""
    return [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("check_")
    ]


def _get_check_id_from_docstring(node: ast.FunctionDef) -> str:
    """The check ID is the first token of the docstring after the leading
    dedent — e.g. 'CAP-001 - the account has no AWS Budget at all.'"""
    doc = ast.get_docstring(node) or ""
    first_word = doc.strip().split()[0] if doc.strip() else ""
    if first_word.startswith("CAP-"):
        return first_word.rstrip(":,.")
    return "CAP-XXX"


def build_challenge(source: str) -> str:
    """Return the full text of capstone_audit_challenge.py."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    # We'll splice: everything before the first check function verbatim,
    # then for each check function, the def+docstring, then the stub body,
    # then a blank line; then everything after the last check function
    # verbatim (from just after the last check function's end).
    checks = _get_check_functions(tree)
    if not checks:
        raise SystemExit("No check_* functions found in reference — bug in generator.")

    first_start = checks[0].lineno - 1
    last_end = checks[-1].end_lineno  # 1-indexed, exclusive when sliced [x:]

    preamble = "".join(lines[:first_start])
    postamble = "".join(lines[last_end:])

    # Strip the reference's module docstring from the preamble so we can put
    # the challenge's own docstring in front.
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc is not None:
        # Find where the module docstring ends. In an AST-parsed module, the
        # first Expr node containing a string constant IS the docstring.
        first_node = tree.body[0] if tree.body else None
        if (
            isinstance(first_node, ast.Expr)
            and isinstance(first_node.value, ast.Constant)
            and isinstance(first_node.value.value, str)
        ):
            # Skip lines up to and including the docstring's end.
            doc_end = first_node.end_lineno  # 1-indexed inclusive
            # Preserve anything above the docstring (shebang, etc.) - none in
            # our case, but be safe. Then skip through doc_end.
            shebang_lines = []
            for ln in lines[: first_node.lineno - 1]:
                shebang_lines.append(ln)
            preamble_after_doc = "".join(lines[doc_end:first_start])
            preamble = "".join(shebang_lines) + preamble_after_doc

    out = [HEADER_BANNER, preamble]

    for index, node in enumerate(checks, start=1):
        check_id = _get_check_id_from_docstring(node)
        signature_and_doc = _extract_function_source(source, node)
        if not signature_and_doc.endswith("\n"):
            signature_and_doc += "\n"
        out.append(signature_and_doc)
        out.append(_stub_body(check_id, index))
        out.append("\n")

    out.append(postamble)
    text = "".join(out)

    # Ensure exactly one trailing newline.
    while text.endswith("\n\n"):
        text = text[:-1]
    if not text.endswith("\n"):
        text += "\n"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if capstone_audit_challenge.py differs from what would be regenerated.",
    )
    args = parser.parse_args()

    with open(REFERENCE, encoding="utf-8") as fh:
        source = fh.read()

    generated = build_challenge(source)

    if args.check:
        try:
            with open(TARGET, encoding="utf-8") as fh:
                existing = fh.read()
        except FileNotFoundError:
            print(f"MISSING: {TARGET}", file=sys.stderr)
            return 1
        if existing != generated:
            print(
                f"DRIFT: {TARGET} does not match what would be regenerated "
                f"from {REFERENCE}. Re-run without --check to update.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {TARGET} is up to date with {REFERENCE}.")
        return 0

    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(generated)
    # Verify the generated file is syntactically valid.
    try:
        ast.parse(generated)
    except SyntaxError as exc:
        print(f"GENERATED FILE HAS SYNTAX ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {TARGET} ({len(generated.splitlines())} lines).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
