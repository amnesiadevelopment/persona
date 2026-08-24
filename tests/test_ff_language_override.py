"""firefox-17 applies the locale to the Accept-Language HEADER (via
intl.accept_languages) but NOT to navigator.language — that stayed at the host OS
locale (uk-UA on a Ukrainian Windows even with a US proxy). Header en-US + JS
uk-UA is an internal mismatch a scanner flags as masking. An init script pins
navigator.language/languages to the SAME locale the header already carries, so JS
matches the header.
"""
import json
import re
import shutil
import subprocess

import pytest

import src.services.browser.invisible_launch as il


def test_override_script_pins_language_to_locale():
    js = il._language_override_script("en-US")
    assert "Navigator.prototype" in js
    assert '"en-US"' in js


def test_override_script_languages_mirrors_the_tags_the_header_actually_sends():
    """The requested locale actually reaches the JS side — the LITERAL half.

    PS-124: navigator.languages must mirror the Accept-Language header this
    launch path actually SENDS. The engine EXPANDS a region-qualified tag
    (invisible_core.prefs._accept_language: "de-DE" -> "de-DE, de") and builds
    the wire header from that pref, so the header carries BOTH tags and the JS
    side must too.

    ⚠️ CORRECTION (round 3). This test previously asserted the OPPOSITE
    (`["de-DE"]`, the single tag) on the stated premise that "Playwright's
    locale kwarg writes intl.accept_languages VERBATIM". That premise was never
    measured on the wire and is FALSE on firefox-20. Measured live through the
    real product launch path against a standalone capture server:

        profile de-DE  ->  wire "de-DE,de;q=0.9" on the top document, img,
                           script, XHR and fetch alike
                       ->  prefs.js: user_pref("intl.accept_languages",
                           "de-DE, de")

    so the single-tag pin advertised FEWER languages than the wire carried.

    THIS TEST IS LOAD-BEARING, not documentation, and the reason is worth
    stating because it is the opposite of the usual advice. Pinning a literal
    is exactly what catches the one failure the cross-channel binding test
    cannot see: both channels moving off the requested locale TOGETHER. That
    build is internally consistent, so `js_tags == header_tags` holds and the
    binding test passes — correctly. Measured: forcing both sides to "zz-ZZ"
    left both binding tests green and turned this one red.

    So do not "simplify" this away as redundant with
    test_the_header_and_navigator_languages_carry_the_SAME_tags. The two
    families cover different failure modes:

        this one     the value is the REQUESTED one
        binding one  the two channels AGREE with each other
    """
    assert '["de-DE", "de"]' in il._language_override_script("de-DE")
    assert '["en-US", "en"]' in il._language_override_script("en-US")
    # a bare tag has no base to add and stays single
    assert '["en"]' in il._language_override_script("en")


def test_override_script_advertises_every_tag_the_wire_carries():
    """The JS side omits no tag the header sends — the round-3 defect, stated
    as a property over several locales rather than as one literal.

    Complementary to the cross-channel binding test in the same way as the test
    directly above: this one constrains the SHAPE the JS side emits on its own,
    so it still fires on a build where the header was moved to match a wrong
    JS side. See that test's docstring for the measured matrix.
    """
    for locale, base in (("de-DE", "de"), ("uk-UA", "uk"), ("pl-PL", "pl")):
        js = il._language_override_script(locale)
        assert f'["{locale}", "{base}"]' in js
        # the single-tag shape is the round-1/2 inversion; it must not return
        assert f'["{locale}"]' not in js


# Sentinel for "the profile declares no locale key at all", which is a different
# case from declaring an empty one and exercises the default fallback.
_UNSET = object()


def _both_locale_channels_from_one_launch(monkeypatch, tmp_path, cfg_locale):
    """Drive the REAL product launch path once and return what each of the two
    locale channels actually carried.

    Both channels are emitted by ``_launch_and_watch`` from the same ``cfg``:
    the header through ``kwargs["locale"]`` handed to the engine (Playwright
    writes it verbatim into ``intl.accept_languages``), and the JS through
    ``_install_spoof("locale", _language_override_script(...))``, which lands on
    ``ctx.add_init_script``. Capturing them from ONE launch is the whole point —
    reading either alone is what let a header-side regression walk past the
    previous guard.

    ``cfg_locale`` is placed in the cfg exactly as a profile would; pass the
    sentinel ``_UNSET`` to omit the key and exercise the default.
    """
    captured: dict = {"engine_kwargs": None, "scripts": []}

    class FakeCtx:
        @property
        def pages(self):
            return []

        def add_init_script(self, script, *_a, **_k):
            captured["scripts"].append(script)

    class FakeEngine:
        def __init__(self, **kwargs):
            captured["engine_kwargs"] = kwargs

        def _default_context_kwargs(self):
            return {}

        def __enter__(self):
            return FakeCtx()

        def __exit__(self, *a):
            return False

    import os
    import signal
    import sys
    import types

    mod = types.ModuleType("invisible_playwright")
    mod.InvisiblePlaywright = FakeEngine
    monkeypatch.setitem(sys.modules, "invisible_playwright", mod)

    # Fork path, closed immediately: this test is about what the launch DECLARES,
    # so the window's whole lifetime is out of scope.
    monkeypatch.setattr(il, "_fork_close_watch", lambda d, closed, **k: {10})
    monkeypatch.setattr(
        il, "_kill_profile_firefox", lambda d, pids=None, rescan=True: None
    )
    monkeypatch.setattr(il.os, "_exit", lambda code: None)
    monkeypatch.setattr(il, "_raise_profile_window", lambda *a, **k: None)

    # profile_data_dir is supplied because _child refuses a cfg without it: it
    # could not pin the child's scratch inside the profile, and launching on
    # the host's shared temp dir in silence is the residue PS-129 closes. This
    # test is about the LOCALE the launch declares, so a refusal here would
    # stop it before it reached the engine kwargs it actually asserts on.
    cfg = {
        "profile_dir": str(tmp_path),
        "profile_data_dir": str(tmp_path),
        "profile_name": "t",
        "seed": 1,
    }
    if cfg_locale is not _UNSET:
        cfg["locale"] = cfg_locale

    old_term = signal.getsignal(signal.SIGTERM)
    # _child runs IN THIS PROCESS here rather than across a real fork, so its
    # scratch pin writes into pytest's own os.environ. Production never does
    # that — the fork path has separate memory and the thread path is guarded
    # precisely because it does not — so the temp vars are saved and restored
    # for the same reason SIGTERM is.
    old_temp = {k: os.environ.get(k) for k in ("TMPDIR", "TMP", "TEMP")}
    r, w = os.pipe()
    try:
        il._child(cfg, w)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        for _k, _v in old_temp.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
    os.read(r, 65536)
    os.close(r)

    # The header, read DOWNSTREAM OF THE ENGINE'S EXPANSION.
    #
    # ⚠️ This is the line that decided PS-124, and it used to read:
    #
    #     header = captured["engine_kwargs"]["locale"]
    #     header_tags = [t.split(";")[0].strip() for t in header.split(",") ...]
    #
    # i.e. it took the string persona hands TO the engine and split it. That is
    # a PREDICTION of the header, not the header. The engine EXPANDS the tag
    # (invisible_core.prefs._accept_language: "de-DE" -> "de-DE, de", written to
    # intl.accept_languages, from which the patched nsHttpHandler builds the
    # wire header), so both "channels" were derived from the same pre-expansion
    # string and agreed BY CONSTRUCTION while the live browser contradicted
    # itself. Three rounds and two audits passed over that.
    #
    # The diagnostic to apply here: *if the engine transformed this value on its
    # way out, would this measurement notice?* Splitting the kwarg: no. Running
    # the kwarg through the engine's own expansion: yes.
    #
    # So we hand the captured kwarg to the ENGINE'S OWN header builder and parse
    # the q-valued result the way a server would. Verified live on firefox-20
    # against a standalone capture server: profile de-DE puts "de-DE,de;q=0.9"
    # on the top document, img, script, XHR and fetch, and the launch writes
    # user_pref("intl.accept_languages", "de-DE, de").
    from invisible_core.prefs import _accept_language_header

    header = _accept_language_header(captured["engine_kwargs"]["locale"])
    header_tags = [t.split(";")[0].strip() for t in header.split(",") if t.strip()]

    # The JS, as it was actually registered on the context by this same launch.
    locale_scripts = [
        s for s in captured["scripts"]
        if "Navigator.prototype" in s and "languages" in s
    ]
    assert len(locale_scripts) == 1, (
        f"expected exactly one locale spoof registered, got "
        f"{len(locale_scripts)} — the rig is reading the wrong script"
    )
    match = re.search(r"LS=(\[.*?\])", locale_scripts[0])
    assert match, "no languages array found in the registered locale spoof"
    js_tags = json.loads(match.group(1))

    return header_tags, js_tags


def test_the_launch_path_carries_the_REQUESTED_locale_to_both_channels(
    monkeypatch, tmp_path
):
    """The literal half, taken ON THE LAUNCH PATH — the measured hole.

    The other literal-pinned tests in this file call
    ``_language_override_script()`` DIRECTLY, so they never traverse
    ``_launch_and_watch`` at all. That makes both families blind to the same
    mutation, and it is not hypothetical — measured, round 3:

        mutation                                    binding   direct literal   this
        JS pin -> [locale] (the round-1/2 inversion)  RED        RED           RED
        JS side alone -> zz-ZZ (channels diverge)     RED        RED           RED
        BOTH launch channels -> zz-ZZ (consistent)    green*     green**       RED

        *  correct: the two surfaces genuinely DO agree, and agreement is all
           the binding tests assert. Not a bug in them.
        ** NOT correct, and the gap this test exists to close: they never call
           the launch path, so a locale substituted at
           ``invisible_launch.py:2902`` / ``:3139`` cannot reach them however
           wrong it is.

    Both channels on the launch path independently read
    ``cfg.get("locale") or "en-US"`` — two separate reads, at two separate
    sites — so this also covers a fix applied to only one of the two fallbacks.

    Unlike the binding tests this one DOES pin a literal, deliberately: it is
    the family that catches both channels moving off the requested locale
    together, which is invisible to a comparison of the channels with each
    other by construction.
    """
    for cfg_locale, expected_first in (
        ("de-DE", "de-DE"), ("fr", "fr"), ("pt-BR", "pt-BR"),
    ):
        header_tags, js_tags = _both_locale_channels_from_one_launch(
            monkeypatch, tmp_path, cfg_locale
        )
        assert header_tags[0] == expected_first, (
            f"the launch sent header {header_tags} for a profile that "
            f"requested {cfg_locale!r}"
        )
        assert js_tags[0] == expected_first, (
            f"the launch pinned navigator.languages to {js_tags} for a profile "
            f"that requested {cfg_locale!r}"
        )

    # and the unset-locale profile really does land on the documented default,
    # rather than merely landing on the SAME wrong thing in both channels
    header_tags, js_tags = _both_locale_channels_from_one_launch(
        monkeypatch, tmp_path, _UNSET
    )
    assert header_tags[0] == "en-US", (
        f"an unset-locale profile defaulted its header to {header_tags}, "
        f"not the documented en-US"
    )
    assert js_tags[0] == "en-US", (
        f"an unset-locale profile defaulted navigator.languages to {js_tags}, "
        f"not the documented en-US"
    )


_UNSET = object()


def test_the_header_and_navigator_languages_carry_the_SAME_tags(
    monkeypatch, tmp_path
):
    """The invariant itself: the two channels agree with EACH OTHER.

    This is the guard PS-124 actually needs, and it deliberately contains no
    locale literal. The defect was never "the languages array has two entries" —
    two entries is correct when the header sends two. The defect was the two
    channels DISAGREEING, which is PS-119's shape one channel over.

    A literal-pinned assertion on ONE channel cannot catch that: it stays green
    on a regression introduced through the channel it does not read. Measured,
    not assumed — with the header widened to ``locale,base`` and the JS left at
    the single tag (PS-119's contradiction, live on the product launch path),
    every literal-pinned test in this file passed and only the two binding
    tests went red.

    WHAT THIS TEST DOES **NOT** CATCH, stated because the obvious reading of
    "cross-surface agreement" over-claims it. Pin BOTH channels to some locale
    nobody asked for and this test passes — correctly, because the surfaces do
    then agree, and agreement is the whole property it asserts. Measured:
    forcing both sides to ``zz-ZZ`` left both binding tests green and turned
    four literal-pinned tests red.

    So the two families are COMPLEMENTARY, not redundant, and neither is
    decoration:

        this test        catches the two channels DIVERGING, on either side
        the literal ones catch both channels moving off the requested locale
                         TOGETHER — which, being self-consistent, is invisible
                         here by construction

    Delete either family and one real failure mode stops being covered.
    """
    for cfg_locale in ("de-DE", "uk-UA", "fr", "pl-PL", "en-US", "pt-BR"):
        header_tags, js_tags = _both_locale_channels_from_one_launch(
            monkeypatch, tmp_path, cfg_locale
        )
        assert js_tags == header_tags, (
            f"locale {cfg_locale!r}: navigator.languages {js_tags} does not "
            f"match the Accept-Language tags actually sent {header_tags} — an "
            f"internal contradiction is what a scanner flags as masking"
        )


def test_the_two_channels_agree_for_a_profile_that_declares_NO_locale(
    monkeypatch, tmp_path
):
    """The unset-locale profile is the same invariant on the default path.

    It gets its own test because it is a genuinely different code path — both
    channels independently fall back through ``cfg.get("locale") or "en-US"``,
    so a fix applied to only one of the two fallbacks would leave the default
    profile contradicting itself while every explicit locale agreed. That is
    the majority of profiles, so the blast radius of missing it is universal
    rather than marginal.

    Still no literal: the two fallbacks are compared to each other, not to
    "en-US". A build that defaults both sides to some other tag together is
    consistent, and consistency is what this file is about.
    """
    header_tags, js_tags = _both_locale_channels_from_one_launch(
        monkeypatch, tmp_path, _UNSET
    )
    assert header_tags, "an unset-locale profile declared no header locale at all"
    assert js_tags == header_tags, (
        f"a profile with no declared locale sent header {header_tags} while "
        f"navigator.languages reported {js_tags} — the two fallbacks disagree"
    )


def test_engine_expansion_matches_local_fallback():
    """The local fallback in ``_engine_accept_language_tags`` must agree with
    the engine's own expansion, on every shape the engine handles.

    ``_engine_accept_language_tags`` calls ``invisible_core.prefs._accept_
    language`` and only falls back to a local re-implementation when that
    import fails. A fallback nobody compares is a fallback that rots: the day
    the engine changes the expansion (three tags, a different base rule, a
    script-subtag policy) the fallback would silently keep producing the old
    shape and re-open exactly the contradiction PS-124 closed — on the machines
    where the import fails, which are the ones nobody is watching.

    So this pins the two against each other rather than against a literal.
    ``zh_Hans-CN`` is in the list deliberately: it is the case a hand-rolled
    "split on the first dash" gets WRONG (correct is ``zh-Hans-CN`` + ``zh``),
    which is why the fallback mirrors the engine's rule instead of inventing
    one.
    """
    from invisible_core.prefs import _accept_language

    def _local(locale):
        lang = locale.replace("_", "-")
        base = lang.split("-")[0]
        expanded = f"{lang}, {base}" if base != lang else lang
        return [t.strip() for t in expanded.split(",") if t.strip()]

    for locale in ("de-DE", "en-US", "fr", "en", "uk-UA", "pt-BR",
                   "zh_Hans-CN", "zh-Hans-CN", "es_ES"):
        engine_tags = [t.strip() for t in _accept_language(locale).split(",")
                       if t.strip()]
        assert il._engine_accept_language_tags(locale) == engine_tags, (
            f"{locale!r}: helper disagrees with the engine's own expansion"
        )
        assert _local(locale) == engine_tags, (
            f"{locale!r}: the local FALLBACK has drifted from the engine's "
            f"expansion ({_local(locale)} vs {engine_tags}) — the fallback "
            f"would silently re-open PS-124 wherever the import fails"
        )


def test_the_js_pin_matches_the_engines_untouched_worker_realm_value():
    """The pin must equal what the engine natively reports in a WORKER realm.

    A second contradiction the round-1/2 single-tag pin produced, and one no
    previous round recorded. This script patches ``Navigator.prototype``, but a
    worker realm has ``WorkerNavigator`` — a different interface the pin's
    ``def()`` never touches — so the worker kept the engine's native value
    while the main realm reported the pinned one. Measured live on firefox-20
    under a host/profile mismatch (profile de-DE, host en-US):

        arm                    main realm        blob-worker realm
        [locale] (round 1-2)   ["de-DE"]         ["de-DE","de"]   *** page vs
                                                                  its own worker
        [locale, base] (now)   ["de-DE","de"]    ["de-DE","de"]   agree

    That native worker value is independent corroboration of the direction:
    it is the engine's own untouched answer, and it is ``[locale, base]``.

    Asserted here against the engine's expansion rather than a literal, because
    the engine is what decides it — see the module docstring's note on reading
    surfaces where the browser emits them.
    """
    from invisible_core.prefs import _accept_language

    for locale in ("de-DE", "en-US", "fr", "pt-BR"):
        native_worker_tags = [t.strip() for t in _accept_language(locale).split(",")
                              if t.strip()]
        js = il._language_override_script(locale)
        match = re.search(r"LS=(\[.*?\])", js)
        assert match, f"no languages array in the locale spoof for {locale!r}"
        assert json.loads(match.group(1)) == native_worker_tags, (
            f"{locale!r}: the main-realm pin would contradict the engine's "
            f"untouched worker-realm value {native_worker_tags}"
        )


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


# --- native cloak -----------------------------------------------------------
# native_ext.py is a Chromium MV3 extension and is loaded only from the Chromium
# launch path, so the "wrappers must read as native" cloak could not reach
# Firefox — which launches through invisible_launch.py with no persona extension
# at all. A page read Intl.DateTimeFormat.name === "Wrapped" (real:
# "DateTimeFormat"), Object.keys(Intl.DateTimeFormat) === ["wrapped",
# "supportedLocalesOf"] (real: []), and Intl.DateTimeFormat.wrapped handed back
# the REAL constructor — the host's true value one documented property read away.
#
# The native form is SPIDERMONKEY's, not the V8 one-liner native_ext.py emits.
# Firefox prints three lines with a four-space indent, and a native ACCESSOR
# stringifies WITHOUT the `get ` prefix its .name carries. Emitting V8's form here
# would trade the `.name === "Wrapped"` tell for an equally cheap toString tell
# over a wider surface — every override, since every override is cloaked. Both
# divergences are captured from a clean Firefox 151 with no init scripts, and
# test_cloak_matches_the_engines_own_native_shape re-derives them from the live
# engine so this cannot drift back to a hard-coded string.

_NATIVE_FORM = "() {\n    [native code]\n}"
# what node's OWN natives print — V8, for the passthrough assertion only
_V8_NATIVE_FORM = "() { [native code] }"

# every function the two builders install: probe key -> (.name, name in SOURCE).
# They differ exactly for accessors, which is the second SpiderMonkey divergence.
_EXPECTED = {
    "toString": ("toString", "toString"),
    "DateTimeFormat": ("DateTimeFormat", "DateTimeFormat"),
    "NumberFormat": ("NumberFormat", "NumberFormat"),
    "Collator": ("Collator", "Collator"),
    "supportedLocalesOf": ("supportedLocalesOf", "supportedLocalesOf"),
    "resolvedOptions": ("resolvedOptions", "resolvedOptions"),
    "toLocaleDateString": ("toLocaleDateString", "toLocaleDateString"),
    "dateToString": ("toString", "toString"),
    "toTimeString": ("toTimeString", "toTimeString"),
    "numberToLocaleString": ("toLocaleString", "toLocaleString"),
    "bigintToLocaleString": ("toLocaleString", "toLocaleString"),
    "Worker": ("Worker", "Worker"),
    "get language": ("get language", "language"),
    "get languages": ("get languages", "languages"),
    "get outerWidth": ("get outerWidth", "outerWidth"),
    "get outerHeight": ("get outerHeight", "outerHeight"),
}


def test_cloak_prelude_is_double_quoted_and_single_line():
    # the prelude is inlined verbatim into the single-quoted worker-payload
    # literal, so a single quote or a newline in it would break that string
    cloak = il._native_cloak_js()
    assert "'" not in cloak
    assert "\n" not in cloak
    assert "\\" not in cloak
    # and it must not disturb the balanced-count assertions above
    assert cloak.count("{") == cloak.count("}")
    assert cloak.count("(") == cloak.count(")")


def test_cloak_patches_function_prototype_tostring_by_chaining():
    cloak = il._native_cloak_js()
    assert "Function.prototype.toString=__ts" in cloak
    # chains onto whatever is already installed rather than guarding on a global
    assert "var __pts=Function.prototype.toString;" in cloak
    assert "__pts.apply(this,arguments)" in cloak
    # the patch itself must read as native — a detector stringifies
    # Function.prototype.toString to catch exactly this trick
    assert '__cloak(__ts,"toString");' in cloak
    # the registry is a closure WeakMap, not an own property on every wrapper
    assert "new WeakMap()" in cloak
    assert "__pnaName" not in cloak


def test_cloak_emits_the_spidermonkey_native_form_not_v8s():
    cloak = il._native_cloak_js()
    # three lines, four-space indent — SpiderMonkey. NOT native_ext.py's V8
    # one-liner, which is correct there only because it is a Chromium extension.
    assert '"function "+(n||"")+"() {"+__nl+"    [native code]"+__nl+"}"' in cloak
    assert '"() { [native code] }"' not in cloak
    # the newline is built with fromCharCode, never written as an escape: a "\n"
    # would be eaten by the OUTER literal when the prelude is inlined into the
    # single-quoted worker payload, putting a raw newline inside a double-quoted
    # string in the worker source — a SyntaxError.
    assert "var __nl=String.fromCharCode(10);" in cloak
    # __cloak takes the stringified name apart from the pinned .name, because a
    # native accessor reports "get language" but stringifies as "function
    # language()". Both accessor call sites must pass the bare property name.
    # __cloak takes the stringified name apart from the pinned .name, and a
    # FOURTH parameter `l` that pins .length. The arity pin is not cosmetic: a
    # wrapper written as function(locales, options) reports length 2 where every
    # native Intl constructor reports 0, which PS-119 measured as a live masking
    # tell. Asserted behaviourally too — see test_no_override_betrays_itself_by_arity.
    assert "var __cloak=function(f,n,s,l){" in cloak
    assert "__nm.set(f,s===undefined?n:s);" in cloak
    assert 'if(l!==undefined)Object.defineProperty(f,"length"' in cloak
    both = il._language_override_script("en-US") + il._outer_size_override_script()
    assert both.count("__cloak(()=>v,'get '+k,k)") == 2


def test_override_scripts_carry_the_cloak_into_every_realm():
    js = il._language_override_script("pl-PL")
    # page realm + the re-blobbed worker payload each need their own copy: a
    # worker is a separate realm with its own Function.prototype
    assert js.count("[native code]") == 2
    # the window-realm builder is separate and conditional — it needs one too
    assert il._outer_size_override_script().count("[native code]") == 1


def test_override_script_drops_the_wrapped_backdoor():
    js = il._language_override_script("en-US")
    assert ".wrapped" not in js
    assert "Wrapped.wrapped" not in js
    # the re-wrap guard the back-door existed to serve is replaced, not deleted
    assert "__om.get(ctor)||ctor" in js
    assert "__om.set(Wrapped,Orig)" in js


def test_override_script_adds_no_enumerable_own_property():
    js = il._language_override_script("en-US")
    # supportedLocalesOf was a plain assignment, which creates an ENUMERABLE own
    # property and is what put it into Object.keys(Intl.DateTimeFormat)
    assert "W.supportedLocalesOf=" not in js
    assert "Wrapped.supportedLocalesOf=" not in js
    assert "{value:slo,writable:true,configurable:true}" in js
    assert "{value:s,writable:true,configurable:true}" in js


def test_override_scripts_cloak_every_installed_function():
    js = il._language_override_script("en-US") + il._outer_size_override_script()
    # navigator + window accessors carry the accessor's own "get <prop>" name,
    # and stringify under the bare property name (SpiderMonkey drops the prefix)
    assert js.count("__cloak(()=>v,'get '+k,k)") == 2
    # Intl constructors, their supportedLocalesOf and resolvedOptions. Each
    # passes the ORIGINAL's .length as the 4th arg so the wrapper's arity
    # matches the native it replaces (PS-119) — read off the original rather
    # than written as a literal, so the pin cannot drift from what it imitates.
    assert "__cloak(Wrapped,Orig.name,undefined,Orig.length)" in js
    assert "__cloak(Orig.supportedLocalesOf.bind(Orig)" in js
    assert "Orig.supportedLocalesOf.length" in js
    assert "ro.call(this);o.locale=L;return o;},ro.name,undefined," in js
    # A native ctor's prototype is non-writable and its prototype.constructor
    # points back at the ctor. Both were measured divergences (PS-119).
    assert "{value:Orig.prototype,writable:false" in js
    assert "{value:Wrapped,writable:true,enumerable:false,configurable:true}" in js
    # Date.toLocale* / toString / toTimeString, Number/BigInt, Worker wrappers
    assert "locales===undefined?L:locales,options);}," in js
    assert "orig.name,undefined,orig.length)" in js
    assert "oTS.name" in js and "oTTS.name" in js
    assert "oTS.length" in js and "oTTS.length" in js
    assert "l===undefined?L:l,opt);},o.name,undefined," in js
    assert "return __cloak(W,Orig.name);" in js
    # worker realm: Intl ctors, supportedLocalesOf, Number/BigInt
    assert "__cloak(W,C.name,undefined,C.length);" in js
    assert "__cloak(C.supportedLocalesOf.bind(C)" in js


# --- behavioural: run the generated JS and read the observable surface -------

_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
globalThis.Navigator = function Navigator() {};
// NOT `globalThis.navigator = ...`. Node >=21 ships a built-in `navigator`
// defined as a getter-only accessor, and a sloppy-mode assignment over a
// getter-only property is a SILENT no-op — no throw, no warning. The stub
// would never be installed, `navigator.language` would return the host's real
// locale, and the behavioural assertions below would fail for a reason that
// has nothing to do with the code under test. defineProperty overwrites the
// accessor on every engine, so this harness reads the same on Node 20 and 24.
Object.defineProperty(globalThis, "navigator", {
  value: Object.create(Navigator.prototype),
  writable: true,
  configurable: true,
});
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.innerWidth = 1200;
globalThis.innerHeight = 800;
let workerBody = null;
globalThis.Blob = function (parts) { workerBody = parts[0]; };
globalThis.URL = { createObjectURL: () => "blob:stub" };
class StubWorker { constructor(u, o) {} }
Object.defineProperty(StubWorker, "name", { value: "Worker" });
globalThis.Worker = StubWorker;

const globalsBefore = new Set(Object.getOwnPropertyNames(globalThis));
eval(fs.readFileSync(process.argv[2], "utf8"));   // _language_override_script
eval(fs.readFileSync(process.argv[3], "utf8"));   // _outer_size_override_script
const newGlobals = Object.getOwnPropertyNames(globalThis)
  .filter((k) => !globalsBefore.has(k));

const T = Function.prototype.toString;
const rec = (fn) => [fn.name, T.call(fn)];
const get = (o, k) => Object.getOwnPropertyDescriptor(o, k).get;
const page = {
  "toString": rec(Function.prototype.toString),
  "DateTimeFormat": rec(Intl.DateTimeFormat),
  "NumberFormat": rec(Intl.NumberFormat),
  "Collator": rec(Intl.Collator),
  "supportedLocalesOf": rec(Intl.DateTimeFormat.supportedLocalesOf),
  "resolvedOptions": rec(Intl.DateTimeFormat.prototype.resolvedOptions),
  "toLocaleDateString": rec(Date.prototype.toLocaleDateString),
  "dateToString": rec(Date.prototype.toString),
  "toTimeString": rec(Date.prototype.toTimeString),
  "numberToLocaleString": rec(Number.prototype.toLocaleString),
  "bigintToLocaleString": rec(BigInt.prototype.toLocaleString),
  "Worker": rec(self.Worker),
  "get language": rec(get(Navigator.prototype, "language")),
  "get languages": rec(get(Navigator.prototype, "languages")),
  "get outerWidth": rec(get(window, "outerWidth")),
  "get outerHeight": rec(get(window, "outerHeight")),
};

// the re-blobbed worker payload is a SEPARATE realm — run it in a fresh one
new self.Worker("https://example.com/w.js");
const body = workerBody.replace(/\ntry\{importScripts[\s\S]*$/, "");
const ctx = vm.createContext({});
vm.runInContext(body, ctx);
const W = vm.runInContext(
  "({T: Function.prototype.toString, Intl: Intl, Number: Number, BigInt: BigInt})",
  ctx);
const wrec = (fn) => [fn.name, W.T.call(fn)];
const worker = {
  "toString": wrec(W.T),
  "NumberFormat": wrec(W.Intl.NumberFormat),
  "DateTimeFormat": wrec(W.Intl.DateTimeFormat),
  "supportedLocalesOf": wrec(W.Intl.NumberFormat.supportedLocalesOf),
  "numberToLocaleString": wrec(W.Number.prototype.toLocaleString),
  "bigintToLocaleString": wrec(W.BigInt.prototype.toLocaleString),
};

console.log(JSON.stringify({
  page: page,
  worker: worker,
  wrapped: Intl.DateTimeFormat.wrapped === undefined ? "undefined" : "PRESENT",
  keys: Object.keys(Intl.DateTimeFormat),
  symbols: Object.getOwnPropertySymbols(Intl.DateTimeFormat).length,
  workerKeys: vm.runInContext("Object.keys(Intl.NumberFormat)", ctx),
  newGlobals: newGlobals,
  values: {
    language: navigator.language,
    languages: navigator.languages,
    locale: new Intl.DateTimeFormat().resolvedOptions().locale,
    number: (1234.5).toLocaleString(),
    instanceOf: new Intl.DateTimeFormat() instanceof Intl.DateTimeFormat,
    supported: Intl.DateTimeFormat.supportedLocalesOf(["en-US"]),
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    workerLocale: vm.runInContext(
      "new Intl.NumberFormat().resolvedOptions().locale", ctx),
    workerNumber: vm.runInContext("(1234.5).toLocaleString()", ctx),
  },
  passthrough: {
    userFn: T.call(function foo(a) { return a; }),
    nativeFn: T.call(Array.prototype.map),
  },
}));
"""


@pytest.fixture(scope="module")
def cloak_probe(tmp_path_factory):
    """Run both generated init scripts and report what a page actually sees."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = tmp_path_factory.mktemp("cloak")
    (d / "lang.js").write_text(il._language_override_script("pl-PL"), encoding="utf-8")
    (d / "outer.js").write_text(il._outer_size_override_script(), encoding="utf-8")
    (d / "harness.js").write_text(_HARNESS, encoding="utf-8")
    out = subprocess.run(
        [node, str(d / "harness.js"), str(d / "lang.js"), str(d / "outer.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("realm", ["page", "worker"])
def test_every_override_reports_the_originals_name_and_native_form(cloak_probe, realm):
    # criteria 1, 2 and 4: every function and accessor the builders install
    # reports the ORIGINAL's name and stringifies as the native form — in the
    # page realm AND inside the re-blobbed worker payload — including the
    # Function.prototype.toString patch itself.
    for key, (name, text) in cloak_probe[realm].items():
        want_name, want_src = _EXPECTED[key]
        assert name == want_name, f"{realm}.{key} reports .name {name!r}"
        assert text == f"function {want_src}{_NATIVE_FORM}", f"{realm}.{key} -> {text!r}"


def test_wrapped_backdoor_is_gone_and_nothing_is_enumerable(cloak_probe):
    # criteria 3 and 5
    assert cloak_probe["wrapped"] == "undefined"
    assert cloak_probe["keys"] == []
    assert cloak_probe["symbols"] == 0
    assert cloak_probe["workerKeys"] == []
    # ...and no new fixed global name for a detector to probe for: the whole
    # registry is closure-scoped. Chromium reaches the same end by a different
    # route — since PS-68 its two cloak scripts CHAIN onto whatever
    # Function.prototype.toString is already installed rather than coordinating
    # through a shared `__pnaToStringPatched` global, so neither engine now
    # publishes a fixed name for this. Chromium still marks each wrapper with a
    # non-enumerable `__pnaName` own property, which this closure WeakMap does
    # not (the other of the two improvements, and its own port).
    assert sorted(cloak_probe["newGlobals"]) == ["outerHeight", "outerWidth"]


def test_reported_values_are_unchanged(cloak_probe):
    # criterion 6: the cloak changes how the overrides READ, never what they report
    v = cloak_probe["values"]
    assert v["language"] == "pl-PL"
    # PS-124 round 3: the tags the header ACTUALLY sends. The engine expands a
    # region-qualified tag (pl-PL -> "pl-PL, pl") and builds the wire header
    # from that pref, so both tags are on the wire and both belong here. This
    # asserted ["pl-PL"] until round 3, on a premise measured false — see
    # test_override_script_languages_mirrors_the_tags_the_header_actually_sends.
    assert v["languages"] == ["pl-PL", "pl"]
    assert v["locale"] == "pl-PL"
    assert v["workerLocale"] == "pl-PL"
    assert v["number"] == v["workerNumber"] != "1234.5"
    assert v["instanceOf"] is True
    assert v["supported"] == ["en-US"]
    assert v["outerWidth"] == 1200 + 14
    assert v["outerHeight"] == 800 + 91


def test_tostring_passthrough_is_intact_for_everything_else(cloak_probe):
    # an uncloaked function still stringifies to its real source, and a genuine
    # built-in still stringifies natively — the patch only answers for its own
    # WeakMap entries. This harness runs under node, so the untouched builtin
    # prints V8's form; the cloak deliberately prints SpiderMonkey's, which is
    # why the two constants differ here and must not be conflated.
    p = cloak_probe["passthrough"]
    assert "return a" in p["userFn"]
    assert p["nativeFn"] == f"function map{_V8_NATIVE_FORM}"


# --- the engine itself is the oracle ----------------------------------------
# Everything above pins the native form to a string this file also spells out, so
# the whole set stays green if the string is wrong for the target engine — which
# is exactly how the V8 one-liner reached a Firefox-only path in the first place.
# These tests never spell it out: they LEARN the shape from an untouched builtin
# in the running engine and require every override to match it. They are the only
# assertions here that can catch the template drifting to another engine's form.

_FF_PROBE = r"""
  const T = Function.prototype.toString;
  // the engine's own native shape, with the name factored out
  const norm = (s) => s.replace(/^function\s*[^(]*\(/, "function (");
  const srcName = (s) => ((s.match(/^function\s*([^(]*)\(/) || [0, ""])[1]).trim();
  const SHAPE = norm(T.call(Object.getPrototypeOf));
  const rows = {};
  const check = (label, fn, wantName, wantSrc) => {
    const s = T.call(fn);
    rows[label] = { name: fn.name, wantName: wantName, str: s,
                    shape: norm(s), src: srcName(s), wantSrc: wantSrc };
  };
"""

_FF_PAGE = "() => {" + _FF_PROBE + r"""
  const g = (o, k) => (Object.getOwnPropertyDescriptor(o, k) || {}).get;
  for (const k of ["Collator", "DateTimeFormat", "NumberFormat", "PluralRules",
                   "ListFormat", "RelativeTimeFormat", "DisplayNames", "Segmenter"]) {
    if (!Intl[k]) continue;
    check("Intl." + k, Intl[k], k, k);
    check("Intl." + k + ".supportedLocalesOf", Intl[k].supportedLocalesOf,
          "supportedLocalesOf", "supportedLocalesOf");
    check("Intl." + k + ".p.resolvedOptions", Intl[k].prototype.resolvedOptions,
          "resolvedOptions", "resolvedOptions");
  }
  for (const k of ["toLocaleString", "toLocaleDateString", "toLocaleTimeString",
                   "toString", "toTimeString"])
    check("Date.p." + k, Date.prototype[k], k, k);
  check("Number.p.toLocaleString", Number.prototype.toLocaleString,
        "toLocaleString", "toLocaleString");
  check("BigInt.p.toLocaleString", BigInt.prototype.toLocaleString,
        "toLocaleString", "toLocaleString");
  check("self.Worker", self.Worker, "Worker", "Worker");
  check("F.p.toString", Function.prototype.toString, "toString", "toString");
  for (const k of ["language", "languages"])
    check("get navigator." + k, g(Navigator.prototype, k), "get " + k, k);
  for (const k of ["outerWidth", "outerHeight"])
    check("get window." + k, g(window, k), "get " + k, k);

  // the sweep a scanner already runs: walk the realm, keep every function that
  // claims [native code] but whose shape differs from the engine's own
  const seen = new Set(); const tells = [];
  const walk = (o, path, depth) => {
    if (!o || seen.has(o) || depth > 2) return;
    seen.add(o);
    for (const k of Object.getOwnPropertyNames(o)) {
      const d = Object.getOwnPropertyDescriptor(o, k);
      if (!d) continue;
      for (const fn of [d.value, d.get, d.set]) {
        if (typeof fn !== "function") continue;
        let s; try { s = T.call(fn); } catch (e) { continue; }
        if (s.indexOf("[native code]") !== -1 && norm(s) !== SHAPE)
          tells.push(path + "." + k + " -> " + JSON.stringify(s));
      }
      if (depth < 2 && d.value && (typeof d.value === "object" ||
                                   typeof d.value === "function")) {
        try { walk(d.value, path + "." + k, depth + 1); } catch (e) {}
      }
    }
  };
  walk(globalThis, "", 0);
  return { shape: SHAPE, rows: rows, tells: tells };
}"""

_FF_WORKER = ("() => new Promise((res, rej) => {\n  const src = " + json.dumps(
    _FF_PROBE + r"""
  for (const k of ["Collator", "DateTimeFormat", "NumberFormat", "PluralRules",
                   "ListFormat", "RelativeTimeFormat", "DisplayNames", "Segmenter"]) {
    if (!Intl[k]) continue;
    check("Intl." + k, Intl[k], k, k);
    check("Intl." + k + ".supportedLocalesOf", Intl[k].supportedLocalesOf,
          "supportedLocalesOf", "supportedLocalesOf");
  }
  check("Number.p.toLocaleString", Number.prototype.toLocaleString,
        "toLocaleString", "toLocaleString");
  check("BigInt.p.toLocaleString", BigInt.prototype.toLocaleString,
        "toLocaleString", "toLocaleString");
  check("F.p.toString", Function.prototype.toString, "toString", "toString");
  postMessage(JSON.stringify({ shape: SHAPE, rows: rows, tells: [] }));
""") + ";\n"
    # built through the WRAPPED self.Worker, so this exercises the re-blob path
    "  const w = new Worker(URL.createObjectURL("
    "new Blob([src], {type: 'text/javascript'})));\n"
    "  w.onmessage = (e) => res(JSON.parse(e.data));\n"
    "  w.onerror = (e) => rej(new Error(e.message));\n})")


@pytest.fixture(scope="module")
def firefox_probe():
    """Run both init scripts in a REAL Firefox, in the real registration order."""
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="playwright not installed").sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch()
            try:
                ctx = browser.new_context(locale="pl-PL")
                # the order invisible_launch.py registers them in
                ctx.add_init_script(il._outer_size_override_script())
                ctx.add_init_script(il._language_override_script("pl-PL"))
                page = ctx.new_page()
                page.goto("data:text/html,<meta charset=utf-8><title>x</title>")
                return {"page": page.evaluate(_FF_PAGE),
                        "worker": page.evaluate(_FF_WORKER)}
            finally:
                browser.close()
    except Exception as exc:  # browser binary absent, no sandbox, no display...
        pytest.skip(f"firefox not runnable here: {exc}")


@pytest.mark.parametrize("realm", ["page", "worker"])
def test_cloak_matches_the_engines_own_native_shape(firefox_probe, realm):
    # criterion 2, against the ONLY oracle that counts: SpiderMonkey itself.
    # SHAPE is read off Object.getPrototypeOf at runtime, so a template written
    # for another engine fails here no matter what this file believes.
    probe = firefox_probe[realm]
    shape = probe["shape"]
    assert "[native code]" in shape
    bad = {k: v for k, v in probe["rows"].items()
           if v["shape"] != shape or v["src"] != v["wantSrc"]
           or v["name"] != v["wantName"]}
    assert not bad, json.dumps(bad, indent=1)
    assert len(probe["rows"]) >= 19


def test_no_function_in_the_realm_betrays_itself_by_shape(firefox_probe):
    # the generic scanner sweep: on main this returned exactly the 31 persona
    # overrides and nothing else — a 100%-precision detector list
    assert firefox_probe["page"]["tells"] == []


# --- PS-119: arity and ctor invariants -------------------------------------
#
# A wrapper written as `function(locales, options)` reports `.length === 2`
# where EVERY native Intl constructor reports 0, and nothing in the cloak
# touched `.length` before PS-119. That is a one-read, zero-false-positive
# masking tell over the whole patched surface — the same class as the
# `.name === "Wrapped"` tell the cloak already existed to close.
#
# These capture the natives BEFORE the scripts run and compare AFTER, in the
# same realm. So they cannot pass by agreeing with a literal this file made up:
# the engine's own values are the oracle.

_ARITY_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
globalThis.Navigator = function Navigator() {};
Object.defineProperty(globalThis, "navigator", {
  value: Object.create(Navigator.prototype), writable: true, configurable: true });
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.innerWidth = 1200; globalThis.innerHeight = 800;
globalThis.Blob = function (parts) {}; 
globalThis.URL = { createObjectURL: () => "blob:stub" };
class StubWorker { constructor(u, o) {} }
Object.defineProperty(StubWorker, "name", { value: "Worker" });
globalThis.Worker = StubWorker;

const INTL = ["DateTimeFormat","NumberFormat","Collator","PluralRules",
              "ListFormat","RelativeTimeFormat","DisplayNames","Segmenter"];
const DATE = ["toLocaleString","toLocaleDateString","toLocaleTimeString",
              "toString","toTimeString"];

const snap = () => {
  const o = {ctor:{}, slo:{}, ro:{}, date:{}, number:null,
             protoWritable:{}, ctorBack:{}};
  for (const k of INTL) {
    if (!Intl[k]) continue;
    o.ctor[k] = Intl[k].length;
    if (Intl[k].supportedLocalesOf) o.slo[k] = Intl[k].supportedLocalesOf.length;
    if (Intl[k].prototype && Intl[k].prototype.resolvedOptions)
      o.ro[k] = Intl[k].prototype.resolvedOptions.length;
    const d = Object.getOwnPropertyDescriptor(Intl[k], "prototype");
    o.protoWritable[k] = d ? d.writable : null;
    o.ctorBack[k] = Intl[k].prototype
      ? Intl[k].prototype.constructor === Intl[k] : null;
  }
  for (const m of DATE) o.date[m] = Date.prototype[m].length;
  o.number = Number.prototype.toLocaleString.length;
  return o;
};

const before = snap();
eval(fs.readFileSync(process.argv[2], "utf8"));
const after = snap();
console.log(JSON.stringify({before: before, after: after}));
"""


@pytest.fixture(scope="module")
def arity_probe(tmp_path_factory):
    """Native arities and ctor invariants, captured before AND after the patch."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = tmp_path_factory.mktemp("arity")
    (d / "lang.js").write_text(il._language_override_script("pl-PL"), encoding="utf-8")
    (d / "harness.js").write_text(_ARITY_HARNESS, encoding="utf-8")
    out = subprocess.run(
        [node, str(d / "harness.js"), str(d / "lang.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_no_override_betrays_itself_by_arity(arity_probe):
    """Every patched function reports the arity the native it replaced did.

    The engine's own pre-patch values are the oracle, so this cannot pass by
    matching a constant written here.
    """
    before, after = arity_probe["before"], arity_probe["after"]
    for group in ("ctor", "slo", "ro", "date"):
        assert after[group] == before[group], (
            f"{group}: arity changed under the layer "
            f"{before[group]!r} -> {after[group]!r}"
        )
    assert after["number"] == before["number"]


def test_the_arity_probe_can_actually_SEE_the_tell():
    """Guard the guard: the wrapper shape really does report 2 uncloaked.

    Without this, the test above could pass on a tree where the arity pin was
    removed AND the wrapper happened to be written with no declared parameters
    — i.e. it could pass for the wrong reason.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.run(
        [node, "-e",
         "const W=function(locales,options){};"
         "console.log(JSON.stringify({wrapper:W.length,"
         "native:Intl.DateTimeFormat.length}))"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["native"] == 0
    assert got["wrapper"] == 2, "the tell this guards is not reproducible"


def test_the_wrapped_intl_ctors_keep_the_native_prototype_invariants(arity_probe):
    """`prototype` non-writable and `prototype.constructor` pointing back.

    Both hold on every real browser and both broke under the plain assignment
    the wrapper used before PS-119 — measured, not theorised.
    """
    before, after = arity_probe["before"], arity_probe["after"]
    assert after["protoWritable"] == before["protoWritable"]
    assert after["ctorBack"] == before["ctorBack"]
    # and the natives really are non-writable / self-referential, so the
    # comparison above is not two identical wrong answers
    assert set(before["protoWritable"].values()) == {False}
    assert set(before["ctorBack"].values()) == {True}
