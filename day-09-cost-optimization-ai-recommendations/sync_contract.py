#!/usr/bin/env python3
"""
sync_contract.py — verify or write the LOCKED finding contract into all copies.

The finding contract is duplicated into five files by design so that a reader
of any one of them sees the totals, states, silent classifications and check
interactions in the same words. Duplication is a maintenance risk that this
script exists to make cheap: one command verifies drift, another command
resolves it in favour of the source of truth.

    python3 sync_contract.py            # verify all copies are in sync (default)
    python3 sync_contract.py --check    # same as above, explicit
    python3 sync_contract.py --write    # rewrite copies to match the source
    python3 sync_contract.py --show     # print the extracted source-of-truth

SOURCE OF TRUTH: the LOCKED contract block in
`lab/python/cost_audit.py`'s module docstring, delimited by two lines of 77
equal signs and beginning with 'DAY 09 FINDING CONTRACT — LOCKED AT CP2'.

The other four copies are:
    lab/terraform/outputs.tf              inside the `finding_contract` output
    lab/python/tests/test_checks.py       inside the module docstring
    README.md                             top-level, contract section
    lab/README.md                         lab-level, contract section

Each copy is bracketed by matching sentinel markers. On mismatch --check
exits 1 and prints the file(s) that drifted; --write overwrites the copy's
bracketed region with the source-of-truth text.

Two notes on scope:

  The four SILENT-BY-DESIGN, SILENT-BY-SITUATION and INTERACTIONS sections
  BELOW the four-state table are part of the block and are propagated too.
  They are as much a part of the contract as the numbers; a copy that has
  the numbers but omits the explanations has failed to communicate the
  contract even if it has succeeded in reproducing the counts.

  README.md and lab/README.md may not yet exist during early checkpoints,
  in which case the script warns rather than failing. Once they land in
  CP6 they are subject to the same rules.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


# Paths, resolved relative to this script's location so it works from any cwd.
HERE = os.path.dirname(os.path.abspath(__file__))

SOURCE_OF_TRUTH = os.path.join(HERE, "lab", "python", "cost_audit.py")

# The five copy sites. Each is (relative_path, pre_marker, post_marker).
# The markers must be present in the file BEFORE sync_contract.py can write
# to it; the script does not add them for you, so the CP6 templates
# introduce them.

# For the source file, we extract between the two equal-sign banners around
# the LOCKED contract; for the target files, we replace the region between
# a start marker and an end marker (both preserved verbatim).

SOURCE_BEGIN = "DAY 09 FINDING CONTRACT — LOCKED AT CP2"
BANNER = "=" * 77

# For target files:
MARK_BEGIN = "<!-- CONTRACT-BEGIN -->"
MARK_END = "<!-- CONTRACT-END -->"

# Python/Terraform-friendly variants (comment tokens differ).
MARK_BEGIN_PY_TF = "# CONTRACT-BEGIN"
MARK_END_PY_TF = "# CONTRACT-END"


@dataclass
class Copy:
    """A file the contract is duplicated into."""
    relative_path: str
    begin: str
    end: str
    prefix: str = ""  # A per-line prefix added to each line of the block on write.

    @property
    def absolute_path(self) -> str:
        return os.path.join(HERE, self.relative_path)


# The copies. README.md and lab/README.md come online at CP6.
COPIES: List[Copy] = [
    Copy(
        relative_path=os.path.join("lab", "terraform", "outputs.tf"),
        begin=MARK_BEGIN_PY_TF,
        end=MARK_END_PY_TF,
        prefix="    ",  # heredoc indentation.
    ),
    Copy(
        relative_path=os.path.join("lab", "python", "tests", "test_checks.py"),
        begin=MARK_BEGIN_PY_TF,
        end=MARK_END_PY_TF,
    ),
    Copy(
        relative_path="README.md",
        begin=MARK_BEGIN,
        end=MARK_END,
    ),
    Copy(
        relative_path=os.path.join("lab", "README.md"),
        begin=MARK_BEGIN,
        end=MARK_END,
    ),
]


###############################################################################
# Source-of-truth extraction
###############################################################################


def extract_source_of_truth(path: str) -> str:
    """Return the LOCKED contract block from cost_audit.py.

    Delimited by two lines of 77 equal signs on either side of a heading
    that includes SOURCE_BEGIN. Returns the text WITHOUT the delimiters and
    stripped of the docstring's baseline indentation.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    lines = text.splitlines()
    # Find the line containing SOURCE_BEGIN.
    header_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if SOURCE_BEGIN in line:
            header_idx = i
            break
    if header_idx is None:
        raise SystemExit(
            f"Could not locate '{SOURCE_BEGIN}' in {path}. The source-of-"
            f"truth file must contain the LOCKED contract heading."
        )

    # Find the equal-sign banners just above and below the block.
    top: Optional[int] = None
    for i in range(header_idx - 1, -1, -1):
        if lines[i].strip() == BANNER:
            top = i
            break
    if top is None:
        raise SystemExit(
            f"No opening banner ('{BANNER[:20]}...') above header in {path}."
        )

    bottom: Optional[int] = None
    # The contract block spans title BANNER + divider BANNER + body + closing
    # BANNER. Take the LAST BANNER within a reasonable window below the header
    # (up to end of file, since a docstring's contract block is bounded).
    for i in range(len(lines) - 1, header_idx, -1):
        if lines[i].strip() == BANNER:
            bottom = i
            break
    if bottom is None:
        raise SystemExit(
            f"No closing banner ('{BANNER[:20]}...') below header in {path}."
        )

    # Extract lines strictly between the banners. This includes the header
    # line and everything up to (but not including) the bottom banner.
    block_lines = lines[top + 1:bottom]

    # Strip common leading whitespace so we can re-indent per copy.
    if block_lines:
        non_blank = [ln for ln in block_lines if ln.strip()]
        if non_blank:
            common = min(len(ln) - len(ln.lstrip(" ")) for ln in non_blank)
            block_lines = [
                (ln[common:] if len(ln) >= common else ln)
                for ln in block_lines
            ]

    # Trim any leading/trailing blank lines.
    while block_lines and not block_lines[0].strip():
        block_lines.pop(0)
    while block_lines and not block_lines[-1].strip():
        block_lines.pop()

    return "\n".join(block_lines) + "\n"


###############################################################################
# Copy read/verify/write
###############################################################################


def find_bracketed_region(text: str, begin: str, end: str) -> Optional[Tuple[int, int, int, int]]:
    """Return (begin_line_end, end_line_start, begin_index, end_index) or
    None if the markers are not both present.

    begin_line_end   — character offset just AFTER the begin marker line
    end_line_start   — character offset at the START of the end marker line
    """
    begin_re = re.compile(r"^[ \t]*" + re.escape(begin) + r"[ \t]*$", re.MULTILINE)
    end_re = re.compile(r"^[ \t]*" + re.escape(end) + r"[ \t]*$", re.MULTILINE)

    m_begin = begin_re.search(text)
    if not m_begin:
        return None
    m_end = end_re.search(text, pos=m_begin.end())
    if not m_end:
        return None

    # Character offsets for splicing.
    begin_end = m_begin.end()
    if begin_end < len(text) and text[begin_end] == "\n":
        begin_end += 1
    end_start = m_end.start()
    return (begin_end, end_start, m_begin.start(), m_end.end())


def read_bracketed_region(text: str, begin: str, end: str) -> Optional[str]:
    region = find_bracketed_region(text, begin, end)
    if region is None:
        return None
    begin_end, end_start, _, _ = region
    return text[begin_end:end_start]


def apply_prefix(block: str, prefix: str) -> str:
    """Apply per-line prefix to each non-blank line of the block."""
    if not prefix:
        return block
    out_lines = []
    for line in block.splitlines(keepends=True):
        if line.strip():
            out_lines.append(prefix + line)
        else:
            out_lines.append(line)
    return "".join(out_lines)


def strip_prefix(block: str, prefix: str) -> str:
    """Reverse apply_prefix so we can compare normalised."""
    if not prefix:
        return block
    out_lines = []
    for line in block.splitlines(keepends=True):
        if line.startswith(prefix):
            out_lines.append(line[len(prefix):])
        else:
            out_lines.append(line)
    return "".join(out_lines)


def verify_copy(copy: Copy, source_block: str) -> Tuple[str, str]:
    """Return ("ok" | "drift" | "missing" | "no_markers", detail)."""
    if not os.path.exists(copy.absolute_path):
        return "missing", f"{copy.relative_path} does not exist yet"
    with open(copy.absolute_path, encoding="utf-8") as fh:
        text = fh.read()
    region = read_bracketed_region(text, copy.begin, copy.end)
    if region is None:
        return "no_markers", (
            f"{copy.relative_path} is missing the markers "
            f"'{copy.begin}' / '{copy.end}'"
        )
    got_normalised = strip_prefix(region, copy.prefix).strip("\n")
    want_normalised = source_block.strip("\n")
    if got_normalised == want_normalised:
        return "ok", copy.relative_path
    return "drift", copy.relative_path


def write_copy(copy: Copy, source_block: str) -> Tuple[bool, str]:
    """Rewrite the bracketed region. Returns (changed?, message)."""
    if not os.path.exists(copy.absolute_path):
        return False, f"skipped (does not exist yet): {copy.relative_path}"
    with open(copy.absolute_path, encoding="utf-8") as fh:
        text = fh.read()
    region = find_bracketed_region(text, copy.begin, copy.end)
    if region is None:
        return False, (
            f"skipped (markers missing): {copy.relative_path}. Add "
            f"'{copy.begin}' and '{copy.end}' on their own lines to enable "
            f"sync."
        )
    begin_end, end_start, _, _ = region
    new_block = apply_prefix(source_block, copy.prefix)
    if not new_block.endswith("\n"):
        new_block += "\n"
    new_text = text[:begin_end] + new_block + text[end_start:]
    if new_text == text:
        return False, f"already in sync: {copy.relative_path}"
    with open(copy.absolute_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return True, f"rewrote: {copy.relative_path}"


###############################################################################
# CLI
###############################################################################


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", default=True,
                      help="Verify all copies match the source-of-truth (default).")
    mode.add_argument("--write", action="store_true",
                      help="Overwrite copies to match the source-of-truth.")
    mode.add_argument("--show", action="store_true",
                      help="Print the extracted source-of-truth to stdout and exit.")
    args = parser.parse_args()

    source_block = extract_source_of_truth(SOURCE_OF_TRUTH)

    if args.show:
        sys.stdout.write(source_block)
        return 0

    if args.write:
        rc = 0
        for copy in COPIES:
            changed, message = write_copy(copy, source_block)
            marker = "CHANGED" if changed else "unchanged"
            print(f"  [{marker}] {message}")
        return rc

    # --check (default)
    rc = 0
    for copy in COPIES:
        status, detail = verify_copy(copy, source_block)
        if status == "ok":
            print(f"  [OK]        {detail}")
        elif status == "missing":
            print(f"  [SKIPPED]   {detail} (expected during early checkpoints)")
        elif status == "no_markers":
            print(f"  [SKIPPED]   {detail}")
        elif status == "drift":
            print(f"  [DRIFT]     {detail}")
            rc = 1
    if rc == 0:
        print("\nAll copies in sync with source-of-truth.")
    else:
        print(
            "\nDRIFT DETECTED. Run `python3 sync_contract.py --write` to "
            "propagate the source-of-truth from cost_audit.py."
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
