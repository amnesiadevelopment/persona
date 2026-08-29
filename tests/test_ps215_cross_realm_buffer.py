"""PS-215: the readback perturbation must not brand-check its buffer per-realm.

THE DEFECT. ``perturbBytes`` gated its pixel buffer with
``buf instanceof Uint8Array``. ``instanceof`` compares against the
``Uint8Array`` binding the RUNNING COPY OF THE LEAF closed over, so its answer
depends on which realm that copy was evaluated in. A detector that allocates
its buffer in one realm and calls ``readPixels`` on a context belonging to
ANOTHER fails the check, and the readback is returned UNPERTURBED.

That is exactly how CreepJS reads us: it builds the pixel buffer in the TOP
realm and reads from its phantom iframe realm's context. Measured on real
Firefox (loopback, the committed ``readings/ps193-2026-08-26/realm_probe.py``),
one run, one instant, differing ONLY in which realm allocated the buffer::

    new win.Uint8Array(...)  (child realm)  -> 855826239   perturbed
    new Uint8Array(...)      (top realm)    -> 660023932   UNPERTURBED

WHY THIS WAS MISDIAGNOSED, which is the part worth keeping. A missing-delivery
defect and this one produce the PUBLISHED PS-193 TABLE IDENTICALLY — phantom
realm seed-invariant while the page realm moves with the seed. The reading
could not distinguish them because it never asked whether the child realm's
``readPixels`` was ALREADY patched. It was: the wrapper was installed there and
RAN (admitted once out of two calls) and then declined. So the symptom reads as
"the spoof never arrived" while the spoof is present and refusing.

The tests below are therefore written to separate those two explanations by
construction: the leaf is installed in the child realm in EVERY case here, so a
green result can only mean the buffer was admitted, and a red one can only mean
the brand check rejected it. Delivery is not a variable in this file.
"""

import json
import shutil
import subprocess

import pytest

from src.services.browser.webgl_ext import _CHROMIUM_NATIVE_WRAP, _webgl_patch_js

_SEED_A = 1337
_SEED_B = 4242

# A two-realm world, built to be faithful in the ONE way this defect depends on:
# the child realm has its OWN `Uint8Array` intrinsic, so a buffer allocated in
# the parent is not an `instanceof` match there — which is the whole mechanism.
#
# The leaf is evaluated INSIDE the child realm, i.e. the child's `readPixels` is
# genuinely patched. That is what makes these tests about the brand check rather
# than about delivery.
_HARNESS = r"""
const vm = require("node:vm");
const IN = JSON.parse(require("fs").readFileSync(0, "utf8"));

function fnv1a(buf) {
  let h = 2166136261;
  for (let i = 0; i < buf.length; i++) h = Math.imul(h ^ buf[i], 16777619);
  return h >>> 0;
}

// One realm with a fake GL context whose readPixels copies a fixed base image.
function makeRealm(src, base) {
  const sb = {};
  vm.createContext(sb);
  vm.runInContext(`
    globalThis.self = globalThis; globalThis.window = globalThis;
    globalThis.__base = null;
    function C1() {}
    C1.prototype.readPixels = function (x,y,w,h,f,t,px) { px.set(globalThis.__base); };
    globalThis.WebGLRenderingContext = C1;
  `, sb);
  vm.runInContext("globalThis.__base = new Uint8Array(" + JSON.stringify(base) + ");", sb);
  vm.runInContext(src, sb);
  return sb;
}

// Read the CHILD's context with a buffer allocated in `allocRealm`.
function readCrossRealm(child, allocRealm, len) {
  // Hand the child realm a buffer built by the OTHER realm's constructor.
  const buf = vm.runInContext("new Uint8Array(" + len + ")", allocRealm);
  child.__foreign = buf;
  return vm.runInContext(`
    (function () {
      var px = globalThis.__foreign;
      new globalThis.WebGLRenderingContext().readPixels(0,0,0,0,0,0,px);
      return Array.from(px);
    })()
  `, child);
}

const out = {};
const base = IN.base;

// The child realm, leaf installed, one per seed.
const childA = makeRealm(IN.srcA, base);
const childB = makeRealm(IN.srcB, base);
// A separate realm that only ALLOCATES. No leaf, no GL -- it stands in for the
// page realm CreepJS builds its buffer in.
const alloc = vm.createContext({});
vm.runInContext("globalThis.self = globalThis;", alloc);

// 1. SAME-REALM read (the buffer is the child's own). This always worked and is
//    the control: it must keep working, byte for byte.
out.sameRealmA = vm.runInContext(`
  (function () { var px = new Uint8Array(${base.length});
    new globalThis.WebGLRenderingContext().readPixels(0,0,0,0,0,0,px);
    return Array.from(px); })()
`, childA);
out.sameRealmB = vm.runInContext(`
  (function () { var px = new Uint8Array(${base.length});
    new globalThis.WebGLRenderingContext().readPixels(0,0,0,0,0,0,px);
    return Array.from(px); })()
`, childB);

// 2. CROSS-REALM read -- CreepJS's shape. The buffer comes from `alloc`.
out.crossRealmA = readCrossRealm(childA, alloc, base.length);
out.crossRealmB = readCrossRealm(childB, alloc, base.length);

out.base = base;
out.digests = {
  base: fnv1a(base),
  sameA: fnv1a(out.sameRealmA), sameB: fnv1a(out.sameRealmB),
  crossA: fnv1a(out.crossRealmA), crossB: fnv1a(out.crossRealmB),
};
console.log(JSON.stringify(out));
"""


def _base_image(n=2912):
    """Bytes the perturbation is allowed to touch (it skips 0/1 and 254/255)."""
    return [(i * 7 + 40) % 200 + 20 for i in range(n)]


@pytest.fixture(scope="module")
def readings():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    payload = {
        "srcA": _webgl_patch_js(_SEED_A, _CHROMIUM_NATIVE_WRAP),
        "srcB": _webgl_patch_js(_SEED_B, _CHROMIUM_NATIVE_WRAP),
        "base": _base_image(),
    }
    out = subprocess.run(
        [node, "-e", _HARNESS],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_probe_actually_perturbs_in_the_simple_case(readings):
    """Control. If the same-realm read were already inert, every assertion below
    would pass for the wrong reason."""
    d = readings["digests"]
    assert d["sameA"] != d["base"], "the leaf did not perturb even same-realm"
    assert d["sameB"] != d["base"]


def test_a_buffer_from_another_realm_is_still_perturbed(readings):
    """THE PS-215 ASSERTION.

    The context belongs to the child realm; the buffer was allocated in another.
    This is CreepJS's exact shape, and before the fix it returned the base image
    untouched — the detector receiving unperturbed pixels while the page realm
    reported spoofed ones.
    """
    d = readings["digests"]
    assert d["crossA"] != d["base"], (
        "a cross-realm buffer came back UNPERTURBED — the brand check rejected "
        "it, so a detector reading from a child realm receives the real pixels"
    )
    assert d["crossB"] != d["base"]


def test_two_seeds_stay_unlinkable_across_a_realm_boundary(readings):
    """The Level 2 property the vector exists for. Equal digests at two seeds is
    the collision PS-16 Table 2 records live (`webgl_pixel_hash` reading one
    value for four distinct seeds), and it is what made two profiles linkable."""
    d = readings["digests"]
    assert d["crossA"] != d["crossB"], (
        "two seeds produced the SAME cross-realm digest — the profiles are "
        "linkable to each other in exactly the realm CreepJS reads"
    )


def test_the_cross_realm_result_matches_the_same_realm_result(readings):
    """The perturbation must not merely be non-zero across a realm boundary — it
    must be the SAME perturbation. A realm-dependent delta would itself be a
    fingerprint (page and child disagreeing is a sharper tell than no spoof, per
    worker_wrap.py's own docstring)."""
    assert readings["crossRealmA"] == readings["sameRealmA"]
    assert readings["crossRealmB"] == readings["sameRealmB"]


def test_a_non_byte_buffer_is_still_declined(readings):
    """The guard was widened across REALMS, not across TYPES. It must still
    decline anything that is not byte pixel data — a Float32Array readback is
    not this vector's business and perturbing it would corrupt real content."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    src = _webgl_patch_js(_SEED_A, _CHROMIUM_NATIVE_WRAP)
    # The patch source arrives on STDIN, not on the command line.
    #
    # It used to be inlined via `json.dumps(src)`, which made the argument
    # 37,602 chars against Windows' CreateProcess ceiling of 32,767 — so this
    # test died with `FileNotFoundError: [WinError 206]` before node started,
    # and the brand check it exists to verify was never executed there.
    #
    # The `readings` fixture above already reads its payload with
    # `readFileSync(0, "utf8")`; this is the same mechanism, so the probe's
    # command line is now a fixed ~700 chars regardless of how large the
    # generated patch grows.
    probe = (
        "const vm=require('node:vm');"
        "const src=require('fs').readFileSync(0,'utf8');"
        "const sb={};vm.createContext(sb);"
        "vm.runInContext(`globalThis.self=globalThis;globalThis.window=globalThis;"
        "function C1(){};C1.prototype.readPixels=function(x,y,w,h,f,t,px){"
        "  for(var i=0;i<px.length;i++)px[i]=100;};"
        "globalThis.WebGLRenderingContext=C1;`,sb);"
        "vm.runInContext(src,sb);"
        "const alloc={};vm.createContext(alloc);"
        "const f=vm.runInContext('new Float32Array(64)',alloc);"
        "sb.__foreign=f;"
        "const r=vm.runInContext(`(function(){var px=globalThis.__foreign;"
        "  new globalThis.WebGLRenderingContext().readPixels(0,0,0,0,0,0,px);"
        "  var all100=true; for(var i=0;i<px.length;i++) if(px[i]!==100) all100=false;"
        "  return all100;})()`,sb);"
        "console.log(JSON.stringify({untouched:r}));"
    )
    out = subprocess.run(
        [node, "-e", probe], input=src,
        capture_output=True, text=True, timeout=60, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["untouched"] is True, (
        "a Float32Array was perturbed — the guard was widened across types, not "
        "just across realms"
    )
