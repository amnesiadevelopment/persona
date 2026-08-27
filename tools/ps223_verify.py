"""PS-223 end-to-end verification on the USER'S PATH.

Not a unit test. This drives the REAL BrowserLauncher, spawns a REAL chromium,
kills persona's process UNCLEANLY (SIGKILL, so atexit never runs), then starts a
SECOND process against the same PERSONA_HOME and asks whether the guard holds.

That process boundary is the whole defect and no unit test crosses it.

Run:  python3 tools/ps223_verify.py <phase>
  launch   — spawn a real browser, record it, print the pid, then SIGKILL self
  inspect  — fresh process: scan survivors, report what the guard now says
  stale    — write a record for a genuinely dead process, prove it does NOT block
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


if __name__ == "__main__":
    {"launch": phase_launch, "inspect": phase_inspect, "stale": phase_stale}[
        sys.argv[1]
    ]()
