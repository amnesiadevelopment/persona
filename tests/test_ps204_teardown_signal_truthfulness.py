"""PS-204: the teardown's two FALSE SIGNALS — deferred from PS-192.

PS-192 fixed the process-group LEAK and is out of scope here (do not re-measure
it). This ticket owns two adjacent defects where the REPORTING lies, and they
pull in OPPOSITE directions, so they are pinned as two independent findings:

  A. ``close=all-pids-exit`` OVER-reports success. The claim quantified over a
     pid set captured ONCE and never widened, so it could not become false about
     a child that appeared after the capture — a completion signal structurally
     unable to report incompletion.

  B. ``reap_process_group`` UNDER-reports success. On a fork-path
     ``InvisibleProcess`` it returned False on a call where a group signal WAS
     delivered, because the recorded pgid was stashed on the inner
     ``mp.Process`` and the generic reaper looks on the HANDLE.

⚠️ ANCHOR CORRECTION CARRIED IN CODE. The ticket attributes Defect A to
``_fork_close_watch``. It is not there. At the commit this was written against,
``close=all-pids-exit`` is emitted at ``invisible_launch.py:3709`` inside
``_thread_close_watch`` — the Windows/macOS THREAD path — and ``_fork_close_watch``
never emits it at all (grep: two hits in the whole tree, the emit and the
``_QUIET_CLOSE_REASONS`` membership). The field evidence FITS the correction: the
reporting user was on v3.0.1 **Windows**, which is the thread path. Tests below
drive ``_thread_close_watch`` for that reason.

HOW THESE TESTS BIND — THE POINT OF THE TICKET
-----------------------------------------------
A signal that cannot go false is the defect, so **a test that cannot go red does
not close this** (PS-11). Nothing below asserts that a function was called or
that a line was emitted. Each test constructs the state the old code could not
describe and asserts the OUTPUT CHANGES.

THE PRECONDITION IS ASSERTED, NEVER ASSUMED. A negative control that comes back
clean means the harness never established the precondition — not that the old
code was fine (PS-192's QA seat lost two cycles to exactly this, and its reviewer
measured the product CLEAN in a container where psutil was missing while ``ps``
showed three live processes). For Defect A the precondition is *a real child that
is alive and was NOT in the captured set*; every test that depends on it asserts
it against the OS, from the test's own side, before drawing a conclusion.
"""

import os
import subprocess
import sys
import threading
import time

import pytest

from src.core import platform as _platform
from src.services.browser import invisible_launch as il
from src.services.browser.process_group import group_of

pytestmark = pytest.mark.timeout(120)

# Long enough that a survivor is unambiguously a survivor and not a race
# against natural exit — the same bar tests/test_browser_process_group.py uses.
_SLEEP = "import time; time.sleep(300)"


def _spawn_orphan_under(parent_alive_marker):
    """A real child process, started by the test, that outlives the watch."""
    return subprocess.Popen(
        [sys.executable, "-c", _SLEEP],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _reap(proc):
    for fn in ("kill", "wait"):
        try:
            getattr(proc, fn)()
        except Exception:
            pass


def _run_thread_close_watch(
    monkeypatch, *, tracked, alive_pids, descendants_seq, die_after=None
):
    """Drive the REAL ``_thread_close_watch`` to its completion claim.

    Only the platform PROBES are stubbed — the pid resolver, the liveness
    check, the descendant scan and the window enumeration. The watch's own
    logic (what it tracks, when it claims, and what the claim says) is the
    real code under test.

    ``descendants_seq`` is what the scan returns on successive polls (the last
    entry repeats), so a test can make the tree LOOK one way while the parent
    lives and another way after it dies — which is the whole point: a child
    reparented to init is no longer a descendant of anything we hold.

    ``die_after`` kills the tracked pids after that many scans, reproducing the
    real sequence (parent alive for a while, then gone) rather than a state
    that never closes.
    """
    log: "list[str]" = []
    state = {"polls": 0}

    # ⚠️ THE CLOCK IS DRIVEN FROM `_pid_alive`, NOT FROM THE DESCENDANT SCAN,
    # AND THAT IS LOAD-BEARING FOR THE FALSIFICATION RUN.
    #
    # It was the other way round first, and the falsification exposed it: with
    # the fix removed there is no `_session_descendants` call at all, so the
    # stub driving `die_after` never fired, the tracked pid stayed alive
    # forever, and the test failed with "the close-watch never reached a
    # verdict" — a TIMEOUT. That is a red for a reason that says nothing about
    # the property, and a harness whose clock lives inside the code under test
    # cannot measure that code's absence (PS-14: check the instrument before
    # attributing anything to the product).
    #
    # `_pid_alive` is called by the completion claim on EVERY version of this
    # function — before the fix and after it — so a counter here advances
    # identically in both, and the only thing that changes between runs is what
    # the claim SAYS. Callers using `die_after` therefore track a single pid, so
    # one call == one poll.
    def _alive(p):
        state["polls"] += 1
        if die_after is not None and state["polls"] > die_after:
            for t in tracked:
                alive_pids.discard(t)
        return p in alive_pids

    def _descendants(roots):
        i = min(state["polls"], len(descendants_seq) - 1)
        res = descendants_seq[i]
        return None if res is None else set(res)

    monkeypatch.setattr(il, "_profile_firefox_pids", lambda d: set(tracked))
    monkeypatch.setattr(il, "_pid_alive", _alive)
    monkeypatch.setattr(il, "_session_descendants", _descendants)
    # No window verdict, and no content-proc verdict either: this test is about
    # the all-pids/tracked-pids claim, so every OTHER close route is held off.
    monkeypatch.setattr(il, "_pids_have_visible_window", lambda pids: None)
    monkeypatch.setattr(il, "_firefox_content_proc_count", lambda d, parent=None: None)

    closed = threading.Event()
    done = threading.Event()
    result = {}

    def _go():
        try:
            result["pids"] = il._thread_close_watch(
                "/tmp/profile-ps204",
                closed,
                None,
                lambda: None,
                no_process_timeout=5.0,
                interval=0.05,
                log=log.append,
            )
        finally:
            done.set()

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    assert done.wait(20), "the close-watch never reached a verdict"
    return log, result.get("pids")


def _close_line(log):
    for line in log:
        if "LIFECYCLE close=" in line:
            return line
    return ""


# --------------------------------------------------------------------------
# DEFECT A — the completion claim must be able to become FALSE.
# --------------------------------------------------------------------------

def test_the_completion_claim_reports_a_child_that_outlived_the_tracked_set(
    monkeypatch,
):
    """⭐ THE MUTATION TEST. DoD #2 and #3.

    A child that appeared AFTER the pid set was captured is alive when every
    tracked pid has exited. The old claim quantified only over the captured
    set, so this state produced a clean ``close=all-pids-exit`` — a completion
    signal over an incomplete teardown.

    The assertion is on the OUTPUT CHANGING, not on a call being made.
    """
    tracked = {4242001}
    # The child is NOT in the tracked set — that is the whole precondition.
    survivor = 4242002
    alive = {4242001, survivor}

    # THE REAL SEQUENCE, and the reason the observation must happen early:
    # while the parent lives the child is visible as its descendant; once the
    # parent dies the child is reparented to init and is a descendant of
    # NOTHING we hold. So the scan returns the child on the first polls and an
    # empty tree afterwards — a watch that only looked at claim time would see
    # exactly nothing and report a clean completion.
    log, _ = _run_thread_close_watch(
        monkeypatch,
        tracked=tracked,
        alive_pids=alive,
        descendants_seq=[{4242001, survivor}, {4242001, survivor}, set()],
        die_after=2,
    )
    # PRECONDITION, asserted rather than assumed: the watch really did track a
    # pid set, so a green below cannot mean the watch never ran at all.
    assert any("watch-pids" in line for line in log), "the watch never tracked"

    close = _close_line(log)
    assert close, "the watch never reported a close"
    assert str(survivor) in close, (
        "the completion claim did not mention the process that outlived the "
        "tracked set — this is the false signal the ticket is about: "
        f"{close!r}"
    )
    assert "survivors-after-tracked-exit" in "\n".join(log), (
        "no incompletion was reported for a session that left a survivor"
    )


def test_the_completion_claim_is_silent_about_survivors_when_there_are_none(
    monkeypatch,
):
    """The other half, so the test above cannot pass by always shouting.

    A clean session — nothing outside the tracked set — must still report a
    plain completion, with an EMPTY survivor list and no incompletion line. A
    signal that always reports survivors is as useless as one that never does.
    """
    tracked = {4243001}
    alive = set()

    log, _ = _run_thread_close_watch(
        monkeypatch,
        tracked=tracked,
        alive_pids=alive,
        descendants_seq=[set()],
    )
    close = _close_line(log)
    assert "close=tracked-pids-exit" in close
    assert "survivors=[]" in close, f"a clean close did not read as clean: {close!r}"
    assert "survivors-after-tracked-exit" not in "\n".join(log)


def test_the_claim_names_only_what_it_actually_checked(monkeypatch):
    """DoD #2 — naming it honestly is an acceptable fix; a false claim is not.

    ``all-pids-exit`` asserts every process the session spawned has exited.
    The watch cannot know that. The renamed claim says what was checked: the
    TRACKED set. Pinned as a string because the launcher parses this token and
    an operator reads it.
    """
    log, _ = _run_thread_close_watch(
        monkeypatch,
        tracked={4244001},
        alive_pids=set(),
        descendants_seq=[set()],
    )
    joined = "\n".join(log)
    assert "close=tracked-pids-exit" in joined
    assert "all-pids-exit" not in joined, (
        "the claim still asserts that ALL pids exited, which the watch cannot "
        "observe"
    )


def test_a_scan_that_cannot_run_never_widens_or_narrows_the_claim(monkeypatch):
    """PS-14 / PS-192's psutil lesson, applied to the new probe.

    ``_session_descendants`` returns None for "could not look". That must not
    render as "no descendants" (a false clean) NOR invent survivors. The close
    still happens — a broken probe must not wedge a profile "running" forever.
    """
    monkeypatch.setattr(il, "_session_descendants", lambda roots: None)
    log, _ = _run_thread_close_watch(
        monkeypatch,
        tracked={4245001},
        alive_pids=set(),
        descendants_seq=[set()],  # overridden by the None patch above
    )
    close = _close_line(log)
    assert "close=tracked-pids-exit" in close, (
        "a no-verdict scan must not prevent the close"
    )
    assert "survivors=[]" in close


def test_the_renamed_reason_is_still_a_quiet_close(monkeypatch):
    """The consumer half of the rename, in the SAME change.

    ``launcher.py`` matches ``LIFECYCLE close=(\\S+)`` and looks the token up in
    ``_QUIET_CLOSE_REASONS``. A renamed reason that is not added there makes
    every NORMAL close read as "Session ended unexpectedly" in the Activity
    Log — a second false signal introduced by fixing the first.
    """
    from src.services.browser.launcher import _CLOSE_REASON, _QUIET_CLOSE_REASONS

    log, _ = _run_thread_close_watch(
        monkeypatch,
        tracked={4246001},
        alive_pids=set(),
        descendants_seq=[set()],
    )
    close = _close_line(log)
    m = _CLOSE_REASON.search(close)
    assert m, f"the launcher's own regex does not parse the close line: {close!r}"
    assert m.group(1) in _QUIET_CLOSE_REASONS, (
        f"close reason {m.group(1)!r} is not in _QUIET_CLOSE_REASONS, so a "
        "normal close now reports as an unexpected end"
    )


def test_the_survivor_scan_is_anchored_on_tracked_pids_not_a_profile_rescan():
    """DoD #5 — the #150 relaunch race must stay disarmed.

    The teardown deliberately does NOT rescan the profile dir, because the
    launch lock is released at BROWSER_STARTED and a rescan can match — and
    kill — a CONCURRENTLY RELAUNCHING Firefox of the same profile. The new
    survivor observation must not reintroduce that by the back door: it takes
    ROOTS (the pids we tracked) and walks descendants, so it is structurally
    incapable of naming a relaunch's process, which is nobody's descendant here.

    Pinned on the signature rather than on behaviour because that is the
    property: a profile_dir parameter is what would make it rescannable.
    """
    import inspect

    sig = inspect.signature(il._session_descendants)
    assert list(sig.parameters) == ["roots"], (
        "_session_descendants must take only the tracked roots; a profile_dir "
        "parameter would re-arm the #150 relaunch race"
    )
    # And the teardown still refuses to rescan when it has tracked pids.
    src = inspect.getsource(il._invisible_session_body) if hasattr(
        il, "_invisible_session_body"
    ) else ""
    del src  # presence is asserted by the teardown test below, not here


def test_the_teardown_still_refuses_a_profile_dir_rescan_when_pids_are_tracked():
    """DoD #5, on the real call site: ``rescan=not tracked_pids`` is unchanged.

    Read from the shipped source so a future edit that flips it to True — the
    fix this ticket explicitly forbids — fails here with the reason attached.
    """
    import pathlib

    text = pathlib.Path(il.__file__).read_text(encoding="utf-8")
    assert "rescan=not tracked_pids" in text, (
        "the teardown no longer scopes its kill to the tracked pids; a fresh "
        "profile-dir rescan can match and kill a concurrently relaunching "
        "Firefox of the same profile (the #150 race)"
    )


# --------------------------------------------------------------------------
# DEFECT B — the reap must report DELIVERY truthfully.
# --------------------------------------------------------------------------

def _fork_child_that_spawns_and_exits(cfg, wf, stop_event=None):
    """Stand-in for ``_child`` with its FIRST ACT preserved: its own session.

    Copied in shape from tests/test_browser_process_group.py — a real Firefox
    tree needs a DISPLAY this container does not have, and the MECHANISM under
    test is entirely the session/record/signal triple. The wrapper exits at
    once, which is the shape that orphans: a leader that stayed alive would
    keep ``getpgid`` answering and hide the defect.
    """
    from src.services.browser.process_group import start_own_session

    start_own_session()
    for _ in range(3):
        subprocess.Popen(
            [sys.executable, "-c", _SLEEP],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        os.write(wf, b"BROWSER_STARTED\n")
    except Exception:
        pass
    os._exit(0)


def _kill_leftovers(pgid):
    if pgid is None:
        return
    try:
        os.killpg(pgid, 9)
    except Exception:
        pass


_FORK_ONLY = pytest.mark.skipif(
    not _platform.needs_fork_launch(),
    reason=(
        "the fork launch path is taken only where needs_fork_launch() is true "
        "(Linux). Guarding on hasattr(os,'fork') is NOT equivalent and was "
        "measured wrong: macOS HAS os.fork but needs_fork_launch() is False, so "
        "InvisibleProcess takes the THREAD path, the stand-in _child runs in the "
        "test runner's own thread, and its os._exit(0) kills pytest outright "
        "(macOS CI aborted at 1m52s). start_own_session() would likewise move "
        "persona's OWN session -- the exact hazard its docstring names."
    ),
)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
@_FORK_ONLY
def test_the_recorded_group_is_reachable_THROUGH_THE_HANDLE(monkeypatch):
    """DoD #4. The existing coverage reaches PAST the handle and so cannot see
    this: ``test_the_firefox_fork_path_records_its_group_at_launch`` asserts
    ``recorded_group(proc._proc)``, the inner ``mp.Process``. Every GENERIC
    teardown is handed the HANDLE, and that is where the lookup came back
    empty.
    """
    from src.services.browser.process_group import recorded_group

    monkeypatch.setattr(il, "_child", _fork_child_that_spawns_and_exits)
    proc = il.InvisibleProcess({}, in_process=False)
    pgid = proc.pid
    try:
        assert proc._fork, "this test must exercise the fork path, not the thread"
        proc.wait(timeout=15)  # what launcher.py's wait_for_exit does

        # PRECONDITION: the live lookup has genuinely gone blind, so a passing
        # assertion below cannot be a lucky re-resolution.
        assert group_of(proc.pid) is None, (
            "precondition: a waited-on leader must be unresolvable, or this "
            "test cannot tell a recorded group from a live lookup"
        )
        assert recorded_group(proc) == pgid, (
            "the pgid is not reachable through the HANDLE, so every generic "
            "teardown falls back to a blind live lookup and reports False on a "
            "call where the group WAS signalled"
        )
    finally:
        _kill_leftovers(pgid)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups")
@_FORK_ONLY
def test_reap_reports_delivery_truthfully_on_a_fork_path_handle(monkeypatch):
    """⭐ DoD #4's bar, stated as the RETURN VALUE.

    ``reap_process_group``'s True is documented to mean "a group signal was
    actually DELIVERED" — a caller measuring the fix needs it to tell a group
    teardown from a single-process fallback. On this handle it returned False
    while the group was being signalled by polymorphism through
    ``InvisibleProcess.terminate()``.

    The tree died either way, so this is NOT a leak assertion — the survivor
    count is asserted too, precisely so the two claims stay separable and a
    future regression in either one is attributable.
    """
    from src.services.browser.process_group import (
        process_group_survivors,
        reap_process_group,
    )

    monkeypatch.setattr(il, "_child", _fork_child_that_spawns_and_exits)
    proc = il.InvisibleProcess({}, in_process=False)
    pgid = proc.pid
    try:
        assert proc._fork
        proc.wait(timeout=15)
        assert group_of(proc.pid) is None, "precondition: the leader is reaped"

        # The precondition that makes the return value meaningful: there IS a
        # live group to signal. Asserted against the OS process table (PS-17),
        # not inferred from a green.
        deadline = time.time() + 10
        while time.time() < deadline and not process_group_survivors(pgid):
            time.sleep(0.1)
        assert process_group_survivors(pgid), (
            "precondition: the session left a live group to signal, otherwise "
            "a truthful False and a false False are indistinguishable"
        )

        delivered = reap_process_group(proc, timeout=10)

        assert delivered is True, (
            "reap_process_group reported that NO group signal was delivered on "
            "a call where the group was signalled — the false signal this "
            "ticket owns"
        )
        # And the separate, already-fixed property (PS-192), asserted so the
        # two cannot be confused for one another.
        deadline = time.time() + 10
        while time.time() < deadline and process_group_survivors(pgid):
            time.sleep(0.1)
        assert process_group_survivors(pgid) == [], (
            "the tree survived — this would be a LEAK regression, separate "
            "from the reporting defect above"
        )
    finally:
        _kill_leftovers(pgid)


def test_the_thread_path_reap_still_reports_False_truthfully():
    """The opposite direction, so Defect B's fix cannot be 'return True more'.

    On the THREAD path (``in_process=True``) ``pid`` is 0 and there is no
    process group at all — the session is a thread in our own process. False is
    the TRUTHFUL answer there, and the ticket says so explicitly: baseline.py's
    call site is correct as written and is out of scope. If this ever flips to
    True, the fix has started lying in the other direction.
    """
    from src.services.browser.process_group import reap_process_group

    class _ThreadHandle:
        """The shape InvisibleProcess presents on the thread path."""

        pid = 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    assert reap_process_group(_ThreadHandle()) is False, (
        "a handle with no process group must report that no group signal was "
        "delivered; reporting True would make the boolean meaningless"
    )
