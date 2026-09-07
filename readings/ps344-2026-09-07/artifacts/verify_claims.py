#!/usr/bin/env python3
"""PS-344 — re-check the REPORT's load-bearing numbers against the artifacts.

A report is only evidence if its figures can be re-derived from the records it
ships. This re-reads the committed JSON and re-computes every claim REPORT.md
rests on, and exits non-zero on a miss — so a later reader can tell "the report
still describes these artifacts" from "the artifacts moved underneath it".

⚠️ THIS IS A CONSISTENCY CHECK, NOT THE VERDICT. It answers "does the report
describe these files?" — it does NOT decide whether the patches are live. That
is ``scripts/ps344_verdict.py``, which has a direction and a demonstrated red
arm. Passing here while the verdict script goes red would mean the report
accurately describes an unpatched engine, which is a coherent state and one
this file must not paper over.

⛔ EVERY NUMBER ASSERTED HERE WAS MEASURED IN THIS RUN, ON THE DOWNLOADED
ARTIFACT. Nothing is carried across from PS-301: those are readings of a
different binary (144.0.7559.132) and the ticket forbids importing them.

Run from the repo root::

    python3 readings/ps344-2026-09-07/artifacts/verify_claims.py
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


product = json.loads((HERE / "readings-published-152.json").read_text())
control = json.loads((HERE / "readings-stock-cft-152.json").read_text())
falsify = json.loads((HERE / "readings-stock-as-product.json").read_text())

REALMS = (
    "page",
    "iframe_same_origin",
    "iframe_about_blank",
    "iframe_srcdoc",
    "worker_blob",
    "worker_in_iframe",
    "worker_nested",
)
DOM_REALMS = REALMS[:4]


def cell(payload, seed, layer):
    for r in payload["records"]:
        if r["seed"] == seed and r["masking_layer"] == layer:
            return r
    raise KeyError((seed, layer))


def realm(payload, seed, layer, name):
    return cell(payload, seed, layer)["reading"]["realms"][name]


# --- provenance: THE ARTIFACT IS THE PUBLISHED ONE -------------------------
#
# The whole ticket rests on this: a reading of a locally-built binary would be
# PS-301 again. The digest below is the one the GitHub release API publishes and
# the one `src/services/engine/updater.py` verifies a download against.
check(
    "product sha256 is the PUBLISHED release asset's digest",
    product["binary"]["sha256"]
    == "6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3",
    product["binary"]["sha256"],
)
check(
    "product size matches the published asset",
    product["binary"]["size_bytes"] == 202193400,
    str(product["binary"]["size_bytes"]),
)
check(
    "product real path is the downloaded AppImage (not a local build)",
    product["binary"]["real_path"].endswith(
        "personium-152.0.7977.75-linux-x86_64.AppImage"
    ),
    product["binary"]["real_path"],
)
check(
    "product resolved through the UNMODIFIED resolver name (fpchrome.AppImage)",
    product["binary"]["resolved_path"].endswith("/fpchrome.AppImage"),
    product["binary"]["resolved_path"],
)
check(
    "product binary reports Chromium 152.0.7977.75",
    product["binary"]["version_string"] == "Chromium 152.0.7977.75",
    product["binary"]["version_string"],
)
check(
    "control is stock Chrome for Testing at the SAME version",
    control["binary"]["version_string"] == "Google Chrome for Testing 152.0.7977.75",
    control["binary"]["version_string"],
)
check(
    "control sha256 is the CfT chrome the report names",
    control["binary"]["sha256"]
    == "3c84cfdbadd0b5b9d1943568b67faea54cf818d20ca32a337fd8a68a0d61ac76",
    control["binary"]["sha256"],
)
check(
    "the two arms are DIFFERENT binaries",
    product["binary"]["sha256"] != control["binary"]["sha256"],
)
check(
    "the falsification arm is BYTE-IDENTICAL to the control (that is its design)",
    falsify["binary"]["sha256"] == control["binary"]["sha256"],
    falsify["binary"]["sha256"],
)

# --- completeness: no arm silently lost a cell or a realm ------------------
for name, payload in (
    ("product", product),
    ("control", control),
    ("falsification", falsify),
):
    check(f"{name}: 4 cells (2 seeds x 2 layer states)", len(payload["records"]) == 4)
    for rec in payload["records"]:
        tag = f"{name} seed{rec['seed']}/layer{rec['masking_layer']}"
        check(f"{tag}: no cell error", not rec.get("error"), str(rec.get("error")))
        got = set((rec.get("reading") or {}).get("realms") or {})
        check(f"{tag}: all 7 realms present", got == set(REALMS), str(sorted(got)))

# --- Q2 GPU: the headline, and it is a per-realm claim ----------------------
for seed, expected in ((24601, "RTX 4070"), (5150, "RTX 3080 Laptop GPU")):
    renderers = {realm(product, seed, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS}
    check(
        f"seed {seed}: ONE spoofed renderer across all 7 realms",
        len(renderers) == 1,
        str(sorted(renderers)),
    )
    check(
        f"seed {seed}: renderer names {expected}",
        expected in next(iter(renderers)),
        next(iter(renderers)),
    )
    vendors = {realm(product, seed, "off", r)["webgl"]["unmasked_vendor"] for r in REALMS}
    check(
        f"seed {seed}: vendor is spoofed NVIDIA in all 7 realms",
        vendors == {"Google Inc. (NVIDIA Corporation)"},
        str(sorted(vendors)),
    )

stock_renderers = {realm(control, 24601, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS}
check(
    "control reports the host's real SwiftShader in every realm",
    len(stock_renderers) == 1 and "SwiftShader" in next(iter(stock_renderers)),
    str(sorted(stock_renderers)),
)
r1 = next(iter({realm(product, 24601, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS}))
r2 = next(iter({realm(product, 5150, "off", r)["webgl"]["unmasked_renderer"] for r in REALMS}))
check("GPU is SEED-DERIVED, not a constant fake", r1 != r2, f"{r1!r} vs {r2!r}")

# --- Q1 switches -----------------------------------------------------------
hc = {
    seed: {realm(product, seed, "off", r)["switches"]["hardware_concurrency"] for r in REALMS}
    for seed in (24601, 5150)
}
check("seed 24601: hardwareConcurrency is 18 in all 7 realms", hc[24601] == {18}, str(hc[24601]))
check("seed 5150: hardwareConcurrency is 12 in all 7 realms", hc[5150] == {12}, str(hc[5150]))
stock_hc = {realm(control, 24601, "off", r)["switches"]["hardware_concurrency"] for r in REALMS}
check("control reports the host's real 8 cores", stock_hc == {8}, str(stock_hc))

tz = {realm(product, 24601, "off", r)["switches"]["timezone"] for r in REALMS}
stock_tz = {realm(control, 24601, "off", r)["switches"]["timezone"] for r in REALMS}
check("patch 018: --timezone reaches all 7 realms as America/Chicago", tz == {"America/Chicago"}, str(tz))
check("control falls back to the host clock (UTC)", stock_tz == {"UTC"}, str(stock_tz))

check(
    "patch 009: webdriver is false in the product's DOM realms",
    all(realm(product, 24601, "off", r)["switches"]["webdriver"] is False for r in DOM_REALMS),
)
check(
    "control reports webdriver TRUE under CDP (that is the contrast)",
    all(realm(control, 24601, "off", r)["switches"]["webdriver"] is True for r in DOM_REALMS),
)

# The user agent is NOT spoofed under --fingerprint-platform=linux on a Linux
# host, and asserting that explicitly stops a later reader reading its absence
# from the differing-vectors list as a missing measurement.
check(
    "user agent matches stock (declared platform == host platform here)",
    realm(product, 24601, "off", "page")["switches"]["user_agent"]
    == realm(control, 24601, "off", "page")["switches"]["user_agent"],
)

# --- Q3 canvas / rects ------------------------------------------------------
for seed in (24601, 5150):
    gid = {realm(product, seed, "off", r)["canvas"]["getimagedata_hash"] for r in REALMS}
    check(f"seed {seed}: getImageData digest is one value across all 7 realms", len(gid) == 1, str(gid))
gid1 = realm(product, 24601, "off", "page")["canvas"]["getimagedata_hash"]
gid2 = realm(product, 5150, "off", "page")["canvas"]["getimagedata_hash"]
sgid = realm(control, 24601, "off", "page")["canvas"]["getimagedata_hash"]
check("patch 012: getImageData is seed-derived", gid1 != gid2, f"{gid1} vs {gid2}")
check("patch 012: and differs from stock", gid1 != sgid, f"{gid1} vs stock {sgid}")

rp1 = realm(product, 24601, "off", "page")["webgl"]["readpixels_hash"]
rp2 = realm(product, 5150, "off", "page")["webgl"]["readpixels_hash"]
srp = realm(control, 24601, "off", "page")["webgl"]["readpixels_hash"]
check("patch 016: readPixels is seed-derived", rp1 != rp2, f"{rp1} vs {rp2}")
check("patch 016: and differs from stock", rp1 != srp, f"{rp1} vs stock {srp}")

# patch 014 — the movement AND the two things that must NOT move.
rx1 = realm(product, 24601, "off", "page")["client_rects"]["x"]
rx2 = realm(product, 5150, "off", "page")["client_rects"]["x"]
srx = realm(control, 24601, "off", "page")["client_rects"]["x"]
check("patch 014: an eligible rect's x is offset per seed", rx1 != rx2, f"{rx1} vs {rx2}")
check("patch 014: and differs from stock", rx1 != srx, f"{rx1} vs stock {srx}")
check(
    "NEGATIVE CONTROL: rect WIDTH does not move (Offset, not Scale)",
    all(
        realm(product, s, "off", r)["client_rects"]["width"]
        == realm(control, s, "off", r)["client_rects"]["width"]
        for s in (24601, 5150)
        for r in DOM_REALMS
    ),
)
check(
    "NEGATIVE CONTROL: the EXEMPT shape's x does not move",
    all(
        realm(product, s, "off", r)["client_rects_exempt"]["x"]
        == realm(control, s, "off", r)["client_rects_exempt"]["x"]
        for s in (24601, 5150)
        for r in DOM_REALMS
    ),
)

# --- the patch-015 DEFECT, reproduced in the SHIPPED binary ------------------
mt24 = realm(product, 24601, "off", "page")["canvas"]["measuretext_width"]
mt51 = realm(product, 5150, "off", "page")["canvas"]["measuretext_width"]
smt = realm(control, 24601, "off", "page")["canvas"]["measuretext_width"]
check("DEFECT: measureText width is NEGATIVE at seed 24601", mt24 < 0, repr(mt24))
check("DEFECT: measureText width is NEGATIVE at seed 5150", mt51 < 0, repr(mt51))
check("control's measureText is the sane positive width", smt > 0, repr(smt))
check(
    "the defect is DOM-only: the worker realm reads the stock width",
    realm(product, 24601, "off", "worker_blob")["canvas"]["measuretext_width"] == smt,
    repr(realm(product, 24601, "off", "worker_blob")["canvas"]["measuretext_width"]),
)

# --- patch 003 audio: the PARTIAL result, asserted rather than omitted -------
#
# Recorded as a check so it cannot quietly change: at seed 5150 the product's
# audio digest differs from stock, and at seed 24601 it is BIT-IDENTICAL. That
# asymmetry is a finding, and a report that only mentioned the differing seed
# would be selecting its evidence.
a24p = cell(product, 24601, "off")["reading"]["audio_page"]["sum"]
a24c = cell(control, 24601, "off")["reading"]["audio_page"]["sum"]
a51p = cell(product, 5150, "off")["reading"]["audio_page"]["sum"]
check("patch 003: seed 24601 audio is IDENTICAL to stock", a24p == a24c, f"{a24p} vs {a24c}")
check("patch 003: seed 5150 audio DIFFERS from stock", a51p != a24c, f"{a51p} vs {a24c}")

# --- the masking layer still installs on this build --------------------------
EXPECTED_LAYER = [
    "audio", "canvas_ctx", "device", "gpu", "locale",
    "measuretext", "native", "stealth", "voice", "webgl",
]
check(
    "Q4: all 10 masking-layer modules install on the published engine",
    cell(product, 24601, "on")["layer_installed"] == EXPECTED_LAYER,
    str(cell(product, 24601, "on")["layer_installed"]),
)
check(
    "layer-OFF cells really ran with no layer (the attribution rests on this)",
    all(cell(product, s, "off")["layer_installed"] == [] for s in (24601, 5150)),
)

# --- the command line actually presented -----------------------------------
argv = cell(product, 24601, "off")["argv"]
check("--appimage-extract-and-run leads the command line", argv[1] == "--appimage-extract-and-run", str(argv[:2]))
check("--fingerprint=24601 was presented", "--fingerprint=24601" in argv, "")
check("--fingerprint-platform=linux was presented", "--fingerprint-platform=linux" in argv, "")
check("no chromium was taken from PATH", argv[0].endswith("/fpchrome.AppImage"), argv[0])

# --- the FALSIFICATION arm is genuinely the stock browser -------------------
check(
    "falsification arm reads stock's real SwiftShader (it is not the product)",
    "SwiftShader" in realm(falsify, 24601, "off", "page")["webgl"]["unmasked_renderer"],
    realm(falsify, 24601, "off", "page")["webgl"]["unmasked_renderer"],
)

# ---------------------------------------------------------------------------
print(f"{len(oks)} checks passed")
for f in fails:
    print(f"FAIL: {f}")
if fails:
    print(f"\n{len(fails)} CHECK(S) FAILED — the report no longer describes these artifacts.")
    sys.exit(1)
print("all load-bearing figures in REPORT.md re-derived from the committed records")
sys.exit(0)
