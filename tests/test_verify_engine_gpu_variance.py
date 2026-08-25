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

import argparse
import json

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


def test_a_known_pool_arm_whose_bar_cannot_be_read_is_inconclusive_not_ok(
    monkeypatch,
):
    # THE BAR DISAPPEARING MUST NOT READ AS THE BAR BEING MET.
    #
    # fallback_pool_size SCRAPES the pool literals out of gpu_ext's emitted
    # source, so any reformatting there — a changed indent, a trailing comment,
    # a reflow — can make its regex miss and return 0. Before this was fixed,
    # bar_for then returned None, classify's TOO_NARROW branch was skipped for
    # want of a bar, and the arm fell through to `else: OK`.
    #
    # That silently downgrades this gate from "did it vary at LEAST as well as
    # the pool we gave up?" to "did it vary AT ALL?" — and the weaker question
    # is demonstrably insufficient: macos varies (2 distinct values) while two
    # profiles collide 76.9% of the time, so it passes "varied" and fails the
    # bar. Simulate the drift by making the scrape miss.
    monkeypatch.setattr(v, "fallback_pool_size", lambda arm: 0)

    # Readings that are otherwise perfectly healthy: 10 seeds, 5 evenly-used
    # identities. The ONLY thing wrong is that we could not read the bar.
    result = v.classify(_arm(["c0", "c1", "c2", "c3", "c4"] * 2))
    entry = result["per_arm"]["windows"]

    assert entry["verdict"] == "INCONCLUSIVE", (
        "a known-pool arm whose bar could not be read fell through to a PASS; "
        "we failed to look must never wear the code that means we looked and "
        "it was fine"
    )
    assert result["inconclusive"] == ["windows"]
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN
    # The detail must name the actual cause so the next reader fixes the scrape
    # rather than hunting the engine.
    assert "fallback_pool_size" in entry["detail"]


def test_a_missing_bar_does_not_downgrade_a_constant_arm_to_inconclusive(
    monkeypatch,
):
    # CONSTANT is a flat Level 2 breach, not a COMPARISON against the bar, so it
    # must still be a FINDING when the bar is unreadable. Ordering the missing-
    # bar check ahead of it would turn the severest verdict this gate has into
    # "we could not say" — trading a false pass for a lost finding.
    monkeypatch.setattr(v, "fallback_pool_size", lambda arm: 0)

    result = v.classify(_arm(["one-card"] * 10))
    assert result["per_arm"]["windows"]["verdict"] == "CONSTANT"
    assert result["findings"] == ["windows"]
    assert v.exit_code_for(result) == v.EXIT_FINDING


def test_an_arm_with_no_pool_by_design_is_not_forced_inconclusive(monkeypatch):
    # The two reasons fallback_pool_size returns 0 are different facts and only
    # one is a failure. An arm persona ships NO pool for has nothing to compare
    # against by construction, and must keep judging on what it can (distinct-
    # ness) rather than being reported as a broken run forever.
    assert v.has_known_pool("windows") is True
    assert v.has_known_pool("plan9") is False

    result = v.classify(_arm(["c0", "c1", "c2", "c3", "c4"] * 2, arm="plan9"))
    assert result["per_arm"]["plan9"]["verdict"] == "OK"
    assert v.exit_code_for(result) == v.EXIT_PASS


def test_has_known_pool_agrees_with_the_arms_the_scrape_can_actually_read():
    # has_known_pool is what promotes a 0 into a finding, so it must not claim
    # an arm the scrape cannot in fact read — that would make every run of a
    # healthy gate INCONCLUSIVE. Assert the two agree on the shipped source.
    for arm in ("windows", "macos", "linux", "android"):
        assert v.has_known_pool(arm) is True
        assert v.fallback_pool_size(arm) > 0, (
            f"{arm} is declared a known-pool arm but its size scrapes as 0 — "
            "the regex and the map have drifted apart"
        )


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


# --------------------------------------------------------------------------
# PS-176: the WIRING. Everything above tests the judgement; these test the
# things a scheduled job depends on to be able to go red.
# --------------------------------------------------------------------------

def test_selftest_passes_and_actually_exercises_all_three_red_paths():
    # The self-test is what the workflow runs BEFORE trusting a green from the
    # live check. Assert both halves of its contract: that it passes today, and
    # that what it exercises really is the three failure modes at the exit
    # codes they must produce. Asserting only "it exits 0" would let it degrade
    # into a check of nothing while still reporting success.
    assert v._cmd_selftest(argparse.Namespace(arm="windows")) == v.EXIT_PASS

    cases = v._selftest_cases("windows")
    assert [name for name, _, _ in cases] == [
        "CONSTANT", "TOO_NARROW", "INCONCLUSIVE"
    ]
    for name, readings, expected in cases:
        verdict = v.classify(readings)["per_arm"]["windows"]["verdict"]
        assert verdict == name, f"{name} case no longer produces a {name} verdict"
        assert v.exit_code_for(v.classify(readings)) == expected


def test_selftest_goes_red_when_the_judgement_stops_being_able_to_fail():
    # The point of the self-test is to catch a classify() that has lost its
    # teeth. Simulate exactly that — a judgement that calls everything OK — and
    # assert the self-test REFUSES to pass. Without this, the self-test itself
    # is a check that has never been shown to fail.
    original = v.classify
    try:
        v.classify = lambda readings: {           # type: ignore[assignment]
            "per_arm": {a: {"verdict": "OK"} for a in readings},
            "findings": [], "inconclusive": [], "arms_checked": sorted(readings),
        }
        assert v._cmd_selftest(argparse.Namespace(arm="windows")) == v.EXIT_FINDING
    finally:
        v.classify = original                     # type: ignore[assignment]


def test_selftest_defaults_to_the_arms_the_product_actually_defers_on():
    # Called with no --arm (which is how the workflow calls it), it must police
    # a real engine-authored arm rather than silently checking nothing.
    assert v._cmd_selftest(argparse.Namespace(arm="")) == v.EXIT_PASS


def test_replay_reproduces_the_verdict_without_taking_a_reading(tmp_path):
    # Replay is how a red run's evidence is re-read after the fact — the
    # forensic half, operator-invoked, called by no workflow. (What proves the
    # gate can go red is `selftest`, which the scheduled job runs first.)
    # It must NOT be able to take a measurement: if it ever reached the live
    # half, a replay would silently become a fresh reading of a different build.
    record = tmp_path / "reading.json"
    record.write_text(json.dumps({
        "readings": {"windows": {str(s): "Vendor | SAME" for s in SEEDS}}
    }), encoding="utf-8")

    def _explode(*a, **k):                        # pragma: no cover - must not run
        raise AssertionError("replay must never take a live reading")

    original = v.measure
    try:
        v.measure = _explode                      # type: ignore[assignment]
        out = tmp_path / "verdict.json"
        code = v._cmd_replay(
            argparse.Namespace(record=str(record), output=str(out))
        )
    finally:
        v.measure = original                      # type: ignore[assignment]

    assert code == v.EXIT_FINDING
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["result"]["per_arm"]["windows"]["verdict"] == "CONSTANT"


def test_replay_restores_integer_seeds_so_a_record_round_trips(tmp_path):
    # JSON keys are strings. A record written by `check --output` and read back
    # by `replay` must produce the same verdict as the in-memory readings did,
    # or the evidence file disagrees with the run that produced it.
    live = {"windows": {s: f"Vendor | GPU-{i}" for i, s in enumerate(SEEDS)}}
    record = tmp_path / "reading.json"
    record.write_text(json.dumps(v._record(live, v.classify(live))), encoding="utf-8")

    out = tmp_path / "verdict.json"
    assert v._cmd_replay(
        argparse.Namespace(record=str(record), output=str(out))
    ) == v.EXIT_PASS
    written = json.loads(out.read_text(encoding="utf-8"))
    assert list(written["readings"]["windows"]) == [str(s) for s in SEEDS]
    assert written["result"]["per_arm"]["windows"]["seeds_readable"] == len(SEEDS)


def test_replay_refuses_a_record_it_cannot_read_rather_than_passing(tmp_path):
    # A missing/corrupt/empty record established NOTHING. It must exit
    # CANNOT_RUN, never PASS — the same "we failed to look" discipline the
    # module applies to MIN_SEEDS, applied to the file.
    missing = tmp_path / "nope.json"
    assert v._cmd_replay(
        argparse.Namespace(record=str(missing), output="")
    ) == v.EXIT_CANNOT_RUN

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert v._cmd_replay(
        argparse.Namespace(record=str(corrupt), output="")
    ) == v.EXIT_CANNOT_RUN

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"readings": {}}), encoding="utf-8")
    assert v._cmd_replay(
        argparse.Namespace(record=str(empty), output="")
    ) == v.EXIT_CANNOT_RUN


def test_an_undersampled_replay_is_cannot_run_so_the_job_cannot_launder_it():
    # The wired path must not be able to turn INCONCLUSIVE into a pass. The
    # workflow relies on the exit code alone, so this is the property that
    # keeps a half-failed reading from reading as "the engine varies".
    readings = {"windows": {s: (f"Vendor | GPU-{i}" if i < 3 else None)
                            for i, s in enumerate(SEEDS)}}
    result = v.classify(readings)
    assert result["per_arm"]["windows"]["verdict"] == "INCONCLUSIVE"
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN
    assert v.exit_code_for(result) != v.EXIT_PASS


def test_the_record_carries_the_engine_build_so_a_finding_can_be_blocklisted():
    # The remedy for a finding is to name the bad tag in
    # policy.KNOWN_BAD_VERSIONS. A record that does not say WHICH BUILD it
    # measured cannot be acted on, so the build is part of the artifact.
    live = {"windows": {s: "Vendor | SAME" for s in SEEDS}}
    doc = v._record(live, v.classify(live))
    assert "engine_build" in doc
    assert doc["engine_build"]
    assert doc["engine_authored_arms"] == sorted(v.ENGINE_AUTHORED_IDENTITY_ARMS)
    assert doc["measured_at"]


def test_engine_build_reads_unknown_rather_than_empty_when_unresolvable(
    monkeypatch,
):
    # An empty string in the record would read like a value. Mirrors
    # snapshot.engine_build's contract: never raise, and say "unknown".
    import src.services.engine.updater as updater
    monkeypatch.setattr(updater, "current_version", lambda: "")
    assert v.engine_build() == "unknown"

    def _raise():
        raise RuntimeError("engine package not importable")

    monkeypatch.setattr(updater, "current_version", _raise)
    assert v.engine_build() == "unknown"
