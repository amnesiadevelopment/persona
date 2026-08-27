import json
import pathlib

from src.services.browser.audio_ext import build_audio_extension
from tests.native_mask_probe import (
    AUDIO_OBSERVABLE_PROBE,
    AUDIO_OBSERVABLE_STUBS,
    AUDIO_STUBS,
    assert_profiles_unlinkable,
    assert_reads_native,
    assert_seed_changes_observable,
)


def test_creates_files(tmp_path):
    d = build_audio_extension(12345, str(tmp_path / "ext"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "audio.js").exists()


def test_main_world_document_start(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    m = json.loads((pathlib.Path(d) / "manifest.json").read_text(encoding="utf-8"))
    cs = m["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"


def test_seed_changes_the_observable_output(tmp_path):
    # THE INVARIANT: the seed must reach the value a fingerprinter reads. This
    # used to be a substring check for the seed literal in the generated text —
    # a check that passes on a spoof which declares its seed and installs
    # nothing, i.e. on a fully dead file.
    #
    # Asserted by EXECUTION: two seeds are run in isolated realms and the float
    # samples read back off getChannelData must differ. assert_seed_changes_
    # observable also runs the counterfactual — the same probe against a neutered
    # spoof must observe the SAME output for both seeds.
    assert_seed_changes_observable(
        tmp_path,
        build_audio_extension,
        "audio.js",
        AUDIO_OBSERVABLE_STUBS,
        AUDIO_OBSERVABLE_PROBE,
    )


def test_patches_audio_readback_paths(tmp_path):
    d = build_audio_extension(1, str(tmp_path / "ext"))
    js = (pathlib.Path(d) / "audio.js").read_text(encoding="utf-8")
    # the float-buffer readers fingerprinters use
    assert "getChannelData" in js
    assert "getFloatFrequencyData" in js
    assert "getByteFrequencyData" in js


def test_different_seeds_are_unlinkable_to_a_page(tmp_path):
    # THE INVARIANT (Level 2 of the bar): two profiles must not be linkable on
    # the audio vector. This used to compare the two GENERATED FILES as text —
    # which certifies unlinkability for two spoofs that install nothing, since
    # two dead files carrying different seed literals are still not identical.
    #
    # Asserted on what a page reads: three profiles must observe pairwise
    # different float samples, one profile must observe the SAME samples when
    # built twice (per-profile, not random — a random vector makes a profile
    # unrecognisable to itself), and with the spoof neutered all three must
    # collapse onto one observable.
    assert_profiles_unlinkable(
        tmp_path,
        build_audio_extension,
        "audio.js",
        AUDIO_OBSERVABLE_STUBS,
        AUDIO_OBSERVABLE_PROBE,
    )


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
    js = (pathlib.Path(d) / "audio.js").read_text(encoding="utf-8")
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
    js = (pathlib.Path(build_audio_extension(1, str(tmp_path / "ext"))) / "audio.js").read_text(encoding="utf-8")
    assert "applyAudioPatch" in js
    assert "G.Worker" in js
    body = js.split("function applyAudioPatch(G)", 1)[1].split("__pnaBoot", 1)[0]
    assert "var SEED =" in body
    assert "var REL =" in body


def test_carries_audio_noise_into_iframes(tmp_path):
    js = (pathlib.Path(build_audio_extension(1, str(tmp_path / "ext"))) / "audio.js").read_text(encoding="utf-8")
    assert "contentWindow" in js and "HTMLIFrameElement" in js
    # routed through the shared realm bootstrap, which chains the iframe
    # accessors and re-runs the installer in the child (recursively). The
    # behavioural proof lives in tests/test_worker_wrap.py.
    assert "__pnaInstall(SELF, applyAudioPatch)" in js
    assert "__pnaInstall(w, LEAF)" in js
