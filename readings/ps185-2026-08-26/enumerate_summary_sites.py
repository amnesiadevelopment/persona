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
searches by behaviour instead, along **two axes**, because a stored summary
block can fail in two ways and each axis is blind to the other's members.

**Axis 1 — destroy the readings, keep the summary.** Null an arm's seeds, or
empty a readback leg's vectors, then re-render: *any number that does not move
is not computed from those readings.*

**Axis 2 — poison the summary, keep the readings.** The inverse, and it is the
axis round 5 did not run. Destroying readings cannot detect a figure that never
consulted readings in the first place, so axis 1 returns a clean sweep over
live members. Axis 2 walks **every scalar field of every stored summary block**
and poisons each in turn: *any line that moves is a rendered figure depending
on a summary rather than on the evidence.*

Axis 2 is a GENERIC WALK rather than a list of known fields, deliberately. A
list can only re-find what someone already named, which is the failure mode
this script exists to end. Walking the records found ``pool_size`` — a site on
nobody's list, where the estimator FORMULAE were recounted but were being fed a
``k`` read from the stored block, leaving ``E[plug-in | uniform]`` and the
``1/k`` bar half-derived and moving the one sentence the artefact finding rests
on.

THE EXEMPTION, AND WHY IT IS ASSERTED RATHER THAN LISTED
--------------------------------------------------------
Axis 2's rule is **not** a blanket "no rendered line may depend on a stored
field", because one site depends on one **on purpose**: ``gpu_completeness``
cross-checks its recount against the sweep's stored ``seeds_readable`` and
DISCLOSES any disagreement, so that a record whose summary and readings tell
different stories says so out loud instead of silently preferring one. A
blanket rule would delete that disclosure.

So ``seeds_readable`` is exempt — but the exemption is **tested, not waived**.
For those fields the script asserts the disclosure actually FIRES, and that
what moves is only prose: **no ``|`` table row may move**, because a table row
is a published figure and the row that mixes a recounted percentage with a
frozen count is the exact artifact this class produces. An exemption nobody
checks is how a defect hides behind the word "intentional".

USE
---
Run it after ANY change to ``derive.py``::

    python3 readings/ps185-2026-08-26/enumerate_summary_sites.py

Axis 1: every scenario should report changed lines. Axis 2: every field should
report NO change, except the asserted exemption above. Either way round, a
violation is a live defect of this class.

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


# ---------------------------------------------------------------------------
# AXIS 2 — poison the stored summary, leave the readings intact
# ---------------------------------------------------------------------------

# The one field a rendered line may legitimately depend on, SCOPED TO THE
# RECORD THAT CROSS-CHECKS IT. `gpu_completeness` compares its own recount
# against the GPU sweep's stored `seeds_readable` and DISCLOSES a
# disagreement, so a record whose summary and readings tell different stories
# says so out loud. See the module docstring: the exemption is asserted below,
# not waived.
#
# ⚠️ SCOPED BY LABEL PREFIX, NOT BY BARE FIELD NAME, and that distinction is
# load-bearing. The uniformity records carry a `seeds_readable` of their OWN,
# which is a different quantity that nothing cross-checks and which is now
# fully recounted. A name-keyed exemption matched those too, and would have
# waived any future defect in them for no better reason than a shared field
# name — an exemption is only as good as its scope.
DISCLOSED_FIELDS = {("gpu[on]", "seeds_readable"), ("gpu[off]", "seeds_readable")}


def _is_disclosed(label: str, field: str) -> bool:
    return any(
        label.startswith(prefix) and field == name
        for prefix, name in DISCLOSED_FIELDS
    )


def poison(value):
    """A clearly-wrong replacement of the SAME TYPE.

    Type-preserving on purpose: a replacement that changed the type could move
    the render by raising a formatting error rather than by being consulted,
    which would report a site that is actually clean.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, float):
        return 0.123456
    if isinstance(value, int):
        return 999
    if isinstance(value, str):
        return "POISONED"
    if isinstance(value, list):
        return ["POISONED"]
    if isinstance(value, dict):
        return {k: poison(v) for k, v in value.items()}
    return value


def _summary_fields():
    """Every scalar field of every stored summary block, as (label, mutator).

    A GENERIC WALK of the records rather than a list of known field names —
    the whole point of the axis. Anything a future record grows is covered
    without editing this file.
    """
    out = []
    base = load_all()

    # GPU sweeps: result.per_arm[arm][field], plus the result-level blocks.
    for mode in ("on", "off"):
        for arm in sorted(base[mode]["result"]["per_arm"]):
            for field in base[mode]["result"]["per_arm"][arm]:
                def mk(mode=mode, arm=arm, field=field):
                    def apply(r):
                        blk = r[mode]["result"]["per_arm"][arm]
                        blk[field] = poison(blk[field])
                    return apply
                out.append(
                    (f"gpu[{mode}].result.per_arm.{arm}.{field}", field, mk()))
        for field in ("findings", "inconclusive", "arms_checked"):
            if field not in base[mode]["result"]:
                continue

            def mk2(mode=mode, field=field):
                def apply(r):
                    r[mode]["result"][field] = poison(r[mode]["result"][field])
                return apply
            out.append((f"gpu[{mode}].result.{field}", field, mk2()))

    # Uniformity records: per_arm[arm][field]. No raw readings of their own,
    # so EVERY field here is a stored summary.
    for mode in ("uon", "uoff"):
        for arm in sorted(base[mode]["per_arm"]):
            for field in base[mode]["per_arm"][arm]:
                def mk3(mode=mode, arm=arm, field=field):
                    def apply(r):
                        blk = r[mode]["per_arm"][arm]
                        blk[field] = poison(blk[field])
                    return apply
                out.append(
                    (f"unif[{mode}].per_arm.{arm}.{field}", field, mk3()))

    # Readback records: the verdicts block is the run's account of itself.
    for key in ("rb", "rep", "repc"):
        for engine, vecs in (base[key].get("verdicts") or {}).items():
            for vec, blk in vecs.items():
                if not isinstance(blk, dict):
                    continue
                for field in blk:
                    def mk4(key=key, engine=engine, vec=vec, field=field):
                        def apply(r):
                            b = r[key]["verdicts"][engine][vec]
                            b[field] = poison(b[field])
                        return apply
                    out.append(
                        (f"rb[{key}].verdicts.{engine}.{vec}.{field}",
                         field, mk4()))
        if base[key].get("cross_engine_contrast") is not None:
            def mk5(key=key):
                def apply(r):
                    r[key]["cross_engine_contrast"] = poison(
                        r[key]["cross_engine_contrast"])
                return apply
            out.append(
                (f"rb[{key}].cross_engine_contrast",
                 "cross_engine_contrast", mk5()))
    return out


def run_axis2(base: "list[str]", quiet: bool) -> "list[str]":
    """Poison each stored field in turn. Return the labels that VIOLATE."""
    violations: "list[str]" = []
    fields = _summary_fields()
    print(f"\n{'=' * 76}\nAXIS 2 — poison the stored summary, readings INTACT")
    print(f"  {len(fields)} stored fields walked\n")

    for label, field, apply in fields:
        records = load_all()
        apply(records)
        after = render(records).split("\n")
        changed = [(b, a) for b, a in zip(base, after) if b != a]
        rows_moved = [(b, a) for b, a in changed
                      if b.startswith("|") or a.startswith("|")]

        if _is_disclosed(label, field):
            # Exempt — but PROVE the exemption rather than assuming it. The
            # disclosure must actually fire, and it must move PROSE ONLY: a
            # moving `|` row would be a published figure taken from the
            # summary, which is what this class ships.
            fired = any("stored summary disagrees" in a for _, a in changed)
            if not fired:
                violations.append(
                    f"{label} — exempt as DISCLOSED, but poisoning it did not "
                    "fire the drift disclosure")
                print(f"  ✗ {label}: exemption claimed, disclosure SILENT")
            elif rows_moved:
                violations.append(
                    f"{label} — disclosed field moved a TABLE ROW, not just "
                    "the disclosure prose")
                print(f"  ✗ {label}: moved a published row")
            elif not quiet:
                print(f"  ~ {label}: exempt, disclosure fired (prose only)")
            continue

        if changed:
            violations.append(label)
            print(f"  ✗ {label}: {len(changed)} line(s) MOVED — rendered from "
                  "the stored summary, not the readings")
            if not quiet:
                for b, a in changed[:3]:
                    print(f"      - {b[:130]}")
                    print(f"      + {a[:130]}")
        elif not quiet:
            print(f"  ✓ {label}")
    return violations


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="report only the verdict per scenario")
    ap.add_argument("--axis", choices=("1", "2", "both"), default="both",
                    help="which mutation axis to run (default: both)")
    args = ap.parse_args(argv)

    base = render(load_all()).split("\n")
    inert: "list[str]" = []
    violations: "list[str]" = []

    if args.axis in ("1", "both"):
        print(f"\n{'=' * 76}\nAXIS 1 — destroy the readings, summary INTACT")
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
                print("  ⚠️  NOTHING MOVED — a rendered figure is not coming "
                      "from the readings this mutation destroyed.")
            elif not args.quiet:
                for _, b, a in changed:
                    print(f"  - {b[:150]}")
                    print(f"  + {a[:150]}")

    if args.axis in ("2", "both"):
        violations = run_axis2(base, args.quiet)

    print()
    if inert or violations:
        if inert:
            print("AXIS 1 — DEFECTS OF THIS CLASS REMAIN, in:")
            for name in inert:
                print(f"  - {name}")
        if violations:
            print("AXIS 2 — RENDERED FIGURES DEPEND ON A STORED SUMMARY:")
            for name in violations:
                print(f"  - {name}")
        return 1
    print("Axis 1: every scenario moved the rendered output — no figure "
          "survives the destruction of its own readings.")
    print("Axis 2: no stored summary field moves the render, except the "
          "asserted drift disclosure. The class is closed on both axes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
