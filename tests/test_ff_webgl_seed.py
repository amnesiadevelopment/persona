"""PS-78: the Firefox arm of the WebGL readPixels vector, and its delivery.

Three defects were filed together because they share one delivery path:

  1. ``build_webgl_extension`` has a single call site (``process.py:503``) that
     sits ~150 lines AFTER ``spawn_browser`` returns on the Firefox arm, so the
     WebGL delta was UNREACHABLE on that engine — the strings were spoofed per
     seed and the pixels were not.
  2. ``add_init_script`` does not reach RESTORED tabs, so the locale and
     outer-size overrides were present on a first launch and absent on every
     restart.
  3. The realm bootstrap's ``blob:`` branch silently produced an UNSPOOFED
     worker on Firefox.

What each test PINS is the observable a detector reads, not the text this
implementation happens to emit. The behavioural proof — real launches, real
restarts, two profiles — is recorded on the ticket; these are the fast
regression seats around the parts that are decidable without a browser.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from src.services.browser.webgl_ext import (
    build_webgl_extension,
    firefox_webgl_init_script,
)
from src.services.browser.worker_wrap import realm_bootstrap_js


def _code_only(js: str) -> str:
    """Drop ``//`` comment text.

    These scripts are heavily commented, and several of the comments NAME the
    very construct the assertion is checking for the absence of (the data:
    branch's comment explains why it does not use importScripts). Asserting over
    raw text would match the explanation and report a defect that is not there —
    which is exactly the "assert on what was written, not on what happens" trap
    PS-11 catalogues. Compare CODE.
    """
    out = []
    for line in js.splitlines():
        i = line.find("//")
        out.append(line[:i] if i >= 0 else line)
    return "\n".join(out)


# --- the Firefox script exists, is self-contained, and carries the seed ------


def test_firefox_script_is_self_contained_and_seed_bearing():
    """It is evaluated as one expression, both by add_init_script and by the
    restored-tab replay, so it must be a single self-invoking unit."""
    js = firefox_webgl_init_script(0xDEADBEEF)
    assert js.startswith("(function(){")
    assert js.rstrip().endswith("})();")
    assert "readPixels" in js
    assert "applyWebglPatch" in js


def test_the_seed_reaches_the_generated_patch():
    """Two seeds must produce different text — the necessary (not sufficient)
    condition for two profiles reading different pixels. The SUFFICIENT one is
    behavioural and is measured on a real launch; see the ticket."""
    assert firefox_webgl_init_script(1) != firefox_webgl_init_script(2)


def test_same_seed_is_stable():
    """A vector that varies per launch is not an identity: it would make a
    profile unrecognisable to itself across a restart, trading unlinkability for
    the restart-continuity outcome."""
    assert firefox_webgl_init_script(999) == firefox_webgl_init_script(999)


def test_seed_is_masked_into_range():
    """The seed is masked to 32 bits, so equivalent seeds must agree rather than
    emitting an out-of-range literal into the JS."""
    assert firefox_webgl_init_script(0) == firefox_webgl_init_script(2**32)
    assert firefox_webgl_init_script(-1) == firefox_webgl_init_script(2**32 - 1)


def test_firefox_carries_the_cloak_and_chromiums_marker_is_absent():
    """Firefox loads NO persona extension, so ``__pnaName`` has nobody to read
    it: on that engine the marker is not a cloak, it is a bare own property on
    every wrapper — a tell rather than a hiding place. The Firefox script must
    carry its own closure-WeakMap cloak instead.

    Pinned as ABSENCE of the marker, mirroring the standard already set by
    tests/test_ff_language_override.py, which asserts ``"__pnaName" not in``.
    """
    js = _code_only(firefox_webgl_init_script(7))
    # The leaf ends where the shared realm bootstrap begins (`var SELF =`).
    # NOT split on "__pnaInstall": that name appears inside the cloak's own
    # comment, so splitting there truncates the leaf mid-cloak and the test
    # measures the wrong region.
    leaf = js.split("function applyWebglPatch(G)", 1)[1].split("var SELF =", 1)[0]
    # the leaf's own body must not stamp the Chromium marker onto wrappers
    assert "__pnaName" not in leaf
    assert "WeakMap" in leaf
    # SpiderMonkey's native shape, not V8's one-liner: emitting V8's form on
    # Firefox is itself a masking tell.
    assert "[native code]" in leaf


def test_firefox_and_chromium_share_the_perturbation():
    """A profile's WebGL identity must not depend on which engine it launched.

    The two engines differ ONLY in the cloak seam; everything that computes the
    perturbation (seed mixing, stride, byte nudging, the readPixels overrides) is
    one shared body. Pinned by comparing the parts that must agree.
    """
    ff = firefox_webgl_init_script(4242)
    d = tempfile.mkdtemp()
    chrome = (
        pathlib.Path(build_webgl_extension(4242, str(pathlib.Path(d) / "ext")))
        / "webgl.js"
    ).read_text()
    for shared in (
        "var SEED = 4242;",
        "var STRIDE = 17;",
        "function perturbBytes(buf)",
        "proto.readPixels = nativeWrap(orig,",
    ):
        assert shared in ff, f"firefox script lost {shared!r}"
        assert shared in chrome, f"chromium script lost {shared!r}"


# --- the boundary: Chromium must not move -----------------------------------


def test_chromium_extension_text_is_stable_across_seeds():
    """Regression seat for the boundary: the same seed must always produce the
    same Chromium bytes, and equivalent (masked) seeds must agree.

    The full byte-identity check against the pre-refactor tree was run at
    development time across 11 seeds including the boundary values (0, 1,
    2**31-1, 2**31, 2**32-1, 2**32, -1); it is recorded on the ticket. This seat
    keeps the property from drifting afterwards.
    """
    def text(seed):
        d = tempfile.mkdtemp()
        return (
            pathlib.Path(build_webgl_extension(seed, str(pathlib.Path(d) / "e")))
            / "webgl.js"
        ).read_bytes()

    for seed in (0, 1, 2**31 - 1, 2**31, 2**32 - 1, 123456789):
        assert text(seed) == text(seed), f"seed {seed} is not deterministic"
    # masking equivalences
    assert text(0) == text(2**32)
    assert text(-1) == text(2**32 - 1)
    # and the seed genuinely reaches the output
    assert text(1) != text(2)


def test_both_engines_share_one_worker_delivery_path():
    """THE BOUNDARY THAT MATTERS MOST for the worker fix, and the seat that
    stops the deleted shim coming back.

    An earlier revision of PS-78 gave Firefox its own ``blob:`` branch behind a
    ``blob_via_import_scripts`` flag, on the belief that this engine refuses a
    synchronous XHR against a ``blob:`` URL. THE PREMISE WAS FALSE (measured:
    status 200 through the real launch path on a real https origin, and the
    locale spoof at ``invisible_launch.py:504`` has shipped that same sync-XHR
    path in production all along). The shim also BROKE workers whose URL is
    revoked after construction — see
    ``test_a_blob_worker_survives_revoke_after_construction``.

    So there is ONE branch, and both engines get identical text. Chromium's
    generated bytes were verified unchanged against the pre-PS-78 tree across 11
    seeds (including 0, 2**31, 2**32-1, -1) and all 13 leaf consumers; that
    check is recorded on the ticket, and this seat keeps the shape from drifting.
    """
    for leaf in (
        "applyWebglPatch",
        "applyGpuPatch",
        "applyAudioPatch",
        "applyLocalePatch",
        "applyNativePatch",
    ):
        code = _code_only(realm_bootstrap_js(leaf))
        # ONE combined branch, read through a sync XHR, on every engine.
        assert "/^blob:|^data:/i.test(s)" in code
        assert "bbody" not in code, f"{leaf}: the deleted firefox shim is back"

    # The Firefox script is the SAME bootstrap, not a per-engine variant.
    ff = _code_only(firefox_webgl_init_script(5))
    assert "/^blob:|^data:/i.test(s)" in ff
    assert "/^blob:/i.test(s)" not in ff.replace("/^blob:|^data:/i.test(s)", "")


# --- the worker fix, executed rather than read ------------------------------
#
# PS-11: the seats above assert on TEXT THIS CODE GENERATED, which is exactly
# what let the shim ship — its source said "importScripts" as intended while the
# worker it produced never ran. The seats below EXECUTE the generated bootstrap
# against a worker whose blob URL is revoked right after construction, and read
# a value back out. That is the ordering the shim died on, and nothing that
# merely inspects source can see it.


def _run_worker_case(revoke: bool) -> dict:
    """Execute the real generated bootstrap in node, spawn a blob: worker, and
    report whether the ORIGINAL BODY RAN and whether the LEAF ARRIVED.

    Models the two facts independently, because they fail apart: the shim's
    regression was a worker that carried the spoof correctly in the no-revoke
    case and did not run AT ALL in the revoke case.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the generated bootstrap")

    leaf = "function applyWebglPatch(G){ try { G.__WEBGL_SEED__ = 4242; } catch (e) {} }\n"
    bootstrap = realm_bootstrap_js("applyWebglPatch")

    harness = r"""
const vm = require("node:vm");
const REVOKE = %(revoke)s;
const BLOBS = new Map();      // live object URLs -> body text
let counter = 0;

function makeRealm() {
  const captured = [];
  const sandbox = {
    Reflect, WeakSet,
    URL: {
      createObjectURL: (b) => {
        const u = "blob:pna-" + (++counter);
        BLOBS.set(u, b.__parts.join(""));
        captured.push(u);
        return u;
      },
      // The whole point: revoking DROPS the mapping, so anything that reads the
      // URL later - an importScripts shim inside the worker - finds nothing.
      revokeObjectURL: (u) => { BLOBS.delete(u); },
    },
    Blob: function Blob(parts) { this.__parts = parts; },
    Worker: function Worker(url) { this.url = url; },
    XMLHttpRequest: function XMLHttpRequest() {
      const me = this;
      this.status = 0; this.responseText = "";
      this.open = function (m, u) { me.__u = u; };
      this.send = function () {
        if (BLOBS.has(me.__u)) { me.status = 200; me.responseText = BLOBS.get(me.__u); }
        else { me.status = 0; me.responseText = ""; }   // revoked: gone
      };
    },
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);
  return { ctx, captured };
}

const page = makeRealm();
vm.runInContext("(function(){" + %(leaf)s + %(bootstrap)s + "})();", page.ctx);

// The page builds a blob: worker and (optionally) revokes the URL immediately,
// which is the pattern MDN documents and bundlers emit.
const ORIGINAL_BODY = "self.__BODY_RAN__ = true;";
vm.runInContext(
  "var u = self.URL.createObjectURL(new self.Blob([" + JSON.stringify(ORIGINAL_BODY) + "]));" +
  "var w = new self.Worker(u);" +
  (REVOKE ? "self.URL.revokeObjectURL(u);" : "") +
  "globalThis.__spawned = w.url;",
  page.ctx);

// Whatever the wrapper handed the engine is the worker's real body. Run it in a
// fresh realm - that realm IS the worker.
const spawnedUrl = vm.runInContext("globalThis.__spawned", page.ctx);
const workerBody = BLOBS.has(spawnedUrl) ? BLOBS.get(spawnedUrl) : null;

const out = { bodyRan: false, leafArrived: false, workerResolvable: workerBody !== null };
if (workerBody !== null) {
  const worker = makeRealm();
  // importScripts is how a shim would pull the original body in at STARTUP;
  // against a revoked URL there is nothing to pull, which is the regression.
  vm.runInContext(
    "var importScripts = function(u){ if (!__BLOBS_HAS(u)) { throw new Error('SecurityError'); } __EVAL(__BLOBS_GET(u)); };",
    worker.ctx);
  worker.ctx.__BLOBS_HAS = (u) => BLOBS.has(u);
  worker.ctx.__BLOBS_GET = (u) => BLOBS.get(u);
  worker.ctx.__EVAL = (src) => vm.runInContext(src, worker.ctx);
  try { vm.runInContext(workerBody, worker.ctx); } catch (e) {}
  out.bodyRan = vm.runInContext("self.__BODY_RAN__ === true", worker.ctx);
  out.leafArrived = vm.runInContext("self.__WEBGL_SEED__ === 4242", worker.ctx);
}
console.log(JSON.stringify(out));
""" % {
        "revoke": "true" if revoke else "false",
        "leaf": json.dumps(leaf),
        "bootstrap": json.dumps(bootstrap),
    }

    with tempfile.TemporaryDirectory() as d:
        script = pathlib.Path(d, "harness.js")
        script.write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60
        )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("revoke", [False, True])
def test_a_blob_worker_survives_revoke_after_construction(revoke):
    """THE REGRESSION THAT REJECTED THE FIRST ROUND, pinned behaviourally.

    ``const w = new Worker(url); URL.revokeObjectURL(url);`` is the ordering MDN
    documents and bundlers emit. The importScripts shim DEFERRED reading the
    original body into the worker's own startup, so by the time it looked, the
    URL was gone: ``SecurityError``, swallowed by the shim's own ``catch``, and
    the worker ran NOTHING. Measured on the real engine at the time: zero events.

    The surviving path reads the body SYNCHRONOUSLY, inside the constructor,
    while the URL is still valid — so BOTH orderings keep the original body AND
    carry the spoof. Both halves are asserted: a worker that is spoofed but dead
    is not a pass.
    """
    out = _run_worker_case(revoke)
    assert out["workerResolvable"], "the wrapper produced no runnable worker body"
    assert out["bodyRan"], (
        "the ORIGINAL worker body did not run — a functional break, which is "
        "worse than the leak it would be trading against"
    )
    assert out["leafArrived"], "the spoof did not reach the worker realm"
