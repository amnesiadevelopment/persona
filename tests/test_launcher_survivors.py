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
persona — no test IN THIS FILE crosses that boundary.

⭐ THAT BOUNDARY IS NOW CROSSED AUTOMATICALLY, and this paragraph used to end
"that is exercised by hand on the user's path (PS-17), because no unit test
crosses that boundary." PS-348 retired the second half of that sentence:
``tests/test_unclean_exit_survivors.py`` forks a REAL child persona, has it
register a REAL surviving process, SIGKILLs the persona, and asks a genuinely
new process what it believes. Read the two files together — this one pins the
launcher's LOGIC cheaply and in milliseconds, that one pins the PROCESS
BOUNDARY the logic is only worth anything across.

⚠️ AND THE SPLIT IS LOAD-BEARING, not tidiness. Measured on PS-348 by making
the registry in-memory again (i.e. reproducing PS-223 exactly): all 17 tests in
THIS file passed, and so did all 75 across the whole launch-guard neighbourhood.
Only the cross-process file went red. So do not read a green run of this file as
evidence that the durability survives — by construction it cannot be.
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


class _ThreadArmProc:
    """The handle shape ``InvisibleProcess`` presents on its NON-FORK arm.

    ``pid = 0`` because there is no child process: the session runs on a THREAD
    of persona. ``needs_fork_launch()`` is ``IS_LINUX``, so this is the arm a
    WINDOWS or macOS host takes for a Firefox profile — and the shape is not
    asserted from this class but pinned against the real one by
    ``test_the_in_process_launch_arm_really_reports_no_pid`` below, so this
    stand-in cannot drift away from the product it stands in for.

    ``lines`` are the engine's own stdout, delivered one per ``readline`` and
    then BLOCKING (never EOF) until the handle is terminated — a session that
    is still on screen, which is the whole situation the restart guard is for.
    """

    def __init__(self, lines=()):
        self._done = threading.Event()
        self.returncode = None
        self.pid = 0
        self.stdout = _BlockingStdout(list(lines), self._done)

    def poll(self):
        return None if not self._done.is_set() else 0

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self._done.set()
        self.returncode = 0

    def kill(self):
        self.terminate()


class _BlockingStdout:
    """A stdout that yields queued lines and then STAYS OPEN.

    Returning "" after the queue drains would end the monitor loop, which on
    this path means the session is over — the opposite of the state under test.
    """

    def __init__(self, lines, done):
        self._lines = lines
        self._done = done

    def readline(self):
        if self._lines:
            return self._lines.pop(0) + "\n"
        self._done.wait(10)
        return ""

    def close(self):
        pass


def _quiet_launcher(monkeypatch, registry, proc=None, *, real_monitor=False):
    """A launcher whose spawn is stubbed and whose monitor threads do nothing.

    ``real_monitor`` keeps the PRODUCT's ``_monitor_process`` in place, for the
    one thing a stubbed monitor structurally cannot exercise: the durable record
    the in-process launch arm can only complete from the engine's own stdout
    (PS-353). Defaulted off, so every existing caller is byte-identical.
    """
    monkeypatch.setattr(
        launcher_mod, "spawn_browser", lambda profile: proc or _Proc()
    )
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    if not real_monitor:
        monkeypatch.setattr(
            BrowserLauncher, "_monitor_process", lambda *a, **k: None
        )
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


def test_a_clean_exit_keeps_the_record_of_a_browser_it_could_not_close(
    tmp_path, monkeypatch
):
    """A CLEAN QUIT MUST NOT ERASE THE GUARD OVER A BROWSER IT LEFT RUNNING.

    THE DEFECT THIS GUARDS, and it defeats outcome 1 on the most ordinary path
    there is — a normal quit. `shutdown_all` reaps `_active_sessions` and
    nothing else, so a SURVIVOR inherited from a previous persona is not killed
    by it. But it used to finish by wiping the WHOLE registry, which deleted
    the record of the very browser it had just failed to kill. The next persona
    then started with an empty file, found no survivor, and offered a second
    launch on that profile directory: the user's original bug, reached through
    the front door.

    Sequence: B adopts a live survivor -> B exits CLEANLY -> the browser is
    still alive -> C must still find it and still refuse.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    reg = SessionRegistry(str(tmp_path / "s.json"))
    reg.record(make_record("alpha", proc, "chromium"))

    try:
        b = _quiet_launcher(monkeypatch, SessionRegistry(reg.path))
        survivors, _ = b.scan_survivors()
        assert [r.profile for r in survivors] == ["alpha"], "precondition"
        assert "alpha" not in b._active_sessions, (
            "precondition: a survivor is NOT a session this run owns, which is "
            "exactly why shutdown_all cannot reach it"
        )

        # The clean/atexit path. It does not — and cannot — kill the survivor.
        b.shutdown_all()

        assert proc.poll() is None, (
            "precondition: shutdown_all did not kill the survivor, so the "
            "record of it is the only thing standing between the user and a "
            "second launch"
        )
        assert [r.profile for r in SessionRegistry(reg.path).load()] == ["alpha"], (
            "the clean exit erased the record of a browser that is STILL "
            "RUNNING — the next persona will offer a second launch on this "
            "profile directory"
        )

        c = _quiet_launcher(monkeypatch, SessionRegistry(reg.path))
        again, _ = c.scan_survivors()
        assert [r.profile for r in again] == ["alpha"], (
            "the guard did not survive a clean exit"
        )
        assert c.is_running("alpha") is True
    finally:
        proc.kill()
        proc.wait()


def test_a_clean_exit_still_forgets_the_sessions_it_did_reap(tmp_path, monkeypatch):
    """The complement, and the thing that keeps the fix above honest.

    Preserving a SURVIVOR's record must not turn into preserving EVERY record:
    a session this process launched and then reaped on the way out is genuinely
    gone, and its record goes with it. (A leftover record would be dropped at
    the next scan anyway — the guard is grounded in liveness, never in a
    record's presence — but leaving it would be an accumulating lie on disk.)
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)
    bl.start_thread(Profile(name="alpha", os_type="windows"), lambda m: None)
    assert [r.profile for r in reg.load()] == ["alpha"], "precondition"

    bl.shutdown_all()

    assert reg.load() == [], "a session we DID reap must not keep its record"


def test_closing_all_survivors_reports_the_one_that_would_not_die(
    tmp_path, monkeypatch
):
    """The exit dialog's promise has to be answerable, including when it fails.

    `close_all_survivors` is what makes the dialog's "closing persona will
    close the browser(s) for: ..." true for survivors, which `shutdown_all`
    structurally cannot reach. It returns the names that did NOT close so the
    caller can say so — and, crucially, a name that would not die KEEPS ITS
    RECORD, so the next start still guards it. Failing to close is survivable;
    failing to close and then forgetting is the defect.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    bl = _quiet_launcher(monkeypatch, reg)
    bl._survivors = {
        "alpha": SessionRecord(
            profile="alpha", pid=1, create_time=1.0, pgid=None,
            engine="chromium", started_at=0.0, owner_pid=0,
        ),
        "beta": SessionRecord(
            profile="beta", pid=2, create_time=2.0, pgid=None,
            engine="chromium", started_at=0.0, owner_pid=0,
        ),
    }
    for rec in bl._survivors.values():
        reg.record(rec)

    monkeypatch.setattr(
        launcher_mod, "terminate_record", lambda rec, **k: rec.profile != "beta"
    )

    stubborn = bl.close_all_survivors()

    assert stubborn == ["beta"], "the caller cannot warn about what it is not told"
    assert [r.profile for r in bl.survivors()] == ["beta"], (
        "the one that closed must be forgotten and the one that did not must "
        "still be held, or the guard forgets a live browser"
    )
    assert [r.profile for r in reg.load()] == ["beta"], (
        "a survivor we could not close must KEEP its record, or the next start "
        "loses the guard over a browser we promised to close and did not"
    )


# --------------------------------------------------------------------------
# PS-353 — THE IN-PROCESS (THREAD) LAUNCH ARM, whose durable record used to be
# written and then silently discarded.
#
# THE DEFECT, in one sentence: `InvisibleProcess` runs the Firefox session on a
# THREAD of persona wherever `needs_fork_launch()` is false (i.e. on a WINDOWS
# or macOS host), so the handle honestly reports `pid = 0`; `make_record` wrote
# that verbatim, `from_json`'s `pid <= 0` guard dropped it on the way back in,
# and the NEXT `record()` of any profile rebuilt the file without it. Every one
# of those lines is individually correct, which is why it survived review — the
# gap was at the seam, where the writer accepted a handle shape the persistence
# format cannot represent and NEITHER END KNEW.
#
# ⚠️ THESE TESTS RUN ON LINUX and the arm under test does not fire here, so
# the platform switch is DRIVEN EXPLICITLY rather than waited for: the handle
# shape is used directly in the launcher tests, and
# `test_the_in_process_launch_arm_really_reports_no_pid` pins that shape against
# the REAL `InvisibleProcess` with `needs_fork_launch()` forced false — so the
# stand-in cannot drift away from the product it stands in for.
#
# ⛔ THE FIX IS NOT TO RELAX `from_json`'s `pid <= 0` GUARD, and
# `test_an_unprobeable_record_is_still_refused_by_the_reader` pins that: an
# unprobeable record would load, probe UNKNOWN, and be read by
# `running_session_builds()` as "live, build unknown, do not prune" — deferring
# an engine prune forever.
# --------------------------------------------------------------------------

_WATCH = "LIFECYCLE watch-pids pids=[%d]"


def test_the_in_process_launch_arm_really_reports_no_pid(monkeypatch):
    """THE PREMISE, MEASURED ON THE PRODUCT rather than assumed.

    `_ThreadArmProc` above claims to be the shape `InvisibleProcess` presents on
    its non-fork arm. This is what makes that claim checkable: the real class,
    with `needs_fork_launch()` forced FALSE — which is precisely what a Windows
    or macOS host reports — must take the thread path and carry pid 0.

    THE SWITCH IS DRIVEN, NOT WAITED FOR. This suite runs on Linux, where the
    arm never fires on its own, so a test that merely launched would exercise
    the fork path and pass while establishing nothing. Forcing the platform
    predicate reproduces the host decision at the one line that makes it.

    Safe to run anywhere, and deliberately NOT guarded by the fork-path skipif
    in test_browser_process_group.py (which exists because that path's stand-in
    child calls `os._exit(0)`, killing pytest outright where the thread arm is
    taken). Here `_child` is replaced by a no-op and the thread arm's `_finish`
    never calls `os._exit` at all — the class checks `in_thread` for exactly
    that reason.
    """
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il._platform, "needs_fork_launch", lambda: False)
    monkeypatch.setattr(il, "_child", lambda cfg, wf, stop_event=None: None)

    proc = il.InvisibleProcess({})
    try:
        assert proc._fork is False, (
            "this test must exercise the THREAD arm; it took the fork path, so "
            "it establishes nothing about the platform under test"
        )
        assert proc.pid == 0, (
            "the premise of PS-353 has been overtaken: the thread arm now "
            "carries a pid, so the record is no longer unrepresentable"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_an_unprobeable_record_is_still_refused_by_the_reader(tmp_path):
    """THE FIX MUST NOT BE 'LET pid 0 THROUGH'.

    Relaxing `from_json` would make such a record LOAD, probe UNKNOWN (pid 0 is
    not probeable), and land in the indeterminate bucket — which
    `running_session_builds()` reads as "live, build unknown, do not prune",
    deferring an engine prune forever on a row that names nothing.

    So the guard stays, and this pins it: hand-write the row onto disk, past
    every product writer, and the reader must still drop it.
    """
    import json

    path = tmp_path / "s.json"
    path.write_text(json.dumps({
        "version": 1,
        "sessions": [{
            "profile": "ff-thread", "pid": 0, "create_time": None, "pgid": None,
            "engine": "firefox", "started_at": 0.0, "owner_pid": 0,
        }],
    }), encoding="utf-8")

    assert SessionRegistry(str(path)).load() == [], (
        "an unprobeable record must not load: it would be read as an "
        "indeterminate live session and defer engine pruning forever"
    )


def test_a_restarted_persona_reports_a_thread_arm_browser_as_running(
    tmp_path, monkeypatch
):
    """THE DEFECT, ASSERTED THROUGH WHAT THE PRODUCT ANSWERS.

    A Firefox profile launched from a Windows/macOS host takes the thread arm,
    so its handle has no pid. Before the fix that session got NO durable record
    at all, and a restarted persona answered "not running" about a browser alive
    on screen — the exact sentence PS-223 was filed on, still true on the
    platforms the thread arm fires on.

    The engine's own close-watch resolves the profile's REAL firefox processes a
    moment after launch and announces them on the same pipe every other
    LIFECYCLE line travels on. That is the first honest moment a record is
    possible, and this asserts the product uses it: launch, let the monitor read
    the engine's report, then ask a FRESH launcher over the same registry file.

    The pid announced is a REAL live process (a sleeper this test owns), because
    a fabricated one would either not exist — making every assertion vacuous by
    probing GONE — or belong to something unrelated.
    """
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        reg = SessionRegistry(str(tmp_path / "s.json"))
        proc = _ThreadArmProc([_WATCH % engine.pid])
        bl = _quiet_launcher(monkeypatch, reg, proc=proc, real_monitor=True)

        bl.start_thread(Profile(name="fox", os_type="windows"), lambda m: None)

        deadline = time.time() + 5
        while time.time() < deadline and not reg.load():
            time.sleep(0.02)

        # THE RESTART: a second launcher over the same file, exactly as the
        # rest of this suite models it. Its dicts are empty, so it can only
        # answer from what was persisted.
        restarted = _quiet_launcher(monkeypatch, SessionRegistry(reg.path))
        survivors, unknown = restarted.scan_survivors()

        assert restarted.is_running("fox") is True, (
            "a restarted persona reports the profile as NOT running while its "
            "browser is alive on screen — the user is offered a second launch "
            "on the same profile directory"
        )
        assert restarted.survivor_for("fox") is not None, (
            "the UI has nothing to warn with"
        )
        assert [r.profile for r in survivors] == ["fox"]
        assert unknown == []
        # Corroboration only — the assertions above are the product's answer.
        assert [(r.profile, r.pid) for r in reg.load()] == [("fox", engine.pid)]
    finally:
        proc.terminate()
        engine.kill()
        engine.wait()


def test_a_thread_arm_launch_that_never_reports_a_pid_says_so(
    tmp_path, monkeypatch
):
    """A WRITER THAT CANNOT REPORT ITS OWN NO-OP IS THE DEFECT, not a detail.

    Where the engine never resolves a pid there is genuinely nothing probeable
    to record, and the honest outcome is an EMPTY registry plus a statement —
    never a row written and silently discarded, which is what shipped. The
    ticket's roadmap names this failure mode exactly: not a mechanism that does
    not work, but a check that cannot fail.

    The launch itself must be untouched: provenance is a by-product of
    launching, never a precondition for it.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    proc = _ThreadArmProc()  # the engine reports no pids at all
    logs: list[str] = []
    bl = _quiet_launcher(monkeypatch, reg, proc=proc, real_monitor=True)
    try:
        bl.start_thread(Profile(name="fox", os_type="windows"), logs.append)

        assert bl.is_running("fox") is True, (
            "the session must still be launched and tracked — a record that "
            "cannot be written must cost the guard, never the browser"
        )
        assert reg.load() == [], (
            "an unprobeable row must not be written: the reader drops it and "
            "the next record() erases it, so it is a no-op that also destroys "
            "the evidence of itself"
        )
        assert any("restart guard is pending" in m for m in logs), (
            "the no-op was silent — the operator has no way to learn that this "
            "session has no durable guard"
        )
    finally:
        proc.terminate()


def test_a_thread_arm_record_does_not_outlive_the_session_that_needed_it(
    tmp_path, monkeypatch
):
    """A PENDING RECORD MUST NOT BE COMPLETED AFTER THE SESSION IS OVER.

    The repair is deferred to a line the engine emits later, which opens a
    window this closes: if the session ends first, a stale `watch-pids` line
    must not write a durable record asserting a browser that is no longer
    running. That is the stale-record shape the whole module is built to avoid,
    and it would refuse the user's NEXT launch.
    """
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        reg = SessionRegistry(str(tmp_path / "s.json"))
        proc = _ThreadArmProc()
        bl = _quiet_launcher(monkeypatch, reg, proc=proc, real_monitor=True)
        bl.start_thread(Profile(name="fox", os_type="windows"), lambda m: None)
        assert "fox" in bl._pending_record, "precondition: the record is pending"

        bl.stop_profile("fox")

        assert "fox" not in bl._pending_record, (
            "a session that ended still has a record pending; a later engine "
            "line would write a durable record for a browser that is gone"
        )
        assert reg.load() == []
    finally:
        proc.terminate()
        engine.kill()
        engine.wait()


def test_the_fork_and_popen_arm_is_untouched(tmp_path, monkeypatch):
    """THE CONTROL, and it is load-bearing.

    Without it these tests read as "the registry is fragile". With it, the
    discard is located precisely at the one handle shape that carries no pid:
    the chromium arm (and the Linux Firefox fork arm) go through a real Popen,
    yield a real pid, and must record at SPAWN exactly as they always did —
    nothing pending, no engine line needed, the record on disk immediately.
    """
    reg = SessionRegistry(str(tmp_path / "s.json"))
    proc = _Proc()  # a real pid, the fork/Popen shape
    bl = _quiet_launcher(monkeypatch, reg, proc=proc)

    bl.start_thread(Profile(name="cr", os_type="windows"), lambda m: None)

    assert [(r.profile, r.pid) for r in reg.load()] == [("cr", proc.pid)], (
        "the arm that carries a real pid must record at spawn, unchanged"
    )
    assert bl._pending_record == {}, (
        "a handle with a probeable pid has nothing to defer"
    )


def test_the_engine_pid_parser_reads_only_the_line_that_carries_pids():
    """THE MATCHER, PINNED — because it reads strings another module emits.

    `engine_pid_from` matches `LIFECYCLE watch-pids` and NOTHING ELSE, and each
    negative here is a line invisible_launch.py really emits:

    * `watch-pid pid=N` is the FORK path's singular form. That arm records a
      real pid at spawn and has nothing pending, so matching it would be a
      branch that can never usefully fire — and, if it ever did, would rewrite
      a good record from a line meant for a different arm.
    * `close=` and `teardown-kill` lines ALSO carry a `pids=[...]` list. They
      describe a session that is ENDING, and completing a durable record from
      one would assert a running browser at the moment it stopped running.

    A loose matcher here is not a cosmetic problem: it is the stale-record
    shape the whole module is built to avoid.
    """
    from src.services.browser.launcher import engine_pid_from

    assert engine_pid_from("LIFECYCLE watch-pids pids=[1234, 5678]") == 1234, (
        "the lowest pid is the deterministic choice and the likeliest parent"
    )
    assert engine_pid_from("LIFECYCLE watch-pids pids=[42]") == 42

    # An empty set carries no pid: "not a usable line", never a pid of 0.
    assert engine_pid_from("LIFECYCLE watch-pids pids=[]") is None

    assert engine_pid_from("LIFECYCLE watch-pid pid=99") is None
    assert engine_pid_from("LIFECYCLE close=window-gone pids=[1, 2]") is None
    assert engine_pid_from("LIFECYCLE teardown-kill pids=[7] rescan=False") is None
    assert engine_pid_from("BROWSER_STARTED") is None
