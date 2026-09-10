"""The pid-release precondition helper (PS-384).

WHAT THIS PINS, AND WHY IT IS ITS OWN FILE. ``tests/pid_release.py`` is a
PRECONDITION instrument: six tests across the launch-guard neighbourhood now
call it before asserting that the product reads a killed process as GONE. An
instrument that is silently wrong does not fail — it makes every test that
leans on it vacuous, which is the exact failure mode the launch-guard suite
already records about itself (a green run proving nothing). So the instrument
is measured here rather than trusted, on the same convention
``tests/posix_shell.py`` follows with ``test_ps254_posix_shell_resolution.py``.

THE DEFECT IT EXISTS TO CURE. ``proc.kill(); proc.wait()`` leaves a pid still
RESOLVABLE on Windows for a short interval, so ``liveness_of`` truthfully reads
ALIVE about a dead process and a test asserting the release fails. That is what
reddened ``tests (windows-latest, main)`` on run 34422663145 — one test, on one
platform, on a commit touching neither the launcher nor the registry.

⚠️ THE TWO ASSERTIONS THAT MATTER MOST ARE THE NEGATIVE ONES, and they are the
reason this file is not ceremony. A helper that simply returned would pass
every caller's precondition and cure nothing, and a helper that returned on
TIMEOUT would do the same thing more slowly. Both are pinned below.
"""

import os
import subprocess
import sys
import time

import pytest

from tests.pid_release import await_pid_release, pid_is_resolvable


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


# --------------------------------------------------------------------------
# pid_is_resolvable — the question the wait is built on
# --------------------------------------------------------------------------


def test_a_live_process_is_resolvable():
    """The positive case. Without it the negative ones below are vacuous: a
    function that always answered False would pass them all."""
    proc = _sleeper()
    try:
        assert pid_is_resolvable(proc.pid) is True
    finally:
        proc.kill()
        proc.wait()


def test_a_reaped_process_is_not_resolvable():
    """The case the callers depend on. On POSIX ``wait()`` has already
    released the pid, so this holds immediately."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    await_pid_release(proc.pid)
    assert pid_is_resolvable(proc.pid) is False


@pytest.mark.parametrize("pid", [0, -1, -12345])
def test_a_nonsense_pid_is_not_resolvable(pid):
    """0 and negatives never name a process to wait for.

    ⛔ AND THEY MUST NOT REACH ``os.kill``: on POSIX ``os.kill(0, ...)``
    signals OUR ENTIRE PROCESS GROUP and a negative pid signals a group by
    number, so this is a safety fence rather than input validation.
    """
    assert pid_is_resolvable(pid) is False


# --------------------------------------------------------------------------
# await_pid_release — and the two ways it could be a no-op
# --------------------------------------------------------------------------


def test_awaiting_a_released_pid_returns_immediately():
    """The green path must be FREE on the platforms that never had the defect.

    A helper that cost a fixed sleep would tax Linux and macOS forever to cure
    a Windows-only race. This asserts the loop exits on its first check.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()

    started = time.monotonic()
    await_pid_release(proc.pid)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"await_pid_release took {elapsed:.2f}s on an already-released pid — "
        "the POSIX path is supposed to cost nothing"
    )


def test_awaiting_a_live_pid_raises_rather_than_passing_quietly():
    """⭐ THE ASSERTION THAT MAKES THE HELPER WORTH ANYTHING.

    If an unmet precondition returned quietly, every caller would go on to
    assert GONE about a process that is still ALIVE — and the helper would have
    converted a legible failure into a mysterious one, at six call sites.
    Waiting is not the contract; ESTABLISHING THE PRECONDITION OR SAYING IT
    COULD NOT BE is.
    """
    proc = _sleeper()
    try:
        with pytest.raises(AssertionError) as excinfo:
            await_pid_release(proc.pid, timeout=0.3)
        message = str(excinfo.value)
        assert str(proc.pid) in message, "the failure must name the pid"
        assert "measuring nothing" in message, (
            "the failure must say WHY an unestablished precondition matters, "
            "not merely that a timeout elapsed"
        )
    finally:
        proc.kill()
        proc.wait()


def test_the_wait_actually_blocks_until_the_pid_goes_away():
    """It POLLS THE CONDITION rather than returning on a guess.

    A process is killed from another thread partway through the wait; the call
    must not return before that happens, and must return once it has.
    """
    import threading

    proc = _sleeper()
    release_after = 0.5
    threading.Timer(release_after, lambda: (proc.kill(), proc.wait())).start()

    started = time.monotonic()
    await_pid_release(proc.pid, timeout=30.0)
    elapsed = time.monotonic() - started

    assert elapsed >= release_after, (
        f"returned after {elapsed:.2f}s but the process was alive until "
        f"{release_after}s — the wait did not observe the condition"
    )
    assert pid_is_resolvable(proc.pid) is False


# --------------------------------------------------------------------------
# The Windows window itself, reproduced on whatever platform runs this
# --------------------------------------------------------------------------


def test_the_helper_bridges_a_pid_that_lingers_after_being_reaped():
    """⭐ THE DEFECT, REPRODUCED — not merely described in a docstring.

    Windows keeps a killed-and-reaped pid resolvable for a short interval.
    Linux does not, so on the platform this suite usually runs on the bug is
    UNREACHABLE and any test of it would be green for the wrong reason. Here
    the lingering pid is simulated so the assertion has real content on EVERY
    platform: before the wait the pid still reads resolvable, and the helper
    is what carries the caller across to the point where it does not.

    ⛔ This simulates the OS, never the helper. If ``await_pid_release`` were
    gutted to ``return``, this test would go red.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    target = proc.pid
    proc.wait()

    linger = 0.4
    died_at = time.monotonic()
    real_probe = pid_is_resolvable

    def lingering(pid: int) -> bool:
        if pid == target and time.monotonic() - died_at < linger:
            return True  # Windows: reaped, but the pid still resolves
        return real_probe(pid)

    import tests.pid_release as module

    original = module.pid_is_resolvable
    module.pid_is_resolvable = lingering
    try:
        assert lingering(target) is True, (
            "precondition: the pid must still be reading as resolvable, or "
            "this test is not reproducing the Windows window at all"
        )

        started = time.monotonic()
        module.await_pid_release(target, timeout=30.0)
        elapsed = time.monotonic() - started

        assert elapsed >= linger, (
            f"returned after {elapsed:.2f}s while the pid was still resolving "
            f"for {linger}s — the helper did not bridge the window"
        )
        assert lingering(target) is False
    finally:
        module.pid_is_resolvable = original


def test_an_unanswerable_probe_is_not_read_as_death():
    """A probe that RAISES must not be mistaken for "the process is gone".

    Reading an error as death is the direction that silently defeats the
    precondition: the caller would proceed on a release nobody observed. It
    reads as "still there" instead, so the timeout fails loudly.
    """
    import tests.pid_release as module

    real_import = __import__

    class _Exploding:
        @staticmethod
        def pid_exists(pid):
            raise RuntimeError("psutil exploded")

    def fake_import(name, *a, **k):
        if name == "psutil":
            return _Exploding
        return real_import(name, *a, **k)

    import builtins

    original = builtins.__import__
    builtins.__import__ = fake_import
    try:
        assert module.pid_is_resolvable(os.getpid()) is True
    finally:
        builtins.__import__ = original
