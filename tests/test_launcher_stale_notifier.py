"""audit6 #7: notify_stopped must evict a session by proc IDENTITY, not just by
name. A delayed stale notifier from a previous crashed launch could otherwise pop
the NEW live session the user relaunched — leaving the new browser untracked,
stop_profile False for it, and a second engine launchable on the same dir."""
import threading

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher


class _Proc:
    def __init__(self):
        self._done = threading.Event()
        self.stdout = None
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self._done.set()
        self.returncode = 0

    def kill(self):
        self.terminate()


def test_stale_notifier_does_not_evict_new_session(monkeypatch):
    # Capture each launch's notify_stopped (passed as wait_for_exit's 3rd arg)
    # without actually running the wait thread, so we can fire a stale one on
    # demand after a relaunch.
    notifiers = []

    def capture_wait(proc, name, notify_stopped):
        notifiers.append(notify_stopped)

    monkeypatch.setattr(launcher_mod, "wait_for_exit", capture_wait)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    procs = [_Proc(), _Proc()]
    spawned = []

    def spawn(profile):
        p = procs[len(spawned)]
        spawned.append(p)
        return p

    monkeypatch.setattr(launcher_mod, "spawn_browser", spawn)

    bl = BrowserLauncher()

    # First launch registers proc[0]; capture its notifier.
    bl.start_thread(Profile(name="fox", os_type="windows"), lambda _l: None)
    assert bl._active_sessions["fox"] is procs[0]
    stale_notify = notifiers[0]

    # It ends (stop_profile pops proc[0] cleanly).
    assert bl.stop_profile("fox") is True
    assert "fox" not in bl._active_sessions

    # User relaunches: proc[1] registers under the same name.
    bl.start_thread(Profile(name="fox", os_type="windows"), lambda _l: None)
    assert bl._active_sessions["fox"] is procs[1]

    # The STALE notifier from launch #0 fires late. With the identity guard it
    # must NOT evict proc[1]'s live entry.
    stale_notify()

    assert bl._active_sessions.get("fox") is procs[1], (
        "a stale notifier evicted the new live session (name-only eviction)"
    )
    assert bl.is_running("fox") is True
