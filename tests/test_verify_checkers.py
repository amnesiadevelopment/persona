"""The checker-matrix reader, proven against the pages it actually reads.

Every pattern in the catalogue is asserted against a REAL captured page under
``tests/fixtures/checker-pages/``, taken through the mobile exit on
2026-08-21. That is deliberate and it is the point of this file: a reader
observed only agreeing has not been observed, and four of the catalogue's
original patterns were WRONG against the real pages in ways that a
hand-written "does it match?" test would have happily confirmed.

The two that matter most, both pinned below:

* **The negation trap.** Pixelscan renders its clean verdicts as ``No proxy
  detected`` / ``No masking detected`` / ``No automated behavior detected``.
  The obvious adverse patterns (``proxy detected``) match all three of those,
  so a reader built on them reports a CLEAN page as three red flags — with a
  real match and a real quote behind it. ``test_pixelscan_clean_page_*``
  asserts the negated forms do not match, and
  ``test_naive_pattern_would_have_misread_the_clean_page`` demonstrates the
  bug the lookbehind fixes, so the guard cannot be deleted as decoration.

* **The false ABSENT.** Sannysoft renders ``WebDriver\\n(New)\\n\\tmissing
  (passed)``. A pattern that assumed one line read that page as ABSENT — the
  page said "passed" and the reader recorded "the page did not say this".
"""

from __future__ import annotations

import json
import os

import pytest

from src.services.verify.browser_tier import readings_from_texts
from src.services.verify.checkers import (
    ALL_SORTS,
    HARNESS,
    BROWSER_CHECKERS,
    CHECKERS,
    EXIT,
    FINGERPRINT,
    HOST,
    JSON_CHECKERS,
    JsonItem,
    TIER_UNREADABLE,
    TextItem,
    checker_by_id,
)
from src.services.verify.matrix import (
    ABSENT,
    READ,
    UNOBTAINABLE,
    build_record,
    dumps,
    extract_json_item,
    extract_text_item,
    read_json_tier,
    read_unreadable_tier,
    write,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "checker-pages")

# Which fixture file carries which checker's captured page.
PAGES = {
    "bot.sannysoft.com": "sannysoft.txt",
    "iphey.com": "iphey.txt",
    "pixelscan.net": "pixelscan.txt",
    "creepjs": "creepjs.txt",
}


def page(checker_id: str) -> str:
    with open(os.path.join(FIXTURES, PAGES[checker_id]), encoding="utf-8") as fh:
        return fh.read()


def reading_for(checker_id: str, item_id: str, text: str | None = None):
    checker = checker_by_id(checker_id)
    assert checker is not None, checker_id
    body = page(checker_id) if text is None else text
    item = next(i for i in checker.items if i.id == item_id)
    return extract_text_item(checker, item, body)


# --- the negation trap ------------------------------------------------------


@pytest.mark.parametrize(
    "item_id", ["proxy_detected", "masking_detected", "automation_detected"]
)
def test_pixelscan_clean_page_does_not_read_as_a_detection(item_id):
    """The clean page contains the adverse phrase, prefixed by "No"."""
    reading = reading_for("pixelscan.net", item_id)
    assert reading.state == ABSENT, (
        f"{item_id} matched a page that says 'No ...' — a clean verdict is "
        f"being recorded as a detection (matched {reading.matched_text!r})"
    )


def test_the_clean_page_really_does_contain_the_adverse_phrases():
    """Guard the guard: if the page stopped saying "No proxy detected", the
    test above would pass for the wrong reason (nothing to trap)."""
    body = page("pixelscan.net").lower()
    assert "no proxy detected" in body
    assert "no masking detected" in body
    assert "no automated behavior detected" in body


def test_naive_pattern_would_have_misread_the_clean_page():
    """Show the bug the lookbehind fixes, so it cannot be deleted as noise.

    This is the version that was written first. It matches the CLEAN page.
    """
    import re

    naive = re.search(r"proxy detected", page("pixelscan.net"), re.IGNORECASE)
    assert naive is not None, (
        "the naive pattern no longer matches, so this guard proves nothing"
    )
    assert naive.group(0).lower() == "proxy detected"
    # ...and the catalogue's pattern does NOT.
    assert reading_for("pixelscan.net", "proxy_detected").state == ABSENT


def test_pixelscan_detection_is_read_when_the_page_really_says_it():
    """The other direction: an adverse pattern must still FIRE on a real
    detection. A lookbehind that suppressed everything would pass every test
    above while reading nothing at all."""
    dirty = page("pixelscan.net").replace("No proxy detected", "Proxy detected")
    reading = reading_for("pixelscan.net", "proxy_detected", dirty)
    assert reading.state == READ
    assert reading.value is True
    assert reading.adverse is True


# --- the false ABSENT -------------------------------------------------------


def test_sannysoft_webdriver_passed_is_read_not_absent():
    reading = reading_for("bot.sannysoft.com", "webdriver_missing_passed")
    assert reading.state == READ, (
        "the page says 'missing (passed)' — recording ABSENT here is a reader "
        "that missed a verdict, not a page that lacked one"
    )
    assert "missing (passed)" in reading.matched_text


def test_sannysoft_webdriver_present_does_not_match_the_clean_page():
    assert reading_for("bot.sannysoft.com", "webdriver_present").state == ABSENT


def test_sannysoft_phantom_probe_names_are_not_detections():
    """The page names every probe PHANTOM_* whatever the outcome, so a loose
    pattern reads a clean page as a PhantomJS detection."""
    assert "PHANTOM_UA" in page("bot.sannysoft.com")
    assert reading_for("bot.sannysoft.com", "phantom_js").state == ABSENT


def test_phantom_probe_label_is_not_a_detection():
    """The defect the FIRST LIVE RUN caught, which the fixture could not.

    The live page carries a probe label spelled ``phantomJS`` — no space. The
    original pattern used ``\\s*``, which matches zero spaces, so the run
    recorded a clean browser as a PhantomJS DETECTION: an adverse verdict
    manufactured entirely by the reader, with a real match to back it up.

    The fixture does not contain that label, so this asserts against the
    string directly rather than pretending the fixture proves it.
    """
    checker = checker_by_id("bot.sannysoft.com")
    item = next(i for i in checker.items if i.id == "phantom_js")
    assert extract_text_item(checker, item, "phantomJS").state == ABSENT
    # ...and the real prose form still reads.
    assert extract_text_item(
        checker, item, "Phantom JS detected"
    ).state == READ


def test_creepjs_gpu_capture_is_not_truncated_at_an_inner_paren():
    """Also caught by the first live run, not by the fixture.

    ``ANGLE (Intel, Intel(R) HD Graphics 400 ...)`` contains a ``)`` INSIDE
    ``Intel(R)``, so a ``[^)]+`` capture stopped there and recorded the
    truncated ``Intel, Intel(R``. A truncated renderer silently defeats the
    machine comparison this row exists for.
    """
    checker = checker_by_id("creepjs")
    item = next(i for i in checker.items if i.id == "gpu_renderer")
    live = "gpu:\nANGLE (Intel, Intel(R) HD Graphics 400 Direct3D11 vs_5_0)\n"
    reading = extract_text_item(checker, item, live)
    assert reading.state == READ
    assert reading.value.endswith(")")
    assert "HD Graphics 400" in reading.value


# --- values, not booleans ---------------------------------------------------


def test_creepjs_ratings_are_captured_as_values():
    """A rating must be a VALUE. As a boolean, 0% -> 40% reads as "still
    matching", i.e. as no change at all."""
    assert reading_for("creepjs", "headless_rating").value == "0"
    assert reading_for("creepjs", "like_headless_rating").value == "6"
    assert reading_for("creepjs", "stealth_rating").value == "0"


def test_creepjs_records_the_host_gpu_string():
    reading = reading_for("creepjs", "gpu_renderer")
    assert reading.state == READ
    assert reading.sort == HOST
    assert "GeForce GTX 980" in reading.value


def test_pixelscan_timezone_agrees_with_the_polish_exit():
    reading = reading_for("pixelscan.net", "timezone_from_js")
    assert reading.state == READ
    assert reading.sort == EXIT
    assert reading.value == "Europe/Warsaw"


def test_iphey_reads_the_trustworthy_verdict_across_its_newline():
    reading = reading_for("iphey.com", "trustworthy")
    assert reading.state == READ
    assert reading.adverse is False
    assert reading_for("iphey.com", "not_trustworthy").state == ABSENT


def test_a_capturing_item_with_no_group_is_unobtainable_not_true():
    """Our own defect must not publish a boolean where a number is expected."""
    checker = checker_by_id("creepjs")
    broken = TextItem("broken", r"\d+% headless", FINGERPRINT, capture=True)
    reading = extract_text_item(checker, broken, page("creepjs"))
    assert reading.state == UNOBTAINABLE
    assert "no group" in reading.reason


def test_a_malformed_pattern_is_unobtainable_not_absent():
    """We did not look, so we cannot say the page lacks the verdict."""
    checker = checker_by_id("creepjs")
    bad = TextItem("bad", r"( unclosed", FINGERPRINT)
    reading = extract_text_item(checker, bad, "anything")
    assert reading.state == UNOBTAINABLE
    assert reading.state != ABSENT


# --- a reading that did not happen ------------------------------------------


def test_an_unreachable_checker_keeps_its_full_width_as_unobtainable():
    """A checker that refused the connection must not shrink the matrix."""
    checker = checker_by_id("deviceandbrowserinfo.com")
    readings = readings_from_texts(
        {"deviceandbrowserinfo.com": {"error": "NS_ERROR_CONNECTION_REFUSED"}},
        checkers=(checker,),
    )
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("CONNECTION_REFUSED" in r.reason for r in readings)
    # ...and the reason carries the standing note about this checker.
    assert any("mobile exit" in r.reason for r in readings)


def test_a_checker_absent_from_the_run_is_unobtainable_not_skipped():
    checker = checker_by_id("iphey.com")
    readings = readings_from_texts({}, checkers=(checker,))
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)


def test_an_empty_page_is_unobtainable_not_a_clean_verdict():
    """A page read before it settled says nothing. Recording its adverse items
    as ABSENT would read exactly like a clean checker."""
    checker = checker_by_id("pixelscan.net")
    readings = readings_from_texts(
        {"pixelscan.net": {"text": "   \n  "}}, checkers=(checker,)
    )
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("settle" in r.reason for r in readings)


def test_every_browser_checker_reads_or_is_recorded_unobtainable():
    """Whole-tier completeness against the captured run: every catalogued item
    appears exactly once, whatever happened to its checker."""
    pages = {cid: {"text": page(cid)} for cid in PAGES}
    pages["deviceandbrowserinfo.com"] = {"error": "NS_ERROR_CONNECTION_REFUSED"}
    pages["bot-detector.rebrowser.net"] = {"error": "NS_ERROR_CONNECTION_REFUSED"}
    readings = readings_from_texts(pages)
    expected = sum(len(c.items) for c in BROWSER_CHECKERS)
    assert len(readings) == expected
    assert {(r.checker, r.item) for r in readings} == {
        (c.id, i.id) for c in BROWSER_CHECKERS for i in c.items
    }


# --- the JSON tier ----------------------------------------------------------


def test_json_path_that_does_not_exist_is_absent_not_none():
    checker = checker_by_id("tls.peet.ws")
    item = JsonItem("gone", ("tls", "nope"), FINGERPRINT)
    reading = extract_json_item(checker, item, {"tls": {"ja4": "x"}})
    assert reading.state == ABSENT
    assert reading.value is None
    assert "tls.nope" in reading.reason


def test_json_null_is_a_value_not_an_absence():
    """A field the checker published AS null is a reading. Folding it into
    ABSENT would erase the difference between "it said null" and "it stopped
    publishing this"."""
    checker = checker_by_id("ipleak.net")
    item = JsonItem("postal", ("postal_code",), EXIT)
    reading = extract_json_item(checker, item, {"postal_code": None})
    assert reading.state == READ
    assert reading.value is None


def test_a_json_checker_that_fails_yields_unobtainable_for_every_item():
    checker = checker_by_id("tools.scrapfly.io")

    def boom(url, **kw):
        raise TimeoutError("timed out")

    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,), fetch_json=boom
    )
    assert len(readings) == len(checker.items)
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("TimeoutError" in r.reason for r in readings)


def test_a_json_checker_that_answers_an_error_page_is_not_read():
    """An HTML error page is not a verdict. This is the shape that most looks
    like success: HTTP 200, bytes on the wire, nothing to parse."""
    from src.services.verify.socks_fetch import FetchFailed

    checker = checker_by_id("tls.peet.ws")

    def html(url, **kw):
        raise FetchFailed("the checker answered HTTP 200 with a body that is "
                          "not JSON")

    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,), fetch_json=html
    )
    assert all(r.state == UNOBTAINABLE for r in readings)


def test_json_readings_carry_their_sort():
    checker = checker_by_id("ipleak.net")
    payload = {"country_code": "PL", "ip": "1.2.3.4", "as_number": 9141,
               "city_name": "Warsaw", "isp_name": "Play",
               "time_zone": "Europe/Warsaw"}
    readings = read_json_tier(
        "socks5h://127.0.0.1:1", checkers=(checker,),
        fetch_json=lambda url, **kw: payload,
    )
    assert {r.sort for r in readings} == {EXIT}


# --- the unreadable tier is a RESULT ----------------------------------------


def test_hostile_checkers_are_recorded_as_unobtainable_with_a_reason():
    readings = read_unreadable_tier()
    assert readings, "the hostile checkers must appear in the record"
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all(r.reason for r in readings)
    assert {r.checker for r in readings} >= {"whoer.net", "amiunique.org"}


# --- the catalogue itself ---------------------------------------------------


def test_every_pattern_compiles():
    import re

    for checker in CHECKERS:
        for item in checker.items:
            if isinstance(item, TextItem):
                re.compile(item.pattern)


def test_every_item_declares_a_known_sort():
    for checker in CHECKERS:
        for item in checker.items:
            assert item.sort in ALL_SORTS, (
                f"{checker.id}.{item.id} has sort {item.sort!r}; without a "
                "sort a rotating exit makes every run look changed"
            )


def test_the_python_fetchers_tls_shape_is_not_tagged_as_personas_fingerprint():
    """The FIRST LIVE RUN recorded ``user_agent: curl/8.14.1`` and
    ``http_version: HTTP/1.1`` on rows tagged FINGERPRINT.

    Those endpoints are fetched by this repository's own Python client, so
    their TLS shape describes THE INSTRUMENT. Left tagged FINGERPRINT, a
    future Python or OpenSSL upgrade would read as PERSONA'S FINGERPRINT
    MOVING — the exact false alarm that makes a real one unbelievable.
    """
    for checker in JSON_CHECKERS:
        for item in checker.items:
            assert item.sort != FINGERPRINT, (
                f"{checker.id}.{item.id} claims to read persona's "
                "fingerprint, but this tier is fetched by the Python client"
            )


def test_the_harness_sort_is_used_and_is_not_a_synonym_for_fingerprint():
    """Guard the guard: HARNESS must actually be carried by real rows, or the
    test above passes because the tier was silently emptied."""
    harness = [
        (c.id, i.id)
        for c in JSON_CHECKERS
        for i in c.items
        if i.sort == HARNESS
    ]
    assert harness, "no row carries HARNESS; the distinction is not being made"
    assert ("tls.peet.ws", "ja4") in harness


def test_exit_driven_geography_stays_exit_driven():
    """ipleak's items are geography — a property of the EXIT, identical
    whichever client asked — so they must NOT be relabelled harness."""
    ipleak = checker_by_id("ipleak.net")
    assert {i.sort for i in ipleak.items} == {EXIT}


def test_ja3_is_not_read_anywhere():
    """JA3 moves with TLS extension permutation and manufactures false drift.
    ja4 / ja3n are read instead."""
    for checker in JSON_CHECKERS:
        for item in checker.items:
            leaf = item.path[-1]
            assert leaf not in ("ja3_hash", "ja3_text"), (
                f"{checker.id}.{item.id} reads raw JA3"
            )


def test_item_ids_are_unique_within_a_checker():
    for checker in CHECKERS:
        ids = [i.id for i in checker.items]
        assert len(ids) == len(set(ids)), checker.id


def test_unreadable_checkers_carry_a_reason_and_no_items():
    for checker in CHECKERS:
        if checker.tier == TIER_UNREADABLE:
            assert checker.unreadable_reason
            assert checker.items == ()


# --- the record -------------------------------------------------------------


def _record(readings):
    from src.services.verify.exit_guard import Exit

    return build_record(
        readings,
        exit_=Exit(ip="1.2.3.4", country="PL", city="Warsaw",
                   org="AS9141 P4", timezone="Europe/Warsaw"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-21T23:00:00Z",
        environment="linux-x86_64 (agent sandbox)",
    )


def test_the_record_carries_the_exit_beside_the_readings():
    """Without the address in the record, "a fingerprint reading moved when
    only the address moved" cannot be asked at all."""
    record = _record(readings_from_texts({cid: {"text": page(cid)} for cid in PAGES}))
    assert record["exit"]["country"] == "PL"
    assert record["exit"]["ip"] == "1.2.3.4"


def test_the_record_counts_unobtainable_separately_from_read():
    readings = readings_from_texts(
        {"pixelscan.net": {"error": "boom"}, "creepjs": {"text": page("creepjs")}},
        checkers=(checker_by_id("pixelscan.net"), checker_by_id("creepjs")),
    )
    counts = _record(readings)["counts"]
    assert counts["unobtainable"] == len(checker_by_id("pixelscan.net").items)
    assert counts["read"] > 0
    assert counts["total"] == len(readings)


def test_an_unobtainable_reading_carries_no_value_key():
    """A value key on an unread row is exactly how "we did not look" starts
    reading as "it said nothing"."""
    readings = readings_from_texts(
        {"iphey.com": {"error": "boom"}}, checkers=(checker_by_id("iphey.com"),)
    )
    for row in _record(readings)["readings"]:
        assert "value" not in row
        assert row["reason"]


def test_the_record_round_trips_as_json_with_sorted_keys():
    record = _record(readings_from_texts({"creepjs": {"text": page("creepjs")}},
                                         checkers=(checker_by_id("creepjs"),)))
    text = dumps(record)
    assert json.loads(text) == record
    assert text.endswith("\n")


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    record = _record([])
    target = tmp_path / "sub" / "reading.json"
    write(record, str(target))
    assert json.loads(target.read_text()) == record
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["reading.json"]
