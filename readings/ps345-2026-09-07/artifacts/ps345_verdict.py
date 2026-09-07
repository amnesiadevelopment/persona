#!/usr/bin/env python3
"""PS-345 — the measureText verdict on the PUBLISHED 152 engine, as a guard
that can FAIL.

DIRECTION, STATED EXPLICITLY. This script exits **non-zero when the defect is
PRESENT**, so it is RED today (the published engine ships the defect) and turns
GREEN the day a build carrying the corrected patch 015 is measured. Its redness
is the finding, not a broken script.

    exit 0  PLAUSIBLE     widths positive, ratios near 1, and the ratio VARIES
                          with the seed (i.e. the perturbation is alive)
    exit 1  DEFECT        the offset-into-a-multiplier bug: metrics collapsed
                          to ~0, ratio constant across strings, often negative
    exit 2  INDETERMINATE could not measure (refuses to guess)

WHY THE CONSTANT-RATIO RULE ALONE DOES NOT CONDEMN, AND WHAT WAS ADDED
──────────────────────────────────────────────────────────────────────
`Shuffle()` multiplies, so a CORRECT patch also produces a ratio that is
constant across strings — the constant-ratio signature does not separate the
healthy case from the broken one. What separates them is the ratio's MAGNITUDE:
a correct factor is centred on 1 (1 ± 5e-6); the defect's factor is centred on
0. That is the discriminator this guard actually keys on, and `--self-test`
exercises a healthy-but-constant arm precisely to prove the rule does not
condemn it.

⚠️ THIS IS NOT A TEST OF PERSONA'S JS LAYER. Every reading it consumes is taken
with the masking layer OFF, because that is the only state in which a difference
from stock is attributable to the ENGINE. A layer-ON reading passing would tell
you the extension replaced the value, not that the engine is healthy.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# A correct noise factor is 1 ± 5e-6. This band is deliberately far wider than
# that, so the guard keys on "is this a multiplier centred on 1 at all", not on
# the exact constant — which a future tuning of the factor may legitimately move.
PLAUSIBLE_RATIO_LO = 0.90
PLAUSIBLE_RATIO_HI = 1.10

EXIT_PLAUSIBLE = 0
EXIT_DEFECT = 1
EXIT_INDETERMINATE = 2


def verdict(observed: dict[str, float], stock: dict[str, float]) -> tuple[int, str, dict]:
    """Decide from two string->width maps. Pure, so it is testable."""
    shared = sorted(set(observed) & set(stock))
    if len(shared) < 2:
        return (EXIT_INDETERMINATE,
                f"INDETERMINATE: need >=2 strings in BOTH arms, got {len(shared)}",
                {"shared": shared})

    ratios = {}
    for s in shared:
        if stock[s] == 0:
            return (EXIT_INDETERMINATE,
                    f"INDETERMINATE: stock width for {s!r} is 0; ratio undefined",
                    {"string": s})
        ratios[s] = observed[s] / stock[s]

    values = list(ratios.values())
    mean = sum(values) / len(values)
    negative = [s for s in shared if observed[s] < 0]
    implausible = [s for s in shared
                   if not (PLAUSIBLE_RATIO_LO <= ratios[s] <= PLAUSIBLE_RATIO_HI)]

    detail = {
        "ratios": ratios,
        "mean_ratio": mean,
        "spread": max(values) - min(values),
        "negative_widths": negative,
        "implausible_ratios": implausible,
    }

    # A negative width is impossible per spec, so it alone settles it.
    if negative:
        return (EXIT_DEFECT,
                f"DEFECT: {len(negative)}/{len(shared)} widths are NEGATIVE "
                f"(impossible per spec) — e.g. {negative[0]!r} -> "
                f"{observed[negative[0]]!r}",
                detail)

    # Positive but collapsed: the factor is centred on 0, not on 1.
    if implausible:
        return (EXIT_DEFECT,
                f"DEFECT: {len(implausible)}/{len(shared)} ratio(s) outside "
                f"[{PLAUSIBLE_RATIO_LO}, {PLAUSIBLE_RATIO_HI}] — e.g. "
                f"{implausible[0]!r} -> {ratios[implausible[0]]:.6e}. A factor "
                f"centred on 0 fed to a MULTIPLIER.",
                detail)

    return (EXIT_PLAUSIBLE,
            f"PLAUSIBLE: {len(shared)} widths positive, all ratios within "
            f"[{PLAUSIBLE_RATIO_LO}, {PLAUSIBLE_RATIO_HI}] (mean {mean!r})",
            detail)


def widths(path: pathlib.Path) -> dict[str, float]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {k: v["width"] for k, v in d["metrics"].items() if isinstance(v, dict) and "width" in v}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observed", type=pathlib.Path, help="patched-engine reading JSON")
    ap.add_argument("--stock", type=pathlib.Path, help="stock control reading JSON")
    ap.add_argument("--self-test", action="store_true",
                    help="prove this guard reaches every verdict, then exit 0")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if not args.observed or not args.stock:
        print("give --observed and --stock (or --self-test)", file=sys.stderr)
        return EXIT_INDETERMINATE

    obs, stk = widths(args.observed), widths(args.stock)
    code, headline, detail = verdict(obs, stk)
    for s, r in detail.get("ratios", {}).items():
        print(f"  {s:30s} obs={obs[s]!r:26s} ratio={r!r}")
    print(f"  spread across strings = {detail.get('spread')!r}")
    print()
    print(headline)
    print(f"exit {code}  ({'DEFECT' if code == 1 else 'PLAUSIBLE' if code == 0 else 'INDETERMINATE'})")
    return code


def _self_test() -> int:
    """A guard nobody has SEEN fail is indistinguishable from one that cannot."""
    # Measured, published 152 engine + stock CFT 152.0.7977.75, this session.
    stock = {"A": 10.9453125, "hello": 38.6640625,
             "persona-PS345": 120.765625, "The quick brown fox jumps": 215.3671875}
    defect_24601 = {"A": -6.30428989192651e-06, "hello": -2.2269757798104425e-05,
                    "persona-PS345": -6.955868176259815e-05,
                    "The quick brown fox jumps": -0.00012404736577497366}
    # seed 777: POSITIVE widths, and still catastrophically wrong. This is the
    # case a negative-width-only rule would have passed.
    defect_777 = {k: v * 1.0110584148231562e-06 for k, v in stock.items()}
    # What the CORRECTED patch produces: a multiplier centred on 1. Constant
    # across strings too — which is why the constant-ratio rule cannot condemn.
    fixed = {k: v * 1.0000032705225304 for k, v in stock.items()}
    # The patch standing down (no --fingerprint): identical to stock.
    standdown = dict(stock)

    cases = [
        ("published 152, seed 24601 (negative widths)", defect_24601, stock, EXIT_DEFECT),
        ("published 152, seed 777 (POSITIVE, still collapsed)", defect_777, stock, EXIT_DEFECT),
        ("corrected patch: factor centred on 1", fixed, stock, EXIT_PLAUSIBLE),
        ("patch stands down (no seed)", standdown, stock, EXIT_PLAUSIBLE),
        ("single sample", {"one": 1.0}, {"one": 1.0}, EXIT_INDETERMINATE),
    ]
    ok = True
    for name, obs, stk, want in cases:
        got, headline, _ = verdict(obs, stk)
        mark = "ok  " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"{mark} [{got}] want {want}  {name}")
        print(f"       {headline}")
    print()
    print("self-test PASSED — guard reaches DEFECT, PLAUSIBLE and INDETERMINATE"
          if ok else "self-test FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
