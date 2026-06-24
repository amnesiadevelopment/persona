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
