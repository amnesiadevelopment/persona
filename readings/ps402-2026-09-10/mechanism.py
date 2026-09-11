"""PS-402 — WHICH mechanism removes the Gecko tree? Two arms, one difference.

Established: the Gecko tree (10-11 processes) is NOT in the recorded group (2
members) — `firefox` calls setsid, so its pgid == sid == its own pid. Yet the
product's `terminate()` removes it, wedged or not.

Two candidate mechanisms remain, and the difference between them is what a
group-anchored gate can and cannot see:

  (B) THE IN-CHILD GRACEFUL HANDLER. `_child` installs
      `signal.signal(SIGTERM, stop_gracefully)`, which runs `session.teardown()`
      and logs `LIFECYCLE teardown-kill pids=[<gecko leader>]`. It kills the
      engine BY PID from inside the child. `terminate_process_group` sends
      SIGTERM to the group FIRST, so this is on the product's normal path.
  (C) THE PIPE CASCADE. firefox runs with `-wait-for-browser` and exits when the
      driver's pipe closes.

THE ISOLATION IS THE SIGNAL, not the wedge:

    arm `graceful` : wedge the tree, then SIGTERM the group  -> (B) available
    arm `hard`     : wedge the tree, then SIGKILL the group  -> (B) IMPOSSIBLE
                     (SIGKILL cannot be handled) and (C) blocked by the wedge

If `hard` leaks and `graceful` does not, the tree's removal depends on the ENGINE
or the CHILD acting — never on the group signal reaching it — and the group
signal is the only thing the survivor gate measures.
"""
import contextlib, os, signal, sys, threading, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

def drain(p):
    def pump():
        s = getattr(p, "stdout", None)
        if s is None: return
        with contextlib.suppress(Exception):
            for line in s: print("    CHILD:", line.rstrip(), flush=True)
    threading.Thread(target=pump, daemon=True).start()

def gecko(root):
    import psutil
    out = []
    with contextlib.suppress(Exception):
        for c in psutil.Process(root).children(recursive=True):
            with contextlib.suppress(Exception):
                a = " ".join(c.cmdline())
                if "invisible-playwright" in a and "firefox" in a: out.append(c.pid)
    return sorted(out)

def live(pids):
    import psutil
    alive, stopped = [], []
    for pid in pids:
        with contextlib.suppress(Exception):
            st = psutil.Process(pid).status()
            if st == psutil.STATUS_ZOMBIE: continue
            alive.append(pid)
            if st == psutil.STATUS_STOPPED: stopped.append(pid)
    return alive, stopped

def run(label, sig):
    from src.services.browser.process import spawn_browser
    from src.services.browser.process_group import process_group_survivors, recorded_group
    from src.services.verify.behaviour import Context
    import psutil
    signame = "SIGTERM (handler CAN run)" if sig == "term" else "SIGKILL (handler CANNOT run)"
    print(f"\n{'='*74}\n=== {label}: wedge the tree, then killpg {signame}\n{'='*74}", flush=True)
    ctx = Context(home=os.environ["PERSONA_HOME"])
    prof = ctx.make_profile(f"p402{label}", os_type="windows", engine="firefox")
    proc = spawn_browser(prof); drain(proc)
    pgid = recorded_group(proc); leader = proc.pid
    time.sleep(25.0)
    grp = process_group_survivors(pgid); g = gecko(leader)
    print(f"  leader={leader} pgid={pgid}  GROUP({len(grp)})={grp}")
    print(f"  GECKO({len(g)})={g}   gecko∩group={sorted(set(g)&set(grp))}")
    try:
        # RETRY the confirmation rather than the signal. A member forked
        # microseconds before the sweep is a member the first pass never saw, and
        # a partial freeze measures nothing (an unfrozen member can still react
        # to the pipe, which is the mechanism under test). Re-read the tree and
        # re-signal newcomers for up to 5s.
        froze = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            g = sorted(set(g) | set(gecko(leader)))
            for pid in g:
                with contextlib.suppress(Exception): os.kill(pid, signal.SIGSTOP)
            alive_now, froze = live(g)
            if froze and len(froze) == len(alive_now):
                g = alive_now
                break
            time.sleep(0.3)
        alive_now, froze = live(g)
        g = alive_now
        print(f"  WEDGE: {len(froze)} of {len(g)} confirmed STOPPED")
        if len(froze) != len(g):
            print("  ⚠️ PARTIAL FREEZE — stated, not glossed"); return {"label": label, "error": "partial freeze"}
        s = signal.SIGTERM if sig == "term" else signal.SIGKILL
        print(f"  ACTION: os.killpg({pgid}, {s.name})")
        with contextlib.suppress(Exception): os.killpg(pgid, s)
        with contextlib.suppress(Exception): proc.wait(timeout=12)
        time.sleep(12.0)
        alive, stopped = live(g)
        print(f"  ⛔ GECKO SURVIVORS 12s later: {len(alive)} of {len(g)}  (STOPPED: {len(stopped)})")
        print(f"     pids {alive}")
        return {"label": label, "signal": s.name, "group": len(grp), "gecko": len(g),
                "froze": len(froze), "gecko_survivors": len(alive), "still_stopped": len(stopped)}
    finally:
        for pid in g:
            with contextlib.suppress(Exception): psutil.Process(pid).send_signal(signal.SIGCONT)
            with contextlib.suppress(Exception): psutil.Process(pid).kill()
        with contextlib.suppress(Exception): os.killpg(pgid, signal.SIGKILL)
        time.sleep(1.5)
        a, _ = live(g)
        print(f"  LEAK AUDIT: {len(a)} alive after sweep")

if __name__ == "__main__":
    print("RESULT", run(sys.argv[1], sys.argv[2]))
