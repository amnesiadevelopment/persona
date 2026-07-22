"""firefox-17 applies the locale to the Accept-Language HEADER (via
intl.accept_languages) but NOT to navigator.language — that stayed at the host OS
locale (uk-UA on a Ukrainian Windows even with a US proxy). Header en-US + JS
uk-UA is an internal mismatch a scanner flags as masking. An init script pins
navigator.language/languages to the SAME locale the header already carries, so JS
matches the header.
"""
import re

import src.services.browser.invisible_launch as il


def test_override_script_pins_language_to_locale():
    js = il._language_override_script("en-US")
    assert "Navigator.prototype" in js
    assert '"en-US"' in js
    # the base language is also present for navigator.languages
    assert '"en"' in js


def test_override_script_derives_base_language():
    # a region tag yields [full, base]; a bare tag yields just itself
    js = il._language_override_script("de-DE")
    assert '["de-DE", "de"]' in js
    js2 = il._language_override_script("en")
    assert '["en"]' in js2


def test_override_script_empty_locale_is_noop():
    assert il._language_override_script("") == ""
    assert il._language_override_script(None) == ""


def test_override_script_defines_both_getters():
    js = il._language_override_script("fr-FR")
    # defines both navigator getters via the shared def() helper
    assert "def('language'," in js
    assert "def('languages'," in js
    assert "defineProperty" in js
    # balanced braces/parens — no obvious syntax garbage
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
