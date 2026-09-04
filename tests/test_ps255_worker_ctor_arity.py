"""PS-255: the ``Worker``/``SharedWorker`` wrappers report the ORIGINAL's ARITY.

WHAT THIS PINS, AND WHY IT IS A PROPERTY READ. Every seam in this project that
installs a JS wrapper over a page-reachable native already pins ``.name`` and
registers a source text for ``toString``. Both of those cost a detector a
stringification. ``.length`` costs it ONE PROPERTY READ, and before this ticket
all three worker-constructor seams left it alone: the wrappers are written
``function (url, options)`` and so reported **2**, where the engine's own
``Worker(scriptURL, options?)`` reports **1**.

So every assertion here reads ``Worker.length`` off a wrapper that was installed
BY THE PRODUCT into a realm, and compares it to the ORIGINAL's own ``.length``.
Never a substring of the generated source, and never "a helper was called" —
the generated text said all the right things about ``name`` while the arity tell
sat open beside it, which is exactly how this outlived PS-131 on the same
wrapper.

THREE SEAMS, ASSERTED SEPARATELY, because they are three different code paths in
two files:

  1. ``worker_wrap.CHROMIUM_WORKER_CLOAK.apply``   — Chromium's realm bootstrap
  2. ``worker_wrap.firefox_worker_cloak()``        — Firefox's, through ``__bcloak``
  3. ``invisible_launch._language_override_script`` — Firefox's locale spoof,
     through ``__cloak`` (whose ``l`` parameter PS-119 added and this call site
     was not retrofitted with)

THE ORIGINAL'S LENGTH IS PARAMETRIZED, and that is the load-bearing part of the
design rather than thoroughness. The in-tree rule
(``invisible_launch.py:360-361``) is to copy from ``Orig.length`` and NEVER a
literal, so that an engine whose arity differs from the spec still matches
itself. A test that only ever presents a 1-arg original passes just as happily
against a hard-coded ``1``; presenting a 3-arg original is what tells the two
apart.

FALSIFICATION. Each seam has a MUTATION ARM that strips only the arity pin from
the generated script and asserts the property read goes RED — on the read, not
on a source string. ``_mutate`` pins its anchor's occurrence count, so an arm
that has stopped reproducing the defect fails loudly instead of passing as a
no-op.

⚠️ WHAT THIS FILE CANNOT ANSWER. The wrapper's ``2`` is a property of OUR code
and is engine-independent, so ``node:vm`` measures it honestly. The engine's own
``Worker.length`` is NOT measurable here — no browser binary exists in this
container — which is precisely why the fix reads ``Orig.length`` at runtime: the
stub below stands in for the original, and the product never depends on the
stub's value being the real one.
"""

import json
import shutil
import subprocess

import pytest

from src.services.browser import invisible_launch as il
from src.services.browser.worker_wrap import firefox_worker_cloak, realm_bootstrap_js

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the generated JS"
)


# --------------------------------------------------------------------------
# The three generated scripts, and the anchors a mutation arm strips.
# --------------------------------------------------------------------------

_CHROMIUM = realm_bootstrap_js("applyPatch")
_FIREFOX = realm_bootstrap_js("applyPatch", firefox_worker_cloak())
_LANG = il._language_override_script("pl-PL")

# The arity pin, per seam, exactly as each generator emits it. Stripping only
# this is what reproduces the pre-PS-255 defect — `name`, `prototype`,
# `__pnaName` and the toString registration all survive the mutation, so a RED
# arm can only be the arity.
_CHROMIUM_PIN = (
    '        try { Object.defineProperty(W, "length", '
    "{ value: Orig.length, configurable: true }); } catch (e) {}"
)
_FIREFOX_PIN = "__bcloak(W, Orig.name, undefined, Orig.length);"
_FIREFOX_PIN_REVERTED = "__bcloak(W, Orig.name);"
_LANG_PIN = "__cloak(W,Orig.name,undefined,Orig.length)"
_LANG_PIN_REVERTED = "__cloak(W,Orig.name)"


def _mutate(js: str, anchor: str, replacement: str, *, occurrences: int = 1) -> str:
    """Strip an anchor, refusing to run if it no longer says what it claims."""
    found = js.count(anchor)
    assert found == occurrences, (
        f"FALSIFICATION BROKEN: anchor {anchor!r} occurs {found} times in the "
        f"generated script, expected {occurrences}. This arm no longer "
        f"reproduces the defect it claims to and would be a no-op that always "
        f"passes. Update the anchor to match the source."
    )
    return js.replace(anchor, replacement)


# --------------------------------------------------------------------------
# The probe. One `node:vm` realm per case, with ORIGINALS whose arity is set by
# the caller — the fix must copy it, so a hard-coded value fails the 3-arg case.
# --------------------------------------------------------------------------

_PROBE = r"""
const fs = require("fs"), vm = require("vm");
const SCRIPT = fs.readFileSync(process.argv[2], "utf8");
const SEAM = process.argv[3];              // "bootstrap" | "language_override"
const ARITY = parseInt(process.argv[4], 10);

// Originals declaring ARITY required parameters. `Worker(scriptURL, options?)`
// is one required plus one optional, so a real engine reports 1 — the same
// WebIDL shape `URL(url, base?)` reports 1 for. The 3-arg case exists only to
// tell a copied pin apart from a hard-coded one.
function makeOriginal(name, arity) {
  const params = [];
  for (let i = 0; i < arity; i++) params.push("a" + i);
  // eslint-disable-next-line no-new-func
  const f = new Function(
    "return function " + name + "(" + params.join(",") + ") { this.url = a0; };"
  )();
  return f;
}

function makeRealm(arity) {
  const sandbox = {
    Reflect, WeakSet, WeakMap, Map,
    URL: function URL(url, base) { this.href = String(url); },
    XMLHttpRequest: function XMLHttpRequest() {
      this.status = 404; this.responseText = "";
      this.open = function () {}; this.send = function () {};
    },
    Blob: function Blob(parts) { this.__parts = parts; },
    Navigator: function Navigator() {},
    innerWidth: 1200, innerHeight: 800,
  };
  sandbox.URL.createObjectURL = function createObjectURL(obj) { return "blob:stub"; };
  sandbox.URL.revokeObjectURL = function revokeObjectURL(u) { return undefined; };
  sandbox.navigator = Object.create(sandbox.Navigator.prototype);
  sandbox.Worker = makeOriginal("Worker", arity);
  sandbox.SharedWorker = makeOriginal("SharedWorker", arity);
  // The iframe-accessor path: `__bcloak` is ALSO the frame-accessor cloak, so a
  // pin applied there rather than at the constructor is the trap this ticket
  // names. Present so the probe can read those getters back.
  sandbox.HTMLIFrameElement = function HTMLIFrameElement() {};
  Object.defineProperty(sandbox.HTMLIFrameElement.prototype, "contentWindow", {
    configurable: true, get: function () { return { name: "child" }; },
  });
  Object.defineProperty(sandbox.HTMLIFrameElement.prototype, "contentDocument", {
    configurable: true, get: function () { return { defaultView: { name: "child" } }; },
  });
  const ctx = vm.createContext(sandbox);
  vm.runInContext(
    "var self = this; globalThis.self = globalThis; globalThis.window = globalThis;",
    ctx);
  // The ORIGINALS' own arity, read before anything is installed: the value the
  // wrappers must end up reporting. Captured from the objects themselves, so
  // nothing here is a literal either.
  vm.runInContext(
    "globalThis.__origWorkerLength = self.Worker.length;" +
    "globalThis.__origSharedLength = self.SharedWorker.length;", ctx);
  return ctx;
}

const ctx = makeRealm(ARITY);
if (SEAM === "bootstrap") {
  // Each rider wraps leaf + bootstrap in an IIFE — running it bare would make
  // `__pnaInstall` a global and this probe would measure the harness.
  const leaf = "function applyPatch(G){ try { G.__LEAF__ = 1; } catch (e) {} }\n";
  vm.runInContext("(function(){" + leaf + SCRIPT + "})();", ctx);
} else {
  vm.runInContext(SCRIPT, ctx);
}

const out = vm.runInContext(`(function () {
  var T = Function.prototype.toString;
  var frameGet = Object.getOwnPropertyDescriptor(
    HTMLIFrameElement.prototype, "contentWindow").get;
  var langDesc = Object.getOwnPropertyDescriptor(Navigator.prototype, "language");
  return {
    origWorkerLength: __origWorkerLength,
    origSharedLength: __origSharedLength,
    workerLength: self.Worker.length,
    sharedLength: self.SharedWorker.length,
    workerName: self.Worker.name,
    sharedName: self.SharedWorker.name,
    workerToString: T.call(self.Worker),
    workerOwnProps: Object.getOwnPropertyNames(self.Worker).sort(),
    workerLengthDesc: Object.getOwnPropertyDescriptor(self.Worker, "length"),
    workerPnaName: self.Worker.__pnaName === undefined ? null : self.Worker.__pnaName,
    prototypeIsOriginals: self.Worker.prototype === Object.getPrototypeOf(
      Object.create(self.Worker.prototype)),
    // THE TRAP: the accessor paths must still report 0, like the native getters
    // they replace. Read as properties, never inferred from the source.
    frameGetterLength: frameGet.length,
    frameGetterName: frameGet.name,
    langGetterLength: langDesc ? langDesc.get.length : null,
    langGetterName: langDesc ? langDesc.get.name : null,
  };
})()`, ctx);

console.log(JSON.stringify(out));
"""


def _run(script: str, seam: str, arity: int, tmp_path) -> dict:
    node = shutil.which("node")
    (tmp_path / "script.js").write_text(script, encoding="utf-8")
    (tmp_path / "probe.js").write_text(_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(tmp_path / "probe.js"), str(tmp_path / "script.js"), seam, str(arity)],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# The three seams, each with the SEAM kind its generator produces.
_SEAMS = {
    "chromium_bootstrap": (_CHROMIUM, "bootstrap"),
    "firefox_bootstrap": (_FIREFOX, "bootstrap"),
    "firefox_language_override": (_LANG, "language_override"),
}


# --------------------------------------------------------------------------
# AC1 + AC3: the property read, on each engine's seam separately.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seam", sorted(_SEAMS))
@pytest.mark.parametrize("arity", [1, 3])
def test_worker_constructors_report_the_originals_arity(seam, arity, tmp_path):
    script, kind = _SEAMS[seam]
    r = _run(script, kind, arity, tmp_path)
    # The control: the original really did declare `arity` parameters, so a
    # green result below cannot be an artefact of a stub that reported 2 anyway.
    assert r["origWorkerLength"] == arity
    assert r["origSharedLength"] == arity
    assert r["workerLength"] == r["origWorkerLength"], (
        f"{seam}: Worker.length reads {r['workerLength']}, the engine's own "
        f"reports {r['origWorkerLength']} — a one-read masking tell."
    )
    assert r["sharedLength"] == r["origSharedLength"], (
        f"{seam}: SharedWorker.length reads {r['sharedLength']}, the engine's "
        f"own reports {r['origSharedLength']}."
    )


@pytest.mark.parametrize("seam", sorted(_SEAMS))
def test_the_length_pin_carries_a_native_functions_descriptor(seam, tmp_path):
    # A native function's `length` is non-enumerable and configurable. Pinning
    # it with a descriptor that differs from the one it replaces would trade the
    # value tell for a descriptor tell.
    script, kind = _SEAMS[seam]
    r = _run(script, kind, 1, tmp_path)
    assert r["workerLengthDesc"]["enumerable"] is False
    assert r["workerLengthDesc"]["configurable"] is True
    assert r["workerLengthDesc"]["value"] == 1
    # ...and it adds no own property beyond what a native function carries.
    # `arguments`/`caller` are V8's own on a sloppy-mode function expression and
    # are an artefact of the probe host, not of the product.
    extra = set(r["workerOwnProps"]) - {
        "length", "name", "prototype", "arguments", "caller", "__pnaName",
    }
    assert extra == set(), f"{seam} added unexpected own properties: {extra}"


# --------------------------------------------------------------------------
# AC5: PS-131's and PS-215's guarantees are JOINED, never replaced.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seam", sorted(_SEAMS))
def test_name_prototype_and_tostring_are_untouched_by_the_arity_pin(seam, tmp_path):
    script, kind = _SEAMS[seam]
    r = _run(script, kind, 1, tmp_path)
    assert r["workerName"] == "Worker"
    assert r["sharedName"] == "SharedWorker"
    assert r["prototypeIsOriginals"] is True
    if seam == "chromium_bootstrap":
        # Chromium's extension-side toString patch reads this marker; the
        # wrapper is not cloaked in-page, so its source text is its own.
        assert r["workerPnaName"] == "Worker"
    else:
        # Firefox cloaks through a closure WeakMap and adds no marker.
        assert r["workerPnaName"] is None
        assert r["workerToString"] == "function Worker() {\n    [native code]\n}"


# --------------------------------------------------------------------------
# AC4: the accessor and arrow-getter paths still report 0.
#
# `__bcloak` is the frame-accessor cloak as well as the constructor cloak, so
# widening it is exactly where an arity could be pinned on something that must
# not have one. Read as a property, on the installed getter.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seam", sorted(_SEAMS))
def test_accessor_paths_still_report_arity_zero(seam, tmp_path):
    script, kind = _SEAMS[seam]
    r = _run(script, kind, 1, tmp_path)
    assert r["frameGetterLength"] == 0, (
        f"{seam}: the HTMLIFrameElement accessor reports arity "
        f"{r['frameGetterLength']} — native getters report 0, and pinning one "
        f"here is the trap PS-255 names."
    )
    if seam == "firefox_bootstrap":
        # The one seam that actually cloaks the frame accessor: `__bcloak` is
        # spliced AROUND it (`frame_open`/`frame_close`), so this getter went
        # through the very helper PS-255 widened. Its `.name` proves it did —
        # and its arity is still 0, which is the whole point of making `l` a
        # TRAILING optional that this call site omits.
        assert r["frameGetterName"] == "get contentWindow"
    if seam == "firefox_language_override":
        # The `__cloak(()=>v,'get '+k,k)` arrow accessors of the locale spoof.
        assert r["langGetterLength"] == 0
        assert r["langGetterName"] == "get language"


# --------------------------------------------------------------------------
# AC6 (non-waivable): revert ONLY the arity pin -> the property read goes RED.
# --------------------------------------------------------------------------

def test_falsification_chromium_without_the_pin_reports_two(tmp_path):
    broken = _mutate(_CHROMIUM, _CHROMIUM_PIN, "")
    r = _run(broken, "bootstrap", 1, tmp_path)
    assert r["origWorkerLength"] == 1
    assert r["workerLength"] == 2 and r["sharedLength"] == 2
    # ...and everything else the seam guarantees survived the mutation, so the
    # RED above can only be the arity.
    assert r["workerName"] == "Worker" and r["workerPnaName"] == "Worker"


def test_falsification_firefox_bootstrap_without_the_pin_reports_two(tmp_path):
    broken = _mutate(_FIREFOX, _FIREFOX_PIN, _FIREFOX_PIN_REVERTED)
    r = _run(broken, "bootstrap", 1, tmp_path)
    assert r["origWorkerLength"] == 1
    assert r["workerLength"] == 2 and r["sharedLength"] == 2
    assert r["workerToString"] == "function Worker() {\n    [native code]\n}"


def test_falsification_firefox_language_override_without_the_pin_reports_two(tmp_path):
    # TWO occurrences: `_worker_wrap_js` is emitted for the page realm and again
    # inside the worker payload it delivers. Both are the same seam.
    broken = _mutate(_LANG, _LANG_PIN, _LANG_PIN_REVERTED, occurrences=2)
    r = _run(broken, "language_override", 1, tmp_path)
    assert r["origWorkerLength"] == 1
    assert r["workerLength"] == 2 and r["sharedLength"] == 2
    assert r["workerName"] == "Worker"


def test_falsification_arms_would_notice_a_missing_pin():
    # The arms above strip an anchor; this asserts the anchors are actually
    # THERE in the shipped scripts, so a future edit that removes the pin
    # altogether fails here rather than turning the arms into no-ops.
    assert _CHROMIUM.count(_CHROMIUM_PIN) == 1
    assert _FIREFOX.count(_FIREFOX_PIN) == 1
    assert _LANG.count(_LANG_PIN) == 2
