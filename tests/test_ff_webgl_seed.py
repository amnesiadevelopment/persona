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
from src.services.browser.worker_wrap import (
    firefox_worker_cloak,
    realm_bootstrap_js,
)


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
    ).read_text(encoding="utf-8")
    for shared in (
        "var SEED = 4242;",
        "var BUDGET = 512;",
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
            [node, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8"
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


# --- the BOOTSTRAP's own wrappers must be cloaked too, on Firefox -----------
#
# ROUND 2 SHIPPED A TELL HERE, and the seat above is the reason it got through.
# `test_firefox_carries_the_cloak_and_chromiums_marker_is_absent` slices the
# LEAF out first:
#
#     leaf = js.split("function applyWebglPatch(G)", 1)[1].split("var SELF =", 1)[0]
#
# `var SELF =` is exactly where the shared bootstrap BEGINS, so the assertion
# stops one line before the copy of the marker that survived — it measures the
# region that was already fixed. This PR is the first thing to deliver
# `realm_bootstrap_js` to Firefox at all, and the bootstrap installs wrappers of
# its OWN (Worker, SharedWorker, the two HTMLIFrameElement accessors) quite apart
# from the leaf's. On an engine with no extension to read `__pnaName`, those were
# left carrying a bare own property no browser has, and stringifying as raw patch
# source where every real engine returns `[native code]`.
#
# So this seat reads the WHOLE generated script, and it EXECUTES rather than
# greps: it installs the real script in a node:vm realm, carries it across two
# worker generations, and stringifies each wrapper from inside the realm a
# detector would run in. `readPixels` is the POSITIVE CONTROL — the cloak should
# always cover it, so a run where the control fails is a broken harness rather
# than a finding.


# --- the CSP blocker, executed rather than read -----------------------------
#
# THE DEFECT THIS SEAT EXISTS FOR, and why every earlier round missed it.
#
# The bootstrap carried the boot payload into a `blob:` worker by reading the
# body with a SYNCHRONOUS XHR inside the Worker constructor. That XHR is subject
# to the page's `connect-src`. On an origin whose CSP forbids it the XHR throws,
# the wrapper's own `catch` falls back to constructing the ORIGINAL Worker, and
# the result is the worst available shape: A WORKER THAT RUNS NORMALLY AND
# CARRIES NO SPOOF. Two profiles then read the SAME readPixels digest in the
# worker realm -- the exact linkability webgl_ext.py exists to prevent, in
# exactly the realm its docstring names as the one detectors read.
#
# AND IT IS THE DEFAULT START PAGE. `_ensure_firefox_policies()` pins
# DuckDuckGo, which sends `default-src 'none'` with no `blob:` in connect-src.
# So the affected origin is where every Firefox profile already is the moment it
# opens -- not somewhere reached by browsing anywhere unusual.
#
# EVERY PRIOR MEASUREMENT RAN ON `example.com` OR A LOCAL ORIGIN, which ship no
# CSP, and reported success on a profile that collides in production. That is
# PS-11's third shape arriving from a new direction: not a vacuous test, but a
# CORRECT test on the WRONG ORIGIN. Hence the parameter below -- the seat is run
# on both, and the no-CSP arm is what keeps the CSP arm honest.
#
# Measured on the real engine (recorded on the ticket): with the fix, seeds 1000
# and 2000 read DIFFERENT worker digests on duckduckgo.com; without it, both read
# 3113650808, which is byte-for-byte the value they read with the mechanism
# switched off entirely.


def _run_csp_worker_case(csp_blocks_blob_xhr: bool, cloak=None) -> dict:
    """Execute the real generated bootstrap against an XHR that behaves the way a
    restrictive ``connect-src`` makes it behave, and report whether the LEAF
    ARRIVED in the worker and whether the ORIGINAL BODY RAN.

    Both facts, independently, because they fail apart and only one of the two
    failures is loud: the CSP regression produced a worker that ran the original
    body perfectly and carried no spoof at all.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the generated bootstrap")

    leaf = "function applyWebglPatch(G){ try { G.__WEBGL_SEED__ = 4242; } catch (e) {} }\n"
    bootstrap = realm_bootstrap_js(
        "applyWebglPatch", cloak if cloak is not None else firefox_worker_cloak()
    )

    harness = r"""
const vm = require("node:vm");
const CSP_BLOCKS = %(csp)s;
const BLOBS = new Map();      // live object URLs -> Blob object
let counter = 0;

// A faithful-enough Blob: it keeps its PARTS, and composing a Blob out of other
// Blobs concatenates their bodies. That composition is the whole mechanism under
// test -- `new Blob([BOOT, retainedBlob])` -- so modelling it as a plain string
// join would test nothing.
const BLOB_SRC = `
  function Blob(parts) {
    var out = "";
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      out += (p && typeof p === "object" && typeof p.__text === "string")
        ? p.__text : String(p);
    }
    this.__text = out;
  }
`;

function makeRealm() {
  const sandbox = { Reflect, WeakSet, WeakMap, Map };
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(BLOB_SRC + `
    var __n = 0;
    function Worker(url, options) { this.url = url; }
    function SharedWorker(url, options) { this.url = url; }
    function XMLHttpRequest() {}
    XMLHttpRequest.prototype.open = function (m, u) { this._u = u; };
    XMLHttpRequest.prototype.send = function () {
      // THE CSP. A restrictive connect-src refuses the fetch outright: the send
      // THROWS, it does not return a status. That is what the wrapper's catch
      // swallows before falling back to an unspoofed worker.
      if (__CSP_BLOCKS && /^blob:/i.test(this._u)) {
        var e = new Error("NetworkError"); e.name = "NetworkError"; throw e;
      }
      var b = __BLOB_GET(this._u);
      if (b === undefined) { this.status = 0; this.responseText = ""; return; }
      this.status = 200; this.responseText = b;
    };
  `, ctx);
  ctx.__CSP_BLOCKS = CSP_BLOCKS;
  ctx.__BLOB_GET = (u) => (BLOBS.has(u) ? BLOBS.get(u).__text : undefined);
  // URL lives outside the sandbox source so the host Map is the single registry
  // every realm shares -- that is what makes a url minted in the page realm
  // resolvable when the worker realm is built from it. The two host callables
  // must be INSTALLED BEFORE the source that closes over them is evaluated.
  ctx.__COU = (b) => { const u = "blob:pna-" + (++counter); BLOBS.set(u, b); return u; };
  ctx.__ROU = (u) => { BLOBS.delete(u); };
  vm.runInContext("var URL = { createObjectURL: __COU, revokeObjectURL: __ROU };", ctx);
  return ctx;
}

const page = makeRealm();
vm.runInContext("(function(){" + %(leaf)s + %(bootstrap)s + "})();", page);

const ORIGINAL_BODY = "self.__BODY_RAN__ = true;";
vm.runInContext(
  "var u = URL.createObjectURL(new Blob([" + JSON.stringify(ORIGINAL_BODY) + "]));" +
  "var w = new self.Worker(u);" +
  "globalThis.__spawned = w.url;",
  page);

const spawnedUrl = vm.runInContext("globalThis.__spawned", page);
const workerBody = BLOBS.has(spawnedUrl) ? BLOBS.get(spawnedUrl).__text : null;

const out = { bodyRan: false, leafArrived: false, workerResolvable: workerBody !== null };
if (workerBody !== null) {
  const worker = makeRealm();
  try { vm.runInContext(workerBody, worker); } catch (e) { out.threw = String(e); }
  out.bodyRan = vm.runInContext("self.__BODY_RAN__ === true", worker);
  out.leafArrived = vm.runInContext("self.__WEBGL_SEED__ === 4242", worker);
}
console.log(JSON.stringify(out));
""" % {
        "csp": "true" if csp_blocks_blob_xhr else "false",
        "leaf": json.dumps(leaf),
        "bootstrap": json.dumps(bootstrap),
    }

    with tempfile.TemporaryDirectory() as d:
        script = pathlib.Path(d, "csp_harness.js")
        script.write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("csp_blocks_blob_xhr", [False, True])
def test_the_spoof_reaches_a_worker_even_when_csp_forbids_the_fetch(csp_blocks_blob_xhr):
    """THE BLOCKER THAT FAILED QA, pinned behaviourally and on BOTH origins.

    The no-CSP arm is not padding: it is what distinguishes "the fix works" from
    "the harness cannot see anything", and it is the arm every earlier round ran
    exclusively.
    """
    out = _run_csp_worker_case(csp_blocks_blob_xhr)

    assert out["workerResolvable"], "the wrapper produced no runnable worker body"
    assert out["bodyRan"], (
        "the ORIGINAL worker body did not run — a functional break, which is the "
        "regression that killed the importScripts shim in round 2"
    )
    assert out["leafArrived"], (
        "the spoof did not reach the worker realm: the worker runs UNSPOOFED, so "
        "two profiles read the same WebGL digest there — on the DEFAULT start "
        "page, whose CSP forbids the sync XHR the shared path uses"
    )


def test_that_seat_goes_red_without_the_firefox_delivery():
    """THE CONTROL FOR THE SEAT ABOVE — without it the green means nothing.

    Reruns the identical harness with ONLY the delivery seam removed (the round-3
    form: Firefox's cloak seams intact, ``blob_resolve`` empty, so the shared
    sync-XHR path is what runs). Under a CSP the leaf must then FAIL to arrive,
    and — the diagnostic half — the worker must still RUN, because "runs normally
    and carries no spoof" is precisely the shape that made this defect invisible.

    Without CSP the same stripped form must still deliver, which is what proves
    the red above is caused by the CSP and not by the strip.
    """
    stripped = firefox_worker_cloak()._replace(blob_resolve="")

    blocked = _run_csp_worker_case(True, cloak=stripped)
    assert not blocked["leafArrived"], (
        "the seat cannot go red: the leaf arrived even with the delivery seam "
        "removed, so it is not the seam that is being measured"
    )
    assert blocked["bodyRan"], (
        "expected the WORST shape — a worker that runs and carries no spoof; a "
        "dead worker would be a different (louder) defect"
    )

    ok = _run_csp_worker_case(False, cloak=stripped)
    assert ok["leafArrived"], (
        "the stripped form must still deliver WITHOUT a CSP — otherwise the red "
        "above is caused by the strip rather than by the CSP"
    )


def _cloak_report(js: str) -> dict:
    """Install ``js`` in a node:vm realm and report, per realm, how each wrapper
    stringifies and whether it carries an own ``__pnaName``.

    THE WRAPPER SET IS DERIVED, NOT LISTED, and that is the whole point of this
    helper. It walks every function-valued slot reachable from the realm global
    BEFORE the script runs and again AFTER, and reports every slot whose function
    IDENTITY changed. A wrapper is, by definition, a slot the script replaced —
    so the thing being enumerated is the script's own EFFECT ON THE REALM, which
    is also exactly what a detector enumerates.

    Three consecutive rounds of this ticket were rejected for the same class of
    defect, each time because a check carried its own copy of the wrapper set:

      r2  the seat sliced the leaf out at ``var SELF =`` — the region boundary
          excluded the bootstrap, so the bootstrap's bare marker was invisible
      r3  the report named only the wrappers a human had called out, and the two
          iframe accessors were bare
      r4  the probe list was a fixed tuple, and the two wrappers that round ADDED
          (``URL.createObjectURL`` / ``URL.revokeObjectURL``) were not in it

    A set restated in a second place cannot be kept in sync by discipline; it
    goes stale in whichever copy someone forgets, and here the forgotten copy was
    the one doing the asserting. Deriving it means a wrapper added WITHOUT a
    cloak fails automatically, with nobody remembering to extend a tuple.

    Three realms: the page, a depth-1 worker, and a depth-2 worker (a worker
    spawning a worker) — the last because a silently-uncovered depth-2 realm
    reporting REAL values is the failure this module's docstring records.
    """
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment-dependent
        pytest.skip("node is required to execute the generated bootstrap")

    harness = r"""
const vm = require("node:vm");
const JS = %(js)s;

// Walk every function-valued slot reachable from the realm global, one level
// into each object/constructor and its prototype, and return [{path, fn}].
// ACCESSORS are collected as their getter, because two of the wrappers this
// script installs ARE getters and a value-only walk would silently miss them.
const WALK = `
  (function () {
    var out = [];
    var seen = new WeakSet();
    function members(path, obj) {
      if (!obj || seen.has(obj)) return;
      try { seen.add(obj); } catch (e) { return; }
      var names;
      try { names = Object.getOwnPropertyNames(obj); } catch (e) { return; }
      for (var i = 0; i < names.length; i++) {
        var n = names[i];
        // Poison pills on function objects in strict mode.
        if (n === "caller" || n === "callee" || n === "arguments") continue;
        var d;
        try { d = Object.getOwnPropertyDescriptor(obj, n); } catch (e) { continue; }
        if (!d) continue;
        if (typeof d.get === "function") {
          out.push({ path: path + "." + n + " (get)", fn: d.get });
        } else if (d.value && typeof d.value === "function") {
          out.push({ path: path + "." + n, fn: d.value });
        }
      }
    }
    var top;
    try { top = Object.getOwnPropertyNames(self); } catch (e) { top = []; }
    for (var i = 0; i < top.length; i++) {
      var n = top[i];
      // The realm's self-references, and this harness's own snapshot slot.
      if (n === "self" || n === "globalThis" || n === "__pnaSnap") continue;
      var v;
      try { v = self[n]; } catch (e) { continue; }
      if (v === null || v === undefined) continue;
      if (typeof v === "function") {
        out.push({ path: n, fn: v });
        members(n, v);
        try { if (v.prototype) members(n + ".prototype", v.prototype); } catch (e) {}
      } else if (typeof v === "object") {
        members(n, v);
      }
    }
    return out;
  })()
`;

// Probe a single wrapper. Uses indexOf on the literal rather than a regex: this
// source is inside a JS TEMPLATE LITERAL, so the backticks eat one level of
// escaping before a regex is parsed, and a singly-escaped /\[native code\]/
// degrades into a CHARACTER CLASS that matches almost any source text — a probe
// that reports every wrapper as cloaked and turns the whole seat green for
// nothing. There is no escaping to get wrong in a plain substring search.
const PROBE_FN = `
  (function (label, fn) {
    var src = "";
    try { src = Function.prototype.toString.call(fn); } catch (e) { src = "<threw>"; }
    return {
      wrapper: label,
      readsNative: src.indexOf("[native code]") >= 0,
      hasMarker: Object.prototype.hasOwnProperty.call(fn, "__pnaName"),
      // String.fromCharCode(10) rather than a newline escape, for the same
      // reason the [native code] test above is an indexOf: this source sits
      // inside a template literal, so a backslash escape is consumed BEFORE the
      // JS here is parsed. A charCode has no escaping to get wrong -- and note
      // this comment may not spell the escape either, for the same reason.
      head: src.slice(0, 80).split(String.fromCharCode(10)).join(" "),
    };
  })
`;

function makeRealm() {
  const sandbox = { Reflect, WeakSet, WeakMap };
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(`
    var __blobs = {}; var __n = 0;
    function Worker(url, options) { this.url = url; }
    function SharedWorker(url, options) { this.url = url; }
    function WebGLRenderingContext() {}
    WebGLRenderingContext.prototype.readPixels = function readPixels(){};
    function WebGL2RenderingContext() {}
    WebGL2RenderingContext.prototype.readPixels = function readPixels(){};
    function HTMLIFrameElement() {}
    Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow",
      { configurable: true, get: function contentWindow() { return null; } });
    Object.defineProperty(HTMLIFrameElement.prototype, "contentDocument",
      { configurable: true, get: function contentDocument() { return null; } });
    function Blob(parts) { this.text = String(parts[0]); }
    var URL = {
      createObjectURL: function (b) { var u = "blob:s/" + (++__n); __blobs[u] = b.text; return u; },
      revokeObjectURL: function () {},
    };
    function XMLHttpRequest() {}
    XMLHttpRequest.prototype.open = function (m, u) { this._u = u; };
    XMLHttpRequest.prototype.send = function () {
      this.status = 200;
      this.responseText = __blobs[this._u] !== undefined ? __blobs[this._u] : "";
    };
  `, ctx);
  return ctx;
}

// Snapshot -> run `source` -> report every slot whose function identity MOVED.
// The snapshot lives on the realm global under a name WALK skips, so taking it
// cannot perturb the very comparison it feeds.
function installAndDerive(ctx, source) {
  const before = vm.runInContext(WALK, ctx);
  const beforeByPath = new Map();
  for (const row of before) beforeByPath.set(row.path, row.fn);

  vm.runInContext(source, ctx);

  const after = vm.runInContext(WALK, ctx);
  const probe = vm.runInContext(PROBE_FN, ctx);
  const rows = [];
  for (const row of after) {
    const prior = beforeByPath.get(row.path);
    // Unchanged identity => this script did not install here.
    if (prior === row.fn) continue;
    const out = probe(row.path, row.fn);
    out.added = !beforeByPath.has(row.path);
    rows.push(out);
  }
  return rows;
}

// Construct a worker through the installed wrapper and return the payload the
// wrapper actually handed the engine. Re-evaluating it in a fresh realm IS the
// worker: that is the only way to see whether the cloak CROSSED, which is a
// property of __pnaInstall.toString() and not of the page realm.
function workerBody(ctx) {
  return vm.runInContext(`
    (function () {
      var u = URL.createObjectURL(new Blob(["/*orig*/"]));
      var w = new self.Worker(u);
      return __blobs[w.url] || null;
    })()`, ctx);
}

const out = {};
const page = makeRealm();
out.page = installAndDerive(page, JS);

const b1 = workerBody(page);
let w1 = null;
if (b1) { w1 = makeRealm(); out.worker1 = installAndDerive(w1, b1); }
else { out.worker1 = null; }

const b2 = w1 ? workerBody(w1) : null;
if (b2) { const w2 = makeRealm(); out.worker2 = installAndDerive(w2, b2); }
else { out.worker2 = null; }

console.log(JSON.stringify(out));
""" % {"js": json.dumps(js)}

    with tempfile.TemporaryDirectory() as d:
        script = pathlib.Path(d, "cloak_harness.js")
        script.write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8"
        )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_no_wrapper_the_firefox_script_installs_is_detectable():
    """Every wrapper the Firefox script installs must stringify as native and
    carry no own marker, in every realm the script reaches.

    THE WRAPPER SET IS DERIVED FROM THE SCRIPT'S EFFECT ON THE REALM (see
    :func:`_cloak_report`), not from a list kept here. That is the point: this
    seat's claim is a UNIVERSAL one, and a universal claim checked against a
    hand-maintained tuple is a promise the code cannot keep — it is green
    because of what it omits. Three rounds of this ticket were rejected for
    exactly that, the last one because the two wrappers that round ADDED were
    not on the list the check carried.

    So a wrapper added without a cloak fails HERE, automatically, with nobody
    remembering to extend anything.
    """
    report = _cloak_report(firefox_webgl_init_script(1234))

    for realm in ("page", "worker1", "worker2"):
        rows = report[realm]
        assert rows, f"{realm}: no wrapper moved — the script did not install"

        # POSITIVE CONTROL first. The leaf's own wrapper must always be among the
        # derived set and must always be cloaked, so a control failure means the
        # harness is broken and every other reading in this realm is meaningless.
        control = next(
            (r for r in rows if r["wrapper"].endswith(".readPixels")), None
        )
        assert control is not None, (
            f"{realm}: the POSITIVE CONTROL (readPixels) is not in the derived "
            f"set — the harness is broken, not the product. Derived: "
            f"{[r['wrapper'] for r in rows]}"
        )
        assert control["readsNative"] and not control["hasMarker"], (
            f"{realm}: the POSITIVE CONTROL (readPixels) is not cloaked — the "
            f"harness is broken, not the product: {control}"
        )

        # SECOND CONTROL: the derivation must reach past the leaf. If only
        # readPixels ever moved, the walk is not seeing the bootstrap's wrappers
        # and every "no bare wrapper" verdict below would be vacuously true —
        # which is precisely how r2's leaf-slice boundary passed.
        assert len(rows) > 1, (
            f"{realm}: the derived set is the leaf alone, so the bootstrap's own "
            f"wrappers are invisible to this walk: {[r['wrapper'] for r in rows]}"
        )

        for row in rows:
            name = row["wrapper"]
            assert not row["hasMarker"], (
                f"{realm}: {name} carries an own __pnaName — a property no "
                f"browser has, on an engine with no extension to read it"
            )
            assert row["readsNative"], (
                f"{realm}: {name}.toString() returns patch source where every "
                f"real engine returns [native code]: {row['head']!r}"
            )


def test_the_derived_wrapper_set_actually_covers_the_new_wrappers():
    """The derivation is only worth having if it SEES what a list would forget.

    Round 4 added ``URL.createObjectURL`` / ``URL.revokeObjectURL`` and the
    hand-written tuple did not mention them. Pin that the derived set reaches
    them, so a future refactor that narrows the walk (back to globals-only, say,
    or to value properties only — which would drop both iframe ACCESSORS) fails
    here rather than silently shrinking the universal claim above into a smaller
    one that still passes.
    """
    report = _cloak_report(firefox_webgl_init_script(4321))
    derived = {r["wrapper"] for r in report["page"]}

    for expected in (
        "URL.createObjectURL",       # round 4 added these two
        "URL.revokeObjectURL",
        "Worker",                    # round 2 left these two bare
        "SharedWorker",
        "HTMLIFrameElement.prototype.contentWindow (get)",   # round 3, accessors
        "HTMLIFrameElement.prototype.contentDocument (get)",
    ):
        assert expected in derived, (
            f"the walk no longer reaches {expected!r}, so the seat above has "
            f"quietly stopped checking it. Derived: {sorted(derived)}"
        )


def test_chromiums_bootstrap_keeps_its_marker():
    """The other side of the seam: Chromium's wrappers MUST keep ``__pnaName``.

    That marker is not incidental there — ``native_ext.py`` installs the single
    ``Function.prototype.toString`` patch that reads it, so dropping it would
    uncloak every Chromium wrapper. Pins that the Firefox fix did not "clean up"
    the shared default, which is the way this refactor would most plausibly break
    the engine it was told not to touch.
    """
    code = _code_only(realm_bootstrap_js("applyWebglPatch"))
    assert '"__pnaName"' in code, "Chromium's bootstrap lost its cloak marker"
    # and the Firefox form must NOT be what Chromium gets
    assert "__bcloak" not in code
