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
    # masks the override so it doesn't read as patched
    assert "[native code]" in js


def test_detects_noise_regardless_of_sign(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The engine's noise factor can be positive OR negative depending on the
    # seed, so a near-zero POSITIVE width is corrupt too — detection keys on
    # magnitude, not sign, and skips empty strings (which measure zero).
    assert "Math.abs(m.width)" in js
    assert "String(text).length" in js


def test_repairs_baseline_metrics(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The engine noises the whole TextMetrics, including the baseline fields.
    # Repairing only width/boundingBox left these near-zero (a fingerprint tell
    # and a layout hazard for widgets that position off a baseline), so the
    # rebuilt metrics must cover them too.
    assert "hangingBaseline" in js
    assert "alphabeticBaseline" in js
    assert "ideographicBaseline" in js


def test_only_overrides_metrics_present_on_native_object(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # A metric absent from the real TextMetrics (e.g. emHeight* on some builds)
    # must NOT be synthesised, or its mere presence becomes a new tell. The
    # Proxy only substitutes properties the native object actually carries.
    assert "in t" in js or "t.hasOwnProperty" in js or "p in m" in js


def test_measuring_node_is_inert_to_compositor(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The measuring span persists for the page's lifetime and is rewritten on
    # every measurement. On apps that set 'transition: all' (Google Sheets) an
    # unguarded node turns each rewrite into a CSS transition, so the browser's
    # CompositorAnimationObserver never idles ("active for too long" -> a stuck
    # 'Working…' throbber). The node must therefore opt out of animation and
    # transition, and be layout/paint-contained so a rewrite can't dirty the
    # document. (Size containment is excluded — it would zero the width.)
    assert "animation:none" in js
    assert "transition:none" in js
    assert "contain:layout paint style" in js
    assert "contain:strict" not in js


def test_no_perpetual_animation_frame_loop(tmp_path):
    d = build_measuretext_extension(str(tmp_path / "mt"))
    js = (pathlib.Path(d) / "measuretext.js").read_text()
    # The repair is pull-based (only runs inside measureText); it must never
    # spin a self-scheduling frame/timer loop, which would itself keep the
    # compositor busy forever.
    assert "requestAnimationFrame" not in js
    assert "setInterval" not in js
