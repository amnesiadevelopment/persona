"""PS-349 ANALYSIS — does any candidate signal DISTINGUISH healthy from degraded?

Reads the arm files, splits each arm's samples at its own ``DEGRADED`` mark (or
takes the whole steady-state window for the control), and reports each
candidate signal's distribution on both sides. This answers the ticket's
falsification requirement directly: **a signal that reads the same on a healthy
and on a deliberately degraded session is not a health check.**

TWO WINDOW RULES, both stated rather than tuned:

* ``SETTLE`` (8 s) is dropped after READY and after DEGRADED. Startup burns CPU
  and context switches in *every* arm, healthy or not, so a threshold fitted
  across a launch would fire on every launch.
* the last ``TAIL_DROP`` (10 s) of every arm is dropped. Teardown is a burst of
  activity in every arm and it is not part of either state being compared —
  leaving it in put a 2145 switch/s spike inside the *wedged* window.

The sustained test is what a watchdog would actually do: not "is this sample
low" but "have the last N samples ALL been low". A single sample is noise; the
question is whether a *window* separates.
"""
import json
import os
import statistics
import sys

D = os.path.dirname(os.path.abspath(__file__))
ARMS = sys.argv[1:] or ["healthy", "sigstop", "spin", "jugwedge", "unreachable"]
SETTLE = 8.0
TAIL_DROP = 10.0
SUSTAIN = 5  # consecutive samples (10 s at the 2 s cadence)


def load(arm):
    path = os.path.join(D, f"{arm}.txt")
    if not os.path.exists(path):
        return None
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
    return marks, rows


def stat(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return {"n": len(vals), "min": round(min(vals), 1),
            "med": round(statistics.median(vals), 1),
            "p90": round(vals[int(0.9 * (len(vals) - 1))], 1),
            "max": round(max(vals), 1)}


def sustained_min(vals, k):
    """The LOWEST value that every k-consecutive window contains at least one
    of — i.e. the best threshold a k-sample sustained rule could use here.
    Concretely: max over windows of (min within window)."""
    vals = [v for v in vals if v is not None]
    if len(vals) < k:
        return None
    return max(min(vals[i:i + k]) for i in range(len(vals) - k + 1))


print("=" * 78)
print("PS-349 — candidate health signals, healthy vs degraded")
print(f"settle={SETTLE}s  tail_drop={TAIL_DROP}s  sustain={SUSTAIN} samples")
print("=" * 78)

summary = {}
for arm in ARMS:
    got = load(arm)
    if not got:
        print(f"\n[{arm}] NO READING")
        continue
    marks, rows = got
    ready = marks.get("READY", 0.0)
    deg = marks.get("DEGRADED")
    last_t = max((r["t"] for r in rows if r["nproc"] > 0), default=0.0)
    live = [r for r in rows if r["nproc"] > 0
            and r["t"] >= ready + SETTLE and r["t"] <= last_t - TAIL_DROP]
    if deg is not None:
        pre = [r for r in live if r["t"] < deg]
        post = [r for r in live if r["t"] >= deg + SETTLE]
    else:
        pre, post = live, []

    print(f"\n[{arm}]  READY={ready}  DEGRADED={deg}  "
          f"samples pre={len(pre)} post={len(post)}")
    for label, group in (("pre/healthy", pre), ("post/degraded", post)):
        if not group:
            continue
        row = {
            "alive": all(r["alive"] for r in group),
            "cpu": stat([r["cpu"] for r in group]),
            "ctxt_v": stat([r["ctxt_v"] for r in group]),
            "ctxt_all": stat([(r["ctxt_v"] or 0) + (r["ctxt_nv"] or 0)
                              if r["ctxt_v"] is not None else None
                              for r in group]),
            "nonS": stat([sum(v for k, v in r["st"].items() if k != "S")
                          for r in group]),
            "T": stat([r["st"].get("T", 0) for r in group]),
            "sust_ctxt_v": sustained_min([r["ctxt_v"] for r in group], SUSTAIN),
            "sust_cpu": sustained_min([r["cpu"] for r in group], SUSTAIN),
        }
        summary[(arm, label)] = row
        print(f"  {label:14s} alive={row['alive']}   "
              f"sustained({SUSTAIN}) ctxt_v<= {row['sust_ctxt_v']}  "
              f"cpu<= {row['sust_cpu']}")
        for k in ("cpu", "ctxt_v", "ctxt_all", "nonS", "T"):
            v = row[k]
            if v:
                print(f"      {k:10s} min={v['min']:8} med={v['med']:8} "
                      f"p90={v['p90']:8} max={v['max']:8}  (n={v['n']})")

print("\n" + "=" * 78)
print("DISCRIMINATION")
print("=" * 78)
ctrl = summary.get(("healthy", "pre/healthy"))
if not ctrl:
    sys.exit("no control arm — nothing to compare against")

print(f"CONTROL (healthy, idle, answering every ping):")
print(f"  ctxt_v      min={ctrl['ctxt_v']['min']}  med={ctrl['ctxt_v']['med']}")
print(f"  sustained({SUSTAIN}) floor = {ctrl['sust_ctxt_v']}   "
      f"(no {SUSTAIN}-sample window on a healthy session drops below this)")
print(f"  cpu         min={ctrl['cpu']['min']}  med={ctrl['cpu']['med']}")
print(f"  nonS        max={ctrl['nonS']['max']}\n")

for (arm, label), row in summary.items():
    if label != "post/degraded":
        continue
    cv, cp = row["ctxt_v"], row["cpu"]
    verdict = []
    # single-sample separation
    verdict.append(("ctxt_v single",
                    cv["max"] < ctrl["ctxt_v"]["min"]))
    # sustained separation: the degraded window's best sustained floor is
    # BELOW the control's, with no overlap
    verdict.append((f"ctxt_v sustained({SUSTAIN})",
                    row["sust_ctxt_v"] is not None
                    and ctrl["sust_ctxt_v"] is not None
                    and row["sust_ctxt_v"] < ctrl["sust_ctxt_v"]))
    verdict.append(("cpu single", cp["max"] < ctrl["cpu"]["min"]
                    or cp["min"] > ctrl["cpu"]["max"]))
    verdict.append(("thread-state", row["T"]["max"] > 0
                    or row["nonS"]["min"] > ctrl["nonS"]["max"]))
    print(f"{arm:12s} (ground truth: DEGRADED)")
    for name, ok in verdict:
        print(f"    {name:22s} distinguishes? {'YES' if ok else 'no'}")
