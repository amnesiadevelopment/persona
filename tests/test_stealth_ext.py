import json
import pathlib

from src.services.browser.stealth_ext import build_stealth_extension


def test_creates_files(tmp_path):
    d = build_stealth_extension(str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "stealth.js").exists()


def test_main_world_document_start(tmp_path):
    d = build_stealth_extension(str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"


def test_patches_headless_signals(tmp_path):
    d = build_stealth_extension(str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "stealth.js").read_text()
    # the signals CreepJS flags as "like headless"
    assert "downlinkMax" in js
    assert "contentIndex" in js or "ContentIndex" in js


def test_does_not_fake_mobile_only_apis(tmp_path):
    # ContactsManager is mobile-only; faking it on desktop would be inconsistent
    d = build_stealth_extension(str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "stealth.js").read_text()
    assert "ContactsManager" not in js
