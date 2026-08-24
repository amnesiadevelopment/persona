"""PS-135: the `canvas.readback` probe — the canvas-2D vector, OBSERVED.

WHY THIS PROBE EXISTS. Canvas 2D was the one surface persona held by a claim
about a third-party binary and the one surface no probe had ever read — the
same fact twice. `webgl_ext.py:6` says, verbatim, that "readback is not patched
in C++ (unlike 2D-canvas toDataURL)", and that clause is the reason no canvas
spoof was ever written. Nothing read the vector to check.

WHAT THESE TESTS ASSERT ON, AND WHY IT IS THE COMMITTED ARTIFACT. The project's
standing directive is that a check which could not have failed is not coverage,
and its named failure mode (PS-11) is a test asserting on text the code itself
generated. So the assertions below read the REAL RECORDED SNAPSHOT — the
committed `tests/fixtures/engine-fingerprint-baseline.firefox.json`, produced by
launching a real Firefox and reading it — rather than grepping `probes.py` for
the string "getImageData" or checking that a helper ran. Remove the probe's
record from the inventory and re-record, and these go red, because the row they
assert on stops existing.

THE HONEST BOUND ON THEM. These tests do not launch a browser (playwright is not
importable in CI, per `test_verify_baseline.py`'s own docstring). They pin what
the recorded reading SAYS. The live two-engine measurement that classified this
vector is a separate act, recorded under `readings/ps135-2026-08-24/` and
summarised in the `variance` comment in `probes.py`. Neither substitutes for the
other: the measurement decided the classification, and these tests keep the
inventory and the artifact from drifting away from it.
"""

import json
import pathlib

import pytest

from src.services.verify import baseline, probes

PROBE_ID = "canvas.readback"


def _artifact():
    root = pathlib.Path(__file__).resolve().parents[1]
    return root / baseline.BASELINE_ARTIFACT


def _snapshot():
    return json.loads(_artifact().read_text(encoding="utf-8"))


def _recorded(realm):
    """The recorded entry for this probe, or a failure that NAMES what is wrong.

    Reached through a helper rather than by subscripting the snapshot inline so
    the missing-row case reads as a sentence instead of as ``KeyError:
    'canvas.readback'``. This is the exact path the AC5 falsification takes —
    remove the probe record, re-record, and every assertion below arrives here —
    so it is the one failure that most needs to explain itself.
    """
    realms = _snapshot()["probes"]
    if realm not in realms:
        pytest.fail(f"the committed baseline has no {realm!r} realm at all")
    entries = realms[realm]
    if PROBE_ID not in entries:
        pytest.fail(
            f"the committed baseline carries NO {PROBE_ID} row in the {realm!r} "
            f"realm ({len(entries)} probes recorded). Either the probe was "
            f"removed from the inventory, or the baseline was not re-recorded "
            f"after it was added — a probe that is not recorded is a vector "
            f"nobody is reading."
        )
    return entries[PROBE_ID]


def _probe():
    return next(p for p in probes.PROBES if p.id == PROBE_ID)


# --- the inventory record ---------------------------------------------------


def test_the_probe_is_in_the_inventory_in_both_realms():
    # AC1, the inventory half. `Probe.__post_init__` already refuses an unknown
    # realm at construction, so this pins the DECLARATION rather than re-testing
    # the validator: a canvas-2D context is obtainable both on a detached
    # element and on an OffscreenCanvas, and dropping a realm here would quietly
    # stop half the vector being read.
    assert PROBE_ID in {p.id for p in probes.PROBES}
    assert _probe().realms == probes.BOTH


# --- AC1 / AC5: the RECORDED reading, not the generated source --------------


@pytest.mark.parametrize("realm", ["window", "worker"])
def test_the_committed_baseline_carries_a_real_canvas_reading(realm):
    """AC1 + AC5. THE falsification anchor: this asserts on a row in the real
    recorded snapshot. Delete the probe from `PROBES` and re-record and the row
    is gone; keep the record but break the draw and `value` becomes an `error`.
    Either way this fails, which is what makes it coverage rather than
    decoration."""
    entry = _recorded(realm)

    assert "error" not in entry, (
        f"canvas.readback was RECORDED AS AN ERROR in the {realm} realm "
        f"({entry.get('error')}). An unread probe compares EQUAL against the "
        f"same failure later and is reported as agreement."
    )
    assert "value" in entry, entry

    value = entry["value"]
    # The shape the reduction promises: an integer digest (no float formatting
    # for a snapshot comparison to trip on) plus the two self-check counters.
    assert isinstance(value["digest"], int)
    assert 0 <= value["digest"] <= 0xFFFFFFFF
    assert isinstance(value["bytes"], int) and value["bytes"] > 0


@pytest.mark.parametrize("realm", ["window", "worker"])
def test_the_recorded_draw_is_mid_range_rather_than_black(realm):
    """THE TRAP that would make a WORKING spoof read as a dead one, asserted on
    the recorded bytes rather than on the source that drew them.

    A perturbation that nudges a byte only when it is strictly inside the guard
    band leaves pure black (0) and pure white (255) untouched. A probe that
    cleared to black would therefore read back a surface nothing could move,
    observe no variance, and look exactly like a total masking failure. `mid`
    is the probe's own self-check: how many bytes were even ELIGIBLE.

    Asserted as a real fraction of the surface, not as the literal 6144: pinning
    the constant would fail the day the draw legitimately changes size and train
    its reader to edit the number instead of thinking."""
    value = _recorded(realm)["value"]

    assert value["mid"] > value["bytes"] // 2, (
        f"only {value['mid']} of {value['bytes']} recorded bytes are inside the "
        f"perturbable mid-range, so this draw would read a working canvas spoof "
        f"as a dead one"
    )


def test_the_two_realms_were_read_independently():
    """Both realms carry a reading of their own. The two digests are ALLOWED to
    be equal — on the packaged Firefox they are, because the same renderer draws
    both — so this deliberately does NOT assert they differ. What it pins is
    that the worker row is a real reading and not a null: a null is recorded as
    {"value": null}, and the cross-profile comparator keys on the PRESENCE of
    "value", so two profiles both reading null would compare EQUAL and be
    reported COLLIDING on an INDEPENDENT probe — a fabricated finding on every
    pair."""
    for realm in ("window", "worker"):
        value = _recorded(realm)["value"]
        assert value is not None, (
            f"the {realm} realm recorded a NULL canvas reading. On an "
            f"INDEPENDENT probe two profiles both reading null compare EQUAL "
            f"and are reported as a collision that never happened."
        )


# --- AC3: the classification, and the argument behind it --------------------


def test_the_probe_is_classified_must_differ():
    """AC3, earned from the AC2 reading rather than by reflex.

    MEASURED on chromium under the delegated `--fingerprint=` patch
    (process.py:561): five seeds produced five DISTINCT digests, and removing
    the flag collapsed two different seeds onto ONE shared value — so the
    entropy is caused by the flag and the vector is genuinely seed-derived.
    SHARED would have been the false claim ("not seed-derived at all"), and it
    is not the safe default here either: SHARED probes are skipped by
    `compare_profiles` entirely, so the Firefox collision this ticket exists to
    expose would be recorded once and never reported again.

    The full numbers, both engines stated separately, are in `readings/
    ps135-2026-08-24/` and in the `variance` comment on the record itself."""
    assert _probe().variance == probes.INDEPENDENT
    assert PROBE_ID in probes.must_differ_ids()


# --- AC6 / Delta 3: the env-sensitivity decision ----------------------------


def test_the_probe_is_declared_environment_sensitive():
    """Delta 3, decided explicitly rather than left to default.

    This probe rasterises glyphs and a stroked arc and hashes the resulting
    PIXELS, so it sits strictly downstream of `fonts.measureText` — which is
    already listed and only reads advance WIDTHS. Everything that moves a text
    width between machines moves these bytes too, plus antialiasing, which a
    width never sees. A vector downstream of a listed one cannot be less
    host-dependent than it.

    Getting this wrong fails silently in both directions: omitted, the baseline
    reds on a different machine for a reason invisible in the artifact; listed
    wrongly, it costs one line of caveat. The asymmetry is why it is listed."""
    assert PROBE_ID in baseline.ENV_SENSITIVE_PROBES

    # And the artifact must SAY so, in the file a red diff is read from — not
    # only in the constant. This is the assertion `test_verify_baseline.py:110`
    # enforces as an equality, checked here from the recorded side.
    assert PROBE_ID in _snapshot()["provenance"]["env_sensitive_probes"]


# --- AC7: no existing probe's classification moved --------------------------


def test_no_other_probe_was_reclassified():
    """AC7. Adding a vector must not quietly re-grade an existing one — every
    POOLED probe keeps the recorded argument that made it POOLED, and the
    must-differ set grows by exactly this probe.

    Pinned as the exact expected set rather than a count: a count would go green
    if one probe were promoted while another was demoted in the same edit."""
    assert probes.must_differ_ids() == {
        "audio.digest",
        "webgl.readback",
        PROBE_ID,
    }
    assert {p.id for p in probes.probes_with_variance(probes.POOLED)} == {
        "navigator.hardwareConcurrency",
        "navigator.deviceMemory",
        "screen.geometry",
        "screen.devicePixelRatio",
        "webgl.unmasked",
    }
