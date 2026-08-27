"""A per-test bound for the WHOLE suite that does not need a pytest plugin.

WHY THIS EXISTS
---------------
``pyproject.toml`` sets ``timeout = 120``. That key belongs to ``pytest-timeout``
and **pytest ignores an ini key whose plugin is not loaded**, so in an
environment that installs only the project the bound silently does not exist.
``conftest.py`` has printed ``per-test timeout: INERT`` on every such run since
PS-104, and that banner was accurate — but a notice is not a bound.

PS-140 measured what the notice was worth. A worker ran ``pytest tests/ -q`` in
an agent container, the suite wedged at 74–76%, and it stayed there for roughly
**ninety minutes** while the worker polled it. The banner had already said
``INERT``. Reading it changes nothing at the moment you read it: you still need
the suite to pass and you have no way to bound it. The ticket's conclusion is
the one this module implements — *the answer is not another warning.*

PS-104 built exactly the right mechanism for this and deliberately scoped it
narrowly: :mod:`tests.ui_driver.watchdog` bounds each UI-driver operation with a
plain thread and reaps the children, with no ``pytest_timeout`` reference in it
anywhere. The gap PS-140 names is **scope, not mechanism** — that watchdog
bounds one family, and ``pytest tests/ -q`` is the whole suite. This module is
that idea applied to every test, and it reuses PS-104's reaping rather than
reimplementing it.

WHY A THREAD THAT HARD-EXITS, AND NOT SOMETHING GENTLER
-------------------------------------------------------
The obvious wish is to fail the wedged test and let the run continue. That is
not reliably available, and pretending otherwise is how a bound becomes a lie:

* **You cannot interrupt the main thread from another thread.** When the main
  thread is blocked inside a C call or a syscall — reading a pipe, waiting on a
  lock — no flag, no exception and no ``KeyboardInterrupt`` reaches it. This is
  not theory here: it is the measured shape PS-104 documented, a parent in
  ``ep_poll`` with both children alive.
* **``SIGALRM`` is POSIX-only**, so a signal-based bound would leave Windows —
  which this project ships and tests — unbounded, and the whole point is a bound
  that holds *where the plugin is absent*, not one that holds on some platforms.

So this does what ``pytest-timeout``'s own ``thread`` method does, which is also
the method ``pyproject.toml``'s comment names for Windows: dump the tracebacks
and terminate the process. **The honest cost, stated rather than buried: the run
ends at the first hang, and the tests after it do not run.** That is a real loss
and it is the correct trade against ninety minutes of nothing — a run that ends
in 120s naming the wedged test is diagnosable, and a run that never ends is not.
It is also the *same* behaviour CI already has when the plugin is present, so
the two environments now agree instead of differing on a load-bearing property.

HOW A FIRED BOUND IS TOLD APART FROM A TEST THAT SIMPLY FAILED
--------------------------------------------------------------
A bound that reads like an ordinary failure turns hangs into a mysterious flake
and buys a worse problem than it solves. PS-140 makes this a requirement, and it
is met three ways, any one of which is sufficient on its own:

1. **The exit code is** :data:`TIMEOUT_EXIT_CODE` **(124)**, which pytest itself
   never returns — pytest uses 0–5, where 1 means "tests failed". 124 is the
   conventional "command timed out" code (``timeout(1)`` uses it), so a script
   reading only an exit status can tell a wedge from a red test.
2. **An unmistakable banner** built around :data:`BANNER_TOKEN`, which appears
   nowhere else in this repo, saying in words that the run was KILLED at a limit
   and that this is not an assertion failure.
3. **A** :mod:`faulthandler` **traceback of every thread**, which no assertion
   failure produces. This is what names the stuck frame — the cheap way to find
   out *where* it wedged without the plugin.

The banner and the dump go to the real ``stderr`` file descriptor
(``sys.__stderr__``), NOT to ``sys.stderr``. Under pytest, ``sys.stderr`` is a
capture object: it may have no ``fileno()`` for :mod:`faulthandler` to write to,
and anything written to it is buffered for a summary that :func:`os._exit` will
never reach. A diagnostic that the kill discards is not a diagnostic.

WHAT IS BOUNDED
---------------
The whole test item — setup, call and teardown — not just the call phase. A hang
in a fixture wedges the run exactly as thoroughly as a hang in a test body.
Collection is bounded too: an import that never returns is the same defect
arriving earlier, and "the suite cannot hang" is not true if collection can.

Per-test ``@pytest.mark.timeout(N)`` is honoured HERE, by reading the marker
directly. Without the plugin an unknown marker is inert metadata, so the two
driven modules that declare 600s and 900s would otherwise be killed at the
120s default — a bound that fails healthy long tests would be removed by the
first person it inconvenienced, and rightly so.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time

#: Process exit code when the bound fires.
#:
#: Deliberately outside pytest's own range (0–5, where 1 = "tests failed"): the
#: whole requirement is that a killed run does not read as a red test. 124 is
#: what ``timeout(1)`` returns for the same event, so it is the code a reader or
#: a CI script is most likely to already recognise.
TIMEOUT_EXIT_CODE = 124

#: A string that appears nowhere else in this repository, so grepping a captured
#: log for it answers "was this run killed at a limit?" with no false positives.
BANNER_TOKEN = "PERSONA-SUITE-WATCHDOG-TIMEOUT"

#: Used only if ``pyproject.toml``'s ``timeout`` cannot be read. Not a second
#: opinion about the right bound — :func:`configured_timeout_s` reads the real
#: setting so the two environments cannot drift apart, and PS-140 puts changing
#: that value explicitly out of scope.
FALLBACK_TIMEOUT_S = 120.0

#: Overrides the bound for one run. ``0`` disables it entirely, matching
#: ``pytest-timeout``'s reading of ``timeout = 0``. An escape hatch is not a
#: weakness here: someone with a genuinely long local run needs a way through
#: that is not "delete the watchdog", and the tests below need to set a bound
#: short enough to actually observe firing.
TIMEOUT_ENV_VAR = "PERSONA_SUITE_TIMEOUT"

#: How long a reaped child tree gets to exit before it is killed outright.
REAP_GRACE = 5.0


def configured_timeout_s() -> float:
    """The suite's configured per-test bound, in seconds.

    Reads ``[tool.pytest.ini_options] timeout`` out of ``pyproject.toml``
    directly with :mod:`tomllib` rather than through ``config.getini``.
    ``getini`` RAISES for that key where the plugin is absent, because the key
    is registered BY the plugin — so the one environment this module exists for
    is the one environment ``getini`` cannot answer in.

    Reading the file keeps the fallback bound and the configured bound in
    lockstep instead of duplicating 120 into a constant that goes stale the day
    someone edits the ini. Any failure to read falls back rather than raising:
    a bound that breaks collection is worse than the hang it replaces.
    """
    override = os.environ.get(TIMEOUT_ENV_VAR)
    if override is not None:
        try:
            return float(override)
        except ValueError:
            pass

    try:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as handle:
            data = tomllib.load(handle)
        value = data["tool"]["pytest"]["ini_options"]["timeout"]
        return float(value)
    except Exception:
        return FALLBACK_TIMEOUT_S


def marker_timeout_s(item) -> float | None:
    """The bound declared by ``@pytest.mark.timeout(N)`` on ``item``, if any.

    Read here because, without the plugin, that marker is inert metadata that
    nothing enforces and nothing consumes. Two modules in this suite declare
    600s and 900s for real reasons — they boot a browser per test — so a flat
    120s applied over the top of them would fail healthy tests, which is the
    fastest way to get a safety net deleted.
    """
    try:
        marker = item.get_closest_marker("timeout")
    except Exception:
        return None
    if marker is None:
        return None

    value = marker.args[0] if marker.args else marker.kwargs.get("timeout")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _emergency_stream():
    """A writable stream on the REAL stderr, after capture has been suspended.

    WHY THIS IS NOT SIMPLY ``sys.__stderr__``, MEASURED RATHER THAN REASONED.
    Pytest's DEFAULT capture mode is ``fd``, which ``dup2``s over file
    descriptors 1 and 2 themselves — so under a normal run ``sys.__stderr__``
    still reports ``fileno() == 2``, but fd 2 now points at a temp file pytest
    reads back only when it prints its summary. :func:`os._exit` guarantees that
    summary never happens. First cut of this module wrote there and the result
    was a run that exited 124 in five seconds having printed **nothing at all**;
    only ``-s`` revealed the dump. A bound that ends the run without saying why
    is most of the way back to the hang it replaced — PS-140's ninety minutes
    were expensive because they were MUTE, not merely because they were long.

    Duplicating fd 2 early does not fix it either, and that was the second wrong
    answer: pytest starts global capturing in ``pytest_load_initial_conftests``,
    i.e. BEFORE it imports the conftest that imports this module, so the earliest
    dup available to us is already a handle on the capture file.

    The fix is to ask pytest to put the real fds back — see
    ``_restore_output`` — and only then take fd 2. Once capture is suspended,
    fd 2 is the terminal again, and :mod:`faulthandler` gets the real
    ``fileno()`` it needs.
    """
    try:
        return os.fdopen(os.dup(2), "w", closefd=True, encoding="utf-8")
    except Exception:
        pass

    stream = sys.__stderr__
    if stream is None:
        return None
    try:
        stream.fileno()
    except Exception:
        return None
    return stream


def _reap_children() -> list[int]:
    """Kill everything this process spawned, so the kill does not leak a tree.

    Reuses PS-104's reaper rather than writing a second one — it already handles
    the part that is easy to get wrong (collect descendants BEFORE signalling the
    parent, or they are reparented to init and escape). Imported lazily and with
    every failure tolerated: this runs while the process is already dying, and an
    error here must not replace a useful diagnostic with a traceback from the
    cleanup.
    """
    try:
        from tests.ui_driver.watchdog import child_pids, reap_process_tree
    except Exception:
        return []

    reaped: list[int] = []
    try:
        for pid in child_pids():
            try:
                reaped.extend(reap_process_tree(pid, grace=REAP_GRACE))
            except Exception:
                continue
    except Exception:
        return reaped
    return reaped


class SuiteWatchdog:
    """Terminate the run, loudly and diagnosably, if one item overruns.

    One daemon thread for the whole session. :meth:`arm` starts a clock for one
    labelled phase and :meth:`disarm` stops it, so idle time between items is
    deliberately not bounded — only the time a single item is actually running.
    """

    def __init__(
        self, default_timeout_s: float, poll_s: float = 0.25, config=None
    ) -> None:
        self.default_timeout_s = float(default_timeout_s)
        self._poll_s = poll_s
        # Held ONLY to reach the capturemanager when firing. Optional so the
        # unit tests can drive this class without building a pytest Config.
        self._config = config
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._label: str | None = None
        self._armed_at: float | None = None
        self._bound: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="persona-suite-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    # ---- arming ------------------------------------------------------

    def arm(self, label: str, timeout_s: float | None = None) -> None:
        """Bound ``label`` for ``timeout_s`` (default: the configured bound).

        A non-positive bound disarms instead of arming, so ``timeout = 0`` and
        ``@pytest.mark.timeout(0)`` mean "unbounded" exactly as they do under
        ``pytest-timeout``. Matching the plugin's reading matters more than
        picking the stricter one: a suite that behaves differently in the two
        environments is the defect this module exists to close.
        """
        bound = self.default_timeout_s if timeout_s is None else float(timeout_s)
        if bound <= 0:
            self.disarm()
            return
        now = time.monotonic()
        with self._lock:
            self._label = label
            self._bound = bound
            self._armed_at = now
            self._deadline = now + bound

    def disarm(self) -> None:
        with self._lock:
            self._deadline = None
            self._label = None
            self._armed_at = None
            self._bound = None

    # ---- the thread --------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            with self._lock:
                deadline = self._deadline
                label = self._label
                bound = self._bound
                armed_at = self._armed_at
            if deadline is None or time.monotonic() < deadline:
                continue
            self._fire(
                label or "<unknown>",
                bound if bound is not None else self.default_timeout_s,
                time.monotonic() - (armed_at or time.monotonic()),
            )

    def _fire(self, label: str, bound: float, waited: float) -> None:
        """Report, reap, and terminate. Does not return."""
        # FIRST, before anything is written: put the real fds back. Under the
        # default --capture=fd everything below would otherwise land in a temp
        # file that os._exit() guarantees nobody reads.
        if self._config is not None:
            _restore_output(self._config)

        stream = _emergency_stream()
        if stream is not None:
            try:
                stream.write(_banner(label, bound, waited))
                stream.flush()
            except Exception:
                pass
            try:
                # Names the stuck frame — the whole reason a reader can act on
                # this instead of only knowing that something, somewhere, hung.
                faulthandler.dump_traceback(file=stream)
                stream.flush()
            except Exception:
                pass

        reaped = _reap_children()
        if stream is not None and reaped:
            try:
                stream.write(
                    f"\n{BANNER_TOKEN}: reaped {len(reaped)} orphaned "
                    f"child process(es): {sorted(reaped)}\n"
                )
                stream.flush()
            except Exception:
                pass

        # os._exit, not sys.exit: SystemExit is an exception, and an exception
        # raised on this thread would be swallowed here while the main thread
        # stayed blocked exactly where it was. Nothing short of ending the
        # process ends a wedge that the main thread cannot be woken from.
        os._exit(TIMEOUT_EXIT_CODE)


def _banner(label: str, bound: float, waited: float) -> str:
    rule = "=" * 78
    return (
        f"\n{rule}\n"
        f"{BANNER_TOKEN}\n"
        f"{rule}\n"
        f"KILLED AT A TIME LIMIT. This is NOT a test failure: nothing asserted\n"
        f"the wrong value. This run was terminated because one item stopped\n"
        f"making progress and would otherwise have hung indefinitely.\n"
        f"\n"
        f"  hung item : {label}\n"
        f"  bound     : {bound:.0f}s\n"
        f"  waited    : {waited:.0f}s\n"
        f"  exit code : {TIMEOUT_EXIT_CODE} (pytest itself only ever returns 0-5)\n"
        f"\n"
        f"Why this bound exists rather than pytest-timeout's: that plugin is not\n"
        f"active in this run, so `timeout` in pyproject.toml and every\n"
        f"@pytest.mark.timeout(...) are being ignored. Install it with\n"
        f"`pip install -r requirements-dev.txt` for per-test failures that let\n"
        f"the rest of the run continue; this fallback can only end the process.\n"
        f"\n"
        f"Every thread's stack follows. The hung item's frame is in the main\n"
        f"thread and names the exact line that stopped returning.\n"
        f"{rule}\n"
    )


def _restore_output(config) -> None:
    """Ask pytest to put the real stdout/stderr file descriptors back.

    The one step that makes the diagnostic visible under a DEFAULT run. Pytest's
    ``capturemanager`` owns the ``dup2`` over fds 1 and 2;
    ``suspend_global_capture`` undoes it, which is the same call pytest itself
    makes before dropping into ``--pdb``. After it returns, fd 2 is the terminal
    again and :func:`_emergency_stream` can hand :mod:`faulthandler` a real one.

    ``in_=False`` leaves stdin alone: this process is about to die and has no use
    for it, and touching it risks an exception on the path that must not fail.

    Every failure is swallowed. This runs while the run is already lost, so the
    worst case must be "the banner did not print", never "the watchdog raised on
    its way to killing the process and printed nothing at all".
    """
    try:
        manager = config.pluginmanager.getplugin("capturemanager")
    except Exception:
        return
    if manager is None:
        return
    try:
        manager.suspend_global_capture(in_=False)
    except Exception:
        pass
