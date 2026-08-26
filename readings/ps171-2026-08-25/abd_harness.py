"""PS-171 tab-stall harness.

Opens N tabs one at a time under persona's engine and records, per tab, how long
new_page() and goto() take — with a WATCHDOG THREAD sampling the firefox
processes (state / %cpu / rss / threads) every 2s throughout.

The watchdog is the point. "Lag" covers four different defects (UI stops
repainting, page stops loading, whole process stops responding, recovers after
N seconds) and they have different causes. A wall-clock number alone cannot tell
them apart; a spinning process and a blocked one look identical from the caller,
which sees only "new_page() has not returned". So the OS-level sample runs
independently of the blocked call and says which.

Arms (env):
  PS171_LAYER=0   packaged engine ONLY, none of persona's masking layer.
                  This is the internal control arm: if the stall is present
                  here it is NOT persona's per-tab spoof path.
  PS171_HEADLESS=0  run headful (needs a DISPLAY, e.g. under xvfb-run).
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/workspace/persona")

from invisible_playwright import InvisiblePlaywright  # noqa: E402

from src.services.verify.masking_layer import (  # noqa: E402
    DEFAULT_LOCALE,
    context_for,
    install_firefox_layer,
)

HEADLESS = os.environ.get("PS171_HEADLESS", "1") == "1"
LAYER = os.environ.get("PS171_LAYER", "1") == "1"
NTABS = int(os.environ.get("PS171_NTABS", "5"))
URL = os.environ.get("PS171_URL", "about:blank")
TAG = os.environ.get("PS171_TAG", "run")

_t0 = time.time()
_stop = threading.Event()
samples = []


def _now():
    return round(time.time() - _t0, 1)


def sample_procs():
    """One OS-level reading of every firefox process: state, cpu, rss, threads."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,stat,pcpu,rss,nlwp,etimes,comm"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception as exc:
        return {"t": _now(), "err": str(exc)[:80]}
    procs = []
    for line in out.strip().splitlines()[1:]:
        f = line.split(None, 6)
        if len(f) == 7 and "firefox" in f[6].lower():
            procs.append({
                "pid": int(f[0]), "stat": f[1], "cpu": float(f[2]),
                "rss_mb": round(int(f[3]) / 1024, 1), "threads": int(f[4]),
            })
    return {
        "t": _now(),
        "n": len(procs),
        "cpu_total": round(sum(p["cpu"] for p in procs), 1),
        "rss_mb_total": round(sum(p["rss_mb"] for p in procs), 1),
        "threads_total": sum(p["threads"] for p in procs),
        "stats": "".join(sorted(p["stat"][0] for p in procs)),
    }


def watchdog(phase):
    while not _stop.is_set():
        s = sample_procs()
        s["phase"] = phase[0]
        samples.append(s)
        print("  WD " + json.dumps(s), flush=True)
        _stop.wait(2.0)


phase = ["startup"]
th = threading.Thread(target=watchdog, args=(phase,), daemon=True)
th.start()

rec = {
    "tag": TAG, "headless": HEADLESS, "layer": LAYER,
    "url": URL, "ntabs": NTABS, "tabs": [],
}
kwargs = {"headless": HEADLESS, "humanize": False, "locale": DEFAULT_LOCALE}

try:
    a = time.time()
    engine = InvisiblePlaywright(**kwargs)
    with engine as live:
        ctx, note = context_for(live)
        rec["t_launch_s"] = round(time.time() - a, 2)
        rec["context_note"] = note
        print(f"LAUNCH {rec['t_launch_s']}s | {note}", flush=True)

        if LAYER:
            r = install_firefox_layer(ctx, 12345, locale=DEFAULT_LOCALE)
            rec["layer_installed"] = list(r.installed)
            rec["layer_failed"] = dict(r.failed)
            print(f"LAYER installed={r.installed} failed={r.failed}", flush=True)
        else:
            rec["layer_installed"] = []
            print("LAYER: NOT INSTALLED (control arm: packaged engine only)",
                  flush=True)

        pages = []
        for i in range(NTABS):
            n = i + 1
            e = {"tab": n}
            phase[0] = f"new_page{n}"
            print(f"--- tab {n}: new_page() at t={_now()}s", flush=True)
            a = time.time()
            pg = ctx.new_page()
            e["t_new_page_s"] = round(time.time() - a, 2)
            print(f"    new_page returned in {e['t_new_page_s']}s", flush=True)

            phase[0] = f"goto{n}"
            a = time.time()
            try:
                pg.goto(URL, timeout=180000)
                e["goto_ok"] = True
            except Exception as ex:
                e["goto_ok"] = False
                e["goto_err"] = f"{type(ex).__name__}: {ex}"[:200]
            e["t_goto_s"] = round(time.time() - a, 2)
            pages.append(pg)
            print(f"    goto {e['goto_ok']} in {e['t_goto_s']}s", flush=True)

            # Is the FIRST tab still alive while the newest one is open? This is
            # the "does the rest of the browser still work" axis.
            phase[0] = f"ping{n}"
            a = time.time()
            try:
                pages[0].evaluate("1+1")
                e["t_ping_tab0_s"] = round(time.time() - a, 3)
            except Exception as ex:
                e["t_ping_tab0_s"] = None
                e["ping_err"] = f"{type(ex).__name__}: {ex}"[:200]
            e["procs"] = sample_procs()
            rec["tabs"].append(e)
            print("TAB " + json.dumps(e), flush=True)
        rec["outcome"] = "all_tabs_opened"
except Exception as exc:
    rec["outcome"] = f"EXCEPTION {type(exc).__name__}: {exc}"[:300]
    print("EXCEPTION " + rec["outcome"], flush=True)
finally:
    _stop.set()
    rec["samples"] = samples
    rec["t_total_s"] = round(time.time() - _t0, 1)
    out = os.environ.get("PS171_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(rec, fh, indent=2)
    print("RESULT " + json.dumps({k: v for k, v in rec.items()
                                  if k != "samples"}), flush=True)
