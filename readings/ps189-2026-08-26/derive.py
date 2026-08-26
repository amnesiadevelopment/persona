"""PS-189: turn the realm sweep into records the CONSISTENCY GATE can judge.

``scripts/ps189_realm_gpu.py`` reads the WebGL identity pair from twelve realms
in one launch. The merged consistency gate
(``verify.matrix_consistency``) judges a CHECKER-MATRIX record — rows tagged
with a ``checker`` and a ``vector``. This script is the adapter between them,
and it exists so PS-189's finding is judged BY THE PROJECT'S OWN INSTRUMENT
rather than by a bespoke assertion written to agree with it.

WHY THE REALMS ARE MAPPED ONTO ``checker``
--------------------------------------------
The gate's question is *"does one record name more than one graphics identity?"*
It does not care WHY two rows differ — only that a single profile, in a single
run, presented two identities. PS-186 asked that of two third-party checkers.
This asks it of two REALMS of the same browser. The question is identical and so
is the answer's meaning: a profile whose page says ``Apple M1`` while its
service worker says ``Apple M2`` has exactly the defect PS-155/PS-161 named,
observed one layer closer to the cause.

Each realm becomes a ``checker`` named ``realm:<name>`` so a reader of the
derived record can never mistake it for a third-party reading. The vector is
``gpu_claimed`` — what the renderer SAYS IT IS — which is the vector both
PS-186 GPU rows carry.

WHAT THIS DOES NOT DO
---------------------
It does not re-measure anything and it invents no values: every row's value is
copied verbatim from the committed realm sweep. A realm that failed to read is
carried through as an absent value rather than dropped, because "the realm did
not answer" and "the realm agreed" must not collapse into one another — that
collapse is the exact failure ``COVERAGE_HOLE`` exists to prevent.

Run from the repo root::

    .venv/bin/python -m readings.ps189-2026-08-26.derive
"""

from __future__ import annotations

import json
import pathlib

# The vector both GPU identity rows carry in a checker-matrix record: what the
# renderer DECLARES, as opposed to `gpu_rendered` (hashes of pixels a checker
# drew itself, which are per-checker by construction and never comparable).
GPU_CLAIMED = "gpu_claimed"

# Realms that are not identity readings at all — the fix-reachability arms and
# the WebGPU probe. Excluded by NAME rather than by guessing from their shape,
# so a future realm added to the sweep fails loudly here instead of being
# silently dropped into the record as a null identity row.
NON_IDENTITY_REALMS = frozenset({
    "fix_blob_registration",
    "fix_cross_origin_registration",
    "fix_register_patchable",
    "webgpu_adapter",
})


def rows_for(realms: dict) -> "list[dict]":
    """One ``gpu_claimed`` row pair per realm, in the matrix record's shape."""
    rows = []
    for name in sorted(realms):
        if name in NON_IDENTITY_REALMS:
            continue
        reading = realms[name] or {}
        checker = f"realm:{name}"
        renderer = reading.get("unmasked_renderer")
        vendor = reading.get("unmasked_vendor")
        # A realm that errored carries no identity. Recorded as a READ row with
        # a null value would be a lie; recorded as absent, the gate files it as
        # a coverage hole, which is what actually happened.
        state = "read" if renderer else "absent"
        rows.append({
            "checker": checker,
            "item": "gpu_renderer",
            "vector": GPU_CLAIMED,
            "state": state,
            "adverse": False,
            "value": renderer,
        })
        rows.append({
            "checker": checker,
            "item": "gpu_vendor",
            "vector": GPU_CLAIMED,
            "state": "read" if vendor else "absent",
            "adverse": False,
            "value": vendor,
        })
    return rows


def derive(sweep: dict) -> "list[dict]":
    """Every cell of a realm sweep, as a checker-matrix record."""
    out = []
    for rec in sweep.get("records", []):
        reading = rec.get("reading") or {}
        realms = reading.get("realms") or {}
        if not realms:
            continue
        out.append({
            "schema_version": 4,
            "source": "PS-189 realm sweep (scripts/ps189_realm_gpu.py)",
            "arm": rec.get("arm"),
            "seed": rec.get("seed"),
            "masking_layer_state": rec.get("masking_layer"),
            "declared_machine": rec.get("arm"),
            "observed_at": sweep.get("observed_at"),
            "venue": sweep.get("venue"),
            "readings": rows_for(realms),
        })
    return out


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    sweep = json.loads((here / "realm-gpu.json").read_text(encoding="utf-8"))
    out_dir = here / "derived-matrix"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for rec in derive(sweep):
        name = f"realm-matrix.chromium.{rec['arm']}.seed{rec['seed']}.json"
        (out_dir / name).write_text(json.dumps(rec, indent=2), encoding="utf-8")
        written.append(name)
    for name in written:
        print(f"wrote derived-matrix/{name}")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
