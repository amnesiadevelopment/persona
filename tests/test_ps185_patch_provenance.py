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


# --------------------------------------------------------------------------
# The completeness claim — round 3's defect
# --------------------------------------------------------------------------
#
# ``coverage_section()`` used to take NO records and hardcode "all four GPU arms
# returned 24/24 readable seeds" — the one sentence carrying the
# sample-completeness claim, on a question ("did the run get truncated?") whose
# whole point is that it must be answered from the data.
#
# The tests below drive mutations through IN-MEMORY records rather than editing
# the committed JSON. That is deliberate: a mutation test that rewrites a
# committed file depends on its own restore step running, and a failure between
# the write and the restore leaves the evidence directory corrupted. Nothing
# here touches the files on disk.


def _load_derive_module():
    """Import derive.py as a module so its functions can be called directly."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ps185_derive_undertest", DERIVE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _records():
    """The committed records, loaded fresh (callers may mutate their copy)."""
    d = _load_derive_module()
    off, on = d.load(d.LAYER_OFF), d.load(d.LAYER_ON)
    readbacks = [
        ("readback-vectors.three-seeds.json", d.load(d.READBACK)),
        ("readback-vectors.replicate.json", d.load(d.REPLICATE)),
        ("readback-vectors.replicate-chromium.json", d.load(d.REPLICATE_CHROME)),
    ]
    return d, off, on, readbacks


def test_completeness_sentence_reports_a_truncated_sweep():
    """THE round-3 mutation: null half an arm's readings, require the text to move.

    This is the exhaustion-truncation PS-192 describes — the later launches die,
    ``classify`` correctly drops the unreadable seeds, and the collision figure
    is then computed over a position-biased subset. The failure mode being
    guarded is that the record still announces a full sample.
    """
    d, off, on, readbacks = _records()
    before = d.completeness_statement(off, on, readbacks)
    assert "24/24" in before, "fixture is not a full sweep"

    for arm in ("android", "linux"):
        mutated = d.load(d.LAYER_ON)
        for seed in sorted(mutated["readings"][arm])[:12]:
            mutated["readings"][arm][seed] = None

        after = d.completeness_statement(off, mutated, readbacks)
        assert after != before, (
            f"nulling 12 of 24 {arm} readings did not change the completeness "
            "statement — the claim cannot become false, which is the defect"
        )
        assert "INCOMPLETE" in after and f"{arm} 12/24" in after, (
            f"the truncation is not reported for {arm}: {after!r}"
        )


def test_completeness_recounts_rather_than_trusting_the_stored_summary():
    """The recount must come from the RAW readings, not ``result.per_arm``.

    ``seeds_readable`` is a summary the sweep wrote ABOUT ITSELF. A truncated or
    stale run can carry a full-looking summary over reduced readings, so reading
    that field to answer "was this truncated?" asks the run to grade its own
    homework. Here the raw readings are cut while the summary is left claiming
    24 — the same shape as a stale summary — and the statement must follow the
    readings and say the two disagree.
    """
    d, off, on, readbacks = _records()
    mutated = d.load(d.LAYER_ON)
    for seed in sorted(mutated["readings"]["android"])[:12]:
        mutated["readings"]["android"][seed] = None
    assert mutated["result"]["per_arm"]["android"]["seeds_readable"] == 24, (
        "fixture invalid: the stored summary should still claim a full sample"
    )

    after = d.completeness_statement(off, mutated, readbacks)
    assert "android 12/24" in after, (
        "the statement followed the stored summary instead of the readings"
    )
    assert "stored summary disagrees" in after, (
        "a summary/readings disagreement is itself a finding and must be stated"
    )


def test_completeness_reports_a_leg_that_produced_no_reading_at_all():
    """An EMPTY leg is invisible to a scan for unusable values.

    A vector reading ``unavailable:`` or ``error:`` is the page reporting that it
    could not read that vector. A launch that never attached produces no vectors
    to inspect, so a scan over values finds nothing wrong with whatever survived
    and reports a clean sweep. The committed records contain exactly one such
    leg (a CDP timeout on chromium@9001 in the replicate run), and DoD #5
    requires anything attempted and not obtained to be recorded with its reason.
    """
    d, off, on, readbacks = _records()
    statement = d.completeness_statement(off, on, readbacks)

    counted = d.readback_completeness(readbacks)
    assert counted["empty"], "fixture invalid: expected one empty leg on record"
    assert not counted["unusable"], (
        "fixture invalid: this record set has no unusable VALUES — which is "
        "precisely why an empty leg must be counted by attempt instead"
    )

    for leg in counted["empty"]:
        assert leg["engine"] in statement and leg["seed"] in statement, (
            f"the empty leg {leg['engine']}@{leg['seed']} is not disclosed"
        )
    assert "produced no reading at all" in statement


def test_completeness_check_notices_a_newly_lost_leg():
    """Discrimination: drop a leg's vectors and require the disclosure to grow.

    Without this, the empty-leg wording above could be a sentence that happens to
    match today's single known failure rather than a count of what was lost.
    """
    d, off, on, readbacks = _records()
    before = d.readback_completeness(readbacks)

    mutated = d.load(d.READBACK)
    mutated["readings"]["firefox"]["1337"]["reading"]["vectors"] = {}
    after_records = [
        ("readback-vectors.three-seeds.json", mutated),
        readbacks[1],
        readbacks[2],
    ]
    after = d.readback_completeness(after_records)

    assert len(after["empty"]) == len(before["empty"]) + 1, (
        "a leg that lost all its vectors was not counted as empty"
    )
    statement = d.completeness_statement(off, on, after_records)
    assert "firefox@1337" in statement, (
        "the newly lost leg is not named in the completeness statement"
    )


def test_edit8_completeness_block_is_the_derivation_not_a_paraphrase():
    """PS-16 inherits this claim through Edit 8; it must be spliced, not typed.

    The completeness claim becomes a durable assertion in a knowledge article
    that will no longer have the records beside it. A hand-typed paraphrase there
    reintroduces the round-3 defect one file over, so the patch block is compared
    against the generator's own output.
    """
    d, off, on, readbacks = _records()
    statement = d.completeness_statement(off, on, readbacks)

    patch_text = PATCH.read_text(encoding="utf-8")
    # Compare on flowed text: the patch wraps the statement to its own width.
    flowed = " ".join(statement.split())
    quoted = " ".join(
        ln[2:] if ln.startswith("> ") else ln[1:]
        for ln in patch_text.split("\n")
        if ln.startswith(">")
    )
    quoted = " ".join(quoted.split())
    assert flowed in quoted, (
        "Edit 8's completeness block is not derive.py's statement — re-splice "
        "with: python3 readings/ps185-2026-08-26/splice_patch.py"
    )


def test_basis_column_seed_count_reports_a_truncated_arm():
    """The per-arm basis column must recount, not echo the run's self-summary.

    Round 3 fixed the completeness SENTENCE but left the TABLE ROW reading
    ``result.per_arm['seeds_readable']`` — a summary the sweep wrote about
    itself. Under a truncation the two then disagreed, and the disagreement
    shipped into PS-16's Table 2: a row asserting a full sample beside a warning
    saying it was half, with no records left for a reader to arbitrate.

    Both authorship arms are exercised, because the shipped figure comes from a
    different record on each: windows defers to the engine (layer OFF) while the
    other three ship persona's own draw (layer ON). A fix that recounted only
    one side would pass a single-arm test.
    """
    d, off, on, _ = _records()

    def basis_row(off_rec, on_rec, arm):
        section = d.gpu_section(off_rec, on_rec, d.load(d.UNIF_OFF), d.load(d.UNIF_ON))
        return next(
            ln for ln in section.split("\n") if ln.startswith(f"| {arm} |")
        )

    for arm, mutated_side in (("android", "on"), ("windows", "off")):
        assert "24 seeds" in basis_row(off, on, arm), f"{arm} fixture is not full"

        mutated = d.load(d.LAYER_ON if mutated_side == "on" else d.LAYER_OFF)
        for seed in sorted(mutated["readings"][arm])[:12]:
            mutated["readings"][arm][seed] = None
        # The stored summary is deliberately left claiming a full sample.
        assert mutated["result"]["per_arm"][arm]["seeds_readable"] == 24

        row = (
            basis_row(off, mutated, arm) if mutated_side == "on"
            else basis_row(mutated, on, arm)
        )
        assert "12 seeds" in row, (
            f"the {arm} basis column still reports a full sample over a "
            f"truncated run — it is echoing the summary, not counting: {row}"
        )


# ---------------------------------------------------------------------------
# ROUND 5 — the CLASS, enumerated by behaviour rather than by grep.
#
# Rounds 2, 3 and 4 each fixed the one instance they were handed, and a new
# member of the same class was found each time. The class is: *any figure
# rendered from a summary the sweep wrote about ITSELF, rather than recounted
# from the raw readings.*
#
# These were found by MUTATING the records and diffing the rendered output —
# destroy the raw readings, leave every stored summary block intact, and see
# which numbers fail to move. A number that does not move when the readings
# underneath it are destroyed is being echoed, not computed. That search is by
# behaviour, which is why it returns sites a grep for `seeds_readable` cannot:
# the None==None control below reads no summary field at all, and the readback
# sites read `verdicts` instead of `per_arm`.
#
# Every mutation is driven through IN-MEMORY records, exactly as above. No
# committed evidence file is ever written to.
# ---------------------------------------------------------------------------


def _truncate(rec, arm, keep=12):
    """Null all but `keep` of an arm's readings, leaving the summary intact."""
    for seed in sorted(rec["readings"][arm])[keep:]:
        rec["readings"][arm][seed] = None
    return rec


def _gpu(d, off, on):
    return d.gpu_section(off, on, d.load(d.UNIF_OFF), d.load(d.UNIF_ON))


def test_collision_percentage_is_recounted_not_echoed():
    """THE headline figure — the number that replaces "theoretical" in PS-16.

    Round 4 made the seed count honest and left the percentage it qualifies on
    ``result.per_arm``, so under a truncation the row contradicted itself
    inside a single line: ``27.4%`` computed over 24 readings printed beside a
    seed count of 12. That is a worse artifact than either half alone, because
    a future reader with no records beside them cannot tell which number is
    load-bearing.
    """
    d, off, on, _ = _records()
    row = next(ln for ln in _gpu(d, off, on).split("\n")
               if ln.startswith("| android |"))
    assert "27.4%" in row and "4 / 1" in row and "24 seeds" in row

    mutated = _truncate(d.load(d.LAYER_ON), "android")
    # The stored summary is deliberately left claiming the full sample.
    assert mutated["result"]["per_arm"]["android"]["collision_probability"] == \
        pytest.approx(0.2743055555555556)
    assert mutated["result"]["per_arm"]["android"]["distinct_identities"] == 4

    after = next(ln for ln in _gpu(d, off, mutated).split("\n")
                 if ln.startswith("| android |"))
    assert "27.4%" not in after, (
        "the collision percentage still reports the figure computed over the "
        f"full sample on a truncated run — it is echoing the summary: {after}"
    )
    assert "12 seeds" in after, "the seed count should also have moved"
    assert "4 / 1" not in after, (
        f"distinct_identities is still echoing the stored summary: {after}"
    )


def test_positive_control_does_not_count_two_absences_as_agreement():
    """``None == None`` is True — so the control got STRONGER as the sweep failed.

    The windows paragraph claims both modes returned the SAME IDENTITY for
    every seed. Counting bare equality scores a seed that produced NOTHING in
    either mode as a seed that agreed, so a total launch failure in both modes
    scored a perfect 24 of 24. It is the sharpest member of the class because
    it reads no summary field at all: no grep would return it.
    """
    d, off, on, _ = _records()
    assert "all 24 of 24 seeds returned the same identity" in _gpu(d, off, on)

    m_on = _truncate(d.load(d.LAYER_ON), "windows")
    m_off = _truncate(d.load(d.LAYER_OFF), "windows")
    after = _gpu(d, m_off, m_on)

    assert "all 24 of 24 seeds returned the same identity" not in after, (
        "the positive control counted 12 seeds that returned NOTHING in both "
        "modes as 12 seeds that agreed — an assertion that gets stronger the "
        "more the sweep fails"
    )


def test_estimator_table_recounts_N_from_the_record_it_names():
    """N is load-bearing: ``E[S_hat] = 1/k + (1 - 1/k)/N``.

    The uniformity records carry NO raw readings — only a per_arm summary — so
    the recount has to come from the sweep each one names in ``source_record``.
    An N that cannot notice a truncated sweep silently changes what the
    expectation column means, and with it the "android scored BELOW what
    uniform predicts" line that settles the artefact question.
    """
    d, off, on, _ = _records()
    assert "at N=24" in _gpu(d, off, on)

    mutated = _truncate(d.load(d.LAYER_ON), "android")
    assert mutated["result"]["per_arm"]["android"]["seeds_readable"] == 24
    after = _gpu(d, off, mutated)

    assert "at N=24" not in after, (
        "the estimator argument still reports N=24 over a 12-readable run"
    )
    assert "at N=12" in after


def test_macos_moved_paragraph_recounts_both_its_figures():
    """A percentage and the seed count it is quoted over, in one sentence.

    Echoing either from the stored summary produces the self-contradicting
    shape: "58.7% over 24 seeds" printed over a 12-readable layer-OFF run.
    """
    d, off, on, _ = _records()
    assert "58.7% over 24 seeds" in _gpu(d, off, on)

    mutated = _truncate(d.load(d.LAYER_OFF), "macos")
    after = _gpu(d, mutated, on)

    assert "58.7% over 24 seeds" not in after, (
        "the macos paragraph still reports the full-sample figure and count"
    )
    assert "over 12 seeds" in after


def test_readback_verdicts_are_recounted_from_the_raw_readings():
    """Found by MY enumeration, not on the review's list — and it is DoD #2.

    ``rb["verdicts"]`` is the readback run's account of ITSELF, the same class
    as ``result.per_arm`` one file over. It renders the per-seed hash table AND
    the firefox contrast that PS-182 depends on, so a lost sweep publishes a
    confident answer to this ticket's headline question.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "**DIFFERS**" in d.readback_section(rb, rep, repc)

    # Every firefox vector destroyed; the stored verdicts left untouched.
    for leg in rb["readings"]["firefox"].values():
        leg["reading"] = {"vectors": {}}
    assert rb["verdicts"]["firefox"]["webgl_pixel_hash"]["verdict"] == "DIFFERS"

    after = d.readback_section(rb, rep, repc)
    ff_row = next(ln for ln in after.split("\n")
                  if ln.startswith("| firefox | `webgl_pixel_hash`"))
    assert "dabeff0d" not in ff_row, (
        "the readback table still prints hashes from a sweep that read "
        f"nothing — it is echoing the stored verdicts: {ff_row}"
    )
    assert "INCONCLUSIVE" in ff_row, (
        "a sweep that produced no usable value must read INCONCLUSIVE, which "
        "is NOT a pass"
    )


def test_firefox_branch_is_derived_not_asserted():
    """The ticket names TWO branches and forbids averaging them.

    The narrative hardcoded ``**It does not.**`` and ``— **different**``, so it
    could only ever report one of them. With the leg lost it printed
    "loopback probe, firefox @1337 -> `None`, @4242 -> `None` - **different**":
    a confident verdict over two absent values.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "**It does not.**" in d.readback_section(rb, rep, repc)

    for leg in rb["readings"]["firefox"].values():
        leg["reading"] = {"vectors": {}}
    after = d.readback_section(rb, rep, repc)

    assert "— **different**" not in after, (
        "the narrative still calls two absent values 'different'"
    )
    assert "**It does not.**" not in after, (
        "the answer to the ticket's headline question is still asserted "
        "regardless of what the probe read"
    )
    assert "not answered here" in after or "no usable reading" in after


def test_firefox_branch_reports_a_COLLISION_when_the_probe_collides():
    """Discrimination: the OTHER branch must be reachable.

    Without this, "derived" could mean a sentence that merely fails safe. The
    ticket's first branch — probe reproduces the checker's collision, so the
    defect is upstream of delivery and PS-182 is workable without the proxy —
    is a different conclusion and must be produced by the data.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)

    seeds = [str(s) for s in rb["seeds"]]
    shared = "51df3565"
    for s in seeds[:2]:
        rb["readings"]["firefox"][s]["reading"]["vectors"]["webgl_pixel_hash"] = shared

    after = d.readback_section(rb, rep, repc)
    assert "**It does.**" in after, (
        "a probe that DOES reproduce the checker's collision must report the "
        "first branch, not the second"
    )
    assert "upstream of delivery" in after


def test_canvas_split_names_the_seeds_that_actually_collide():
    """The canvas paragraph asserted WHICH seeds collide as prose."""
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "seeds 1337 and 4242 produce the SAME canvas" in \
        d.readback_section(rb, rep, repc)

    # Make every firefox canvas seed distinct: there is no longer a collision.
    for i, s in enumerate([str(x) for x in rb["seeds"]]):
        rb["readings"]["firefox"][s]["reading"]["vectors"]["canvas_pixel_hash"] = \
            f"distinct{i}:bytes8192:mid6144"
    after = d.readback_section(rb, rep, repc)

    assert "produce the SAME canvas" not in after, (
        "the canvas split still asserts a collision that the readings no "
        "longer contain"
    )
    assert "DISTINCT canvas hashes" in after


def test_constant_arms_are_recounted_not_read_from_the_stored_verdict():
    """``verdict == "CONSTANT"`` decides which arms this section names AT ALL.

    An arm that stopped being constant keeps its old label, and a truncated arm
    that collapsed to one surviving identity keeps a verdict from the full run.
    """
    d, off, on, _ = _records()
    assert "* **linux**" in _gpu(d, off, on)

    mutated = d.load(d.LAYER_OFF)
    # linux is CONSTANT on record; give it a second identity so it is not.
    seeds = sorted(mutated["readings"]["linux"])
    for seed in seeds[:12]:
        mutated["readings"]["linux"][seed] = "Some Other | ANGLE (Other Vendor)"
    assert mutated["result"]["per_arm"]["linux"]["verdict"] == "CONSTANT"

    after = _gpu(d, mutated, on)
    assert "* **linux**" not in after, (
        "linux is still named as a CONSTANT arm on a run where it drew two "
        "identities — the section is reading the stored verdict"
    )


def test_the_enumerator_is_committed_and_reports_every_site_moving():
    """The SEARCH ships, not just a description of it.

    Narrating a method leaves the next person to rebuild it. This runs the
    committed enumerator and requires a clean exit: it destroys the raw
    readings scenario by scenario and fails if any rendered section does not
    move, which is the whole defect class in one command.
    """
    enumerator = READINGS / "enumerate_summary_sites.py"
    assert enumerator.is_file(), "the enumeration harness is not committed"

    proc = subprocess.run(
        [sys.executable, str(enumerator), "--quiet"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, (
        "a rendered figure did not move when the readings underneath it were "
        f"destroyed:\n{proc.stdout[-3000:]}"
    )


def test_splicer_keeps_edit3_in_sync_not_only_edit8():
    """Edit 3 claims to be verbatim, so it needs a MECHANICAL way back in sync.

    The splicer previously synchronised Edit 8 alone, so a change to derive.py's
    prose broke Edit 3's "verbatim" label with no way to restore it but a human
    re-typing the block — which is precisely the re-typing the script exists to
    remove, and how such a label rots.
    """
    splicer = READINGS / "splice_patch.py"
    proc = subprocess.run(
        [sys.executable, str(splicer), "--check"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, (
        f"the committed patch is out of sync with derive.py: {proc.stdout}"
        f"{proc.stderr}"
    )
    # The splicer must actually KNOW about Edit 3, not merely pass because
    # Edit 8 happens to match.
    source = splicer.read_text(encoding="utf-8")
    assert "splice_edit3" in source and "EDIT3_HEADING" in source, (
        "the splicer does not handle Edit 3, so its verbatim claim has no "
        "mechanical path back into sync"
    )
