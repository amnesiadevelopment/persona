"""Runtime tests for the iOS ``webkit-3d`` canvas context alias (PS-23).

ASSERT ON BEHAVIOUR, NOT ON THE EMITTED TEXT. Every pool and block in these
extension modules is written into every generated file regardless of platform —
the OS is baked in as a value and branched on at RUNTIME — so a substring check
proves nothing about what a given profile actually does. That is the same trap
PS-12 documented and PS-22 re-documented. These tests run the generated script in
an isolated node realm against a stubbed canvas and ask what a PAGE sees.

The canvas stub reproduces the parts of Chromium's getContext that this patch
must not break, in particular the one-context-per-canvas rule: a canvas caches
its context and returns the SAME object for a repeat call, and returns null for
a request of a DIFFERENT kind once a context exists. Returning a fresh context on
a second call is itself anomalous, so the stub has to model this for the test to
be able to catch it.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.canvas_ctx_ext import build_canvas_ctx_extension
from src.services.browser.gpu_ext import build_gpu_extension
from tests.native_mask_probe import assert_reads_native

ALIAS = "webkit-3d"

# A canvas realm close enough to Chromium's that the invariants under test are
# real. `getContext` here is the NATIVE function the patch wraps: it knows the
# six names Chromium resolves (2d, webgl, experimental-webgl, webgl2,
# bitmaprenderer, webgpu) and — crucially — NOT webkit-3d, which is exactly the
# gap the extension exists to close.
_CANVAS_STUBS = r"""
function WebGLRenderingContext() {}
function WebGL2RenderingContext() {}
function CanvasRenderingContext2D() {}
function ImageBitmapRenderingContext() {}
function GPUCanvasContext() {}
for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
  C.prototype.getParameter = function getParameter() { return "HOST_VALUE_NOT_SPOOFED"; };
  C.prototype.getExtension = function getExtension() { return null; };
  C.prototype.getSupportedExtensions = function getSupportedExtensions() { return ["HOST_EXT"]; };
  C.prototype.getShaderPrecisionFormat = function getShaderPrecisionFormat() { return null; };
}

// Chromium's CanvasRenderingContext::RenderingAPIFromId resolves exactly these
// six names; anything else is kUnknown and getContext returns null.
const KINDS = {
  "2d": CanvasRenderingContext2D,
  "webgl": WebGLRenderingContext,
  "experimental-webgl": WebGLRenderingContext,
  "webgl2": WebGL2RenderingContext,
  "bitmaprenderer": ImageBitmapRenderingContext,
  "webgpu": GPUCanvasContext,
};

function HTMLCanvasElement() { this.__ctx = null; this.__kind = null; }
HTMLCanvasElement.prototype.getContext = function getContext(contextId, options) {
  const Ctor = KINDS[String(contextId)];
  if (!Ctor) return null;                       // unknown name -> null
  // One context per canvas: same kind returns the SAME object, a different
  // kind returns null (a canvas cannot hold two context types).
  if (this.__ctx) return this.__kind === Ctor ? this.__ctx : null;
  this.__ctx = new Ctor();
  this.__kind = Ctor;
  this.__ctx.__optionsSeen = options;           // so a test can prove forwarding
  return this.__ctx;
};

// OffscreenCanvas takes a WebIDL ENUM in both WebKit and Chromium, and that
// enum has never included webkit-3d — conversion throws a TypeError before any
// engine code runs. Modelled so a test can prove the patch leaves it alone.
const OFFSCREEN_KINDS = ["2d", "webgl", "webgl2", "bitmaprenderer", "webgpu"];
function OffscreenCanvas() {}
OffscreenCanvas.prototype.getContext = function getContext(contextType, options) {
  if (OFFSCREEN_KINDS.indexOf(String(contextType)) === -1) {
    throw new TypeError(
      "Failed to execute 'getContext' on 'OffscreenCanvas': The provided value '" +
      contextType + "' is not a valid enum value of type OffscreenRenderingContextType."
    );
  }
  return new (contextType === "webgl2" ? WebGL2RenderingContext : WebGLRenderingContext)();
};

globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.OffscreenCanvas = OffscreenCanvas;
globalThis.WebGLRenderingContext = WebGLRenderingContext;
globalThis.WebGL2RenderingContext = WebGL2RenderingContext;
globalThis.CanvasRenderingContext2D = CanvasRenderingContext2D;
globalThis.ImageBitmapRenderingContext = ImageBitmapRenderingContext;
globalThis.GPUCanvasContext = GPUCanvasContext;
"""

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  "globalThis.self = globalThis; globalThis.window = globalThis; globalThis.top = globalThis;",
  sandbox
);
vm.runInContext(cfg.stubs, sandbox, { filename: 'stubs.js' });
for (const p of cfg.scripts) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
}
const result = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: result }));
"""


def _run(tmp_path, os_type, probe, *, with_gpu=False, tag="", stubs=None):
    """Build the extension(s) for `os_type`, run them in a node realm, evaluate
    `probe` there and return its value.

    `stubs` overrides the canvas realm, which is how a test can hand the patch a
    NATIVE getContext that misbehaves (throws, or counts what it was given) —
    the plain-string-literal probes elsewhere in this file cannot see either.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    work = pathlib.Path(tmp_path) / f"probe{os_type}{tag}"
    work.mkdir(parents=True, exist_ok=True)

    scripts = []
    if with_gpu:
        # gpu_ext installs the identity PS-12 landed; the alias must carry it.
        gpu_dir = build_gpu_extension(1, os_type, str(work / "gpu"))
        scripts.append(str(pathlib.Path(gpu_dir) / "gpu.js"))
    cc_dir = build_canvas_ctx_extension(os_type, str(work / "cc"))
    scripts.append(str(pathlib.Path(cc_dir) / "canvas_ctx.js"))

    harness = work / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(
        json.dumps({"stubs": stubs or _CANVAS_STUBS, "scripts": scripts, "probe": probe}),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(harness), str(cfg)], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["result"]


# ---------------------------------------------------------------------------
# The gap itself
# ---------------------------------------------------------------------------


def test_ios_profile_gets_a_context_from_the_alias(tmp_path):
    # THE DEFECT: a real iPhone answers getContext('webkit-3d') with a working
    # WebGL context; without this extension a persona iOS profile answers null,
    # which is a one-line, zero-ambiguity proof the iPhone UA is false.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();"
        "var ctx=c.getContext('webkit-3d');"
        "return ctx===null?'null':(ctx instanceof WebGLRenderingContext?'webgl1':'other');})()",
    )
    assert got == "webgl1"


def test_alias_maps_to_webgl1_not_webgl2(tmp_path):
    # Settled from WebKit source, not from the capture's bold-webgl2 formatting:
    # HTMLCanvasElement::toWebGLVersion returns WebGL2 for exactly one string
    # ("webgl2") and WebGL1 for every other name isWebGLType accepts — including
    # webkit-3d. A WebGL2 context here would be the wrong context TYPE, carrying
    # the WebGL2 extension list and version strings a real device would not.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();var ctx=c.getContext('webkit-3d');"
        "return ctx instanceof WebGL2RenderingContext ? 'webgl2' : "
        "(ctx instanceof WebGLRenderingContext ? 'webgl1' : 'none');})()",
    )
    assert got == "webgl1"


@pytest.mark.parametrize("os_type", ["windows", "macos", "android", "linux"])
def test_non_ios_profiles_still_return_null(tmp_path, os_type):
    # SCOPE, and getting it wrong inverts the ticket. These profiles claim to be
    # CHROMIUM, and real Chromium has never supported webkit-3d. Answering it
    # here would manufacture a brand-new impossibility (a Chrome-on-Windows
    # profile answering a Safari-only alias) on EVERY profile — worse than the
    # tell being fixed. macOS included: persona's macOS profiles present
    # Chrome-on-macOS, not Safari. The alias is Safari's, not Apple's.
    got = _run(
        tmp_path, os_type,
        "(function(){var c=new HTMLCanvasElement();"
        "return c.getContext('webkit-3d')===null?'null':'context';})()",
    )
    assert got == "null"


# ---------------------------------------------------------------------------
# The alias is the SAME context, carrying the identity PS-12 installed
# ---------------------------------------------------------------------------


def test_alias_and_webgl_return_the_same_object(tmp_path):
    # They are the same context TYPE, so one canvas must yield one object. A
    # second, distinct context — or a null from the second call — is anomalous.
    # Asserted in BOTH orders: the alias must not be a special first-caller path.
    both = _run(
        tmp_path, "ios",
        "(function(){"
        "var a=new HTMLCanvasElement();"
        "var aliasFirst = a.getContext('webkit-3d')===a.getContext('webgl');"
        "var b=new HTMLCanvasElement();"
        "var webglFirst = b.getContext('webgl')===b.getContext('webkit-3d');"
        "return (aliasFirst?'1':'0')+(webglFirst?'1':'0');})()",
    )
    assert both == "11"


def test_repeat_alias_calls_return_the_same_object(tmp_path):
    # Returning a fresh context on the second call for the same name is itself
    # a tell — real engines cache one context per canvas.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();"
        "return c.getContext('webkit-3d')===c.getContext('webkit-3d');})()",
    )
    assert got is True


def test_alias_carries_the_ps12_ios_gpu_identity(tmp_path):
    # TERRITORY: the object obtained through the alias must carry the full
    # spoofed identity, not a bare context. Read the unmasked vendor/renderer
    # THROUGH the alias with gpu_ext also installed.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();var gl=c.getContext('webkit-3d');"
        "return [gl.getParameter(0x9245), gl.getParameter(0x9246),"
        " gl.getParameter(7938), gl.getParameter(3379),"
        " gl.getSupportedExtensions().indexOf('WEBGL_compressed_texture_pvrtc')>=0].join('|');})()",
        with_gpu=True,
    )
    vendor, renderer, version, max_texture, has_pvrtc = got.split("|")
    assert vendor == "Apple Inc."
    assert renderer == "Apple GPU"
    # The bare WebGL1 version string — not the "(OpenGL ES 2.0 Chromium)" form,
    # which would announce Chromium on a device where it cannot exist.
    assert version == "WebGL 1.0"
    assert max_texture == "16384"
    # A WebGL1 iOS extension list (pvrtc is in IOS_GL1_EXTS), proving the alias
    # landed on the WebGL1 context and inherited its per-context list.
    assert has_pvrtc == "true"


# ---------------------------------------------------------------------------
# The wrapper must not become its own tell
# ---------------------------------------------------------------------------


def test_patched_getcontext_reads_native(tmp_path):
    # getContext is one of the most-inspected functions in the browser. The
    # replacement must stringify as native under the .call form detectors use
    # (a per-function .toString override is bypassed by it). assert_reads_native
    # also runs the FALSIFICATION: without native_ext's patch the same probe
    # must NOT read native, so this witnesses the cloak rather than merely
    # executing green code.
    d = build_canvas_ctx_extension("ios", str(tmp_path / "cc"))
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "canvas_ctx.js"],
        _CANVAS_STUBS,
        "Function.prototype.toString.call(HTMLCanvasElement.prototype.getContext)",
        "getContext",
    )


def test_wrapper_does_not_hand_roll_a_tostring_override(tmp_path):
    # The .call form bypasses an own .toString, so hand-rolling one is both
    # ineffective and an extra tell. Use the shared marker mechanism instead.
    js = (pathlib.Path(build_canvas_ctx_extension("ios", str(tmp_path / "cc")))
          / "canvas_ctx.js").read_text(encoding="utf-8")
    assert "replacement.toString = function" not in js


def test_function_name_and_length_match_the_original(tmp_path):
    # Both are read by detectors; a wrapper reporting name "" or length 0 where
    # the platform reports "getContext"/1 is visible for free.
    got = _run(
        tmp_path, "ios",
        "HTMLCanvasElement.prototype.getContext.name + '|' + "
        "HTMLCanvasElement.prototype.getContext.length",
    )
    assert got == "getContext|2"


def test_property_descriptor_shape_is_preserved(tmp_path):
    # A method that became an own ENUMERABLE property where the platform has a
    # non-enumerable prototype method is visible to a for...in or a descriptor
    # read. Compare the patched descriptor against the pre-patch one.
    got = _run(
        tmp_path, "ios",
        "(function(){var d=Object.getOwnPropertyDescriptor("
        "HTMLCanvasElement.prototype,'getContext');"
        "return [d.writable,d.enumerable,d.configurable,"
        "Object.keys(HTMLCanvasElement.prototype).indexOf('getContext')].join('|');})()",
    )
    # The stub installs getContext as a plain assignment (writable, enumerable,
    # configurable) — whatever the original was, the patched one must match it.
    assert got == "true|true|true|0"


def test_descriptor_matches_a_non_enumerable_original(tmp_path):
    # The realistic platform case: a prototype method is non-enumerable. The
    # patch must copy THAT shape rather than a hardcoded one, so a for...in over
    # the prototype still does not reveal getContext.
    stubs = _CANVAS_STUBS + (
        "\nObject.defineProperty(HTMLCanvasElement.prototype,'getContext',"
        "{value:HTMLCanvasElement.prototype.getContext,"
        "writable:true,enumerable:false,configurable:true});\n"
    )
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    work = pathlib.Path(tmp_path) / "nonenum"
    work.mkdir(parents=True, exist_ok=True)
    cc = build_canvas_ctx_extension("ios", str(work / "cc"))
    harness = work / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(json.dumps({
        "stubs": stubs,
        "scripts": [str(pathlib.Path(cc) / "canvas_ctx.js")],
        "probe": "(function(){var d=Object.getOwnPropertyDescriptor("
                 "HTMLCanvasElement.prototype,'getContext');"
                 "var seen=false; for (var k in HTMLCanvasElement.prototype)"
                 " if (k==='getContext') seen=true;"
                 "return [d.enumerable,d.writable,d.configurable,seen,"
                 "new HTMLCanvasElement().getContext('webkit-3d')!==null].join('|');})()",
    }), encoding="utf-8")
    out = subprocess.run([node, str(harness), str(cfg)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)["result"]
    # non-enumerable preserved, invisible to for...in, and the alias still works
    assert result == "false|true|true|false|true"


# ---------------------------------------------------------------------------
# Everything that already worked must keep working
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2d", "CanvasRenderingContext2D"),
        ("webgl", "WebGLRenderingContext"),
        ("experimental-webgl", "WebGLRenderingContext"),
        ("webgl2", "WebGL2RenderingContext"),
        ("bitmaprenderer", "ImageBitmapRenderingContext"),
        ("webgpu", "GPUCanvasContext"),
    ],
)
def test_existing_context_types_are_untouched(tmp_path, name, expected):
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();"
        "var x=c.getContext(%r);"
        "return x===null?'null':x.constructor.name;})()" % (name,),
        tag=name.replace("-", ""),
    )
    assert got == expected


@pytest.mark.parametrize("name", ["", "nope", "webgl3", "WEBKIT-3D", "3d"])
def test_unknown_names_still_return_null(tmp_path, name):
    # Including the wrong-case form: context names are case-sensitive, so
    # 'WEBKIT-3D' must NOT be aliased — matching it would be a fresh anomaly.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();"
        "return c.getContext(%r)===null?'null':'context';})()" % (name,),
        tag="u" + (name or "empty"),
    )
    assert got == "null"


def test_options_argument_is_forwarded_through_the_alias(tmp_path):
    # The context-creation attributes object must still reach the engine — both
    # on the aliased call and on an ordinary one.
    got = _run(
        tmp_path, "ios",
        "(function(){"
        "var a=new HTMLCanvasElement();"
        "var x=a.getContext('webkit-3d',{alpha:false,antialias:true});"
        "var b=new HTMLCanvasElement();"
        "var y=b.getContext('webgl',{alpha:false});"
        "return [x.__optionsSeen&&x.__optionsSeen.alpha===false,"
        "x.__optionsSeen&&x.__optionsSeen.antialias===true,"
        "y.__optionsSeen&&y.__optionsSeen.alpha===false].join('|');})()",
    )
    assert got == "true|true|true"


# ---------------------------------------------------------------------------
# The wrapper must be invisible to a probe that INSTRUMENTS what it is given
#
# Every other probe in this file passes plain string literals, and a literal
# survives a second String() unchanged — so a double conversion, or a swallowed
# throw followed by a retry, is invisible to all of them. These two hand the
# patch objects that COUNT their own conversions and an engine that THROWS,
# which is the only way to see either.
# ---------------------------------------------------------------------------


def test_contextid_is_converted_to_a_string_exactly_once(tmp_path):
    # WebIDL converts a DOMString argument EXACTLY ONCE. A wrapper that reads
    # String(arguments[0]) to make its decision and then forwards the ORIGINAL
    # arguments makes the engine convert a second time, so a contextId carrying
    # a counting toString distinguishes the profile for free.
    #
    # Note WHERE that fires: on the non-alias path too, i.e. on getContext('2d')
    # and every other canvas call on the profile — not only on a probe for the
    # alias. That inverts the ticket's own cost argument (the missing alias
    # "only matters to a script that specifically probes for it"), and puts the
    # divergence on iOS, the one profile whose story this ticket protects.
    got = _run(
        tmp_path, "ios",
        "(function(){"
        "function count(name){var n=0;"
        " var id={toString:function(){n++;return name;}};"
        " new HTMLCanvasElement().getContext(id);"
        " return n;}"
        # the alias itself, an ordinary context type, and an unknown name
        "return [count('webkit-3d'),count('2d'),count('nope')].join('|');})()",
    )
    assert got == "1|1|1"


def test_contextid_conversion_count_matches_a_non_ios_profile(tmp_path):
    # The same probe on a profile whose leaf returns before touching getContext
    # is the unpatched baseline. iOS diverging from it IS the tell — stated as a
    # comparison so the test names the actual anomaly, not just a magic number.
    ios = _run(
        tmp_path, "ios",
        "(function(){var n=0;var id={toString:function(){n++;return '2d';}};"
        "new HTMLCanvasElement().getContext(id);return n;})()",
        tag="cmpios",
    )
    baseline = _run(
        tmp_path, "windows",
        "(function(){var n=0;var id={toString:function(){n++;return '2d';}};"
        "new HTMLCanvasElement().getContext(id);return n;})()",
        tag="cmpwin",
    )
    assert baseline == 1
    assert ios == baseline


def test_an_engine_throw_propagates_and_is_not_retried(tmp_path):
    # A real device performs the context-attributes dictionary conversion once
    # and lets the TypeError escape. A wrapper that wraps its DELEGATION in a
    # try/catch turns that throw into a return of the unaliased call — which for
    # 'webkit-3d' is null, i.e. THE EXACT ANSWER THE UNPATCHED PROFILE GIVES.
    # A detector probing the alias with a throwing attributes object would get
    # the pre-fix tell back, plus a new one: a context name that answers on a
    # clean call and null on a throwing one is not a shape any engine produces.
    #
    # It also proves the engine is entered ONCE. A swallow-and-retry logs
    # ["webgl","webkit-3d"] for a single page-level call, so any engine-side
    # side effect of context creation happens twice on the failure path.
    stubs = _CANVAS_STUBS + r"""
globalThis.__calls = [];
const __nativeGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function getContext(contextId, options) {
  globalThis.__calls.push(String(contextId));
  // Model the engine's own dictionary conversion of the attributes argument:
  // reading a member whose getter throws propagates out of getContext.
  if (options) { void options.alpha; }
  return __nativeGetContext.apply(this, arguments);
};
"""
    got = _run(
        tmp_path, "ios",
        "(function(){"
        "var opts={get alpha(){throw new TypeError('bad attrs');}};"
        "var outcome;"
        "try{ new HTMLCanvasElement().getContext('webkit-3d',opts);"
        "     outcome='returned'; }"
        "catch(e){ outcome='threw:'+e.constructor.name; }"
        "return outcome+'|'+self.__calls.join(',');})()",
        stubs=stubs,
    )
    # The TypeError escapes, exactly as it would on a real device, and the
    # engine saw ONE call — the aliased one — not a second unaliased retry.
    assert got == "threw:TypeError|webgl"


def test_a_throwing_contextid_propagates_rather_than_becoming_null(tmp_path):
    # The same swallow also caught String(arguments[0]) throwing. A contextId
    # whose toString throws must surface that error, not be quietly converted
    # into a null (or into a second engine call with the original object).
    #
    # HONEST NOTE: unlike its three neighbours, this one was already GREEN
    # before the fix — but for the wrong reason. The pre-fix wrapper swallowed
    # the throw and retried, and the retry's own String() conversion threw
    # again, so the error reached the caller by accident on the second attempt
    # (having entered the engine twice and run the hostile toString twice).
    # After the fix it is green because the single conversion propagates. The
    # assertion is the same; what it witnesses is not. Kept because it pins the
    # invariant, not offered as evidence the fix was needed — the counting and
    # throwing-engine probes above are that evidence.
    got = _run(
        tmp_path, "ios",
        "(function(){"
        "var id={toString:function(){throw new TypeError('nope');}};"
        "try{ var r=new HTMLCanvasElement().getContext(id);"
        "     return r===null?'null':'ctx'; }"
        "catch(e){ return 'threw:'+e.constructor.name; }})()",
    )
    assert got == "threw:TypeError"


def test_a_symbol_contextid_throws_rather_than_being_converted(tmp_path):
    # A Symbol is the ONE input where String() and WebIDL disagree: String(sym)
    # yields "Symbol(...)" while the DOMString conversion throws a TypeError. A
    # wrapper that converts it itself would hand the engine that string and turn
    # the engine's TypeError into a null — a divergence from every other browser
    # on a completely ordinary argument type. The wrapper must forward a Symbol
    # untouched and let the engine throw as a real device does. (Nothing is
    # missed by skipping the alias check: a Symbol can never equal 'webkit-3d'.)
    #
    # HONEST NOTE, same as the throwing-contextId probe above: this was already
    # green against the pre-fix wrapper, because that wrapper's String(Symbol)
    # threw, got swallowed, and the retry forwarded the untouched Symbol so the
    # engine threw on the second attempt. The observable outcome matched for the
    # wrong reason. It is kept to PIN the explicit symbol arm the fix added —
    # without that arm the post-fix single conversion would call String() on the
    # Symbol and hand the engine "Symbol(webgl)", turning a TypeError into null.
    stubs = _CANVAS_STUBS + r"""
const __nativeGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function getContext(contextId, options) {
  // Model WebIDL's DOMString conversion, which rejects a Symbol.
  if (typeof contextId === 'symbol') {
    throw new TypeError("Cannot convert a Symbol value to a string");
  }
  return __nativeGetContext.apply(this, arguments);
};
"""
    got = _run(
        tmp_path, "ios",
        "(function(){"
        "try{ var r=new HTMLCanvasElement().getContext(Symbol('webgl'));"
        "     return r===null?'null':'ctx'; }"
        "catch(e){ return 'threw:'+e.constructor.name; }})()",
        stubs=stubs,
    )
    assert got == "threw:TypeError"


def test_a_2d_canvas_still_refuses_a_webgl_context(tmp_path):
    # One context per canvas: once a 2d context exists, a webgl/alias request
    # returns null. The alias must not bypass that rule.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();c.getContext('2d');"
        "return [c.getContext('webgl'),c.getContext('webkit-3d')]"
        ".map(function(v){return v===null?'null':'ctx';}).join('|');})()",
    )
    assert got == "null|null"


def test_call_without_arguments_does_not_throw(tmp_path):
    # A zero-argument call is a TypeError in a real browser, not an internal
    # crash from the wrapper reading arguments[0] of an empty list.
    got = _run(
        tmp_path, "ios",
        "(function(){var c=new HTMLCanvasElement();"
        "try{var r=c.getContext();return r===null?'null':'ctx';}"
        "catch(e){return 'threw:'+e.constructor.name;}})()",
    )
    assert got in ("null", "threw:TypeError")


def test_this_binding_is_preserved(tmp_path):
    # The wrapper forwards `this`, so two different canvases get two different
    # contexts rather than sharing one.
    got = _run(
        tmp_path, "ios",
        "(function(){var a=new HTMLCanvasElement(),b=new HTMLCanvasElement();"
        "return a.getContext('webkit-3d')!==b.getContext('webkit-3d');})()",
    )
    assert got is True


# ---------------------------------------------------------------------------
# Realm coverage
# ---------------------------------------------------------------------------


def test_alias_reaches_a_child_realm(tmp_path):
    # A detector that finds the alias in the page and not in an about:blank
    # child frame has learned MORE than if it had never been added. The shared
    # bootstrap must carry the leaf into child realms — asserted by BUILDING a
    # second realm with its own HTMLCanvasElement (as a fresh child frame has),
    # running the registered leaves against it, and calling through it. Not by
    # grepping for the bootstrap's name.
    got = _run(
        tmp_path, "ios",
        "(function(){"
        # A child realm: its own canvas constructor and its own UNPATCHED
        # getContext, which (like Chromium) does not know webkit-3d.
        "function ChildCanvas(){this.__ctx=null;this.__kind=null;}"
        "ChildCanvas.prototype.getContext=function getContext(id){"
        " var K={'webgl':WebGLRenderingContext,'webgl2':WebGL2RenderingContext};"
        " var C=K[String(id)]; if(!C) return null;"
        " if(this.__ctx) return this.__kind===C?this.__ctx:null;"
        " this.__ctx=new C(); this.__kind=C; return this.__ctx;};"
        "var child={HTMLCanvasElement:ChildCanvas};"
        # Before the bootstrap runs, the child must NOT know the alias — this is
        # what makes the post-bootstrap result meaningful rather than a stub
        # that answered all along.
        "var before=new ChildCanvas().getContext('webkit-3d')===null;"
        "for (var i=0;i<self.__pnaBoots.length;i++){try{self.__pnaBoots[i](child);}catch(e){}}"
        "var after=new ChildCanvas().getContext('webkit-3d') instanceof WebGLRenderingContext;"
        "return (before?'1':'0')+(after?'1':'0');})()",
    )
    # '1' = the child realm genuinely lacked the alias, '1' = the bootstrap gave it one
    assert got == "11"


def test_registered_into_the_shared_realm_registry(tmp_path):
    # The leaf must be in the shared per-realm registry (that is the mechanism
    # by which every realm the rest of the masking reaches also gets the alias).
    got = _run(tmp_path, "ios", "typeof self.__pnaBoots !== 'undefined' && self.__pnaBoots.length > 0")
    assert got is True


def test_offscreen_canvas_is_deliberately_left_alone(tmp_path):
    # NOT AN OMISSION — the ticket's realm requirement is satisfied by matching
    # the REAL DEVICE in every realm, which is not the same as installing the
    # alias in every realm.
    #
    # OffscreenCanvas.getContext takes a WebIDL ENUM in BOTH WebKit
    # (OffscreenCanvas.idl:43-49) and Chromium (offscreen_canvas_module.idl:15),
    # and webkit-3d has never been a member: a real iPhone throws a TypeError
    # there. Persona's iOS profiles therefore ALREADY match a real device on the
    # offscreen path. Adding the alias would make persona the only browser on
    # earth accepting webkit-3d on an OffscreenCanvas — a fresh impossibility on
    # a surface reachable from any worker, which is the same class of mistake as
    # adding the alias to a non-iOS profile.
    got = _run(
        tmp_path, "ios",
        "(function(){var o=new OffscreenCanvas();"
        "try{o.getContext('webkit-3d');return 'accepted';}"
        "catch(e){return 'threw:'+e.constructor.name;}})()",
    )
    assert got == "threw:TypeError"
    # ...while the offscreen names a real device DOES accept keep working.
    ok = _run(
        tmp_path, "ios",
        "(function(){var o=new OffscreenCanvas();"
        "return o.getContext('webgl') instanceof WebGLRenderingContext;})()",
        tag="offok",
    )
    assert ok is True


def test_worker_realm_without_a_canvas_is_a_safe_noop(tmp_path):
    # A worker has no HTMLCanvasElement, so the leaf must bail cleanly rather
    # than throwing — a leaf that throws in a worker realm can abort the shared
    # bootstrap and take OTHER modules' spoofs down with it.
    got = _run(
        tmp_path, "ios",
        "(function(){var workerRealm={};"
        "var errs=0;"
        "for (var i=0;i<self.__pnaBoots.length;i++){"
        " try{self.__pnaBoots[i](workerRealm);}catch(e){errs++;}}"
        "return errs===0 && workerRealm.HTMLCanvasElement===undefined;})()",
    )
    assert got is True


# ---------------------------------------------------------------------------
# Build surface
# ---------------------------------------------------------------------------


def test_builds_files(tmp_path):
    d = pathlib.Path(build_canvas_ctx_extension("ios", str(tmp_path / "cc")))
    assert (d / "canvas_ctx.js").is_file()
    assert (d / "manifest.json").is_file()


def test_manifest_mv3_main_world_all_frames(tmp_path):
    d = build_canvas_ctx_extension("ios", str(tmp_path / "cc"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text(encoding="utf-8"))
    cs = m["content_scripts"][0]
    # MAIN world (the page's realm, where getContext is read), at document_start
    # (before any page script can capture the unpatched function), all frames.
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


@pytest.mark.parametrize(
    "alias_in,expected",
    [("ios", "ios"), ("iphone", "ios"), ("ipad", "ios"), ("iPadOS", "ios"),
     ("macos", "macos"), ("Windows", "windows"), ("android", "android"),
     ("linux", "windows")],
)
def test_os_normalisation_matches_gpu_ext(tmp_path, alias_in, expected):
    # The OS spellings that reach build_gpu_extension must reach this one the
    # same way, or a profile could get the iOS GPU identity WITHOUT the alias
    # (or, worse, the alias without the identity).
    js = (pathlib.Path(build_canvas_ctx_extension(alias_in, str(tmp_path / alias_in)))
          / "canvas_ctx.js").read_text(encoding="utf-8")
    assert f'var OS = "{expected}";' in js
    assert "__OS__" not in js


def test_every_ios_spelling_actually_enables_the_alias(tmp_path):
    # The normalisation above is only meaningful if it changes BEHAVIOUR, so
    # check the runtime outcome for each spelling too.
    for spelling in ("ios", "iphone", "ipad", "ipadOS"):
        got = _run(
            tmp_path, spelling,
            "(function(){var c=new HTMLCanvasElement();"
            "return c.getContext('webkit-3d')!==null;})()",
            tag=spelling,
        )
        assert got is True, f"{spelling} did not enable the alias"
