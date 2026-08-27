"""Guard: no text decode in this repo may depend on the platform's locale.

PS-184. An unnamed text encoding resolves via locale.getpreferredencoding(False)
-> utf-8 on Linux/macOS, cp1252 on Windows.  The product writes with an explicit
encoding="utf-8", so an unnamed READ disagrees with it, and the disagreement is
invisible until a non-ASCII byte lands in an emitted artifact.  PS-161: a single
U+FE0F failed four tests on windows-latest and passed on every other runner.

DEFECT CLASS: "a read whose encoding is platform-dependent, against a write that
is not."

This guard exists because the sweep that fixed 323 call sites cannot stop the
324th from being written tomorrow.  A comment asking authors to remember is not
a guard; this fails the suite instead.

WHY IT IS AN AST WALK AND NOT A GREP -- three ways a grep gets this wrong:

  1. A call spans lines, so `encoding=` routinely sits on a line other than
     `text=True`.  src/services/browser/process.py is the proof: `text=True` on
     one line, `encoding="utf-8"` on the next.  A line-based `grep -v encoding=`
     both misses correct sites and reports false ones.
  2. `open` is not one function.  os.open() returns a raw fd; tarfile, zipfile
     and gzip .open() are archive APIs; urllib opener.open() yields bytes.  None
     of them decode text, and a name match cannot tell them apart.
  3. capture_output=True ALONE returns bytes and is NOT in the class.  Only
     text=True / universal_newlines=True decode.

This guard was verified by INJECTING a bare read and watching it go red, not by
observing it pass on a clean tree (PS-11: a check that could not have failed is
not coverage).  If you change the detector, re-run that proof.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from encoding_audit import find  # noqa: E402  the ONE definition of the class


def _scan(rel: str):
    """Every non-ambiguous hit under `rel`, as (relative_path, lineno, what)."""
    hits = []
    for path in sorted((REPO_ROOT / rel).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, _col, _el, _ec, what, why in find(path):
            if "AMBIGUOUS" in why:
                continue
            hits.append((path.relative_to(REPO_ROOT).as_posix(), lineno, what))
    return hits


def test_no_platform_dependent_text_decode_in_tests():
    """tests/ must stay at zero.  This is the sweep's ratchet."""
    hits = _scan("tests")
    assert hits == [], (
        f"{len(hits)} text decode(s) in tests/ do not name an encoding, so they "
        f"resolve to cp1252 on Windows while the product writes utf-8:\n"
        + "\n".join(f"  {p}:{ln}: {what}" for p, ln, what in hits)
        + "\n\nAdd encoding=\"utf-8\" to the call.  On a site that genuinely "
        "reads bytes, use a binary mode instead."
    )


# --- product side -------------------------------------------------------
#
# src/ is NOT clean, and this guard deliberately does not pretend otherwise.
# These are a SEPARATE finding (a shipped bug, not a test bug) and fixing them
# is out of scope for PS-184 -- see the finding filed against this ticket.
# The frozen set stops the population GROWING while that is decided.
#
# Frozen by FILE, not by line number, so ordinary edits above a call site do
# not fail the suite for an unrelated reason.
KNOWN_PRODUCT_FILES = {
    # 7 subprocess(text=True) sites: certutil (NSS), pgrep, ps, and -- the one
    # that actually bites on the owner's platform -- two PowerShell calls whose
    # stdout is decoded under cp1252 on Windows.
    "src/services/browser/invisible_launch.py",
    # open(path, "a+") that never reads: seek/truncate//write of an ASCII pid.
    # Kept in the frozen set rather than silently excused -- it is in the shape
    # even though it cannot currently misdecode.
    "src/core/single_instance.py",
}


def test_product_side_platform_dependent_decodes_do_not_grow():
    """A new bare decode in src/ is a SHIPPED bug and must not slip in quietly."""
    offenders = {p for p, _ln, _what in _scan("src")}
    new = offenders - KNOWN_PRODUCT_FILES
    assert not new, (
        "New platform-dependent text decode(s) in PRODUCT code: "
        + ", ".join(sorted(new))
        + "\nThis ships to users on Windows. Name encoding=\"utf-8\" on the call."
    )


def test_the_guard_actually_detects_a_bare_read(tmp_path):
    """Prove the detector fires, so this file cannot pass vacuously.

    PS-11: a guard observed passing on a clean tree has not been shown to work.
    If the detector ever silently stops matching, the two tests above would go
    green forever and the sweep would rot without anyone noticing.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import pathlib, subprocess\n"
        "pathlib.Path('a').read_text()\n"                      # bare read
        "open('b').read()\n"                                   # bare text open
        "subprocess.run(['x'], text=True)\n"                   # locale-decoded
        "pathlib.Path('c').read_text(encoding='utf-8')\n"      # correct
        "open('d', 'rb').read()\n"                             # binary, correct
        "subprocess.run(['y'], capture_output=True)\n",        # bytes, correct
        encoding="utf-8",
    )
    flagged = {(ln, what) for ln, _c, _el, _ec, what, why in find(sample)
               if "AMBIGUOUS" not in why}
    assert {ln for ln, _ in flagged} == {2, 3, 4}, (
        f"detector did not flag exactly the three defective lines: {sorted(flagged)}"
    )


def test_the_guard_does_not_flag_an_already_correct_call(tmp_path):
    """The **kwargs shape must NOT be flagged.

    A `**{"encoding": "utf-8"}` entry has arg=None, so the absence of a keyword
    literally named `encoding` does not mean none was passed.  Adding one raises
    "got multiple values for keyword argument 'encoding'" -- which is exactly
    how this sweep broke tests/test_locale_ext.py before the detector learned
    the shape.
    """
    sample = tmp_path / "kw.py"
    sample.write_text(
        "import pathlib\n"
        "_UTF8 = {'encoding': 'utf-8'}\n"
        "pathlib.Path('a').read_text(**_UTF8)\n",
        encoding="utf-8",
    )
    assert [h for h in find(sample) if "AMBIGUOUS" not in h[5]] == []
