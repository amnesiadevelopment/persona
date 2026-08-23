"""The UI-driver bound, demonstrated by INDUCING hangs rather than asserting config.

PS-104. ``pyproject.toml``'s ``timeout = 120`` and the ``@pytest.mark.timeout(...)``
on both driven modules are ``pytest-timeout`` constructs, so all of them are
inert wherever that plugin is not loaded. CI installs it; an environment that
installs only the project does not. A UI-driver test was measured wedged for 25
minutes where the comment promised two.

WHAT THESE TESTS ASSERT, AND WHY THEY ARE SHAPED THIS WAY
---------------------------------------------------------
The ticket is explicit that the observable is **that the run terminates**, and
that it must be shown by inducing the condition rather than by checking a
timeout value was read from config. A test asserting ``getini("timeout") == 120``
would pass in exactly the environment where the bound does not exist — it reads
back the string this project wrote down, not the behaviour, which is the
always-green shape catalogued in the project's own knowledge base.

So every test here **creates a real hang** — a real blocking read on a real
child process, a real app that starts and never serves — and asserts the run
ended by itself, with a failure naming what it waited for, and that nothing was
left running. The whole point is that a test which fails its own bound and
leaves a flet server and a chromium behind has MOVED the hang, not fixed it.

Deliberately *not* marked ``ui_driver`` or ``requires_capability``: the bound has
to hold in the environments that cannot run the driven tier at all, so these
must not be skipped there. The heavier cases guard themselves individually and
say what is missing.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from tests.ui_driver import driver as driver_module
from tests.ui_driver.driver import SYSTEM_CHROMIUM, FletDriver, SemanticsNotAvailable
from tests.ui_driver.watchdog import (
    ChildWatchdog,
    UiDriverTimeout,
    child_pids,
    reap_process_tree,
    survivors,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A child that blocks forever holding its stdout pipe open. Reading that pipe
#: from the parent is a faithful, dependency-free analogue of the real wedge:
#: the parent blocks in a read on a pipe owned by a child, which is precisely
#: where playwright's synchronous API blocks when its node driver stops
#: answering, and precisely what no in-thread timeout can interrupt.
_BLOCKING_CHILD = "import time; time.sleep(600)"

#: Short bounds keep these tests fast. The mechanism is identical at 120s; what
#: is being asserted is that the deadline FIRES and the run ENDS, not its value.
FAST_TIMEOUT = 2.0


def _await_gone(pids, timeout: float = 10.0) -> list[int]:
    """Which of ``pids`` are still running, once the table has had time to catch up.

    WHY A POLL AND NOT A BARE ``survivors()``: a reaped process does not leave
    the process table at the instant the thing we were waiting on returns. The
    two are not the same event, and on the paths below they are not even on the
    same THREAD — ``proc.stdout.read()`` returns as soon as the pipe CLOSES,
    while the reap that closed it is still running in the watchdog thread. So
    reading the table immediately asks "is it gone yet?" a moment too early.

    On POSIX that gap is invisible, which is why this went unnoticed for two
    rounds: a terminated child becomes a zombie until it is waited on, and
    :func:`survivors` deliberately does not count a zombie as running. Windows
    has no zombie state and no equivalent filter, so the same in-between moment
    reads as a live SURVIVOR and the assertion fails — intermittently, only on
    ``windows-latest``, and never on a Linux container. That is exactly the
    shape the CI matrix exists to catch.

    This does NOT weaken the assertion it serves. It returns the moment nothing
    is left, so a correct reap pays only the poll interval; a process that
    genuinely survives is still reported, just ``timeout`` later. A leak fails
    the test either way — the only thing bought here is that a slow exit is no
    longer mistaken for one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = survivors(pids)
        if not left:
            return []
        time.sleep(0.1)
    return survivors(pids)


def _spawn_blocking_child() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _BLOCKING_CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


# --------------------------------------------------------------------------
# 1. The reap itself: does anything actually die?
# --------------------------------------------------------------------------


def test_reaping_a_tree_kills_grandchildren_rather_than_orphaning_them():
    """A child of a child must not survive its parent's death.

    This is the cleanup half of the ticket and it is the half that is easy to
    get wrong: signalling only the pid you hold leaves the grandchildren
    reparented to init — still running, still holding the port — while the
    cleanup code reads as if it worked. ``ft.run`` is exactly this shape, so
    the test spawns the same shape and checks the process table afterwards.
    """
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {_BLOCKING_CHILD!r}]); "
            "time.sleep(600)",
        ]
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and len(child_pids()) < 2:
            time.sleep(0.1)
        family = [parent.pid, *sorted(child_pids(parent.pid))]
        assert len(family) >= 2, (
            f"the fixture did not produce a grandchild to orphan: {family}. "
            f"Without one this test cannot detect the bug it exists for."
        )

        reap_process_tree(parent.pid, grace=2.0)

        left = _await_gone(family)
        assert left == [], (
            f"{len(left)} process(es) survived the reap: {left}. A grandchild "
            f"outliving its parent is how a 'successful' cleanup leaves a flet "
            f"server and a chromium running."
        )
    finally:
        reap_process_tree(parent.pid, grace=2.0)


# --------------------------------------------------------------------------
# 2. The bound: does a wedged read END, and does it end with a real diagnosis?
# --------------------------------------------------------------------------


def test_a_blocking_read_on_a_child_ends_itself_instead_of_hanging():
    """THE HEADLINE. A parent blocked on a child's pipe gets its control back.

    No timeout, signal or flag can interrupt this read — the thread is inside a
    blocking syscall on a pipe the child holds open. The watchdog does not try
    to interrupt it: it removes the child, which closes the pipe, which is what
    returns control. That is the entire mechanism, asserted here on the real
    thing rather than described.
    """
    proc = _spawn_blocking_child()
    watchdog = ChildWatchdog(timeout_s=FAST_TIMEOUT, poll_s=0.05)
    watchdog.register(proc.pid)
    watchdog.start()
    try:
        watchdog.pet("a child that never answers")
        started = time.monotonic()
        proc.stdout.read()  # blocks until the child is gone
        elapsed = time.monotonic() - started

        assert watchdog.expired, "the read returned but the watchdog never fired"
        assert elapsed < FAST_TIMEOUT + 15, (
            f"the read took {elapsed:.1f}s against a {FAST_TIMEOUT:.0f}s bound. "
            f"Terminating LATE is still terminating, but this is meant to be "
            f"comparable to the bound, not merely finite."
        )
        assert _await_gone([proc.pid]) == [], (
            "the read returned but the child is still running — the bound fired "
            "and left the process behind, which moves the hang instead of "
            "fixing it."
        )
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)


def test_the_timeout_failure_names_what_it_waited_for():
    """A failure that does not say what it was waiting for is a second puzzle.

    The ticket asks for "a clear failure naming what it waited for", because
    the agent that hits this cannot otherwise tell a broken change from a stuck
    suite. Asserted on the real error object the driver raises.
    """
    proc = _spawn_blocking_child()
    watchdog = ChildWatchdog(timeout_s=FAST_TIMEOUT, poll_s=0.05)
    watchdog.register(proc.pid)
    watchdog.start()
    try:
        watchdog.pet("the dropdown captioned 'Engine' to open")
        proc.stdout.read()
        error = watchdog.timeout_error()

        assert isinstance(error, UiDriverTimeout)
        assert isinstance(error, AssertionError), (
            "a bound that fires should read as a test FAILURE, not as an "
            "infrastructure error"
        )
        message = str(error)
        assert "the dropdown captioned 'Engine' to open" in message, (
            f"the failure does not name the operation that hung: {message!r}"
        )
        assert "pytest-timeout" in message, (
            "the failure should say the bound is plugin-independent — that is "
            "the fact the next reader needs and the one this ticket exists for"
        )
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)


# --------------------------------------------------------------------------
# 3. The false-positive side: a bound that fires on healthy work is worse
# --------------------------------------------------------------------------


def test_the_watchdog_does_not_fire_between_operations():
    """Idle time is NOT bounded, and that is deliberate rather than an oversight.

    A driven test reads its result back through the service layer in a
    subprocess while the browser is still open. A watchdog that kept running
    across that gap would reap a healthy browser and fail a passing test — a
    fix that manufactures a worse bug than the one it removes.
    """
    proc = _spawn_blocking_child()
    watchdog = ChildWatchdog(timeout_s=FAST_TIMEOUT, poll_s=0.05)
    watchdog.register(proc.pid)
    watchdog.start()
    try:
        watchdog.pet("a quick operation")
        watchdog.rest()
        time.sleep(FAST_TIMEOUT * 2.5)

        assert not watchdog.expired, (
            "the watchdog fired while no operation was in flight — it would "
            "reap a healthy browser during a service-layer read"
        )
        assert survivors([proc.pid]) == [proc.pid], (
            "a registered child was reaped despite no operation being in flight"
        )
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)


def test_a_nested_operation_does_not_disarm_the_bound_of_its_caller():
    """Reentrancy, asserted — the driver's compound gestures depend on it.

    ``select_option`` calls ``_open`` calls ``find_dropdown`` calls ``nodes``,
    and each is bounded. If an inner ``rest()`` cleared the deadline, the
    OUTERMOST gesture — the long compound one, the most likely to wedge — would
    silently run unbounded. That failure would be invisible: everything still
    passes, and the bound is simply gone.
    """
    proc = _spawn_blocking_child()
    watchdog = ChildWatchdog(timeout_s=FAST_TIMEOUT, poll_s=0.05)
    watchdog.register(proc.pid)
    watchdog.start()
    try:
        watchdog.pet("the outer gesture")
        watchdog.pet("an inner read")
        watchdog.rest()  # inner finishes; the outer bound must SURVIVE this

        proc.stdout.read()

        assert watchdog.expired, (
            "the inner rest() disarmed the outer operation, leaving the "
            "compound gesture unbounded"
        )
        assert watchdog.expired_label == "the outer gesture", (
            f"the failure names {watchdog.expired_label!r}; a reader needs the "
            f"outermost gesture, not whichever leaf call was in flight"
        )
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)


def test_a_child_spawned_after_the_baseline_is_reaped_even_if_never_registered():
    """Covers the wedge that happens DURING launch, before any pid is known.

    ``chromium.launch()`` is itself a blocking call, so a hang inside it stalls
    while the process it spawned has not been registered — the one window where
    registration cannot help. The baseline diff closes it.
    """
    watchdog = ChildWatchdog(timeout_s=FAST_TIMEOUT, poll_s=0.05)
    watchdog.mark_baseline()
    proc = _spawn_blocking_child()  # spawned AFTER the baseline, never registered
    watchdog.start()
    try:
        assert watchdog.registered == [], "this test must not register the child"
        watchdog.pet("the browser to launch")
        proc.stdout.read()

        assert watchdog.expired
        assert _await_gone([proc.pid]) == [], (
            "a process spawned during the wedged operation survived — a hang "
            "inside launch() would leave a browser running"
        )
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)


# --------------------------------------------------------------------------
# 4. The claim the whole ticket rests on: this works WITHOUT the plugin
# --------------------------------------------------------------------------

_INNER_TEST = '''
import subprocess, sys, time
import pytest
from tests.ui_driver.watchdog import ChildWatchdog, reap_process_tree, survivors

pytestmark = pytest.mark.timeout(1)  # INERT here: the plugin is disabled


def test_a_wedged_operation_fails_instead_of_hanging():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    watchdog = ChildWatchdog(timeout_s=3.0, poll_s=0.05)
    watchdog.register(proc.pid)
    watchdog.start()
    try:
        watchdog.pet("a child that never answers")
        proc.stdout.read()
        if watchdog.expired:
            raise watchdog.timeout_error()
    finally:
        watchdog.stop()
        reap_process_tree(proc.pid, grace=2.0)
'''


def test_the_bound_still_fires_with_pytest_timeout_disabled(tmp_path):
    """The property the ticket is actually about, proven the only honest way.

    The inner run disables the plugin outright (``-p no:timeout``), so the ini
    ``timeout`` AND the inner file's own ``@pytest.mark.timeout(1)`` are both
    dead — the exact environment where the 25-minute wedge was measured. The
    inner test then induces a real wedge.

    Two assertions, and the first is the one that matters: the inner run
    **finished at all**. An unbounded run would sit here until this outer
    timeout killed it, and that is what the ``timeout=`` below distinguishes —
    a subprocess timeout is an OUTER bound, so if the inner bound did not work
    we get a TimeoutExpired rather than a false pass.

    The inner file is written to ``tmp_path``, NOT into ``tests/``. This case
    deliberately provokes hangs and hard kills, and a kill between writing the
    file and the cleanup in ``finally`` would leave a real, collectable test
    file in the suite for every later run. The inner run still uses
    ``cwd=REPO_ROOT``, so ``tests.ui_driver`` imports resolve from a temp
    location exactly as they did from the repo.
    """
    inner = str(tmp_path / "test_ps104_inner_wedge.py")
    with open(inner, "w") as handle:
        handle.write(_INNER_TEST)
    try:
        started = time.monotonic()
        run = subprocess.run(
            [
                sys.executable, "-m", "pytest", inner,
                "-p", "no:timeout",       # the plugin is genuinely gone
                "-p", "no:randomly",
                "-x", "-q", "--no-header",
            ],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=180,
        )
        elapsed = time.monotonic() - started
        output = run.stdout + run.stderr

        assert run.returncode != 0, (
            f"the inner run PASSED, so no wedge was induced and this proves "
            f"nothing:\n{output[-3000:]}"
        )
        assert "UiDriverTimeout" in output or "UI driver timed out" in output, (
            f"the inner run failed for some other reason than its own bound:\n"
            f"{output[-3000:]}"
        )
        assert elapsed < 120, (
            f"the inner run took {elapsed:.0f}s; the bound was 3s. It ended, "
            f"but not on its own bound."
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the inner run HUNG with pytest-timeout disabled — the bound is "
            "still plugin-dependent, which is exactly the defect PS-104 exists "
            "to remove."
        )
    finally:
        if os.path.exists(inner):
            os.remove(inner)


# --------------------------------------------------------------------------
# 5. The real thing: a served app that starts and never becomes serviceable
# --------------------------------------------------------------------------


def test_an_app_that_starts_but_never_serves_fails_and_leaves_nothing_running(
    monkeypatch,
):
    """The ticket's exact failing shape, induced end to end.

    Not a child that fails to start — that already errors promptly. This one
    STARTS, stays alive, and never answers on its port, which is the case that
    used to hang. The startup bound is shortened so the test is quick; the
    mechanism under test is unchanged.

    The second assertion is the one that would catch a regression in the fix:
    the served process tree must be GONE afterwards. A bound that fails the
    test and leaves a flet server holding the port has moved the hang.
    """
    pytest.importorskip("flet", reason="flet not installed: nothing can be served")

    from tests.ui_driver import server as server_module

    monkeypatch.setattr(server_module, "STARTUP_TIMEOUT", 8.0)

    # Executed in the child before the app is built: it starts, and never
    # reaches ft.run, so the port is never served.
    never_serve = textwrap.dedent(
        """
        import time
        time.sleep(600)
        """
    )

    seen: list[int] = []
    started = time.monotonic()
    with pytest.raises(RuntimeError) as caught:
        with server_module.serve_app(REPO_ROOT, patch=never_serve) as app:
            seen.extend(app.descendants())
            pytest.fail("serve_app yielded an app that was never serving")
    elapsed = time.monotonic() - started

    message = str(caught.value)
    assert "did not serve" in message, (
        f"the failure does not name what it waited for: {message[:400]!r}"
    )
    assert elapsed < 90, (
        f"the readiness wait took {elapsed:.0f}s against an 8s bound — it "
        f"terminated, but not on its bound."
    )
    left = _await_gone(seen) if seen else []
    assert left == [], (
        f"{len(left)} served process(es) survived the failure: {left}. The "
        f"startup bound fired and left the app running, which is the hang "
        f"relocated rather than removed."
    )


# --------------------------------------------------------------------------
# 6. The other failure path: start() itself raising must not leak the browser
# --------------------------------------------------------------------------

#: A page that is valid HTML and is NOT a flet app. Serving this drives
#: wake_semantics() down its documented "no <flt-semantics-placeholder>" path,
#: which raises from INSIDE start(). That is the induction: a real chromium is
#: really launched, and the failure is a real one the driver already declares.
_NOT_A_FLET_APP = b"<!doctype html><html><body><h1>not a flet app</h1></body></html>"


@contextlib.contextmanager
def _serve_static_page(payload: bytes):
    """Serve one fixed page on a free port, in-thread. No flet required."""
    import http.server

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib's spelling
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # keep the test output readable
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    httpd = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_a_driver_whose_start_fails_leaves_no_browser_behind(monkeypatch):
    """``with FletDriver(...)`` must not leak children when start() RAISES.

    The context-manager protocol does not call ``__exit__`` when ``__enter__``
    raises, so ``close()`` — which carries all of the reaping — does not run on
    this path unless ``__enter__`` arranges it. ``start()`` ends with
    ``wake_semantics()``, which raises on two documented paths, so this is a
    path the real suite takes, not a contrived one: all of the driven call
    sites use ``with serve_app(...) as app, FletDriver(app.url) as drv:``.

    The watchdog does NOT cover this case and is not expected to: the raise is
    prompt, so ``_op``'s finally disarms the clock and nothing ever expires.
    There is nothing wedged to reap — there is a failure unwinding past
    children nobody is going to collect.

    Measured before the fix: **ten** surviving chromium processes and a
    watchdog thread that never stopped. The assertion is on the process table,
    not on whether a finally block ran.
    """
    pytest.importorskip("playwright", reason="playwright not installed")
    if not os.path.exists(SYSTEM_CHROMIUM):
        pytest.skip(f"chromium not runnable here: {SYSTEM_CHROMIUM} is absent")

    # The real 25s settle is headroom for the real app painting a splash. This
    # page is static and the mechanism under test is the unwind, not the wait.
    monkeypatch.setattr(driver_module, "SETTLE_MS", 1_000)

    before = child_pids()
    with _serve_static_page(_NOT_A_FLET_APP) as url:
        drv = FletDriver(url)
        with pytest.raises(SemanticsNotAvailable) as caught:
            with drv:
                pytest.fail(
                    "start() succeeded against a page that is not a flet app, "
                    "so no failure was induced and this proves nothing."
                )

    assert "placeholder" in str(caught.value), (
        f"the failure came from somewhere other than the induced path, so the "
        f"leak this guards was not the one exercised: {str(caught.value)[:300]!r}"
    )

    leaked = _await_gone(sorted(child_pids() - before))
    assert leaked == [], (
        f"{len(leaked)} process(es) survived a FAILED start(): {leaked}. "
        f"__enter__ raised without close() ever running, so the browser tree "
        f"outlived the test — the hang relocated rather than removed."
    )

    assert not [t for t in threading.enumerate() if t.name == "ui-driver-watchdog"], (
        "the watchdog thread outlived a failed start(); close() never ran, so "
        "the driver leaked its thread as well as its children."
    )
