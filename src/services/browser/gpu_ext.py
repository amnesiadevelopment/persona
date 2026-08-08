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

from .worker_wrap import realm_bootstrap_js

_CONTENT_SCRIPT = r"""
(function () {
  // Patch one realm G (window or a WorkerGlobalScope). Detectors read the WebGL
  // vendor/renderer from an OffscreenCanvas inside a Web Worker to catch a
  // page-only spoof — the real GPU (a different IHV than the page reports) leaks
  // there. So the same override runs in every realm, carried into workers below.
  // SEED/OS live INSIDE this function so applyGpuPatch.toString() carries them
  // into the worker realm (a var in the outer IIFE would be undefined there).
  function applyGpuPatch(G) {
   try {
    if (!G || G.__personaGpu) return;
    G.__personaGpu = true;
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
      // Mark for the native_ext Function.prototype.toString patch so a detector
      // calling Function.prototype.toString.call(replacement) reads native. A
      // plain replacement.toString override is bypassed by that .call form.
      Object.defineProperty(replacement, '__pnaName', { value: orig.name });
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

    // getShaderPrecisionFormat otherwise returns the HOST GPU's real precision
    // (D3D11/Metal go through native ANGLE, not SwiftShader on Windows/mac), which
    // contradicts the spoofed renderer — a renderer↔precision mismatch creepjs
    // cross-checks. Normalize to the canonical ANGLE-over-D3D11/Metal values that
    // every real Chrome reports for these backends (float highp/medium/low all
    // 127/127/23; int 31/30/0), so precision agrees with the claimed ANGLE GPU.
    var realGSPF = proto.getShaderPrecisionFormat;
    if (realGSPF) {
      var HIGH_FLOAT = 0x8DF2, MEDIUM_FLOAT = 0x8DF1, LOW_FLOAT = 0x8DF0;
      proto.getShaderPrecisionFormat = nativeWrap(realGSPF, function (shaderType, precisionType) {
        try {
          var isFloat = (precisionType === HIGH_FLOAT ||
                         precisionType === MEDIUM_FLOAT ||
                         precisionType === LOW_FLOAT);
          var out = isFloat ? { rangeMin: 127, rangeMax: 127, precision: 23 }
                            : { rangeMin: 31, rangeMax: 30, precision: 0 };
          // Return a WebGLShaderPrecisionFormat-shaped object; a plain object is
          // indistinguishable via the standard getters detectors read.
          return out;
        } catch (e) {}
        return realGSPF.call(this, shaderType, precisionType);
      });
    }

    // getSupportedExtensions otherwise reflects the host GPU's real extension set
    // (e.g. a real RTX exposes extensions a claimed UHD 630 wouldn't). Return the
    // stable ANGLE-D3D11/Metal set real Chrome reports so it matches the renderer.
    var realGSE = proto.getSupportedExtensions;
    if (realGSE) {
      var STABLE_EXTS = [
        "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float",
        "EXT_disjoint_timer_query", "EXT_float_blend", "EXT_frag_depth",
        "EXT_shader_texture_lod", "EXT_texture_compression_bptc",
        "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic",
        "OES_element_index_uint", "OES_fbo_render_mipmap", "OES_standard_derivatives",
        "OES_texture_float", "OES_texture_float_linear", "OES_texture_half_float",
        "OES_texture_half_float_linear", "OES_vertex_array_object",
        "WEBGL_color_buffer_float", "WEBGL_compressed_texture_s3tc",
        "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
        "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
        "WEBGL_lose_context", "WEBGL_multi_draw"
      ];
      proto.getSupportedExtensions = nativeWrap(realGSE, function () {
        try { return STABLE_EXTS.slice(); } catch (e) {}
        return realGSE.call(this);
      });
    }
  }

  try { installOn(G.WebGLRenderingContext, GL1); } catch (e) {}
  try { installOn(G.WebGL2RenderingContext, GL2); } catch (e) {}
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
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
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyGpuPatch"))
    )
    (ext_dir / "gpu.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
