"""PS-171 arm A (RE-RUN) — the LAYER-ON side of the control comparison.

WHY THIS FILE EXISTS
--------------------
The first arm A was run with ``abd_harness.py`` and its log was written as
``A.log`` — which ``.gitignore:183`` (``*.log``) silently swallowed, so the
record cited arm A's numbers with no committed reading behind them. Arm A is the
ONLY arm with persona's masking layer INSTALLED, so losing it left the record's
headline — a *differential* claim, "the stall is unchanged with the layer
absent" — with no layer-ON side to compare against. Code review blocked on
exactly that, correctly.

This re-run restores that side of the comparison, and it does so on the FIXED
instrument rather than the one the original arm A used.

THE INSTRUMENT, AND WHY IT DIFFERS FROM THE ORIGINAL ARM A
----------------------------------------------------------
The original arms A/B/D sampled CPU with ``ps pcpu``, which is an average over
the process's WHOLE LIFETIME: a process that spun hard for ten seconds and then
blocked forever keeps reporting a high number. That makes **spinning** and
**blocked** indistinguishable — the single distinction this ticket turns on.

Arms E/F used the corrected sampler (``utime+stime`` deltas read from
``/proc/<pid>/stat``, an INSTANTANEOUS reading). This re-run uses that corrected
sampler, so arm A now lands on the same instrument as the arm it is compared
against. That is the point of re-running it rather than merely re-labelling it.

The two schemas are therefore deliberately distinguishable in the committed
readings, and the record says which file carries which:

    lifetime-average schema : cpu_total / threads_total / stats / phase
    instantaneous schema    : cpu / rss_mb / thr

Everything else about the gesture is held identical to the original arm A:
headless, ``about:blank``, tabs opened one at a time via ``ctx.new_page()``.
"""
import json
import os
import signal
import sys
import time

sys.path.insert(0, "/workspace/persona")

LOG = open(os.environ.get("PS171_LOG", "/tmp/ps171/A2.log"), "w", buffering=1)


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

    Never ``ps pcpu`` — see the module docstring. A blocked process reads near
    zero here, and that is the whole reason this sampler exists.
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
    """Run ``fn`` under a hard SIGALRM deadline so a stall is OBSERVED, not waited on."""
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


NTABS = int(os.environ.get("PS171_NTABS", "4"))
DEADLINE = int(os.environ.get("PS171_DEADLINE", "40"))
RECOVERY = int(os.environ.get("PS171_RECOVERY", "40"))
LAYER = os.environ.get("PS171_LAYER", "1") == "1"

from invisible_playwright import InvisiblePlaywright  # noqa: E402
from src.services.verify.masking_layer import (  # noqa: E402
    DEFAULT_LOCALE,
    context_for,
    install_firefox_layer,
)

rec = {"arm": "A2", "method": "new_page", "layer": LAYER, "url": "about:blank",
       "tabs": []}
say(f"START arm A re-run | layer={'ON' if LAYER else 'OFF'} headless "
    f"ntabs={NTABS} deadline={DEADLINE}s | INSTANTANEOUS cpu sampler")
try:
    with InvisiblePlaywright(headless=True, humanize=False,
                             locale=DEFAULT_LOCALE) as live:
        ctx, note = context_for(live)
        say(f"LAUNCH {round(time.time()-_t0,2)}s | {note}")

        if LAYER:
            r = install_firefox_layer(ctx, 12345, locale=DEFAULT_LOCALE)
            rec["layer_installed"] = list(r.installed)
            rec["layer_failed"] = dict(r.failed)
            say(f"LAYER INSTALLED installed={sorted(r.installed)} "
                f"failed={dict(r.failed)}")
            if not r.installed:
                say("!! LAYER EMPTY — this is NOT a layer-ON arm; do not "
                    "report it as one")
        else:
            rec["layer_installed"] = []
            say("LAYER: NOT INSTALLED")

        first = ctx.new_page()
        first.goto("about:blank", timeout=30000)
        say("tab1 open | " + json.dumps(sample()))

        for i in range(2, NTABS + 1):
            def op():
                ctx.new_page().goto("about:blank", timeout=DEADLINE * 1000)
            ok, secs, detail = guarded(op, DEADLINE)
            say(f"--- tab{i} open ok={ok} {secs}s {detail}")

            # Ping tab 1: the discriminator between "the NEW tab stalled" and
            # "the browser stopped answering at all".
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
