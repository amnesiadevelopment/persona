"""Real mobile device presets used for mobile profiles.

The engine has no Android/iOS support, so a mobile profile is assembled at the
persona layer from one of these presets: user-agent, screen geometry, device
pixel ratio, deviceMemory, hardwareConcurrency and the touch/Client-Hints shape
all come from a real device. A profile picks one preset deterministically from
its fingerprint seed and the chosen OS family.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePreset:
    key: str
    os_type: str          # "android" | "ios"
    label: str
    user_agent: str
    # CSS pixels (layout viewport) and device pixel ratio
    width: int
    height: int
    dpr: float
    device_memory: int
    hardware_concurrency: int
    # Client Hints
    platform: str         # navigator.userAgentData platform, e.g. "Android"
    ua_full_version: str
    model: str            # Sec-CH-UA model, e.g. "Pixel 7"


# A small set of common, current real devices. Physical pixel resolution =
# width*dpr x height*dpr; the CSS viewport is width x height.
ANDROID_PRESETS = [
    DevicePreset(
        key="pixel-7", os_type="android", label="Pixel 7",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36"
        ),
        width=412, height=915, dpr=2.625,
        device_memory=8, hardware_concurrency=8,
        platform="Android", ua_full_version="148.0.0.0", model="Pixel 7",
    ),
    DevicePreset(
        key="galaxy-s23", os_type="android", label="Samsung Galaxy S23",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36"
        ),
        width=360, height=780, dpr=3.0,
        device_memory=8, hardware_concurrency=8,
        platform="Android", ua_full_version="148.0.0.0", model="SM-S911B",
    ),
    DevicePreset(
        key="xiaomi-13", os_type="android", label="Xiaomi 13",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; 2211133G) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36"
        ),
        width=393, height=873, dpr=2.75,
        device_memory=12, hardware_concurrency=8,
        platform="Android", ua_full_version="148.0.0.0", model="2211133G",
    ),
]

# iOS Safari has no Client Hints (Apple doesn't ship UA-CH); userAgentData is
# undefined on real iOS, which the mobile extension must reproduce.
IOS_PRESETS = [
    DevicePreset(
        key="iphone-15", os_type="ios", label="iPhone 15",
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        width=393, height=852, dpr=3.0,
        device_memory=4, hardware_concurrency=6,
        platform="iPhone", ua_full_version="", model="iPhone",
    ),
    DevicePreset(
        key="iphone-14", os_type="ios", label="iPhone 14",
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        width=390, height=844, dpr=3.0,
        device_memory=4, hardware_concurrency=6,
        platform="iPhone", ua_full_version="", model="iPhone",
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
