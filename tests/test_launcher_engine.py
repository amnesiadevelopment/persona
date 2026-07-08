import threading

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher


class _Proc:
    stdout = None

    def wait(self):
        return 0

    def poll(self):
        return 0


def _ready_fired(monkeypatch, profile) -> bool:
    # #137: on_ready must fire at launch START — the process being up is what
    # makes the profile stoppable. Gating Firefox readiness on BROWSER_STARTED
    # left a wedged proxied launch stuck "loading" with no stop button; stdout
    # here never carries a readiness line, so ready must not depend on one.
    ready = threading.Event()
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: _Proc())
    bl = BrowserLauncher()
    bl.start_thread(profile, lambda _m: None, on_ready=ready.set)
    return ready.wait(5)


def test_firefox_profile_stoppable_at_launch_start(monkeypatch):
    profile = Profile(name="fox", engine="firefox", os_type="windows")
    assert _ready_fired(monkeypatch, profile)


def test_chromium_profile_stoppable_at_launch_start(monkeypatch):
    profile = Profile(name="chr", engine="chromium", os_type="windows")
    assert _ready_fired(monkeypatch, profile)


def test_mobile_profile_stoppable_at_launch_start(monkeypatch):
    # A mobile profile stored as firefox actually launches chromium; either
    # way the stop button must be available from launch start.
    profile = Profile(name="mob", engine="firefox", os_type="android")
    assert _ready_fired(monkeypatch, profile)
