"""PS-282 — a log call site DECLARES its event's severity.

WHAT THESE TESTS ARE. Every one of them DRIVES a consumer and reads what the
consumer produced: the row's dot colour off the built control, the collapsed
dock's pulse colour off the built widget, and the fullscreen view's severity
FILTER by calling the real filter predicate over a real ring. None of them
asserts that a sink "was called with" a declaration — the ticket's own standing
directive is that the behaviour is verified by observing it, and "the sink
received the right argument" is exactly the check that passes while the dot on
screen stays green.

WHAT IS NOT COVERED, AND WHY IT IS SAID RATHER THAN PAPERED OVER. These build
real flet controls in-process and read their properties; they do not render
pixels, and no assertion here is a claim about what a human eye sees. The
project's live-driver tier (``tests/ui_driver/``) is where a rendered surface is
driven, and it needs a served UI plus a chromium — the ``ui_driver`` capability,
which this container does not supply. The properties asserted here (``bgcolor``
on the dot and the pulse, membership in the filter's output) are the values the
renderer is handed, which is the strongest claim available without that tier.
"""

from __future__ import annotations

import flet as ft
import pytest

from src.core.strings import CHROMIUM_ENGINE_NAME

from src.ui.components.log_dock import LogDock
from src.ui.log_console import (
    SEV_COLOR,
    SEV_FAIL,
    SEV_IDLE,
    SEV_INFO,
    SEV_OK,
    DeclaredMessage,
    declare,
    declared_severity,
    event_row,
    event_severity,
    parse_event,
    severity,
)
from src.ui.state import AppState

ROSTER = frozenset({"acme-bank", "shop-de-03", "mail-us-011"})


def _state(monkeypatch, tmp_path) -> AppState:
    """An AppState whose ring starts EMPTY — no seed from a real log dir."""
    import src.ui.state as state_mod

    monkeypatch.setattr(state_mod, "LOG_DIR", str(tmp_path / "no-logs"))
    return AppState()


def _log_and_arm(st: AppState, message) -> None:
    """Log, and make the flush PENDING regardless of wall-clock timing.

    `add_log` only arms a flush when the message is urgent or ~0.15s has passed
    since the last one, so a test that logs twice quickly gets `None` back from
    a flush and passes or fails on timing rather than on behaviour. Resetting
    the rate-limit clock is what makes these assertions about the CHANNEL.
    """
    st._last_log_ui_update = 0.0
    st.add_log(message)


def _dot_colour(row: ft.Control) -> str:
    """The colour of the severity dot the row actually built.

    Read out of the control tree rather than recomputed, so this fails if the
    row stops routing the declaration to the mark the operator looks at.
    """
    found = []

    def walk(c):
        if isinstance(c, ft.Container) and c.width == 7 and c.height == 7:
            found.append(c.bgcolor)
        for attr in ("content", "controls"):
            child = getattr(c, attr, None)
            if isinstance(child, list):
                for x in child:
                    walk(x)
            elif child is not None:
                walk(child)

    walk(row)
    assert len(found) == 1, f"expected exactly one 7x7 dot, found {found}"
    return found[0]


def _dock(roster) -> LogDock:
    d = LogDock(window_height=680)
    d.set_profiles(roster)
    return d


# ---------------------------------------------------------------------------
# 1. The declaration itself
# ---------------------------------------------------------------------------


def test_a_declared_message_is_still_the_same_string():
    """The declaration must cost NOTHING at every site that only wants text.

    This is what makes the migration incremental: `logger.info`, the persistent
    file log, `"".join`, `.lower()`, `==` and every existing test fake keep
    working on a declared message because it IS the string, byte for byte.
    """
    msg = declare("Could not delete acme-bank: its data could not be moved.", SEV_FAIL)
    assert msg == "Could not delete acme-bank: its data could not be moved."
    assert isinstance(msg, str)
    assert str(msg) == msg
    assert msg.lower().startswith("could not delete")
    assert f"[{msg}]".startswith("[Could not delete")


def test_nothing_is_encoded_in_the_text_so_no_renderer_can_leak_it():
    """The severity rides on an ATTRIBUTE, never on the message text.

    An in-band marker would have to be stripped correctly by four renderers AND
    by the fullscreen search box; one that was not would be printed to the
    operator. Asserting the text is untouched is what makes that whole class of
    defect unreachable rather than merely unobserved.
    """
    plain = "Browser engine not ready yet — wait for the download to finish."
    assert declare(plain, SEV_FAIL) == plain
    assert repr(str(declare(plain, SEV_FAIL))) == repr(plain)
    for token in (SEV_FAIL, SEV_OK, SEV_INFO, SEV_IDLE, "sev", "::"):
        assert token not in str(declare(plain, SEV_FAIL)).replace(plain, "")


def test_an_unknown_severity_is_refused_rather_than_silently_ignored():
    """A typo must not quietly revert a site to prose-matching.

    That failure would be invisible — the line keeps working, keeps rendering,
    and keeps being misclassified — which is the defect this module exists to
    close wearing the declaration as a costume.
    """
    import pytest

    with pytest.raises(ValueError, match="unknown severity"):
        declare("anything", "critical")


def test_a_plain_string_declares_nothing_and_that_means_ask_the_classifier():
    """`None`, never `idle` — the fallback must be reached, not defaulted past."""
    assert declared_severity("Browser started!") is None
    assert declared_severity(DeclaredMessage("x", SEV_OK)) == SEV_OK
    # event_severity falls back to the SAME answer severity() gives.
    assert event_severity("Browser started!", "Browser started!") == severity(
        "Browser started!"
    )


def test_an_fstring_around_a_declaration_declares_nothing():
    """Documented, deliberate degradation — an f-string is a NEW message.

    Its author is the one who gets to say what it means, so a wrapped
    declaration falling back to prose-matching is correct rather than lossy.
    Pinned so the behaviour is a decision on the record, not an accident.
    """
    inner = declare("Browser engine not ready yet.", SEV_FAIL)
    assert declared_severity(f"[chromium] {inner}") is None


# ---------------------------------------------------------------------------
# 2. AC1 — the declaration reaches the DOT
# ---------------------------------------------------------------------------


def test_the_launch_refusal_paints_a_failure_dot_instead_of_the_green_one():
    """THE MEASURED DEFECT: "ready" is a substring of "not ready yet".

    Both engine-not-ready refusals painted the green SUCCESS dot — the product
    telling an operator the launch worked, on the ordinary
    clicked-before-the-download-finished path. Driven end to end: the real
    action logs into a real ring, and the dot is read off the built row.
    """
    from src.ui.actions import browser as browser_actions

    for engine_kind in ("chromium", "firefox"):
        lines: list[str] = []
        if engine_kind == "firefox":
            from src.services.browser import invisible_launch as il

            _saved = il.is_invisible_installed
            il.is_invisible_installed = lambda: False
        else:
            _saved_engine = browser_actions.engine.is_installed
            browser_actions.engine.is_installed = lambda: False

        class _P:
            name = "acme-bank"
            engine = engine_kind
            os_type = "windows"

        class _PM:
            def __init__(self):
                self.profiles = {"acme-bank": _P()}

        class _BL:
            started = False

            def is_running(self, name):
                return False

            def start_thread(self, *a, **k):
                self.started = True

        try:
            st = AppState.__new__(AppState)
            AppState.__init__(st)
            st._log_lines.clear()
            browser_actions.launch_or_stop(
                "acme-bank", _PM(), _BL(), st, lines.append
            )
        finally:
            if engine_kind == "firefox":
                il.is_invisible_installed = _saved
            else:
                browser_actions.engine.is_installed = _saved_engine

        assert len(lines) == 1, lines
        message = lines[0]
        # The PROSE still classifies green — the wording was deliberately not
        # changed, so this is the premise of the defect, still true.
        assert severity(str(message)) == SEV_OK
        # ...and the DECLARATION overrides it, all the way to the dot.
        stored = f"10:00:01  > {message}"
        stored = DeclaredMessage(stored, declared_severity(message))
        assert _dot_colour(event_row(stored, ROSTER)) == SEV_COLOR[SEV_FAIL]


def test_the_failed_delete_paints_a_failure_dot_instead_of_the_dimmest_one():
    """A destructive operation that did NOT happen got SEV_IDLE — the dimmest
    mark in the console, because the line carries none of the twenty tokens."""
    from src.core.strings import get_string
    from src.ui.actions.profile import delete_profile

    lines: list[str] = []

    class _PM:
        def delete_profile(self, name):
            return False  # the data dir could not be parked

    captured = {}

    def _fake_confirm(page, name, on_confirm):
        captured["go"] = on_confirm

    from src.ui.actions import profile as profile_actions

    saved = profile_actions.open_confirm_dialog
    profile_actions.open_confirm_dialog = _fake_confirm
    try:
        delete_profile(None, "acme-bank", _PM(), lines.append, lambda: None)
        captured["go"]()
    finally:
        profile_actions.open_confirm_dialog = saved

    assert lines == [get_string("delete_profile_failed", name="acme-bank")]
    assert severity(str(lines[0])) == SEV_IDLE  # the premise, still true
    stored = DeclaredMessage(f"10:00:02  > {lines[0]}", declared_severity(lines[0]))
    assert _dot_colour(event_row(stored, ROSTER)) == SEV_COLOR[SEV_FAIL]


def test_the_bulk_delete_failure_declares_too_and_the_success_does_not():
    """Both halves of the bulk path, because only the FAILURE half declares.

    A blanket declaration on the whole line would have made a successful delete
    a failure — this pins that the conversion is per-outcome.
    """
    from src.ui.actions import bulk as bulk_actions
    from src.ui.actions.bulk import bulk_delete_profiles

    outcomes = {"gone": True, "stuck": False}
    lines: list[str] = []
    captured = {}

    class _PM:
        def delete_profile(self, name):
            return outcomes[name]

    def _fake_confirm(page, names, on_confirm, **kw):
        captured["go"] = on_confirm

    saved = bulk_actions.open_confirm_dialog
    bulk_actions.open_confirm_dialog = _fake_confirm
    try:
        bulk_delete_profiles(
            None,
            ["gone", "stuck"],
            _PM(),
            lines.append,
            lambda: None,
            lambda: None,
            ui=lambda fn: fn(),
        )
        captured["go"]()
        import time

        time.sleep(0.4)
    finally:
        bulk_actions.open_confirm_dialog = saved

    by_name = {("stuck" if "stuck" in m else "gone"): m for m in lines}
    assert declared_severity(by_name["stuck"]) == SEV_FAIL
    assert declared_severity(by_name["gone"]) is None


def test_the_interpolated_exception_no_longer_decides_the_colour():
    """ONE authored line whose severity was decided by TEXT ITS AUTHOR NEVER WROTE.

    `app.py` logs `f"...couldn't read the build record ({e})"`. The classifier
    sees the whole rendered line, so whatever `str(e)` happens to contain votes:
    an exception whose message says "error" classifies `fail`, and an equally
    fatal one whose message is a bare key name classifies `idle`. Same code
    path, same operator situation, two different dots and two different answers
    to the `failures` filter.

    ⚠️ A CORRECTION TO THE TICKET'S OWN EXAMPLE, MEASURED HERE. PS-282 cites the
    pair as `OSError` → fail / `KeyboardInterrupt` → idle. That reproduces only
    when the exception CLASS is interpolated (`<class 'OSError'>` contains
    "error"); with INSTANCES, which is what `except Exception as e` binds and
    what this call site actually formats, BOTH classify `idle` —
    `str(OSError("disk full"))` is "disk full" and carries no token. The defect
    is real and is worse than stated: it is not the class that decides, it is
    whatever prose the raising code put in the message. The pairs below are
    measured against the shipped classifier rather than taken from the ticket.
    """
    def _raw(exc):
        return (
            f"{CHROMIUM_ENGINE_NAME} engine: couldn't read the build "
            f"record ({exc})"
        )

    # THE PREMISE, measured. Two ways the SAME read fails, classified opposite.
    assert severity(_raw(ValueError("build record is missing a version field"))) == (
        SEV_FAIL
    )
    assert severity(_raw(KeyError("version"))) == SEV_IDLE
    assert severity(_raw(OSError("disk full"))) == SEV_IDLE
    assert severity(_raw(FileNotFoundError(2, "No such file", "/x/build.json"))) == (
        SEV_IDLE
    )

    # ...and the declaration settles every one of them the same way, at the dot.
    for exc in (
        ValueError("build record is missing a version field"),
        KeyError("version"),
        OSError("disk full"),
        FileNotFoundError(2, "No such file", "/x/build.json"),
        KeyboardInterrupt(),
    ):
        stored = DeclaredMessage(f"10:00:03  > {declare(_raw(exc), SEV_FAIL)}", SEV_FAIL)
        assert _dot_colour(event_row(stored, ROSTER)) == SEV_COLOR[SEV_FAIL], exc
        assert _filter_to([stored], SEV_FAIL) == [stored], exc


def test_the_two_lines_the_code_CALLS_SIBLINGS_now_classify_the_same():
    """`app.py:770-778`'s own comment calls the update-hold read failure the
    "Same lesson as the Chromium row" — and prose-matching told them apart: the
    update line carries "update" (`info`), its named sibling carries "error"
    only when the interpolated text happens to. Both declare `fail` now."""
    hold = "Update: couldn't read the update-hold state"
    rollback = (
        f"{CHROMIUM_ENGINE_NAME} engine: couldn't read the rollback state "
        "(disk full)"
    )
    assert severity(hold) == SEV_INFO  # the premise
    assert severity(rollback) == SEV_IDLE  # ...and it disagreed
    for raw in (hold, rollback):
        stored = DeclaredMessage(f"10:00:03  > {declare(raw, SEV_FAIL)}", SEV_FAIL)
        assert _dot_colour(event_row(stored, ROSTER)) == SEV_COLOR[SEV_FAIL], raw


# ---------------------------------------------------------------------------
# 3. AC1 — the declaration reaches the collapsed PULSE
# ---------------------------------------------------------------------------


def test_the_collapsed_pulse_shows_the_declared_severity():
    """Collapsed is the one state where the pulse is the ONLY signal the
    operator gets, so a declaration that stopped at the row would be worse than
    none — it would make the two disagree, which PS-179 blocked a PR over."""
    line = DeclaredMessage(
        "10:00:04  > Browser engine not ready yet — wait for the download to finish.",
        SEV_FAIL,
    )
    d = _dock(ROSTER)
    d.render([line], seq=1)
    assert d._pulse.bgcolor == SEV_COLOR[SEV_FAIL]


def test_the_pulse_and_the_row_agree_on_every_shape_declared_or_not():
    """PS-179's equality, re-run across BOTH populations at once.

    Sampling cannot catch a row/pulse disagreement — the defect lived only in
    the ANCHORED clause — so the two derivations are diffed over every input
    shape, declared and undeclared, exactly as `b4d3186a` prescribes.
    """
    undeclared = [
        "10:00:04  > Session ended: mail-us-011",  # the anchored rule
        "10:00:05  > shop-de-03: LAUNCH_FAILED: engine firefox-142 missing",
        "10:00:06  > Launching shop-de-03",
        "10:00:07  > Browser started for shop-de-03",
        "10:00:08  > Engine update available",
    ]
    cases = list(undeclared)
    # The SAME shapes, each declaring each of the four values — so a
    # declaration that reached one derivation and not the other fails here on
    # every shape rather than on one lucky sample.
    for line in undeclared:
        for sev in (SEV_FAIL, SEV_OK, SEV_INFO, SEV_IDLE):
            cases.append(DeclaredMessage(line, sev))

    for line in cases:
        d = _dock(ROSTER)
        d.render([line], seq=1)
        row_sev = parse_event(line, ROSTER)[3]
        assert d._pulse.bgcolor == SEV_COLOR[row_sev], (
            f"pulse disagrees with its own row for {str(line)!r} "
            f"(declared={declared_severity(line)})"
        )


# ---------------------------------------------------------------------------
# 4. AC2 — the declaration reaches the FILTER (the consequence with no cover)
# ---------------------------------------------------------------------------


def _filter_to(lines, sev, roster=ROSTER):
    """The fullscreen view's own filter predicate, verbatim.

    Copied from `dialogs/log.py::matching` rather than imported, because that
    closure is built inside `open_log_dialog` and needs a live flet Page. The
    predicate is three lines and is asserted to still MATCH its source by
    `test_the_filter_predicate_under_test_is_the_shipped_one` below, so this
    cannot drift into testing a private reimplementation.
    """
    out = []
    for line in lines:
        _stamp, _prof, _msg, line_sev = parse_event(line, roster)
        if sev != "all" and line_sev != sev:
            continue
        out.append(line)
    return out


def test_the_filter_predicate_under_test_is_the_shipped_one():
    """Pins the copy above against the real source, so a change to the shipped
    filter breaks this file rather than silently outdating it."""
    import inspect

    from src.ui.dialogs import log as log_dialog

    src = inspect.getsource(log_dialog.open_log_dialog)
    assert "_stamp, prof, msg, sev = parse_event(line, profiles)" in src
    assert 'if state["sev"] != "all" and sev != state["sev"]:' in src


def test_a_declared_failure_is_RETURNED_when_the_operator_filters_to_failures(
    monkeypatch, tmp_path
):
    """AC2 — THE PRIMARY CONSEQUENCE, and the one that had no coverage.

    A misclassified failure is not merely a wrong-coloured dot: the fullscreen
    Activity Log FILTERS by severity, so a failed profile delete classified
    `idle` was ABSENT from the list for an operator who explicitly asked what
    went wrong. Driven through the REAL ring: the real string is logged through
    `add_log`, and the filter is run over what the ring actually holds.
    """
    from src.core.strings import get_string

    st = _state(monkeypatch, tmp_path)
    failure = get_string("delete_profile_failed", name="acme-bank")
    st.add_log("Launching shop-de-03")
    st.add_log(declare(failure, SEV_FAIL))
    st.add_log("Browser started!")

    lines = st.get_all_log_lines()
    failures = _filter_to(lines, SEV_FAIL)

    assert len(failures) == 1, failures
    assert failure in failures[0]

    # And the premise: WITHOUT the declaration the same line is dropped.
    st2 = _state(monkeypatch, tmp_path)
    st2.add_log(failure)
    assert _filter_to(st2.get_all_log_lines(), SEV_FAIL) == []


def test_a_declared_line_leaves_the_bucket_the_prose_would_have_put_it_in(
    monkeypatch, tmp_path
):
    """A declaration MOVES a line, it does not add it to two buckets.

    The launch refusal used to be returned under `ok`. If it were now returned
    under both `ok` and `fail` the filter would be lying in a new direction.
    """
    st = _state(monkeypatch, tmp_path)
    refusal = "Browser engine not ready yet — wait for the download to finish."
    st.add_log(declare(refusal, SEV_FAIL))

    lines = st.get_all_log_lines()
    assert len(_filter_to(lines, SEV_FAIL)) == 1
    assert _filter_to(lines, SEV_OK) == []
    assert len(_filter_to(lines, "all")) == 1


def test_the_filter_still_classifies_undeclared_lines_by_prose(
    monkeypatch, tmp_path
):
    """The fallback is reached from the filter path too, not only from the row."""
    st = _state(monkeypatch, tmp_path)
    st.add_log("LAUNCH_FAILED: engine firefox-142 missing")
    st.add_log("Engine update available")
    st.add_log("Browser started!")

    lines = st.get_all_log_lines()
    assert len(_filter_to(lines, SEV_FAIL)) == 1
    assert len(_filter_to(lines, SEV_INFO)) == 1
    assert len(_filter_to(lines, SEV_OK)) == 1


# ---------------------------------------------------------------------------
# 5. The channel — the ring, the flush, and the file seed
# ---------------------------------------------------------------------------


def test_the_ring_preserves_a_declaration_through_the_stored_line(
    monkeypatch, tmp_path
):
    """`add_log` formats `f"{stamp}  > {message}"`, which FLATTENS a str
    subclass. The wrapper is re-applied to the formatted line on purpose; this
    is what pins that it still is."""
    st = _state(monkeypatch, tmp_path)
    st.add_log(declare("Could not delete acme-bank.", SEV_FAIL))
    stored = st.get_all_log_lines()[0]
    assert declared_severity(stored) == SEV_FAIL
    assert stored.endswith("  > Could not delete acme-bank.")


def test_the_stored_line_is_still_an_ordinary_string_for_every_other_consumer(
    monkeypatch, tmp_path
):
    """`clear_log`, `log_seq`, the wire format and the fullscreen SEARCH all
    treat ring lines as text, and none may notice the difference."""
    st = _state(monkeypatch, tmp_path)
    _log_and_arm(st, declare("Could not delete acme-bank.", SEV_FAIL))
    _log_and_arm(st, "Browser started!")

    text = st.flush_log()
    assert text is not None
    assert text.count("\n") == 1
    assert "Could not delete acme-bank." in text
    # the fullscreen search matches the message, and nothing else
    assert "fail" not in text.lower().replace("could not delete acme-bank.", "")
    assert st.log_seq() == 2
    st.clear_log()
    assert st.get_all_log_lines() == []


def test_flush_log_lines_keeps_the_declaration_that_the_joined_form_destroys(
    monkeypatch, tmp_path
):
    """The console path takes LINES, not text, and this is why.

    `"\\n".join(...)` then `.split("\\n")` yields plain `str` — silently
    dropping every declaration precisely on the path to the console that
    renders it. Both halves are asserted so the reason for the extra method is
    on the record and cannot be "simplified" away.
    """
    st = _state(monkeypatch, tmp_path)
    _log_and_arm(st, declare("Could not delete acme-bank.", SEV_FAIL))

    lines = st.flush_log_lines()
    assert lines is not None
    assert declared_severity(lines[0]) == SEV_FAIL

    _log_and_arm(st, declare("Could not delete acme-bank.", SEV_FAIL))
    joined = st.flush_log()
    assert joined is not None
    assert declared_severity(joined.split("\n")[0]) is None  # the trap, pinned


def test_flush_log_lines_still_distinguishes_nothing_pending_from_an_empty_ring(
    monkeypatch, tmp_path
):
    """`None` != `[]`. An empty ring with a pending flush is the panic WIPE, and
    collapsing the two would leave wiped profile names painted on screen."""
    st = _state(monkeypatch, tmp_path)
    assert st.flush_log_lines() is None

    _log_and_arm(st, "Browser started!")
    assert st.flush_log_lines() is not None
    assert st.flush_log_lines() is None  # consumed

    st.clear_log()
    assert st.flush_log_lines() == []


def test_the_two_flush_forms_are_one_flush_not_two(monkeypatch, tmp_path):
    """They consume the same pending flag, so a caller cannot take a flush
    twice and paint the same arrivals into the console two times."""
    st = _state(monkeypatch, tmp_path)
    _log_and_arm(st, "Browser started!")
    assert st.flush_log_lines() is not None
    assert st.flush_log() is None


def test_a_line_seeded_from_a_PREVIOUS_SESSIONS_LOG_FILE_still_classifies_by_prose(
    tmp_path, monkeypatch
):
    """AC4 — the case a structural ring most easily breaks.

    `_load_recent_log_lines` fills the ring at STARTUP from the persistent
    `persona_*.log` file, re-parsing text a PREVIOUS PROCESS wrote. Those lines
    can never carry a declaration, whatever the sink signature becomes — which
    is the second, independent reason `severity()` is permanent rather than
    transitional. Driven with real files through the real seed, and the verdict
    read off the real row's dot.
    """
    import src.ui.state as state_mod
    from src.core.logging import SESSION_MARKER

    log_dir = tmp_path / "persona_logs"
    log_dir.mkdir(parents=True)
    (log_dir / "persona_20260903.log").write_text(
        "\n".join(
            [
                f"2026-09-03 09:00:00 - INFO - persona - {SESSION_MARKER} 3.0.2 ====",
                "2026-09-03 09:00:01 - ERROR - persona.api - LAUNCH_FAILED: engine missing",
                "2026-09-03 09:00:02 - INFO - persona.api - Browser started!",
                "2026-09-03 09:00:03 - INFO - persona.api - Engine update available",
                "2026-09-03 09:00:04 - INFO - persona.api - Trashed bookmark: old-jar",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(state_mod, "LOG_DIR", str(log_dir))

    st = state_mod.AppState()
    lines = st.get_all_log_lines()
    seeded = {ln.split("  > ", 1)[1]: ln for ln in lines if "  > " in ln}

    expected = {
        "LAUNCH_FAILED: engine missing": SEV_FAIL,
        "Browser started!": SEV_OK,
        "Engine update available": SEV_INFO,
        "Trashed bookmark: old-jar": SEV_IDLE,
    }
    for msg, want in expected.items():
        line = seeded[msg]
        assert declared_severity(line) is None, "a file line cannot declare"
        assert parse_event(line, ROSTER)[3] == want
        # ...and it reaches the DOT unchanged, which is the operator-visible
        # claim rather than a claim about the classifier.
        assert _dot_colour(event_row(line, ROSTER)) == SEV_COLOR[want]

    # The filter sees them too, by prose, exactly as before. NOTE the session
    # marker line ("persona session started 3.0.2") is itself seeded and itself
    # classifies `ok` — counted here rather than filtered out, because it is a
    # real line the operator really sees.
    assert len(_filter_to(lines, SEV_FAIL)) == 1
    assert len(_filter_to(lines, SEV_OK)) == 2


def test_a_declared_line_and_a_seeded_line_coexist_in_one_ring(
    tmp_path, monkeypatch
):
    """The two populations are in the SAME ring at the same time — this is the
    real steady state after a restart, and each keeps its own source of truth."""
    import src.ui.state as state_mod
    from src.core.logging import SESSION_MARKER

    log_dir = tmp_path / "persona_logs"
    log_dir.mkdir(parents=True)
    (log_dir / "persona_20260903.log").write_text(
        f"2026-09-03 09:00:00 - INFO - persona - {SESSION_MARKER} 3.0.2 ====\n"
        "2026-09-03 09:00:01 - INFO - persona.api - Browser started!\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(state_mod, "LOG_DIR", str(log_dir))

    st = state_mod.AppState()
    st.add_log(declare("Could not delete acme-bank.", SEV_FAIL))

    lines = st.get_all_log_lines()
    # Two seeded `ok` lines: "Browser started!" and the session marker itself.
    assert len(_filter_to(lines, SEV_OK)) == 2  # seeded, by prose
    assert len(_filter_to(lines, SEV_FAIL)) == 1  # declared


# ---------------------------------------------------------------------------
# 5b. The WHOLE path, end to end: a real App, a real ring, a real dock
# ---------------------------------------------------------------------------


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    """A real App on a real Container, pointed at a tmp PERSONA_HOME.

    Built the way `tests/test_wipe_clears_activity_log.py` builds one, because
    the seam this exercises — `App._flush_log` — is the ONE link in the chain
    no smaller test can reach: it takes the tail out of the ring and hands it to
    the dock, and taking it as TEXT rather than as LINES silently destroys every
    declaration exactly there. Nothing about the log path is stubbed.
    """
    import src.core.config as cfg
    import src.services.profile.manager as mod
    import src.ui.state as state_mod
    from src.core.container import Container
    from src.core.logging import setup_logging
    from src.ui.app import App
    from src.ui.refs import UIRefs

    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(cfg, "LOG_DIR", str(log_dir), raising=False)
    monkeypatch.setattr(state_mod, "LOG_DIR", str(log_dir), raising=False)
    setup_logging(str(log_dir))
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)

    class _FakePage:
        def show_dialog(self, dlg):
            pass

        def pop_dialog(self):
            pass

        def update(self):
            pass

        def run_task(self, handler, *a, **k):
            return None

    a = App(container=Container())
    a.page = _FakePage()
    a.refs = UIRefs(
        stats_text=ft.Text(),
        running_text=ft.Text(),
        content_subtitle=ft.Text(),
        profile_list_area=ft.Column(),
        prev_btn=ft.IconButton(),
        next_btn=ft.IconButton(),
        page_label=ft.Text(),
        bulk_bar=ft.Row(),
        file_picker=ft.FilePicker(),
    )
    a._dock = LogDock()
    a.state._log_lines.clear()
    a.state._log_seq = 0
    return a


def test_a_declaration_survives_the_whole_path_from_App_log_to_the_painted_dot(
    real_app,
):
    """END TO END, through the REAL app: `App._log` → `AppState.add_log` → the
    ring → `App._flush_log` → `LogDock.render` → the row's dot and the pulse.

    THIS IS THE TEST THAT COVERS `_flush_log`. That method used to join the tail
    into one string and re-split it; a `str` subclass does not survive that, so
    the declaration would be dropped in the one step between the ring that holds
    it and the console that renders it — and every smaller test in this file
    would still pass. The dot is read off the control the dock actually built.
    """
    real_app.state._last_log_ui_update = 0.0
    real_app._log(declare("Could not delete acme-bank.", SEV_FAIL))
    real_app._flush_log()

    assert real_app._dock.row_count == 1, "premise: the dock painted the row"
    assert _dot_colour(real_app._dock.list.controls[0]) == SEV_COLOR[SEV_FAIL]
    assert real_app._dock._pulse.bgcolor == SEV_COLOR[SEV_FAIL]

    # The premise, in the same App: the prose alone says otherwise.
    assert severity("Could not delete acme-bank.") == SEV_IDLE


def test_the_undeclared_lines_around_it_still_paint_by_prose(real_app):
    """The same path, unchanged, for the population that declares nothing."""
    for msg, want in [
        ("LAUNCH_FAILED: engine firefox-142 missing", SEV_FAIL),
        ("Browser started!", SEV_OK),
        ("Engine update available", SEV_INFO),
        ("Trashed bookmark: old-jar", SEV_IDLE),
    ]:
        real_app.state._last_log_ui_update = 0.0
        real_app._log(msg)
        real_app._flush_log()
        assert _dot_colour(real_app._dock.list.controls[-1]) == SEV_COLOR[want], msg


# ---------------------------------------------------------------------------
# 6. AC3 — the fallback is untouched
# ---------------------------------------------------------------------------


def test_severity_is_byte_identical_to_the_shipped_classifier():
    """AC3, as an in-suite guard rather than only as a one-off diff.

    The whole-corpus before/after diff is in the PR; this pins the FUNCTION
    ITSELF against the version that shipped at 14c9b24, so a later edit to the
    token bag fails here with the offending message named — and cannot be
    excused as "the declaration covers it", because the fallback still governs
    every un-migrated site and every line seeded from disk.
    """
    shipped_fail = [
        "Engine download failed: timeout",
        "Error starting process: boom",
        "LAUNCH_FAILED: engine firefox-142 missing",
        "Session ended: mail-us-011",
    ]
    shipped_ok = [
        "Browser started!",
        "Engine installed: 142",
        "Profile imported successfully",
        "Profile exported successfully",
        "Firefox engine not ready yet — wait for the download to finish.",
        "Engine updated to 142",
        "Bookmarks synced",
        "Profile frozen",
        "Retention floor reached",
    ]
    shipped_info = [
        "Firefox engine update available (142)",
        "Downloading engine 142...",
        "Launching shop-de-03",
        "Purged 1 trash entry/entries past the 30-day retention window",
    ]
    shipped_idle = [
        "Trashed bookmark: old-jar",
        "Moved bookmark to trash: old-jar",
        "emptied trash (3 item(s))",
        "permanently deleted profile: alpha",
        "restored bookmark: old-jar",
        # NOT a failure to the shipped classifier, and that is the point of
        # pinning it: the bag matches "refusED", and this line says "refusING".
        # A near-miss like this is exactly what a declaration is for, and
        # exactly what must NOT be fixed by widening the bag.
        "PLACES_INIT refusing to start: this profile has a proxy assigned",
        (
            "Could not delete acme-bank: its data could not be moved to the "
            "trash. The profile is unchanged."
        ),
    ]
    for m in shipped_fail:
        assert severity(m) == SEV_FAIL, m
    for m in shipped_ok:
        assert severity(m) == SEV_OK, m
    for m in shipped_info:
        assert severity(m) == SEV_INFO, m
    for m in shipped_idle:
        assert severity(m) == SEV_IDLE, m


def test_severity_is_still_importable_from_log_console():
    """The classifier MOVED to a flet-free module so `state.py` could preserve a
    declaration without growing a UI dependency. Every existing import site —
    two suites and two modules — must be unaffected by that move."""
    from src.ui import log_console, log_severity

    assert log_console.severity is log_severity.severity
    for name in ("SEV_FAIL", "SEV_OK", "SEV_INFO", "SEV_IDLE"):
        assert getattr(log_console, name) == getattr(log_severity, name)
    assert set(log_console.SEV_COLOR) == {SEV_FAIL, SEV_OK, SEV_INFO, SEV_IDLE}
