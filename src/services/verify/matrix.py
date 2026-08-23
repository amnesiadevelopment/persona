"""One reading, per checker, per item — in a form a machine can compare.

This is the record the ticket asks for: not a screenshot and not a page dump,
but a structured reading naming the checker, the item, and what it said.

The one rule this module is built around
----------------------------------------
**An unobtainable reading is recorded as unobtainable — never as a pass, never
as a change.** Every way a reading can fail to happen (the checker refused the
connection, the page never settled, the field is gone, the pattern did not
match, the whole tier was skipped) produces a :class:`Reading` with a ``state``
that is not ``READ`` and a reason saying which. Nothing is defaulted, nothing
is coerced to a falsy value that later reads as "fine".

This is the same concern PS-58 addresses one layer up (a skipped test must not
read as a passing one), and it deliberately reuses that vocabulary rather than
inventing a second: ``inconclusive is never a pass``.

Three states, and why not two
-----------------------------
``READ``
    The checker answered and the item was extracted. The reading has a value.

``ABSENT``
    The checker answered, and the item was NOT in the answer — a JSON path that
    does not exist, a pattern that did not match. This is a fact ABOUT THE
    CHECKER's answer and it is not an error: for an adverse pattern ("Proxy
    detected"), ABSENT is precisely the good news. Distinct from ``READ`` with
    a false value because "the page did not say this" and "the page said no"
    are different observations, and a checker that stops publishing a field
    must not read as a checker that published a negative.

``UNOBTAINABLE``
    The checker was not read at all. No verdict was seen, so NOTHING about the
    identity may be inferred from this row — including "no news is good news".

Folding ABSENT into UNOBTAINABLE would make every clean page look unread;
folding it into READ-false would make a vanished field look like a verdict.
Both erase a distinction the record exists to keep.

Byte-stability
--------------
A record carries the observed exit and the run's own timestamp, so it is NOT
byte-stable across runs — unlike ``snapshot.py``, deliberately. A checker
reading is a DATED OBSERVATION of a third party, and the exit it was taken
through is what makes a later comparison interpretable at all (see the
three-sort rule). What is stabilised instead is the SHAPE: sorted keys, every
catalogued item present, so ``git diff`` between two committed readings shows
verdict movement rather than key reordering.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .checkers import (
    Checker,
    JSON_CHECKERS,
    JsonItem,
    TextItem,
    UNREADABLE_CHECKERS,
)
from .exit_guard import Exit

SCHEMA_VERSION = 4

# The body of the document — data, not header. See `schema_ledger.header_keys`.
RECORD_BODY_KEY = "readings"

# What each generation's HEADER ACTUALLY CONTAINS. Maintained by hand and
# checked against the REAL output of :func:`build_record` (never against this
# module's source text — see knowledge article PS-11) by
# ``tests/test_verify_schema_ledger.py``.
#
# **Editing `build_record`'s header means editing this map**, and that edit is
# the moment the version question gets asked. That is the entire mechanism: the
# convention used to live in whoever happened to be reading, and PS-69 shows
# what that is worth.
#
# The rule that fixes each generation's key set: **generation N is defined by
# what committed records labelled N actually carry.** It is not a wish about
# what they should have carried.
#
# 1 — PS-59 (`9bf7f69`), the shape of the one committed reading
#     (`tests/fixtures/checker-matrix-reading.sandbox.json`). That file says
#     `schema_version: 1` and carries exactly these keys, so this entry
#     describes a real artifact rather than a reconstruction.
# 2 — PS-69 (`6741df7`) added `declared_machine` and
#     `declared_machine_honoured` and left the version at 1. THIS TICKET
#     (PS-81) assigns that shape its own number. The committed record is NOT
#     re-tagged: it is genuinely generation 1 and keeps saying so. What changes
#     is that a reading taken from now on says 2, so the two are distinguishable
#     without a consumer sniffing for individual fields — which is precisely
#     what PS-67 was forced to do.
# 3 - PS-103 added `masking_layer`. This is the generation boundary that MATTERS
#     MOST to a consumer, because it is the one that changes the record's
#     SUBJECT rather than adding a field about it. Generations 1 and 2 describe
#     **the packaged engines persona ships, configured with a seed and some
#     flags**: the harness installed none of persona's own masking layer, so
#     none of `webgl_ext`, `audio_ext`, `locale_ext`, `native_ext`,
#     `stealth_ext`, `measuretext_ext`, `voice_ext`, `device_ext`, `gpu_ext` or
#     `canvas_ctx_ext` was present in any reading. Generation 3 describes the
#     engine WITH that layer on top - what an operator's profile presents.
#
#     Those older records are NOT re-tagged and NOT backfilled. They are a real
#     measurement of the engines and the exit, and they keep saying what they
#     are. The version is what lets a consumer tell the two subjects apart
#     without knowing PS-103 exists - which is the whole reason the subject is
#     stated in the header rather than in a note.
# 4 - PS-110 added `evidence`. The generation that states whether the record
#     MEANS anything. Generations 1-3 could describe a run whose browser died
#     on the first heavy page — two fingerprint rows out of twenty-seven — in
#     fields identical to a clean run's, because every catalogued row is
#     present by design and `counts` counts rows. The block carries the
#     verdict (`sufficient` / `inconclusive`), the numerator, the denominator,
#     the contributing checkers and the floor that was applied, so a reader
#     re-derives it rather than trusting it. See `evidence.assess`.
#
#     A generation-3 record is NOT re-tagged and NOT backfilled: it is a real
#     measurement, and a verdict computed today from rows recorded before the
#     question existed would be a claim about a run nobody assessed. It keeps
#     saying 3, and a consumer reads the absence of the block as "this run did
#     not ask", never as "sufficient".
HEADER_GENERATIONS: "dict[int, frozenset[str]]" = {
    1: frozenset({
        "schema_version",
        "observed_at",
        "environment",
        "engine",
        "seed",
        "skipped_tiers",
        "exit",
        "counts",
        "notes",
    }),
    2: frozenset({
        "schema_version",
        "observed_at",
        "environment",
        "engine",
        "seed",
        "declared_machine",
        "declared_machine_honoured",
        "skipped_tiers",
        "exit",
        "counts",
        "notes",
    }),
    3: frozenset({
        "schema_version",
        "observed_at",
        "environment",
        "engine",
        "seed",
        "declared_machine",
        "declared_machine_honoured",
        "masking_layer",
        "skipped_tiers",
        "exit",
        "counts",
        "notes",
    }),
    4: frozenset({
        "schema_version",
        "observed_at",
        "environment",
        "engine",
        "seed",
        "declared_machine",
        "declared_machine_honoured",
        "masking_layer",
        "skipped_tiers",
        "exit",
        "counts",
        "evidence",
        "notes",
    }),
}

# --- reading states ---------------------------------------------------------

READ = "read"
ABSENT = "absent"
UNOBTAINABLE = "unobtainable"


@dataclass(frozen=True)
class Reading:
    """What one checker said about one item.

    ``sort`` (exit / host / fingerprint) rides on the reading itself rather
    than being looked up from the catalogue at comparison time, so a record
    stays interpretable after the catalogue moves — a reading taken when an
    item was tagged EXIT must not silently re-interpret as FINGERPRINT because
    the tag was later corrected.
    """

    checker: str
    item: str
    state: str
    sort: str
    value: Any = None
    # Which GPU vector this reading answers — GPU_CLAIMED (the strings the
    # renderer declares) or GPU_RENDERED (hashes the checker computed from
    # pixels it actually drew). Empty on every reading that is not about the
    # GPU.
    #
    # Rides ON THE READING for the same reason ``sort`` does: a record must
    # stay interpretable after the catalogue moves. It is also the field that
    # makes the owner's "report WHICH of the two a red came from" answerable
    # from a record alone — the two vectors have different fixes, so a report
    # that collapses them into "GPU red" cannot be acted on by whoever picks
    # up the masking ticket.
    vector: str = ""
    # For a prose checker: the pattern used, and the text it matched. Both are
    # recorded so a later run can tell "the verdict changed" from "the checker
    # reworded its page" — two facts that are indistinguishable if only the
    # boolean is kept.
    pattern: str = ""
    matched_text: str = ""
    # Why this reading is not READ. Empty on a READ reading.
    reason: str = ""
    # True when this row was NEVER ASKED — the session ended before the run
    # reached this checker, so nothing was attempted here at all.
    #
    # NOT a synonym for unobtainable, and the difference is the whole of
    # PS-110's second half. "This checker was asked and could not answer" is a
    # reading about that checker. "The browser died and nothing after it was
    # ever asked" is ONE fact about the run wearing the costume of forty-five
    # independent ones — and a comparison against a healthy record reads those
    # forty-five as forty-five moved vectors. The flag is what lets a reader
    # attribute them to the single cause they actually share.
    never_asked: bool = False
    # True when MATCHING this item is the bad news. Carried into the record so
    # a comparator never has to guess a pattern's polarity.
    adverse: bool = False

    def as_record(self) -> dict:
        out = {
            "checker": self.checker,
            "item": self.item,
            "state": self.state,
            "sort": self.sort,
            "adverse": self.adverse,
        }
        if self.vector:
            out["vector"] = self.vector
        if self.state == READ:
            out["value"] = self.value
        if self.pattern:
            out["pattern"] = self.pattern
        if self.matched_text:
            out["matched_text"] = self.matched_text
        if self.reason:
            out["reason"] = self.reason
        if self.never_asked:
            # Emitted only when true, so every record written before this
            # question existed keeps its exact shape and a reader treats a
            # missing key as "not stated" rather than as "it was asked".
            out["never_asked"] = True
        return out


def read(checker: str, item: "JsonItem | TextItem", value: Any, **extra) -> Reading:
    return Reading(
        checker=checker,
        item=item.id,
        state=READ,
        sort=item.sort,
        value=value,
        adverse=getattr(item, "adverse", False),
        vector=getattr(item, "vector", ""),
        **extra,
    )


def absent(checker: str, item: "JsonItem | TextItem", reason: str, **extra) -> Reading:
    return Reading(
        checker=checker,
        item=item.id,
        state=ABSENT,
        sort=item.sort,
        reason=reason,
        adverse=getattr(item, "adverse", False),
        vector=getattr(item, "vector", ""),
        **extra,
    )


def unobtainable(
    checker: str, item: "JsonItem | TextItem", reason: str, **extra
) -> Reading:
    return Reading(
        checker=checker,
        item=item.id,
        state=UNOBTAINABLE,
        sort=item.sort,
        reason=reason,
        adverse=getattr(item, "adverse", False),
        vector=getattr(item, "vector", ""),
        **extra,
    )


# --- extraction -------------------------------------------------------------


def extract_json_item(checker: Checker, item: JsonItem, payload: Any) -> Reading:
    """Walk ``item.path`` into a parsed JSON response.

    A path that runs off the document is ABSENT, not an error and not ``None``:
    the checker answered, it simply did not carry this field. A field present
    but explicitly null IS a value and is recorded as one.
    """
    node = payload
    for key in item.path:
        if not isinstance(node, dict) or key not in node:
            return absent(
                checker.id,
                item,
                f"the response carries no {'.'.join(item.path)}",
            )
        node = node[key]
    return read(checker.id, item, node)


def _negated_at(text: str, start: int, negator: str) -> bool:
    """Is the match at ``start`` introduced by ``negator``?

    Looks BACKWARDS from the match over any run of whitespace and asks whether
    the word immediately before it is the negator. Whitespace-insensitive by
    construction, which is the whole point: this replaced a fixed-width
    lookbehind that could only ever spell a single space.

    Anchored on a word boundary, so "casino masking detected" is not read as a
    negation of "masking detected" — the preceding word must BE the negator,
    not merely end with it.
    """
    before = text[:start]
    stripped = before.rstrip()
    # Something must separate the negator from the phrase; a match glued
    # directly onto the preceding word is not a negated verdict.
    if stripped == before:
        return False
    return bool(
        re.search(rf"(?:^|[^0-9A-Za-z]){re.escape(negator)}$", stripped, re.I)
    )


def extract_text_item(checker: Checker, item: TextItem, text: str) -> Reading:
    """Match ``item.pattern`` against a rendered page's visible text.

    Records the pattern on EVERY outcome, matched or not, so a later run can
    tell a changed verdict from changed wording. A pattern that does not match
    is ABSENT — for an adverse pattern that is the good news, and it must not
    read as "we could not look".

    ``\\s+`` is applied to the haystack's runs of whitespace only in the sense
    that patterns are written against the text as the page renders it; nothing
    is normalised away here, because normalising the haystack is how a pattern
    silently starts matching something it was not written for. That constraint
    is why ``negated_by`` is enforced by WALKING the matches below rather than
    by collapsing the haystack's whitespace first: the GPU patterns are
    newline-anchored and a global collapse would break them.

    THE NEGATION IS ENFORCED HERE, NOT IN THE PATTERN (PS-119). An item that
    declares ``negated_by`` is matched against EVERY occurrence in the page, and
    the ones a negator introduces are skipped. The first occurrence that is NOT
    negated is the reading; if every occurrence is negated, the item is ABSENT —
    which for an adverse item is the clean verdict.

    This exists because the previous spelling, an inline ``(?<!no )``, is
    FIXED-WIDTH: it matched a clean "No masking detected" the moment the page
    put a newline, a tab or two spaces between the two — which is exactly what
    ``inner_text`` yields when a checker renders its verdict as a component
    tree. A clean page then read as a detection, with a real match and a real
    quote behind it.

    ``capture_all`` — WHEN THE COUNT OF MATCHES *IS* THE VERDICT (PS-121)
    -------------------------------------------------------------------
    The walk above takes the FIRST non-negated match, and for the checkers that
    publish ONE answer per page (a score, a country) that is the whole reading.
    It is wrong for a checker that renders ONE ROW PER TEST, because there the
    number of adverse rows is the verdict: rebrowser's page carries a line per
    probe, so "caught by one test" and "caught by three" are different results.

    A first-match reading records the same name in both — and
    :func:`~.matrix_diff._verdict` compares ``(state, value)`` only, so the two
    compare EQUAL and ``_classify`` returns ``None``: its "read on both sides
    and agreed" branch, the silent pass. A browser getting caught by two more
    tests would report no change at all, on the one checker in the tier that
    reads modern CDP leaks. That is the regression Level 3 exists to catch,
    invisible to the instrument meant to catch it.

    So an item may declare ``capture_all`` and take EVERY non-negated match,
    deduplicated and SORTED — sorted because the value must depend on which
    tests fired and never on the order the page rendered them, or a reshuffled
    table reads as a changed verdict and we are back to manufacturing findings.
    ``matched_text`` widens to quote every matched row for the same reason: a
    quote that backs one third of the value beside it is not evidence.

    Opt-in rather than the default, deliberately — joining the incidental
    repeats of a single-answer item would corrupt the one value its comparator
    is written to read.
    """
    try:
        matches = list(re.finditer(item.pattern, text, re.IGNORECASE))
    except re.error as exc:
        # A malformed pattern is OUR defect, and it is unobtainable rather than
        # absent: we did not look, so we cannot say the page lacks the verdict.
        return unobtainable(
            checker.id,
            item,
            f"the pattern is not a valid regular expression: {exc}",
            pattern=item.pattern,
        )

    negator = getattr(item, "negated_by", "")
    live = []
    negated = 0
    for candidate in matches:
        if negator and _negated_at(text, candidate.start(), negator):
            negated += 1
            continue
        live.append(candidate)
    match = live[0] if live else None

    if match is None:
        # Distinguish "the page never says this" from "the page says it, and
        # every time it does a negator introduces it". Both are ABSENT — the
        # verdict is the same and must not read as "we could not look" — but
        # only the second one tells a later reader that the guard did work.
        reason = (
            f"the pattern matched {negated} time(s), every one of them "
            f"negated by {negator!r}"
            if negated
            else "the pattern did not match"
        )
        return absent(checker.id, item, reason, pattern=item.pattern)
    whole = match.group(0)
    value: Any = True
    if item.capture:
        if match.groups():
            if getattr(item, "capture_all", False):
                # EVERY non-negated match, not the first (PS-121). The
                # comparator reads (state, value) only, so a first-match value
                # on a page that renders one row per test records the same
                # reading whether one row or five are adverse — a browser going
                # from 1 detection to 3 then compares EQUAL and the diff
                # returns None, its "read on both sides and agreed" branch.
                #
                # Sorted and deduplicated so the value depends on WHICH tests
                # fired and never on the order the page rendered them: an
                # unsorted join would report a reshuffled table as a changed
                # verdict, which is the false positive this ticket is about
                # wearing different clothes.
                value = ",".join(sorted({m.group(1) for m in live}))
                # The quote must cover what the value claims, so it is every
                # matched row rather than the first — otherwise `matched_text`
                # backs one third of the reading it sits beside.
                whole = " | ".join(m.group(0).strip() for m in live)
            else:
                value = match.group(1)
        else:
            # Declared as capturing but the pattern has no group: our defect,
            # and recording `True` would quietly publish a boolean where a
            # comparator expects a number.
            return unobtainable(
                checker.id,
                item,
                "the item declares capture=True but the pattern has no group",
                pattern=item.pattern,
                matched_text=whole[:200],
            )
    return read(
        checker.id,
        item,
        value,
        pattern=item.pattern,
        matched_text=whole[:200],
    )


def readings_for_unread_checker(
    checker: Checker, reason: str, *, never_asked: bool = False
) -> "list[Reading]":
    """Every item of a checker that was not read, as UNOBTAINABLE rows.

    A checker that did not answer still occupies its full width in the record.
    Emitting one summary row instead would make the matrix silently narrower on
    exactly the runs where something went wrong.

    ``never_asked`` marks the rows the run never got to ATTEMPT, as opposed to
    the ones it attempted and could not obtain. Both are unobtainable — nothing
    may be inferred from either — but they are different facts about the RUN,
    and only one of them is a reading about the checker. See
    :attr:`Reading.never_asked`.
    """
    return [
        unobtainable(checker.id, item, reason, never_asked=never_asked)
        for item in checker.items
    ]


# --- the run ----------------------------------------------------------------


def read_json_tier(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]" = JSON_CHECKERS,
    timeout: float | None = None,
    fetch_json=None,
) -> "list[Reading]":
    """Read every JSON-tier checker through the proxy.

    ``fetch_json`` is injected for tests; it defaults to the real socks5h
    fetcher. A checker that fails to answer yields UNOBTAINABLE rows for ALL
    of its items, with the failure as the reason — never a partial row set and
    never a silent skip.
    """
    if fetch_json is None:
        from .socks_fetch import fetch_json as _real

        def fetch_json(url, **kw):  # type: ignore[misc]
            return _real(url, proxy_url=proxy_url, **kw)

    out: "list[Reading]" = []
    for checker in checkers:
        kwargs = {} if timeout is None else {"timeout": timeout}
        try:
            payload = fetch_json(checker.url, **kwargs)
        except Exception as exc:
            out.extend(
                readings_for_unread_checker(
                    checker, f"{type(exc).__name__}: {exc}"
                )
            )
            continue
        for item in checker.items:
            out.append(extract_json_item(checker, item, payload))
    return out


def read_unreadable_tier(
    checkers: "tuple[Checker, ...]" = UNREADABLE_CHECKERS,
) -> "list[Reading]":
    """The checkers we do not read, recorded as not read.

    They carry no items, so each contributes ONE row naming the checker and the
    reason. This is a result, not a gap: "we could not read this" is exactly
    what the ticket asks to be recorded, and a matrix that omitted them would
    quietly claim a coverage it does not have.

    Nothing is fetched here. Building anything to get past a challenge is out
    of scope by charter.
    """
    return [
        Reading(
            checker=checker.id,
            item="(whole checker)",
            state=UNOBTAINABLE,
            sort="",
            reason=checker.unreadable_reason,
        )
        for checker in checkers
    ]


# --- the record -------------------------------------------------------------


def build_record(
    readings: "list[Reading]",
    *,
    exit_: Exit,
    engine: str,
    observed_at: str,
    environment: str = "",
    seed: int = 0,
    declared_machine: str = "",
    declared_machine_honoured: bool = True,
    masking_layer: "dict | None" = None,
    skipped_tiers: "list[str] | None" = None,
    notes: "list[str] | None" = None,
    evidence_floor: "dict | None" = None,
) -> dict:
    """Assemble the committed document.

    THE EVIDENCE BLOCK IS THE RECORD'S STATEMENT OF WHETHER IT MEANS ANYTHING,
    and it is why generation 4 exists. A record can be complete, correctly
    counted, written to disk and describe nothing: PS-110 was filed off a run
    whose browser died on the first heavy page, recorded 2 fingerprint rows out
    of 27, and was indistinguishable from a clean run in every field this
    header carried. ``counts`` could not close that hole and was never able to
    — it counts ROWS, and the rows are all present by design (see
    :func:`readings_for_unread_checker`), so a run that measured nothing has
    exactly the same ``total`` as one that measured everything.

    It is COMPUTED HERE, from the rows being written, rather than accepted from
    the caller. The verdict must describe what the record actually contains,
    and a caller that could pass it in is a caller that could pass in its own
    optimism — the same argument that has always put ``counts`` here rather
    than taking a tally from whoever ran the tiers.

    ``evidence_floor`` overrides the thresholds for a caller that has grounds
    to (a test inducing the condition, an operator reading a narrower matrix).
    The floor that was actually APPLIED is recorded inside the block, so a
    record is never judged against a threshold a reader has to guess.

    THE MASKING LAYER IS THE HEADER'S STATEMENT OF ITS OWN SUBJECT, and it is
    the reason this generation exists. Every record written before PS-103
    describes **the packaged engines persona ships, configured with a seed and
    some flags** — the harness installed none of persona's own masking layer,
    so a reading could not move when persona's masking changed. Those records
    are not deleted: they are a real measurement of the engines and the exit.
    But a consumer must be able to tell WHICH SUBJECT a record describes without
    knowing that ticket exists, and this key plus the schema version is where
    that belongs.

    ``None`` means the run did not say — which is what every generation-2 record
    is, and it is recorded as ``null`` rather than as an empty layer. The two are
    genuinely different findings: "no layer was installed" is a measurement,
    "this run predates the question" is not, and collapsing them would let an
    old engine-only record read as a deliberate control arm.

    The observed EXIT is a first-class part of the header, not a footnote: a
    fingerprint reading that moved when only the address moved is a coupling,
    and that correlation cannot be made at all unless the address is in the
    record beside the readings.

    The SEED is in the header for the same reason and it is the other half of
    that same question. The engine's fingerprint is SEED-DERIVED — two runs on
    one seed present one identity, two runs on different seeds present two —
    so without it a comparison cannot tell A REAL COUPLING from A DIFFERENT
    SEED, which is the exact analysis the record exists to enable. Measured:
    the renderer moved ``NVIDIA GTX 980`` -> ``Intel HD Graphics 400`` between
    two runs here purely because the seed differed. ``0`` means the engine's
    own default was used (no seed passed), which is itself the reproducible
    fact worth recording.

    The DECLARED MACHINE is in the header for the third time by the same
    argument. It is the spine of a presented identity — it constrains GPU
    strings, voices, fonts, screen conventions, platform flags, the user agent
    and client hints — so two records taken on different declared machines
    differ for a reason that has nothing to do with a coupling. Without it in
    the header a later comparison cannot tell a real coupling from a different
    configuration, which is exactly the argument that put the seed here.

    ``declared_machine_honoured`` is the half that keeps that field honest.
    persona's two engines are NOT symmetric: chromium honours the requested
    machine, and the Firefox engine cannot be asked at all (no parameter
    exists) and presents Windows regardless — ``services/browser/process.py``
    records the same behaviour for the product as #211. So the header states
    what the engine ACTUALLY DECLARED plus whether asking changed anything. A
    record that simply echoed the request would fabricate a machine on every
    Firefox run, and a comparator would read the resulting difference as a
    product coupling rather than as an engine that ignored the question.

    ``skipped_tiers`` names any tier the operator asked not to read. A skipped
    tier's rows are UNOBTAINABLE rows like any other unread checker — see
    :func:`readings_for_unread_checker` — so the matrix keeps its full width;
    this key is the header-level statement of the same fact, so a later
    comparison can tell "the browser tier was skipped" from "those checkers did
    not exist in that schema" without inferring it from a row count.
    """
    from .evidence import assess

    by_state: "dict[str, int]" = {READ: 0, ABSENT: 0, UNOBTAINABLE: 0}
    for reading in readings:
        by_state[reading.state] = by_state.get(reading.state, 0) + 1
    rows = [r.as_record() for r in sorted(
        readings, key=lambda r: (r.checker, r.item)
    )]
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at,
        "environment": environment,
        "engine": engine,
        "seed": seed,
        "declared_machine": declared_machine,
        "declared_machine_honoured": declared_machine_honoured,
        "masking_layer": masking_layer,
        "skipped_tiers": list(skipped_tiers or []),
        "exit": exit_.as_record(),
        "counts": {
            "total": len(readings),
            "read": by_state[READ],
            "absent": by_state[ABSENT],
            "unobtainable": by_state[UNOBTAINABLE],
        },
        # Asked of the EMITTED rows, not of the Reading objects, so the verdict
        # is derived from the same document a later reader will re-derive it
        # from. A block computed off richer in-memory state than the file
        # carries could claim a verdict the record cannot support.
        "evidence": assess(
            rows, floor=evidence_floor, skipped_tiers=skipped_tiers
        ),
        "notes": list(notes or []),
        "readings": rows,
    }


def dumps(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(record: dict, path: str) -> None:
    """Write atomically, so an interrupted run cannot leave a half record.

    A truncated JSON file is not a bad reading, it is an unreadable one — and
    the next run would have to guess whether it was a real record.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False
    )
    try:
        handle.write(dumps(record))
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


__all__ = [
    "ABSENT",
    "HEADER_GENERATIONS",
    "READ",
    "RECORD_BODY_KEY",
    "Reading",
    "SCHEMA_VERSION",
    "UNOBTAINABLE",
    "absent",
    "build_record",
    "dumps",
    "extract_json_item",
    "extract_text_item",
    "read",
    "read_json_tier",
    "read_unreadable_tier",
    "readings_for_unread_checker",
    "unobtainable",
    "write",
]
