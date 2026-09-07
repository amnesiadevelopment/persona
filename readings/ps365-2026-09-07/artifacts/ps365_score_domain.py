#!/usr/bin/env python3
"""PS-365 — score the two DOMAIN invariants over the PS-344 measured readings,
and FALSIFY the scorer against synthetic inputs.

Why this exists
---------------
PS-365 asks which spoofs can move onto the engine's stealthy FLAG mechanism.
The flag mechanism is stealthy because the engine authors a plausible value
before any page code runs. A NOISE patch is different in kind: it takes a value
Blink already produced and perturbs it. This scorer measures the consequence of
that difference — a perturbation computed in double space leaves the numeric
DOMAIN Blink's own pipeline is confined to.

  INVARIANT 1  client-rect coordinates are exact multiples of 1/64 (LayoutUnit).
  INVARIANT 2  canvas TextMetrics fields are float32-exact doubles.

Both are properties of STOCK Chromium, measured this session with
``ps365_domain_probe.html`` (6008 rect values / 1008 metric values, 0
violations each). They are NOT persona values, which is the point: a detector
can check them with no knowledge of persona at all.

Inputs are the committed PS-344 artifacts — the published 152 engine and its
version-matched stock control, both already measured on a host.
NO ENGINE IS BUILT OR LAUNCHED BY THIS SCRIPT.

Usage:
    python3 ps365_score_domain.py [--readings DIR]

Exit 0 = scored. Exit 1 = a falsification arm failed (the scorer is broken).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys

# --------------------------------------------------------------------------
# the two invariants
# --------------------------------------------------------------------------


def is_float32_exact(x: float) -> bool:
    """True when ``x`` survives a round trip through float32.

    Blink computes text metrics in ``float`` and widens to double at the
    bindings layer, so every stock TextMetrics field is float32-exact. A
    perturbation applied in double space is not.
    """
    try:
        return struct.unpack("f", struct.pack("f", float(x)))[0] == float(x)
    except (OverflowError, ValueError):
        return False


def on_layout_unit_lattice(x: float, denom: int = 64) -> bool:
    """True when ``x`` is an exact multiple of 1/64.

    Blink stores layout geometry in ``LayoutUnit``, a 1/64-px fixed-point type,
    so every stock client-rect coordinate lands on that lattice.
    """
    q = float(x) * denom
    return abs(q - round(q)) < 1e-9


# Fields to score. Rect POSITION fields only for invariant 1: patch 014 calls
# Offset() and never Scale(), so width/height are untouched by design and
# scoring them would dilute the signal with rows that cannot move.
RECT_POSITION_FIELDS = ("x", "y", "left", "top")
TEXTMETRIC_FIELDS = (
    "measuretext_width",
    "measuretext_actual_left",
    "measuretext_actual_right",
)


def score_reading(doc: dict, label: str) -> list[dict]:
    """One row per (seed, layer, realm) with both invariants scored."""
    rows = []
    for rec in doc["records"]:
        layer = "ON" if rec.get("layer_installed") else "OFF"
        for realm, rv in sorted(rec["reading"]["realms"].items()):
            rect = rv.get("client_rects") or {}
            canvas = rv.get("canvas") or {}

            rect_vals = [rect[f] for f in RECT_POSITION_FIELDS
                         if isinstance(rect.get(f), (int, float))]
            mt_vals = [canvas[f] for f in TEXTMETRIC_FIELDS
                       if isinstance(canvas.get(f), (int, float))]

            rows.append({
                "build": label,
                "seed": rec["seed"],
                "layer": layer,
                "realm": realm,
                "rect_probed": len(rect_vals),
                "rect_off_lattice": sum(1 for v in rect_vals
                                        if not on_layout_unit_lattice(v)),
                "mt_probed": len(mt_vals),
                "mt_off_float32": sum(1 for v in mt_vals
                                      if not is_float32_exact(v)),
                "mt_negative": sum(1 for v in mt_vals if v < 0 and abs(v) < 1),
            })
    return rows


# --------------------------------------------------------------------------
# falsification — a scorer that has only ever passed is not known to work
# --------------------------------------------------------------------------


def falsify() -> list[tuple[str, bool, str]]:
    """Drive both predicates to BOTH verdicts on values whose answer is known.

    Every case is a value this project actually measured, or a stock value it
    measured, so none of them is a hypothetical.
    """
    cases = [
        # (description, predicate, input, expected)
        ("stock rect x=8 is on the 1/64 lattice",
         on_layout_unit_lattice, 8.0, True),
        ("stock rect x=60.109375 is on the lattice",
         on_layout_unit_lattice, 60.109375, True),
        ("stock exempt rect x=13.296875 is on the lattice",
         on_layout_unit_lattice, 13.296875, True),
        ("ENGINE rect x=7.999754428863525 is OFF the lattice",
         on_layout_unit_lattice, 7.999754428863525, False),
        ("ENGINE rect x=60.108951568603516 is OFF the lattice",
         on_layout_unit_lattice, 60.108951568603516, False),
        ("stock measureText width 172.1083984375 is float32-exact",
         is_float32_exact, 172.1083984375, True),
        ("stock actualBoundingBoxRight 171.90673828125 is float32-exact",
         is_float32_exact, 171.90673828125, True),
        ("stock actualBoundingBoxLeft -1 is float32-exact",
         is_float32_exact, -1.0, True),
        ("ENGINE (layer ON) 171.9077136995075 is NOT float32-exact",
         is_float32_exact, 171.9077136995075, False),
        ("ENGINE (layer ON) 172.10937500000003 is NOT float32-exact",
         is_float32_exact, 172.10937500000003, False),
        ("ENGINE (layer OFF) -9.913113367801893e-05 is NOT float32-exact",
         is_float32_exact, -9.913113367801893e-05, False),
    ]
    out = []
    for desc, pred, value, expected in cases:
        got = pred(value)
        out.append((desc, got == expected, f"expected {expected}, got {got}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readings", default=None,
                    help="dir holding the PS-344 artifacts (default: sibling)")
    args = ap.parse_args()

    here = pathlib.Path(__file__).resolve().parent
    src = (pathlib.Path(args.readings) if args.readings
           else here.parent.parent / "ps344-2026-09-07" / "artifacts")

    print("=" * 78)
    print("FALSIFICATION — both predicates driven to BOTH verdicts")
    print("=" * 78)
    results = falsify()
    for desc, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}"
              + ("" if ok else f"  <- {detail}"))
    if not all(ok for _, ok, _ in results):
        print("\n!! the scorer does not agree with known values — refusing to score")
        return 1
    print(f"\n  {len(results)}/{len(results)} — both predicates demonstrated in "
          "BOTH states on measured values.\n")

    pub = json.loads((src / "readings-published-152.json").read_text())
    stk = json.loads((src / "readings-stock-cft-152.json").read_text())
    rows = score_reading(pub, "ENGINE") + score_reading(stk, "STOCK")

    print("=" * 78)
    print("SCORED — PS-344 measured readings (published 152 vs version-matched stock)")
    print("=" * 78)
    hdr = f"{'build':7} {'seed':6} {'lyr':4} {'realm':19} {'rect off/n':11} {'metrics off/n':13} neg"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r["rect_probed"] and not r["mt_probed"]:
            continue
        flag = "  <-- violates" if (r["rect_off_lattice"] or r["mt_off_float32"]) else ""
        print(f"{r['build']:7} {r['seed']:<6} {r['layer']:4} {r['realm']:19} "
              f"{r['rect_off_lattice']}/{r['rect_probed']:<9} "
              f"{r['mt_off_float32']}/{r['mt_probed']:<11} {r['mt_negative']}{flag}")

    def tally(build, key, probed_key):
        sel = [r for r in rows if r["build"] == build]
        return sum(r[key] for r in sel), sum(r[probed_key] for r in sel)

    print("\n" + "=" * 78)
    print("TOTALS")
    print("=" * 78)
    for build in ("ENGINE", "STOCK"):
        ro, rp = tally(build, "rect_off_lattice", "rect_probed")
        mo, mp = tally(build, "mt_off_float32", "mt_probed")
        print(f"  {build:7} rect off-lattice {ro:3}/{rp:<4}   "
              f"TextMetrics off-float32 {mo:3}/{mp}")
    print("\n  control (this session, stock Chromium 152.0.7977.82, "
          "ps365_domain_probe.html):")
    print("           rect off-lattice   0/6008   TextMetrics off-float32   0/1008")
    return 0


if __name__ == "__main__":
    sys.exit(main())
