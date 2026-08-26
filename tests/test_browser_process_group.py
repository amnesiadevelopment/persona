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


# --------------------------------------------------------------------------
# THE PRODUCT LAUNCH PATH ITSELF (PS-192 round 2)
#
# Round 1 shipped a green suite while the product path still leaked 3/3. Every
# test above builds its handle with `popen_in_new_session`, so they pinned the
# HELPER and never `spawn_browser`'s actual launch shape. `spawn_browser`
# passed `start_new_session=True` BY HAND, which creates the session but never
# records the group — and `launcher.py:400` waits the leader on every launch,
# after which `getpgid` answers ESRCH and the teardown degrades to a
# single-process kill. The two shapes differ ONLY in whether the pgid was
# recorded at launch, which is why a test must look at the real one.
# --------------------------------------------------------------------------

def _fake_engine(tmp_path):
    """A WRAPPER that spawns children and EXITS — the fpchrome.AppImage shape.

    Only the wrapper shape leaks: chromium launched DIRECTLY honours SIGTERM
    and reaps its own tree. The defect lives in the shim layer, so the fixture
    has to be a shim or it measures a path with no defect.
    """
    script = tmp_path / "fake_engine.sh"
    script.write_text("#!/bin/sh\nfor i in 1 2 3; do sleep 300 & done\nexit 0\n")
    script.chmod(0o755)
    return str(script)


def _drive_spawn_browser(monkeypatch, tmp_path, engine):
    """Call the REAL `spawn_browser`, with only the engine binary swapped."""
    from src.models.profile import Profile
    from src.services.browser import process as _process

    class _Store:
        def get(self, *a, **k): return None
        def resolve(self, *a, **k): return None

    class _Bookmarks:
        def resolve_selection(self, *a, **k): return []

    monkeypatch.setattr(_process, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(_process, "ProxyStore", _Store)
    monkeypatch.setattr(_process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(_process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(_process, "seed_bookmarks", lambda *a, **k: None)
    monkeypatch.setattr(_process, "seed_profile_prefs", lambda *a, **k: None)
    monkeypatch.setattr(_process, "FINGERPRINT_CHROMIUM", engine)
    return _process, _process.spawn_browser(Profile(name="ps192-real"))


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_spawn_browser_records_its_process_group_at_launch(monkeypatch, tmp_path):
    # THE ROUND-1 GAP, stated directly: the recorded group must be present on a
    # handle built by `spawn_browser` ITSELF, not merely on one built by the
    # helper. Asserted AFTER the leader is waited on, because that is the
    # moment live re-resolution goes blind and the recorded value is the only
    # thing left that can address the orphans.
    from src.services.browser.process_group import recorded_group

    _process, proc = _drive_spawn_browser(
        monkeypatch, tmp_path, _fake_engine(tmp_path)
    )
    pgid = proc.pid
    try:
        proc.wait()  # exactly what launcher.py:400's wait_for_exit thread does
        assert group_of(proc.pid) is None, (
            "precondition: a waited-on leader must be unresolvable, or this "
            "test cannot tell a recorded group from a live lookup"
        )
        assert recorded_group(proc) == pgid, (
            "spawn_browser did not record its process group at launch — the "
            "teardown will degrade to a single-process kill and orphan the "
            "whole engine tree (measured 3/3 surviving in round 1)"
        )
    finally:
        _kill_leftovers(pgid)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_a_real_spawn_browser_launch_leaves_no_survivor(monkeypatch, tmp_path):
    # The ticket's bar (DoD #4) applied to the PRODUCT path and driven through
    # the REAL `process.terminate()`, with the leader waited on first — the
    # failure path DoD #3 is about, and the one round 1 still leaked on.
    _process, proc = _drive_spawn_browser(
        monkeypatch, tmp_path, _fake_engine(tmp_path)
    )
    pgid = proc.pid
    try:
        # THREE, not four: the wrapper spawns its children and exits at once
        # (that is the fpchrome.AppImage shape, and the whole reason the leak
        # exists), so the leader is already a zombie awaiting our wait() and is
        # correctly excluded from the survivor count. The three orphans-to-be
        # are what a single-process kill would leave behind.
        assert _settle(pgid, 3) == 3, "the engine tree did not come up (3 children)"
        proc.wait()

        _process.terminate(proc, "ps192-real", timeout=5)

        assert _settle(pgid, 0) == 0, (
            "processes survived a completed teardown of the PRODUCT launch "
            "path — this is the site PS-192 was filed for"
        )
    finally:
        _kill_leftovers(pgid)


# --------------------------------------------------------------------------
# PORTABILITY: the module's stated contract on a platform without killpg
# --------------------------------------------------------------------------

def test_a_recorded_group_still_falls_back_when_killpg_is_unavailable(monkeypatch):
    # The module docstring promises the reaper "degrades to proc.kill() where
    # [killpg] does not exist". It did not: `recorded_group` returns the
    # stashed attr WITHOUT checking killpg (only `resolve_group` guards it), so
    # a recorded handle took the group branch, `_signal_group` raised
    # AttributeError internally, swallowed it as False — and the process was
    # signalled by NEITHER path. Strictly worse than the code replaced.
    called = {"terminate": False, "kill": False}

    class _Handle:
        pid = 4321
        _persona_pgid = 4321  # recorded at launch, as the helper does
        def terminate(self): called["terminate"] = True
        def kill(self): called["kill"] = True
        def wait(self, timeout=None): return 0

    monkeypatch.delattr(os, "killpg", raising=False)

    hit_group = terminate_process_group(_Handle(), timeout=0.1)

    assert hit_group is False, (
        "no group can have been signalled without os.killpg"
    )
    assert called["terminate"] and called["kill"], (
        "a recorded-group handle was signalled NEITHER by killpg NOR by the "
        "documented terminate()/kill() fallback — the browser is left alive"
    )


def test_the_survivor_count_cannot_report_success_when_it_cannot_look(monkeypatch):
    # This is the MEASUREMENT'S OWN EVIDENCE FUNCTION, and PS-192's reviewer
    # measured the product path as CLEAN in a container with no psutil while
    # `ps` showed 3 live processes — a green from a broken instrument, on the
    # ticket about a leak that hides behind a green (PS-14). "Nothing survived"
    # and "I could not look" must never render as the same value.
    import builtins

    real_import = builtins.__import__

    def _no_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("simulated: psutil unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_psutil)

    with pytest.raises(RuntimeError, match="cannot measure"):
        process_group_survivors(os.getpgrp())


# --------------------------------------------------------------------------
# THE TOOLING PATH (tests/ui_driver/server.py) — PS-192 caution #2
#
# The processes observed at 361% CPU for 12.5h were `/usr/lib/chromium/chromium
# --headless`: the SYSTEM browser, i.e. agent TOOLING, not persona's engine. So
# this path gets the same measured bar as the product one. Its `stop()` runs a
# descendant WALK first and uses the group only as a backstop — but it resolved
# that group LIVE, and the kernel stops answering `getpgid` the moment the
# leader is waited on. The backstop was therefore None on precisely the branch
# whose premise is that the parent has ALREADY EXITED.
# --------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
def test_the_served_app_backstop_still_resolves_after_its_parent_exits(tmp_path):
    from tests.ui_driver.server import ServedApp

    # The wrapper shape: spawn children, exit at once. `serve_app`'s child is a
    # python process that starts flet, which starts its own children, and the
    # driven tests add a playwright node driver with a chromium behind it.
    proc = popen_in_new_session(
        ["/bin/sh", "-c", "for i in 1 2 3; do sleep 300 & done; exit 0"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pgid = proc.pid
    app = ServedApp(url="", home=str(tmp_path), port=0, process=proc)
    try:
        assert _settle(pgid, 3) == 3, "the tooling tree did not come up"
        proc.wait()  # the exited-parent branch stop() must handle

        app.stop()

        assert _settle(pgid, 0) == 0, (
            "processes survived the tooling teardown — this is the class "
            "observed at 361% CPU for 12.5h on a user's workstation"
        )
    finally:
        _kill_leftovers(pgid)
