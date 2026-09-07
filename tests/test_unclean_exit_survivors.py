"""A REAL persona is SIGKILLed mid-session, and the next start tells the truth.

WHY THIS FILE EXISTS, AND WHY IT IS NOT MORE OF ``test_launcher_survivors.py``.
That file's own module docstring names its boundary, and names it accurately::

    What it does NOT reproduce is a real browser outliving a real persona —
    that is exercised by hand on the user's path (PS-17), because no unit test
    crosses that boundary.

"Exercised by hand" is precisely the state PS-223 was in before it bit someone:
the record of which profiles had a browser running lived only in memory, so
after an unclean stop persona reported a live browser as not running and let the
operator launch a SECOND engine onto the same profile directory. This file
crosses that boundary automatically. Every test here forks a REAL child python
process that plays the part of persona, has it register a REAL long-lived
grandchild that plays the part of the engine, and then **SIGKILLs the persona**
— an ending that runs no code at all — before asking a fresh, genuinely new
process what it believes about what is alive.

THREE DELIBERATE CHOICES, EACH OF WHICH THE TICKET COULD HAVE GONE THE OTHER WAY
ON.

1. **SIGKILL, never a clean exit.** A clean shutdown runs ``atexit`` handlers,
   and a handler that tidies up proves nothing about the case this guard exists
   for. The whole question is what survives an ending that ran no code. This is
   the same reasoning ``env_policy.py`` uses to justify sweeping scratch at
   LAUNCH rather than at exit: the exit you must survive is the one that never
   happened. ``_kill_hard`` is the only teardown used on a persona here, and it
   is SIGKILL by construction — a SIGTERM would let Python run handlers and
   quietly turn every test in this file into a clean-exit test that still
   passed.

2. **The engine stand-in is a plain ``python -c 'sleep'``, not a browser.**
   What must be real is the PROCESS BOUNDARY — a pid that outlives its parent,
   is reparented to init, and can be probed by an unrelated process — and a
   sleeping python satisfies every one of those properties. A real chromium
   satisfies them no better while adding a 584 MB engine download, a display
   server, and (measured on PS-223) ~35 processes per launch to leak on
   failure. ⚠️ THE ENGINE ITSELF IS NOT WHAT IS UNDER TEST: the product's
   answer comes from ``liveness_of``, which asks psutil about a pid and knows
   nothing about what is executing on it.

3. **This is a test FILE, not a behavioural check.** ``src/services/verify/
   behaviour.py`` is the right home for checks that need a real engine, a real
   display and a scratch store, and the wrong home for this one: this needs
   none of the three, runs in under ten seconds, and — the deciding reason —
   the defect it guards is a defect in a MODULE, reachable from ``pytest``,
   which every PR already runs. A behavioural check is gated behind a separate
   workflow with an engine download, so putting it there would make the
   regression signal arrive later and less often for no gain. It is a NEW file
   rather than more of ``test_launcher_survivors.py`` because these tests are
   the only ones in the suite that fork a real persona, and their cleanup
   discipline (below) is a property of the file, not of an individual test.

⛔ THE ORPHAN HAZARD, AND WHY EVERY KILL GOES THROUGH ONE FUNCTION. A test about
processes that outlive their parent will, on any failure path, leave processes
that outlive their parent. Left alone they accumulate for the life of the
container and poison every later run on that machine — the exact accumulation
PS-192 recorded (a chromium burning 361% CPU for 12.5 hours). So every child is
registered with ``_Reaper``, which kills on the way out of the test whether it
passed, failed or errored, and ``_kill_hard`` REFUSES to signal pid 0, pid 1,
our own pid, or our own process group. That last refusal is not decoration:
``process_group.py`` records the same self-kill hazard, where a group kill aimed
at a non-leader resolves to the caller's own group and terminates the test
runner.

⛔ NO FABRICATED PIDS, ANYWHERE. Inherited verbatim from ``_Proc`` in
``test_launcher_survivors.py``: "a fabricated pid would either not exist (making
every record read GONE and every assertion vacuous) or, worse, belong to an
unrelated live process." Every pid in this file came from a process this file
actually started.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Long enough that the engine stand-in cannot expire mid-test and turn a
#: genuine ALIVE into a spurious GONE, short enough that a leaked one dies on
#: its own if every cleanup below somehow failed. It is a backstop for the
#: reaper, never the primary teardown.
_ENGINE_LIFETIME = 300

_SPAWN_TIMEOUT = 60


pytestmark = pytest.mark.skipif(
    not hasattr(signal, "SIGKILL"),
    reason="needs a POSIX SIGKILL: the whole point is an exit that runs no code",
)


def _kill_hard(pid: int | None) -> None:
    """SIGKILL ``pid`` if — and only if — it is safe to signal.

    THE CLEANUP MUST NOT BE ABLE TO KILL THE RUNNER. Four refusals, each
    guarding a way this could turn from a teardown into an outage:

    * ``None``/``<= 0`` — ``os.kill(0, SIGKILL)`` signals OUR ENTIRE PROCESS
      GROUP, and a negative pid signals a group by number. Nothing here ever
      wants a group.
    * ``1`` — init.
    * our own pid, and our own group leader — a test process reaping itself.

    SIGKILL rather than SIGTERM on purpose, in both roles it plays: as a
    teardown it must work on a wedged child, and as the simulated crash it must
    run no handler.
    """
    if pid is None or pid <= 1:
        return
    if pid == os.getpid() or pid == os.getpgrp():
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _await_exit(pid: int, timeout: float = 10.0) -> None:
    """Reap ``pid`` and wait until the OS agrees it is gone.

    Two separate obligations, and skipping either makes a later probe lie. The
    ``waitpid`` clears the ZOMBIE — a killed child whose parent has not reaped
    it keeps a resolvable pid, and this suite's whole subject is what a pid
    probe answers. The poll then waits for the kernel, because ``os.kill``
    returns before the process is actually torn down.
    """
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists(f"/proc/{pid}"):
            return
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


class _Reaper:
    """Every process this test started, killed on the way out — always.

    Registered EAGERLY, before the process is used for anything, so a failure
    between spawn and assertion cannot leak it. Used through a fixture whose
    teardown runs on pass, fail and error alike.
    """

    def __init__(self) -> None:
        self._pids: list[int] = []

    def watch(self, pid: int) -> int:
        self._pids.append(pid)
        return pid

    def reap_all(self) -> None:
        for pid in reversed(self._pids):
            _kill_hard(pid)
        for pid in reversed(self._pids):
            _await_exit(pid, timeout=5.0)
        self._pids.clear()


@pytest.fixture
def reaper():
    r = _Reaper()
    try:
        yield r
    finally:
        r.reap_all()


# --- the child persona -------------------------------------------------------

#: THE PROGRAM A "PERSONA" RUNS. It launches a real engine stand-in exactly the
#: way the product does — ``start_new_session=True``, so the child is a process
#: GROUP LEADER and ``recorded_group`` will accept it, which is the same
#: property ``spawn_browser`` gives a real browser — registers it through the
#: product's OWN ``make_record`` (so the create time is captured at
#: registration, from a live handle, exactly as in production), then reports and
#: waits to be killed.
#:
#: ⚠️ IT USES THE PRODUCT'S CODE, NOT A REIMPLEMENTATION OF IT. If this script
#: wrote the JSON itself, the test would assert that the test can write a file.
_PERSONA_SOURCE = textwrap.dedent(
    """
    import json, os, subprocess, sys, time

    sys.path.insert(0, sys.argv[1])
    registry_path = sys.argv[2]
    profile = sys.argv[3]
    lifetime = int(sys.argv[4])

    from src.services.browser.session_registry import SessionRegistry, make_record

    engine = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(%d)" % lifetime],
        start_new_session=True,
    )

    SessionRegistry(registry_path).record(make_record(profile, engine, "chromium"))

    print(json.dumps({"persona_pid": os.getpid(), "engine_pid": engine.pid}),
          flush=True)

    # Wait to be killed. No handler, no atexit, nothing to run: the point of
    # this process is to STOP without stopping cleanly.
    time.sleep(3600)
    """
)


def _launch_persona(reaper: _Reaper, registry_path: str, profile: str) -> dict:
    """Start a real child 'persona', and return its pid and its engine's pid.

    Blocks until the child has registered its engine, so a caller that goes on
    to kill it is guaranteed to be killing a persona that got as far as writing
    the record. Without that wait, a fast kill would produce an EMPTY registry
    and the test would assert nothing while passing.
    """
    proc = subprocess.Popen(
        [
            sys.executable, "-c", _PERSONA_SOURCE,
            REPO_ROOT, registry_path, profile, str(_ENGINE_LIFETIME),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    reaper.watch(proc.pid)

    # BOUNDED BY select, NOT BY A BARE readline. A child that starts and then
    # wedges before printing would block readline forever, and this file's
    # subject makes that failure especially expensive: the wedged persona is
    # holding a live engine grandchild, so a hang here leaks the very orphan
    # the file exists to avoid. The suite's own `timeout` ini key cannot be
    # relied on for this — conftest.py's report header prints it as INERT
    # whenever pytest-timeout is absent, which is the case in any environment
    # that installed only the project.
    import select

    ready, _, _ = select.select([proc.stdout], [], [], _SPAWN_TIMEOUT)
    if not ready:  # pragma: no cover - defensive
        raise AssertionError(
            f"the child persona did not register within {_SPAWN_TIMEOUT}s"
        )
    line = proc.stdout.readline()
    if not line:
        raise AssertionError(
            "the child persona produced no handshake — it died before "
            f"registering (returncode {proc.poll()})"
        )

    pids = json.loads(line)
    reaper.watch(pids["engine_pid"])
    assert pids["persona_pid"] == proc.pid
    return pids


def _crash(persona_pid: int) -> None:
    """END A PERSONA THE WAY THIS TICKET MEANS. SIGKILL: no atexit, no
    ``shutdown_all``, no registry cleanup — the process simply ceases."""
    _kill_hard(persona_pid)
    _await_exit(persona_pid)


def _fresh_launcher(registry_path: str):
    """A launcher in the state a NEWLY STARTED persona is in.

    Imported here rather than at module scope so the import cost is paid by the
    tests that need it, and constructed with an explicit registry so it reads
    the file the crashed child wrote rather than the suite-isolated default.
    """
    from src.services.browser.launcher import BrowserLauncher
    from src.services.browser.session_registry import SessionRegistry

    return BrowserLauncher(registry=SessionRegistry(registry_path))


# --- the tests ---------------------------------------------------------------


def test_a_browser_that_outlived_a_killed_persona_is_reported_as_running(
    tmp_path, reaper
):
    """THE BOUNDARY THE EXISTING SUITE SAYS IT CANNOT CROSS.

    A real persona, a real engine process, a real SIGKILL, and then a real
    question: does the next start know that browser is there? This is PS-223's
    user sequence with nothing simulated in the middle.

    THE ASSERTION IS ``is_running``, NOT THE FILE. What went wrong in PS-223 was
    what the product ANSWERED, not what it wrote; a test that inspected the JSON
    would pass over a persona that persisted a perfect record and still offered
    a second launch.
    """
    registry_path = str(tmp_path / "running_sessions.json")

    pids = _launch_persona(reaper, registry_path, "alpha")
    _crash(pids["persona_pid"])

    assert os.path.exists(f"/proc/{pids['engine_pid']}"), (
        "precondition: the engine must have OUTLIVED the persona, or this test "
        "is measuring nothing"
    )

    restarted = _fresh_launcher(registry_path)
    assert restarted.is_running("alpha") is False, (
        "a launcher that has not scanned must behave exactly as before"
    )

    survivors, unknown = restarted.scan_survivors()

    assert [r.profile for r in survivors] == ["alpha"], (
        "the browser is alive on this machine right now and the next persona "
        "does not know: this is the PS-223 double-launch, reproduced"
    )
    assert unknown == []
    assert restarted.is_running("alpha") is True
    assert restarted.survivor_for("alpha") is not None


def test_the_second_launch_onto_one_profile_directory_is_refused(
    tmp_path, reaper, monkeypatch
):
    """THE DEFECT ITSELF, END TO END — the operator's next click.

    The report is upstream of what actually hurt the user: two engines on ONE
    ``--user-data-dir``. So the check does not stop at "is_running is True"; it
    drives ``start_thread``, the entry point the API and MCP lanes use, and
    asserts that NO SECOND SPAWN HAPPENED.

    ``spawn_browser`` is stubbed to a recorder rather than to a browser: what is
    under test is whether the guard reaches the spawn at all, and a stub that
    records the attempt answers that exactly, without a second real engine to
    leak. The stub deliberately RAISES after recording, so a guard that fails to
    refuse cannot go on to register a phantom session — the recorded attempt is
    the whole finding, and the launch must not proceed past it.
    """
    import src.services.browser.launcher as launcher_mod
    from src.models.profile import Profile

    registry_path = str(tmp_path / "running_sessions.json")
    pids = _launch_persona(reaper, registry_path, "alpha")
    _crash(pids["persona_pid"])

    restarted = _fresh_launcher(registry_path)
    restarted.scan_survivors()

    spawned: list[str] = []

    def _record_and_refuse(profile):
        spawned.append(profile.name)
        raise AssertionError("a second engine was spawned onto a live profile dir")

    monkeypatch.setattr(launcher_mod, "spawn_browser", _record_and_refuse)
    monkeypatch.setattr(launcher_mod, "wait_for_exit", lambda *a, **k: None)
    monkeypatch.setattr(
        type(restarted), "_monitor_process", lambda *a, **k: None
    )

    logs: list[str] = []
    restarted.start_thread(Profile(name="alpha", os_type="windows"), logs.append)

    assert spawned == [], (
        "persona launched a SECOND engine onto a profile directory that "
        "already has one running — PS-223's user-facing failure"
    )
    assert any("already has a browser running" in m for m in logs), (
        "the refusal must SAY why, or the operator sees a click that did "
        "nothing"
    )


def test_a_record_whose_engine_also_died_resolves_to_gone(tmp_path, reaper):
    """THE NEGATIVE CASE, and the one that keeps the positive one honest.

    Kill the persona AND the engine. The record on disk is byte-identical in
    shape to the one above — same writer, same fields, same file — so anything
    that answered "running" from the record's PRESENCE passes the first test
    and fails this one.

    Failing this direction is not a lesser defect. A false positive here is a
    permanent LOCKOUT: persona refusing a profile whose browser is dead, on a
    machine where the engine itself would happily open it (measured on PS-223 —
    chromium recovers a stale ``SingletonLock`` and launches). That is stricter
    than the browser it launches, with no way out from the UI.
    """
    registry_path = str(tmp_path / "running_sessions.json")

    pids = _launch_persona(reaper, registry_path, "alpha")
    _crash(pids["persona_pid"])

    # And now the browser goes too — the user closes it, or the machine takes
    # the whole tree down. Reaped by the CHILD's parent (init), so we poll for
    # the pid's disappearance rather than waiting on a process we cannot wait
    # on.
    _kill_hard(pids["engine_pid"])
    deadline = time.time() + 10
    while time.time() < deadline and os.path.exists(f"/proc/{pids['engine_pid']}"):
        time.sleep(0.05)
    assert not os.path.exists(f"/proc/{pids['engine_pid']}"), (
        "precondition: the engine must really be gone"
    )

    restarted = _fresh_launcher(registry_path)
    survivors, unknown = restarted.scan_survivors()

    assert survivors == [], "a dead engine must not be reported as a survivor"
    assert unknown == []
    assert restarted.is_running("alpha") is False, (
        "a record of a browser that is GONE refused a launch — the lockout, "
        "which is the worse half of this defect"
    )


def test_a_killed_persona_leaves_a_record_a_new_process_can_actually_read(
    tmp_path, reaper
):
    """THE DURABILITY ITSELF, asserted where a reader can see it fail.

    Deliberately the ONE test here that looks at the file, and it is a
    PRECONDITION check rather than the product's answer: if the crash left
    nothing on disk, the three tests above would still be able to pass for the
    wrong reason on a future refactor that answered from some in-process cache.
    This pins the thing that must be true for them to mean anything.
    """
    from src.services.browser.session_registry import SessionRegistry

    registry_path = str(tmp_path / "running_sessions.json")
    pids = _launch_persona(reaper, registry_path, "alpha")
    _crash(pids["persona_pid"])

    records = SessionRegistry(registry_path).load()

    assert [r.profile for r in records] == ["alpha"]
    assert records[0].pid == pids["engine_pid"]
    assert records[0].create_time is not None, (
        "without a create time the record cannot discriminate pid reuse and "
        "every probe of it downgrades to UNKNOWN, which refuses nothing"
    )


def test_a_reused_pid_is_not_mistaken_for_the_browser_that_had_it(
    tmp_path, reaper
):
    """PID REUSE — the case the create-time check exists for, and the one a
    naive implementation gets wrong.

    A persisted pid is a NUMBER, and the OS hands numbers back out. So a record
    naming pid 4242 and an unrelated process that has since inherited 4242 are
    indistinguishable BY PID, and a guard that stops at "is something alive on
    this pid" refuses a launch on behalf of a stranger — the same permanent
    lockout as the stale-record case, in a costume that looks like evidence.

    ⭐ HOW REUSE IS CONSTRUCTED HERE, AND WHY THIS SHAPE. Both processes are
    REAL and both facts are REAL: ``old_create_time`` is measured from a
    process that genuinely lived and died, and the pid is a genuinely live
    process's. What is synthesised is only their PAIRING — that this pid now
    carries a process born later than the record says. That pairing is exactly
    and only what the OS produces when it reuses a pid, and it is what the
    probe must detect.

    THIS IS THE DISCRIMINATOR, NOT THE WRAP. Making the kernel actually hand
    back one specific number requires exhausting the whole pid space, which is
    MEASURED AND REAL but takes ~8 minutes — see the opt-in test below, which
    performs it. This one asserts the same property in milliseconds, so the
    protection is checked on every PR rather than only when someone opts in.

    THE GAP MUST EXCEED THE TOLERANCE, and that is a real property rather than
    a test detail: ``_CREATE_TIME_TOLERANCE`` exists because a create time
    round-tripped through JSON is a float, so an exact ``==`` would be fragile
    in the dangerous direction. A reuse INSIDE that window is genuinely
    indistinguishable and is not what this test claims to cover — measured
    here, a 0.27s gap correctly reads ALIVE. Real reuse is separated by a full
    traversal of the pid space, which is many orders of magnitude wider.
    """
    from src.services.browser.session_registry import (
        Liveness,
        SessionRecord,
        _CREATE_TIME_TOLERANCE,
        capture_create_time,
        liveness_of,
    )

    # A process that genuinely lived and genuinely died. Its create time is a
    # real measurement of a real process — never a fabricated float.
    departed = subprocess.Popen([sys.executable, "-c", "pass"])
    reaper.watch(departed.pid)
    old_create_time = capture_create_time(departed.pid)
    departed.wait()
    assert old_create_time is not None, "psutil could not measure a create time"

    # Wait past the tolerance so the two create times are DISTINGUISHABLE. A
    # margin, not the bare bound, so a clock-tick rounding cannot land the two
    # exactly on it.
    time.sleep(_CREATE_TIME_TOLERANCE + 0.5)

    # A different, live process. Its pid is real and it is running right now.
    inheritor = subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({_ENGINE_LIFETIME})"],
        start_new_session=True,
    )
    reaper.watch(inheritor.pid)

    reused = SessionRecord(
        profile="alpha",
        pid=inheritor.pid,          # real, and ALIVE
        create_time=old_create_time,  # real, and belongs to a DIFFERENT process
        pgid=None,
        engine="chromium",
        started_at=time.time(),
        owner_pid=os.getpid(),
    )

    assert liveness_of(reused) is Liveness.GONE, (
        "the probe answered ALIVE about a pid that is alive but is NOT the "
        "process the record named — persona would refuse this profile forever "
        "on behalf of an unrelated process"
    )

    # THE CONTROL, and without it the assertion above is vacuous: a probe that
    # answered GONE for everything would pass it. The SAME live pid, with its
    # OWN create time, must read ALIVE.
    genuine = SessionRecord(
        profile="alpha",
        pid=inheritor.pid,
        create_time=capture_create_time(inheritor.pid),
        pgid=None,
        engine="chromium",
        started_at=time.time(),
        owner_pid=os.getpid(),
    )
    assert liveness_of(genuine) is Liveness.ALIVE, (
        "control: the probe must still recognise a process that IS the one "
        "recorded, or the GONE above proves nothing"
    )

    # And the same discrimination through the PRODUCT's answer, not only
    # through the probe — the guard must not refuse on the reused pid.
    registry_path = str(tmp_path / "running_sessions.json")
    from src.services.browser.session_registry import SessionRegistry

    SessionRegistry(registry_path).record(reused)
    launcher = _fresh_launcher(registry_path)
    survivors, unknown = launcher.scan_survivors()

    assert survivors == []
    assert unknown == []
    assert launcher.is_running("alpha") is False, (
        "a reused pid blocked a launch — the lockout, reached through the "
        "one signal that was supposed to prevent it"
    )


@pytest.mark.skipif(
    os.environ.get("PERSONA_PID_WRAP_TEST") != "1",
    reason=(
        "exhausts the whole pid space (~8 min, measured): set "
        "PERSONA_PID_WRAP_TEST=1 to run the real-wrap pid-reuse test"
    ),
)
@pytest.mark.timeout(1800)
def test_real_os_pid_reuse_does_not_resurrect_a_dead_browser(tmp_path, reaper):
    """THE SAME CLAIM, WITH THE KERNEL ACTUALLY HANDING THE PID BACK.

    ⭐ MEASURED, NOT ASSUMED. This was run to completion on the CI-equivalent
    Linux container: ``pid_max`` 4,194,304, the allocator advanced at ~9,200
    pids/s by cycling threads, the counter wrapped at **t=+487s**, and a fork
    landed on the exact recorded pid at **t=+490s** — a genuinely different
    process wearing the number a dead "browser" used to have. The product's
    probe answered ``GONE``. So the create-time check is not merely reasoned
    about here; it has been defeated-tested against the real thing.

    WHY IT IS OPT-IN RATHER THAN DELETED, and why the fast test above is not
    considered a substitute for it but its stand-in. This is the only check in
    the repo where the pid reuse is performed by the OPERATING SYSTEM rather
    than by the test, so it is the only one that can catch a divergence between
    what we believe reuse looks like and what it is. But eight minutes of
    saturating the pid allocator is not something to put in every PR run, and
    it is environment-dependent in a way the fast test is not: a host with a
    small ``pid_max`` wraps in seconds, one with a large one may not finish,
    and a container without a writable ``ns_last_pid`` cannot shortcut it.

    ⚠️ IT IS ALSO NOT UNIVERSALLY CONSTRUCTIBLE, and the honest reading of a
    skip here is "unmeasured on this host", never "measured and fine".
    """
    from src.services.browser.session_registry import (
        Liveness,
        SessionRecord,
        capture_create_time,
        liveness_of,
    )

    last_pid_path = "/proc/sys/kernel/ns_last_pid"
    if not os.path.exists(last_pid_path):
        pytest.skip("no /proc/sys/kernel/ns_last_pid: cannot observe the allocator")

    def _counter() -> int:
        return int(open(last_pid_path).read().strip())

    departed = subprocess.Popen([sys.executable, "-c", "pass"])
    target = departed.pid
    recorded_create_time = capture_create_time(target)
    departed.wait()
    assert recorded_create_time is not None

    record = SessionRecord(
        profile="alpha",
        pid=target,
        create_time=recorded_create_time,
        pgid=None,
        engine="chromium",
        started_at=time.time(),
        owner_pid=os.getpid(),
    )
    assert liveness_of(record) is Liveness.GONE, "precondition: the pid is free"

    import threading

    def _burn(n: int) -> None:
        threads = [threading.Thread(target=lambda: None) for _ in range(n)]
        for t in threads:
            t.start()
            t.join()

    # PHASE 1 — COARSE. Cycle threads in bulk to drive the allocator all the
    # way around the pid space and back below the recorded pid. Bulk because
    # this is the long stretch (~4.19M pids at ~9,200/s, measured); precision
    # here would be wasted.
    deadline = time.time() + 1500
    wrapped = False
    while time.time() < deadline:
        current = _counter()
        if not wrapped and current < target:
            wrapped = True
        if wrapped and current >= target - 2000:
            break
        _burn(600)
    else:  # pragma: no cover - host-dependent
        pytest.skip("the pid allocator did not wrap within the time budget")

    # PHASE 2 — EXACT, and it closes in with FORKS rather than with a blind
    # walk, because the counter is SHARED. Every other process on the host is
    # allocating pids too, so a walk that reaches ``target - 1`` and then forks
    # can be overtaken between the two — and an overshoot is unrecoverable,
    # forfeiting the whole eight-minute wrap. Measured: a bare walk-then-fork
    # skipped itself twice with "could not land a process on the recorded pid"
    # while the wrap underneath it had succeeded both times.
    #
    # A fork is SELF-REPORTING: the pid it returns says exactly where the
    # allocator is, so each attempt re-reads the position instead of trusting a
    # reading that may already be stale. Each miss below the target is killed
    # and reaped immediately; a miss ABOVE it means the allocator has gone past
    # and this attempt is genuinely lost.
    inheritor = None
    overshot = False
    for _ in range(6000):
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            time.sleep(_ENGINE_LIFETIME)
            os._exit(0)
        if pid == target:
            inheritor = reaper.watch(pid)
            break
        _kill_hard(pid)
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass
        if pid > target and _counter() > target:  # pragma: no cover - lost race
            overshot = True
            break

    if inheritor is None:  # pragma: no cover - host-dependent
        pytest.skip(
            "could not land a process on the recorded pid"
            + (" (the allocator was overtaken by other processes on this host)"
               if overshot else "")
            + " — UNMEASURED on this host, which is NOT the same as measured "
            "and fine"
        )

    assert capture_create_time(target) != recorded_create_time, (
        "precondition: the process now on this pid must be a DIFFERENT one"
    )

    assert liveness_of(record) is Liveness.GONE, (
        "the OS reused the pid and the probe was fooled: persona would refuse "
        "this profile on behalf of a process it never launched"
    )


def test_two_crashed_personas_are_both_still_known_to_the_third(tmp_path, reaper):
    """ACCUMULATION ACROSS SEPARATE CRASHES.

    One record surviving one crash could be a single lucky write. Two personas
    crashed in sequence, each having registered a different profile, must BOTH
    be visible to a third — the second write must not have clobbered the first,
    and the second crash must not have taken the first's record with it.

    This is the shape an operator actually reaches: crashes are rarely a single
    isolated event, and a registry that holds only the most recent one silently
    hands back the double launch for every profile but that one.
    """
    registry_path = str(tmp_path / "running_sessions.json")

    first = _launch_persona(reaper, registry_path, "alpha")
    _crash(first["persona_pid"])
    second = _launch_persona(reaper, registry_path, "beta")
    _crash(second["persona_pid"])

    third = _fresh_launcher(registry_path)
    survivors, unknown = third.scan_survivors()

    assert sorted(r.profile for r in survivors) == ["alpha", "beta"]
    assert unknown == []
    assert third.is_running("alpha") is True
    assert third.is_running("beta") is True
