"""PS-215: what the DOM-INSERTION wrappers cost at 13 riders.

WHY THIS FILE EXISTS, AND WHY ``test_ps215_tostring_chain.py`` DOES NOT COVER
IT. That suite pins depth-invariance for ``Function.prototype.toString``, and
explicitly for ``toString`` only. Both chains are thirteen deep for the same
reason — ``WorkerCloak.setup`` and the reach are spliced INSIDE
``__pnaInstall``, which all thirteen riders carry — but the two chains have
completely different cost shapes, and a result measured on one does NOT
transfer to the other:

  * each ``toString`` link is an O(1) delegation;
  * each INSERTION link would otherwise run ``collectFrames``, i.e. a
    ``querySelectorAll`` walk over the whole inserted subtree.

So the module docstring's "a marked hit answered at the innermost link measured
FASTER than an unmarked passthrough (372ns vs 426ns)" is a true statement about
the ``toString`` chain and says nothing about this one. This file measures the
axis that was never measured.

THE DEFECT THIS PINS (reported by review, reproduced before it was fixed). With
no guard, thirteen riders scanned the same subtree thirteen times for ONE native
insertion — a 200-node subtree was walked 199 times at N=1 and 2587 times at
N=13, identically on both engine arms.

⚠️ WHY THAT IS A MASKING BUG AND NOT A PERFORMANCE NOTE. This is the reason the
file sits in the PS-215 set rather than in a benchmark. ``appendChild``,
``insertBefore`` and the ``innerHTML`` setter are the hottest and most heavily
probed functions in the DOM, and the cloak beside them goes to real lengths to
close their STATIC tells — native ``toString``, no ``prototype`` own property,
copied arity, no ``__pnaName`` marker. A 13x O(subtree) amplification is a
DYNAMIC tell on those same functions: a detector needs a large subtree and a
clock, and no own-property or ``toString`` comparison at all. PS-215's AC2
forbids trading the Level 2 failure for a fresh detectable tell, so a timing
signature installed by the fix lands on AC2 exactly as a marker would.

⚠️ WHY THE OBVIOUS FIX IS THE WRONG ONE, MEASURED RATHER THAN ARGUED. "Wrap the
prototype once per realm" removes twelve wrappers and with them twelve scans —
and twelve SPOOFS. Each rider's wrapper closes over its OWN ``LEAF``, so
wrap-once reaches the phantom realm with 1 of 13 leaves installed: PS-215's own
defect, reintroduced in the name of closing a tell. ``test_wrap_once_would_
break_delivery`` is that measurement, kept as an executable arm so the rejected
design cannot quietly come back. The redundant thing is the SCAN; the WRAPPERS
are not redundant.

SCOPE — SINGLE REALM, COUNTS NOT NANOSECONDS. The DOM here is a shim, so its
``querySelectorAll`` is synthetic and its wall-clock is meaningless. Scans per
insertion and nodes walked are the robust numbers and are what this file
asserts. Cross-realm intrinsic identity is out of scope for the same reason the
``toString`` suite gives: ``node:vm`` does not expose a context's intrinsics, so
such a probe reads ``undefined`` and produces a FALSE FAIL that looks exactly
like a masking regression. That question needs a real browser.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import firefox_worker_cloak, realm_bootstrap_js

# ONE owner for each shared fact. The rider count and the DOM shim are imported
# rather than copied: a second copy of either would drift from the tree it is
# supposed to describe, which is the failure the count pin exists to prevent.
from tests.test_ps215_indexed_frame_reach import _DOM
from tests.test_ps215_tostring_chain import _RIDER_COUNT

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the bootstrap"
)

# The subtree the probe inserts. Big enough that a 13x walk is unmistakable
# rather than in the noise, and it is the NODES-WALKED figure that scales with
# it — the scan COUNT is what this file pins, and that is size-independent.
_SUBTREE = 200


def _anchored(text: str, anchor: str, replacement: str) -> str:
    """Replace ``anchor`` exactly once, or fail loudly.

    Same discipline as ``_mutate`` in the ``toString`` suite: an edit whose
    anchor has silently stopped matching produces a probe that measures
    something other than what it claims, and a green result from it is worse
    than no result.
    """
    n = text.count(anchor)
    assert n == 1, (
        f"ANCHOR BROKEN: {anchor!r} occurs {n} times, expected exactly 1. This "
        f"edit does not do what it says, so any result derived from it is void. "
        f"Update the anchor to match the source."
    )
    return text.replace(anchor, replacement)


def _counting_dom() -> str:
    """The shared DOM shim, with both ``querySelectorAll`` sites counted.

    Instrumented here rather than in the shared shim so the reach suite keeps
    measuring behaviour with nothing extra in it.
    """
    dom = _DOM
    count = "    globalThis.__scans = (globalThis.__scans || 0) + 1;\n"
    for holder in ("Element", "DocumentFragment"):
        anchor = (
            f"{holder}.prototype.querySelectorAll = function querySelectorAll(sel) {{\n"
            f"    return findFrames(this); }};"
        )
        replacement = (
            f"{holder}.prototype.querySelectorAll = function querySelectorAll(sel) {{\n"
            f"{count}    return findFrames(this); }};"
        )
        dom = _anchored(dom, anchor, replacement)
    # Count the nodes each scan actually visits, so the cost is reported in the
    # unit that scales with subtree size rather than only as a call count.
    dom = _anchored(
        dom,
        "      for (var i = 0; i < n.childNodes.length; i++) {\n        var c = n.childNodes[i];",
        "      for (var i = 0; i < n.childNodes.length; i++) {\n"
        "        globalThis.__walked = (globalThis.__walked || 0) + 1;\n"
        "        var c = n.childNodes[i];",
    )
    # Count real insertions, to prove the guard suppresses SCANS and never the
    # native call the page asked for.
    dom = _anchored(
        dom,
        "  function adopt(parent, n) {",
        "  function adopt(parent, n) {\n"
        "    globalThis.__native = (globalThis.__native || 0) + 1;",
    )
    # A text node, which the shared shim has no need for (the reach suite only
    # ever inserts elements and fragments). Added here because the fast-path
    # this file asserts — `collectFrames` bailing on `nodeType` before it
    # touches the DOM — is only observable on a node that is neither.
    dom = _anchored(
        dom,
        "  globalThis.Node = Node;",
        "  function Text(t) { Node.call(this); this.nodeType = 3; this.data = t; }\n"
        "  Text.prototype = Object.create(Node.prototype);\n"
        "  Text.prototype.constructor = Text;\n"
        "  globalThis.Text = Text;\n"
        "  globalThis.Node = Node;",
    )
    return dom


_PROBE = r"""
const vm = require("vm");
const fs = require("fs");
const BOOT = fs.readFileSync(process.argv[2], "utf8");
const DOM = fs.readFileSync(process.argv[3], "utf8");
const N = parseInt(process.argv[4], 10);
const SIZE = parseInt(process.argv[5], 10);

const sandbox = { Reflect, WeakSet, WeakMap, console };
const ctx = vm.createContext(sandbox);
vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);
vm.runInContext(DOM, ctx);
vm.runInContext("function applyPatch(G) { try { G.__LEAF__ = 1; } catch (e) {} }", ctx);

// N riders, each an IIFE -- how the product splices it (see native_ext.
// CONTENT_SCRIPT). Running it bare would make the bootstrap's own vars global
// and the probe would report a disclosure the product does not have.
for (let i = 0; i < N; i++) {
  vm.runInContext("(function () {\n" + BOOT + "\n})();", ctx);
}

const out = vm.runInContext(`(function () {
  function build(size) {
    var root = document.createElement("div"), cur = root;
    for (var i = 0; i < size - 1; i++) {
      var d = document.createElement("div");
      cur.appendChild(d); cur = d;
    }
    return root;
  }
  function measure(fn) {
    globalThis.__scans = 0; globalThis.__walked = 0; globalThis.__native = 0;
    fn();
    return { scans: globalThis.__scans, walked: globalThis.__walked,
             native: globalThis.__native };
  }

  var el = build(${SIZE});
  var r_el = measure(function () { document.body.appendChild(el); });

  var frag = new DocumentFragment();
  frag.appendChild(build(${SIZE}));
  var r_frag = measure(function () { document.body.appendChild(frag); });

  var host = document.createElement("div");
  document.body.appendChild(host);
  var r_html = measure(function () {
    host.innerHTML = '<div><iframe></iframe></div>'; });

  // A text node cannot contain a frame; the reach must bail before touching
  // the DOM at all. Zero at every depth, or the nodeType fast-path is gone.
  var r_text = measure(function () {
    document.body.appendChild(new Text("x")); });

  var ap = Node.prototype.appendChild;
  var hd = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
  return {
    N: ${N},
    appendChild_element: r_el,
    appendChild_fragment: r_frag,
    innerHTML_set: r_html,
    appendChild_text: r_text,
    // The static tells, re-read here so this file cannot pass by having
    // quietly traded one kind of visibility for another.
    ap_own_props: Object.getOwnPropertyNames(ap).sort(),
    ap_length: ap.length,
    ap_name: ap.name,
    ap_tostring: Function.prototype.toString.call(ap),
    set_own_props: Object.getOwnPropertyNames(hd.set).sort(),
    set_tostring: Function.prototype.toString.call(hd.set),
    enumerable_globals: Object.keys(globalThis).filter(
      (k) => /^__pna|^__persona|^__h|^__b/.test(k)),
  };
})()`, ctx);

console.log(JSON.stringify(out));
"""

# 13 distinct leaves, inserted through creepjs's own gesture and read BY INDEX.
# This is the arm that keeps the rejected wrap-once design rejected.
_DELIVERY_PROBE = r"""
const vm = require("vm");
const fs = require("fs");
const BOOT = fs.readFileSync(process.argv[2], "utf8");
const DOM = fs.readFileSync(process.argv[3], "utf8");
const N = parseInt(process.argv[4], 10);

const sandbox = { Reflect, WeakSet, WeakMap, console };
const ctx = vm.createContext(sandbox);
vm.runInContext("var self = this; globalThis.self = globalThis;", ctx);
vm.runInContext(DOM, ctx);

for (let i = 0; i < N; i++) {
  vm.runInContext(`function applyR${i}(G){ try { G.__R${i}__ = 1; } catch (e) {} }`, ctx);
  vm.runInContext(
    "(function () {\n" + BOOT.split("applyPatch").join("applyR" + i) + "\n})();", ctx);
}

const out = vm.runInContext(`(function () {
  globalThis.__scans = 0;
  var n = self.length;
  var frag = new DocumentFragment();
  var div = document.createElement('div');
  div.innerHTML = '<div style="display:none"><iframe></iframe></div>';
  frag.appendChild(div);
  globalThis.__scans = 0;
  document.body.appendChild(frag);
  var scans = globalThis.__scans;
  var win = self[n];                 // INDEXED. Never .contentWindow.
  var delivered = [], missing = [];
  for (var i = 0; i < ${N}; i++) {
    if (win && win["__R" + i + "__"]) delivered.push(i); else missing.push(i);
  }
  return { reached: !!win, scans_on_the_insertion: scans,
           delivered: delivered.length, missing: missing };
})()`, ctx);

console.log(JSON.stringify(out));
"""

_CHROMIUM = realm_bootstrap_js("applyPatch")
_FIREFOX = realm_bootstrap_js("applyPatch", firefox_worker_cloak())

_ARMS = [("chromium", _CHROMIUM), ("firefox", _FIREFOX)]
_IDS = [a[0] for a in _ARMS]


def _node(probe: str, boot: str, tmp_path, *args) -> dict:
    d = pathlib.Path(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    kw = {"encoding": "utf-8"}
    (d / "boot.js").write_text(boot, **kw)
    (d / "dom.js").write_text(_counting_dom(), **kw)
    (d / "probe.js").write_text(probe, **kw)
    r = subprocess.run(
        ["node", str(d / "probe.js"), str(d / "boot.js"), str(d / "dom.js"),
         *[str(a) for a in args]],
        capture_output=True,
        text=True,
        # encoding NAMED: `text=True` alone decodes with the PLATFORM default,
        # cp1252 on Windows, while the bootstrap echoed back here is utf-8.
        encoding="utf-8",
        timeout=120,
    )
    assert r.returncode == 0, f"probe failed:\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _run(boot, tmp_path, n, size=_SUBTREE) -> dict:
    return _node(_PROBE, boot, tmp_path, n, size)


def _deliver(boot, tmp_path, n=_RIDER_COUNT) -> dict:
    return _node(_DELIVERY_PROBE, boot, tmp_path, n)


_DOORS = ("appendChild_element", "appendChild_fragment", "innerHTML_set")


# --------------------------------------------------------------------------
# THE HEADLINE: the DOM axis is depth-invariant.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_one_scan_per_insertion_at_every_depth(engine, boot, tmp_path):
    """ONE subtree scan per insertion, whether one rider is installed or 13.

    This is the DOM-axis counterpart of ``test_chain_is_depth_invariant``. If
    the scan count tracked the rider count, the number of riders would be
    readable off a clock by anyone who can insert a large subtree — on the three
    hottest functions in the DOM.
    """
    one = _run(boot, tmp_path / "a", 1)
    many = _run(boot, tmp_path / "b", _RIDER_COUNT)

    for door in _DOORS:
        assert one[door]["scans"] == 1, (
            f"[{engine}] {door}: a single rider should scan the subtree exactly "
            f"once, got {one[door]['scans']}"
        )
        assert many[door]["scans"] == 1, (
            f"[{engine}] {door}: {_RIDER_COUNT} riders scanned the subtree "
            f"{many[door]['scans']} times for ONE insertion — an O(subtree) "
            f"amplification on a hot, heavily probed DOM function, i.e. a "
            f"TIMING tell of exactly the kind AC2 forbids. Nodes walked: "
            f"{one[door]['walked']} at N=1 vs {many[door]['walked']} at "
            f"N={_RIDER_COUNT}."
        )
        assert one[door]["walked"] == many[door]["walked"], (
            f"[{engine}] {door}: chain DEPTH is observable through the amount "
            f"of DOM walked: {one[door]['walked']} at N=1 vs "
            f"{many[door]['walked']} at N={_RIDER_COUNT}."
        )


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_the_native_insertion_still_happens_exactly_once(engine, boot, tmp_path):
    """The guard suppresses SCANS, never the insertion the page asked for.

    A guard that skipped the call-through would be a functional break dressed as
    an optimisation, so this is asserted separately from the scan count rather
    than inferred from it.
    """
    for n in (1, _RIDER_COUNT):
        r = _run(boot, tmp_path / f"n{n}", n)
        for door in ("appendChild_element", "appendChild_fragment"):
            assert r[door]["native"] == 1, (
                f"[{engine}] N={n} {door}: expected exactly ONE native "
                f"insertion, got {r[door]['native']}"
            )


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_a_text_node_is_never_walked(engine, boot, tmp_path):
    """A node that cannot contain a frame costs nothing, at any depth.

    ``collectFrames`` bails on ``nodeType`` before touching the DOM. Most
    insertions on a real page are not elements, so losing this fast-path would
    put the wrapper's cost on traffic that can never yield a frame.
    """
    for n in (1, _RIDER_COUNT):
        r = _run(boot, tmp_path / f"n{n}", n)
        assert r["appendChild_text"]["scans"] == 0, (
            f"[{engine}] N={n}: a text node triggered "
            f"{r['appendChild_text']['scans']} subtree scan(s); the nodeType "
            f"fast-path in collectFrames is gone."
        )
        assert r["appendChild_text"]["native"] == 1


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_the_static_tells_are_still_closed(engine, boot, tmp_path):
    """The scan guard must not have bought its win with a visible marker.

    Re-read here rather than left to the reach suite, because this file changes
    the wrappers' bodies and the cheapest way to pass its own assertions would
    be to stash state somewhere a detector can read.
    """
    r = _run(boot, tmp_path, _RIDER_COUNT)
    assert r["ap_own_props"] == ["length", "name"], (
        f"[{engine}] appendChild owns properties a native does not: "
        f"{r['ap_own_props']}"
    )
    assert r["ap_length"] == 1, f"[{engine}] arity tell: {r['ap_length']}"
    assert r["ap_name"] == "appendChild", f"[{engine}] name tell: {r['ap_name']!r}"
    assert "[native code]" in r["ap_tostring"], (
        f"[{engine}] appendChild does not read as native: {r['ap_tostring']!r}"
    )
    assert r["set_own_props"] == ["length", "name"], (
        f"[{engine}] the innerHTML setter owns properties a native does not: "
        f"{r['set_own_props']}"
    )
    assert "[native code]" in r["set_tostring"], (
        f"[{engine}] the innerHTML setter does not read as native: "
        f"{r['set_tostring']!r}"
    )
    assert r["enumerable_globals"] == [], (
        f"[{engine}] the guard published a shared enumerable name — the PS-48 "
        f"disclosure: {r['enumerable_globals']}"
    )


# --------------------------------------------------------------------------
# THE OTHER HALF: cheap must not mean broken.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_all_thirteen_leaves_still_reach_the_indexed_realm(engine, boot, tmp_path):
    """One scan, thirteen leaves.

    The guard deduplicates the SCAN. It must not deduplicate the DELIVERY: each
    rider's wrapper closes over a different ``LEAF``, and the phantom realm
    needs all of them. This is the assertion that makes the scan guard correct
    rather than merely cheap.
    """
    r = _deliver(boot, tmp_path)
    assert r["reached"] is True, (
        f"[{engine}] the probe's own control failed: self[N] yielded nothing, "
        f"so every other assertion here would pass vacuously"
    )
    assert r["scans_on_the_insertion"] == 1, (
        f"[{engine}] expected ONE scan, got {r['scans_on_the_insertion']}"
    )
    assert r["delivered"] == _RIDER_COUNT, (
        f"[{engine}] only {r['delivered']} of {_RIDER_COUNT} leaves reached the "
        f"realm taken by INDEX; riders {r['missing']} are missing. A realm that "
        f"is reached with some leaves absent is PS-215's own defect, narrowed "
        f"rather than fixed."
    )


# --------------------------------------------------------------------------
# FALSIFICATION. Every claim above is paired with a mutation that breaks one
# property and must go RED. An arm that stays green proves its assertion is
# decorative -- the standard the toString suite already sets.
# --------------------------------------------------------------------------
_GUARD_METHODS = "                  var outermost = true;\n" \
                 "                  try { outermost = (proto[prop] === wrapped); } catch (e) {}"
_GUARD_INNERHTML = "                      top = !!(cd && cd.set === hset);"


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_falsification_removing_the_method_guard_goes_red(engine, boot, tmp_path):
    """Defeat the insertion-method guard; the scan count MUST blow up.

    This reproduces the reported defect on demand, which is what proves
    ``test_one_scan_per_insertion_at_every_depth`` is discriminating.
    """
    mutated = _anchored(boot, _GUARD_METHODS, "                  var outermost = true;")
    r = _run(mutated, tmp_path, _RIDER_COUNT)

    assert r["appendChild_element"]["scans"] == _RIDER_COUNT, (
        f"[{engine}] MUTATION did not restore the per-rider scan: expected "
        f"{_RIDER_COUNT} scans with the guard removed, got "
        f"{r['appendChild_element']['scans']}. The guard is therefore NOT what "
        f"the passing test is measuring, and that assertion is decorative."
    )
    assert r["appendChild_element"]["walked"] > _SUBTREE * 2, (
        f"[{engine}] MUTATION did not restore the O(subtree) amplification: "
        f"{r['appendChild_element']['walked']} nodes walked."
    )


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_falsification_removing_the_innerhtml_guard_goes_red(engine, boot, tmp_path):
    """The ``innerHTML`` setter is a SEPARATE door and needs its own arm.

    Guarding the insertion methods alone left this one still walking the subtree
    thirteen times per assignment — measured, and the reason this arm exists as
    well as the one above.
    """
    mutated = _anchored(boot, _GUARD_INNERHTML, "                      top = true;")
    r = _run(mutated, tmp_path, _RIDER_COUNT)

    assert r["innerHTML_set"]["scans"] == _RIDER_COUNT, (
        f"[{engine}] MUTATION did not restore the per-rider scan on the "
        f"innerHTML setter: expected {_RIDER_COUNT}, got "
        f"{r['innerHTML_set']['scans']}."
    )


@pytest.mark.parametrize("engine,boot", _ARMS, ids=_IDS)
def test_wrap_once_would_break_delivery(engine, boot, tmp_path):
    """THE REJECTED DESIGN, kept executable so it cannot quietly come back.

    "Guard so a prototype is wrapped once per realm" is the obvious-looking fix
    and it collapses the scan count correctly. This arm shows what it costs: the
    other twelve riders' leaves never reach the realm, because a suppressed
    wrapper is a suppressed ``LEAF``. That is PS-215's defect reintroduced, so
    the SCAN is deduplicated and the WRAPPERS are not.

    The guard is expressed here the only way it CAN be across riders — a name
    all thirteen can spell, i.e. on the global object — which is independently
    the PS-48 disclosure this design also avoids.
    """
    anchor = (
        "          var hookInsert = function (proto, prop) {\n"
        "            try {\n"
        "              var orig = proto[prop];\n"
        '              if (typeof orig !== "function") return;'
    )
    mutated = _anchored(
        boot,
        anchor,
        anchor + "\n"
        "              try {\n"
        "                var MK = G.__pnaHooked || (G.__pnaHooked = {});\n"
        '                var key = (proto === G.Node.prototype ? "N." : "E.") + prop;\n'
        "                if (MK[key]) return;\n"
        "                MK[key] = 1;\n"
        "              } catch (e) {}",
    )
    r = _deliver(mutated, tmp_path)

    assert r["reached"] is True, f"[{engine}] control failed: self[N] yielded nothing"
    assert r["delivered"] == 1, (
        f"[{engine}] the wrap-once arm was expected to deliver exactly ONE "
        f"leaf (its whole point being that it suppresses the other twelve "
        f"wrappers, and with them twelve leaves), but delivered "
        f"{r['delivered']}. This arm no longer reproduces the design it exists "
        f"to reject, so it proves nothing — re-derive it against the source."
    )
    assert len(r["missing"]) == _RIDER_COUNT - 1
