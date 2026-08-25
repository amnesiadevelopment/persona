"""PS-161: the gate that makes an engine-authored arm safe to hold.

These tests exercise :func:`classify` — the whole judgement — including every
case where it must go RED. The live reading half needs the product's own engine
and cannot run on CI (``browser_chromium`` is provisioned nowhere, see
``ci.yml``), so the judgement is what is gated here, deliberately and
explicitly, rather than the gate being left with no automatic coverage at all.

The point of a gate is that it can FAIL. Most of what follows is therefore
negative cases: a gate only ever verified against good data is a check that
could not have failed, which this project does not count as coverage.
"""

import pytest

from src.services.browser import gpu_ext
from src.services.verify import engine_gpu_variance as v


# Ten readable seeds clears MIN_SEEDS (8) so the sample-size floor is not what
# is under test in the cases that are about the VERDICT.
SEEDS = (9001, 4242, 1337, 7, 101, 555, 2024, 31337, 86420, 12345)


def _arm(values, arm="windows"):
    """Build a readings dict for one arm from a list of identity strings."""
    return {arm: dict(zip(SEEDS, values))}


# --------------------------------------------------------------------------
# The bar itself
# --------------------------------------------------------------------------

def test_bar_is_read_from_the_shipped_pool_not_a_hardcoded_number():
    # The bar must track gpu_ext's actual pools, so editing a pool cannot leave
    # this gate policing a number nobody updated. Assert the RELATIONSHIP to the
    # source of truth rather than the literals: hardcoding 5/2/8 here would
    # reintroduce exactly the duplication this is built to avoid.
    import re

    for arm, name in (
        ("windows", "WIN_GPUS"), ("macos", "MAC_GPUS"),
        ("linux", "LINUX_GPUS"), ("android", "ANDROID_GPUS"),
    ):
        m = re.search(
            r"var " + name + r" = \[(.*?)\n  \];", gpu_ext._CONTENT_SCRIPT, re.S
        )
        expected = m.group(1).count("unmaskedVendor")
        assert expected > 0, f"could not read {name} from the extension source"
        assert v.fallback_pool_size(arm) == expected
        assert v.bar_for(arm) == pytest.approx(1.0 / expected)


def test_unknown_arm_has_no_bar_rather_than_a_zero_one():
    # None and 0.0 mean different things: "nothing to compare against" must not
    # collapse into "a bar of zero", which every reading would fail.
    assert v.bar_for("plan9") is None
    assert v.fallback_pool_size("plan9") == 0


# --------------------------------------------------------------------------
# Collision probability — the metric, and WHY it is not a distinct count
# --------------------------------------------------------------------------

def test_collision_probability_is_sensitive_to_skew_not_just_distinctness():
    # THE measurement that motivated this whole metric. Both samples hold
    # exactly TWO distinct values, so a distinct-count check scores them
    # identically and passes both. They are not remotely equivalent: the skewed
    # one is what the engine actually does on macOS (87/13 over 30 seeds) and it
    # links profiles far more often.
    even = ["A"] * 5 + ["B"] * 5
    skewed = ["A"] * 9 + ["B"] * 1
    assert len(set(even)) == len(set(skewed)) == 2
    assert v.collision_probability(even) == pytest.approx(0.50)
    assert v.collision_probability(skewed) == pytest.approx(0.82)
    assert v.collision_probability(skewed) > v.collision_probability(even)


def test_collision_probability_of_one_shared_value_is_total():
    assert v.collision_probability(["same"] * 10) == pytest.approx(1.0)


def test_collision_probability_of_all_distinct_falls_with_sample_size():
    assert v.collision_probability([str(i) for i in range(10)]) == pytest.approx(0.1)


# --------------------------------------------------------------------------
# GREEN — and it is the REAL measured data, not a hand-built ideal
# --------------------------------------------------------------------------

def test_real_measured_windows_readings_pass():
    # The actual layer-off readings taken 2026-08-25 against
    # fingerprint-chromium/148.0.7778.215 (readings/ps161-armsweep-2026-08-25).
    # This is the arm PS-161 hands to the engine, so the gate must be green on
    # the evidence the decision was made from — otherwise the change ships with
    # its own guard already red.
    result = v.classify(_arm([
        "Google Inc. (AMD) | ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x0000A7A0) …",
        "Google Inc. (NVIDIA) | ANGLE (NVIDIA, RTX 4060 Laptop (0x000028E0) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x000046A6) …",
        "Google Inc. (Intel) | ANGLE (Intel, UHD 620 (0x00003EA0) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x00009A49) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x00009A49) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x000046A6) …",
        "Google Inc. (Intel) | ANGLE (Intel, Iris Xe (0x00009A49) …",
        "Google Inc. (Intel) | ANGLE (Intel, Arc (0x00007D55) …",
    ]))
    assert result["per_arm"]["windows"]["verdict"] == "OK"
    assert result["findings"] == []
    assert v.exit_code_for(result) == v.EXIT_PASS


# --------------------------------------------------------------------------
# RED — the three ways this gate must fail
# --------------------------------------------------------------------------

def test_constant_identity_is_a_finding_and_names_the_remedy():
    # The severe form: every profile handed the same card. This is the exact
    # state the linux arm is in, and it is why linux is NOT engine-authored.
    result = v.classify(_arm(["Google Inc. (Google) | SwiftShader"] * 10))
    entry = result["per_arm"]["windows"]
    assert entry["verdict"] == "CONSTANT"
    assert result["findings"] == ["windows"]
    assert v.exit_code_for(result) == v.EXIT_FINDING
    # The message must tell an operator what to DO, not merely that it is bad —
    # the remedy is a one-line edit and the gate knows which one.
    assert "ENGINE_AUTHORED_IDENTITY_ARMS" in entry["detail"]


def test_engine_pool_narrower_than_our_own_is_a_finding():
    # THE case this gate exists for, and the one a distinct-count check misses.
    # Two values skewed 9/1 collide 82% of the time; persona's own WIN_GPUS
    # would collide 20%. The engine "varies" and is still strictly worse than
    # what was given up, so deferring is costing unlinkability.
    result = v.classify(_arm(["Apple M2"] * 9 + ["Apple M4"]))
    entry = result["per_arm"]["windows"]
    assert entry["verdict"] == "TOO_NARROW"
    assert entry["collision_probability"] > entry["bar_collision_probability"]
    assert v.exit_code_for(result) == v.EXIT_FINDING


def test_a_narrowing_that_still_beats_our_pool_is_not_a_finding():
    # The counterfactual that keeps the test above honest. This sample has
    # FEWER distinct values than the real measured data (6, not 7), so variety
    # genuinely fell — but it still collides less often than persona's own
    # 5-entry WIN_GPUS. The gate must not fire merely because variety dropped:
    # the bar is the pool we gave up, not the best reading ever seen. Without
    # this case, the test above would also pass for a gate that flags ANY
    # narrowing at all, which would send healthy arms back to our own layer.
    result = v.classify(_arm(["A", "B", "C", "D", "E", "F", "A", "B", "C", "D"]))
    entry = result["per_arm"]["windows"]
    assert entry["distinct_identities"] == 6
    assert entry["collision_probability"] == pytest.approx(0.18)
    assert entry["collision_probability"] < entry["bar_collision_probability"]
    assert entry["verdict"] == "OK"
    assert v.exit_code_for(result) == v.EXIT_PASS


def test_the_bar_is_an_inclusive_boundary_not_a_strict_one():
    # Exactly AT the bar must pass: matching the pool we gave up costs nothing,
    # so it is not a finding. Pinned because ">" vs ">=" here is a one-character
    # difference that silently flips a whole arm's verdict, and 5 evenly-used
    # identities over 10 seeds lands precisely on WIN_GPUS' 20%.
    result = v.classify(_arm(["A", "B", "C", "D", "E"] * 2))
    entry = result["per_arm"]["windows"]
    assert entry["collision_probability"] == pytest.approx(
        entry["bar_collision_probability"])
    assert entry["verdict"] == "OK"


# --------------------------------------------------------------------------
# "We failed to look" must never wear the code that means "it was fine"
# --------------------------------------------------------------------------

def test_too_few_seeds_is_inconclusive_and_is_not_a_pass():
    # A cheap two-seed run must not be able to certify the property: an
    # estimate from a handful of samples can clear the bar by luck.
    result = v.classify({"windows": {1: "A", 2: "B", 3: "C"}})
    assert result["per_arm"]["windows"]["verdict"] == "INCONCLUSIVE"
    assert result["per_arm"]["windows"]["collision_probability"] is None
    assert v.exit_code_for(result) != v.EXIT_PASS
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


def test_all_cells_unreadable_is_inconclusive_not_constant():
    # A run where nothing could be read must not present as "every profile got
    # the same value" — an unreadable cell and a colliding cell are different
    # findings, and merging them would let a broken run read as a narrow pool
    # (or, worse, a broken run read as a pass).
    result = v.classify({"windows": {s: None for s in SEEDS}})
    assert result["per_arm"]["windows"]["verdict"] == "INCONCLUSIVE"
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


def test_unreadable_cells_are_excluded_from_the_statistics():
    # Nine good readings plus one failure is nine samples, not ten, and the
    # None must not be counted as an identity value in its own right.
    values = dict(zip(SEEDS, [str(i) for i in range(9)] + [None]))
    result = v.classify({"windows": values})
    entry = result["per_arm"]["windows"]
    assert entry["seeds_requested"] == 10
    assert entry["seeds_readable"] == 9
    assert None not in entry["identities"]
    assert entry["distinct_identities"] == 9


def test_no_engine_authored_arms_is_not_a_silent_pass():
    # An empty check established nothing, so it must not exit 0. This is what
    # stops a misconfiguration ("we checked zero arms, all green!") from
    # reading as coverage.
    result = v.classify({})
    assert result["arms_checked"] == []
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


# --------------------------------------------------------------------------
# Wiring: the gate polices what the product actually ships
# --------------------------------------------------------------------------

def test_the_gate_polices_exactly_the_arms_the_product_defers_on():
    # The gate imports the set from gpu_ext rather than restating it, so an arm
    # can never be handed to the engine without this gate policing it. Assert
    # they are the SAME object's contents, not two lists that happen to agree.
    assert v.ENGINE_AUTHORED_IDENTITY_ARMS is gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS
    # And every arm in it must be one this gate can compute a bar for —
    # otherwise it would be policed against no standard at all.
    for arm in v.ENGINE_AUTHORED_IDENTITY_ARMS:
        assert v.bar_for(arm) is not None, (
            f"{arm} is engine-authored but has no fallback pool to measure "
            "against, so the gate could not tell a narrowing from a pass"
        )


def test_every_engine_authored_arm_is_reported_even_when_green():
    # A gate that prints only its failures cannot be read as evidence that the
    # green arms were looked at.
    result = v.classify(_arm([str(i) for i in range(10)]))
    text = v.format_result(result)
    assert "windows" in text
    assert "OK" in text


def test_format_says_so_when_there_is_nothing_to_police():
    # An empty run must SAY it was empty rather than printing a bare heading
    # that reads like a clean bill of health.
    text = v.format_result(v.classify({}))
    assert "nothing to police" in text.lower()
