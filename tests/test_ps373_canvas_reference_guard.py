"""PS-373 — the canvas noise loop must leave REFERENCE renders byte-exact.

WHAT THIS PINS, AND WHY IT IS AN EXECUTION TEST
───────────────────────────────────────────────
Masking detectors do not measure how MUCH a canvas was altered. pixelscan's
`canvasNoiseOn2d` probe fills a small canvas with a handful of solid reference
colours and checks it reads back BYTE-EXACT; any substitution at all sets
`isCanvas=false`, which gates its font probe, which is what surfaces as
"masking detected" (feder-cr's reverse-engineering of px294.js, recorded on
PS-373). One modified pixel fails that equality check exactly as hard as a
thousand would — so no amount of tuning the noise budget can pass it, and only
NOT WRITING to such a render can.

`012-canvas-get-image-data.patch` therefore carries a reference-render guard,
and this file pins it.

⛔ THE TEST THAT MATTERS IS THE NEGATIVE ONE. Exempting reference renders is
trivially achievable by switching the noise off altogether — and PS-373 states
the trap explicitly: *"No masking detected" is NOT the goal; the only
configuration that currently achieves it has ZERO protection.* So this file
asserts BOTH halves, and the protection half is the one that constrains:

    1. a reference-like render (few distinct colours)  -> byte-exact
    2. a realistic fingerprinting canvas               -> STILL noised
    3. that noise                                      -> still seed-dependent

WHY IT COMPILES C++ RATHER THAN READING THE PATCH
──────────────────────────────────────────────────
A regex over the patch text would pass against a guard that never runs, and
would pin the CURRENT SPELLING of the constant rather than the BEHAVIOUR. This
project has hit that vacuity class six times (knowledge article PS-11), so the
harness compiles the patch's OWN added lines — extracted mechanically by
`extract_shuffle.py`, never re-typed — and executes them. Mechanism is free to
change; the observable a detector reads is what is pinned.

⚠️ WHAT IS **NOT** VERBATIM, stated rather than glossed: Chromium's tree is not
present in an agent container, so the Skia surface the loop calls into is a
shim (`skia_shim.h`). The shim round-trips known values through pack/unpack for
every colour type before any measurement runs, and the harness aborts if that
self-test fails — a mis-ordered channel would make every number meaningless.

⚠️ AND THE FINGERPRINT CANVAS IS MODELLED, NOT CAPTURED. There is no browser
here to rasterise text, so antialiased glyph coverage is synthesised. The claim
this supports is deliberately coarse enough to survive that: not "a real canvas
has exactly K colours", but "a real canvas has THOUSANDS while a probe has tens".
Measured separation is 6,224–45,551 against a ceiling of 16 — three orders of
magnitude, which no plausible modelling error closes.

THE DISCRIMINATION CLAIM
────────────────────────
These assertions can fail, and that is checked by MUTATION rather than asserted
in a comment. `test_regression_harness_rejects_an_over_wide_guard` widens the
ceiling so the guard swallows real canvases, and requires the harness to reject
it; `test_regression_harness_rejects_a_missing_guard` removes the guard and
requires the probe assertion to reject that. A harness that cannot fail in
either direction would pin nothing.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ARTIFACTS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "readings"
    / "ps373-2026-09-07"
    / "artifacts"
)
PATCH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "engine"
    / "patches"
    / "fingerprint"
    / "012-canvas-get-image-data.patch"
)

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None, reason="g++ not available to compile the patch body"
)


def _extract(tmp_path: pathlib.Path) -> pathlib.Path:
    """Run the mechanical extractor, then copy the harness sources beside it."""
    subprocess.run(
        ["python3", str(ARTIFACTS / "extract_shuffle.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    for name in (
        "extracted_shuffle_body.inc",
        "skia_shim.h",
        "protection_regression.cc",
        "probe_harness.cc",
    ):
        shutil.copy(ARTIFACTS / name, tmp_path / name)
    return tmp_path


def _build_and_run(
    workdir: pathlib.Path, source: str = "protection_regression.cc"
) -> subprocess.CompletedProcess:
    exe = workdir / "harness"
    build = subprocess.run(
        ["g++", "-std=c++17", "-O1", "-o", str(exe), str(workdir / source)],
        capture_output=True,
        text=True,
        cwd=workdir,
    )
    assert build.returncode == 0, f"harness failed to compile:\n{build.stderr}"
    return subprocess.run([str(exe)], capture_output=True, text=True, cwd=workdir)


# ---------------------------------------------------------------------------
# The property under test
# ---------------------------------------------------------------------------


def test_reference_renders_are_byte_exact_and_protection_is_preserved(tmp_path):
    """Both halves at once: the probe passes AND real canvases are still noised."""
    work = _extract(tmp_path)
    run = _build_and_run(work)
    assert run.returncode == 0, (
        "the canvas noise loop failed a reference-render or protection check:\n"
        + run.stdout
        + run.stderr
    )
    out = run.stdout
    assert "ALL CHECKS PASSED" in out, out
    # The specific observable a detector reads.
    assert "70x5 / 14 colours modified = 0" in out, out


def test_realistic_fingerprint_canvases_still_receive_noise(tmp_path):
    """The negative control: exempting everything would be a total protection loss."""
    work = _extract(tmp_path)
    out = _build_and_run(work).stdout
    noised = [
        line
        for line in out.splitlines()
        if "OK (protection preserved)" in line
    ]
    assert len(noised) >= 4, f"expected every realistic canvas to be noised:\n{out}"
    assert "NOISE LOST" not in out, out


def test_noise_remains_seed_dependent(tmp_path):
    """Unlinkability: two profiles must not produce an identical canvas."""
    work = _extract(tmp_path)
    out = _build_and_run(work).stdout
    assert "two different seeds differ : yes OK" in out, out
    assert "same seed reproduces       : yes OK" in out, out


# ---------------------------------------------------------------------------
# Discrimination — these prove the assertions above are not vacuous
# ---------------------------------------------------------------------------


def test_regression_harness_rejects_an_over_wide_guard(tmp_path):
    """Widen the ceiling until real canvases are exempted; the harness must object."""
    work = _extract(tmp_path)
    inc = work / "extracted_shuffle_body.inc"
    inc.write_text(
        inc.read_text(encoding="utf-8").replace(
            "kMaxRefColors = 16;", "kMaxRefColors = 100000;"
        ),
        encoding="utf-8",
    )
    run = _build_and_run(work)
    assert run.returncode != 0, (
        "an over-wide guard swallows all masking and the harness did not "
        "notice — it is not measuring protection:\n" + run.stdout
    )
    assert "NOISE LOST" in run.stdout, run.stdout


def test_regression_harness_rejects_a_missing_guard(tmp_path):
    """Remove the guard; the reference-probe assertion must object."""
    work = _extract(tmp_path)
    inc = work / "extracted_shuffle_body.inc"
    text = inc.read_text(encoding="utf-8")
    assert "if (!exceeded_ref_colors) {" in text, "guard shape changed; update this test"
    inc.write_text(text.replace("if (!exceeded_ref_colors) {", "if (false) {"), encoding="utf-8")
    run = _build_and_run(work)
    assert run.returncode != 0, (
        "with the guard removed the reference probe is modified, and the "
        "harness did not notice:\n" + run.stdout
    )


def test_extractor_is_reading_the_shipped_patch(tmp_path):
    """The body under test must come from the patch, not from a stale copy."""
    work = _extract(tmp_path)
    body = (work / "extracted_shuffle_body.inc").read_text(encoding="utf-8")
    patch = PATCH.read_text(encoding="utf-8")
    # Every non-trivial line of the extracted body must exist as an ADDED line.
    added = {
        line[1:].strip()
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    }
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        assert stripped in added, (
            f"extracted line is not an added line of {PATCH.name}: {stripped!r}"
        )
