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


def test_spoofs_hardware_concurrency_and_device_memory(tmp_path):
    # fingerprint-chromium leaves hardwareConcurrency/deviceMemory at the host's
    # real values on a desktop profile (18 cores / 8 GB on a VM host), an obvious
    # tell under a consumer-Windows identity. The script must pin a plausible pair.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert "hardwareConcurrency" in js
    assert "deviceMemory" in js


def test_carries_hardware_into_workers(tmp_path):
    # navigator.hardwareConcurrency in a Web Worker otherwise reports the real
    # host cores (a VM host leaked 32 in a worker while the page reported 12) — a
    # worker/page mismatch. The spoof must be carried into workers.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert "applyHwPatch" in js
    assert "SELF.Worker" in js
    # SEED lives inside applyHwPatch so .toString() re-derives the same pair
    body = js.split("function applyHwPatch(G)", 1)[1].split("var SELF", 1)[0]
    assert "var SEED =" in body


def test_carries_screen_and_hardware_into_iframes(tmp_path):
    # creepjs reads a pristine screen (real 1920x1080) and real cores/ram from a
    # fresh about:blank iframe where the content script didn't inject; the
    # page/iframe mismatch is the tell. The spoof must be carried into same-realm
    # child frames on access.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert "contentWindow" in js
    assert "contentDocument" in js
    assert "HTMLIFrameElement" in js


def test_script_pins_device_pixel_ratio(tmp_path):
    # A spoofed screen with the host's real DPR leaking through makes scanners
    # report screen.width*dpr (e.g. 3840*1.5=5760) — a resolution no monitor
    # has. The script must pin devicePixelRatio and keep matchMedia consistent.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert "devicePixelRatio" in js
    assert "matchMedia" in js
    assert "dppx" in js


def test_matchmedia_device_dimensions_agree_with_screen(tmp_path):
    # CSS media features device-width/device-height read the physical screen,
    # which under --force-device-scale-factor is the real panel / scale factor
    # (e.g. screen.width 2560 spoofed but device-width resolves to 1706 at 1.5x)
    # — a screen.width != device-width mismatch a scanner flags. The matchMedia
    # wrapper must answer device-width/device-height consistently with the
    # spoofed W/H so both report the same value.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev")) + "/device.js"
    ).read_text()
    assert "device-width" in js
    assert "device-height" in js


def test_forced_resolution_wins_without_containment_gate(tmp_path):
    # A user-picked resolution must be honored outright. Gating it on the window
    # extent (needW) leaked the render scale (#167): under
    # --force-device-scale-factor the window's outerWidth reads in PHYSICAL px
    # (3840 on a 4K/150% panel), so a chosen 2560 failed FORCED[0] >= needW,
    # fell through to the seeded pick, and reported ~4K instead of 2560x1440.
    js = pathlib.Path(
        build_device_extension(1, str(tmp_path / "dev"), resolution=(2560, 1440))
        + "/device.js"
    ).read_text()
    assert "[2560, 1440]" in js
    assert "if (FORCED) {" in js
    # the containment comparison must no longer gate the forced branch
    assert "FORCED[0] >= needW" not in js
