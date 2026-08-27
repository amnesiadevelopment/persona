"""The launch guard SURVIVES A RESTART of persona (PS-223).

THE DEFECT, in one sentence: the launcher's record of which profiles are
running is a plain dict of Popen handles, and that dict dies with the process —
so after an unclean exit `is_running()` answers "not running" about a browser
that is alive on screen, and the user launches a second one on the same profile
directory.

A NOTE ON WHAT "RESTART" MEANS HERE. These tests build a SECOND BrowserLauncher
against the SAME registry file. That is the honest in-process stand-in for a
restart, and it is exact on the point at issue: the new launcher's dicts are
empty, exactly as a new process's would be, so it can only answer from the
persisted record. What it does NOT reproduce is a real browser outliving a real
persona — that is exercised by hand on the user's path (PS-17), because no unit
test crosses that boundary.
"""

import os
import subprocess
import sys
import threading
import time

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher
from src.services.browser.session_registry import (
    SessionRecord,
    SessionRegistry,
    capture_create_time,
    make_record,
)


class _Proc:
    """A Popen stand-in that stays 'running' and carries a REAL pid.

    The pid is real (this test process) so liveness probes have something
    truthful to resolve — a fabricated pid would either not exist (making every
    record read GONE and every assertion vacuous) or, worse, belong to an
    unrelated live process.
    """

    def __init__(self, pid=None):
        self._done = threading.Event()
        self.stdout = None
        self.returncode = None
        self.pid = pid or os.getpid()

    def poll(self):
        return None

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self._done.set()
        self.returncode = 0

    def kill(self):
        self.terminate()


def _quiet_launcher(monkeypatch, registry, proc=None):
    """A launcher whose spawn is stubbed and whose monitor threads do nothing."""
    monkeypatch.setattr(
        launcher_mod, "spawn_browser", lambda profile: proc or _Proc()
    )
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)
    return BrowserLauncher(registry=registry)


def test_a_launch_is_recorded_where_it_survives_the_process(tmp_path, monkeypatch):
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)

    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    assert [r.profile for r in reg.load()] == ["alpha"]


def test_a_restarted_persona_still_reports_the_browser_as_running(
    tmp_path, monkeypatch
):
    """THE USER'S SEQUENCE, minus the real browser.

    Launch, then discard the launcher WITHOUT a clean shutdown (no
    shutdown_all — that is what an unclean exit means), then build a new one on
    the same registry. It must find the survivor and report the profile as
    running, where the old code answered "not running" and offered a second
    launch.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    # An unclean exit: the object goes away, the file does not.
    restarted = _quiet_launcher(monkeypatch, SessionRegistry(reg.path))
    assert restarted.is_running("alpha") is False, (
        "a launcher that has not scanned must behave exactly as before"
    )

    survivors, unknown = restarted.scan_survivors()

    assert [r.profile for r in survivors] == ["alpha"]
    assert unknown == []
    assert restarted.is_running("alpha") is True


def test_a_clean_shutdown_leaves_no_survivor(tmp_path, monkeypatch):
    """The complement, and the thing that makes the survivor signal mean
    anything: after a CLEAN exit there is nothing on disk, so a restart finds
    nothing and refuses nothing."""
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    bl.shutdown_all()

    restarted = _quiet_launcher(monkeypatch, SessionRegistry(reg.path))
    survivors, unknown = restarted.scan_survivors()

    assert survivors == []
    assert unknown == []
    assert restarted.is_running("alpha") is False


def test_a_stale_record_does_not_block_a_launch(tmp_path, monkeypatch):
    """THE LOCKOUT CASE — the failure mode most likely to be missed, because it
    needs the record and the process to deliberately disagree.

    A record of a running profile whose process is GENUINELY GONE must not
    refuse a launch. Measured against a real chromium (PS-223), the engine
    itself recovers a stale SingletonLock and launches; a persona that refused
    here would be stricter than the browser it launches, with no way out from
    the UI.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = capture_create_time(proc.pid)
    proc.wait()  # the process is now genuinely gone

    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(
        SessionRecord(
            profile="ghost",
            pid=proc.pid,
            create_time=ct,
            pgid=None,
            engine="chromium",
            started_at=time.time(),
            owner_pid=os.getpid(),
        )
    )

    bl = _quiet_launcher(monkeypatch, reg)
    survivors, unknown = bl.scan_survivors()

    assert survivors == [], "a dead process must not be reported as a survivor"
    assert unknown == []
    assert bl.is_running("ghost") is False
    assert reg.load() == [], "the stale record is dropped, not kept to re-probe"


def test_an_indeterminate_record_does_not_block_a_launch(tmp_path, monkeypatch):
    """UNKNOWN FAILS OPEN, and says so.

    A record with no create time cannot rule out pid reuse, so liveness is
    indeterminate. It is reported (the caller tells the user the check could
    not be made) but it is NOT adopted as a survivor and refuses nothing.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(
        SessionRecord(
            profile="murky",
            pid=os.getpid(),
            create_time=None,
            pgid=None,
            engine="chromium",
            started_at=time.time(),
            owner_pid=os.getpid(),
        )
    )

    bl = _quiet_launcher(monkeypatch, reg)
    survivors, unknown = bl.scan_survivors()

    assert survivors == []
    assert [r.profile for r in unknown] == ["murky"]
    assert bl.is_running("murky") is False, "an unanswerable question must not refuse"


def test_stopping_a_profile_forgets_its_record(tmp_path, monkeypatch):
    """Teardown drops the record, so a stopped profile cannot later be mistaken
    for a survivor."""
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)
    assert reg.load()

    bl.stop_profile("alpha")

    assert reg.load() == []


def test_a_survivor_never_shadows_a_session_this_run_owns(tmp_path, monkeypatch):
    """A profile THIS process launched is tracked normally and must not also be
    treated as a survivor — that would offer the user a second, weaker way to
    kill their own live session."""
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(
        make_record("alpha", _Proc(), "chromium")
    )
    bl = _quiet_launcher(monkeypatch, reg)
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    survivors, _ = bl.scan_survivors()

    assert survivors == []
    assert bl.survivor_for("alpha") is None


def test_survivor_for_re_probes_and_releases_a_browser_since_closed(
    tmp_path, monkeypatch
):
    """The scan is a point in time; the user may close the window right after
    being told about it. survivor_for must re-probe, or the block outlives the
    browser — the lockout arriving a few minutes late.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(make_record("alpha", proc, "chromium"))

    bl = _quiet_launcher(monkeypatch, reg)
    survivors, _ = bl.scan_survivors()
    assert [r.profile for r in survivors] == ["alpha"]
    assert bl.survivor_for("alpha") is not None

    # The user closes the browser by hand.
    proc.kill()
    proc.wait()

    assert bl.survivor_for("alpha") is None, "a closed browser must stop blocking"
    assert bl.is_running("alpha") is False


def test_start_thread_itself_refuses_a_launch_over_a_survivor(tmp_path, monkeypatch):
    """THE GUARD IS IN THE LAUNCHER, NOT ONLY IN THE UI.

    The UI asks survivor_for() before it ever reaches start_thread, but
    start_thread is ALSO the entry point for the API and MCP lanes — and a
    guard that lives only in the UI is a guard two lanes do not have.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        reg = SessionRegistry(str(tmp_path / "s.json"))
        reg.record(make_record("alpha", proc, "chromium"))

        spawned = []
        monkeypatch.setattr(
            launcher_mod,
            "spawn_browser",
            lambda profile: spawned.append(profile.name) or _Proc(),
        )
        monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
        monkeypatch.setattr(
            BrowserLauncher, "_monitor_process", lambda *a, **k: None
        )
        bl = BrowserLauncher(registry=reg)
        bl.scan_survivors()

        logs: list[str] = []
        bl.start_thread(Profile(name="alpha", os_type="windows"), logs.append)

        assert spawned == [], "no second browser on a profile dir that has one"
        assert any("already has a browser running" in m for m in logs)
    finally:
        proc.kill()
        proc.wait()


def test_start_thread_allows_a_launch_when_the_record_is_stale(
    tmp_path, monkeypatch
):
    """The same guard, failing OPEN — a record whose process is gone must not
    refuse the launch in these lanes either, which have no card to click and so
    no way out of a false refusal."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = capture_create_time(dead.pid)
    dead.wait()

    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(
        SessionRecord(
            profile="alpha",
            pid=dead.pid,
            create_time=ct,
            pgid=None,
            engine="chromium",
            started_at=time.time(),
            owner_pid=os.getpid(),
        )
    )

    spawned = []
    monkeypatch.setattr(
        launcher_mod,
        "spawn_browser",
        lambda profile: spawned.append(profile.name) or _Proc(),
    )
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)
    bl = BrowserLauncher(registry=reg)
    bl.scan_survivors()

    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    assert spawned == ["alpha"], "a stale record must never block a launch"


def test_scanning_survives_an_unreadable_registry(tmp_path, monkeypatch):
    """A registry that cannot be read refuses nothing and does not crash
    startup."""
    path = tmp_path / "s.json"
    path.write_text("not json at all", encoding="utf-8")

    bl = _quiet_launcher(monkeypatch, SessionRegistry(str(path)))
    survivors, unknown = bl.scan_survivors()

    assert survivors == []
    assert unknown == []


def test_two_concurrent_launches_of_one_profile_spawn_only_one_browser(
    tmp_path, monkeypatch
):
    """THE CHECK AND THE RESERVATION ARE ONE ATOMIC STEP.

    Two browsers on a single profile directory is the defect this whole ticket
    exists to remove, and a restart is not the only way to reach it: two
    concurrent launches of ONE profile can reach it inside a single process. If
    the membership check and the `_starting` reservation happen in two separate
    acquisitions, both callers read "not running", both reserve, and both
    spawn.

    The window is not instruction-sized, which is why this is worth a test
    rather than a comment: the survivor probe sits between the two, and it does
    psutil/file IO. The delay injected below stands in for that IO — it does not
    manufacture the race, it makes an existing one observable instead of
    relying on thread scheduling.

    THE UI's is_loading FLAG DOES NOT COVER THIS. It serialises clicks on a
    card; the API and MCP lanes call start_thread directly and have no such
    flag — the same two lanes that motivate the guard living here at all.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))

    spawned: list[str] = []
    spawn_lock = threading.Lock()

    def _spawn(profile):
        with spawn_lock:
            spawned.append(profile.name)
        return _Proc()

    monkeypatch.setattr(launcher_mod, "spawn_browser", _spawn)
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher(registry=reg)

    # Stand in for the psutil IO the real probe performs, so both threads are
    # reliably inside the check->reserve window rather than depending on the
    # scheduler to interleave them.
    real_survivor_for = bl.survivor_for

    def _slow_survivor_for(name):
        time.sleep(0.2)
        return real_survivor_for(name)

    monkeypatch.setattr(bl, "survivor_for", _slow_survivor_for)

    both_ready = threading.Barrier(2)

    def _launch():
        both_ready.wait(timeout=5)
        bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    threads = [threading.Thread(target=_launch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a launch thread hung"

    assert spawned == ["alpha"], (
        "two concurrent launches of one profile spawned "
        f"{len(spawned)} browsers against a single profile directory: {spawned}"
    )


def test_a_survivor_refusal_releases_the_slot_it_reserved(tmp_path, monkeypatch):
    """A REFUSAL MUST NOT COST THE PROFILE ITS NEXT LAUNCH.

    The reservation is taken before the survivor probe, so the refusal path now
    owns a slot it has to give back. If it leaks, `alpha` sits in `_starting`
    for the life of the process and EVERY later launch is refused as a
    duplicate — a permanent lockout, which is the failure mode this ticket
    forbids in its strongest form: there is no gesture in the UI that clears it.

    So: refuse over a live survivor, let that survivor die, and assert the very
    next launch is allowed.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(make_record("alpha", proc, "chromium"))

    spawned: list[str] = []
    monkeypatch.setattr(
        launcher_mod,
        "spawn_browser",
        lambda profile: spawned.append(profile.name) or _Proc(),
    )
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher(registry=reg)
    bl.scan_survivors()

    try:
        bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)
        assert spawned == [], "the live survivor must have refused this launch"
    finally:
        proc.kill()
        proc.wait()

    assert "alpha" not in bl._starting, (
        "the refusal leaked its reservation: alpha is stuck in _starting and "
        "every future launch will be refused as a duplicate"
    )

    # The survivor is gone now, so the guard must let the user back in.
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    assert spawned == ["alpha"], (
        "a refusal cost the profile its next launch — this is the lockout"
    )


def test_a_probe_that_raises_allows_the_launch_and_leaks_no_slot(
    tmp_path, monkeypatch
):
    """AN UNANSWERABLE LIVENESS QUESTION FAILS OPEN, AND FAILS CLEAN.

    `liveness_of` is written to answer UNKNOWN rather than raise, but the guard
    must not depend on that discipline holding forever: an exception escaping
    the probe would otherwise propagate out of start_thread with the slot still
    reserved, which both refuses this launch and locks out every later one.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))

    spawned: list[str] = []
    monkeypatch.setattr(
        launcher_mod,
        "spawn_browser",
        lambda profile: spawned.append(profile.name) or _Proc(),
    )
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(BrowserLauncher, "_monitor_process", lambda *a, **k: None)

    bl = BrowserLauncher(registry=reg)

    def _boom(name):
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(bl, "survivor_for", _boom)

    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)

    assert spawned == ["alpha"], "an unanswerable probe must not refuse a launch"
