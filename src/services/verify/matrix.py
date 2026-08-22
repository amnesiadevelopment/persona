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

SCHEMA_VERSION = 1

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
    # For a prose checker: the pattern used, and the text it matched. Both are
    # recorded so a later run can tell "the verdict changed" from "the checker
    # reworded its page" — two facts that are indistinguishable if only the
    # boolean is kept.
    pattern: str = ""
    matched_text: str = ""
    # Why this reading is not READ. Empty on a READ reading.
    reason: str = ""
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
        if self.state == READ:
            out["value"] = self.value
        if self.pattern:
            out["pattern"] = self.pattern
        if self.matched_text:
            out["matched_text"] = self.matched_text
        if self.reason:
            out["reason"] = self.reason
        return out


def read(checker: str, item: "JsonItem | TextItem", value: Any, **extra) -> Reading:
    return Reading(
        checker=checker,
        item=item.id,
        state=READ,
        sort=item.sort,
        value=value,
        adverse=getattr(item, "adverse", False),
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


def extract_text_item(checker: Checker, item: TextItem, text: str) -> Reading:
    """Match ``item.pattern`` against a rendered page's visible text.

    Records the pattern on EVERY outcome, matched or not, so a later run can
    tell a changed verdict from changed wording. A pattern that does not match
    is ABSENT — for an adverse pattern that is the good news, and it must not
    read as "we could not look".

    ``\\s+`` is applied to the haystack's runs of whitespace only in the sense
    that patterns are written against the text as the page renders it; nothing
    is normalised away here, because normalising the haystack is how a pattern
    silently starts matching something it was not written for.
    """
    try:
        match = re.search(item.pattern, text, re.IGNORECASE)
    except re.error as exc:
        # A malformed pattern is OUR defect, and it is unobtainable rather than
        # absent: we did not look, so we cannot say the page lacks the verdict.
        return unobtainable(
            checker.id,
            item,
            f"the pattern is not a valid regular expression: {exc}",
            pattern=item.pattern,
        )
    if match is None:
        return absent(
            checker.id, item, "the pattern did not match", pattern=item.pattern
        )
    whole = match.group(0)
    value: Any = True
    if item.capture:
        if match.groups():
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


def readings_for_unread_checker(checker: Checker, reason: str) -> "list[Reading]":
    """Every item of a checker that was not read, as UNOBTAINABLE rows.

    A checker that did not answer still occupies its full width in the record.
    Emitting one summary row instead would make the matrix silently narrower on
    exactly the runs where something went wrong.
    """
    return [unobtainable(checker.id, item, reason) for item in checker.items]


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
    skipped_tiers: "list[str] | None" = None,
    notes: "list[str] | None" = None,
) -> dict:
    """Assemble the committed document.

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

    ``skipped_tiers`` names any tier the operator asked not to read. A skipped
    tier's rows are UNOBTAINABLE rows like any other unread checker — see
    :func:`readings_for_unread_checker` — so the matrix keeps its full width;
    this key is the header-level statement of the same fact, so a later
    comparison can tell "the browser tier was skipped" from "those checkers did
    not exist in that schema" without inferring it from a row count.
    """
    by_state: "dict[str, int]" = {READ: 0, ABSENT: 0, UNOBTAINABLE: 0}
    for reading in readings:
        by_state[reading.state] = by_state.get(reading.state, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at,
        "environment": environment,
        "engine": engine,
        "seed": seed,
        "skipped_tiers": list(skipped_tiers or []),
        "exit": exit_.as_record(),
        "counts": {
            "total": len(readings),
            "read": by_state[READ],
            "absent": by_state[ABSENT],
            "unobtainable": by_state[UNOBTAINABLE],
        },
        "notes": list(notes or []),
        "readings": [r.as_record() for r in sorted(
            readings, key=lambda r: (r.checker, r.item)
        )],
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
    "READ",
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
