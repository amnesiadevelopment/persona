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

import functools
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


@functools.lru_cache(maxsize=1)
def _run_derive() -> str:
    """Execute derive.py exactly as the patch instructs a maintainer to.

    ``encoding`` is pinned explicitly: ``text=True`` alone decodes the child's
    stdout with the PARENT's locale encoding, which on a Windows runner is
    cp1252 and raises UnicodeDecodeError on the ⚠️ and — this output carries.
    That is a second, distinct defect from the child-side one derive.py fixes:
    the child WRITING utf-8 does not help if the parent READS cp1252.

    MEMOISED because the round-6 recount made a run genuinely expensive: the
    Monte-Carlo p-value is now recomputed rather than echoed, which is 200k
    trials per record. The output is a pure function of the committed records,
    which no test mutates on disk, so one run serves every caller — and
    ``derive.py`` is still executed as a real subprocess exactly once, which is
    what the cp1252 and verbatim checks are actually testing.
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


@functools.lru_cache(maxsize=1)
def _load_derive_module():
    """Import derive.py as a module so its functions can be called directly.

    MEMOISED for the same reason as ``_run_derive``: the round-6 recount runs a
    200k-trial simulation per record, and ``derive.py`` memoises it per MODULE
    INSTANCE — so a fresh import per test threw that cache away and paid the
    simulation again. Sharing one instance is safe because no test mutates the
    module; every test loads its own records with ``d.load()`` and mutates
    those, which is what keeps the cases independent.
    """
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


@pytest.mark.timeout(1800)
def test_the_enumerator_is_committed_and_reports_every_site_moving():
    """The SEARCH ships, not just a description of it.

    Narrating a method leaves the next person to rebuild it. This runs the
    committed enumerator's AXIS 1 and requires a clean exit: it walks every
    raw-reading field, destroys it, and fails if a rendered claim does not
    move — which is the whole defect class in one command.

    Axis 1 is named explicitly rather than running the default `both`. Axis 2
    has its own test, and running both here would double a four-minute walk
    for no extra coverage.

    ⚠️ TWO bounds have to be raised, not one, and missing the second is how
    this test failed after round 7 generalised the walk. The `subprocess`
    timeout below bounds the CHILD; `pyproject.toml`'s `timeout = 120` is
    pytest-timeout bounding the TEST THREAD, and the smaller of the two wins.
    Raising only the child's bound left the thread bound to kill it at 120 s,
    which reads as a harness failure rather than as the walk being slow.

    Both are sized for the GENERALISED walk: round 7 replaced six hand-written
    scenarios with 95 fields x 3 mutation operations, and the recount
    underneath each render is 200k Monte-Carlo trials per record. Generous on
    purpose — a harness that times out reports a false green, which is the
    failure mode this file exists to end.
    """
    enumerator = READINGS / "enumerate_summary_sites.py"
    assert enumerator.is_file(), "the enumeration harness is not committed"

    proc = subprocess.run(
        [sys.executable, str(enumerator), "--axis", "1", "--quiet"],
        capture_output=True, text=True, encoding="utf-8", timeout=1800,
    )
    assert proc.returncode == 0, (
        "a rendered claim did not move when the readings underneath it were "
        f"destroyed:\n{proc.stdout[-3000:]}"
    )
    assert "raw-reading fields walked" in proc.stdout, (
        "axis 1 did not report walking any raw-reading fields"
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


# ---------------------------------------------------------------------------
# ROUND 6 — the class has TWO AXES, and round 5's harness ran only one.
#
# A stored summary block can fail in two ways, and each axis is blind to the
# other's members:
#
#   AXIS 1  destroy the readings, keep the summary  -> catches a figure that
#           claims to be recounted but is echoed;
#   AXIS 2  poison the summary, keep the readings   -> catches a figure that
#           never consulted the readings AT ALL. Destroying readings cannot
#           detect that, so axis 1 returns a clean sweep over live members.
#
# The last member hid behind a FALSE EXEMPTION. `_uniformity_stats` documented
# `monte_carlo_p_value` as "the ONE figure that genuinely cannot be recomputed
# — a seeded simulation, not a function of the readings". Both uniformity
# records store the `monte_carlo_seed` and `monte_carlo_trials` they were run
# with, which makes it a DETERMINISTIC function of the readings plus two
# recorded parameters. It is the column that decides artefact-vs-genuine, and
# it lands in PS-16.
#
# Every mutation below is driven through IN-MEMORY records. No committed
# evidence file is ever written to.
# ---------------------------------------------------------------------------


def _unif_row(d, arm, off=None, on=None):
    """The estimator-table row for `arm`, rendered from the given records."""
    off = off if off is not None else d.load(d.LAYER_OFF)
    on = on if on is not None else d.load(d.LAYER_ON)
    section = d.gpu_section(off, on, d.load(d.UNIF_OFF), d.load(d.UNIF_ON))
    return next(
        ln for ln in section.split("\n")
        if ln.startswith(f"| {arm} |") and ln.count("|") == 8
    )


def test_monte_carlo_p_value_is_recomputed_not_echoed():
    """The column that decides artefact-vs-genuine must track the readings.

    This is the site the false exemption protected. Truncating android's
    layer-ON arm moves the three recounted columns beside it; if the p-value
    sits frozen while they move, the row contradicts itself INSIDE ONE LINE —
    the same shape round 4 blocked on, one table further down, and it ships
    into PS-16 where no records sit beside it for a reader to arbitrate.
    """
    d = _load_derive_module()
    base = _unif_row(d, "android")
    assert "| 0.580 |" in base, f"android p-value fixture moved: {base}"

    truncated = _truncate(d.load(d.LAYER_ON), "android")
    # The stored summary is deliberately left claiming the full run.
    assert d.load(d.UNIF_ON)["per_arm"]["android"]["monte_carlo_p_value"] == 0.579675

    row = _unif_row(d, "android", on=truncated)
    assert "| 0.580 |" not in row, (
        "the Monte-Carlo p-value did not move when half the readings under it "
        f"were destroyed — it is echoing the stored summary: {row}"
    )


def test_genuine_narrowing_verdict_is_recomputed_not_echoed():
    """The artefact/genuine verdict is a CONCLUSION, so it must be re-derived.

    Echoed, it would keep asserting "artefact" over a sample that no longer
    supports the claim — reporting an estimator artefact as settled when the
    evidence for settling it had been destroyed.
    """
    d = _load_derive_module()
    unif = d.load(d.UNIF_ON)
    off, on = d.load(d.LAYER_OFF), d.load(d.LAYER_ON)

    # Poison the STORED verdict only; every reading stays intact.
    unif["per_arm"]["android"]["genuine_narrowing_finding"] = True
    section = d.gpu_section(off, on, d.load(d.UNIF_OFF), unif)
    row = next(
        ln for ln in section.split("\n")
        if ln.startswith("| android |") and ln.count("|") == 8
    )
    assert "**genuine**" not in row, (
        "flipping the stored genuine_narrowing_finding changed the rendered "
        f"verdict, so the verdict is the record's word rather than a "
        f"re-derivation: {row}"
    )


def test_pool_size_is_pinned_to_the_measurement_epoch_not_the_live_product():
    """`k` is an INPUT to two rendered columns, and it is an AS-OF fact.

    Two distinct defects meet at this one field, and fixing either alone
    leaves the other live.

    ROUND 6 (still guarded below): round 5 recomputed the estimator FORMULAE
    but fed them a `k` read out of the uniformity record's stored block,
    leaving `E[plug-in | uniform]` and the `1/k` bar resting on the record's
    word. That site was on nobody's list and it moves the single sentence the
    whole artefact finding rests on.

    ROUND 7: taking `k` from the LIVE product fixed that and introduced the
    opposite failure. `k` is not a property of the readings and not a summary
    the sweep wrote about itself — it is an ENVIRONMENTAL INPUT the sweep
    recorded, and 24 observed draws cannot recover it. PS-183 widened
    `MAC_GPUS` 2 -> 11 the day after these readings were taken, and the same
    committed macos draw scored against a `1/11` bar instead of `1/2` moves
    p 0.308 -> 0.000 and flips the arm from **artefact** to **genuine** —
    manufacturing a product finding out of an unrelated pool edit, which is
    the PS-14 false attribution, and rewriting a published verdict
    retroactively every time someone edits a pool.

    So `k` comes from the SWEEP's own `fallback_pool_size` witness: the record
    of what the pool held when the draw was taken.
    """
    d = _load_derive_module()
    unif = d.load(d.UNIF_ON)
    off, on = d.load(d.LAYER_OFF), d.load(d.LAYER_ON)

    def android_row(uon, layer_on):
        section = d.gpu_section(off, layer_on, d.load(d.UNIF_OFF), uon)
        return next(
            ln for ln in section.split("\n")
            if ln.startswith("| android |") and ln.count("|") == 8
        ), section

    # ROUND 6's guard: the uniformity record's own copy is not consulted.
    poisoned = d.load(d.UNIF_ON)
    poisoned["per_arm"]["android"]["pool_size"] = 999
    row, section = android_row(poisoned, on)
    assert "| 0.2812 | 0.2500 |" in row, (
        "poisoning the stored pool_size moved the expectation column or the "
        f"bar, so `k` is taken from the uniformity record's copy: {row}"
    )
    settles = next(
        ln for ln in section.split("\n") if "single line that settles it" in ln
    )
    assert "0.2812" in settles, (
        f"the sentence the artefact finding rests on moved with a poisoned "
        f"stored pool size: {settles}"
    )

    # ROUND 7's guard: the SWEEP's witness is what drives it. Move that, and
    # the columns MUST move — otherwise `k` is coming from somewhere else.
    epoch = d.load(d.LAYER_ON)
    epoch["result"]["per_arm"]["android"]["fallback_pool_size"] = 11
    moved_row, _ = android_row(unif, epoch)
    assert "| 0.2812 | 0.2500 |" not in moved_row, (
        "changing the pool size the SWEEP recorded did not move the estimator "
        f"columns, so `k` is not read from the measurement epoch: {moved_row}"
    )


def test_the_article_reproduces_independently_of_todays_gpu_pools():
    """The published figures must not depend on the machine rendering them.

    This is the property the epoch pin buys, stated end to end: a committed
    measurement describes the moment it was taken, so re-deriving it a month
    later — on a product whose pools have since moved — must produce the same
    document. Before the pin, PS-183's `MAC_GPUS` widening silently changed a
    published verdict on all three CI platforms.

    The live product is patched to a WIDENED pool rather than mocked at the
    derive layer, so this exercises the same path a real post-PS-183 checkout
    takes.

    ⚠️ THE FIXTURE MUST DIFFER FROM TODAY'S LIVE POOLS, AND THAT IS ASSERTED
    RATHER THAN ASSUMED (PS-239). This test previously hardcoded
    ``{"windows": 5, "macos": 11, "linux": 8, "android": 4}`` — which, once
    PS-183 landed, *was* the live pool on every arm. It therefore substituted
    a stub identical to the real function and compared a document against
    itself: it passed unconditionally, and went on passing while the macos
    verdict was in fact being scored against the wrong null. A test that
    cannot fail proves nothing, so the widened pool is now derived FROM the
    live one and the difference is checked before it is relied on.
    """
    from src.services.verify import engine_gpu_variance as egv

    d = _load_derive_module()
    off, on = d.load(d.LAYER_OFF), d.load(d.LAYER_ON)
    before = d.gpu_section(off, on, d.load(d.UNIF_OFF), d.load(d.UNIF_ON))

    # Derived from the live pool so it CANNOT silently coincide with it, and
    # far enough from every recorded `k` that a leak would move a verdict
    # rather than merely a digit.
    wide = {
        arm: egv.fallback_pool_size(arm) + 7
        for arm in ("windows", "macos", "linux", "android")
    }
    live = {
        arm: egv.fallback_pool_size(arm)
        for arm in ("windows", "macos", "linux", "android")
    }
    assert wide != live, (
        "the widened fixture equals the live pool, so patching it is a no-op "
        "and this test cannot fail — the exact defect PS-239 found here"
    )

    real_size, real_bar = egv.fallback_pool_size, egv.bar_for
    try:
        egv.fallback_pool_size = lambda arm, generation=None: wide.get(arm, 0)
        egv.bar_for = lambda arm, generation=None: (
            (1.0 / wide[arm]) if wide.get(arm) else None
        )
        # POSITIVE CONTROL: the stub must actually reach the gate. If the
        # UNPINNED gate does not move under a pool this different, the
        # substitution is not taking effect and the assertion below would pass
        # for the wrong reason.
        moved = egv.classify(on["readings"])["per_arm"]["macos"]
        assert moved["fallback_pool_size"] == wide["macos"], (
            "the patched pool did not reach classify(), so this test's stub "
            f"is inert: {moved['fallback_pool_size']}"
        )
        after = d.gpu_section(off, on, d.load(d.UNIF_OFF), d.load(d.UNIF_ON))
    finally:
        egv.fallback_pool_size, egv.bar_for = real_size, real_bar

    assert after == before, (
        "the rendered GPU section changed when the product's GPU pools were "
        "widened, so a committed measurement is being scored against today's "
        "pool rather than the one it was drawn from"
    )


def test_module_verdict_is_asked_of_the_gate_not_transcribed():
    """The gate's verdict is quoted deliberately — so quote the GATE.

    This column exists to report what `engine_gpu_variance` said, so the
    estimator can be contrasted against it. That is a good reason to render it
    and NOT a reason to trust a transcription: the uniformity record's copy is
    a copy of the sweep's copy. Asking `classify` afresh still reports the
    gate's verdict, with one fewer link that can rot.
    """
    from src.services.verify import engine_gpu_variance as egv

    d = _load_derive_module()
    unif = d.load(d.UNIF_ON)
    off, on = d.load(d.LAYER_OFF), d.load(d.LAYER_ON)

    # WHAT THE GATE ACTUALLY SAYS, asked the same way `derive.py` asks it:
    # pinned to the pool the sweep witnessed. Computed rather than written
    # down, because a literal here would be a SECOND TRANSCRIPTION — the very
    # thing this test forbids one line below. PS-239 found the previous
    # hardcoded "TOO_NARROW" had silently become false when PS-191 corrected
    # the gate, so the test asserted a stale answer while claiming to check
    # provenance.
    expected = egv.classify(
        on["readings"], d._epoch_pool_sizes(on)
    )["per_arm"]["android"]["verdict"]

    unif["per_arm"]["android"]["module_verdict"] = "POISONED"
    section = d.gpu_section(off, on, d.load(d.UNIF_OFF), unif)
    row = next(
        ln for ln in section.split("\n")
        if ln.startswith("| android |") and ln.count("|") == 8
    )
    assert "POISONED" not in row and f"| {expected} " in row, (
        "the module verdict came from the uniformity record's transcription "
        f"rather than from the gate itself (gate says {expected!r}): {row}"
    )


def test_widening_a_pool_cannot_move_an_archived_records_verdict():
    """A pool edit must not retroactively re-label a measurement. PS-239.

    This is the defect that turned `main` red, stated as a property. The
    verdict column is asked of the live gate — deliberately, so it reports
    what the product says rather than a transcription — and that made it the
    ONE column still reading the live pool while every other column was
    already pinned to the measurement epoch. PS-183 widened `MAC_GPUS` 2 -> 11
    the day after these readings were taken, so macos' committed draw was
    re-scored against a `1/11` bar it never faced.

    The two halves below are asserted TOGETHER because either alone is
    satisfiable by a broken gate: a gate that ignored `pool_sizes` entirely
    would pass the first, and one that always returned the same verdict
    whatever it was given would pass the second.
    """
    from src.services.verify import engine_gpu_variance as egv

    d = _load_derive_module()
    on = d.load(d.LAYER_ON)
    epoch = d._epoch_pool_sizes(on)

    # HALF 1 — pinned to the epoch, the verdict is what the pool the readings
    # actually faced implies, and a widened live pool cannot move it.
    pinned = egv.classify(on["readings"], epoch)["per_arm"]["macos"]
    wider = dict(epoch, macos=epoch["macos"] + 9)
    assert egv.classify(on["readings"], epoch)["per_arm"]["macos"][
        "verdict"
    ] == pinned["verdict"], "pinning is not deterministic"
    assert pinned["fallback_pool_size"] == epoch["macos"], (
        "the pinned verdict was scored against a pool other than the one the "
        f"sweep witnessed: {pinned['fallback_pool_size']} != {epoch['macos']}"
    )

    # HALF 2 — the pin is LOAD-BEARING, not decorative: scoring the SAME
    # readings against a wider pool really does flip this arm, so half 1 is
    # holding back a live failure rather than describing a distinction that
    # makes no difference.
    widened = egv.classify(on["readings"], wider)["per_arm"]["macos"]
    assert widened["verdict"] != pinned["verdict"], (
        "widening macos' pool did not change the verdict, so this test no "
        "longer demonstrates why the epoch pin matters — re-derive the "
        f"fixture (both read {pinned['verdict']!r})"
    )


def test_recomputed_uniformity_matches_the_stored_record_exactly():
    """The recount must REPRODUCE the evidence, not merely differ from it.

    A recount that silently disagreed with the committed record would mean the
    article no longer describes the run it cites — the opposite failure to an
    echo, and just as bad. All four arms in both modes must land exactly.

    ⚠️ `module_verdict` IS CHECKED SEPARATELY, AND MORE STRICTLY, BY
    `test_the_stored_verdict_is_reproducible_under_the_rule_that_wrote_it`.
    It is the one field here that is NOT a statistic: every other column is a
    function of the READINGS alone, so a disagreement means the recount is
    broken. The verdict is a function of the readings AND the product's rule,
    and that rule is versioned code which is allowed to be corrected — PS-191
    replaced a biased bar comparison with a hypothesis test. Demanding that
    today's rule reproduce a verdict recorded under the old one would make any
    correction to the gate permanently red on `main` (PS-239), which is
    precisely how this ticket's CI failure arose.
    """
    d = _load_derive_module()
    sources = {
        "engine-gpu-variance.layer-off.json": d.load(d.LAYER_OFF),
        "engine-gpu-variance.layer-on.json": d.load(d.LAYER_ON),
    }
    for unif_path in (d.UNIF_ON, d.UNIF_OFF):
        unif = d.load(unif_path)
        for arm in ("windows", "macos", "linux", "android"):
            got = d._uniformity_stats(unif, arm, sources)
            stored = unif["per_arm"][arm]
            for field in (
                "monte_carlo_p_value", "genuine_narrowing_finding",
                "pool_size", "plugin_estimate",
                "unbiased_estimate", "expected_plugin_under_uniform",
                "bar_collision_probability", "seeds_readable",
            ):
                assert got[field] == stored[field], (
                    f"{unif_path.name} {arm}: recomputed {field}={got[field]!r} "
                    f"but the committed record stores {stored[field]!r}"
                )


def test_the_stored_verdict_is_reproducible_under_the_rule_that_wrote_it():
    """The record's verdict must be REPRODUCIBLE, not merely explained away.

    This is the other half of the recount, carved out of it because the
    verdict is a function of the readings AND the gate's rule (PS-239). The
    weak move would have been to drop `module_verdict` from the recount and
    call the suite green — that would let the committed verdict be anything at
    all, including a hand-typed one, which is the transcription this whole
    file exists to forbid.

    So the record is held to the STRONGER property instead: re-running the
    rule that WROTE it must reproduce it exactly, on all four arms in both
    modes. `_bar_verdict` reconstructs the pre-PS-191 rule from `meets_bar`,
    which PS-191 deliberately kept computing — so this is a recount, not a
    memory of one.

    Both halves are asserted together, because either alone is satisfiable by
    a broken implementation: half 1 alone passes if the old rule is a constant
    function, and half 2 alone passes if nothing ever changed.
    """
    d = _load_derive_module()

    changed = []
    for src_key, unif_path in (
        (d.LAYER_ON, d.UNIF_ON),
        (d.LAYER_OFF, d.UNIF_OFF),
    ):
        src = d.load(src_key)
        unif = d.load(unif_path)
        live = d._classify(src["readings"], d._epoch_pool_sizes(src))["per_arm"]

        for arm in ("windows", "macos", "linux", "android"):
            stored = unif["per_arm"][arm]["module_verdict"]

            # HALF 1 — the rule that wrote the record reproduces it EXACTLY.
            assert d._bar_verdict(live[arm], arm) == stored, (
                f"{unif_path.name} {arm}: the committed verdict {stored!r} is "
                f"NOT reproducible under the rule that wrote it "
                f"(recomputed {d._bar_verdict(live[arm], arm)!r}). The record does "
                "not describe the run it cites."
            )
            if live[arm]["verdict"] != stored:
                changed.append(f"{unif_path.name} {arm} {stored}->"
                               f"{live[arm]['verdict']}")

    # HALF 2 — the gate HAS since been corrected, so the carve-out above is
    # load-bearing rather than decorative. If this ever fires, the two rules
    # agree everywhere and `module_verdict` should go back into the recount.
    assert changed, (
        "today's gate reproduces every stored verdict, so splitting this "
        "field out of the recount is no longer buying anything — fold it back "
        "into test_recomputed_uniformity_matches_the_stored_record_exactly"
    )


def test_bar_verdict_keeps_the_old_rules_known_pool_term(monkeypatch):
    """A missing bar meant two different things, and the old rule knew it.

    `_bar_verdict` claims to RECONSTRUCT the pre-PS-191 rule rather than
    remember it, so it has to reconstruct the awkward branch too. `meets_bar`
    is None whenever the bar is None, but the old chain reached INCONCLUSIVE
    on `elif bar_missing` — `bar is None` AND `has_known_pool(arm)`. An arm
    with no bar and NO known pool fell past it to the final `else` and
    returned OK. Collapsing both into INCONCLUSIVE would be a reconstruction
    that quietly disagrees with its own source (PS-239 review, finding 3).

    Both halves are asserted together because either alone is satisfiable by a
    broken implementation: half 1 alone passes if the function always answered
    INCONCLUSIVE, and half 2 alone passes if it always answered OK.
    """
    from src.services.browser import gpu_ext
    from src.services.verify import engine_gpu_variance as egv

    d = _load_derive_module()
    varied = {i: f"c{i}" for i in range(24)}

    # HALF 1 — NO known pool: "there was never a comparison to make" -> OK.
    unknown = egv.classify({"plan9": varied})["per_arm"]["plan9"]
    assert egv.has_known_pool("plan9") is False, "fixture arm gained a pool"
    assert unknown["meets_bar"] is None, "fixture no longer exercises a nil bar"
    assert d._bar_verdict(unknown, "plan9") == "OK", (
        "an arm with no bar AND no known pool must reconstruct as OK — the "
        "old rule fell through to its else branch here"
    )

    # HALF 2 — HAS a pool we failed to read: "we failed to look" is not a
    # pass, so the same nil bar must reconstruct as INCONCLUSIVE instead.
    assert egv._POOL_VAR_FOR_ARM["android"] == "ANDROID_GPUS"
    pools = {k: v for k, v in gpu_ext.GPU_POOLS.items() if k != "ANDROID_GPUS"}
    assert "ANDROID_GPUS" not in pools, "fixture no longer matches the source"
    monkeypatch.setattr(gpu_ext, "GPU_POOLS", pools)

    known = egv.classify({"android": varied})["per_arm"]["android"]
    assert egv.has_known_pool("android") is True
    assert known["meets_bar"] is None, "fixture no longer nils the bar"
    assert d._bar_verdict(known, "android") == "INCONCLUSIVE", (
        "an arm that HAS a pool we could not read must reconstruct as "
        "INCONCLUSIVE — a failure to look is not a met bar"
    )


@pytest.mark.timeout(1800)
def test_enumerator_runs_the_second_mutation_axis():
    """The harness must poison summaries, not only destroy readings.

    Round 5's enumerator returned exit 0 over four live members of the class
    because every scenario it ran mutated readings. An enumeration is only
    evidence for the axes it actually runs.

    ⚠️ NEEDS ITS OWN BOUND, exactly as the axis-1 test beside it does. Both
    spawn the SAME enumerator, which walks every field of every record and
    re-renders the article once per mutation; axis 2 measures at ~175-200s
    here. The ini-wide `timeout = 120` therefore killed it mid-subprocess and
    reported a TIMEOUT rather than a verdict — while the enumerator itself
    exited 0 when run by hand. Its sibling
    `test_the_enumerator_is_committed_and_reports_every_site_moving` was given
    `@pytest.mark.timeout(1800)` for this reason and this one was not, so the
    same work was bounded at 1800s through one door and 120s through the
    other. The subprocess already caps itself at `timeout=900`, so a genuine
    hang still fails rather than running forever (PS-239).
    """
    proc = subprocess.run(
        [sys.executable, str(READINGS / "enumerate_summary_sites.py"),
         "--axis", "2", "--quiet"],
        capture_output=True, text=True, encoding="utf-8", timeout=900,
    )
    assert proc.returncode == 0, (
        "a rendered figure depends on a stored summary field:\n"
        f"{proc.stdout[-3000:]}"
    )
    assert "stored fields walked" in proc.stdout, (
        "axis 2 did not report walking any stored fields"
    )


def test_axis2_exemption_is_scoped_to_the_record_that_cross_checks_it():
    """An exemption is only as good as its scope.

    `seeds_readable` on the GPU sweeps is exempt because `gpu_completeness`
    deliberately cross-checks it and DISCLOSES a disagreement. The uniformity
    records carry a field of the same name which nothing cross-checks. Keying
    the exemption on the bare NAME waived both, which would hide any future
    defect in the second for no better reason than a shared spelling.
    """
    source = (READINGS / "enumerate_summary_sites.py").read_text(encoding="utf-8")
    assert "DISCLOSED_FIELDS = {(" in source, (
        "the axis-2 exemption is not scoped by record — a bare field name "
        "exempts every record that happens to reuse the name"
    )
    assert '("gpu[on]", "seeds_readable")' in source, (
        "the exemption does not name the GPU sweep record it applies to"
    )


# ---------------------------------------------------------------------------
# ROUND 7 — the class covers PROSE, and axis 1 was still a hand-written list.
#
# Round 6 closed the stored-summary class on both axes, but the two axes were
# not symmetric: axis 2 was a GENERIC WALK of every stored field, while axis 1
# was six hand-written scenarios whose whole mutation vocabulary was
# `readings[arm][seed] = None` and `leg["reading"] = {"vectors": {}}`. A
# readback leg carries NINE fields; axis 1 touched exactly one of them.
#
# Live defects sat in the blind spot BETWEEN the axes — invisible to axis 1
# (which never mutated `layer`) and to axis 2 (a reading is not a summary).
# The property, stated to cover both halves:
#
#     No rendered claim — figure OR prose — may be frozen against the
#     evidence it describes.
#
# The sites below were returned by the GENERALISED axis-1 walk, not supplied.
# Two matched the review's list; three did not (the derived heading, the
# render CRASHING on a fully-destroyed arm, and the ownership claim).
#
# Every mutation is driven through IN-MEMORY records. No committed evidence
# file is ever written to.
# ---------------------------------------------------------------------------


def test_chromium_canvas_clause_is_derived_from_chromium_readings():
    """S1. A hardcoded conclusion about ONE engine inside the OTHER's branch.

    ``On chromium all three differ.`` was a literal, sitting in a paragraph
    selected entirely by firefox data. Forcing chromium's canvas to collide
    rendered the recounted table row as **COLLIDES** with this sentence one
    line below still saying they all differ — a caption contradicting the row
    directly above it, in the paragraph that IS DoD #2's deliverable and that
    the ticket forbids averaging into one verdict.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "On chromium all three differ." in d.readback_section(rb, rep, repc)

    for s in [str(x) for x in rb["seeds"]]:
        rb["readings"]["chromium"][s]["reading"]["vectors"][
            "canvas_pixel_hash"] = "SAME:bytes8192:mid6144"
    after = d.readback_section(rb, rep, repc)

    assert "On chromium all three differ." not in after, (
        "the caption still says chromium's three seeds differ while the table "
        "row above it reports COLLIDES"
    )
    assert "COLLIDES" in after
    assert "must not be averaged into one verdict" in after, (
        "a chromium collision is not the engine split described below, and "
        "the paragraph must say so rather than describing a split"
    )


def test_layer_sentence_reads_the_layer_report_it_cites():
    """S2. An explanation that cannot be contradicted by the record it cites.

    The sentence cites "the layer report in these records" as its evidence and
    then spelled BOTH halves out as literals. Nothing in derive.py consulted
    ``leg["layer"]`` at all, so handing every firefox leg a canvas extension
    and shrinking chromium's layer to two entries moved nothing while the
    paragraph went on describing a layer report neither engine had.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    before = d.readback_section(rb, rep, repc)
    assert "no canvas extension at all" in before
    assert "against ten on chromium" in before

    for leg in rb["readings"]["firefox"].values():
        leg["layer"]["installed"] = sorted(leg["layer"]["installed"] + ["canvas_ctx"])
    for leg in rb["readings"]["chromium"].values():
        leg["layer"]["installed"] = ["audio", "webgl"]
    after = d.readback_section(rb, rep, repc)

    assert "no canvas extension at all" not in after, (
        "the mechanism still claims firefox installs no canvas extension "
        "while the layer report it cites now lists one"
    )
    assert "against ten on chromium" not in after, (
        "the chromium layer size is still spelled out rather than counted"
    )
    assert "against two on chromium" in after


def test_ownership_claim_is_derived_from_the_same_layer_evidence():
    """S3. The ownership verdict rests on the mechanism, so it must move with it.

    ``**This is a two-engine-rule cell, not a chromium cell.**`` is the
    conclusion of the mechanism paragraph above it. Left as a literal it would
    keep telling the reader a chromium fix cannot touch the cell even once the
    records stopped supporting the reason.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "not a chromium cell" in d.readback_section(rb, rep, repc)

    for leg in rb["readings"]["firefox"].values():
        leg["layer"]["installed"] = sorted(leg["layer"]["installed"] + ["canvas_ctx"])
    after = d.readback_section(rb, rep, repc)

    assert "not a chromium cell" not in after, (
        "the ownership verdict survives the disappearance of the mechanism "
        "it rests on"
    )
    assert "no longer holds in these records" in after


def test_webgl_heading_reports_the_branch_the_body_derived():
    """S4. A heading is a rendered claim, and this one names a branch.

    "and it is the harder answer" NAMES the expensive branch — the one where
    the internal difference does not survive the trip out. Making the probe
    collide flips the body to the OTHER branch ("upstream of delivery ...
    PS-182 can be verified entirely on loopback") while the heading went on
    calling it the harder answer, three lines above the sentence that now
    contradicted it. Found by the generalised walk; on nobody's list.
    """
    d, _, _, _ = _records()
    rb = d.load(d.READBACK)
    rep, repc = d.load(d.REPLICATE), d.load(d.REPLICATE_CHROME)
    assert "ANSWERED, and it is the harder answer" in \
        d.readback_section(rb, rep, repc)

    for s in [str(x) for x in rb["seeds"]][:2]:
        rb["readings"]["firefox"][s]["reading"]["vectors"][
            "webgl_pixel_hash"] = "51df3565"
    after = d.readback_section(rb, rep, repc)

    assert "upstream of delivery" in after, "the body did not flip branch"
    assert "the harder answer" not in after, (
        "the heading still announces the harder branch while the body reports "
        "the tractable one"
    )
    assert "the tractable answer" in after


def test_an_unreadable_arm_is_reported_not_crashed_on():
    """S5. INCONCLUSIVE is a RESULT the article has to be able to print.

    The generalised walk destroys a field outright rather than truncating it,
    and an arm with no readable seed legitimately produces ``None`` for every
    estimator column. Rendering that raised ``TypeError: unsupported format
    string passed to NoneType`` — the document did not report the arm as
    unobtainable, it failed to build at all. A traceback publishes nothing,
    and the ticket is explicit that anything not obtained is recorded WITH ITS
    REASON rather than left blank.
    """
    d, off, on, _ = _records()
    for seed in on["readings"]["android"]:
        on["readings"]["android"][seed] = None

    section = _gpu(d, off, on)  # must not raise

    assert "no readable seed" in section, (
        "a fully unreadable arm must be reported as unobtainable"
    )
    assert "INCONCLUSIVE" in section and "not a pass" in section, (
        "an unobtainable arm must be recorded as INCONCLUSIVE and explicitly "
        "not as a pass"
    )


def test_axis1_is_a_generic_walk_not_a_scenario_list():
    """THE round-7 blocker, pinned so it cannot regress to a list.

    Axis 2 was generic while axis 1 was six hand-written scenarios, and a
    supplied list can only re-find what someone already named. This asserts
    axis 1 derives its mutations FROM THE RECORDS and reports how many fields
    it walked, rather than iterating a constant.
    """
    source = (READINGS / "enumerate_summary_sites.py").read_text(encoding="utf-8")

    assert "SCENARIOS = [" not in source, (
        "axis 1 is back to a hand-written scenario list"
    )
    assert "_reading_groups" in source and "_subpaths" in source, (
        "axis 1 does not walk the reading tree generically"
    )
    assert "CITED BUT FROZEN" in source, (
        "axis 1 has no detector for a value the article prints but never reads"
    )
    # The declaration list must carry REASONS, not just names: an inert field
    # is a decision someone made, and the harness records it rather than
    # silently skipping it.
    assert "NOT_RENDERED = {" in source
    for key in ('("rb", "layer.route")', '("rb", "error")'):
        assert key in source, f"{key} is not declared as inert-with-a-reason"
