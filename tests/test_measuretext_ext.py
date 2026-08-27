import json
import pathlib

import pytest

from src.services.browser.measuretext_ext import build_measuretext_extension
from tests.native_mask_probe import CANVAS_STUBS, assert_reads_native


def test_builds_unpacked_extension(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    p = pathlib.Path(d)
    assert (p / "manifest.json").exists()
    assert (p / "measuretext.js").exists()


def test_manifest_runs_in_main_world_at_document_start(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    man = json.loads((pathlib.Path(d) / "manifest.json").read_text(encoding="utf-8"))
    cs = man["content_scripts"][0]
    assert cs["world"] == "MAIN"
    assert cs["run_at"] == "document_start"
    assert cs["all_frames"] is True


def test_script_hooks_measuretext_and_uses_dom_width(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    assert "measureText" in js
    # repairs via an un-noised DOM measurement
    assert "getBoundingClientRect" in js
    # substitutes the repaired metrics through a Proxy over the native object
    assert "Proxy" in js
    # THE INVARIANT: the measureText override must stringify as native under
    # Function.prototype.toString.call(fn) — the form a masking detector uses,
    # and the one an own `.toString` override is bypassed by.
    #
    # Asserted by EXECUTION, not by grepping the generated text for the marker
    # the current implementation happens to use. A substring check passes whether
    # or not the override installed and whether or not the patch honours it, and
    # would fail on a marker-free implementation that is strictly better.
    # assert_reads_native also runs the counterfactual: without native_ext's
    # patch the same probe must NOT read native.
    assert_reads_native(
        tmp_path,
        [pathlib.Path(d) / "measuretext.js"],
        CANVAS_STUBS,
        "Function.prototype.toString.call(CanvasRenderingContext2D.prototype.measureText)",
        "measureText",
    )
    assert "measureText.toString = function" not in js


def test_detects_noise_regardless_of_sign(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    # The engine's noise factor can be positive OR negative depending on the
    # seed, so a near-zero POSITIVE width is corrupt too — detection keys on
    # magnitude, not sign, and skips empty strings (which measure zero).
    assert "Math.abs(m.width)" in js
    assert "String(text).length" in js


def test_repairs_every_numeric_metric(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    # The engine scales the WHOLE TextMetrics by one factor, so the repair
    # divides every numeric field (width, bounding boxes AND the baselines) by
    # that factor rather than rebuilding a subset — leaving any field near-zero
    # is both a fingerprint tell and a layout hazard for baseline-positioned
    # widgets.
    assert "typeof v === 'number'" in js
    assert "/ scale" in js


def test_only_overrides_native_numeric_properties(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    # The Proxy reads through to the native object and only rescales its own
    # numeric members, so a metric the real TextMetrics lacks is never
    # synthesised (its mere presence would be a new tell) and non-numeric
    # members (e.g. toJSON) pass through untouched.
    assert "var v = t[p]" in js
    assert "typeof v === 'number'" in js


def test_repair_does_no_per_call_layout(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
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
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    # The one-shot calibration node is appended, measured and immediately
    # removed, so nothing our extension injects stays in the document to inherit
    # the page's transitions or keep the CompositorAnimationObserver busy.
    assert "appendChild" in js
    assert "removeChild" in js


def test_measuretext_on_shared_recursive_registry(tmp_path):
    # #3: the same noise repair must hold in a fresh child frame and in a Web
    # Worker's OffscreenCanvas measureText. Route through the shared recursive
    # registry, and share the session-constant factor with realms that cannot
    # calibrate for themselves.
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    assert "applyMtPatch" in js
    assert "__pnaInstall(SELF, applyMtPatch)" in js
    assert "G.Worker" in js and "HTMLIFrameElement" in js
    # THE FACTOR-SHARING HALF IS ASSERTED BY EXECUTION, in the test below.
    #
    # This line used to read `assert "__personaMtFactor" in js`, which MANDATED
    # THE LEAK: the factor is the divisor that inverts the text-metrics spoof,
    # and that assertion required it to be present as a plain enumerable global,
    # so removing the leak turned this test red (PS-139). It was replaced rather
    # than deleted, and deliberately NOT re-pointed at whatever substring the
    # new implementation happens to use — a substring check passes on a build
    # that merely renames the channel and fails on one that reworded a comment,
    # which is sensitivity to exactly the wrong thing. See knowledge PS-11.


def test_a_child_frame_repairs_text_from_the_factor_the_top_learned(tmp_path):
    """The observable the assertion above used to stand in for.

    A document_start child frame has no documentElement, so it CANNOT calibrate
    the noise itself. It reports a repaired width only by reading the factor the
    top realm learned, across a real realm boundary — which is the property
    "shared via the registry" actually means. Driven through the real generated
    scripts in real node realms.

    The control that makes this non-vacuous (the same realm with no top, which
    must report the RAW noised width) lives beside the probe in
    tests/test_realm_value_channels.py, along with the leak gate that replaced
    the substring assertion above.
    """
    from tests.test_realm_value_channels import (
        REPAIRED_WIDTH, _build_scripts, _run,
    )

    got = _run(tmp_path / "realms", *_build_scripts(tmp_path / "build"))

    assert got["topMeasured"] == pytest.approx(REPAIRED_WIDTH), (
        "the top realm did not repair its own noised width, so there was no "
        "learned factor for the child to share"
    )
    assert got["childMeasured"] == pytest.approx(got["topMeasured"]), (
        "the child frame did not repair text to the same width as the top "
        f"(child {got['childMeasured']} vs top {got['topMeasured']}) — a scanner "
        "measuring text in the two realms reads two different machines"
    )


def test_no_perpetual_animation_frame_loop(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text(encoding="utf-8")
    # The repair is pull-based (only runs inside measureText); it must never
    # spin a self-scheduling frame/timer loop, which would itself keep the
    # compositor busy forever.
    assert "requestAnimationFrame" not in js
    assert "setInterval" not in js
