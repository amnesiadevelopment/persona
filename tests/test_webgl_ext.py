import json
import pathlib

from src.services.browser.webgl_ext import build_webgl_extension
from tests.native_mask_probe import GL_STUBS, assert_reads_native


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
    # THE INVARIANT: a webgl wrapper must stringify as native under
    # Function.prototype.toString.call(fn) — the form a masking detector uses,
    # and the one an own `.toString` override is bypassed by.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the override installed and whether or not the patch honours it, and
    # would fail on a marker-free implementation that is strictly better.
    # assert_reads_native also runs the counterfactual: without native_ext's
    # patch the same probe must NOT read native.
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "webgl.js"],
        GL_STUBS,
        "Function.prototype.toString.call(WebGLRenderingContext.prototype.readPixels)",
        "readPixels",
    )
    assert "replacement.toString = function" not in js


def test_carries_readpixels_noise_into_workers(tmp_path):
    # Detectors read a WebGL pixel hash from an OffscreenCanvas inside a Worker;
    # the readback noise must run there too, or page/worker hashes disagree.
    js = (pathlib.Path(build_webgl_extension(1, str(tmp_path / "ext"))) / "webgl.js").read_text()
    assert "applyWebglPatch" in js
    assert "G.Worker" in js
    body = js.split("function applyWebglPatch(G)", 1)[1].split("__pnaBoot", 1)[0]
    assert "var SEED =" in body
    assert "var STRIDE =" in body


def test_only_byte_buffers_touched(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    # float/int pixel reads must be left alone (WebGL maths unaffected)
    assert "Uint8Array" in js


def test_carries_webgl_noise_into_iframes(tmp_path):
    js = (pathlib.Path(build_webgl_extension(1, str(tmp_path / "ext"))) / "webgl.js").read_text()
    assert "contentWindow" in js and "HTMLIFrameElement" in js
    # routed through the shared realm bootstrap, which chains the iframe
    # accessors and re-runs the installer in the child (recursively). The
    # behavioural proof that a leaf reaches child frames and nested workers
    # lives in tests/test_worker_wrap.py; this pins the wiring.
    assert "__pnaInstall(SELF, applyWebglPatch)" in js
    assert "__pnaInstall(w, LEAF)" in js
