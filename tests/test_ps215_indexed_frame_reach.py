"""PS-215: the leaf must reach a child realm taken by INDEX (``self[N]``).

THE DEFECT THIS PINS. ``realm_bootstrap_js`` reached child frames through
exactly one door — a chained ``HTMLIFrameElement.prototype.contentWindow`` /
``contentDocument`` accessor. CreepJS never opens that door: it takes its
phantom iframe by INDEX::

    div.innerHTML = '<div style="..."><iframe></iframe></div>';
    document.body.appendChild(frag);
    const iframeWindow = self[numberOfIframes];   // <- INDEXED
    canvas = new win.OffscreenCanvas(256, 256);   // <- built FROM that realm

``self[N]`` is a ``WindowProxy`` read off the window's indexed properties and it
never invokes the accessor, so the leaf was never installed in that realm.
Measured, not argued (``readings/ps193-2026-08-26/``): with the shipped spoof,
CreepJS received digest ``1379655975`` at BOTH seeds while the page realm moved
(``855826239`` / ``1729355265``) in the same run at the same instant — the
page-realm column being the positive control that rules out "the spoof never
ran". Live consequence: two profiles linkable to each other, a Level 2
(mutual unlinkability) failure.

WHY THESE TESTS OBSERVE BEHAVIOUR AND NEVER SOURCE TEXT. The suite this joins
opens by recording that its predecessor was mostly substring checks over the
generated JS, and that those were structurally unable to see the defect they
were meant to guard. A ``"appendChild" in js`` assertion here would be that
same false green: it would pass on a bootstrap that installs the hook and never
reaches the realm. So every assertion below runs the generated bootstrap in
isolated ``node:vm`` realms and asks the CHILD REALM what it ended up with.

THE ACCESSOR DOOR IS SEALED SHUT after install. Once the bootstrap has captured
the native getter, the probe replaces ``contentWindow``/``contentDocument`` with
getters that THROW. If the reach were quietly still going through the accessor,
these tests would error rather than pass — which is what makes this the INDEXED
twin of the accessor case in ``test_worker_wrap.py`` rather than a test that
merely happens not to touch it.
"""

import json
import shutil
import subprocess

import pytest

from src.services.browser.worker_wrap import firefox_worker_cloak, realm_bootstrap_js
from tests.realm_harness import HARNESS

# --------------------------------------------------------------------------
# A minimal DOM. It is faithful in the four ways these tests actually depend
# on, and deliberately no further:
#
#   1. A DocumentFragment is EMPTIED by insertion — its children move out. This
#      is why the reach must collect frames BEFORE calling through to the
#      native, and a fake that kept them would hide a real bug.
#   2. An iframe's window only becomes reachable as `self[N]` once the frame is
#      CONNECTED to the document. Nothing else publishes it, so an indexed read
#      can only succeed by the same route it does in a browser.
#   3. `innerHTML` parses and builds detached children; connection happens
#      later, when an ancestor is inserted.
#   4. `contentWindow` is an accessor on HTMLIFrameElement.prototype, so the
#      bootstrap's existing chain has something real to capture.
# --------------------------------------------------------------------------
_DOM = r"""
(function () {
  var UID = 0;
  function Node() { this.childNodes = []; this.parentNode = null; this.__conn = false; }
  Object.defineProperty(Node.prototype, "isConnected", {
    configurable: true, get: function () { return this.__conn; } });

  function connect(n) {
    if (n.__conn) return;
    n.__conn = true;
    // A connected iframe publishes its window on the next indexed slot -- the
    // WindowProxy `self[N]` reads. This is the ONLY thing that publishes it.
    if (n.tagName === "IFRAME") {
      n.__win = { __tag: "frameWin" + (UID++) };
      var i = globalThis.length || 0;
      globalThis[i] = n.__win;
      globalThis.length = i + 1;
    }
    for (var k = 0; k < n.childNodes.length; k++) connect(n.childNodes[k]);
  }

  function adopt(parent, n) {
    if (n && n.nodeType === 11) {
      // THE FRAGMENT IS EMPTIED. Its children move to the parent.
      var kids = n.childNodes.slice();
      n.childNodes.length = 0;
      for (var i = 0; i < kids.length; i++) {
        kids[i].parentNode = parent; parent.childNodes.push(kids[i]);
        if (parent.__conn) connect(kids[i]);
      }
      return n;
    }
    n.parentNode = parent; parent.childNodes.push(n);
    if (parent.__conn) connect(n);
    return n;
  }

  Node.prototype.appendChild = function appendChild(n) { return adopt(this, n); };
  Node.prototype.insertBefore = function insertBefore(n, ref) { return adopt(this, n); };
  Node.prototype.replaceChild = function replaceChild(n, old) { return adopt(this, n); };

  function findFrames(root) {
    var out = [];
    (function walk(n) {
      for (var i = 0; i < n.childNodes.length; i++) {
        var c = n.childNodes[i];
        if (c.tagName === "IFRAME") out.push(c);
        walk(c);
      }
    })(root);
    return out;
  }

  function Element() { Node.call(this); this.nodeType = 1; this.tagName = "DIV"; }
  Element.prototype = Object.create(Node.prototype);
  Element.prototype.constructor = Element;
  Element.prototype.append = function append() {
    for (var i = 0; i < arguments.length; i++) adopt(this, arguments[i]); };
  Element.prototype.querySelectorAll = function querySelectorAll(sel) {
    return findFrames(this); };

  Object.defineProperty(Element.prototype, "innerHTML", {
    configurable: true,
    get: function () { return this.__html || ""; },
    set: function (v) {
      this.__html = String(v);
      this.childNodes.length = 0;
      // The one markup shape this fake parser understands is creepjs's.
      if (/<iframe/i.test(String(v))) {
        var box = new Element();
        var f = new HTMLIFrameElement();
        box.childNodes.push(f); f.parentNode = box;
        this.childNodes.push(box); box.parentNode = this;
        if (this.__conn) connect(box);
      }
    },
  });

  function DocumentFragment() { Node.call(this); this.nodeType = 11; }
  DocumentFragment.prototype = Object.create(Node.prototype);
  DocumentFragment.prototype.constructor = DocumentFragment;
  // Real DocumentFragments have this; the reach uses it to find frames in a
  // fragment before insertion empties it.
  DocumentFragment.prototype.querySelectorAll = function querySelectorAll(sel) {
    return findFrames(this); };

  function HTMLIFrameElement() { Element.call(this); this.tagName = "IFRAME"; }
  HTMLIFrameElement.prototype = Object.create(Element.prototype);
  HTMLIFrameElement.prototype.constructor = HTMLIFrameElement;
  Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
    configurable: true, get: function () { return this.__win || null; } });
  Object.defineProperty(HTMLIFrameElement.prototype, "contentDocument", {
    configurable: true,
    get: function () { return this.__win ? { defaultView: this.__win } : null; } });

  globalThis.Node = Node;
  globalThis.Element = Element;
  globalThis.DocumentFragment = DocumentFragment;
  globalThis.HTMLIFrameElement = HTMLIFrameElement;
  globalThis.length = 0;
  var body = new Element(); body.tagName = "BODY"; body.__conn = true;
  globalThis.document = {
    body: body,
    createElement: function (t) {
      if (String(t).toLowerCase() === "iframe") return new HTMLIFrameElement();
      var e = new Element(); e.tagName = String(t).toUpperCase(); return e;
    },
  };
})();
"""

# Slam the accessor door AFTER the bootstrap has captured the native getter.
# Any read of `.contentWindow` from here on THROWS, so a reach that secretly
# still depended on it cannot pass these tests.
_SEAL = r"""
Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
  configurable: true,
  get: function () { throw new Error("ACCESSOR DOOR USED"); } });
Object.defineProperty(HTMLIFrameElement.prototype, "contentDocument", {
  configurable: true,
  get: function () { throw new Error("ACCESSOR DOOR USED"); } });
"""

_PROBE = HARNESS + r"""
const BOOTSTRAP_A = fs.readFileSync(process.argv[2], "utf8");
const BOOTSTRAP_B = fs.readFileSync(process.argv[3], "utf8");
const DOM = fs.readFileSync(process.argv[4], "utf8");
const SEAL = fs.readFileSync(process.argv[5], "utf8");

// Two leaves from two separate content scripts: the multi-module case, which
// is the one a per-module guard would break. Each carries a distinct fake seed
// compiled INSIDE the body, as the real seed-bearing leaves do.
const SEED_A = 1234567, SEED_B = 7654321;
const LEAF_A = "function applyAlpha(G){ try { var SEED = " + SEED_A + "; G.__ALPHA__ = SEED; } catch (e) {} }\n";
const LEAF_B = "function applyBeta(G){ try { var SEED = " + SEED_B + "; G.__BETA__ = SEED; } catch (e) {} }\n";

function asContentScript(leaf, bootstrap) {
  return "(function(){" + leaf + bootstrap + "})();";
}

// Build a realm with the DOM in it, install both modules, then seal the door.
// `hideDom` reproduces the PRE-FIX world: with no Node/Element to hook, the
// reach cannot install and only the accessor chain remains.
function install(opts) {
  opts = opts || {};
  const realm = makeRealm();
  vm.runInContext(DOM, realm.ctx);
  if (opts.hideDom) {
    vm.runInContext("globalThis.__Node = Node; globalThis.__Element = Element;" +
                    "delete globalThis.Node; delete globalThis.Element;", realm.ctx);
  }
  vm.runInContext(asContentScript(LEAF_A, BOOTSTRAP_A), realm.ctx);
  vm.runInContext(asContentScript(LEAF_B, BOOTSTRAP_B), realm.ctx);
  if (opts.hideDom) {
    vm.runInContext("globalThis.Node = __Node; globalThis.Element = __Element;" +
                    "delete globalThis.__Node; delete globalThis.__Element;", realm.ctx);
  }
  if (!opts.keepAccessor) vm.runInContext(SEAL, realm.ctx);
  return realm;
}

// CreepJS's getPhantomIframe(), gesture for gesture, then its INDEXED read.
const CREEPJS_GESTURE = `
(function () {
  var n = self.length;
  var frag = new DocumentFragment();
  var div = document.createElement('div');
  div.innerHTML = '<div style="display:none"><iframe></iframe></div>';
  frag.appendChild(div);
  document.body.appendChild(frag);
  var win = self[n];                 // <- INDEXED. Never .contentWindow.
  return {
    reached: !!win,
    alpha: !!win && win.__ALPHA__ === ${SEED_A},
    beta:  !!win && win.__BETA__  === ${SEED_B},
    childGlobals: !!win ? Object.getOwnPropertyNames(win)
      .filter(function (k) { return /pna|persona|boot/i.test(k); }) : null
  };
})()
`;

const out = {};

// 1. THE PRIMARY OBSERVATION: indexed reach, accessor sealed.
out.indexed = vm.runInContext(CREEPJS_GESTURE, install().ctx);

// 2. FALSIFICATION: same probe, reach disabled. Must NOT be spoofed, which is
//    what proves observation 1 is discriminating rather than vacuous.
out.falsified = vm.runInContext(CREEPJS_GESTURE, install({ hideDom: true }).ctx);

// 3. Idempotence: a frame inserted, removed and RE-inserted, plus an accessor
//    read afterwards, must install exactly once. The leaves are counters here.
out.idempotent = (function () {
  const realm = makeRealm();
  vm.runInContext(DOM, realm.ctx);
  vm.runInContext(
    "function applyCount(G){ try { G.__N__ = (G.__N__ || 0) + 1; } catch (e) {} }",
    realm.ctx);
  vm.runInContext("(function(){" + fs.readFileSync(process.argv[2], "utf8")
    .replace(/applyAlpha/g, "applyCount") + "})();", realm.ctx);
  return vm.runInContext(`
    (function () {
      var n = self.length;
      var f = document.createElement('iframe');
      document.body.appendChild(f);
      var win = self[n];
      document.body.childNodes.length = 0;          // remove
      document.body.appendChild(f);                 // re-insert
      var viaAccessor = f.contentWindow;            // and the other door too
      return { installs: win ? win.__N__ : null,
               sameWindow: viaAccessor === win };
    })()
  `, realm.ctx);
})();

// 4. Every other insertion door, one realm each, all reaching by index.
out.doors = {};
[["insertBefore", "document.body.insertBefore(frag, null)"],
 ["replaceChild", "document.body.appendChild(document.createElement('span'));" +
                  "document.body.replaceChild(frag, document.body.childNodes[0])"],
 ["append",       "document.body.append(frag)"],
 ["innerHTML",    "document.body.innerHTML = '<div><iframe></iframe></div>'"],
].forEach(function (pair) {
  const name = pair[0], gesture = pair[1];
  try {
    out.doors[name] = vm.runInContext(`
      (function () {
        var n = self.length;
        var frag = new DocumentFragment();
        var div = document.createElement('div');
        div.innerHTML = '<div><iframe></iframe></div>';
        frag.appendChild(div);
        ${gesture};
        var win = self[n];
        return { reached: !!win,
                 alpha: !!win && win.__ALPHA__ === ${SEED_A},
                 beta:  !!win && win.__BETA__  === ${SEED_B} };
      })()
    `, install().ctx);
  } catch (e) { out.doors[name] = { error: String(e) }; }
});

// 5. WHAT A DETECTOR SEES on each wrapped function: its stringification, its
//    own-property names, its arity and its .name. Read from the PAGE realm,
//    with the cloak in place.
out.tells = vm.runInContext(`
  (function () {
    var r = {};
    function look(obj, prop, holder) {
      var d = Object.getOwnPropertyDescriptor(obj, prop);
      var f = d && (d.set || d.value);
      if (typeof f !== "function") return;
      r[holder + "." + prop] = {
        str: Function.prototype.toString.call(f),
        own: Object.getOwnPropertyNames(f).sort(),
        length: f.length,
        name: f.name
      };
    }
    ["appendChild", "insertBefore", "replaceChild"].forEach(function (p) {
      look(Node.prototype, p, "Node"); });
    ["append", "innerHTML"].forEach(function (p) {
      look(Element.prototype, p, "Element"); });
    r["__globals"] = Object.getOwnPropertyNames(self)
      .filter(function (k) { return /pna|persona|boot|hnm|hts|bnm|bts/i.test(k); });
    r["__toStringStr"] = Function.prototype.toString.call(Function.prototype.toString);
    return r;
  })()
`, install({ keepAccessor: true }).ctx);

console.log(JSON.stringify(out));
"""


def _run(bootstrap_cloak):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    import tempfile
    import pathlib

    d = pathlib.Path(tempfile.mkdtemp(prefix="ps215_"))
    kw = {"encoding": "utf-8"}
    (d / "boot_a.js").write_text(
        realm_bootstrap_js("applyAlpha", bootstrap_cloak)
        if bootstrap_cloak
        else realm_bootstrap_js("applyAlpha"),
        **kw,
    )
    (d / "boot_b.js").write_text(
        realm_bootstrap_js("applyBeta", bootstrap_cloak)
        if bootstrap_cloak
        else realm_bootstrap_js("applyBeta"),
        **kw,
    )
    (d / "dom.js").write_text(_DOM, **kw)
    (d / "seal.js").write_text(_SEAL, **kw)
    (d / "probe.js").write_text(_PROBE, **kw)
    out = subprocess.run(
        [
            node, str(d / "probe.js"), str(d / "boot_a.js"), str(d / "boot_b.js"),
            str(d / "dom.js"), str(d / "seal.js"),
        ],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def chromium():
    return _run(None)


@pytest.fixture(scope="module")
def firefox():
    return _run(firefox_worker_cloak())


@pytest.fixture(params=["chromium", "firefox"])
def arm(request, chromium, firefox):
    """Both engines, every test. The bootstrap is SHARED — 13 modules ride it on
    both arms — so a one-engine fix would leave the other arm failing exactly as
    before, and PS-215's AC5 says a one-engine fix does not close the ticket."""
    return chromium if request.param == "chromium" else firefox


# --- AC3: the indexed path, observed ---------------------------------------

def test_the_phantom_realm_is_reachable_at_all(arm):
    """The probe's own control: if `self[N]` yielded nothing, every assertion
    below would pass vacuously."""
    assert arm["indexed"]["reached"] is True
    assert arm["falsified"]["reached"] is True


def test_both_leaves_run_in_a_realm_reached_only_by_index(arm):
    # THE PS-215 ASSERTION. The accessor door is sealed shut (it throws), so the
    # only way the leaf can be in this realm is the connection-time trigger.
    # Two leaves, because a per-module guard on the single getter is the failure
    # mode the chaining design exists to prevent.
    assert arm["indexed"]["alpha"] is True, (
        "module A's leaf did not reach the phantom realm — CreepJS reads its "
        "WebGL from exactly this realm and would receive unperturbed pixels"
    )
    assert arm["indexed"]["beta"] is True, "module B's leaf did not reach it"


def test_falsification_without_the_reach_the_phantom_realm_is_unspoofed(arm):
    """Proves the test above discriminates. With no `Node`/`Element` to hook the
    reach cannot install, only the accessor chain remains, and the indexed read
    lands in a pristine realm — the exact pre-fix behaviour PS-193 measured.

    Without this arm, `test_both_leaves_run_in_a_realm_reached_only_by_index`
    could be green because the fake DOM leaked the leaf some other way.
    """
    assert arm["falsified"]["alpha"] is False, (
        "the phantom realm was spoofed even with the reach disabled — the probe "
        "is not measuring what it claims to"
    )
    assert arm["falsified"]["beta"] is False


@pytest.mark.parametrize("door", ["insertBefore", "replaceChild", "append", "innerHTML"])
def test_every_insertion_door_reaches_the_child_realm(arm, door):
    """`appendChild` is what CreepJS happens to call today. The others are the
    same gesture and a checker may use any of them, so covering only the
    measured one would make this fix a single-call-site patch."""
    cell = arm["doors"][door]
    assert "error" not in cell, cell
    assert cell["reached"] is True
    assert cell["alpha"] is True and cell["beta"] is True


def test_a_frame_is_installed_exactly_once_however_it_is_reached(arm):
    """The existing `SEEN`/`fresh()` guard is REUSED rather than duplicated, so
    a frame inserted, removed, re-inserted and then read through the accessor
    must run the leaf once. A second run is not merely wasteful: a leaf that
    perturbs on each application would perturb twice and desync this realm from
    the page."""
    assert arm["idempotent"]["sameWindow"] is True
    assert arm["idempotent"]["installs"] == 1


# --- AC2: the reach must not be a tell -------------------------------------

_WRAPPED = [
    "Node.appendChild", "Node.insertBefore", "Node.replaceChild",
    "Element.append", "Element.innerHTML",
]

# What each function's arity is in the fake DOM, which mirrors the spec.
_ARITY = {
    "Node.appendChild": 1, "Node.insertBefore": 2, "Node.replaceChild": 2,
    "Element.append": 0, "Element.innerHTML": 1,
}


@pytest.mark.parametrize("fn", _WRAPPED)
def test_every_wrapped_function_stringifies_as_native(arm, fn):
    """A detector calls `Function.prototype.toString` on these. `appendChild` is
    among the most probed functions in the DOM, so raw patch source here is a
    direct read of "this browser is masking" — the class of tell PS-78 round 3
    was rejected for, and it would be one INTRODUCED by the fix for another."""
    cell = arm["tells"][fn]
    assert "[native code]" in cell["str"], (
        f"{fn} stringifies as patch source: {cell['str'][:200]!r}"
    )
    assert "collectFrames" not in cell["str"] and "__pnaInstall" not in cell["str"]


@pytest.mark.parametrize("fn", _WRAPPED)
def test_no_wrapped_function_carries_an_own_property_marker(arm, fn):
    """No `__pnaName` and no marker of any other name — on EITHER engine.

    This is the one place the Chromium bootstrap does NOT use `__pnaName`, and
    deliberately: these functions' own-property lists are known by heart, so a
    third name on `appendChild` is a ONE-LINE tell, cheaper to run than the
    `toString` comparison the marker exists to satisfy. PS-78 round 2 left
    exactly such a bare marker on Firefox's `Worker`.

    `prototype` is included in the forbidden set for the same reason: a native
    DOM method has none, a plain function expression does, and it cannot be
    deleted after the fact because it is non-configurable — which is why the
    wrappers are method shorthand.
    """
    own = set(arm["tells"][fn]["own"])
    assert own <= {"length", "name"}, (
        f"{fn} carries own properties a native method does not: "
        f"{sorted(own - {'length', 'name'})}"
    )


@pytest.mark.parametrize("fn", _WRAPPED)
def test_every_wrapped_function_keeps_its_native_arity(arm, fn):
    """`Function.length` is an own property and is as cheap to read as the name.
    A wrapper taking its arguments through `arguments` reports 0 where
    `appendChild` reports 1 and `insertBefore` reports 2, so leaving it alone
    would trade the `toString` tell for a `length` tell."""
    assert arm["tells"][fn]["length"] == _ARITY[fn]


def test_the_tostring_patch_itself_reads_as_native(arm):
    """A detector stringifies `Function.prototype.toString` to catch exactly
    this trick, so the cloak must cloak itself."""
    assert "[native code]" in arm["tells"]["__toStringStr"]


def test_the_reach_adds_no_enumerable_global(arm):
    """PS-48: nothing is stored on the global object. The reach's helpers and
    its cloak WeakMap live in the bootstrap's closure, so a page cannot name
    them, enumerate them, or recover the seed through them."""
    assert arm["tells"]["__globals"] == []
