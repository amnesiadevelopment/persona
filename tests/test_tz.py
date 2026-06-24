from src.services.browser.process import _locale_for, _timezone_for


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
