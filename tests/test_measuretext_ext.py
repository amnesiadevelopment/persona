import json
import pathlib

from src.services.browser.measuretext_ext import build_measuretext_extension


def test_builds_unpacked_extension(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "measuretext.js").exists()


def test_manifest_runs_in_main_world_at_document_start(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    man = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = man["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_script_hooks_measuretext_and_uses_dom_width(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    assert "measureText" in js
    # repairs via an un-noised DOM measurement
    assert "getBoundingClientRect" in js
    # only overrides the width, keeps other metrics
    assert "Proxy" in js
    # masks the override so it doesn't read as patched
    assert "[native code]" in js


def test_detects_noise_regardless_of_sign(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The engine's noise factor can be positive OR negative depending on the
    # seed, so a near-zero POSITIVE width is corrupt too — detection keys on
    # magnitude, not sign, and skips empty strings (which measure zero).
    assert "Math.abs(m.width)" in js
    assert "String(text).length" in js
