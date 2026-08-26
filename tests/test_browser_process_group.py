"""PS-192: no process survives a completed run, and the fix cannot kill its caller.

WHAT IS PINNED HERE, AND WHY IT IS NOT A MOCK TEST
--------------------------------------------------
The property this ticket asserts is **"a completed run leaves no surviving
process"**. A test that asserts a reaper was CALLED does not establish that
(PS-11): the leak these tests exist to catch is precisely a teardown that runs,
returns cleanly, and leaves ~35 processes alive.

So every test below **starts real process trees and counts real survivors**.
The parent/child/grandchild shape mirrors what actually leaks — chromium's
zygote + gpu-process + renderers under a wrapper (``fpchrome.AppImage``), and
flet + the playwright node driver under the UI-driver tooling — using
``sys.executable`` sleepers, which need no browser and run anywhere.

Counting is anchored on the **process group or on recorded pids**, never on a
command-line substring: PS-185's worker lost two cycles to a ``pkill -f
chromium`` that matched its own command line.

THE SECOND PROPERTY, WHICH IS EASY TO FORGET
--------------------------------------------
A group kill aimed at a process that is NOT a group leader resolves to the
CALLER's group. The fix's blast radius would then exceed the leak: persona, the
test runner, or the agent goes down. Several tests here exist only to prove the
refusals hold — including on a **fabricated pid**, because two fakes in this
suite carry a hardcoded ``pid = 4242``.
"""

import os
import subprocess
import sys
import time

import pytest

from src.services.browser.process_group import (
    group_of,
    popen_in_new_session,
    process_group_survivors,
    reap_process_group,
    resolve_group,
    start_own_session,
    terminate_process_group,
)

pytestmark = pytest.mark.timeout(120)

# A child that outlives any test unless something kills it. Long enough that a
# survivor is unambiguously a LEAK rather than a race against natural exit.
_SLEEP = "import time; time.sleep(300)"

# parent -> N children -> N grandchildren each. The shape that leaks: killing
# the parent alone reparents the rest to init, where no handle reaches them.
_TREE = (
    "import subprocess, sys, time;"
    "[subprocess.Popen([sys.executable, '-c', "
    "'import subprocess,sys,time;"
    "[subprocess.Popen([sys.executable,\"-c\",\"import time; time.sleep(300)\"]) "
    "for _ in range(2)];"
    "time.sleep(300)']) for _ in range(2)];"
    "time.sleep(300)"
)


def _spawn_tree(*, session: bool = True):
    """Start the leaking tree shape. ``session=False`` reproduces the DEFECT.

    The two branches are the whole point of this file: ``session=True`` is the
    sanctioned launch (own group, pgid recorded), and ``session=False`` is a
    bare ``Popen`` exactly as every site in PS-192's table did it before the
    fix. The negative control needs the second, or it would be testing the fix
    against itself.
    """
    if not session:
        return subprocess.Popen(
            [sys.executable, "-c", _TREE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return popen_in_new_session(
        [sys.executable, "-c", _TREE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _settle(pgid, want, timeout=15.0):
    """Wait until the group reaches ``want`` members, then return the count.

    Polls rather than sleeping a fixed interval so a slow box does not make a
    correct reap look like a failed one, and a fast one does not pass by luck.
    """
    deadline = time.monotonic() + timeout
    seen = len(process_group_survivors(pgid))
    while time.monotonic() < deadline:
        seen = len(process_group_survivors(pgid))
        if seen == want:
            return seen
        time.sleep(0.1)
    return seen


def _kill_leftovers(pgid):
    """Belt-and-braces cleanup so a FAILING test cannot itself leak a tree."""
    if pgid is None:
        return
    try:
        os.killpg(pgid, 9)
    except Exception:
        pass


# --------------------------------------------------------------------------
# THE PROPERTY: a completed teardown leaves NOTHING alive
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_a_reaped_launch_leaves_no_surviving_process():
    # THE ticket's bar, stated as a count rather than as a call. Seven
    # processes go in (1 + 2 + 4); zero come out.
    proc = _spawn_tree()
    pgid = resolve_group(proc.pid)
    try:
        assert pgid == proc.pid, "the launch did not become its own group leader"
        assert _settle(pgid, 7) == 7, "the tree did not come up as expected"

        reap_process_group(proc, timeout=10)

        assert _settle(pgid, 0) == 0, (
            "processes survived a completed teardown — the exact leak PS-192 "
            "exists to close (~35/launch measured, 361% CPU for 12.5h observed)"
        )
    finally:
        _kill_leftovers(pgid)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_without_a_session_terminate_orphans_the_tree_which_is_the_defect():
    # THE NEGATIVE CONTROL, and the reason the other tests are not vacuous.
    # It reproduces the ORIGINAL defect: no start_new_session, plain
    # terminate() — and asserts the children SURVIVE. Without this, every test
    # above would also pass against a build that never leaked, and none of them
    # would prove the fix does anything.
    proc = _spawn_tree(session=False)  # deliberately NO start_new_session
    kids = []
    try:
        psutil = pytest.importorskip("psutil")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                kids = psutil.Process(proc.pid).children(recursive=True)
            except Exception:
                kids = []
            if len(kids) >= 6:
                break
            time.sleep(0.1)
        assert len(kids) >= 6, "the tree did not come up as expected"

        proc.terminate()  # the OLD teardown: signals the direct child only
        proc.wait(timeout=10)

        time.sleep(1.5)
        alive = [k for k in kids if k.is_running()
                 and k.status() != psutil.STATUS_ZOMBIE]
        assert alive, (
            "the un-sessioned control did NOT leak, so these tests cannot "
            "distinguish a fixed build from a broken one"
        )
    finally:
        for k in kids:
            try:
                k.kill()
            except Exception:
                pass


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_teardown_reaps_even_when_the_parent_already_exited():
    # DoD #3: the failure paths are where a leak of this size accumulates. A
    # wrapper that has already handed off and exited reads as "nothing to do"
    # to a poll()-guarded teardown, while its children ARE the leak. The
    # parent's exit status is not evidence about its descendants.
    code = (
        "import subprocess, sys;"
        "[subprocess.Popen([sys.executable, '-c', "
        f"{_SLEEP!r}]) for _ in range(3)]"
    )  # spawns 3 children then EXITS IMMEDIATELY
    proc = popen_in_new_session(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pgid = proc.pid  # captured before the wait; the group outlives its leader
    try:
        proc.wait(timeout=15)
        assert proc.poll() is not None, "the parent should have exited on its own"
        assert _settle(pgid, 3) == 3, "the orphaned children are not there to reap"

        reap_process_group(proc, timeout=10)

        assert _settle(pgid, 0) == 0, (
            "an exited parent's children survived; a teardown that returns "
            "early on poll() is how this leak accumulates on failure paths"
        )
    finally:
        _kill_leftovers(pgid)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_repeated_launches_leave_nothing_behind_across_a_run():
    # DoD #4: measured across a RUN of several launches, not one. A per-launch
    # residue is what compounds into 35 processes and an exhausted machine —
    # and a single-launch test cannot see an accumulation.
    pgids = []
    try:
        for _ in range(3):
            proc = _spawn_tree()
            pgid = resolve_group(proc.pid)
            pgids.append(pgid)
            assert _settle(pgid, 7) == 7
            reap_process_group(proc, timeout=10)
            assert _settle(pgid, 0) == 0

        total = sum(len(process_group_survivors(p)) for p in pgids)
        assert total == 0, (
            f"{total} processes survived across three launches; the bar is "
            "that a completed run leaves NONE"
        )
    finally:
        for p in pgids:
            _kill_leftovers(p)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_reaping_twice_is_safe_and_still_leaves_nothing():
    # Teardown runs from `finally` blocks and can legitimately be reached more
    # than once (close() then __exit__). The second call must not raise, and
    # must not signal a recycled pid.
    proc = _spawn_tree()
    pgid = resolve_group(proc.pid)
    try:
        assert _settle(pgid, 7) == 7
        reap_process_group(proc, timeout=10)
        reap_process_group(proc, timeout=10)  # must be a no-op, not an error
        assert _settle(pgid, 0) == 0
    finally:
        _kill_leftovers(pgid)


# --------------------------------------------------------------------------
# THE SELF-KILL GUARDS — the fix must not be able to kill its own caller
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_a_child_without_its_own_session_is_never_group_signalled():
    # A child launched WITHOUT start_new_session sits in the CALLER's group.
    # os.killpg on it would take down persona / the test runner / the agent.
    # The refusal is what makes reap_process_group safe to call unconditionally
    # — including on a legacy handle that never got a session.
    child = subprocess.Popen([sys.executable, "-c", _SLEEP])
    try:
        assert group_of(child.pid) == os.getpgrp(), (
            "precondition: the child should share our group"
        )
        assert resolve_group(child.pid) is None, (
            "a non-leader pid resolved to a signallable group; killpg on it "
            "would signal OUR OWN group"
        )

        # The reap still works — it just uses the single-process fallback.
        hit_group = terminate_process_group(child, timeout=10)
        assert hit_group is False, "no group should have been signalled"
        assert child.poll() is not None, "the fallback must still kill the child"
    finally:
        try:
            child.kill()
        except Exception:
            pass


def test_a_fabricated_pid_is_refused_rather_than_resolved():
    # Two fakes in this suite carry a hardcoded `pid = 4242`. Without the
    # leader check, a teardown would resolve whatever real process happens to
    # hold that pid and signal ITS group — processes this call was never given
    # authority over. Same self-inflicted class as `pkill -f` matching itself.
    for pid in (4242, 999_999_999, 0, -1):
        assert resolve_group(pid) in (None, pid), (
            f"pid {pid} resolved to a foreign group"
        )
    # 4242 specifically: refused unless it genuinely is its own leader.
    if group_of(4242) not in (None, 4242):
        assert resolve_group(4242) is None


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_reaping_a_handle_with_no_usable_pid_does_not_raise():
    # Teardown must never be the thing that breaks a failing path.
    class _NoPid:
        pid = None
        def terminate(self): pass
        def kill(self): pass
        def wait(self, timeout=None): return 0

    assert reap_process_group(_NoPid()) is False
    assert reap_process_group(None) is False


# --------------------------------------------------------------------------
# The forked-child seam (invisible_launch's Linux path)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "setsid"), reason="POSIX sessions")
def test_start_own_session_makes_a_forked_child_its_own_leader():
    # `start_new_session=True` is a Popen kwarg and is therefore unavailable to
    # invisible_launch's multiprocessing fork path, which calls this instead.
    # Verified in a REAL forked child: doing it in-process would move the test
    # runner's own session.
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child branch
        try:
            os.close(r)
            ok = start_own_session()
            os.write(w, b"1" if (ok and os.getpgid(0) == os.getpid()) else b"0")
            os.close(w)
        finally:
            os._exit(0)
    os.close(w)
    try:
        answer = os.read(r, 1)
        os.waitpid(pid, 0)
        assert answer == b"1", "the forked child did not become its own leader"
    finally:
        os.close(r)
