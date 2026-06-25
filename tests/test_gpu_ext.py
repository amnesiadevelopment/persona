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
