#!/usr/bin/env python3
"""PS-396 AC #2 — BOTH ARMS, against a REAL browser session.

Runs `SessionSeriesRecorder` (the SHIPPED class, imported from src/, not a
copy) against a real chromium tree launched into a real profile dir, and
reports what the record says.

    QUIET arm  — the session runs UNDER LOAD (a page that keeps a renderer
                 genuinely busy). Chosen deliberately: PS-349 measured the
                 healthy arm `busymax` reading BELOW the wedged arm
                 `jugwedge` on the context-switch axis, so a quiet arm run
                 IDLE would be a suspiciously easy reading. A LOADED healthy
                 arm is the hard case.
    SIGNAL arm — the SAME session, SIGSTOP'd. PS-349's cheapest gesture and
                 the one whose ground truth is CERTAIN: "alive, cannot answer".

⛔ THE INSTRUMENT IS CHECKED BEFORE ANY VERDICT. This project has shipped a
false green twice from an instrument rather than an engine (PS-299's rebase
probe printing "81/81 hunks, 0 rejects" against an EMPTY directory; PS-341's
--dump-dom reading "8 of 8 moved"). So: the tree size is printed, and an arm
that matched ZERO processes is an ABORT, not a clean result.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.services.browser.session_series import (  # noqa: E402
    SessionSeriesRecorder,
    engine_pids_for,
    series_path,
)

PDIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ps396-arm/pdir")
ARM = os.environ.get("PS396_ARM", "quiet")
SECONDS = float(os.environ.get("PS396_SECONDS", "24"))

os.makedirs(PDIR, exist_ok=True)

# A page that keeps a renderer genuinely busy — the LOAD the quiet arm runs
# under, stated rather than implied.
BUSY = (
    "data:text/html,<script>let x=0;function f(){for(let i=0;i<8e6;i++)"
    "x+=Math.sqrt(i);requestAnimationFrame(f);}f();</script>"
)

proc = subprocess.Popen(
    ["/usr/bin/chromium", "--headless=new", "--no-sandbox", "--disable-gpu",
     "--disable-dev-shm-usage", f"--user-data-dir={PDIR}", BUSY],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

stop = threading.Event()
rec = SessionSeriesRecorder(PDIR, period_s=1.0, engine="chromium")
t = threading.Thread(target=rec.run, args=(stop,), daemon=True)
t.start()

time.sleep(4)
pids = engine_pids_for(PDIR)
print(f"INSTRUMENT CHECK: arm={ARM} matched {len(pids)} engine processes", flush=True)
if not pids:
    print("ABORT: matched ZERO processes — the instrument, not the engine, "
          "would be producing any reading below.", flush=True)
    proc.kill()
    sys.exit(2)

half = SECONDS / 2
time.sleep(half)

if ARM == "signal":
    print(f"SIGSTOP -> {len(pids)} pids at t~{half + 4:.0f}s", flush=True)
    for p in pids:
        try:
            os.kill(p, signal.SIGSTOP)
        except OSError:
            pass

time.sleep(half)

if ARM == "signal":
    for p in pids:
        try:
            os.kill(p, signal.SIGCONT)
        except OSError:
            pass

stop.set()
t.join(5)
proc.kill()
proc.wait(10)

path = series_path(PDIR)
size = os.path.getsize(path)
rows = [json.loads(ln) for ln in open(path) if ln.strip()]
samples = [r for r in rows if "meta" not in r]
print(f"\nFILE {path}  {size} bytes, {len(rows)} lines ({len(samples)} samples)")
print(f"{'t':>6} {'nproc':>5} {'cpu':>8} {'ctxt_v':>7} {'ctxt_nv':>7} "
      f"{'denied':>6} {'gone':>5}")
for s in samples:
    print(f"{s['t']:>6} {s['nproc']:>5} {str(s['cpu']):>8} "
          f"{str(s['ctxt_v']):>7} {str(s['ctxt_nv']):>7} "
          f"{s['denied']:>6} {s['gone']:>5}")

mid = len(samples) // 2
first = [s for s in samples[1:mid] if s["cpu"] is not None]
second = [s for s in samples[mid:] if s["cpu"] is not None]


def med(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


print(f"\nBEFORE half: cpu med={med([s['cpu'] for s in first])} "
      f"ctxt_v med={med([s['ctxt_v'] for s in first if s['ctxt_v'] is not None])}")
print(f"AFTER  half: cpu med={med([s['cpu'] for s in second])} "
      f"ctxt_v med={med([s['ctxt_v'] for s in second if s['ctxt_v'] is not None])}")
elapsed = samples[-1]["t"] if samples else 0
print(f"\nCOST: {len(samples)} samples over {elapsed}s = "
      f"{len(samples)/max(elapsed,1):.2f} samples/s; {size} bytes -> "
      f"{size/max(elapsed,1)*3600/1024:.1f} KiB/hour at this cadence")
