#!/usr/bin/env python3
"""PS-301 — the measureText verdict, as a guard that can actually FAIL.

WHY THIS FILE EXISTS
────────────────────
`ps301_repro.sh` reproduces the patch-015 measureText defect and prints the
readings. What it did NOT do is *decide* anything: 162 lines, one conditional,
zero non-zero exit paths. It ended with a prose paragraph beginning "READ:"
that told a human what to conclude. A reader who ran it and skimmed the output
got a confident-looking transcript and an exit code of 0 — the same exit code
it would produce if every number in it were plausible.

That is the shape this project has been bitten by repeatedly: **a guard nobody
has seen fail.** A check that cannot go red is not evidence, however detailed
its output. This file is the missing verdict, and it is deliberately separate
from the capture step so that it can be run — and *falsified* — without a
browser present.

THE VERDICT DIRECTION, STATED EXPLICITLY
────────────────────────────────────────
This script exits **non-zero when the measureText defect is PRESENT.**

    exit 1  →  DEFECT: widths are negative and the observed/stock ratio is
               constant across strings, i.e. `Shuffle` multiplied by an
               offset-shaped value. This is the state of the self-built
               144 engine, layer OFF.
    exit 0  →  PLAUSIBLE: widths are positive and the per-string ratios are
               near 1. This is what the layer-ON cells read, and what a FIXED
               patch 015 would read layer-OFF.

So when patch 015 is repaired, this script turns green on the layer-OFF cells,
and it is a regression guard from that day forward. Until then it is red, and
its redness is the finding.

⚠️ IT IS NOT A TEST OF OUR JS. A layer-ON reading passing tells you the
extension replaced the value, NOT that the engine is healthy — that distinction
is the whole reason the report's attributions are taken layer-OFF.

WHAT PS-345 ADDED, AND WHAT IT DELIBERATELY DID NOT
────────────────────────────────────────────────────
PS-345 measured the same defect on the PUBLISHED `personium-152.0.7977.75`
engine and drew a third seed, 777, whose factor is **positive**: widths come
back spec-legal and still collapsed by seven orders of magnitude. Since
`norm_x`'s sign is a coin flip, roughly half of all seeds are in that class,
where the two seeds both earlier readings drew (24601, 5150) were not.

**That case does not need a new rule and did not get one.** The
`constant and implausible` branch below was written for it and catches it —
verified by running this guard against PS-345's own committed seed-777 reading
(exit 1, DEFECT). What was added is the OPPOSITE of a fix: a `--self-test` case
that PINS the behaviour already present, so the negative-width rule cannot be
mistaken for the whole guard and the stronger branch quietly dropped. The
sharper framing PS-345 contributes is about the invariant, not about this file:
what condemns is the factor's **centre** (≈0 where the consumer needs ≈1), and
the sign is incidental.

PS-345 also added `widths_from_measure_json`, a reader for that reading's
four-strings-per-file JSON. It is a second INPUT SHAPE for the same `verdict()`,
which is why this file remains the single authoritative measureText guard: a
second script keyed on the same predicates was written during PS-345 and
deliberately dropped rather than committed alongside this one.

WHAT IT REFUSES TO DO
─────────────────────
It never *infers* a reading. Given a cell it cannot parse, or fewer than two
strings to compare, it exits 2 (INDETERMINATE) rather than picking a verdict —
because "I could not measure this" and "this is fine" are different answers and
collapsing them is how the original script came to say nothing while looking
thorough.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# A real browser's TextMetrics.width is non-negative (per spec) and a healthy
# patch perturbs it by a fraction of a percent. Anything at or below this is not
# "slightly off", it is a different quantity.
PLAUSIBLE_RATIO_LO = 0.90
PLAUSIBLE_RATIO_HI = 1.10

# Two ratios are "the same" if they agree to within this RELATIVE tolerance.
# The measured spread on the real defect is exactly 0.0 (bit-identical float64
# across four strings), so this bound is enormously generous — it exists so the
# check does not depend on exact float equality, not because the signal is
# marginal.
CONSTANT_REL_TOL = 1e-9

EXIT_PLAUSIBLE = 0
EXIT_DEFECT = 1
EXIT_INDETERMINATE = 2


def verdict(observed: dict[str, float], stock: dict[str, float]) -> tuple[int, str, dict]:
    """Decide PLAUSIBLE / DEFECT / INDETERMINATE from two string->width maps.

    Returns (exit_code, headline, detail). Pure — no I/O, so it is testable.
    """
    shared = sorted(set(observed) & set(stock))
    if len(shared) < 2:
        return (
            EXIT_INDETERMINATE,
            f"INDETERMINATE: need >=2 strings measured in BOTH arms, got {len(shared)}",
            {"shared": shared},
        )

    ratios = {}
    for s in shared:
        if stock[s] == 0:
            return (
                EXIT_INDETERMINATE,
                f"INDETERMINATE: stock width for {s!r} is 0; ratio undefined",
                {"string": s},
            )
        ratios[s] = observed[s] / stock[s]

    values = list(ratios.values())
    spread = max(values) - min(values)
    mean = sum(values) / len(values)
    # Relative spread, guarded against a mean of 0.
    rel_spread = abs(spread / mean) if mean else float("inf")
    constant = rel_spread <= CONSTANT_REL_TOL

    negative = [s for s in shared if observed[s] < 0]
    implausible = [s for s in shared if not (PLAUSIBLE_RATIO_LO <= ratios[s] <= PLAUSIBLE_RATIO_HI)]

    detail = {
        "ratios": ratios,
        "spread": spread,
        "relative_spread": rel_spread,
        "constant_across_strings": constant,
        "negative_widths": negative,
        "implausible_ratios": implausible,
    }

    # A negative width is impossible per spec, so it alone settles it.
    if negative:
        return (
            EXIT_DEFECT,
            f"DEFECT: {len(negative)}/{len(shared)} widths are NEGATIVE "
            f"(impossible per spec) — e.g. {negative[0]!r} -> {observed[negative[0]]!r}",
            detail,
        )

    # The arithmetic signature: one constant factor applied to every string is a
    # MULTIPLY. A perturbation would leave a different ratio per string.
    if constant and implausible:
        return (
            EXIT_DEFECT,
            f"DEFECT: observed/stock ratio is CONSTANT across {len(shared)} strings "
            f"(spread {spread:.3e}) at {mean:.6e} — a multiply by an offset-shaped "
            f"value, not a perturbation",
            detail,
        )

    if implausible:
        return (
            EXIT_DEFECT,
            f"DEFECT: {len(implausible)} ratio(s) outside "
            f"[{PLAUSIBLE_RATIO_LO}, {PLAUSIBLE_RATIO_HI}] — e.g. "
            f"{implausible[0]!r} -> {ratios[implausible[0]]:.6e}",
            detail,
        )

    return (
        EXIT_PLAUSIBLE,
        f"PLAUSIBLE: {len(shared)} widths positive, all ratios within "
        f"[{PLAUSIBLE_RATIO_LO}, {PLAUSIBLE_RATIO_HI}] (mean {mean!r})",
        detail,
    )


# ── reading the committed harness JSON ───────────────────────────────────────
#
# There is deliberately no reader for the PS-301 *realm harness* JSON here. That
# harness records ONE string per realm, so the path could only ever yield a
# single sample and would return INDETERMINATE on its own; the multi-string
# comparison this guard needs lives in the shell repro's transcript, parsed
# below. Two helpers that read the harness shape (`_cell`,
# `_widths_from_reading`) were carried here with no caller in the whole repo and
# are removed rather than left to read as a supported path — the self-test
# covers the single-sample case directly.
#
# `widths_from_measure_json` below IS a supported path, and is a different
# shape: `readings/ps345-2026-09-07/artifacts/measure.py` emits FOUR strings per
# file, which is exactly the multi-string evidence `verdict()` needs. It was
# added in PS-345 so that reading could be judged by THIS guard instead of by a
# second copy of it — see the PS-345 note in the module docstring.


def widths_from_measure_json(path: pathlib.Path) -> dict[str, float]:
    """Parse one `measure.py` reading into {string: width}.

    The shape is ``{"label":…, "binary":…, "args":[…], "metrics": {string:
    {"width": float, …}}}``. Entries without a numeric `width` are skipped
    rather than guessed at, so a malformed file yields too few samples and
    `verdict()` returns INDETERMINATE — which is the honest answer, and the
    reason this reader does not raise on them itself.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    metrics = doc.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    out: dict[str, float] = {}
    for name, cell in metrics.items():
        if isinstance(cell, dict) and isinstance(cell.get("width"), (int, float)):
            out[name] = float(cell["width"])
    return out


def from_transcript(path: pathlib.Path) -> dict[str, dict[str, float]]:
    """Parse the committed repro transcript into {arm_label: {string: width}}.

    The transcript is the multi-string evidence — four strings per arm — which
    is what makes the constant-ratio test possible at all.
    """
    arms: dict[str, dict[str, float]] = {}
    label = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("---"):
            label = line.lstrip("- ").strip()
            arms[label] = {}
            continue
        if label and "| w=" in line:
            parts = [p.strip() for p in line.split("|")]
            name = parts[0]
            wtxt = next((p[2:] for p in parts if p.startswith("w=")), None)
            if wtxt is not None:
                try:
                    arms[label][name] = float(wtxt)
                except ValueError:
                    pass
    return {k: v for k, v in arms.items() if v}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", type=pathlib.Path,
                    help="repro-transcript.txt — the multi-string evidence")
    ap.add_argument("--observed-arm", default="self-built, --fingerprint=24601",
                    help="substring naming the arm under test")
    ap.add_argument("--stock-arm", default="STOCK control",
                    help="substring naming the control arm")
    ap.add_argument("--observed", type=pathlib.Path,
                    help="measure.py reading JSON for the arm under test "
                         "(PS-345 shape; use with --stock)")
    ap.add_argument("--stock", type=pathlib.Path,
                    help="measure.py reading JSON for the stock control arm")
    ap.add_argument("--self-test", action="store_true",
                    help="prove this guard can reach BOTH verdicts, then exit 0")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    # The measure.py JSON pair (PS-345 readings). Same verdict(), different reader.
    if args.observed or args.stock:
        if not (args.observed and args.stock):
            print("give BOTH --observed and --stock", file=sys.stderr)
            return EXIT_INDETERMINATE
        obs = widths_from_measure_json(args.observed)
        stk = widths_from_measure_json(args.stock)
        code, headline, detail = verdict(obs, stk)
        print(f"observed arm : {args.observed}")
        print(f"control arm  : {args.stock}")
        for s, r in detail.get("ratios", {}).items():
            print(f"  {s:30s} obs={obs[s]!r:26s} ratio={r!r}")
        if "spread" in detail:
            print(f"  spread across strings = {detail['spread']!r}")
        print()
        print(headline)
        print(f"exit {code}  ({'DEFECT' if code == 1 else 'PLAUSIBLE' if code == 0 else 'INDETERMINATE'})")
        return code

    if not args.transcript:
        print("give --transcript <repro-transcript.txt>, or --observed/--stock "
              "<measure.py JSON> (or --self-test)", file=sys.stderr)
        return EXIT_INDETERMINATE

    arms = from_transcript(args.transcript)
    obs_key = next((k for k in arms if args.observed_arm in k), None)
    stk_key = next((k for k in arms if args.stock_arm in k), None)
    if not obs_key or not stk_key:
        print(f"INDETERMINATE: could not find both arms. saw: {list(arms)}", file=sys.stderr)
        return EXIT_INDETERMINATE

    code, headline, detail = verdict(arms[obs_key], arms[stk_key])
    print(f"observed arm : {obs_key}")
    print(f"control arm  : {stk_key}")
    for s, r in detail.get("ratios", {}).items():
        print(f"  {s:30s} obs={arms[obs_key][s]!r:26s} ratio={r!r}")
    if "spread" in detail:
        print(f"  spread across strings = {detail['spread']!r}")
    print()
    print(headline)
    print(f"exit {code}  ({'DEFECT' if code == 1 else 'PLAUSIBLE' if code == 0 else 'INDETERMINATE'})")
    return code


def _self_test() -> int:
    """Demonstrate the guard reaching every verdict. This is the point of the file.

    A guard nobody has SEEN fail is indistinguishable from a guard that cannot.
    """
    stock = {"A": 11.62939453125, "hello": 41.08056640625,
             "persona-PS301": 128.3134765625, "The quick brown fox jumps": 228.82763671875}

    # The REAL measured defect, seed 24601, layer OFF (from repro-transcript.txt).
    defect = {"A": -0.000006698308010171917, "hello": -0.000023661617660485952,
              "persona-PS301": -0.00007390609937276053,
              "The quick brown fox jumps": -0.00013180032613590952}

    # The REAL layer-ON reading: our JS replaces the value, so it is plausible.
    # Ratio measured from the committed harness JSON: 1.0000056741129943.
    healthy = {k: v * 1.0000056741129943 for k, v in stock.items()}

    # A CONSTANT ratio well inside the plausible band — the case that proves the
    # constant-ratio rule alone does not condemn: a real scale-shaped noise
    # factor is constant too, and must NOT read as a defect.
    scaled = {k: v * 1.0000031 for k, v in stock.items()}

    # PS-345, seed 777: POSITIVE widths, still collapsed by seven orders of
    # magnitude. This case is pinned here because it is the one a NEGATIVE-WIDTH
    # rule alone would pass, and `norm_x`'s sign is a coin flip — so roughly half
    # of all seeds land in this class rather than in the two seeds (24601, 5150)
    # both prior readings happened to draw.
    #
    # ⚠️ It is pinned to record behaviour this guard ALREADY HAS, not to add any.
    # The `constant and implausible` branch above was written for exactly this
    # case and catches it. The case exists so the negative-width rule cannot be
    # mistaken for the whole guard and the stronger branch quietly dropped.
    # Ratio measured on the published personium-152.0.7977.75 AppImage.
    defect_777 = {k: v * 1.0110584148231562e-06 for k, v in stock.items()}

    cases = [
        ("measured defect (seed 24601, layer OFF)", defect, stock, EXIT_DEFECT),
        ("measured defect (PS-345 seed 777: POSITIVE widths, still collapsed)",
         defect_777, stock, EXIT_DEFECT),
        ("measured layer-ON (JS rescues it)", healthy, stock, EXIT_PLAUSIBLE),
        ("healthy scale-shaped noise factor", scaled, stock, EXIT_PLAUSIBLE),
        ("single sample (harness JSON shape)", {"one": 1.0}, {"one": 1.0}, EXIT_INDETERMINATE),
    ]
    ok = True
    for name, obs, stk, want in cases:
        got, headline, _ = verdict(obs, stk)
        mark = "ok " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"[{mark}] want exit {want}, got {got}  — {name}")
        print(f"        {headline}")
    print()
    print("This guard reaches DEFECT, PLAUSIBLE and INDETERMINATE on real inputs."
          if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
