"""Ask the TLS endpoints with persona's ENGINE, and record only what is proven.

The JSON tier (``matrix.read_json_tier``) asks these same endpoints with this
repository's Python client, so every TLS row it produces describes **the
instrument**. This module asks them with **the engine**, so that what the
endpoint records is the handshake persona actually performs — the layer an
antidetect browser most often gets wrong, and the one invisible to every
JS-level probe persona already owns. A page cannot see it; the server sees it
before a single byte of JavaScript runs.

Two properties make this trustworthy, and both are easy to lose by accident.

1. It is the SAME EXTRACTION PATH, not a second parser
-------------------------------------------------------
The response is parsed and walked by ``matrix.extract_json_item`` — the
function the Python tier already uses. Only the TRANSPORT differs, which is
the whole point of the ticket. A second extractor written beside the first
would be free to disagree with it about what a missing field means, and the
record's central distinction (ABSENT is not UNOBTAINABLE) would then depend on
which client happened to fetch the row.

2. The SORT IS EARNED FROM THE RESPONSE, never from the transport
------------------------------------------------------------------
This is the correction the ticket exists to make. The catalogue declares these
items FINGERPRINT because that is what they are WORTH; this module refuses to
record them that way until the checker's own answer shows a browser engine
asked (see :mod:`origin_proof`). Concretely:

===================== ==========================================================
witness says          the row is recorded as
===================== ==========================================================
a browser engine      **FINGERPRINT** — it describes persona
a scripting client    **HARNESS** — it describes the instrument
unrecognised/absent   **UNOBTAINABLE** — it describes nothing that may be read
===================== ==========================================================

"The engine made this request, therefore these rows are the engine's" is
exactly the reasoning that produced the original defect: the JSON tier was
*known* to be fetched by ``socks_fetch`` and its rows were still tagged
FINGERPRINT. So the transport does not get to vouch for itself here. A
mis-wired client, a proxy answering from somewhere else, or a future refactor
that swaps the engine underneath all land as HARNESS or UNOBTAINABLE instead
of as a false claim about persona.

EXIT rows are NOT demoted, and that is deliberate: an observed address is a
property of the exit and is the same fact whichever client asked. Demoting it
would discard a true reading to punish an unrelated uncertainty. (The Python
tier's catalogue records the identical exception for ``ipleak.net``.)

Why the body is read as TEXT
-----------------------------
``page.evaluate`` is refused under the engine's context on real checker pages
(``call to eval() blocked by CSP``), so the browser tier reads ``inner_text``.
That works here because ``browser_tier._prefs`` already pins
``devtools.jsonview.enabled`` OFF: with Firefox's JSON viewer ON the body would
render as a DOM tree and the text would not be JSON at all. This module
therefore depends on that pref rather than merely coexisting with it, and says
so — a later run that flipped it would produce a body that does not parse, and
that outcome is recorded as UNOBTAINABLE with the parse error rather than as an
endpoint that went quiet.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .checkers import (
    Checker,
    ENGINE_TLS_CHECKERS,
    FINGERPRINT,
    HARNESS,
)
from .matrix import (
    READ,
    Reading,
    UNOBTAINABLE,
    extract_json_item,
    readings_for_unread_checker,
)
from .origin_proof import (
    ENGINE,
    SCRIPTING_CLIENT,
    UNKNOWN,
    classify_user_agent,
    describe,
)

# The item every one of these checkers catalogues as its witness.
WITNESS_ITEM = "user_agent"


def _witness(checker: Checker, payload: Any) -> "tuple[str, Any]":
    """Read the checker's echo of who asked, and classify it.

    Returns ``(origin, user_agent_value)``. A checker whose witness item is
    missing from the catalogue, or whose response does not carry it, yields
    :data:`origin_proof.UNKNOWN` — the answer that makes every row on that
    checker unobtainable, which is the fail-safe direction.
    """
    for item in checker.items:
        if item.id != WITNESS_ITEM:
            continue
        reading = extract_json_item(checker, item, payload)
        if reading.state != READ:
            return UNKNOWN, None
        return classify_user_agent(reading.value), reading.value
    return UNKNOWN, None


def _http_version(checker: Checker, payload: Any) -> Any:
    """Corroboration for the record's reason string. Never a gate."""
    for item in checker.items:
        if item.id != "http_version":
            continue
        reading = extract_json_item(checker, item, payload)
        if reading.state == READ:
            return reading.value
    return None


def retag(reading: Reading, origin: str, reason: str) -> Reading:
    """Apply the origin verdict to one reading.

    The catalogue's declared sort is the CEILING, not the floor: a FINGERPRINT
    item is recorded as FINGERPRINT only when the engine is proven, and an EXIT
    item is left alone because an address is the exit's fact whoever asked.

    A demoted-to-HARNESS row keeps its VALUE — it is a real, comparable reading
    about the instrument, which is exactly what the harness rows are for. A row
    whose origin is UNKNOWN keeps none: nothing may be read from it, so it is
    UNOBTAINABLE with the reason, and the value is dropped rather than
    published under a state that says it should not be trusted.
    """
    if reading.sort != FINGERPRINT:
        return reading
    if origin == ENGINE:
        return reading
    if origin == SCRIPTING_CLIENT:
        return dataclasses.replace(
            reading,
            sort=HARNESS,
            reason=(f"{reading.reason} — {reason}" if reading.reason else reason),
        )
    return dataclasses.replace(
        reading,
        state=UNOBTAINABLE,
        value=None,
        reason=(f"{reading.reason} — {reason}" if reading.reason else reason),
    )


def readings_from_payloads(
    payloads: "dict[str, dict]",
    *,
    checkers: "tuple[Checker, ...]" = ENGINE_TLS_CHECKERS,
) -> "list[Reading]":
    """Turn captured engine responses into readings. Pure: no network.

    ``payloads`` is ``{checker_id: {"payload": <parsed json>} | {"error": str}}``
    — the same shape ``browser_tier.read_page_texts`` returns for prose pages,
    so the test suite drives this half against recorded responses exactly as it
    drives the prose half against recorded pages.

    A checker missing from ``payloads`` keeps its full width as UNOBTAINABLE
    rows. The record never silently narrows on the runs where less was read.
    """
    out: "list[Reading]" = []
    for checker in checkers:
        got = payloads.get(checker.id)
        if got is None:
            out.extend(
                readings_for_unread_checker(
                    checker, "the run produced no result for this checker"
                )
            )
            continue
        if "error" in got or "payload" not in got:
            reason = got.get("error", "the run captured no response")
            note = checker.note_unreachable
            if note:
                reason = f"{reason} — {note}"
            out.extend(readings_for_unread_checker(checker, reason))
            continue

        payload = got["payload"]
        origin, user_agent = _witness(checker, payload)
        reason = describe(origin, user_agent, _http_version(checker, payload))

        for item in checker.items:
            reading = extract_json_item(checker, item, payload)
            out.append(retag(reading, origin, reason))
    return out


def fetch_payloads_with_engine(
    live,
    *,
    checkers: "tuple[Checker, ...]" = ENGINE_TLS_CHECKERS,
    navigation_timeout_ms: int | None = None,
) -> "dict[str, dict]":
    """Navigate an ALREADY-RUNNING engine to each endpoint and parse the body.

    Takes the live engine rather than launching one, so this tier rides the
    same launch — and therefore the same proven exit — as the prose tier. A
    second launch would be a second set of sockets whose exit had not been
    proven, which is the failure ``browser_tier._observe_engine_exit`` exists
    to prevent.

    One endpoint's failure never takes the others down: it is recorded as that
    checker's error and the rest are still asked.
    """
    if navigation_timeout_ms is None:
        from .browser_tier import NAVIGATION_TIMEOUT_MS

        navigation_timeout_ms = NAVIGATION_TIMEOUT_MS

    out: "dict[str, dict]" = {}
    for checker in checkers:
        try:
            page = live.new_page()
        except Exception as exc:
            out[checker.id] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        try:
            page.goto(
                checker.url,
                timeout=navigation_timeout_ms,
                wait_until="domcontentloaded",
            )
            text = page.inner_text("body")
            try:
                out[checker.id] = {"payload": json.loads(text)}
            except ValueError as exc:
                # NOT an endpoint that went quiet: the body arrived and did not
                # parse. Most likely Firefox's JSON viewer was re-enabled, so
                # the reason names that rather than leaving the next reader to
                # rediscover it.
                out[checker.id] = {
                    "error": (
                        f"the response body did not parse as JSON ({exc}). "
                        "Check that devtools.jsonview.enabled is OFF — with "
                        "the viewer on, the body renders as a DOM tree rather "
                        "than as raw JSON"
                    )
                }
        except Exception as exc:
            out[checker.id] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                page.close()
            except Exception:
                pass
    return out


def unread_engine_tls_tier(
    reason: str,
    *,
    checkers: "tuple[Checker, ...]" = ENGINE_TLS_CHECKERS,
) -> "list[Reading]":
    """Every engine-TLS row as UNOBTAINABLE, with one shared reason.

    Used when the engine never started or its exit was not proven. The rows are
    still PRESENT: "persona's TLS fingerprint was not read" is the exact fact
    this ticket was written because the record failed to state.
    """
    out: "list[Reading]" = []
    for checker in checkers:
        out.extend(readings_for_unread_checker(checker, reason))
    return out


__all__ = [
    "ENGINE_TLS_CHECKERS",
    "WITNESS_ITEM",
    "fetch_payloads_with_engine",
    "readings_from_payloads",
    "retag",
    "unread_engine_tls_tier",
]
