"""PS-174: `canvas.readback` on the LOOPBACK PROBE PAGE — the third must-differ
vector, and the only one persona does not spoof itself.

WHAT WAS MISSING. `must_differ_probes()` returns three vectors; the loopback
page read two of them. `webgl.readback` -> `webgl_pixel_hash` and
`audio.digest` -> `audio_digest` were both there; `canvas.readback` mapped to
nothing, and `getImageData` appeared nowhere in `local_probe.py`.

WHY THAT ABSENCE COSTS SOMETHING SPECIFIC, rather than being one fewer number.
The two instruments answer different questions, and `local_probe`'s own
docstring states the split: *"an assertion that the layer was installed is not
evidence that it reached the page."* `diff.compare_profiles` can report canvas
COLLIDING — and with no loopback vector, nothing could answer the follow-up the
differential exists for: **did the reading fail to move because the layer
failed, or because the probe never reached the page?** That is the PS-97 shape
("a fix looked like it failed because the fixed code never ran"), and canvas is
where it is most live, because canvas is the one must-differ vector persona
DELEGATES — to fingerprint-chromium's C++ patch — rather than spoofing itself.

THE ONE TEST WITH TWO ARMS, AND WHY IT IS NOT TWO TESTS
-------------------------------------------------------
The acceptance criterion is a single claim with a positive and a negative half:
`differential --axis seed` reports canvas under `moved` on chromium and under
`unchanged` on firefox.

The firefox half asserts a NEGATIVE, and `unchanged` is *also exactly what a
broken probe returns*. A firefox-only test here would be a check that could not
fail — the failure class the project's standing directive names, arriving inside
the subsystem built to refuse it. So the chromium `moved` arm is the POSITIVE
CONTROL that converts the firefox `unchanged` arm from an absence into evidence,
and the two live in ONE test that runs chromium FIRST. If chromium is
unavailable the whole test SKIPS as inconclusive; the firefox arm can never
report a lone green.

FIREFOX READING `unchanged` IS A CORRECT PASS, NOT A DEFECT TO FIX HERE.
The collision is real and understood: `--fingerprint=` is chromium-only and the
firefox arm returns at `process.py:353` well before it. The control that makes
this a statement about CANVAS rather than about the harness is that in the very
same snapshots `audio.digest` DOES move per seed. A canvas spoof is PS-2's,
assigned there explicitly by PS-135 — this ticket adds a READING and changes no
spoof and no masking code.

THE HONEST BOUND ON WHAT RUNS WHERE
-----------------------------------
The live two-arm test needs an engine. On a container without one it skips, and
says which engine was missing. The rest of this file is deliberately made of
checks that CAN fail on a bare checkout, so the wiring is not left resting
entirely on a test that does not run here:

* the gap itself is closed and stays closed — every must-differ probe has a
  loopback vector (this is the assertion that was RED before this change);
* the page evaluates the INVENTORY'S OWN expression rather than a copy, so the
  two surfaces cannot drift into reading canvas by two different draws;
* the reduction is executed under node and checked against an independent
  implementation of FNV-1a — not grepped for the string "getImageData";
* an unreadable canvas is reported `unavailable:` and is therefore DROPPED from
  the comparison rather than compared as a value;
* the COMMITTED LIVE READINGS (`readings/ps135-2026-08-24/`, the same artifacts
  that classified the probe) are replayed through the real
  `build_differential_record` and must produce `moved` on chromium and
  `unmoved` on firefox — the offline proxy for the live test's own claim.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from src.services.verify import local_probe, probes
from src.services.verify.layer_differential import (
    AXIS_SEED,
    Arm,
    build_differential_record,
)
from src.services.verify.local_probe import ProbeReading
from src.services.verify.masking_layer import LayerReport

CANVAS_PROBE_ID = "canvas.readback"
CANVAS_VECTOR = local_probe.CANVAS_PIXEL_HASH

# The seed pair the differential's own defaults use (layer_differential.py:85-86)
# and the pair the committed chromium readings were taken at.
SEED = 4242
CONTROL_SEED = 1337

READINGS = pathlib.Path(__file__).resolve().parents[1] / "readings" / "ps135-2026-08-24"


# --- the gap this ticket closed ---------------------------------------------


def test_every_must_differ_vector_is_readable_on_the_loopback_page():
    """THE ASSERTION THAT WAS RED BEFORE THIS CHANGE.

    `must_differ_probes()` returns the vectors two distinct profiles must not
    agree on. Each needs a loopback reading, because the page is the only
    instrument that can distinguish a dead spoof from a dead probe — and
    `canvas.readback` had none.

    Written as a mapping over the INVENTORY rather than as
    `assert "canvas_pixel_hash" in PROBE_VECTORS`, so a fourth must-differ probe
    added tomorrow is policed the day it lands instead of silently inheriting
    the same gap.
    """
    # probe id -> the loopback page's name for the same vector.
    expected = {
        "webgl.readback": local_probe.WEBGL_PIXEL_HASH,
        "audio.digest": local_probe.AUDIO_DIGEST,
        CANVAS_PROBE_ID: local_probe.CANVAS_PIXEL_HASH,
    }
    must_differ = [p.id for p in probes.must_differ_probes()]

    unmapped = sorted(set(must_differ) - set(expected))
    assert not unmapped, (
        f"must-differ probe(s) {unmapped} have no loopback vector mapped in this "
        "test. A new must-differ vector needs a reading on the page too: without "
        "one, a COLLIDING report on it cannot be told apart from a probe that "
        "never reached the page."
    )
    for probe_id in must_differ:
        assert expected[probe_id] in local_probe.PROBE_VECTORS, (
            f"{probe_id} is a must-differ vector but the loopback page does not "
            f"read {expected[probe_id]!r}"
        )


def test_the_page_really_performs_a_2d_readback():
    """The page must DO the readback, not merely name a vector.

    `PROBE_VECTORS` is a tuple of strings; adding a name to it costs nothing and
    proves nothing. What makes the vector real is that the served page calls
    `getContext('2d')` and `getImageData` — and specifically NOT `toDataURL`,
    which routes through a PNG encoder and would mix the pixels with a
    compressor's choices.
    """
    html = local_probe.probe_page_html()

    assert "getContext('2d')" in html
    assert "getImageData" in html
    assert "toDataURL" not in html, (
        "a PNG encoder's output is not a pixel readback: toDataURL would mix "
        "the renderer's bytes with a compressor's own choices"
    )
    assert "%%" not in html, "an unsubstituted placeholder reached the page"


# --- one source, so the two surfaces cannot drift ---------------------------


def test_the_page_evaluates_the_INVENTORYS_OWN_expression_not_a_copy():
    """The page and `probes.canvas.readback` must evaluate ONE source.

    This is the property that keeps the numbers COMPARABLE. Two surfaces reading
    "canvas" by even slightly different draws — a different font string, a band
    boundary off by one — produce digests that are each self-consistent and
    mutually incomparable, and nothing would report that, because neither
    surface's own numbers would look wrong.

    Asserted by identity against the probe record actually in the inventory, so
    editing either surface alone breaks it.
    """
    canvas_probe = next(p for p in probes.PROBES if p.id == CANVAS_PROBE_ID)

    assert canvas_probe.expr == probes.CANVAS_READBACK_EXPR, (
        "the inventory's canvas probe stopped using the shared expression"
    )
    assert probes.CANVAS_READBACK_EXPR in local_probe.probe_js(), (
        "the loopback page is no longer evaluating the inventory's own canvas "
        "expression — the two surfaces have drifted into separate drafts"
    )


def test_the_shared_expression_is_pinned_to_the_draw_the_readings_measured():
    """The committed readings are digests of THIS draw.

    `readings/ps135-2026-08-24/` is what classified this probe INDEPENDENT, and
    those numbers are only meaningful for the exact draw that produced them. So
    the load-bearing constants are pinned here: change any of them and the
    committed readings quietly stop being measurements of the shipped probe.

    The surface size in particular is pinned INSIDE the expression (W=64,H=32)
    rather than inherited from the shared `_CANVAS` (300x150) — the digest
    depends on it, so it must not follow a future edit to a shared helper.
    """
    expr = probes.CANVAS_READBACK_EXPR

    assert "var W=64,H=32;" in expr, "the surface size the readings were taken at"
    # The FNV-1a constants, and the eligible-byte window for `mid`.
    assert "var h=2166136261,mid=0;" in expr
    assert "h=Math.imul(h^v,16777619);" in expr
    assert "if(v>1&&v<254)mid++;" in expr
    # The draw's own signal carriers: text and a curve. Band fills alone are
    # flat colour any renderer reproduces exactly, so a fills-only draw would
    # observe nothing at all.
    assert "ctx.fillText('Persona mMwWgjpq\\u00c9\\u4e2d',2,20);" in expr
    assert "ctx.arc(50,16,9,0,Math.PI*1.5);" in expr


# --- the reduction, EXECUTED rather than read -------------------------------


NODE_REDUCTION_HARNESS = r"""
// A stub 2D surface. The DRAW is recorded and `getImageData` hands back bytes
// this harness chooses, which isolates the REDUCTION — the part the loopback
// page and the inventory must agree on — from a real rasteriser.
function makeCanvas(pixelFn) {
  const calls = [];
  const ctx = {
    fillStyle: '', strokeStyle: '', font: '', lineWidth: 0,
    fillRect: (...a) => calls.push(['fillRect', ...a]),
    fillText: (...a) => calls.push(['fillText', ...a]),
    beginPath: () => calls.push(['beginPath']),
    arc: (...a) => calls.push(['arc', ...a]),
    stroke: () => calls.push(['stroke']),
    getImageData: (x, y, w, h) => {
      calls.push(['getImageData', x, y, w, h]);
      const d = new Uint8ClampedArray(w * h * 4);
      for (let i = 0; i < d.length; i++) d[i] = pixelFn(i);
      return { data: d };
    },
  };
  const c = { width: 0, height: 0, getContext: (k) => (k === '2d' ? ctx : null) };
  return { c, calls };
}

const made = makeCanvas((i) => (i * 37 + 11) % 256);
global.document = { createElement: () => made.c };
console.log(JSON.stringify({ value: (%%EXPR%%), calls: made.calls }));
"""


def _node_or_skip():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


def _fnv1a_reference(data):
    """An INDEPENDENT implementation of the reduction, written from the spec.

    Deliberately not a call into any project helper: the point is to check the
    shipped JS against a second opinion, and a shared helper would let one bug
    satisfy both sides.
    """
    h = 2166136261
    mid = 0
    for v in data:
        if 1 < v < 254:
            mid += 1
        h = ((h ^ v) * 16777619) & 0xFFFFFFFF
    return {"digest": h, "bytes": len(data), "mid": mid}


def test_the_shared_reduction_really_computes_FNV1a_over_every_byte(tmp_path):
    """EXECUTED, not grepped.

    A test that asserted `"Math.imul" in expr` would prove the text is present.
    This runs the shipped expression under node against a known byte pattern and
    checks its digest, byte count and eligible-byte counter against an
    independent implementation — so a reduction that reads a SAMPLE, or sums
    instead of hashing, or gets the `mid` window's boundaries wrong, fails here.

    The pattern is chosen to cross `mid`'s boundaries in both directions: values
    0, 1, 254 and 255 are ineligible and everything between them counts.
    """
    node = _node_or_skip()

    script = tmp_path / "reduction.js"
    script.write_text(
        NODE_REDUCTION_HARNESS.replace("%%EXPR%%", probes.CANVAS_READBACK_EXPR),
        encoding="utf-8",
    )
    out = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, f"the shared expression did not run: {out.stderr}"

    got = json.loads(out.stdout)
    expected = _fnv1a_reference([(i * 37 + 11) % 256 for i in range(64 * 32 * 4)])

    assert got["value"] == expected, (
        "the shipped reduction disagrees with an independent FNV-1a over the "
        f"same bytes: {got['value']} != {expected}"
    )

    # The draw actually happened, and READ BACK the surface it drew on — a
    # reduction over an untouched buffer would still hash to something.
    kinds = [c[0] for c in got["calls"]]
    assert kinds.count("fillRect") == 4, "the four mid-range bands"
    assert "fillText" in kinds and "stroke" in kinds, "text and curve carry the signal"
    assert got["calls"][-1] == ["getImageData", 0, 0, 64, 32]


def test_the_served_page_is_valid_javascript(tmp_path):
    """A page that does not parse reads as an unread reading, not as an error.

    The canvas expression is substituted into an inline `<script>`; a syntax
    error there would leave the page showing "reading..." forever, which parses
    downstream as "the page said nothing" rather than as a broken probe.
    """
    node = _node_or_skip()

    import re

    html = local_probe.probe_page_html()
    inline = re.search(r"<script>(.*)</script>", html, re.S)
    assert inline, "the probe page carries no inline script"

    script = tmp_path / "page_inline.js"
    script.write_text(inline.group(1), encoding="utf-8")
    out = subprocess.run(
        [node, "--check", str(script)], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, f"the probe page's script does not parse: {out.stderr}"


# --- an unreadable canvas must not be COMPARED ------------------------------


def _arm(label: str, vectors: dict, seed: int, error: str = "") -> Arm:
    return Arm(
        label=label,
        reading=ProbeReading(vectors=vectors),
        layer=LayerReport(route="init_scripts"),
        seed=seed,
        error=error,
    )


def test_an_unreadable_canvas_is_UNAVAILABLE_and_never_compared():
    """The five null paths must report `unavailable:`, not a value.

    `_computed_vectors` drops `unavailable:`/`error:` readings before comparing.
    If a null stringified into a value instead, two sides agreeing on "there is
    no canvas here" would be counted as an UNMOVED vector — manufacturing the
    exact false negative this instrument exists to make impossible.
    """
    reading = ProbeReading(
        vectors={
            CANVAS_VECTOR: "unavailable:no-canvas-2d-readback",
            "audio_digest": "35.749971",
        }
    )
    record = build_differential_record(
        AXIS_SEED,
        "firefox",
        _arm("a", dict(reading.vectors), SEED),
        _arm("b", dict(reading.vectors), CONTROL_SEED),
    )

    assert CANVAS_VECTOR not in record["comparable_vectors"], (
        "a canvas the page could not read must not be compared as a value"
    )
    assert CANVAS_VECTOR not in record["diff"]["unchanged"]
    # The page's own wording has to be one the dropper recognises.
    assert "unavailable:no-canvas-2d-readback" in local_probe.probe_page_html()


# --- the committed live readings, replayed through the real reporting -------


def _committed(name: str) -> dict:
    path = READINGS / name
    if not path.exists():
        pytest.fail(f"the committed reading {name!r} is missing from {READINGS}")
    return json.loads(path.read_text(encoding="utf-8"))


def _canvas_vector_from(reading: dict, realm: str = "window") -> str:
    """Render a committed probe reading in the LOOPBACK PAGE'S vector format.

    The page reduces the probe's `{digest, bytes, mid}` to one string, because
    the differential compares vectors for equality. Formatting the committed
    readings the same way is what lets the real reporting code be driven by real
    measurements instead of by invented values.
    """
    entry = reading["probes"][realm][CANVAS_PROBE_ID]["value"]
    return f"{entry['digest']}:bytes{entry['bytes']}:mid{entry['mid']}"


def test_the_committed_chromium_readings_report_canvas_MOVED_on_the_seed_axis():
    """The offline proxy for the live test's POSITIVE arm.

    Driven by `readings/ps135-2026-08-24/` — the real two-engine measurement
    that classified this probe — pushed through the real
    `build_differential_record`. Nothing here is invented: if the vector format
    or the diff logic breaks, these committed numbers stop reporting `moved`.
    """
    before = _canvas_vector_from(_committed("reading.chromium.seed4242.json"))
    after = _canvas_vector_from(_committed("reading.chromium.seed1337.json"))

    assert before != after, (
        "the committed chromium readings for the two default seeds are equal — "
        "the measurement this test rests on has changed"
    )

    record = build_differential_record(
        AXIS_SEED,
        "chromium",
        _arm("chromium/seed4242", {CANVAS_VECTOR: before}, SEED),
        _arm("chromium/seed1337", {CANVAS_VECTOR: after}, CONTROL_SEED),
    )

    assert record["verdict"] == "moved"
    assert CANVAS_VECTOR in record["diff"]["moved"]


def test_the_committed_firefox_readings_report_canvas_UNCHANGED_and_that_is_correct():
    """The offline proxy for the live test's NEGATIVE arm.

    Firefox reads `4242351214` at every seed because `--fingerprint=` is
    chromium-only and the firefox arm returns at `process.py:353` well before
    it. That is a REPORT, not a defect for this ticket: fixing it means writing
    a canvas spoof, which PS-135 assigned to PS-2 explicitly.

    This arm is meaningful only NEXT TO the chromium one above — on its own,
    `unchanged` is also what a broken probe returns.
    """
    before = _canvas_vector_from(_committed("reading.firefox.seed4242.json"))
    after = _canvas_vector_from(_committed("reading.firefox.seed1337.json"))

    assert before == after, "the committed firefox collision has changed"

    record = build_differential_record(
        AXIS_SEED,
        "firefox",
        _arm("firefox/seed4242", {CANVAS_VECTOR: before}, SEED),
        _arm("firefox/seed1337", {CANVAS_VECTOR: after}, CONTROL_SEED),
    )

    assert record["verdict"] == "unmoved"
    assert CANVAS_VECTOR in record["diff"]["unchanged"]
    # And the prose must send the reader at the SEED axis, not at the layer.
    assert "not seed-derived" in record["detail"]


def test_the_chromium_entropy_is_caused_by_the_FLAG_not_by_the_harness():
    """The counterfactual, which is what makes the chromium arm EVIDENCE.

    With `--fingerprint=` removed, seed args 1337 and 4242 both read
    `2616755061` — the same value. Without this, "chromium moves and firefox
    does not" is a correlation between engine and outcome; with it, the flag is
    shown to be the cause.
    """
    a = _canvas_vector_from(
        _committed("counterfactual.chromium.no-fingerprint-flag.seedarg1337.json")
    )
    b = _canvas_vector_from(
        _committed("counterfactual.chromium.no-fingerprint-flag.seedarg4242.json")
    )
    assert a == b, "the counterfactual no longer collides"

    seeded = _canvas_vector_from(_committed("reading.chromium.seed1337.json"))
    assert seeded != a, (
        "the flagged and unflagged chromium readings agree — the flag would "
        "then not be what causes the entropy"
    )


def test_every_committed_reading_carries_the_shape_the_draw_should_produce():
    """`bytes: 8192, mid: 6144` on every reading — the draw landed.

    `mid` is the self-check that keeps a green from being empty: it counts how
    many bytes were even ELIGIBLE to be nudged. A draw gone black or white
    collapses it toward the alpha-only floor, which says WHICH of "the spoof did
    nothing" and "nothing was measured" happened.
    """
    files = sorted(READINGS.glob("*.json"))
    assert files, f"no committed readings under {READINGS}"

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for realm, entries in data["probes"].items():
            entry = entries[CANVAS_PROBE_ID]["value"]
            assert entry["bytes"] == 8192, f"{path.name}/{realm}"
            assert entry["mid"] == 6144, f"{path.name}/{realm}"


# --- THE LIVE TWO-ARM TEST --------------------------------------------------


def _run_seed_axis(engine: str) -> dict:
    """One `differential --axis seed` run, through the shipped entry point."""
    from src.services.verify.layer_differential import run_differential

    return run_differential(
        axis=AXIS_SEED,
        engine=engine,
        seed=SEED,
        control_seed=CONTROL_SEED,
        # The container this usually runs in forbids the user namespace
        # chromium's sandbox needs. Waived explicitly, and the record discloses
        # it (see `_waiver_notes`) so a reading taken on that surface is never
        # byte-identical to one taken on a healthy host.
        allow_unsandboxed=True,
        allow_small_dev_shm=True,
    )


# CHROMIUM ALONE, and the omission of `browser_firefox` is deliberate rather
# than an oversight. This marker names what GATES the test, and only chromium
# does: the firefox arm is unreachable unless chromium ran first (see the
# docstring — that ordering is Condition 1, not a style choice). Marking
# firefox too made a correct "chromium engine not runnable here" skip report as
# a `browser_firefox` FAILURE under CI's `PERSONA_REQUIRED_CAPABILITIES=browser`
# — a red whose own message named the one engine that was not missing.
#
# The firefox guard is NOT lost by narrowing this. conftest's
# `capabilities_for_skip` unions a marker's names with what the skip's REASON
# matched, so a genuine "firefox not runnable here" still classifies as
# `browser_firefox` and still fails wherever that capability is declared.
# Verified both ways in tests/test_skip_visibility.py.
@pytest.mark.requires_capability("browser_chromium")
def test_the_seed_axis_moves_canvas_on_chromium_and_collides_on_firefox():
    """THE ACCEPTANCE CRITERION: ONE test, BOTH arms.

    ARM 1 (chromium, POSITIVE CONTROL) — `canvas_pixel_hash` under `moved`.
    ARM 2 (firefox, the finding) — `canvas_pixel_hash` under `unchanged`.

    THE ARMS ARE NOT SEPARABLE, and the ordering below enforces it. `unchanged`
    is also exactly what a broken probe returns, so the firefox arm asserts a
    NEGATIVE and cannot stand alone: a firefox-only version of this test would
    be a check that could not fail. Chromium therefore runs FIRST, and if it is
    unavailable the whole test SKIPS — reporting inconclusive — rather than
    proceeding to a firefox arm that would report a green meaning nothing.

    FIREFOX `unchanged` IS A CORRECT PASS. `--fingerprint=` is chromium-only and
    the firefox arm returns at `process.py:353` before it. Do NOT "fix" this by
    adding a canvas spoof: that is PS-2's, per PS-135. The control that makes
    this a statement about canvas rather than about the harness is asserted
    below — `audio_digest` DOES move on the same firefox run.
    """
    from src.services.verify.browser_tier import EngineUnavailable

    # --- ARM 1: chromium. The positive control, and the gate on the rest. ---
    try:
        chromium = _run_seed_axis("chromium")
    except (EngineUnavailable, ImportError) as exc:
        pytest.skip(f"chromium engine not runnable here: {exc}")

    if chromium["verdict"] == "inconclusive":
        # An arm that never launched is not a failure — and critically, it is
        # not a licence to go on and pass on firefox alone.
        pytest.skip(f"chromium engine not runnable here: {chromium['detail']}")

    assert CANVAS_VECTOR in chromium["comparable_vectors"], (
        "the chromium page could not READ canvas at all, so this run says "
        f"nothing about whether the vector moves: {chromium['detail']}"
    )
    assert CANVAS_VECTOR in chromium["diff"]["moved"], (
        "canvas did NOT move between two seeds on chromium, where "
        "--fingerprint= drives it. Either the loopback probe is not reaching "
        f"the page or the flag stopped taking effect: {chromium['detail']}"
    )

    # --- ARM 2: firefox. Now, and only now, the negative is evidence. -------
    try:
        firefox = _run_seed_axis("firefox")
    except (EngineUnavailable, ImportError) as exc:
        pytest.skip(f"firefox not runnable here: {exc}")

    if firefox["verdict"] == "inconclusive":
        pytest.skip(f"firefox not runnable here: {firefox['detail']}")

    assert CANVAS_VECTOR in firefox["comparable_vectors"], (
        "the firefox page could not READ canvas, which is a DEAD PROBE — not "
        f"the collision this arm is asserting: {firefox['detail']}"
    )
    assert CANVAS_VECTOR in firefox["diff"]["unchanged"], (
        "canvas moved per seed on firefox. That contradicts the measured "
        "position (--fingerprint= is chromium-only), so the reading — not the "
        f"expectation — is what to check first: {firefox['detail']}"
    )

    # THE CONTROL that makes the firefox arm a statement about CANVAS rather
    # than about a masking layer that failed to load. Without this, "nothing
    # moved on firefox" would be equally well explained by a dead harness.
    assert local_probe.AUDIO_DIGEST in firefox["diff"]["moved"], (
        "audio_digest did not move per seed on firefox either, so the firefox "
        "arm is not evidence about canvas: the masking layer looks inert here, "
        f"which is a harness finding, not a canvas one: {firefox['detail']}"
    )
