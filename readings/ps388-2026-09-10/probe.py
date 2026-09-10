"""PS-388 — drive the DEGRADED arm's whole sequence against a REAL process tree.

⛔ READ THE BOUND FIRST. THE TREE IS NOT CHROMIUM. This container cannot launch
the Personium chromium engine at all — unprivileged user namespaces are denied
(`kernel.apparmor_restrict_unprivileged_userns=1`, and this user has no sudo),
so the engine exits FATAL "No usable sandbox!" ~3s in and the settle guard
correctly refuses to measure it. Measured here on BOTH survivor arms, which
report the identical CANNOT RUN:

    the launched group N never held a SETTLED tree of at least 3 live
    processes (peak 1, last 0) within 90s

So this probe substitutes `spawn_browser` with a REAL multi-process POSIX tree
in its own session — a shell that forks four `sleep` children — and drives
everything else unchanged:

    _launch_and_grow      REAL (its own settle loop, its own sweep contract)
    _stop_group_or_refuse REAL (a real SIGSTOP, read back from the process table)
    terminate()           REAL — the PRODUCT's own teardown, unsubstituted:
                          terminate -> reap_process_group ->
                          terminate_process_group (SIGTERM -> wait -> SIGKILL
                          on the GROUP)
    _survivors_or_refuse  REAL (psutil over the live process table)
    _resume_group         REAL
    _sweep_group          REAL

WHAT THAT DOES AND DOES NOT ESTABLISH, stated rather than blurred:

  ✅ the arm's SEQUENCE works end to end on a real tree, and its verdict tracks
     the product's teardown rather than a return value;
  ✅ the RED arm goes to FINDING and the QUIET arm goes to PASS — the pair that
     shows the arm DISCRIMINATES rather than merely FIRES;
  ✅ the wedge is real: the members are confirmed STOPPED before the teardown;
  ❌ NOTHING about a chromium wrapper tree's teardown. A `sleep` under a shell
     is not `fpchrome.AppImage` above a zygote above renderers, and the shapes
     differ in exactly the way PS-192 was about. The chromium verdict must come
     from the launch lane.

Run: `python3 readings/ps388-2026-09-10/probe.py`
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from src.services.browser import process as product_process  # noqa: E402
from src.services.browser import process_group  # noqa: E402
from src.services.verify import behaviour_checks as bc  # noqa: E402
from src.services.verify.behaviour import CANNOT_RUN, FINDING, PASS  # noqa: E402
from src.services.verify.behaviour import Context, run_check  # noqa: E402

#: A wrapper above four children, all in ONE new session — the closest a
#: sandbox-less container can get to the wrapper/zygote/renderer SHAPE the
#: check is about. `exec` on the last child is deliberately NOT used: the
#: wrapper must stay alive as a distinct process, exactly as fpchrome.AppImage
#: does above the browser.
_TREE = (
    "for i in 1 2 3 4; do sleep 300 & done; "
    "sleep 300"
)


def _fake_spawn(profile, **kwargs):
    """A REAL multi-process tree in its own session, recorded like the product.

    ⚠️ IT GOES THROUGH `popen_in_new_session`, NOT `subprocess.Popen`. That
    helper is what RECORDS the group on the handle, and the recording is half
    the PS-192 fix — a tree started without it resolves its group live, which
    goes blind the moment the leader is waited on. Substituting the helper
    would make this probe measure a shape the product does not produce.
    """
    return process_group.popen_in_new_session(
        ["/bin/sh", "-c", _TREE],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _sabotage_terminate(proc, name, timeout=5):
    """THE PRE-PS-192 DEFECT: signal ONLY the pid we hold, and nothing else.

    ⚠️ `os.kill` ON THE HELD PID, NOT `proc.terminate()` / `proc.kill()`. On
    persona's Linux path the handle's own methods are group-aware — they ARE
    the fix — so a "pre-fix shape" built on them would tear the group down,
    report zero survivors, and certify nothing. This is the shape the shipped
    falsification uses, for the reason its docstring states in capitals.
    """
    import signal

    try:
        os.kill(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
    except Exception:
        pass
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _run_arm(label: str, *, sabotage: bool) -> "tuple[str, str, list[str]]":
    home = tempfile.mkdtemp(prefix="pb-p388-")
    os.environ["PERSONA_HOME"] = home
    # `core.config` resolves PERSONA_HOME at import time, so the store modules
    # are reloaded here rather than trusted to see the new value.
    for mod in [m for m in list(sys.modules) if m.startswith("src.core.config")]:
        del sys.modules[mod]

    real_spawn = product_process.spawn_browser
    real_terminate = product_process.terminate
    product_process.spawn_browser = _fake_spawn
    if sabotage:
        product_process.terminate = _sabotage_terminate

    # `_launch_and_grow` and the arms import from the module at CALL time, so
    # patching the module attribute is what they will see.
    try:
        ctx = Context(home=home)
        entry = next(
            c
            for c in bc.CHECKS
            if c.name == "no-process-survives-a-degraded-session"
        )
        started = time.monotonic()
        outcome = run_check(entry, ctx)
        elapsed = time.monotonic() - started
        print(f"\n=== {label} ===")
        print(f"  status:       {outcome.status}")
        print(f"  detail:       {outcome.detail[:400]}")
        for ev in outcome.evidence:
            print(f"    | {ev}")
        print(f"  falsification: {outcome.falsification[:300]}")
        print(f"  launches: {ctx.launches}   wall clock: {elapsed:.1f}s")
        return outcome.status, outcome.detail, list(outcome.evidence)
    finally:
        product_process.spawn_browser = real_spawn
        product_process.terminate = real_terminate
        shutil.rmtree(home, ignore_errors=True)


def _leak_audit(label: str) -> int:
    """⛔ THE MOST IMPORTANT LINE IN THIS FILE. Did a STOPPED process escape?

    A leaked running process is a bug this project has measured. A leaked
    STOPPED one is worse: invisible to any CPU sampler, holding its RSS
    indefinitely, and reachable by no ordinary teardown ever again. This arm
    plants exactly that state on purpose, so the audit is the proof the undo
    works — not the code review of it.
    """
    import psutil

    strays = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "status"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "sleep 300" in cmd:
                strays.append((p.info["pid"], p.info.get("status"), cmd[:60]))
        except Exception:
            continue
    stopped = [s for s in strays if s[1] == psutil.STATUS_STOPPED]
    print(f"\n=== LEAK AUDIT after {label} ===")
    print(f"  processes from this probe's trees still alive: {len(strays)}")
    print(f"  of those, STOPPED (the worst outcome):         {len(stopped)}")
    for s in strays[:10]:
        print(f"    | pid={s[0]} status={s[1]} {s[2]}")
    return len(strays)


def _time_healthy_teardown() -> float:
    """⭐ AC7's CONTROL. How long does the SAME tree take when it CAN answer?

    The degraded arm reports its own teardown duration, but a duration with
    nothing to compare it against is a number rather than a finding. This runs
    the identical tree through the identical `terminate()` with NO wedge, so
    the delta is attributable to the degradation and not to the tree.
    """
    proc = _fake_spawn(None)
    pgid = process_group.recorded_group(proc)
    # Let it settle, exactly as `_launch_and_grow` does.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if len(bc._survivors_or_refuse(pgid)) >= bc._MIN_LIVE_TREE:
            break
        time.sleep(bc._SAMPLE_INTERVAL)
    time.sleep(bc._STABLE_SAMPLES * bc._SAMPLE_INTERVAL)
    started = time.monotonic()
    product_process.terminate(proc, "p388ctl", timeout=10)
    elapsed = time.monotonic() - started
    bc._sweep_group(pgid)
    return elapsed


def main() -> int:
    print(__doc__)
    print(f"repo HEAD: {subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()}")

    healthy_seconds = _time_healthy_teardown()
    print(f"\n=== TIMING CONTROL: the SAME tree, NOT wedged ===")
    print(f"  terminate() returned in {healthy_seconds:.2f}s")

    quiet_status, _, quiet_ev = _run_arm("QUIET ARM (product teardown intact)", sabotage=False)
    quiet_leaks = _leak_audit("QUIET ARM")

    red_status, red_detail, red_ev = _run_arm(
        "RED ARM (terminate sabotaged to the pre-PS-192 shape)", sabotage=True
    )
    red_leaks = _leak_audit("RED ARM")

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(f"  QUIET arm: {quiet_status}   (expected {PASS})")
    print(f"  RED   arm: {red_status}   (expected {FINDING})")
    print(f"  strays after QUIET: {quiet_leaks}   after RED: {red_leaks}")
    print(f"  healthy teardown: {healthy_seconds:.2f}s   "
          f"(_TEARDOWN_GRACE = {bc._TEARDOWN_GRACE}s)")
    ok = (
        quiet_status == PASS
        and red_status == FINDING
        and quiet_leaks == 0
        and red_leaks == 0
    )
    print(f"\n  DISCRIMINATES AND LEAKS NOTHING: {ok}")
    if quiet_status == CANNOT_RUN:
        print("  (a CANNOT RUN here means the probe's own tree did not settle)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
