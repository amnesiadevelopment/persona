#!/usr/bin/env python3
"""PS-344 — the verdict on the PUBLISHED engine, as a guard that can FAIL.

WHAT THIS DECIDES
─────────────────
One question: **are the fingerprint patches PRESENT AND FUNCTIONING in the
binary an operator downloads?** It reads the committed readings from
``scripts/ps344_launch_published.py`` and answers with an exit code.

    exit 0  →  PATCHES LIVE. Every required signal below was observed, and
               every must-NOT-move negative control held still.
    exit 1  →  PATCHES ABSENT (or a negative control moved). This is the
               verdict the FALSIFICATION arm must produce; if it does not, the
               green verdict on the product arm is worthless.
    exit 2  →  INDETERMINATE — a reading could not be parsed, an arm is
               missing, a realm errored. NOT a pass. "I could not measure this"
               and "this is fine" are different answers, and collapsing them is
               the failure this project keeps recording.

WHY A SEPARATE FILE FROM THE COMPARISON
────────────────────────────────────────
``scripts/ps301_compare.py`` *renders* a per-vector table and always exits 0 —
it is an instrument, not a judge. A human reading a 44-row table can conclude
anything they like from it, which is exactly how "the artifact was verified"
comes to mean "somebody looked at a transcript". This file turns that table
into a decision with a direction, and the direction is demonstrated rather than
asserted: the falsification arm below is run through this same code and is
REQUIRED to come out red.

EVERY SIGNAL IS LAYER-**OFF**, AND THAT IS THE WHOLE POINT
───────────────────────────────────────────────────────────
With persona's JS masking layer ON, a difference from stock could be the engine
OR the extension. Only a layer-OFF difference is attributable to the ENGINE —
and the engine is what this ticket is about. A layer-ON reading passing would
tell you the extension replaced a value on top of a possibly-unpatched browser,
which is precisely the reading that would hide an unpatched shipped engine.

THE NEGATIVE CONTROLS ARE NOT DECORATION
─────────────────────────────────────────
``client_rects.width`` (patch 014 calls ``Offset()``, not ``Scale()`` — it MOVES
a rect and never resizes it) and ``client_rects_exempt.x`` (patch 014
deliberately exempts a ``position:absolute`` element with deterministic
top+left) must read IDENTICAL to stock. A run where everything differs is a
broken instrument, not a well-patched engine, and these two rows are what tells
those apart.

USAGE
─────
    # the product arm (expects exit 0)
    python3 scripts/ps344_verdict.py --dir readings/ps344-2026-09-07/artifacts

    # the falsification arm — stock run as if it were the product (expects 1)
    python3 scripts/ps344_verdict.py --dir readings/ps344-2026-09-07/artifacts \\
        --product readings-stock-as-product.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.ps301_compare import verdicts  # the ONE comparison, reused

# Each entry: (vector, needs_all_realms, needs_seed_derived, why).
#
# ``needs_all_realms`` is asserted only where an engine-level patch genuinely
# should reach every realm a detector can open. It is FALSE for the canvas /
# rects family because those are DOM-vs-worker split by construction
# (OffscreenCanvas has no toDataURL; a worker has no DOM), and demanding it
# there would fail a healthy engine.
#
# ``needs_seed_derived`` separates "returns a fixed fake" from "derives a value
# from the seed". Only the second is the product's contract, and a single-seed
# record cannot tell them apart — which is why two seeds are run.
REQUIRED = (
    (
        "switches.hardware_concurrency",
        True,
        True,
        "patch 005 — the switch reaches the page, in every realm, per seed",
    ),
    (
        "switches.timezone",
        True,
        False,
        "patch 018 — --timezone is honoured (a constant per run by design)",
    ),
    (
        "switches.tz_offset_minutes",
        True,
        False,
        "patch 018 — the Date offset moves with it, not just the Intl string",
    ),
    (
        "webgl.unmasked_vendor",
        True,
        False,
        "patch 011 — the GPU vendor a detector reads is not the host's",
    ),
    (
        "webgl.unmasked_renderer",
        True,
        True,
        "patch 011 — and the renderer is DERIVED from the seed, not a constant",
    ),
    (
        "webgl.readpixels_hash",
        True,
        True,
        "patch 016 — WebGL readback is perturbed per seed, in every realm",
    ),
    (
        "canvas.getimagedata_hash",
        True,
        True,
        "patch 012 — canvas readback is perturbed per seed, in every realm",
    ),
    (
        "canvas.todataurl_hash",
        False,
        True,
        "patch 013 — DOM realms only (a worker canvas has no toDataURL)",
    ),
    (
        "client_rects.x",
        False,
        True,
        "patch 014 — an eligible element's rect is offset per seed (DOM only)",
    ),
    (
        "switches.webdriver",
        False,
        False,
        "patch 009 — webdriver is false under CDP, where stock reports true",
    ),
)

# Must read IDENTICAL to stock. A movement here means the instrument, not the
# engine — see the module docstring.
NEGATIVE_CONTROLS = (
    ("client_rects.width", "patch 014 Offsets, never Scales — width must not move"),
    ("client_rects_exempt.x", "patch 014 exempts absolute+fixed top/left elements"),
)


def _row(vs: "list[dict]", vector: str, layer: str) -> "dict | None":
    for v in vs:
        if v["vector"] == vector and v["layer"] == layer:
            return v
    return None


def _every_realm_and_seed_differs(row: dict) -> "tuple[bool, list[str]]":
    """Does this vector differ from stock in EVERY realm, on EVERY seed?

    ⚠️ THIS IS NOT ``all_realms_differ``, AND THE GAP IS NOT ACADEMIC — it was
    found by sabotaging one realm's GPU string on ONE seed and watching this
    script still report PATCHES LIVE.

    ``ps301_compare`` computes a realm's ``differs_from_control`` as
    ``any(a != b for a, b in zip(product_values, control_values))`` across
    seeds. So a realm that leaks the host's real GPU on seed A while spoofing
    it on seed B is scored as DIFFERING, and ``all_realms_differ`` stays true.
    That is exactly the shape of a partial engine-level leak — the known
    chromium/linux realm leak this project has chased is one realm, not one
    vector — so the aggregate this guard leaned on could not see the defect it
    most needs to see.

    ``ps301_compare`` is deliberately left alone: it is the shared instrument,
    PS-301's committed table was rendered by it, and quietly redefining its
    columns would silently restate an older ticket's published findings. The
    STRICTER reading belongs to the judge, not to the renderer, so it is
    computed here from the per-seed lists that comparison already carries.
    """
    leaks = []
    for name, per in row["per_realm"].items():
        for idx, (pv, cv) in enumerate(zip(per["product"], per["control"])):
            if pv == cv:
                leaks.append(f"{name}[seed#{idx}] reads stock: {pv}")
    return (not leaks), leaks


def _arm_is_readable(payload: dict) -> "list[str]":
    """Every reason this arm cannot be judged. Empty list = judgeable."""
    problems = []
    for rec in payload.get("records", []):
        tag = f"seed{rec.get('seed')}/layer{rec.get('masking_layer')}"
        if rec.get("error"):
            problems.append(f"{tag}: cell errored — {rec['error']}")
            continue
        realms = (rec.get("reading") or {}).get("realms")
        if not realms:
            problems.append(f"{tag}: no realms in reading")
            continue
        for name, body in realms.items():
            if isinstance(body, dict) and body.get("error"):
                problems.append(f"{tag}/{name}: {body['error']}")
    return problems


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="Directory holding readings-*.json")
    ap.add_argument("--product", default="readings-published-152.json")
    ap.add_argument("--control", default="readings-stock-cft-152.json")
    ap.add_argument(
        "--expect",
        choices=("live", "absent"),
        help=(
            "Assert the verdict rather than merely report it. With "
            "--expect absent (the falsification arm) the script exits 0 when "
            "the patches ARE reported absent — so a CI runner can demand the "
            "guard's red arm without inverting exit codes by hand."
        ),
    )
    args = ap.parse_args(argv)

    d = pathlib.Path(args.dir)
    try:
        product = json.loads((d / args.product).read_text(encoding="utf-8"))
        control = json.loads((d / args.control).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"INDETERMINATE: could not read an arm — {type(exc).__name__}: {exc}")
        return 2

    print(f"product arm : {product.get('engine_label')}")
    print(f"  binary    : {product.get('binary', {}).get('version_string')}")
    print(f"  sha256    : {product.get('binary', {}).get('sha256')}")
    print(f"control arm : {control.get('engine_label')}")
    print(f"  binary    : {control.get('binary', {}).get('version_string')}")
    print(f"  sha256    : {control.get('binary', {}).get('sha256')}")
    print()

    unreadable = _arm_is_readable(product) + _arm_is_readable(control)
    if unreadable:
        print("INDETERMINATE — an arm could not be read:")
        for p in unreadable:
            print(f"  · {p}")
        print("\nNothing is claimed. This is NOT a pass.")
        return 2

    vs = verdicts(product, control)

    missing: "list[str]" = []
    print("REQUIRED SIGNALS (layer OFF — engine-attributable):")
    for vector, need_all, need_seed, why in REQUIRED:
        row = _row(vs, vector, "off")
        if row is None:
            missing.append(f"{vector}: vector absent from the comparison")
            print(f"  ?? {vector:34} ABSENT")
            continue
        ok_differs = row["any_realm_differs"]
        strict_all, leaks = _every_realm_and_seed_differs(row)
        ok_all = (not need_all) or strict_all
        ok_seed = (not need_seed) or row["moves_with_seed"]
        ok = ok_differs and ok_all and ok_seed
        flag = "OK" if ok else "!!"
        detail = []
        if not ok_differs:
            detail.append("reads IDENTICAL to stock")
        if not ok_all:
            detail.append(
                "does not differ in every realm on every seed — "
                + "; ".join(leaks[:4])
            )
        if not ok_seed:
            detail.append("does not move with the seed")
        print(
            f"  {flag} {vector:34} differs={row['any_realm_differs']!s:5} "
            f"all_realms={strict_all!s:5} "
            f"seed_derived={row['moves_with_seed']!s:5}   {why}"
        )
        if not ok:
            missing.append(f"{vector}: " + "; ".join(detail))

    print("\nNEGATIVE CONTROLS (must read IDENTICAL to stock):")
    broken_controls: "list[str]" = []
    for vector, why in NEGATIVE_CONTROLS:
        row = _row(vs, vector, "off")
        if row is None:
            broken_controls.append(f"{vector}: vector absent")
            print(f"  ?? {vector:34} ABSENT")
            continue
        ok = not row["any_realm_differs"]
        print(f"  {'OK' if ok else '!!'} {vector:34} differs={row['any_realm_differs']!s:5}   {why}")
        if not ok:
            broken_controls.append(f"{vector}: MOVED — instrument suspect")

    print()
    if broken_controls:
        print("VERDICT: PATCHES ABSENT / INSTRUMENT SUSPECT — a negative control moved:")
        for b in broken_controls:
            print(f"  · {b}")
        verdict = "absent"
    elif missing:
        print(
            f"VERDICT: PATCHES ABSENT — {len(missing)} of {len(REQUIRED)} required "
            "signals were not observed:"
        )
        for m in missing:
            print(f"  · {m}")
        verdict = "absent"
    else:
        print(
            f"VERDICT: PATCHES LIVE — all {len(REQUIRED)} required signals observed "
            f"and both negative controls held still."
        )
        verdict = "live"

    if args.expect:
        agreed = verdict == args.expect
        print(
            f"\n--expect {args.expect}: {'MET' if agreed else 'NOT MET'} "
            f"(measured: {verdict})"
        )
        return 0 if agreed else 1

    return 0 if verdict == "live" else 1


if __name__ == "__main__":
    raise SystemExit(main())
