"""The schema ledger, proven by CALLING THE WRITERS — never by reading their source.

Why these tests are shaped this way
-----------------------------------
The defect this module guards against is *a header field added without the
version being addressed*. The tempting test is a grep: assert the writer's
source mentions each key. That test would be worthless here, and knowledge
article PS-11 documents six instances of exactly that failure on this project —
**a green test asserting on text your own code generated**. It proves the
generator ran; it cannot prove the emitted document carries anything.

So every test below **calls the real writer and reads the document it returns**.
`build_record` and `build_snapshot` are invoked; their output is the subject.
The ledger is a hand-maintained literal precisely so it can DISAGREE with the
writer — a key set derived from the writer would agree by construction and
could never fail.

The counterfactuals at the bottom are the load-bearing half: they add a header
field the way a future ticket would, and assert the mechanism goes red. A guard
against an accident only ever observed on a correct tree has not been observed.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from src.services.verify import matrix, snapshot
from src.services.verify.exit_guard import Exit
from src.services.verify.schema_ledger import (
    SchemaLedgerViolation,
    check_emitted_header,
    generation_of,
    header_keys,
    mislabelled,
)

# Resolved from THIS file's location, never from the CWD: other tests in the
# suite chdir into tmp dirs, so a relative path here passes in isolation and
# fails in a full run (it did — 3 FileNotFoundError on all three CI platforms).
# The two tests these feed are the ones covering "existing committed records
# still read", so a CWD-dependent path made that requirement's guard an error
# rather than an assertion.
_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
MATRIX_FIXTURE = _FIXTURES / "checker-matrix-reading.sandbox.json"
SNAPSHOT_FIXTURE = _FIXTURES / "engine-fingerprint-baseline.firefox.json"


# --- calling the real writers ----------------------------------------------


def a_record(**kwargs) -> dict:
    """The REAL checker-record writer's REAL output."""
    return matrix.build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-22T10:00:00Z",
        **kwargs,
    )


def a_snapshot(**kwargs) -> dict:
    """The REAL snapshot writer's REAL output."""
    return snapshot.build_snapshot(
        {}, engine="firefox", profile="p", version="9.9.9", build="firefox-20", **kwargs
    )


def check_record(record: dict) -> None:
    check_emitted_header(
        record,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
        current_version=matrix.SCHEMA_VERSION,
        artifact="checker record",
        version_symbol="matrix.SCHEMA_VERSION",
    )


def check_snapshot(document: dict) -> None:
    check_emitted_header(
        document,
        body_key=snapshot.SNAPSHOT_BODY_KEY,
        generations=snapshot.HEADER_GENERATIONS,
        current_version=snapshot.SCHEMA_VERSION,
        artifact="snapshot",
        version_symbol="snapshot.SCHEMA_VERSION",
        annotations=snapshot.POST_WRITER_ANNOTATIONS,
    )


# --- the writers match their ledgers ---------------------------------------


def test_the_checker_record_writer_emits_exactly_its_recorded_generation():
    """Not "the source mentions these keys" — the EMITTED DOCUMENT carries them."""
    check_record(a_record())  # must not raise


def test_the_snapshot_writer_emits_exactly_its_recorded_generation():
    check_snapshot(a_snapshot())  # must not raise


def test_both_artifacts_are_covered_not_just_one():
    """Hardening one and leaving the other moves the trap rather than removing it.

    The two versions are independent integers governing different documents —
    ``matrix`` records what third-party checkers reported, ``snapshot`` records
    what a live profile exposes — so a ledger for one says nothing about the
    other. This test exists to fail if a future edit deletes one of them.
    """
    assert matrix.HEADER_GENERATIONS and snapshot.HEADER_GENERATIONS
    check_record(a_record())
    check_snapshot(a_snapshot())


def test_each_ledgers_newest_generation_is_that_modules_SCHEMA_VERSION():
    """The ledger stops describing the writer the moment these disagree."""
    assert max(matrix.HEADER_GENERATIONS) == matrix.SCHEMA_VERSION
    assert max(snapshot.HEADER_GENERATIONS) == snapshot.SCHEMA_VERSION


# --- the two generations are distinguishable (the ticket's core outcome) ----


def test_a_pre_PS69_and_a_post_PS69_record_are_DIFFERENT_generations():
    """The whole point: tell them apart WITHOUT sniffing for individual fields.

    PS-67 was forced to sniff — to ask ``"declared_machine" in record`` per side
    — precisely because both generations said ``schema_version: 1``. A consumer
    can now ask the version instead.
    """
    committed = json.loads(MATRIX_FIXTURE.read_text(encoding="utf-8"))
    today = a_record(declared_machine="windows", declared_machine_honoured=True)

    assert committed["schema_version"] != today["schema_version"]
    assert generation_of(
        committed,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    ) == 1
    assert generation_of(
        today,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    ) == 2


def test_the_generation_is_read_from_the_KEYS_not_from_the_claimed_version():
    """A record written during a drift window claims one and carries the other.

    This is what lets a consumer SEE the discrepancy instead of trusting the
    label — and it is the exact state every record written between PS-69 and
    this ticket is in.
    """
    drifted = a_record(declared_machine="windows")
    drifted["schema_version"] = 1  # what PS-69 left it saying

    assert generation_of(
        drifted,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    ) == 2
    assert mislabelled(
        drifted,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    )


def test_an_unrecognised_shape_reports_None_never_a_guess():
    """"I have never seen this shape" and "it is really generation N" differ.

    Collapsing them would turn an honest unknown into a confident wrong answer.
    """
    weird = a_record()
    weird["something_nobody_has_ever_emitted"] = 1

    assert generation_of(
        weird, body_key=matrix.RECORD_BODY_KEY, generations=matrix.HEADER_GENERATIONS
    ) is None
    # ...and an unrecognised shape is NOT reported as mislabelled.
    assert not mislabelled(
        weird, body_key=matrix.RECORD_BODY_KEY, generations=matrix.HEADER_GENERATIONS
    )


# --- existing committed records still read ---------------------------------


def test_the_committed_checker_reading_is_a_RECOGNISED_generation():
    """A version scheme that refuses every record on disk has broken more than
    it fixed. This file is the entire checker-matrix test corpus."""
    committed = json.loads(MATRIX_FIXTURE.read_text(encoding="utf-8"))

    assert generation_of(
        committed,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    ) == committed["schema_version"] == 1
    assert not mislabelled(
        committed,
        body_key=matrix.RECORD_BODY_KEY,
        generations=matrix.HEADER_GENERATIONS,
    )


def test_the_committed_snapshot_baseline_is_a_RECOGNISED_generation():
    """It says version 1 and ALREADY carries ``engine_build`` — which is why
    the snapshot side records that shape as generation 1 rather than bumping.
    Nothing on disk is re-tagged and nothing is re-interpreted."""
    committed = json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))

    assert generation_of(
        committed,
        body_key=snapshot.SNAPSHOT_BODY_KEY,
        generations=snapshot.HEADER_GENERATIONS,
        annotations=snapshot.POST_WRITER_ANNOTATIONS,
    ) == committed["schema_version"] == 1


def test_a_post_writer_annotation_does_not_change_a_documents_generation():
    """``baseline.record`` attaches ``provenance``; ``engine_gate`` attaches
    ``engine_stack``. Neither is the writer, so neither may make a snapshot
    unrecognisable — otherwise a document's generation would depend on which
    pipeline happened to handle it."""
    plain = a_snapshot()
    annotated = dict(plain, provenance={"any": "thing"}, engine_stack="20.14.0")

    for document in (plain, annotated):
        assert generation_of(
            document,
            body_key=snapshot.SNAPSHOT_BODY_KEY,
            generations=snapshot.HEADER_GENERATIONS,
            annotations=snapshot.POST_WRITER_ANNOTATIONS,
        ) == 1


# --- the boundary this module does and does not claim ----------------------


def test_the_body_is_not_part_of_the_header():
    record = a_record()
    assert matrix.RECORD_BODY_KEY not in header_keys(
        record, body_key=matrix.RECORD_BODY_KEY
    )
    assert "exit" in header_keys(record, body_key=matrix.RECORD_BODY_KEY)


def test_a_change_INSIDE_a_nested_header_object_is_NOT_claimed_to_be_caught():
    """Stated rather than implied: this tracks the top-level KEY SET.

    A field added inside ``exit`` or ``counts`` is a different question and
    this mechanism does not answer it. Recording that honestly is better than
    letting a reader assume a coverage that does not exist — the same reason
    the record itself distinguishes ABSENT from UNOBTAINABLE.
    """
    record = a_record()
    record["exit"] = dict(record["exit"], a_brand_new_nested_field=1)

    check_record(record)  # does NOT raise — and that is the documented boundary


# --- SHOW IT FAILING: the counterfactuals ----------------------------------
#
# Each adds a header field the way a future ticket would, and asserts the
# mechanism catches it. Without these, the guard has only ever been observed on
# a tree where it had nothing to find.


def test_a_NEW_header_field_without_a_ledger_edit_is_CAUGHT_on_the_record():
    """PS-69, re-enacted. This is the accident the ticket exists to prevent."""
    drifted = a_record()
    drifted["declared_timezone"] = "Europe/Warsaw"  # a plausible next field

    with pytest.raises(SchemaLedgerViolation) as excinfo:
        check_record(drifted)

    message = str(excinfo.value)
    assert "declared_timezone" in message, "the message must name the new key"
    assert "SCHEMA_VERSION" in message, "and tell the author what to do about it"


def test_a_NEW_header_field_without_a_ledger_edit_is_CAUGHT_on_the_snapshot():
    """PS-19, re-enacted — the same accident in the other module.

    Both artifacts are covered, so the trap was removed rather than moved.
    """
    drifted = a_snapshot()
    drifted["engine_channel"] = "stable"

    with pytest.raises(SchemaLedgerViolation) as excinfo:
        check_snapshot(drifted)
    assert "engine_channel" in str(excinfo.value)


def test_a_REMOVED_header_field_is_caught_too():
    """Drift runs both ways: a dropped key silently narrows every record."""
    drifted = a_record()
    del drifted["seed"]

    with pytest.raises(SchemaLedgerViolation) as excinfo:
        check_record(drifted)
    assert "seed" in str(excinfo.value)


def test_a_version_bumped_without_its_key_set_being_recorded_is_caught():
    """The other half of the discipline: bumping alone is not enough either.

    A number nobody wrote a key set for is a version that describes nothing.
    """
    record = a_record()
    record["schema_version"] = 99

    with pytest.raises(SchemaLedgerViolation):
        check_emitted_header(
            record,
            body_key=matrix.RECORD_BODY_KEY,
            generations=matrix.HEADER_GENERATIONS,
            current_version=99,
            artifact="checker record",
            version_symbol="matrix.SCHEMA_VERSION",
        )


def test_a_writer_whose_emitted_version_disagrees_with_its_constant_is_caught():
    """The header must state the version the writer actually is."""
    record = a_record()
    record["schema_version"] = matrix.SCHEMA_VERSION - 1

    with pytest.raises(SchemaLedgerViolation) as excinfo:
        check_record(record)
    assert "schema_version" in str(excinfo.value)


def test_the_ledger_is_a_LITERAL_that_can_disagree_with_the_writer():
    """The property that makes this a mechanism rather than a restatement.

    A key set derived from the writer's own output would match it by
    construction and could never fail. Proving that here means showing the
    check goes red when the LEDGER is wrong and the writer is untouched — the
    mirror image of the counterfactuals above, which move the writer instead.
    """
    truncated = {
        version: frozenset(keys) - {"seed"}
        for version, keys in matrix.HEADER_GENERATIONS.items()
    }

    with pytest.raises(SchemaLedgerViolation) as excinfo:
        check_emitted_header(
            a_record(),
            body_key=matrix.RECORD_BODY_KEY,
            generations=truncated,
            current_version=matrix.SCHEMA_VERSION,
            artifact="checker record",
            version_symbol="matrix.SCHEMA_VERSION",
        )
    assert "seed" in str(excinfo.value)
