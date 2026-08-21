"""Real mobile device presets used for mobile profiles.

The engine has no Android/iOS support, so a mobile profile is assembled at the
persona layer from one of these presets: user-agent, screen geometry, device
pixel ratio, deviceMemory, hardwareConcurrency and the touch/Client-Hints shape
all come from a real device. A profile picks one preset deterministically from
its fingerprint seed and the chosen OS family.

A preset carries the DEVICE facts — model, geometry, memory, core count — and
deliberately NOT the Chromium version. That one comes from the engine that is
actually installed (see ``engine_version.py``): it used to be duplicated into
every Android user agent here as ``Chrome/148.0.0.0``, which meant a routine
engine bump left these strings claiming a version the engine underneath no
longer was. The Android user agents below are TEMPLATES with a ``{chrome}``
slot, filled at launch. iOS presets have no slot — Safari advertises no Chromium
version at all — so ``user_agent_for`` is an identity for them.
"""

from dataclasses import dataclass

from .engine_version import ChromiumVersion


@dataclass(frozen=True)
class DevicePreset:
    key: str
    os_type: str          # "android" | "ios"
    label: str
    # A '{chrome}' slot on Android (filled with the installed engine's REDUCED
    # version, e.g. '149.0.0.0'); a literal string on iOS.
    user_agent_template: str
    # CSS pixels (layout viewport) and device pixel ratio
    width: int
    height: int
    dpr: float
    device_memory: int
    hardware_concurrency: int
    # Client Hints
    platform: str         # navigator.userAgentData platform, e.g. "Android"
    model: str            # Sec-CH-UA model, e.g. "Pixel 7"

    def user_agent_for(self, version: "ChromiumVersion | None") -> str:
        """This device's user agent as launched, on the installed engine.

        Uses the REDUCED form (``149.0.0.0``): Chrome froze the UA's
        minor/build/patch, so a real device never shows its true build here.
        The true build travels in ``uaFullVersion``/``fullVersionList`` instead.

        ``version`` may be None ONLY for a template with no ``{chrome}`` slot —
        i.e. iOS, whose Safari UA carries no Chromium version at all. An Android
        template with no version is a programming error and raises, rather than
        emitting a placeholder UA into a live launch.
        """
        if version is None:
            if "{chrome}" in self.user_agent_template:
                raise ValueError(
                    f"device preset {self.key!r} advertises a Chromium version "
                    f"and cannot build a user agent without one"
                )
            return self.user_agent_template
        return self.user_agent_template.format(chrome=version.reduced)


# A small set of common, current real devices. Physical pixel resolution =
# width*dpr x height*dpr; the CSS viewport is width x height.
ANDROID_PRESETS = [
    DevicePreset(
        key="pixel-7", os_type="android", label="Pixel 7",
        user_agent_template=(
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
        ),
        width=412, height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8,
        platform="Android", model="Pixel 7",
    ),
    DevicePreset(
        key="galaxy-s23", os_type="android", label="Samsung Galaxy S23",
        user_agent_template=(
            "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
        ),
        width=360, height=780, dpr=3.0,
        device_memory=8, hardware_concurrency=8,
        platform="Android", model="SM-S911B",
    ),
    DevicePreset(
        key="xiaomi-13", os_type="android", label="Xiaomi 13",
        user_agent_template=(
            "Mozilla/5.0 (Linux; Android 14; 2211133G) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
        ),
        width=393, height=873, dpr=2.75,
        device_memory=12, hardware_concurrency=8,
        platform="Android", model="2211133G",
    ),
]

# iOS Safari has no Client Hints (Apple doesn't ship UA-CH); userAgentData is
# undefined on real iOS, which the mobile extension must reproduce.
#
# VERSION FLOOR — read before adding a preset claiming an OLDER iOS.
# gpu_ext.py's iOS WebGL extension set (IOS_GL1_EXTS / IOS_GL2_EXTS) is only
# valid for iOS >= 17. Two floors stack:
#   - s3tc/bptc/rgtc (BC compression) requires iOS >= 16.4
#     (DisplayMtl::supportsBCTextureCompression is annotated for 16.4).
#   - the 2023-08-08 WebKit batch — EXT_clip_control, WEBGL_polygon_mode,
#     EXT_conservative_depth, EXT_render_snorm, EXT_depth_clamp,
#     WEBGL_render_shared_exponent, WEBGL_stencil_texturing — ships no earlier
#     than iOS 17.
# ALIASED_POINT_SIZE_RANGE in COMMON_IOS is likewise [1,511] for iOS >= 15.0
# and was [1,64] below that.
# Every preset here claims iOS 17.5, which clears all three. A preset claiming
# an older iOS would present an extension set that device could not report —
# an internally impossible profile. Update gpu_ext.py alongside it.
IOS_PRESETS = [
    DevicePreset(
        key="iphone-15", os_type="ios", label="iPhone 15",
        user_agent_template=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        width=393, height=852, dpr=3.0,
        device_memory=4, hardware_concurrency=6,
        platform="iPhone", model="iPhone",
    ),
    DevicePreset(
        key="iphone-14", os_type="ios", label="iPhone 14",
        user_agent_template=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        width=390, height=844, dpr=3.0,
        device_memory=4, hardware_concurrency=6,
        platform="iPhone", model="iPhone",
    ),
]

_ALL = {p.key: p for p in ANDROID_PRESETS + IOS_PRESETS}


def presets_for(os_type: str) -> list[DevicePreset]:
    if os_type == "ios":
        return IOS_PRESETS
    return ANDROID_PRESETS


def pick_preset(seed: int, os_type: str) -> DevicePreset:
    """Deterministically choose a device preset for the OS family from the
    profile seed, so a profile always presents the same device."""
    pool = presets_for(os_type)
    return pool[(int(seed) & 0xFFFFFFFF) % len(pool)]


def get_preset(key: str) -> DevicePreset | None:
    return _ALL.get(key)


def is_mobile_os(os_type: str) -> bool:
    return os_type in ("android", "ios")
