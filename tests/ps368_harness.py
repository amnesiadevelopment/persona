"""PS-368 — shared realm harness for the `__pnaName` own-property axis.

WHY A SHARED HARNESS AND NOT A COPY PER TEST. This ticket moves 21 marker write
sites across 12 modules onto closure WeakMaps, and the whole point of the change
is that a wrapper must own exactly ``["length","name"]`` AFTER the real generated
script has run. That reading can only be taken from a realm — a regex over the
source text would pass against a script that does not parse, and a substring
check would pass against a marker that is still pinned three lines further down.
So every assertion in ``test_ps368_marker_weakmap.py`` builds a REAL extension,
evaluates it in node over the same native-shaped prelude ``test_ps314`` uses, and
reads ``Object.getOwnPropertyNames`` off the function the script INSTALLED.

The prelude is imported from ``test_ps314_native_wrapper_shape`` rather than
copied: that file's ``test_the_native_baseline_is_itself_native_shaped`` is the
control which proves the stand-in built-ins are native-shaped, and a second copy
here would be a baseline nothing asserts.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

from src.services.browser.audio_ext import build_audio_extension
from src.services.browser.canvas_ctx_ext import build_canvas_ctx_extension
from src.services.browser.device_ext import build_device_extension
from src.services.browser.engine_version import ChromiumVersion
from src.services.browser.geo_ext import build_geo_extension
from src.services.browser.gpu_ext import build_gpu_extension
from src.services.browser.locale_ext import build_locale_extension
from src.services.browser.measuretext_ext import build_measuretext_extension
from src.services.browser.mobile_ext import build_mobile_extension
from src.services.browser.native_ext import build_native_extension
from src.services.browser.voice_ext import build_voice_extension
from src.services.browser.webgl_ext import build_webgl_extension

from tests.test_ps314_native_wrapper_shape import REALM_PRELUDE

NODE = shutil.which("node")

# The exact set a native function owns. Anything else is a tell.
NATIVE_SHAPE = ["length", "name"]


def run_node(js: str) -> dict:
    """Evaluate `js` in node and return the JSON it prints on its last line."""
    path = pathlib.Path(tempfile.mkdtemp()) / "probe.js"
    path.write_text(js, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"the probe did not run, so it measured NOTHING:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    return json.loads(last)


# ---------------------------------------------------------------------------
# The real generated scripts, one per marker-bearing module.
# ---------------------------------------------------------------------------
#
# The extra surface below what `REALM_PRELUDE` supplies is added here rather
# than in the prelude so `test_ps314`'s own baseline control keeps measuring the
# exact realm it has always measured.
EXTRA_PRELUDE = r"""
// measuretext / canvas_ctx need a CanvasRenderingContext2D prototype to patch.
class CanvasRenderingContext2D {}
CanvasRenderingContext2D.prototype.measureText = nativeMethod('measureText', 1,
  function () { return { width: 42, actualBoundingBoxAscent: 7 }; });
G.CanvasRenderingContext2D = CanvasRenderingContext2D;
class OffscreenCanvasRenderingContext2D {}
OffscreenCanvasRenderingContext2D.prototype.measureText =
  nativeMethod('measureText', 1, function () { return { width: 42 }; });
G.OffscreenCanvasRenderingContext2D = OffscreenCanvasRenderingContext2D;
class HTMLCanvasElement {}
HTMLCanvasElement.prototype.getContext = nativeMethod('getContext', 1, () => null);
G.HTMLCanvasElement = HTMLCanvasElement;
class OffscreenCanvas {}
OffscreenCanvas.prototype.getContext = nativeMethod('getContext', 1, () => null);
G.OffscreenCanvas = OffscreenCanvas;

// geo
G.navigator.geolocation = {
  getCurrentPosition: nativeMethod('getCurrentPosition', 1, () => undefined),
  watchPosition: nativeMethod('watchPosition', 1, () => 0),
  clearWatch: nativeMethod('clearWatch', 1, () => undefined),
};

// voice
class SpeechSynthesisVoice {}
G.SpeechSynthesisVoice = SpeechSynthesisVoice;
G.speechSynthesis = {
  getVoices: nativeMethod('getVoices', 0, () => []),
  dispatchEvent: nativeMethod('dispatchEvent', 1, () => true),
};
G.Event = G.Event || function Event(t) { this.type = t; };

// mobile / device want a mediaDevices to patch
G.navigator.mediaDevices = G.navigator.mediaDevices || {
  enumerateDevices: nativeMethod('enumerateDevices', 0, () => Promise.resolve([])),
};
try {
  Object.defineProperty(G.navigator, 'hardwareConcurrency',
    { get: nativeAccessor('hardwareConcurrency', 8), configurable: true });
  Object.defineProperty(G.navigator, 'deviceMemory',
    { get: nativeAccessor('deviceMemory', 8), configurable: true });
} catch (e) {}
G.Worker = G.Worker || nativeMethod('Worker', 1, function () {});
"""


def build_scripts(tmp: str) -> dict[str, str]:
    """Build every marker-bearing Chromium extension and return its script text.

    Keyed by module name so a probe can compose an arbitrary subset — and, for
    AC2's load-order requirement, compose the SAME subset in either order.
    """
    d = pathlib.Path(tmp)

    def read(sub: str, fn: str) -> str:
        return (d / sub / fn).read_text(encoding="utf-8")

    build_native_extension(str(d / "native"))
    build_gpu_extension(
        4242, "windows", str(d / "gpu"), 1, engine_platform="chromium"
    )
    build_webgl_extension(4242, str(d / "webgl"))
    build_audio_extension(24601, str(d / "audio"))
    build_device_extension(4242, str(d / "device"), 1, os_type="windows")
    build_mobile_extension(
        str(d / "mobile"),
        is_ios=False,
        platform="Linux armv81",
        model="Pixel 7",
        chromium_version=ChromiumVersion("144.0.7559.132"),
        css_width=412,
        css_height=915,
        dpr=2.625,
        device_memory=8,
        hardware_concurrency=8,
    )
    build_geo_extension(48.85, 2.35, str(d / "geo"))
    build_voice_extension("en-US", str(d / "voice"))
    build_locale_extension("fr-FR", str(d / "locale"))
    build_canvas_ctx_extension("ios", str(d / "canvasctx"))
    build_measuretext_extension(str(d / "mt"))

    return {
        "native_ext": read("native", "native.js"),
        "gpu_ext": read("gpu", "gpu.js"),
        "webgl_ext": read("webgl", "webgl.js"),
        "audio_ext": read("audio", "audio.js"),
        "device_ext": read("device", "device.js"),
        "mobile_ext": read("mobile", "mobile.js"),
        "geo_ext": read("geo", "geo.js"),
        "voice_ext": read("voice", "voices.js"),
        "locale_ext": read("locale", "locale.js"),
        "canvas_ctx_ext": read("canvasctx", "canvas_ctx.js"),
        "measuretext_ext": read("mt", "measuretext.js"),
    }


# (label -> expression evaluated AFTER the scripts ran). Every one of these is a
# wrapper a marker write site produced, so the set below IS the AC2 population.
WRAPPER_READS: dict[str, str] = {
    # gpu_ext (1 site, nativeWrap) — installOn wraps the WebGL getters
    "gpu:getParameter": "WebGLRenderingContext.prototype.getParameter",
    "gpu:getExtension": "WebGLRenderingContext.prototype.getExtension",
    "gpu:getSupportedExtensions":
        "WebGLRenderingContext.prototype.getSupportedExtensions",
    # webgl_ext (1 site, nativeWrap)
    "webgl:readPixels": "WebGLRenderingContext.prototype.readPixels",
    # audio_ext (1 site, Chromium nativeWrap)
    "audio:getChannelData": "AudioBuffer.prototype.getChannelData",
    "audio:getFloatFrequencyData": "AnalyserNode.prototype.getFloatFrequencyData",
    "audio:getByteFrequencyData": "AnalyserNode.prototype.getByteFrequencyData",
    # device_ext (5 sites: def x3 copies, nw x2)
    "device:matchMedia": "G.matchMedia",
    "device:screen.width":
        "Object.getOwnPropertyDescriptor(G.screen, 'width').get",
    "device:screen.availHeight":
        "Object.getOwnPropertyDescriptor(G.screen, 'availHeight').get",
    "device:devicePixelRatio":
        "Object.getOwnPropertyDescriptor(G, 'devicePixelRatio').get",
    "device:hardwareConcurrency":
        "Object.getOwnPropertyDescriptor(G.navigator, 'hardwareConcurrency').get",
    "device:enumerateDevices": "G.navigator.mediaDevices.enumerateDevices",
    # geo_ext (1 site, mark)
    "geo:getCurrentPosition": "G.navigator.geolocation.getCurrentPosition",
    "geo:watchPosition": "G.navigator.geolocation.watchPosition",
    "geo:clearWatch": "G.navigator.geolocation.clearWatch",
    # voice_ext (1 site)
    "voice:getVoices": "G.speechSynthesis.getVoices",
    # locale_ext (3 sites: _pts self-cloak, _wrap W, _mark)
    "locale:Date#toLocaleString": "G.Date.prototype.toLocaleString",
    "locale:Date#toString": "G.Date.prototype.toString",
    "locale:Number#toLocaleString": "G.Number.prototype.toLocaleString",
    # canvas_ctx_ext (1 site)
    "canvas_ctx:getContext": "G.HTMLCanvasElement.prototype.getContext",
    # measuretext_ext (2 sites — same wrapper, primary + fallback arm)
    "mt:measureText": "G.CanvasRenderingContext2D.prototype.measureText",
    # native_ext (1 site — the cloak itself)
    "toString": "G.Function.prototype.toString",
}

# The Intl constructors are read separately: `locale_ext`'s `_wrap` deliberately
# does NOT re-house them in a method shorthand (a shorthand is not constructible,
# so `new Intl.DateTimeFormat()` would throw), and a native Intl constructor
# legitimately owns `prototype`/`supportedLocalesOf` too. So their AC2 assertion
# is "does not own __pnaName", not "owns exactly length+name".
CONSTRUCTOR_READS: dict[str, str] = {
    "locale:Intl.DateTimeFormat": "G.Intl.DateTimeFormat",
    "locale:Intl.NumberFormat": "G.Intl.NumberFormat",
    "locale:Intl.Collator": "G.Intl.Collator",
}


def probe(
    scripts: list[str],
    reads: dict[str, str] | None = None,
    extra_js: str = "",
    raw_reads: bool = False,
) -> dict:
    """Run `scripts` in order in one realm, then report each read's shape.

    Each entry comes back as ``{own, name, length, marker, stringified}`` — the
    four axes knowledge article PS-22 names, read in ONE pass so a change that
    fixes the own-property set by breaking the stringification cannot pass.

    ``raw_reads`` hands back what each expression EVALUATES TO instead of
    measuring the function it selects. It exists for the cross-realm arm: a
    second realm's function must be measured with THAT realm's own
    ``Function.prototype.toString`` (every realm has its own, and this realm's
    cloak has never seen the child's wrapper, so measuring from here reports a
    leak against a correct fix). So the child measures itself and only the
    resulting scalars cross back — this switch says "these are already readings".
    """
    reads = dict(WRAPPER_READS if reads is None else reads)
    body = "".join(
        "\ntry {\n" + s + "\n} catch (e) { OUT_ERR.push(String(e)); }\n"
        for s in scripts
    )
    # `REALM_SRC` is the prelude + every script, as ONE string. `extra_js` uses it
    # to model the worker crossing: the bootstrap re-evaluates the leaf as SOURCE
    # TEXT in the worker's own realm, so a probe that wants a second realm needs
    # the same text rather than a reference to a function object from this one.
    realm_src = REALM_PRELUDE + EXTRA_PRELUDE + body
    js = (
        REALM_PRELUDE
        + EXTRA_PRELUDE
        + "\nconst OUT_ERR = [];\n"
        + "const REALM_SRC = " + json.dumps(
            REALM_PRELUDE + EXTRA_PRELUDE
            + "\nvar OUT_ERR = [];\n" + body
        ) + ";\n"
        + body
        + extra_js
        + "\nconst OUT = { __errors: OUT_ERR };\n"
        + "".join(
            (
                f"""
try {{ OUT[{json.dumps(label)}] = ({expr}) || null; }}
catch (e) {{ OUT[{json.dumps(label)}] = null; }}
"""
                if raw_reads
                else f"""
try {{
  const f = ({expr});
  OUT[{json.dumps(label)}] = (f === undefined || f === null)
    ? null
    : {{ own: Object.getOwnPropertyNames(f).sort(),
         name: f.name,
         length: f.length,
         marker: ("__pnaName" in f),
         stringified: Function.prototype.toString.call(f) }};
}} catch (e) {{ OUT[{json.dumps(label)}] = null; }}
"""
            )
            for label, expr in reads.items()
        )
        + "console.log(JSON.stringify(OUT));\n"
    )
    return run_node(js)


CHROMIUM_MODULES = [
    "native_ext",
    "gpu_ext",
    "webgl_ext",
    "audio_ext",
    "device_ext",
    "mobile_ext",
    "geo_ext",
    "voice_ext",
    "locale_ext",
    "canvas_ctx_ext",
    "measuretext_ext",
]


def native_form(name: str) -> str:
    """V8's one-line native shape — what every Chromium wrapper must stringify as."""
    return "function " + name + "() { [native code] }"
