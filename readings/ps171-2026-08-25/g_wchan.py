"""PS-171 arm G: WHERE is the stalled browser asleep?

Arm F established the stall is not a busy spin and not blocked-in-kernel-IO:
at the stall every firefox thread sits in S (interruptible sleep), total CPU
~1%, RSS ~1.2 GB of 16 GB. So the browser is IDLE-WAITING, not exhausted.

"Idle-waiting" is still three different defects, so this arm reads the KERNEL
WAIT CHANNEL (/proc/<tid>/wchan) and the userspace stack (/proc/<tid>/stack
where readable) of every firefox thread AT THE MOMENT OF THE STALL. A pile of
threads parked on a futex names a lock; threads parked on a pipe/socket read
names an IPC peer that never answered.

Reproduces the stall the same way arm F did (ctx.new_page(), which is the arm
that actually creates tabs), then dumps.
"""
import json
import os
import signal
import sys
import time

sys.path.insert(0, "/workspace/persona")

LOG = open(os.environ.get("PS171_LOG", "G.log"), "w", buffering=1)


def say(m):
    LOG.write(m + "\n")
    LOG.flush()


ENGINE_DIR = os.path.expanduser("~/.cache/invisible-playwright")
DEADLINE = int(os.environ.get("PS171_DEADLINE", "40"))


class Blocked(Exception):
    pass


signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(Blocked()))


def ff_pids():
    out = []
    for p in sorted(x for x in os.listdir("/proc") if x.isdigit()):
        try:
            with open(f"/proc/{p}/cmdline", "rb") as fh:
                cl = fh.read().decode("utf8", "replace")
            if ENGINE_DIR in cl:
                out.append((int(p), cl.replace("\x00", " ")[:120]))
        except Exception:
            continue
    return out


def dump_waits():
    """Per process: how many threads are parked on each wait channel."""
    rows = []
    for pid, cl in ff_pids():
        chans = {}
        states = {}
        try:
            tids = os.listdir(f"/proc/{pid}/task")
        except Exception:
            continue
        for tid in tids:
            try:
                with open(f"/proc/{pid}/task/{tid}/wchan") as fh:
                    w = fh.read().strip() or "0"
            except Exception:
                w = "?"
            try:
                with open(f"/proc/{pid}/task/{tid}/stat") as fh:
                    r = fh.read()
                st = r[r.rindex(")") + 2:].split()[0]
            except Exception:
                st = "?"
            chans[w] = chans.get(w, 0) + 1
            states[st] = states.get(st, 0) + 1
        rows.append({"pid": pid, "cmd": cl, "nthreads": len(tids),
                     "states": states,
                     "wchans": dict(sorted(chans.items(),
                                           key=lambda kv: -kv[1])[:8])})
    return rows


from invisible_playwright import InvisiblePlaywright  # noqa: E402
from src.services.verify.masking_layer import DEFAULT_LOCALE, context_for  # noqa: E402

say(f"START arm G (wchan at stall), deadline={DEADLINE}s, layer OFF")
try:
    with InvisiblePlaywright(headless=True, humanize=False,
                             locale=DEFAULT_LOCALE) as live:
        ctx, note = context_for(live)
        first = ctx.new_page()
        first.goto("about:blank", timeout=30000)
        say("tab1 open")
        p2 = ctx.new_page()
        p2.goto("about:blank", timeout=30000)
        say("tab2 open")
        say("BEFORE-STALL waits: " + json.dumps(dump_waits(), indent=1))

        say("--- opening tab3 (expected to stall)")
        a = time.time()
        signal.alarm(DEADLINE)
        try:
            ctx.new_page().goto("about:blank", timeout=DEADLINE * 1000)
            signal.alarm(0)
            say(f"tab3 opened OK in {round(time.time()-a,2)}s — NO STALL")
        except Blocked:
            say(f"tab3 BLOCKED >{DEADLINE}s — dumping wait channels")
            say("AT-STALL waits: " + json.dumps(dump_waits(), indent=1))
            time.sleep(20)
            say("AT-STALL+20s waits: " + json.dumps(dump_waits(), indent=1))
        except Exception as exc:
            signal.alarm(0)
            say(f"tab3 error {type(exc).__name__}: {exc}"[:200])
except Exception as exc:
    say(f"EXC {type(exc).__name__}: {exc}"[:250])
say("DONE")
