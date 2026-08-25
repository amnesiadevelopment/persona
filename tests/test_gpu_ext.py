import json
import pathlib

import pytest

from src.services.browser.engine_platform import engine_platform_for
from src.services.browser.gpu_ext import build_gpu_extension
from tests.native_mask_probe import GL_STUBS, assert_reads_native


def _read(d, name):
    return (pathlib.Path(d) / name).read_text(encoding="utf-8")


def test_builds_files(tmp_path):
    d = build_gpu_extension(0xDEADBEEF, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop"))
    p = pathlib.Path(d)
    assert (p / "gpu.js").is_file()
    assert (p / "manifest.json").is_file()


def test_native_tostring_masking(tmp_path):
    # THE INVARIANT: getExtension/getParameter wrappers must stringify as native
    # under Function.prototype.toString.call(fn) — the form a masking detector
    # uses, and the one an own `.toString` override is bypassed by.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the override installed and whether or not the patch honours it, and
    # would fail on a marker-free implementation that is strictly better.
    # assert_reads_native also runs the counterfactual: without native_ext's
    # patch the same probe must NOT read native.
    d = build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop"))
    js = _read(d, "gpu.js")
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "gpu.js"],
        GL_STUBS,
        "Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)",
        "getParameter",
    )
    assert "replacement.toString = function" not in js


def test_carries_gpu_spoof_into_workers(tmp_path):
    # A detector reads the WebGL vendor/renderer from an OffscreenCanvas inside a
    # Web Worker to catch a page-only spoof — the real GPU (a different IHV than
    # the page reports) leaks there. The spoof must be carried into workers.
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert "applyGpuPatch" in js
    assert "G.Worker" in js
    assert "SharedWorker" in js
    # SEED/OS must live INSIDE applyGpuPatch so .toString() carries them into the
    # worker; a value referenced from the outer IIFE is undefined in the worker.
    body = js.split("function applyGpuPatch(G)", 1)[1].split("__pnaBoot", 1)[0]
    assert "var SEED =" in body
    assert 'var OS =' in body


def test_manifest_mv3_main_world(tmp_path):
    d = build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop"))
    m = json.loads(_read(d, "manifest.json"))
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_seed_and_os_baked(tmp_path):
    w = _read(build_gpu_extension(0xABCDEF, "windows", str(tmp_path / "w"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    m = _read(build_gpu_extension(0xABCDEF, "macos", str(tmp_path / "m"), 0, engine_platform=engine_platform_for("macos", "desktop")), "gpu.js")
    assert str(0xABCDEF) in w
    assert 'var OS = "windows";' in w
    assert 'var OS = "macos";' in m
    assert "__SEED__" not in w and "__OS__" not in w


def test_unmasked_constants_and_extension(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert "0x9245" in js and "0x9246" in js
    assert "UNMASKED_VENDOR_WEBGL" in js and "UNMASKED_RENDERER_WEBGL" in js
    assert "WEBGL_debug_renderer_info" in js


def test_real_windows_strings(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert "Direct3D11 vs_5_0 ps_5_0, D3D11)" in js
    assert "Google Inc. (NVIDIA)" in js
    assert "Google Inc. (Intel)" in js


def test_real_macos_strings(tmp_path):
    js = _read(build_gpu_extension(1, "macos", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("macos", "desktop")), "gpu.js")
    assert "ANGLE Metal Renderer: Apple M1, Unspecified Version" in js
    assert "Google Inc. (Apple)" in js


# --- linux arm (PS-36) -------------------------------------------------------
#
# Reference values and their per-value provenance live in
# tests/fixtures/linux-webgl-reference.md. Read that file before changing any
# string here; do NOT re-derive them from this test or from gpu_ext.py.
#
# The eight page-visible strings, kept as whole tuples. The ASIC codename and
# the compiler term are coherent with the marketing name (navi21 belongs to
# RX 6800 and to nothing else), so these are compared as complete strings and
# must never be recombined term-by-term across rows.
_LINUX_RENDERERS = {
    "Google Inc. (AMD)": [
        "ANGLE (AMD, AMD Radeon RX 6800 (radeonsi navi21 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 7900 XTX (radeonsi navi31 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 7600 (radeonsi navi33 ACO), OpenGL 4.6)",
        "ANGLE (AMD, AMD Radeon RX 6600 (radeonsi navi23 LLVM 18.1.6), OpenGL 4.6)",
    ],
    "Google Inc. (Intel)": [
        "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (ADL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) HD Graphics 530 (SKL GT2), OpenGL 4.6)",
        "ANGLE (Intel, Mesa Intel(R) UHD Graphics 770 (ADL-S GT1), OpenGL 4.6)",
    ],
}


def test_real_linux_strings(tmp_path):
    # AC 7 — mirrors test_real_windows_strings / test_real_macos_strings.
    js = _read(build_gpu_extension(1, "linux", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("linux", "desktop")), "gpu.js")
    for vendor, renderers in _LINUX_RENDERERS.items():
        assert vendor in js
        for r in renderers:
            assert r in js, f"missing harvested linux renderer: {r}"


def test_linux_resolves_to_its_own_os_marker_not_windows(tmp_path):
    # os_norm's else-branch used to swallow linux into "windows", which is what
    # made every downstream selection serve a Windows D3D11 GPU.
    js = _read(build_gpu_extension(1, "linux", str(tmp_path / "l"), 0, engine_platform=engine_platform_for("linux", "desktop")), "gpu.js")
    assert 'var OS = "linux";' in js
    assert 'var OS = "windows";' not in js


def test_linux_page_reads_a_mesa_gpu_and_never_direct3d(tmp_path):
    # AC 1. THE point of this arm. Assert on what the page RECEIVES, not on the
    # file: every pool is a literal in every emitted file (the OS marker selects
    # at runtime), so a substring check against gpu.js would prove nothing here
    # — gpu.js legitimately still contains WIN_GPUS' D3D11 strings.
    p = _probe(tmp_path, 1, "linux")
    served = (p["unmaskedVendor"] or "") + " " + (p["unmaskedRenderer"] or "")

    # Direct3D is a Windows-only API — on Linux it is impossible, not merely
    # implausible. This is the assertion the whole ticket exists for.
    for impossible in ("Direct3D", "D3D11", "vs_5_0", "ps_5_0"):
        assert impossible not in served, f"linux profile served a Windows-only value: {served}"
    # Nor may it serve any other platform's pool.
    for impossible in ("Metal Renderer", "Apple GPU", "Adreno", "Mali", "SwiftShader"):
        assert impossible not in served, f"linux profile served a foreign value: {served}"

    # It must serve one of the harvested tuples, whole.
    assert p["unmaskedVendor"] in _LINUX_RENDERERS, p["unmaskedVendor"]
    assert p["unmaskedRenderer"] in _LINUX_RENDERERS[p["unmaskedVendor"]], (
        "served a renderer that is not a harvested tuple, or one recombined "
        f"across rows: {p['unmaskedRenderer']}"
    )


def test_linux_and_windows_no_longer_emit_the_same_renderer(tmp_path):
    # AC 2 — premise inversion. BEFORE the linux arm these two were
    # byte-identical (linux fell through os_norm's else to "windows"); if this
    # ever passes trivially again, the arm has regressed.
    #
    # PS-161 CHANGED THE CONTROL, NOT THE CLAIM. Windows is now an
    # engine-authored-identity arm, so this extension deliberately does NOT
    # write the identity pair there and the probe's stub getParameter answers
    # its sentinel. Asserting "Direct3D11 in win" would now assert the bug this
    # ticket fixes. The control it provided — "the probe observes a real
    # difference rather than a no-op" — is preserved by pinning what windows
    # must do INSTEAD (fall through), which is an equally strong statement and
    # fails just as loudly if the arm silently reverts to spoofing.
    for seed in (1, 42, 0xABCDEF, 7):
        lin = _probe(tmp_path / f"l{seed}", seed, "linux")
        win = _probe(tmp_path / f"w{seed}", seed, "windows")
        assert lin["unmaskedRenderer"] != win["unmaskedRenderer"], (
            f"seed {seed}: linux still emits the windows renderer"
        )
        assert win["unmaskedRenderer"] == "HOST_VALUE_NOT_SPOOFED", (
            "windows must FALL THROUGH for the identity pair so the engine's "
            f"own value reaches the page; got {win['unmaskedRenderer']!r}"
        )
        # And the linux side must still be genuinely ours — the half of this
        # comparison that proves the difference is not two fall-throughs.
        assert "OpenGL 4.6" in lin["unmaskedRenderer"], lin["unmaskedRenderer"]


def test_linux_emits_no_kernel_drm_or_mesa_build_version(tmp_path):
    # AC 5. ANGLE strips these before a page sees them, so emitting one would be
    # a value that cannot occur in a real browser:
    #   - SanitizeRendererString (DisplayGL.cpp:36-52) truncates at ", DRM "
    #     under feature sanitizeAMDGPURendererString / IsLinux() && hasAMD
    #     (renderergl_utils.cpp:2552) — crbug.com/1181193.
    #   - Context.cpp:3697 passes getBackendVersionString(!isWebGL()), so
    #     SanitizeVersionString keeps only the first version token.
    import re

    p = _probe(tmp_path, 1, "linux")
    served = p["unmaskedRenderer"]
    assert ", DRM " not in served and " (DRM " not in served
    # a kernel release (e.g. 6.17.7-ba28.fc43.x86_64 / 6.12.74-1-lts)
    assert not re.search(r"\d+\.\d+\.\d+-", served), f"kernel release leaked: {served}"
    # a Mesa build version (e.g. "Mesa 22.1.0-develgit-"); the bare vendor
    # prefix "Mesa Intel(R) ..." is correct and must NOT trip this.
    assert not re.search(r"Mesa \d", served), f"Mesa build version leaked: {served}"
    # The version element is the truncated first token, never the full string.
    assert "OpenGL 4.6" in served
    assert "Core Profile" not in served


def test_linux_limits_and_extensions_are_the_desktop_defaults(tmp_path):
    # AC 3. The expected outcome is explicitly "verify, no change": linux is a
    # desktop, so COMMON_DESKTOP (selected by the `var COMMON =` gate in
    # gpu_ext.py) and DESKTOP_EXTS (the `var STABLE_EXTS =` gate) are already
    # correct for it. Neither gate routes linux the way `var POOL =` does: both
    # fall through to their desktop default. Pinned as a test so a future edit
    # that gratuitously forks a "Linux variant" of the desktop limits — i.e.
    # invents unsourced values — fails loudly.
    lin = _probe(tmp_path / "l", 42, "linux")
    win = _probe(tmp_path / "w", 42, "windows")
    for k in ("maxTextureSize", "maxCubeMapTextureSize", "maxRenderbufferSize",
              "maxViewportDims", "maxVertexAttribs", "maxVaryingVectors",
              "maxCombinedTextureImageUnits"):
        assert lin[k] == win[k], f"linux forked the desktop limit {k}"
    assert lin["maxViewportDims"] == [32767, 32767], (
        "a desktop viewport is required for coherence with a desktop renderer"
    )
    assert lin["exts_gl1"] == win["exts_gl1"]
    assert lin["exts_gl2"] == win["exts_gl2"]
    # The BC/DXT family stays advertised: rgtc/bptc are DONE for all relevant
    # Mesa drivers and S3TC has been unconditional since Mesa 17.3, so both
    # radeonsi and iris expose them (see the fixture's "Limits and extension
    # sets" section).
    for ext in ("WEBGL_compressed_texture_s3tc", "WEBGL_compressed_texture_s3tc_srgb",
                "EXT_texture_compression_bptc", "EXT_texture_compression_rgtc"):
        assert ext in lin["exts_gl1"], f"{ext} vanished from the linux set"


def test_linux_vendor_agrees_with_the_renderer_it_is_paired_with(tmp_path):
    # UNMASKED_VENDOR is EGL's "Google Inc. (<GL_VENDOR>)" (Display.cpp:2478-2486)
    # and GL_VENDOR is the driver literal — "AMD" (si_get.c:13-16) or "Intel"
    # (iris_screen.c:80-84). A radeonsi renderer under an (Intel) vendor is a
    # cross-field contradiction a detector reads in one call.
    for seed in (1, 42, 7, 0xABCDEF, 99, 12345):
        p = _probe(tmp_path / f"s{seed}", seed, "linux")
        vendor, renderer = p["unmaskedVendor"], p["unmaskedRenderer"]
        if vendor == "Google Inc. (AMD)":
            assert "radeonsi" in renderer and "ANGLE (AMD," in renderer
        elif vendor == "Google Inc. (Intel)":
            assert "Mesa Intel(R)" in renderer and "ANGLE (Intel," in renderer
        else:
            raise AssertionError(f"unexpected linux vendor {vendor!r}")


def test_deterministic_linux_build(tmp_path):
    a = _read(build_gpu_extension(42, "linux", str(tmp_path / "a"), 0, engine_platform=engine_platform_for("linux", "desktop")), "gpu.js")
    b = _read(build_gpu_extension(42, "linux", str(tmp_path / "b"), 0, engine_platform=engine_platform_for("linux", "desktop")), "gpu.js")
    assert a == b


def test_os_gate_present(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    # 4-way pool gate: macOS→Apple/Metal, android→Adreno/Mali, linux→Mesa/GL,
    # else Windows/D3D11. (iOS bypasses the pool entirely — see IOS_GPU.)
    assert '(OS === "macos") ? MAC_GPUS' in js
    assert '(OS === "android") ? ANDROID_GPUS' in js
    assert '(OS === "linux") ? LINUX_GPUS' in js
    assert "WIN_GPUS" in js


def test_android_profile_gets_mobile_gpu(tmp_path):
    # #5 (audit4): an Android profile (phone UA + touch) returning a D3D11 desktop
    # GPU is impossible — it must get the Adreno/Mali ANGLE-over-GLES pool.
    js = _read(build_gpu_extension(1, "android", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("android", "desktop")), "gpu.js")
    assert 'var OS = "android"' in js
    assert "Adreno" in js and "Mali" in js
    assert "OpenGL ES 3.2" in js


def test_version_strings(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert "WebGL 1.0 (OpenGL ES 2.0 Chromium)" in js
    assert "WebGL 2.0 (OpenGL ES 3.0 Chromium)" in js


def test_required_limits(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    for p in ["3379:", "3386:", "34024:", "34921:", "36348:", "35661:", "33902:"]:
        assert p in js, f"missing param {p}"


def test_deterministic_build(tmp_path):
    a = _read(build_gpu_extension(42, "windows", str(tmp_path / "a"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    b = _read(build_gpu_extension(42, "windows", str(tmp_path / "b"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert a == b


def test_seed_varies_gpu_choice():
    # mirror the in-page h32 to confirm seeds spread across the 5-GPU windows pool
    def h32(seed, x):
        h = (seed ^ (x & 0xFFFFFFFF)) & 0xFFFFFFFF
        h ^= (h >> 16); h &= 0xFFFFFFFF
        h = (h * 0x85ebca6b) & 0xFFFFFFFF
        h ^= (h >> 13); h &= 0xFFFFFFFF
        h = (h * 0xc2b2ae35) & 0xFFFFFFFF
        h ^= (h >> 16); h &= 0xFFFFFFFF
        return h
    idxs = {h32(s & 0xFFFFFFFF, 0x67900) % 5 for s in
            (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 99, 1000, 0xDEADBEEF, 0xCAFE)}
    assert len(idxs) > 1


def test_carries_gpu_spoof_into_iframes(tmp_path):
    # creepjs reads WebGL from a fresh about:blank/srcdoc iframe; on real hardware
    # the pristine iframe leaks the real GPU (a VM's SwiftShader hides it). The
    # spoof must be carried into same-realm child frames.
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")), "gpu.js")
    assert "contentWindow" in js and "HTMLIFrameElement" in js
    # the leaf is routed through the shared realm bootstrap, which chains the
    # iframe accessors and re-runs the installer in the child (recursively).
    # That the leaf REALLY reaches a child frame — and a depth-3 worker — is
    # proven by execution in tests/test_worker_wrap.py; this pins the wiring.
    assert "__pnaInstall(SELF, applyGpuPatch)" in js
    assert "__pnaInstall(w, LEAF)" in js


def test_shader_precision_and_extensions_spoofed(tmp_path):
    # #5 (audit3): getShaderPrecisionFormat otherwise returns the HOST GPU's real
    # precision (native D3D11/Metal on Win/mac), contradicting the spoofed
    # renderer — a renderer↔precision mismatch creepjs cross-checks. And
    # getSupportedExtensions reflects the host GPU's real set. Both must be
    # normalized to the canonical ANGLE values.
    import pathlib

    from src.services.browser.gpu_ext import build_gpu_extension

    js = pathlib.Path(
        build_gpu_extension(1, "windows", str(tmp_path / "g"), 0, engine_platform=engine_platform_for("windows", "desktop")) + "/gpu.js"
    ).read_text()
    assert "getShaderPrecisionFormat = nativeWrap" in js
    assert "getSupportedExtensions = nativeWrap" in js
    # canonical ANGLE-D3D11 float precision (127/127/23) present
    assert "rangeMin: 127" in js and "precision: 23" in js
    # stable extension set includes s3tc (a real-GPU marker, not SwiftShader)
    assert "WEBGL_compressed_texture_s3tc" in js


def test_android_extensions_and_limits_are_gles_coherent(tmp_path):
    # audit7 #3: an Android profile spoofs an Adreno/Mali renderer, so its
    # extension set + limits must be GLES-coherent — NOT desktop D3D11. s3tc/bptc/
    # rgtc (DXT/BC) never appear on a real Adreno/Mali; ETC/ETC1/ASTC do; and the
    # viewport is 16384 not the desktop 32767. Shipping the desktop set was a
    # renderer<->extension impossibility CreepJS/Pixelscan cross-check.
    import pathlib

    from src.services.browser.gpu_ext import build_gpu_extension

    js = pathlib.Path(
        build_gpu_extension(1, "android", str(tmp_path / "a"), 0, engine_platform=engine_platform_for("android", "desktop")) + "/gpu.js"
    ).read_text()
    # the ANDROID set is what the code selects for OS==="android"
    assert 'STABLE_EXTS = (OS === "android") ? ANDROID_EXTS' in js
    assert "WEBGL_compressed_texture_etc" in js
    assert "WEBGL_compressed_texture_astc" in js
    assert "KHR_parallel_shader_compile" in js
    # Android limit block selected + GLES viewport present
    assert "COMMON = (OS === \"android\") ? COMMON_ANDROID" in js
    assert "16384, 16384" in js  # MAX_VIEWPORT_DIMS for GLES


def test_apple_extensions_drop_s3tc(tmp_path):
    # audit7 #3: Apple/Metal has no s3tc either; the macOS set drops it.
    import pathlib

    from src.services.browser.gpu_ext import build_gpu_extension

    js = pathlib.Path(
        build_gpu_extension(1, "macos", str(tmp_path / "m"), 0, engine_platform=engine_platform_for("macos", "desktop")) + "/gpu.js"
    ).read_text()
    assert '(OS === "macos") ? APPLE_EXTS' in js
    # APPLE_EXTS must not contain s3tc (verify the array itself, not the whole
    # file — DESKTOP_EXTS still has it for windows profiles)
    apple = js.split("APPLE_EXTS = [", 1)[1].split("]", 1)[0]
    assert "s3tc" not in apple


# --------------------------------------------------------------------------
# PS-12: iOS profiles must not be served a Mac GPU.
#
# THE TRAP: every pool/limit block/extension list is emitted as a literal into
# EVERY generated gpu.js, whatever the OS — only the runtime `OS` marker selects
# among them. So `"Direct3D11" in gpu_js` is True even for an iOS profile and
# means nothing. Every assertion below is therefore on the SELECTED value:
# either by splitting the specific array out of the source, or (preferred) by
# running the emitted script in node and CALLING the patched methods.
#
# Reference values + provenance: tests/fixtures/ios-webgl-reference.md
# --------------------------------------------------------------------------

_GPU_PROBE = r"""
// Stub the two context ctors, run the emitted extension against them, then CALL
// the patched methods — what a page actually sees, not what the file contains.
function makeRealm() {
  function WebGLRenderingContext() {}
  function WebGL2RenderingContext() {}
  for (const C of [WebGLRenderingContext, WebGL2RenderingContext]) {
    C.prototype.getParameter = function () { return "HOST_VALUE_NOT_SPOOFED"; };
    C.prototype.getExtension = function () { return null; };
    C.prototype.getSupportedExtensions = function () { return ["HOST_EXT"]; };
    C.prototype.getShaderPrecisionFormat = function () { return null; };
  }
  return { WebGLRenderingContext, WebGL2RenderingContext };
}
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const G = makeRealm();
const sandbox = { self: G, window: G, ...G };
require('vm').createContext(sandbox);
require('vm').runInContext(src, sandbox);
const gl1 = new G.WebGLRenderingContext();
const gl2 = new G.WebGL2RenderingContext();
const num = (p) => { const v = gl2.getParameter(p); return ArrayBuffer.isView(v) ? Array.from(v) : v; };
console.log(JSON.stringify({
  unmaskedVendor: gl2.getParameter(0x9245),
  unmaskedRenderer: gl2.getParameter(0x9246),
  vendor: num(7936), renderer: num(7937),
  version_gl1: gl1.getParameter(7938), slversion_gl1: gl1.getParameter(35724),
  version_gl2: gl2.getParameter(7938), slversion_gl2: gl2.getParameter(35724),
  maxTextureSize: num(3379), maxCubeMapTextureSize: num(34076),
  maxRenderbufferSize: num(34024), maxViewportDims: num(3386),
  maxVertexAttribs: num(34921), maxVertexUniformVectors: num(36347),
  maxFragmentUniformVectors: num(36349), maxVaryingVectors: num(36348),
  maxVertexTextureImageUnits: num(35660), maxTextureImageUnits: num(34930),
  maxCombinedTextureImageUnits: num(35661),
  aliasedPointSizeRange: num(33901), aliasedLineWidthRange: num(33902),
  exts_gl1: gl1.getSupportedExtensions(),
  exts_gl2: gl2.getSupportedExtensions(),
  // WebGL2-only block (PS-22). Read on BOTH contexts: on WebGL2 these must be
  // the reference values, on WebGL1 they must still fall through (the pnames do
  // not exist there and a real browser answers INVALID_ENUM).
  webgl2_on_gl2: readWebgl2Params(gl2),
  webgl2_on_gl1: readWebgl2Params(gl1),
  // The dual-context three (anisotropy / draw buffers / colour attachments).
  // These must carry the reference values on BOTH contexts: WebGL1 reaches
  // them through extensions this profile advertises, so falling through there
  // leaks the host renderer.
  dual_on_gl2: readDualParams(gl2),
  dual_on_gl1: readDualParams(gl1),
  // Must BOTH fall through: MAX_SAMPLES is deliberately unspoofed, and 35378 is
  // the geometry-stage parameter WebGL2 does not expose.
  maxSamples_gl2: gl2.getParameter(36183),
  geometryStage35378_gl2: gl2.getParameter(35378),
}));
"""

# The WebGL2-only reference block, keyed by pname, with the spec name spelled
# out. Source of record: tests/fixtures/ios-webgl-reference.md (all [device]).
#
# Written out per-parameter ON PURPOSE. An earlier revision of PS-22 shipped
# four transposed enums, and neither shape is self-catching: a wrong-but-VALID
# enum still returns a value (just the wrong parameter's — e.g. SEPARATE_ATTRIBS
# answering 128 instead of 4, a sharper tell than the fall-through being fixed),
# while a wrong-and-UNUSED enum never matches and falls through silently, so the
# bug looks like success. A test that only checked "some value came back" would
# have passed that revision. These assert value-for-value, per pname.
_WEBGL2_IOS_REFERENCE = {
    32883: ("MAX_3D_TEXTURE_SIZE", 2048),
    35071: ("MAX_ARRAY_TEXTURE_LAYERS", 2048),
    34045: ("MAX_TEXTURE_LOD_BIAS", 15),
    35658: ("MAX_VERTEX_UNIFORM_COMPONENTS", 4096),
    35657: ("MAX_FRAGMENT_UNIFORM_COMPONENTS", 4096),
    35659: ("MAX_VARYING_COMPONENTS", 124),
    37154: ("MAX_VERTEX_OUTPUT_COMPONENTS", 124),
    37157: ("MAX_FRAGMENT_INPUT_COMPONENTS", 124),
    35371: ("MAX_VERTEX_UNIFORM_BLOCKS", 16),
    35373: ("MAX_FRAGMENT_UNIFORM_BLOCKS", 16),
    35374: ("MAX_COMBINED_UNIFORM_BLOCKS", 32),
    35375: ("MAX_UNIFORM_BUFFER_BINDINGS", 32),
    35376: ("MAX_UNIFORM_BLOCK_SIZE", 16384),
    35377: ("MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS", 69632),
    35379: ("MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS", 69632),
    35380: ("UNIFORM_BUFFER_OFFSET_ALIGNMENT", 16),
    35076: ("MIN_PROGRAM_TEXEL_OFFSET", -8),
    35077: ("MAX_PROGRAM_TEXEL_OFFSET", 7),
    35968: ("MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS", 4),
    35978: ("MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS", 128),
    35979: ("MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS", 4),
}

# The three parameters that are reachable on BOTH contexts, so they live in
# COMMON_IOS rather than WEBGL2_IOS and must be answered on WebGL1 as well.
#
# They are core parameters in WebGL2, but on a WebGL1 context they are ALSO
# reachable at the same numeric enums through extensions this profile already
# advertises in IOS_GL1_EXTS:
#   34047 <- EXT_texture_filter_anisotropic (MAX_TEXTURE_MAX_ANISOTROPY_EXT)
#   34852 <- WEBGL_draw_buffers            (MAX_DRAW_BUFFERS_WEBGL)
#   36063 <- WEBGL_draw_buffers            (MAX_COLOR_ATTACHMENTS_WEBGL)
#
# An earlier revision of PS-22 classified these as WebGL2-only and pinned them
# to the V2 extraMap. The result was that a WebGL1 context ADVERTISED both
# extensions and then answered the HOST renderer's real values for them — the
# same cross-vector incoherence the WebGL2 block exists to close, pointed at
# the other context. "Core WebGL2" is not the same question as "unreachable on
# WebGL1"; only the second one justifies a V2-only entry.
_DUAL_CONTEXT_IOS_REFERENCE = {
    34047: ("MAX_TEXTURE_MAX_ANISOTROPY_EXT", 16),
    34852: ("MAX_DRAW_BUFFERS", 8),
    36063: ("MAX_COLOR_ATTACHMENTS", 8),
}

_GPU_PROBE = _GPU_PROBE.replace(
    "const num = (p) =>",
    "const readWebgl2Params = (ctx) => {\n"
    "  const out = {};\n"
    "  for (const p of %s) out[p] = ctx.getParameter(p);\n"
    "  return out;\n"
    "};\n"
    "const readDualParams = (ctx) => {\n"
    "  const out = {};\n"
    "  for (const p of %s) out[p] = ctx.getParameter(p);\n"
    "  return out;\n"
    "};\n"
    "const num = (p) =>" % (
        sorted(_WEBGL2_IOS_REFERENCE),
        sorted(_DUAL_CONTEXT_IOS_REFERENCE),
    ),
)


def _probe(tmp_path, seed, os_type, device_type="desktop"):
    """Run the emitted extension in node and report what a page actually sees.

    ``device_type`` is a real parameter rather than a fixed "desktop", because
    the platform the ENGINE is told is a function of BOTH — and a probe that
    could only ask about ``os_type`` is structurally unable to observe the
    windows+mobile host leak. It is built through ``engine_platform_for``, the
    product's own single source, so the probe cannot answer a question the
    product does not ask.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    tag = f"p{seed}{os_type or 'empty'}{device_type}"
    d = pathlib.Path(build_gpu_extension(
        seed, os_type, str(tmp_path / tag), 0,
        engine_platform=engine_platform_for(os_type, device_type)))
    harness = d / "harness.js"
    harness.write_text(_GPU_PROBE, encoding="utf-8")
    out = subprocess.run(
        [node, str(harness), str(d / "gpu.js")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# The reference lists, in WebKit source order. Order is load-bearing: WebKit's
# getSupportedExtensions() is a hardcoded ordered sequence of APPEND_IF_SUPPORTED
# macros, so real devices never reorder — a fingerprinter hashing the raw array
# catches a wrong order instantly. Compared as sequences, never as sets.
_IOS_GL2_EXTS = [
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
    "WEBGL_render_shared_exponent", "WEBGL_stencil_texturing",
]
_IOS_GL1_EXTS = [
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
    "WEBGL_lose_context", "WEBGL_multi_draw", "WEBGL_polygon_mode",
]


def test_ios_resolves_to_its_own_os_marker_not_macos(tmp_path):
    # AC 1. `ios` used to normalize to "macos", which is what made every
    # downstream selection (pool, limits, extensions) serve a Mac.
    js = _read(build_gpu_extension(1, "ios", str(tmp_path / "i"), 0, engine_platform=engine_platform_for("ios", "desktop")), "gpu.js")
    assert 'var OS = "ios";' in js
    assert 'var OS = "macos";' not in js


def test_ipad_and_iphone_aliases_also_resolve_to_ios(tmp_path):
    # os_type is an unvalidated free-text str on the profile model, so the
    # obvious spellings must not silently fall through to the windows default.
    for alias in ("ios", "iOS", "iPhone", "ipad", "ipados"):
        js = _read(build_gpu_extension(1, alias, str(tmp_path / alias), 0, engine_platform=engine_platform_for(alias, "desktop")), "gpu.js")
        assert 'var OS = "ios";' in js, f"{alias} did not resolve to ios"


def test_ios_page_reads_apple_gpu_and_no_desktop_renderer(tmp_path):
    # AC 2. Assert on what the page RECEIVES, not on the file: every pool is a
    # literal in every file, so a substring check here would prove nothing.
    p = _probe(tmp_path, 1, "ios")
    assert p["unmaskedVendor"] == "Apple Inc."
    assert p["unmaskedRenderer"] == "Apple GPU"
    assert p["vendor"] == "WebKit"
    assert p["renderer"] == "WebKit WebGL"
    served = p["unmaskedVendor"] + " " + p["unmaskedRenderer"]
    for impossible in ("Metal Renderer", "Apple M1", "Apple M2", "ANGLE (", "Direct3D11"):
        assert impossible not in served


def test_ios_limits_match_the_measured_reference(tmp_path):
    # AC 3. Full value-for-value check against tests/fixtures/ios-webgl-reference.md.
    p = _probe(tmp_path, 1, "ios")
    assert p["maxTextureSize"] == 16384
    assert p["maxCubeMapTextureSize"] == 16384
    assert p["maxRenderbufferSize"] == 16384
    assert p["maxViewportDims"] == [16384, 16384]
    assert p["maxVertexAttribs"] == 16
    assert p["maxVertexUniformVectors"] == 1024
    assert p["maxFragmentUniformVectors"] == 1024
    assert p["maxVaryingVectors"] == 31
    assert p["maxVertexTextureImageUnits"] == 16
    assert p["maxTextureImageUnits"] == 16
    assert p["maxCombinedTextureImageUnits"] == 32
    assert p["aliasedPointSizeRange"] == [1, 511]
    assert p["aliasedLineWidthRange"] == [1, 1]


def test_ios_limits_differ_from_the_desktop_block_on_every_discriminator(tmp_path):
    # AC 3, sharpened: these four are exactly what gave the old fold away, so
    # pin the CONTRAST against macOS rather than only the absolute values.
    ios = _probe(tmp_path, 1, "ios")
    mac = _probe(tmp_path, 1, "macos")
    assert ios["maxViewportDims"] == [16384, 16384] and mac["maxViewportDims"] == [32767, 32767]
    assert ios["aliasedPointSizeRange"] == [1, 511] and mac["aliasedPointSizeRange"] == [1, 1024]
    assert ios["maxVertexUniformVectors"] == 1024 and mac["maxVertexUniformVectors"] == 4096
    # iOS reports 31; macOS Metal subtracts [[position]] and reports 30.
    assert ios["maxVaryingVectors"] == 31 and mac["maxVaryingVectors"] == 30


def test_ios_version_strings_drop_the_chromium_parenthetical(tmp_path):
    # AC 4. "(OpenGL ES 3.0 Chromium)" is a Chromium build artifact announcing
    # Chromium on a device where Chromium cannot exist.
    p = _probe(tmp_path, 1, "ios")
    assert p["version_gl2"] == "WebGL 2.0"
    assert p["slversion_gl2"] == "WebGL GLSL ES 3.00"
    assert p["version_gl1"] == "WebGL 1.0"
    assert p["slversion_gl1"] == "WebGL GLSL ES 1.00"
    for s in (p["version_gl1"], p["version_gl2"], p["slversion_gl1"], p["slversion_gl2"]):
        assert "Chromium" not in s and "OpenGL ES" not in s


def test_ios_returns_a_different_extension_list_per_context_in_source_order(tmp_path):
    # AC 5. Two claims, both load-bearing: the lists DIFFER between contexts
    # (real browsers never return the same list for both), and each is in exact
    # source order (compared as an ordered sequence, never as a set).
    p = _probe(tmp_path, 1, "ios")
    assert p["exts_gl1"] != p["exts_gl2"]
    assert p["exts_gl1"] == _IOS_GL1_EXTS
    assert p["exts_gl2"] == _IOS_GL2_EXTS
    assert len(p["exts_gl1"]) == 39
    assert len(p["exts_gl2"]) == 36
    # sorting the lists must NOT make them equal to themselves — i.e. the order
    # we ship is genuinely not the sorted order, so a set-compare would miss it
    assert p["exts_gl2"] != sorted(p["exts_gl2"])


def test_ios_extension_set_keeps_bc_compression_all_or_nothing(tmp_path):
    # The old comment claimed Apple has no s3tc; Apple silicon does expose BC.
    # BC1-BC7 share one boolean, so s3tc/s3tc_srgb/bptc/rgtc are all present or
    # all absent together — emitting s3tc without bptc/rgtc is an internally
    # inconsistent set and a detection signal in its own right.
    p = _probe(tmp_path, 1, "ios")
    bc = {"WEBGL_compressed_texture_s3tc", "WEBGL_compressed_texture_s3tc_srgb",
          "EXT_texture_compression_bptc", "EXT_texture_compression_rgtc"}
    for ctx in ("exts_gl1", "exts_gl2"):
        present = bc & set(p[ctx])
        assert present == bc, f"{ctx} has a partial BC set: {present}"
    # and the mobile lineage coexists with it — all five families on one device
    for fam in ("WEBGL_compressed_texture_astc", "WEBGL_compressed_texture_etc",
                "WEBGL_compressed_texture_pvrtc"):
        assert fam in p["exts_gl2"]


def test_ios_excludes_draft_extensions(tmp_path):
    # Draft extensions are guarded by `&& enableDraftExtensions` in WebKit and
    # are off in shipping Safari; confirmed absent on the reference device.
    p = _probe(tmp_path, 1, "ios")
    for ctx in ("exts_gl1", "exts_gl2"):
        for draft in ("WEBGL_draw_instanced_base_vertex_base_instance",
                      "WEBGL_multi_draw_instanced_base_vertex_base_instance"):
            assert draft not in p[ctx]


def test_two_ios_seeds_select_the_identical_vendor_renderer_pair(tmp_path):
    # AC 6 — INVERTED on purpose. The pair is a WebKit compile-time literal
    # (bug 191393): every iPhone reports it, so it carries zero entropy. Any
    # per-profile variation is impossible on real hardware and would be a
    # STRONGER tell than the defect this fixes. There is no iOS pool.
    a = _probe(tmp_path, 0xAAAA, "ios")
    b = _probe(tmp_path, 0x5555, "ios")
    assert a["unmaskedVendor"] == b["unmaskedVendor"] == "Apple Inc."
    assert a["unmaskedRenderer"] == b["unmaskedRenderer"] == "Apple GPU"
    # the seed must not reach this value at all
    assert a["exts_gl2"] == b["exts_gl2"]
    assert a["maxViewportDims"] == b["maxViewportDims"]


def _webgl2_ios_block_from_source():
    """Parse the WEBGL2_IOS pname->value map out of gpu_ext.py.

    Asserting on the parsed BLOCK rather than on raw file text matters here: the
    surrounding comments deliberately mention the excluded enums (36183, 35378)
    by number to explain why they are absent, so a substring check over the file
    would report them as spoofed when they are not.
    """
    import re

    from src.services.browser import gpu_ext

    src = pathlib.Path(gpu_ext.__file__).read_text(encoding="utf-8")
    block = re.search(r"var WEBGL2_IOS = \{(.*?)\n  \};", src, re.S)
    assert block, "WEBGL2_IOS block not found in gpu_ext.py"
    parsed = {}
    for line in block.group(1).splitlines():
        entry = re.match(r"\s*(\d+)\s*:\s*(-?\d+)\s*,?\s*$", re.sub(r"//.*", "", line))
        if entry:
            parsed[int(entry.group(1))] = int(entry.group(2))
    return parsed


def test_ios_webgl2_params_match_the_reference_value_for_value(tmp_path):
    # PS-22. THE enum-correctness assertion. Each pname is checked against the
    # value the reference assigns to THAT parameter, so a transposed constant
    # fails loudly and names itself. A test asserting only "a value came back"
    # would pass revision 1's four swapped enums — which is exactly how they
    # reached review.
    p = _probe(tmp_path, 1, "ios")
    got = p["webgl2_on_gl2"]
    wrong = {}
    for pname, (name, expected) in sorted(_WEBGL2_IOS_REFERENCE.items()):
        actual = got[str(pname)]
        if actual != expected:
            wrong[f"{name} ({pname})"] = {"expected": expected, "got": actual}
    assert not wrong, f"WebGL2 parameters disagree with the reference: {wrong}"


def test_ios_webgl2_params_are_numbers_not_strings_or_arrays(tmp_path):
    # A detector reads the return TYPE, not just the value. All 24 are scalars
    # in WebGL2 (GLint/GLint64/GLfloat) — a plain Array or a numeric string
    # where the platform gives a number is itself the tell.
    p = _probe(tmp_path, 1, "ios")
    for pname, (name, _) in sorted(_WEBGL2_IOS_REFERENCE.items()):
        actual = p["webgl2_on_gl2"][str(pname)]
        assert isinstance(actual, int) and not isinstance(actual, bool), (
            f"{name} ({pname}) came back as {type(actual).__name__}: {actual!r}"
        )


def test_ios_webgl2_params_do_not_leak_onto_a_webgl1_context(tmp_path):
    # None of these 21 is reachable on a WebGL1 context — not as a core
    # parameter and not through any extension this profile advertises — so a
    # real browser answers INVALID_ENUM for every one of them there. Answering
    # them would be a FRESH impossibility, not a fix, so the WebGL1 context must
    # still fall through. This is what pins the block to the V2 extraMap.
    #
    # The membership of _WEBGL2_IOS_REFERENCE is what makes this claim true: an
    # earlier revision included three pnames that ARE WebGL1-reachable, and this
    # assertion then DEFENDED the resulting host leak instead of catching it.
    # Before adding a pname here, check it against IOS_GL1_EXTS.
    p = _probe(tmp_path, 1, "ios")
    leaked = {
        f"{_WEBGL2_IOS_REFERENCE[int(k)][0]} ({k})": v
        for k, v in p["webgl2_on_gl1"].items()
        if v != "HOST_VALUE_NOT_SPOOFED"
    }
    assert not leaked, f"WebGL2-only parameters answered on a WebGL1 context: {leaked}"


def test_ios_dual_context_params_are_spoofed_on_webgl1_too(tmp_path):
    # The counterpart of the leak test above, and the regression guard for the
    # defect that failed review: anisotropy (34047), draw buffers (34852) and
    # colour attachments (36063) are reachable on a WebGL1 context through
    # EXT_texture_filter_anisotropic / WEBGL_draw_buffers, BOTH of which this
    # profile advertises in IOS_GL1_EXTS. So WebGL1 must answer them with the
    # reference values; falling through hands back the HOST renderer's real
    # numbers while the same profile claims the extensions are supported —
    # a value inconsistent with the value next to it, one call apart.
    #
    # Asserts per-pname against its OWN expected value, not "something came
    # back", for the same reason the WebGL2 table does.
    p = _probe(tmp_path, 1, "ios")

    # The premise: the profile really does advertise both extensions on WebGL1.
    # If this ever stops being true the parameters may legitimately move back to
    # the V2 block, so the premise is asserted rather than assumed.
    assert "EXT_texture_filter_anisotropic" in p["exts_gl1"]
    assert "WEBGL_draw_buffers" in p["exts_gl1"]

    wrong = {}
    for pname, (name, expected) in sorted(_DUAL_CONTEXT_IOS_REFERENCE.items()):
        for ctx in ("dual_on_gl1", "dual_on_gl2"):
            actual = p[ctx][str(pname)]
            if actual != expected:
                wrong[f"{name} ({pname}) on {ctx}"] = {
                    "expected": expected, "got": actual,
                }
    assert not wrong, f"dual-context parameters wrong or falling through: {wrong}"


def test_ios_dual_context_params_live_in_common_not_the_webgl2_block(tmp_path):
    # Structural counterpart of the behavioural test above. COMMON is consulted
    # on both contexts while the V2 extraMap is not, so membership is what makes
    # the WebGL1 answer possible — assert it directly, so moving one back into
    # WEBGL2_IOS breaks a named test instead of silently reopening the leak.
    block = _webgl2_ios_block_from_source()
    for pname, (name, _) in sorted(_DUAL_CONTEXT_IOS_REFERENCE.items()):
        assert pname not in block, (
            f"{name} ({pname}) is reachable on WebGL1 via an advertised "
            f"extension — it belongs in COMMON_IOS, not WEBGL2_IOS"
        )


def test_ios_webgl1_parameters_are_unchanged_by_the_webgl2_block(tmp_path):
    # The WebGL1 surface must read exactly as it did before PS-22 — the new
    # block is additive, and extraMap wins over COMMON, so a colliding key would
    # silently change a WebGL1 value. Spot-checks the three sharp discriminators
    # plus the shared-context values.
    p = _probe(tmp_path, 1, "ios")
    assert p["maxViewportDims"] == [16384, 16384]
    assert p["aliasedPointSizeRange"] == [1, 511]
    assert p["maxVertexUniformVectors"] == 1024
    assert p["maxVaryingVectors"] == 31
    assert p["maxTextureSize"] == 16384
    assert p["version_gl1"] == "WebGL 1.0"
    assert p["slversion_gl1"] == "WebGL GLSL ES 1.00"
    # The version strings must survive the V2 merge (they are merged in AFTER
    # the parameter block, so a precedence regression would show up here).
    assert p["version_gl2"] == "WebGL 2.0"
    assert p["slversion_gl2"] == "WebGL GLSL ES 3.00"


def test_ios_max_samples_is_deliberately_left_falling_through(tmp_path):
    # MAX_SAMPLES (0x8D57, 36183) is the one genuinely runtime-queried value:
    # source predicts 8, the physical device reported 4, and the conflict is
    # unexplained with no second capture. Falling through is honest; pinning an
    # unexplained constant is a guess wearing a citation. This test exists so
    # that "adding" it is a deliberate act that breaks a named assertion rather
    # than a tidy-up nobody notices.
    p = _probe(tmp_path, 1, "ios")
    assert p["maxSamples_gl2"] == "HOST_VALUE_NOT_SPOOFED"
    assert 36183 not in _webgl2_ios_block_from_source(), (
        "MAX_SAMPLES must stay unspoofed — see PS-22"
    )


def test_ios_does_not_spoof_the_geometry_stage_parameter(tmp_path):
    # 0x8A32 (35378) is MAX_COMBINED_GEOMETRY_UNIFORM_COMPONENTS, which WebGL2
    # does not expose at all. The uniform-block run is contiguous from 0x8A2B,
    # so it is easy to land on by miscount — revision 1 of PS-22 put
    # MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS (correctly 35377) there.
    p = _probe(tmp_path, 1, "ios")
    assert p["geometryStage35378_gl2"] == "HOST_VALUE_NOT_SPOOFED"
    assert 35378 not in _webgl2_ios_block_from_source()


def test_ios_webgl2_block_agrees_with_the_reference_fixture(tmp_path):
    # The fixture is the source of record, so the code and the file must not
    # drift. Parses the WebGL2 table out of the markdown and compares it to the
    # block in gpu_ext.py — and re-derives every enum from its own hex column,
    # which is the check that catches a transposition on inspection.
    import re

    in_code = _webgl2_ios_block_from_source()

    fixture = pathlib.Path(__file__).parent / "fixtures" / "ios-webgl-reference.md"
    doc = fixture.read_text(encoding="utf-8")

    def rows(section):
        """Parse the [device] rows out of one section of the fixture."""
        out = {}
        for row in re.finditer(
            r"^\|\s*`([A-Z0-9_]+)`\s*\|\s*(0x[0-9A-F]+)\s*\|\s*(\d+)\s*\|"
            r"\s*(\u2212?-?\d+)\s*\|\s*\[device\]\s*\|",
            section,
            re.M,
        ):
            name, hexval, dec, value = row.groups()
            assert int(hexval, 16) == int(dec), f"{name}: hex {hexval} != decimal {dec}"
            out[int(dec)] = int(value.replace("\u2212", "-"))
        return out

    # The WebGL2-only table stops at the dual-context subsection, which sits
    # between it and MAX_SAMPLES. Slicing to the wrong boundary would silently
    # fold those three rows back in and re-assert the very defect they were
    # split out to fix, so the boundary is asserted rather than assumed.
    assert "### Reachable on both contexts" in doc
    body = doc.split("## Numeric limits (WebGL2-only")[1]
    webgl2_table = body.split("### Reachable on both contexts")[0]
    dual_table = body.split("### Reachable on both contexts")[1].split("### `MAX_SAMPLES`")[0]

    in_doc = rows(webgl2_table)
    assert in_doc, "no [device] WebGL2 rows parsed out of the reference fixture"
    assert in_code == in_doc, "gpu_ext.py and the reference fixture disagree"
    # ...and both must agree with the table this test file asserts against.
    assert in_code == {p: v for p, (_, v) in _WEBGL2_IOS_REFERENCE.items()}

    # The dual-context three must ALSO be documented, and must not have leaked
    # back into the WebGL2 table.
    in_dual_doc = rows(dual_table)
    assert in_dual_doc == {p: v for p, (_, v) in _DUAL_CONTEXT_IOS_REFERENCE.items()}, (
        "the dual-context table in the fixture disagrees with the test reference"
    )
    assert not (in_doc.keys() & in_dual_doc.keys()), (
        "a dual-context pname is also listed in the WebGL2-only table"
    )


@pytest.mark.parametrize("os_type", ["windows", "macos", "android"])
def test_non_ios_platforms_select_unchanged_values(tmp_path, os_type):
    # AC 7 — RESTATED. Byte-identity of the emitted FILE is not the criterion
    # and is not achievable (adding the iOS arrays necessarily changes the bytes
    # for every platform, since all pools are emitted into every file). What
    # must not change is what each platform SELECTS.
    p = _probe(tmp_path, 42, os_type)
    assert p["version_gl1"] == "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
    assert p["version_gl2"] == "WebGL 2.0 (OpenGL ES 3.0 Chromium)"
    # non-iOS keeps ONE shared list across both contexts (unchanged behaviour;
    # splitting theirs is explicitly out of scope for this ticket)
    assert p["exts_gl1"] == p["exts_gl2"]
    assert "Apple GPU" not in (p["unmaskedRenderer"] or "")
    if os_type == "macos":
        assert "ANGLE Metal Renderer" in p["unmaskedRenderer"]
        assert p["maxViewportDims"] == [32767, 32767]
        assert p["maxVaryingVectors"] == 30
    elif os_type == "android":
        assert "ANGLE (" in p["unmaskedRenderer"] and "OpenGL ES 3.2" in p["unmaskedRenderer"]
        assert p["maxViewportDims"] == [16384, 16384]
    else:
        # PS-161: windows is an engine-authored-IDENTITY arm, so the identity
        # pair is deliberately NOT written here — asserting "Direct3D11" would
        # now assert the two-spoofer bug. The LIMITS are still ours on every
        # arm (the gate is identity-only), which is what this row now pins.
        assert p["unmaskedRenderer"] == "HOST_VALUE_NOT_SPOOFED"
        assert p["maxViewportDims"] == [32767, 32767]


def test_deterministic_ios_build(tmp_path):
    # AC 8, mirroring test_deterministic_build for the new arm.
    a = _read(build_gpu_extension(42, "ios", str(tmp_path / "a"), 0, engine_platform=engine_platform_for("ios", "desktop")), "gpu.js")
    b = _read(build_gpu_extension(42, "ios", str(tmp_path / "b"), 0, engine_platform=engine_platform_for("ios", "desktop")), "gpu.js")
    assert a == b


def test_ios_docstring_and_s3tc_comment_no_longer_state_the_old_falsehoods(tmp_path):
    # AC 10. The docstring claimed `macos/ios -> Apple/Metal`, and a comment
    # claimed "Apple/Metal also has no s3tc" — that comment argued FOR
    # reintroducing the defect, so leaving it would mislead the next reader.
    import inspect

    from src.services.browser import gpu_ext

    assert "macos/ios -> Apple/Metal" not in inspect.getdoc(gpu_ext.build_gpu_extension)
    src = pathlib.Path(gpu_ext.__file__).read_text(encoding="utf-8")
    assert "has no s3tc" not in src
    assert "Apple/Metal also has no s3tc" not in src


def test_emitted_script_is_syntactically_valid_for_every_os(tmp_path):
    # The extension is injected at document_start into every page; a syntax
    # error means the whole GPU spoof silently never installs, on every profile.
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    for os_type in ("windows", "macos", "android", "ios", "linux"):
        d = build_gpu_extension(1, os_type, str(tmp_path / f"s{os_type}"), 0, engine_platform=engine_platform_for(os_type, "desktop"))
        out = subprocess.run(
            [node, "--check", str(pathlib.Path(d) / "gpu.js")],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, f"{os_type}: {out.stderr}"


# ---------------------------------------------------------------------------
# PS-161 — the per-arm identity-authorship split.
#
# The bug: TWO independent GPU spoofers, both live, disagreeing. Ours won on
# pixelscan's read path while the engine's won on creepjs's, so ONE profile in
# ONE run reported two different graphics cards. The fix is not "spoof harder"
# — it is to make exactly ONE author exist per vector per arm, so the
# contradiction is structurally impossible rather than merely fixed once.
#
# Every test below asserts on what a PAGE RECEIVES (via _probe, which runs the
# emitted script against stub contexts and CALLS the patched methods), never on
# the text of gpu.js. Every pool and both branches are literals in every emitted
# file — the arm is selected at runtime — so a substring check against the
# source would prove nothing here.
# ---------------------------------------------------------------------------


def test_engine_authored_arm_does_not_write_the_identity_pair(tmp_path):
    # THE fix. On an engine-authored arm this extension must not answer the two
    # debug-renderer-info constants at all, so the engine's own seed-derived
    # value reaches the page unopposed. The probe's stub returns a sentinel for
    # anything that falls through, so seeing the sentinel IS seeing the
    # fall-through.
    from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

    assert ENGINE_AUTHORED_IDENTITY_ARMS, (
        "no arm is engine-authored, so this ticket's fix is not installed"
    )
    for arm in sorted(ENGINE_AUTHORED_IDENTITY_ARMS):
        p = _probe(tmp_path / f"e{arm}", 42, arm)
        assert p["unmaskedVendor"] == "HOST_VALUE_NOT_SPOOFED", (
            f"{arm} is engine-authored but this extension still wrote the "
            f"unmasked VENDOR: {p['unmaskedVendor']!r} — two authors again"
        )
        assert p["unmaskedRenderer"] == "HOST_VALUE_NOT_SPOOFED", (
            f"{arm} is engine-authored but this extension still wrote the "
            f"unmasked RENDERER: {p['unmaskedRenderer']!r} — two authors again"
        )


def test_persona_authored_arms_still_write_the_identity_pair(tmp_path):
    # The other half, and the control that stops the test above from passing
    # for an extension that simply stopped working everywhere. linux and macos
    # keep OUR identity — linux because the engine hands every seed the same
    # SwiftShader string, macos because across 30 seeds it produced only two
    # values skewed 87/13 (a 76.9% collision, worse than our own pool's 50%).
    for arm, marker in (("linux", "OpenGL 4.6"),
                        ("macos", "ANGLE Metal Renderer"),
                        ("android", "OpenGL ES 3.2")):
        p = _probe(tmp_path / f"o{arm}", 42, arm)
        assert p["unmaskedRenderer"] != "HOST_VALUE_NOT_SPOOFED", (
            f"{arm} must keep persona's own identity, but the extension fell "
            "through to the host"
        )
        assert marker in p["unmaskedRenderer"], p["unmaskedRenderer"]
        assert p["unmaskedVendor"].startswith("Google Inc. ("), p["unmaskedVendor"]


def test_ios_identity_is_untouched_by_the_split(tmp_path):
    # iOS pins ONE constant pair for every device on earth by design, and the
    # engine has no ios platform at all. It must not be swept into either arm.
    p = _probe(tmp_path / "i", 42, "ios")
    assert p["unmaskedVendor"] == "Apple Inc."
    assert p["unmaskedRenderer"] == "Apple GPU"


def test_the_split_is_identity_only_every_other_vector_stays_ours(tmp_path):
    # THE SCOPE OF (b), and the measurement that set it. Layer OFF, the engine
    # authors the identity and the limits, but NOT the extension set: that set
    # was 36 entries and BYTE-IDENTICAL across all three declared arms, i.e. the
    # HOST's own set rather than a spoof. It advertises the mobile GLES
    # compression families (ASTC/ETC/ETC1) ALONGSIDE the Direct3D BC families —
    # on a claimed D3D11 card that is the renderer<->extension impossibility
    # this module already calls a hard cross-check failure (audit7 #3).
    #
    # So "defer to the engine" must never mean "do not install this extension":
    # dropping it wholesale would hand the extension set to a GPU-less
    # SwiftShader VM and leak the host. This pins the gate as identity-ONLY.
    from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

    for arm in sorted(ENGINE_AUTHORED_IDENTITY_ARMS):
        p = _probe(tmp_path / f"s{arm}", 42, arm)

        # The extension set is still OURS — not the host's stub value.
        assert p["exts_gl1"] != ["HOST_EXT"], (
            f"{arm}: getSupportedExtensions fell through to the host — this is "
            "the host leak the identity split must not cause"
        )
        assert "WEBGL_debug_renderer_info" in p["exts_gl1"]
        # And it must NOT advertise the mobile GLES families on a desktop arm.
        for mobile_only in ("WEBGL_compressed_texture_astc",
                            "WEBGL_compressed_texture_etc",
                            "WEBGL_compressed_texture_etc1"):
            assert mobile_only not in p["exts_gl1"], (
                f"{arm}: advertised {mobile_only} on a desktop arm — the "
                "renderer<->extension impossibility (audit7 #3)"
            )

        # The limits are still ours.
        assert p["maxViewportDims"] == [32767, 32767]
        assert p["maxTextureSize"] == 16384
        # The masked VENDOR/RENDERER are still ours.
        assert p["vendor"] == "WebKit"
        assert p["renderer"] == "WebKit WebGL"
        # And the WebGL version strings.
        assert p["version_gl1"] == "WebGL 1.0 (OpenGL ES 2.0 Chromium)"


def test_a_platform_the_engine_does_not_honour_keeps_our_authorship(tmp_path):
    # FAIL-SAFE DIRECTION, asserted on the EMITTED ARTIFACT rather than on the
    # helper. The helper answering correctly in isolation is not the property
    # that matters: what reaches the page is decided by the gate baked into
    # gpu.js, so that is what this asserts.
    #
    # ⚠️ THE SUBJECT IS THE PLATFORM THE ENGINE IS TOLD, NOT `os_type`, and the
    # rename from "arm" to "platform" is the finding rather than tidying. Two
    # rounds leaked here, in two directions, because authorship was resolved
    # from something OTHER than the value on the command line:
    #
    #   * `win`  — a spelling OUR fold recognises and the engine REJECTS.
    #   * `windows` + device_type=mobile — an os_type the engine honours, on a
    #     profile where the engine is handed `linux` instead.
    #
    # Both produced the same outcome: our layer stood down for an identity the
    # engine was never asked to write, getParameter fell through, and the
    # HOST's GL strings reached the page (Invariant #0). Measured on the
    # product's own engine (fingerprint-chromium/148.0.7778.215, layer OFF,
    # seed 9001, readings/ps161-engine-vocabulary-2026-08-25/):
    #
    #   windows / WINDOWS -> Google Inc. (AMD)    / …Radeon(TM) (0x00001638)
    #   win     / Win     -> Google Inc. (Google) / SwiftShader   <- NOT honoured
    #   linux             -> SwiftShader on EVERY seed  <- engine authors nothing
    #
    # so both classes are covered here, each built through the product's own
    # single source rather than through a value this test invented.
    for unknown in ("freebsd", "chromeos", "plan9", "", "win", "Win"):
        js = _read(
            build_gpu_extension(
                1, unknown, str(tmp_path / f"u{unknown or 'empty'}"), 0,
                engine_platform=engine_platform_for(unknown, "desktop")),
            "gpu.js",
        )
        assert 'ENGINE_AUTHORS_IDENTITY = false' in js, (
            f"os_type={unknown!r}: stood our layer down on a platform the "
            "ENGINE does not honour. Authorship was resolved from our fold's "
            "vocabulary instead of from the value the engine is told."
        )

    # THE device_type AXIS, in BOTH directions on the SAME os_type. This is the
    # combination no test in this file referenced at all, and the one that
    # leaked: `windows` is genuinely engine-honoured, so nothing about os_type
    # distinguishes these two rows — only the value the engine is handed does.
    # Built through engine_platform_for so the test asks the question the
    # PRODUCT asks; a test that hard-coded "linux" here would pass even if the
    # product stopped computing it that way.
    desktop_js = _read(
        build_gpu_extension(
            1, "windows", str(tmp_path / "wd"), 0,
            engine_platform=engine_platform_for("windows", "desktop")),
        "gpu.js")
    mobile_js = _read(
        build_gpu_extension(
            1, "windows", str(tmp_path / "wm"), 0,
            engine_platform=engine_platform_for("windows", "mobile")),
        "gpu.js")
    assert 'ENGINE_AUTHORS_IDENTITY = true' in desktop_js, (
        "windows+desktop: the engine IS told `windows` and DOES author the "
        "identity pair there, so our layer must stand down — otherwise the two "
        "spoofers contradict each other, which is the defect this ticket began "
        "with."
    )
    assert 'ENGINE_AUTHORS_IDENTITY = false' in mobile_js, (
        "windows+mobile: the engine is handed `linux` (a mobile profile is "
        "backed by the nearest desktop platform), and the engine's linux arm "
        "returns SwiftShader on every seed. Deferring here means NEITHER author "
        "writes the identity pair, getParameter falls through, and the host's "
        "software rasteriser reaches the page — Invariant #0, and a HOST_LEAK "
        "row under matrix_consistency on a single row."
    )

    # And what a page actually sees, executed rather than grepped. The probe's
    # stub answers HOST_VALUE_NOT_SPOOFED whenever the patch falls through to
    # the real getParameter, so this is the host leak stated as an observation.
    # windows+mobile is probed for the same reason `win` is: it is the pair that
    # actually shipped the leak, and the artifact assertion alone would not show
    # that a page stops seeing the host's GL strings.
    for leaky_os, leaky_dt in (
        ("freebsd", "desktop"), ("win", "desktop"), ("windows", "mobile"),
    ):
        p = _probe(tmp_path, 1, leaky_os, leaky_dt)
        assert p["unmaskedVendor"] != "HOST_VALUE_NOT_SPOOFED", (
            f"os_type={leaky_os!r} device_type={leaky_dt!r} fell through to "
            "the host's UNMASKED_VENDOR"
        )
        assert p["unmaskedRenderer"] != "HOST_VALUE_NOT_SPOOFED", (
            f"os_type={leaky_os!r} device_type={leaky_dt!r} fell through to "
            "the host's UNMASKED_RENDERER"
        )

    # The recognised arms are unchanged by the fix: windows still defers, and
    # the arms measured back onto our own authorship still keep it. Without
    # this, "nothing defers ever" would also pass the assertions above.
    for ours in ("linux", "macos", "android", "ios"):
        assert 'ENGINE_AUTHORS_IDENTITY = false' in _read(
            build_gpu_extension(
                1, ours, str(tmp_path / f"k{ours}"), 0,
                engine_platform=engine_platform_for(ours, "desktop")), "gpu.js")

    # The helper's own contract, kept as a unit-level pin beneath the artifact
    # assertions rather than instead of them. Asked about the value the ENGINE
    # IS TOLD — there is deliberately no os_type-taking helper left to ask.
    from src.services.browser.gpu_ext import (
        engine_authors_identity,
        engine_authors_identity_for_engine_platform as authors,
    )

    assert engine_authors_identity("plan9") is False
    assert engine_authors_identity("") is False
    assert authors("freebsd") is False
    assert authors("") is False
    # Platforms the ENGINE honours defer; everything else keeps our spoof.
    # `win` normalises for POOL selection but the engine rejects it as a
    # --fingerprint-platform value. Case folds because the engine folds case —
    # measured, not assumed.
    assert authors("windows") is True
    assert authors("WINDOWS") is True
    assert authors("win") is False
    assert authors("darwin") is False
    # linux IS honoured by the engine, but is not an arm we defer on — the
    # engine's linux identity is constant across seeds, which Level 2 forbids.
    # Honoured and deferred-to are two different questions.
    assert authors("linux") is False


def test_the_product_hands_one_string_to_both_consumers(tmp_path, monkeypatch):
    # THE CLASS, NOT THE INSTANCE. Every previous round fixed the leak for the
    # axis that had just been found — our-fold-vs-engine (round 2), then
    # os_type-vs-engine_platform (round 3) — and each fix left the NEXT
    # unenumerated axis open, because the authorship decision was still being
    # RE-DERIVED somewhere other than where the flag is built.
    #
    # This asserts the property that makes a round 5 impossible on this vector:
    # the string emitted as --fingerprint-platform and the string authorship is
    # resolved from are ONE value, taken from ONE computation. It does not
    # enumerate axes at all — it reads both consumers off a real spawn_browser
    # call and requires them to agree, so an axis nobody has thought of yet
    # cannot separate them without failing here.
    import os as _os
    import re as _re
    from src.services.browser import process
    from src.models.profile import Profile

    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = _os.getpid()

    class _Store:
        def resolve(self, name):
            return None

        def get(self, name):
            return None

    class _Bookmarks:
        def resolve_selection(self, *a, **k):
            return []

    # Every (os_type, device_type) pair the model can hold, including the ones
    # the create door refuses: coherence.py's own docstring records that
    # import_profile does NOT reconcile Rule 3, restore_profile is "intentionally
    # EXEMPT", and legacy records predate it — so windows+mobile is storable and
    # LAUNCHABLE even though it is not creatable. A test that only covered
    # creatable pairs would not have covered the pair that leaked.
    for os_type in ("windows", "macos", "linux", "android", "ios", "win", "freebsd"):
        for device_type in ("desktop", "mobile"):
            d = tmp_path / f"{os_type or 'empty'}-{device_type}"
            monkeypatch.setattr(process, "DATA_DIR", str(d))
            monkeypatch.setattr(process, "ProxyStore", _Store)
            monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
            monkeypatch.setattr(process, "write_window_entry", lambda name: None)
            monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
            monkeypatch.setattr(process._platform, "IS_LINUX", False)
            process.spawn_browser(Profile(
                name=f"{os_type}-{device_type}",
                os_type=os_type, device_type=device_type))

            flag = next(
                a.split("=", 1)[1] for a in captured["args"]
                if a.startswith("--fingerprint-platform="))

            gpu_js = None
            for root, _dirs, files in _os.walk(d):
                if "gpu.js" in files and ".persona-gpu-ext" in root:
                    gpu_js = pathlib.Path(root) / "gpu.js"
            assert gpu_js is not None, f"no gpu extension emitted for {os_type}/{device_type}"
            gate = _re.search(
                r"var ENGINE_AUTHORS_IDENTITY = (\w+);", gpu_js.read_text()).group(1)

            # The property: the gate is exactly what the FLAG's own value
            # implies. Computed from `flag` — the string actually on the command
            # line — never from os_type, so this cannot be satisfied by a second
            # copy of the derivation agreeing with the first by luck.
            from src.services.browser.gpu_ext import (
                engine_authors_identity_for_engine_platform as authors,
            )
            expected = "true" if authors(flag) else "false"
            assert gate == expected, (
                f"os_type={os_type!r} device_type={device_type!r}: the engine is "
                f"told --fingerprint-platform={flag!r} but the GPU extension's "
                f"authorship gate says {gate!r} (expected {expected!r}). The two "
                "consumers disagree, which means either both authors write the "
                "identity pair (a contradiction) or NEITHER does (a host leak)."
            )


def test_engine_honoured_platforms_match_the_declared_machines(tmp_path):
    # ENGINE_HONOURED_PLATFORMS is RESTATED in the browser layer rather than
    # imported from verify — engine_platform sits on the launch path and must
    # not pull the whole verify tier in to answer one question. A restated list
    # is a list that can drift, so pin it to the repo's other statement of the
    # same fact.
    #
    # This is the check that would have caught the round-2 defect: authorship
    # and --fingerprint-platform are two names for ONE vocabulary, and that leak
    # happened precisely because a second, different list (RECOGNISED_OS_TYPES)
    # was consulted instead. If the engine ever gains or loses a platform,
    # DECLARED_MACHINES is where the repo says so, and this fails until the
    # browser layer is told too.
    from src.services.browser.engine_platform import ENGINE_HONOURED_PLATFORMS
    from src.services.verify.browser_tier import DECLARED_MACHINES

    assert ENGINE_HONOURED_PLATFORMS == set(DECLARED_MACHINES), (
        "the browser layer's engine vocabulary drifted from "
        "browser_tier.DECLARED_MACHINES. Authorship must be keyed on what the "
        "engine honours, so these two cannot be allowed to disagree."
    )

    # gpu_ext must not hold a SECOND copy of it. It re-exports the one in
    # engine_platform; a restatement here would be the round-2 defect's exact
    # shape (two lists, written independently, free to drift).
    from src.services.browser import gpu_ext

    assert gpu_ext.ENGINE_HONOURED_PLATFORMS is ENGINE_HONOURED_PLATFORMS, (
        "gpu_ext restated the engine vocabulary instead of re-exporting the "
        "one in engine_platform — two copies can drift, and that is the class "
        "of defect this pins."
    )

    # And the property that actually matters, stated as behaviour rather than as
    # set equality: our fold recognises spellings the engine does NOT honour,
    # and every one of them must keep OUR authorship. This is the gap the
    # `win` host leak lived in, asserted as a class rather than as one alias.
    from src.services.browser.gpu_ext import (
        RECOGNISED_OS_TYPES,
        engine_authors_identity_for_engine_platform,
    )

    alias_only = RECOGNISED_OS_TYPES - ENGINE_HONOURED_PLATFORMS
    assert alias_only, "expected our fold to recognise aliases the engine does not"
    for alias in sorted(alias_only):
        # An alias reaches the engine unchanged — engine_platform_for passes a
        # desktop os_type straight through — so this is the value the engine
        # would genuinely be told.
        assert engine_authors_identity_for_engine_platform(
            engine_platform_for(alias, "desktop")) is False, (
            f"os_type={alias!r} is recognised by OUR fold but not honoured by "
            "the engine, yet authorship deferred — neither author would write "
            "the identity pair and the host's GL strings would reach the page."
        )



def test_recognised_os_types_cover_every_spelling_the_fold_accepts(tmp_path):
    # The fail-safe above is only as good as the agreement between the two: a
    # spelling the fold maps to a real arm but RECOGNISED_OS_TYPES omits would
    # lose its deferral silently, and one recognised but unmapped would be
    # treated as measured. Derived from one table so they cannot drift — pin
    # that, rather than a hand-copied list that would restate the bug.
    from src.services.browser.gpu_ext import (
        RECOGNISED_OS_TYPES,
        _OS_NORM_TABLE,
        _os_norm,
    )

    from_table = {s for spellings, _ in _OS_NORM_TABLE for s in spellings}
    assert RECOGNISED_OS_TYPES == from_table

    # Every recognised spelling folds to a real arm, never to the default.
    for spellings, arm in _OS_NORM_TABLE:
        for spelling in spellings:
            assert _os_norm(spelling) == arm

    # And the default is still what an unrecognised value POOLS as — the fold
    # itself is unchanged, only the authorship read off it.
    assert _os_norm("plan9") == "windows"


def test_engine_authored_set_names_only_arms_the_engine_actually_has(tmp_path):
    # The engine has no android or ios platform — process.py backs those with
    # the nearest desktop platform it DOES spoof — so an engine-authored
    # identity there would be a desktop card on a phone UA, the exact
    # impossibility the ANDROID_GPUS arm exists to prevent.
    from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS

    assert "android" not in ENGINE_AUTHORED_IDENTITY_ARMS
    assert "ios" not in ENGINE_AUTHORED_IDENTITY_ARMS
    # linux is measured CONSTANT across seeds — deferring there would give every
    # linux profile one shared card, which Level 2 forbids outright.
    assert "linux" not in ENGINE_AUTHORED_IDENTITY_ARMS


def test_no_unsubstituted_placeholder_survives_into_any_emitted_script(tmp_path):
    # A placeholder that reaches the page is a JS syntax error, which means the
    # whole GPU spoof silently never installs — on every profile of that arm.
    for arm in ("windows", "macos", "android", "ios", "linux"):
        js = _read(build_gpu_extension(7, arm, str(tmp_path / f"ph{arm}"), 0, engine_platform=engine_platform_for(arm, "desktop")), "gpu.js")
        assert "__ENGINE_AUTHORS_IDENTITY__" not in js
        assert "var ENGINE_AUTHORS_IDENTITY = " in js
