import src.services.browser.process as process
from src.services.browser.process import _locale_for, _proxy_timezone, _timezone_for


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


def test_proxy_timezone_unchecked_falls_back_to_host_zone(monkeypatch):
    monkeypatch.setattr(process, "_host_timezone", lambda: "Europe/Kyiv")
    assert _proxy_timezone(_Proxy()) == "Europe/Kyiv"
