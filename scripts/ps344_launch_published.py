"""PS-344: run the PUBLISHED engine — the one an operator downloads — and read
what a page actually sees, against a version-matched stock control.

WHY THIS SCRIPT EXISTS AND WHY IT IS NOT PS-301
------------------------------------------------
PS-301 executed a **self-built** binary (Chromium 144.0.7559.132, a CI
artifact). `scripts/ps307_verify_patches_in_tree.sh` reads the **source tree**.
`.github/workflows/engine-gpu-variance.yml` reads the published release on ONE
vector (GPU seed-variance). None of the three is "the engine our users download,
executed, read across every vector, against a control".

PS-343 established the published asset's PROVENANCE (digest + content-level
switch strings, plus two spot executions). A switch STRING present in machine
code is not a patch that RUNS, and two spot reads are not a per-vector table.
This script is the PS-301 harness pointed at the downloaded artifact.

⛔ PS-301's NUMBERS ARE NOT CARRIED ACROSS. They are readings of a different
binary; the ticket forbids it and so does arithmetic. Every figure this script
emits is measured here, in this run, on this artifact.

THE METHOD IS REUSED, NOT REINVENTED
-------------------------------------
Everything load-bearing — the probe, the realm sweep, the cell runner, the
provenance block — is IMPORTED from ``scripts.ps301_engine_launch``. One probe
body, one instrument. A re-implementation would manufacture exactly the
cross-run disagreement the comparison exists to detect, and a reader could not
tell a real difference between the 144 and 152 engines from a difference
between two probes. What this file adds is: which binaries, which labels, which
ticket, and a THIRD arm PS-301 had no need of.

THE THIRD ARM — FALSIFICATION, AND IT IS NOT OPTIONAL
------------------------------------------------------
The product arm and the control arm together can only ever show a difference.
They cannot show that the harness is CAPABLE of reporting "the patches are
absent" — and a verification that has only been seen to pass is not known to
work. So a third arm runs the **stock control a second time, labelled as if it
were the product**. Its verdict table is what "patches absent" looks like
through this exact instrument, and ``scripts/ps344_verdict.py`` is required to
go RED on it. If it does not, the green verdict on the real product arm means
nothing.

THE CONTROL IS VERSION-MATCHED, WHICH PS-301's WAS NOT QUITE
-------------------------------------------------------------
Stock Chrome for Testing **152.0.7977.75** — the same Chromium version as the
published engine, not merely the same major. So a difference between the arms
cannot be a difference between two Chromium releases; it is the patch set or it
is nothing.

THE ARM IDS ARE PARAMETERS, NOT LITERALS (PS-370)
--------------------------------------------------
They used to be the literals ``published-152`` / ``stock-cft-152``, and the arm
id IS the output filename (``readings-{engine_id}.json``), which IS the
default ``--product`` / ``--control`` of ``scripts/ps344_verdict.py``. So the
string ``152`` was load-bearing at three hops, and a caller measuring a
different release would have written files the verdict could not find — or,
worse, left last release's files in place for the verdict to read as if they
described this one. ``--product-id`` / ``--control-id`` exist so a scheduled
caller can derive both from the version it actually resolved.

⚠️ THE DEFAULTS ARE DELIBERATELY THE OLD LITERALS. They are the names of the
COMMITTED readings in ``readings/ps344-2026-09-07/artifacts/``, so the
reproduction recipe in that REPORT.md still runs verbatim. They are a
historical record, NOT a sensible default for a new reading: any caller
measuring a release other than 152.0.7977.75 must pass both explicitly.
``.github/workflows/published-engine-verdict.yml`` does, via
``.github/scripts/ps344_gate_plan.py``.

⚠️ THE STOCK ARM IS A CONTROL AND IS NOT THE PRODUCT. Both arms are staged the
same way — a directory holding a SYMLINK under the name ``_engine_binary()``
expects — because the resolver's refusal to fall back to a chromium on PATH is a
real guard and is respected: it is not edited, ``fingerprint_chromium_filename()``
is not edited, nothing is dropped on PATH, and no artifact is renamed. The
control is labelled ``stock`` in every row it produces.

Run from the repo root::

    python3 -m scripts.ps344_launch_published \\
        --published-dir /tmp/ps344/engine-published \\
        --stock-dir     /tmp/ps344/engine-stock \\
        -o readings/ps344-2026-09-07/artifacts
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

from scripts.ps301_engine_launch import (  # the ONE instrument, imported
    ARM,
    SEEDS,
    _serve_probe_page,
    binary_provenance,
    read_cell,
)

TICKET = "PS-344"


def _run_arm(
    srv_url: str,
    *,
    engine_dir: str,
    engine_id: str,
    label: str,
    seeds: "list[int]",
    layers: "list[bool]",
    timezone: str,
) -> dict:
    """One engine, every seed x layer cell, plus its provenance block.

    ⚠️ ``PERSONA_ENGINE_DIR`` is re-pointed and ``src.core.config`` evicted from
    ``sys.modules`` BEFORE each arm, because config reads the env at import
    time. Without the eviction the second arm silently launches the FIRST arm's
    binary while every row claims the second's label — a false reading that
    looks exactly like "the two engines agree".
    """
    os.environ["PERSONA_ENGINE_DIR"] = engine_dir
    for mod in list(sys.modules):
        if mod.startswith("src.core.config") or mod.startswith("src.services.verify"):
            del sys.modules[mod]

    prov = binary_provenance(engine_dir)
    print(f"\n=== arm: {engine_id} — {label}")
    print(f"  resolved: {prov.get('resolved_path')}")
    print(f"  real:     {prov.get('real_path')}")
    print(f"  size:     {prov.get('size_bytes')}")
    print(f"  sha256:   {prov.get('sha256')}")
    print(f"  version:  {prov.get('version_string')}")

    records = []
    for seed in seeds:
        for install_layer in layers:
            print(
                f"  cell seed={seed} layer={'on' if install_layer else 'off'} ...",
                flush=True,
            )
            rec = read_cell(
                srv_url,
                engine=engine_id,
                seed=seed,
                install_layer=install_layer,
                timezone=timezone,
            )
            if rec.get("error"):
                print(f"    ERROR: {rec['error']}")
            else:
                realms = (rec.get("reading") or {}).get("realms") or {}
                print(f"    ok — {len(realms)} realms")
            records.append(rec)

    return {
        "ticket": TICKET,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_label": label,
        "engine_id": engine_id,
        "binary": prov,
        "host": {
            "uname": " ".join(os.uname()),
            "container_hostname": os.uname().nodename,
        },
        "records": records,
    }


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--published-dir", required=True)
    ap.add_argument("--stock-dir", required=True)
    ap.add_argument("--published-label", required=True)
    ap.add_argument("--stock-label", required=True)
    ap.add_argument("-o", "--out", required=True)
    # See the module docstring: the arm id IS the filename IS the verdict's
    # default. These defaults name PS-344's COMMITTED readings so its recipe
    # still runs verbatim; a caller measuring any other release must pass both.
    ap.add_argument(
        "--product-id",
        default="published-152",
        help=(
            "Arm id for the PRODUCT arm; the reading is written to "
            "readings-<id>.json and ps344_verdict.py must be pointed at that "
            "same name. Default names PS-344's committed 152.0.7977.75 reading."
        ),
    )
    ap.add_argument(
        "--control-id",
        default="stock-cft-152",
        help=(
            "Arm id for the version-matched STOCK CONTROL arm. Same "
            "filename/verdict coupling as --product-id."
        ),
    )
    ap.add_argument(
        "--falsification-id",
        default="stock-as-product",
        help=(
            "Arm id for the FALSIFICATION arm — the control run a second time "
            "and labelled as if it were the product."
        ),
    )
    ap.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    ap.add_argument("--timezone", default="America/Chicago")
    ap.add_argument(
        "--skip-falsification",
        action="store_true",
        help=(
            "Skip the third (stock-as-product) arm. Present so a re-run can "
            "resume, NOT so the falsification can be waived — a run without it "
            "cannot show the instrument is able to report absence."
        ),
    )
    args = ap.parse_args(argv)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    layers = [True, False]

    published_dir = os.path.abspath(args.published_dir)
    stock_dir = os.path.abspath(args.stock_dir)

    # ONE server for every arm: the page bytes are then provably identical
    # across arms, so a difference cannot be a difference in what was served.
    with _serve_probe_page() as srv:
        arms = [
            (args.product_id, args.published_label, published_dir),
            (args.control_id, args.stock_label, stock_dir),
        ]
        if not args.skip_falsification:
            arms.append(
                (
                    args.falsification_id,
                    "FALSIFICATION ARM — the STOCK control run a second time and "
                    "labelled as if it were the product. Its table is what "
                    "'patches absent' looks like through this instrument.",
                    stock_dir,
                )
            )
        for engine_id, label, engine_dir in arms:
            payload = _run_arm(
                srv.url,
                engine_dir=engine_dir,
                engine_id=engine_id,
                label=label,
                seeds=seeds,
                layers=layers,
                timezone=args.timezone,
            )
            dest = out_dir / f"readings-{engine_id}.json"
            dest.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"wrote {dest}")

    print(f"\narm: {ARM} (Linux). macOS and Windows are UNMEASURED here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
