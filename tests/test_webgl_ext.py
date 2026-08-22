import json
import pathlib

from src.services.browser.webgl_ext import build_webgl_extension
from tests.native_mask_probe import (
    GL_OBSERVABLE_PROBE,
    GL_STUBS,
    assert_profiles_unlinkable,
    assert_reads_native,
    assert_seed_changes_observable,
)


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


def test_seed_changes_the_observable_output(tmp_path):
    # THE INVARIANT: the seed must reach the value a fingerprinter reads. This
    # used to be a substring check for the seed literal in the generated text —
    # a check that passes on a spoof which declares its seed and installs
    # nothing, i.e. on a fully dead file.
    #
    # Asserted by EXECUTION: two seeds are run in isolated realms and the pixel
    # bytes read back through readPixels must differ. The whole 512-byte buffer
    # is compared, never a sample: STRIDE is 17, so only 31 indices are touched
    # at all and two seeds agree at several of them (index 0 agrees for
    # 111/222) — a narrow probe reports a false "no divergence".
    # assert_seed_changes_observable also runs the counterfactual: the same
    # probe against a neutered spoof must observe the SAME bytes for both seeds.
    assert_seed_changes_observable(
        tmp_path,
        build_webgl_extension,
        "webgl.js",
        GL_STUBS,
        GL_OBSERVABLE_PROBE,
    )


def test_patches_both_webgl_versions(tmp_path):
    d = build_webgl_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "webgl.js").read_text()
    assert "readPixels" in js
    assert "WebGLRenderingContext" in js
    assert "WebGL2RenderingContext" in js


def test_different_seeds_are_unlinkable_to_a_page(tmp_path):
    # THE INVARIANT (Level 2 of the bar): two profiles must not be linkable on
    # the WebGL readback vector. This used to compare the two GENERATED FILES as
    # text — which certifies unlinkability for two spoofs that install nothing,
    # since two dead files carrying different seed literals are still not
    # identical.
    #
    # Asserted on what a page reads: three profiles must observe pairwise
    # different pixel buffers, one profile must observe the SAME bytes when
    # built twice (per-profile, not random — a random vector makes a profile
    # unrecognisable to itself), and with the spoof neutered all three must
    # collapse onto one observable.
    assert_profiles_unlinkable(
        tmp_path,
        build_webgl_extension,
        "webgl.js",
        GL_STUBS,
        GL_OBSERVABLE_PROBE,
    )


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
