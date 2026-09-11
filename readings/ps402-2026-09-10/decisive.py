"""PS-402 — THE DECISIVE EXPERIMENT: what actually removes the Gecko tree?

`tree_vs_group.py` measured that a persona firefox launch's RECORDED GROUP holds
2 members (the forked python leader + the playwright node driver) while the
Gecko tree is 11 processes in a DIFFERENT session (`firefox` calls setsid, so
its pgid == sid == its own pid). The tree nonetheless disappeared on
`terminate()`.

Two mechanisms could explain that, and they have OPPOSITE consequences for the
survivor gate:

  (A) THE GROUP SIGNAL reached it — the PS-192 fix, the thing the gate measures.
  (B) THE CHILD'S OWN SIGTERM HANDLER reached it — `_child` installs
      `signal.signal(SIGTERM, stop_gracefully)`, which runs `session.teardown()`
      and logs `LIFECYCLE teardown-kill pids=[...]`. That is a GRACEFUL,
      in-child, playwright-level teardown, and it is NOT what the survivor gate
      counts, signals, or would notice regressing.

The experiment that separates them: SIGKILL the held pid. SIGKILL cannot be
handled, so mechanism (B) is structurally unavailable — nothing in the child
runs. If the Gecko tree SURVIVES that, then (B) was what removed it and the
group teardown never reached the tree at all.

⚠️ CONTROL, so this is not one reading: the SAME sequence with SIGTERM (which
DOES let the handler run) is measured in the same script, on its own launch.
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


def _gecko(root: int) -> list[int]:
    """The Gecko tree: descendants of the leader whose argv is the engine."""
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
    """Genuinely RUNNING pids — a zombie is not a survivor."""
    import psutil

    out = []
    for pid in pids:
        with contextlib.suppress(Exception):
            p = psutil.Process(pid)
            if p.status() != psutil.STATUS_ZOMBIE:
                out.append(pid)
    return out


def arm(label: str, how: str) -> dict:
    from src.services.browser.process import spawn_browser
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
    time.sleep(25.0)  # let the tree come up fully

    group = process_group_survivors(pgid)
    gecko = _gecko(leader)
    sids = {}
    for pid in gecko[:1]:
        with contextlib.suppress(Exception):
            sids[pid] = (os.getpgid(pid), os.getsid(pid))
    print(f"  leader={leader} recorded pgid={pgid}")
    print(f"  GROUP     ({len(group)}): {group}")
    print(f"  GECKO TREE({len(gecko)}): {gecko}")
    print(f"  gecko leader (pgid, sid): {sids}")
    print(f"  ⭐ is the gecko tree IN the recorded group? "
          f"{bool(set(gecko) & set(group))}")

    try:
        if how == "sigkill-leader":
            # Mechanism (B) is structurally unavailable: SIGKILL cannot be
            # handled, so `_child`'s stop_gracefully never runs.
            print(f"  ACTION: os.kill({leader}, SIGKILL) — handler CANNOT run")
            with contextlib.suppress(Exception):
                os.kill(leader, getattr(signal, "SIGKILL", 9))
        elif how == "sigterm-leader":
            print(f"  ACTION: os.kill({leader}, SIGTERM) — handler CAN run")
            with contextlib.suppress(Exception):
                os.kill(leader, getattr(signal, "SIGTERM", 15))
        elif how == "killpg-group":
            # What the PS-192 fix actually does, on the group it recorded.
            print(f"  ACTION: os.killpg({pgid}, SIGKILL) — the RECORDED group")
            with contextlib.suppress(Exception):
                os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)

        time.sleep(8.0)
        still = _alive(gecko)
        print(f"  ⛔ GECKO SURVIVORS 8s later: {len(still)} of {len(gecko)} "
              f"— pids {still}")
        return {"arm": label, "how": how, "group": len(group),
                "gecko": len(gecko), "gecko_survivors": len(still),
                "in_group": bool(set(gecko) & set(group))}
    finally:
        import psutil

        for pid in gecko:
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()
        with contextlib.suppress(Exception):
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
        time.sleep(1.0)
        print(f"  LEAK AUDIT: {len(_alive(gecko))} gecko alive after sweep")


if __name__ == "__main__":
    results = [arm(sys.argv[1], sys.argv[2])]
    print("\nRESULTS", results)
