#!/usr/bin/env python3
"""Enumerate the "renders a stored summary" defect class in ``derive.py``.

WHY THIS EXISTS
---------------
Rounds 2, 3 and 4 of PS-185 each fixed the ONE instance they were handed, and
each time the next review found another member of the same class. The class is:

    any figure rendered from a summary the sweep wrote about ITSELF, rather
    than recounted from the raw readings.

``result.per_arm.seeds_readable`` is such a summary: it records what the run
*believed* it read, so a truncated sweep carries a full-looking count and every
consumer of that field inherits the blindness. So do
``collision_probability``, ``distinct_identities`` and ``verdict``, and so does
the ``verdicts`` block in the readback records one file over.

THE SEARCH IS BY BEHAVIOUR, NOT BY GREP
---------------------------------------
A grep is shaped by the instances you already know about, so it returns those
instances again — which is exactly how this ticket reached round 5. This script
searches by behaviour instead:

1. load the committed records;
2. **destroy the raw readings** — null an arm's seeds, or empty a readback
   leg's vectors — while leaving **every stored summary block untouched**;
3. re-render each section and diff it against the unmutated render;
4. **any number that does not move is not computed from those readings.**

That finds sites no field-name search can. Two of the four this added were
invisible to any grep: the positive control counted ``None == None`` as
agreement (it reads no summary field at all, and got STRONGER the more the
sweep failed), and the firefox narrative asserted its CONCLUSION as a literal,
so with the leg lost it printed ``@1337 -> None, @4242 -> None - different``.

USE
---
Run it after ANY change to ``derive.py``::

    python3 readings/ps185-2026-08-26/enumerate_summary_sites.py

Every scenario should report changed lines. A scenario that reports **no
change** in a section whose readings it destroyed is a live defect of this
class — the rendered number is coming from somewhere other than the evidence.

This script only READS the committed records; every mutation is applied to an
in-memory copy. It never writes to the reading set.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_derive():
    spec = importlib.util.spec_from_file_location(
        "ps185_derive_enumerate", HERE / "derive.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


D = _load_derive()


def load_all() -> dict:
    """A FRESH copy of every committed record, safe for a caller to mutate."""
    return {
        "off": D.load(D.LAYER_OFF),
        "on": D.load(D.LAYER_ON),
        "uoff": D.load(D.UNIF_OFF),
        "uon": D.load(D.UNIF_ON),
        "rb": D.load(D.READBACK),
        "rep": D.load(D.REPLICATE),
        "repc": D.load(D.REPLICATE_CHROME),
    }


def render(r: dict) -> str:
    """The whole derived document, from one record set."""
    return "\n".join([
        D.gpu_section(r["off"], r["on"], r["uoff"], r["uon"]),
        D.readback_section(r["rb"], r["rep"], r["repc"]),
        D.coverage_section(r["off"], r["on"], [
            ("readback-vectors.three-seeds.json", r["rb"]),
            ("readback-vectors.replicate.json", r["rep"]),
            ("readback-vectors.replicate-chromium.json", r["repc"]),
        ]),
    ])


def truncate_arm(rec: dict, arm: str, keep: int = 12) -> None:
    """Null all but ``keep`` of an arm's readings. Summary left UNTOUCHED."""
    for seed in sorted(rec["readings"][arm])[keep:]:
        rec["readings"][arm][seed] = None


def empty_readback_legs(rec: dict, engine: str) -> None:
    """Every leg of ``engine`` produces no vectors. Verdicts left intact."""
    for leg in (rec.get("readings", {}).get(engine, {}) or {}).values():
        if isinstance(leg, dict):
            leg["reading"] = {"vectors": {}}


def _truncate(arm: str, mode: str):
    def apply(r: dict) -> None:
        truncate_arm(r[mode], arm)
    return apply


def _truncate_both(arm: str):
    def apply(r: dict) -> None:
        truncate_arm(r["on"], arm)
        truncate_arm(r["off"], arm)
    return apply


def _empty(engine: str):
    def apply(r: dict) -> None:
        empty_readback_legs(r["rb"], engine)
    return apply


SCENARIOS = [
    ("android layer-ON truncated 24 -> 12", _truncate("android", "on")),
    ("macos layer-OFF truncated 24 -> 12", _truncate("macos", "off")),
    ("linux layer-OFF truncated 24 -> 12", _truncate("linux", "off")),
    ("windows truncated in BOTH modes 24 -> 12", _truncate_both("windows")),
    ("firefox readback legs emptied (verdicts kept)", _empty("firefox")),
    ("chromium readback legs emptied (verdicts kept)", _empty("chromium")),
]


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="report only the verdict per scenario")
    args = ap.parse_args(argv)

    base = render(load_all()).split("\n")
    inert: "list[str]" = []

    for name, apply in SCENARIOS:
        records = load_all()
        apply(records)
        after = render(records).split("\n")

        changed = [
            (i, b, a) for i, (b, a) in enumerate(zip(base, after)) if b != a
        ]
        print(f"\n{'=' * 76}\nMUTATION: {name}")
        print(f"  lines changed: {len(changed)} of {len(base)}")
        if not changed:
            inert.append(name)
            print("  ⚠️  NOTHING MOVED — a rendered figure is not coming from "
                  "the readings this mutation destroyed.")
        elif not args.quiet:
            for _, b, a in changed:
                print(f"  - {b[:150]}")
                print(f"  + {a[:150]}")

    print()
    if inert:
        print("DEFECTS OF THIS CLASS REMAIN, in:")
        for name in inert:
            print(f"  - {name}")
        return 1
    print("Every scenario moved the rendered output: no site in this sweep "
          "renders a destroyed reading from a stored summary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
