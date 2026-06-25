import json
import pathlib

from src.services.browser.device_presets import (
    is_mobile_os,
    pick_preset,
    presets_for,
    get_preset,
)
from src.services.browser.mobile_ext import build_mobile_extension


def test_is_mobile_os():
    assert is_mobile_os("android")
    assert is_mobile_os("ios")
    assert not is_mobile_os("windows")
    assert not is_mobile_os("macos")


def test_pick_preset_deterministic_and_in_family():
    a = pick_preset(12345, "android")
    b = pick_preset(12345, "android")
    assert a.key == b.key  # stable per seed
    assert a.os_type == "android"
    i = pick_preset(12345, "ios")
    assert i.os_type == "ios"


def test_presets_have_required_fields():
    for p in presets_for("android") + presets_for("ios"):
        assert "Mobile" in p.user_agent or "iPhone" in p.user_agent
        assert p.width > 0 and p.height > 0 and p.dpr >= 1
        assert p.device_memory > 0 and p.hardware_concurrency > 0


def test_get_preset_by_key():
    assert get_preset("pixel-7").label == "Pixel 7"
    assert get_preset("nope") is None


def test_mobile_ext_builds_with_touch_and_screen(tmp_path):
    d = build_mobile_extension(
        str(tmp_path / "m"), is_ios=False, platform="Android",
        model="Pixel 7", ua_full_version="148.0.0.0",
        css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    )
    p = pathlib.Path(d)
    man = json.loads((p / "manifest.json").read_text())
    assert man["content_scripts"][0]["world"] == "MAIN"
    js = (p / "mobile.js").read_text()
    assert "maxTouchPoints" in js
    assert "ontouchstart" in js
    assert "userAgentData" in js
    assert "412" in js and "915" in js  # screen baked in
    assert "__CSS_W__" not in js


def test_mobile_ext_ios_drops_uadata(tmp_path):
    d = build_mobile_extension(
        str(tmp_path / "i"), is_ios=True, platform="iPhone",
        model="iPhone", ua_full_version="",
        css_width=393, css_height=852, dpr=3.0,
        device_memory=4, hardware_concurrency=6, touch_points=5,
    )
    js = pathlib.Path(d + "/mobile.js").read_text()
    # iOS Safari exposes no userAgentData
    assert "IS_IOS" in js
    assert "undefined" in js
