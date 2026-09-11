"""PS-402 AC3 — the GECKO SETTLE CURVE, measured rather than inherited.

The survivor gate's thresholds (`_MIN_LIVE_TREE=3`, `_STABLE_SAMPLES=8`,
`_TREE_GROW_TIMEOUT=90s`) were tuned on a CHROMIUM tree measured at 10 members
reaching size within ~7s. A Gecko tree's growth curve is a DIFFERENT curve, and
nobody had measured it against these constants: PS-171 measured 6 processes at
one tab and 11 at two, PS-349 measured 10 and 12 — neither says HOW FAST the
tree gets there, which is the only thing that decides whether the arm can be
built as specified.

⛔ WHAT THIS IS NOT. It is not the check. It samples the SAME thing the check
samples (`process_group_survivors` on the group recorded at launch) through the
SAME entry point (`spawn_browser`), so the numbers are comparable with the
check's own thresholds — but it prints the whole curve rather than a verdict,
because the question is the SHAPE and a pass/fail cannot carry one.

Sequence per arm:

    spawn_browser(profile) -> drain stdout -> recorded_group(proc)
      -> sample the group every 0.25s until it holds _MIN_LIVE_TREE for
         _STABLE_SAMPLES consecutive samples, or _TREE_GROW_TIMEOUT elapses
      -> report peak, settle time, and the whole series
      -> terminate(proc, name, timeout=10)   [the PRODUCT's own teardown]
      -> survivors after _TEARDOWN_GRACE

⚠️ THE PROFILE IS `os_type="windows"`, WHICH IS WHAT RESOLVES TO FIREFOX.
`coherent_engine("linux", "firefox")` returns CHROMIUM — executed, not read —
so a profile copied from `_survivor_profile` and edited at `engine=` launches
chromium and would silently measure the arm that is ALREADY gated.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

SAMPLE_INTERVAL = 0.25
MIN_LIVE_TREE = 3
STABLE_SAMPLES = 8
TREE_GROW_TIMEOUT = 90.0
TEARDOWN_GRACE = 5.0


def _drain(proc) -> None:
    def _pump() -> None:
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        with contextlib.suppress(Exception):
            for line in stream:
                if os.environ.get("P402_ECHO"):
                    print("CHILD:", line.rstrip(), flush=True)

    threading.Thread(target=_pump, daemon=True).start()


def _sweep(pgid) -> None:
    if pgid is None:
        return
    from src.services.browser.process_group import signallable_group

    target = signallable_group(pgid)
    if target is None:
        return
    with contextlib.suppress(Exception):
        os.killpg(target, getattr(signal, "SIGKILL", 9))


def measure(name: str, sabotage: bool) -> dict:
    from src.services.browser.process import spawn_browser, terminate
    from src.services.browser.process_group import (
        process_group_survivors,
        recorded_group,
    )
    from src.services.verify.behaviour import Context

    ctx = Context(home=os.environ["PERSONA_HOME"])
    # ⚠️ os_type WINDOWS. That is what resolves to firefox — see the module
    # docstring. engine="firefox" alone does NOT do it.
    profile = ctx.make_profile(name, os_type="windows", engine="firefox")

    t0 = time.monotonic()
    proc = spawn_browser(profile)
    print(f"[{name}] spawn_browser returned in {time.monotonic() - t0:.2f}s; "
          f"handle={type(proc).__name__} pid={getattr(proc, 'pid', None)} "
          f"_fork={getattr(proc, '_fork', None)}")
    _drain(proc)

    pgid = recorded_group(proc)
    print(f"[{name}] recorded_group -> {pgid}")
    if pgid is None:
        _sweep(None)
        with contextlib.suppress(Exception):
            proc.kill()
        return {"name": name, "error": "no recorded group"}

    series: list[tuple[float, int]] = []
    peak = 0
    stable = 0
    last = -1
    settled_at = None
    deadline = time.monotonic() + TREE_GROW_TIMEOUT
    try:
        while time.monotonic() < deadline:
            size = len(process_group_survivors(pgid))
            series.append((round(time.monotonic() - t0, 2), size))
            peak = max(peak, size)
            stable = stable + 1 if size == last and size >= MIN_LIVE_TREE else 0
            last = size
            if stable >= STABLE_SAMPLES:
                settled_at = round(time.monotonic() - t0, 2)
                break
            time.sleep(SAMPLE_INTERVAL)

        print(f"[{name}] SERIES (t=s since spawn, group members):")
        for t, n in series:
            print(f"           {t:7.2f}s  {n}")
        print(f"[{name}] peak={peak}  settled_at={settled_at}  "
              f"stable_run={stable}/{STABLE_SAMPLES}")

        if settled_at is None:
            _sweep(pgid)
            return {"name": name, "peak": peak, "settled_at": None,
                    "series": series, "error": "never settled"}

        if sabotage:
            # THE PRE-PS-192 SHAPE, and it must be os.kill on the HELD PID —
            # never proc.terminate(). On the fork path the handle's own kill()
            # IS group-aware (it IS the fix), so a control built on the handle
            # measures the fix and returns a comfortable zero.
            print(f"[{name}] SABOTAGE: os.kill on the held pid {proc.pid} only")
            with contextlib.suppress(Exception):
                os.kill(proc.pid, getattr(signal, "SIGTERM", 15))
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
            with contextlib.suppress(Exception):
                os.kill(proc.pid, getattr(signal, "SIGKILL", 9))
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            teardown_seconds = None
        else:
            started = time.monotonic()
            terminate(proc, name, timeout=10)
            teardown_seconds = round(time.monotonic() - started, 2)
            print(f"[{name}] terminate() returned in {teardown_seconds:.2f}s")

        time.sleep(TEARDOWN_GRACE)
        survivors = process_group_survivors(pgid)
        print(f"[{name}] survivors after grace {TEARDOWN_GRACE:.0f}s: "
              f"{len(survivors)} — pids {survivors}")
        return {"name": name, "peak": peak, "settled_at": settled_at,
                "series": series, "survivors": len(survivors),
                "teardown_seconds": teardown_seconds}
    finally:
        _sweep(pgid)
        leftover = []
        with contextlib.suppress(Exception):
            leftover = process_group_survivors(pgid)
        print(f"[{name}] LEAK AUDIT after sweep: {len(leftover)} alive")


if __name__ == "__main__":
    arm = sys.argv[1] if len(sys.argv) > 1 else "quiet"
    result = measure(f"p402{'q' if arm == 'quiet' else 'r'}", sabotage=(arm == "red"))
    print("RESULT", result.get("name"), "peak", result.get("peak"),
          "settled_at", result.get("settled_at"),
          "survivors", result.get("survivors"), "error", result.get("error"))
