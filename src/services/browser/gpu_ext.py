"""MAIN-world extension that gives each profile a believable, deterministic
real-GPU WebGL signature.

ON THE LINUX ARM, and there only, the engine renders WebGL through ANGLE's
software fallback (SwiftShader) on a GPU-less VM, so UNMASKED_VENDOR_WEBGL /
UNMASKED_RENDERER_WEBGL read as a generic "Google Inc. (Google)" / SwiftShader
pair. Detectors (CreepJS, Pixelscan, anti-bot WAFs) hash the WebGL
vendor/renderer plus the getParameter limits against real-device datasets; a
SwiftShader value is an instant headless/VM tell.

⚠️ THE SENTENCE ABOVE IS ARM-SPECIFIC AND USED TO BE STATED UNQUALIFIED — it
described the linux arm while reading as a claim about "the engine". Measured
2026-08-25 against ``fingerprint-chromium/148.0.7778.215``, layer OFF, 15 seeds
per arm (PS-161; ``readings/ps161-armsweep-2026-08-25/``):

    linux    every seed -> "Google Inc. (Google)" / SwiftShader   <- HOLDS
    windows  7 distinct real IHV cards across 15 seeds            <- STALE
    macos    "Google Inc. (Apple)" / a real Metal renderer        <- STALE

So on windows and macos the engine ALREADY authors a plausible, seed-derived
identity, and this extension was overriding an already-working spoofer. That
double authorship is not a cosmetic duplication: two independent spoofers that
disagree is precisely the same-vector contradiction PS-155/PS-161 exist to fix
(creepjs read the engine's card while pixelscan read ours, from one profile in
one run). Keep this paragraph qualified — it is the sentence that would
otherwise justify re-adding our identity override on an arm that does not need
it.

WHO AUTHORS THE IDENTITY, PER ARM — see ENGINE_AUTHORED_IDENTITY_ARMS below.
Exactly one author per vector per arm, which is the property that makes the
contradiction structurally impossible rather than merely fixed once.

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

from ...models.hardware_generation import normalize_generation
from ...models.os_type import OS_NORM_TABLE
from ...models.os_type import RECOGNISED_OS_TYPES as _RECOGNISED_OS_TYPES
from ...models.os_type import canonical_os_type
from .engine_platform import ENGINE_HONOURED_PLATFORMS as _ENGINE_HONOURED_PLATFORMS
from .engine_platform import engine_honours
from .worker_wrap import realm_bootstrap_js, realm_guard_js

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
    if (!G) return;
__REALM_GUARD__
    var SEED = __SEED__;
    var OS = "__OS__";
    // The profile's frozen hardware generation. A GPU is only visible to a
    // profile whose generation is >= the generation that GPU was added in, so
    // appending to a pool below cannot change `visible(...).length` for an
    // existing profile — which is what stops the modulo below re-indexing it
    // onto a different card. See models/hardware_generation.py.
    var GEN = __GEN__;

  function h32(x) {
    var h = SEED ^ (x | 0);
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }
  // Entries with no `since` are generation 0 (every entry that shipped before
  // generations existed), so an untagged pool filters to itself unchanged.
  function visible(arr) {
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      if ((arr[i].since || 0) <= GEN) out.push(arr[i]);
    }
    return out;
  }
  // Divide by the length of the profile's OWN generation's pool, never by the
  // whole array's — taking arr.length here is the original defect.
  function pick(arr, salt) {
    var pool = visible(arr);
    return pool[h32(salt) % pool.length];
  }

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
  // Android mobile Chrome runs WebGL over ANGLE-on-OpenGL-ES with a real phone
  // GPU. A D3D11 desktop string on a phone UA is impossible; use the Adreno/Mali
  // pool + GLES version strings below.
  var ANDROID_GPUS = [
    { unmaskedVendor: "Google Inc. (Qualcomm)",
      unmaskedRenderer: "ANGLE (Qualcomm, Adreno (TM) 730, OpenGL ES 3.2)" },
    { unmaskedVendor: "Google Inc. (Qualcomm)",
      unmaskedRenderer: "ANGLE (Qualcomm, Adreno (TM) 660, OpenGL ES 3.2)" },
    { unmaskedVendor: "Google Inc. (ARM)",
      unmaskedRenderer: "ANGLE (ARM, Mali-G78 MP20, OpenGL ES 3.2)" },
    { unmaskedVendor: "Google Inc. (ARM)",
      unmaskedRenderer: "ANGLE (ARM, Mali-G710 MC10, OpenGL ES 3.2)" }
  ];

  // Linux Chrome runs WebGL through ANGLE-over-desktop-GL on top of Mesa. A
  // D3D11 string here is impossible for the same reason it is on a phone UA:
  // Direct3D is a Windows-only API. Before this arm existed, `linux` fell
  // through os_norm's else and was served WIN_GPUS — an impossible value, while
  // voice_ext in the same launch served eSpeak (Linux) voices.
  //
  // Values: harvested from upstream Mesa issue reports (each row names the
  // issue that pasted it) and then put through the two transforms ANGLE applies
  // before a page sees the string. Transcribed with per-value provenance into
  // tests/fixtures/linux-webgl-reference.md — read that file, not this comment,
  // and do not re-derive them from either.
  //
  // The two transforms are why these are NOT the strings glxinfo prints:
  //   1. SanitizeRendererString (ANGLE DisplayGL.cpp:36-52) truncates the
  //      renderer at the first ", DRM " and re-closes the paren. Gated on
  //      feature sanitizeAMDGPURendererString, condition IsLinux() && hasAMD
  //      (renderergl_utils.cpp:2552), on by default. Chromium added it because
  //      the kernel + DRM version were a privacy leak (crbug.com/1181193).
  //      So the DRM version and the uname() kernel release in Mesa's five-term
  //      radeonsi composition (si_get.c:106-110) never reach the page.
  //   2. Context::initRendererString (Context.cpp:3684-3701) composes
  //      "ANGLE (" + vendor + ", " + renderer + ", " + version + ")" and ERASES
  //      EVERY COMMA from each element first — which is why the surviving
  //      "(radeonsi navi21 ACO)" is space-separated here but comma-separated in
  //      the raw driver string. Non-obvious; do not "fix" it back.
  //
  // The vendor element is the driver's GL_VENDOR literal — "AMD" (si_get.c:13-16)
  // or "Intel" (iris_screen.c:80-84) — and UNMASKED_VENDOR is EGL's
  // "Google Inc. (<GL_VENDOR>)" (Display.cpp:2478-2486), the same convention
  // WIN_GPUS already uses.
  //
  // The version element is "OpenGL 4.6", NOT "OpenGL ES 3.2": ANGLE's Linux
  // backend is ANGLE-over-desktop-GL (kDefaultANGLEVulkan is
  // FEATURE_DISABLED_BY_DEFAULT, gl_switches.cc:299-301). Confirmed against a
  // real chrome://gpu capture in mesa#6144, which shows ANGLE composing
  // "..., OpenGL 4.6 (Core Profile) Mesa 22.1.0-develgit-". WebGL truncates that
  // tail: Context.cpp:3697 passes getBackendVersionString(!isWebGL()), so
  // includeFullVersion is false and SanitizeVersionString (DisplayGL.cpp:56-83)
  // keeps only the first token -> "OpenGL 4.6". No Mesa build version is ever
  // page-visible, so none is baked in below.
  //
  // Each tuple is kept EXACTLY as harvested. The ASIC codename and the compiler
  // term (ACO vs LLVM <ver>) are coherent with the marketing name — navi21
  // belongs to RX 6800 and to nothing else. Do not recombine terms across rows:
  // that is the one remaining way to build a well-formed string that never
  // shipped, which is a novel tell and worse than the bug this arm fixes.
  //
  // NVIDIA's proprietary driver is deliberately absent: real strings exist, but
  // its GL_VENDOR literal is "NVIDIA Corporation" rather than "NVIDIA" and that
  // was not confirmed from the closed-source driver. Two vendors is enough.
  var LINUX_GPUS = [
    { unmaskedVendor: "Google Inc. (AMD)",
      unmaskedRenderer: "ANGLE (AMD, AMD Radeon RX 6800 (radeonsi navi21 ACO), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (AMD)",
      unmaskedRenderer: "ANGLE (AMD, AMD Radeon RX 7900 XTX (radeonsi navi31 ACO), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (AMD)",
      unmaskedRenderer: "ANGLE (AMD, AMD Radeon RX 7600 (radeonsi navi33 ACO), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (AMD)",
      unmaskedRenderer: "ANGLE (AMD, AMD Radeon RX 6600 (radeonsi navi23 LLVM 18.1.6), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Mesa Intel(R) HD Graphics 530 (SKL GT2), OpenGL 4.6)" },
    { unmaskedVendor: "Google Inc. (Intel)",
      unmaskedRenderer: "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (ADL-S GT1), OpenGL 4.6)" }
  ];

  // iOS Safari does NOT report a GPU. WebKit returns four compile-time string
  // literals from WebGLRenderingContextBase.cpp before any hardware is
  // consulted — no platform/model/chip branching — after bug 191393 (commit
  // 0a982a43f4, 2018-11-07, "[iOS] WebGL leaks exact GPU type") replaced a
  // passthrough that leaked the real chip. Apple never exposes the A-series part.
  //
  // So this is ONE pair, deliberately not a pool: every iPhone on earth reports
  // exactly this, and the value carries zero entropy. Diversifying it across
  // profiles would itself be the anomaly — any other value is impossible on real
  // hardware. iOS profiles are differentiated on other vectors, never this one.
  var IOS_GPU = { unmaskedVendor: "Apple Inc.",
                  unmaskedRenderer: "Apple GPU" };

  var POOL = (OS === "macos") ? MAC_GPUS
           : (OS === "android") ? ANDROID_GPUS
           : (OS === "linux") ? LINUX_GPUS
           : WIN_GPUS;
  // Do not route iOS through pick(): there is no pool to pick from, and the
  // seed must not reach this value.
  var GPU = (OS === "ios") ? IOS_GPU : pick(POOL, 0x67900);

  // WHO AUTHORS THE IDENTITY on this profile. Baked at build time from the
  // string the ENGINE IS TOLD (--fingerprint-platform), checked against
  // ENGINE_AUTHORED_IDENTITY_ARMS — see the Python side, which carries the
  // measurement and the reasoning. When true, this extension does NOT write
  // UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL and the engine's own
  // seed-derived value reaches the page unopposed.
  //
  // ⚠️ "ON THIS PROFILE", NOT "ON THIS ARM", AND THE DIFFERENCE LEAKED TWICE.
  // OS above is the GPU POOL's arm, folded from os_type. It is NOT what the
  // engine was told and must never be used to answer this question: a mobile
  // profile is backed by linux/macos at the engine while its pool stays on its
  // own arm, and an alias like `win` folds to windows here while the engine
  // rejects it outright. Deferring on either means NEITHER author writes the
  // pair and the host's real GL strings reach the page.
  //
  // THIS GATE IS DELIBERATELY NARROW — it covers the IDENTITY PAIR ONLY, and
  // every other vector below (limits, extension set, shader precision, the
  // masked VENDOR/RENDERER) stays ours on every arm. Measured 2026-08-25,
  // layer OFF, which is what an un-overridden arm actually gets:
  //
  //   * limits    — the engine DOES author these per declared arm (windows
  //     32767x32767 / linux 8192x8192 / macos 16384x16384). On windows its
  //     values are IDENTICAL to COMMON_DESKTOP, so there is nothing to defer.
  //   * extensions — the engine does NOT author these at all. Layer off, the
  //     set is 36 entries and BYTE-IDENTICAL on all three arms: it is the
  //     HOST's set, not a spoof. It advertises the mobile GLES compression
  //     families (ASTC/ETC/ETC1) ALONGSIDE the Direct3D BC families
  //     (S3TC/BPTC/RGTC) — the "supports everything" software-rasteriser
  //     signature. On a claimed "AMD Radeon ... Direct3D11" card that is the
  //     renderer<->extension impossibility this module already calls a hard
  //     cross-check failure (audit7 #3, see DESKTOP_EXTS).
  //
  // So "defer to the engine" can NEVER mean "do not install this extension".
  // Dropping it wholesale would hand the extension set to a GPU-less
  // SwiftShader VM and leak the host — trading a contradiction for a leak,
  // against Invariant #0. One author PER VECTOR is the property that makes the
  // contradiction structurally impossible; one author per MODULE is not.
  var ENGINE_AUTHORS_IDENTITY = __ENGINE_AUTHORS_IDENTITY__;

  // Stable Chrome desktop limits for these GPU tiers on the ANGLE/D3D11 & Metal
  // backends. Float ranges are Float32Array so they read identically to a
  // native getParameter() return (detectors check the type).
  // Desktop D3D11/Metal limits. MAX_VIEWPORT_DIMS is the giveaway: desktop ANGLE
  // reports [32767,32767], but a real Adreno/Mali GLES device reports [16384,
  // 16384] — a desktop viewport on an Adreno renderer is another cross-check
  // impossibility (audit7 #3). Select the limit block by OS like the GPU pool.
  var COMMON_DESKTOP = {
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
  var COMMON_ANDROID = {
    7936: "WebKit",
    7937: "WebKit WebGL",
    3379: 16384,                                // MAX_TEXTURE_SIZE (Adreno/Mali)
    34076: 16384,                               // MAX_CUBE_MAP_TEXTURE_SIZE
    3386: new Float32Array([16384, 16384]),     // MAX_VIEWPORT_DIMS (GLES, not 32767)
    34024: 16384,                               // MAX_RENDERBUFFER_SIZE
    34921: 16,
    36347: 4096,
    36349: 1024,
    36348: 30,
    35660: 16,
    34930: 16,
    35661: 32,
    33901: new Float32Array([1, 1024]),
    33902: new Float32Array([1, 1])
  };
  // iOS WebGL runs on ANGLE-over-Metal (since iOS 15). Every value below is
  // reproduced from ANGLE's DisplayMtl.mm ensureCapsInitialized(); WebKit does
  // no clamping of its own (getParameter passes straight through). The three
  // sharp discriminators against the desktop block are the viewport
  // ([16384,16384] not [32767,32767]), the point size ([1,511] not [1,1024])
  // and MAX_VERTEX_UNIFORM_VECTORS (1024 not 4096).
  //
  // MAX_VARYING_VECTORS = 31 is an iOS-vs-macOS discriminator: macOS Metal
  // subtracts [[position]] and reports 30, iOS reports 31. Copying the desktop
  // value would leave a subtler impossible pair behind.
  //
  // These values are NOT independent — MAX_VIEWPORT_DIMS is definitionally
  // [MAX_TEXTURE_SIZE, MAX_TEXTURE_SIZE], and cube-map/renderbuffer sizes equal
  // it too. Changing one without the others is self-inconsistent and detectable.
  // Do not parameterise them.
  //
  // No model variance: the only model-dependent gate is supportsAppleGPUFamily(3)
  // (= A9), and iOS 15 — the first release with this backend — already requires
  // A9, so every iPhone reaching this path takes the identical branch.
  // ALIASED_POINT_SIZE_RANGE is the one iOS-version-dependent value: [1,64] below
  // iOS 15.0, [1,511] from 15.0. IOS_PRESETS claim iOS 17.5, so [1,511] holds.
  var COMMON_IOS = {
    7936: "WebKit",                             // VENDOR (masked)
    7937: "WebKit WebGL",                       // RENDERER (masked)
    3379: 16384,                                // MAX_TEXTURE_SIZE
    34076: 16384,                               // MAX_CUBE_MAP_TEXTURE_SIZE
    3386: new Float32Array([16384, 16384]),     // MAX_VIEWPORT_DIMS (not 32767)
    34024: 16384,                               // MAX_RENDERBUFFER_SIZE
    34921: 16,                                  // MAX_VERTEX_ATTRIBS
    36347: 1024,                                // MAX_VERTEX_UNIFORM_VECTORS (not 4096)
    36349: 1024,                                // MAX_FRAGMENT_UNIFORM_VECTORS
    36348: 31,                                  // MAX_VARYING_VECTORS (macOS reports 30)
    35660: 16,                                  // MAX_VERTEX_TEXTURE_IMAGE_UNITS
    34930: 16,                                  // MAX_TEXTURE_IMAGE_UNITS
    35661: 32,                                  // MAX_COMBINED_TEXTURE_IMAGE_UNITS
    33901: new Float32Array([1, 511]),          // ALIASED_POINT_SIZE_RANGE (not 1024)
    33902: new Float32Array([1, 1]),            // ALIASED_LINE_WIDTH_RANGE
    // The three below are reachable on BOTH contexts, which is why they live
    // here rather than in WEBGL2_IOS. They are core parameters in WebGL2, but
    // on a WebGL1 context they are also reachable through extensions THIS
    // PROFILE ALREADY ADVERTISES in IOS_GL1_EXTS, at the same numeric enums:
    //   34047 <- EXT_texture_filter_anisotropic (MAX_TEXTURE_MAX_ANISOTROPY_EXT)
    //   34852 <- WEBGL_draw_buffers            (MAX_DRAW_BUFFERS_WEBGL)
    //   36063 <- WEBGL_draw_buffers            (MAX_COLOR_ATTACHMENTS_WEBGL)
    // Spoofing them on WebGL2 only meant the profile ADVERTISED both extensions
    // on WebGL1 and then answered the HOST renderer's real values for them —
    // the exact cross-vector incoherence WEBGL2_IOS exists to close, pointed at
    // the other context. Same device, same page, one call apart.
    // The values are identical on both contexts, so one entry serves both.
    // (34047 is not core WebGL2 either — it is an extension parameter on BOTH
    // contexts, so "WebGL2-only" was wrong about it in both directions. The
    // device capture agrees: it lists MAX_ANISOTROPY unmarked, while the
    // genuinely WebGL2 texture values on the next line carry a (WebGL2) marker.)
    34047: 16,                                  // 0x84FF MAX_TEXTURE_MAX_ANISOTROPY_EXT
    34852: 8,                                   // 0x8824 MAX_DRAW_BUFFERS
    36063: 8                                    // 0x8CDF MAX_COLOR_ATTACHMENTS
  };
  var COMMON = (OS === "android") ? COMMON_ANDROID
             : (OS === "ios") ? COMMON_IOS
             : COMMON_DESKTOP;
  var GL1 = {
    7938: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
    35724: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"
  };
  var GL2 = {
    7938: "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
    35724: "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)"
  };
  // Real iOS Safari reports the bare strings. The "(OpenGL ES 3.0 Chromium)"
  // parenthetical is a Chromium BUILD artifact — announcing Chromium on a device
  // where Chromium cannot exist is a direct contradiction of the iPhone UA.
  var GL1_IOS = {
    7938: "WebGL 1.0",
    35724: "WebGL GLSL ES 1.00"
  };
  var GL2_IOS = {
    7938: "WebGL 2.0",
    35724: "WebGL GLSL ES 3.00"
  };

  // The WebGL2-only parameter block for iOS. Without it every pname below falls
  // through to the HOST renderer (SwiftShader in a GPU-less VM, or the
  // operator's real GPU), so a profile presented a constructed identity on the
  // WebGL1 parameters and the host's truth on these — inconsistent with the
  // value next to it, one call apart. That cross-vector incoherence is the tell
  // this block closes.
  //
  // Values: measured on a physical iPhone (iOS 18.7 Safari, browserleaks.com
  // /webgl). Transcribed with per-value provenance into
  // tests/fixtures/ios-webgl-reference.md — read that file, not this comment,
  // and do not re-derive them from either.
  //
  // WHY THIS IS V2-ONLY: none of the 21 pnames below is reachable on a WebGL1
  // context — not as a core parameter and not through any extension this
  // profile advertises — so a real browser answers INVALID_ENUM for every one
  // of them there. Installed via the V2 extraMap so they reach
  // WebGL2RenderingContext ONLY; answering them on a WebGL1 context would be a
  // fresh impossibility, not a fix.
  //
  // That "not through any extension" clause is load-bearing, and getting it
  // wrong once already shipped a leak: three parameters (34047 anisotropy,
  // 34852 draw buffers, 36063 colour attachments) ARE reachable on WebGL1 via
  // EXT_texture_filter_anisotropic / WEBGL_draw_buffers, both of which this
  // profile advertises in IOS_GL1_EXTS. They now live in COMMON_IOS so both
  // contexts answer them. Before adding a pname here, check it against
  // IOS_GL1_EXTS — "core WebGL2" is NOT the same question as "unreachable on
  // WebGL1", and only the second one justifies a V2-only entry.
  //
  // NOT independent — do NOT parameterise or vary any of these per profile.
  // They are compile-time constants in ANGLE's Metal backend, identical on every
  // iPhone, so per-profile variation is impossible on real hardware and would
  // itself be the tell. The arithmetic ties them together:
  //   MAX_TEXTURE_LOD_BIAS = log2(MAX_TEXTURE_SIZE) + 1 = log2(16384) + 1 = 15
  //   combined uniform components = 4096 + 16384*16/4 = 69632 (both stages)
  //   the three 124s (varying / vertex-output / fragment-input) move together
  //
  // MAX_SAMPLES (0x8D57, 36183) IS DELIBERATELY ABSENT — do not add it. It is
  // the one genuinely runtime-queried value in the set: ANGLE derives it from
  // mFormatTable.getMaxSamples() over {2,4,8}. Source predicts 8; the physical
  // device reported 4. The discrepancy is unexplained and there is no second
  // capture, so it falls through to the host. Falling through is honest;
  // pinning an unexplained constant is a guess wearing a citation.
  //
  // Hex is given beside each decimal so a transposed enum is visible on
  // inspection. This matters more than a typo would: a wrong-but-valid enum
  // still RETURNS a value — just the wrong parameter's — which is a sharper
  // tell than the fall-through being fixed here, while a wrong-and-unused enum
  // never matches and fails silently. Neither is self-catching, so the tests
  // assert each pname against its own expected value.
  //
  // All 21 are scalars: WebGL2 returns these as plain numbers (GLint/GLint64/
  // GLfloat), not as typed arrays. No Float32Array copy applies here — that
  // path exists for the WebGL1 range/vector parameters in COMMON.
  var WEBGL2_IOS = {
    32883: 2048,      // 0x8073 MAX_3D_TEXTURE_SIZE
    35071: 2048,      // 0x88FF MAX_ARRAY_TEXTURE_LAYERS
    34045: 15,        // 0x84FD MAX_TEXTURE_LOD_BIAS = log2(16384)+1
    // 34047 / 34852 / 36063 (anisotropy, draw buffers, colour attachments) are
    // NOT here: they are reachable on a WebGL1 context too, through extensions
    // this profile advertises, so they live in COMMON_IOS. See the note there.
    35658: 4096,      // 0x8B4A MAX_VERTEX_UNIFORM_COMPONENTS (= 1024 vectors * 4)
    35657: 4096,      // 0x8B49 MAX_FRAGMENT_UNIFORM_COMPONENTS (= 1024 vectors * 4)
    35659: 124,       // 0x8B4B MAX_VARYING_COMPONENTS (= 31 vectors * 4)
    37154: 124,       // 0x9122 MAX_VERTEX_OUTPUT_COMPONENTS
    37157: 124,       // 0x9125 MAX_FRAGMENT_INPUT_COMPONENTS
    35371: 16,        // 0x8A2B MAX_VERTEX_UNIFORM_BLOCKS
    35373: 16,        // 0x8A2D MAX_FRAGMENT_UNIFORM_BLOCKS
    35374: 32,        // 0x8A2E MAX_COMBINED_UNIFORM_BLOCKS
    35375: 32,        // 0x8A2F MAX_UNIFORM_BUFFER_BINDINGS
    35376: 16384,     // 0x8A30 MAX_UNIFORM_BLOCK_SIZE
    // 0x8A32 (35378) is MAX_COMBINED_GEOMETRY_UNIFORM_COMPONENTS — a
    // geometry-stage parameter WebGL2 does not expose at all. The uniform-block
    // run is contiguous from 0x8A2B, so it is easy to land on by miscount; it
    // must NOT appear here.
    35377: 69632,     // 0x8A31 MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS
    35379: 69632,     // 0x8A33 MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS
    35380: 16,        // 0x8A34 UNIFORM_BUFFER_OFFSET_ALIGNMENT
    35076: -8,        // 0x8904 MIN_PROGRAM_TEXEL_OFFSET
    35077: 7,         // 0x8905 MAX_PROGRAM_TEXEL_OFFSET
    35968: 4,         // 0x8C80 MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS
    35978: 128,       // 0x8C8A MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS
    35979: 4          // 0x8C8B MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS
  };

  // getSupportedExtensions otherwise reflects the host GPU's real extension set
  // (e.g. a real RTX exposes extensions a claimed UHD 630 wouldn't). Return the
  // stable set real Chrome/Safari reports so it matches the claimed renderer.
  //
  // Extensions must match the CLAIMED renderer, not just the host. The desktop
  // set below has s3tc/s3tc_srgb (DXT) + bptc + rgtc — Direct3D/BC formats a real
  // Adreno/Mali NEVER exposes; those GPUs expose ETC/ETC1/ASTC instead. Shipping
  // the desktop set on an Android profile was a hard renderer<->extension
  // impossibility CreepJS/Pixelscan cross-check (audit7 #3).
  //
  // NOTE: Apple silicon DOES expose s3tc — see the IOS_GL*_EXTS note below. The
  // macOS set here omits it on separate grounds and is out of scope to revisit.
  // Pick the set by OS like the GPU pool.
  var DESKTOP_EXTS = [
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
  var APPLE_EXTS = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query", "EXT_float_blend", "EXT_frag_depth",
    "EXT_shader_texture_lod", "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic",
    "OES_element_index_uint", "OES_fbo_render_mipmap", "OES_standard_derivatives",
    "OES_texture_float", "OES_texture_float_linear", "OES_texture_half_float",
    "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_color_buffer_float", "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
    "WEBGL_lose_context", "WEBGL_multi_draw"
  ];
  var ANDROID_EXTS = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query", "EXT_float_blend", "EXT_frag_depth",
    "EXT_shader_texture_lod", "EXT_texture_filter_anisotropic",
    "KHR_parallel_shader_compile", "OES_element_index_uint",
    "OES_fbo_render_mipmap", "OES_standard_derivatives", "OES_texture_float",
    "OES_texture_float_linear", "OES_texture_half_float",
    "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_color_buffer_float", "WEBGL_compressed_texture_astc",
    "WEBGL_compressed_texture_etc", "WEBGL_compressed_texture_etc1",
    "WEBGL_debug_renderer_info", "WEBGL_debug_shaders", "WEBGL_depth_texture",
    "WEBGL_draw_buffers", "WEBGL_lose_context", "WEBGL_multi_draw"
  ];

  // iOS: getSupportedExtensions() in WebKit is a hardcoded ordered sequence of
  // APPEND_IF_SUPPORTED macros (WebGLRenderingContext.cpp:221-262 for WebGL1,
  // WebGL2RenderingContext.cpp:2694-2732 for WebGL2), NOT a dynamic enumeration.
  // The return order is therefore deterministic and identical on every device —
  // entries drop out, they never reorder. The order is nearly alphabetical but
  // provably not sorted (note WEBKIT_WEBGL_compressed_texture_pvrtc sitting right
  // after WEBGL_compressed_texture_pvrtc, and EXT_sRGB after
  // EXT_texture_mirror_clamp_to_edge — a lowercase-s sorting artifact in the
  // source, deliberate and not a transcription error). A fingerprinter that sorts
  // before comparing sees nothing; one that hashes the raw array catches a wrong
  // order instantly. EMIT SOURCE ORDER VERBATIM — do not "tidy" these lists.
  //
  // s3tc IS REAL on Apple silicon, contrary to the older comment above.
  // DisplayMtl::supportsBCTextureCompression() returns
  // [mMetalDevice supportsBCTextureCompression] behind an iOS 16.4 availability
  // annotation; Apple silicon natively supports BC/DXT while retaining the mobile
  // PVRTC/ETC/ASTC lineage — hence all five families coexisting here.
  // ALL-OR-NOTHING: BC1-BC7 share that single boolean, so s3tc, s3tc_srgb, bptc
  // and rgtc are all present or all absent together. Emitting s3tc without
  // bptc/rgtc is an internally inconsistent set and a detection signal.
  //
  // VERSION FLOOR: s3tc needs iOS >= 16.4, and the 2023-08-08 WebKit batch
  // (EXT_clip_control, WEBGL_polygon_mode, EXT_conservative_depth,
  // EXT_render_snorm, EXT_depth_clamp, WEBGL_render_shared_exponent,
  // WEBGL_stencil_texturing) ships no earlier than iOS 17. IOS_PRESETS claim
  // iOS 17.5, which clears both. If a preset is ever added claiming an OLDER iOS,
  // this set becomes wrong for it — see the note in device_presets.py.
  //
  // Draft extensions (WEBGL_draw_instanced_base_vertex_base_instance and
  // WEBGL_multi_draw_instanced_base_vertex_base_instance) are guarded by
  // && enableDraftExtensions in source and confirmed absent on device: off in
  // shipping Safari. EXT_disjoint_timer_query[_webgl2] is excluded because timer
  // queries are a timing-attack surface Safari is conservative about, but its
  // shipping default could not be established — this is the weakest single
  // decision in the set and is recorded as a known uncertainty.
  var IOS_GL2_EXTS = [
    "EXT_clip_control", "EXT_color_buffer_float", "EXT_color_buffer_half_float",
    "EXT_conservative_depth", "EXT_depth_clamp", "EXT_float_blend",
    "EXT_polygon_offset_clamp", "EXT_render_snorm", "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic",
    "EXT_texture_mirror_clamp_to_edge", "EXT_texture_norm16",
    "KHR_parallel_shader_compile", "NV_shader_noperspective_interpolation",
    "OES_draw_buffers_indexed", "OES_sample_variables",
    "OES_shader_multisample_interpolation", "OES_texture_float_linear",
    "WEBGL_blend_func_extended", "WEBGL_clip_cull_distance",
    "WEBGL_compressed_texture_astc", "WEBGL_compressed_texture_etc",
    "WEBGL_compressed_texture_etc1", "WEBGL_compressed_texture_pvrtc",
    "WEBKIT_WEBGL_compressed_texture_pvrtc", "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders", "WEBGL_lose_context", "WEBGL_multi_draw",
    "WEBGL_polygon_mode", "WEBGL_provoking_vertex",
    "WEBGL_render_shared_exponent", "WEBGL_stencil_texturing"
  ];
  var IOS_GL1_EXTS = [
    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_clip_control",
    "EXT_color_buffer_half_float", "EXT_depth_clamp", "EXT_float_blend",
    "EXT_frag_depth", "EXT_polygon_offset_clamp", "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc", "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic", "EXT_texture_mirror_clamp_to_edge",
    "EXT_sRGB", "KHR_parallel_shader_compile", "OES_element_index_uint",
    "OES_fbo_render_mipmap", "OES_standard_derivatives", "OES_texture_float",
    "OES_texture_float_linear", "OES_texture_half_float",
    "OES_texture_half_float_linear", "OES_vertex_array_object",
    "WEBGL_blend_func_extended", "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_astc", "WEBGL_compressed_texture_etc",
    "WEBGL_compressed_texture_etc1", "WEBGL_compressed_texture_pvrtc",
    "WEBKIT_WEBGL_compressed_texture_pvrtc", "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
    "WEBGL_lose_context", "WEBGL_multi_draw", "WEBGL_polygon_mode"
  ];

  var STABLE_EXTS = (OS === "android") ? ANDROID_EXTS
                  : (OS === "macos") ? APPLE_EXTS
                  : DESKTOP_EXTS;
  // Real browsers NEVER return the same extension list for a WebGL1 and a WebGL2
  // context, so the list is selected per context rather than shared. Only iOS
  // models that split today; the other platforms keep their single list until
  // each is given its own measured pair.
  var EXTS_GL1 = (OS === "ios") ? IOS_GL1_EXTS : STABLE_EXTS;
  var EXTS_GL2 = (OS === "ios") ? IOS_GL2_EXTS : STABLE_EXTS;

  var UNMASKED_VENDOR = 0x9245;    // 37445
  var UNMASKED_RENDERER = 0x9246;  // 37446

  function installOn(Ctor, extraMap, extList) {
    if (!Ctor || !Ctor.prototype) return;
    var proto = Ctor.prototype;

    // Synthesising the debug-renderer-info handle only makes sense when WE
    // answer the two constants it carries. When the engine authors the
    // identity, getExtension is left completely alone: a synthesised handle
    // whose constants then fall through to a context that never enabled the
    // extension is a state no real browser is in.
    var realGetExtension = proto.getExtension;
    if (realGetExtension && !ENGINE_AUTHORS_IDENTITY) {
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
        // The IDENTITY PAIR — the one vector whose authorship is arm-dependent.
        // When the engine authors it, fall through to the real getParameter so
        // the engine's own seed-derived value reaches the page. Everything
        // below this point stays ours on every arm.
        if (!ENGINE_AUTHORS_IDENTITY) {
          if (pname === UNMASKED_VENDOR) return GPU.unmaskedVendor;
          if (pname === UNMASKED_RENDERER) return GPU.unmaskedRenderer;
        }
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

    // getSupportedExtensions otherwise reflects the host GPU's real extension
    // set (e.g. a real RTX exposes extensions a claimed UHD 630 wouldn't).
    // Return the stable per-context set for the claimed renderer, chosen by the
    // caller — WebGL1 and WebGL2 get DIFFERENT lists (see EXTS_GL1/EXTS_GL2).
    var realGSE = proto.getSupportedExtensions;
    if (realGSE && extList) {
      proto.getSupportedExtensions = nativeWrap(realGSE, function () {
        try { return extList.slice(); } catch (e) {}
        return realGSE.call(this);
      });
    }
  }

  var V1 = (OS === "ios") ? GL1_IOS : GL1;
  var V2 = GL2;
  if (OS === "ios") {
    // iOS WebGL2 gets the version strings AND the WebGL2-only parameter block.
    // Merged into ONE extraMap because installOn takes a single map; built here
    // rather than as a literal so GL2_IOS stays the version-string pair.
    //
    // PRECEDENCE, made deliberate rather than incidental: installOn's lookup is
    // extraMap BEFORE COMMON (see getParameter above), so anything in this map
    // wins over COMMON_IOS. The three key sets are disjoint by construction —
    // GL2_IOS is {7938, 35724}, WEBGL2_IOS is the 21 WebGL2-only pnames, and
    // COMMON_IOS is the WebGL1 set — so no key is contested and the merge order
    // below changes nothing today. It is fixed anyway so that if a future key
    // ever does collide, the WebGL2 block loses to the version strings and the
    // outcome is a stated rule instead of an accident of iteration order.
    V2 = {};
    for (var wk in WEBGL2_IOS) {
      if (Object.prototype.hasOwnProperty.call(WEBGL2_IOS, wk)) V2[wk] = WEBGL2_IOS[wk];
    }
    for (var gk in GL2_IOS) {
      if (Object.prototype.hasOwnProperty.call(GL2_IOS, gk)) V2[gk] = GL2_IOS[gk];
    }
  }
  try { installOn(G.WebGLRenderingContext, V1, EXTS_GL1); } catch (e) {}
  try { installOn(G.WebGL2RenderingContext, V2, EXTS_GL2); } catch (e) {}
   } catch (e) {}
  }

__REALM_BOOTSTRAP__
})();
"""

# The arms on which the ENGINE authors the WebGL identity pair
# (UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL) and this extension stands
# down for that ONE vector. Every other vector this module spoofs stays ours on
# every arm — see the ENGINE_AUTHORS_IDENTITY comment in the content script for
# why the gate is deliberately this narrow.
#
# WHY THIS IS A SET AND NOT A BOOLEAN: the answer is per arm, because the
# engine's behaviour is per arm. Measured 2026-08-25 against
# fingerprint-chromium/148.0.7778.215, masking layer OFF, one profile per seed,
# reading UNMASKED_RENDERER_WEBGL (PS-161):
#
#   arm      seeds  distinct  P(two profiles collide)  effective pool
#   linux      15      1              100.0%                1.00
#   windows    15      7               15.6%                6.43
#   macos      30      2               76.9%                1.30
#
# and, for comparison, THIS module's own pools:
#
#   LINUX_GPUS  8 entries -> 12.5%      WIN_GPUS 5 -> 20.0%     MAC_GPUS 2 -> 50.0%
#
# * windows  -> ENGINE. It authors 7 distinct real IHV cards and is strictly
#   better than WIN_GPUS (15.6% vs 20.0%). Deferring removes the second author
#   without costing unlinkability. This is the whole (b) arm.
# * linux    -> OURS. The engine returns ONE identical SwiftShader string on
#   every seed. Deferring would give every linux profile the same "GPU" — a
#   shared cross-profile identifier, which Level 2 (mutual unlinkability)
#   forbids outright. (a) is forced here by measurement, not preference.
# * macos    -> OURS. The engine varies, but across 30 seeds it produced only
#   TWO values (Apple M2 87%, Apple M4 13%): a 76.9% chance that two profiles
#   share a card, WORSE than the 50.0% of our own two-entry MAC_GPUS. The
#   owner's decision selected (b) for macos "subject to the seed requirement",
#   and the planner pre-registered the outcome: "if macos turns out to vary
#   weakly — a pool small enough that profiles collide often — that is a
#   finding that changes macos to (a) on the same Level 2 grounds as linux".
#   The extra seeds were run and that condition fired, so this line EXECUTES a
#   conditional the decision-maker already wrote down rather than making a new
#   choice. Note "small pool" and "colliding often" are different claims and
#   only the second one matters here: Apple ships M1-M4 in Pro/Max/Ultra
#   variants, so two values is not forced by Apple's real product line.
#
# android and ios are absent deliberately, and not merely for want of a
# measurement. The engine has no android or ios platform at all — process.py
# backs those profiles with the nearest desktop platform it DOES spoof — so an
# engine-authored desktop identity on a phone UA would be the impossible value
# this module's ANDROID_GPUS arm exists to prevent. iOS additionally pins one
# constant pair for every device on earth by design (see IOS_GPU).
#
# ⚠️ THIS SET IS A CLAIM ABOUT A THIRD PARTY'S BUILD, not a permanent property.
# The engine autobumps daily and could narrow its pool at any time, which would
# silently cost us unlinkability on any arm listed here.
#
# `verify.engine_gpu_variance` is the check written for exactly that, and since
# PS-176 it IS WIRED TO A PATH THAT CAN GO RED. Its status, stated as it
# actually is, because this set is only as safe as the thing watching it:
#   * Its JUDGEMENT (`classify`) is pure and IS exercised in CI on every run,
#     including the cases where it must go red.
#   * Its READING (`measure`) needs the product's own engine, which CI does not
#     provision for the normal test jobs (`browser_firefox` only, see ci.yml).
#     It still runs on an operator machine via
#     `python -m src.services.verify.engine_gpu_variance check`.
#   * THE READING IS NOW GATED DAILY, by `.github/workflows/engine-gpu-variance.yml`
#     (06:40 UTC). That job provisions the engine itself and measures the build
#     UPSTREAM IS SERVING RIGHT NOW — deliberately UNPINNED, because the risk is
#     `/releases/latest` and a pin is exactly what would hide it. A narrowed arm
#     fails that job; an under-sampled run fails it too, so "we failed to look"
#     cannot wear the colour of "we looked and it was fine".
#     (NOT `engine-autoupdate.yml`: that job bumps FIREFOX and never touches
#     fingerprint-chromium, so a chromium check there could never fire on the
#     event it exists to catch.)
#
# ⚠️ WHAT IS STILL NOT COVERED, so this set is not read as safer than it is:
# DETECTION IS DAILY, INSTALLATION IS HOURLY. `app.py:_check_engines_periodic`
# polls every hour, unattended, and installs whatever upstream published, with
# `policy.KNOWN_BAD_VERSIONS` empty and no ceiling. So a bad build can reach
# machines up to ~24h before the gate reads it. That window is not closable by
# measuring at install time — you cannot seed-vary a build before installing it,
# and a 15-launch measurement inside an unattended install would wedge the app.
# The remedy for a red run is to name the tag in `policy.KNOWN_BAD_VERSIONS`,
# which every chromium install passes through and which refuses a build by name
# without waiting for a persona release.
ENGINE_AUTHORED_IDENTITY_ARMS = frozenset({"windows"})


# The engine's own vocabulary lives in ``engine_platform``, which OWNS the value
# ``--fingerprint-platform`` is set to. Re-exported here so this module's
# existing readers still find it beside the set it constrains, but NOT restated:
# a second copy is exactly the drift that produced the `win` leak.
ENGINE_HONOURED_PLATFORMS = _ENGINE_HONOURED_PLATFORMS


# The RAW ``os_type`` spellings the fold recognises, and the fold itself.
#
# ⚠️ NEITHER IS DEFINED HERE ANY MORE (PS-187). The table moved DOWN to
# ``models/os_type.py`` and is RE-EXPORTED here, exactly as
# ``ENGINE_HONOURED_PLATFORMS`` above is re-exported from ``engine_platform``
# and for the identical reason: a second copy is the drift that produced the
# `win` leak. Every existing reader of these three names still finds them here.
#
# It had to move because the same vocabulary now governs what may be STORED,
# and that guarantee is enforced at the MODEL (``Profile.__setattr__``) — the
# one place every write door funnels through. ``models/`` cannot import
# ``services/`` without inverting the dependency direction, so the owner is the
# model and this layer is the consumer.
#
# NOTE this set is about GPU POOL selection, NOT about authorship — see
# ``ENGINE_HONOURED_PLATFORMS`` above for why asking it the authorship question
# is what produced the `win` host leak.
_OS_NORM_TABLE = OS_NORM_TABLE
RECOGNISED_OS_TYPES = _RECOGNISED_OS_TYPES


def _os_norm(os_type: str) -> str:
    """Fold a raw ``os_type`` onto the arm vocabulary the content script uses.

    Unrecognised values fold to ``"windows"``, which is what the GPU POOL
    selection wants (a plausible desktop card beats no card). Authorship is a
    SEPARATE question and must not be read off this value — see
    :func:`engine_authors_identity_for_engine_platform`.

    Delegates to the model's ``canonical_os_type`` rather than re-walking the
    table, so the value this layer POOLS on and the value the model STORES are
    computed by one function and cannot disagree.
    """
    return canonical_os_type(os_type)


def engine_authors_identity(os_norm: str) -> bool:
    """Whether the ENGINE, not this extension, authors the identity pair.

    Takes an already-normalised arm (the ``os_norm`` vocabulary the builder
    computes), so an unknown value is simply not in the set and this module
    keeps authorship.

    ⚠️ CALLERS MUST NOT ASK THIS ABOUT A RAW ``os_type``, AND MUST NOT ASK IT
    ABOUT ``os_norm`` EITHER. ``os_norm`` folds every unrecognised spelling
    into ``"windows"`` — the one engine-authored arm — so the fold runs BEFORE
    this function would ever see the unknown value, and asking here is too late
    to be fail-safe. Nor is ``os_type`` the right question: it is not what the
    engine receives. A mobile profile is backed by ``linux``/``macos`` at the
    engine, so a ``windows`` + ``mobile`` profile is handed ``linux`` — and
    this layer standing down for a ``windows`` identity nobody wrote is the
    same host leak by a different route. Either way neither author writes the
    pair and the HOST's GL strings reach the page (Invariant #0). Use
    :func:`engine_authors_identity_for_engine_platform`, which resolves
    authorship from the value ``--fingerprint-platform`` is actually set to.
    """
    return os_norm in ENGINE_AUTHORED_IDENTITY_ARMS


def engine_authors_identity_for_engine_platform(engine_platform: str) -> bool:
    """Fail-safe authorship resolution from the value THE ENGINE IS TOLD.

    Takes ``--fingerprint-platform``'s own string — the one
    ``engine_platform.engine_platform_for`` computed and ``process.py`` put on
    the command line — never an ``os_type`` this module re-derived it from.
    That is the entire point, and it is what makes the two provably one value
    instead of two copies that agree by coincidence.

    A platform the ENGINE does not honour keeps OUR spoof rather than being
    deferred to an engine that was never told to spoof it: not-honoured in,
    ``False`` out — persona authors the identity.

    ⚠️ ASKING THIS ABOUT ``os_type`` IS THE BUG, TWICE OVER. ``os_type`` is not
    what the engine receives, and the gap between them has now leaked in two
    different directions:

      * ``win`` — a spelling OUR fold recognises and the engine REJECTS.
        Measured: ``--fingerprint-platform=win`` returns ``Google Inc.
        (Google)`` / SwiftShader, while ``windows`` returns a real AMD card.
      * ``windows`` + ``device_type=mobile`` — an ``os_type`` the engine
        honours, on a profile where the engine is handed ``linux`` instead,
        because a mobile profile is backed by the nearest desktop platform.
        Our layer stood down expecting a ``windows`` identity the engine was
        never asked for, and the engine's linux arm returns SwiftShader on
        every seed.

    Both are ONE failure: whoever answers this question from anything other
    than the value on the command line is guessing, and a wrong guess means
    NEITHER author writes the identity pair. ``getParameter(UNMASKED_*)`` falls
    through to the real implementation and the HOST's GL strings reach the page
    — Invariant #0, and a ``HOST_LEAK`` row under ``matrix_consistency``, which
    ranks it above ``CONTRADICTION`` and fires on a single row.

    Keying on the value itself closes the CLASS rather than either instance:
    there is no second computation left to diverge from, so a future axis
    nobody has enumerated cannot reopen this the way ``device_type`` reopened
    the alias fix.
    """
    # Lowercased because the engine itself lowercases its platform argument —
    # MEASURED (see ENGINE_HONOURED_PLATFORMS), so "WINDOWS" genuinely does
    # reach the engine's windows spoofer and deferring on it is correct.
    if not engine_honours(engine_platform):
        return False
    return engine_authors_identity(str(engine_platform).lower())


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


def build_gpu_extension(
    seed: int, os_type: str, base_dir: str, generation: int,
    *, engine_platform: str,
) -> str:
    """Generate an unpacked extension that spoofs the WebGL getParameter GPU
    signature deterministically per profile seed, constrained to the profile's
    spoofed OS.

    Five arms: macos -> Apple/Metal pool, android -> Adreno/Mali ANGLE-over-GLES
    pool, linux -> Mesa radeonsi/iris ANGLE-over-desktop-GL pool, windows
    (default) -> ANGLE/D3D11 pool, and ios -> the single WebKit compile-time
    vendor/renderer pair (no pool: real iOS reports one constant value for every
    device, so a seed-varied one would itself be the tell).

    linux needs its own arm for the same reason android did: every WIN_GPUS
    entry is a literal Direct3D11 string, and Direct3D is a Windows-only API, so
    serving one to a --fingerprint-platform=linux profile is an impossible value
    rather than merely an implausible one.

    ``engine_platform`` is the string the ENGINE is launched with — the value
    ``engine_platform.engine_platform_for`` computed and ``process.py`` puts on
    the command line as ``--fingerprint-platform``. AUTHORSHIP IS RESOLVED FROM
    IT, and it is a REQUIRED, SEPARATE argument from ``os_type`` because the two
    genuinely differ: a mobile profile is backed by ``linux``/``macos`` at the
    engine while its GPU POOL must stay on its own arm. This module cannot
    derive one from the other — ``engine_platform`` is a function of
    ``(os_type, device_type)`` and ``device_type`` is deliberately not passed
    here — which is precisely why it is handed in rather than recomputed. Two
    review rounds were spent on two different re-derivations of it, each leaking
    on the axis the other did not cover.

    Returns its directory.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    ot = str(os_type).lower()
    os_norm = _os_norm(ot)
    script = (
        _CONTENT_SCRIPT
        .replace("__SEED__", str(int(seed) & 0xFFFFFFFF))
        .replace("__GEN__", str(normalize_generation(generation)))
        .replace("__OS__", os_norm)
        .replace(
            "__ENGINE_AUTHORS_IDENTITY__",
            # Resolved from the value the ENGINE IS TOLD, not from os_type and
            # not from os_norm. os_norm folds every unrecognised spelling to
            # "windows" (the one engine-authored arm), and os_type is not what
            # the engine receives on a mobile profile — reading authorship off
            # either one makes a profile defer to an engine that was never
            # asked to spoof that platform, and neither author writes the pair.
            "true" if engine_authors_identity_for_engine_platform(
                engine_platform) else "false",
        )
        .replace("__REALM_BOOTSTRAP__", realm_bootstrap_js("applyGpuPatch"))
        .replace("__REALM_GUARD__", realm_guard_js("gpu"))
    )
    (ext_dir / "gpu.js").write_text(script, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(_MANIFEST, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
