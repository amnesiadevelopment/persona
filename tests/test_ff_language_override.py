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


def test_override_script_pins_intl_locale():
    # pixelscan reads the Intl "Internationalization API" locale from
    # Intl.DateTimeFormat().resolvedOptions().locale — firefox-17 leaves it at the
    # host default (uk-UA) even when navigator.language is pinned. The mismatch is
    # the masking tell. The script must pin every Intl formatter's resolved locale.
    js = il._language_override_script("en-US")
    assert "resolvedOptions" in js
    assert "DateTimeFormat" in js
    assert "NumberFormat" in js


def test_override_script_pins_date_locale_formatting():
    # Date.prototype.toString / toLocaleString on firefox-17 render the timezone
    # description in the host locale ("за північноамериканським…" on a Ukrainian
    # host) — another uk leak a scanner catches. The script must force Date's
    # default locale to the pinned one.
    js = il._language_override_script("en-US")
    assert "toLocaleString" in js or "DateTimeFormat" in js


def test_override_script_pins_number_currency_locale():
    # Number.prototype.toLocaleString uses the host ICU locale internally (not the
    # wrapped Intl.NumberFormat), so a currency NAME leaked in the host locale —
    # creepjs's lang/timezone check read "1 US dollar" (en-US) under a pl-PL
    # identity. The script must default Number/BigInt toLocaleString to the pin.
    js = il._language_override_script("pl-PL")
    assert "Number" in js
    assert "toLocaleString" in js
    # balanced braces/parens after the added block
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")


def test_override_script_carries_locale_into_workers():
    # add_init_script only runs in the page; Web Workers get a fresh Intl at the
    # host locale, so creepjs reads currency/list from a blob worker as en-US
    # under pl-PL. The script must wrap Worker/SharedWorker to carry a locale
    # patch into blob:/data: (via re-blob) and http(s) (via importScripts) workers.
    js = il._language_override_script("pl-PL")
    assert "self.Worker" in js
    assert "SharedWorker" in js
    assert "importScripts" in js
    assert "blob:|^data:" in js
    assert "XMLHttpRequest" in js
    # balanced after the added worker block
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")


def test_override_script_defines_both_getters():
    js = il._language_override_script("fr-FR")
    # defines both navigator getters via the shared def() helper
    assert "def('language'," in js
    assert "def('languages'," in js
    assert "defineProperty" in js
    # balanced braces/parens — no obvious syntax garbage
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
