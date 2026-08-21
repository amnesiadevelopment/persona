import json
import pathlib

from src.services.browser.geo_ext import build_geo_extension
from tests.native_mask_probe import GEO_STUBS, assert_reads_native


def test_creates_manifest_and_script(tmp_path):
    ext = build_geo_extension(45.5, -73.6, str(tmp_path / "geo"))
    assert (pathlib.Path(ext) / "manifest.json").exists()
    assert (pathlib.Path(ext) / "geo.js").exists()
    m = json.loads((pathlib.Path(ext) / "manifest.json").read_text())
    assert m["manifest_version"] == 3
    cs = m["content_scripts"][0]
    assert cs["run_at"] == "document_start"
    assert cs["world"] == "MAIN"


def test_script_embeds_coordinates(tmp_path):
    ext = build_geo_extension(45.5017, -73.5673, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text()
    assert "45.5017" in js
    assert "-73.5673" in js


def test_overrides_getcurrentposition(tmp_path):
    ext = build_geo_extension(1.0, 2.0, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text()
    assert "getCurrentPosition" in js
    assert "watchPosition" in js


def test_overrides_are_native_masked(tmp_path):
    # THE INVARIANT: each geolocation override must stringify as native under
    # Function.prototype.toString.call(fn) — the form a detector reading
    # geo.getCurrentPosition's source would use.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the override installed and whether or not the patch honours it, and
    # would fail on a marker-free implementation that is strictly better.
    # assert_reads_native also runs the counterfactual: without native_ext's
    # patch the same probe must NOT read native.
    ext = build_geo_extension(1.0, 2.0, str(tmp_path / "geo"))
    assert_reads_native(
        tmp_path,
        [pathlib.Path(ext) / "geo.js"],
        GEO_STUBS,
        "Function.prototype.toString.call(navigator.geolocation.getCurrentPosition)",
        "getCurrentPosition",
    )


def test_geo_on_shared_recursive_registry(tmp_path):
    # #3: geolocation is Window-only, so the leak vector is a fresh (nested)
    # about:blank/srcdoc iframe with a pristine navigator.geolocation. Route the
    # patch through the shared recursive registry so every child frame is covered.
    ext = build_geo_extension(1.0, 2.0, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text()
    assert "applyGeoPatch" in js
    assert "__pnaInstall(SELF, applyGeoPatch)" in js
    assert "HTMLIFrameElement" in js
    # LAT/LON live inside the leaf so .toString() carries them per realm
    body = js.split("function applyGeoPatch(G)", 1)[1].split("__pnaInstall", 1)[0]
    assert "var LAT =" in body


def test_idempotent_path(tmp_path):
    base = str(tmp_path / "geo")
    assert build_geo_extension(1.0, 2.0, base) == build_geo_extension(1.0, 2.0, base)


def test_deny_mode_when_coords_none(tmp_path):
    # audit7 #5: a proxy with a country/timezone but null coords must NOT let
    # getCurrentPosition fall through to the real host location. build_geo_extension
    # with lat/lon None runs in DENY mode — LAT/LON serialize to null and the
    # override returns PERMISSION_DENIED (code 1).
    ext = build_geo_extension(None, None, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text()
    assert "var LAT = null" in js and "var LON = null" in js
    assert "DENY" in js
    assert "PERMISSION_DENIED: 1" in js
    assert "code: 1" in js
    # the error callback is invoked in deny mode
    assert "error(denied())" in js


def test_coords_mode_does_not_deny(tmp_path):
    # with real coords, DENY resolves false and success returns the pinned pos.
    ext = build_geo_extension(52.52, 13.405, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text()
    assert "52.52" in js and "13.405" in js
    assert "var LAT = null" not in js


def test_script_is_iife_no_globals(tmp_path):
    # #233: this content script runs in the MAIN world. A bare top-level
    # const/function becomes a page global, and when the page's own bundle later
    # declares the same identifier the page throws "Identifier '…' has already
    # been declared" and its script dies — Google Sheets' calc worker did, so the
    # sheet stuck on "Working" and the calendar never opened (only with a proxy,
    # since geo is proxy-only). Everything must be wrapped in an IIFE so no name
    # leaks to the page.
    ext = build_geo_extension(1.0, 2.0, str(tmp_path / "geo"))
    js = (pathlib.Path(ext) / "geo.js").read_text().strip()
    assert js.startswith("(function"), f"geo.js must start with an IIFE, got: {js[:30]!r}"
    assert js.endswith(("})();", "})()")), f"geo.js must end by invoking the IIFE, got: {js[-10:]!r}"
