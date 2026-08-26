"""Splice the derived blocks of ``PS-16-PATCH.md`` from ``derive.py``.

WHY THIS EXISTS
---------------
``PS-16-PATCH.md`` quotes ``derive.py``'s output in two places, and both quotes
are load-bearing claims about provenance:

* **Edit 3** embeds the whole GPU-unlinkability section, labelled *"It is the
  ``derive.py`` output verbatim."*
* **Edit 8** embeds the **sample-completeness statement** — the claim that the
  sweep was not truncated.

Round 1 of PS-185 failed because Edit 3 carried six hand-added fragments under a
"verbatim" label. Round 3 failed because the completeness sentence was a string
literal that could not become false. Both defects have the same root: **a
human re-typing text that claims to be machine-derived.**

``tests/test_ps185_patch_provenance.py`` is what makes the claim enforceable —
it fails if either block drifts. This script is the other half: the mechanical
way to make them agree again, so the fix for a failing guard is *"re-splice"*
rather than *"hand-edit until the test goes green"*, which is how a verbatim
label rots in the first place.

Usage::

    python3 readings/ps185-2026-08-26/splice_patch.py          # rewrite in place
    python3 readings/ps185-2026-08-26/splice_patch.py --check  # report only
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
PATCH = HERE / "PS-16-PATCH.md"

# The patch file wraps its prose at roughly this width. Edit 3 is quoted at the
# generator's own line breaks; Edit 8's statement is a flowing paragraph, so it
# is re-wrapped to match the surrounding document.
WRAP = 98

COMPLETENESS_START = (
    "> No arm was recorded",
    "> ⚠️ **The sample is INCOMPLETE",
)
COMPLETENESS_END = "> Re-derive with `readings/ps185-2026-08-26/derive.py`"


def _load_derive():
    spec = importlib.util.spec_from_file_location("ps185_derive", HERE / "derive.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def derived_completeness() -> str:
    """The completeness statement, straight from the generator."""
    derive = _load_derive()
    off, on = derive.load(derive.LAYER_OFF), derive.load(derive.LAYER_ON)
    return derive.completeness_statement(off, on, [
        ("readback-vectors.three-seeds.json", derive.load(derive.READBACK)),
        ("readback-vectors.replicate.json", derive.load(derive.REPLICATE)),
        ("readback-vectors.replicate-chromium.json",
         derive.load(derive.REPLICATE_CHROME)),
    ])


def as_blockquote(statement: str, width: int = WRAP) -> "list[str]":
    """Render the statement as the wrapped blockquote the patch embeds."""
    out: "list[str]" = []
    for para in statement.split("\n"):
        if not para.strip():
            out.append(">")
            continue
        for line in textwrap.wrap(
            para, width=width, break_long_words=False, break_on_hyphens=False
        ):
            out.append("> " + line)
    return out


def splice(text: str, block: "list[str]") -> str:
    """Replace Edit 8's completeness block with ``block``."""
    lines = text.split("\n")
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith(COMPLETENESS_START)),
        None,
    )
    if start is None:
        raise SystemExit("could not find the completeness block in PS-16-PATCH.md")
    end = next(
        i for i, ln in enumerate(lines[start:], start)
        if ln.startswith(COMPLETENESS_END)
    )
    # Keep the blank quote line that separates the block from the re-derive note.
    lines[start:end - 1] = block
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report whether the patch is in sync; do not write")
    args = ap.parse_args(argv)

    current = PATCH.read_text(encoding="utf-8")
    wanted = splice(current, as_blockquote(derived_completeness()))

    if current == wanted:
        print("[splice] PS-16-PATCH.md completeness block is in sync")
        return 0
    if args.check:
        print("[splice] OUT OF SYNC — re-run without --check", file=sys.stderr)
        return 1
    PATCH.write_text(wanted, encoding="utf-8")
    print("[splice] rewrote the completeness block in PS-16-PATCH.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
