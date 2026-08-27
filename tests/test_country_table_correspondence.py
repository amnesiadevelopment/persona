"""The correspondence invariant between the two country tables, and the death of
the silent-UTC fallback.

Why this file exists at all: before it,
``grep -rn "_COUNTRY_TZ\\|_COUNTRY_LOCALE" tests/`` returned **zero files**.
Neither table was tested in either direction, so nothing stopped a future
half-populated row from landing — a country added to one table and forgotten in
the other, which is exactly how the defect this file was written for came to be.

The defect, stated once: ``_COUNTRY_LOCALE`` carried ``"TW": "zh-TW"`` while
``_COUNTRY_TZ`` had no ``TW`` row, so a Taiwan exit declared a Taiwanese locale
beside a UTC clock. Two of our own tables answering one question differently,
with neither wrong on its own — the same shape as the two-GPU contradiction.

The gate below is deliberately INDEPENDENT of how the missing-row case is
answered. It pinned the invariant when the answer was "return UTC" and it pins
it now that the answer is "refuse", because what it asserts is that the two
tables AGREE about which countries exist — not what either one returns.
"""

import pytest

from src.services.browser.launch_policy import (
    _COUNTRY_LOCALE,
    _COUNTRY_TZ,
    _locale_for,
    _timezone_for,
)
from src.services.proxy.errors import TimezoneUnderivableError

# Countries deliberately present in ``_COUNTRY_LOCALE`` but absent from
# ``_COUNTRY_TZ``, or vice versa.
#
# EMPTY, AND MEANT TO STAY THAT WAY. It exists so that adding a one-sided row is
# a VISIBLE, ARGUED act — you cannot land one without editing this list, and
# editing it means writing down why. An empty allowlist is the strong form of
# the invariant; a populated one is a documented exception, never an oversight.
#
# ``TW`` was the entry this list was created to hold, and it is not here: the
# whole point of the accompanying change is that a locale-without-a-zone country
# no longer produces a contradictory profile. If you are about to add a country
# here, the question to answer first is why the profile it produces is coherent.
_INTENTIONALLY_ASYMMETRIC: dict[str, str] = {}


def test_every_country_with_a_locale_also_has_a_timezone():
    """The direction the shipped defect ran in.

    A country in ``_COUNTRY_LOCALE`` but not ``_COUNTRY_TZ`` is the
    contradictory profile: the locale asserts a place, and the zone cannot back
    it. This is the assertion that would have failed on the TW row.
    """
    missing = set(_COUNTRY_LOCALE) - set(_COUNTRY_TZ) - set(_INTENTIONALLY_ASYMMETRIC)
    assert not missing, (
        f"{sorted(missing)} declare a locale but no timezone. Such a profile "
        "contradicts itself — it claims a country while its clock says "
        "otherwise, which is the 'spoofed location' tell launch_policy's own "
        "comment warns about. Add the _COUNTRY_TZ row, or record the country "
        "in _INTENTIONALLY_ASYMMETRIC with a reason."
    )


def test_every_country_with_a_timezone_also_has_a_locale():
    """The other direction, which has never yet been violated.

    Tested anyway, and that is the point of a correspondence gate: the tables
    were never held in correspondence in EITHER direction, so the fact that
    this one happens to be clean today is luck rather than a property. A
    zone-without-a-locale country would produce the mirror contradiction — a
    Japanese clock beside an en-US Accept-Language.
    """
    missing = set(_COUNTRY_TZ) - set(_COUNTRY_LOCALE) - set(_INTENTIONALLY_ASYMMETRIC)
    assert not missing, (
        f"{sorted(missing)} declare a timezone but no locale, so they fall back "
        "to en-US while their clock says otherwise — the same contradiction "
        "mirrored. Add the _COUNTRY_LOCALE row, or record the country in "
        "_INTENTIONALLY_ASYMMETRIC with a reason."
    )


def test_the_allowlist_cannot_silently_absorb_a_country_that_is_in_both_tables():
    """A guard on the guard.

    An allowlist that accumulates stale entries stops being a record of
    decisions and starts being a place to hide failures — and a stale entry
    would keep suppressing a real regression if that country were later removed
    from one table again. So an entry that is no longer asymmetric is itself an
    error.
    """
    stale = {
        c
        for c in _INTENTIONALLY_ASYMMETRIC
        if (c in _COUNTRY_LOCALE) == (c in _COUNTRY_TZ)
    }
    assert not stale, (
        f"{sorted(stale)} are listed as intentionally asymmetric but are now "
        "present in both tables (or neither). Remove the stale entries — an "
        "allowlist that outlives its reason silences the next real regression."
    )


def test_taiwan_specifically_is_no_longer_the_contradictory_case():
    """The instance the ticket was originally drafted about, pinned by name.

    The class is covered by the two correspondence tests above; this is here
    because TW is the row that actually shipped broken, and a named regression
    test for the concrete historical defect is worth more than a general one
    when someone is bisecting.
    """
    assert "TW" in _COUNTRY_LOCALE, "precondition: the locale row is what made TW contradictory"
    assert _locale_for("TW") == "zh-TW"
    assert _timezone_for("TW") == "Asia/Taipei", (
        "a TW exit must declare a Taiwanese clock beside its Taiwanese locale, "
        "not UTC"
    )


def test_both_tables_are_keyed_by_uppercase_two_letter_codes():
    """The lookups upper-case their input, so a lower-case or malformed key
    would be permanently unreachable — dead data that looks live."""
    for name, table in (("_COUNTRY_LOCALE", _COUNTRY_LOCALE), ("_COUNTRY_TZ", _COUNTRY_TZ)):
        for code in table:
            assert code == code.upper() and len(code) == 2 and code.isalpha(), (
                f"{name} key {code!r} is not an upper-case 2-letter ISO code, so "
                "the lookup can never reach it"
            )


def test_every_timezone_row_is_a_concrete_iana_zone_never_utc():
    """What the tables must not degrade back into.

    ``UTC`` is not a location — it is the value the removed fallback used to
    smuggle in to mean "unknown". If a row is ever added mapping a real country
    to UTC, the sentinel is back, this time indistinguishable from a real
    answer, and every refusal in the accompanying change becomes unreachable
    for that country.
    """
    for code, zone in _COUNTRY_TZ.items():
        assert "/" in zone, f"{code} maps to {zone!r}, which is not an IANA zone path"
        assert zone != "UTC", (
            f"{code} maps to UTC — that is the 'unknown' sentinel the refusal "
            "replaced, not a location"
        )


def test_a_country_in_neither_table_is_refused_rather_than_answered():
    """The residual population, and the honest bound on it.

    ~165 ISO countries are in NEITHER table. Those are not the contradictory
    case (they get en-US AND UTC — uniformly wrong rather than
    self-contradictory), but they are the population the refusal now stops.
    """
    assert "ZZ" not in _COUNTRY_LOCALE and "ZZ" not in _COUNTRY_TZ
    assert _locale_for("ZZ") == "en-US"
    with pytest.raises(TimezoneUnderivableError):
        _timezone_for("ZZ")
