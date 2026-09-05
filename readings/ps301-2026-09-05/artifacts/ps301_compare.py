"""PS-301: compare the self-built engine's readings against the stock control.

Every claim in the report is derived here, not typed by hand. The comparison
answers one question per vector: *does the value the patched engine presents
DIFFER from the value stock chromium presents under the same flags, on the same
host, at the same page?* — because that is the only form of the question a
GPU-less container can answer honestly.

Three verdicts, and the third is the one that matters most:

  PATCH-ATTRIBUTABLE  the two engines disagree ⇒ the difference is our patch set
  NO-OBSERVABLE-DIFF  the two engines agree     ⇒ the patch changed nothing here
  SEED-DERIVED        the value moves with the seed ⇒ derived, not a constant

SEED-DERIVED is reported SEPARATELY from PATCH-ATTRIBUTABLE because they are
different claims. A patch that returns one fixed fake value and a patch that
derives a value per seed both "differ from stock"; only the second is what an
antidetect engine's contract asks for, and a single-seed record cannot tell
them apart.

Run::

    python3 -m scripts.ps301_compare readings/ps301-2026-09-05/artifacts
"""

from __future__ import annotations

import argparse
import json
import pathlib

REALMS = (
    "page",
    "iframe_same_origin",
    "iframe_about_blank",
    "iframe_srcdoc",
    "worker_blob",
    "worker_in_iframe",
    "worker_nested",
)

# The vectors compared, as (label, realm-relative dotted path). Kept explicit
# rather than derived from the payload so a vector that VANISHES from a reading
# is reported as absent instead of silently dropping out of the table.
VECTORS = (
    ("switches.hardware_concurrency", "Q1 navigator.hardwareConcurrency"),
    ("switches.platform", "Q1 navigator.platform"),
    ("switches.user_agent", "Q1 navigator.userAgent"),
    ("switches.uad_platform", "Q1 userAgentData.platform"),
    ("switches.screen_width", "Q1 screen.width"),
    ("switches.screen_height", "Q1 screen.height"),
    ("switches.device_pixel_ratio", "Q1 devicePixelRatio"),
    ("switches.timezone", "Q1 Intl timeZone (patch 018)"),
    ("switches.tz_offset_minutes", "Q1 Date offset (patch 018)"),
    ("switches.webdriver", "Q1 navigator.webdriver (patch 009)"),
    ("webgl.unmasked_vendor", "Q2 WebGL UNMASKED_VENDOR (patch 011)"),
    ("webgl.unmasked_renderer", "Q2 WebGL UNMASKED_RENDERER (patch 011)"),
    ("webgl.masked_vendor", "Q2 WebGL VENDOR"),
    ("webgl.masked_renderer", "Q2 WebGL RENDERER"),
    ("webgl.readpixels_hash", "Q3 WebGL readPixels (patch 016)"),
    ("canvas.getimagedata_hash", "Q3 canvas getImageData (patch 012)"),
    ("canvas.todataurl_hash", "Q3 canvas toDataURL (patch 013)"),
    ("canvas.measuretext_width", "Q3 measureText width (patch 015)"),
    ("client_rects.x", "Q3 getBoundingClientRect.x (patch 014, ELIGIBLE elem)"),
    ("client_rects.y", "Q3 getBoundingClientRect.y (patch 014, ELIGIBLE elem)"),
    ("client_rects.width", "Q3 getBoundingClientRect.width (Offset ⇒ must NOT move)"),
    ("client_rects_exempt.x", "Q3 clientRects x on the EXEMPT shape (must NOT move)"),
)

_ABSENT = object()


def _dig(node, path: str):
    cur = node
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _ABSENT
        cur = cur[part]
    return cur


def _fmt(v) -> str:
    if v is _ABSENT:
        return "<absent>"
    return json.dumps(v)


def _cells(payload: dict) -> dict:
    """(seed, layer) -> reading."""
    out = {}
    for rec in payload["records"]:
        out[(rec["seed"], rec["masking_layer"])] = rec
    return out


def compare(product: dict, control: dict) -> dict:
    p_cells, c_cells = _cells(product), _cells(control)
    seeds = sorted({k[0] for k in p_cells})
    rows = []

    for path, label in VECTORS:
        row: dict = {"vector": path, "label": label, "realms": {}}
        for realm in REALMS:
            per: dict = {}
            for layer in ("on", "off"):
                for seed in seeds:
                    prec = p_cells.get((seed, layer))
                    crec = c_cells.get((seed, layer))
                    pv = (
                        _dig(
                            ((prec or {}).get("reading") or {})
                            .get("realms", {})
                            .get(realm, {}),
                            path,
                        )
                        if prec
                        else _ABSENT
                    )
                    cv = (
                        _dig(
                            ((crec or {}).get("reading") or {})
                            .get("realms", {})
                            .get(realm, {}),
                            path,
                        )
                        if crec
                        else _ABSENT
                    )
                    per[f"layer{layer}/seed{seed}"] = {
                        "product": _fmt(pv),
                        "control": _fmt(cv),
                    }
            row["realms"][realm] = per
        rows.append(row)
    return {"seeds": seeds, "rows": rows}


def verdicts(product: dict, control: dict) -> "list[dict]":
    """One verdict per (vector, layer-state), read across realms and seeds.

    Layer ON and layer OFF are kept as SEPARATE verdicts on purpose. With the
    layer on, a difference from stock could be the engine OR our JS; only the
    layer-OFF row attributes a difference to the ENGINE alone, which is the
    question this ticket asks.
    """
    p_cells, c_cells = _cells(product), _cells(control)
    seeds = sorted({k[0] for k in p_cells})
    out = []
    for path, label in VECTORS:
        for layer in ("off", "on"):
            per_realm: dict = {}
            for realm in REALMS:
                pvals, cvals = [], []
                for seed in seeds:
                    prec, crec = p_cells.get((seed, layer)), c_cells.get((seed, layer))
                    pvals.append(
                        _fmt(
                            _dig(
                                ((prec or {}).get("reading") or {})
                                .get("realms", {})
                                .get(realm, {}),
                                path,
                            )
                        )
                    )
                    cvals.append(
                        _fmt(
                            _dig(
                                ((crec or {}).get("reading") or {})
                                .get("realms", {})
                                .get(realm, {}),
                                path,
                            )
                        )
                    )
                differs = any(a != b for a, b in zip(pvals, cvals))
                seed_derived = len(set(pvals)) > 1
                control_seed_derived = len(set(cvals)) > 1
                per_realm[realm] = {
                    "product": pvals,
                    "control": cvals,
                    "differs_from_control": differs,
                    "product_moves_with_seed": seed_derived,
                    "control_moves_with_seed": control_seed_derived,
                }
            distinct_product = sorted(
                {v for r in per_realm.values() for v in r["product"]}
            )
            out.append(
                {
                    "vector": path,
                    "label": label,
                    "layer": layer,
                    "any_realm_differs": any(
                        r["differs_from_control"] for r in per_realm.values()
                    ),
                    "all_realms_differ": all(
                        r["differs_from_control"] for r in per_realm.values()
                    ),
                    "moves_with_seed": any(
                        r["product_moves_with_seed"] for r in per_realm.values()
                    ),
                    "realm_coherent": len(distinct_product) <= len(seeds),
                    "distinct_product_values_across_realms": distinct_product,
                    "per_realm": per_realm,
                }
            )
    return out


def render(vs: "list[dict]") -> str:
    lines = []
    header = (
        f"{'vector':38} {'layer':5} {'differs':10} {'all realms':10} "
        f"{'seed-derived':12} {'realm-coherent':14}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for v in vs:
        lines.append(
            f"{v['vector']:38} {v['layer']:5} "
            f"{('YES' if v['any_realm_differs'] else 'no'):10} "
            f"{('YES' if v['all_realms_differ'] else 'no'):10} "
            f"{('YES' if v['moves_with_seed'] else 'no'):12} "
            f"{('YES' if v['realm_coherent'] else 'NO — SPLIT'):14}"
        )
    return "\n".join(lines)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir", help="Directory holding readings-*.json")
    ap.add_argument("--product", default="readings-self-built-144.json")
    ap.add_argument("--control", default="readings-stock-cft-144.json")
    args = ap.parse_args(argv)

    d = pathlib.Path(args.dir)
    product = json.loads((d / args.product).read_text(encoding="utf-8"))
    control = json.loads((d / args.control).read_text(encoding="utf-8"))

    vs = verdicts(product, control)
    table = render(vs)
    print(table)

    (d / "verdicts.json").write_text(
        json.dumps(
            {
                "product_engine": product["engine_label"],
                "product_binary": product["binary"],
                "control_engine": control["engine_label"],
                "control_binary": control["binary"],
                "verdicts": vs,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (d / "verdicts.txt").write_text(table + "\n", encoding="utf-8")
    (d / "side-by-side.json").write_text(
        json.dumps(compare(product, control), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"\nwrote {d/'verdicts.json'}, {d/'verdicts.txt'}, {d/'side-by-side.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
