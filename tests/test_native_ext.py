import json
import pathlib

from src.services.browser.native_ext import build_native_extension
from tests.native_mask_probe import assert_reads_native


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


def test_marked_wrapper_reads_native_under_call(tmp_path):
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
    # The probe deliberately stringifies a WRAPPER, never
    # Function.prototype.toString itself: the engine's own toString is genuinely
    # native, so probing it would read native with the patch absent too and the
    # counterfactual below could never go red. assert_reads_native runs that
    # counterfactual — without the patch installed the same probe must NOT read
    # native.
    assert_reads_native(
        tmp_path,
        [],
        "",
        '(function () {'
        '  function max() { return 1; }'
        '  Object.defineProperty(max, "__pnaName", { value: "max" });'
        '  return Function.prototype.toString.call(max);'
        '})()',
        "max",
    )


def test_idempotent_guard(tmp_path):
    # a second injection into the same realm must not re-patch (double-wrap would
    # break the native rendering)
    js = (pathlib.Path(build_native_extension(str(tmp_path / "n"))) / "native.js").read_text()
    assert "__pnaToStringPatched" in js
