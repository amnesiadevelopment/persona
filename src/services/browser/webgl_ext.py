"""MAIN-world extension that makes each profile's WebGL pixel readback distinct.

The engine spoofs the WebGL vendor/renderer/parameter strings per seed, but the
actual rendered pixels come from the shared software renderer (SwiftShader in a
GPU-less VM), so gl.readPixels collides across profiles and links them. WebGL
readback is not patched in C++ (unlike 2D-canvas toDataURL), so a MAIN-world
override of readPixels survives and is where per-profile entropy belongs.

A deterministic per-(seed, position) sub-pixel delta is added to a sparse set of
bytes in the returned buffer: enough to change the readback hash per profile,
small enough to stay a plausible pixel output. It only touches Uint8/clamped
byte readbacks (RGBA UNSIGNED_BYTE), the path fingerprinters use; float/integer
pixel reads are left untouched so WebGL maths is unaffected.
"""

import json
import pathlib

from .worker_wrap import firefox_native_wrap_js, realm_bootstrap_js

# One byte is nudged per this many bytes, by +/-1. Sparse enough to be invisible
# and to keep the image plausible, dense enough that the readback hash differs
# per profile.
_STRIDE = 17

# The ONLY engine-specific part of the patch, kept as a seam rather than a
# second copy of the whole script: everything that computes the perturbation is
# shared, and the two engines differ solely in how a wrapper is made to
# stringify as a native built-in.
#
# Chromium's form is the ORIGINAL text, unchanged and pinned byte-for-byte by
# tests/test_webgl_ext.py. Chromium's readback is the baseline every prior
# reading was taken against (PS-78 boundary: "Chromium is unchanged"), so this
# seam must reproduce it EXACTLY, not merely equivalently.
_CHROMIUM_NATIVE_WRAP = r"""  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      // Mark for the native_ext Function.prototype.toString patch so a detector
      // calling Function.prototype.toString.call(replacement) reads native. A
      // plain replacement.toString override is bypassed by that .call form.
      Object.defineProperty(replacement, '__pnaName', { value: orig.name });
    } catch (e) {}
    return replacement;
  }"""

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G (window or a WorkerGlobalScope). Detectors read a WebGL
  // pixel hash from an OffscreenCanvas inside a worker to catch a page-only
  // spoof, so the readback noise must run in every realm — carried into workers
  // below. SEED/STRIDE live INSIDE so applyWebglPatch.toString() carries them
  // into the worker realm (a var in the outer IIFE would be undefined there).
  function applyWebglPatch(G) {
   try {
    if (!G || G.__personaWebgl) return;
    G.__personaWebgl = true;
    var SEED = __SEED__;
    var STRIDE = __STRIDE__;

  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

__NATIVE_WRAP__

  function perturbBytes(buf) {
    // Only byte-typed pixel data (the RGBA UNSIGNED_BYTE readback path).
    if (!(buf instanceof Uint8Array) && !(buf instanceof Uint8ClampedArray)) {
      return;
    }
    for (var i = 0; i < buf.length; i += STRIDE) {
      var v = buf[i];
      // skip fully transparent/black and fully opaque/white edges so we don't
      // make obviously-wrong pixels; nudge mid-range bytes only.
      if (v > 1 && v < 254) {
        buf[i] = v + bit(i);
      }
    }
  }

  function patch(proto) {
    if (!proto || !proto.readPixels) return;
    var orig = proto.readPixels;
    proto.readPixels = nativeWrap(orig, function (x, y, w, h, fmt, type, pixels) {
      var r = orig.apply(this, arguments);
      try { perturbBytes(pixels); } catch (e) {}
      return r;
    });
  }

  try { if (G.WebGLRenderingContext) patch(G.WebGLRenderingContext.prototype); } catch (e) {}
  try { if (G.WebGL2RenderingContext) patch(G.WebGL2RenderingContext.prototype); } catch (e) {}
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-webgl",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["webgl.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def _webgl_patch_js(seed: int, native_wrap: str) -> str:
    """The shared patch body, with the engine's ``nativeWrap`` seam spliced in.

    Everything that computes the perturbation — the seed mixing, the stride, the
    byte nudging, the readPixels overrides, the realm bootstrap — is identical on
    both engines. Only the cloak differs, and it arrives as ``native_wrap``.

    THE WORKER DELIVERY PATH IS SHARED TOO, deliberately. An earlier revision of
    PS-78 gave Firefox its own ``blob:`` branch here via a
    ``blob_via_import_scripts`` flag; the premise was wrong and the flag broke
    workers whose URL is revoked after construction. See ``realm_bootstrap_js``
    for the measurement. One path, both engines.
    """
    return (
        _CONTENT_SCRIPT.replace("__SEED__", str(int(seed) & 0xFFFFFFFF))
        .replace("__STRIDE__", str(_STRIDE))
        .replace("__NATIVE_WRAP__", native_wrap)
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyWebglPatch"))
    )


def firefox_webgl_init_script(seed: int) -> str:
    """The same per-seed readPixels perturbation, as an init script for FIREFOX.

    WHY THIS EXISTS AT ALL. ``build_webgl_extension`` has exactly one call site —
    ``process.py:503`` — and ``spawn_browser`` returns on the Firefox arm about
    150 lines BEFORE it, so the extension list carrying the WebGL delta is
    assembled on a path Firefox never reaches. The vector was not merely unwired
    on that engine, it was unreachable.

    That matters because of what this module's own docstring records: the engine
    spoofs the WebGL vendor/renderer/parameter STRINGS per seed, but on a
    GPU-less host the actual rendering is identical everywhere, so ``readPixels``
    collides across profiles and links them. On Firefox the strings were spoofed
    and the pixels were not — which is the sharper of the two failures, because a
    profile that CLAIMS a distinct GPU while RENDERING the shared one is
    self-contradictory in a way a plain missing spoof is not.

    An init script rather than a copied extension directory: MV3 unpacked
    extensions are not a mechanism this engine has, and this is the route the
    other per-profile Firefox spoofs already take. ``add_init_script`` runs at
    document_start in the page realm — the same moment and realm the Chromium
    content script gets — and ``realm_bootstrap_js`` carries the leaf onward into
    workers and child frames, so the worker realm is covered too.

    Deliberately shares ``_CONTENT_SCRIPT`` with the Chromium builder rather than
    copying it: a second copy of the perturbation would let the two engines drift
    apart, and a profile's WebGL identity must not depend on which engine it
    launched. The two differ ONLY in the cloak seam, and Chromium's is reproduced
    verbatim so its readbacks do not move.
    """
    return (
        "(function(){"
        + _webgl_patch_js(seed, firefox_native_wrap_js())
        + "})();"
    )


def build_webgl_extension(seed: int, base_dir: str) -> str:
    """Generate an unpacked extension that adds a deterministic per-seed delta
    to WebGL readPixels byte readbacks, so each profile has a distinct WebGL
    pixel fingerprint. Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = _webgl_patch_js(seed, _CHROMIUM_NATIVE_WRAP)
    (ext_dir / "webgl.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
