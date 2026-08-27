"""The UI's half of the surviving-browser guard (PS-223).

Two behaviours are covered here, and both are about what the USER meets:

1. Clicking a card whose browser survived a previous persona must END that
   browser, not fall into a stop path that has nothing to stop. Without this
   the click reports "stopping", finds no handle, answers False, and the card
   flips back to LAUNCH — the double-launch defect wearing a different hat.

2. The click is the CONSENT. The ticket forbids silently killing a surviving
   browser; it does not forbid killing one the user asked for by name.
"""

import threading

from src.core.strings import get_string
from src.ui.actions import browser as browser_actions
from src.ui.state import AppState


class _Profile:
    def __init__(self, name, engine="chromium", os_type="windows"):
        self.name = name
        self.engine = engine
        self.os_type = os_type


class _PM:
    def __init__(self, profile):
        self.profiles = {profile.name: profile}


class _Survivor:
    profile = "p1"
    pid = 4242


class _BL:
    """A launcher whose profile is 'running' but has no session THIS run."""

    def __init__(self, survivor=_Survivor(), close_ok=True):
        self._survivor = survivor
        self._close_ok = close_ok
        self.started = False
        self.stopped = False
        self.closed = None

    def is_running(self, name):
        return True

    def survivor_for(self, name):
        return self._survivor

    def close_survivor(self, name):
        self.closed = name
        return self._close_ok

    def stop_profile(self, name, timeout=2):
        self.stopped = True
        return True

    def start_thread(self, *a, **k):
        self.started = True


class _LegacyBL:
    """A launcher with NO survivor surface — a pre-PS-223 double.

    Written as its own class rather than a subclass that nulls the methods out,
    because the attribute must be genuinely ABSENT: an attribute set to None is
    still found by lookup and fails later, at the call, which is a different
    failure from the one this guards.
    """

    def __init__(self):
        self.started = False
        self.stopped = False

    def is_running(self, name):
        return True

    def stop_profile(self, name, timeout=2):
        self.stopped = True
        return True

    def start_thread(self, *a, **k):
        self.started = True


def _run(bl, pm=None, logs=None):
    logs = logs if logs is not None else []
    pm = pm or _PM(_Profile("p1"))
    browser_actions.launch_or_stop("p1", pm, bl, AppState(), logs.append)
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)
    return logs


def test_clicking_a_survivor_closes_it_rather_than_launching_again():
    """The click ends the leftover browser. It must NOT reach start_thread —
    that is the second launch on one profile dir this ticket exists to stop."""
    bl = _BL()

    logs = _run(bl)

    assert bl.closed == "p1"
    assert bl.started is False
    assert any(get_string("survivor_closed", name="p1") == m for m in logs)


def test_a_survivor_click_does_not_use_the_normal_stop_path():
    """stop_profile has no handle for a browser this process did not launch;
    routing there would report success over a window that is still open."""
    bl = _BL()

    _run(bl)

    assert bl.stopped is False


def test_a_failed_survivor_close_says_so_rather_than_claiming_success():
    """PS-204's lesson one ticket over: a teardown must report what it
    OBSERVED, not what it attempted."""
    bl = _BL(close_ok=False)

    logs = _run(bl)

    assert any(get_string("survivor_close_failed", name="p1") == m for m in logs)
    assert bl.started is False


def test_a_running_profile_with_no_survivor_takes_the_normal_stop_path():
    """The ordinary case is untouched: a session THIS run owns still stops the
    way it always did."""
    bl = _BL(survivor=None)

    _run(bl)

    assert bl.stopped is True
    assert bl.closed is None


def test_a_launcher_without_the_survivor_surface_behaves_as_before():
    """A pre-PS-223 launcher (or a test double) must not start raising."""
    bl = _LegacyBL()

    _run(bl)

    assert bl.stopped is True
