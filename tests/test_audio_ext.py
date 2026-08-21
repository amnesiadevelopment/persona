import json
import pathlib

from src.services.browser.audio_ext import build_audio_extension
from tests.native_mask_probe import AUDIO_STUBS, assert_reads_native


def test_creates_files(tmp_path):
    d = build_audio_extension(12345, str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "audio.js").exists()


def test_main_world_document_start(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"


def test_seed_embedded(tmp_path):
    d = build_audio_extension(987654, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    assert "987654" in js


def test_patches_audio_readback_paths(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    # the float-buffer readers fingerprinters use
    assert "getChannelData" in js
    assert "getFloatFrequencyData" in js
    assert "getByteFrequencyData" in js


def test_different_seeds_differ(tmp_path):
    a = (pathlib.Path(build_audio_extension(111, str(tmp_path / "a"))) / "audio.js").read_text()
    b = (pathlib.Path(build_audio_extension(222, str(tmp_path / "b"))) / "audio.js").read_text()
    assert a != b


def test_native_tostring_masking(tmp_path):
    # THE INVARIANT: an audio wrapper must stringify as native under
    # Function.prototype.toString.call(fn) — the form a masking detector uses,
    # and the one an own `.toString` override is bypassed by.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the override installed and whether or not the patch honours it, and
    # would fail on a marker-free implementation that is strictly better.
    # assert_reads_native also runs the counterfactual: without native_ext's
    # patch the same probe must NOT read native.
    d = build_audio_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text()
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "audio.js"],
        AUDIO_STUBS,
        "Function.prototype.toString.call(AudioBuffer.prototype.getChannelData)",
        "getChannelData",
    )
    assert "replacement.toString = function" not in js


def test_carries_audio_noise_into_workers(tmp_path):
    # Audio fingerprinting is commonly run in a worker via OfflineAudioContext;
    # the noise must apply there too, or page/worker audio hashes disagree.
    js = (pathlib.Path(build_audio_extension(1, str(tmp_path / "ext"))) / "audio.js").read_text()
    assert "applyAudioPatch" in js
    assert "G.Worker" in js
    body = js.split("function applyAudioPatch(G)", 1)[1].split("__pnaBoot", 1)[0]
    assert "var SEED =" in body
    assert "var REL =" in body


def test_carries_audio_noise_into_iframes(tmp_path):
    js = (pathlib.Path(build_audio_extension(1, str(tmp_path / "ext"))) / "audio.js").read_text()
    assert "contentWindow" in js and "HTMLIFrameElement" in js
    # routed through the shared realm bootstrap, which chains the iframe
    # accessors and re-runs the installer in the child (recursively). The
    # behavioural proof lives in tests/test_worker_wrap.py.
    assert "__pnaInstall(SELF, applyAudioPatch)" in js
    assert "__pnaInstall(w, LEAF)" in js
