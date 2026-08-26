#!/usr/bin/env python3
"""PS-185 — is the layer-ON draw consistent with UNIFORM selection?

WHY THIS EXISTS: CHECK THE INSTRUMENT BEFORE ATTRIBUTING ANYTHING (PS-14)
-------------------------------------------------------------------------
The layer-ON sweep returns ``TOO_NARROW`` on macos, linux AND android — three
arms out of three. An identical adverse result across every cell is exactly the
shape this project has learned to distrust, so it gets tested before it gets
reported as a product finding.

It does not survive the test. **All three are consistent with perfectly uniform
selection**, and the verdicts are an artefact of how the statistic is compared
to its bar, not evidence that ``pick()`` is skewed.

THE BIAS, STATED EXACTLY
------------------------
``collision_probability`` is the PLUG-IN (maximum-likelihood) Simpson index

    S_hat = sum_i (n_i / N)^2

over N observed draws. ``bar_for(arm)`` is ``1 / k`` for a k-entry pool — the
collision probability of a uniform draw from that pool **in the limit**. Those
two quantities are not comparable at finite N, because S_hat is a BIASED
estimator of the population Simpson index. Under a genuinely uniform draw:

    E[S_hat] = 1/k + (1 - 1/k)/N          (exact, not an approximation)

The second term is the probability that two draws collide *because the sample is
finite*, and it does not vanish until N is large. So a PERFECTLY uniform pick()
is EXPECTED to score above 1/k, and ``classify``'s ``p > bar`` test flags it.
The bar is only reachable by a draw that is more even than random — for a small
pool at N=24 that means an almost perfectly balanced one.

Worked, from this run:

    android  k=4  N=24   observed 0.2743   E[S_hat | uniform] = 0.2812
             -> the observed value is BELOW its own uniform expectation, and
                the module still reported TOO_NARROW.

That single line is the whole finding: an arm cannot be "worse than uniform"
while scoring better than uniform predicts. The comparison, not the pool, is
what failed.

WHAT THIS SCRIPT DOES
---------------------
Two independent checks, because one alone would be weaker:

1. **The unbiased estimator.** Simpson's estimator without replacement,

       S_unb = sum_i n_i (n_i - 1) / (N (N - 1))

   which is unbiased for the population collision probability and IS directly
   comparable to ``1/k``.

2. **A Monte-Carlo null.** Draw N times uniformly from k, TRIALS times, and ask
   how often a uniform pool scores at least as high as we observed. That p-value
   needs no distributional assumption and covers the small-N, small-k regime
   where a normal approximation would not be trusted.

An arm is only a genuine narrowing finding if it fails BOTH — a low p-value AND
an unbiased estimate above the bar.

WHAT IT IS NOT
--------------
Not a fix. PS-185 measures and reports; it does not change ``classify``. The
sweep records keep the module's own verdicts verbatim, and this analysis is
recorded ALONGSIDE them so the two can be read against each other. Whether the
gate should adopt the unbiased estimator (or compare against the finite-N
expectation) is a decision for the owner of ``engine_gpu_variance``.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import pathlib
import random
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.verify import engine_gpu_variance as egv  # noqa: E402

# Significance floor for "this draw is unusually collision-heavy". 0.05 is
# conventional; the observed p-values are nowhere near it, so nothing here
# turns on the exact choice.
ALPHA = 0.05


def simpson_plugin(counts: "list[int]", n: int) -> float:
    """What ``engine_gpu_variance.collision_probability`` computes. Biased."""
    return sum((c / n) ** 2 for c in counts)


def simpson_unbiased(counts: "list[int]", n: int) -> "float | None":
    """Unbiased for the population collision probability. Comparable to 1/k."""
    if n < 2:
        return None
    return sum(c * (c - 1) for c in counts) / (n * (n - 1))


def expected_plugin_under_uniform(k: int, n: int) -> "float | None":
    """E[S_hat] for a uniform draw: 1/k + (1 - 1/k)/n. Exact."""
    if k <= 0 or n <= 0:
        return None
    return 1.0 / k + (1.0 - 1.0 / k) / n


def monte_carlo_p(observed: float, k: int, n: int, trials: int, rng) -> float:
    """P(S_hat >= observed) when selection really is uniform over k."""
    hits = 0
    for _ in range(trials):
        draw = collections.Counter(rng.randrange(k) for _ in range(n))
        if simpson_plugin(list(draw.values()), n) >= observed - 1e-12:
            hits += 1
    return hits / trials


def analyse(record: dict, *, trials: int, seed: int) -> dict:
    rng = random.Random(seed)
    out = {}
    for arm in sorted(record["readings"]):
        values = [v for v in record["readings"][arm].values() if v]
        n = len(values)
        counts = list(collections.Counter(values).values())
        k = egv.fallback_pool_size(arm)
        bar = egv.bar_for(arm)
        module_verdict = record["result"]["per_arm"][arm]["verdict"]

        entry = {
            "seeds_readable": n,
            "distinct_observed": len(counts),
            "pool_size": k,
            "bar_collision_probability": bar,
            "module_verdict": module_verdict,
            "plugin_estimate": simpson_plugin(counts, n) if n else None,
            "unbiased_estimate": simpson_unbiased(counts, n),
            "expected_plugin_under_uniform": expected_plugin_under_uniform(k, n),
        }

        if n >= 2 and k and k > 0:
            p = monte_carlo_p(entry["plugin_estimate"], k, n, trials, rng)
            entry["monte_carlo_p_value"] = p
            entry["monte_carlo_trials"] = trials
            unbiased_over_bar = (
                entry["unbiased_estimate"] is not None
                and bar is not None
                and entry["unbiased_estimate"] > bar
            )
            entry["consistent_with_uniform"] = bool(p > ALPHA)
            entry["genuine_narrowing_finding"] = bool(
                p <= ALPHA and unbiased_over_bar
            )
            if entry["genuine_narrowing_finding"]:
                entry["reading"] = (
                    "FINDING STANDS: the draw is both unlikely under a uniform "
                    "null and above the bar on the unbiased estimator."
                )
            elif module_verdict == "TOO_NARROW":
                entry["reading"] = (
                    f"ARTEFACT OF THE ESTIMATOR, not a product finding. A "
                    f"uniform draw from this {k}-entry pool at N={n} is "
                    f"EXPECTED to score "
                    f"{entry['expected_plugin_under_uniform']:.4f} on the "
                    f"plug-in statistic the gate uses, versus the {bar:.4f} "
                    f"bar it is compared against; the observed "
                    f"{entry['plugin_estimate']:.4f} has p={p:.3f} under that "
                    f"uniform null. Nothing here shows pick() is skewed."
                )
            else:
                entry["reading"] = (
                    "Consistent with uniform selection; module verdict is not "
                    "a narrowing finding."
                )
        else:
            entry["monte_carlo_p_value"] = None
            entry["consistent_with_uniform"] = None
            entry["genuine_narrowing_finding"] = None
            entry["reading"] = (
                "not enough readable seeds, or no pool to compare against"
            )
        out[arm] = entry
    return out


def format_table(analysis: dict) -> str:
    lines = [
        "UNIFORM-SELECTION CHECK — is the observed draw consistent with pick() "
        "selecting uniformly?",
        "",
        f"  {'arm':9} {'k':>2} {'N':>3} {'plug-in':>8} {'unbiased':>9} "
        f"{'E[plug|unif]':>13} {'bar':>7} {'p':>7}  module -> reading",
    ]
    for arm, e in analysis.items():
        def f(x, w=7, p=4):
            return f"{x:{w}.{p}f}" if isinstance(x, float) else " " * w
        verdict = (
            "GENUINE" if e["genuine_narrowing_finding"]
            else ("artefact" if e["module_verdict"] == "TOO_NARROW" else "-")
        )
        lines.append(
            f"  {arm:9} {e['pool_size'] or 0:2d} {e['seeds_readable']:3d} "
            f"{f(e['plugin_estimate'], 8)} {f(e['unbiased_estimate'], 9)} "
            f"{f(e['expected_plugin_under_uniform'], 13)} "
            f"{f(e['bar_collision_probability'])} "
            f"{f(e['monte_carlo_p_value'], 7, 3)}  "
            f"{e['module_verdict']} -> {verdict}"
        )
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", required=True, help="a sweep.py record")
    ap.add_argument("--output", default="", help="write the analysis here")
    ap.add_argument("--trials", type=int, default=200000)
    ap.add_argument("--seed", type=int, default=20260826)
    args = ap.parse_args(argv)

    record = json.load(open(args.record, encoding="utf-8"))
    analysis = analyse(record, trials=args.trials, seed=args.seed)
    print(format_table(analysis))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "ticket": "PS-185",
                    "analysed_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                    "source_record": os.path.basename(args.record),
                    "source_mode": record.get("provenance", {}).get("mode"),
                    "null_hypothesis": (
                        "pick() selects uniformly from the arm's own pool"
                    ),
                    "alpha": ALPHA,
                    "monte_carlo_trials": args.trials,
                    "monte_carlo_seed": args.seed,
                    "per_arm": analysis,
                },
                fh, indent=2,
            )
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
