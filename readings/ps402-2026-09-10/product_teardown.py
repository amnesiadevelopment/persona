"""PS-402 — THE LEAK, through the PRODUCT'S OWN TEARDOWN, at n=2.

`/tmp/wedge_gecko.py` measured 10 of 10 Gecko processes surviving a `killpg` on
the RECORDED GROUP once the engine could not react to the pipe cascade. That was
a raw `killpg`, which is the mechanism but not the product. This re-runs it
through `terminate()` from `..browser.process` — the function every close path in
the launcher calls, and the exact one the survivor gate hands its handle to — so
the result is a fact about the SHIPPED teardown rather than about a signal.

THE CONTROL IS THE SAME SCRIPT'S OTHER ARM. `quiet` is the identical sequence
with NO wedge: if it leaves zero, the difference between the two arms is the
engine's ability to REACT, which is the whole claim.

⚠️ WHY THE WEDGE IS NOT A CONTRIVANCE. It is the state PS-349 measured on nine
real sessions (`readings/ps349-2026-09-09/`): a 12-process firefox tree outliving
its session's last confirmed state by 95.4s, and one arm recording no child_exit
at all. This reproduces that observation's SHAPE through `spawn_browser` and the
product's own teardown, which is what PS-349's own Recommendation 5 asked for.

⚠️ AND A SIGSTOPPED PROCESS IS NOT UNKILLABLE. SIGKILL reaches a stopped process
— that is precisely why the wedge is a fair test rather than a trick: if the
Gecko tree were IN the recorded group, `killpg(SIGKILL)` would remove it wedged
or not. The freeze-the-node-driver arm in `isolation.py` is that control and it
removed 10 of 10, because the node driver IS in the group. What the wedge removes
is not the tree's killability but the CASCADE — the only mechanism that was ever
reaching it.

Zombies are excluded from every count.
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


def _live(pids: list[int]) -> tuple[list[int], list[int]]:
    """(alive non-zombie pids, of which STOPPED)."""
    import psutil

    alive: list[int] = []
    stopped: list[int] = []
    for pid in pids:
        with contextlib.suppress(Exception):
            p = psutil.Process(pid)
            status = p.status()
            if status == psutil.STATUS_ZOMBIE:
                continue
            alive.append(pid)
            if status == psutil.STATUS_STOPPED:
                stopped.append(pid)
    return alive, stopped


def run(label: str, wedge: bool) -> dict:
    from src.services.browser.process import spawn_browser, terminate
    from src.services.browser.process_group import (
        process_group_survivors,
        recorded_group,
    )
    from src.services.verify.behaviour import Context

    kind = "WEDGED (engine cannot react)" if wedge else "QUIET (control, no wedge)"
    print(f"\n{'=' * 74}\n=== {label}: {kind}\n{'=' * 74}", flush=True)
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
    print(f"  leader={leader} recorded pgid={pgid}  gecko sid={gsid}")
    print(f"  RECORDED GROUP ({len(group)}): {group}")
    print(f"  GECKO TREE     ({len(gecko)}): {gecko}")
    print(f"  gecko ∩ group = {sorted(set(gecko) & set(group))}")

    froze: list[int] = []
    try:
        if wedge:
            for pid in gecko:
                with contextlib.suppress(Exception):
                    os.kill(pid, getattr(signal, "SIGSTOP", 19))
            _, froze = _live(gecko)
            print(f"  WEDGE: {len(froze)} of {len(gecko)} confirmed STOPPED")
            if len(froze) != len(gecko):
                print("  ⚠️ PARTIAL FREEZE — stated rather than presented as clean")

        started = time.monotonic()
        terminate(proc, f"p402{label}", timeout=10)
        seconds = time.monotonic() - started
        print(f"  terminate() returned in {seconds:.2f}s")

        time.sleep(TEARDOWN_GRACE + 5.0)
        group_after = []
        with contextlib.suppress(Exception):
            group_after, _ = _live(process_group_survivors(pgid))
        gecko_after, gecko_stopped = _live(gecko)
        print(f"  ⛔ AFTER terminate() + {TEARDOWN_GRACE + 5.0:.0f}s:")
        print(f"     recorded-group survivors: {len(group_after)} — {group_after}")
        print(f"     GECKO survivors:          {len(gecko_after)} of {len(gecko)} "
              f"— {gecko_after}")
        print(f"     of which still STOPPED:   {len(gecko_stopped)}")
        return {"label": label, "wedge": wedge, "pgid": pgid, "gecko_sid": gsid,
                "group": len(group), "gecko": len(gecko),
                "froze": len(froze),
                "teardown_seconds": round(seconds, 2),
                "group_survivors": len(group_after),
                "gecko_survivors": len(gecko_after),
                "gecko_stopped": len(gecko_stopped)}
    finally:
        import psutil

        for pid in gecko:
            with contextlib.suppress(Exception):
                psutil.Process(pid).send_signal(getattr(signal, "SIGCONT", 18))
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
        with contextlib.suppress(Exception):
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        time.sleep(1.5)
        left, _ = _live(gecko)
        print(f"  LEAK AUDIT: {len(left)} alive after sweep")


if __name__ == "__main__":
    label = sys.argv[1]
    print("RESULT", run(label, wedge=(sys.argv[2] == "wedge")))
