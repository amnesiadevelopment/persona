"""PS-171 arm E (compact): does the tab-3 stall survive REMOVING the automation
channel from the tab-opening gesture?

Arms A (layer on, headless), B (layer off, headless) and D (layer off, HEADFUL)
all stalled at tab 3 on about:blank. All three opened tabs with playwright's
``ctx.new_page()`` — a juggler protocol command. THAT IS NOT THE OWNER'S
GESTURE; he opens a tab in the UI. So the stall could still be the automation
channel rather than the browser, and attributing it to the product without
testing that is the instrument-not-product error PS-14 records three times.

Here tabs are opened by ``window.open()`` evaluated INSIDE an existing page, so
the tab is created by the browser's own code path. If tab 3 still stalls, the
channel is exonerated and the browser owns it.

Writes its own log (line-buffered, flushed per line) rather than relying on a
shell redirect, because several long background runs lost their stdout.
"""
import json
import os
import signal
import sys
import time

sys.path.insert(0, "/workspace/persona")

LOG = open(os.environ.get("PS171_LOG", "/tmp/ps171/E.log"), "w", buffering=1)


def say(msg):
    LOG.write(msg + "\n")
    LOG.flush()
    print(msg, flush=True)


CLK = os.sysconf("SC_CLK_TCK")
ENGINE_DIR = os.path.expanduser("~/.cache/invisible-playwright")
_t0 = time.time()
_prev = {}


class Blocked(Exception):
    pass


signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(Blocked()))


def sample():
    """Per-process state + INSTANTANEOUS cpu (utime+stime deltas from /proc).

    Never ``ps pcpu``: that is a LIFETIME AVERAGE, so a process that spun hard
    and then blocked forever keeps reporting a high number — which would make
    "spinning" and "blocked" indistinguishable, the one distinction that
    decides where this defect lives.
    """
    procs, cpu_total, thr = [], 0.0, {}
    for pid in sorted(p for p in os.listdir("/proc") if p.isdigit()):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if ENGINE_DIR not in fh.read().decode("utf8", "replace"):
                    continue
            with open(f"/proc/{pid}/stat") as fh:
                raw = fh.read()
        except Exception:
            continue
        t = raw[raw.rindex(")") + 2:].split()
        ticks = int(t[11]) + int(t[12])
        cpu = None
        p = _prev.get(pid)
        _prev[pid] = (ticks, time.time())
        if p:
            dt = time.time() - p[1]
            if dt > 0:
                cpu = round((ticks - p[0]) / CLK / dt * 100, 1)
                cpu_total += cpu
        try:
            for tid in os.listdir(f"/proc/{pid}/task"):
                with open(f"/proc/{pid}/task/{tid}/stat") as f2:
                    r2 = f2.read()
                s2 = r2[r2.rindex(")") + 2:].split()[0]
                thr[s2] = thr.get(s2, 0) + 1
        except Exception:
            pass
        procs.append({"pid": int(pid), "st": t[0], "cpu": cpu,
                      "rss_mb": round(int(t[21]) * 4096 / 1048576, 1)})
    return {"t": round(time.time() - _t0, 1), "n": len(procs),
            "cpu": round(cpu_total, 1),
            "rss_mb": round(sum(p["rss_mb"] for p in procs), 1),
            "thr": thr}


def guarded(fn, deadline):
    a = time.time()
    signal.alarm(deadline)
    try:
        fn()
        signal.alarm(0)
        return True, round(time.time() - a, 2), ""
    except Blocked:
        return False, round(time.time() - a, 2), f"BLOCKED>{deadline}s"
    except Exception as exc:
        signal.alarm(0)
        return False, round(time.time() - a, 2), f"{type(exc).__name__}: {exc}"[:150]


METHOD = os.environ.get("PS171_METHOD", "window_open")
NTABS = int(os.environ.get("PS171_NTABS", "4"))
DEADLINE = int(os.environ.get("PS171_DEADLINE", "20"))
RECOVERY = int(os.environ.get("PS171_RECOVERY", "20"))

from invisible_playwright import InvisiblePlaywright  # noqa: E402
from src.services.verify.masking_layer import DEFAULT_LOCALE, context_for  # noqa: E402

rec = {"arm": "E", "method": METHOD, "layer": False, "url": "about:blank",
       "tabs": []}
say(f"START method={METHOD} ntabs={NTABS} deadline={DEADLINE}s "
    f"(layer OFF, headless)")
try:
    with InvisiblePlaywright(headless=True, humanize=False,
                             locale=DEFAULT_LOCALE) as live:
        ctx, note = context_for(live)
        say(f"LAUNCH {round(time.time()-_t0,2)}s | {note}")
        first = ctx.new_page()
        first.goto("about:blank", timeout=30000)
        say("tab1 open | " + json.dumps(sample()))

        for i in range(2, NTABS + 1):
            if METHOD == "window_open":
                def op():
                    first.evaluate("window.open('about:blank','_blank')")
            else:
                def op():
                    ctx.new_page().goto("about:blank",
                                        timeout=DEADLINE * 1000)
            ok, secs, detail = guarded(op, DEADLINE)
            say(f"--- tab{i} open ok={ok} {secs}s {detail}")

            res = {}

            def ping():
                res["v"] = first.evaluate("1+1")
            pok, psecs, pdetail = guarded(ping, 8)
            npages = len(getattr(ctx, "pages", []) or [])
            say(f"    ping-tab1 ok={pok} {psecs}s {pdetail} pages={npages} | "
                + json.dumps(sample()))
            rec["tabs"].append({"tab": i, "open_ok": ok, "t_open_s": secs,
                                "detail": detail, "ping_ok": pok,
                                "t_ping_s": psecs, "pages": npages})
            if not ok:
                say(f"*** STALL at tab{i}: watching {RECOVERY}s for recovery")
                time.sleep(RECOVERY)
                rok, rsecs, rdetail = guarded(ping, 8)
                say(f"RECOVERY after {RECOVERY}s: ping ok={rok} {rsecs}s "
                    f"{rdetail} | " + json.dumps(sample()))
                rec["recovery"] = {"after_s": RECOVERY, "ping_ok": rok,
                                   "t_ping_s": rsecs, "detail": rdetail}
                break
        rec["outcome"] = "completed"
except Exception as exc:
    rec["outcome"] = f"EXC {type(exc).__name__}: {exc}"[:250]
    say("EXC " + rec["outcome"])

out = os.environ.get("PS171_OUT")
if out:
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2)
say("RESULT " + json.dumps(rec))
