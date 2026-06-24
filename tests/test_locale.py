from src.services.browser.process import _locale_for


def test_known_countries():
    assert _locale_for("CA") == "en-CA"
    assert _locale_for("US") == "en-US"
    assert _locale_for("DE") == "de-DE"
    assert _locale_for("ua") == "uk-UA"


def test_unknown_falls_back():
    assert _locale_for("ZZ") == "en-US"
    assert _locale_for("") == "en-US"
