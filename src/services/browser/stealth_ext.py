"""MAIN-world extension that fills in a few Navigator APIs that headless and
VM Chromium omit — the exact signals CreepJS counts toward "like headless".
Only APIs a real desktop Chrome actually exposes are added; mobile-only APIs
(e.g. ContactsManager) are deliberately left absent to stay consistent.
"""

import json
import pathlib

CONTENT_SCRIPT = r"""
(function () {
  try {
    // navigator.connection.downlinkMax — present on real Chrome, missing in
    // many headless/VM builds. CreepJS flags its absence as headless-like.
    if (navigator.connection && !('downlinkMax' in navigator.connection)) {
      Object.defineProperty(navigator.connection, 'downlinkMax', {
        get: function () { return Infinity; },
        configurable: true, enumerable: true,
      });
    }
  } catch (e) {}

  try {
    // ContentIndex API on ServiceWorkerRegistration — real Chrome exposes it.
    if (window.ServiceWorkerRegistration &&
        !('index' in ServiceWorkerRegistration.prototype)) {
      function ContentIndex() {}
      ContentIndex.prototype.getAll = function () { return Promise.resolve([]); };
      ContentIndex.prototype.add = function () { return Promise.resolve(); };
      ContentIndex.prototype.delete = function () { return Promise.resolve(); };
      Object.defineProperty(ServiceWorkerRegistration.prototype, 'index', {
        get: function () { return new ContentIndex(); },
        configurable: true, enumerable: true,
      });
    }
  } catch (e) {}
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
    (ext_dir / "stealth.js").write_text(CONTENT_SCRIPT, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
