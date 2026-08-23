"""A per-operation bound for the UI driver that does not need a pytest plugin.

WHY THIS EXISTS
---------------
``pyproject.toml`` sets ``timeout = 120`` and both driven test modules carry an
explicit ``pytest.mark.timeout(...)``. All three are ``pytest-timeout``
constructs, so all three are **inert wherever that plugin is not installed** —
pytest ignores an ini key belonging to an absent plugin, and an unknown marker
is just metadata. CI installs ``requirements-dev.txt`` and is therefore bounded;
an environment that installs only the project is not, and nothing said so. The
intent to bound these tests was expressed three times and none of the three
expressions survives a missing plugin.

That gap matters most for exactly these tests, because they are the ones that
spawn **external children** — a flet web server and a playwright node driver
with a chromium behind it. A thread-based timeout is weakest precisely here:
the blocking wait is inside a child process, not in the test thread. A test
that owns a subprocess should own its own bound.

WHAT THIS DOES, AND WHY IT WORKS
--------------------------------
The hang is not in the readiness wait — :func:`tests.ui_driver.server._await_ready`
is already bounded and raises with the child's log. It is **past** readiness, in
a synchronous playwright call (``page.goto``, ``evaluate``, a click) that never
returns. Nothing in the test thread can interrupt that call: it is blocked
reading a pipe from the node driver, so no flag, no exception and no
``KeyboardInterrupt`` reaches it, and neither ``serve_app``'s ``finally`` nor
``FletDriver.close()`` ever runs. That is the observed shape — a parent in
``ep_poll`` with both children alive.

So the bound is enforced **where the block actually is**: a watchdog thread
reaps the child processes. Killing the node driver closes the pipe the wedged
call is reading, the call raises, control returns to the test thread, and every
existing ``finally`` runs normally.

MEASURED, not assumed (2026-08-23, this container): a ``page.evaluate`` running
``while(true){}`` — which no playwright timeout applies to — was unblocked
**5.0s** after its driver tree was killed, raising ``Connection closed while
reading from the driver``, and left **zero** surviving processes. That
experiment is the premise this module rests on, and it is re-run as a test in
``tests/test_ui_driver_watchdog.py`` rather than left as a claim.

A SINGLE OPERATION IS WHAT IS BOUNDED
-------------------------------------
:meth:`ChildWatchdog.pet` starts the clock and :meth:`ChildWatchdog.rest` stops
it, so the deadline covers one driver operation and idle time between operations
is deliberately NOT bounded. This is not a detail: a driven test reads its
result back through the service layer in a subprocess while the session is still
open, and a watchdog that kept running across that gap would reap a healthy
browser and fail a passing test. Bounding the operation is the honest claim —
"no single UI gesture may block forever" — and it is the one that fires only on
a real wedge.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time

#: How long any ONE driver operation may block before its children are reaped.
#: Comparable to the suite's configured per-test bound rather than derived from
#: it: the ini value belongs to a plugin that may be absent, and reading it
#: would reintroduce the very dependency this module exists to remove.
DEFAULT_OP_TIMEOUT = 120.0

#: Grace between asking a child to exit and killing it. The point is to be
#: certain nothing survives, not to be polite: this path runs only when a test
#: is already failing, and a lingering chromium is what moves a hang rather
#: than fixing it.
REAP_GRACE = 5.0


class UiDriverTimeout(AssertionError):
    """A driver operation exceeded its own bound and its children were reaped.

    An ``AssertionError`` so it reads as a test failure rather than an
    infrastructure error: the run terminating with a named cause IS the
    behaviour this is meant to produce.
    """


def _psutil():
    """psutil or ``None``. Declared in ``pyproject.toml``, so normally present.

    Imported lazily and tolerated absent so this module cannot be the reason a
    suite fails to import — a bound that breaks collection is worse than the
    hang it replaces.
    """
    try:
        import psutil

        return psutil
    except ImportError:  # pragma: no cover - psutil is a declared dependency
        return None


def child_pids(pid: int | None = None) -> set[int]:
    """Every descendant of ``pid`` (default: this process), recursively.

    Used to identify what playwright spawned: the node driver's pid is not
    exposed by its public API, so the children are snapshotted before the launch
    and diffed after it. That is stable across playwright versions in a way that
    reaching into ``_connection`` internals is not.
    """
    ps = _psutil()
    if ps is None:  # pragma: no cover - psutil is a declared dependency
        return set()
    try:
        return {c.pid for c in ps.Process(pid or os.getpid()).children(recursive=True)}
    except Exception:
        return set()


def reap_process_tree(pid: int, grace: float = REAP_GRACE) -> list[int]:
    """Terminate ``pid`` and every descendant; kill whatever ignores that.

    Returns the pids actually reaped, so a caller can assert on the cleanup
    rather than trust it.

    The descendants are collected BEFORE anything is signalled. Kill a parent
    first and its children are reparented to init, at which point they are no
    longer reachable from the pid you hold — which is precisely how a "cleanup"
    leaves a chromium running. flet spawns its own children and playwright's
    node driver owns a browser tree, so both cases are real here.
    """
    ps = _psutil()
    if ps is None:  # pragma: no cover - psutil is a declared dependency
        with contextlib.suppress(Exception):
            os.kill(pid, 9)
        return []

    try:
        parent = ps.Process(pid)
    except Exception:
        return []

    try:
        family = parent.children(recursive=True) + [parent]
    except Exception:
        family = [parent]

    for proc in family:
        with contextlib.suppress(Exception):
            proc.terminate()
    _gone, alive = ps.wait_procs(family, timeout=grace)
    for proc in alive:
        with contextlib.suppress(Exception):
            proc.kill()
    ps.wait_procs(alive, timeout=grace)

    return [p.pid for p in family if not _is_running(ps, p.pid)]


def _is_running(ps, pid: int) -> bool:
    try:
        proc = ps.Process(pid)
        # A zombie is not running; it is a corpse awaiting a wait(). Counting
        # one as a survivor would make a correct reap look like a failed one.
        return proc.is_running() and proc.status() != ps.STATUS_ZOMBIE
    except Exception:
        return False


def survivors(pids) -> list[int]:
    """Which of ``pids`` are still running. The cleanup assertion's evidence."""
    ps = _psutil()
    if ps is None:  # pragma: no cover - psutil is a declared dependency
        return []
    return [pid for pid in pids if _is_running(ps, pid)]


class ChildWatchdog:
    """Reap registered process trees when one operation overruns its bound.

    Not a general-purpose timeout: it cannot interrupt the test thread, and it
    does not try to. It removes the thing the thread is blocked ON, which is
    what makes it work where a thread-based timeout does not.
    """

    def __init__(self, timeout_s: float = DEFAULT_OP_TIMEOUT, poll_s: float = 0.25):
        self.timeout_s = float(timeout_s)
        self._poll_s = poll_s
        self._pids: list[int] = []
        self._baseline: set[int] | None = None
        self._deadline: float | None = None
        self._label: str | None = None
        self._depth = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.expired = False
        self.expired_label: str | None = None
        self.waited_s: float = 0.0
        self.reaped: list[int] = []

    # ---- registration ------------------------------------------------

    def register(self, *pids: int) -> None:
        """Add process trees to reap on expiry. Unknown pids are harmless."""
        with self._lock:
            for pid in pids:
                if pid and pid not in self._pids:
                    self._pids.append(pid)

    @property
    def registered(self) -> list[int]:
        with self._lock:
            return list(self._pids)

    def mark_baseline(self) -> None:
        """Record which descendants existed BEFORE the driver spawned anything.

        Registration alone leaves a real gap: the launch itself is a blocking
        call, so a wedge INSIDE ``chromium.launch`` or ``sync_playwright().start()``
        hangs before there is any pid to register — the exact window in which
        the children being waited on are the ones that do not exist yet. The
        baseline closes it: on expiry, anything that appeared since is ours and
        is reaped, whether or not it was ever registered.

        Diffing against a baseline is also why this needs no playwright
        internals. The node driver's pid is not exposed by the public API, and
        reaching into ``_connection`` to find it would break on any version
        bump; "what is new since I started" is stable across versions.
        """
        with self._lock:
            self._baseline = child_pids()

    def _spawned_since_baseline(self) -> list[int]:
        with self._lock:
            baseline = self._baseline
        if baseline is None:
            return []
        return sorted(child_pids() - baseline)

    # ---- the clock ---------------------------------------------------

    def pet(self, label: str) -> None:
        """Start the clock for one operation, named so a failure can say what.

        REENTRANT, and it has to be: driver methods call each other
        (``wake_semantics`` calls ``nodes``, ``select_option`` calls ``_open``
        calls ``find_dropdown``). A nested call must not shorten or rename the
        bound its caller is running under, and — more importantly — the inner
        ``rest`` must not DISARM the outer operation, which would silently
        unbound exactly the long compound gestures most likely to wedge. So the
        clock is set by the OUTERMOST operation only, and the label reported on
        failure is the outermost one: "select_option" is what a reader needs to
        see, not whichever leaf call happened to be in flight.
        """
        with self._lock:
            self._depth += 1
            if self._depth == 1:
                self._deadline = time.monotonic() + self.timeout_s
                self._label = label

    def rest(self) -> None:
        """Stop the clock once the outermost operation finishes.

        Idle time between operations is deliberately NOT bounded — see the
        module docstring: a driven test reads its result back through a
        subprocess while the browser is still open, and reaping a healthy
        browser during that gap would fail a passing test.
        """
        with self._lock:
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._deadline = None
                self._label = None

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> "ChildWatchdog":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="ui-driver-watchdog", daemon=True
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "ChildWatchdog":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            with self._lock:
                deadline, label = self._deadline, self._label
            if deadline is not None and time.monotonic() >= deadline:
                self._expire(label)
                # SINGLE-SHOT BY INTENT: returning ends the watchdog thread, so
                # the driver is unbounded from here on. That is deliberate —
                # once a bound has fired the test is already failing and is on
                # its way out, and a watchdog that re-armed would be reaping
                # during the unwind, racing the very cleanup it exists to make
                # possible. close() still reaps directly on this path, via its
                # own reap_process_tree loop over _spawned, so "every spawned
                # child is gone" does not depend on this thread still running.
                return

    def _expire(self, label: str | None) -> None:
        self.expired = True
        self.expired_label = label
        self.waited_s = self.timeout_s
        with self._lock:
            self._deadline = None
            pids = list(self._pids)
        # Registered trees first, then anything that appeared since the
        # baseline — that second set is what covers a wedge during the launch
        # itself, before there was any pid to register. Reaping a registered
        # parent usually takes its descendants with it, so the survivors filter
        # keeps this from signalling pids that are already gone.
        targets = pids + [
            pid for pid in self._spawned_since_baseline() if pid not in pids
        ]
        reaped: list[int] = []
        for pid in targets:
            reaped.extend(reap_process_tree(pid))
        self.reaped = reaped

    # ---- reporting ---------------------------------------------------

    def timeout_error(self, label: str | None = None) -> UiDriverTimeout:
        """The failure a wedged operation should raise. Names what it waited for."""
        what = label or self.expired_label or "a UI driver operation"
        return UiDriverTimeout(
            f"UI driver timed out after {self.waited_s:.0f}s waiting for {what}. "
            f"Its child processes were reaped ({len(self.reaped)} killed) so the "
            f"run could end instead of hanging. This bound is enforced by "
            f"tests/ui_driver/watchdog.py and does NOT depend on pytest-timeout, "
            f"which is absent in some environments."
        )
