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
    HOST_LEAK,
    NOT_COMPARABLE,
    SOFTWARE_RASTERISER,
    adapter_text,
    check_vector,
    consistency_pass,
    contradictions,
    coverage_holes,
    findings,
    format_consistency,
    host_leaks,
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
    assert "NOT a pass" in text
    # The heading must NOT be the unqualified "none." that a genuinely clean
    # record earns. See the test below for the positive form of this claim.
    assert "FINDINGS — none." not in text


def test_a_record_that_established_nothing_does_not_claim_a_clean_headline():
    """"I looked and found nothing" and "I could not look" are DIFFERENT
    STATEMENTS, and the headline is where they were being collapsed.

    This is the wider defect behind the SwiftShader case: 13 of the 21
    readable records in ``readings/`` route to a coverage hole, and every one
    of them printed the same unqualified "FINDINGS — none." that a clean
    record prints. A reader who stops at the headline — which is what a
    headline is FOR — was told a record established agreement when it
    established nothing at all.
    """
    text = format_consistency(consistency_pass(load(ALL_NULL)), source=ALL_NULL)
    headline = next(line for line in text.splitlines() if "FINDINGS" in line)
    assert "COULD NOT BE READ" in headline
    assert "NOT a clean record" in headline


def test_the_clean_headline_is_still_reachable(negative):
    """The corollary, and the reason the test above can fail.

    If a hole and a pass BOTH printed a qualified headline, the assertion
    above would hold for a check that never reports anything clean. A real
    clean record must still earn the unqualified form.
    """
    text = format_consistency(consistency_pass(negative), source=NEGATIVE)
    headline = next(line for line in text.splitlines() if "FINDINGS" in line)
    assert "FINDINGS — none." in headline
    assert "COULD NOT BE READ" not in headline


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


# --- the host showing through ----------------------------------------------
#
# The case the check was BLIND to when PR #127 was first submitted: a
# software-rasteriser value mapped to `None`, the row was dropped, the vector
# routed to a coverage hole, and the report printed "FINDINGS — none" over a
# leak of the real machine. 0 of the 48 tests then present covered it.
#
# `environment` on every record in the corpus is `linux-x86_64 (agent
# sandbox)`, a host with NO GPU — so this is not a hypothetical shape. It is
# what a spoof that stops covering one read path actually produces here, and
# the ticket ranks it worse than the contradiction that motivated the module:
# "a leak of the true adapter is a materially worse finding than an
# inconsistent spoof".
#
# Both spellings are mutated from a COMMITTED record on ONE axis, exactly as
# the falsification tests above are. A fixture row is legitimate here (and the
# planner said so explicitly): the value is a STRING A CHECKER RETURNS, not a
# rendered artifact, so the fixture is the real thing rather than a stand-in.

SWIFTSHADER = (
    "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) "
    "(0x0000C0DE)), SwiftShader driver)"
)
LLVMPIPE = "ANGLE (Mesa/X.org, llvmpipe (LLVM 15.0.7, 256 bits), OpenGL 4.5)"


def leak_one_checker(record: dict, renderer: str, vendor: str) -> dict:
    """One checker leaks the host; the other keeps its spoof."""
    mutated = copy.deepcopy(record)
    for row in mutated["readings"]:
        if row.get("vector") != "gpu_claimed":
            continue
        if row.get("checker") != "pixelscan.net":
            continue
        if row["item"] == "webgl_renderer":
            row["value"] = renderer
        elif row["item"] == "webgl_vendor":
            row["value"] = vendor
    return mutated


@pytest.mark.parametrize(
    "renderer,vendor",
    [(SWIFTSHADER, "Google Inc. (Google)"), (LLVMPIPE, "Mesa/X.org")],
    ids=["swiftshader", "llvmpipe"],
)
def test_a_leaked_host_is_reported_not_filed_as_a_coverage_hole(
    positive, renderer, vendor
):
    """THE REGRESSION TEST for the defect that failed the audit.

    Before the fix this asserted-on record printed "FINDINGS — none" and
    exited 3. pixelscan DID speak — it said SwiftShader — so filing it beside
    a `None` as "these rows did not disagree because they did not speak" was
    not merely quiet, it was false.
    """
    entries = consistency_pass(leak_one_checker(positive, renderer, vendor))
    leaks = host_leaks(entries)

    assert [e["vector"] for e in leaks] == ["gpu_claimed"]
    assert entry_for(entries, "gpu_claimed")["classification"] == HOST_LEAK
    # And it is NOT filed as the thing it used to be filed as.
    assert coverage_holes(entries) == []


@pytest.mark.parametrize(
    "renderer,vendor",
    [(SWIFTSHADER, "Google Inc. (Google)"), (LLVMPIPE, "Mesa/X.org")],
    ids=["swiftshader", "llvmpipe"],
)
def test_a_leak_is_a_finding_even_when_every_checker_agrees_on_it(
    positive, renderer, vendor
):
    """The WORST case, and the one a pair-based rule can never catch.

    When the spoof fails completely, every checker reads the same host
    rasteriser. There is no disagreement anywhere in the record — so a check
    that only ever looks for a PAIR of differing values finds nothing and the
    record reads clean. A leak needs no second row to be a finding, which is
    exactly why HOST_LEAK is satisfied by one row and is tested here without
    any contradiction present.
    """
    mutated = copy.deepcopy(positive)
    for row in mutated["readings"]:
        if row.get("vector") != "gpu_claimed":
            continue
        if "renderer" in str(row.get("item")):
            row["value"] = renderer
        elif "vendor" in str(row.get("item")):
            row["value"] = vendor

    entries = consistency_pass(mutated)
    entry = entry_for(entries, "gpu_claimed")

    assert entry["classification"] == HOST_LEAK
    # There is genuinely no contradiction here — the rows AGREE.
    assert contradictions(entries) == []
    assert "every checker saw the host" in entry["reason"]


@pytest.mark.parametrize(
    "renderer,vendor",
    [(SWIFTSHADER, "Google Inc. (Google)"), (LLVMPIPE, "Mesa/X.org")],
    ids=["swiftshader", "llvmpipe"],
)
def test_the_leak_headline_does_not_say_findings_none(positive, renderer, vendor):
    """The report is the deliverable, not just the classification.

    Exit 3 was always non-zero, which limited the blast radius for a
    machine-read gate — but a HUMAN reads the headline, and the headline said
    the opposite of the truth. This asserts on what a person actually sees.
    """
    mutated = leak_one_checker(positive, renderer, vendor)
    text = format_consistency(consistency_pass(mutated), source="mutated")
    headline = next(line for line in text.splitlines() if "FINDINGS" in line)

    assert "LEAKED THE HOST MACHINE" in headline
    assert "FINDINGS — none" not in text


@pytest.mark.parametrize(
    "renderer,vendor",
    [(SWIFTSHADER, "Google Inc. (Google)"), (LLVMPIPE, "Mesa/X.org")],
    ids=["swiftshader", "llvmpipe"],
)
def test_cli_exits_1_on_a_leaked_host(tmp_path, capsys, positive, renderer, vendor):
    """A finding ABOUT THE PRODUCT, so 1 — never 3.

    3 means "no finding, but the coverage is not what you think". A leak is a
    finding, and the code it exits under is what a CI gate reads.

    THE EXIT CODE ALONE IS NOT ENOUGH, and this is not caution — it is
    measured. With the leak rule reverted, the llvmpipe case exits 1 ANYWAY:
    `Mesa/X.org` happens to parse as an IHV, which happens to disagree with
    the AMD rows, so the record trips the CONTRADICTION rule by coincidence.
    A test asserting only on the code passes for a reason it did not intend
    and would not have caught this defect. So the report is asserted too: it
    must be reported AS A LEAK, not as an incidental disagreement.
    """
    path = tmp_path / "leak.json"
    path.write_text(
        json.dumps(leak_one_checker(positive, renderer, vendor)), encoding="utf-8"
    )
    assert main(["consistency", str(path)]) == 1
    assert "LEAKED THE HOST MACHINE" in capsys.readouterr().out


def test_a_software_rasteriser_is_not_confused_with_a_missing_value():
    """The distinction the whole class exists to draw, at the unit level.

    `None` means "this row said nothing". SOFTWARE_RASTERISER means "this row
    said something, and what it said is alarming". Collapsing the second into
    the first is the defect.
    """
    assert identity(None) is None
    assert identity("-") is None
    assert identity(SWIFTSHADER) == SOFTWARE_RASTERISER
    assert identity(LLVMPIPE) == SOFTWARE_RASTERISER


def test_the_bare_wrapper_vendor_is_left_as_unidentified_deliberately():
    """`Google Inc. (Google)` stays `None`, and that is a DECISION.

    It is the vendor string that accompanies a SwiftShader render, so it is
    tempting to mark it as a leak too. It is not marked, because on its own it
    names no rasteriser — it says only "the wrapper named itself", which is
    weaker evidence than the renderer string that actually spells SwiftShader.
    Treating a suggestive value as proof is the kind of overclaim this project
    keeps retracting.

    Nothing is lost by the restraint: the RENDERER row in the same vector
    carries the real marker and fires the leak, so the vector is reported
    either way. This test exists so that decision is visible rather than
    mistaken for the bug that was just fixed.
    """
    assert identity("Google Inc. (Google)") is None


def test_real_hardware_is_never_read_as_a_software_rasteriser():
    """The falsification direction: the marker must not fire on real adapters.

    Bare "Mesa" is the trap — `Mesa DRI Intel(R) HD Graphics 620` is a real
    Intel adapter on a real Linux machine. Mesa is the driver stack, not the
    rasteriser; only the software devices it can fall back to are markers. A
    rule that matched "mesa" would report a leak on ordinary Linux hardware.
    """
    for value in (
        "ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 "
        "vs_5_0 ps_5_0, D3D11)",
        "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) Direct3D11 "
        "vs_5_0 ps_5_0, D3D11)",
        "Google Inc. (AMD)",
        "Google Inc. (NVIDIA)",
        "Mesa DRI Intel(R) HD Graphics 620",
        "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ):
        assert identity(value) != SOFTWARE_RASTERISER


def test_no_committed_record_is_read_as_a_leaked_host():
    """The false-alarm measurement, pinned.

    Zero values in the whole committed corpus carry a software-rasteriser
    marker, so adding this class changed no existing verdict — the census
    below is byte-identical either side of the fix. If a future marker starts
    firing on a real record, that is a FINDING to report (per the ticket's
    standing instruction), and this test is what surfaces it rather than
    letting it be absorbed.
    """
    for path in discover():
        try:
            record = load(path)
        except (OSError, json.JSONDecodeError):  # pragma: no cover
            continue
        try:
            entries = consistency_pass(record)
        except NotARecord:
            continue
        assert host_leaks(entries) == [], f"unexpected leak verdict on {path}"


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
        if findings(entries):
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
