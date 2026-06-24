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

# One byte is nudged per this many bytes, by +/-1. Sparse enough to be invisible
# and to keep the image plausible, dense enough that the readback hash differs
# per profile.
_STRIDE = 17

_CONTENT_SCRIPT = r"""
(function () {
  var SEED = __SEED__;
  var STRIDE = __STRIDE__;

  function bit(i) {
    var h = SEED ^ (i + 0x9e3779b1);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    h = (h ^ (h >>> 16)) >>> 0;
    return (h & 1) ? 1 : -1;
  }

  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      replacement.toString = function () { return orig.toString(); };
    } catch (e) {}
    return replacement;
  }

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

  try { if (window.WebGLRenderingContext) patch(WebGLRenderingContext.prototype); } catch (e) {}
  try { if (window.WebGL2RenderingContext) patch(WebGL2RenderingContext.prototype); } catch (e) {}
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


def build_webgl_extension(seed: int, base_dir: str) -> str:
    """Generate an unpacked extension that adds a deterministic per-seed delta
    to WebGL readPixels byte readbacks, so each profile has a distinct WebGL
    pixel fingerprint. Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    script = _CONTENT_SCRIPT.replace(
        "__SEED__", str(int(seed) & 0xFFFFFFFF)
    ).replace("__STRIDE__", str(_STRIDE))
    (ext_dir / "webgl.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
