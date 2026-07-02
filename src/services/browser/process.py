import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from urllib.parse import urlparse

from ...core.config import DATA_DIR
from ...core.logging import get_logger
from ...models.profile import Profile
from ...utils.proxy_parser import parse_proxy_server
from ..bookmark.store import BookmarkStore
from ..proxy.bridge import ProxyBridge
from ..proxy.store import ProxyStore
from .bookmarks_seed import seed_bookmarks
from .cdp import cdp_port_for
from .audio_ext import build_audio_extension
from .device_ext import build_device_extension
from .resolution import parse_resolution, resolve_resolution
from .device_presets import is_mobile_os, pick_preset
from .gpu_ext import build_gpu_extension
from .measuretext_ext import build_measuretext_extension
from .mobile_ext import build_mobile_extension
from .webgl_ext import build_webgl_extension
from .geo_ext import build_geo_extension
from .locale_ext import build_locale_extension
from .stealth_ext import build_stealth_extension
from .font_config import build_font_config
from .profile_seed import seed_profile_prefs
from .title_ext import build_title_extension
from .window_entry import app_id_for, write_window_entry

logger = get_logger("browser.process")


from ...core.config import ENGINE_DIR
from ...core import platform as _platform

FINGERPRINT_CHROMIUM = os.path.join(
    ENGINE_DIR, _platform.fingerprint_chromium_filename()
)


def _proxy_arg(proxy_url: str | None) -> tuple[str | None, ProxyBridge | None]:
    """Resolve the --proxy-server value, starting a local bridge when the
    upstream proxy needs username/password auth (Chromium can't pass creds).
    """
    if not proxy_url:
        return None, None
    parsed = urlparse(proxy_url if "://" in proxy_url else "socks5://" + proxy_url)
    if parsed.username:
        bridge = ProxyBridge(proxy_url)
        port = bridge.start()
        logger.info("Proxy bridge for upstream %s on 127.0.0.1:%s", parsed.hostname, port)
        return f"socks5://127.0.0.1:{port}", bridge
    return parse_proxy_server(proxy_url), None


# Map a proxy's country to a sensible browser locale, so Accept-Language
# matches the exit IP. Falls back to en-US when the country is unknown.
_COUNTRY_LOCALE = {
    "US": "en-US", "CA": "en-CA", "GB": "en-GB", "AU": "en-AU", "IE": "en-IE",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH", "FR": "fr-FR", "BE": "fr-BE",
    "ES": "es-ES", "MX": "es-MX", "IT": "it-IT", "NL": "nl-NL", "PT": "pt-PT",
    "BR": "pt-BR", "PL": "pl-PL", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK",
    "FI": "fi-FI", "UA": "uk-UA", "RU": "ru-RU", "TR": "tr-TR", "JP": "ja-JP",
    "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW", "IN": "en-IN", "SG": "en-SG",
}


def _locale_for(country_code: str) -> str:
    return _COUNTRY_LOCALE.get((country_code or "").upper(), "en-US")


# Default timezone per country, used when the proxy record has no timezone yet,
# so a profile never falls back to the host's UTC and contradicts its exit IP.
_COUNTRY_TZ = {
    "US": "America/New_York", "CA": "America/Toronto", "GB": "Europe/London",
    "IE": "Europe/Dublin", "DE": "Europe/Berlin", "AT": "Europe/Vienna",
    "CH": "Europe/Zurich", "FR": "Europe/Paris", "BE": "Europe/Brussels",
    "ES": "Europe/Madrid", "IT": "Europe/Rome", "NL": "Europe/Amsterdam",
    "PT": "Europe/Lisbon", "PL": "Europe/Warsaw", "SE": "Europe/Stockholm",
    "NO": "Europe/Oslo", "DK": "Europe/Copenhagen", "FI": "Europe/Helsinki",
    "UA": "Europe/Kyiv", "RU": "Europe/Moscow", "TR": "Europe/Istanbul",
    "JP": "Asia/Tokyo", "KR": "Asia/Seoul", "CN": "Asia/Shanghai",
    "IN": "Asia/Kolkata", "SG": "Asia/Singapore", "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo", "MX": "America/Mexico_City",
}


def _timezone_for(country_code: str) -> str:
    return _COUNTRY_TZ.get((country_code or "").upper(), "UTC")


# Windows timezone keys (from the registry / GetDynamicTimeZoneInformation) map
# to IANA zones; only the common ones are listed, with an offset-based fallback
# for the rest. A concrete zone is what makes Firefox report a local time that
# matches the exit IP — otherwise a direct profile shows UTC and scanners flag a
# "spoofed location".
_WINDOWS_TZ_TO_IANA = {
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau",
    "GTB Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul",
    "Israel Standard Time": "Asia/Jerusalem",
    "Arabic Standard Time": "Asia/Baghdad",
    "Arab Standard Time": "Asia/Riyadh",
    "Iran Standard Time": "Asia/Tehran",
    "Arabian Standard Time": "Asia/Dubai",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "Singapore Standard Time": "Asia/Singapore",
    "W. Australia Standard Time": "Australia/Perth",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Eastern Standard Time": "America/Cayenne",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Central Brazilian Standard Time": "America/Cuiaba",
}


def _windows_timezone_key() -> str | None:
    """The Windows TimeZoneKeyName (e.g. 'FLE Standard Time'), via
    GetDynamicTimeZoneInformation. None if it can't be read."""
    try:
        import ctypes
        from ctypes import wintypes

        class DTZI(ctypes.Structure):
            _fields_ = [
                ("Bias", ctypes.c_long),
                ("StandardName", wintypes.WCHAR * 32),
                ("StandardDate", wintypes.BYTE * 16),
                ("StandardBias", ctypes.c_long),
                ("DaylightName", wintypes.WCHAR * 32),
                ("DaylightDate", wintypes.BYTE * 16),
                ("DaylightBias", ctypes.c_long),
                ("TimeZoneKeyName", wintypes.WCHAR * 128),
                ("DynamicDaylightTimeDisabled", wintypes.BOOLEAN),
            ]

        tzi = DTZI()
        ctypes.windll.kernel32.GetDynamicTimeZoneInformation(ctypes.byref(tzi))
        return tzi.TimeZoneKeyName or None
    except Exception:
        return None


def _offset_zone() -> str:
    """An Etc/GMT zone matching the host's current UTC offset — a coarse but
    scanner-consistent fallback (POSIX Etc/GMT signs are inverted)."""
    try:
        from datetime import datetime

        off = datetime.now().astimezone().utcoffset()
        if off is None:
            return "UTC"
        hours = int(off.total_seconds() // 3600)
        if hours == 0:
            return "UTC"
        return f"Etc/GMT{'+' if hours < 0 else '-'}{abs(hours)}"
    except Exception:
        return "UTC"


def _host_timezone() -> str:
    """The host's IANA timezone, for a direct (no-proxy) profile. Resolving to a
    concrete zone keeps invisible from doing a ~40s egress lookup at launch and
    makes Firefox's clock match the exit IP. Falls back to an offset zone, then
    UTC, when the host zone can't be resolved."""
    if _platform.IS_WINDOWS:
        key = _windows_timezone_key()
        if key and key in _WINDOWS_TZ_TO_IANA:
            return _WINDOWS_TZ_TO_IANA[key]
        return _offset_zone()
    try:
        from datetime import datetime

        name = datetime.now().astimezone().tzname()
        # tzname() can yield an abbreviation (e.g. "CET") rather than an IANA
        # zone; only accept a slash-form IANA path, else read /etc/localtime.
        if name and "/" in name:
            return name
    except Exception:
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return "UTC"




def _spawn_invisible(profile: Profile, profile_dir: str):
    """Launch the invisible_playwright (patched Firefox 150) engine. SOCKS5
    proxy auth is handled natively (no bridge). Returns a Popen-compatible
    handle."""
    from .invisible_launch import ensure_invisible_installed, spawn

    store = ProxyStore()
    proxy_url = store.resolve(profile.proxy) or ""
    proxy = store.get(profile.proxy) if profile.proxy else None
    # Locale + timezone follow the proxy's geo so they match the exit IP. Always
    # resolve to a CONCRETE zone — never leave it empty: invisible treats an
    # empty timezone as "auto" and blocks the launch ~40s on an egress-IP lookup
    # (direct, over Tor). A direct profile uses the host zone; that lookup is the
    # single biggest hit to open time.
    lang = _locale_for(proxy.country_code) if proxy else "en-US"
    tz = (proxy.timezone or _timezone_for(proxy.country_code)) if proxy else _host_timezone()

    if _platform.supports_linux_desktop_integration():
        write_window_entry(profile.name, icon="firefox")

    chosen = BookmarkStore().resolve_selection(
        profile.bookmark_pool, profile.bookmarks
    )

    width, height = resolve_resolution(
        getattr(profile, "resolution", "auto"), profile.fingerprint_seed
    )

    cfg = {
        "os_type": profile.os_type,
        "proxy_url": proxy_url,
        "profile_name": profile.name,
        "search_engine": profile.search_engine,
        "locale": lang,
        "timezone": tz,
        "bookmarks": [{"name": b.name, "url": b.url} for b in chosen],
        "resolution": [width, height],
        "profile_dir": os.path.join(profile_dir, ".invisible-profile"),
        "_needs_fetch": not ensure_invisible_installed(),
    }
    return spawn(cfg)


def spawn_browser(profile: Profile) -> subprocess.Popen:
    """Launch a persona browser (fingerprint-chromium or the patched Firefox)
    for the given profile."""
    profile_dir = os.path.join(DATA_DIR, profile.name)
    os.makedirs(profile_dir, exist_ok=True)

    engine = getattr(profile, "engine", "chromium")
    # "camoufox" is the retired engine name; treat any leftover as the Firefox
    # engine so an old profile keeps launching.
    # A mobile profile always launches on chromium: the Firefox engine has no
    # mobile mode, and only chromium carries the device preset that makes an
    # android/ios profile coherent. Fall back even if an old profile stored
    # engine=firefox with a mobile OS.
    if is_mobile_os(profile.os_type):
        engine = "chromium"
    if engine in ("firefox", "camoufox"):
        proc = _spawn_invisible(profile, profile_dir)
        proc._proxy_bridge = None  # type: ignore[attr-defined]
        return proc

    seed_profile_prefs(profile_dir, profile.search_engine)

    chosen = BookmarkStore().resolve_selection(
        profile.bookmark_pool, profile.bookmarks
    )
    seed_bookmarks(profile_dir, chosen)
    if _platform.supports_linux_desktop_integration():
        write_window_entry(profile.name)
    title_ext = build_title_extension(
        profile.name, os.path.join(profile_dir, ".persona-title-ext")
    )

    store = ProxyStore()
    proxy = store.get(profile.proxy) if profile.proxy else None
    proxy_url = store.resolve(profile.proxy)

    # Locale + timezone follow the proxy's geo so they match the exit IP.
    lang = _locale_for(proxy.country_code) if proxy else "en-US"

    # Mobile profiles are assembled at this layer (the engine has no Android/iOS
    # mode): a real device preset drives the UA, window size, screen and the
    # touch/Client-Hints extension. A profile is mobile when its OS is a mobile
    # family (android/ios) — device_type is kept on the model for the API but
    # the OS is the source of truth so the UI only needs the OS dropdown.
    is_mobile = is_mobile_os(profile.os_type) or profile.device_type == "mobile"
    # the mobile OS family for preset selection (android unless explicitly ios)
    mobile_os = profile.os_type if is_mobile_os(profile.os_type) else "android"
    preset = (
        pick_preset(profile.fingerprint_seed, mobile_os) if is_mobile else None
    )

    extensions = [title_ext]
    extensions.append(
        build_locale_extension(
            lang, os.path.join(profile_dir, ".persona-locale-ext")
        )
    )
    extensions.append(
        build_stealth_extension(
            os.path.join(profile_dir, ".persona-stealth-ext")
        )
    )
    extensions.append(
        build_measuretext_extension(
            os.path.join(profile_dir, ".persona-measuretext-ext")
        )
    )
    extensions.append(
        build_audio_extension(
            profile.fingerprint_seed,
            os.path.join(profile_dir, ".persona-audio-ext"),
        )
    )
    if is_mobile and preset is not None:
        extensions.append(
            build_mobile_extension(
                os.path.join(profile_dir, ".persona-mobile-ext"),
                is_ios=(preset.os_type == "ios"),
                platform=preset.platform,
                model=preset.model,
                ua_full_version=preset.ua_full_version,
                css_width=preset.width,
                css_height=preset.height,
                dpr=preset.dpr,
                device_memory=preset.device_memory,
                hardware_concurrency=preset.hardware_concurrency,
                touch_points=5,
            )
        )
    else:
        extensions.append(
            build_device_extension(
                profile.fingerprint_seed,
                os.path.join(profile_dir, ".persona-device-ext"),
                resolution=parse_resolution(getattr(profile, "resolution", "auto")),
            )
        )
    extensions.append(
        build_webgl_extension(
            profile.fingerprint_seed,
            os.path.join(profile_dir, ".persona-webgl-ext"),
        )
    )
    extensions.append(
        build_gpu_extension(
            profile.fingerprint_seed,
            profile.os_type,
            os.path.join(profile_dir, ".persona-gpu-ext"),
        )
    )
    if proxy and proxy.lat is not None and proxy.lon is not None:
        extensions.append(
            build_geo_extension(
                proxy.lat,
                proxy.lon,
                os.path.join(profile_dir, ".persona-geo-ext"),
            )
        )

    # The engine has no android/ios platform; back a mobile profile with the
    # nearest desktop platform the engine DOES spoof (linux for Android, macos
    # for iOS) so its native spoofs stay coherent, while the UA, window size
    # and the mobile extension supply the actual mobile signals.
    engine_platform = profile.os_type
    if is_mobile:
        engine_platform = "macos" if profile.os_type == "ios" else "linux"

    args = [FINGERPRINT_CHROMIUM]
    # --appimage-extract-and-run only applies to the Linux AppImage engine; the
    # Windows .exe / macOS .app are launched directly.
    if _platform.IS_LINUX:
        args.append("--appimage-extract-and-run")
    args += [
        f"--user-data-dir={profile_dir}",
        f"--fingerprint={profile.fingerprint_seed}",
        f"--fingerprint-platform={engine_platform}",
        "--fingerprint-brand=Chrome",
        f"--lang={lang}",
        f"--accept-lang={lang},{lang.split('-')[0]}",
        f"--load-extension={','.join(extensions)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        "--hide-crash-restore-bubble",
        "--force-dark-mode",
    ]

    if _platform.IS_LINUX:
        # Software GL (SwiftShader) keeps the GPU process alive so the
        # fingerprint WebGL spoofer populates a believable vendor/renderer;
        # --disable-gpu left a blank WebGL that flagged as fake. On Windows the
        # native D3D11 ANGLE backend renders correctly — forcing SwiftShader for
        # the whole GL stack there paints a BLACK, unrendered window, so keep
        # these Linux-only. The keychain flags are Linux/mac password-store
        # concepts and are meaningless (and unneeded) on Windows.
        args += [
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--password-store=basic",
            "--use-mock-keychain",
        ]

    # Wayland app_id (taskbar label/icon per persona) is an X11/Wayland concept;
    # only pass it on Linux. Matches the .desktop StartupWMClass via app_id_for.
    if _platform.supports_linux_desktop_integration():
        args.append(f"--class={app_id_for(profile.name)}")

    if is_mobile and preset is not None:
        # Drive the real device's UA and a window sized to its CSS viewport, so
        # the browser presents the device's screen and layout. The mobile
        # extension fills the JS-visible touch/Client-Hints/screen signals.
        args.append(f"--user-agent={preset.user_agent}")
        args.append(f"--window-size={preset.width},{preset.height}")

    if proxy:
        tz = proxy.timezone or _timezone_for(proxy.country_code)
        args.append(f"--timezone={tz}")

    if getattr(profile, "ai_control", False):
        args.append(f"--remote-debugging-port={cdp_port_for(profile.name)}")
        # Chrome 132+ rejects a DevTools WebSocket whose Origin isn't allow-listed
        # (403 "Rejected an incoming WebSocket connection"). The client's Origin
        # includes the port (e.g. http://127.0.0.1:9238) and varies, so allow all
        # origins — the port only listens on loopback, reachable by local tools.
        args.append("--remote-allow-origins=*")

    proxy_server, bridge = _proxy_arg(proxy_url)
    if proxy_server:
        args.append(f"--proxy-server={proxy_server}")
        # Keep DNS and WebRTC from leaking past the proxy. Chrome's built-in
        # DNS-over-HTTPS resolves names directly to a DoH endpoint, bypassing
        # the SOCKS proxy entirely (so the DNS test shows a country unrelated
        # to the exit IP). Turn DoH off so name lookups go through the proxy,
        # and forbid WebRTC's non-proxied UDP which can reveal the real IP.
        args.append("--disable-features=DnsOverHttps")
        args.append("--dns-over-https-mode=off")
        args.append(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
        )
        args.append("--dns-prefetch-disable")

    env = os.environ.copy()
    # X11 DISPLAY and fontconfig are Linux concepts; on Win/Mac the OS supplies
    # fonts and there's no DISPLAY to set.
    if _platform.IS_LINUX:
        env.setdefault("DISPLAY", ":0")
        env["FONTCONFIG_FILE"] = build_font_config(profile_dir, engine_platform)

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=os.path.expanduser("~"),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **_platform.no_window_kwargs(),
    )
    proc._proxy_bridge = bridge  # type: ignore[attr-defined]
    return proc


def _stop_bridge(proc: subprocess.Popen) -> None:
    bridge = getattr(proc, "_proxy_bridge", None)
    if bridge is not None:
        with _suppress():
            bridge.stop()
        proc._proxy_bridge = None  # type: ignore[attr-defined]


def terminate(proc: subprocess.Popen, name: str, timeout: int = 5) -> None:
    """Gracefully terminate a browser process, force-kill on timeout."""
    if proc.poll() is not None:
        _stop_bridge(proc)
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            logger.info("Browser %s terminated gracefully", name)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
            logger.warning("Browser %s force killed after timeout", name)
    except Exception as e:
        logger.exception("Error terminating browser %s: %s", name, e)
    finally:
        _stop_bridge(proc)


def wait_for_exit(
    proc: subprocess.Popen,
    name: str,
    notify_stopped: Callable[[], None],
) -> None:
    """Block until the process exits, then fire the callback."""
    try:
        proc.wait()
    except Exception as e:
        logger.exception("Wait error for profile %s: %s", name, e)
    finally:
        _stop_bridge(proc)
        notify_stopped()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True
