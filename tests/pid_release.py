"""Wait until the OS has GENUINELY let go of a pid, on every platform.

WHY THIS EXISTS
───────────────
``proc.kill(); proc.wait()`` reads like "the process is now gone". On POSIX it
is: ``wait()`` reaps the child, the pid is released, and the very next liveness
probe answers GONE. On **Windows it is not**, and the gap is a real window
rather than a theoretical one.

Terminating a process and collecting its exit status does not oblige the kernel
to retire its pid immediately. For a short interval afterwards the pid is still
resolvable — ``OpenProcess`` succeeds and the pid is still returned by the
system's process enumeration — even though the process has exited. psutil
documents this in its own ``Process.wait()``:

    # At this point WaitForSingleObject() returned WAIT_OBJECT_0,
    # meaning the process is gone. Stupidly there are cases where
    # its PID may still stick around so we do a further internal
    # polling.

It polls for exactly the reason this module exists. Anything that asks "is this
pid alive?" during that window — including ``session_registry.liveness_of`` —
gets a truthful ALIVE about a process that is dead, because the pid resolves
*and* its recorded create time still matches. The tri-state probe has no escape
hatch here either: the ZOMBIE arm is inert on Windows, where psutil's
``status()`` only ever answers RUNNING or STOPPED.

That is what reddened ``tests (windows-latest, main)`` on run 34422663145
(PS-384) — one test, on one platform, on a commit that touched neither the
launcher nor the registry:

    FAILED tests/test_launcher_survivors.py::
      test_survivor_for_re_probes_and_releases_a_browser_since_closed
      AssertionError: a closed browser must stop blocking
      assert SessionRecord(profile='alpha', pid=9696, ...) is None

⛔ IT IS NOT A FLAKE TO RE-RUN AND IT IS NOT A PRODUCT DEFECT. The product
answered the question it was asked, correctly, about the state the OS was
actually reporting. The **test** asserted a consequence of death before death
had finished happening. So the repair belongs in the test's PRECONDITION, and
what it establishes is the thing the test always assumed and never checked.

⛔ AND IT IS NOT FIXED BY A ``sleep``. A fixed pause is either too short (the
race comes back under load, on a runner that is regularly slower than a laptop)
or too long (paid on every platform, forever, to cure a defect only one has).
This polls the actual condition and returns the instant it holds — which on
POSIX is the first iteration, so the green platforms pay nothing.

⛔ AND IT MUST NOT BE ALLOWED TO PASS ON A TIMEOUT. If the pid never releases,
the caller's precondition is FALSE and whatever it went on to assert would be
measuring nothing. This raises rather than returning quietly, so an
unestablished precondition reads as a failure that names itself instead of a
green tick nobody can trust.

⚠️ REAP FIRST, THEN CALL THIS. On POSIX an unreaped child is a ZOMBIE, and a
zombie's pid resolves — so this would spin until it timed out and then fail,
blaming the platform for the caller's missing ``wait()``. Every caller here
kills and waits before asking.
"""

import os
import time

#: Generous, because it bounds a FAILURE rather than a success: the loop
#: returns the moment the pid is released (immediately, on POSIX), so this is
#: only ever paid in full when something is genuinely wrong. A hosted Windows
#: runner under full-suite load is the slowest environment this has to hold on.
_DEFAULT_TIMEOUT = 30.0

_POLL_INTERVAL = 0.01


def pid_is_resolvable(pid: int) -> bool:
    """Can the OS still resolve ``pid`` to a process?

    NOT the same question as "is a browser running", and deliberately weaker
    than the product's own ``liveness_of``: there is no create-time identity
    check here, so a reused pid reads True. That is the RIGHT direction for a
    precondition — it keeps waiting while there is any doubt, and it cannot
    hand a caller a false "it's gone".

    ⛔ ``os.kill(pid, 0)`` IS POSIX-ONLY, AND ON WINDOWS IT IS ACTIVELY
    DANGEROUS rather than merely wrong: ``os.kill`` there routes any signal
    that is not ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` to ``TerminateProcess``,
    so the "harmless existence check" would KILL the process it was asked
    about. The platform branch below is a safety fence, not a portability
    nicety, and must not be flattened.
    """
    if pid <= 0:
        return False

    try:
        import psutil
    except Exception:  # pragma: no cover - psutil is a declared dependency
        psutil = None

    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            # An unanswerable probe is not evidence of death. Say "still
            # there" and let the caller's timeout be the thing that fails,
            # loudly, rather than inventing a release that was never observed.
            return True

    if os.name == "nt":  # pragma: no cover - psutil is installed in CI
        raise AssertionError(
            "cannot establish pid release on Windows without psutil: os.kill "
            "would TERMINATE the process rather than probe it"
        )

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means there IS a process to be denied access to.
        return True
    return True


def await_pid_release(pid: int, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
    """Block until ``pid`` is no longer resolvable. Raise if it never is.

    Call it AFTER the process has been killed and reaped, and BEFORE asserting
    anything that depends on the product agreeing the process is gone.
    """
    deadline = time.monotonic() + timeout
    while pid_is_resolvable(pid):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"precondition not established: pid {pid} was killed and "
                f"reaped but the OS still resolves it after {timeout:g}s, so "
                "any assertion that it reads as GONE would be measuring "
                "nothing"
            )
        time.sleep(_POLL_INTERVAL)
