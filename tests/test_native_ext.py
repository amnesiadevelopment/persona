import json
import pathlib

from src.services.browser.audio_ext import build_audio_extension
from src.services.browser.native_ext import build_native_extension
from tests.native_mask_probe import (
    AUDIO_STUBS,
    assert_reads_native,
    native_form,
    stringify_in_realm,
)


def test_builds_manifest_and_js(tmp_path):
    d = build_native_extension(str(tmp_path / "n"))
    p = pathlib.Path(d)
    man = json.loads((p / "manifest.json").read_text())
    assert man["content_scripts"][0]["world"] == "MAIN"
    assert man["content_scripts"][0]["run_at"] == "document_start"
    js = (p / "native.js").read_text()
    # the generated script patches Function.prototype.toString to render the
    # native form; that it actually WORKS is asserted by execution below, in
    # test_marked_wrapper_reads_native_under_call
    assert "Function.prototype.toString" in js
    assert "[native code]" in js


def test_wrapper_reads_native_under_call_whatever_the_load_order(tmp_path):
    # THE INVARIANT this extension exists for: a wrapper persona installs must
    # stringify as native under Function.prototype.toString.call(fn) — the form a
    # masking detector uses, and the one an own `.toString` override is bypassed
    # by.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the patch installed and whether or not it honours the marker, and
    # would fail on a marker-free implementation that is strictly better.
    #
    # The wrapper is one a REAL extension installs (audio_ext's getChannelData),
    # never one this test marks by hand. Hand-rolling the marker as a string
    # literal here would make this the one site in the suite that still hardcodes
    # the *mechanism*: it would go red for a mechanism RENAME rather than for a
    # masking regression, and the marker-free implementation this slice exists to
    # unblock would still turn the build red. The marker protocol stays private to
    # src/ — the tests only ever assert the observable a detector reads.
    #
    # The probe deliberately stringifies a WRAPPER, never
    # Function.prototype.toString itself: the engine's own toString is genuinely
    # native, so probing it would read native with the patch absent too and the
    # counterfactual below could never go red. assert_reads_native runs that
    # counterfactual — without the patch installed the same probe must NOT read
    # native.
    #
    # What makes this native_ext's OWN test rather than a copy of
    # test_audio_ext's: native.js is loaded AFTER the extension whose wrapper is
    # probed (native_first=False). That pins this module's docstring claim that
    # "load order doesn't matter because Function.prototype is one object across
    # every content script in the realm" — a native_ext property no per-extension
    # test covers, since each of those loads native.js first.
    d = build_audio_extension(1, str(tmp_path / "audio"))
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "audio.js"],
        AUDIO_STUBS,
        "Function.prototype.toString.call(AudioBuffer.prototype.getChannelData)",
        "getChannelData",
        native_first=False,
    )


def test_a_second_patch_in_the_same_realm_does_not_double_wrap(tmp_path):
    # THE PROPERTY the retired `__pnaToStringPatched` flag existed for. Two
    # persona content scripts (native_ext, locale_ext) both patch
    # Function.prototype.toString in one MAIN world; they used to coordinate
    # through a shared enumerable global so at most ONE of them ever wrapped a
    # realm. PS-68 replaced that with CHAINING — each wraps whatever it finds and
    # delegates down — so the guard is gone and this property has to be asserted
    # directly instead of assumed from the flag's presence.
    #
    # This test used to be `assert "__pnaToStringPatched" in js`. That PINNED THE
    # MECHANISM: it went red for a rename, stayed green for a build where the flag
    # was set but the patch never installed, and would have blocked exactly the
    # marker-free implementation that is strictly better. The sibling test at :23
    # already documents in-file why a substring check on the marker is the wrong
    # shape; this is that argument applied to the guard.
    #
    # So: load native_ext TWICE into one realm and read the observable a detector
    # reads. A double-wrap renders `function function getChannelData() { [native
    # code] }() { [native code] }` or leaks the wrapper's own source — either way
    # it is visible here and invisible to any string check.
    #
    # Both load orders across the two REAL extensions are covered separately, in
    # tests/test_tostring_chain.py (PS-68 AC4), along with every worker/iframe
    # depth. This keeps native_ext's own idempotency pinned in native_ext's own
    # suite.
    d = build_audio_extension(1, str(tmp_path / "audio"))
    native = pathlib.Path(build_native_extension(str(tmp_path / "n"))) / "native.js"
    doubled = stringify_in_realm(
        tmp_path,
        [native, pathlib.Path(d) / "audio.js", native],
        AUDIO_STUBS,
        "Function.prototype.toString.call(AudioBuffer.prototype.getChannelData)",
        install_native=False,
    )
    assert doubled == native_form("getChannelData"), (
        "a second injection into the same realm double-wrapped the cloak: a "
        f"marked wrapper no longer renders the native form exactly once — {doubled!r}"
    )
