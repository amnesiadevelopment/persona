import json
import pathlib

from src.services.browser.geo_ext import build_geo_extension


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


def test_idempotent_path(tmp_path):
    base = str(tmp_path / "geo")
    assert build_geo_extension(1.0, 2.0, base) == build_geo_extension(1.0, 2.0, base)


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
