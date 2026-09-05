"""PS-301 — re-check the REPORT's load-bearing numbers against the artifacts.

A report is only evidence if its figures can be re-derived from the records it
ships. This re-reads the committed JSON and re-computes every claim the report
rests on, and exits non-zero on a miss — so a later reader can tell "the report
still describes these artifacts" from "the artifacts moved underneath it".

Run from the repo root::

    python3 readings/ps301-2026-09-05/artifacts/verify_claims.py
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

fails: "list[str]" = []
oks: "list[str]" = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (oks if ok else fails).append(f"{name}{(' — ' + detail) if detail else ''}")


product = json.loads((HERE / "readings-self-built-144.json").read_text())
control = json.loads((HERE / "readings-stock-cft-144.json").read_text())

REALMS = (
    "page",
    "iframe_same_origin",
    "iframe_about_blank",
    "iframe_srcdoc",
    "worker_blob",
    "worker_in_iframe",
    "worker_nested",
)


def cell(payload, seed, layer):
    for r in payload["records"]:
        if r["seed"] == seed and r["masking_layer"] == layer:
            return r
    raise KeyError((seed, layer))


def realm(payload, seed, layer, name):
    return cell(payload, seed, layer)["reading"]["realms"][name]


# --- provenance ------------------------------------------------------------
check(
    "binary sha256 is the one the report names",
    product["binary"]["sha256"]
    == "3b403afbdd6e6847d394a630a27e097831701123fad738de25e8e0d71c3c0fe4",
    product["binary"]["sha256"],
)
check(
    "binary size is 456,688,328 bytes",
    product["binary"]["size_bytes"] == 456688328,
    str(product["binary"]["size_bytes"]),
)
check(
    "binary reports Chromium 144.0.7559.132",
    "144.0.7559.132" in product["binary"]["version_string"],
    product["binary"]["version_string"],
)
check(
    "control is stock Chrome for Testing 144.0.7559.133",
    "Chrome for Testing" in control["binary"]["version_string"]
    and "144.0.7559.133" in control["binary"]["version_string"],
    control["binary"]["version_string"],
)

# --- §3 GPU ----------------------------------------------------------------
for seed, expect in ((24601, "RTX 4070"), (5150, "RTX 3080 Laptop GPU")):
    vals = {
        realm(product, seed, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS
    }
    check(
        f"§3 seed {seed}: ONE identity across all 7 realms, layer OFF",
        len(vals) == 1,
        f"{len(vals)} distinct",
    )
    check(
        f"§3 seed {seed}: that identity is {expect}",
        all(expect in v for v in vals),
        next(iter(vals)),
    )
    cvals = {
        realm(control, seed, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS
    }
    check(
        f"§3 seed {seed}: stock control reads SwiftShader in all 7 realms",
        len(cvals) == 1 and "SwiftShader" in next(iter(cvals)),
        next(iter(cvals)),
    )

p24 = realm(product, 24601, "off", "page")["webgl"]["unmasked_renderer"]
p51 = realm(product, 5150, "off", "page")["webgl"]["unmasked_renderer"]
check("§3 the identity MOVES with the seed", p24 != p51)

check(
    "§3 layer OFF really was off (no layer modules recorded)",
    not cell(product, 24601, "off").get("layer_installed"),
    str(cell(product, 24601, "off").get("layer_installed")),
)

# The claim that our JS pool could not have produced these strings.
sys.path.insert(0, str(HERE.parents[2]))
try:
    from src.services.browser.gpu_ext import LINUX_GPUS  # noqa: E402

    nvidia = [g for g in LINUX_GPUS if "NVIDIA" in str(g)]
    check(
        "§3 LINUX_GPUS contains ZERO NVIDIA entries (so this is not our JS)",
        len(nvidia) == 0,
        f"{len(nvidia)} of {len(LINUX_GPUS)}",
    )
except Exception as exc:  # noqa: BLE001
    check("§3 LINUX_GPUS importable", False, str(exc))

# --- §4 measureText --------------------------------------------------------
STOCK_W = realm(control, 24601, "off", "page")["canvas"]["measuretext_width"]
for seed in (24601, 5150):
    for r in ("page", "iframe_same_origin", "iframe_about_blank", "iframe_srcdoc"):
        w = realm(product, seed, "off", r)["canvas"]["measuretext_width"]
        check(f"§4 seed {seed} {r}: measureText width is NEGATIVE", w < 0, str(w))
    for r in ("worker_blob", "worker_in_iframe", "worker_nested"):
        w = realm(product, seed, "off", r)["canvas"]["measuretext_width"]
        check(
            f"§4 seed {seed} {r}: worker realm is UNSPOOFED (equals stock)",
            w == STOCK_W,
            f"{w} vs stock {STOCK_W}",
        )

# The constant-ratio argument, re-derived from the transcript's four strings.
RATIOS = {
    24601: (
        (-0.000006698308010171917, 11.62939453125),
        (-0.000023661617660485952, 41.08056640625),
        (-0.00007390609937276053, 128.3134765625),
        (-0.00013180032613590952, 228.82763671875),
    ),
    5150: (
        (-0.00004504364377844973, 11.62939453125),
        (-0.0001591156267377214, 41.08056640625),
        (-0.0004969911816754288, 128.3134765625),
        (-0.0008863084425699669, 228.82763671875),
    ),
}
for seed, pairs in RATIOS.items():
    rs = [obs / stock for obs, stock in pairs]
    check(
        f"§4 seed {seed}: observed/stock ratio is CONSTANT across 4 strings "
        "(⇒ multiplication, not perturbation)",
        max(rs) - min(rs) == 0.0,
        f"ratio={rs[0]:.6e} spread={max(rs) - min(rs):.3e}",
    )
    check(
        f"§4 seed {seed}: |ratio| <= 5e-6, the patch's own noise_x bound",
        abs(rs[0]) <= 5e-6,
        f"{abs(rs[0]):.3e}",
    )

# --- §5 switches -----------------------------------------------------------
for seed in (24601, 5150):
    tzs = {realm(product, seed, "off", r)["switches"]["timezone"] for r in REALMS}
    check(
        f"§5 seed {seed}: timezone is America/Chicago in all 7 realms",
        tzs == {"America/Chicago"},
        str(tzs),
    )
    ctz = {realm(control, seed, "off", r)["switches"]["timezone"] for r in REALMS}
    check(f"§5 seed {seed}: stock reads UTC in all 7 realms", ctz == {"UTC"}, str(ctz))

hc = [
    realm(product, s, "off", "page")["switches"]["hardware_concurrency"]
    for s in (24601, 5150)
]
chc = [
    realm(control, s, "off", "page")["switches"]["hardware_concurrency"]
    for s in (24601, 5150)
]
check("§5 hardwareConcurrency MOVES with the seed", len(set(hc)) == 2, str(hc))
check("§5 stock hardwareConcurrency is CONSTANT", len(set(chc)) == 1, str(chc))

check(
    "§5 navigator.webdriver is false on the product",
    realm(product, 24601, "off", "page")["switches"]["webdriver"] is False,
)
check(
    "§5 navigator.webdriver is TRUE on the stock control",
    realm(control, 24601, "off", "page")["switches"]["webdriver"] is True,
)

# The three dead switches: screen/dpr agree with the control, i.e. unspoofed.
for key in ("screen_width", "screen_height", "device_pixel_ratio"):
    p = realm(product, 24601, "off", "page")["switches"][key]
    c = realm(control, 24601, "off", "page")["switches"][key]
    check(f"§5 {key} is NOT spoofed (equals stock)", p == c, f"{p} vs {c}")

# Static half: which switches no patch outside 000 consumes.
patch_dir = HERE.parents[2] / "engine" / "patches" / "fingerprint"
bodies = {
    p.name: p.read_text(errors="replace")
    for p in patch_dir.glob("*.patch")
    if not p.name.startswith("000-")
}
for sw in (
    "kFingerprintScreenWidth",
    "kFingerprintScreenHeight",
    "kFingerprintDeviceScaleFactor",
    "kFingerprintLocation",
):
    consumers = [n for n, b in bodies.items() if sw in b]
    check(f"§5 {sw} has NO consumer outside patch 000", not consumers, str(consumers))
for sw, expect_some in (
    ("kFingerprintHardwareConcurrency", True),
    ("kFingerprintTimezone", True),
    ("kFingerprintPlatform", True),
):
    consumers = [n for n, b in bodies.items() if sw in b]
    check(f"§5 {sw} DOES have a consumer (contrast)", bool(consumers) == expect_some)

# --- §7 readback + client rects -------------------------------------------
for vec, key in (
    ("getImageData", "getimagedata_hash"),
    ("readPixels", None),
):
    for seed_pair in [(24601, 5150)]:
        a, b = seed_pair
        if key:
            va = realm(product, a, "off", "page")["canvas"][key]
            vb = realm(product, b, "off", "page")["canvas"][key]
            ca = realm(control, a, "off", "page")["canvas"][key]
        else:
            va = realm(product, a, "off", "page")["webgl"]["readpixels_hash"]
            vb = realm(product, b, "off", "page")["webgl"]["readpixels_hash"]
            ca = realm(control, a, "off", "page")["webgl"]["readpixels_hash"]
        check(f"§7 {vec} moves with the seed", va != vb, f"{va} vs {vb}")
        check(f"§7 {vec} differs from stock", va != ca, f"{va} vs stock {ca}")

for seed in (24601, 5150):
    pr = realm(product, seed, "off", "page")
    cr = realm(control, seed, "off", "page")
    check(
        f"§7 seed {seed}: clientRects x DIFFERS from stock (eligible element)",
        pr["client_rects"]["x"] != cr["client_rects"]["x"],
        f"{pr['client_rects']['x']} vs {cr['client_rects']['x']}",
    )
    check(
        f"§7 seed {seed}: clientRects WIDTH does NOT move (Offset, not Scale)",
        pr["client_rects"]["width"] == cr["client_rects"]["width"],
    )
    check(
        f"§7 seed {seed}: the EXEMPT shape is not offset (exemption honoured)",
        pr["client_rects_exempt"]["x"] == cr["client_rects_exempt"]["x"],
        f"{pr['client_rects_exempt']['x']} vs {cr['client_rects_exempt']['x']}",
    )

# --- §6 layer --------------------------------------------------------------
mods = cell(product, 24601, "on")["layer_installed"]
check(
    "§6 the masking layer installs 10 modules on the self-built engine",
    isinstance(mods, list) and len(mods) == 10,
    str(mods),
)

# --- coverage --------------------------------------------------------------
for payload, tag in ((product, "self-built"), (control, "stock")):
    for rec in payload["records"]:
        got = set((rec.get("reading") or {}).get("realms", {}))
        check(
            f"coverage {tag} seed {rec['seed']} layer {rec['masking_layer']}: 7/7 realms",
            got == set(REALMS),
            f"{len(got)}/7",
        )
        check(
            f"coverage {tag} seed {rec['seed']} layer {rec['masking_layer']}: no cell error",
            not rec.get("error"),
            str(rec.get("error")),
        )

# --- report --------------------------------------------------------------
print(f"{len(oks)} checks passed")
for f in fails:
    print(f"FAIL: {f}")
if fails:
    print(f"\n{len(fails)} FAILED")
    raise SystemExit(1)
print("all report claims re-derived from the committed artifacts")
