import pytest

import src.services.browser.launch_policy as launch_policy
from src.services.browser.process import _locale_for, _proxy_timezone, _timezone_for
from src.services.proxy.errors import GeographyUnknownError


def test_locale_known():
    assert _locale_for("US") == "en-US"
    assert _locale_for("DE") == "de-DE"


def test_timezone_for_known_countries():
    assert _timezone_for("US") == "America/New_York"
    assert _timezone_for("DE") == "Europe/Berlin"
    assert _timezone_for("GB") == "Europe/London"
    assert _timezone_for("FR") == "Europe/Paris"
    assert _timezone_for("JP") == "Asia/Tokyo"


def test_timezone_unknown_falls_back_utc():
    assert _timezone_for("ZZ") == "UTC"
    assert _timezone_for("") == "UTC"


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
