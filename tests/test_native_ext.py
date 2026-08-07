import json
import pathlib

from src.services.browser.native_ext import build_native_extension


def test_builds_manifest_and_js(tmp_path):
    d = build_native_extension(str(tmp_path / "n"))
    p = pathlib.Path(d)
    man = json.loads((p / "manifest.json").read_text())
    assert man["content_scripts"][0]["world"] == "MAIN"
    assert man["content_scripts"][0]["run_at"] == "document_start"
    js = (p / "native.js").read_text()
    # patches Function.prototype.toString and honours the __pnaName marker
    assert "Function.prototype.toString" in js
    assert "__pnaName" in js
    assert "[native code]" in js


def test_idempotent_guard(tmp_path):
    # a second injection into the same realm must not re-patch (double-wrap would
    # break the native rendering)
    js = (pathlib.Path(build_native_extension(str(tmp_path / "n"))) / "native.js").read_text()
    assert "__pnaToStringPatched" in js
