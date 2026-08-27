"""PS-223 end-to-end verification on the USER'S PATH.

Not a unit test. This drives the REAL BrowserLauncher, spawns a REAL chromium,
kills persona's process UNCLEANLY (SIGKILL, so atexit never runs), then starts a
SECOND process against the same PERSONA_HOME and asks whether the guard holds.

That process boundary is the whole defect and no unit test crosses it.

Run:  python3 tools/ps223_verify.py <phase>
  launch    — spawn a real browser, record it, print the pid, then SIGKILL self
  inspect   — fresh process: scan survivors, report what the guard now says
  stale     — write a record for a genuinely dead process, prove it does NOT block
  cleanexit — fresh process: adopt the survivor, then exit CLEANLY (shutdown_all,
              the atexit path) and report whether the record — and so the guard —
              outlived an ordinary quit. This is the round-3 defect: shutdown_all
              reaps only _active_sessions, so it cannot kill a survivor, and it
              used to wipe the whole registry anyway.
  confirmed — fresh process: adopt the survivor and drive the exit dialog's
              CONFIRM handler, which must actually close the survivor the dialog
              promised to close (shutdown_all structurally cannot reach it).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _launcher():
    from src.services.browser.launcher import BrowserLauncher

    return BrowserLauncher()


def phase_launch() -> None:
    """Launch a real browser, then die the way a crash does."""
    from src.models.profile import Profile

    bl = _launcher()
    name = os.environ.get("PS223_PROFILE", "ps223-probe")
    profile = Profile(name=name, os_type="windows")

    ready = []
    bl.start_thread(
        profile,
        lambda m: print(f"   [log] {m}", flush=True),
        on_ready=lambda: ready.append(True),
    )

    # Give the real engine time to come up and be registered.
    for _ in range(60):
        if bl.is_running(name):
            break
        time.sleep(0.5)

    from src.core import config

    with open(config.SESSIONS_FILE, encoding="utf-8") as fh:
        recorded = json.load(fh)
    print(f"REGISTRY={json.dumps(recorded)}", flush=True)
    print(f"IS_RUNNING={bl.is_running(name)}", flush=True)
    sys.stdout.flush()

    # THE UNCLEAN EXIT. os.kill on ourselves with SIGKILL: atexit does not run,
    # shutdown_all does not run, the browser is left alive — exactly a crash or
    # a kill from Task Manager.
    os.kill(os.getpid(), 9)


def phase_inspect() -> None:
    """A FRESH persona process: what does the guard say about the survivor?"""
    name = os.environ.get("PS223_PROFILE", "ps223-probe")
    bl = _launcher()

    print(f"BEFORE_SCAN_IS_RUNNING={bl.is_running(name)}", flush=True)
    survivors, unknown = bl.scan_survivors()
    print(
        f"SURVIVORS={[ (r.profile, r.pid) for r in survivors ]}", flush=True
    )
    print(f"UNKNOWN={[ r.profile for r in unknown ]}", flush=True)
    print(f"AFTER_SCAN_IS_RUNNING={bl.is_running(name)}", flush=True)

    # Now the thing the user does: click launch again.
    from src.ui.actions import browser as actions
    from src.ui.state import AppState

    class _PM:
        def __init__(self):
            from src.models.profile import Profile

            self.profiles = {name: Profile(name=name, os_type="windows")}

    logs: list[str] = []
    started = {"v": False}
    real_start = bl.start_thread

    def spy_start(*a, **k):
        started["v"] = True
        return real_start(*a, **k)

    bl.start_thread = spy_start  # type: ignore[method-assign]
    actions.launch_or_stop(name, _PM(), bl, AppState(), logs.append)
    time.sleep(1.0)
    print(f"SECOND_LAUNCH_STARTED_BROWSER={started['v']}", flush=True)
    print(f"USER_SAW={logs}", flush=True)


def phase_stale() -> None:
    """A record whose process is genuinely gone must NOT block a launch."""
    import subprocess

    from src.core import config
    from src.services.browser.session_registry import (
        SessionRecord,
        SessionRegistry,
        capture_create_time,
    )

    name = "ps223-stale"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    ct = capture_create_time(dead.pid)
    dead.wait()

    reg = SessionRegistry(config.SESSIONS_FILE)
    reg.record(
        SessionRecord(
            profile=name,
            pid=dead.pid,
            create_time=ct,
            pgid=None,
            engine="chromium",
            started_at=time.time(),
            owner_pid=os.getpid(),
        )
    )
    print(f"WROTE_STALE_RECORD pid={dead.pid} (process is dead)", flush=True)

    bl = _launcher()
    survivors, unknown = bl.scan_survivors()
    print(f"SURVIVORS={[r.profile for r in survivors]}", flush=True)
    print(f"UNKNOWN={[r.profile for r in unknown]}", flush=True)
    print(f"IS_RUNNING={bl.is_running(name)}", flush=True)
    print(f"REGISTRY_AFTER={reg.load()}", flush=True)


def phase_cleanexit() -> None:
    """A CLEAN quit must not erase the guard over a browser it left running.

    shutdown_all reaps _active_sessions and nothing else, so the survivor it
    inherited is NOT killed by it. The question is whether the record — the
    only thing standing between the user and a second launch — is still there
    afterwards.
    """
    name = os.environ.get("PS223_PROFILE", "ps223-probe")
    bl = _launcher()

    survivors, unknown = bl.scan_survivors()
    print(f"SURVIVORS={[(r.profile, r.pid) for r in survivors]}", flush=True)
    print(f"IN_ACTIVE_SESSIONS={name in bl._active_sessions}", flush=True)
    pids = [r.pid for r in survivors]

    # THE CLEAN EXIT — the same call atexit makes.
    bl.shutdown_all()

    for pid in pids:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
        print(f"SURVIVOR_STILL_ALIVE_AFTER_SHUTDOWN_ALL pid={pid} {alive}", flush=True)

    from src.core import config

    try:
        with open(config.SESSIONS_FILE, encoding="utf-8") as fh:
            print(f"REGISTRY_AFTER_CLEAN_EXIT={fh.read().strip()}", flush=True)
    except FileNotFoundError:
        print("REGISTRY_AFTER_CLEAN_EXIT=<missing>", flush=True)

    # And the question that matters: does a NEXT persona still see it?
    nxt = _launcher()
    again, _ = nxt.scan_survivors()
    print(f"NEXT_PERSONA_SURVIVORS={[r.profile for r in again]}", flush=True)
    print(f"NEXT_PERSONA_IS_RUNNING={nxt.is_running(name)}", flush=True)


def phase_confirmed() -> None:
    """The exit dialog's CONFIRM must make good on what it promised.

    The dialog names survivors among the browsers it is about to close. This
    drives the real confirm handler and asks whether the survivor actually died.
    """
    name = os.environ.get("PS223_PROFILE", "ps223-probe")
    bl = _launcher()
    survivors, _ = bl.scan_survivors()
    pids = [r.pid for r in survivors]
    print(f"SURVIVORS={[(r.profile, r.pid) for r in survivors]}", flush=True)

    from src.ui.app import App

    app = App.__new__(App)
    app.bl = bl

    class _W:
        prevent_close = False
        on_event = None
        destroyed = False

        def destroy(self):
            type(self).destroyed = True

    class _P:
        window = _W()
        dialogs: list = []

        def show_dialog(self, d):
            type(self).dialogs.append(d)

        def pop_dialog(self):
            pass

        def update(self):
            pass

    app.page = _P()
    names = sorted(app._open_browser_names())
    print(f"DIALOG_WOULD_NAME={names}", flush=True)

    import flet as ft

    class _E:
        type = ft.WindowEventType.CLOSE

    app._on_window_event(_E())
    dlg = _P.dialogs[0]
    print(f"DIALOG_BODY={dlg.content.value!r}", flush=True)

    # Press "close them and exit".
    dlg.actions[1].on_click(None)
    time.sleep(2.0)

    for pid in pids:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
        print(f"SURVIVOR_ALIVE_AFTER_CONFIRM pid={pid} {alive}", flush=True)
    print(f"WINDOW_DESTROYED={_W.destroyed}", flush=True)


if __name__ == "__main__":
    {
        "launch": phase_launch,
        "inspect": phase_inspect,
        "stale": phase_stale,
        "cleanexit": phase_cleanexit,
        "confirmed": phase_confirmed,
    }[
        sys.argv[1]
    ]()
