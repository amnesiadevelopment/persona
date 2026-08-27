"""The realm bootstrap: what a page can SEE, and where the leaves actually RUN.

Every assertion here is either an EXECUTION result or an ABSENCE claim. That is
deliberate and it is the point of PS-48.

The suite this replaced was mostly substring checks over the generated JS
(`assert "__pnaBoots.push(applyGpuPatch)" in js`). Those pinned one
*implementation* of realm coverage rather than realm coverage itself, and they
were structurally unable to see the defect PS-48 fixes: the generated text said
`self.__pnaBoots=[...]` in all the right places — so every string matched —
while that same text published each leaf's SOURCE, and with it the profile seed
compiled inside it, under a readable global name. A detector reading
`__pnaBootSrc` got positive tool identification AND the seed in one property
read, and not one string assertion could tell.

So the invariants pinned below are:

  1. COVERAGE — each module's leaf actually runs in the page realm, in a worker,
     in a worker spawned by that worker (depth 2), at depth 3, and in a child
     frame. Asserted by RUNNING the generated bootstrap in isolated `node:vm`
     realms, one context per realm, so a leaf can only arrive by being genuinely
     transported.
  2. INVISIBILITY — from inside any of those realms, enumerating the global
     object yields nothing that identifies the tool, and no readable property
     holds the seed.
  3. COMPOSITION — N modules each reach every child realm. This is the property
     that forced a shared registry in the old design (a per-module *guard* on
     HTMLIFrameElement let only the first module win the single getter), so it
     is the one most at risk from the rewrite and it is tested with two modules
     throughout, never one.
  4. COST — the payload does not grow with worker depth. The naive way to carry
     N leaves across a realm boundary re-embeds the accumulated source at every
     generation, which is exponential in depth.

Harness shape follows the in-tree precedent rather than inventing one
(`shutil.which` -> `pytest.skip` -> subprocess over a `node:vm` harness; see
`tests/native_mask_probe.py`, which makes the same argument for asserting a
runtime property instead of a marker string).
"""

import json
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import realm_bootstrap_js
from tests.realm_harness import HARNESS


# --------------------------------------------------------------------------
# Absence claims. These need no JS engine and they are the PS-48 invariant in
# its cheapest form: a name that is not emitted cannot be enumerated.
# --------------------------------------------------------------------------

# Every global the pre-PS-48 bootstrap defined on the realm. `__pnaBootSrc` is
# the one that carried the seed; the rest are each a positive tool tell.
_RETIRED_GLOBALS = (
    "__pnaBoots",
    "__pnaBootSrc",
    "__pnaBootInstalled",
    "__pnaBooted",
    "__pnaFramed",
)


@pytest.mark.parametrize("name", _RETIRED_GLOBALS)
def test_no_registry_global_is_emitted(name):
    # PS-48: a page identified persona in one property read, and recovered the
    # profile seed from __pnaBootSrc while it was there.
    js = realm_bootstrap_js("applyGpuPatch")
    assert name not in js, (
        f"{name} is back on the global object: a page can enumerate it, and "
        "__pnaBootSrc additionally hands over the seed as leaf source text"
    )


def test_registry_state_is_not_assigned_to_the_realm():
    # Stronger than the name check above and the reason a RENAME is not a fix:
    # no state of any name is parked on the realm object. The bootstrap's only
    # contact with the global is reading built-ins and installing the wrappers
    # it must install (Worker/SharedWorker/HTMLIFrameElement accessors).
    js = realm_bootstrap_js("applyGpuPatch")
    # SELF is resolved once, to hand the realm to the installer...
    assert js.count("var SELF =") == 1
    # ...and never written to. `SELF.x = ...` is what PS-48 removes.
    assert "SELF." not in js.replace("SELF, %s" % "applyGpuPatch", ""), (
        "the bootstrap assigns to the realm object; PS-48 requires the "
        "registry to live in closure"
    )


def test_installer_is_a_named_function_expression():
    # Regression, carried over from the old suite because the failure it
    # prevents is unchanged: the installer's own serialized body must re-bind
    # itself by name, because that body is what crosses into a worker and has to
    # keep covering the worker's OWN children. A bare anonymous `(fn)(self)`
    # left it unresolvable there, so a NESTED worker's wrapper threw and ran
    # completely unspoofed (a creepjs GPU tell).
    js = realm_bootstrap_js("applyGpuPatch")
    assert "function __pnaInstall(G, LEAF)" in js
    assert "__pnaInstall.toString()" in js


def test_bootstrap_is_syntactically_balanced():
    js = realm_bootstrap_js("applyFoo")
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")


# --------------------------------------------------------------------------
# Execution. One node:vm context per realm — a realm genuinely cannot see
# another's globals, so a leaf only arrives if it was really transported.
# --------------------------------------------------------------------------

# The realm machinery (makeRealm/spawn/BLOBS) is the SHARED harness in
# tests/realm_harness.py — PS-68 reuses it to ask the same realms a different
# question (does the toString cloak still render native at depth), rather than
# standing up a second one that could drift from this.
_PROBE = HARNESS + r"""
const BOOTSTRAP_A = fs.readFileSync(process.argv[2], "utf8");
const BOOTSTRAP_B = fs.readFileSync(process.argv[3], "utf8");

// Two leaves from two separate "content scripts": the multi-module case. Each
// marks the realm it reaches, and each carries a distinct fake SEED compiled
// INSIDE the function body — exactly as gpu_ext.py:36 and webgl_ext.py:31-32
// do it, and for the same reason (a var in the enclosing scope is undefined in
// the worker realm). So these stand in for the real seed-bearing leaves.
const SEED_A = 1234567, SEED_B = 7654321;
const LEAF_A = "function applyAlpha(G){ try { var SEED = " + SEED_A + "; G.__ALPHA__ = SEED; } catch (e) {} }\n";
const LEAF_B = "function applyBeta(G){ try { var SEED = " + SEED_B + "; G.__BETA__ = SEED; } catch (e) {} }\n";

// Each *_ext.py content script wraps its leaf + bootstrap in an IIFE. Reproduce
// that: at top level `var __pnaInstall` would become a global property and the
// invisibility assertions would measure the harness instead of the product.
function asContentScript(leaf, bootstrap) {
  return "(function(){" + leaf + bootstrap + "})();";
}


// What this realm ended up with. `alpha`/`beta` are OBSERVABLES — did the leaf
// run here — not a count of some registry. `globals`/`seedFindable` are what a
// detector standing in this realm can enumerate.
function report(realm) {
  return vm.runInContext(
    "({alpha: self.__ALPHA__ === " + SEED_A + ", beta: self.__BETA__ === " + SEED_B + "," +
    " globals: Object.getOwnPropertyNames(self).filter(function(k){ return /pna|persona|boot/i.test(k); })," +
    " seedFindable: (function(){ var hits=[];" +
    "   Object.getOwnPropertyNames(self).forEach(function(k){" +
    "     try { var v = self[k];" +
    "       if (typeof v === 'string' && /" + SEED_A + "|" + SEED_B + "/.test(v)) hits.push(k);" +
    "       if (Array.isArray(v)) { try { if (/" + SEED_A + "|" + SEED_B + "/.test(v.join(','))) hits.push(k); } catch(e){} }" +
    "       if (typeof v === 'function') { try { if (/" + SEED_A + "|" + SEED_B + "/.test(v.toString())) hits.push(k); } catch(e){} }" +
    "     } catch (e) {}" +
    "   }); return hits; })()})",
    realm.ctx);
}

const page = makeRealm();
vm.runInContext(asContentScript(LEAF_A, BOOTSTRAP_A), page.ctx);
vm.runInContext(asContentScript(LEAF_B, BOOTSTRAP_B), page.ctx);

const out = { page: report(page), payloads: {}, depths: {} };

let parent = page;
for (let depth = 1; depth <= 3; depth++) {
  const payload = spawn(parent);
  out.payloads["d" + depth] = { length: payload.length };
  const child = makeRealm();
  vm.runInContext(payload, child.ctx);
  out.depths["d" + depth] = report(child);
  parent = child;
}

// Child-frame path: touch the accessor twice, as a page does, so the dedup that
// stops a leaf re-running on every property read is exercised too.
out.iframe = (function () {
  const realm = makeRealm();
  vm.runInContext(
    "globalThis.__child = { name: 'child' };" +
    "globalThis.HTMLIFrameElement = function HTMLIFrameElement(){};" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow'," +
    "  { configurable:true, get: function(){ return globalThis.__child; } });" +
    "Object.defineProperty(HTMLIFrameElement.prototype,'contentDocument'," +
    "  { configurable:true, get: function(){ return { defaultView: globalThis.__child }; } });",
    realm.ctx);
  vm.runInContext(asContentScript(LEAF_A, BOOTSTRAP_A), realm.ctx);
  vm.runInContext(asContentScript(LEAF_B, BOOTSTRAP_B), realm.ctx);
  return vm.runInContext(
    "(function(){ var f = new HTMLIFrameElement();" +
    " var w1 = f.contentWindow; var w2 = f.contentWindow;" +
    " return { alpha: __child.__ALPHA__ === " + SEED_A + "," +
    "          beta: __child.__BETA__ === " + SEED_B + "," +
    "          childGlobals: Object.getOwnPropertyNames(__child)" +
    "            .filter(function(k){ return /pna|persona|boot/i.test(k); }) }; })()",
    realm.ctx);
})();

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def realms(tmp_path_factory):
    """Run the generated bootstrap down three worker generations plus a child
    frame, and report what each realm actually ended up with."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = tmp_path_factory.mktemp("realm_probe")
    (d / "boot_a.js").write_text(realm_bootstrap_js("applyAlpha"), encoding="utf-8")
    (d / "boot_b.js").write_text(realm_bootstrap_js("applyBeta"), encoding="utf-8")
    (d / "probe.js").write_text(_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(d / "probe.js"), str(d / "boot_a.js"), str(d / "boot_b.js")],
        capture_output=True, text=True, timeout=120,
     encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- coverage --------------------------------------------------------------

def test_both_leaves_run_in_the_page_realm(realms):
    # the control: two modules, two content scripts, both leaves applied.
    assert realms["page"]["alpha"] is True
    assert realms["page"]["beta"] is True


@pytest.mark.parametrize("depth", ["d1", "d2", "d3"])
def test_every_leaf_runs_at_every_worker_depth(realms, depth):
    # The invariant worker_wrap.py exists for, and the one that a rewrite is
    # most likely to break silently: a realm that receives nothing does not
    # throw, it just reports the REAL GPU/hardwareConcurrency/audio/fonts while
    # the page reports spoofed ones. depth>=2 is the case that regressed before
    # (a nested worker with an empty registry, a creepjs GPU tell).
    r = realms["depths"][depth]
    assert r["alpha"] is True, f"{depth}: module A's leaf never ran — realm UNSPOOFED"
    assert r["beta"] is True, f"{depth}: module B's leaf never ran — realm UNSPOOFED"


def test_both_leaves_reach_a_child_frame(realms):
    # COMPOSITION: this is the property that justified a shared registry. A
    # per-module guard on HTMLIFrameElement would let only the FIRST module win
    # the single getter, so `beta` here is the assertion that chaining really
    # does compose across modules.
    assert realms["iframe"]["alpha"] is True
    assert realms["iframe"]["beta"] is True, (
        "only one module reached the child frame: the iframe accessors are "
        "guarding instead of chaining, and every later module's spoof is absent"
    )


# --- invisibility (PS-48) --------------------------------------------------

def test_page_realm_exposes_no_tool_marker(realms):
    assert realms["page"]["globals"] == []


@pytest.mark.parametrize("depth", ["d1", "d2", "d3"])
def test_worker_realms_expose_no_tool_marker(realms, depth):
    assert realms["depths"][depth]["globals"] == []


def test_child_frame_exposes_no_tool_marker(realms):
    assert realms["iframe"]["childGlobals"] == []


def test_the_seed_is_not_recoverable_from_any_realm(realms):
    # THE PS-48 ASSERTION. Before this change every one of these lists came back
    # ["__pnaBoots", "__pnaBootSrc"] — verified in a real browser, page realm and
    # worker realm alike: the leaf source was readable by name, and the seed is
    # compiled inside the leaf, so one property read yielded both "this is
    # persona" and the integer that determines the entire presented machine.
    assert realms["page"]["seedFindable"] == [], "the page can recover the seed"
    for depth in ("d1", "d2", "d3"):
        assert realms["depths"][depth]["seedFindable"] == [], (
            f"{depth} can recover the seed"
        )


# --- cost ------------------------------------------------------------------

def test_payload_does_not_grow_with_worker_depth(realms):
    # Carrying N leaves across a realm boundary by re-embedding the accumulated
    # source at each generation is exponential in depth: a page that nests
    # workers a few deep would ship megabytes. Each chain link must contribute
    # only its OWN fragment, once.
    p = realms["payloads"]
    assert p["d2"]["length"] <= 2 * p["d1"]["length"]
    assert p["d3"]["length"] <= 2 * p["d1"]["length"]
    # in fact it is stable, not merely bounded
    assert p["d1"]["length"] == p["d2"]["length"] == p["d3"]["length"]
