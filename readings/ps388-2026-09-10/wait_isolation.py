"""Isolate WHY a degraded teardown costs the full timeout — one mechanism, alone.

The arm reports `terminate() returned in 10.00s` on a wedged session and
`0.00s` on a healthy one. That is a MEASUREMENT, and this is the ISOLATION:
which of the escalation's three legs spends the ten seconds, measured leg by
leg rather than inferred from the total.

Run: `python3 readings/ps388-2026-09-10/wait_isolation.py`
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.services.browser import process_group  # noqa: E402

TREE = "for i in 1 2 3; do sleep 60 & done; sleep 60"


def _leg(label: str, fn) -> float:
    started = time.monotonic()
    try:
        fn()
    except Exception as exc:
        print(f"  {label}: raised {type(exc).__name__}")
    elapsed = time.monotonic() - started
    print(f"  {label}: {elapsed:.2f}s")
    return elapsed


def _run(wedge: bool) -> None:
    proc = process_group.popen_in_new_session(
        ["/bin/sh", "-c", TREE], stdout=subprocess.DEVNULL
    )
    pgid = process_group.recorded_group(proc)
    time.sleep(1.0)
    pids = process_group.process_group_survivors(pgid)
    print(f"\n=== {'WEDGED (SIGSTOP)' if wedge else 'HEALTHY'} — tree {pids} ===")
    if wedge:
        for pid in pids:
            os.kill(pid, signal.SIGSTOP)
        time.sleep(0.5)

    # The three legs of `terminate_process_group`, run by hand and timed.
    _leg("killpg(SIGTERM)", lambda: os.killpg(pgid, signal.SIGTERM))
    _leg("proc.wait(timeout=10)", lambda: proc.wait(timeout=10))
    _leg("killpg(SIGKILL)", lambda: os.killpg(pgid, signal.SIGKILL))
    _leg("proc.wait(timeout=5)", lambda: proc.wait(timeout=5))
    time.sleep(0.5)
    print(f"  survivors: {process_group.process_group_survivors(pgid)}")


if __name__ == "__main__":
    print(__doc__)
    _run(wedge=False)
    _run(wedge=True)
    print(
        "\nCONCLUSION: SIGTERM to a STOPPED process is QUEUED, not delivered —\n"
        "the kernel holds it until the process is continued — so the wait()\n"
        "between the SIGTERM and the SIGKILL blocks for the WHOLE timeout.\n"
        "The SIGKILL that follows lands immediately and the tree goes, so the\n"
        "OUTCOME is correct; what the wedge costs is LATENCY, bounded by the\n"
        "caller's own timeout rather than by the browser."
    )
