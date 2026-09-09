"""PS-349 THRESHOLD SWEEP — the honest version of the discrimination question.

``analyse.py`` reports each arm's distributions. This asks the operational
question a watchdog would actually face: **pick one threshold, apply it to every
arm, and count what it gets right and wrong.**

GROUND TRUTH, and note that SIX of the nine arms are deliberately NEGATIVE:

  healthy      NEGATIVE — idle, answered 12/12 pings in 0.01 s.
  unreachable  NEGATIVE — the BROWSER IS FINE; only the observer's eval hook
               was torn out. This is the false-positive trap: a health check
               that reads through the automation channel calls this dead.
               ⚠️ Its health is STRUCTURAL, not measured: with the hook gone
               there is no channel to ping, so its committed result carries
               "pings": [] and it is the one negative arm nothing could see.
  busy         NEGATIVE — healthy and DOING REAL WORK: 69-91% CPU sustained
               while answering 12/12 pings in 0.01-0.02 s. The
               false-positive trap for any PINNED-CPU rule.
  busyheavy    NEGATIVE — the same work across SIX concurrent chunk loops:
               102-114% CPU AND ctxt_v down to 20/s, while still answering
               12/12 pings in 0.02 s. It lands inside BOTH degraded bands
               at once, and it is the arm that decides the whole question.
  busyheavy2   NEGATIVE — a repeat of busyheavy, for reproducibility.
  busymax      NEGATIVE — SIXTEEN loops. Its ctxt_v floor of 9/s is BELOW the
               wedged arm's floor of 18, which is why the margin here is a
               duration artifact rather than a magnitude one.
  sigstop      POSITIVE — every process alive, none can answer (SIGSTOP).
  spin         POSITIVE — alive, main thread pinned, answers nothing.
  jugwedge     POSITIVE — PS-171's tab-3 stall; tab-1 ping blocked, no recovery.
               ⚠️ Only t <= 121.5 is this arm; see END_BOUND below.

The rule under test is deliberately the simplest one that could work:

    DEGRADED  iff  every sample in the last K has ctxt_v/s <= T

on the whole engine tree, where ``ctxt_v`` is the per-second delta of
``voluntary_ctxt_switches`` summed over the tree. It is evaluated as a sliding
window over each arm's steady-state samples, and an arm is called POSITIVE if
the rule ever fires inside that arm's ground-truth window.
"""
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
SETTLE = 8.0
TAIL_DROP = 10.0

# ⚠️ EXPLICIT END BOUND, and it is not a convenience. The jugwedge arm's
# teardown NEVER COMPLETED: after the recovery ping at t=121.5 the subject
# printed nothing more, its observer's `while child.poll() is None` loop never
# ended, and it went on sampling to t=1605.2.
#
# ⛔ THE EXCLUDED SPAN IS NOT THIS ARM'S TEARDOWN. An earlier version of this
# comment said it was "wedged AND being torn down", and that its high-CPU
# samples were "teardown work". Both are wrong, and the truth is a STRONGER
# reason to exclude them. The wedged session's own orphaned tree runs to
# t=216.9 and is reaped at t=218.9; everything after that is the NEXT FIVE ARMS
# (unreachable, busy, busyheavy, busyheavy2, busymax), whose engine trees the
# still-running stale observer matched with the same engine-path matcher and
# folded into this file. Their episode CPU medians (5.4 / 72.9 / 103.9 / 104.0
# / 104.0) reproduce those arms' own medians (5.9 / 72.8 / 103.9 / 104.0 /
# 103.9). So the exclusion is not a judgement about teardown; it is a
# correctness requirement -- those samples are OTHER SESSIONS.
#
# The tail carries 254 samples above 60% CPU (249 above 70%; the earlier "249
# above 60%" conflated the two thresholds). The window therefore ends at the
# LAST GROUND-TRUTH OBSERVATION: the moment the subject last confirmed the
# session's state. Left unbounded it inflates the wedged arm's CPU and would
# have made a CPU rule look better than it is. The teardown itself was not
# blocked but SPINNING -- 100% CPU with the engine tree already gone, still
# going ~26 min later; see PROBE.md finding (b).
#
# THE BOUND IS UNCHANGED AT 121.5. Only its justification was wrong.
END_BOUND = {"jugwedge": 121.5}

TRUTH = {"healthy": False, "unreachable": False, "busy": False,
         "busyheavy": False, "busyheavy2": False, "busymax": False,
         "sigstop": True, "spin": True, "jugwedge": True}


def windows(arm):
    path = os.path.join(D, f"{arm}.txt")
    marks, rows = {}, []
    for line in open(path):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("meta") == "mark":
            marks.setdefault(o["mark"], o["t"])
        elif "meta" not in o:
            rows.append(o)
    ready = marks.get("READY", 0.0)
    deg = marks.get("DEGRADED")
    last = max((r["t"] for r in rows if r["nproc"] > 0), default=0.0)
    end = min(last - TAIL_DROP, END_BOUND.get(arm, float("inf")))
    live = [r for r in rows if r["nproc"] > 0
            and r["t"] >= ready + SETTLE and r["t"] <= end]
    if deg is not None:
        live = [r for r in live if r["t"] >= deg + SETTLE]
    return [r["ctxt_v"] for r in live if r["ctxt_v"] is not None]


def fires(series, T, K):
    """True if K CONSECUTIVE samples are all <= T."""
    run = 0
    for v in series:
        run = run + 1 if v <= T else 0
        if run >= K:
            return True
    return False


series = {a: windows(a) for a in TRUTH if os.path.exists(os.path.join(D, f"{a}.txt"))}
print("samples per arm:", {a: len(s) for a, s in series.items()})
print()

print(f"{'K':>3} {'T':>6} | " + " ".join(f"{a[:9]:>9}" for a in series)
      + " |  verdict")
print("-" * 78)
best = []
for K in (3, 5, 10, 15):
    for T in (0, 5, 10, 20, 30, 50, 80, 100, 120, 150, 200, 300):
        got = {a: fires(s, T, K) for a, s in series.items()}
        tp = sum(1 for a, v in got.items() if v and TRUTH[a])
        fp = sum(1 for a, v in got.items() if v and not TRUTH[a])
        fn = sum(1 for a, v in got.items() if not v and TRUTH[a])
        perfect = fp == 0 and fn == 0
        if perfect:
            best.append((K, T))
        flag = "  <-- PERFECT" if perfect else ("  FP!" if fp else "")
        print(f"{K:>3} {T:>6} | "
              + " ".join(f"{('FIRE' if got[a] else '.'):>9}" for a in series)
              + f" |  tp={tp} fp={fp} fn={fn}{flag}")
    print()

print("=" * 78)
if best:
    Ks = sorted({k for k, _ in best})
    Ts = sorted({t for _, t in best})
    print(f"Rules with zero false positives AND zero false negatives on these "
          f"five arms:\n  K in {Ks}, T in {Ts}")
    print("\n⚠️ FIVE ARMS IS NOT A VALIDATION SET. A separating threshold on "
          "\n   five runs on one container is a HYPOTHESIS, not a calibration.")
else:
    print("NO single (K,T) low-activity rule separates all five arms.")
    print("`spin` is the arm it cannot reach: it is DEGRADED with HIGH activity,")
    print("so no LOW-activity threshold can ever catch it, and the only T that")
    print("does (300) also fires on the healthy control.")

# ---------------------------------------------------------------------------
# The composite: does adding a PINNED-CPU arm close the gap?
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("COMPOSITE — low-activity OR pinned-CPU")
print("=" * 78)


def cpu_series(arm):
    path = os.path.join(D, f"{arm}.txt")
    marks, rows = {}, []
    for line in open(path):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("meta") == "mark":
            marks.setdefault(o["mark"], o["t"])
        elif "meta" not in o:
            rows.append(o)
    ready = marks.get("READY", 0.0)
    deg = marks.get("DEGRADED")
    last = max((r["t"] for r in rows if r["nproc"] > 0), default=0.0)
    end = min(last - TAIL_DROP, END_BOUND.get(arm, float("inf")))
    live = [r for r in rows if r["nproc"] > 0
            and r["t"] >= ready + SETTLE and r["t"] <= end]
    if deg is not None:
        live = [r for r in live if r["t"] >= deg + SETTLE]
    return [r["cpu"] for r in live if r["cpu"] is not None]


def fires_above(series, T, K):
    run = 0
    for v in series:
        run = run + 1 if v >= T else 0
        if run >= K:
            return True
    return False


cpus = {a: cpu_series(a) for a in series}
K, T_LOW = 10, 20
print(f"arm A: ctxt_v <= {T_LOW} for {K} consecutive samples (the wedge arm)")
for T_HI in (60, 80, 90, 95, 100, 105, 110):
    print(f"\narm B: cpu >= {T_HI}% for {K} consecutive samples (the spin arm)")
    fp = fn = 0
    for a in series:
        low = fires(series[a], T_LOW, K)
        hi = fires_above(cpus[a], T_HI, K)
        got = low or hi
        ok = got == TRUTH[a]
        if got and not TRUTH[a]:
            fp += 1
        if not got and TRUTH[a]:
            fn += 1
        print(f"   {a:12s} truth={'DEGRADED' if TRUTH[a] else 'healthy ':9s}"
              f" low={'Y' if low else '.'} cpu={'Y' if hi else '.'}"
              f" -> {'DEGRADED' if got else 'healthy':9s} {'ok' if ok else '** WRONG **'}")
    print(f"   => fp={fp} fn={fn}"
          + ("   <-- SEPARATES ALL ARMS" if fp == 0 and fn == 0 else ""))
