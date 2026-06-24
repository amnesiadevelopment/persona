import json
import pathlib

from src.services.browser.title_ext import build_title_extension


def test_creates_manifest_and_script(tmp_path):
    ext_dir = build_title_extension("test8", str(tmp_path / "ext"))
    manifest = pathlib.Path(ext_dir) / "manifest.json"
    script = pathlib.Path(ext_dir) / "title.js"
    assert manifest.exists()
    assert script.exists()
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3
    assert m["content_scripts"][0]["run_at"] == "document_start"


def test_script_embeds_profile_name(tmp_path):
    ext_dir = build_title_extension("acc-42", str(tmp_path / "ext"))
    js = (pathlib.Path(ext_dir) / "title.js").read_text(encoding="utf-8")
    assert "acc-42" in js


def test_profile_name_is_json_escaped(tmp_path):
    # a quote in the name must not break the generated JS
    ext_dir = build_title_extension('a"b', str(tmp_path / "ext"))
    js = (pathlib.Path(ext_dir) / "title.js").read_text(encoding="utf-8")
    assert '\\"' in js or json.dumps('a"b') in js


def test_idempotent_path(tmp_path):
    base = str(tmp_path / "ext")
    p1 = build_title_extension("x", base)
    p2 = build_title_extension("x", base)
    assert p1 == p2
