"""PS-131: the wrappers the BOOTSTRAP installs must stringify as native, in
EVERY realm the page can reach — on Firefox.

THE DEFECT. `audio_ext.firefox_audio_init_script` passed the LEAF's cloak
(`_FIREFOX_NATIVE_WRAP`) and left the BOOTSTRAP's at its default,
`CHROMIUM_WORKER_CLOAK`. On an engine that loads no persona extension that
default is not merely unnecessary, it is the tell: it stamps `__pnaName` (an own
property no browser has, which nothing on Firefox reads) and installs no
`toString` cloak at all. Measured in the WINDOW realm: `Worker.toString()`
returned 2109 characters of raw patch source where every real engine returns
`[native code]` — the standard `worker_wrap.py` states in its own words at the
guard. One `toString()` call and a length check identifies the tool.

WHY THE WORKER REALM LOOKED CLEAN, AND WHY THAT WAS WORSE THAN A LEAK. PS-128
recorded the worker realm still returning `[native code]`, which is what made
this look like a one-realm defect. It was not a passing realm — it was a realm
the spoof never reached. `firefox_worker_cloak()` carries this engine's
WORKER-BODY DELIVERY (`blob_setup`/`blob_resolve`) as well as its cloak, and
without it the `blob:` worker body is fetched with a synchronous XHR that the
`connect-src` of the DEFAULT start page refuses (`_ensure_firefox_policies()`
pins DuckDuckGo). The wrapper's own `catch` then falls back to the ORIGINAL
`Worker`, so the worker ran UNSPOOFED and stringified natively because there was
nothing there to cloak. A false pass sitting on top of the audio collision
`audio.digest` exists to prevent.

So these seats verify BOTH realms, and each one is guarded against passing for
the wrong reason:

  * VACUITY — before a stringification is allowed to count, the realm must be
    shown to have been genuinely reached (the leaf's OBSERVABLE differs from the
    unpatched value, and the transported payload carries the leaf). Without this
    the native-form assertion passes hardest exactly when the spoof is missing,
    which is the specific way this defect hid.
  * COUNTERFACTUAL — the pre-fix cloak is run through the SAME probe and must go
    red ON THE STRINGIFICATION ITSELF, not on a precondition.

Everything here is an EXECUTION result. PS-11 catalogues six instances in this
project of a green test sitting on a real defect because it asserted on text the
code generated, and this ticket is where that trap is sharpest: the patch source
IS the artifact, so a substring assertion over it can be made to pass while the
page still reads the source.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.audio_ext import _audio_patch_js  # noqa: F401
from src.services.browser.audio_ext import (
    _FIREFOX_NATIVE_WRAP,
    firefox_audio_init_script,
)
from src.services.browser.webgl_ext import firefox_webgl_init_script
from src.services.browser.worker_wrap import CHROMIUM_WORKER_CLOAK

# The four wrappers `realm_bootstrap_js` installs, quite apart from the leaf's.
# Named here so a wrapper ADDED to that path without a cloak fails a seat rather
# than going unnoticed — `workerConstructor` was simply the one PS-128 happened
# to record, and it would be a coincidence if a mechanism installing four leaked
# through exactly one.
BOOTSTRAP_WRAPPERS = ("Worker", "SharedWorker", "contentWindow", "contentDocument")


# The two FURTHER wrappers this delivery path installs, which the four above do
# not cover. They come from `firefox_worker_cloak()`'s `_FIREFOX_BLOB_RETAIN_SETUP`
# (`worker_wrap.py`, `__bcloak(__wcou, "createObjectURL")` / `__bcloak(__wrv,
# "revokeObjectURL")`), so on Firefox this path installs SIX wrappers, not four.
#
# Kept as a separate constant rather than folded into BOOTSTRAP_WRAPPERS because
# they hang off `URL` rather than off the global, and so need their own read in
# STRINGIFY.
#
# WHY THEY ARE PINNED HERE AT ALL, given they already read native: this is the
# enumeration the ticket asked for ("enumerate what that path installs and state
# each one's position"), and PS-131 exists precisely because a mechanism that
# installs several wrappers was checked at the one probe that happened to be
# recorded. Covering four of six would reproduce that reasoning one level down.
# `worker_wrap.py` also names these two specifically as a repeat offence — "an
# uncloaked `URL.createObjectURL` stringifying as raw patch source is the
# identical class of tell that got round 3 rejected" — and a property the
# codebase flags in those terms with no test pinning it is one regression away
# from being live, silently, while the other four seats stay green.
DELIVERY_WRAPPERS = ("createObjectURL", "revokeObjectURL")


# The realms a detector can reach, in the order the page reaches them. `window`
# is where the leak was recorded; `worker` is the sibling that appeared to pass.
REALMS = ("window", "worker", "worker2")


_HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const SCRIPTS = cfg.scripts.map((p) => fs.readFileSync(p, "utf8"));

// Model the origin the profile actually opens on. `_ensure_firefox_policies()`
// pins DuckDuckGo, which sends `default-src 'none'` with no `blob:` in its
// connect-src — so a synchronous XHR against a blob: URL THROWS. This is the
// condition under which the un-delivered worker realm read `[native code]` off
// a wrapper that was never installed.
const CSP_REFUSES_BLOB_XHR = cfg.cspRefusesBlobXhr;

const BLOBS = new Map();
let minted = 0;

function flatten(parts) {
  return parts
    .map((p) => (p && p.__parts ? flatten(p.__parts) : String(p)))
    .join("");
}

function makeRealm() {
  const captured = [];
  // ⚠️ NO HOST INTRINSICS. `vm.createContext` already gives the new realm its
  // own Object/Function/Reflect/WeakMap/Float32Array; handing in the HOST's
  // would overwrite them and make every realm share one `Object`. That is not
  // cosmetic here: `realm_guard_js` keeps its per-module "already covered"
  // marker on `G.Object.__pnaRealm`, so a shared `Object` means the window
  // realm's marker makes the WORKER's leaf return early — the leaf never
  // applies, and the probe reports a vacuous realm against a correct fix.
  // (The same class of trap `native_mask_probe.py` records for `G.Function`.)
  const sandbox = {
    URL: {
      createObjectURL: (b) => {
        const u = "blob:pna-" + (++minted);
        BLOBS.set(u, flatten(b.__parts));
        captured.push(BLOBS.get(u));
        return u;
      },
      revokeObjectURL: () => {},
    },
    Blob: function Blob(parts) { this.__parts = parts; },
    Worker: function Worker(url) { this.url = url; },
    SharedWorker: function SharedWorker(url) { this.url = url; },
    XMLHttpRequest: function XMLHttpRequest() {
      const me = this;
      this.status = 0; this.responseText = "";
      this.open = function (m, u) { me.__u = u; };
      this.send = function () {
        if (CSP_REFUSES_BLOB_XHR) { throw new Error("NetworkError"); }
        if (BLOBS.has(me.__u)) { me.status = 200; me.responseText = BLOBS.get(me.__u); }
        else { me.status = 404; }
      };
    },
  };
  const ctx = vm.createContext(sandbox);
  // `self` must be the realm's own global, never the sandbox view: the sandbox
  // object carries no Function/Object of its own, so a cloak reading
  // `G.Function` would take its fail-soft path and this probe would report a
  // LEAK against a CORRECT fix. (The trap native_mask_probe.py records.)
  vm.runInContext(
    "var self = this; globalThis.self = globalThis;" +
    // The audio leaf's observable, so a realm can be shown to be genuinely
    // reached before its stringification is allowed to count.
    "function AudioBuffer() {}" +
    "AudioBuffer.prototype.getChannelData = function getChannelData() {" +
    "  var d = new Float32Array(8);" +
    "  for (var i = 0; i < d.length; i++) { d[i] = 0.25 + i / 128; }" +
    "  return d;" +
    "};" +
    "function AnalyserNode() {}" +
    "AnalyserNode.prototype.getFloatFrequencyData = function getFloatFrequencyData() {};" +
    "AnalyserNode.prototype.getByteFrequencyData = function getByteFrequencyData() {};" +
    // The two iframe accessors the bootstrap chains.
    "function HTMLIFrameElement(){}" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow'," +
    "  {configurable:true,get:function contentWindow(){return null;}});" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentDocument'," +
    "  {configurable:true,get:function contentDocument(){return null;}});" +
    "globalThis.HTMLIFrameElement = HTMLIFrameElement;",
    ctx);
  return { ctx, captured };
}

// What a MASKING detector reads, through the `.call` form — a per-function
// `.toString` override does not intercept this.
const STRINGIFY = "(function(){var r={};" +
  "['Worker','SharedWorker'].forEach(function(k){" +
  "  try { r[k] = self[k] === undefined ? 'absent'" +
  "        : Function.prototype.toString.call(self[k]); }" +
  "  catch (e) { r[k] = 'throws:' + e; }" +
  "});" +
  "var P = self.HTMLIFrameElement && self.HTMLIFrameElement.prototype;" +
  "['contentWindow','contentDocument'].forEach(function(p){" +
  "  try {" +
  "    var d = P && Object.getOwnPropertyDescriptor(P, p);" +
  "    r[p] = (d && d.get) ? Function.prototype.toString.call(d.get) : 'absent';" +
  "  } catch (e) { r[p] = 'throws:' + e; }" +
  "});" +
  // The two further wrappers firefox_worker_cloak()'s blob_setup installs. They
  // hang off URL rather than the global, hence the separate read. Note these
  // start life in this harness as the sandbox's OWN arrow functions, which
  // stringify as their own source — so an absent cloak reads as a leak here
  // exactly as it would on the engine, rather than silently reading native.
  "['createObjectURL','revokeObjectURL'].forEach(function(k){" +
  "  try { var f = self.URL && self.URL[k];" +
  "        r[k] = f === undefined ? 'absent'" +
  "             : Function.prototype.toString.call(f); }" +
  "  catch (e) { r[k] = 'throws:' + e; }" +
  "});" +
  // Own properties, so `__pnaName` (Chromium's marker, a tell on this engine)
  // is reported as well as the stringification.
  "r.__own = {};" +
  "['Worker','SharedWorker'].forEach(function(k){" +
  "  try { r.__own[k] = Object.getOwnPropertyNames(self[k]); } catch (e) {}" +
  "});" +
  "['createObjectURL','revokeObjectURL'].forEach(function(k){" +
  "  try { r.__own[k] = Object.getOwnPropertyNames(self.URL[k]); } catch (e) {}" +
  "});" +
  // The two iframe accessors are GETTERS, so the marker (if any) rides the get
  // function, not a global binding — `self['contentWindow']` is undefined and
  // reading own props off THAT silently reports nothing. Read the descriptor's
  // getter, which is the object `__bcloak`/`__pnaName` would actually stamp.
  "['contentWindow','contentDocument'].forEach(function(p){" +
  "  try {" +
  "    var d2 = P && Object.getOwnPropertyDescriptor(P, p);" +
  "    if (d2 && d2.get) { r.__own[p] = Object.getOwnPropertyNames(d2.get); }" +
  "  } catch (e) {}" +
  "});" +
  "return JSON.stringify(r);})()";

// The leaf's observable: the float samples a fingerprinter reads.
const OBSERVABLE = "(function(){var d = new AudioBuffer().getChannelData(0);" +
  "var out = []; for (var i = 0; i < d.length; i++) { out.push(d[i].toPrecision(9)); }" +
  "return out.join(',');})()";

function read(realm) {
  return {
    stringified: JSON.parse(vm.runInContext(STRINGIFY, realm.ctx)),
    observable: vm.runInContext(OBSERVABLE, realm.ctx),
  };
}

// An UNPATCHED realm, to compare the observable against. If a realm reads this
// value the leaf never arrived there, and its native-form reading is vacuous.
const pristine = read(makeRealm()).observable;

const out = { unpatched: pristine, realms: {} };

// --- window realm: the scripts as invisible_launch installs them ------------
const win = makeRealm();
for (const s of SCRIPTS) { vm.runInContext(s, win.ctx); }
out.realms.window = read(win);

// --- worker realm: the page mints a blob: worker, exactly as verify's
// `worker_expression` does. Whatever the wrapper handed the engine IS the
// worker's body; running it in a fresh realm makes that realm the worker.
function spawnFrom(realm) {
  vm.runInContext(
    "var u = URL.createObjectURL(new Blob(['/*original*/']," +
    "{type:'application/javascript'})); self.__w = new self.Worker(u);",
    realm.ctx);
  return realm.captured[realm.captured.length - 1];
}

const body1 = spawnFrom(win);
out.realms.window.payloadCarriesLeaf = /applyAudioPatch/.test(body1 || "");
const w1 = makeRealm();
if (body1) { vm.runInContext(body1, w1.ctx); }
out.realms.worker = read(w1);

const body2 = spawnFrom(w1);
out.realms.worker.payloadCarriesLeaf = /applyAudioPatch/.test(body2 || "");
const w2 = makeRealm();
if (body2) { vm.runInContext(body2, w2.ctx); }
out.realms.worker2 = read(w2);

console.log(JSON.stringify(out));
"""


def _firefox_scripts(tmp_path, *, cloak):
    """The Firefox init scripts, in the order `invisible_launch` installs them.

    ORDER IS LOAD-BEARING and it is why this takes both scripts rather than
    audio alone. `_install_spoof("webgl", ...)` runs at :3133 and the audio init
    script at :3157, so audio's wrapper chains ON TOP of webgl's — the outermost
    wrapper is the one a page stringifies. webgl's cloak is a closure WeakMap
    that never saw audio's wrapper, so an uncloaked audio wrapper leaks THROUGH
    a correctly cloaked webgl one. Testing audio in isolation would still catch
    this defect, but it would not pin the composition that produced the recorded
    reading.

    `cloak` selects the bootstrap cloak spliced into the AUDIO script, so the
    same probe can be pointed at the pre-fix state.
    """
    d = pathlib.Path(tmp_path) / "ff"
    d.mkdir(parents=True, exist_ok=True)

    if cloak is None:
        audio_js = firefox_audio_init_script(4242)
    else:
        # The PRE-FIX state, reconstructed through the real builder rather than
        # by editing text: the leaf keeps its Firefox cloak and the BOOTSTRAP
        # gets Chromium's, which is exactly what shipped.
        audio_js = (
            "(function(){"
            + _audio_patch_js(4242, _FIREFOX_NATIVE_WRAP, cloak)
            + "})();"
        )

    webgl = d / "webgl.js"
    webgl.write_text(firefox_webgl_init_script(4242), encoding="utf-8")
    audio = d / "audio.js"
    audio.write_text(audio_js, encoding="utf-8")
    return [webgl, audio]


def _probe(tmp_path, *, cloak=None, csp_refuses_blob_xhr=True):
    """Execute the real generated scripts and report what each realm READS.

    `csp_refuses_blob_xhr` defaults to True — the DEFAULT START PAGE's condition,
    which is where every Firefox profile already is the moment it opens.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the generated bootstrap")

    scripts = _firefox_scripts(tmp_path, cloak=cloak)
    work = pathlib.Path(tmp_path) / "probe"
    work.mkdir(parents=True, exist_ok=True)

    harness = work / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(
        json.dumps(
            {
                "scripts": [str(s) for s in scripts],
                "cspRefusesBlobXhr": bool(csp_refuses_blob_xhr),
            }
        ),
        encoding="utf-8",
    )

    out = subprocess.run(
        [node, str(harness), str(cfg)], capture_output=True, text=True, timeout=60
    , encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _assert_realm_was_reached(report, realm):
    """A realm's stringification only counts once the leaf is shown to be THERE.

    This is the guard the original defect needed and did not have. An unreached
    realm holds the UNTOUCHED built-in, which stringifies natively — so the
    masking assertion passes for precisely the opposite of the reason wanted:
    not "the cloak covered this realm" but "there was nothing here to cloak".
    That is not a hypothetical; it is what the worker realm was doing when
    PS-128 recorded it as passing.
    """
    got = report["realms"][realm]
    assert got["observable"] != report["unpatched"], (
        f"VACUOUS: the audio leaf never reached the {realm!r} realm (it reads "
        f"the unpatched value {report['unpatched']!r}). Every native-form "
        "assertion about this realm would pass on untouched built-ins and "
        "witness nothing — which is exactly the false pass PS-131 found."
    )


# --- the fix: every bootstrap wrapper reads native, in every realm ----------


@pytest.mark.parametrize("realm", REALMS)
@pytest.mark.parametrize("wrapper", BOOTSTRAP_WRAPPERS)
def test_bootstrap_wrapper_stringifies_as_native(tmp_path, realm, wrapper):
    """What a page gets when it stringifies the constructors this bootstrap
    installs: the native form, in the window realm and in its worker siblings.

    BOTH REALMS, not just the repaired one. The whole shape of this defect is
    one realm passing while its sibling leaked, so a fix confirmed only where it
    was applied reproduces the original error.
    """
    report = _probe(tmp_path)
    _assert_realm_was_reached(report, realm)

    read = report["realms"][realm]["stringified"][wrapper]
    assert "[native code]" in read, (
        f"a page in the {realm!r} realm that stringifies {wrapper} reads "
        f"{len(read)} characters of raw patch source instead of the native "
        f"form. Every real engine returns `[native code]`; this identifies the "
        f"tool in one toString() call. Read: {read[:120]!r}"
    )


@pytest.mark.parametrize("realm", REALMS)
@pytest.mark.parametrize("wrapper", DELIVERY_WRAPPERS)
def test_delivery_wrapper_stringifies_as_native(tmp_path, realm, wrapper):
    """The other two wrappers this path installs — `firefox_worker_cloak()`'s
    `URL.createObjectURL` / `URL.revokeObjectURL` — read native in every realm
    too. Six wrappers on this path, so six pinned, not the four that hang off
    the global.

    These PASS TODAY; the seat is the enumeration, not a repair. `worker_wrap.py`
    cloaks both through `__bcloak` at the point it installs them, and names them
    there as a known repeat offence ("an uncloaked `URL.createObjectURL`
    stringifying as raw patch source is the identical class of tell that got
    round 3 rejected"). A property flagged in those terms with nothing pinning it
    regresses silently — the four sibling seats would all stay green.

    IT CANNOT PASS VACUOUSLY, and for a sharper reason than its siblings can
    claim. The four global constructors have a genuine native built-in
    underneath, so an UNREACHED realm reads `[native code]` off the untouched
    original and the assertion passes for the wrong reason — which is the exact
    false pass this file's vacuity guard exists to catch. These two have no
    native original in this harness: they start as the sandbox's OWN arrow
    functions, which stringify as their own source. So a realm the delivery
    never reached reads an arrow here and goes RED, rather than passing quietly.
    `_assert_realm_was_reached` is still called, to keep every seat in this file
    reading the same way and to fail with the diagnostic rather than a confusing
    stringification mismatch.
    """
    report = _probe(tmp_path)
    _assert_realm_was_reached(report, realm)

    read = report["realms"][realm]["stringified"][wrapper]
    assert "[native code]" in read, (
        f"a page in the {realm!r} realm that stringifies URL.{wrapper} reads "
        f"{len(read)} characters of source instead of the native form. This is "
        f"the tell worker_wrap.py names at the point it installs this wrapper; "
        f"every real engine returns `[native code]`. Read: {read[:120]!r}"
    )


@pytest.mark.parametrize("realm", REALMS)
def test_no_bootstrap_wrapper_carries_the_chromium_marker(tmp_path, realm):
    """`__pnaName` is a bare own property no browser has, and on Firefox nothing
    reads it — the marker is not a hiding place there, it is a second,
    independent tell alongside the stringification.

    Covers all SIX wrappers: the probe reports `__own` for the two `URL` ones
    beside the global constructors, and this walks whatever it is given rather
    than a list of its own, so the delivery pair is checked here too.

    Pinned as ABSENCE, mirroring tests/test_ff_language_override.py.
    """
    report = _probe(tmp_path)
    _assert_realm_was_reached(report, realm)

    own = report["realms"][realm]["stringified"]["__own"]
    for name, props in own.items():
        assert "__pnaName" not in props, (
            f"{name} in the {realm!r} realm carries the Chromium marker "
            f"`__pnaName`, which no browser has and nothing on this engine "
            f"reads: {props!r}"
        )


def test_the_worker_realm_is_reached_under_the_default_start_pages_csp(tmp_path):
    """The other half of the false pass, pinned so it cannot come back quietly.

    The worker realm read `[native code]` before this fix because the audio leaf
    never arrived: its bootstrap had no `blob_resolve`, so the body was fetched
    with a sync XHR that DuckDuckGo's `default-src 'none'` refuses, and the
    wrapper fell back to the ORIGINAL Worker. A worker that runs UNSPOOFED is
    worse than the leak it was masking — `audio.digest` is the one probe the
    inventory grades INDEPENDENT, so two profiles collide there.

    This asserts the DELIVERY, on the origin the profile actually opens on.
    """
    report = _probe(tmp_path, csp_refuses_blob_xhr=True)
    assert report["realms"]["window"]["payloadCarriesLeaf"], (
        "the payload handed to the worker does not carry the audio leaf, so "
        "the worker runs unspoofed on the default start page"
    )
    _assert_realm_was_reached(report, "worker")
    _assert_realm_was_reached(report, "worker2")


# --- the counterfactual: reverting must go red ON THE STRINGIFICATION -------


@pytest.mark.parametrize("wrapper", ("Worker", "SharedWorker"))
def test_the_pre_fix_cloak_leaks_patch_source_to_the_window_realm(tmp_path, wrapper):
    """Reverting the fix turns the check red on the stringification itself.

    This is what binds the seats above to the mechanism rather than to code that
    happens to be green. The pre-fix state is reconstructed through the real
    builder — the leaf keeps its Firefox cloak, the BOOTSTRAP gets Chromium's —
    and the SAME probe is pointed at it.

    The window realm is asserted here rather than the worker one deliberately:
    pre-fix, the worker realm was never reached, so it reads native and could
    not witness the regression. That asymmetry IS the defect.
    """
    report = _probe(tmp_path, cloak=CHROMIUM_WORKER_CLOAK)
    # The realm is genuinely patched — this is a real leak, not an empty realm.
    _assert_realm_was_reached(report, "window")

    read = report["realms"]["window"]["stringified"][wrapper]
    assert "[native code]" not in read, (
        "the pre-fix cloak no longer leaks, so the seats above are no longer "
        "pinned to anything — this counterfactual has stopped witnessing the "
        "defect and must be re-grounded."
    )
    assert len(read) > 500, (
        f"expected the raw patch source a page could read off {wrapper}; got "
        f"{len(read)} characters"
    )
