"""Shared launch policy: the locale / timezone / display-scale derivations that
decide what a profile *claims* about its location and screen.

Extracted verbatim from ``process.py`` (behavior-preserving, no logic changed).
It lives on its own so the Chromium launcher (``process.py``) and the Firefox
launcher (``invisible_launch.py``) have one place to share these invariants
instead of rediscovering them one engine at a time.

``process.py`` re-exports every member below, so existing imports and
``monkeypatch.setattr(process, ...)`` on functions whose *callers* stay in
``process.py`` keep working unchanged.
"""

import os

from ...core import platform as _platform

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

