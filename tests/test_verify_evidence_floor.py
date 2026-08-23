"""A run that measured nothing must not report like a clean one — PS-110.

WHY EVERY TEST HERE INDUCES THE CONDITION
------------------------------------------
The ticket is explicit about the shape these tests must have: *"Demonstrate it
by inducing the condition, not by asserting a threshold was read from config."*
That instruction is doing real work, because the tempting test for an evidence
floor is to call ``assess`` with a hand-built list of two rows and assert it
says ``inconclusive``. That test passes on a tree where the floor is computed
correctly and never reaches the record, or reaches the record and never reaches
the exit code — i.e. on a tree with the defect still in it. It asserts on a
number this file chose.

So the crash tests below KILL THE BROWSER — through the same ``new_page()``
seam a real ``TargetClosedError`` comes through — and then assert on what the
REAL CLI wrote to a REAL file and what it returned to the shell. Knowledge
article PS-11 is about the alternative.

THE CONTROL IS A COMMITTED REAL RECORD, NOT A FIXTURE WRITTEN FOR THIS FILE
---------------------------------------------------------------------------
A floor is only half-tested by runs it refuses; the half that gets a floor
switched off is the healthy run it fires on. The control here is
``tests/fixtures/checker-matrix-reading.sandbox.json`` — the first real reading
ever taken on this project (PS-59), through the real Polish exit — and it is
the same artifact the floor's threshold was derived against. It carries 7 of 18
fingerprint rows from 2 checkers, which is a GOOD run on this matrix: 24 of 53
rows unobtainable is the designed steady state (see
``matrix_diff.coverage_lost``). If the floor ever fires on it, the floor is
wrong, and ``test_the_real_healthy_control_record_is_not_called_inconclusive``
is the test that says so.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from src.services.verify import browser_tier as bt
from src.services.verify import checker_cli as cli
from src.services.verify import evidence as ev
from src.services.verify.checkers import (
    BROWSER_CHECKERS,
    ENGINE_EXIT_CHECKER,
    FINGERPRINT,
)
from src.services.verify.exit_guard import Exit
from src.services.verify.matrix import READ, UNOBTAINABLE

# Resolved from THIS file, never the CWD — other tests chdir into tmp dirs.
_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
CONTROL_RECORD = _FIXTURES / "checker-matrix-reading.sandbox.json"
PAGES = _FIXTURES / "checker-pages"

_EXIT_JSON = (
    '{"ip": "83.6.13.226", "city": "Warsaw", "country": "PL", '
    '"org": "AS5617 Orange Polska", "timezone": "Europe/Warsaw"}'
)

# The real crash, verbatim from the PS-110 run. Two different exceptions, in
# the order they actually occurred: the renderer dies while a page is being
# READ, and the death is only discovered when the NEXT page is opened.
CRASH_ON_READ = "Page.inner_text: Target crashed"
CONTEXT_DEAD = (
    "BrowserContext.new_page: Target page, context or browser has been closed"
)


# --- a session that dies mid-run, driven through the real seams -------------


class _CrashingPage:
    def __init__(self, session):
        self._session = session
        self.closed = False

    def goto(self, url, timeout=0, wait_until="load"):
        self._session.visited.append(url)

    def inner_text(self, selector):
        url = self._session.visited[-1]
        if url == self._session.crash_url:
            # The renderer dies while this page is being read — exactly where
            # pixelscan killed it. The session is now unusable.
            self._session.dead = True
            raise RuntimeError(CRASH_ON_READ)
        # A checker with no page serves EMPTY text, which the reader records as
        # unobtainable. Serving one checker's page to another would be worse
        # than unrealistic: every adverse pattern would fail to match, each
        # miss would be recorded as ABSENT, and absent IS evidence — so the
        # fake would manufacture fingerprint evidence from checkers that never
        # answered and quietly lift the run over the floor under test. (It did:
        # the first draft of this file scored a crashed run at 10 rows from 4
        # checkers and called it sufficient.)
        return self._session.texts.get(url, "")

    def close(self):
        self.closed = True


class _CrashingSession:
    """A browser that dies on one checker and cannot make pages afterwards.

    The contract is the one both real engines satisfy — ``new_page()`` and a
    page with ``goto`` / ``inner_text`` / ``close`` — so this drives the SAME
    loop the real run drives, which is what makes the induced condition mean
    anything. ``new_page()`` raising after the death is the real
    ``TargetClosedError`` behaviour, not a stand-in for it.
    """

    def __init__(self, crash_url, texts):
        self.crash_url = crash_url
        self.texts = texts
        self.dead = False
        self.visited: "list[str]" = []
        self.pages: "list[_CrashingPage]" = []
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        if self.dead:
            raise RuntimeError(CONTEXT_DEAD)
        page = _CrashingPage(self)
        self.pages.append(page)
        return page


def _url_of(checker_id):
    return next(c for c in BROWSER_CHECKERS if c.id == checker_id).url


def _pages_after_a_crash(crash_id="iphey.com"):
    """Run the REAL loop against a session that dies on ``crash_id``.

    Shaped to reproduce the PS-110 run rather than merely to fail: the exit is
    proven, ONE checker answers with its real captured page, the next one kills
    the renderer, and everything sequenced after it is never asked. That leaves
    a handful of fingerprint rows from a single checker — the shape the ticket
    describes ("five of them the exit rows, and two ``bot.sannysoft.com``
    booleans").

    ``iphey.com`` is the crash point rather than pixelscan because the
    catalogue orders pixelscan second-to-last, so crashing there would leave
    only CreepJS behind it and would not exercise a cascade worth the name. The
    defect is about a browser dying MID-RUN; where in the run is incidental.
    """
    texts = {
        _url_of(ENGINE_EXIT_CHECKER.id): _EXIT_JSON,
        _url_of("bot.sannysoft.com"): (
            PAGES / "sannysoft.txt"
        ).read_text(encoding="utf-8"),
    }
    session = _CrashingSession(_url_of(crash_id), texts)
    pages = bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )
    return session, pages


# --- the cascade is contained -----------------------------------------------


def test_a_dead_session_stops_the_run_instead_of_being_asked_forty_more_times():
    """The loop must not keep calling new_page() on a context that cannot
    make pages. Measured on main: it called it once per remaining checker and
    wrote each identical failure as that checker's own reading."""
    session, pages = _pages_after_a_crash()

    # One call per checker actually attempted, plus the one that discovered
    # the death. Never one per catalogue entry.
    assert session.new_page_calls <= len(BROWSER_CHECKERS)
    assert session.dead, "the induced crash must really have killed the session"


def test_rows_lost_to_one_dead_browser_are_marked_never_asked():
    """THE LOAD-BEARING DISTINCTION. 'This checker could not answer' is a
    reading about that checker; 'nothing after this point was ever asked' is
    one fact about the run wearing the costume of many.

    Without this, 45 rows lost to a single crash read to a later comparison as
    45 independently moved vectors.
    """
    _session, pages = _pages_after_a_crash()
    readings = bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)

    never_asked = [r for r in readings if r.never_asked]
    assert never_asked, "the checkers after the crash were never asked"
    assert all(r.state == UNOBTAINABLE for r in never_asked)
    # They name the shared cause rather than each other's failures.
    assert all(CONTEXT_DEAD in r.reason for r in never_asked)
    assert all("NEVER ASKED" in r.reason for r in never_asked)


def test_the_checker_that_actually_crashed_is_not_marked_never_asked():
    """It WAS asked — it is the one that answered by killing the browser. Its
    row is a reading about that checker and must stay one, or the record loses
    the only pointer to what caused the cascade."""
    _session, pages = _pages_after_a_crash()
    readings = bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)

    crashed = [r for r in readings if r.checker == "iphey.com"]
    assert crashed, "the crashing checker must still occupy its full width"
    assert not any(r.never_asked for r in crashed)
    assert all(CRASH_ON_READ in r.reason for r in crashed)


def test_a_checker_that_merely_refuses_does_not_end_the_run():
    """The counterfactual that keeps the containment honest. A goto() failure
    is THAT CHECKER's answer and the run continues — ending the run on it
    would trade this defect for a worse one, where one refusing checker costs
    the whole matrix."""

    class _RefusingPage(_CrashingPage):
        def goto(self, url, timeout=0, wait_until="load"):
            self._session.visited.append(url)
            if url in self._session.refuse:
                raise RuntimeError("net::ERR_CONNECTION_REFUSED")

    class _RefusingSession(_CrashingSession):
        def new_page(self):
            self.new_page_calls += 1
            page = _RefusingPage(self)
            self.pages.append(page)
            return page

    target = next(c for c in BROWSER_CHECKERS if c.id != ENGINE_EXIT_CHECKER.id)
    session = _RefusingSession("", {_url_of(ENGINE_EXIT_CHECKER.id): _EXIT_JSON})
    session.refuse = {target.url}
    pages = bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )

    assert "error" in pages[target.id]
    assert not pages[target.id].get("never_asked")
    # Every other checker was still asked.
    assert set(pages) == {c.id for c in BROWSER_CHECKERS}
    readings = bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)
    assert not any(r.never_asked for r in readings)


# --- the count stops lying --------------------------------------------------


def test_the_reported_reading_count_does_not_grow_when_nothing_is_read():
    """"browser tier: 37 readings" over a run that obtained two fingerprint
    rows. Every unread checker contributes its full width by design, so the old
    figure GREW as the browser died.

    Asserted as a PROPERTY rather than against a golden string: the number
    reported for a dead run must not exceed the number reported for a healthy
    one over the same catalogue.
    """
    _session, crashed_pages = _pages_after_a_crash()
    crashed = bt.readings_from_texts(crashed_pages, checkers=BROWSER_CHECKERS)

    healthy_pages = {
        c.id: {"text": _EXIT_JSON} for c in BROWSER_CHECKERS
    }
    for cid, name in (
        ("creepjs", "creepjs.txt"),
        ("iphey.com", "iphey.txt"),
        ("pixelscan.net", "pixelscan.txt"),
        ("bot.sannysoft.com", "sannysoft.txt"),
    ):
        healthy_pages[cid] = {"text": (PAGES / name).read_text(encoding="utf-8")}
    healthy = bt.readings_from_texts(healthy_pages, checkers=BROWSER_CHECKERS)

    # Same row count — the record keeps its full width either way, which is
    # precisely why a row count could never separate these two runs.
    assert len(crashed) == len(healthy)

    crashed_read = sum(1 for r in crashed if r.state == READ)
    healthy_read = sum(1 for r in healthy if r.state == READ)
    assert crashed_read < healthy_read

    # And what the CLI prints is the read figure, not the row figure.
    line = cli._tally(crashed)
    assert line.startswith(f"{crashed_read} read")
    assert str(len(crashed)) in line  # rows still reported, just not summed in
    assert not line.startswith(f"{len(crashed)} ")


# --- the floor, at both ends, against real records ---------------------------


def test_the_real_healthy_control_record_is_not_called_inconclusive():
    """THE FLOOR'S UPPER CONSTRAINT, and the test that would catch a floor set
    too high. This is the first real reading ever taken on this project
    (PS-59), through the real exit — 7 of 18 fingerprint rows from 2 checkers.

    24 of 53 rows unobtainable is this matrix's DESIGNED STEADY STATE, so this
    is a good run. A floor that fires on it is a floor that will be switched
    off within a week.
    """
    record = json.loads(CONTROL_RECORD.read_text(encoding="utf-8"))
    verdict = ev.assess(record["readings"])

    assert verdict["verdict"] == ev.SUFFICIENT
    assert not ev.is_inconclusive(verdict)
    assert verdict["reasons"] == []


def test_the_control_and_a_crashed_run_do_not_report_the_same_way():
    """The ticket's acceptance condition, stated as the comparison it is:
    *"what is not acceptable is a run with two fingerprint rows reporting the
    same way as one with twenty-six."*"""
    control = json.loads(CONTROL_RECORD.read_text(encoding="utf-8"))
    _session, pages = _pages_after_a_crash()
    crashed_rows = [
        r.as_record()
        for r in bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)
    ]

    good = ev.assess(control["readings"])
    bad = ev.assess(crashed_rows)

    assert good["verdict"] != bad["verdict"]
    assert bad["verdict"] == ev.INCONCLUSIVE
    assert bad["cause"] == ev.SESSION_DIED


def test_the_exit_rows_alone_cannot_clear_the_floor():
    """The technical note's warning, made into a test. The engine-exit rows are
    proven BEFORE the browser tier runs, so they survive a dead browser — a
    floor over all READ rows would have scored the PS-110 run at seven and
    cleared. The floor is over rows that carry fingerprint evidence."""
    _session, pages = _pages_after_a_crash()
    readings = bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)

    exit_rows = [r for r in readings if r.checker == ENGINE_EXIT_CHECKER.id]
    assert any(r.state == READ for r in exit_rows), (
        "the exit rows really do survive the crash — that is the trap"
    )
    verdict = ev.assess([r.as_record() for r in readings])
    assert verdict["verdict"] == ev.INCONCLUSIVE


def test_rows_from_one_checker_are_not_counted_as_independent_evidence():
    """CreepJS alone is 9 of 27 fingerprint rows (33%), so a fraction-only
    floor is cleared by ONE page load answering. Rows within a checker are
    perfectly correlated — one page load yields all of them or none."""
    creep = [
        {"checker": "creepjs", "item": f"i{n}", "state": "read", "sort": FINGERPRINT}
        for n in range(9)
    ]
    others = [
        {"checker": "x", "item": f"i{n}", "state": "unobtainable", "sort": FINGERPRINT}
        for n in range(18)
    ]
    verdict = ev.assess(creep + others)

    assert verdict["fingerprint_fraction"] >= ev.DEFAULT_FLOOR["fraction"], (
        "this input must clear the FRACTION term, or it tests nothing"
    )
    assert verdict["verdict"] == ev.INCONCLUSIVE
    assert verdict["checkers_contributing"] == ["creepjs"]


def test_an_absent_reading_is_evidence_and_a_clean_run_clears_the_floor():
    """``absent`` is the checker answering and NOT saying this — for an adverse
    item that is the good news. Folding it in with unobtainable would make a
    perfectly clean run fail this floor, which is the one outcome that would
    get the floor removed."""
    rows = [
        {"checker": f"c{n % 3}", "item": f"i{n}", "state": "absent",
         "sort": FINGERPRINT}
        for n in range(27)
    ]
    verdict = ev.assess(rows)

    assert verdict["verdict"] == ev.SUFFICIENT
    assert verdict["fingerprint_obtained"] == 27


# --- the record and the exit code both report it ----------------------------


def _run_cli(tmp_path, monkeypatch, pages):
    """Drive the REAL `read` command end to end, browser tier included."""
    monkeypatch.setattr(
        cli, "prove_exit",
        lambda **kw: (
            "socks5h://u:p@host:1080",
            Exit(ip="83.6.13.226", country="PL", city="Warsaw",
                 org="AS5617 Orange Polska", timezone="Europe/Warsaw"),
        ),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    monkeypatch.setattr(
        bt, "read_page_texts", lambda *a, **k: pages
    )
    target = tmp_path / "reading.json"
    rc = cli.main(["read", "-o", str(target)])
    return rc, json.loads(target.read_text(encoding="utf-8"))


def test_a_crashed_run_reports_it_in_the_record_AND_the_exit_code(
    tmp_path, monkeypatch
):
    """THE TICKET'S DEMONSTRATION. Kill the browser mid-run and show that both
    the record and the exit code say so — rather than exit 0 and a file that
    reads like a clean one.

    The record carries it too, not only the exit code, because the record
    outlives the terminal: a later diff reads the file, not the shell.
    """
    _session, pages = _pages_after_a_crash()
    rc, record = _run_cli(tmp_path, monkeypatch, pages)

    assert rc == 3, "a run that gathered nothing must not exit 0"

    ev_block = record["evidence"]
    assert ev_block["verdict"] == ev.INCONCLUSIVE
    assert ev_block["cause"] == ev.SESSION_DIED
    assert ev_block["never_asked"] > 0
    # Re-derivable rather than merely asserted: the numbers behind the verdict
    # are in the record, so a reader is never asked to trust the word.
    assert ev_block["fingerprint_obtained"] < ev_block["fingerprint_total"]
    assert ev_block["floor"] == ev.DEFAULT_FLOOR
    assert ev_block["reasons"]


def test_a_healthy_run_still_exits_zero_and_says_sufficient(
    tmp_path, monkeypatch
):
    """The other direction, and the one that keeps this from being a floor that
    fails everything. Same command, same seams, pages that answered."""
    pages = {c.id: {"text": _EXIT_JSON} for c in BROWSER_CHECKERS}
    for cid, name in (
        ("creepjs", "creepjs.txt"),
        ("iphey.com", "iphey.txt"),
        ("pixelscan.net", "pixelscan.txt"),
        ("bot.sannysoft.com", "sannysoft.txt"),
    ):
        pages[cid] = {"text": (PAGES / name).read_text(encoding="utf-8")}

    rc, record = _run_cli(tmp_path, monkeypatch, pages)

    assert rc == 0
    assert record["evidence"]["verdict"] == ev.SUFFICIENT
    assert record["evidence"]["cause"] == ""
    assert record["evidence"]["never_asked"] == 0


def test_the_written_record_is_what_carries_the_verdict_not_just_stderr(
    tmp_path, monkeypatch
):
    """A human watching the console is one of three consumers, and the other
    two read the file. The verdict must survive being written and re-read."""
    _session, pages = _pages_after_a_crash()
    target = tmp_path / "reading.json"

    monkeypatch.setattr(
        cli, "prove_exit",
        lambda **kw: (
            "socks5h://u:p@host:1080",
            Exit(ip="83.6.13.226", country="PL", city="Warsaw",
                 org="AS5617 Orange Polska", timezone="Europe/Warsaw"),
        ),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *a, **k: [])
    monkeypatch.setattr(bt, "read_page_texts", lambda *a, **k: pages)
    cli.main(["read", "-o", str(target)])

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert ev.is_inconclusive(on_disk["evidence"])
    # And the never-asked rows survive the round trip, so a comparator can
    # attribute them to one cause rather than to 45 moved vectors.
    unasked = ev.never_asked_rows(on_disk["readings"])
    assert unasked
    assert all(r["state"] == UNOBTAINABLE for r in unasked)


def test_a_record_with_no_evidence_block_reads_as_inconclusive_never_as_fine():
    """Every record written before generation 4 lacks the block. The direction
    of that error is the whole point: the failure being guarded against is a
    run that could not say it measured nothing, so silence must never resolve
    to 'fine'."""
    old = json.loads(CONTROL_RECORD.read_text(encoding="utf-8"))
    assert "evidence" not in old

    assert ev.is_inconclusive(old.get("evidence"))
    assert ev.is_inconclusive(None)
    assert ev.is_inconclusive({"verdict": "something else"})
    assert ev.is_inconclusive("sufficient")  # not a mapping


# --- the floor is one definition, shared with `compare` ----------------------


def test_read_and_compare_cannot_disagree_about_what_a_reading_is():
    """PS-92 owns the same floor over ``compare``. Two copies of "what counts
    as evidence" is how the two lanes come to disagree — at which point a
    record ``read`` called inconclusive gets compared as though it were
    evidence.

    Asserted BEHAVIOURALLY, over the states that matter, rather than by
    inspecting how the delegation is wired: an identity check on a function
    object passes on a tree where the two agree by accident and fails on a
    correct refactor, which is the wrong sensitivity in both directions. What
    must hold is that the two answers are the same answer.
    """
    from src.services.verify import matrix_diff

    for row, expected in (
        ({"state": "read"}, True),
        ({"state": "absent"}, True),
        ({"state": "unobtainable"}, False),
        ({"state": "unobtainable", "never_asked": True}, False),
        ({}, False),
        (None, False),
    ):
        assert matrix_diff._obtained(row) is expected
        assert ev.obtained(row) is expected
