"""PS-402 — CAN THE FALSIFICATION BE SATISFIED ON FIREFOX AT ALL?

This is the load-bearing arm, and it decides whether the firefox survivor arm can
EXIST rather than how it should be written.

`run_check` (`behaviour.py`) runs `falsify` FIRST and a check that fails its
self-test never reaches its verdict — it reports CANNOT_RUN. So a firefox
survivor arm is buildable ONLY IF the pre-PS-192 sabotage leaves an OBSERVABLE
survivor on a real Gecko tree. If it leaves zero, the arm is a permanent
CANNOT_RUN by construction and adding it would put a check in the lane that can
never certify anything.

THE SABOTAGE IS `_falsify_no_process_survives_a_closed_session`'s BODY VERBATIM:

    os.kill(proc.pid, SIGTERM) -> proc.wait(10) -> os.kill(proc.pid, SIGKILL)
    -> proc.wait(5) -> sleep(_TEARDOWN_GRACE) -> count survivors

⚠️ `os.kill` ON THE HELD PID, NEVER `proc.terminate()`. On the fork path the
handle's own `kill()` IS group-aware (it IS the PS-192 fix), so a control built
on the handle measures the fix and returns a comfortable zero. That sentence was
written ABOUT this path.

TWO POPULATIONS ARE COUNTED SEPARATELY, because they are the whole finding:

  * the RECORDED GROUP (`process_group_survivors(pgid)`) — what the shipped
    check counts;
  * the GECKO SESSION (`os.getsid()` of the engine leader) — where the 10-11
    process tree actually lives.

Zombies are excluded from both. A reaped-but-unwaited process is not a leaked
one, and counting it as such is the false-positive twin of the vacuous zero.

RUN IT TWICE (n=2). One reading of a teardown is an anecdote — this project has
retracted single-reading attributions twice in one day.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

TEARDOWN_GRACE = 5.0


def _drain(proc) -> None:
    def _pump() -> None:
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        with contextlib.suppress(Exception):
            for line in stream:
                print("    CHILD:", line.rstrip(), flush=True)

    threading.Thread(target=_pump, daemon=True).start()


def _gecko(root: int) -> list[int]:
    import psutil

    out: list[int] = []
    with contextlib.suppress(Exception):
        for child in psutil.Process(root).children(recursive=True):
            with contextlib.suppress(Exception):
                argv = " ".join(child.cmdline())
                if "invisible-playwright" in argv and "firefox" in argv:
                    out.append(child.pid)
    return sorted(out)


def _alive(pids: list[int]) -> list[int]:
    import psutil

    out = []
    for pid in pids:
        with contextlib.suppress(Exception):
            p = psutil.Process(pid)
            if p.status() != psutil.STATUS_ZOMBIE:
                out.append(pid)
    return out


def _session_members(sid: int) -> list[int]:
    """Every live process in session ``sid`` — the population the group misses."""
    import psutil

    out = []
    for p in psutil.process_iter(["pid", "status"]):
        with contextlib.suppress(Exception):
            if os.getsid(p.info["pid"]) == sid and p.info["status"] != psutil.STATUS_ZOMBIE:
                out.append(p.info["pid"])
    return sorted(out)


def run(label: str) -> dict:
    from src.services.browser.process import spawn_browser
    from src.services.browser.process_group import (
        process_group_survivors,
        recorded_group,
    )
    from src.services.verify.behaviour import Context

    print(f"\n{'=' * 74}\n=== RUN {label}: the pre-PS-192 sabotage, verbatim\n{'=' * 74}",
          flush=True)
    ctx = Context(home=os.environ["PERSONA_HOME"])
    profile = ctx.make_profile(f"p402{label}", os_type="windows", engine="firefox")

    proc = spawn_browser(profile)
    _drain(proc)
    pgid = recorded_group(proc)
    leader = proc.pid
    time.sleep(25.0)

    group = process_group_survivors(pgid)
    gecko = _gecko(leader)
    gsid = None
    if gecko:
        with contextlib.suppress(Exception):
            gsid = os.getsid(gecko[0])
    session = _session_members(gsid) if gsid else []
    print(f"  leader={leader} recorded pgid={pgid}  gecko sid={gsid}")
    print(f"  RECORDED GROUP  ({len(group)}): {group}")
    print(f"  GECKO TREE      ({len(gecko)}): {gecko}")
    print(f"  GECKO SESSION   ({len(session)}): {session}")

    try:
        print("  SABOTAGE: SIGTERM -> wait -> SIGKILL, on the HELD PID ONLY")
        with contextlib.suppress(Exception):
            os.kill(leader, getattr(signal, "SIGTERM", 15))
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        with contextlib.suppress(Exception):
            os.kill(leader, getattr(signal, "SIGKILL", 9))
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        time.sleep(TEARDOWN_GRACE)

        group_after = []
        with contextlib.suppress(Exception):
            group_after = _alive(process_group_survivors(pgid))
        gecko_after = _alive(gecko)
        session_after = _alive(session)
        print(f"  ⛔ AFTER THE SABOTAGE (+{TEARDOWN_GRACE:.0f}s grace):")
        print(f"     recorded-group survivors: {len(group_after)} — {group_after}")
        print(f"     gecko-tree   survivors: {len(gecko_after)} — {gecko_after}")
        print(f"     gecko-session survivors: {len(session_after)} — {session_after}")
        return {"run": label, "pgid": pgid, "gecko_sid": gsid,
                "group": len(group), "gecko": len(gecko),
                "group_survivors": len(group_after),
                "gecko_survivors": len(gecko_after),
                "session_survivors": len(session_after)}
    finally:
        import psutil

        for pid in gecko + session:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
        with contextlib.suppress(Exception):
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        time.sleep(1.5)
        print(f"  LEAK AUDIT: {len(_alive(gecko + session))} alive after sweep")


if __name__ == "__main__":
    print("RESULT", run(sys.argv[1]))
