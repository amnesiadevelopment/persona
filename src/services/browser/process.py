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
from ..cert.store import CertStore
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
from .voice_ext import build_voice_extension
from .native_ext import build_native_extension
from .stealth_ext import build_stealth_extension
from .profile_seed import seed_profile_prefs
from .search_ext import build_search_extension
from .title_ext import build_title_extension
from .window_entry import app_id_for, write_window_entry

logger = get_logger("browser.process")


from ...core.config import ENGINE_DIR
from ...core import platform as _platform

FINGERPRINT_CHROMIUM = os.path.join(
    ENGINE_DIR, _platform.fingerprint_chromium_filename()
)


def _cert_session_for(profile: Profile, profile_dir: str, upstream: str | None):
    """Start an mTLS terminator for the profile's assigned certificate, or None.
    The terminator's own upstream is the profile's real proxy so the exit IP is
    unchanged. Its work dir is under the profile so leaf/PEM material is cleaned
    with the profile."""
    cert_name = getattr(profile, "certificate", None)
    if not cert_name:
        return None
    from ..cert.manager import start_cert_session

    cert = CertStore().get(cert_name)
    if cert is None:
        return None
    work = os.path.join(profile_dir, ".persona-mtls")
    return start_cert_session(cert, upstream or None, work)


def _proxy_arg(proxy_url: str | None) -> tuple[str | None, ProxyBridge | None]:
    """Resolve the --proxy-server value, starting a local bridge when the
    upstream proxy needs username/password auth (Chromium can't pass creds).

    Chromium's SOCKS5 client already resolves the destination at the proxy
    (remote DNS) — socks5h is a curl-ism Chromium rejects with
    ERR_NO_SUPPORTED_PROXIES — so the scheme stays socks5.
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


def _host_display_scale() -> float:
    """The host display's scale factor (1.0 at 100%, 1.5 at 150%, 2.0 at 200%).
    Windows reads the system DPI; other desktops render at 1.0. Clamped to a
    sane range so a weird reading can't blow the window up."""
    if _platform.IS_MACOS:
        # Retina backing scale (2.0), so --force-device-scale-factor stops the
        # chromium engine painting 1:1 physical px (unreadably tiny). Mirrors the
        # Firefox engine's macOS dpr fix. CoreGraphics ctypes (no PyObjC needed).
        try:
            import ctypes
            import ctypes.util

            cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
            cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
            cg.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
            cg.CGDisplayModeGetPixelWidth.argtypes = [ctypes.c_void_p]
            cg.CGDisplayModeGetWidth.restype = ctypes.c_size_t
            cg.CGDisplayModeGetWidth.argtypes = [ctypes.c_void_p]
            cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
            did = cg.CGMainDisplayID()
            mode = cg.CGDisplayCopyDisplayMode(did)
            if mode:
                pw = cg.CGDisplayModeGetPixelWidth(mode)
                w = cg.CGDisplayModeGetWidth(mode)
                cg.CGDisplayModeRelease(mode)
                if w:
                    return max(1.0, min(3.0, round(pw / w, 2)))
        except Exception:
            pass
        return 1.0
    if not _platform.IS_WINDOWS:
        return 1.0
    try:
        import ctypes

        # Per-monitor DPI awareness so GetDpiForSystem returns the real scale.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / 96.0 if dpi else 1.0
        return max(1.0, min(3.0, round(scale, 2)))
    except Exception:
        return 1.0


def _proxy_timezone(proxy) -> str:
    """The timezone for a proxied profile. An unchecked proxy (no geo data yet)
    falls back to the host zone — UTC against a non-UTC exit IP is a louder
    fingerprint tell than the host zone, and matches direct-profile behavior."""
    if proxy.timezone:
        return proxy.timezone
    if proxy.country_code:
        return _timezone_for(proxy.country_code)
    return _host_timezone()


def _spawn_invisible(profile: Profile, profile_dir: str):
    """Launch the invisible_playwright (patched Firefox 150) engine. SOCKS5
    proxy auth is handled natively (no bridge). Returns a Popen-compatible
    handle."""
    from .invisible_launch import is_invisible_installed, spawn

    store = ProxyStore()
    proxy_url = store.resolve(profile.proxy) or ""
    proxy = store.get(profile.proxy) if profile.proxy else None
    # Locale + timezone follow the proxy's geo so they match the exit IP. Always
    # resolve to a CONCRETE zone — never leave it empty: invisible treats an
    # empty timezone as "auto" and blocks the launch ~40s on an egress-IP lookup
    # (direct, over Tor).
    #
    # With NO proxy the language is forced to en-US (persona never leaks the host
    # locale, e.g. uk-UA — #218), so the timezone must AGREE with en-US, not the
    # host zone: a fresh CreepJS run on a Kyiv host showed language=en-US paired
    # with timezone=Europe/Kyiv, and language⊥timezone is a classic inconsistency
    # a detector flags. Pin a US zone so the direct identity reads as one coherent
    # US-English user AND the host location stays hidden.
    lang = _locale_for(proxy.country_code) if proxy else "en-US"
    tz = _proxy_timezone(proxy) if proxy else _timezone_for("US")

    if _platform.supports_linux_desktop_integration():
        write_window_entry(profile.name, icon="firefox")

    chosen = BookmarkStore().resolve_selection(
        profile.bookmark_pool, profile.bookmarks
    )

    # An assigned mTLS certificate starts a terminator (in this parent) that the
    # engine talks to as its proxy; the engine trusts the terminator's leaf by
    # importing its CA into the profile's cert9.db. The certificate is presented
    # to the admin host only and never enters the browser.
    cert_session = _cert_session_for(profile, profile_dir, proxy_url)
    if cert_session is not None:
        proxy_url = cert_session.proxy_url

    width, height = resolve_resolution(
        getattr(profile, "resolution", "auto"), profile.fingerprint_seed
    )

    cfg = {
        "os_type": profile.os_type,
        "proxy_url": proxy_url,
        "mtls_ca_path": cert_session.ca_path if cert_session else None,
        "profile_name": profile.name,
        # The stable crc32 fingerprint seed — the child must NOT derive it via
        # hash(), which is salted per-process and changes every app restart.
        "seed": profile.fingerprint_seed,
        "search_engine": profile.search_engine,
        "locale": lang,
        "timezone": tz,
        "bookmarks": [{"name": b.name, "url": b.url} for b in chosen],
        "resolution": [width, height],
        "profile_dir": os.path.join(profile_dir, ".invisible-profile"),
        # A pure presence check: ensure_invisible_installed would DOWNLOAD the
        # ~118MB engine here and block the launch for minutes over Tor.
        "_needs_fetch": not is_invisible_installed(),
    }
    proc = spawn(cfg)
    # Stop the terminator when the FF session ends (same hook the chromium path
    # uses; None when no certificate is assigned).
    proc._cert_session = cert_session  # type: ignore[attr-defined]
    return proc


def effective_engine(profile: Profile) -> str:
    """The engine actually launched for a profile — readiness monitoring and
    install checks must follow this, not the stored engine."""
    # A mobile profile always launches on chromium: the Firefox engine has no
    # mobile mode, and only chromium carries the device preset that makes an
    # android/ios profile coherent. Fall back even if an old profile stored
    # engine=firefox with a mobile OS.
    if is_mobile_os(profile.os_type):
        return "chromium"
    engine = getattr(profile, "engine", "chromium")
    # "camoufox" is the retired engine name; treat any leftover as the Firefox
    # engine so an old profile keeps launching.
    return "firefox" if engine == "camoufox" else engine


def spawn_browser(profile: Profile) -> subprocess.Popen:
    """Launch a persona browser (fingerprint-chromium or the patched Firefox)
    for the given profile."""
    profile_dir = os.path.join(DATA_DIR, profile.name)
    os.makedirs(profile_dir, exist_ok=True)

    engine = effective_engine(profile)
    if engine == "firefox":
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

    # An assigned mTLS certificate starts a local terminator that presents the
    # client cert to the admin host only; its own upstream is the profile's real
    # proxy, so the exit IP is unchanged. Unlike Firefox (which points its whole
    # proxy at the terminator and trusts the leaf via cert9.db), chromium keeps
    # its REAL proxy and reaches the terminator DIRECTLY for the admin host only
    # (--proxy-bypass-list + --host-resolver-rules below): its spki-list trust
    # only covers a leaf seen on a direct connection, not one behind a CONNECT
    # proxy. So proxy_url stays the real proxy here.
    cert_session = _cert_session_for(profile, profile_dir, proxy_url)

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
    # native_ext patches Function.prototype.toString so persona's wrapped
    # built-ins (Intl/matchMedia/getVoices/Worker/…) stringify as native code,
    # hiding the JS-override tell a masking detector reports.
    extensions.append(
        build_native_extension(
            os.path.join(profile_dir, ".persona-native-ext")
        )
    )
    extensions.append(
        build_locale_extension(
            lang, os.path.join(profile_dir, ".persona-locale-ext")
        )
    )
    # fingerprint-chromium leaks the host OS speech-voice list (~180 macOS voices
    # led by the host locale); replace it with a Windows-plausible set matching
    # `lang`, at parity with the Firefox engine.
    extensions.append(
        build_voice_extension(
            lang, os.path.join(profile_dir, ".persona-voice-ext")
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
    # On Windows/macOS the seeded default_search_provider_data pref is reset by
    # tracked-preference (default-search) enforcement, so a settings-override
    # extension is the per-profile mechanism that actually applies the chosen
    # engine. On Linux enforcement is off and the plaintext seed already sticks,
    # so skip the extension there (it would raise the "an extension changed your
    # search settings" bubble on a path that already works silently).
    if not _platform.IS_LINUX:
        extensions.append(
            build_search_extension(
                profile.search_engine,
                os.path.join(profile_dir, ".persona-search-ext"),
            )
        )
    extensions.append(
        build_audio_extension(
            profile.fingerprint_seed,
            os.path.join(profile_dir, ".persona-audio-ext"),
        )
    )
    if is_mobile and preset is not None:
        # iOS always reports 5 touch points; Android varies by device (commonly 5
        # or 10). A constant 5 on every Android profile is a weak cluster tell, so
        # pick it deterministically from the profile seed — stable per profile,
        # spread across profiles.
        if preset.os_type == "ios":
            touch_points = 5
        else:
            touch_points = (5, 10)[profile.fingerprint_seed % 2]
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
                touch_points=touch_points,
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
    # Chromium honors only the LAST --disable-features switch on the command
    # line — repeated switches replace, not merge. Collect every disabled
    # feature here and emit a single merged flag below.
    disabled_features = []
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
        # Chromium 130+ shows a default-search choice screen (EEA) and, until the
        # choice is recorded, drives the default from the prepopulated set —
        # which overrides the profile's chosen engine. Suppress it so the seeded
        # engine / search-override extension is what takes effect.
        "--disable-search-engine-choice-screen",
        "--restore-last-session",
        "--hide-crash-restore-bubble",
        "--force-dark-mode",
        # Keep the page's Page-Visibility state "visible" and its rAF running
        # even when the window isn't the foreground/focused window. Chromium
        # otherwise marks a non-foreground or occluded window hidden and throttles
        # requestAnimationFrame to ~0fps. Google Sheets mounts its overlays — the
        # date-cell calendar picker and the custom-currency dialog — via
        # rAF-driven animations, so a throttled window never paints them: they
        # read as "dead"/never-open on every OS, while the grid (already drawn)
        # looks fine. Firefox drives those overlays off a path that isn't
        # visibility-throttled, which is why it was never affected. Measured on a
        # real-GPU host: a persona window reported visibilityState=hidden and rAF
        # fired 0 frames in 5s; with visibility forced, rAF ran full speed and the
        # overlays opened.
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
    ]
    # Windows computes native window occlusion and marks a covered window hidden;
    # that alone throttled rAF to zero even with the backgrounding flags above.
    disabled_features.append("CalculateNativeWinOcclusion")

    if _platform.IS_MACOS:
        # Keep the cookie-encryption key out of the login Keychain (no Keychain
        # prompt, no host-identity leak) — the password-store flags Linux also
        # uses. Separate from the Linux SwiftShader block (must NOT run on mac).
        args += ["--password-store=basic", "--use-mock-keychain"]

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
            # Under the software (SwiftShader) compositor the frame clock is
            # degenerate ("Frame latency is negative") and a browser-UI
            # animation can spin without ever reaching its end state — the log
            # shows "CompositorAnimationObserver is active for too long (180s)
            # location=Button". While it spins, the compositor never idles, so
            # the "Working…" throbber sticks and Google Sheets' overlays (the
            # date-cell calendar, the custom-currency menu) never get a frame
            # to paint into. Starve those animations at the source instead of
            # trusting the clock: --animation-duration-scale=0 completes every
            # gfx UI animation on its first tick (read in
            # ui/compositor/compositor.cc → ScopedAnimationDurationScaleMode →
            # LinearAnimation::GetDuration) and --wm-window-animations-disabled
            # drops window show/hide animations outright
            # (ui/wm/core/window_animations.cc). Both act on browser UI only —
            # web-content animations are Blink-side — so nothing is
            # page-visible (unlike prefers-reduced-motion, which is).
            # --disable-threaded-animation stays so the compositor thread holds
            # no animation state of its own to get stuck on.
            "--disable-threaded-animation",
            "--animation-duration-scale=0",
            "--wm-window-animations-disabled",
            # Under SwiftShader the frame clock is degenerate, and chromium's
            # vsync throttle paces the compositor off it: measured
            # requestAnimationFrame ran at ~6fps in Google Sheets. Detaching from
            # the broken vsync clock lifts it back to ~60fps. (Harmless, no GPU
            # readback stalls — unlike --disable-gpu-compositing, which forced a
            # CPU readback that spammed "GL_CLOSE_PATH_NV: GPU stall due to
            # ReadPixels" and did NOT fix the real Sheets-through-proxy hang.)
            "--disable-gpu-vsync",
        ]
        # The VM has no VA-API hardware, so chromium's attempt to init
        # hardware video decode logs a red "vaInitialize failed: unknown
        # libva error" (media/gpu/vaapi/vaapi_wrapper.cc). Harmless, but it
        # noises the log AND is a VM tell — real desktop Chrome on a GPU
        # inits VA-API fine, a hard failure says "no hardware". Don't try:
        # software decode is what a machine without video hardware uses.
        disabled_features += ["VaapiVideoDecoder", "VaapiVideoEncoder"]

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

    # Render scale is decoupled from the fingerprint: the device/mobile
    # extension pins the JS-visible screen.*, devicePixelRatio and the
    # matchMedia dppx answers, while --force-device-scale-factor only sets how
    # many physical pixels draw one CSS px. Without it a dpr-1 profile paints
    # 1:1 physical on a 150%/200% display, so a 2560x1440 profile renders
    # unreadably small even though scanners see the correct 2K/dpr-1 screen.
    scale = _host_display_scale()
    if scale != 1.0:
        args.append(f"--force-device-scale-factor={scale:g}")

    # Always pin a concrete timezone. With a proxy it follows the exit geo; with
    # NO proxy it must AGREE with the forced en-US language (lang above), not leak
    # the host zone — CreepJS on a Kyiv host showed en-US paired with Europe/Kyiv,
    # a language⊥timezone tell. A US zone keeps the direct identity coherent and
    # hides the host location (matches the Firefox path).
    args.append(f"--timezone={_proxy_timezone(proxy) if proxy else _timezone_for('US')}")

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
        disabled_features.append("DnsOverHttps")
        args.append("--dns-over-https-mode=off")
        args.append(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
        )
        args.append("--dns-prefetch-disable")
        # A SOCKS5 proxy tunnels only TCP; it has no UDP path. Google apps
        # (Sheets/Docs) prefer QUIC — HTTP/3 over UDP — for their realtime
        # collaboration channel, so behind the proxy that channel's UDP never
        # reaches Google, Chromium doesn't fall back cleanly, and the app hangs
        # on a permanent "Working" while the calendar / custom-currency overlays
        # that load through it never paint. Disable QUIC so every request uses
        # HTTP/2 over TCP, which the proxy carries. The webrtc flag above only
        # covers WebRTC's UDP, not QUIC's, so this is a separate switch. A Chrome
        # behind a UDP-blocking proxy runs without HTTP/3 too, so this reads as
        # normal, not as a spoof tell.
        args.append("--disable-quic")
        disabled_features.append("EnableQuic")

    if cert_session is not None:
        # Trust the terminator's leaf without touching any OS store — keyed to the
        # leaf's public-key hash, so only the terminator's MITM is trusted. This
        # only takes effect on a DIRECT connection, so route the admin host to the
        # terminator directly: resolve it to the terminator's loopback port and
        # bypass the proxy for it (its traffic still exits via the real proxy —
        # that's the terminator's own upstream). Everything else keeps the proxy.
        host = cert_session.admin_host
        term_addr = f"127.0.0.1:{cert_session.port}"
        args.append(
            f"--ignore-certificate-errors-spki-list={cert_session.spki_b64}"
        )
        args.append(
            f'--host-resolver-rules=MAP {host} 127.0.0.1:{cert_session.port}'
        )
        if proxy_server:
            args.append(f"--proxy-bypass-list={host}")
        logger.info("chromium mTLS: %s -> terminator %s (direct, spki-pinned)",
                    host, term_addr)

    if disabled_features:
        args.append("--disable-features=" + ",".join(disabled_features))

    env = os.environ.copy()
    # Fonts come from the system fontconfig. A FONTCONFIG_FILE override floods
    # live sessions with "Cannot load default config file" errors from chromium
    # child processes and makes pages render with the wrong fonts; the engine
    # spoofs the JS-visible font list itself, so an override buys no
    # anti-detect value. The app's own runtime can export FONTCONFIG_* into
    # os.environ (an AppImage bundle points them into its mount, which is gone
    # for the relaunched process after a self-update), so scrub them rather
    # than trust the inherited environment.
    for var in ("FONTCONFIG_FILE", "FONTCONFIG_PATH", "FONTCONFIG_SYSROOT"):
        env.pop(var, None)
    if _platform.IS_LINUX:
        env.setdefault("DISPLAY", ":0")

    try:
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
    except BaseException:
        # The bridge/terminator started before the browser; if the launch never
        # produces a proc to attach them to, stop them here so a failed launch
        # can't orphan a listener on 127.0.0.1 (repeated failures exhaust ports).
        if bridge is not None:
            with _suppress():
                bridge.stop()
        if cert_session is not None:
            with _suppress():
                cert_session.stop()
        raise
    proc._proxy_bridge = bridge  # type: ignore[attr-defined]
    proc._cert_session = cert_session  # type: ignore[attr-defined]
    return proc


def _stop_bridge(proc: subprocess.Popen) -> None:
    bridge = getattr(proc, "_proxy_bridge", None)
    if bridge is not None:
        with _suppress():
            bridge.stop()
        proc._proxy_bridge = None  # type: ignore[attr-defined]
    session = getattr(proc, "_cert_session", None)
    if session is not None:
        with _suppress():
            session.stop()
        proc._cert_session = None  # type: ignore[attr-defined]


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
