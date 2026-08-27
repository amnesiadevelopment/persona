"""The PS-177 Level 2 comparator, pinned on the branch that decides a bar level.

WHY THIS FILE EXISTS
--------------------
``readings/ps177-2026-08-25/derive.py`` answers Level 2 of the bar (mutual
unlinkability): *given two profiles at different seeds, can a checker tie them
to each other?* When it shipped for review the comparison branch had **never
executed** — the sweep obtained one record, so every run took the
``len(seeds) < 2`` early-``continue``. The reviewer executed it by hand and
found it reported two maximally-different profiles as **linked**, because the
only rows read on both sides were detector verdicts (``webdriver_passed=True``,
``trustworthy=True``) that read ``True`` for *any* well-masked profile.

That inverts the question. Identical **high-entropy** values (a canvas hash, a
GPU renderer string) mean *linkable*. Identical **verdict** values mean *both
clean*. So the classification is now explicit, and this file pins it.

THE FIXTURES ARE REAL AND ARE NAMED
-----------------------------------
Following ``test_verify_matrix_consistency.py``'s convention — every case is a
committed record from ``readings/`` or a single mutation of one, never a
hand-built dict. A check observed only on inputs invented to satisfy it has not
been observed. Both branches have a *real* fixture:

* **The answerable arm** — ``readings/ps128-2026-08-23/run1-matrix/`` firefox
  windows seeds ``1337`` and ``4242``. Same exit (``95.49.113.111``), same
  masking layer, 3.5 minutes apart. 15 fingerprint rows read on both sides:
  9 entropy-bearing, 6 verdicts. This arm is why the comparator can be tested
  against reality at all — it is the corpus' only two-seed arm with
  entropy rows read on both sides, and it was already committed.
* **The unanswerable arm** — ``readings/ps177-2026-08-25/reading.firefox.
  windows.seed5150.json``, whose entropy rows are all ``unobtainable`` (the
  exit died mid-record). Paired with a reseeded copy of itself it must report
  UNANSWERABLE, **not** a clean pass — that is the exact false-pass the
  reviewer caught.

WHAT THE MUTATIONS ARE FOR
--------------------------
The real corpus proves the classifier works on the values it has seen. It
cannot prove the tool would still refuse a verdict-only overlap it has never
seen, so ``test_verdict_only_overlap_is_unanswerable`` reseeds a real record
rather than inventing one. One mutation per case keeps the expected
classification unambiguous.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READINGS = os.path.join(REPO_ROOT, "readings")

PS177_DIR = os.path.join(READINGS, "ps177-2026-08-25")
PS177_RECORD = os.path.join(PS177_DIR, "reading.firefox.windows.seed5150.json")

PS128_RUN1 = os.path.join(READINGS, "ps128-2026-08-23", "run1-matrix")
PS128_SEED1337 = os.path.join(PS128_RUN1, "reading.firefox.windows.seed1337.json")
PS128_SEED4242 = os.path.join(PS128_RUN1, "reading.firefox.windows.seed4242.json")


def _load_derive():
    """Import ``derive.py`` by path — it is a committed instrument, not a module.

    It deliberately lives beside the record it derives (the ticket's DoD #1:
    "the instrument committed beside it so the next person can re-run it"), so
    there is no package to import it from.
    """
    path = os.path.join(PS177_DIR, "derive.py")
    spec = importlib.util.spec_from_file_location("ps177_derive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


derive = _load_derive()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _level2_output(capsys, *records):
    """Run the comparator over records and return what it printed."""
    derive.level2([(os.path.basename(f"r{i}.json"), r)
                   for i, r in enumerate(records)])
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# The classifier itself, on real rows.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "checker,item",
    [
        ("bot.sannysoft.com", "webdriver_advanced_passed"),
        ("bot.sannysoft.com", "webdriver_missing_passed"),
        ("iphey.com", "software_fine"),
        ("iphey.com", "trustworthy"),
    ],
)
def test_detector_verdicts_are_not_linkage_evidence(checker, item):
    """The four rows that produced the reviewer's false linkage finding.

    These are the ONLY fingerprint rows read in the PS-177 record. Each is a
    boolean detector verdict, so each must classify as `verdict` — a row that
    cannot tie two profiles together at any value.
    """
    record = _read(PS177_RECORD)
    rows = [
        r for r in record["readings"]
        if r["checker"] == checker and r["item"] == item
        and r.get("state") == derive.STATE_READ
    ]
    assert rows, f"{checker}::{item} is not a read row in the PS-177 record"
    for row in rows:
        assert derive.linkage_class(row) == derive.CLASS_VERDICT


@pytest.mark.parametrize(
    "item,expected_class",
    [
        # High-cardinality profile attributes — these CAN tie two profiles.
        ("canvas_data_hash", "entropy"),
        ("webgl_image_hash", "entropy"),
        ("webgl_pixel_hash", "entropy"),
        ("gpu_renderer", "entropy"),
        ("gpu_vendor", "entropy"),
        # Detector output / low-cardinality tokens — these cannot.
        ("chromium_claim", "verdict"),        # "false"
        ("headless_rating", "verdict"),       # "33"
        ("like_headless_rating", "verdict"),  # "0"
        ("stealth_rating", "verdict"),        # "20"
    ],
)
def test_classification_of_real_rows(item, expected_class):
    """Every read fingerprint row in a real two-seed record, classified.

    The ratings are the subtle half: ``headless_rating`` 33 looks like data but
    is a detector's score ABOUT the profile, not an attribute OF it — it reads
    33 on chromium and firefox alike, so treating it as entropy would
    manufacture a tie between every profile the checker rates the same.
    """
    record = _read(PS128_SEED1337)
    rows = [
        r for r in record["readings"]
        if r["item"] == item and r.get("state") == derive.STATE_READ
    ]
    assert rows, f"{item} is not a read row in the ps128 seed1337 record"
    for row in rows:
        assert derive.linkage_class(row) == expected_class


def test_vector_annotation_wins_over_value_shape():
    """A row the record tagged as a leak vector is entropy-bearing, always.

    The record's own catalogue annotation outranks this tool's guess at the
    shape of a value. Pinned because the fallback heuristic (length >= 8) would
    misclassify a short renderer string, and the tag is what prevents that.
    """
    record = _read(PS128_SEED1337)
    tagged = [
        r for r in record["readings"]
        if (r.get("vector") or "").strip() and r.get("state") == derive.STATE_READ
    ]
    assert tagged, "expected vector-tagged read rows in the ps128 record"
    for row in tagged:
        assert derive.linkage_class(row) == derive.CLASS_ENTROPY

    # ...even when the value looks like a verdict token.
    row = copy.deepcopy(tagged[0])
    row["value"] = "true"
    assert derive.linkage_class(row) == derive.CLASS_ENTROPY


# ---------------------------------------------------------------------------
# The comparator branch — the one that had never executed.
# ---------------------------------------------------------------------------

def test_verdict_only_overlap_is_unanswerable(capsys):
    """THE REGRESSION. Two profiles sharing only verdicts are NOT linked.

    This is the reviewer's case, built the honest way: the PS-177 record paired
    with a reseeded copy of itself. Its entropy rows are all `unobtainable`, so
    the only overlap is the four boolean verdicts. The old comparator reported
    those under "ROWS THAT TIE THE TWO PROFILES TOGETHER".

    The correct answer is UNANSWERABLE — a coverage statement, not a result.
    """
    a = _read(PS177_RECORD)
    b = copy.deepcopy(a)
    b["seed"] = 24601

    out = _level2_output(capsys, a, b)

    assert "UNANSWERABLE" in out
    assert "TIE THE TWO PROFILES TOGETHER" not in out
    # It must not silently drop the rows either — they are reported as
    # non-evidence so the reader can see what was excluded and why.
    assert "webdriver_advanced_passed" in out
    assert "BOTH CLEAN" in out


def test_verdict_only_overlap_never_claims_a_pass(capsys):
    """The failure direction that matters: absence of data is not a clean bill.

    Separate from the test above because the dangerous outcome is not merely
    the wrong heading — it is a reader concluding Level 2 HOLDS on an arm where
    nothing capable of answering it was ever read.
    """
    a = _read(PS177_RECORD)
    b = copy.deepcopy(a)
    b["seed"] = 24601

    out = _level2_output(capsys, a, b)

    assert "LEVEL 2 HOLDS" not in out
    assert "coverage, not a clean result" in out


def test_real_two_seed_arm_reports_the_linkage_it_actually_has(capsys):
    """The answerable arm — and the finding it carries.

    ps128 firefox/windows at seeds 1337 and 4242, same exit, same masking
    layer. Eight of nine entropy-bearing rows differ across the two seeds, but
    ``creepjs :: webgl_pixel_hash`` reads ``51df3565`` on BOTH. That is a row
    that ties the two profiles together, and it must be reported as one.

    This is the branch the comparator existed for and had never run. It is
    pinned against the real records rather than a mutation precisely because
    the finding is real.
    """
    out = _level2_output(capsys, _read(PS128_SEED1337), _read(PS128_SEED4242))

    assert "9 entropy-bearing" in out
    assert "8 DIFFER, 1 IDENTICAL" in out
    assert "TIE THE TWO PROFILES TOGETHER" in out
    assert "webgl_pixel_hash" in out
    assert "LEVEL 2 FAILS on this arm" in out
    # The six verdict rows must be excluded from the verdict, not counted.
    assert "6 verdict/low-cardinality row(s) excluded" in out


def test_verdict_rows_cannot_flip_the_answerable_arm(capsys):
    """Mutating every verdict row must not change the Level 2 conclusion.

    The discrimination test: if verdict rows were still feeding the finding,
    making them all differ would change the counts. They must not — the verdict
    rests on the nine entropy rows alone.
    """
    a = _read(PS128_SEED1337)
    b = _read(PS128_SEED4242)
    for row in b["readings"]:
        if (row.get("state") == derive.STATE_READ
                and row.get("sort") == derive.SORT_FINGERPRINT
                and derive.linkage_class(row) == derive.CLASS_VERDICT):
            row["value"] = "MUTATED-VERDICT"

    out = _level2_output(capsys, a, b)

    assert "9 entropy-bearing" in out
    assert "8 DIFFER, 1 IDENTICAL" in out
    assert "webgl_pixel_hash" in out


def test_single_seed_arm_is_unanswerable(capsys):
    """One profile cannot answer a two-profile question. Unchanged behaviour.

    Pinned because it is the state the whole PS-177 sweep landed in, and a
    future edit that made a single record report a pass would be the worst
    possible regression in this tool.
    """
    out = _level2_output(capsys, _read(PS177_RECORD))

    assert "UNANSWERABLE" in out
    assert "only ONE seed" in out
    assert "LEVEL 2 HOLDS" not in out
    assert "TIE THE TWO PROFILES TOGETHER" not in out
