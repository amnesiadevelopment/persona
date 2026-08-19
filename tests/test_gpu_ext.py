import json
import pathlib

import pytest

from src.services.browser.gpu_ext import build_gpu_extension


def _read(d, name):
    return (pathlib.Path(d) / name).read_text(encoding="utf-8")


def test_builds_files(tmp_path):
    d = build_gpu_extension(0xDEADBEEF, "windows", str(tmp_path / "g"))
    p = pathlib.Path(d)
    assert (p / "gpu.js").is_file()
    assert (p / "manifest.json").is_file()


def test_native_tostring_masking(tmp_path):
    # getExtension/getParameter wrappers must mark themselves with __pnaName so
    # the native_ext Function.prototype.toString patch renders them native even
    # under Function.prototype.toString.call(fn); an own `.toString` override is
    # bypassed by that .call form and must NOT be used.
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "__pnaName" in js
    assert "replacement.toString = function" not in js


def test_carries_gpu_spoof_into_workers(tmp_path):
    # A detector reads the WebGL vendor/renderer from an OffscreenCanvas inside a
    # Web Worker to catch a page-only spoof — the real GPU (a different IHV than
    # the page reports) leaks there. The spoof must be carried into workers.
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "applyGpuPatch" in js
    assert "G.Worker" in js
    assert "SharedWorker" in js
    # SEED/OS must live INSIDE applyGpuPatch so .toString() carries them into the
    # worker; a value referenced from the outer IIFE is undefined in the worker.
    body = js.split("function applyGpuPatch(G)", 1)[1].split("__pnaBoot", 1)[0]
    assert "var SEED =" in body
    assert 'var OS =' in body


def test_manifest_mv3_main_world(tmp_path):
    d = build_gpu_extension(1, "windows", str(tmp_path / "g"))
    m = json.loads(_read(d, "manifest.json"))
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_seed_and_os_baked(tmp_path):
    w = _read(build_gpu_extension(0xABCDEF, "windows", str(tmp_path / "w")), "gpu.js")
    m = _read(build_gpu_extension(0xABCDEF, "macos", str(tmp_path / "m")), "gpu.js")
    assert str(0xABCDEF) in w
    assert 'var OS = "windows";' in w
    assert 'var OS = "macos";' in m
    assert "__SEED__" not in w and "__OS__" not in w


def test_unmasked_constants_and_extension(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "0x9245" in js and "0x9246" in js
    assert "UNMASKED_VENDOR_WEBGL" in js and "UNMASKED_RENDERER_WEBGL" in js
    assert "WEBGL_debug_renderer_info" in js


def test_real_windows_strings(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "Direct3D11 vs_5_0 ps_5_0, D3D11)" in js
    assert "Google Inc. (NVIDIA)" in js
    assert "Google Inc. (Intel)" in js


def test_real_macos_strings(tmp_path):
    js = _read(build_gpu_extension(1, "macos", str(tmp_path / "g")), "gpu.js")
    assert "ANGLE Metal Renderer: Apple M1, Unspecified Version" in js
    assert "Google Inc. (Apple)" in js


def test_os_gate_present(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    # 3-way pool gate: macOS→Apple/Metal, android→Adreno/Mali, else Windows/D3D11
    assert '(OS === "macos") ? MAC_GPUS' in js
    assert '(OS === "android") ? ANDROID_GPUS' in js
    assert "WIN_GPUS" in js


def test_android_profile_gets_mobile_gpu(tmp_path):
    # #5 (audit4): an Android profile (phone UA + touch) returning a D3D11 desktop
    # GPU is impossible — it must get the Adreno/Mali ANGLE-over-GLES pool.
    js = _read(build_gpu_extension(1, "android", str(tmp_path / "g")), "gpu.js")
    assert 'var OS = "android"' in js
    assert "Adreno" in js and "Mali" in js
    assert "OpenGL ES 3.2" in js


def test_version_strings(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "WebGL 1.0 (OpenGL ES 2.0 Chromium)" in js
    assert "WebGL 2.0 (OpenGL ES 3.0 Chromium)" in js


def test_required_limits(tmp_path):
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    for p in ["3379:", "3386:", "34024:", "34921:", "36348:", "35661:", "33902:"]:
        assert p in js, f"missing param {p}"


def test_deterministic_build(tmp_path):
    a = _read(build_gpu_extension(42, "windows", str(tmp_path / "a")), "gpu.js")
    b = _read(build_gpu_extension(42, "windows", str(tmp_path / "b")), "gpu.js")
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
    js = _read(build_gpu_extension(1, "windows", str(tmp_path / "g")), "gpu.js")
    assert "contentWindow" in js and "HTMLIFrameElement" in js
    assert "__pnaBoot(w)" in js


def test_shader_precision_and_extensions_spoofed(tmp_path):
    # #5 (audit3): getShaderPrecisionFormat otherwise returns the HOST GPU's real
    # precision (native D3D11/Metal on Win/mac), contradicting the spoofed
    # renderer — a renderer↔precision mismatch creepjs cross-checks. And
    # getSupportedExtensions reflects the host GPU's real set. Both must be
    # normalized to the canonical ANGLE values.
    import pathlib

    from src.services.browser.gpu_ext import build_gpu_extension

    js = pathlib.Path(
        build_gpu_extension(1, "windows", str(tmp_path / "g")) + "/gpu.js"
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
        build_gpu_extension(1, "android", str(tmp_path / "a")) + "/gpu.js"
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
        build_gpu_extension(1, "macos", str(tmp_path / "m")) + "/gpu.js"
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
}));
"""


def _probe(tmp_path, seed, os_type):
    """Run the emitted extension in node and report what a page actually sees."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    d = pathlib.Path(build_gpu_extension(seed, os_type, str(tmp_path / f"p{seed}{os_type}")))
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
    js = _read(build_gpu_extension(1, "ios", str(tmp_path / "i")), "gpu.js")
    assert 'var OS = "ios";' in js
    assert 'var OS = "macos";' not in js


def test_ipad_and_iphone_aliases_also_resolve_to_ios(tmp_path):
    # os_type is an unvalidated free-text str on the profile model, so the
    # obvious spellings must not silently fall through to the windows default.
    for alias in ("ios", "iOS", "iPhone", "ipad", "ipados"):
        js = _read(build_gpu_extension(1, alias, str(tmp_path / alias)), "gpu.js")
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
        assert "Direct3D11" in p["unmaskedRenderer"]
        assert p["maxViewportDims"] == [32767, 32767]


def test_deterministic_ios_build(tmp_path):
    # AC 8, mirroring test_deterministic_build for the new arm.
    a = _read(build_gpu_extension(42, "ios", str(tmp_path / "a")), "gpu.js")
    b = _read(build_gpu_extension(42, "ios", str(tmp_path / "b")), "gpu.js")
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
    for os_type in ("windows", "macos", "android", "ios"):
        d = build_gpu_extension(1, os_type, str(tmp_path / f"s{os_type}"))
        out = subprocess.run(
            [node, "--check", str(pathlib.Path(d) / "gpu.js")],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, f"{os_type}: {out.stderr}"
