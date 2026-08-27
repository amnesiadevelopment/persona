import pytest

import src.services.browser.launch_policy as launch_policy
from src.services.browser.launch_policy import _COUNTRY_TZ
from src.services.browser.process import _locale_for, _proxy_timezone, _timezone_for
from src.services.proxy.errors import GeographyUnknownError, TimezoneUnderivableError


def test_locale_known():
    assert _locale_for("US") == "en-US"
    assert _locale_for("DE") == "de-DE"


def test_timezone_for_known_countries():
    assert _timezone_for("US") == "America/New_York"
    assert _timezone_for("DE") == "Europe/Berlin"
    assert _timezone_for("GB") == "Europe/London"
    assert _timezone_for("FR") == "Europe/Paris"
    assert _timezone_for("JP") == "Asia/Tokyo"


def test_timezone_for_an_unlisted_country_refuses_instead_of_answering_utc():
    """Was test_timezone_unknown_falls_back_utc, which asserted "UTC" was the
    RIGHT answer here. It was not — it was the defect, pinned.

    A country with no `_COUNTRY_TZ` row produced a profile reporting UTC,
    silently. That is not a degraded profile but a CONTRADICTORY one: the locale
    table declares the country while the clock declares UTC, which is precisely
    what launch_policy's own comment calls a "spoofed location" tell.

    `UTC` was never an approximation — see the sibling test below, which proves
    no key in the table maps to it — so it could only ever be a sentinel
    smuggled into a field an engine consumes as fact.
    """
    with pytest.raises(TimezoneUnderivableError):
        _timezone_for("ZZ")


def test_utc_is_not_a_legitimate_answer_for_any_country_in_the_table():
    """The discriminator that makes the refusal above correct rather than
    merely strict.

    If some country legitimately mapped to UTC, the old fallback would have
    been a defensible approximation for its neighbours. Nothing does — so the
    old return value was unreachable as a real answer and could only ever mean
    "unknown", in a string an engine ships as a real zone.
    """
    assert "UTC" not in _COUNTRY_TZ.values()


def test_timezone_for_refuses_an_empty_country_rather_than_answering_utc():
    with pytest.raises(TimezoneUnderivableError):
        _timezone_for("")


def test_the_refusal_names_the_country_so_an_operator_can_act_on_it():
    """A refusal nobody can diagnose is a worse product than a wrong timezone.
    The whole remedy is "which country is missing a row", so the message has to
    carry it — asserting the TYPE alone would let a generic sentence pass.

    ``NG`` (Nigeria), not ``TW``: TW now HAS a row, so using it here would
    assert nothing. Any country genuinely absent from the table works; this one
    is picked because it is populous and plausible as a real exit, i.e. the
    case an operator is actually likely to hit.
    """
    assert "NG" not in _COUNTRY_TZ, "precondition: pick a country the table lacks"
    with pytest.raises(TimezoneUnderivableError) as raised:
        _timezone_for("NG")
    assert "NG" in str(raised.value), (
        "the operator is told the type of failure but not which country caused "
        "it, so they cannot act on it"
    )


def test_an_underivable_timezone_is_caught_by_existing_fail_closed_handlers():
    """Subclassing is load-bearing, not cosmetic: every fail-closed handler
    already written says `except GeographyUnknownError`, and all of them must
    keep catching this without being touched."""
    assert issubclass(TimezoneUnderivableError, GeographyUnknownError)
    with pytest.raises(GeographyUnknownError):
        _timezone_for("ZZ")


class _Proxy:
    def __init__(self, timezone="", country_code=""):
        self.timezone = timezone
        self.country_code = country_code


def test_proxy_timezone_prefers_explicit_zone():
    assert _proxy_timezone(_Proxy(timezone="Asia/Tokyo", country_code="DE")) == "Asia/Tokyo"


def test_proxy_timezone_derives_from_country():
    assert _proxy_timezone(_Proxy(country_code="DE")) == "Europe/Berlin"


def test_proxy_timezone_refuses_when_the_proxy_has_no_geography(monkeypatch):
    # Was test_proxy_timezone_unchecked_falls_back_to_host_zone, which asserted
    # the host zone WAS the right answer here. That fallback declared the
    # operator's real timezone inside the tunnel — a real-location disclosure on
    # the very vector the proxy exists to close. When no geography is available
    # the answer is STOP, not a host-derived value.
    #
    # Patch on launch_policy, not process: _proxy_timezone lives there too and
    # resolves _host_timezone in its OWN namespace, so a patch on the process
    # re-export alias is silently bypassed (real host zone would be read). The
    # distinctive value is what the assertion would catch if the removed branch
    # were somehow still reached.
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "Europe/Kyiv")
    with pytest.raises(GeographyUnknownError):
        _proxy_timezone(_Proxy())
