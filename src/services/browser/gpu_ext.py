"""MAIN-world extension that gives each profile a believable, deterministic
real-GPU WebGL signature.

The engine renders WebGL through ANGLE's software fallback (SwiftShader) on a
GPU-less VM, so UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL read as a
generic "Google Inc. (Google)" / SwiftShader pair. Detectors (CreepJS,
Pixelscan, anti-bot WAFs) hash the WebGL vendor/renderer plus the getParameter
limits against real-device datasets; a SwiftShader value is an instant
headless/VM tell.

This extension picks one real desktop GPU deterministically from the seed and
overrides gl.getParameter() (plus getExtension for the WEBGL_debug_renderer_info
constants) on both WebGLRenderingContext and WebGL2RenderingContext so the
fingerprint-relevant params report that GPU. The chosen GPU matches the
profile's spoofed OS: a Windows profile gets an ANGLE/D3D11 string with a
"Google Inc. (<IHV>)" vendor, a macOS profile an Apple/Metal string. The
readPixels pixel-noise extension is orthogonal and stays as-is.

GPU string formats verified against real-Chrome captures (deviceandbrowserinfo,
CloakBrowser issue reports): Windows = ANGLE-over-D3D11 with the literal
"Google Inc. (<IHV>)" UNMASKED_VENDOR convention; macOS = ANGLE-over-Metal with
"Unspecified Version".
"""

import json
import pathlib

_CONTENT_SCRIPT = r"""
(function () {
  var SEED = __SEED__;
  var OS = "__OS__";

  function h32(x) {
    var h = SEED ^ (x | 0);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }
  function pick(arr, salt) { return arr[h32(salt) % arr.length]; }

  function nativeWrap(orig, replacement) {
    try {
      Object.defineProperty(replacement, 'name', { value: orig.name });
      replacement.toString = function () { return orig.toString(); };
    } catch (e) {}
    return replacement;
  }

  var WIN_GPUS = [
    { unmaskedVendor: "Google Inc. (NVIDIA)",
      unmaskedRenderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002487) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
    { unmaskedVendor: "Google Inc. (NVIDIA)",
      unmaskedRenderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 (0x00002484) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x0000A7A1) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
    { unmaskedVendor: "Google Inc. (AMD)",
      unmaskedRenderer: "ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)" }
  ];
  var MAC_GPUS = [
    { unmaskedVendor: "Google Inc. (Apple)",
      unmaskedRenderer: "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)" },
    { unmaskedVendor: "Google Inc. (Apple)",
      unmaskedRenderer: "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)" }
  ];

  var POOL = (OS === "macos") ? MAC_GPUS : WIN_GPUS;
  var GPU = pick(POOL, 0x67900);

  // Stable Chrome desktop limits for these GPU tiers on the ANGLE/D3D11 & Metal
  // backends. Float ranges are Float32Array so they read identically to a
  // native getParameter() return (detectors check the type).
  var COMMON = {
    7936: "WebKit",                            // VENDOR (masked)
    7937: "WebKit WebGL",                       // RENDERER (masked)
    3379: 16384,                                // MAX_TEXTURE_SIZE
    34076: 16384,                               // MAX_CUBE_MAP_TEXTURE_SIZE
    3386: new Float32Array([32767, 32767]),     // MAX_VIEWPORT_DIMS
    34024: 16384,                               // MAX_RENDERBUFFER_SIZE
    34921: 16,                                  // MAX_VERTEX_ATTRIBS
    36347: 4096,                                // MAX_VERTEX_UNIFORM_VECTORS
    36349: 1024,                                // MAX_FRAGMENT_UNIFORM_VECTORS
    36348: 30,                                  // MAX_VARYING_VECTORS
    35660: 16,                                  // MAX_VERTEX_TEXTURE_IMAGE_UNITS
    34930: 16,                                  // MAX_TEXTURE_IMAGE_UNITS
    35661: 32,                                  // MAX_COMBINED_TEXTURE_IMAGE_UNITS
    33901: new Float32Array([1, 1024]),         // ALIASED_POINT_SIZE_RANGE
    33902: new Float32Array([1, 1])             // ALIASED_LINE_WIDTH_RANGE
  };
  var GL1 = {
    7938: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
    35724: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"
  };
  var GL2 = {
    7938: "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    35724: "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)"
  };

  var UNMASKED_VENDOR = 0x9245;    // 37445
  var UNMASKED_RENDERER = 0x9246;  // 37446

  function installOn(Ctor, extraMap) {
    if (!Ctor || !Ctor.prototype) return;
    var proto = Ctor.prototype;

    var realGetExtension = proto.getExtension;
    if (realGetExtension) {
      proto.getExtension = nativeWrap(realGetExtension, function (name) {
        if (name === 'WEBGL_debug_renderer_info') {
          var r = null;
          try { r = realGetExtension.call(this, name); } catch (e) {}
          if (!r) {
            r = { UNMASKED_VENDOR_WEBGL: UNMASKED_VENDOR,
                  UNMASKED_RENDERER_WEBGL: UNMASKED_RENDERER };
          }
          return r;
        }
        return realGetExtension.call(this, name);
      });
    }

    var realGetParameter = proto.getParameter;
    if (!realGetParameter) return;
    proto.getParameter = nativeWrap(realGetParameter, function (pname) {
      try {
        if (pname === UNMASKED_VENDOR) return GPU.unmaskedVendor;
        if (pname === UNMASKED_RENDERER) return GPU.unmaskedRenderer;
        if (extraMap && Object.prototype.hasOwnProperty.call(extraMap, pname)) {
          var ev = extraMap[pname];
          return (ev instanceof Float32Array) ? new Float32Array(ev) : ev;
        }
        if (Object.prototype.hasOwnProperty.call(COMMON, pname)) {
          var cv = COMMON[pname];
          return (cv instanceof Float32Array) ? new Float32Array(cv) : cv;
        }
      } catch (e) {}
      return realGetParameter.call(this, pname);
    });
  }

  try { installOn(window.WebGLRenderingContext, GL1); } catch (e) {}
  try { installOn(window.WebGL2RenderingContext, GL2); } catch (e) {}
})();
"""

_MANIFEST = {
    "manifest_version": 3,
    "name": "persona-gpu",
    "version": "1.0",
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["gpu.js"],
            "run_at": "document_start",
            "all_frames": True,
            "world": "MAIN",
        }
    ],
}


def build_gpu_extension(seed: int, os_type: str, base_dir: str) -> str:
    """Generate an unpacked extension that spoofs the WebGL getParameter GPU
    signature deterministically per profile seed, constrained to the profile's
    spoofed OS (macos/ios -> Apple/Metal, everything else -> ANGLE/D3D11).
    Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    os_norm = (
        "macos"
        if str(os_type).lower() in ("macos", "mac", "darwin", "ios")
        else "windows"
    )
    script = (
        _CONTENT_SCRIPT
        .replace("__SEED__", str(int(seed) & 0xFFFFFFFF))
        .replace("__OS__", os_norm)
    )
    (ext_dir / "gpu.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
