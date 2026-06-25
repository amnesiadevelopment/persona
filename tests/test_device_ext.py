import json
import pathlib

from src.services.browser.device_ext import build_device_extension


def test_builds_unpacked_extension(tmp_path):
    d = build_device_extension(123, str(tmp_path / "dev"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "device.js").exists()


def test_manifest_main_world_document_start(tmp_path):
    d = build_device_extension(123, str(tmp_path / "dev"))
    man = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = man["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_seed_baked_into_script(tmp_path):
    js = pathlib.Path(
        build_device_extension(0xABCDEF, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert str(0xABCDEF) in js
    assert "__SEED__" not in js


def test_script_spoofs_screen_and_mediadevices(tmp_path):
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    # screen geometry
    assert "availHeight" in js and "colorDepth" in js
    # taskbar inset so availHeight != height (a VM tell when equal)
    assert "TASKBAR" in js
    # mediaDevices set with a camera (no-camera reads as VM/server)
    assert "enumerateDevices" in js
    assert "videoinput" in js
    # masked as native
    assert "[native code]" not in js  # we keep real toString via nativeWrap
    assert "nativeWrap" in js
