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
#   2. Read the WHOLE buffer, never a sample. webgl_ext's STRIDE is 17, so only
#      indices 0, 17, 34, … are touched at all, and two seeds agree at many of
#      them (index 0 agrees for 111/222). A probe that samples narrowly reports
#      a false negative.
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
