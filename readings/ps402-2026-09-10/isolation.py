"""PS-402 — ISOLATION: if the group signal does not reach the Gecko tree, what does?

`decisive.py` established, on two independent launches, that the Gecko tree is
NOT in the group the PS-192 fix records and signals: `firefox` calls setsid, so
its pgid == sid == its own pid, and the recorded group holds exactly 2 members
(the forked python leader + the playwright node driver). Yet the tree died on
every arm — including `SIGKILL` on the leader, where `_child`'s SIGTERM handler
structurally cannot run.

So a THIRD mechanism removes it, and this isolates it. The candidate is the
PIPE-CLOSURE CASCADE: the node driver is a CHILD of the forked leader, `firefox`
is launched with `-wait-for-browser` and holds the driver's pipe, so killing
anything upstream collapses the chain from the top.

THE ISOLATION: SIGSTOP the node driver FIRST, so it cannot act on a closed pipe
or a dead parent, and only then signal the group. If the Gecko tree survives
that, the cascade is what has been removing it and the group signal reaches the
tree on no path at all.

Second arm, the LEAK PATH the unit test drives: `wait()` the leader BEFORE the
teardown (the DoD #3 shape), then run the product's own `terminate()`.

⚠️ EVERY SURVIVOR COUNT HERE EXCLUDES ZOMBIES. A reaped-but-unwaited process is
not a leaked one, and counting it as such is the false-positive twin of the
vacuous zero.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _drain(proc) -> None:
    def _pump() -> None:
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        with contextlib.suppress(Exception):
            for line in stream:
                print("    CHILD:", line.rstrip(), flush=True)

    threading.Thread(target=_pump, daemon=True).start()


def _classify(root: int) -> tuple[list[int], list[int]]:
    """(gecko pids, node-driver pids) among the leader's descendants."""
    import psutil

    gecko: list[int] = []
    node: list[int] = []
    with contextlib.suppress(Exception):
        for child in psutil.Process(root).children(recursive=True):
            with contextlib.suppress(Exception):
                argv = " ".join(child.cmdline())
                if "invisible-playwright" in argv and "firefox" in argv:
                    gecko.append(child.pid)
                elif "playwright" in argv and "node" in argv:
                    node.append(child.pid)
    return sorted(gecko), sorted(node)


def _alive(pids: list[int]) -> list[int]:
    import psutil

    out = []
    for pid in pids:
        with contextlib.suppress(Exception):
            p = psutil.Process(pid)
            if p.status() != psutil.STATUS_ZOMBIE:
                out.append(pid)
    return out


def arm(label: str, how: str) -> dict:
    from src.services.browser.process import spawn_browser, terminate
    from src.services.browser.process_group import (
        process_group_survivors,
        recorded_group,
    )
    from src.services.verify.behaviour import Context

    print(f"\n{'=' * 74}\n=== ARM {label}: {how}\n{'=' * 74}", flush=True)
    ctx = Context(home=os.environ["PERSONA_HOME"])
    profile = ctx.make_profile(f"p402{label}", os_type="windows", engine="firefox")

    proc = spawn_browser(profile)
    _drain(proc)
    pgid = recorded_group(proc)
    leader = proc.pid
    time.sleep(25.0)

    group = process_group_survivors(pgid)
    gecko, node = _classify(leader)
    print(f"  leader={leader} pgid={pgid}")
    print(f"  GROUP({len(group)}): {group}   NODE: {node}")
    print(f"  GECKO({len(gecko)}): {gecko}")
    print(f"  gecko in group? {bool(set(gecko) & set(group))}")

    try:
        if how == "freeze-node-then-killpg":
            # Take the cascade OUT of the mechanism: a stopped node driver
            # cannot react to a closed pipe or to a dead parent.
            for pid in node:
                with contextlib.suppress(Exception):
                    os.kill(pid, getattr(signal, "SIGSTOP", 19))
            import psutil

            froze = []
            for pid in node:
                with contextlib.suppress(Exception):
                    if psutil.Process(pid).status() == psutil.STATUS_STOPPED:
                        froze.append(pid)
            print(f"  FROZE node driver(s): {froze} (confirmed STOPPED)")
            if not froze:
                print("  ⛔ could not confirm the freeze — this arm measures "
                      "nothing and says so rather than reporting a result")
                return {"arm": label, "how": how, "error": "freeze unconfirmed"}
            print(f"  ACTION: os.killpg({pgid}, SIGKILL) — the RECORDED group, "
                  "with the cascade disabled")
            with contextlib.suppress(Exception):
                os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        elif how == "reap-leader-then-terminate":
            # The DoD #3 leak path the unit test drives: the leader is waited
            # on BEFORE the teardown, which is what blinds a live re-resolution.
            print(f"  ACTION: proc.wait() first (leader reaped), then the "
                  "product's terminate()")
            with contextlib.suppress(Exception):
                os.kill(leader, getattr(signal, "SIGTERM", 15))
            with contextlib.suppress(Exception):
                proc.wait(timeout=15)
            started = time.monotonic()
            terminate(proc, f"p402{label}", timeout=10)
            print(f"  terminate() returned in {time.monotonic() - started:.2f}s")

        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        time.sleep(10.0)
        still = _alive(gecko)
        node_still = _alive(node)
        print(f"  ⛔ GECKO SURVIVORS 10s later: {len(still)} of {len(gecko)} "
              f"— pids {still}")
        print(f"     node driver alive: {len(node_still)} — {node_still}")
        return {"arm": label, "how": how, "group": len(group),
                "gecko": len(gecko), "gecko_survivors": len(still),
                "node_survivors": len(node_still)}
    finally:
        import psutil

        for pid in node + gecko:
            with contextlib.suppress(Exception):
                psutil.Process(pid).send_signal(getattr(signal, "SIGCONT", 18))
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
        with contextlib.suppress(Exception):
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        time.sleep(1.5)
        print(f"  LEAK AUDIT: {len(_alive(gecko + node))} alive after sweep")


if __name__ == "__main__":
    print("RESULT", arm(sys.argv[1], sys.argv[2]))
