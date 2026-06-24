import json
import pathlib

from src.services.browser.webgl_ext import build_webgl_extension


def test_creates_files(tmp_path):
    d = build_webgl_extension(12345, str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "webgl.js").exists()


def test_main_world_document_start(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"


def test_seed_embedded(tmp_path):
    d = build_webgl_extension(987654, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    assert "987654" in js


def test_patches_both_webgl_versions(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    assert "readPixels" in js
    assert "WebGLRenderingContext" in js
    assert "WebGL2RenderingContext" in js


def test_different_seeds_differ(tmp_path):
    a = (pathlib.Path(build_webgl_extension(111, str(tmp_path / "a"))) / "webgl.js").read_text()
    b = (pathlib.Path(build_webgl_extension(222, str(tmp_path / "b"))) / "webgl.js").read_text()
    assert a != b


def test_native_tostring_masking(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    assert "toString" in js


def test_only_byte_buffers_touched(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    # float/int pixel reads must be left alone (WebGL maths unaffected)
    assert "Uint8Array" in js
