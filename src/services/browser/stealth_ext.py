"""MAIN-world extension that fills in a few Navigator APIs that headless and
VM Chromium omit — the exact signals CreepJS counts toward "like headless".
Only APIs a real desktop Chrome actually exposes are added; mobile-only APIs
(e.g. ContactsManager) are deliberately left absent to stay consistent.
"""

import json
import pathlib

from .worker_wrap import realm_bootstrap_js

# A fresh about:blank/srcdoc iframe (and a Web Worker) start without these
# desktop-only APIs re-added, so a scanner reading such a pristine realm sees
# "like headless" again. The shared recursive registry carries applyStealthPatch
# into every nested realm.
CONTENT_SCRIPT = r"""
(function () {
  function applyStealthPatch(G) {
   try {
    if (!G) return;
    var __pnaReg = null;
    try {
      var __pnaO = G.Object;
      if (__pnaO) {
        __pnaReg = __pnaO.__pnaRealm;
        if (!__pnaReg) {
          __pnaReg = {};
          __pnaO.defineProperty(__pnaO, '__pnaRealm',
                                { value: __pnaReg, configurable: true });
        }
      }
    } catch (e) { __pnaReg = null; }
    try {
      if (__pnaReg) {
        if (__pnaReg["stealth"] === true) return;
        __pnaReg["stealth"] = true;
      }
    } catch (e) {}
    try {
      // navigator.connection.downlinkMax — present on real Chrome, missing in
      // many headless/VM builds. CreepJS flags its absence as headless-like.
      var conn = G.navigator && G.navigator.connection;
      if (conn && !('downlinkMax' in conn)) {
        Object.defineProperty(conn, 'downlinkMax', {
          get: function () { return Infinity; },
          configurable: true, enumerable: true,
        });
      }
    } catch (e) {}

    try {
      // ContentIndex API on ServiceWorkerRegistration — real Chrome exposes it.
      var SWR = G.ServiceWorkerRegistration;
      if (SWR && !('index' in SWR.prototype)) {
        function ContentIndex() {}
        ContentIndex.prototype.getAll = function () { return Promise.resolve([]); };
        ContentIndex.prototype.add = function () { return Promise.resolve(); };
        ContentIndex.prototype.delete = function () { return Promise.resolve(); };
        Object.defineProperty(SWR.prototype, 'index', {
          get: function () { return new ContentIndex(); },
          configurable: true, enumerable: true,
        });
      }
    } catch (e) {}
   } catch (e) {}
  }
__STEALTH_REALM_BOOTSTRAP__
})();
"""

MANIFEST = {
    "manifest_version": 3,
    "name": "persona-stealth",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["stealth.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_stealth_extension(base_dir: str) -> str:
    """Generate an unpacked extension that fills missing desktop Navigator APIs
    so the browser stops reading as 'like headless'. Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    js = CONTENT_SCRIPT.replace(
        "__STEALTH_REALM_BOOTSTRAP__", realm_bootstrap_js("applyStealthPatch")
    )
    (ext_dir / "stealth.js").write_text(js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
