#!/usr/bin/env python3
"""PS-365 rework — establish the DEVICE-SCALE boundary of INVARIANT 1.

Why this exists
---------------
The committed control (``ps365_domain_probe.html``) measured 0 / 6008 rect
values off the 1/64 lattice on stock Chromium and the report generalised that
to "a property of STOCK Chromium" that "a detector can check with no knowledge
of persona at all".

The code review showed that control never varied device scale factor, and that
stock Chromium violates the CSS-space 1/64 lattice in the THOUSANDS at
non-integer scale. That matters here rather than academically, because
``process.py`` adds ``--force-device-scale-factor`` from the host's Windows DPI
or macOS backing scale (``launch_policy._host_display_scale``), so a scaled
display is an ordinary persona launch and not a contrived parameter.

This script consumes the measured sweep and answers the question the sweep was
run to answer: **which formulation of invariant 1, if any, is scale-independent?**

Three candidate lattices, and the reason there are three
--------------------------------------------------------
Blink stores layout in ``LayoutUnit`` — 1/64 of a **device** pixel. It divides
by the scale factor and stores the CSS-pixel result through a **float**. So a
page reads a float32 widened to double, and the candidates differ in how much
of that pipeline they model:

  css_1_64      v * 64          integral   — the committed claim, CSS space
  dev_1_64      v * dpr * 64    integral   — device space, double arithmetic
  dev_exact     exists integer n such that float32(n / 64 / dpr) == v
                                           — device space, EXACT, no epsilon

``dev_exact`` is the one that matters and the epsilon is why. The float32 store
DISCARDS bits, so multiplying the widened double back by ``dpr * 64`` cannot
recover an integer in general — it recovers an integer only to within a float32
ulp, which at a coordinate of ~4000 is far above any fixed 1e-9. An
epsilon-based device test therefore reports violations that are an artifact of
the tolerance rather than a fact about the value. ``dev_exact`` asks the
question with no tolerance at all: is the value the float32 image of SOME
integer count of 1/64 device pixels? That is exactly what Blink's pipeline
produces, so a real Blink value must answer yes.

Reading
-------
A formulation is usable as a detector invariant only if it reads 0 at EVERY
scale. One non-zero cell disqualifies it — a detector shipping it would flag
stock Chrome.

Falsification (non-waivable, and run)
-------------------------------------
A predicate only ever seen to pass is not known to work. Each of the three is
driven to BOTH verdicts on constructed values whose status is known by
construction, and the script exits 1 if any arm fails.

Usage:
    python3 ps365_dpr_boundary.py            # scores the committed sweep data
    python3 ps365_dpr_boundary.py --self-test-only

Exit 0 = scored. Exit 1 = a falsification arm failed (the tool is broken).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys

# ---------------------------------------------------------------------------
# the three candidate predicates
# ---------------------------------------------------------------------------


def f32(x: float) -> float:
    """The double that a float32 store-then-load would yield."""
    try:
        return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError:
        return float("inf")


def on_css_1_64(v: float, dpr: float) -> bool:
    """v is an exact multiple of 1/64 in CSS space. The committed claim."""
    p = v * 64.0
    return abs(p - round(p)) < max(1e-9, abs(p) * 1e-12)


def on_dev_1_64(v: float, dpr: float) -> bool:
    """v * dpr is an exact multiple of 1/64, in double arithmetic."""
    p = v * dpr * 64.0
    return abs(p - round(p)) < max(1e-9, abs(p) * 1e-12)


def on_dev_exact(v: float, dpr: float) -> bool:
    """v is the float32 image of an integer count of 1/64 DEVICE pixels.

    No epsilon: the candidate integer is derived from v and then the forward
    pipeline (divide, store as float32) is REPLAYED and compared for exact
    equality. Neighbours are tried because the derivation itself rounds.
    """
    n = round(v * dpr * 64.0)
    return any(f32(c / 64.0 / dpr) == v for c in (n - 1, n, n + 1))


PREDICATES = {
    "css_1_64": on_css_1_64,
    "dev_1_64": on_dev_1_64,
    "dev_exact": on_dev_exact,
}

# ---------------------------------------------------------------------------
# MEASURED — the sweep, run this session on the container's stock Chromium
# 152.0.7977.82 with ps365_dpr_lattice_probe.html, which reproduces the
# COMMITTED control's geometry exactly (500 simple divs x 8 bounding-box
# fields + their client rects + the Range rects), so the dpr-1 row is directly
# comparable to the committed 0 / 6008.
#
# Transcribed from dpr-sweep-output.txt, committed beside this file. This
# script scores the FORMULATIONS against those counts; it launches nothing.
# (probed moves 6008 -> 6016 at dpr 6 and 8: the Range wraps onto a different
# number of line boxes at extreme scale. Same geometry, more client rects.)
# ---------------------------------------------------------------------------

SWEEP = [
    # scale,   dpr,                  n,   css_off, dev_eps_off, dev_exact_off, non_f32
    ("0.5",    0.5,                6008,        0,           0,             0,       0),
    ("0.75",   0.75,               6008,     2914,        2914,          1643,     345),
    ("1",      1.0,                6008,        0,           0,             0,       0),
    ("1.1",    1.100000023841858,  6008,     5833,        6008,          1709,     903),
    ("1.25",   1.25,               6008,     3591,        3591,          1229,     477),
    ("1.3333", 1.333299994468689,  6008,     6005,        6008,          2957,     884),
    ("1.5",    1.5,                6008,     2925,        2925,          1552,     344),
    ("1.75",   1.75,               6008,     4877,        4877,          3503,     715),
    ("2",      2.0,                6008,     1810,           0,             0,       0),
    ("2.5",    2.5,                6008,     3156,        3156,          1135,     482),
    ("3",      3.0,                6008,     2188,        2188,          1250,     342),
    ("4",      4.0,                6008,     3306,           0,             0,       0),
    ("5",      5.0,                6008,     3801,        3801,          1222,     570),
    ("6",      6.0,                6016,     3793,        3037,          1661,     389),
    ("8",      8.0,                6016,     3441,           0,             0,       0),
]


def is_pow2(x: float) -> bool:
    """True when x is an exact power of two (including 0.5, 0.25, ...)."""
    if x <= 0:
        return False
    # a positive float is a power of two iff its mantissa bits are all zero
    bits = struct.unpack(">Q", struct.pack(">d", x))[0]
    return (bits & ((1 << 52) - 1)) == 0


# ---------------------------------------------------------------------------
# falsification
# ---------------------------------------------------------------------------


def falsify() -> list[str]:
    """Drive every predicate to BOTH verdicts on values known by construction.

    Returns a list of failure strings; empty means every arm behaved.
    """
    fails: list[str] = []

    def check(name, pred, v, dpr, want, why):
        got = pred(v, dpr)
        if got != want:
            fails.append(
                f"{name}({v!r}, dpr={dpr}) -> {got}, expected {want}  [{why}]"
            )

    # --- css_1_64 -----------------------------------------------------------
    # MUST PASS: exact multiples of 1/64 in CSS space.
    check("css_1_64", on_css_1_64, 5.0, 1.0, True, "integer is 320/64")
    check("css_1_64", on_css_1_64, 5.359375, 1.0, True, "343/64 exactly")
    check("css_1_64", on_css_1_64, 0.015625, 1.0, True, "1/64 itself")
    # MUST FAIL: values deliberately between lattice points.
    check("css_1_64", on_css_1_64, 5.3625001907, 1.0, False, "measured dpr-1.25 value")
    check("css_1_64", on_css_1_64, 5.359375 + 0.5 / 64, 1.0, False, "half a unit off")
    check("css_1_64", on_css_1_64, 0.1, 1.0, False, "0.1 is not n/64")

    # --- dev_1_64 -----------------------------------------------------------
    # MUST PASS: on the lattice once scaled.
    check("dev_1_64", on_dev_1_64, 343.0 / 64 / 2, 2.0, True, "343/64 device px @2")
    check("dev_1_64", on_dev_1_64, 5.0, 1.0, True, "trivially on at dpr 1")
    # MUST FAIL.
    check("dev_1_64", on_dev_1_64, 5.0 + 0.5 / 64 / 2, 2.0, False, "half unit off @2")
    check("dev_1_64", on_dev_1_64, 0.1, 3.0, False, "0.1*3*64 = 19.2")

    # --- dev_exact ----------------------------------------------------------
    # MUST PASS: constructed by REPLAYING the real pipeline, so these are
    # exactly the values a stock Blink can emit.
    for dpr in (1.0, 1.25, 1.5, 2.0, 3.0):
        for n in (1, 343, 1629, 100000):
            v = f32(n / 64.0 / dpr)
            check("dev_exact", on_dev_exact, v, dpr, True,
                  f"float32 image of n={n} @dpr={dpr}")
    # MUST FAIL: perturb by enough to leave the float32 neighbourhood entirely.
    # (This is the shape a noise patch produces: a double-space offset.)
    for dpr in (1.0, 1.25, 1.5, 2.0):
        v = f32(343 / 64.0 / dpr)
        check("dev_exact", on_dev_exact, v + 0.002, dpr, False,
              f"perturbed by +0.002 @dpr={dpr} (014's noise magnitude)")
        check("dev_exact", on_dev_exact, v * 1.0007, dpr, False,
              f"perturbed multiplicatively @dpr={dpr}")

    # --- the tolerance claim itself ----------------------------------------
    # The whole reason dev_exact exists: at fractional dpr, a REAL Blink value
    # fails the epsilon-based dev_1_64 while passing dev_exact. If this arm
    # ever stops holding, the justification for the third predicate is gone.
    v = f32(1542 / 64.0 / 1.25)
    if on_dev_1_64(v, 1.25) or not on_dev_exact(v, 1.25):
        fails.append(
            "tolerance arm: expected a real Blink value @dpr 1.25 to FAIL the "
            f"epsilon dev_1_64 and PASS dev_exact; got dev_1_64="
            f"{on_dev_1_64(v, 1.25)} dev_exact={on_dev_exact(v, 1.25)}"
        )

    return fails


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test-only", action="store_true")
    args = ap.parse_args()

    print("PS-365 rework — device-scale boundary of INVARIANT 1")
    print("=" * 78)
    print()
    print("FALSIFICATION — every predicate driven to BOTH verdicts")
    print("-" * 78)
    fails = falsify()
    if fails:
        print("  ✗ FAILED — the tool is broken, no reading below is trustworthy:")
        for f in fails:
            print("     ", f)
        return 1
    print("  ✓ all arms behaved (must-pass and must-fail, all three predicates,")
    print("    plus the tolerance arm that justifies dev_exact existing at all)")
    print()

    if args.self_test_only:
        return 0

    print("MEASURED SWEEP — stock Chromium 152.0.7977.82, ~6008 rect values/scale")
    print("-" * 78)
    print(f"{'scale':>7} {'dpr':>8} {'pow2':>6} {'probed':>7} {'css_1_64':>9}"
          f" {'dev_eps':>8} {'dev_exact':>10} {'non-f32':>8}")
    print(f"{'-'*7:>7} {'-'*8:>8} {'-'*6:>6} {'-'*7:>7} {'-'*9:>9}"
          f" {'-'*8:>8} {'-'*10:>10} {'-'*8:>8}")
    for scale, dpr, n, css_off, eps_off, dev_off, nf in SWEEP:
        print(f"{scale:>7} {dpr:>8.4g} {str(is_pow2(dpr)):>6} {n:>7}"
              f" {css_off:>9} {eps_off:>8} {dev_off:>10} {nf:>8}")
    print()

    css_clean   = [s for s, d, n, c, e, x, f in SWEEP if c == 0]
    dev_clean   = [s for s, d, n, c, e, x, f in SWEEP if x == 0]
    dev_dirty   = [s for s, d, n, c, e, x, f in SWEEP if x != 0]
    pow2_scales = [s for s, d, n, c, e, x, f in SWEEP if is_pow2(d)]

    print("VERDICT")
    print("-" * 78)
    print(f"  css_1_64  is clean at: {', '.join(css_clean)}")
    print("            => NOT scale-independent. The committed invariant is a")
    print("               property of stock Blink AT dpr 1 (and 0.5), not of")
    print("               stock Blink. A detector shipping it flags stock Chrome")
    print("               on any scaled display.")
    print()
    print(f"  dev_exact is clean at: {', '.join(dev_clean)}")
    print(f"            dirty at:    {', '.join(dev_dirty)}")
    print(f"            pow2 scales: {', '.join(pow2_scales)}")
    ok = set(dev_clean) == set(pow2_scales)
    print(f"            clean set == power-of-two set ? {ok}")
    print("            => the boundary is EXACT-REPRESENTABILITY OF THE DIVISION,")
    print("               not integrality of the scale. dpr 3 and 5 are integers")
    print("               and BOTH fail; 0.5 is fractional and passes. Dividing")
    print("               by a power of two only shifts the exponent, so the")
    print("               float32 store is lossless and the lattice survives.")
    print("               Any other divisor loses mantissa bits.")
    print()
    print("  CONSEQUENCE for the report: invariant 1 is CONDITIONAL. It is usable")
    print("  as stated only when dpr is a power of two — which covers the common")
    print("  1 and 2 but NOT Windows at 125% / 150%, a configuration persona")
    print("  itself produces via --force-device-scale-factor. A detector must")
    print("  read devicePixelRatio and either restrict to pow2 or accept that")
    print("  the test is a display-scale detector as much as a masking one.")
    print()
    print("  NOT ESTABLISHED HERE: whether a scale-independent formulation exists")
    print("  at all. dev_exact was the best candidate and it is not one. Saying so")
    print("  is the result; inventing a fourth predicate that happens to fit these")
    print("  15 rows would be curve-fitting, not measurement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
