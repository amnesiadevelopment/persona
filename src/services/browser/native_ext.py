"""MAIN-world extension that installs a chained, self-cloaking
``Function.prototype.toString`` in every realm.

persona's other extensions replace built-ins (matchMedia, Intl.*, getVoices,
Worker, …) with JS wrappers. A masking detector (pixelscan) calls
Function.prototype.toString on them and sees injected source instead of
`function name() { [native code] }`, then reports "masking detected". A
per-function `.toString` override doesn't help — detectors use
Function.prototype.toString.call(fn), which bypasses it.

⛔ THIS EXTENSION NO LONGER SERVES THE OTHER MODULES' WRAPPERS, AND THAT IS THE
POINT OF PS-368. It used to be the single cross-script reader: every other
extension pinned a non-enumerable `__pnaName` OWN PROPERTY on its wrappers and
`applyNativePatch` read `this.__pnaName`. A marker property is what made that
protocol work across twelve content scripts with no shared closure — and it made
every spoofed wrapper own a third name:

    NATIVE   Array.prototype.map          ["length","name"]
    CHROMIUM wrapper (own-property tell)  ["length","name","__pnaName"]

`Object.getOwnPropertyNames(fn)` read that in one line, and `"__pnaName" in fn`
was positive persona identification — cheaper for a detector to run than the
toString comparison the marker existed to satisfy, and entirely independent of
it. So every module now carries its OWN closure-WeakMap cloak
(`worker_wrap.chromium_leaf_cloak_js`), registering its wrappers where a page
cannot enumerate them.

What survives here is the part that was never about the marker: a cloak in every
realm. A fresh about:blank iframe (or a worker) has its own Function.prototype,
so a wrapper carried there by another extension would stringify as source unless
something patches that realm too — and this extension is the one that ships
unconditionally on every Chromium launch, carried everywhere by the shared
bootstrap. It CHAINS, so it composes with the twelve leaf cloaks in any load
order, and it registers ITSELF so `Function.prototype.toString.toString()` reads
native (a detector stringifies the cloak to catch exactly this trick).
"""

import json
import pathlib

from .worker_wrap import chromium_leaf_cloak_js, realm_bootstrap_js

CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G's Function.prototype.toString. Every realm needs its own
  // patch: a fresh about:blank iframe (or a worker) has its own Function.prototype,
  // so a wrapper carried there by another extension would otherwise stringify as
  // source and betray the override. Carried into all realms by the bootstrap.
  //
  // CHAIN, don't flag-guard. Twelve other leaves install this same cloak in
  // their own realms, and they are separate content scripts in one MAIN world
  // with no guaranteed load order and no shared closure. They used to
  // coordinate through an enumerable global (`G.__pnaToStringPatched`), which
  // `Object.keys(window)` found in one line, in EVERY realm, at every
  // worker/iframe depth — positive identification of a persona-family tool,
  // under persona's own `__pna` prefix.
  //
  // Chaining DISSOLVES the coordination problem instead of solving it: this
  // wrapper delegates to whatever Function.prototype.toString it found (the
  // engine's own, or another script's patch), so N scripts compose with no
  // shared name between them. Same idiom as worker_wrap.py's `__hts` (the
  // Worker/iframe accessors) and invisible_launch.py's `__ts` (the Firefox
  // cloak, which this ports).
  //
  // ⛔ THE REGISTRY IS A CLOSURE WEAKMAP, NOT AN OWN `__pnaName` PROPERTY
  // (PS-368). The whole body below is `chromium_leaf_cloak_js` — the ONE source
  // of this text, shared with the twelve leaves — rather than a hand-rolled
  // copy. See the module docstring for what the marker cost and why it is gone.
  function applyNativePatch(G) {
   try {
    if (!G || !G.Function) return;
__LEAF_CLOAK__
   } catch (e) {}
  }
__REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-native",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["native.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_native_extension(base_dir: str) -> str:
    """Generate the unpacked extension that installs a chained, self-cloaking
    ``Function.prototype.toString`` in every realm the launch reaches.

    Since PS-368 it serves only the wrappers registered in its OWN closure
    WeakMap — which, in this script, is the cloak itself. The other twelve
    modules each carry their own copy of the same emitter and register their own
    wrappers there; see the module docstring for why the cross-script
    ``__pnaName`` own-property protocol was removed rather than kept.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = CONTENT_SCRIPT.replace(
        "__LEAF_CLOAK__", chromium_leaf_cloak_js(4)
    ).replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyNativePatch"))
    (ext_dir / "native.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
