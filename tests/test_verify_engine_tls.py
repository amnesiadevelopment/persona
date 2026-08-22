"""The engine-driven TLS tier, and the rule that a row must prove its own origin.

WHY THIS FILE EXISTS
--------------------
PS-59's first live reading returned real, well-formed JA3/JA4/HTTP-2 data from
the TLS endpoints — and none of it described persona. It described this
repository's Python fetcher, and the tell was in the reading itself::

    user_agent: curl/8.14.1

Tagged FINGERPRINT, a later Python or OpenSSL upgrade would have read as
**persona's fingerprint moving**, sending somebody after a masking regression
that never happened. PS-62 closes that gap by driving the same endpoints from
the ENGINE — and, more importantly, by refusing to record any row as persona's
until the checker's own answer shows the engine is what asked.

THE TEST THIS FILE IS REALLY FOR
---------------------------------
"Show it distinguishing." A tagger that has only ever seen one kind of caller
has not been tested: it would pass identically if it were a constant. So the
central tests here feed the SAME endpoint's response twice — once as the
engine answered it, once as the Python client answered it — and assert the two
produce **different values, different sorts, and different states**. Both
directions, in one assertion, because either alone is satisfiable by a stub.

The payloads below are shaped from the REAL responses these endpoints return
(the harness half is the shape PS-59 actually recorded live, ``curl/8.14.1`` /
``HTTP/1.1`` included). They are fixtures rather than live reads because the
exit is the operator's and rotates: a live pair proves the tagger once, on one
exit, on one day; these prove it on every run of the suite.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.services.verify.checkers import (
    ALL_SORTS,
    ENGINE_TLS_CHECKERS,
    EXIT,
    FINGERPRINT,
    HARNESS,
    JSON_CHECKERS,
    checker_by_id,
)
from src.services.verify.engine_tls import (
    WITNESS_ITEM,
    readings_from_payloads,
    retag,
    unread_engine_tls_tier,
)
from src.services.verify.matrix import (
    ABSENT,
    READ,
    UNOBTAINABLE,
    build_record,
)
from src.services.verify.origin_proof import (
    ENGINE,
    SCRIPTING_CLIENT,
    UNKNOWN,
    classify_user_agent,
    describe,
)

PEET = "tls.peet.ws@engine"

# The UA persona's patched Firefox presents. A real engine announces a layout
# engine; this is what makes the row provable.
ENGINE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
# What PS-59's live run actually recorded, verbatim. The whole ticket exists
# because THIS was tagged as persona's fingerprint.
HARNESS_UA = "curl/8.14.1"


def _peet_payload(user_agent: str, *, ja4: str, http_version: str) -> dict:
    """A tls.peet.ws response, in the shape the endpoint really returns."""
    return {
        "ip": "188.146.33.135:51000",
        "http_version": http_version,
        "user_agent": user_agent,
        "tls": {
            "ja4": ja4,
            "ja3": f"ja3n-of-{ja4}",
            "ja3_hash": "permutation-sensitive-do-not-read",
            "peetprint_hash": f"peet-of-{ja4}",
        },
        "http2": {"akamai_fingerprint": "1:65536;2:0;4:131072;5:16384|…"},
        "http1": {"headers": ["Host: tls.peet.ws", "User-Agent: …"]},
    }


def engine_payload() -> dict:
    """The endpoint's answer when PERSONA'S ENGINE asked."""
    return _peet_payload(
        ENGINE_UA, ja4="t13d1715h2_5b57614c22b0_3d5424432f57",
        http_version="h2",
    )


def python_payload() -> dict:
    """The endpoint's answer when THIS REPO'S PYTHON CLIENT asked.

    The values are PS-59's live reading: a different JA4, HTTP/1.1, and the
    ``curl/8.14.1`` UA that exposed the original mistake.
    """
    return _peet_payload(
        HARNESS_UA, ja4="t13d1712h1_5b57614c22b0_93c746dc12af",
        http_version="HTTP/1.1",
    )


def _peet_rows(result: dict):
    """Read ONE checker, scoped deliberately.

    ``readings_from_payloads`` defaults to the whole engine-TLS catalogue, and
    a run that supplies only peet correctly records the other two as
    unobtainable. Keying those by item alone would collapse three checkers into
    one dict and let an unread row overwrite a read one — so the checker set is
    named here rather than left to the default.
    """
    return readings_from_payloads(result, checkers=(checker_by_id(PEET),))


def _by_item(readings) -> dict:
    return {r.item: r for r in readings}


# --- THE CENTRAL TEST: the tagger, shown distinguishing ----------------------


def test_the_same_endpoint_read_by_two_clients_yields_two_DIFFERENT_taggings():
    """THE ticket's "show it distinguishing" requirement, in one assertion.

    The identical endpoint, the identical catalogue, the identical extraction
    path — and the only thing that differs is WHO ASKED. The engine's rows are
    persona's fingerprint; the Python client's rows are the instrument's. A
    tagger that returned a constant, or that read the transport instead of the
    response, fails this: it cannot produce two different answers from one code
    path without actually consulting the evidence.
    """
    engine = _by_item(_peet_rows({PEET: {"payload": engine_payload()}}))
    harness = _by_item(_peet_rows({PEET: {"payload": python_payload()}}))

    # 1. The SORT differs — the whole point.
    assert engine["ja4"].sort == FINGERPRINT
    assert harness["ja4"].sort == HARNESS

    # 2. The VALUE differs — so these are genuinely two different handshakes
    #    and not one reading relabelled.
    assert engine["ja4"].value != harness["ja4"].value
    assert engine["ja4"].value.startswith("t13d1715h2_")
    assert harness["ja4"].value.startswith("t13d1712h1_")

    # 3. The demoted row KEEPS its value: it is a real, comparable reading
    #    about the instrument, which is exactly what PS-46's egress work needs.
    assert harness["ja4"].state == READ

    # 4. The reason NAMES the evidence, so a reader of the record does not have
    #    to take the tag on faith.
    assert "curl/8.14.1" in harness["ja4"].reason
    assert "instrument" in harness["ja4"].reason.lower()


def test_the_witness_is_the_user_agent_the_checker_echoed_back():
    """The row that proves the others. It is catalogued FIRST on every
    engine-TLS checker precisely so a row set can demonstrate its own origin."""
    engine = _by_item(_peet_rows({PEET: {"payload": engine_payload()}}))
    assert engine[WITNESS_ITEM].value == ENGINE_UA
    assert engine[WITNESS_ITEM].state == READ


@pytest.mark.parametrize(
    "user_agent,expected",
    [
        (ENGINE_UA, ENGINE),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", ENGINE),
        ("curl/8.14.1", SCRIPTING_CLIENT),
        ("python-requests/2.32.3", SCRIPTING_CLIENT),
        ("python-urllib3/2.2.1", SCRIPTING_CLIENT),
        ("Go-http-client/2.0", SCRIPTING_CLIENT),
        ("", UNKNOWN),
        (None, UNKNOWN),
        (12345, UNKNOWN),
        ("something-nobody-catalogued/1.0", UNKNOWN),
    ],
)
def test_classify_user_agent_reads_both_kinds_of_caller(user_agent, expected):
    """BOTH directions and the middle. persona ships a patched FIREFOX and a
    CHROMIUM, so both layout-engine families must classify as the engine — a
    matrix that recognised only the one it ran today would silently drop every
    row the other produced."""
    assert classify_user_agent(user_agent) == expected


def test_a_scripting_client_wearing_a_browser_token_is_read_as_the_instrument():
    """Precedence, pinned. ``python-requests`` never carries a Gecko token, but
    a client claiming BOTH is claiming to be two things at once, and the honest
    reading of that is "the instrument", never "the product".

    Getting this order backwards is how a harness row gets PROMOTED to a
    fingerprint row — the exact defect this module exists to prevent.
    """
    assert classify_user_agent(f"{ENGINE_UA} python-requests/2.32.3") == (
        SCRIPTING_CLIENT
    )


# --- the fail-safe direction ------------------------------------------------


def test_an_unprovable_origin_is_UNOBTAINABLE_and_never_a_fingerprint():
    """A row that cannot demonstrate its own origin is not a reading about
    persona. It is recorded as unobtainable WITH THE REASON — never guessed
    into FINGERPRINT (which would publish a false fingerprint) and never
    guessed into HARNESS (which would bury a real one)."""
    payload = engine_payload()
    del payload["user_agent"]

    rows = _by_item(_peet_rows({PEET: {"payload": payload}}))

    assert rows["ja4"].state == UNOBTAINABLE
    assert rows["ja4"].sort == FINGERPRINT  # the CEILING is unchanged…
    assert rows["ja4"].value is None        # …but nothing is published under it
    assert "does not show which client" in rows["ja4"].reason


def test_an_unrecognised_user_agent_does_not_become_personas_fingerprint():
    """The quieter half of the same rule: the field is PRESENT but says nothing
    we can attribute. Silence and an unfamiliar name must land identically."""
    payload = engine_payload()
    payload["user_agent"] = "acme-fetcher/9"

    rows = _by_item(_peet_rows({PEET: {"payload": payload}}))
    assert rows["ja4"].state == UNOBTAINABLE
    assert rows["ja4"].value is None


def test_an_exit_row_is_NOT_demoted_by_an_unproven_origin():
    """An observed ADDRESS is a property of the exit and is the same fact
    whichever client asked. Demoting it would discard a true reading to punish
    an unrelated uncertainty — and the record would lose the one row that makes
    a rotating exit interpretable.

    (The Python tier's catalogue records the identical exception for
    ipleak.net's geography.)
    """
    payload = python_payload()  # origin is the SCRIPTING CLIENT
    rows = _by_item(_peet_rows({PEET: {"payload": payload}}))

    assert rows["observed_ip"].sort == EXIT
    assert rows["observed_ip"].state == READ
    assert rows["observed_ip"].value == "188.146.33.135:51000"


def test_a_checker_that_did_not_answer_keeps_its_FULL_WIDTH_as_unobtainable():
    """A tier that read less must never look like a tier that read clean. Every
    catalogued item is present with the reason."""
    rows = _peet_rows({PEET: {"error": "NS_ERROR_UNKNOWN_HOST"}})
    peet = checker_by_id(PEET)

    assert len(rows) == len(peet.items)
    assert all(r.state == UNOBTAINABLE for r in rows)
    assert all("NS_ERROR_UNKNOWN_HOST" in r.reason for r in rows)


def test_a_body_that_did_not_parse_names_the_json_viewer_as_the_suspect():
    """The engine reads these endpoints as TEXT, which only works because
    ``devtools.jsonview.enabled`` is pinned OFF. A future run that flipped it
    would get a DOM tree instead of raw JSON — recorded as unobtainable with a
    reason that names the cause, not as an endpoint that went quiet."""
    from src.services.verify.engine_tls import fetch_payloads_with_engine

    class _Page:
        def goto(self, *a, **k):
            return None

        def inner_text(self, _sel):
            return "{ ip: not-quoted-json }"

        def close(self):
            return None

    class _Live:
        def new_page(self):
            return _Page()

    peet = checker_by_id(PEET)
    out = fetch_payloads_with_engine(
        _Live(), checkers=(peet,), navigation_timeout_ms=1000
    )
    assert "devtools.jsonview.enabled" in out[PEET]["error"]


def test_an_engine_that_never_started_still_records_every_tls_row():
    """"persona's TLS fingerprint was not read" is the exact fact this ticket
    exists because the record previously failed to state. It is recorded."""
    rows = unread_engine_tls_tier("the engine could not be launched here")
    expected = sum(len(c.items) for c in ENGINE_TLS_CHECKERS)

    assert len(rows) == expected
    assert all(r.state == UNOBTAINABLE for r in rows)
    assert all("could not be launched" in r.reason for r in rows)


# --- catalogue invariants ---------------------------------------------------


def test_every_engine_tls_checker_catalogues_its_witness_first():
    """Without the witness the rows cannot be attributed at all, and it is
    FIRST so a reader meets the evidence before the claims."""
    for checker in ENGINE_TLS_CHECKERS:
        assert checker.items[0].id == WITNESS_ITEM, (
            f"{checker.id} does not lead with its witness"
        )


def test_raw_ja3_is_read_on_neither_tier():
    """JA3 moves with TLS extension PERMUTATION, so it manufactures drift in
    the one record built to detect drift. PS-59 pinned this for the Python
    tier; the engine tier must not quietly reintroduce it."""
    for checker in tuple(JSON_CHECKERS) + tuple(ENGINE_TLS_CHECKERS):
        for item in checker.items:
            assert item.path[-1] not in ("ja3_hash", "ja3_text"), (
                f"{checker.id}.{item.id} reads raw JA3"
            )


def test_the_engine_tier_reads_the_comparison_keys_it_claims_to():
    """Guard the guard above: JA3 being absent must not be because the tier
    reads no TLS vector at all."""
    peet = _by_item(_peet_rows({PEET: {"payload": engine_payload()}}))
    assert peet["ja4"].state == READ
    assert peet["ja3n"].state == READ


def test_engine_rows_do_not_collide_with_the_harness_rows_they_sit_beside():
    """The harness rows STAY — they pin what this repo's own fetcher looks like,
    which is what PS-46's egress work needs. The engine rows are added BESIDE
    them, so the record must carry both without either overwriting the other.

    The record is keyed by (checker, item), so distinct checker ids are what
    make that possible.
    """
    engine_ids = {c.id for c in ENGINE_TLS_CHECKERS}
    python_ids = {c.id for c in JSON_CHECKERS}
    assert not (engine_ids & python_ids)

    # And the pairing is real: every engine checker names a python one.
    for cid in engine_ids:
        assert cid.replace("@engine", "") in python_ids


def test_every_engine_tls_item_declares_a_known_sort():
    for checker in ENGINE_TLS_CHECKERS:
        for item in checker.items:
            assert item.sort in ALL_SORTS


def test_both_readings_survive_into_one_record_side_by_side():
    """End to end, at the level a human reads: one record carrying the SAME
    endpoint twice — the instrument's handshake and persona's — distinguishable
    by sort, and neither having displaced the other."""
    from src.services.verify.exit_guard import Exit

    engine_rows = _peet_rows({PEET: {"payload": engine_payload()}})
    harness_rows = _peet_rows({PEET: {"payload": python_payload()}})
    # Re-key the harness half onto the python checker id, as a real run would:
    # those rows come from JSON_CHECKERS, not from the @engine catalogue.
    harness_rows = [
        dataclasses.replace(r, checker="tls.peet.ws") for r in harness_rows
    ]

    record = build_record(
        engine_rows + harness_rows,
        exit_=Exit(ip="188.146.33.135", country="PL", city="Warsaw",
                   org="AS12912 T-Mobile Polska", timezone="Europe/Warsaw"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-22T03:00:00Z",
    )

    ja4 = {
        (r["checker"], r["item"]): r
        for r in record["readings"]
        if r["item"] == "ja4"
    }
    assert ja4[("tls.peet.ws@engine", "ja4")]["sort"] == FINGERPRINT
    assert ja4[("tls.peet.ws", "ja4")]["sort"] == HARNESS
    assert (
        ja4[("tls.peet.ws@engine", "ja4")]["value"]
        != ja4[("tls.peet.ws", "ja4")]["value"]
    )


# --- the demotion itself ----------------------------------------------------


def test_retag_leaves_a_proven_engine_row_untouched():
    rows = _peet_rows({PEET: {"payload": engine_payload()}})
    ja4 = _by_item(rows)["ja4"]
    assert retag(ja4, ENGINE, "reason") is ja4


def test_describe_quotes_the_evidence_rather_than_asserting_a_verdict():
    """A row that merely said "origin unproven" would send the next reader back
    to a live run to find out what was actually seen."""
    assert "curl/8.14.1" in describe(SCRIPTING_CLIENT, "curl/8.14.1", "HTTP/1.1")
    assert "HTTP/1.1" in describe(SCRIPTING_CLIENT, "curl/8.14.1", "HTTP/1.1")
    assert "(none)" in describe(UNKNOWN, None)
