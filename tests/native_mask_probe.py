"""Execution-based probe for the native-masking invariant (PS-17).

THE INVARIANT under test is a runtime property, not a string in a file:

    Function.prototype.toString.call(<a persona wrapper>)
        === "function <name>() { [native code] }"

The suite used to assert `"__pnaName" in js` instead. That is one *implementation*
of the invariant, and a poor witness for it: a substring check passes whether or
not the override installed, whether or not native_ext's patch honours the marker,
and whether or not it survives into the realm the page actually reads from. It
also passes on a file that merely mentions the marker in a comment, and FAILS on a
correct marker-free implementation (a closure WeakMap) that is strictly better —
which is exactly what `tests/test_ff_language_override.py:166` already ships and
pins with `assert "__pnaName" not in cloak`.

So these helpers assert the PROPERTY by running the generated JS. Mechanism is
free to change; the observable a detector reads is what is pinned.

Harness shape mirrors the in-tree precedent rather than inventing one:
`tests/test_worker_wrap.py` (shutil.which -> pytest.skip -> subprocess over a
`node:vm` harness, one isolated context per realm) and `tests/test_gpu_ext.py`'s
`_probe`. Standard library + node only — no new dependency.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.native_ext import build_native_extension

# The realm is built from INSIDE the vm context so that `G` is the context's own
# globalThis — carrying a real `Function`, `Object`, `Reflect`, … A hand-built
# `{self: G, window: G}` sandbox object has no `Function`, so native_ext's patch
# would take its `if (!G || !G.Function) return` bail-out and the probe would
# quietly measure nothing. `self`/`window`/`top` are the three names the
# extensions reach for when they resolve their realm.
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

// Per-extension stubs: the built-ins the extension expects to wrap. These stand
// in for the browser objects; each original is given its real name so the
// wrapper inherits the name a detector would expect to read back.
vm.runInContext(cfg.stubs, sandbox, { filename: 'stubs.js' });

// The generated content scripts, in the order given.
for (const p of cfg.scripts) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), sandbox, { filename: p });
}

// `probe` is a JS expression evaluated in that same realm. It must yield the
// string a detector would read.
const result = vm.runInContext(cfg.probe, sandbox, { filename: 'probe.js' });
console.log(JSON.stringify({ result: result, type: typeof result }));
"""


# --------------------------------------------------------------------------
# Per-extension stubs. Each stands in for the browser built-in the extension
# wraps, and is declared with its REAL name so the wrapper inherits the name a
# detector expects to read back (the extensions copy `orig.name` onto the
# replacement). Deliberately minimal: enough shape for the patch to install.
# --------------------------------------------------------------------------

AUDIO_STUBS = r"""
function AudioBuffer() {}
AudioBuffer.prototype.getChannelData = function getChannelData() {
  return new Float32Array(8);
};
function AnalyserNode() {}
AnalyserNode.prototype.getFloatFrequencyData = function getFloatFrequencyData() {};
AnalyserNode.prototype.getByteFrequencyData = function getByteFrequencyData() {};
"""

# Shared by webgl_ext (readPixels) and gpu_ext (getParameter/getExtension/…).
GL_STUBS = r"""
function WebGLRenderingContext() {}
function WebGL2RenderingContext() {}
for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
  C.prototype.readPixels = function readPixels() {};
  C.prototype.getParameter = function getParameter() { return "HOST_VALUE"; };
  C.prototype.getExtension = function getExtension() { return null; };
  C.prototype.getSupportedExtensions = function getSupportedExtensions() { return []; };
  C.prototype.getShaderPrecisionFormat = function getShaderPrecisionFormat() { return null; };
}
"""

GEO_STUBS = r"""
globalThis.navigator = {
  geolocation: {
    getCurrentPosition: function getCurrentPosition() {},
    watchPosition: function watchPosition() {},
    clearWatch: function clearWatch() {},
  },
};
"""

CANVAS_STUBS = r"""
function CanvasRenderingContext2D() {}
CanvasRenderingContext2D.prototype.measureText = function measureText() {
  return { width: 10 };
};
"""


def native_form(name: str) -> str:
    """The exact string a real engine renders for a native function."""
    return "function " + name + "() { [native code] }"


def spidermonkey_native_form(name: str) -> str:
    """SpiderMonkey's native rendering: THREE lines, four-space indent.

    Deliberately not the same string as `native_form` (V8's one-liner). Emitting
    V8's form on Firefox is itself a masking tell — one
    `Array.prototype.map.toString()` comparison away — so the two engines'
    expected forms are kept as separate literals rather than one shared
    "native-ish" matcher that would accept either.
    """
    return "function " + name + "() {\n    [native code]\n}"


def stringify_in_realm(
    tmp_path, scripts, stubs, probe, *, install_native=True, native_first=True
):
    """Run `scripts` in an isolated node realm and return what `probe` evaluates to.

    `install_native` is the FALSIFICATION switch (PS-17 AC#3). With it False the
    native_ext content script is not loaded, so `Function.prototype.toString` is
    the engine's own: any assertion that the wrapper reads as native MUST go red.
    A test that still passes here would be testing nothing.

    `native_first` places native_ext's script before (default) or after the given
    scripts. native_ext documents that load order does not matter — every content
    script in the realm shares one Function.prototype — so the False case pins
    that claim by execution.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    work = pathlib.Path(tmp_path) / "probe"
    work.mkdir(parents=True, exist_ok=True)

    scripts = [str(s) for s in scripts]
    if install_native:
        native_dir = build_native_extension(str(work / "native_ext"))
        native_js = str(pathlib.Path(native_dir) / "native.js")
        scripts = [native_js] + scripts if native_first else scripts + [native_js]

    harness = work / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(
        json.dumps({"stubs": stubs, "scripts": scripts, "probe": probe}),
        encoding="utf-8",
    )

    out = subprocess.run(
        [node, str(harness), str(cfg)],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["result"]


# ==========================================================================
# CHILD-FRAME realm probe (PS-73).
#
# WHY A SECOND REALM IS THE ONLY WITNESS. A cloak that captures and assigns a
# BARE `Function.prototype.toString` and one that goes through
# `G.Function.prototype` generate BYTE-IDENTICAL text in every other respect,
# so no substring assertion can tell them apart. The difference is which realm
# the binding resolves in, and that is only observable by running it.
#
# THE MECHANISM THIS MODELS. `worker_wrap.realm_bootstrap_js` chains the
# `contentWindow` accessor and calls `__pnaInstall(childWindow, LEAF)` — where
# LEAF is a PARENT-REALM FUNCTION OBJECT, not source text. Its lexical
# `Function.prototype` is therefore the PARENT's, so a bare binding re-patches
# the parent (a no-op, already patched) and leaves the child realm's own
# pristine while still installing the wrappers there. A detector running in
# that frame then reads raw patch source off the wrapper.
#
# Contrast the WORKER realm, which is correct under BOTH forms and must not be
# "fixed": the leaf crosses into a worker as SOURCE TEXT and is re-evaluated in
# the worker's own realm, so a bare `Function.prototype` resolves to the
# worker's there. Only the frame case is affected.
#
# ⚠️ THE TRAP THAT MAKES THIS PROBE LIE. `vm.createContext(obj)` turns `obj`
# into a VIEW onto the new realm, and that view carries NO `Function` /
# `Object` / `Reflect` of its own — only what a script later assigned to it. A
# real `iframe.contentWindow` IS the child's inner global and does carry them.
# So the child handed to the parent must be the realm's own `globalThis`
# (`vm.runInContext('globalThis', child)`), never the sandbox object. Hand over
# the sandbox object and `G.Function` reads as absent, the cloak takes its
# fail-soft path, and this probe reports a LEAK against a CORRECT fix — the
# same trap `_HARNESS` above records for the single-realm case.
_CHILD_REALM_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const REALM_INIT =
  "globalThis.self = globalThis; globalThis.window = globalThis; globalThis.top = globalThis;";

// --- child realm: stubs only, NO persona script installed directly ----------
const child = {};
vm.createContext(child);
vm.runInContext(REALM_INIT, child);
vm.runInContext(cfg.stubs, child, { filename: 'child-stubs.js' });

// --- parent realm -----------------------------------------------------------
const parent = {};
vm.createContext(parent);
vm.runInContext(REALM_INIT, parent);
vm.runInContext(cfg.stubs, parent, { filename: 'parent-stubs.js' });

// The iframe element whose accessor the bootstrap chains. Its `contentWindow`
// hands back the CHILD realm's own global -- see the trap note in the module.
vm.runInContext(
  `function HTMLIFrameElement() {}
   Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
     configurable: true, enumerable: true,
     get: function () { return globalThis.__CHILD_WINDOW__; },
   });
   Object.defineProperty(HTMLIFrameElement.prototype, 'contentDocument', {
     configurable: true, enumerable: true,
     get: function () { return null; },
   });`,
  parent, { filename: 'iframe-stub.js' }
);
parent.__CHILD_WINDOW__ = vm.runInContext('globalThis', child);

// Install into the PARENT only, exactly as add_init_script / a content script does.
for (const p of cfg.scripts) {
  vm.runInContext(fs.readFileSync(p, 'utf8'), parent, { filename: p });
}

// Touching contentWindow is what fires the chained accessor and carries the
// leaf into the child. A page does this simply by reaching into its iframe.
vm.runInContext('(new HTMLIFrameElement()).contentWindow;', parent);

const result = vm.runInContext(cfg.probe, child, { filename: 'child-probe.js' });
console.log(JSON.stringify({ result: result, type: typeof result }));
"""


def observe_in_child_realm(tmp_path, scripts, stubs, probe):
    """Install `scripts` in a PARENT realm, let the bootstrap carry the leaf into
    a CHILD frame realm, and evaluate `probe` from INSIDE that child.

    This is what a detector running in an iframe reads. `probe` is evaluated in
    the child realm, so `Function.prototype.toString.call(...)` there resolves
    against the CHILD's Function.prototype — the binding under test.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")

    work = pathlib.Path(tmp_path) / "child-probe"
    work.mkdir(parents=True, exist_ok=True)

    harness = work / "harness.js"
    harness.write_text(_CHILD_REALM_HARNESS, encoding="utf-8")
    cfg = work / "cfg.json"
    cfg.write_text(
        json.dumps(
            {"stubs": stubs, "scripts": [str(s) for s in scripts], "probe": probe}
        ),
        encoding="utf-8",
    )

    out = subprocess.run(
        [node, str(harness), str(cfg)],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["result"]


def assert_reads_native_in_child_realm(
    tmp_path, scripts, stubs, probe, expected, *, reached_probe,
    unpatched_observable
):
    """Assert the wrapper reads as native FROM INSIDE A CHILD FRAME, and that the
    child realm was genuinely reached by the patch.

    THE SECOND HALF IS NOT OPTIONAL, and it is what makes this test different
    from a green that means nothing. If the bootstrap never carried the leaf
    into the child at all, the wrapper there is the UNTOUCHED NATIVE STUB —
    which stringifies natively too. The masking assertion would then pass for
    precisely the opposite of the reason wanted: not "the cloak covered the
    child" but "there was nothing in the child to cloak".

    So `reached_probe` reads a VALUE from the same child realm, and it must
    differ from `unpatched_observable` (what that realm reads with no patch
    installed). That pins the patch as present in the child before its
    stringification is allowed to count as evidence.
    """
    reached = observe_in_child_realm(tmp_path, scripts, stubs, reached_probe)
    assert reached != unpatched_observable, (
        "VACUOUS TEST: the patch never reached the child realm at all "
        f"(it reads {reached!r}, the unpatched value). The native-form "
        "assertion below would pass on the untouched built-in, so it would "
        "witness nothing."
    )

    read = observe_in_child_realm(tmp_path, scripts, stubs, probe)
    assert read == expected, (
        f"read from inside a CHILD FRAME, the wrapper stringifies as "
        f"{read!r} rather than the expected native form {expected!r}. A page "
        f"that looks in an iframe sees the patch source — a masking tell."
    )


def assert_reads_native(tmp_path, scripts, stubs, probe, name, *, native_first=True):
    """Assert the wrapper `probe` selects stringifies natively — AND that it does
    so BECAUSE of native_ext's patch.

    Both halves are load-bearing. The first pins the invariant. The second is the
    counterfactual (AC#3): with the cloak absent the same probe must NOT read
    native, which is what binds this test to the mechanism instead of merely
    executing code that happens to be green.

    `probe` must select a wrapper a real extension installs. Do NOT mark a
    hand-rolled function with the marker here: that would hardcode the mechanism
    into the test, so it would go red for a mechanism RENAME rather than for a
    masking regression — the exact defect class PS-17 exists to remove. The marker
    protocol stays private to src/.
    """
    masked = stringify_in_realm(
        tmp_path, scripts, stubs, probe,
        install_native=True, native_first=native_first,
    )
    assert masked == native_form(name), (
        f"wrapper did not stringify as native under "
        f"Function.prototype.toString.call: {masked!r}"
    )

    unmasked = stringify_in_realm(
        tmp_path, scripts, stubs, probe,
        install_native=False, native_first=native_first,
    )
    assert unmasked != native_form(name), (
        "FALSIFICATION FAILED: the wrapper read as native with native_ext's patch "
        "NOT installed, so this test does not actually witness the cloak."
    )


# ==========================================================================
# Observable-divergence probes (PS-63).
#
# The mutual-unlinkability claim ("two profiles are not linkable on this
# vector") is a statement about what a PAGE SEES, so it is asserted on values
# read back after executing the generated script in a realm — never on the
# generated text. Two dead files that merely declare different seed literals are
# still not byte-identical, so a text diff certifies unlinkability for a spoof
# that installs nothing; these helpers cannot.
#
# TWO TRAPS, both of which read as "the spoof is dead" when it is not:
#
#   1. The buffer must be PERTURBABLE. `perturbFloat` skips zeros
#      (audio_ext.py:64 `if (v !== 0 && isFinite(v))`) and `perturbBytes` only
#      nudges bytes where `1 < v < 254` (webgl_ext.py:68). A zero-filled audio
#      buffer or a zero/saturated pixel buffer comes back identical for every
#      seed — a dead STUB reported as a dead spoof. AUDIO_STUBS (above) is
#      zero-filled by design for the toString probe, which needs no values; the
#      observable probes use their own non-zero buffers and leave it alone.
#
#   2. Read the WHOLE buffer, never a sample. `perturbBytes` moves at most
#      `_BUDGET` (512) bytes and picks WHICH ones by ordinal among the eligible
#      bytes, so on any buffer with more content than that the touched offsets
#      are spread thinly and unpredictably across the whole array. Two seeds
#      also agree at some of them by chance. A probe that samples narrowly
#      reports a false negative.
#
#      This used to read "STRIDE is 17, so only indices 0, 17, 34, … are
#      touched". That stride is gone — PS-97 removed it precisely BECAUSE a
#      fixed byte comb is aliased away by a row width the caller chooses, which
#      is how two profiles came to publish one `pixels:` hash. The trap it
#      described is unchanged; only the reason the touched set is sparse is.
# --------------------------------------------------------------------------

# Non-zero float samples: `perturbFloat` skips zeros, so the zero-filled
# AUDIO_STUBS buffer is unperturbable and identical for every seed (Trap 1).
# Values are spread across the buffer so the relative (1e-5) delta is visible at
# every index.
AUDIO_OBSERVABLE_STUBS = r"""
function AudioBuffer() {}
AudioBuffer.prototype.getChannelData = function getChannelData() {
  var d = new Float32Array(64);
  for (var i = 0; i < d.length; i++) { d[i] = 0.25 + i / 128; }
  return d;
};
function AnalyserNode() {}
AnalyserNode.prototype.getFloatFrequencyData = function getFloatFrequencyData() {};
AnalyserNode.prototype.getByteFrequencyData = function getByteFrequencyData() {};
"""

# What a fingerprinter reads off an AudioBuffer: the float samples themselves.
# toPrecision(9) keeps the 1e-5 relative delta legible while staying stable.
# Every sample is rendered — no sampling (Trap 2).
AUDIO_OBSERVABLE_PROBE = r"""
(function () {
  var d = new AudioBuffer().getChannelData(0);
  var out = [];
  for (var i = 0; i < d.length; i++) { out.push(d[i].toPrecision(9)); }
  return out.join(',');
})()
"""

# What a MASKING detector reads: the wrapper's own source, through the
# `.call` form (a per-function `.toString` override does not intercept this).
# Evaluated INSIDE a child frame realm by `observe_in_child_realm`, so
# `Function.prototype` here is the CHILD's — which is the binding under test.
AUDIO_STRINGIFY_PROBE = r"""
Function.prototype.toString.call(AudioBuffer.prototype.getChannelData)
"""

# What a fingerprinter reads off a WebGL context: the pixel bytes. NOTE the
# shape — the stub readPixels is a no-op and the wrapper perturbs the CALLER'S
# buffer in place (argument index 6), so the probe reads back the array it
# passed in, not a return value. The buffer is mid-range (128) because
# perturbBytes skips bytes outside 1 < v < 254 (Trap 1), and 512 bytes are read
# in full so the 31 touched indices are all covered (Trap 2).
GL_OBSERVABLE_PROBE = r"""
(function () {
  var gl = Object.create(WebGLRenderingContext.prototype);
  var px = new Uint8Array(512);
  px.fill(128);
  gl.readPixels(0, 0, 8, 16, 0, 0, px);
  return Array.prototype.join.call(px, ',');
})()
"""

# --------------------------------------------------------------------------
# READBACK stubs (PS-90). A framebuffer faithful enough that the REAL
# `webgl.readback` probe expression runs against it unmodified — the probe under
# test is imported from `PROBES`, never retyped, so these cannot drift from what
# ships.
#
# `GL_STUBS` above is not enough here: its `readPixels` is a no-op, so the
# caller's buffer keeps whatever it was pre-filled with. That models the wrapper
# fine (which is all it was built for) but not the DRAW, and this probe's whole
# claim is about bytes that came out of a rendered surface.
#
# Faithful in the three ways that decide whether the probe works at all:
#   * scissored clears actually write the rect, so the readback carries the
#     probe's own MID-RANGE bands (0.30-0.70 -> 76-178). perturbBytes skips
#     `v <= 1 || v >= 254`, so a stub that left the surface black would make a
#     WORKING spoof read as a dead one.
#   * alpha is written 255 and is therefore skipped, which is why `mid` comes
#     out at exactly 3/4 of the buffer — the same ratio the real engine reports.
#   * `getExtension('WEBGL_lose_context')` returns a working stub and RECORDS
#     the release, so a probe that leaks the context is observable rather than
#     merely disapproved of.
# --------------------------------------------------------------------------

GL_READBACK_STUBS = r"""
globalThis.__released = 0;

function WebGLRenderingContext() {}
function WebGL2RenderingContext() {}

(function () {
  for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
    const P = C.prototype;
    P.RGBA = 0x1908;
    P.UNSIGNED_BYTE = 0x1401;
    P.COLOR_BUFFER_BIT = 0x4000;
    P.SCISSOR_TEST = 0x0C11;
    P.DEPTH_TEST = 0x0B71;

    P.enable = function enable() {};
    P.disable = function disable() {};
    P.scissor = function scissor(x, y, w, h) { this._sc = [x, y, w, h]; };
    P.clearColor = function clearColor(r, g, b, a) { this._cc = [r, g, b, a]; };

    P.clear = function clear() {
      const W = this._w, fb = this._fb, cc = this._cc || [0, 0, 0, 1];
      const sc = this._sc || [0, 0, this._w, this._h];
      const to8 = (v) => Math.max(0, Math.min(255, Math.round(v * 255)));
      const px = [to8(cc[0]), to8(cc[1]), to8(cc[2]), to8(cc[3])];
      for (let y = sc[1]; y < sc[1] + sc[3]; y++) {
        for (let x = sc[0]; x < sc[0] + sc[2]; x++) {
          const i = (y * W + x) * 4;
          fb[i] = px[0]; fb[i + 1] = px[1]; fb[i + 2] = px[2]; fb[i + 3] = px[3];
        }
      }
    };

    // Named `readPixels` so the wrapper inherits the name a detector reads.
    P.readPixels = function readPixels(x, y, w, h, fmt, type, pixels) {
      const W = this._w, fb = this._fb;
      for (let j = 0; j < h; j++) {
        for (let i = 0; i < w; i++) {
          const src = ((y + j) * W + (x + i)) * 4;
          const dst = (j * w + i) * 4;
          pixels[dst] = fb[src]; pixels[dst + 1] = fb[src + 1];
          pixels[dst + 2] = fb[src + 2]; pixels[dst + 3] = fb[src + 3];
        }
      }
      // REARRANGEMENT HOOK: swap two bytes that hold DIFFERENT values, so the
      // buffer's SUM is unchanged and only the arrangement moves. Lets a test
      // ask the shipped probe the one question that separates a
      // position-sensitive digest from a summing one. Off unless asked for.
      if (globalThis.__swap) {
        const i0 = 0, i1 = pixels.length - 4;
        const t = pixels[i0]; pixels[i0] = pixels[i1]; pixels[i1] = t;
      }
    };

    P.getExtension = function getExtension(name) {
      if (name === 'WEBGL_lose_context') {
        return { loseContext: function loseContext() { globalThis.__released++; } };
      }
      return null;
    };
  }
})();

globalThis.document = {
  createElement: function (tag) {
    if (tag !== 'canvas') return {};
    const canvas = { width: 300, height: 150 };
    canvas.getContext = function (kind) {
      if (kind !== 'webgl' && kind !== 'experimental-webgl' && kind !== 'webgl2') {
        return null;
      }
      const gl = Object.create(WebGLRenderingContext.prototype);
      gl._w = canvas.width;
      gl._h = canvas.height;
      gl._fb = new Uint8Array(canvas.width * canvas.height * 4);
      return gl;
    };
    return canvas;
  },
};
"""

# The same realm with NO WebGL available. AC#2: the probe must return null
# rather than throw, matching the other GL probes.
GL_NO_CONTEXT_STUBS = r"""
function WebGLRenderingContext() {}
function WebGL2RenderingContext() {}
globalThis.document = {
  createElement: function () {
    return { width: 300, height: 150, getContext: function () { return null; } };
  },
};
"""

# The SAME vector as GL_OBSERVABLE_PROBE, at the geometry a real fingerprinter
# actually reads it with. PS-97 measured why the difference matters: the probe
# above is 512 bytes ALL of which are 128, so every byte is perturbable and any
# selection scheme whatsoever lands on content. It passed green for months while
# two profiles published a byte-identical `pixels:` hash to CreepJS.
#
# CreepJS reads `readPixels(0, 0, drawingBufferWidth/15, drawingBufferHeight/6)`
# (`src/webgl/index.ts:355`). Off its 256x256 OffscreenCanvas that truncates to
# 17x42 — a 68-byte row — and it clears to (0,0,0,0) because its `clearColor` is
# commented out, so the region is 98.9% zeros with only the antialiased edge of a
# LINE_LOOP in it. Measured on a real engine: 2856 bytes, 32 non-zero, and just
# 16 that pass the `1 < v < 254` guard.
#
# That geometry is hostile in two independent ways, and this fixture reproduces
# BOTH so a regression in either is caught:
#
#   ALIASING — the 68-byte row is exactly 4x the old `_STRIDE` of 17, so a fixed
#   byte-comb visited pixel 0/4/8/12 of every row forever and never reached the
#   content at x=13..16. Any scheme that selects by BYTE OFFSET can be aliased
#   away by a row width chosen by the caller.
#
#   STARVATION — with 16 eligible bytes in 2856, a sparse selector expects about
#   ONE hit. A vector that depends on winning that lottery is not delivering
#   per-profile entropy even on the runs where it happens to work.
#
# Values are the real histogram from that measurement, not invented ones: a run
# of mid-range edge bytes, saturated 255s that the guard must skip, and zeros
# everywhere else.
GL_CREEPJS_OBSERVABLE_PROBE = r"""
(function () {
  var gl = Object.create(WebGLRenderingContext.prototype);
  var W = 17, H = 42;
  var px = new Uint8Array(W * H * 4);
  // Mostly-zero, exactly as CreepJS's cleared corner comes back.
  var edge = [25, 27, 27, 29, 29, 30, 31, 33, 76, 76, 76, 76, 76, 79, 81, 83];
  // Put the content where the drawn edge really is: the last rows, at x=13..16,
  // i.e. precisely the columns a 17-byte comb over a 68-byte row cannot reach.
  var at = 0;
  for (var row = H - 4; row < H; row++) {
    for (var col = 13; col < 17; col++) {
      var base = (row * W + col) * 4;
      px[base] = edge[at % edge.length];
      px[base + 1] = 255;  // saturated: the guard must skip these
      at++;
    }
  }
  gl.readPixels(0, 0, W, H, 0, 0, px);
  return Array.prototype.join.call(px, ',');
})()
"""


def gl_budget_probe(eligible: int) -> str:
    """A probe that returns HOW MANY bytes the readback perturbation moved.

    Every other probe here asks WHETHER the value changed. None of them can see
    how LOUD the change is, and that gap is not hypothetical: PS-97 round 1
    shipped a 2x budget overshoot green, because `floor(eligible / BUDGET)`
    collapses to a stride of 1 for every `eligible` in [BUDGET, 2*BUDGET) and
    then moves EVERY eligible byte while claiming a cap. At 1023 eligible bytes
    it moved 1023 of them. No divergence assertion goes red for that — the bytes
    still differ per seed, just far more of them than intended.

    That matters because the module's constraint is two-sided. Too FEW bytes
    moved and two profiles collide (the linkability defect this ticket exists
    for); too MANY and the perturbation is itself the tell, on the very vector
    being perturbed to avoid being noticed. `_BUDGET` is the ceiling on the
    second side, so it needs a seat of its own.

    The buffer is all-128 so every byte is eligible and `eligible` is exactly
    the buffer length — the count is then unambiguous rather than a function of
    the fixture's histogram.
    """
    return r"""
(function () {
  var gl = Object.create(WebGLRenderingContext.prototype);
  var n = %d;
  var px = new Uint8Array(n);
  px.fill(128);
  gl.readPixels(0, 0, n / 4, 1, 0, 0, px);
  var moved = 0;
  for (var i = 0; i < n; i++) { if (px[i] !== 128) moved++; }
  return moved;
})()
""" % int(eligible)


# The falsification (PS-63 AC#4): a spoof that DECLARES its seed and installs
# nothing. This is what the replaced text assertions could not tell apart from
# the real thing — `"987654" in js` passes on it, and two of these with
# different seeds are not byte-identical so `a != b` passes too.
_DEAD_SPOOF = "(function(){ var SEED = %d; })();\n"


def dead_spoof_script(tmp_path, seed: int):
    """Write the neutered counterpart of a generated spoof and return its path."""
    work = pathlib.Path(tmp_path) / f"dead-{seed}"
    work.mkdir(parents=True, exist_ok=True)
    path = work / "dead.js"
    path.write_text(_DEAD_SPOOF % seed, encoding="utf-8")
    return path


def observe_in_realm(tmp_path, script, stubs, probe):
    """Execute one generated spoof in a fresh realm and return the observable.

    native_ext is deliberately NOT loaded (`install_native=False`): the cloak is
    irrelevant to what values a page reads back, and leaving it out keeps this
    probe measuring the spoof under test alone.
    """
    return stringify_in_realm(
        tmp_path, [script], stubs, probe, install_native=False
    )


def assert_seed_changes_observable(tmp_path, build, filename, stubs, probe,
                                   seeds=(111, 222)):
    """Assert two seeds produce DIFFERENT observable output — and that they do so
    because the spoof runs, not because the two files differ as text.

    Both halves are load-bearing, mirroring assert_reads_native. The first pins
    per-profile divergence in the value a fingerprinter reads. The second is the
    counterfactual: with the spoof neutered (seed declared, nothing installed)
    the same probe must read the SAME output for both seeds. A test that skipped
    it would be green against a spoof that patches nothing — the exact defect
    PS-63 exists to remove.
    """
    a_seed, b_seed = seeds

    def observe(seed):
        ext = pathlib.Path(build(seed, str(pathlib.Path(tmp_path) / f"ext-{seed}")))
        return observe_in_realm(
            pathlib.Path(tmp_path) / f"run-{seed}", ext / filename, stubs, probe
        )

    a, b = observe(a_seed), observe(b_seed)
    assert a != b, (
        f"seeds {a_seed} and {b_seed} produced IDENTICAL observable output, so a "
        f"page can link the two profiles on this vector: {a!r}"
    )

    dead_a = observe_in_realm(
        pathlib.Path(tmp_path) / "dead-run-a",
        dead_spoof_script(tmp_path, a_seed), stubs, probe,
    )
    dead_b = observe_in_realm(
        pathlib.Path(tmp_path) / "dead-run-b",
        dead_spoof_script(tmp_path, b_seed), stubs, probe,
    )
    assert dead_a == dead_b, (
        "FALSIFICATION FAILED: two seeds diverged with NO spoof installed, so "
        "this probe is reading something other than the spoof and would stay "
        "green against a dead one."
    )
    assert a != dead_a, (
        "FALSIFICATION FAILED: the spoofed observable equals the unspoofed one, "
        "so the generated script changed nothing a page can read."
    )


def assert_profiles_unlinkable(tmp_path, build, filename, stubs, probe,
                               seeds=(111, 222, 333)):
    """Assert several profiles are mutually unlinkable on this vector — the
    Level-2 claim, stated about what a page sees rather than about file bytes.

    Also pins REPRODUCIBILITY: one seed built twice must observe identically.
    Divergence alone is satisfied by randomness, which would make a profile
    unrecognisable to itself across sessions; the property wanted is a stable
    per-profile value that differs between profiles.

    Counterfactual: the same seeds with the spoof neutered must all COLLAPSE to
    one observable, which is what makes the pairwise-distinctness above evidence
    about the spoof rather than about the test.
    """
    def observe(seed, tag):
        ext = pathlib.Path(build(seed, str(pathlib.Path(tmp_path) / f"ext-{tag}")))
        return observe_in_realm(
            pathlib.Path(tmp_path) / f"run-{tag}", ext / filename, stubs, probe
        )

    observed = {seed: observe(seed, f"{seed}-1") for seed in seeds}

    for i, a in enumerate(seeds):
        for b in seeds[i + 1:]:
            assert observed[a] != observed[b], (
                f"profiles {a} and {b} are LINKABLE: both observe {observed[a]!r}"
            )

    first = seeds[0]
    assert observe(first, f"{first}-2") == observed[first], (
        f"profile {first} observed differently on a second build — the vector is "
        f"random, not per-profile, so a profile cannot be recognised as itself."
    )

    dead = {
        seed: observe_in_realm(
            pathlib.Path(tmp_path) / f"dead-run-{seed}",
            dead_spoof_script(tmp_path, seed), stubs, probe,
        )
        for seed in seeds
    }
    assert len(set(dead.values())) == 1, (
        "FALSIFICATION FAILED: neutered spoofs observed differently per seed, so "
        "this probe reads something other than the spoof."
    )
    assert set(observed.values()).isdisjoint(dead.values()), (
        "FALSIFICATION FAILED: a spoofed profile observes exactly what the dead "
        "spoof does, so the generated script changed nothing a page can read."
    )
