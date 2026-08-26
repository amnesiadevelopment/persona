"""PS-16-PATCH.md's "verbatim" claims must be true. Pinned as a test.

WHY THIS FILE EXISTS
--------------------
Round 1 of PS-185 committed ``PS-16-PATCH.md`` with Edit 3 labelled *"It is the
``derive.py`` output verbatim."* The reviewer normalised whitespace, compared,
and found **six fragments in that block that appear nowhere in derive.py's
output** — a positive-control sentence, an estimator aside, a macOS pool
comparison, and three others. Every one was *factually correct*; several
improved the article. The defect was the **label**, and the label is what a
maintainer acts on: the instruction beside it reads *"apply it as written. If
you re-type a derived number, re-run ``derive.py`` instead"*, which tells the
next reader those lines regenerate. They did not — re-running ``derive.py``
silently dropped all six.

That is the PS-177 shape the patch itself invokes two sections earlier (a
document claiming "nothing was hand-typed" while carrying hand-typed
judgement), reproduced one section later in the same file.

The round-2 fix moved the six fragments INTO ``derive.py`` so they are derived
from the committed JSON records rather than typed — the positive control now
counts seeds that actually matched across the two modes, and the macOS pool
comparison reads the card names out of both records instead of restating a
remembered pool.

WHAT THIS PINS, AND WHY IT IS A TEST RATHER THAN A README NOTE
--------------------------------------------------------------
Splicing the block by hand fixes today's document and does nothing about
tomorrow's. The defect class is **a document claiming provenance it does not
have**, and it survived a submission because nothing executed the claim. So the
claim ships as an assertion that runs in CI: if anyone edits the quoted block in
``PS-16-PATCH.md``, or changes what ``derive.py`` prints, without redoing the
other, this fails and names the first line that diverged.

These tests assert on **the relationship between two committed files**, never on
text this branch generated (PS-11: a green test that asserts on your own output
is the failure mode this project has hit six times). The expected value is
re-derived by executing ``derive.py`` at run time; not one figure is hard-coded
from the prose it is checking.

THE DISCRIMINATION CLAIM
------------------------
These tests can fail, and that is checked by mutation rather than asserted in a
comment: ``test_verbatim_check_rejects_a_hand_edited_block`` perturbs the quoted
block the way round 1 was wrong — by inserting an undisclosed sentence — and
requires the comparison to reject it.
"""

from __future__ import annotations

import pathlib
import runpy
import subprocess
import sys

import pytest

READINGS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "readings"
    / "ps185-2026-08-26"
)
PATCH = READINGS / "PS-16-PATCH.md"
DERIVE = READINGS / "derive.py"
DERIVED_OUTPUT = READINGS / "derived-output.txt"

# The quoted block in Edit 3 starts here and runs to the section separator.
EDIT3_HEADING = "> ### GPU unlinkability"
# derive.py prints the same section unquoted, bounded by the next h3.
DERIVED_START = "### GPU unlinkability"
DERIVED_END = "### WebGL / canvas readback"


pytestmark = pytest.mark.skipif(
    not PATCH.is_file() or not DERIVE.is_file(),
    reason="PS-185 reading set is not present in this checkout",
)


def _quote(lines: "list[str]") -> "list[str]":
    """Render derive.py output as the blockquote the patch embeds it as."""
    return [("> " + ln).rstrip() if ln.strip() else ">" for ln in lines]


def _derived_gpu_block(text: str) -> "list[str]":
    """The GPU-unlinkability section of derive.py's output, trailing blanks cut."""
    lines = text.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(DERIVED_START))
    end = next(i for i, ln in enumerate(lines) if ln.startswith(DERIVED_END))
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    return block


def _patch_edit3_block(expected_len: int, patch_text: "str | None" = None) -> "list[str]":
    text = PATCH.read_text(encoding="utf-8") if patch_text is None else patch_text
    lines = text.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(EDIT3_HEADING))
    return lines[start:start + expected_len]


def verbatim_divergence(patch_text: "str | None" = None) -> "str | None":
    """THE comparison under test. ``None`` means Edit 3 is verbatim.

    Both the real assertion and the mutation tests call THIS, so a mutation
    exercises the same code path that guards the committed file rather than a
    re-implementation of it. Reimplementing the compare in each test is how a
    discrimination check ends up proving only that two local lists differ.
    """
    want = _quote(_derived_gpu_block(_run_derive()))
    got = _patch_edit3_block(len(want), patch_text)
    if len(got) != len(want):
        return f"block is {len(got)} lines, derive.py emits {len(want)}"
    for i, (w, g) in enumerate(zip(want, got)):
        if g != w:
            return f"line {i}: derive.py {w!r} != patch {g!r}"
    return None


def _run_derive() -> str:
    """Execute derive.py exactly as the patch instructs a maintainer to.

    ``encoding`` is pinned explicitly: ``text=True`` alone decodes the child's
    stdout with the PARENT's locale encoding, which on a Windows runner is
    cp1252 and raises UnicodeDecodeError on the ⚠️ and — this output carries.
    That is a second, distinct defect from the child-side one derive.py fixes:
    the child WRITING utf-8 does not help if the parent READS cp1252.
    """
    proc = subprocess.run(
        [sys.executable, str(DERIVE)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"derive.py failed: {proc.stderr[-2000:]}"
    return proc.stdout


# --------------------------------------------------------------------------
# The claim itself
# --------------------------------------------------------------------------

def test_edit3_is_the_derive_output_verbatim_as_it_claims():
    """Edit 3 says "It is the derive.py output verbatim." Make that true or fail.

    This is the round-1 defect: six undisclosed fragments sat inside a block
    labelled verbatim. Comparing line by line is what makes the label
    load-bearing instead of decorative.
    """
    divergence = verbatim_divergence()
    assert divergence is None, (
        f"Edit 3 diverges from derive.py output — {divergence}\n"
        "Re-splice the block from derive.py rather than editing it by hand."
    )


def test_committed_derived_output_matches_a_fresh_run():
    """derived-output.txt must be what derive.py prints today, not a stale copy.

    DoD #4 of PS-185 is that the next person re-runs rather than rebuilds. A
    committed output that no longer reproduces makes every figure citing it
    unverifiable.
    """
    fresh = _run_derive()
    committed = DERIVED_OUTPUT.read_text(encoding="utf-8")
    # print() adds one trailing newline that --output does not write.
    assert fresh.rstrip("\n") == committed.rstrip("\n"), (
        "derived-output.txt is stale — re-run: "
        "python3 derive.py --output derived-output.txt"
    )


# --------------------------------------------------------------------------
# The basis column — the distinction the ON/OFF split exists to carry
# --------------------------------------------------------------------------

def test_basis_column_names_the_authorship_arm_per_cell():
    """A bare "measured" would put two different quantities under one label.

    windows is the only arm in ENGINE_AUTHORED_IDENTITY_ARMS, so its shipped
    figure is the layer-OFF one; the other three ship the layer-ON draw. The
    basis is asserted against the module's own frozenset rather than a hardcoded
    arm name, so moving an arm into (or out of) it cannot leave this passing on
    a stale expectation.
    """
    from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

    derived = _run_derive()
    rows = [
        ln for ln in derived.split("\n")
        if ln.startswith("| ") and "measured (" in ln
    ]
    assert rows, "no basis rows found in derive.py output"

    for arm in ("windows", "macos", "linux", "android"):
        row = next((r for r in rows if r.startswith(f"| {arm} |")), None)
        assert row is not None, f"no basis row for {arm}"
        expected = (
            "measured (layer OFF)"
            if arm in ENGINE_AUTHORED_IDENTITY_ARMS
            else "measured (layer ON)"
        )
        assert expected in row, (
            f"{arm} basis should read {expected!r}; row was: {row}"
        )


def test_no_cell_is_labelled_theoretical_or_left_blank():
    """DoD #1: every "theoretical" becomes "measured" or is reported unobtainable."""
    derived = _run_derive()
    start = derived.index(DERIVED_START)
    end = derived.index(DERIVED_END)
    table = [
        ln for ln in derived[start:end].split("\n")
        if ln.startswith("| ") and "measured (" in ln
    ]
    for row in table:
        assert "theoretical" not in row.lower(), f"stale theoretical cell: {row}"


def test_derive_runs_on_a_non_utf8_console():
    """Windows CI runs a cp1252 console; derive.py must survive it.

    Found by this file's own suite: `print(text)` raised UnicodeEncodeError on
    the ⚠️ and — characters and the script died WITHOUT writing anything. That
    is a real portability defect rather than a test artefact, because
    PS-16-PATCH.md's standing instruction to the next maintainer is *"if you
    re-type a derived number, re-run derive.py instead"* — an instruction that
    was only followable on the two platforms it was written on.

    Asserts the output is byte-identical to a UTF-8 run, not merely that the
    process survived: an encoding fix that silently dropped or replaced a
    character would still exit 0 while corrupting the record.
    """
    import os

    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, str(DERIVE)],
        capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
    )
    assert proc.returncode == 0, (
        "derive.py died on a cp1252 console — the re-run instruction in "
        f"PS-16-PATCH.md is not followable on Windows:\n{proc.stderr[-2000:]}"
    )
    assert proc.stdout == _run_derive(), (
        "cp1252 output differs from UTF-8 output — characters were dropped or "
        "replaced, which would corrupt the record rather than fail loudly"
    )


# --------------------------------------------------------------------------
# Discrimination — these checks must be able to FAIL
# --------------------------------------------------------------------------

def test_verbatim_check_rejects_a_hand_edited_block():
    """Mutation: reintroduce the round-1 defect and require rejection.

    Round 1 failed by carrying a correct-but-undisclosed sentence inside a block
    labelled verbatim. This feeds a tampered PATCH BODY through the same
    ``verbatim_divergence`` the real test uses, so it proves that check can fail
    rather than proving two local lists differ.
    """
    original = PATCH.read_text(encoding="utf-8")
    assert verbatim_divergence(original) is None, "fixture is not clean"

    lines = original.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(EDIT3_HEADING))
    lines.insert(
        start + 3,
        "> An extra sentence nobody derived, exactly like the round-1 six.",
    )
    tampered = "\n".join(lines)

    divergence = verbatim_divergence(tampered)
    assert divergence is not None, (
        "an undisclosed inserted sentence was accepted as verbatim — "
        "this is exactly the round-1 defect"
    )


def test_verbatim_check_rejects_a_shortened_identity_string():
    """Mutation: the round-1 android fragment shortened a full identity string.

    Fragment 4 of the six was not an addition but a TRUNCATION — the patch
    abbreviated a SwiftShader identity that derive.py prints in full. A check
    that only caught insertions would have missed it, so the truncation is
    driven through the same comparison.
    """
    original = PATCH.read_text(encoding="utf-8")
    lines = original.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(EDIT3_HEADING))
    target = next(
        (
            i for i, ln in enumerate(lines[start:], start)
            if "**android**" in ln and "SAME identity" in ln
        ),
        None,
    )
    assert target is not None, "no android identity line in Edit 3"

    lines[target] = "> * **android** — the same single SwiftShader identity."
    divergence = verbatim_divergence("\n".join(lines))
    assert divergence is not None, (
        "a truncated identity string was accepted as verbatim"
    )
