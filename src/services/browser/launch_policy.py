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
import time

from ...core import platform as _platform
from ..proxy.errors import GeographyDisprovenError, GeographyUnknownError
from ..proxy.freshness import proxy_indicator_state

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
    """The host's IANA timezone. Reads the operator's REAL location.

    NOT USED ON ANY LAUNCH PATH, deliberately. It has no caller in ``src/`` —
    ``git grep -n "_host_timezone" -- src/`` returns only this definition and
    the re-export in ``process.py``. Its one live call site was
    ``_proxy_timezone``'s removed third branch, which declared this value inside
    a proxied profile.

    It is KEPT rather than deleted because the test that proves a direct profile
    does NOT leak the host zone patches this name
    (``test_chromium_no_proxy_timezone_matches_en_us_language``), and
    ``monkeypatch.setattr`` raises ``AttributeError`` on a missing attribute —
    so deleting it would break the very test that guards the adjacent leak.
    A distinctive patched value is how that test proves the host zone never
    reaches an engine.

    Do not reintroduce a call to this from any path that decides what a profile
    CLAIMS about its location. Both a direct profile (a US zone, agreeing with
    the forced en-US language) and a proxied one (the exit's zone) are answered
    without it; a proxy with no geography is REFUSED, not answered from here.

    Falls back to an offset zone, then UTC, when the host zone can't be resolved.
    """
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
    """The timezone for a proxied profile — or a REFUSAL when there isn't one.

    Two branches answer honestly: the zone the check recorded, else the zone
    implied by the checked country. Both describe the EXIT, which is the only
    location a proxied persona may claim — but only while the product still
    believes them. A stored zone whose most recent check FAILED is geography the
    product's own latest evidence disproves, so it is refused BEFORE either
    branch is consulted (see the guard below).

    An unchecked proxy (no geo at all) has no third answer. It used to fall back
    to the host zone, on the reasoning that UTC against a non-UTC exit IP is a
    louder fingerprint tell. That trade is off the table: the "quieter" value was
    the OPERATOR'S REAL TIMEZONE, declared inside the tunnel — a real-location
    disclosure on precisely the vector the proxy exists to close. Trading a
    fingerprint tell against deanonymization is not a trade worth making.

    So when no geography is available the answer is STOP: not a host-derived
    value, not a coarser value, not a quieter value. This raises rather than
    returning a sentinel so the unknown is UNREPRESENTABLE as a zone string and
    no caller can ship it to an engine by accident. A persona that will not
    launch has disclosed nothing.

    The refusal is escapable and the remedy is one click: check the proxy, which
    writes country_code + timezone (ProxyStore.mark_checked), and the profile
    then launches through the first or second branch declaring the exit's zone.

    WHICH freshness states refuse, and why only these:

    - "failed"     -> REFUSED. The check ran and did not pass, so the stored
                      zone is contradicted by the product's own most recent
                      evidence. A failure does not age into something softer.
    - "unverified" -> refused ONLY when it also carries no geography, which is
                      PS-31's existing no-geo branch below. A record with geo
                      but no successful check on file is left launching here;
                      see the note under the guard.
    - "stale"      -> LAUNCHES. Verified, just old. Deliberately not merged with
                      "failed": PROXY_STALE_AFTER_S was calibrated for a RENDER
                      ("should this flag look confident?"), which does not
                      transfer to a REFUSAL ("may this profile launch at all?").
                      Rotating/backconnect proxies are the product's stated
                      target configuration, so staleness is their steady state
                      and a launch-time age limit would lock operators out of
                      their own profiles between checks. A fabricated threshold
                      is worse than a stale zone.
    - "verified"   -> LAUNCHES, unchanged.

    Reads stored state only — proxy_indicator_state never probes, so consulting
    it here opens no socket. This function does not re-check the proxy, by
    design: live verification is legitimate only as an explicit operator act.

    Raises:
        GeographyDisprovenError: the last recorded check FAILED, so the stored
            geography is disproven. A subclass of GeographyUnknownError, so
            every existing fail-closed handler catches it unchanged.
        GeographyUnknownError: the proxy carries neither timezone nor country.
    """
    # BEFORE the branches, not after: branch 1 would otherwise keep returning a
    # stale zone forever, which is exactly the defect. `time.time()` only feeds
    # the stale/verified split, which this guard does not act on — a failed
    # check reads "failed" at any age, so the answer here is time-independent.
    #
    # GATED ON GEOGRAPHY BEING ON FILE, and that conjunct is load-bearing rather
    # than defensive. "failed" is a verdict about the CHECK, not about the
    # record: a brand-new proxy whose FIRST check fails (app.py's
    # on_check_failed -> ProxyStore.mark_check_failed) reads "failed" with
    # tz='' country='' — it never had geography for anything to disprove. Both
    # states refuse either way, so this changes no launch outcome; it decides
    # which SENTENCE the operator is told, and "the geography still on file is
    # disproven" asserts a record that does not exist. Without the conjunct that
    # case falls in here and gets a false explanation, replacing PS-31's true
    # "never successfully checked" — the inverse of the error AC4 forbids, on
    # the state a new operator is most likely to reach first. With it, a failed
    # check carrying no geo falls through to PS-31's raise below, which
    # describes it accurately.
    if (proxy.timezone or proxy.country_code) and proxy_indicator_state(
        proxy, time.time()
    ) == "failed":
        raise GeographyDisprovenError(
            "the proxy's last check FAILED, so its recorded geography is "
            "disproven: refusing to declare a location the most recent "
            "evidence contradicts. Re-check the proxy to resolve it"
        )
    # NOTE — the tri-state row (`last_check_ok is None` WITH geography on file,
    # e.g. a legacy/hand-edited proxies.json, which loads via store.py:62 as
    # None) reads "unverified" and is deliberately left LAUNCHING by this slice.
    # Refusing it is defensible on the shipped rule that a country code without
    # a timestamp is not evidence, but it is a strictly wider behaviour change
    # than the disproven case this ticket is scoped to, and it cannot be made
    # here without editing assertions the ticket requires to pass untouched
    # (the duck-typed proxy stand-ins in test_tz.py / test_geo_unknown_refusal.py
    # / test_process.py carry geography but no check bookkeeping, so they all
    # read "unverified"). Called out in the PR rather than decided silently.
    if proxy.timezone:
        return proxy.timezone
    if proxy.country_code:
        return _timezone_for(proxy.country_code)
    raise GeographyUnknownError(
        "proxy has no geography (never successfully checked): refusing to "
        "derive a timezone from the host, which would disclose the operator's "
        "real location inside the tunnel"
    )

