"""The profile card must report a session's OPEN CDP CHANNEL from the live
session, not from the stored ``ai_control`` record.

``ProfileManager.set_ai_control`` mutates and saves ``p.ai_control`` with no
reference to whether the profile is running, so the record and the running
session diverge the moment an operator flips the connect-page checkbox
mid-session. The port chromium already bound does not close because a boolean on
disk changed. An indicator derived from the record therefore fails in BOTH
directions, and the dangerous one is falsely reassuring: a quiet card over a
listening unauthenticated channel.

These tests pin the mechanism (a fact captured at launch), not the shape of any
call — see ``test_falsification_*`` at the bottom, which asserts the suite goes
RED when the capture is removed.
"""
import threading

import flet as ft

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.automation_channel import opens_cdp_channel
from src.services.browser.launcher import BrowserLauncher
from src.ui.components.profile_card import build_profile_card


def _noop(*a, **k):
    pass


def _texts(control):
    out = []

    def walk(c):
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif v is not None and not isinstance(v, str):
                walk(v)

    walk(control)
    return out


def _card(profile, *, is_running, cdp_channel_open):
    return build_profile_card(
        profile, False, is_running, _noop, _noop, _noop,
        cdp_channel_open=cdp_channel_open,
    )


def _reports_channel(card) -> bool:
    """True if the card asserts an automation channel to the operator.

    Matched on the rendered WORD rather than on a container identity so the test
    describes what the operator can actually read off the row.
    """
    return any("automation" in t.lower() for t in _texts(card))


# --------------------------------------------------------------------------
# AC1 / AC6 — what the card renders
# --------------------------------------------------------------------------

def test_running_chromium_session_with_a_channel_reports_it():
    # AC1. The card is the surface the operator launches from and watches; this
    # is the statement that a remote-debugging port is open on THIS session.
    p = Profile(name="a", os_type="windows", engine="chromium")
    assert _reports_channel(_card(p, is_running=True, cdp_channel_open=True))


def test_running_session_without_a_channel_says_nothing():
    p = Profile(name="a", os_type="windows", engine="chromium")
    assert not _reports_channel(_card(p, is_running=True, cdp_channel_open=False))


def test_a_stopped_profile_renders_exactly_as_before():
    # AC6. Not running -> the card is byte-identical to the pre-slice render,
    # even if a stale True were somehow handed in. Two independent gates.
    p = Profile(name="a", os_type="windows", engine="chromium")
    before = build_profile_card(p, False, False, _noop, _noop, _noop)
    after = _card(p, is_running=False, cdp_channel_open=True)
    assert _texts(before) == _texts(after)
    assert not _reports_channel(after)


# --------------------------------------------------------------------------
# AC4 / AC5 — the engine dimension. Lighting the indicator for a Firefox
# session would be a NEW false claim: the inverse of the defect being fixed.
# --------------------------------------------------------------------------

def test_firefox_opens_no_channel_at_any_ai_control_value():
    # AC4. invisible_launch.py never reads ai_control — Firefox opens no CDP
    # port at all, so the predicate must refuse to claim one.
    for want in (True, False):
        p = Profile(name="f", os_type="windows", engine="firefox")
        p.ai_control = want
        assert opens_cdp_channel(p) is False, f"firefox claimed a channel at {want}"


def test_mobile_profile_storing_firefox_does_open_a_channel():
    # AC5. The stored engine says firefox, but a mobile profile is reconciled to
    # chromium by the coherence rules, so it LAUNCHES chromium, DOES get
    # --remote-debugging-port=0, and must light. This passes only if
    # effective_engine was used; reading profile.engine reports "closed" over a
    # listening port.
    p = Profile(name="m", os_type="android", engine="firefox")
    p.ai_control = True
    assert opens_cdp_channel(p) is True


def test_chromium_without_ai_control_opens_no_channel():
    p = Profile(name="c", os_type="windows", engine="chromium")
    p.ai_control = False
    assert opens_cdp_channel(p) is False


def test_the_predicate_performs_no_io():
    # The predicate sits on a render-feeding path. read_cdp_port is a file read
    # and cdp_info_for does real network IO; neither may be reachable from here.
    import socket

    opened = []
    real_socket = socket.socket

    class _Spy(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            opened.append(a)
            super().__init__(*a, **k)

    p = Profile(name="c", os_type="windows", engine="chromium")
    p.ai_control = True
    socket.socket = _Spy
    try:
        assert opens_cdp_channel(p) is True
    finally:
        socket.socket = real_socket
    assert opened == [], f"the predicate opened a socket: {opened}"


# --------------------------------------------------------------------------
# The launcher: capture at registration, forget at teardown
# --------------------------------------------------------------------------

class _Proc:
    def __init__(self, returncode=0):
        self._exited = threading.Event()
        self.stdout = None
        self.returncode = returncode

    def wait(self, timeout=None):
        self._exited.wait(timeout)
        return self.returncode

    def poll(self):
        return self.returncode if self._exited.is_set() else None

    def terminate(self):
        self._exited.set()

    def kill(self):
        self._exited.set()


def _live_session(monkeypatch, profile):
    """Start a session and keep it registered until the test terminates it."""
    proc = _Proc()
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    registered = threading.Event()

    def fake_monitor(*a, **k):
        registered.set()
        proc._exited.wait()

    monkeypatch.setattr(BrowserLauncher, "_monitor_process", fake_monitor)
    bl = BrowserLauncher()
    bl.start_thread(profile, _noop)
    assert registered.wait(5), "session never registered"
    return bl, proc


def test_launcher_captures_the_channel_from_the_launched_profile(monkeypatch):
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    try:
        assert bl.cdp_channel_open("a") is True
    finally:
        bl.stop_profile("a")


def test_launcher_reports_no_channel_for_a_profile_never_launched():
    assert BrowserLauncher().cdp_channel_open("nobody") is False


# --------------------------------------------------------------------------
# AC3 ⭐ — mid-session divergence, BOTH directions. This is the reason the
# slice exists: a record-derived implementation fails these two.
# --------------------------------------------------------------------------

def test_open_channel_still_reads_open_after_ai_control_is_switched_off(monkeypatch):
    # Launch with ai_control=True, then flip the RECORD to False, exactly as the
    # connect-page checkbox does via set_ai_control. Chromium's bound port is
    # untouched, so the card must still report OPEN. This is the FALSELY
    # REASSURING direction — the dangerous one.
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    try:
        p.ai_control = False  # the stored record moves; the port does not
        assert bl.cdp_channel_open("a") is True
        assert _reports_channel(
            _card(p, is_running=True, cdp_channel_open=bl.cdp_channel_open("a"))
        ), "card went quiet over a still-listening channel"
    finally:
        bl.stop_profile("a")


def test_closed_channel_still_reads_closed_after_ai_control_is_switched_on(
    monkeypatch,
):
    # The inverse: launched WITHOUT a port, record flipped to True mid-session.
    # No port was opened, so claiming one would be a false alarm — and an
    # indicator that cries wolf is one the operator learns to ignore.
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = False
    bl, proc = _live_session(monkeypatch, p)
    try:
        p.ai_control = True
        assert bl.cdp_channel_open("a") is False
        assert not _reports_channel(
            _card(p, is_running=True, cdp_channel_open=bl.cdp_channel_open("a"))
        ), "card claimed a channel that was never opened"
    finally:
        bl.stop_profile("a")


# --------------------------------------------------------------------------
# AC7 — teardown. A leak here keeps reporting an open port on a dead session.
# --------------------------------------------------------------------------

def test_stop_profile_clears_the_captured_channel(monkeypatch):
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    assert bl.cdp_channel_open("a") is True
    bl.stop_profile("a")
    assert bl.cdp_channel_open("a") is False


def test_shutdown_all_clears_the_captured_channel(monkeypatch):
    # The .clear() site specifically: it tears down EVERY session at once and is
    # the one most easily missed when a new per-session dict is added. A leak
    # here means the indicator asserts an open channel on profiles that are no
    # longer running, and the stale entry shadows the next launch.
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    assert bl.cdp_channel_open("a") is True
    bl.shutdown_all()
    assert bl.cdp_channel_open("a") is False


def test_every_session_fact_dict_is_cleared_at_every_teardown_site():
    """The durable form of AC7: the new dict must be torn down wherever the
    established one is.

    Asserted structurally rather than by line number (anchors in this repo rot —
    one unchanged line in process.py has produced three different citations).
    Both per-session dicts are dropped through ONE pair of helpers, so a future
    dict added to those helpers is cleaned up at all seven sites by
    construction. This test fails if anyone re-introduces a bare
    ``_session_started_at.pop`` beside which a new fact could be forgotten.
    """
    import inspect

    src = inspect.getsource(launcher_mod)
    body = src.split("def _forget_session_facts")[0]
    # Outside the helpers, no site may touch the raw dicts except the single
    # write at registration and the started_at read.
    assert body.count("_session_started_at.pop(") == 0, (
        "a teardown site pops _session_started_at directly instead of going "
        "through _forget_session_facts — a new per-session fact would leak there"
    )
    assert body.count("_session_started_at.clear()") == 0, (
        "shutdown_all clears _session_started_at directly instead of going "
        "through _forget_all_session_facts"
    )


# --------------------------------------------------------------------------
# AC8 ⭐ — falsification. Binds the tests to the MECHANISM (a fact captured at
# launch), not to the shape of the call.
# --------------------------------------------------------------------------

def test_falsification_removing_the_capture_makes_the_channel_unreportable(
    monkeypatch,
):
    """With the session-state capture removed, AC1/AC3 must go RED.

    Simulated by neutralising the capture at its source — the launcher records
    nothing — while every other part of the diff (predicate, accessor, card,
    app wiring) stays in place. If the indicator could still light, it would be
    reading something other than live session state, which is precisely the
    defect: the card would be answering from the record again.
    """
    monkeypatch.setattr(
        launcher_mod, "opens_cdp_channel", lambda profile: False
    )
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    try:
        # AC1's assertion is now FALSE — nothing else in the diff can supply it.
        assert bl.cdp_channel_open("a") is False
        assert not _reports_channel(
            _card(p, is_running=True, cdp_channel_open=bl.cdp_channel_open("a"))
        )
    finally:
        bl.stop_profile("a")


def test_falsification_a_record_derived_indicator_fails_ac3(monkeypatch):
    """The naive implementation, executed, so its failure is on record.

    Rendering ``profile.ai_control and is_running`` — the obvious fix — is
    itself a fresh instance of the defect: a state produced by a stored field
    that no longer describes the session. Here the record says False while the
    port is open, and the naive indicator goes quiet over it. The captured fact
    does not.
    """
    p = Profile(name="a", os_type="windows", engine="chromium")
    p.ai_control = True
    bl, proc = _live_session(monkeypatch, p)
    try:
        p.ai_control = False  # mid-session toggle; the port stays open

        naive = bool(getattr(p, "ai_control", False))
        assert naive is False, "premise: the record now reads False"
        assert not _reports_channel(_card(p, is_running=True, cdp_channel_open=naive))

        # The shipped mechanism gets it right where the naive one is silent.
        assert bl.cdp_channel_open("a") is True
        assert _reports_channel(
            _card(p, is_running=True, cdp_channel_open=bl.cdp_channel_open("a"))
        )
    finally:
        bl.stop_profile("a")


# --------------------------------------------------------------------------
# The accessor is reachable through the TYPE the wiring uses, not just the
# concrete class.
# --------------------------------------------------------------------------

def test_the_protocol_declares_the_accessor_the_card_render_calls():
    """``app.py`` types ``self.bl`` as ``IBrowserLauncher`` and calls
    ``cdp_channel_open`` on it to build the card, so the accessor must be
    declared on the protocol and not only on the concrete launcher.

    This is the same rule ``test_cert_trust_status.py`` states for
    ``start_thread``; that test compares one known signature, so it cannot
    catch a *newly added* method. The REST/MCP lanes take
    ``bl: IBrowserLauncher`` via ``Depends``, so an implementation that
    satisfied the protocol while missing this method would type-check and then
    raise on the render path.

    Scoped deliberately to the accessor this slice adds: ``shutdown_all`` is
    also absent from the protocol, but that predates this branch and widening
    the assertion would fail on unrelated debt.
    """
    import inspect

    from src.interfaces.protocols import IBrowserLauncher

    assert hasattr(IBrowserLauncher, "cdp_channel_open"), (
        "IBrowserLauncher does not declare cdp_channel_open, but app.py calls "
        "it on a value typed as that protocol"
    )
    assert (
        inspect.signature(IBrowserLauncher.cdp_channel_open).parameters.keys()
        == inspect.signature(BrowserLauncher.cdp_channel_open).parameters.keys()
    )


# --------------------------------------------------------------------------
# AC9 — the render path stays IO-free.
# --------------------------------------------------------------------------

def test_building_a_card_with_the_channel_opens_no_socket():
    import socket

    opened = []
    real_socket = socket.socket

    class _Spy(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            opened.append(a)
            super().__init__(*a, **k)

    p = Profile(name="a", os_type="windows", engine="chromium")
    socket.socket = _Spy
    try:
        for running in (True, False):
            for open_ in (True, False):
                _card(p, is_running=running, cdp_channel_open=open_)
    finally:
        socket.socket = real_socket
    assert opened == [], f"building a card opened a socket: {opened}"
