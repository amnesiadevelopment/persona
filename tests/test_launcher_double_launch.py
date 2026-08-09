"""A second launch of a profile fired WHILE its spawn is still in flight must be
refused — otherwise two browsers run on one profile dir (the check-then-spawn
race: check under lock, release, slow spawn, register). The slot is reserved in
_starting before the lock is released."""
import threading
import time

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher


class _Proc:
    def __init__(self):
        self._done = threading.Event()
        self.stdout = None
        self.returncode = None
        self.pid = 4242

    def poll(self):
        return None  # stays "running"

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self._done.set()
        self.returncode = 0

    def kill(self):
        self.terminate()


def test_concurrent_launch_spawns_once(monkeypatch):
    spawn_calls = []
    barrier = threading.Event()

    def slow_spawn(profile):
        spawn_calls.append(profile.name)
        # hold the spawn so a second start_thread races the window between the
        # reservation and registration
        barrier.wait(2.0)
        return _Proc()

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    # don't let the monitor/wait threads do anything real
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher()
    p = Profile(name="race", os_type="windows")

    logs = []
    t1 = threading.Thread(target=bl.start_thread, args=(p, logs.append))
    t2 = threading.Thread(target=bl.start_thread, args=(p, logs.append))
    t1.start()
    # tiny head start so t1 reserves the slot first, then t2 must see it busy
    time.sleep(0.05)
    t2.start()
    barrier.set()
    t1.join(3)
    t2.join(3)

    # spawn_browser ran exactly once despite two concurrent launches
    assert spawn_calls == ["race"], spawn_calls
    assert bl.is_running("race") is True


def test_duplicate_launch_calls_on_stop_to_clear_loading(monkeypatch):
    # audit6 LOW b: a refused duplicate launch must call on_stop so the caller
    # clears its is_loading flag — returning silently left the card stuck
    # "loading" and uncontrollable until a restart.
    release = threading.Event()

    def slow_spawn(profile):
        release.wait(2.0)
        return _Proc()

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher()
    p = Profile(name="dup", os_type="windows")

    # first launch reserves the slot (spawn still in flight)
    t1 = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t1.start()
    time.sleep(0.05)

    # second launch of the same profile is refused — but must fire on_stop
    stopped = []
    bl.start_thread(p, lambda _l: None, on_stop=lambda: stopped.append(True))
    assert stopped == [True], "duplicate refusal must call on_stop to clear loading"

    release.set()
    t1.join(3)


def test_is_running_true_during_spawn(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_spawn(profile):
        started.set()
        release.wait(2.0)
        return _Proc()

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher()
    p = Profile(name="mid", os_type="windows")
    t = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t.start()
    started.wait(2.0)
    # spawn is mid-flight: the profile must already read as running
    assert bl.is_running("mid") is True
    assert "mid" in bl.running_profile_names()
    release.set()
    t.join(3)
