"""audit5 #1: a spawn-in-flight lives in _starting, not _active_sessions.
stop_profile used to check only _active_sessions, so it returned False and
terminated NOTHING for a profile still starting. delete_profile trusts that
no-op stop and then rmtree's the data dir while spawn_browser is concurrently
os.makedirs+seeding into it → torn data dir. stop_profile must handle _starting:
abort the spawn, wait for it to bail, and return a truthful result so the caller
knows the dir is safe to remove."""
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
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self.terminated = True
        self._done.set()
        self.returncode = 0

    def kill(self):
        self.terminate()


def _quiet(monkeypatch):
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)


def test_stop_profile_true_and_blocks_for_spawn_in_flight(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    made = {}

    def slow_spawn(profile):
        started.set()
        release.wait(2.0)
        p = _Proc()
        made["proc"] = p
        return p

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    _quiet(monkeypatch)

    bl = BrowserLauncher()
    p = Profile(name="inflight", os_type="windows")
    t = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t.start()
    started.wait(2.0)

    # stop_profile is called while the spawn is mid-flight. It must NOT return
    # False (the old bug); it must abort and return True.
    result = {}

    def do_stop():
        result["ok"] = bl.stop_profile("inflight")

    ts = threading.Thread(target=do_stop)
    ts.start()
    # stop must block until the spawn resolves (so the caller can safely rmtree
    # only after nothing is writing the dir anymore).
    time.sleep(0.1)
    assert not result, "stop_profile returned before the spawn resolved"
    release.set()
    ts.join(3)
    t.join(3)

    assert result.get("ok") is True, "stop_profile must be truthful for _starting"
    # the spawn's process was terminated, not left running as an orphan session
    assert made["proc"].terminated is True
    # and the profile is no longer running or starting
    assert bl.is_running("inflight") is False
    assert "inflight" not in bl.running_profile_names()


def test_stop_returns_only_after_process_terminated(monkeypatch):
    # audit6 #5: the abort branch must terminate the browser BEFORE leaving
    # _starting — else stop_profile's wait wakes while the process is still alive
    # and delete/wipe rmtree the dir under a live browser. Make terminate slow and
    # assert the process is fully dead by the time stop_profile returns.
    started = threading.Event()
    release = threading.Event()
    made = {}
    order = []

    def slow_spawn(profile):
        started.set()
        release.wait(2.0)
        p = _Proc()
        made["proc"] = p
        return p

    def slow_terminate(proc, name, timeout=2):
        time.sleep(0.2)          # emulate the ~2s terminate window
        proc.terminate()
        order.append("terminated")

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    monkeypatch.setattr(launcher_mod, "terminate", slow_terminate)
    _quiet(monkeypatch)

    bl = BrowserLauncher()
    p = Profile(name="race", os_type="windows")
    t = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t.start()
    started.wait(2.0)

    def do_stop():
        bl.stop_profile("race")
        order.append("stop-returned")

    ts = threading.Thread(target=do_stop)
    ts.start()
    time.sleep(0.1)
    release.set()
    ts.join(3)
    t.join(3)

    # the process was terminated BEFORE stop_profile returned — no window where a
    # caller could rmtree under a live browser.
    assert made["proc"].terminated is True
    assert order == ["terminated", "stop-returned"], order


def test_aborted_spawn_does_not_register_session(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_spawn(profile):
        started.set()
        release.wait(2.0)
        return _Proc()

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    _quiet(monkeypatch)
    monitored = []
    monkeypatch.setattr(
        BrowserLauncher,
        "_monitor_process",
        lambda self, *a, **k: monitored.append(a),
    )

    bl = BrowserLauncher()
    p = Profile(name="abort", os_type="windows")
    t = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t.start()
    started.wait(2.0)

    ts = threading.Thread(target=lambda: bl.stop_profile("abort"))
    ts.start()
    time.sleep(0.1)
    release.set()
    ts.join(3)
    t.join(3)

    # an aborted spawn must not start a monitor thread or leave an active session
    assert monitored == [], "aborted spawn should not begin monitoring"
    assert "abort" not in bl.running_profile_names()


def test_stop_profile_false_when_truly_idle(monkeypatch):
    # unchanged behaviour: stopping a profile that is neither active nor starting
    # is still a no-op returning False.
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: _Proc())
    _quiet(monkeypatch)
    bl = BrowserLauncher()
    assert bl.stop_profile("nobody") is False


def test_delete_waits_for_in_flight_spawn_before_rmtree(tmp_path, monkeypatch):
    # The whole point of #1: delete_profile → stop hook (launcher.stop_profile)
    # must BLOCK until a spawn-in-flight bails, so rmtree never races the
    # os.makedirs+seed. Wire a real launcher as the manager's stop hook and prove
    # the data dir is gone and the spawned proc terminated after delete.
    import os

    import src.core.config as cfg
    import src.services.profile.manager as mgr_mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mgr_mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)

    from src.services.profile.manager import ProfileManager

    started = threading.Event()
    release = threading.Event()
    made = {}

    mgr = ProfileManager()
    mgr.add_profile("victim", "", "windows")
    data_dir = mgr._data_path("victim")

    def slow_spawn(profile):
        # emulate spawn_browser seeding the data dir, then holding
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "seed.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        started.set()
        release.wait(2.0)
        p = _Proc()
        made["proc"] = p
        return p

    monkeypatch.setattr(launcher_mod, "spawn_browser", slow_spawn)
    _quiet(monkeypatch)

    bl = BrowserLauncher()
    mgr.set_stop_hook(bl.stop_profile)

    p = Profile(name="victim", os_type="windows")
    t = threading.Thread(target=bl.start_thread, args=(p, lambda _l: None))
    t.start()
    started.wait(2.0)
    assert os.path.isdir(data_dir)  # spawn seeded it

    deleted = {}

    def do_delete():
        deleted["ok"] = mgr.delete_profile("victim")

    td = threading.Thread(target=do_delete)
    td.start()
    time.sleep(0.1)
    assert not deleted, "delete rmtree'd before the spawn released the dir"
    release.set()
    td.join(3)
    t.join(3)

    assert deleted.get("ok") is True
    assert made["proc"].terminated is True
    assert not os.path.exists(data_dir), "data dir should be gone after delete"
    assert "victim" not in mgr.profiles
