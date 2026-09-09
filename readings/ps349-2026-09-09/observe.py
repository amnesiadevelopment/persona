"""PS-349 OBSERVER — reads a live persona session FROM OUTSIDE, holding only
what ``BrowserLauncher`` holds: a pid and ``/proc``.

It launches ``subject.py`` as a child, resolves that child's engine tree, and
samples every candidate health signal on a fixed cadence until the child exits.
It never touches a session object, a playwright handle or the eval hook — if a
signal here can distinguish healthy from degraded, then the launcher could read
it too, which is the whole question PS-349 asks.

THE CANDIDATE SIGNALS, and what each is a candidate FOR:

  ``alive``      pid resolvable. This is ``session_registry``'s EXISTING probe.
                 Included as the null hypothesis: a wedged session answers YES.
  ``state``      per-thread run-states (R/S/D/T/Z) from ``/proc/<tid>/stat``.
  ``cpu``        INSTANTANEOUS utime+stime delta per second. Never ``ps pcpu``,
                 which is a lifetime average and makes spinning and blocked
                 indistinguishable — the one distinction that decides this
                 (PS-171's corrected instrument; this is the same reading).
  ``ctxt``       voluntary + nonvoluntary context-switch DELTAS from
                 ``/proc/<pid>/status``. A process that is genuinely doing
                 nothing makes no syscalls and yields no switches; an idle but
                 LIVE browser still has timers, IPC and compositor traffic. So
                 this is the candidate for the distinction CPU cannot make —
                 idle-healthy vs wedged, both of which are ~0% CPU.
  ``wchan``      per-thread wait channels from ``/proc/<tid>/wchan``. PS-171
                 wrote this arm (``g_wchan.py``) and never got it to run; a
                 futex pile-up names a lock, a pipe/socket wait names an
                 unanswered IPC peer.
  ``rss``        resident memory, whole tree.
  ``nproc``      process count, whole tree (``/proc/*/cmdline`` matched on the
                 engine path — NEVER ``ps comm``, which PS-171 arm H measured
                 matches the parent and not one single child).

Writes one JSON object per line to a ``.txt``. ``.gitignore:183`` is ``*.log``.
"""
import json
import os
import subprocess
import sys
import threading
import time

ENGINE_DIR = os.path.expanduser("~/.cache/invisible-playwright")
CLK = os.sysconf("SC_CLK_TCK")
ARM = os.environ.get("PS349_ARM", "healthy")
OUT = os.environ["PS349_OBS_OUT"]
PERIOD = float(os.environ.get("PS349_PERIOD", "2.0"))
T0 = time.time()

_prev_cpu = {}
_prev_ctxt = {}


def engine_procs():
    """Whole engine tree, matched on the engine PATH in /proc/<pid>/cmdline.

    NOT ``ps comm``: PS-171 arm H measured that matcher finding 1 process where
    this one finds 11, because ``comm`` is capped at 15 chars and every Firefox
    child is named ``Web Content`` / ``Socket Process`` / ``RDD Process`` —
    none of which contains the substring "firefox".
    """
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                if ENGINE_DIR not in fh.read().decode("utf8", "replace"):
                    continue
            out.append(int(pid))
        except Exception:
            continue
    return sorted(out)


def read_thread_states(pid):
    """(state histogram, wchan histogram, thread count) for every thread."""
    states, wchans, n = {}, {}, 0
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except Exception:
        return states, wchans, 0
    for tid in tids:
        n += 1
        try:
            with open(f"/proc/{pid}/task/{tid}/stat") as fh:
                raw = fh.read()
            st = raw[raw.rindex(")") + 2:].split()[0]
            states[st] = states.get(st, 0) + 1
        except Exception:
            pass
        try:
            with open(f"/proc/{pid}/task/{tid}/wchan") as fh:
                w = fh.read().strip() or "0"
            wchans[w] = wchans.get(w, 0) + 1
        except Exception:
            pass
    return states, wchans, n


def sample():
    now = time.time()
    pids = engine_procs()
    cpu_total = 0.0
    ctxt_v = 0
    ctxt_nv = 0
    rss = 0
    states, wchans = {}, {}
    threads = 0
    cpu_measured = False
    ctxt_measured = False

    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as fh:
                raw = fh.read()
        except Exception:
            continue
        f = raw[raw.rindex(")") + 2:].split()
        ticks = int(f[11]) + int(f[12])
        rss += int(f[21]) * 4096
        prev = _prev_cpu.get(pid)
        _prev_cpu[pid] = (ticks, now)
        if prev and now > prev[1]:
            cpu_total += (ticks - prev[0]) / CLK / (now - prev[1]) * 100
            cpu_measured = True

        try:
            v = nv = None
            with open(f"/proc/{pid}/status") as fh:
                for line in fh:
                    if line.startswith("voluntary_ctxt_switches:"):
                        v = int(line.split()[1])
                    elif line.startswith("nonvoluntary_ctxt_switches:"):
                        nv = int(line.split()[1])
            if v is not None and nv is not None:
                p2 = _prev_ctxt.get(pid)
                _prev_ctxt[pid] = (v, nv, now)
                if p2 and now > p2[2]:
                    ctxt_v += max(0, v - p2[0])
                    ctxt_nv += max(0, nv - p2[1])
                    ctxt_measured = True
        except Exception:
            pass

        st, wc, n = read_thread_states(pid)
        for k, c in st.items():
            states[k] = states.get(k, 0) + c
        for k, c in wc.items():
            wchans[k] = wchans.get(k, 0) + c
        threads += n

    top_wchan = sorted(wchans.items(), key=lambda kv: -kv[1])[:6]
    return {
        "t": round(now - T0, 1),
        "nproc": len(pids),
        "alive": len(pids) > 0,          # session_registry's existing question
        "cpu": round(cpu_total, 1) if cpu_measured else None,
        "ctxt_v": ctxt_v if ctxt_measured else None,
        "ctxt_nv": ctxt_nv if ctxt_measured else None,
        "rss_mb": round(rss / 1048576, 1),
        "threads": threads,
        "st": states,
        "wchan": dict(top_wchan),
    }


# ---- pre-flight: assert a CLEAN box before launching -----------------------
# PS-171 arm B began with a 490 MB foreign firefox process the harness had not
# started, which then folded into every aggregate for the rest of the run.
# Recommendation 8 of that record is to fail loudly on it instead.
pre = engine_procs()
fh = open(OUT, "w", buffering=1)


def emit(obj):
    fh.write(json.dumps(obj) + "\n")
    fh.flush()


emit({"meta": "preflight", "arm": ARM, "engine_pids_before_launch": pre,
      "clean_box": not pre, "period_s": PERIOD,
      "kernel": os.uname().release})
if pre:
    emit({"meta": "ABORT", "why": "engine processes already running"})
    print("PREFLIGHT FAIL: engine already running", pre, flush=True)
    sys.exit(2)
print(f"OBS preflight clean (0 engine procs), arm={ARM}", flush=True)

env = dict(os.environ)
env["PS349_ARM"] = ARM
child = subprocess.Popen(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "subject.py")],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
)

marks = []


def pump():
    for line in child.stdout:
        line = line.rstrip()
        print(line, flush=True)
        # The subject announces its own phase transitions. The observer records
        # WHEN they happened so a sample can be labelled healthy or degraded
        # without the observer ever inspecting the session.
        for m in ("READY", "DEGRADED", "TEARDOWN"):
            if line.endswith(" " + m) or f" {m}" in line:
                marks.append({"mark": m, "t": round(time.time() - T0, 1)})
                emit({"meta": "mark", "mark": m,
                      "t": round(time.time() - T0, 1), "line": line})
                break
        else:
            emit({"meta": "subject", "t": round(time.time() - T0, 1),
                  "line": line[:400]})


threading.Thread(target=pump, daemon=True).start()

while child.poll() is None:
    try:
        emit(sample())
    except Exception as exc:
        emit({"meta": "sample-error", "err": f"{type(exc).__name__}: {exc}"})
    time.sleep(PERIOD)

emit({"meta": "child_exit", "rc": child.returncode,
      "t": round(time.time() - T0, 1), "marks": marks})
print("OBS done rc=", child.returncode, flush=True)
