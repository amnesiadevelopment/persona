import json
import pathlib

from src.services.browser.audio_ext import build_audio_extension


def test_creates_files(tmp_path):
    d = build_audio_extension(12345, str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "audio.js").exists()


def test_main_world_document_start(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"


def test_seed_embedded(tmp_path):
    d = build_audio_extension(987654, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    assert "987654" in js


def test_patches_audio_readback_paths(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    # the float-buffer readers fingerprinters use
    assert "getChannelData" in js
    assert "getFloatFrequencyData" in js
    assert "getByteFrequencyData" in js


def test_different_seeds_differ(tmp_path):
    a = (pathlib.Path(build_audio_extension(111, str(tmp_path / "a"))) / "audio.js").read_text()
    b = (pathlib.Path(build_audio_extension(222, str(tmp_path / "b"))) / "audio.js").read_text()
    assert a != b


def test_native_tostring_masking(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    # wrappers must report as native to survive tamper probes
    assert "toString" in js
