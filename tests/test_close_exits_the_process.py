"""THE OS IS THE ORACLE: after a close, is there still a pid?

WHY A SUBPROCESS AND NOT A UNIT TEST. The second half of PS-303's report is
that the process survives the close, and the fix's backstop ends in
``os._exit(0)``. That cannot be asserted in-process — running it would end the
test runner — so every in-process test of it (``test_window_close_really_closes``)
must patch the force-exit out and assert that it was *armed*. Arming it is not
the same claim as the process ending, and the ticket is explicit that
termination must be "verified by the OS (no surviving pid), not by a log line
saying it is exiting". So this file spawns a REAL interpreter, drives the REAL
close path inside it, and then asks the operating system whether that pid is
gone.

WHAT THIS DOES AND DOES NOT COVER. It cannot press a real X on a real window:
this container has no display and the flet desktop client binary is not
present, so the native Flutter half is out of reach here and remains
hand-verified on the user's path (as PS-223's suite already notes). What it
covers is everything from flet's event dispatch downward — a real ``ft.Window``
receiving a real ``WindowEvent`` of type CLOSE, persona's real handler, its real
destroy, and its real exit backstop — with a stand-in only at the socket to the
client. The gap is therefore narrow and named, rather than papered over.

FALSIFICATION. Against the pre-fix ``_destroy_window`` the child never exits and
this test fails on the timeout, which is exactly the reported symptom rendered
as a test failure: a process that will not die.
"""

import os
import subprocess
import sys
import textwrap
import time

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: The child program. Kept as source text rather than a helper module so the
#: whole scenario the OS is being asked about is readable in one place.
_CHILD = textwrap.dedent(
    """
    import asyncio, sys, weakref
    sys.path.insert(0, {repo!r})

    import flet as ft
    from src.ui.app import App

    class _Session:
        # The one seam a test cannot have: the socket to the Flutter client.
        def __init__(self): self.invoked = []
        async def invoke_method(self, cid, name, args=None, timeout=None):
            self.invoked.append(name); return None
        async def after_event(self, *a, **k): return None
        def error(self, *a, **k): pass
        @property
        def index(self):
            class I:
                def get(self, _): return None
            return I()

    class _BL:
        def running_profile_names(self): return set({running!r})
        def survivors(self): return []
        def shutdown_all(self): print("SHUTDOWN_ALL", flush=True)
        def close_all_survivors(self): return []

    session = _Session()
    page = ft.Page(sess=session)
    object.__setattr__(page.window, "_parent", weakref.ref(page))

    app = App.__new__(App)
    app.bl = _BL()
    app.page = page
    app.stop_api_server = lambda: None
    app._install_close_guard(page)          # the real guard, the real handler

    async def main():
        # The real flet dispatch path for a native window CLOSE.
        await page.window._trigger_event("event", {{"type": "close"}})
        await asyncio.sleep(0)
        print("DESTROY_SENT" if "destroy" in session.invoked
              else "DESTROY_MISSING", flush=True)
        # Hold the loop open well past the exit grace period. If persona does
        # NOT end the process, this sleep is what keeps the pid alive and the
        # parent's wait times out — the reported symptom, reproduced.
        await asyncio.sleep(60)
        print("STILL_ALIVE", flush=True)

    asyncio.run(main())
    """
)


def _run_child(running=()):
    """Spawn the child and let the OS answer. Returns (exitcode, stdout)."""
    src = _CHILD.format(repo=REPO_ROOT, running=tuple(running))
    proc = subprocess.Popen(
        [sys.executable, "-c", src],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # NAMED, not inherited: text=True otherwise decodes with the platform's
        # locale encoding, which is cp1252 on the Windows machines this test
        # most needs to run on, while the child writes utf-8.
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    try:
        out, _ = proc.communicate(timeout=30)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return None, out  # None == the OS says the pid was still there


def test_closing_persona_actually_ends_the_process():
    """No browsers open, X clicked: the pid must be GONE.

    ``returncode is None`` means the child had to be killed — persona was still
    running after its own close, which is the owner's report.
    """
    code, out = _run_child()

    assert code is not None, (
        "the process was STILL RUNNING after the close and had to be killed "
        "— this is the reported bug.\\nchild output:\\n" + out
    )
    assert code == 0, f"expected a clean exit, got {code}\\n{out}"
    assert "DESTROY_SENT" in out, (
        "the window destroy never reached the client:\\n" + out
    )
    assert "STILL_ALIVE" not in out


def test_the_browsers_are_torn_down_before_the_process_is_forced_down():
    """``os._exit`` skips ``atexit``, where ``shutdown_all`` is registered.

    Forcing the exit without reaping first would trade an unclosable window for
    orphaned browser processes. The child prints from its ``shutdown_all``, so
    the OS-verified exit and the teardown are asserted in one run.
    """
    code, out = _run_child(running=())

    assert code == 0, f"child did not exit cleanly: {code}\\n{out}"
    assert "SHUTDOWN_ALL" in out, (
        "the process exited without reaping the browsers:\\n" + out
    )
