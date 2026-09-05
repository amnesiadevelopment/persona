"""PS-314 — every spoof wrapper must read in the NATIVE own-property shape.

WHAT THIS FILE MEASURES, AND WHY IT IS NOT A SOURCE-TEXT TEST
─────────────────────────────────────────────────────────────
`Function.prototype.toString` is ONE read out of at least four. The other three
are cheaper for a detector to run and were, until this ticket, completely
uncloaked:

    Object.getOwnPropertyNames(fn)   the FORM the author happened to type
    fn.length                        arity
    fn.name                          the identifier

A sloppy-mode function EXPRESSION owns ["arguments","caller","length","name",
"prototype"]. A native method owns exactly ["length","name"]. So the difference
between `({ m() {} }).m` and `({ m: function () {} }).m` is a one-line tell,
readable without calling anything and entirely independent of the toString
cloak. `worker_wrap.py` has carried that prescription in prose since PS-48;
these leaves did not follow it.

The assertions below therefore:

  * run the REAL generated script (build_*_extension / firefox_*_init_script),
    never a hand-copied fragment and never a regex over the source text;
  * evaluate it in a REALM (`node --input-type=module` over a fresh vm context)
    that supplies the DOM surface each script patches;
  * read `Object.getOwnPropertyNames` off the function the script INSTALLED,
    pulled back out of the realm afterwards.

A test asserting "the generated source contains `({ m()`" would pass against a
script that never ran, and a test asserting "nativeWrap was called" would pass
against a nativeWrap that does nothing. Both are the vacuity this file exists
to avoid — see knowledge article PS-11.

⚠️ THE BASELINE CONTROL IS LOAD-BEARING (AC2). A stub built as a function
expression leaks `prototype`/`arguments`/`caller` on its OWN, with no wrapping
at all. Measured against such a baseline, a wrapper "leaking" proves nothing —
the leak was already there. `test_the_native_baseline_is_itself_native_shaped`
pins the control so this file cannot silently start measuring nothing.

THE TWO ENGINES REACH DIFFERENT SETS, AND THAT IS DELIBERATE (AC6)
──────────────────────────────────────────────────────────────────
Chromium's toString cloak reads its marker as an OWN property
(`this.__pnaName`, native_ext.py's applyNativePatch), so any Chromium wrapper
the cloak can serve necessarily owns it:

    Chromium  ["__pnaName","length","name"]
    Firefox   ["length","name"]              (marker lives in a WeakMap)

Both drop the three ENGINE-shaped names, which are what identify a wrapper
generically. Removing `__pnaName` would break the toString cloak (PS-131,
PS-16/17) and is explicitly out of scope. The Firefox arm is the proof that the
shape fix itself is complete: where the marker is not an own property, the set
is exactly native.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from src.services.browser.audio_ext import (
    build_audio_extension,
    firefox_audio_init_script,
)
from src.services.browser.device_ext import build_device_extension
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.webgl_ext import build_webgl_extension

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is needed to evaluate the generated scripts in a realm"
)

# The exact set a native function owns. Anything else is a tell.
NATIVE_SHAPE = ["length", "name"]
# What a Chromium wrapper can reach while its cloak reads an own-property marker.
CHROMIUM_SHAPE = ["__pnaName", "length", "name"]


def _run(js: str) -> dict:
    """Evaluate `js` in node and return the JSON it prints on the last line."""
    path = pathlib.Path(tempfile.mkdtemp()) / "probe.js"
    path.write_text(js, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"the probe did not run, so it measured NOTHING:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    return json.loads(last)


# A minimal DOM/WebGL surface. Every built-in the scripts patch is installed as
# a NATIVE-SHAPED method shorthand, so the baseline is native — see the module
# docstring on why an expression-shaped baseline would void every assertion.
REALM_PRELUDE = r"""
function nativeMethod(name, arity, body) {
  // Build a method shorthand, then pin name/length so the stand-in has the
  // SAME shape and arity a real built-in would. Object.defineProperty on a
  // shorthand cannot introduce prototype/arguments/caller.
  const holder = { m(...a) { return body ? body.apply(this, a) : undefined; } };
  const f = holder.m;
  Object.defineProperty(f, 'name',   { value: name });
  Object.defineProperty(f, 'length', { value: arity });
  return f;
}
function nativeAccessor(name, val) {
  const g = Object.getOwnPropertyDescriptor({ get m() { return val; } }, 'm').get;
  Object.defineProperty(g, 'name', { value: 'get ' + name });
  return g;
}
const G = globalThis;

class WebGLRenderingContext {}
class WebGL2RenderingContext {}
for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
  C.prototype.getParameter            = nativeMethod('getParameter', 1, () => 0);
  C.prototype.getExtension            = nativeMethod('getExtension', 1, () => null);
  C.prototype.getSupportedExtensions  = nativeMethod('getSupportedExtensions', 0, () => []);
  C.prototype.getShaderPrecisionFormat= nativeMethod('getShaderPrecisionFormat', 2,
                                          () => ({ rangeMin: 127, rangeMax: 127, precision: 23 }));
  C.prototype.readPixels              = nativeMethod('readPixels', 7, () => undefined);
}
G.WebGLRenderingContext = WebGLRenderingContext;
G.WebGL2RenderingContext = WebGL2RenderingContext;

class AudioBuffer {}
AudioBuffer.prototype.getChannelData = nativeMethod('getChannelData', 1,
  function () { return new Float32Array([0.5, -0.25, 0.125]); });
class AnalyserNode {}
AnalyserNode.prototype.getFloatFrequencyData = nativeMethod('getFloatFrequencyData', 1,
  function (a) { if (a) a.fill(-50); });
AnalyserNode.prototype.getByteFrequencyData  = nativeMethod('getByteFrequencyData', 1,
  function (a) { if (a) a.fill(128); });
G.AudioBuffer = AudioBuffer;
G.AnalyserNode = AnalyserNode;
G.OfflineAudioContext = class {};

G.screen = {};
Object.defineProperty(G.screen, 'width',  { get: nativeAccessor('width', 3840),  configurable: true });
Object.defineProperty(G.screen, 'height', { get: nativeAccessor('height', 2160), configurable: true });
G.screen.orientation = {};
G.navigator = G.navigator || {};
try {
  Object.defineProperty(G, 'devicePixelRatio',
    { get: nativeAccessor('devicePixelRatio', 1), configurable: true });
} catch (e) {}
G.matchMedia = nativeMethod('matchMedia', 1, () => ({ matches: false, media: '' }));
G.chrome = { runtime: {} };
G.document = { documentElement: {}, createElement: () => ({ getContext: () => null }) };
G.window = G;
G.self = G;
"""


def _own_props_probe(script: str, reads: dict[str, str]) -> dict:
    """Run `script` in the realm, then report own-props/name/length per target.

    `reads` maps a label to a JS expression evaluated AFTER the script ran.
    """
    js = (
        REALM_PRELUDE
        + "\ntry {\n"
        + script
        + "\n} catch (e) { /* a leaf may bail on a surface this realm lacks */ }\n"
        + "const OUT = {};\n"
        + "".join(
            f"""
try {{
  const f = ({expr});
  OUT[{json.dumps(label)}] = f === undefined || f === null
    ? null
    : {{ own: Object.getOwnPropertyNames(f).sort(),
         name: f.name, length: f.length }};
}} catch (e) {{ OUT[{json.dumps(label)}] = null; }}
"""
            for label, expr in reads.items()
        )
        + "console.log(JSON.stringify(OUT));\n"
    )
    return _run(js)


def _extension_js(build_dir: str, filename: str) -> str:
    return (pathlib.Path(build_dir) / filename).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — the control. Without this the whole file can silently measure nothing.
# ─────────────────────────────────────────────────────────────────────────────


def test_the_native_baseline_is_itself_native_shaped():
    """The stand-in built-ins must be NATIVE-shaped before any script runs.

    This is the premise every other assertion rests on. If the realm's baseline
    were built from function EXPRESSIONS it would own prototype/arguments/caller
    on its own, and "the wrapper leaks" would be true no matter what the wrapper
    did — a green that cannot go red, and a red that means nothing.
    """
    out = _run(
        REALM_PRELUDE
        + """
const gp = WebGLRenderingContext.prototype.getParameter;
const gcd = AudioBuffer.prototype.getChannelData;
const scr = Object.getOwnPropertyDescriptor(G.screen, 'width').get;
console.log(JSON.stringify({
  method:   { own: Object.getOwnPropertyNames(gp).sort(),  name: gp.name,  length: gp.length },
  audio:    { own: Object.getOwnPropertyNames(gcd).sort(), name: gcd.name, length: gcd.length },
  accessor: { own: Object.getOwnPropertyNames(scr).sort(), name: scr.name, length: scr.length },
  expression_control: Object.getOwnPropertyNames(function (a) {}).sort(),
}));
"""
    )
    assert out["method"]["own"] == NATIVE_SHAPE, (
        f"the realm's stand-in built-in is not native-shaped ({out['method']['own']}), "
        f"so every other assertion in this file is void"
    )
    assert out["audio"]["own"] == NATIVE_SHAPE
    assert out["accessor"]["own"] == NATIVE_SHAPE
    assert out["method"]["length"] == 1 and out["method"]["name"] == "getParameter"
    assert out["accessor"]["name"] == "get width"
    # And the thing we are fixing really is a leak, in this very realm.
    assert out["expression_control"] == [
        "arguments",
        "caller",
        "length",
        "name",
        "prototype",
    ], (
        "a bare function expression must leak prototype/arguments/caller here — "
        "if it does not, this engine cannot see the defect and the file is vacuous"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC1 + AC5 — the Chromium leaves, read out of a realm after the real script ran
# ─────────────────────────────────────────────────────────────────────────────

# (label, expression, expected arity) — arities are the NATIVE ones the realm's
# stand-ins declare, so a wrapper that moved arity fails here.
GPU_TARGETS = {
    "getParameter": ("WebGLRenderingContext.prototype.getParameter", 1),
    "getExtension": ("WebGLRenderingContext.prototype.getExtension", 1),
    "getSupportedExtensions": (
        "WebGLRenderingContext.prototype.getSupportedExtensions",
        0,
    ),
    "getShaderPrecisionFormat": (
        "WebGLRenderingContext.prototype.getShaderPrecisionFormat",
        2,
    ),
}


def test_gpu_leaves_read_in_the_chromium_native_shape():
    d = tempfile.mkdtemp()
    build_gpu_extension(
        4242, "windows", str(pathlib.Path(d) / "ext"), 1, engine_platform="chromium"
    )
    script = _extension_js(str(pathlib.Path(d) / "ext"), "gpu.js")
    out = _own_props_probe(script, {k: v[0] for k, v in GPU_TARGETS.items()})

    for label, (_expr, arity) in GPU_TARGETS.items():
        got = out[label]
        assert got is not None, f"{label} was not installed by the real script"
        assert got["own"] == CHROMIUM_SHAPE, (
            f"{label} owns {got['own']}, not {CHROMIUM_SHAPE}. The engine-shaped "
            f"names (prototype/arguments/caller) identify this as a wrapper "
            f"without calling it."
        )
        assert got["length"] == arity, (
            f"{label} reports arity {got['length']}, native is {arity} — a shape "
            f"fix that moves arity swaps one tell for another (PS-119/PS-255)"
        )
        assert got["name"] == label


def test_webgl_readpixels_reads_in_the_chromium_native_shape():
    d = tempfile.mkdtemp()
    build_webgl_extension(4242, str(pathlib.Path(d) / "ext"))
    script = _extension_js(str(pathlib.Path(d) / "ext"), "webgl.js")
    out = _own_props_probe(
        script, {"readPixels": "WebGLRenderingContext.prototype.readPixels"}
    )
    got = out["readPixels"]
    assert got is not None, "readPixels was not installed by the real script"
    assert got["own"] == CHROMIUM_SHAPE, f"readPixels owns {got['own']}"
    assert got["length"] == 7, (
        f"readPixels reports arity {got['length']}, native is 7 — the widest "
        f"arity in the set and the one a literal would most likely get wrong"
    )
    assert got["name"] == "readPixels"


AUDIO_TARGETS = {
    "getChannelData": ("AudioBuffer.prototype.getChannelData", 1),
    "getFloatFrequencyData": ("AnalyserNode.prototype.getFloatFrequencyData", 1),
    "getByteFrequencyData": ("AnalyserNode.prototype.getByteFrequencyData", 1),
}


def test_chromium_audio_leaves_read_in_the_chromium_native_shape():
    d = tempfile.mkdtemp()
    build_audio_extension(24601, str(pathlib.Path(d) / "ext"))
    script = _extension_js(str(pathlib.Path(d) / "ext"), "audio.js")
    out = _own_props_probe(script, {k: v[0] for k, v in AUDIO_TARGETS.items()})
    for label, (_expr, arity) in AUDIO_TARGETS.items():
        got = out[label]
        assert got is not None, f"{label} was not installed"
        assert got["own"] == CHROMIUM_SHAPE, f"{label} owns {got['own']}"
        assert got["length"] == arity, f"{label} arity {got['length']} != {arity}"


# ─────────────────────────────────────────────────────────────────────────────
# AC1 + AC5 — THE FIREFOX ARM. The marker is a WeakMap here, so this arm must
# reach the EXACT native set. It is the proof the shape fix is complete.
# ─────────────────────────────────────────────────────────────────────────────


def test_firefox_audio_leaves_read_in_the_EXACT_native_shape():
    """Firefox carries its cloak marker in a WeakMap, so nothing persona-shaped
    is an own property and the set must be exactly ["length","name"].

    ⚠️ Arity is asserted against the realm's NATIVE stand-ins, not against the
    0/1/1 figures quoted in the ticket — those were a harness's stub values, and
    pinning them as literals is precisely the trap the ticket warns about. The
    helper copies from `orig.length` at runtime, so this passes on any engine.
    """
    script = firefox_audio_init_script(24601)
    out = _own_props_probe(script, {k: v[0] for k, v in AUDIO_TARGETS.items()})
    for label, (_expr, arity) in AUDIO_TARGETS.items():
        got = out[label]
        assert got is not None, f"{label} was not installed by the Firefox script"
        assert got["own"] == NATIVE_SHAPE, (
            f"{label} owns {got['own']}, not the EXACT native set {NATIVE_SHAPE}. "
            f"The Firefox helper's marker lives in a WeakMap, so there is no "
            f"reason for any third own property here."
        )
        assert got["length"] == arity, (
            f"{label} arity {got['length']} != {arity} (copied from orig.length)"
        )
        assert got["name"] == label


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — the def() accessors: `.name` pinned as a PAIR with `__pnaName`
# ─────────────────────────────────────────────────────────────────────────────


def test_device_accessors_are_real_accessors_and_pin_their_name():
    """`.name` is a SECOND axis the toString cloak cannot reach.

    Before this ticket a def() getter read `.name === "getter"` — a
    persona-internal identifier, on every spoofed screen property, in every
    realm. Native reads `get width` (cf. Map#size -> "get size").

    ⚠️ THIS ASSERTS ON PROPERTIES SERVED BY THE **MINIFIED** def() COPIES, and
    that is deliberate rather than incidental. Measured against the generated
    script: every live `def()` callsite — screen.width/height/availWidth/
    availHeight/colorDepth/pixelDepth, orientation.type/angle, devicePixelRatio,
    MediaQueryList.matches, navigator.hardwareConcurrency/deviceMemory — is
    lexically served by a MINIFIED copy. The readable copy has ZERO callsites
    and is dead code for every property this test can reach.

    That matters for the falsification: reverting the readable copy alone leaves
    this test GREEN, because the reverted code never runs. A single-arm control
    caught exactly that, which is why the properties below are chosen to span
    BOTH minified copies rather than to look representative.
    """
    d = tempfile.mkdtemp()
    build_device_extension(4242, str(pathlib.Path(d) / "ext"), 1, resolution=(1920, 1080))
    script = _extension_js(str(pathlib.Path(d) / "ext"), "device.js")
    out = _own_props_probe(
        script,
        {
            # served by the FIRST minified copy (the screen/applyScreenPatch seam)
            "screen.width": "Object.getOwnPropertyDescriptor(G.screen,'width').get",
            "screen.height": "Object.getOwnPropertyDescriptor(G.screen,'height').get",
            "screen.colorDepth": "Object.getOwnPropertyDescriptor(G.screen,'colorDepth').get",
            # served by the SECOND minified copy (the hardware/applyHwPatch seam)
            "navigator.hardwareConcurrency":
                "Object.getOwnPropertyDescriptor(G.navigator,'hardwareConcurrency').get",
            "navigator.deviceMemory":
                "Object.getOwnPropertyDescriptor(G.navigator,'deviceMemory').get",
        },
    )
    seen = 0
    for label, got in out.items():
        if got is None:
            # A property this realm's surface does not support is skipped rather
            # than failed — but `seen` below refuses an all-skipped vacuous pass.
            continue
        seen += 1
        prop = label.split(".")[1]
        assert got["own"] == CHROMIUM_SHAPE, (
            f"{label} getter owns {got['own']} — an expression getter, not a real "
            f"accessor"
        )
        assert got["length"] == 0, (
            f"{label} getter reports arity {got['length']}; a native accessor is 0"
        )
        assert got["name"] == f"get {prop}", (
            f"{label} getter reads .name === {got['name']!r}, leaking a "
            f"persona-internal identifier instead of the native 'get {prop}'"
        )
    assert seen >= 4, (
        f"only {seen} def()-served accessors were reachable in this realm; the "
        f"test must span BOTH minified copies or it cannot see a revert of either"
    )
