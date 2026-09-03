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
from ..proxy.errors import (
    GeographyDisprovenError,
    GeographyUnknownError,
    LocaleUnderivableError,
    TimezoneUnderivableError,
)
from ..proxy.freshness import proxy_indicator_state

# Map a proxy's country to a sensible browser locale, so Accept-Language
# matches the exit IP.
#
# NO FALLBACK. This table used to be read as ``.get(code, "en-US")``, and that
# default is gone for the reason ``_locale_for`` states below: it answered for
# countries this table does not know, while its sibling ``_COUNTRY_TZ`` refused
# for the very same input. Two halves of one derivation taking opposite
# positions shipped a profile declaring an American-English browser whose clock
# was in Sofia.
#
# THE FIRST 30 ROWS BELOW ARE THE ORIGINAL SET and are deliberately untouched —
# auditing them is explicitly out of scope. The rows after them widen the shared
# country set so that refusing outside it stops a short, nameable residue rather
# than most of the world. ``_COUNTRY_TZ`` gains the SAME codes in the same act;
# ``tests/test_country_table_correspondence.py`` enforces set-equality in both
# directions, so a row added to one table without the other fails the suite.
#
# ONE ROW PER COUNTRY, following the convention the original 30 already set: the
# most common *browser* locale, not the politically complete answer (BE is
# ``fr-BE``, CH is ``de-CH``, IN is ``en-IN``). A reviewer may reasonably
# disagree with an individual row; that is a row-level correction, not an
# argument against the invariant.
_COUNTRY_LOCALE = {
    "US": "en-US", "CA": "en-CA", "GB": "en-GB", "AU": "en-AU", "IE": "en-IE",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH", "FR": "fr-FR", "BE": "fr-BE",
    "ES": "es-ES", "MX": "es-MX", "IT": "it-IT", "NL": "nl-NL", "PT": "pt-PT",
    "BR": "pt-BR", "PL": "pl-PL", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK",
    "FI": "fi-FI", "UA": "uk-UA", "RU": "ru-RU", "TR": "tr-TR", "JP": "ja-JP",
    "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW", "IN": "en-IN", "SG": "en-SG",
    # --- widened shared set (PS-240). Added together with the matching
    # --- _COUNTRY_TZ rows below; neither table may gain a code alone.
    "AD": "ca-AD", "AE": "ar-AE", "AF": "fa-AF", "AG": "en-AG",
    "AI": "en-AI", "AL": "sq-AL", "AM": "hy-AM", "AO": "pt-AO",
    "AR": "es-AR", "AS": "en-AS", "AW": "nl-AW", "AX": "sv-AX",
    "AZ": "az-AZ", "BA": "bs-BA", "BB": "en-BB", "BD": "bn-BD",
    "BF": "fr-BF", "BG": "bg-BG", "BH": "ar-BH", "BI": "fr-BI",
    "BJ": "fr-BJ", "BL": "fr-BL", "BM": "en-BM", "BN": "ms-BN",
    "BO": "es-BO", "BQ": "nl-BQ", "BS": "en-BS", "BT": "dz-BT",
    "BW": "en-BW", "BY": "be-BY", "BZ": "en-BZ", "CC": "en-CC",
    "CD": "fr-CD", "CF": "fr-CF", "CG": "fr-CG", "CI": "fr-CI",
    "CK": "en-CK", "CL": "es-CL", "CM": "fr-CM", "CO": "es-CO",
    "CR": "es-CR", "CU": "es-CU", "CV": "pt-CV", "CW": "nl-CW",
    "CX": "en-CX", "CY": "el-CY", "CZ": "cs-CZ", "DJ": "fr-DJ",
    "DM": "en-DM", "DO": "es-DO", "DZ": "ar-DZ", "EC": "es-EC",
    "EE": "et-EE", "EG": "ar-EG", "EH": "ar-EH", "ER": "ti-ER",
    "ET": "am-ET", "FJ": "en-FJ", "FK": "en-FK", "FM": "en-FM",
    "FO": "fo-FO", "GA": "fr-GA", "GD": "en-GD", "GE": "ka-GE",
    "GF": "fr-GF", "GG": "en-GG", "GH": "en-GH", "GI": "en-GI",
    "GL": "kl-GL", "GM": "en-GM", "GN": "fr-GN", "GP": "fr-GP",
    "GQ": "es-GQ", "GR": "el-GR", "GT": "es-GT", "GU": "en-GU",
    "GW": "pt-GW", "GY": "en-GY", "HK": "zh-HK", "HN": "es-HN",
    "HR": "hr-HR", "HT": "fr-HT", "HU": "hu-HU", "ID": "id-ID",
    "IL": "he-IL", "IM": "en-IM", "IO": "en-IO", "IQ": "ar-IQ",
    "IR": "fa-IR", "IS": "is-IS", "JE": "en-JE", "JM": "en-JM",
    "JO": "ar-JO", "KE": "sw-KE", "KG": "ky-KG", "KH": "km-KH",
    "KI": "en-KI", "KM": "ar-KM", "KN": "en-KN", "KP": "ko-KP",
    "KW": "ar-KW", "KY": "en-KY", "KZ": "kk-KZ", "LA": "lo-LA",
    "LB": "ar-LB", "LC": "en-LC", "LI": "de-LI", "LK": "si-LK",
    "LR": "en-LR", "LS": "en-LS", "LT": "lt-LT", "LU": "fr-LU",
    "LV": "lv-LV", "LY": "ar-LY", "MA": "ar-MA", "MC": "fr-MC",
    "MD": "ro-MD", "ME": "sr-ME", "MF": "fr-MF", "MG": "fr-MG",
    "MH": "en-MH", "MK": "mk-MK", "ML": "fr-ML", "MM": "my-MM",
    "MN": "mn-MN", "MO": "zh-MO", "MP": "en-MP", "MQ": "fr-MQ",
    "MR": "ar-MR", "MS": "en-MS", "MT": "mt-MT", "MU": "en-MU",
    "MV": "dv-MV", "MW": "en-MW", "MY": "ms-MY", "MZ": "pt-MZ",
    "NA": "en-NA", "NC": "fr-NC", "NE": "fr-NE", "NF": "en-NF",
    "NI": "es-NI", "NP": "ne-NP", "NR": "en-NR", "NU": "en-NU",
    "NZ": "en-NZ", "OM": "ar-OM", "PA": "es-PA", "PE": "es-PE",
    "PF": "fr-PF", "PG": "en-PG", "PH": "en-PH", "PK": "ur-PK",
    "PM": "fr-PM", "PN": "en-PN", "PR": "es-PR", "PS": "ar-PS",
    "PW": "en-PW", "PY": "es-PY", "QA": "ar-QA", "RE": "fr-RE",
    "RO": "ro-RO", "RS": "sr-RS", "RW": "fr-RW", "SA": "ar-SA",
    "SB": "en-SB", "SC": "en-SC", "SD": "ar-SD", "SH": "en-SH",
    "SI": "sl-SI", "SJ": "nb-SJ", "SK": "sk-SK", "SL": "en-SL",
    "SM": "it-SM", "SN": "fr-SN", "SO": "so-SO", "SR": "nl-SR",
    "SS": "en-SS", "ST": "pt-ST", "SV": "es-SV", "SX": "nl-SX",
    "SY": "ar-SY", "SZ": "en-SZ", "TC": "en-TC", "TD": "fr-TD",
    "TG": "fr-TG", "TH": "th-TH", "TJ": "tg-TJ", "TK": "en-TK",
    "TL": "pt-TL", "TM": "tk-TM", "TN": "ar-TN", "TO": "en-TO",
    "TT": "en-TT", "TV": "en-TV", "TZ": "sw-TZ", "UG": "en-UG",
    "UY": "es-UY", "UZ": "uz-UZ", "VA": "it-VA", "VC": "en-VC",
    "VE": "es-VE", "VG": "en-VG", "VI": "en-VI", "VN": "vi-VN",
    "VU": "fr-VU", "WF": "fr-WF", "WS": "en-WS", "YE": "ar-YE",
    "YT": "fr-YT", "ZA": "en-ZA", "ZM": "en-ZM",
}


# Codes that mean "NO COUNTRY WAS DETERMINED" rather than naming one. Two alpha
# characters, so `_validate_geo` (proxy_checker.py) keeps them, but they are not
# a place and no table will ever hold a row for one however far coverage widens.
#
# THIS IS WHY `_locale_for("")` AND `_locale_for("ZZ")` ANSWER IDENTICALLY, and
# the identity is deliberate rather than incidental. They are the SAME input —
# "no country" — and that is a DIFFERENT input from "a real country we have no
# row for". Only the second is the contradiction this module's refusal exists
# for; answering en-US for the first is the direct path's settled policy (#218).
#
# ⚠️ DELIBERATELY NARROW — ``ZZ`` ONLY, not the whole ISO 3166-1 user-assigned
# space (AA, QM-QZ, XA-XZ). The wider set was tried and is WRONG, for a reason
# worth recording: ``XK`` lives in that space and is the de facto code for
# KOSOVO, which real exits genuinely report. Treating it as "no country" would
# silently answer ``en-US`` for a real Balkan exit — the precise defect this
# change removes, reintroduced through the exemption meant to preserve AC4.
# Every user-assigned code except ``ZZ`` therefore takes the ordinary refusal,
# which is also what ``_timezone_for`` already does for all of them.
#
# ``ZZ`` is the one code whose "not a country" reading is unambiguous, and it is
# the one the shipped suite pins. It is written down here as a fact about the
# STANDARD, not read from any OS-provided database.
#
# THE ONE PLACE THE TWO HALVES DO NOT AGREE, stated plainly rather than hidden:
# ``_locale_for("ZZ")`` answers while ``_timezone_for("ZZ")`` refuses. That is
# not the defect — the halves agree about every real COUNTRY, which is what the
# invariant claims. ``ZZ`` is not a country, and the asymmetry is required by
# two shipped tests that are both correct (``test_locale.py`` pins the en-US
# answer; ``test_tz.py`` pins the refusal).
_NO_COUNTRY_CODES = frozenset({"ZZ"})


def _locale_for(country_code: str) -> str:
    """The locale a country implies — or a REFUSAL when this table cannot say.

    This used to be ``_COUNTRY_LOCALE.get(code, "en-US")``, and the removed
    default is the reason this docstring exists. Its sibling ``_timezone_for``
    RAISES for a country with no row, so the two halves of one derivation took
    **opposite positions on the same input**: the locale half invented an
    answer where the zone half refused to.

    The contradiction that produced is not hypothetical, and it does not need
    the zone half's refusal to fire — it needs the refusal NOT to fire.
    ``_proxy_timezone``'s FIRST branch returns the zone the check recorded and
    never consults ``_timezone_for`` at all, so an ordinary passing check
    against a Bulgarian exit shipped::

        lang = "en-US"          # this function, inventing
        tz   = "Europe/Sofia"   # branch 1, correct

    An American-English browser whose clock is in Sofia — ``Accept-Language:
    en-US`` beside ``Intl.DateTimeFormat().resolvedOptions().timeZone ===
    "Europe/Sofia"``, a pair checkers compare directly.

    ``"en-US"`` was never an approximation of a Bulgarian locale. It is a
    LEGITIMATE value in this table (``US`` maps to it), which makes it *worse*
    than ``_COUNTRY_TZ``'s old ``UTC`` sentinel rather than better: the sentinel
    was at least distinguishable from every real row, while this one is
    indistinguishable from a genuine US answer. Nothing downstream could tell
    "this profile is American" from "we do not know what this profile is".

    So: refuse, mirroring the sibling. Parity between two disagreeing halves is
    reached by raising the WEAKER side, never by lowering the stricter one —
    do NOT resolve a future disagreement here by giving ``_timezone_for`` its
    fallback back.

    THE NO-COUNTRY CASE IS UNCHANGED AND MUST STAY UNCHANGED. ``_locale_for("")``
    and ``_locale_for("ZZ")`` are two DIFFERENT inputs that happened to share one
    answer, and only the second is the defect. ``""`` means *no country was
    supplied* — the direct (no-proxy) path, where ``en-US`` is not a guess but a
    deliberate policy choice (see ``process.py``'s call sites: persona forces
    ``en-US`` so it never leaks the host locale, and pins a US zone so the two
    agree). ``"ZZ"`` means *a country we cannot answer for*, which is exactly
    what this refusal is. See ``LocaleUnderivableError``.

    ⚠️ USER-VISIBLE. A proxy whose exit is in a country outside the shared table
    set no longer launches. It is a REFUSAL, not a crash: it lands on the same
    fail-closed path as its three siblings, and it names the country so the
    operator can act.

    Raises:
        LocaleUnderivableError: no row for this country. A subclass of
            ``GeographyUnknownError``, so every fail-closed handler already
            written catches it unchanged.
    """
    code = (country_code or "").upper()
    if not code or code in _NO_COUNTRY_CODES:
        # NOT a lookup miss. No country was DETERMINED — either none was
        # supplied at all (the direct path's deliberate en-US policy) or the
        # code is one ISO 3166-1 reserves to mean "not a country". Neither is
        # the case this refusal is for, which is a REAL country we cannot
        # answer for. See _NO_COUNTRY_CODES.
        return "en-US"
    locale = _COUNTRY_LOCALE.get(code)
    if locale:
        return locale
    raise LocaleUnderivableError(
        f"no locale is known for country {code!r}: refusing to fall back to "
        "en-US, which would declare an American-English browser beside the "
        "exit's own non-US clock and is exactly what scanners flag as a "
        f"spoofed location. Add a {code!r} row to _COUNTRY_LOCALE and the "
        "matching _COUNTRY_TZ row (launch_policy.py) to resolve it"
    )


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
    # Added to restore correspondence with _COUNTRY_LOCALE, which has carried
    # "TW": "zh-TW" since it was written. TW was the ONLY country present in one
    # table and absent from the other, and that asymmetry is what produced a
    # profile declaring a Taiwanese locale beside a UTC clock.
    #
    # Recorded knowingly rather than by omission: listing TW as a country row is
    # politically sensitive. The product already took that position when the
    # locale row shipped — this only makes the two tables agree about a decision
    # already made, and it makes no new claim. Leaving it out was not neutrality,
    # it was an incoherent profile.
    "TW": "Asia/Taipei",
    # --- widened shared set (PS-240). The SAME country codes added to
    # --- _COUNTRY_LOCALE above; neither table may gain a code alone.
    #
    # ONE PRIMARY ZONE per country, following the convention the original rows
    # already set for US / RU / BR / AU. This table is a FALLBACK, consulted
    # only when the check recorded no zone at all (_proxy_timezone branch 2), so
    # a primary-zone answer is a degraded-but-COHERENT one — which is what the
    # table already was before these rows.
    "AD": "Europe/Andorra", "AE": "Asia/Dubai", "AF": "Asia/Kabul",
    "AG": "America/Antigua", "AI": "America/Anguilla",
    "AL": "Europe/Tirane", "AM": "Asia/Yerevan", "AO": "Africa/Luanda",
    "AR": "America/Argentina/Buenos_Aires", "AS": "Pacific/Pago_Pago",
    "AW": "America/Aruba", "AX": "Europe/Mariehamn", "AZ": "Asia/Baku",
    "BA": "Europe/Sarajevo", "BB": "America/Barbados", "BD": "Asia/Dhaka",
    "BF": "Africa/Ouagadougou", "BG": "Europe/Sofia", "BH": "Asia/Bahrain",
    "BI": "Africa/Bujumbura", "BJ": "Africa/Porto-Novo",
    "BL": "America/St_Barthelemy", "BM": "Atlantic/Bermuda",
    "BN": "Asia/Brunei", "BO": "America/La_Paz",
    "BQ": "America/Kralendijk", "BS": "America/Nassau",
    "BT": "Asia/Thimphu", "BW": "Africa/Gaborone", "BY": "Europe/Minsk",
    "BZ": "America/Belize", "CC": "Indian/Cocos", "CD": "Africa/Kinshasa",
    "CF": "Africa/Bangui", "CG": "Africa/Brazzaville",
    "CI": "Africa/Abidjan", "CK": "Pacific/Rarotonga",
    "CL": "America/Santiago", "CM": "Africa/Douala",
    "CO": "America/Bogota", "CR": "America/Costa_Rica",
    "CU": "America/Havana", "CV": "Atlantic/Cape_Verde",
    "CW": "America/Curacao", "CX": "Indian/Christmas",
    "CY": "Asia/Nicosia", "CZ": "Europe/Prague", "DJ": "Africa/Djibouti",
    "DM": "America/Dominica", "DO": "America/Santo_Domingo",
    "DZ": "Africa/Algiers", "EC": "America/Guayaquil",
    "EE": "Europe/Tallinn", "EG": "Africa/Cairo", "EH": "Africa/El_Aaiun",
    "ER": "Africa/Asmara", "ET": "Africa/Addis_Ababa",
    "FJ": "Pacific/Fiji", "FK": "Atlantic/Stanley",
    "FM": "Pacific/Pohnpei", "FO": "Atlantic/Faroe",
    "GA": "Africa/Libreville", "GD": "America/Grenada",
    "GE": "Asia/Tbilisi", "GF": "America/Cayenne", "GG": "Europe/Guernsey",
    "GH": "Africa/Accra", "GI": "Europe/Gibraltar", "GL": "America/Nuuk",
    "GM": "Africa/Banjul", "GN": "Africa/Conakry",
    "GP": "America/Guadeloupe", "GQ": "Africa/Malabo",
    "GR": "Europe/Athens", "GT": "America/Guatemala", "GU": "Pacific/Guam",
    "GW": "Africa/Bissau", "GY": "America/Guyana", "HK": "Asia/Hong_Kong",
    "HN": "America/Tegucigalpa", "HR": "Europe/Zagreb",
    "HT": "America/Port-au-Prince", "HU": "Europe/Budapest",
    "ID": "Asia/Jakarta", "IL": "Asia/Jerusalem",
    "IM": "Europe/Isle_of_Man", "IO": "Indian/Chagos",
    "IQ": "Asia/Baghdad", "IR": "Asia/Tehran", "IS": "Atlantic/Reykjavik",
    "JE": "Europe/Jersey", "JM": "America/Jamaica", "JO": "Asia/Amman",
    "KE": "Africa/Nairobi", "KG": "Asia/Bishkek", "KH": "Asia/Phnom_Penh",
    "KI": "Pacific/Tarawa", "KM": "Indian/Comoro",
    "KN": "America/St_Kitts", "KP": "Asia/Pyongyang", "KW": "Asia/Kuwait",
    "KY": "America/Cayman", "KZ": "Asia/Almaty", "LA": "Asia/Vientiane",
    "LB": "Asia/Beirut", "LC": "America/St_Lucia", "LI": "Europe/Vaduz",
    "LK": "Asia/Colombo", "LR": "Africa/Monrovia", "LS": "Africa/Maseru",
    "LT": "Europe/Vilnius", "LU": "Europe/Luxembourg", "LV": "Europe/Riga",
    "LY": "Africa/Tripoli", "MA": "Africa/Casablanca",
    "MC": "Europe/Monaco", "MD": "Europe/Chisinau",
    "ME": "Europe/Podgorica", "MF": "America/Marigot",
    "MG": "Indian/Antananarivo", "MH": "Pacific/Majuro",
    "MK": "Europe/Skopje", "ML": "Africa/Bamako", "MM": "Asia/Yangon",
    "MN": "Asia/Ulaanbaatar", "MO": "Asia/Macau", "MP": "Pacific/Saipan",
    "MQ": "America/Martinique", "MR": "Africa/Nouakchott",
    "MS": "America/Montserrat", "MT": "Europe/Malta",
    "MU": "Indian/Mauritius", "MV": "Indian/Maldives",
    "MW": "Africa/Blantyre", "MY": "Asia/Kuala_Lumpur",
    "MZ": "Africa/Maputo", "NA": "Africa/Windhoek", "NC": "Pacific/Noumea",
    "NE": "Africa/Niamey", "NF": "Pacific/Norfolk",
    "NI": "America/Managua", "NP": "Asia/Kathmandu", "NR": "Pacific/Nauru",
    "NU": "Pacific/Niue", "NZ": "Pacific/Auckland", "OM": "Asia/Muscat",
    "PA": "America/Panama", "PE": "America/Lima", "PF": "Pacific/Tahiti",
    "PG": "Pacific/Port_Moresby", "PH": "Asia/Manila",
    "PK": "Asia/Karachi", "PM": "America/Miquelon",
    "PN": "Pacific/Pitcairn", "PR": "America/Puerto_Rico",
    "PS": "Asia/Gaza", "PW": "Pacific/Palau", "PY": "America/Asuncion",
    "QA": "Asia/Qatar", "RE": "Indian/Reunion", "RO": "Europe/Bucharest",
    "RS": "Europe/Belgrade", "RW": "Africa/Kigali", "SA": "Asia/Riyadh",
    "SB": "Pacific/Guadalcanal", "SC": "Indian/Mahe",
    "SD": "Africa/Khartoum", "SH": "Atlantic/St_Helena",
    "SI": "Europe/Ljubljana", "SJ": "Arctic/Longyearbyen",
    "SK": "Europe/Bratislava", "SL": "Africa/Freetown",
    "SM": "Europe/San_Marino", "SN": "Africa/Dakar",
    "SO": "Africa/Mogadishu", "SR": "America/Paramaribo",
    "SS": "Africa/Juba", "ST": "Africa/Sao_Tome",
    "SV": "America/El_Salvador", "SX": "America/Lower_Princes",
    "SY": "Asia/Damascus", "SZ": "Africa/Mbabane",
    "TC": "America/Grand_Turk", "TD": "Africa/Ndjamena",
    "TG": "Africa/Lome", "TH": "Asia/Bangkok", "TJ": "Asia/Dushanbe",
    "TK": "Pacific/Fakaofo", "TL": "Asia/Dili", "TM": "Asia/Ashgabat",
    "TN": "Africa/Tunis", "TO": "Pacific/Tongatapu",
    "TT": "America/Port_of_Spain", "TV": "Pacific/Funafuti",
    "TZ": "Africa/Dar_es_Salaam", "UG": "Africa/Kampala",
    "UY": "America/Montevideo", "UZ": "Asia/Tashkent",
    "VA": "Europe/Vatican", "VC": "America/St_Vincent",
    "VE": "America/Caracas", "VG": "America/Tortola",
    "VI": "America/St_Thomas", "VN": "Asia/Ho_Chi_Minh",
    "VU": "Pacific/Efate", "WF": "Pacific/Wallis", "WS": "Pacific/Apia",
    "YE": "Asia/Aden", "YT": "Indian/Mayotte", "ZA": "Africa/Johannesburg",
    "ZM": "Africa/Lusaka",
}


def _timezone_for(country_code: str) -> str:
    """The zone a country implies — or a REFUSAL when this table cannot say.

    This used to be ``_COUNTRY_TZ.get(code, "UTC")``. That fallback is gone, and
    what it did is the reason: an exit in a country with no row here produced a
    profile reporting ``UTC``, silently, with no warning and no refusal.

    ``UTC`` was never an approximation. **No key in ``_COUNTRY_TZ`` maps to it**
    — assert that below if you doubt it — so it could only ever be a sentinel,
    one that publicly announced "unknown" inside a field an engine consumes as
    fact. That is the shape ``GeographyUnknownError`` already exists to make
    UNREPRESENTABLE, and this was a hole straight through it.

    The cost is stated eleven lines down in this file, about a different table:
    *"A concrete zone is what makes Firefox report a local time that matches the
    exit IP — otherwise a direct profile shows UTC and scanners flag a 'spoofed
    location'."* The module already knew. The fallback contradicted it.

    Worse than a missing value, it produced a CONTRADICTORY profile: TW is in
    ``_COUNTRY_LOCALE`` (``zh-TW``) and was not in ``_COUNTRY_TZ``, so a Taiwan
    exit declared a Taiwanese locale beside a UTC clock — two of our own tables
    answering one question differently, neither wrong alone.

    So: refuse. This is the fail-closed rule the charter states for a dropped
    proxy (*a stop, not a quiet fallback*) applied to the case it was written
    for — a profile that will not launch has disclosed nothing, while one that
    launches self-contradicting has disclosed that it is spoofed.

    ⚠️ USER-VISIBLE. A proxy whose exit is in an unlisted country, and whose geo
    response carried no usable zone, no longer launches. It is a REFUSAL, not a
    crash: it lands on the same fail-closed path as its two siblings, and it
    names the country so the operator can act. See ``TimezoneUnderivableError``.

    Raises:
        TimezoneUnderivableError: no row for this country. A subclass of
            ``GeographyUnknownError``, so every fail-closed handler already
            written catches it unchanged.
    """
    code = (country_code or "").upper()
    zone = _COUNTRY_TZ.get(code)
    if zone:
        return zone
    raise TimezoneUnderivableError(
        f"no timezone is known for country {code!r}: refusing to fall back to "
        "UTC, which would declare a clock that contradicts the exit's own "
        "country and is exactly what scanners flag as a spoofed location. "
        f"Add a {code!r} row to _COUNTRY_TZ (launch_policy.py) to resolve it"
        if code
        else "no country code was supplied, so no timezone can be derived: "
        "refusing to fall back to UTC rather than declare a clock nothing "
        "supports"
    )


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


def declared_timezone(proxy) -> str:
    """The zone the OPERATOR declared for this exit, or "" when none applies.

    Pure — two attribute reads and a string compare, no IO — so it is safe on a
    render as well as on a launch.

    GATED ON THE COUNTRY, and that gate is the whole reason this is a separate
    field rather than a write into ``proxy.timezone``. A declaration is a claim
    about ONE exit: "this proxy comes out in Romania, and Romania is
    Europe/Bucharest". A backconnect exit moves, and a zone declared for an RO
    exit says nothing true about a CZ one. So the declaration is consulted only
    while the country it was declared for is still the country on file, and
    retires itself the moment the exit moves — the launch then refuses again,
    which is the correct answer, instead of shipping a clock that contradicts
    the exit's own country.

    An unchecked proxy has no country on file, so nothing matches and this
    answers "" — a declaration cannot manufacture geography that was never
    measured.

    Read via ``getattr`` so the duck-typed proxy stand-ins on the launch-path
    tests (which model geography but not this field) answer too, exactly as
    ``proxy_indicator_state`` does.
    """
    zone = getattr(proxy, "manual_timezone", "") or ""
    if not zone:
        return ""
    declared_for = (getattr(proxy, "manual_timezone_country", "") or "").upper()
    country = (getattr(proxy, "country_code", "") or "").upper()
    if not declared_for or declared_for != country:
        return ""
    return zone


def _proxy_timezone(proxy) -> str:
    """The timezone for a proxied profile — or a REFUSAL when there isn't one.

    Three branches try to answer, in a precedence order that is load-bearing:
    the zone the check MEASURED, else the zone the operator DECLARED for this
    exit's country, else the zone implied by the checked country. The first
    always answers; the second answers only while the declaration still applies
    to the country on file (see ``declared_timezone``); the third answers only
    when the country has a ``_COUNTRY_TZ`` row, and REFUSES otherwise rather
    than substituting ``UTC`` (see ``_timezone_for`` above, and
    ``TimezoneUnderivableError``). All three describe the EXIT, which is the
    only location a proxied persona may claim — but only while the product
    still believes them. A stored zone whose most recent check FAILED is
    geography the product's own latest evidence disproves, so it is refused
    BEFORE any branch is consulted (see the guard below).

    ⚠️ MEASURED BEFORE DECLARED, and do not reorder it. The declaration exists
    because a check can report a country and no usable zone, not because an
    operator's typing is better evidence than a measurement. Consulting it
    first would let a value typed once outlive every later measurement that
    contradicts it — the stale-geography class this file already refuses in two
    other places.

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

    That refusal is escapable and the remedy is one click: check the proxy,
    which writes country_code + timezone (ProxyStore.mark_checked), and the
    profile then launches through the first branch declaring the exit's zone.

    ⚠️ THE RE-CHECK REMEDY IS SCOPED TO THAT CASE AND DOES NOT GENERALISE. It
    holds for the no-geography raise below and for the disproven guard above,
    and it does NOT hold for ``TimezoneUnderivableError`` from branch 3: there
    the check may already have PASSED and will keep passing, because what is
    missing is a ``_COUNTRY_TZ`` row rather than a check result. A re-check
    writes the country, reaches branch 3 again and refuses again — the re-check
    remedy LOOPS. That is why branch 2 exists: the operator DECLARES the exit's
    zone from the proxy dialog (``ProxyStore.set_manual_timezone``) and the
    profile launches. Adding a ``_COUNTRY_TZ`` row is still the fix for a
    country the product should know about; the declaration is the fix the
    operator can reach without shipping a build.

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
        TimezoneUnderivableError: the proxy carries a COUNTRY but the check
            recorded no usable zone, no operator declaration applies to that
            country, and the country has no ``_COUNTRY_TZ`` row — raised
            THROUGH this function from branch 3, via ``_timezone_for``. Also a
            subclass of GeographyUnknownError. Alone among the three, its
            remedy is NOT a re-check (see above).
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
    # SECOND, never first. A declaration is the operator's answer for a
    # question the CHECK could not answer, so a measured zone always wins: a
    # hand-typed value that outranked a fresh measurement would be a stale
    # clock with a longer life than the evidence contradicting it. Gated on the
    # country it was declared for — see ``declared_timezone``.
    declared = declared_timezone(proxy)
    if declared:
        return declared
    if proxy.country_code:
        return _timezone_for(proxy.country_code)
    raise GeographyUnknownError(
        "proxy has no geography (never successfully checked): refusing to "
        "derive a timezone from the host, which would disclose the operator's "
        "real location inside the tunnel"
    )


def proxy_is_checked_but_unlaunchable(proxy) -> bool:
    """A proxy whose check PASSED and whose profiles still cannot launch.

    This is the state PS-274 exists for: a check that succeeded, a country on
    file, a flag drawn — and every profile using it refused at launch because
    no zone can be derived for that country. The two surfaces disagreed and
    neither was wrong alone: the network page said "verified, checked just
    now", the profile card said "refused". The object that IS the problem, and
    the only one the operator can act on, rendered as healthy.

    ⚠️ DERIVED FROM THE LAUNCH PATH ITSELF, not re-stated. It CALLS
    ``_proxy_timezone`` and reports whether it refused, so the renderer cannot
    drift from the launcher — a new refusal branch, or a new way to answer
    (this ticket added one), is reflected here the day it lands with no second
    edit. Re-implementing the rule in the render layer is the specific mistake
    ``services/proxy/freshness.py`` was created to stop; this follows it.

    PURE. ``_proxy_timezone`` reads stored fields and ``time.time()`` and never
    probes — that is stated and tested at ``proxy_indicator_state`` — so this
    is safe to call on a render, once per row, with no socket and no disk.

    SCOPED TO A PASSING CHECK, deliberately. A proxy that never checked, or
    whose check failed, ALSO cannot launch, and both already have their own
    honest rendering (the placeholder and the ✕). Folding them in here would
    relabel two states the operator already reads correctly and bury the one
    they cannot currently see at all.
    """
    if getattr(proxy, "last_check_ok", None) is not True:
        return False
    try:
        _proxy_timezone(proxy)
    except GeographyUnknownError:
        # The parent — so GeographyDisprovenError and TimezoneUnderivableError
        # are both caught, exactly as every fail-closed handler in the product
        # catches them (errors.py explains why the hierarchy is that shape).
        return True
    return False
