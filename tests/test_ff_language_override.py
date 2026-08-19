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


# --- native cloak -----------------------------------------------------------
# native_ext.py is a Chromium MV3 extension and is loaded only from the Chromium
# launch path, so the "wrappers must read as native" cloak could not reach
# Firefox — which launches through invisible_launch.py with no persona extension
# at all. A page read Intl.DateTimeFormat.name === "Wrapped" (real:
# "DateTimeFormat"), Object.keys(Intl.DateTimeFormat) === ["wrapped",
# "supportedLocalesOf"] (real: []), and Intl.DateTimeFormat.wrapped handed back
# the REAL constructor — the host's true value one documented property read away.

_NATIVE_FORM = "() { [native code] }"


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


def test_cloak_reproduces_the_chromium_native_form():
    # byte-identical to native_ext.py's template
    assert '"function "+(n||"")+"() { [native code] }"' in il._native_cloak_js()


def test_override_scripts_carry_the_cloak_into_every_realm():
    js = il._language_override_script("pl-PL")
    # page realm + the re-blobbed worker payload each need their own copy: a
    # worker is a separate realm with its own Function.prototype
    assert js.count(_NATIVE_FORM) == 2
    # the window-realm builder is separate and conditional — it needs one too
    assert il._outer_size_override_script().count(_NATIVE_FORM) == 1


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
    # navigator + window accessors carry the accessor's own "get <prop>" name
    assert js.count("__cloak(()=>v,'get '+k)") == 2
    # Intl constructors, their supportedLocalesOf and resolvedOptions
    assert "__cloak(Wrapped,Orig.name)" in js
    assert "__cloak(Orig.supportedLocalesOf.bind(Orig)" in js
    assert "ro.call(this);o.locale=L;return o;},ro.name)" in js
    # Date.toLocale* / toString / toTimeString, Number/BigInt, Worker wrappers
    assert "locales===undefined?L:locales,options);},orig.name)" in js
    assert "oTS.name" in js and "oTTS.name" in js
    assert "l===undefined?L:l,opt);},o.name)" in js
    assert "return __cloak(W,Orig.name);" in js
    # worker realm: Intl ctors, supportedLocalesOf, Number/BigInt
    assert "__cloak(W,C.name);" in js
    assert "__cloak(C.supportedLocalesOf.bind(C)" in js


# --- behavioural: run the generated JS and read the observable surface -------

_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
globalThis.Navigator = function Navigator() {};
globalThis.navigator = Object.create(Navigator.prototype);
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
    expected = {
        "dateToString": "toString",
        "numberToLocaleString": "toLocaleString",
        "bigintToLocaleString": "toLocaleString",
    }
    for key, (name, text) in cloak_probe[realm].items():
        want = expected.get(key, key)
        assert name == want, f"{realm}.{key} reports .name {name!r}"
        assert text == f"function {want}{_NATIVE_FORM}", f"{realm}.{key} -> {text!r}"


def test_wrapped_backdoor_is_gone_and_nothing_is_enumerable(cloak_probe):
    # criteria 3 and 5
    assert cloak_probe["wrapped"] == "undefined"
    assert cloak_probe["keys"] == []
    assert cloak_probe["symbols"] == 0
    assert cloak_probe["workerKeys"] == []
    # ...and no new fixed global name for a detector to probe for: the whole
    # registry is closure-scoped, unlike Chromium's __pnaToStringPatched flag
    assert sorted(cloak_probe["newGlobals"]) == ["outerHeight", "outerWidth"]


def test_reported_values_are_unchanged(cloak_probe):
    # criterion 6: the cloak changes how the overrides READ, never what they report
    v = cloak_probe["values"]
    assert v["language"] == "pl-PL"
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
    # WeakMap entries
    p = cloak_probe["passthrough"]
    assert "return a" in p["userFn"]
    assert p["nativeFn"] == f"function map{_NATIVE_FORM}"
