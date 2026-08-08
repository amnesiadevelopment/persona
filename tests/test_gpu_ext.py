import json
import pathlib

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
    assert 'var POOL = (OS === "macos") ? MAC_GPUS : WIN_GPUS;' in js


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
