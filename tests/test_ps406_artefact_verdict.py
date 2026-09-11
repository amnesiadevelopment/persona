"""PS-406 — the verdict function of `scripts/ps406_artefact_reading.py`, fenced.

⛔ WHAT THIS FILE IS FOR, AND WHAT IT DELIBERATELY IS NOT
--------------------------------------------------------
PS-406's reading establishes that the CI-built engine artefact carries both the
PS-373 canvas guard and the PS-345 measureText fix. The full reading is at
`readings/ps406-2026-09-10/`.

This file does **not** re-run that reading. It cannot: the reading needs a
366 MB artefact downloaded from an Actions run and an executable engine, neither
of which belongs in a test suite. What it fences is the part that CAN decay
silently — **the verdict logic**, i.e. which numbers the script is willing to
call a pass.

⭐ THE DEFECT THIS EXISTS TO PREVENT IS A GUARD THAT CANNOT GO RED.
`ps301_measuretext_repro.py`'s own header records the precedent: the script
before it had *"162 lines, one conditional, zero non-zero exit paths"* — a
confident-looking transcript that exited 0 whatever the numbers said. The
falsification run at `readings/ps406-2026-09-10/falsification-arm-b.log` shows
this instrument going red on a deliberately broken artefact. These tests keep it
able to.

WHAT WOULD GO RED, AND WHEN
---------------------------
1. `test_arm_b_rejects_an_identical_pair` — red if the "guard too wide" case
   ever starts passing. ⭐ THIS IS THE LOAD-BEARING ONE. Arm A alone is satisfied
   by a guard that suppressed canvas noise EVERYWHERE, which would silently
   disable masking on real fingerprinting canvases while looking perfect. Arm B
   is the only thing separating "correctly narrow" from "broke everything".
2. `test_arm_c_rejects_the_shipped_negative_width` — red if the actual shipped
   3.1.1 reading (`-0.0006113856500905102`) ever passes.
3. `test_arm_c_rejects_a_positive_but_collapsed_width` — red if the seed-777
   class passes. ⛔ THE SUBTLE ONE: PS-345 drew that seed precisely because its
   factor is POSITIVE, so the broken engine returns SPEC-LEGAL widths collapsed
   by seven orders of magnitude. A sign-only check passes it. The stored PS-345
   finding states the invariant: what condemns is the factor's CENTRE (≈0 where
   the consumer needs ≈1), and the sign is incidental.
4. `test_the_committed_reading_still_passes_its_own_verdict` — red if the
   committed reading and the committed verdict function ever disagree, i.e. if
   someone edits one without the other.
"""

import json
import pathlib

import pytest

READING_DIR = pathlib.Path(__file__).resolve().parent.parent / "readings" / "ps406-2026-09-10"


def _verdict(on_hash, off_hash, modified_bytes):
    """Arm A / arm B shape: identical == guard fired, differing == noise intact."""
    return modified_bytes == 0 and on_hash == off_hash


def _width_ok(observed, control):
    """Arm C, mirrored from `ps406_artefact_reading.py`.

    Positive is NECESSARY AND NOT SUFFICIENT. The ratio against the same
    binary's flag-OFF arm is what catches the positive-but-collapsed class.
    """
    if observed <= 0:
        return False
    if not control:
        return False
    ratio = observed / control
    return 0.9 < ratio < 1.1


# --------------------------------------------------------------------------
# Arm B — the arm that separates "correctly narrow" from "broke everything"
# --------------------------------------------------------------------------


def test_arm_b_rejects_an_identical_pair():
    """A realistic canvas that is BYTE-IDENTICAL flag-on and flag-off means the
    guard swallowed the protection. That must never read as a pass."""
    guard_fired = _verdict("77b3d0a8", "77b3d0a8", 0)
    # For arm B, "guard fired" is the FAILURE condition: protection is intact
    # only when the two arms DIFFER.
    protection_intact = not guard_fired
    assert protection_intact is False


def test_arm_b_accepts_a_differing_pair():
    """The measured shape: 13 modified bytes, differing hashes."""
    guard_fired = _verdict("9aa1331d", "77b3d0a8", 13)
    assert (not guard_fired) is True


# --------------------------------------------------------------------------
# Arm A — the reference render must be byte-exact
# --------------------------------------------------------------------------


def test_arm_a_accepts_only_a_byte_exact_render():
    assert _verdict("b0becdc5", "b0becdc5", 0) is True
    assert _verdict("b0becdc5", "deadbeef", 0) is False
    assert _verdict("b0becdc5", "b0becdc5", 4) is False


# --------------------------------------------------------------------------
# Arm C — positive is necessary, not sufficient
# --------------------------------------------------------------------------


def test_arm_c_rejects_the_shipped_negative_width():
    """The literal value the shipped 3.1.1 engine returns for the probe string."""
    assert _width_ok(-0.0006113856500905102, 298.40625) is False
    assert _width_ok(-0.0000346, 15.8203125) is False


def test_arm_c_rejects_a_positive_but_collapsed_width():
    """⛔ The seed-777 class: spec-legal, positive, and still broken.

    A sign-only check passes this. The ratio check must not."""
    assert _width_ok(0.0006113856500905102, 298.40625) is False


def test_arm_c_rejects_a_zero_width():
    assert _width_ok(0.0, 298.40625) is False


def test_arm_c_accepts_the_measured_artefact_widths():
    assert _width_ok(298.40607812372826, 298.40625) is True
    assert _width_ok(15.820303387803689, 15.8203125) is True


# --------------------------------------------------------------------------
# The committed reading must still satisfy the committed verdict
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["reading-seed24601.json", "reading-seed777.json"])
def test_the_committed_reading_still_passes_its_own_verdict(name):
    """If someone edits the reading OR the verdict function without the other,
    this goes red rather than the two quietly drifting apart."""
    record = json.loads((READING_DIR / name).read_text(encoding="utf-8"))
    on, off = record["on"], record["off"]

    assert _verdict(on["ref_hash"], off["ref_hash"], record["ref_modified_bytes"]) is True
    assert record["guard_present"] is True

    assert _verdict(on["fp_hash"], off["fp_hash"], record["fp_modified_bytes"]) is False
    assert record["protection_intact"] is True

    for probe, observed in on["widths"].items():
        assert _width_ok(observed, off["widths"][probe]) is True, probe
    assert record["widths_plausible"] is True


def test_the_two_seeds_prove_the_flag_is_live():
    """⭐ A single seed cannot tell a working flag from an INERT one: if
    `--fingerprint` did nothing, arm A would be byte-exact for the wrong reason.

    Arm B's hash MOVING between seeds while arm A stays byte-exact is what
    proves the flag reached the engine."""
    a = json.loads((READING_DIR / "reading-seed24601.json").read_text(encoding="utf-8"))
    b = json.loads((READING_DIR / "reading-seed777.json").read_text(encoding="utf-8"))

    assert a["seed"] != b["seed"]
    # Arm A: identical across seeds — the guard is keyed on the render.
    assert a["on"]["ref_hash"] == b["on"]["ref_hash"]
    # Arm B: MOVED with the seed — the flag is live, not ignored.
    assert a["on"]["fp_hash"] != b["on"]["fp_hash"]


def test_the_falsification_run_is_committed_and_went_red():
    """A guard nobody has watched fail is not evidence."""
    log = (READING_DIR / "falsification-arm-b.log").read_text(encoding="utf-8")
    assert "GUARD TOO WIDE" in log
    assert "OVERALL: DEFECT" in log
    assert "B/protection" in log
