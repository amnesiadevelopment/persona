"""The same-vector consistency check, proven against the records it reads.

Every case here is either a COMMITTED RECORD from ``readings/`` or a single
mutation of one, never a hand-built dict. That is the ticket's own constraint
(*"the existing corpus contains a record with the contradiction in it and
others without — so the check has real positive and negative fixtures and does
not need synthetic ones"*) and it is what makes the assertions mean something:
a check observed only on inputs invented to satisfy it has not been observed.

THE FIXTURES ARE REAL AND ARE NAMED
-----------------------------------
* **The positive** — ``readings/ps150-2026-08-24/arm-a-baseline-layer-on.json``,
  the record PS-155 was filed from. One live Chromium run, one profile, one
  exit, in which ``creepjs`` reads an AMD Radeon and ``pixelscan.net`` reads an
  NVIDIA RTX 3070. All four rows carry ``adverse: false``. This record MUST be
  flagged; that is the ticket's first acceptance criterion.
* **The negative** — ``readings/ps143-2026-08-24/arm-b-layer-off.json``, in
  which both checkers agree on the same AMD adapter. A real clean record, not
  the positive with its contradiction removed.
* **The coverage hole** —
  ``readings/ps137-2026-08-24/run5-differential/CONTROL.no-masking.seed1337.json``,
  whose four ``gpu_claimed`` values are all literally ``None``.
* **The non-record** — ``readings/ps135-2026-08-24/reading.chromium.seed1337.json``,
  one of the 13 files carrying no ``readings`` key at all.

WHY THE MUTATION TESTS EXIST ANYWAY
-----------------------------------
The committed corpus proves the check fires and does not over-fire, but it
cannot prove the check would NOTICE a contradiction it has never seen — the
three positives are all the same AMD-vs-NVIDIA split. So the discrimination
tests mutate ONE value of a real record at a time: a clean record made to
contradict, a contradicting record made clean. One mutation per case is what
makes the expected classification unambiguous.
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from src.services.verify.checker_cli import main
from src.services.verify.matrix_consistency import (
    CONSISTENT,
    CONTRADICTION,
    COVERAGE_HOLE,
    NOT_COMPARABLE,
    adapter_text,
    check_vector,
    consistency_pass,
    contradictions,
    coverage_holes,
    format_consistency,
    identity,
)
from src.services.verify.matrix_diff import NotARecord

READINGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "readings"
)

# The record PS-155 was filed from. The contradiction is a permanent fixture of
# this file; it is committed and must not be regenerated.
POSITIVE = os.path.join(
    READINGS, "ps150-2026-08-24", "arm-a-baseline-layer-on.json"
)
# The SECOND, independent occurrence — same split, ~2.5h earlier, different
# campaign. Its presence is what makes the defect a reproduction rather than a
# one-off glitch.
POSITIVE_PS143 = os.path.join(READINGS, "ps143-2026-08-24", "arm-a-layer-on.json")
NEGATIVE = os.path.join(READINGS, "ps143-2026-08-24", "arm-b-layer-off.json")
ALL_NULL = os.path.join(
    READINGS,
    "ps137-2026-08-24",
    "run5-differential",
    "CONTROL.no-masking.seed1337.json",
)
NOT_A_RECORD = os.path.join(
    READINGS, "ps135-2026-08-24", "reading.chromium.seed1337.json"
)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def positive() -> dict:
    return load(POSITIVE)


@pytest.fixture
def negative() -> dict:
    return load(NEGATIVE)


def gpu_rows(record: dict) -> "list[dict]":
    return [r for r in record["readings"] if r.get("vector") == "gpu_claimed"]


def entry_for(entries: "list[dict]", vector: str) -> dict:
    return next(e for e in entries if e["vector"] == vector)


# --- the acceptance criterion ----------------------------------------------


def test_the_committed_record_PS155_was_filed_from_is_flagged(positive):
    """The ticket's first acceptance criterion, stated as a test.

    This is not a smoke test. If this passes and nothing else in the file
    does, the check still does the one thing it was built to do.
    """
    found = contradictions(consistency_pass(positive))
    assert [e["vector"] for e in found] == ["gpu_claimed"]
    assert "amd" in found[0]["reason"]
    assert "nvidia" in found[0]["reason"]


def test_the_flagged_rows_are_the_four_the_ticket_names(positive):
    """The finding names the rows a reader must go and look at."""
    entry = entry_for(consistency_pass(positive), "gpu_claimed")
    assert entry["identities"] == {
        "amd": ["creepjs/gpu_renderer", "creepjs/gpu_vendor"],
        "nvidia": ["pixelscan.net/webgl_renderer", "pixelscan.net/webgl_vendor"],
    }


def test_the_second_independent_occurrence_is_flagged_too():
    """ps143 arm-a carries the same split from a different campaign.

    Asserted separately from the ps150 record because the two are independent
    evidence: a fix that closes one and not the other has not closed the
    defect.
    """
    found = contradictions(consistency_pass(load(POSITIVE_PS143)))
    assert [e["vector"] for e in found] == ["gpu_claimed"]


def test_every_flagged_row_was_scored_adverse_false_by_the_per_row_pass(positive):
    """The second half of the ticket, asserted rather than asserted-about.

    The whole reason this module exists is that per-row classification cannot
    see a contradiction: each value is individually plausible. If some future
    change starts scoring these rows adverse, the premise of this module has
    moved and that should be a conscious decision, not a silent one.
    """
    assert [row["adverse"] for row in gpu_rows(positive)] == [False] * 4


# --- the negative side: it must be able to NOT fire -------------------------


def test_a_real_record_whose_checkers_agree_is_not_flagged(negative):
    """The committed clean record. A check that cannot pass is not a check."""
    entries = consistency_pass(negative)
    assert contradictions(entries) == []
    assert entry_for(entries, "gpu_claimed")["classification"] == CONSISTENT


def test_the_clean_record_reads_the_same_adapter_from_both_checkers(negative):
    """States WHY the negative is negative, so the fixture cannot silently rot.

    If someone regenerates this file and the two checkers stop agreeing, the
    test above would still pass for the wrong reason (it would be flagged, and
    `contradictions` would be non-empty — but this asserts the premise).
    """
    values = {row["value"] for row in gpu_rows(negative)}
    assert values == {
        "ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "Google Inc. (AMD)",
    }


# --- discrimination: one mutation at a time --------------------------------


def test_making_the_clean_record_disagree_makes_it_fire(negative):
    """THE FALSIFICATION TEST. Mutate one value; the verdict must flip.

    Without this, every green above is consistent with a check that returns
    "consistent" unconditionally.
    """
    before = consistency_pass(negative)
    assert contradictions(before) == []

    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("checker") == "pixelscan.net" and row.get("item") == "webgl_vendor":
            row["value"] = "Google Inc. (NVIDIA)"
            break
    else:  # pragma: no cover - fixture drift
        pytest.fail("the clean record no longer has the row this test mutates")

    assert [e["vector"] for e in contradictions(consistency_pass(mutated))] == [
        "gpu_claimed"
    ]


def test_repairing_the_contradiction_clears_the_finding(positive):
    """The other direction: the check must go quiet when the defect is fixed.

    This is the shape the LIVE verification of the fix takes — if the fix
    works, a fresh record looks like this mutation.
    """
    repaired = copy.deepcopy(positive)
    for row in repaired["readings"]:
        if row.get("checker") != "creepjs" or row.get("vector") != "gpu_claimed":
            continue
        if row["item"] == "gpu_vendor":
            row["value"] = "Google Inc. (NVIDIA)"
        elif row["item"] == "gpu_renderer":
            row["value"] = (
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) "
                "Direct3D11 vs_5_0 ps_5_0, D3D11)"
            )

    entries = consistency_pass(repaired)
    assert contradictions(entries) == []
    assert entry_for(entries, "gpu_claimed")["classification"] == CONSISTENT


def test_same_vendor_but_a_different_card_is_still_a_contradiction(negative):
    """The finer term, which brand comparison alone would pass.

    Two AMD cards are still two cards. A record naming both is no less
    self-contradictory than one naming two vendors, and a check that only
    compared IHV names would call this clean.
    """
    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("checker") == "pixelscan.net" and row.get("item") == "webgl_renderer":
            row["value"] = (
                "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 "
                "vs_5_0 ps_5_0, D3D11)"
            )
            break
    else:  # pragma: no cover - fixture drift
        pytest.fail("the clean record no longer has the row this test mutates")

    found = contradictions(consistency_pass(mutated))
    assert [e["vector"] for e in found] == ["gpu_claimed"]
    assert "one vendor but" in found[0]["reason"]


# --- what must NOT be treated as a contradiction ----------------------------


def test_pixelscans_or_similar_hedge_is_not_a_contradiction(negative):
    """The ticket's "two spellings of the same adapter" case, from real data.

    `pixelscan.net` renders "ANGLE (AMD, Radeon R9 200 Series …), or similar"
    where creepjs renders the same string without the hedge. That is
    pixelscan's prose, not a second GPU, and it is the ONLY spelling variance
    present anywhere in the committed corpus.
    """
    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("checker") == "pixelscan.net" and row.get("item") == "webgl_renderer":
            row["value"] = row["value"] + ", or similar"
            break
    else:  # pragma: no cover - fixture drift
        pytest.fail("the clean record no longer has the row this test mutates")

    assert contradictions(consistency_pass(mutated)) == []


def test_differing_whitespace_and_case_are_not_a_contradiction(negative):
    """A row a checker renders with different whitespace is not a finding."""
    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("vector") == "gpu_claimed" and row.get("checker") == "pixelscan.net":
            row["value"] = "  " + str(row["value"]).upper().replace(" ", "  ") + " "

    assert contradictions(consistency_pass(mutated)) == []


def test_the_hash_vector_is_never_compared_across_checkers(positive):
    """gpu_rendered carries per-checker hashes and must not be compared.

    creepjs emits an 8-hex digest, pixelscan a 32-hex one, over pixels each
    drew itself. They cannot be equal even when the machine is behaving, so
    comparing them would manufacture a finding on almost every record that
    reads both checkers — measured: 14 of the 21 readable records.
    """
    entry = entry_for(consistency_pass(positive), "gpu_rendered")
    assert entry["classification"] == NOT_COMPARABLE
    assert "hash" in entry["reason"].lower()


def test_the_hash_vector_stays_uncompared_even_when_its_values_differ(negative):
    """Not merely uncompared-by-luck: uncompared BY DECLARATION.

    Proven by making the hashes wildly different and asserting nothing fires.

    Mutated on the CLEAN record deliberately: on the positive record the
    `gpu_claimed` contradiction would still be present, so an assertion that
    "nothing fired" could not distinguish "the hashes were ignored" from "the
    hashes fired and so did the real finding". The clean record has nothing
    else to fire, so a single contradiction here could only have come from the
    hashes.
    """
    mutated = copy.deepcopy(negative)
    hashes = [r for r in mutated["readings"] if r.get("vector") == "gpu_rendered"]
    assert len(hashes) >= 2, "the clean record must carry hash rows to mutate"
    for index, row in enumerate(hashes):
        row["value"] = f"deadbeef{index:08x}"

    entries = consistency_pass(mutated)
    assert contradictions(entries) == []
    assert entry_for(entries, "gpu_rendered")["classification"] == NOT_COMPARABLE


# --- null is not agreement (hazard B) ---------------------------------------


def test_a_record_whose_gpu_rows_are_all_null_is_NOT_a_pass():
    """The largest population in the corpus, and the one naive equality gets
    exactly backwards.

    A set of four identical nulls is a set of size one, so an equality check
    scores this record CONSISTENT. It established nothing at all. This is
    PS-144's distinction — "we failed to look" collapsing into "they agreed" —
    arriving through a different door.
    """
    entries = consistency_pass(load(ALL_NULL))
    entry = entry_for(entries, "gpu_claimed")
    assert entry["classification"] == COVERAGE_HOLE
    assert entry["classification"] != CONSISTENT
    assert contradictions(entries) == []
    assert coverage_holes(entries) != []


def test_a_partially_null_vector_is_a_hole_not_a_clean_run(negative):
    """Agreement among the rows that spoke is partial evidence, not a pass."""
    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("checker") == "pixelscan.net" and row.get("vector") == "gpu_claimed":
            row["value"] = None

    entry = entry_for(consistency_pass(mutated), "gpu_claimed")
    assert entry["classification"] == COVERAGE_HOLE


def test_a_rendered_placeholder_in_a_read_row_is_a_hole(negative):
    """`pixelscan.net` emits a literal "-" with state "read" on 6 records.

    A value that is present and says nothing is a coverage hole too. It is
    only visible by looking at the VALUE, which is why identifiability is
    judged from the value and not taken from `state`.
    """
    mutated = copy.deepcopy(negative)
    for row in mutated["readings"]:
        if row.get("checker") == "pixelscan.net" and row.get("vector") == "gpu_claimed":
            row["value"] = "-"
            assert row["state"] == "read"

    entry = entry_for(consistency_pass(mutated), "gpu_claimed")
    assert entry["classification"] == COVERAGE_HOLE


def test_a_contradiction_outranks_a_hole_in_the_same_vector(positive):
    """Holes elsewhere do not make a real disagreement less real."""
    mutated = copy.deepcopy(positive)
    rows = [r for r in mutated["readings"] if r.get("vector") == "gpu_claimed"]
    # Null ONE row, leaving the AMD/NVIDIA disagreement intact between others.
    for row in rows:
        if row["item"] == "gpu_vendor":
            row["value"] = None
            break

    entries = consistency_pass(mutated)
    assert [e["vector"] for e in contradictions(entries)] == ["gpu_claimed"]


# --- the identity parser itself ---------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Google Inc. (AMD)", "amd"),
        ("Google Inc. (NVIDIA)", "nvidia"),
        ("Google Inc. (Intel)", "intel"),
        (
            "ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "amd",
        ),
        (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "nvidia",
        ),
        ("ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)", "apple"),
        ("ANGLE (Qualcomm, Adreno (TM) 730, OpenGL ES 3.2)", "qualcomm"),
    ],
)
def test_identity_reads_the_IHV_from_both_value_shapes(value, expected):
    assert identity(value) == expected


def test_identity_does_not_report_google_for_every_vendor_row():
    """The trap a brand-word scan falls into.

    "Google" is the ANGLE WRAPPER and appears in every vendor value on every
    checker. A scan for brand words returns it for all of them, collapsing the
    comparison into one bucket that can never disagree — a check that silently
    always passes. The IHV is read from the position it occupies, not scanned
    for.
    """
    assert identity("Google Inc. (AMD)") == "amd"
    assert identity("Google Inc. (NVIDIA)") == "nvidia"
    # The software-rasteriser form names no hardware vendor at all.
    assert identity("Google Inc. (Google)") is None


@pytest.mark.parametrize(
    "value", [None, "", "-", "--", "n/a", "unknown", "?", "   "]
)
def test_identity_returns_none_for_values_that_name_no_hardware(value):
    """These must reach COVERAGE_HOLE, never join a consensus."""
    assert identity(value) is None


def test_adapter_text_is_only_returned_for_full_adapter_strings():
    """The finer term is deliberately partial.

    It must never be compared between a renderer and a vendor string: those
    are different KINDS of value and always differ, which is one of the two
    causes of raw equality's false alarms.
    """
    assert adapter_text("Google Inc. (AMD)") is None
    assert adapter_text("-") is None
    assert adapter_text(None) is None
    assert adapter_text(
        "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)"
    ).startswith("angle")


def test_the_hedge_is_stripped_so_two_spellings_compare_equal():
    left = "ANGLE (AMD, Radeon R9 200 Series Direct3D11 vs_5_0 ps_5_0)"
    right = left + ", or similar"
    assert adapter_text(left) == adapter_text(right)


# --- refusals (hazard A) ----------------------------------------------------


def test_a_file_with_no_readings_key_is_REFUSED_not_scored_clean():
    """13 files in the corpus carry no `readings` key at all.

    "I could not read this record" must never be reported as "this record is
    fine". Without the refusal the failure is silent and INVERTED: no rows
    yields no contradictions, and an empty finding list is the pass signal.
    """
    with pytest.raises(NotARecord):
        consistency_pass(load(NOT_A_RECORD))


def test_an_unknown_vector_is_reported_not_silently_passed(negative):
    """A vector nobody has decided about is reported, never assumed harmless.

    The conservative direction the rest of the subsystem takes with anything
    it cannot classify.
    """
    mutated = copy.deepcopy(negative)
    mutated["readings"].append(
        {
            "checker": "creepjs",
            "item": "some_new_thing",
            "vector": "not_a_vector_anyone_declared",
            "sort": "fingerprint",
            "state": "read",
            "value": "whatever",
            "adverse": False,
        }
    )
    entry = entry_for(consistency_pass(mutated), "not_a_vector_anyone_declared")
    assert entry["classification"] == NOT_COMPARABLE
    assert "never been decided" in entry["reason"]


def test_check_vector_on_a_vector_the_record_does_not_carry(negative):
    """Asking about an absent vector yields a hole, not a crash and not a pass."""
    entry = check_vector(negative, "gpu_claimed")
    assert entry["classification"] == CONSISTENT
    absent = check_vector({"readings": []}, "gpu_claimed")
    assert absent["classification"] == COVERAGE_HOLE


# --- the rendered report ----------------------------------------------------


def test_the_report_keeps_the_populations_apart(positive):
    """A contradiction, a hole and an uncompared vector are three different
    facts with three different fixes. Collapsing them trains a reader to skim.
    """
    text = format_consistency(consistency_pass(positive), source=POSITIVE)
    assert "FINDINGS" in text
    assert "NOT COMPARED" in text
    assert "gpu_claimed" in text
    assert "amd" in text and "nvidia" in text


def test_the_report_says_none_rather_than_going_quiet(negative):
    """A clean record must SAY it is clean, not print an empty section."""
    text = format_consistency(consistency_pass(negative), source=NEGATIVE)
    assert "FINDINGS — none." in text


def test_a_hole_is_never_rendered_under_the_findings_heading():
    text = format_consistency(consistency_pass(load(ALL_NULL)), source=ALL_NULL)
    assert "FINDINGS — none." in text
    assert "NOT a pass" in text


# --- the CLI ----------------------------------------------------------------


def test_cli_exits_1_on_the_record_the_ticket_names(capsys):
    """A finding about the product. Not 3: this is a real defect, not a gap."""
    assert main(["consistency", POSITIVE]) == 1
    assert "CONTRADICT THEMSELVES" in capsys.readouterr().out


def test_cli_exits_0_on_the_clean_record(capsys):
    assert main(["consistency", NEGATIVE]) == 0
    assert "FINDINGS — none." in capsys.readouterr().out


def test_cli_exits_3_on_a_record_that_established_nothing(capsys):
    """NOT 0. A record whose GPU rows are all null did not agree about
    anything, and exiting clean is how that becomes invisible."""
    assert main(["consistency", ALL_NULL]) == 3


def test_cli_exits_2_on_a_file_that_is_not_a_record(capsys):
    """A refusal can never wear a code that means something was established."""
    assert main(["consistency", NOT_A_RECORD]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_cli_exits_2_on_a_missing_file():
    assert main(["consistency", os.path.join(READINGS, "no-such-file.json")]) == 2


# --- the corpus census, asserted -------------------------------------------


def discover() -> "list[str]":
    found = []
    for dirpath, _dirnames, filenames in os.walk(READINGS):
        for name in sorted(filenames):
            if name.endswith(".json"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def test_the_check_fires_on_exactly_three_records_in_the_whole_corpus():
    """The rule's corpus behaviour, asserted rather than described.

    The ticket requires the PR to say which rows the rule fires on, and that
    "if it fires on records that are actually fine, that is a finding to
    report, not a threshold to quietly tune until the noise stops". Pinning
    the census here means a future change to the rule that starts flagging
    clean records fails loudly instead of being absorbed.

    Three positives, all the same AMD-vs-NVIDIA split: ps143 arm-a, ps150
    arm-a, ps150 arm-b.
    """
    flagged = []
    for path in discover():
        try:
            record = load(path)
        except (OSError, json.JSONDecodeError):  # pragma: no cover
            continue
        try:
            entries = consistency_pass(record)
        except NotARecord:
            continue
        if contradictions(entries):
            flagged.append(os.path.relpath(path, READINGS))

    assert sorted(flagged) == [
        os.path.join("ps143-2026-08-24", "arm-a-layer-on.json"),
        os.path.join("ps150-2026-08-24", "arm-a-baseline-layer-on.json"),
        os.path.join("ps150-2026-08-24", "arm-b-geo-gap-closed.json"),
    ]


def test_no_committed_record_crashes_the_check():
    """Hazard A: three record schemas live under `readings/`.

    Every file is either judged or refused with a reason. None may raise
    anything else — a checker that crashes on a legitimate sibling artifact
    stops ranging over the corpus it exists to read.
    """
    for path in discover():
        try:
            record = load(path)
        except (OSError, json.JSONDecodeError):  # pragma: no cover
            continue
        try:
            consistency_pass(record)
        except NotARecord:
            continue  # refused, with a reason — the correct outcome
