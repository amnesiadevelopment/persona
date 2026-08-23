import json
import pathlib
import re

from src.services.browser.device_presets import (
    is_mobile_os,
    pick_preset,
    presets_for,
    get_preset,
)
from src.services.browser.engine_version import parse as V
from src.services.browser.mobile_ext import build_mobile_extension


def test_is_mobile_os():
    assert is_mobile_os("android")
    assert is_mobile_os("ios")
    assert not is_mobile_os("windows")
    assert not is_mobile_os("macos")


def test_pick_preset_deterministic_and_in_family():
    a = pick_preset(12345, "android", 0)
    b = pick_preset(12345, "android", 0)
    assert a.key == b.key  # stable per seed
    assert a.os_type == "android"
    i = pick_preset(12345, "ios", 0)
    assert i.os_type == "ios"


def test_presets_have_required_fields():
    for p in presets_for("android", 0) + presets_for("ios", 0):
        ua = p.user_agent_for(V("149.0.8000.10"))
        assert "Mobile" in ua or "iPhone" in ua
        # no template slot survives into a launched UA
        assert "{chrome}" not in ua
        assert p.width > 0 and p.height > 0 and p.dpr >= 1
        assert p.device_memory > 0 and p.hardware_concurrency > 0


def test_get_preset_by_key():
    assert get_preset("pixel-7").label == "Pixel 7"
    assert get_preset("nope") is None


def test_mobile_ext_builds_with_touch_and_screen(tmp_path):
    d = build_mobile_extension(
        str(tmp_path / "m"), is_ios=False, platform="Android",
        model="Pixel 7", chromium_version=V("149.0.8000.10"),
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
        model="iPhone", chromium_version=None,
        css_width=393, css_height=852, dpr=3.0,
        device_memory=4, hardware_concurrency=6, touch_points=5,
    )
    js = pathlib.Path(d + "/mobile.js").read_text()
    # iOS Safari exposes no userAgentData
    assert "IS_IOS" in js
    assert "undefined" in js


def test_ios_color_depth_is_32_android_is_24(tmp_path):
    # A real iPhone reports screen.colorDepth/pixelDepth = 32; Android = 24.
    # A hardcoded 24 on iOS is a device-mismatch tell that CreepJS-class scanners
    # use to flag the profile as fake and cluster all persona iOS profiles.
    ios = pathlib.Path(build_mobile_extension(
        str(tmp_path / "ios"), is_ios=True, platform="iPhone", model="iPhone",
        chromium_version=None, css_width=393, css_height=852, dpr=3.0,
        device_memory=4, hardware_concurrency=6, touch_points=5,
    ) + "/mobile.js").read_text()
    android = pathlib.Path(build_mobile_extension(
        str(tmp_path / "and"), is_ios=False, platform="Android", model="Pixel 7",
        chromium_version=V("149.0.8000.10"), css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    ) + "/mobile.js").read_text()
    # the depth is chosen from IS_IOS at runtime, so both 32 and 24 appear in the
    # built script and the choice keys on IS_IOS
    assert "colorDepth" in ios
    assert "IS_IOS ? 32 : 24" in ios
    assert "IS_IOS ? 32 : 24" in android


def test_mobile_on_shared_recursive_registry(tmp_path):
    # #1 (audit3): mobile_ext was the only _ext not on the shared registry, so a
    # nested worker/iframe reported the desktop-backed engine's platform / cores /
    # pointer:fine while the page claimed a phone → instant "mobile emulation".
    # It must ride the recursive registry like every other _ext.
    from src.services.browser.mobile_ext import build_mobile_extension

    d = build_mobile_extension(
        str(tmp_path / "m"), is_ios=False, platform="Android",
        model="Pixel 8", chromium_version=V("149.0.8000.10"),
        css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    )
    js = (pathlib.Path(d) / "mobile.js").read_text()
    assert "applyMobilePatch" in js
    assert "__pnaInstall(SELF, applyMobilePatch)" in js
    assert "G.Worker" in js and "HTMLIFrameElement" in js
    # worker-safe: Window-only bits gated so the leaf doesn't throw in a worker
    assert "if (G.screen)" in js
    assert "if (G.matchMedia)" in js
    # params live inside the leaf so .toString() carries them per realm
    body = js.split("function applyMobilePatch(G)", 1)[1].split("__pnaInstall", 1)[0]
    assert "var IS_IOS" in body and "var HWC" in body


def test_android_navigator_platform_is_upstream_frozen_armv81(tmp_path):
    # DRIFT DETECTOR, NOT A CORRECTNESS PROOF. If this fails, suspect UPSTREAM
    # CHANGE before suspecting a local regression.
    #
    # Android navigator.platform must be 'Linux armv81' — ending in a DIGIT ONE.
    # It looks like a typo for the ARM kernel's 'armv8l' (letter, an endianness
    # suffix) and it is not one: NavigatorID::platform()'s uname path is virtual
    # and merely the fallback, while NavigatorBase::platform() overrides it and
    # on Android returns GetReducedNavigatorPlatform()'s frozen "Linux armv81"
    # whenever ReduceUserAgentMinorVersion is on — status:"stable" since M101,
    # i.e. every shipping Android Chrome. So the value is correct because Google
    # FROZE it, not because a kernel produces it. That makes it contingent on an
    # upstream decision: if Google ever unfreezes the reduced platform string,
    # this assertion is what surfaces it, and the fix is to re-derive the value
    # from upstream — NOT to "correct" the digit. Emitting the kernel-plausible
    # 'armv8l' would make persona the only Android browser on the internet
    # reporting it: a unique, regex-detectable tell. (Refuted on cancelled PS-98,
    # which would have asserted the exact opposite of this test.)
    #
    # Read out of the BUILT artifact, and parsed as a STATEMENT rather than
    # grepped: the explanatory comment at the literal necessarily spells out
    # 'armv8l' too, so a substring search over mobile.js would match the prose
    # and pass even if the shipped value had flipped. Capturing the emitted
    # def(nav,'platform',...) call also means a refactor that hoists the value
    # into a renamed constant fails here rather than silently escaping coverage.
    d = build_mobile_extension(
        str(tmp_path / "m"), is_ios=False, platform="Android",
        model="Pixel 8", chromium_version=V("149.0.8000.10"),
        css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    )
    js = (pathlib.Path(d) / "mobile.js").read_text()

    m = re.search(
        r"def\(\s*nav\s*,\s*'platform'\s*,\s*IS_IOS\s*\?\s*'([^']*)'\s*:\s*'([^']*)'\s*\)",
        js,
    )
    assert m is not None, (
        "could not find the emitted navigator.platform assignment in mobile.js; "
        "if it was refactored (e.g. the value hoisted into a constant), update "
        "this parse — do not delete the assertion, it is the only thing pinning "
        "the platform string to upstream"
    )
    android_platform = m.group(2)

    assert android_platform == "Linux armv81", (
        f"Android navigator.platform is {android_platform!r}, expected "
        "'Linux armv81' (digit one). If someone 'fixed' this to 'armv8l', "
        "revert it: see the provenance comment at the literal and PS-98."
    )


def test_touch_constructors_gated_behind_window(tmp_path):
    # audit7 #6: the leaf runs in Web Workers via the shared registry. A real
    # Android Chrome worker has NO TouchEvent/Touch on its global (they're
    # Window-only), so defining them unconditionally made `typeof TouchEvent ===
    # 'function'` true inside a worker — a net-new mobile-emulation tell. The
    # TouchEvent/Touch assignments must sit inside an `if (G.Window)` gate.
    d = build_mobile_extension(
        str(tmp_path / "m"), is_ios=False, platform="Android",
        model="Pixel 8", chromium_version=V("149.0.8000.10"),
        css_width=412, css_height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8, touch_points=5,
    )
    js = (pathlib.Path(d) / "mobile.js").read_text()
    # Both TouchEvent and Touch constructor assignments must live inside a
    # `if (G.Window) { ... }` block. Find the G.Window gate that immediately
    # precedes the TouchEvent assignment (there are several G.Window checks) and
    # walk its braces: the assignment must fall between the guard's `{` and `}`.
    te = js.index("G.TouchEvent = function")
    guard = js.rindex("if (G.Window)", 0, te)
    open_brace = js.index("{", guard)
    depth, close_brace = 0, None
    for i in range(open_brace, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                close_brace = i
                break
    assert close_brace is not None
    block = js[open_brace:close_brace]
    assert "G.TouchEvent = function" in block, "TouchEvent must be defined inside the G.Window gate"
    assert "G.Touch = function" in block, "Touch must be defined inside the G.Window gate"
