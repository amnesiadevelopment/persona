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


def test_js_is_iife_no_globals(tmp_path):
    # #233: in the MAIN world a bare top-level const (LOCALE, _resolved) becomes a
    # page global and collides with the page's own bundle ("Identifier '…' has
    # already been declared"), killing the script — Google Sheets' calc worker
    # died and the sheet stuck on "Working". Wrap everything in an IIFE.
    d = build_locale_extension("fr-FR", str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "locale.js").read_text().strip()
    assert js.startswith("(function"), f"locale.js must start with an IIFE, got: {js[:30]!r}"
    assert js.endswith(("})();", "})()")), f"locale.js must end by invoking the IIFE, got: {js[-10:]!r}"
