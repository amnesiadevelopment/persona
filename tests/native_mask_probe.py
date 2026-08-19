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
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["result"]


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
