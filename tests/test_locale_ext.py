import json
import pathlib

from src.services.browser.locale_ext import build_locale_extension


def test_creates_manifest_and_js(tmp_path):
    d = build_locale_extension("en-CA", str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "locale.js").exists()


def test_manifest_injects_main_world_at_start(tmp_path):
    d = build_locale_extension("de-DE", str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert "<all_urls>" in cs["matches"]


def test_js_embeds_locale(tmp_path):
    d = build_locale_extension("fr-FR", str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "locale.js").read_text()
    assert '"fr-FR"' in js
    assert "Intl.DateTimeFormat" in js
    assert "Intl.NumberFormat" in js
