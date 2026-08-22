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


# --- the browser tier proves its OWN exit ------------------------------------
#
# The Python fetcher's proof (exit_guard.prove_exit) is made on a DIFFERENT
# SOCKET IN A DIFFERENT PROCESS and does not transfer to the engine. Without
# the engine's own proof, an engine whose proxy silently failed would render
# every page, parse every verdict and land every row as READ — a
# complete-looking reading of the OPERATOR'S REAL ADDRESS taken against every
# checker in the matrix. These pin that it cannot.


class _FakePage:
    """The two methods the tier uses, and a record of what was asked."""

    def __init__(self, texts, log, fail_on=()):
        self._texts = texts
        self._log = log
        self._fail_on = fail_on
        self._url = None

    def goto(self, url, **kwargs):
        self._log.append(url)
        self._url = url
        for fragment in self._fail_on:
            if fragment in url:
                raise RuntimeError(f"NS_ERROR_CONNECTION_REFUSED for {url}")

    def inner_text(self, _selector):
        for fragment, text in self._texts.items():
            if fragment in (self._url or ""):
                return text
        return ""

    def close(self):
        pass


class _FakeLive:
    def __init__(self, texts, fail_on=()):
        self.texts = texts
        self.visited = []
        self._fail_on = fail_on

    def new_page(self):
        return _FakePage(self.texts, self.visited, self._fail_on)


def _exit_json(country="PL", ip="91.150.1.1"):
    return json.dumps({
        "ip": ip, "country": country, "city": "Warsaw",
        "org": "AS9141 P4 Sp. z o.o.", "timezone": "Europe/Warsaw",
    }, indent=2)


def test_the_engine_observes_its_own_exit_and_reads_it_as_rows():
    from src.services.verify.browser_tier import _observe_engine_exit

    live = _FakeLive({"ipinfo.io": _exit_json()})
    text, country = _observe_engine_exit(live)
    assert country == "PL"
    readings = readings_from_texts(
        {"engine-exit": {"text": text}}, checkers=(checker_by_id("engine-exit"),)
    )
    by_id = {r.item: r for r in readings}
    assert by_id["observed_ip"].value == "91.150.1.1"
    assert by_id["country"].value == "PL"
    assert by_id["timezone"].value == "Europe/Warsaw"
    # It is EXIT-sorted: it is supposed to move between runs.
    assert all(r.sort == EXIT for r in readings)


def test_an_engine_leaving_through_the_wrong_country_refuses_the_tier():
    """The scenario the reviewer named: the Python fetcher's exit was proven,
    the engine's proxy silently failed, and every page still renders."""
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": _exit_json(country="DE", ip="1.2.3.4")})
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "DE" in str(exc.value)
    assert "PL" in str(exc.value)


def test_an_engine_exit_with_no_country_refuses_rather_than_assuming():
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": json.dumps({"ip": "91.150.1.1"})})
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "no country" in str(exc.value).lower()


def test_an_unreachable_exit_observation_refuses_rather_than_reading_on():
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({}, fail_on=("ipinfo.io",))
    with pytest.raises(ExitNotProvenInEngine) as exc:
        _observe_engine_exit(live)
    assert "could not observe its own exit" in str(exc.value)


def test_an_empty_exit_observation_refuses_it_is_not_a_clean_reading():
    """A page that rendered nothing proves nothing. Reading on would take the
    whole matrix through an address nobody established."""
    from src.services.verify.browser_tier import (
        ExitNotProvenInEngine,
        _observe_engine_exit,
    )

    live = _FakeLive({"ipinfo.io": "   \n  "})
    with pytest.raises(ExitNotProvenInEngine):
        _observe_engine_exit(live)


def test_the_exit_is_observed_BEFORE_any_checker_page_is_loaded():
    """Ordering is the whole guarantee: a checker that has already been asked
    cannot be un-asked, so the proof must precede the first page load."""
    from src.services.verify.browser_tier import _observe_engine_exit

    live = _FakeLive({"ipinfo.io": _exit_json()})
    _observe_engine_exit(live)
    assert len(live.visited) == 1
    assert "ipinfo.io" in live.visited[0]


def test_an_unproven_engine_exit_makes_the_WHOLE_tier_unobtainable():
    """Not a partial record and not a crash: every catalogued browser row is
    present and unobtainable, carrying the reason. The matrix keeps its width
    on exactly the run where something went wrong."""
    import src.services.verify.browser_tier as bt

    live = _FakeLive({"ipinfo.io": _exit_json(country="DE")})

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    # Drive the real body with a stubbed engine constructor.
    import types
    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    import sys as _sys
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        out = bt.read_page_texts("socks5h://u:p@host:1080")
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    readings = readings_from_texts(out)
    expected = sum(len(c.items) for c in BROWSER_CHECKERS)
    assert len(readings) == expected
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("DE" in r.reason for r in readings)
    # And no checker page was ever requested.
    assert live.visited == [checker_by_id("engine-exit").url]


def test_a_proven_engine_exit_then_reads_the_checker_pages():
    """The positive half: with the exit proven, the tier goes on to load the
    pages — so the refusal above is a real gate and not a broken tier."""
    import sys as _sys
    import types
    import src.services.verify.browser_tier as bt

    live = _FakeLive({
        "ipinfo.io": _exit_json(),
        "sannysoft": page("bot.sannysoft.com"),
    })

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        out = bt.read_page_texts(
            "socks5h://u:p@host:1080",
            checkers=(checker_by_id("engine-exit"),
                      checker_by_id("bot.sannysoft.com")),
            sleep=lambda _s: None,
        )
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    assert "text" in out["engine-exit"]
    assert "text" in out["bot.sannysoft.com"]
    # The exit was asked FIRST, and asked exactly once.
    assert live.visited[0] == checker_by_id("engine-exit").url
    assert live.visited.count(checker_by_id("engine-exit").url) == 1


def test_the_engine_exit_is_not_asked_twice_in_one_run():
    """Asking again would record a SECOND, later address for one run — on a
    rotating exit that makes the record contradict itself."""
    import sys as _sys
    import types
    import src.services.verify.browser_tier as bt

    live = _FakeLive({"ipinfo.io": _exit_json()})

    class _Engine:
        def __enter__(self):
            return live

        def __exit__(self, *a):
            return False

    fake_module = types.SimpleNamespace(InvisiblePlaywright=lambda **kw: _Engine())
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake_module
    try:
        bt.read_page_texts(
            "socks5h://u:p@host:1080",
            checkers=(checker_by_id("engine-exit"),),
            sleep=lambda _s: None,
        )
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved

    assert live.visited == [checker_by_id("engine-exit").url]


def test_the_tier_pins_firefoxs_proxy_failover_off():
    """The pref that makes a SILENT wrong reading possible: with failover on,
    Firefox answers a dead SOCKS proxy by retrying DIRECTLY, and the pages
    load, parse and record as a clean run on the operator's real address."""
    from src.services.verify.browser_tier import _prefs

    assert _prefs()["network.proxy.failover_direct"] is False
    assert _prefs()["network.proxy.socks_remote_dns"] is True


def test_the_exit_proof_reads_raw_json_not_the_firefox_json_viewer():
    """The proof's patterns are written against RAW JSON. Firefox's viewer
    renders a JSON body as a DOM tree with UNQUOTED keys, which the quoted
    patterns do not match — so the viewer is turned off."""
    from src.services.verify.browser_tier import _prefs

    assert _prefs()["devtools.jsonview.enabled"] is False

    # And the fail direction is SAFE: viewer-style text does not read as a pass.
    from src.services.verify.browser_tier import _observe_engine_exit
    from src.services.verify.browser_tier import ExitNotProvenInEngine

    viewer_text = "ip: 91.150.1.1\ncountry: PL\ncity: Warsaw"
    live = _FakeLive({"ipinfo.io": viewer_text})
    with pytest.raises(ExitNotProvenInEngine):
        _observe_engine_exit(live)


# --- a skipped tier, and the seed -------------------------------------------


def _read(argv, tmp_path, monkeypatch, exit_country="PL"):
    """Drive the real CLI `read` path with the network stubbed out."""
    import src.services.verify.checker_cli as cli
    from src.services.verify.exit_guard import Exit

    monkeypatch.setattr(
        cli, "prove_exit",
        lambda **kw: ("socks5h://u:p@host:1080",
                      Exit(ip="91.150.1.1", country=exit_country, city="Warsaw",
                           org="AS9141 P4", timezone="Europe/Warsaw")),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    target = tmp_path / "reading.json"
    rc = cli.main(["read", "-o", str(target)] + argv)
    assert rc == 0
    return json.loads(target.read_text())


def test_a_skipped_tier_keeps_its_full_width_as_unobtainable_rows(
        tmp_path, monkeypatch):
    """--skip-browser must not make the record silently NARROWER. A later run
    diffing two records could not otherwise tell "the tier was skipped" from
    "those checkers were dropped" from "that schema had no such tier"."""
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)

    rows = {(r["checker"], r["item"]) for r in record["readings"]}
    for checker in BROWSER_CHECKERS:
        for item in checker.items:
            assert (checker.id, item.id) in rows
    for checker in JSON_CHECKERS:
        for item in checker.items:
            assert (checker.id, item.id) in rows

    skipped = [r for r in record["readings"]
               if "tier skipped" in r.get("reason", "")]
    assert skipped, "a skipped tier must leave rows behind"
    assert all(r["state"] == UNOBTAINABLE for r in skipped)
    # Never as a pass.
    assert not any(r["state"] == READ for r in skipped)


def test_the_header_names_which_tiers_were_skipped(tmp_path, monkeypatch):
    record = _read(["--skip-browser"], tmp_path, monkeypatch)
    assert record["skipped_tiers"] == ["browser"]

    record = _read(["--skip-json"], tmp_path, monkeypatch)
    assert record["skipped_tiers"] == ["json"]


def test_a_run_that_skipped_nothing_says_so_rather_than_omitting_the_key(
        tmp_path, monkeypatch):
    """An absent key would have to be guessed at; an empty list is a statement."""
    import src.services.verify.checker_cli as cli
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    record = _read(["--skip-browser"], tmp_path, monkeypatch)
    assert "skipped_tiers" in record
    assert isinstance(record["skipped_tiers"], list)


def test_the_record_carries_the_seed_that_drove_the_fingerprint(
        tmp_path, monkeypatch):
    """The engine's fingerprint is SEED-DERIVED. Without the seed in the
    header, a comparison cannot tell a real coupling from a different seed —
    measured: the renderer moved NVIDIA GTX 980 -> Intel HD Graphics 400
    between two runs purely because the seed differed."""
    record = _read(["--skip-browser", "--skip-json", "--seed", "4242"],
                   tmp_path, monkeypatch)
    assert record["seed"] == 4242


def test_the_seed_is_recorded_even_when_it_is_the_engine_default(
        tmp_path, monkeypatch):
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)
    assert record["seed"] == 0


def test_the_record_still_carries_the_exit_and_engine_beside_the_seed(
        tmp_path, monkeypatch):
    record = _read(["--skip-browser", "--skip-json"], tmp_path, monkeypatch)
    assert record["exit"]["country"] == "PL"
    assert record["engine"]
    assert record["observed_at"]


# --- two catalogue defects the first live run through a ROTATING exit found --
#
# Both were found by MEASUREMENT, not review: the fixture pages could not have
# caught either, because both fixtures were captured on the one exit
# (Warsaw) and the one page state that happens to hide them. They are reader
# defects, which are in scope; a product fix would not be.


def test_a_rotating_polish_exit_still_reads_as_poland():
    """DEFECT 1. `geo_poland` was `poland\\s*/\\s*warsaw` — a CITY hardcoded
    into an EXIT-sorted item. The exit rotates within Poland BY DESIGN, so the
    moment it moved to Ursynów/Krakow (measured 2026-08-22) a perfectly clean
    Polish page read ABSENT, which looks exactly like the checker having
    stopped reporting Poland."""
    item = next(i for i in checker_by_id("pixelscan.net").items
                if i.id == "geo_country_city")
    checker = checker_by_id("pixelscan.net")

    # MULTI-WORD cities are in this loop deliberately. Poland is full of them
    # (Nowy Sącz, Zielona Góra, Nowy Targ, Gorzów Wielkopolski) and the exit
    # rotates by design, so they are reachable rather than theoretical — but
    # every city here was single-token until 2026-08-22, which is exactly why
    # the truncation defect below survived a round of review.
    for city in ("Warsaw", "Krakow", "Ursynów", "Gdansk",
                 "Nowy Sacz", "Zielona Gora", "Gorzów Wielkopolski"):
        reading = extract_text_item(checker, item, f"Check Geo API\n\nPoland / {city}\n")
        assert reading.state == READ, f"a Polish exit in {city} must still read"
        assert reading.value == f"Poland / {city}", (
            f"{city!r} must be captured WHOLE, as the item's note promises"
        )

    # The country is what is asserted: a non-Polish exit does NOT read.
    assert extract_text_item(
        checker, item, "Check Geo API\n\nGermany / Berlin\n").state == ABSENT


def test_the_naive_city_pattern_would_have_missed_the_rotated_exit():
    """Pins the BUG, so the fix cannot be reverted as a cosmetic loosening."""
    naive = TextItem("geo_poland", r"poland\s*/\s*warsaw", EXIT, adverse=False)
    checker = checker_by_id("pixelscan.net")
    # The clean page from the rotated exit — Poland, just not Warsaw.
    assert extract_text_item(checker, naive, "Poland / Krakow").state == ABSENT
    # ...while the corrected item reads it.
    fixed = next(i for i in checker.items if i.id == "geo_country_city")
    assert extract_text_item(checker, fixed, "Poland / Krakow").state == READ


def test_the_naive_word_pattern_would_have_TRUNCATED_a_multi_word_city():
    """DEFECT 1b — the same defect class as 1, one level down, and it survived
    a round of review because every city in the test loop was single-token.

    ``\\S+`` stops at the first space, so a two-word city was captured as its
    first token only. This fails in the QUIETER direction than the hardcoded
    city did: that one read ABSENT (loud — it looks like the checker stopped
    reporting Poland), this one reads READ with a silently CORRUPTED value.
    That is the worse outcome on a ``capture=True`` row, because this record
    exists precisely so a later run can tell "the verdict changed" from "the
    wording changed" — and ``Poland / Nowy`` -> ``Poland / Zielona`` reads as
    a genuine geo change when both are just multi-word cities.
    """
    checker = checker_by_id("pixelscan.net")
    naive = TextItem("geo_naive", r"(poland\s*/\s*\S+)", EXIT,
                     adverse=False, capture=True)
    fixed = next(i for i in checker.items if i.id == "geo_country_city")

    for city in ("Nowy Sacz", "Zielona Gora", "Gorzów Wielkopolski"):
        page = f"Check Geo API\n\nPoland / {city}\n"

        # The BUG: it still READS, which is why it is quiet — but the captured
        # value has silently lost everything after the first space.
        broken = extract_text_item(checker, naive, page)
        assert broken.state == READ
        assert broken.value == f"Poland / {city.split()[0]}"
        assert broken.value != f"Poland / {city}", (
            "this test must demonstrate the truncation, not agree with the fix"
        )

        # ...while the corrected item captures the city WHOLE.
        assert extract_text_item(checker, fixed, page).value == f"Poland / {city}"


def test_creepjs_best_possible_ratings_are_not_recorded_as_adverse():
    """DEFECT 2. The three rating items are CAPTURE items: matching means "the
    rating was PUBLISHED", not "the rating is bad". Tagged adverse=True, the
    clean measured page — 0% headless, 0% stealth, 6% like headless, the BEST
    readings CreepJS gives — recorded three ADVERSE MATCHES, so the run that
    proved the engine looks right reported it as three red flags."""
    for item_id, expected in (("headless_rating", "0"),
                              ("stealth_rating", "0"),
                              ("like_headless_rating", "6")):
        reading = reading_for("creepjs", item_id)
        assert reading.state == READ
        assert reading.value == expected
        assert reading.adverse is False, (
            f"{item_id} is a captured NUMBER — its polarity lives in the "
            f"value, and an adverse flag makes the best possible reading look "
            f"like a defect"
        )


def test_no_adverse_row_matched_on_the_clean_captured_pages():
    """The whole-catalogue statement of both defects above: reading the four
    REAL clean pages must produce NO adverse match at all. Either defect alone
    broke this, and neither showed up as a test failure."""
    readings = readings_from_texts({cid: {"text": page(cid)} for cid in PAGES})
    flagged = [(r.checker, r.item, r.value)
               for r in readings if r.adverse and r.state == READ]
    assert flagged == [], f"clean pages reported adverse matches: {flagged}"
