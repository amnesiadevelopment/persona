import json
import pathlib

from src.services.browser.measuretext_ext import build_measuretext_extension


def test_builds_unpacked_extension(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "measuretext.js").exists()


def test_manifest_runs_in_main_world_at_document_start(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    man = json.loads((pathlib.Path(d) / "manifest.json").read_text())
    cs = man["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_script_hooks_measuretext_and_uses_dom_width(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    assert "measureText" in js
    # repairs via an un-noised DOM measurement
    assert "getBoundingClientRect" in js
    # substitutes the repaired metrics through a Proxy over the native object
    assert "Proxy" in js
    # masks the override so it doesn't read as patched: marked with __pnaName so
    # the native_ext toString patch renders it native even under
    # Function.prototype.toString.call(fn) (an own .toString override is bypassed).
    assert "__pnaName" in js
    assert "measureText.toString = function" not in js


def test_detects_noise_regardless_of_sign(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The engine's noise factor can be positive OR negative depending on the
    # seed, so a near-zero POSITIVE width is corrupt too — detection keys on
    # magnitude, not sign, and skips empty strings (which measure zero).
    assert "Math.abs(m.width)" in js
    assert "String(text).length" in js


def test_repairs_every_numeric_metric(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The engine scales the WHOLE TextMetrics by one factor, so the repair
    # divides every numeric field (width, bounding boxes AND the baselines) by
    # that factor rather than rebuilding a subset — leaving any field near-zero
    # is both a fingerprint tell and a layout hazard for baseline-positioned
    # widgets.
    assert "typeof v === 'number'" in js
    assert "/ scale" in js


def test_only_overrides_native_numeric_properties(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The Proxy reads through to the native object and only rescales its own
    # numeric members, so a metric the real TextMetrics lacks is never
    # synthesised (its mere presence would be a new tell) and non-numeric
    # members (e.g. toJSON) pass through untouched.
    assert "var v = t[p]" in js
    assert "typeof v === 'number'" in js


def test_repair_does_no_per_call_layout(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The noise is a fixed multiplicative factor, learned ONCE and then reused,
    # so a repair must never force a synchronous layout on the hot path. An
    # earlier version measured through a resident node and called
    # getBoundingClientRect on EVERY measureText; on an app that constantly
    # dirties the DOM (Sheets) that thrashed layout, pinned the main thread and
    # left the compositor perpetually busy (stuck 'Working…', blocked popovers).
    # getBoundingClientRect must appear exactly once — in the one-shot
    # calibration — and the calibrated factor must be cached.
    assert js.count("getBoundingClientRect") == 1
    assert "factor" in js
    # the measuring node is created, measured and removed — never left resident
    assert "removeChild" in js


def test_measuring_node_is_not_resident(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The one-shot calibration node is appended, measured and immediately
    # removed, so nothing our extension injects stays in the document to inherit
    # the page's transitions or keep the CompositorAnimationObserver busy.
    assert "appendChild" in js
    assert "removeChild" in js


def test_measuretext_on_shared_recursive_registry(tmp_path):
    # #3: the same noise repair must hold in a nested iframe and a Web Worker's
    # OffscreenCanvas measureText. Route through the shared recursive registry;
    # the session-constant factor is shared via top so a DOM-less realm repairs.
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    assert "applyMtPatch" in js
    assert "__pnaBoots.push(applyMtPatch)" in js
    assert "G.Worker" in js and "HTMLIFrameElement" in js
    assert "__personaMtFactor" in js


def test_no_perpetual_animation_frame_loop(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The repair is pull-based (only runs inside measureText); it must never
    # spin a self-scheduling frame/timer loop, which would itself keep the
    # compositor busy forever.
    assert "requestAnimationFrame" not in js
    assert "setInterval" not in js
