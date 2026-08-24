"""The checker-matrix comparator, proven against the record it actually reads.

Every case here is a MUTATION OF THE COMMITTED RECORD
(``tests/fixtures/checker-matrix-reading.sandbox.json``), not a hand-built
dict, and that is deliberate rather than incidental. There is exactly ONE
committed reading — one revision, one exit, one carrier, one machine — so the
second record is constructed by moving EXACTLY the value under test. One
mutation per case is what makes the expected classification unambiguous; a pair
of independently-captured live records never would be, because everything moves
at once and nothing can be attributed.

It is also what the ticket's "show it failing" outcome asks for: a comparator
observed only on identical inputs has not been observed. So each classification
below is produced by an actual mutation and asserted, including every refusal.

Two properties of the committed record shape these tests, and both come from
``tests/fixtures/README.checker-matrix.md`` rather than from guesswork:

* **It carries 24 UNOBTAINABLE rows** — a mobile-exit DNS outage mid-run, plus
  the permanently unreadable checkers (click-gated, Cloudflare, paywalled).
  Comparing the record with itself must therefore report those rows (an
  unobtainable reading is never a pass) while reporting ZERO findings and zero
  lost coverage. That is ``test_record_against_itself_*``.
* **It has no READ ``host`` row at all** — all three were lost to that outage.
  So the two host tests mutate BOTH sides to make a readable host row exist.
  This is called out because it is the one place these tests cannot derive the
  "before" side from the committed record unchanged.
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from src.services.verify.checker_cli import main
from src.services.verify.matrix_diff import (
    APPEARED,
    COUPLING,
    COVERAGE_LOST,
    COVERAGE_REGAINED,
    ComparisonNotControlled,
    EXIT_ROTATED,
    FINDINGS,
    FINGERPRINT_MOVED,
    HARNESS_MOVED,
    HARNESS_SECTION,
    HOST_MACHINE_DIFFERS,
    HOST_MOVED,
    MACHINE_EXPLAINED,
    NotARecord,
    REWORDED,
    RecordUnreadable,
    SEED_EXPLAINED,
    UNREAD_BOTH,
    UNSORTED_MOVED,
    VANISHED,
    compare_records,
    coverage_lost,
    findings,
    format_comparison,
    header_notes,
    no_evidence,
    observed_count,
    require_record,
)

RECORD_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "checker-matrix-reading.sandbox.json"
)

# A row that is READ in the committed record, on each sort that has one.
# Picked once here so a catalogue change breaks one constant rather than a
# dozen tests.
A_FINGERPRINT_ROW = ("bot.sannysoft.com", "webdriver_advanced_passed")
AN_EXIT_ROW = ("engine-exit", "observed_ip")
A_HARNESS_ROW = ("tls.browserleaks.com", "ja4")
# UNOBTAINABLE in the committed record — one of the outage rows the README
# warns must not be read as a regression when a later run finally reads it.
AN_OUTAGE_ROW = ("pixelscan.net", "proxy_detected")
# Unobtainable and permanently so: a Cloudflare challenge, out of scope by
# charter. Its sort is empty.
AN_UNTAGGED_ROW = ("whoer.net", "(whole checker)")


@pytest.fixture
def record() -> dict:
    with open(RECORD_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def mutate(record: dict) -> dict:
    """A deep copy to move exactly one value in."""
    return copy.deepcopy(record)


def row(record: dict, key: "tuple[str, str]") -> dict:
    checker, item = key
    for reading in record["readings"]:
        if reading["checker"] == checker and reading["item"] == item:
            return reading
    raise AssertionError(f"the committed record has no row {key!r}")


def entry_for(entries: "list[dict]", key: "tuple[str, str]") -> dict:
    checker, item = key
    for entry in entries:
        if entry["checker"] == checker and entry["item"] == item:
            return entry
    raise AssertionError(
        f"no entry for {key!r}; got "
        f"{[(e['checker'], e['item'], e['classification']) for e in entries]}"
    )


def make_unobtainable(reading: dict, reason: str = "Error: NS_ERROR_UNKNOWN_HOST") -> None:
    reading["state"] = "unobtainable"
    reading.pop("value", None)
    reading["reason"] = reason


def make_read(reading: dict, value) -> None:
    reading["state"] = "read"
    reading["value"] = value
    reading.pop("reason", None)


# --- the record against itself ---------------------------------------------


def test_record_against_itself_reports_no_finding_and_no_lost_coverage(record):
    """The one comparison that must be quiet — but not silent.

    Every row read the same, so nothing moved and no coverage was lost. The 24
    unobtainable rows are still REPORTED (see the next test); what this pins is
    that none of them is mistaken for a difference or for a run that failed.
    """
    entries = compare_records(record, mutate(record))

    assert findings(entries) == []
    assert coverage_lost(entries) == []
    assert observed_count(entries) == 0


def test_record_against_itself_still_reports_its_unobtainable_rows(record):
    """An unobtainable reading is never a pass — PS-29's rule, on this artifact.

    The committed record carries 24 rows nobody could read. Comparing it with
    itself must NOT return an empty list: two identical failures are the same
    row failing twice, not two readings agreeing, and reporting nothing would
    quietly claim a coverage the record does not have.
    """
    entries = compare_records(record, mutate(record))

    unread = [e for e in entries if e["classification"] == UNREAD_BOTH]
    assert len(unread) == record["counts"]["unobtainable"] == 24
    assert all(e["observed"] is False for e in unread)


def test_readable_rows_that_agree_produce_no_entry_at_all(record):
    """The silent pass, and the reason the report is short enough to be read.

    22 rows read cleanly and 7 are absent; none of them may appear in the
    output. A run that printed all 53 rows would not be read, and an unread
    comparison is the same as no comparison.
    """
    entries = compare_records(record, mutate(record))

    reported = {(e["checker"], e["item"]) for e in entries}
    assert A_FINGERPRINT_ROW not in reported
    assert AN_EXIT_ROW not in reported
    assert A_HARNESS_ROW not in reported


# --- the sort decides how a difference is reported --------------------------


def test_moved_fingerprint_row_on_a_held_exit_is_a_finding(record):
    after = mutate(record)
    row(after, A_FINGERPRINT_ROW)["value"] = False

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == FINGERPRINT_MOVED
    assert entry["section"] == FINDINGS
    assert entry["observed"] is True
    assert entry in findings(entries)


def test_moved_fingerprint_row_when_the_exit_ALSO_moved_is_a_coupling(record):
    """The finding the whole apparatus exists to surface.

    A fingerprint-driven reading that moves when only the address moved is a
    COUPLING — the entire return on accepting a rotating exit. It must outrank
    the plain fingerprint movement, so this is the case the classification
    exists to separate.
    """
    after = mutate(record)
    row(after, A_FINGERPRINT_ROW)["value"] = False
    after["exit"] = dict(after["exit"], ip="31.0.0.9", city="Kraków")

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == COUPLING
    assert entry["section"] == FINDINGS
    # And it is the FIRST thing on screen: loudest is not a label, it is rank.
    assert entries[0] is entry


def test_moved_exit_row_is_context_not_a_problem(record):
    """The exit rotates by design. This must never be a finding."""
    after = mutate(record)
    row(after, AN_EXIT_ROW)["value"] = "31.0.0.9"

    entries = compare_records(record, after)
    entry = entry_for(entries, AN_EXIT_ROW)

    assert entry["classification"] == EXIT_ROTATED
    assert entry["section"] != FINDINGS
    assert findings(entries) == []


def test_moved_host_row_on_the_SAME_machine_deserves_attention(record):
    """Both sides mutated — the committed record has no READ host row (outage)."""
    before = mutate(record)
    after = mutate(record)
    make_read(row(before, ("pixelscan.net", "webgl_renderer")), "llvmpipe")
    make_read(row(after, ("pixelscan.net", "webgl_renderer")), "Intel HD 400")

    entries = compare_records(before, after)
    entry = entry_for(entries, ("pixelscan.net", "webgl_renderer"))

    assert entry["classification"] == HOST_MOVED
    assert entry["section"] == FINDINGS


def test_the_same_host_row_across_TWO_machines_does_not(record):
    """The record says which machine it came from, so use it.

    Identical mutation to the test above; the ONLY difference is the
    ``environment`` header. A comparator that treated every host difference
    alike would classify these two identically, so this pair is what proves the
    machine is actually being read.
    """
    before = mutate(record)
    after = mutate(record)
    make_read(row(before, ("pixelscan.net", "webgl_renderer")), "llvmpipe")
    make_read(row(after, ("pixelscan.net", "webgl_renderer")), "Intel HD 400")
    after["environment"] = "darwin-arm64 (operator laptop)"

    entries = compare_records(before, after)
    entry = entry_for(entries, ("pixelscan.net", "webgl_renderer"))

    assert entry["classification"] == HOST_MACHINE_DIFFERS
    assert entry["section"] != FINDINGS
    assert findings(entries) == []


def test_moved_harness_row_is_reported_apart_from_the_product(record):
    """The mistake PS-59 caught in its own work and retagged.

    The JSON tier is fetched by this repo's own Python client, so a Python or
    OpenSSL upgrade moves these rows. Reported under their own heading so one
    can never be triaged as persona's fingerprint moving — a false alarm of
    exactly the kind that makes a real one unbelievable.
    """
    after = mutate(record)
    row(after, A_HARNESS_ROW)["value"] = "t13d1234_deadbeef_cafebabe"

    entries = compare_records(record, after)
    entry = entry_for(entries, A_HARNESS_ROW)

    assert entry["classification"] == HARNESS_MOVED
    assert entry["section"] == HARNESS_SECTION
    assert findings(entries) == []
    assert "not the product" in format_comparison(entries)


def test_an_untagged_row_that_moved_is_reported_with_the_findings(record):
    """Conservative: an unclassifiable row could be fingerprint-driven.

    Filing it under a harmless heading would be the one direction that hides a
    real movement, so it goes with the findings and says the sort is missing.
    """
    before = mutate(record)
    after = mutate(record)
    make_read(row(before, AN_UNTAGGED_ROW), "clean")
    make_read(row(after, AN_UNTAGGED_ROW), "suspicious")

    entries = compare_records(before, after)
    entry = entry_for(entries, AN_UNTAGGED_ROW)

    assert entry["classification"] == UNSORTED_MOVED
    assert entry["section"] == FINDINGS
    assert "untagged" in format_comparison(entries)


# --- state movements: evidence vs the absence of it -------------------------


def test_read_to_absent_is_a_real_verdict_movement(record):
    """``absent`` is an OBSERVATION, not a hole.

    The checker answered and did not say this — for an adverse item that is the
    good news. So a row moving read -> absent is the verdict moving, and on a
    fingerprint row it is a finding. This is the state ``diff.py``'s schema has
    no equivalent for, so it is pinned explicitly.
    """
    after = mutate(record)
    reading = row(after, A_FINGERPRINT_ROW)
    reading["state"] = "absent"
    reading.pop("value", None)
    reading["reason"] = "the pattern did not match"

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == FINGERPRINT_MOVED
    assert entry["observed"] is True


def test_a_row_that_became_unreadable_is_NOT_a_row_that_changed(record):
    """``read`` -> ``unobtainable`` means the RUN failed, not that the product moved.

    The deliberate divergence from ``diff.py``, which calls this CHANGED. Here
    an unobtainable row overwhelmingly means the network died — the committed
    record has 24 from one outage — so reporting these as differences would
    fill the output with red on exactly the runs that already went badly, and
    train the reader to skim.
    """
    after = mutate(record)
    make_unobtainable(row(after, A_FINGERPRINT_ROW))

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == COVERAGE_LOST
    assert entry["observed"] is False
    assert findings(entries) == []
    assert entry in coverage_lost(entries)


def test_a_row_that_became_readable_is_not_a_regression(record):
    """The README's explicit warning, pinned.

    Many browser-tier rows in the committed record are unobtainable. "Do not
    treat the first later run that actually reads them as a regression."
    """
    after = mutate(record)
    make_read(row(after, AN_OUTAGE_ROW), True)

    entries = compare_records(record, after)
    entry = entry_for(entries, AN_OUTAGE_ROW)

    assert entry["classification"] == COVERAGE_REGAINED
    assert entry["observed"] is False
    assert findings(entries) == []
    assert coverage_lost(entries) == []


def test_the_iphey_pair_absent_on_both_sides_is_not_reported_as_movement(record):
    """The README's other careful case.

    ``iphey.com`` rendered, so its rows are ABSENT rather than unobtainable —
    but BOTH polarity items are absent, which read together mean the verdict
    block never rendered. Two records that agree on that pair have not observed
    anything moving, and neither row may be reported.
    """
    entries = compare_records(record, mutate(record))
    reported = {(e["checker"], e["item"]) for e in entries}

    iphey = [r for r in record["readings"] if r["checker"] == "iphey.com"]
    absent_pair = [r for r in iphey if r["state"] == "absent"]
    assert len(absent_pair) >= 2, "the README's iphey case is gone from the record"
    for reading in absent_pair:
        assert (reading["checker"], reading["item"]) not in reported


def test_a_reworded_page_is_not_a_changed_verdict(record):
    """Two facts that are indistinguishable if only the boolean is kept."""
    after = mutate(record)
    reading = row(after, A_FINGERPRINT_ROW)
    reading["matched_text"] = "WebDriver Advanced\tPASSED"

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == REWORDED
    assert findings(entries) == []


# --- a verdict that moved WITHIN one reading (PS-121) -----------------------


def test_a_browser_caught_by_MORE_tests_is_not_a_silent_pass(record):
    """The defect this pins is invisible from the reader alone.

    ``bot-detector.rebrowser.net`` renders one row per test, so "how many
    tests caught us" lives in the NUMBER of adverse rows. A reader that
    records the first match alone collapses that to one name — and because
    ``_verdict`` compares ``(state, value)`` only, a browser going from one
    detection to three lands in the "read on both sides and agreed" branch and
    is classified ``None``. Nothing is reported at all.

    So this drives the SHIPPED reader over both pages and compares the two
    readings through the real comparator, rather than asserting on the value
    it happens to produce. Level 3's contract is "the result may not regress";
    a detection count that can triple in silence is a regression the matrix
    cannot see.
    """
    from src.services.verify.checkers import checker_by_id
    from src.services.verify.matrix import extract_text_item

    checker = checker_by_id("bot-detector.rebrowser.net")
    item = next(i for i in checker.items if i.id == "detected")
    header = "Test name\tTime since load\tNotes\n"
    one_red = header + (
        "\U0001F534 mainWorldExecution\t2 ms\tYou've called …ByClassName().\n"
    )
    three_reds = one_red + (
        "\U0001F534 exposeFunctionLeak\t3 ms\tunpatched Playwright.\n"
        "\U0001F534 navigatorWebdriver\t4 ms\tnavigator.webdriver = true.\n"
    )

    key = ("bot-detector.rebrowser.net", "detected")
    before, after = mutate(record), mutate(record)
    for target, text in ((before, one_red), (after, three_reds)):
        reading = row(target, key)
        result = extract_text_item(checker, item, text)
        assert result.state == "read", text
        reading["state"] = "read"
        reading["value"] = result.value
        reading["pattern"] = result.pattern
        reading["matched_text"] = result.matched_text
        reading.pop("reason", None)

    entries = compare_records(before, after)
    entry = entry_for(entries, key)

    assert entry["classification"] == FINGERPRINT_MOVED, (
        "a browser caught by two MORE tests reported "
        f"{entry['classification']!r} — the detection count grew and the "
        "comparator saw no change"
    )
    assert entry["section"] == FINDINGS
    assert entry["observed"] is True
    # ...and it is a real verdict movement, NOT merely the page rewording.
    assert entry["classification"] != REWORDED


# --- the catalogue moving ---------------------------------------------------


def test_a_row_that_vanished_is_reported_as_such_not_as_drift(record):
    after = mutate(record)
    after["readings"] = [
        r
        for r in after["readings"]
        if (r["checker"], r["item"]) != A_FINGERPRINT_ROW
    ]

    entries = compare_records(record, after)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == VANISHED
    assert entry["after"] == {"missing": True}
    assert findings(entries) == []


def test_a_row_that_appeared_is_reported_as_such_not_as_drift(record):
    """PS-62's case, ahead of time.

    Engine-driven TLS rows will arrive as NEW rows in an existing catalogue.
    Getting this right now is what stops that landing as a false alarm.
    """
    after = mutate(record)
    after["readings"].append(
        {
            "adverse": False,
            "checker": "tls.peet.ws",
            "item": "engine_ja4",
            "sort": "fingerprint",
            "state": "read",
            "value": "t13d1516h2_8daaf6152771_b0da82dd1658",
        }
    )

    entries = compare_records(record, after)
    entry = entry_for(entries, ("tls.peet.ws", "engine_ja4"))

    assert entry["classification"] == APPEARED
    assert entry["before"] == {"missing": True}
    assert findings(entries) == []


def test_an_added_row_that_was_never_read_is_not_an_observed_movement(record):
    """The inventory moved; no reading did."""
    after = mutate(record)
    after["readings"].append(
        {
            "adverse": False,
            "checker": "tls.peet.ws",
            "item": "engine_ja4",
            "sort": "fingerprint",
            "state": "unobtainable",
            "reason": "Error: NS_ERROR_UNKNOWN_HOST",
        }
    )

    entries = compare_records(record, after)
    entry = entry_for(entries, ("tls.peet.ws", "engine_ja4"))

    assert entry["classification"] == APPEARED
    assert entry["observed"] is False


# --- refusals ---------------------------------------------------------------


def test_different_seeds_are_REFUSED_not_diffed(record):
    """Without this the fingerprint rows read as catastrophic drift.

    The engine's fingerprint is seed-derived: two runs on different seeds
    present two different identities, so those rows were never supposed to
    match. Measured on this project — the renderer moved ``NVIDIA GTX 980`` ->
    ``Intel HD Graphics 400`` between two runs purely because the seed
    differed.
    """
    after = mutate(record)
    after["seed"] = 999

    with pytest.raises(ComparisonNotControlled) as excinfo:
        compare_records(record, after)
    assert "seed" in str(excinfo.value).lower()


def test_a_MISSING_seed_is_refused_and_no_flag_relaxes_it(record):
    """A fact no flag can supply after the fact."""
    after = mutate(record)
    del after["seed"]

    with pytest.raises(ComparisonNotControlled):
        compare_records(record, after, allow_different_seed=True)


def test_seed_zero_is_a_real_seed_not_a_missing_one(record):
    """``0`` means the engine's own default was used — a recorded fact.

    Refusing on falsiness rather than on the key's absence would refuse every
    default-seed comparison, which is the common case.
    """
    before = mutate(record)
    after = mutate(record)
    before["seed"] = after["seed"] = 0

    compare_records(before, after)  # must not raise


def test_the_seed_override_reports_context_never_a_coupling(record):
    """An override must not be able to manufacture the loudest finding.

    With ``--allow-different-seed`` the operator has weighed the caveat and
    wants the exit/host/harness rows. The fingerprint rows stay reported — but
    as seed-explained context, because under a different seed their movement is
    the expected consequence of the override, not evidence of a coupling.
    """
    after = mutate(record)
    after["seed"] = 999
    row(after, A_FINGERPRINT_ROW)["value"] = False

    entries = compare_records(record, after, allow_different_seed=True)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == SEED_EXPLAINED
    assert entry["section"] != FINDINGS
    assert findings(entries) == []


# --- the declared machine (PS-69 landed this field mid-ticket) --------------
#
# PS-69 widened the reader to both engines and more than one DECLARED machine,
# adding `declared_machine` to the header. Its own reasoning is the argument
# these tests encode: "without it in the header a later comparison cannot tell
# a real coupling from a different configuration, which is exactly the argument
# that put the seed here."
#
# Note the seam these tests sit on. PS-69 added the field WITHOUT bumping
# SCHEMA_VERSION, so for a window a pre-PS-69 record and a post-PS-69 one both
# said `schema_version: 1` and the schema guard could not separate them.
#
# PS-81 closed that at the source (`matrix.HEADER_GENERATIONS`; the writer now
# emits 2), but the fix is NOT retroactive — a record written during the drift
# window still claims 1 while carrying generation 2's keys. So the missing-field
# cases below are still handled per-side rather than by the seed's "absent on
# either side refuses" rule, and they must keep passing: they are what covers
# the records the bump arrived too late for.


def test_different_declared_machines_are_REFUSED_not_diffed(record):
    """Otherwise EVERY fingerprint row across two machines reads as a coupling.

    The declared machine is the spine of a presented identity — it constrains
    GPU strings, voices, fonts, screen conventions, platform flags, the user
    agent and client hints — so those rows were never supposed to match.
    """
    before = mutate(record)
    after = mutate(record)
    before["declared_machine"] = "windows"
    after["declared_machine"] = "macos"

    with pytest.raises(ComparisonNotControlled) as excinfo:
        compare_records(before, after)
    assert "machine" in str(excinfo.value).lower()


def test_the_machine_override_reports_context_never_a_coupling(record):
    """The same rule as the seed override: a flag may not manufacture a finding."""
    before = mutate(record)
    after = mutate(record)
    before["declared_machine"] = "windows"
    after["declared_machine"] = "macos"
    row(before, A_FINGERPRINT_ROW)["value"] = True
    row(after, A_FINGERPRINT_ROW)["value"] = False

    entries = compare_records(before, after, allow_different_machine=True)
    entry = entry_for(entries, A_FINGERPRINT_ROW)

    assert entry["classification"] == MACHINE_EXPLAINED
    assert entry["section"] != FINDINGS
    assert findings(entries) == []


def test_two_records_that_BOTH_predate_the_field_still_compare(record):
    """The committed record has no `declared_machine` — it is the whole corpus.

    Refusing on a field neither record carries would make the comparator
    useless on every reading taken before PS-69, including the only one that
    exists.
    """
    assert "declared_machine" not in record

    compare_records(record, mutate(record))  # must not raise


def test_the_field_present_on_only_ONE_side_is_refused(record):
    """The `None == None` error, one field over — and the reason it matters here.

    PS-69 did not bump SCHEMA_VERSION, so this pair is NOT caught by the schema
    guard: both records say version 1. Treating "no field" as "same machine"
    would silently compare a pre-PS-69 reading against a post-PS-69 one and
    report a configuration change as a coupling.
    """
    after = mutate(record)
    after["declared_machine"] = "macos"

    with pytest.raises(ComparisonNotControlled) as excinfo:
        compare_records(record, after)
    assert "machine" in str(excinfo.value).lower()


def test_the_same_declared_machine_compares_without_an_override(record):
    before = mutate(record)
    after = mutate(record)
    before["declared_machine"] = after["declared_machine"] = "windows"

    compare_records(before, after)  # must not raise


def test_the_host_machine_and_the_declared_machine_are_NOT_the_same_question(
    record,
):
    """One laptop can declare Windows or macOS; the two fields are independent.

    `environment` is the host the reading was taken ON and governs host-sorted
    rows. `declared_machine` is what the profile PRESENTED and governs
    fingerprint-sorted rows. A report that said "different machines" for both
    would send a reader to triage the wrong one, so the two notes are worded
    apart.
    """
    before = mutate(record)
    after = mutate(record)
    before["declared_machine"] = "windows"
    after["declared_machine"] = "macos"

    notes = "\n".join(header_notes(before, after))

    assert "DECLARED MACHINE DIFFERS" in notes
    # Same host both sides, so the host line must NOT claim a difference.
    assert "same host machine" in notes
    assert "DIFFERENT HOST MACHINES" not in notes


def test_different_engine_builds_are_REFUSED_not_diffed(record):
    after = mutate(record)
    after["engine"] = "invisible_playwright/firefox-21"

    with pytest.raises(ComparisonNotControlled) as excinfo:
        compare_records(record, after)
    assert "engine" in str(excinfo.value).lower()


def test_the_cross_engine_override_compares(record):
    after = mutate(record)
    after["engine"] = "invisible_playwright/firefox-21"

    entries = compare_records(record, after, allow_cross_engine=True)
    assert findings(entries) == []


def test_an_UNRECORDED_engine_is_refused_even_with_the_override(record):
    """``--allow-cross-engine`` opts in to a KNOWN, NAMED difference.

    Two records that both omit the field must not slip through on
    ``None == None``, which reads as "same engine" while meaning "no idea".
    An unrecorded engine gives the operator nothing to weigh.
    """
    before = mutate(record)
    after = mutate(record)
    del before["engine"]
    del after["engine"]

    with pytest.raises(ComparisonNotControlled):
        compare_records(before, after, allow_cross_engine=True)


def test_a_schema_version_mismatch_is_refused(record):
    """The row vocabulary itself changed, so nothing is left to be careful WITH."""
    after = mutate(record)
    after["schema_version"] = 2

    with pytest.raises(ComparisonNotControlled) as excinfo:
        compare_records(record, after)
    assert "schema" in str(excinfo.value).lower()


def test_a_file_that_is_not_a_record_is_refused_never_reported_as_clean(record):
    """PS-41's rule on this artifact, and the dangerous direction is the PASS.

    Without this the failure is silent and INVERTED: no ``readings`` means zero
    rows compare, the comparator returns ``[]``, and an empty list is the
    "nothing moved" signal. The tool would be at its most confident exactly
    when it holds the least evidence.
    """
    with pytest.raises(NotARecord):
        compare_records(record, {"not": "a record"})
    with pytest.raises(NotARecord):
        require_record({"readings": "not a list"})


def test_a_moved_exit_does_not_refuse(record):
    """Two records taken through two exits is the NORMAL case, not an error."""
    after = mutate(record)
    after["exit"] = dict(after["exit"], ip="31.0.0.9", city="Kraków")

    compare_records(record, after)  # must not raise


# --- the report -------------------------------------------------------------


def test_an_empty_comparison_states_what_it_does_and_does_not_claim(record):
    """"no differences" is exactly the string a reader over-reads."""
    text = format_comparison([])
    assert "nothing moved" in text
    assert "read on both sides" in text


def test_the_header_notes_say_whether_the_exit_moved(record):
    """The same moved fingerprint row means different things either way."""
    after = mutate(record)
    after["exit"] = dict(after["exit"], ip="31.0.0.9")

    moved = "\n".join(header_notes(record, after))
    held = "\n".join(header_notes(record, mutate(record)))

    assert "EXIT MOVED" in moved
    assert "exit held" in held


def test_the_report_is_short_enough_to_be_read(record):
    """A run that produces pages of unchanged rows will not be read.

    The committed record's 24 unobtainable rows carry multi-line navigation
    errors as their reasons. Rendered as before/after blocks that is two
    screens of stack trace on every comparison, burying the findings — so they
    are named on one line each. This pins the property, not the exact number.
    """
    after = mutate(record)
    row(after, A_FINGERPRINT_ROW)["value"] = False

    text = format_comparison(
        compare_records(record, after), notes=header_notes(record, after)
    )

    assert len(text.splitlines()) < 45
    # The finding is still above the noise.
    finding_line = next(
        i for i, l in enumerate(text.splitlines()) if A_FINGERPRINT_ROW[1] in l
    )
    coverage_line = next(
        i for i, l in enumerate(text.splitlines()) if "unread-both" in l
    )
    assert finding_line < coverage_line


# --- the CLI, and PS-61's exit-code convention ------------------------------


def write_record(tmp_path, name: str, record: dict) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    return path


def test_cli_exits_0_when_there_is_nothing_to_triage(record, tmp_path, capsys):
    before = write_record(tmp_path, "before.json", record)
    after = write_record(tmp_path, "after.json", mutate(record))

    assert main(["compare", before, after]) == 0


def test_cli_exits_1_on_a_finding(record, tmp_path):
    after = mutate(record)
    row(after, A_FINGERPRINT_ROW)["value"] = False

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 1
    )


def test_cli_exits_0_when_only_the_exit_rotated(record, tmp_path):
    """Rotation is the design. A code that fired on it would fire every run."""
    after = mutate(record)
    row(after, AN_EXIT_ROW)["value"] = "31.0.0.9"
    after["exit"] = dict(after["exit"], ip="31.0.0.9")

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 0
    )


def test_cli_exits_3_on_lost_coverage_never_1(record, tmp_path):
    """A run that failed is not the product moving.

    Distinct from 1 so a reader — or a later gate — can treat "look again"
    differently from "the identity moved". Folding these together is exactly
    what would train someone to skim a red report.
    """
    after = mutate(record)
    make_unobtainable(row(after, A_FINGERPRINT_ROW))

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 3
    )


def test_a_finding_outranks_lost_coverage(record, tmp_path):
    """When both happened, the caller is told the identity moved."""
    after = mutate(record)
    row(after, A_FINGERPRINT_ROW)["value"] = False
    make_unobtainable(row(after, ("bot.sannysoft.com", "webdriver_missing_passed")))

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 1
    )


# --- PS-92: the AGGREGATE evidence floor ------------------------------------
#
# A DIFFERENT question from UNREAD_BOTH, and the tests above pin that the
# per-row rule did not move. Per-row: "is THIS row unreadable?" — settled, and
# deliberately absent from the ladder, because 24 of 53 permanently unreadable
# is this matrix's designed steady state. Aggregate: "did this comparison rest
# on ANY evidence?" — 0 of 53 is a run that did not happen, and it used to
# share exit 0 with a clean run.
#
# Every case here asserts on the exit code of a real ``main(["compare", ...])``
# and never that a helper was called: the exit code is the half a CI gate
# reads, and a helper-level assertion cannot catch a ladder that never adopts
# the helper.


def all_rows_unobtainable(record: dict) -> dict:
    """A record in which nothing was obtained — the shape a refused run leaves.

    Built by mutating the COMMITTED record rather than by hand, which is this
    file's rule and here also a trap-avoidance measure: the row key is
    ``state``, not ``status``, and a hand-built row using ``status=`` reads as
    unrecognised — producing zero obtained rows for the WRONG reason and so
    passing these tests without the fix. ``test_the_probe_records_are_the_shape_
    they_claim`` below is the control that must come back empty.
    """
    out = mutate(record)
    for reading in out["readings"]:
        make_unobtainable(reading)
    return out


def all_rows_absent(record: dict) -> dict:
    """Every row ABSENT — evidence, not the absence of it. The control."""
    out = mutate(record)
    for reading in out["readings"]:
        reading["state"] = "absent"
        reading.pop("value", None)
        reading["reason"] = "the checker did not say this"
    return out


def test_the_probe_records_are_the_shape_they_claim(record):
    """The control the ticket asks for, and it must come back EMPTY.

    Without this, every assertion below could pass for a reason that has
    nothing to do with the floor: rows carrying an unrecognised state read as
    "not evidence" exactly like unobtainable ones do, so a typo in the mutation
    helpers would produce the expected exit codes while proving nothing. This
    pins that the probe records really do carry the committed record's 53 rows
    with a RECOGNISED state — and, for the absent record, that those rows are
    evidence.
    """
    unobtainable = all_rows_unobtainable(record)
    absent = all_rows_absent(record)

    assert len(unobtainable["readings"]) == len(record["readings"]) == 53
    assert {r["state"] for r in unobtainable["readings"]} == {"unobtainable"}
    assert {r["state"] for r in absent["readings"]} == {"absent"}
    # The one that would silently break the suite: a row keyed on `status`
    # instead of `state`. Nothing here may carry an unrecognised state.
    assert [r for r in unobtainable["readings"] if "state" not in r] == []
    # And `absent` IS evidence — so the absent record must NOT trip the floor.
    assert not no_evidence(absent, mutate(absent))
    assert no_evidence(unobtainable, mutate(unobtainable))


def test_cli_does_not_exit_0_when_NOTHING_was_read_on_either_side(
    record, tmp_path, capsys
):
    """AC1. The defect: a comparison resting on no evidence returned 0.

    Both records are structurally perfect and every row failed to be obtained
    — the shape an engine that would not launch plus a dead exit leaves behind.
    The printed report was already honest about this; only the exit code lied,
    and the exit code is what a CI gate reads.

    Asserted as "3, and not 0, and not the finding code" rather than merely
    "non-zero", because a run that did not happen must not be reported as the
    product moving either.
    """
    before = all_rows_unobtainable(record)
    after = mutate(before)

    code = main(
        [
            "compare",
            write_record(tmp_path, "before.json", before),
            write_record(tmp_path, "after.json", after),
        ]
    )

    assert code != 0, "a comparison that observed nothing must not read as clean"
    assert code != 1, "nothing was read; the product did not move"
    assert code == 3


def test_cli_does_not_exit_0_on_a_present_but_EMPTY_readings_list(
    record, tmp_path
):
    """AC2. The same hole by a narrower door, and it needs its own case.

    ``require_record`` accepts this: ``readings`` is present and IS a list, so
    the record is a record and nothing refuses. It simply has nothing in it —
    which is what a truncated recording leaves behind. It reaches the ladder
    with an entry list of ``[]``, indistinguishable from the clean run unless
    the question is put to the RECORDS.
    """
    empty = mutate(record)
    empty["readings"] = []

    code = main(
        [
            "compare",
            write_record(tmp_path, "before.json", empty),
            write_record(tmp_path, "after.json", mutate(empty)),
        ]
    )

    assert code != 0
    assert code == 3


def test_one_obtained_row_clears_the_floor(record, tmp_path):
    """AC4. The floor is at ZERO evidence, not at a quorum.

    11 rows nobody could read and 1 that was read and agreed exits 0. This is
    the boundary that keeps this slice from becoming the threshold policy it is
    explicitly not: "fewer than N read is a fail" is a judgement this floor has
    no grounds to make, and the single agreeing row is what pins that it does
    not make it.
    """
    before = mutate(record)
    before["readings"] = copy.deepcopy(record["readings"][:12])
    for reading in before["readings"][1:]:
        make_unobtainable(reading)
    make_read(before["readings"][0], "the one thing anybody managed to read")
    assert sum(1 for r in before["readings"] if r["state"] == "read") == 1

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", before),
                write_record(tmp_path, "after.json", mutate(before)),
            ]
        )
        == 0
    )


def test_a_record_of_only_ABSENT_rows_is_evidence_and_exits_0(record, tmp_path):
    """AC5's fourth control, and the one that could most easily regress.

    ``absent`` means the checker ANSWERED and did not say this — for an adverse
    item (``proxy_detected``) that is the GOOD news, and it is the whole reason
    ``_obtained`` counts two states rather than one. A floor that keyed off
    ``read`` alone would report a perfectly clean matrix as a run that never
    happened.
    """
    before = all_rows_absent(record)

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", before),
                write_record(tmp_path, "after.json", mutate(before)),
            ]
        )
        == 0
    )


def test_the_zero_evidence_report_says_it_is_not_agreement(
    record, tmp_path, capsys
):
    """AC6. The exit code is for the gate; this line is for the human.

    It has to say two things a reader will otherwise supply wrongly: that
    nothing was read, and that this is NOT agreement. It also names the likely
    CAUSE — a refused or truncated recording — as ``baseline.py`` does for the
    same shape, because "no evidence" without "check your recording" sends the
    reader looking at the product instead of at the run.
    """
    before = all_rows_unobtainable(record)
    main(
        [
            "compare",
            write_record(tmp_path, "before.json", before),
            write_record(tmp_path, "after.json", mutate(before)),
        ]
    )

    out = capsys.readouterr().out
    assert "NOT agreement" in out
    assert "nothing was compared" in out.lower()
    assert "refused" in out.lower() or "truncated" in out.lower()


def test_the_standing_state_still_exits_0_with_its_24_unread_rows(
    record, tmp_path, capsys
):
    """AC3. This test protects the ``coverage_lost`` docstring's argument.

    The committed record against a mutation of itself carries 24 rows nobody
    could read — the designed steady state — and must still exit 0 with those
    rows reported as coverage. If this ever needs editing to accommodate a
    change to the floor, the PER-ROW rule has moved, which is out of scope and
    a reason to stop rather than to adjust the number.
    """
    entries = compare_records(record, mutate(record))
    unread = [e for e in entries if e["classification"] == UNREAD_BOTH]
    assert len(unread) == 24

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", mutate(record)),
            ]
        )
        == 0
    )
    assert "unread-both" in capsys.readouterr().out


def test_the_floor_is_asked_of_the_records_not_of_the_entry_list(record):
    """Why ``no_evidence`` takes two RECORDS, pinned as behaviour.

    Both of these comparisons produce entries a reader would call "nothing to
    triage", and one of them rests on 22 readings while the other rests on
    none. The entry list cannot tell them apart — the empty-``readings`` pair
    compares to ``[]`` exactly like a clean run — so a floor derived from
    entries is structurally incapable of answering this, whatever it asserts.
    """
    clean = mutate(record)
    starved = mutate(record)
    starved["readings"] = []

    assert compare_records(starved, mutate(starved)) == []
    assert not no_evidence(record, clean)
    assert no_evidence(starved, mutate(starved))


def test_the_floor_does_not_fire_when_one_side_alone_has_evidence(
    record, tmp_path
):
    """"Either side", not "both sides" — the asymmetric case.

    A run in which the LATER recording was refused wholesale still has the
    earlier record's 22 readings, so this comparison did rest on evidence. It
    is already reported, correctly and loudly, as lost coverage (3). The floor
    must not claim it observed nothing, and must not reclassify it.
    """
    after = all_rows_unobtainable(record)

    assert not no_evidence(record, after)
    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 3
    )


def test_cli_exits_2_when_a_record_cannot_be_READ(record, tmp_path):
    """PS-61's convention, adopted: the drift code never means "I could not look"."""
    before = write_record(tmp_path, "before.json", record)
    missing = os.path.join(str(tmp_path), "nope.json")

    assert main(["compare", before, missing]) == 2


def test_cli_exits_2_on_a_file_that_is_not_JSON(record, tmp_path):
    before = write_record(tmp_path, "before.json", record)
    broken = os.path.join(str(tmp_path), "broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("<html>502 Bad Gateway</html>")

    assert main(["compare", before, broken]) == 2


def test_cli_exits_2_on_a_file_that_is_not_a_record(record, tmp_path):
    """A false GREEN is worse than a false red: a red gets investigated."""
    before = write_record(tmp_path, "before.json", record)
    not_a_record = write_record(tmp_path, "other.json", {"probes": {}})

    assert main(["compare", before, not_a_record]) == 2


def test_cli_exits_2_on_a_refused_comparison_never_1(record, tmp_path):
    """A refusal is not a finding, and a future gate must not read it as one."""
    after = mutate(record)
    after["seed"] = 999

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", after),
            ]
        )
        == 2
    )


def test_the_refusal_explains_itself_on_stderr_and_prints_no_report(
    record, tmp_path, capsys
):
    """The report on stdout is never half a comparison."""
    after = mutate(record)
    after["seed"] = 999
    main(
        [
            "compare",
            write_record(tmp_path, "before.json", record),
            write_record(tmp_path, "after.json", after),
        ]
    )

    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert "REFUSED" in captured.err
    assert "NOT a finding" in captured.err


# --- PS-72's rule, on this artifact: name the path the operator typed -------
#
# The defect is in the FORMATTING, not in the OS: `{path!r}` escapes
# backslashes, so the message renders 'C:\\Users\\...' — which does not contain
# what the operator typed, while claiming to name their file. So each test
# below only needs a path that CONTAINS A BACKSLASH, on whatever OS it runs on.
#
# For a path that need not exist, a Windows-shaped literal works everywhere and
# reproduces the Windows bug on Linux.
WINDOWS_PATH = r"C:\Users\me\Desktop\reading.json"

# For the two cases that need a REAL file, the backslash has to come from a
# different place on each OS, because the two disagree about what a backslash
# IS. On POSIX it is an ordinary filename character, so it is baked into the
# name. On Windows it is the separator — that name would resolve to a real
# drive path and fail to write — so a plain name is used and the backslashes
# arrive for free in the tmp dir's own path. Either way the asserted string
# contains one, which is all the defect needs to show itself.
BACKSLASHED_NAME = "reading.json" if os.name == "nt" else WINDOWS_PATH


def write_named(tmp_path, name: str, text: str) -> str:
    """Write `text` to a file named literally `name` — backslashes and all."""
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def test_a_missing_record_names_the_path_the_operator_typed(record, tmp_path, capsys):
    """`compare` takes TWO paths, so "which of my two was wrong" is the only
    thing this message has to answer — and a typo'd path is the expected way
    into this branch.

    Asserted through the CLI rather than on ``quote_path`` directly, because a
    helper-level test is structurally incapable of catching a caller that never
    adopts the helper — which is exactly the failure that shipped in PS-72's
    first push, and the one this PR reintroduced at three sites.
    """
    before = write_record(tmp_path, "before.json", record)

    assert main(["compare", before, WINDOWS_PATH]) == 2
    assert WINDOWS_PATH in capsys.readouterr().err


def test_an_unreadable_record_names_the_path_the_operator_typed(
    record, tmp_path, capsys
):
    """The second refusal in ``_load_record``: the file exists but is not JSON."""
    before = write_record(tmp_path, "before.json", record)
    broken = write_named(tmp_path, BACKSLASHED_NAME, "<html>502 Bad Gateway</html>")
    assert "\\" in broken  # else this asserts nothing about the defect

    assert main(["compare", before, broken]) == 2
    assert broken in capsys.readouterr().err


def test_a_non_record_names_the_path_the_operator_typed(record, tmp_path, capsys):
    """``require_record``'s refusal, which formats the path independently of
    ``_load_record`` and so needs its own guard."""
    before = write_record(tmp_path, "before.json", record)
    not_a_record = write_named(tmp_path, BACKSLASHED_NAME, json.dumps({"probes": {}}))
    assert "\\" in not_a_record  # else this asserts nothing about the defect

    assert main(["compare", before, not_a_record]) == 2
    assert not_a_record in capsys.readouterr().err


def test_compare_needs_no_exit_and_no_network(record, tmp_path, monkeypatch):
    """It reads files, which is what makes it testable when the link is down.

    ``prove_exit`` is the reader's precondition and must not be this
    subcommand's: an operator most wants to know what the last good record said
    on exactly the runs where the exit is unreachable. Poisoned here so a
    future refactor that reaches for it fails loudly.
    """
    import src.services.verify.checker_cli as cli

    def explode(*args, **kwargs):
        raise AssertionError("compare must not prove an exit")

    monkeypatch.setattr(cli, "prove_exit", explode)

    assert (
        main(
            [
                "compare",
                write_record(tmp_path, "before.json", record),
                write_record(tmp_path, "after.json", mutate(record)),
            ]
        )
        == 0
    )


# ---------------------------------------------------------------------------
# PS-144 — the SILENCE PASS: which checkers have never once answered?
#
# Everything below runs over the REAL COMMITTED RECORDS ON DISK, discovered by
# payload shape from the repository tree — not over mutated copies of the
# fixture, and not over hand-built dicts. That is the point of this lane: the
# claim under test is "never answered in any record we hold", which is a
# statement about the corpus, so a synthetic corpus would prove nothing about
# it. `test_silence_pass_names_exactly_the_three_readable_tier_checkers` is the
# falsification target — delete the silence pass and it goes RED.
#
# The expected value is the three named hosts, NEVER a record count. The count
# is a count of on-disk artifacts, in the exact tree this feature exists to
# consume: it stood at 9 when this ticket was written and at 20 when it was
# implemented, because three legitimate recording campaigns landed in between.
# An AC keyed on it would already be a false RED pointing at nothing.
# ---------------------------------------------------------------------------

from src.services.verify.checkers import BROWSER_CHECKERS  # noqa: E402
from src.services.verify.matrix import (  # noqa: E402
    readings_for_unread_checker,
)
from src.services.verify.matrix_silence import (  # noqa: E402
    CARRIED,
    MINIMUM_RECORDS,
    NEVER_ANSWERED,
    NOT_ASKED,
    NotEnoughRecords,
    alarms,
    answered_by_record,
    asked_by_record,
    carried,
    discover_record_paths,
    format_silence,
    load_record,
    not_asked,
    silence_pass,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Measured live over the committed corpus. These three are `tier=browser` or
# `tier=json` — the catalogue declares them READABLE — and not one record has
# ever obtained a reading from any of them, while the matrix presents as 16
# wide. Each already carries a per-run `note_unreachable`; this is those notes
# added up, which no command could do before this one.
SILENT_READABLE = {
    "bot-detector.rebrowser.net",
    "deviceandbrowserinfo.com",
    "tools.scrapfly.io",
}

# Silent too, and deliberately NOT findings: the catalogue declares each of
# these `tier=unreadable` with a written `unreadable_reason` (click-gated,
# paywalled, Cloudflare challenge). A report that alarmed on all eight would be
# 5/8 false alarm, and on an alarm-shaped deliverable every by-design member
# discounts every genuine one.
SILENT_CARRIED = {
    "amiunique.org",
    "browserscan.net",
    "coveryourtracks.eff.org",
    "fv.pro",
    "whoer.net",
}


@pytest.fixture
def committed_records() -> "list[dict]":
    """Every committed checker-matrix record, discovered by PAYLOAD SHAPE.

    Globs the whole tree and filters on `isinstance(d["readings"], list)`
    rather than reading a remembered subdirectory, so a new recording campaign
    widens the corpus instead of breaking the suite.
    """
    paths = discover_record_paths(REPO_ROOT)
    assert len(paths) >= MINIMUM_RECORDS, (
        f"discovery found {len(paths)} record(s) under {REPO_ROOT}; the "
        "silence lane needs a real corpus to range over"
    )
    return [load_record(path) for path in paths]


def test_silence_pass_names_exactly_the_three_readable_tier_checkers(
    committed_records,
):
    """AC1 — THE FALSIFICATION TARGET.

    Over the committed record set the report names exactly
    `bot-detector.rebrowser.net`, `deviceandbrowserinfo.com` and
    `tools.scrapfly.io` — no more, no fewer. Before this slice no command in
    the subsystem could produce this answer at all, because `compare_records`
    takes exactly two records and "never" is a quantifier over a set.

    Asserted on the return value of a real call over real record files. Not
    that a helper ran, not on source text.
    """
    entries = silence_pass(committed_records)

    assert {e["checker"] for e in alarms(entries)} == SILENT_READABLE


def test_silence_pass_reports_unreadable_tier_as_carried_not_as_findings(
    committed_records,
):
    """AC2 — the partition IS the finding; the raw count is the wrong number.

    Eight checkers are silent across the corpus. Five of them are silent
    BY DESIGN and say so in the catalogue. Reporting 8 undifferentiated is the
    false-alarm failure that trains a reader to ignore the gate.
    """
    entries = silence_pass(committed_records)

    assert {e["checker"] for e in carried(entries)} == SILENT_CARRIED
    assert all(e["tier"] == "unreadable" for e in carried(entries))

    # The whole silent population is the union of the two — and the split is
    # load-bearing, so assert the count is NOT what a naive report would print.
    assert {e["checker"] for e in entries} == SILENT_READABLE | SILENT_CARRIED
    assert len(entries) == 8
    assert len(alarms(entries)) == 3


def test_a_checker_read_in_some_records_is_not_silent(committed_records):
    """AC3 — intermittent is NOT silent.

    `creepjs` and `pixelscan.net` both appear in individual records'
    unobtainable tallies yet read elsewhere, so both must stay out of the
    report entirely. Folding them in would drown the three checkers that have
    genuinely never been read.

    The unit asserted here is the RECORD (the implementation's unit): each
    answered in strictly more than zero and strictly fewer than all of them.
    The ticket quoted 5/9 at filing time and this measured 12/20 at
    implementation; the ratio moves with the corpus, so the assertion is on the
    MIXED PROPERTY, which is what actually discriminates.
    """
    answered = answered_by_record(committed_records)
    total = len(committed_records)
    reported = {e["checker"] for e in silence_pass(committed_records)}

    for checker_id in ("creepjs", "pixelscan.net"):
        assert 0 < answered[checker_id] < total, (
            f"{checker_id} answered in {answered[checker_id]}/{total} records; "
            "this test needs a checker that is genuinely MIXED to discriminate"
        )
        assert checker_id not in reported


def test_a_single_record_is_refused_rather_than_reported_clean(
    committed_records,
):
    """AC4 — the PS-92 evidence floor, one artifact over.

    A one-record silence reading is unfalsifiable: every checker that happened
    to fail in that one run reads as "never answered". Zero silent checkers
    over one record and zero over twenty are the same VALUE and completely
    different EVIDENCE, so the degenerate input must REFUSE rather than return
    an empty report that reads as a clean bill of health.
    """
    with pytest.raises(NotEnoughRecords):
        silence_pass(committed_records[:1])

    with pytest.raises(NotEnoughRecords):
        silence_pass([])


def test_cli_silence_exits_3_over_the_committed_corpus(capsys):
    """The lane end to end, exit code included.

    3 already means "no finding, but the coverage this rests on is not what you
    think" at three existing sites in this CLI, which is exactly what a
    never-read readable-tier checker is. Never 1 — the product did not move.
    """
    assert main(["silence", "--discover", REPO_ROOT]) == 3

    out = capsys.readouterr().out
    for checker_id in SILENT_READABLE:
        assert checker_id in out
    # The carried five are PRINTED (the set is accounted for in full) but they
    # are what the FINDINGS section must not contain.
    findings_section = out.split("CARRIED")[0]
    for checker_id in SILENT_CARRIED:
        assert checker_id in out
        assert checker_id not in findings_section


def test_cli_silence_refuses_a_single_record_with_2(capsys):
    """A refusal is never a verdict — exit 2, the convention PS-61 settled."""
    assert main(["silence", RECORD_PATH]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_cli_silence_refuses_a_file_that_is_not_a_record(
    record, tmp_path, capsys
):
    """A non-record in the set refuses, naming the file. Never a silent skip.

    A file quietly dropped from the set changes the denominator "answered in
    0/N" is quoted against, which is the number the whole report rests on.
    """
    not_a_record = os.path.join(str(tmp_path), "not-a-record.json")
    with open(not_a_record, "w", encoding="utf-8") as handle:
        json.dump({"nope": True}, handle)

    assert main(["silence", not_a_record, RECORD_PATH]) == 2
    assert not_a_record in capsys.readouterr().err


def test_silence_exits_0_when_every_readable_checker_answered(record):
    """The clean path is REACHABLE — a gate that can only fire is not a gate.

    Built by mutating the committed record so every row reads, then handing in
    two of them: the tier split still runs, the carried entries are still
    reported, and the exit is 0 because no READABLE-tier checker is silent.
    """
    healthy = mutate(record)
    for reading in healthy["readings"]:
        reading["state"] = "read"

    entries = silence_pass([healthy, mutate(healthy)])

    assert alarms(entries) == []
    # The unreadable-tier checkers with no rows at all are still carried, and
    # still must not turn the report red.
    assert all(e["classification"] == CARRIED for e in entries)


def test_discovery_is_by_payload_shape_not_by_directory_name(tmp_path, record):
    """A record in an unheard-of directory is found; a non-record is not.

    The guard against the failure mode that makes this feature silently stop
    working: keying discovery on a remembered subdirectory means the next
    recording campaign lands outside the set and nobody is told.
    """
    campaign = os.path.join(str(tmp_path), "readings", "ps999-never-seen")
    os.makedirs(campaign)
    a_record = write_record(tmp_path, os.path.join(campaign, "reading.json"), record)

    decoy = os.path.join(str(tmp_path), "not-a-record.json")
    with open(decoy, "w", encoding="utf-8") as handle:
        json.dump({"readings": "not a list"}, handle)

    found = discover_record_paths(str(tmp_path))

    assert found == [a_record]


def test_format_silence_keeps_the_two_populations_apart(committed_records):
    """The rendering carries the split, not just the data structure.

    A caller reading the printed page must not be able to come away with the
    undifferentiated 8.
    """
    entries = silence_pass(committed_records)
    text = format_silence(entries, records=len(committed_records))

    assert "FINDINGS" in text and "CARRIED" in text
    assert str(len(committed_records)) in text
    findings_section, carried_section = text.split("CARRIED")
    for checker_id in SILENT_READABLE:
        assert checker_id in findings_section
    for checker_id in SILENT_CARRIED:
        assert checker_id in carried_section


def test_absent_counts_as_evidence_so_an_absent_only_checker_is_not_silent(
    record,
):
    """`absent` is a READING, not a failure to read — so it breaks silence.

    This pins the one rule this lane must never quietly redefine. `absent`
    means the checker ANSWERED and did not report the item, which for an
    adverse item is the GOOD news; only `unobtainable` and a missing row are
    non-evidence. `evidence.obtained` is the single owner of that definition
    and this lane delegates to it rather than re-deriving it, because the
    failure mode of writing it twice is that the lanes drift and a checker
    reported "never answered" here is one every other lane can see answering.

    Built as a mutation because the committed corpus cannot express the case:
    NO checker in it has `absent` as its only evidence corpus-wide, so a real
    record set cannot tell a correct implementation from one that counts only
    `read`. Without this test, narrowing the rule to `state == "read"` passes
    the entire suite — measured, not assumed.
    """
    absent_only = mutate(record)
    for reading in absent_only["readings"]:
        if reading["checker"] == "iphey.com":
            reading["state"] = "absent"

    other = mutate(record)
    for reading in other["readings"]:
        if reading["checker"] == "iphey.com":
            reading["state"] = "unobtainable"

    answered = answered_by_record([absent_only, other])
    reported = {e["checker"] for e in silence_pass([absent_only, other])}

    # It answered in exactly the one record where it was `absent`. Read with
    # `.get` so an implementation that counts only `read` fails this as a
    # stated ASSERTION about the rule, rather than as a KeyError that has to
    # be interpreted.
    assert answered.get("iphey.com", 0) == 1, (
        "a checker whose only evidence is `absent` must count as having "
        "answered: `absent` means it replied and did not report the item"
    )
    assert "iphey.com" not in reported


# ---------------------------------------------------------------------------
# The skipped-tier case. THE COMMITTED CORPUS CANNOT EXPRESS IT: all 22 records
# carry `skipped_tiers == []`, so a real record set cannot tell a correct
# implementation from one that alarms on a tier the run never asked. Built as a
# mutation for the same reason `test_absent_counts_as_evidence...` is — and
# built through the REAL production path (`readings_for_unread_checker` over
# `BROWSER_CHECKERS`, exactly as `checker_cli`'s `--skip-browser` calls it)
# rather than by hand-rolling rows, so the test cannot pass against a shape the
# product never emits.
# ---------------------------------------------------------------------------


def skip_browser_record(record: dict) -> dict:
    """A record from a `--skip-browser` run, built the way the CLI builds one.

    The skip path appends "browser" to `skipped_tiers` and emits a full width
    of UNOBTAINABLE rows for every browser checker, so the matrix keeps its
    width. Crucially it does NOT pass `never_asked=True` — which is why the
    row flag cannot carry this distinction and the record-level `skipped_tiers`
    field is the only honest key.
    """
    skipped = mutate(record)
    browser_ids = {c.id for c in BROWSER_CHECKERS}
    skipped["readings"] = [
        r for r in skipped["readings"] if r["checker"] not in browser_ids
    ]
    for checker in BROWSER_CHECKERS:
        skipped["readings"].extend(
            reading.as_record()
            for reading in readings_for_unread_checker(
                checker, "tier skipped by --skip-browser"
            )
        )
    skipped["skipped_tiers"] = ["browser"]
    return skipped


def test_a_skipped_tier_is_not_reported_as_never_answered(record):
    """A tier the run never ASKED is not evidence that its checkers are silent.

    THE REGRESSION THIS PINS: deciding silence from the row state alone
    collapses "the run never asked" into "the checker was asked and could not
    answer" — the exact distinction `evidence.never_asked_rows` calls the
    load-bearing half of PS-110. It is reachable from a shipped, documented
    flag, not a hypothetical: `--skip-browser` emits a full width of
    unobtainable browser rows, and every browser checker then reads as
    NEVER ANSWERED.

    Measured on the pre-fix implementation: 7 of 8 alarms were false, including
    `creepjs` and `pixelscan.net` — the two checkers AC3 exists to keep OUT of
    the report — and `engine-exit`. A gate that is 71% false alarm on the first
    skip campaign teaches the reader to ignore it, which is precisely the
    outcome AC2 exists to prevent, arriving through a door AC2 did not check.
    """
    records = [skip_browser_record(record), skip_browser_record(record)]

    entries = silence_pass(records)
    alarmed = {e["checker"] for e in alarms(entries)}

    # The AC3 discriminators must not be alarmed on for lack of asking.
    for checker_id in ("creepjs", "pixelscan.net", "bot.sannysoft.com"):
        assert checker_id not in alarmed, (
            f"{checker_id} was never ASKED in these records (the browser tier "
            "was skipped), so its silence is a fact about the runs, not a "
            "finding about the checker"
        )

    # Not merely dropped — accounted for, under a heading of their own.
    assert "creepjs" in {e["checker"] for e in not_asked(entries)}


def test_a_skipped_tier_does_not_suppress_a_genuine_finding(record):
    """The guard must NARROW the alarm, never switch it off.

    `tools.scrapfly.io` is `tier=json`, and a `--skip-browser` run still asks
    the JSON tier — so it stays a finding. This is the other half of the fix:
    an implementation that suppressed every silent checker whenever any tier
    was skipped would pass the test above and report nothing at all.
    """
    records = [skip_browser_record(record), skip_browser_record(record)]

    alarmed = {e["checker"] for e in alarms(silence_pass(records))}

    assert "tools.scrapfly.io" in alarmed, (
        "the JSON tier WAS asked in these records and this checker never "
        "answered, so suppressing it would be a false GREEN"
    )


def test_a_tier_skipped_in_only_some_records_still_alarms(record):
    """One skip record must not silence a checker the OTHER records asked.

    The reason the asked-count is per-record rather than a union across the
    set. A union would let a single `--skip-browser` record suppress the alarm
    for a checker every other record asked and never got an answer from —
    switching the gate off with the very flag that should only ever narrow it.

    `bot-detector.rebrowser.net` is browser-tier and genuinely silent in the
    committed corpus, so with enough records that DID ask it, it must still be
    reported however many skipped records sit alongside.
    """
    records = [
        skip_browser_record(record),
        mutate(record),
        mutate(record),
    ]

    entries = silence_pass(records)
    alarmed = {e["checker"] for e in alarms(entries)}

    assert "bot-detector.rebrowser.net" in alarmed
    # And the claim is quoted against the records that actually asked, not
    # against the set size: 2 of these 3 records asked the browser tier.
    entry = next(
        e for e in entries if e["checker"] == "bot-detector.rebrowser.net"
    )
    assert entry["asked_in"] == 2
    assert entry["records"] == 3


def test_cli_exits_0_when_every_silent_checker_was_merely_unasked(
    record, tmp_path, capsys
):
    """End to end: a skip campaign does not turn the gate red on its own.

    The browser tier is skipped and the JSON tier is made to read clean, so
    nothing was asked-and-silent. Exit 0 — and the unasked checkers are still
    PRINTED, because keeping them out of the alarm must not mean hiding them.
    """
    clean = mutate(record)
    for reading in clean["readings"]:
        reading["state"] = "read"
    skipped = skip_browser_record(clean)

    a = write_record(tmp_path, "a.json", skipped)
    b = write_record(tmp_path, "b.json", mutate(skipped))

    assert main(["silence", a, b]) == 0

    out = capsys.readouterr().out
    assert "NOT ASKED" in out
    assert "creepjs" in out
