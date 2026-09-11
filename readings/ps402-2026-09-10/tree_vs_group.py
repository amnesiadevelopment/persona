"""PS-402 — WHERE ARE THE GECKO CHILDREN? group membership vs. the real tree.

The settle probe measured a firefox launch whose RECORDED GROUP holds 2–3
members while the child reported `BROWSER_STARTED` and a watch-pid. PS-171 and
PS-349 both measured a 6-to-12 process Gecko tree. Both cannot be true of the
same population, so this asks which population each is counting.

For a settled launch it prints, side by side:

    * `process_group_survivors(pgid)`        — what the survivor gate counts
    * every DESCENDANT of the leader by ppid — the whole tree, group or not
    * each descendant's own pgid / sid       — WHY it is or is not in the group

⚠️ THE DESCENDANT WALK IS THE CONTROL, NOT THE SUBJECT. The gate is anchored on
the group deliberately (PS-185: a name match reaped an unrelated browser, and on
a self-hosted runner the agent itself), and nothing here proposes changing that.
The walk exists to say whether a group-anchored count of a firefox launch is
counting the tree — because a count that is structurally 2 cannot satisfy
`_MIN_LIVE_TREE=3`, and a gate whose precondition cannot be met reports
CANNOT_RUN forever rather than gating anything.
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
                print("CHILD:", line.rstrip(), flush=True)

    threading.Thread(target=_pump, daemon=True).start()


def _descendants(root: int) -> list[int]:
    import psutil

    out: list[int] = []
    with contextlib.suppress(Exception):
        for child in psutil.Process(root).children(recursive=True):
            out.append(child.pid)
    return sorted(out)


def _row(pid: int) -> str:
    import psutil

    try:
        p = psutil.Process(pid)
        name = p.name()
        cmd = " ".join(p.cmdline())[:110]
    except Exception:
        return f"    pid {pid}: <gone>"
    pgid = sid = "?"
    with contextlib.suppress(Exception):
        pgid = os.getpgid(pid)
    with contextlib.suppress(Exception):
        sid = os.getsid(pid)
    return f"    pid {pid:>7}  pgid {pgid:>7}  sid {sid:>7}  {name:<18} {cmd}"


def main() -> None:
    from src.services.browser.process import spawn_browser, terminate
    from src.services.browser.process_group import (
        process_group_survivors,
        recorded_group,
    )
    from src.services.verify.behaviour import Context

    ctx = Context(home=os.environ["PERSONA_HOME"])
    profile = ctx.make_profile("p402t", os_type="windows", engine="firefox")

    proc = spawn_browser(profile)
    _drain(proc)
    pgid = recorded_group(proc)
    leader = proc.pid
    print(f"leader pid={leader}  recorded pgid={pgid}", flush=True)

    try:
        for t in (5, 10, 20, 30, 45):
            time.sleep(t - (0 if t == 5 else 0))
            group = process_group_survivors(pgid)
            tree = _descendants(leader)
            print(f"\n=== t≈{t}s ===")
            print(f"  GROUP  ({len(group)}): {group}")
            print(f"  TREE   ({len(tree)}): {tree}")
            union = sorted(set(group) | set(tree) | {leader})
            for pid in union:
                print(_row(pid))
            if t == 5:
                continue
            time.sleep(0)
        # And the teardown question: does the product's terminate() reach the
        # WHOLE tree, or only the group?
        group_before = process_group_survivors(pgid)
        tree_before = _descendants(leader)
        started = time.monotonic()
        terminate(proc, "p402t", timeout=10)
        print(f"\nterminate() returned in {time.monotonic() - started:.2f}s")
        time.sleep(5.0)
        print(f"  group before {len(group_before)} -> after "
              f"{len(process_group_survivors(pgid))}")
        # The tree walk needs a live leader; once it is reaped, ask psutil for
        # each previously-seen pid instead.
        import psutil

        alive = [p for p in tree_before if psutil.pid_exists(p)]
        print(f"  tree  before {len(tree_before)} -> after {len(alive)}  "
              f"still-alive pids {alive}")
        for pid in alive:
            print(_row(pid))
    finally:
        from src.services.browser.process_group import signallable_group

        target = signallable_group(pgid) if pgid else None
        if target is not None:
            with contextlib.suppress(Exception):
                os.killpg(target, getattr(signal, "SIGKILL", 9))
        import psutil

        for pid in _descendants(leader):
            with contextlib.suppress(Exception):
                psutil.Process(pid).kill()


if __name__ == "__main__":
    main()
