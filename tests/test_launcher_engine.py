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


def _monitored_engine(monkeypatch, profile):
    recorded = {}
    done = threading.Event()

    def fake_monitor(self, proc, name, log_callback, on_ready,
                     notify_stopped, engine="chromium"):
        recorded["engine"] = engine
        done.set()

    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: _Proc())
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", fake_monitor)
    bl = BrowserLauncher()
    bl.start_thread(profile, lambda _m: None)
    assert done.wait(5)
    return recorded["engine"]


def test_mobile_firefox_profile_monitored_as_chromium(monkeypatch):
    # A mobile profile launches chromium regardless of the stored engine; the
    # monitor must wait for chromium readiness, not a Firefox BROWSER_STARTED
    # that never comes.
    profile = Profile(name="mob", engine="firefox", os_type="android")
    assert _monitored_engine(monkeypatch, profile) == "chromium"


def test_desktop_firefox_profile_monitored_as_firefox(monkeypatch):
    profile = Profile(name="fox", engine="firefox", os_type="windows")
    assert _monitored_engine(monkeypatch, profile) == "firefox"


def test_chromium_profile_monitored_as_chromium(monkeypatch):
    profile = Profile(name="chr", engine="chromium", os_type="windows")
    assert _monitored_engine(monkeypatch, profile) == "chromium"
