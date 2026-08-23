"""MAIN-world extension that makes persona's wrapped functions read as native.

persona's other extensions replace built-ins (matchMedia, Intl.*, getVoices,
Worker, …) with JS wrappers. A masking detector (pixelscan) calls
Function.prototype.toString on them and sees injected source instead of
`function name() { [native code] }`, then reports "masking detected". A
per-function `.toString` override doesn't help — detectors use
Function.prototype.toString.call(fn), which bypasses it.

This patches Function.prototype.toString ONCE, in the shared MAIN-world realm all
persona extensions run in, so a wrapper tagged with a non-enumerable
`__pnaName` marker renders as the native form. Wrappers just set the marker
(see _mark in the other extensions); load order doesn't matter because
Function.prototype is one object across every content script in the realm.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js

CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G's Function.prototype.toString. Every realm needs its own
  // patch: a fresh about:blank iframe (or a worker) has its own Function.prototype,
  // so a wrapper carried there by another extension would otherwise stringify as
  // source and betray the override. Carried into all realms by the bootstrap.
  //
  // CHAIN, don't flag-guard. locale_ext re-applies this same cloak in its own
  // realms, and the two are separate content scripts in one MAIN world with no
  // guaranteed load order and no shared closure. They used to coordinate through
  // an enumerable global (`G.__pnaToStringPatched`), which `Object.keys(window)`
  // found in one line, in EVERY realm, at every worker/iframe depth — positive
  // identification of a persona-family tool, under persona's own `__pna` prefix.
  //
  // Chaining DISSOLVES the coordination problem instead of solving it: this
  // wrapper delegates to whatever Function.prototype.toString it found (the
  // engine's own, or another script's patch), so N scripts compose with no
  // shared name between them. It also preserves the property the flag existed
  // for — a marked wrapper renders the native form EXACTLY once, in either load
  // order, because whichever patch is outermost answers a `__pnaName` hit itself
  // and never reaches the one below. Same idiom as worker_wrap.py:28-32 (the
  // Worker/iframe accessors) and invisible_launch.py:296-302 (the Firefox cloak,
  // which this ports).
  function applyNativePatch(G) {
   try {
    if (!G || !G.Function) return;
    const origToString = G.Function.prototype.toString;
    const native = function (name) {
      return "function " + (name || "") + "() { [native code] }";
    };
    const patched = function () {
      // `this` is the function being stringified.
      try {
        const n = this && this.__pnaName;
        if (typeof n === "string") return native(n);
      } catch (e) {}
      return origToString.apply(this, arguments);
    };
    // The override must itself read as native (a detector may stringify
    // Function.prototype.toString to catch exactly this trick).
    try { Object.defineProperty(patched, "__pnaName", { value: "toString" }); } catch (e) {}
    try { Object.defineProperty(patched, "name", { value: "toString" }); } catch (e) {}
    G.Function.prototype.toString = patched;
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
    """Generate the unpacked extension that makes marked wrappers stringify as
    native code, hiding the JS-override tell a masking detector reports."""
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = CONTENT_SCRIPT.replace(
        "__REALM_BOOTSTRAP__", realm_bootstrap_js("applyNativePatch")
    )
    (ext_dir / "native.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
