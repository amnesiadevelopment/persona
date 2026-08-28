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
import pathlib
import shutil
import subprocess
import sys

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
    #
    # THE SOURCE OF TRUTH IS NOW PYTHON DATA (PS-190). This used to re-derive
    # the count by running the same regex over the JS template that the
    # implementation ran — which made the test agree with the implementation by
    # construction, including when the regex was wrong for both. The pools are
    # now `gpu_ext.GpuEntry` records the JS is rendered from, so this reads the
    # list itself.
    for arm, name in (
        ("windows", "WIN_GPUS"), ("macos", "MAC_GPUS"),
        ("linux", "LINUX_GPUS"), ("android", "ANDROID_GPUS"),
    ):
        expected = len(gpu_ext.GPU_POOLS[name])
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
    # fallback_pool_size reads the pool's size out of gpu_ext.GPU_POOLS — the
    # tagged Python records the emitted JS is rendered from — so it returns 0
    # when the arm's name in _POOL_VAR_FOR_ARM is absent from that registry, or
    # names a pool that is empty. (Until PS-190 it SCRAPED the emitted JS with a
    # regex and a reflow of the literals could make it miss; that failure mode
    # is gone, but the contract below is deliberately KEPT — a name missing from
    # the registry still has to read as "we failed to look".) Before this was
    # fixed, bar_for then returned None, classify's TOO_NARROW branch was
    # skipped for want of a bar, and the arm fell through to `else: OK`.
    #
    # That silently downgrades this gate from "did it vary at LEAST as well as
    # the pool we gave up?" to "did it vary AT ALL?" — and the weaker question
    # is demonstrably insufficient: macos varies (2 distinct values) while two
    # profiles collide 76.9% of the time, so it passes "varied" and fails the
    # bar. Simulate the drift by making the size unreadable.
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
    # The detail must name the actual cause so the next reader repairs the
    # registry drift rather than hunting the engine. Pin the two symbols an
    # operator would have to look at — the registry the size is read from, and
    # the map that claims this arm has a pool — because the REMEDY is what this
    # message exists to deliver. Naming a symbol that no longer exists sends
    # them to fix code that is not there (PS-190 code review).
    detail = entry["detail"]
    assert "gpu_ext.GPU_POOLS" in detail, detail
    assert "_POOL_VAR_FOR_ARM" in detail, detail
    # And it must NOT resurrect the pre-PS-190 remedy: there is no regex and no
    # scrape in fallback_pool_size any more.
    assert "regex" not in detail, detail
    assert "scrape" not in detail, detail


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


def test_has_known_pool_agrees_with_the_arms_whose_size_can_actually_be_read():
    # has_known_pool is what promotes a 0 into a finding, so it must not claim
    # an arm whose size cannot in fact be read — that would make every run of a
    # healthy gate INCONCLUSIVE. Assert the two agree on the shipped source.
    for arm in ("windows", "macos", "linux", "android"):
        assert v.has_known_pool(arm) is True
        assert v.fallback_pool_size(arm) > 0, (
            f"{arm} is declared a known-pool arm but its size reads as 0 — "
            "_POOL_VAR_FOR_ARM and gpu_ext.GPU_POOLS have drifted apart"
        )


def test_the_pool_size_is_reported_per_generation_not_as_one_number():
    # THE DEFECT THIS PINS. `fallback_pool_size` scraped `unmaskedVendor`
    # occurrences and never looked at `since:`, so it counted entries that NO
    # EXISTING PROFILE CAN BE PICKED ONTO. After PS-183 widened MAC_GPUS from 2
    # to 11 with nine `since=1` entries, that made bar_for("macos") report 9.1%
    # while every macOS profile that already existed sat in a 2-entry pool
    # colliding at 50.0% — the helper advertised unlinkability those profiles
    # do not have.
    #
    # Asserted as a RELATIONSHIP to gpu_ext's own filter rather than against
    # the literals 2 and 11: hardcoding those would make this test restate the
    # pool instead of checking the mechanism, and it would go red on the next
    # legitimate widening for no reason.
    from types import SimpleNamespace

    from src.models.hardware_generation import (
        CURRENT_HARDWARE_GENERATION,
        visible_entries,
    )

    for arm in ("windows", "macos", "linux", "android"):
        sizes = v.pool_sizes_by_generation(arm)
        assert sizes, f"{arm}: the per-generation scrape returned nothing"

        # Every reported size must equal what gpu_ext's OWN filter yields.
        sinces = v._pool_entry_generations(arm)
        entries = [SimpleNamespace(since=s) for s in sinces]
        for gen, n in sizes.items():
            assert n == len(visible_entries(entries, gen)), (
                f"{arm} gen {gen}: reported {n} entries, but the shipped "
                f"filter yields {len(visible_entries(entries, gen))}"
            )

        # The default must be the CURRENT generation's pool, not the raw list.
        assert v.fallback_pool_size(arm) == len(
            visible_entries(entries, CURRENT_HARDWARE_GENERATION)
        )

    # And the split must actually be VISIBLE on a widened arm — a helper that
    # merely accepts a generation argument while reporting one number would
    # pass every assertion above.
    macos = v.pool_sizes_by_generation("macos")
    assert len(macos) > 1, (
        "macos was widened across a generation boundary by PS-183, so its "
        "pool has more than one size; a single entry here means the `since:` "
        "scrape stopped seeing the tags"
    )
    assert macos[0] < macos[max(macos)], (
        "the older generation must see FEWER entries than the newer one"
    )


def test_an_existing_profiles_smaller_pool_is_not_hidden_behind_the_new_bar():
    # The number the gate compares against is the NEWEST generation's, which is
    # the right question for "should we defer this arm?" but is NOT a claim
    # about the installed base. That distinction has to survive in the OUTPUT,
    # or the only record of it is a docstring nobody reads at 3am.
    result = v.classify(_arm(["c0", "c1", "c2", "c3", "c4"] * 2, arm="macos"))
    entry = result["per_arm"]["macos"]

    # The split rides the reading, so an ARCHIVED record carries it too.
    assert entry["pool_sizes_by_generation"] == v.pool_sizes_by_generation("macos")

    # And it is printed, with the older generation's worse figure spelled out
    # rather than left for the reader to divide.
    text = v.format_result(
        {"arms_checked": ["macos"], "per_arm": {"macos": entry}}
    )
    assert "gen 0: 2 entries, 50.0%" in text, text
    assert "NOT one size" in text, text


def test_a_pinned_entry_declines_to_report_a_generation_split_it_never_saw():
    """An archived record must not carry TODAY's generation map. PS-239.

    The two halves are asserted together because either alone is satisfiable
    by a broken implementation: half 1 asserts the LIVE path still carries the
    split, so it alone passes if pinning did nothing; half 2 asserts the
    PINNED path carries none, so it alone passes if the field were dropped for
    every caller. Only together do they pin the DIFFERENCE between the paths.

    ⚠️ WHY `None` AND NOT `{0: pinned}`. The sweep records witness `k` ALONE —
    they carry no `pool_sizes_by_generation`, no `since` and no `generation`
    field of any kind — so the split is not recoverable from the evidence.
    Synthesising `{0: k}` would FABRICATE a witness, asserting the pool held
    exactly one generation numbered 0; PS-183 introduced the generation tags
    in the SAME edit that widened `MAC_GPUS`, so a record taken before it
    plausibly had no generation concept at all. `None` says "the record did
    not witness this", which is the only claim the evidence supports — the
    same "we failed to look is not we looked and it was fine" discipline this
    module applies to a missing bar.
    """
    readings = _arm(["c0", "c1"] * 12, arm="macos")

    # HALF 1 — the LIVE path is unchanged and still carries the real split, so
    # this pins a difference between the two paths rather than a deletion.
    live = v.classify(readings)["per_arm"]["macos"]
    assert live["pool_sizes_by_generation"] == v.pool_sizes_by_generation(
        "macos"
    ), "the unpinned path must still report the live generation split"
    assert len(live["pool_sizes_by_generation"]) > 1, (
        "fixture no longer exercises a widened arm — macos is expected to "
        "carry more than one generation (PS-183 widened it 2 -> 11)"
    )

    # HALF 2 — the PINNED path reports no split, and specifically does not
    # report the live one it would have read a moment ago.
    pinned = v.classify(readings, {"macos": 2})["per_arm"]["macos"]
    assert pinned["pinned_pool_size"] == 2
    assert pinned["fallback_pool_size"] == 2
    assert pinned["pool_sizes_by_generation"] is None, (
        "a pinned entry is reporting a generation map; an archived reading "
        f"cannot witness one: {pinned['pool_sizes_by_generation']!r}"
    )

    # And the renderer must tolerate it — printing no split rather than
    # crashing or inventing one.
    text = v.format_result(
        {"arms_checked": ["macos"], "per_arm": {"macos": pinned}}
    )
    assert "NOT one size" not in text, text


def test_a_single_generation_arm_does_not_print_a_meaningless_split():
    # Printing "gen 0: 5 entries" on every unwidened arm would train the reader
    # to skip the line — and this line only earns its space by being rare.
    entry = v.classify(_arm(["c0", "c1", "c2", "c3", "c4"] * 2))["per_arm"][
        "windows"
    ]
    assert len(entry["pool_sizes_by_generation"]) == 1
    text = v.format_result(
        {"arms_checked": ["windows"], "per_arm": {"windows": entry}}
    )
    assert "NOT one size" not in text, text


def test_a_pool_that_cannot_be_read_reads_as_failed_to_look_not_as_empty(
    monkeypatch,
):
    # An unreadable pool must fail SAFE, into this module's standing "we failed
    # to look" answer — never into a small pool (which would LOWER the bar and
    # let a narrow engine pass) or a large one (which would raise it).
    #
    # ⚠️ WHAT THIS TEST USED TO EXERCISE NO LONGER EXISTS, and the replacement
    # is deliberately a DIFFERENT trigger for the SAME contract. It used to
    # corrupt the JS in `gpu_ext._CONTENT_SCRIPT` so the two regexes
    # `_pool_entry_generations` ran (per-entry, and a cruder `unmaskedVendor`
    # tally) disagreed about the entry count. PS-190 lifted the pools out of
    # the JS into `gpu_ext.GPU_POOLS` as real objects and PS-183 read the tags
    # off `entry.since` directly, so there are no regexes left to drift and
    # mangling the JS text now proves nothing about this function. Rewriting
    # the fixture to keep the old trigger alive would have pinned a scrape that
    # is gone; the contract is what survives, so the contract is what is pinned.
    #
    # The surviving way to fail to look: the arm is registered in
    # `_POOL_VAR_FOR_ARM` but that name is absent from `GPU_POOLS` — the two
    # having drifted apart, which is exactly the "we failed to look" case the
    # 0-means-two-things contract exists for.
    assert v._POOL_VAR_FOR_ARM["android"] == "ANDROID_GPUS"
    pools = {k: val for k, val in gpu_ext.GPU_POOLS.items() if k != "ANDROID_GPUS"}
    assert "ANDROID_GPUS" not in pools, "fixture no longer matches the source"
    monkeypatch.setattr(gpu_ext, "GPU_POOLS", pools)

    # has_known_pool still claims the arm — that is the point of the contract:
    # a 0 here means WE FAILED TO LOOK, not "this arm ships no pool".
    assert v.has_known_pool("android") is True
    assert v._pool_entry_generations("android") is None
    assert v.pool_sizes_by_generation("android") == {}
    assert v.fallback_pool_size("android") == 0
    assert v.bar_for("android") is None

    # ...and that 0 must reach the verdict as INCONCLUSIVE, not as a pass.
    result = v.classify(_arm(["c0", "c1", "c2", "c3", "c4"] * 2, arm="android"))
    assert result["per_arm"]["android"]["verdict"] == "INCONCLUSIVE"
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


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


# The tag is HARDCODED, and deliberately cannot be this machine's. A fixture
# built with `v._record(...)` on the machine that then replays it makes source
# and local provenance identical BY CONSTRUCTION, so the substitution this
# guards against is invisible — the probe computes its expected value from the
# thing under test and agrees with itself. That confound is what let the defect
# ship: the neighbouring round-trip test uses it and stayed green throughout.
FOREIGN_BUILD = "999.0.NOT-THIS-MACHINES-BUILD"
FOREIGN_MEASURED_AT = "2026-08-20T06:40:00+00:00"
# Deliberately a SUPERSET of the single arm the product defers on today, so it
# cannot equal the local set on any machine that ships the current constant.
# The previous value here was ["windows"] — identical to the local set, which
# is the SAME confound the comment above warns about, applied to the one field
# the fixture did not guard. That is why `engine_authored_arms` kept being
# regenerated while every test around it stayed green.
FOREIGN_ARMS = ["linux", "macos", "windows"]


def _foreign_record(values, *, arms=None) -> dict:
    """A record as some OTHER machine's `check --output` would have written it."""
    return {
        "measured_at": FOREIGN_MEASURED_AT,
        "engine_build": FOREIGN_BUILD,
        "engine_authored_arms": FOREIGN_ARMS if arms is None else arms,
        "readings": {"windows": {str(s): v_ for s, v_ in zip(SEEDS, values)}},
        "result": {"verdict": "FINDING"},
    }


def test_replay_preserves_the_measuring_machines_build_rather_than_restamping_it(
    tmp_path,
):
    # The documented remedy for a red run is to name the bad tag in
    # policy.KNOWN_BAD_VERSIONS. `replay` is the forensic tool for reading that
    # artifact after the runner that measured it was destroyed — on a machine
    # with no engine at all. If it re-derives the build, it does not blank the
    # field, it substitutes a PLAUSIBLE WRONG tag: the operator blocklists the
    # replaying machine's build, refuses a good build, and leaves the bad one
    # installing hourly. Same failure shape as the breach this module catches.
    assert v.engine_build() != FOREIGN_BUILD, (
        "fixture must not collide with the local build, or this test cannot fail"
    )

    record = tmp_path / "from-the-runner.json"
    record.write_text(
        json.dumps(_foreign_record(["Vendor | SAME"] * len(SEEDS))),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    assert v._cmd_replay(
        argparse.Namespace(record=str(record), output=str(out))
    ) == v.EXIT_FINDING

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_build"] == FOREIGN_BUILD
    assert written["measured_at"] == FOREIGN_MEASURED_AT
    # The re-verdict's own moment is recorded SEPARATELY, so the moment of
    # measurement and the moment of re-reading cannot be confused.
    assert written["replayed_at"]
    assert written["replayed_at"] != FOREIGN_MEASURED_AT


def test_replay_preserves_the_build_even_where_no_engine_is_installed(
    tmp_path, monkeypatch
):
    # The environment `replay`'s own docstring describes: "a machine with no
    # engine, no display and no runner". There engine_build() resolves
    # "unknown" — so a re-derived record would report `engine_build: "unknown"`
    # for a run that knew its tag perfectly well. This is the case that makes
    # the whole artifact unactionable, so it is asserted directly.
    monkeypatch.setattr(v, "engine_build", lambda: "unknown")

    record = tmp_path / "from-the-runner.json"
    record.write_text(
        json.dumps(_foreign_record(["Vendor | SAME"] * len(SEEDS))),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    v._cmd_replay(argparse.Namespace(record=str(record), output=str(out)))

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_build"] == FOREIGN_BUILD
    assert written["engine_build"] != "unknown"


def test_check_still_stamps_THIS_machine_because_it_is_the_one_measuring(
    monkeypatch,
):
    # The mirror image, so the fix cannot over-apply. `check` IS the measuring
    # machine, so local values are the truth there and must still be recorded;
    # only `replay` inherits. A record with no source carries no `replayed_at`.
    monkeypatch.setattr(v, "engine_build", lambda: "148.0.LOCAL")
    live = {"windows": {s: f"Vendor | GPU-{i}" for i, s in enumerate(SEEDS)}}

    doc = v._record(live, v.classify(live))
    assert doc["engine_build"] == "148.0.LOCAL"
    assert "replayed_at" not in doc


def test_a_record_predating_the_provenance_fields_still_resolves_them(tmp_path):
    # A source record missing/blank provenance must not propagate an empty
    # string that reads like a value. Falsy source values fall back to locally
    # derived ones — the only case where re-derivation is correct.
    record = tmp_path / "old.json"
    record.write_text(
        json.dumps({
            "engine_build": "",
            "readings": {"windows": {str(s): "Vendor | SAME" for s in SEEDS}},
        }),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    v._cmd_replay(argparse.Namespace(record=str(record), output=str(out)))

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_build"] == v.engine_build()
    assert written["engine_build"]
    assert written["measured_at"]


def test_replay_preserves_the_measured_arm_set_rather_than_this_machines(
    tmp_path,
):
    # The third provenance field, and the one that survived two rounds of
    # fixing the other two. It records WHICH ARMS WERE DEFERRING WHEN THE
    # BREACH WAS MEASURED — exactly the context needed to act on a red run.
    #
    # It is load-bearing precisely BECAUSE of the documented remedy: a red run
    # tells the operator to "remove it from ENGINE_AUTHORED_IDENTITY_ARMS". So
    # the local set is EXPECTED to differ from the archived one by the time
    # anyone replays the artifact. Re-deriving it there produces a re-verdict
    # asserting that NO arm was engine-authored while still carrying a Level 2
    # breach reading for `windows` — an internally self-contradictory evidence
    # base, produced by the tool whose job is to preserve the record, and made
    # worse precisely because the operator did the right thing.
    assert sorted(v.ENGINE_AUTHORED_IDENTITY_ARMS) != FOREIGN_ARMS, (
        "fixture must not collide with the local arm set, or this cannot fail"
    )

    record = tmp_path / "from-the-runner.json"
    record.write_text(
        json.dumps(_foreign_record(["Vendor | SAME"] * len(SEEDS))),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    assert v._cmd_replay(
        argparse.Namespace(record=str(record), output=str(out))
    ) == v.EXIT_FINDING

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_authored_arms"] == FOREIGN_ARMS
    assert written["engine_authored_arms"] != sorted(
        v.ENGINE_AUTHORED_IDENTITY_ARMS
    )


def test_replay_preserves_an_arm_set_the_operator_has_since_emptied(
    tmp_path, monkeypatch
):
    # The end state of the documented remedy, and the reason this field must
    # NOT use the `or` pattern the other two provenance fields use: an EMPTY
    # list is a LEGITIMATE measured value here ("no arm was deferring"), and it
    # is falsy. `or` would silently substitute this machine's set for it,
    # reintroducing the identical defect for the one case where the archive and
    # the live constant differ the most.
    monkeypatch.setattr(v, "ENGINE_AUTHORED_IDENTITY_ARMS", frozenset({"windows"}))

    record = tmp_path / "from-the-runner.json"
    record.write_text(
        json.dumps(_foreign_record(["Vendor | SAME"] * len(SEEDS), arms=[])),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    v._cmd_replay(argparse.Namespace(record=str(record), output=str(out)))

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_authored_arms"] == []


def test_a_record_predating_the_arm_set_field_falls_back_to_local(tmp_path):
    # The mirror image, so the membership test cannot over-apply: a genuinely
    # ABSENT key (a record written before the field existed) must still resolve
    # to something rather than to null. Absence and a measured empty list are
    # different claims, and only the second one is preserved.
    record = tmp_path / "old.json"
    record.write_text(
        json.dumps({
            "readings": {"windows": {str(s): "Vendor | SAME" for s in SEEDS}},
        }),
        encoding="utf-8",
    )

    out = tmp_path / "re-verdict.json"
    v._cmd_replay(argparse.Namespace(record=str(record), output=str(out)))

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["engine_authored_arms"] == sorted(
        v.ENGINE_AUTHORED_IDENTITY_ARMS
    )


# ---------------------------------------------------------------------------
# The CLASS, not the instances.
#
# Rounds 3, 4 and 5 of PS-176 each cost a full review round to the SAME defect
# wearing a different field name: `_record` re-derived a provenance value from
# the REPLAYING machine, and every test in this file stayed green because each
# one names the fields that already exist. A test that names three fields
# leaves the fourth free.
#
# The guard against the next one was, until this test, a sentence in the
# `_record` docstring. This ticket exists because the standard ruled for this
# module was "not a note in a docstring, not a manual step — a check that runs
# and can go red", so the provenance contract was being held to a weaker
# standard than the thing the module was written to enforce.
#
# Proven by injection rather than asserted: adding a 4th locally-derived field
# (`"measured_on_platform": sys.platform`) to `_record` leaves all 37 of the
# tests above GREEN, while `replay` of a foreign record stamps it "linux".
# The test below goes RED on that same injection, naming the fix in its message.
# ---------------------------------------------------------------------------


def test_replay_partitions_EVERY_key_so_a_new_field_cannot_be_added_unclassified(
    tmp_path,
):
    """Pins the CLASS, not the three fields the class has produced so far.

    The load-bearing line is the ``set(written) ==`` equality: it fails on a
    key ADDED, not merely on the three that exist today. That is what forces a
    maintainer to classify a new field AT THE MOMENT THEY ADD IT, rather than
    discovering a round later that `replay` has been quietly overwriting it.
    """
    src_doc = {
        # Every field hardcoded FOREIGN — never derived from this machine.
        # Deriving the expected value from the local resolver is the confound
        # that hid this defect for two rounds: the fixture's arms were
        # ["windows"], identical to the local set by construction, so a
        # regenerated value was indistinguishable from a preserved one.
        "measured_at": "2026-08-20T06:40:00+00:00",
        "engine_build": "999.0.NOT-THIS-MACHINES-BUILD",
        "engine_authored_arms": ["linux", "macos", "windows"],
        "readings": {"windows": {str(s): "Vendor | SAME" for s in SEEDS}},
        "result": {"verdict": "STALE-FROM-THE-ARCHIVE"},
    }
    record = tmp_path / "foreign.json"
    record.write_text(json.dumps(src_doc), encoding="utf-8")
    out = tmp_path / "re.json"

    v._cmd_replay(argparse.Namespace(record=str(record), output=str(out)))
    written = json.loads(out.read_text(encoding="utf-8"))

    # Describes the MEASURING machine -> must survive the replay untouched.
    SOURCE_PRESERVED = {"measured_at", "engine_build", "engine_authored_arms"}
    # Re-judged on purpose: reproducing the verdict IS the point of a replay.
    RECOMPUTED = {"result"}
    # Carried through from the source document, not derived.
    PARAMETER = {"readings"}
    # The replay's OWN moment, deliberately distinct from `measured_at`.
    REPLAY_STAMPED = {"replayed_at"}

    assert set(written) == (
        SOURCE_PRESERVED | RECOMPUTED | PARAMETER | REPLAY_STAMPED
    ), (
        "`_record` writes a key this test does not classify. Every field that "
        "describes the MEASURING machine must be source-preserved; classify it "
        "here and in _record, or replay will silently stamp it with the "
        "REPLAYING machine's value -- the defect class of PS-176 rounds 3, 4 "
        "and 5, each of which reached main green."
    )

    for key in SOURCE_PRESERVED:
        assert written[key] == src_doc[key], (
            f"{key} was re-derived from the REPLAYING machine. The remedy for "
            f"a red run is to name the measured build in "
            f"policy.KNOWN_BAD_VERSIONS; a substituted value is plausible and "
            f"wrong, so the operator blocklists the wrong build."
        )

    # The other half of the partition: `result` must NOT be inherited, or
    # `replay` would echo the archived verdict instead of re-judging it.
    assert written["result"] != src_doc["result"]
    assert written["result"]["per_arm"]["windows"]["verdict"] == "CONSTANT"

    # The replay's own stamp is present and cannot be mistaken for the
    # measurement's moment.
    assert written["replayed_at"] not in (None, "", src_doc["measured_at"])


# --------------------------------------------------------------------------
# PS-192: a TRUNCATED run must not be able to report like a complete one
# --------------------------------------------------------------------------
#
# The composition this closes has three parts, and none of them is wrong alone:
#
#   1. a process leak exhausts the machine mid-sweep;
#   2. an exhausted launch degrades SILENTLY into a contentless
#      TargetClosedError, so the later seeds come back None;
#   3. `classify` CORRECTLY excludes unreadable cells from the statistics.
#
# Together: the later seeds are dropped and the verdict is computed from a
# position-biased subset that comes back looking clean. Nothing in the output
# said the sample had been truncated.
#
# The tests below are MUTATION tests, deliberately: each takes a complete
# fixture, nulls part of it, and asserts the OUTPUT CHANGES. That is the shape
# `readings/ps177-2026-08-25/derive.py:372 coverage_section()` failed — it
# hardcoded "all four GPU arms returned 24/24 readable seeds", took no records
# at all, and still printed 24/24 after a reviewer nulled 12 of 24 android
# readings. A completeness claim that cannot go false is worse than none.

def _complete_24():
    """24 seeds, 6 evenly-used identities: a genuinely clean, complete arm.

    SIX, not five, and the number is load-bearing. Five evenly-used identities
    over 24 seeds collide 20.14% of the time — a hair ABOVE windows' 20.0% bar
    — so that fixture is a TOO_NARROW finding, not the clean run these tests
    need as their baseline. Six collides 16.7% and is a real pass. Measured
    with `collision_probability`, not eyeballed: the truncation gate must be
    demonstrated on a run that would otherwise be GREEN, or the test cannot
    tell "lost the pass to truncation" from "never had a pass to lose".
    """
    seeds = tuple(range(9000, 9024))
    return {"windows": {s: f"card{i % 6}" for i, s in enumerate(seeds)}}


def test_a_complete_run_says_so_and_the_numbers_come_from_the_records():
    result = v.classify(_complete_24())
    cov = result["completeness"]

    assert cov["complete"] is True
    assert cov["seeds_requested"] == 24
    assert cov["seeds_readable"] == 24
    assert cov["seeds_unreadable"] == 0
    assert cov["arms_truncated"] == []
    # The stated sample size must be the REAL one, not a constant: this is the
    # exact assertion derive.py's hardcoded sentence would have passed while
    # lying, so it is pinned against the records it claims to describe.
    assert cov["seeds_requested"] == sum(
        e["seeds_requested"] for e in result["per_arm"].values()
    )
    assert v.exit_code_for(result) == v.EXIT_PASS


def test_truncating_a_fixture_changes_the_output_and_removes_the_pass():
    # THE MUTATION. Same arm, same identities, same everything — except the
    # last 13 of 24 seeds could not be read, which is exactly what an
    # exhausted machine produces. 11 readable still clears MIN_SEEDS (8), so
    # WITHOUT this gate the arm judges and reports like a normal run.
    complete = _complete_24()
    clean = v.classify(complete)

    truncated = {"windows": dict(complete["windows"])}
    for i, seed in enumerate(sorted(truncated["windows"])):
        if i >= 11:  # the seeds that ran LAST are the ones that die
            truncated["windows"][seed] = None
    result = v.classify(truncated)
    cov = result["completeness"]

    assert cov["complete"] is False, (
        "a run that read 11 of 24 seeds reported as complete"
    )
    assert (cov["seeds_readable"], cov["seeds_requested"]) == (11, 24)
    assert cov["arms_truncated"] == ["windows"]

    # THE TICKET'S BAR, stated exactly: a run that read 11 of 24 seeds must not
    # report like one that read 24.
    assert v.format_result(result) != v.format_result(clean)
    assert v.exit_code_for(result) != v.EXIT_PASS
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


def test_the_printed_output_names_the_truncation_rather_than_burying_it():
    # A machine-readable flag nobody prints is not visibility. The operator
    # reading stdout must be told, in words, that the sample was partial.
    truncated = {"windows": {s: ("A" if i < 11 else None)
                             for i, s in enumerate(range(9000, 9024))}}
    text = v.format_result(v.classify(truncated))

    assert "TRUNCATED" in text
    assert "11" in text and "24" in text
    # And the clean case must state its own completeness rather than staying
    # silent — an absence a reader learns to skim past is not a signal.
    assert "COMPLETE" in v.format_result(v.classify(_complete_24()))


def test_completeness_is_computed_per_arm_not_from_a_healthy_total():
    # One healthy arm must NOT be able to mask a truncated one. Summing first
    # and judging the total is how a 24/24 windows arm hides a 12/24 android
    # arm — the precise shape of the defect the reviewer found in derive.py.
    mixed = {
        "windows": {s: f"card{i % 5}" for i, s in enumerate(range(9000, 9024))},
        "android": {s: (f"card{i % 5}" if i < 12 else None)
                    for i, s in enumerate(range(8000, 8024))},
    }
    result = v.classify(mixed)
    cov = result["completeness"]

    assert cov["complete"] is False
    assert cov["arms_truncated"] == ["android"], (
        "the truncated arm was not named; a caller cannot re-run what it "
        "cannot identify"
    )
    assert v.exit_code_for(result) != v.EXIT_PASS


def test_truncation_does_not_erase_a_finding_it_only_removes_the_pass():
    # Ordering matters and is asserted rather than assumed. Truncation can HIDE
    # a defect; it can never INVENT one — so a partial run that still caught a
    # CONSTANT arm must keep reporting that finding, not downgrade it to "we
    # could not say".
    truncated = {"windows": {s: ("one-card" if i < 11 else None)
                             for i, s in enumerate(range(9000, 9024))}}
    result = v.classify(truncated)

    assert result["per_arm"]["windows"]["verdict"] == "CONSTANT"
    assert result["findings"] == ["windows"]
    assert result["completeness"]["complete"] is False
    assert v.exit_code_for(result) == v.EXIT_FINDING


def test_classify_still_excludes_unreadable_cells_from_the_statistics():
    # DoD #4 GUARD. The remedy for the truncation blindness must NOT be to
    # start counting None as a value — that exclusion is CORRECT and the ticket
    # says so explicitly. This pins that the fix added a DISCLOSURE beside the
    # statistics rather than changing them.
    values = dict(zip(SEEDS, [str(i) for i in range(9)] + [None]))
    entry = v.classify({"windows": values})["per_arm"]["windows"]

    assert entry["seeds_requested"] == 10
    assert entry["seeds_readable"] == 9
    assert None not in entry["identities"]
    assert entry["distinct_identities"] == 9
    # The statistics are computed over the NINE readable cells, unchanged.
    assert entry["collision_probability"] == pytest.approx(1 / 9)


def test_an_all_unreadable_run_is_reported_as_truncated_too():
    # The degenerate end of the same axis: nothing was read at all. It was
    # already INCONCLUSIVE via MIN_SEEDS, but it must ALSO be visibly truncated
    # — otherwise "we read none of it" is indistinguishable from "we read all
    # of it and every cell was fine" in the completeness line.
    result = v.classify({"windows": {s: None for s in SEEDS}})
    cov = result["completeness"]

    assert cov["complete"] is False
    assert (cov["seeds_readable"], cov["seeds_requested"]) == (0, 10)
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN


# --------------------------------------------------------------------------
# The archived reading script must measure the tree it SITS IN
# --------------------------------------------------------------------------
#
# This one is pinned by VENUE, not by inspection, and that is the whole point.
# `readings/ps183-2026-08-26/measure.py` produced this ticket's two headline
# figures. It once resolved the tree under measurement from a hard-coded
# absolute path, and that defect is invisible to reading the code: the line is
# short, obvious and looks correct. It is also invisible to a green suite run
# in the authoring container, because there the fixed path happens to BE the
# right tree.
#
# It only becomes visible when the script is run somewhere else — and then it
# does not crash, which is the dangerous part. It imports whatever tree lives
# at the fixed path, succeeds, and prints a confident figure for a pool it
# never looked at. Demonstrated: placed in a 2-entry worktree the old line
# reported 21.9% over five cards, four of which did not exist in that tree;
# the corrected line reported the true 53.1% over the two that did.
#
# A reading that agrees with you no matter which tree it stands in is a
# fabricated confirmation, and it is the one output a `readings/` directory
# must never produce — the reason PS-16 commits readings at all is the
# "re-derive, never edit-to-match" rule.
#
# So the assertion is not "line 9 contains __file__" (that pins the spelling,
# not the property, and PS-11 is explicit that this does not count). The
# assertion is that the script, placed in a foreign tree, reports THAT tree's
# numbers. Every module it imports is stubbed there with a sentinel, so a
# script that reaches past its own location cannot produce this output.

_SENTINEL_A = "ANGLE (Apple, ANGLE Metal Renderer: SENTINEL-FAKE-TREE-A, Unspecified Version)"
_SENTINEL_B = "ANGLE (Apple, ANGLE Metal Renderer: SENTINEL-FAKE-TREE-B, Unspecified Version)"
# A value no real pool would ever return, so the printed figure identifies
# WHICH tree's collision_probability() actually ran.
_SENTINEL_P = 0.4242


def _materialise_foreign_tree(root):
    """A minimal tree that answers every import `measure.py` makes.

    Deliberately NOT a copy of the repo: every module here returns a sentinel,
    so any output containing one proves the import resolved HERE, and output
    containing real Apple cards proves it resolved somewhere else.
    """
    browser = root / "src" / "services" / "browser"
    verify = root / "src" / "services" / "verify"
    browser.mkdir(parents=True)
    verify.mkdir(parents=True)

    (browser / "engine_platform.py").write_text(
        "def engine_platform_for(os_type, device):\n"
        "    return 'sentinel-platform'\n",
        encoding="utf-8",
    )
    (verify / "engine_gpu_variance.py").write_text(
        # Ignores its input on purpose: the figure is an identity marker.
        f"def collision_probability(values):\n"
        f"    return {_SENTINEL_P}\n",
        encoding="utf-8",
    )
    # Emits a real extension the committed harness.js can execute, so the
    # script's own "did the extension actually patch getParameter?" assertion
    # is exercised rather than bypassed.
    (browser / "gpu_ext.py").write_text(
        "import pathlib\n"
        f"A = {_SENTINEL_A!r}\n"
        f"B = {_SENTINEL_B!r}\n"
        "def build_gpu_extension(seed, os_type, out_dir, generation,\n"
        "                        engine_platform=None):\n"
        "    d = pathlib.Path(out_dir)\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    card = A if seed % 2 else B\n"
        "    (d / 'gpu.js').write_text(\n"
        "        'for (const C of [self.WebGLRenderingContext, '\n"
        "        'self.WebGL2RenderingContext]) {'\n"
        "        '  C.prototype.getParameter = function (p) {'\n"
        "        '    if (p === 0x9246) return ' + repr(card).replace(\"'\", '\"') + ';'\n"
        "        '    if (p === 0x9245) return \"Google Inc. (Apple)\";'\n"
        "        '    return null;'\n"
        "        '  };'\n"
        "        '}',\n"
        "        encoding='utf-8')\n"
        "    return str(d)\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="the reading harness executes the emitted gpu.js under node")
def test_the_reading_script_measures_the_tree_it_is_placed_in(tmp_path):
    # Materialise a foreign tree and drop the COMMITTED script into it,
    # byte-for-byte, at the same relative location it occupies in the repo.
    foreign = _materialise_foreign_tree(tmp_path / "foreign-tree")
    readings_dir = foreign / "readings" / "ps183-2026-08-26"
    readings_dir.mkdir(parents=True)

    src_dir = pathlib.Path(__file__).resolve().parents[1] / "readings" / "ps183-2026-08-26"
    for name in ("measure.py", "harness.js"):
        shutil.copy(src_dir / name, readings_dir / name)

    # Run it from a cwd that is NEITHER tree, so nothing can be resolved by
    # accident of where the command happened to be typed.
    out = subprocess.run(
        [sys.executable, str(readings_dir / "measure.py"), "1", "8"],
        capture_output=True, text=True, timeout=180, cwd=str(tmp_path), encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr

    # It measured THIS tree: both sentinel cards, and this tree's own
    # collision_probability(). None of these strings exist in the real repo.
    assert "SENTINEL-FAKE-TREE-A" in out.stdout
    assert "SENTINEL-FAKE-TREE-B" in out.stdout
    assert f"{_SENTINEL_P:.4f}" in out.stdout
    assert "seeds=8" in out.stdout

    # And it did NOT reach past itself into the tree this suite is running in.
    # If the script resolves its imports from a fixed absolute path, it reports
    # real Apple cards here and succeeds while measuring the wrong pool — the
    # exact failure this pins, which no amount of reading the line reveals.
    assert "Apple M" not in out.stdout


def test_the_reading_script_does_not_resolve_its_tree_from_a_fixed_path(tmp_path):
    # The mutation guard for the test above. That test passes trivially in the
    # authoring container if the hard-coded path and the real repo coincide, so
    # this one states the structural property directly: the script must not
    # carry an absolute filesystem literal for the tree it imports.
    #
    # Kept narrow deliberately — it pins the ABSENCE of the defect class, while
    # the venue test above pins the behaviour. Neither alone is sufficient.
    script = pathlib.Path(__file__).resolve().parents[1] / "readings" / "ps183-2026-08-26" / "measure.py"
    code = [ln for ln in script.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]

    path_setup = [ln for ln in code if "sys.path.insert" in ln]
    assert path_setup, "the script must still put its own tree on sys.path"
    for line in path_setup:
        assert "__file__" in line, f"tree resolved from a fixed path: {line!r}"


# --------------------------------------------------------------------------
# PS-191 — THE COMPARISON ITSELF: a biased estimator against a limit bar
#
# The defect these pin is NOT "the threshold was a bit tight". `classify` used
# to compare the PLUG-IN Simpson index (biased upward at finite N) against
# `bar_for` = 1/k (the collision probability of a uniform draw IN THE LIMIT).
# Those two quantities are not comparable, and the gap is the bias term
# `(1 - 1/k)/N`. So a PERFECTLY uniform pool was flagged TOO_NARROW for being
# exactly what it should be.
#
# ⚠️ WHAT MAKES THESE COVERAGE RATHER THAN CEREMONY (PS-11). A test that
# asserts the p-value "is a number between 0 and 1" would pass against a stub
# returning 0.5 forever. The property is TWO-SIDED and every test below pins
# one side of it against the other: a uniform draw at realistic N must NOT be
# flagged by the bias term alone, AND a genuinely degenerate arm must STILL be
# flagged. Either half alone is satisfiable by a broken gate — the first by one
# that passes everything, the second by the old gate that flagged everything.
#
# THE FIXTURES ARE PS-185's REAL MEASUREMENTS, at N=24 against
# fingerprint-chromium/148.0.7778.215, taken on loopback with the masking layer
# both ON and OFF.
#
# ⚠️ THEY ARE VENDORED AS COUNTS RATHER THAN READ FROM `readings/`, and that is
# deliberate rather than lazy: PS-185's records are on an unmerged branch
# (`feature/PS-185-loopback-matrix-measurements`) and are NOT in this tree, so a
# test that opened a path would be a test that skipped. The identity STRINGS do
# not matter to any statistic here — only the multiset of counts does — so the
# counts are the whole reading for this purpose.
#
# ⚠️ AND THE POOL SIZE IS PINNED FROM THE RECORD, NEVER READ LIVE. PS-183 has
# since widened MAC_GPUS from 2 entries to 11, so today's `bar_for("macos")` is
# 1/11 while PS-185 measured against 1/2. Reading the live pool would silently
# re-point these fixtures at a bar the measurement never faced and quietly stop
# testing the thing they exist to test.
# --------------------------------------------------------------------------

# arm -> (identity counts at N=24, the pool size AS MEASURED by PS-185)
PS185_LAYER_ON = {
    "windows": ([5, 4, 4, 3, 2, 2, 2, 1, 1], 5),
    "macos": ([15, 9], 2),
    "linux": ([6, 6, 3, 3, 3, 2, 1], 8),
    "android": ([9, 6, 5, 4], 4),
}

# The engine's own identities, with persona's layer NOT installed. linux and
# android hand every single profile the SAME SwiftShader string.
PS185_LAYER_OFF = {
    "windows": ([5, 4, 4, 3, 2, 2, 2, 1, 1], 5),
    "macos": ([17, 7], 2),
    "linux": ([24], 8),
    "android": ([24], 4),
}

# PS-185's own published p-values, from an INDEPENDENT 200,000-trial
# Monte-Carlo run (seed 20260826). This module computes the null EXACTLY, by
# enumeration, so agreeing with these to within sampling noise is a real
# cross-check between two different methods rather than a restatement.
PS185_PUBLISHED_P_LAYER_ON = {
    "windows": 1.000000,
    "macos": 0.308370,
    "linux": 0.163825,
    "android": 0.579675,
}


def _values_from_counts(counts):
    """Expand identity counts into the reading list `classify` consumes."""
    out = []
    for i, c in enumerate(counts):
        out.extend([f"IDENTITY-{i}"] * c)
    return out


def _classify_at_pool_size(counts, pool_size, monkeypatch, arm="windows"):
    """Classify a fixture against the pool size it was MEASURED against."""
    monkeypatch.setattr(v, "fallback_pool_size", lambda a, g=None: pool_size)
    values = _values_from_counts(counts)
    return v.classify({arm: dict(enumerate(values))})["per_arm"][arm]


@pytest.mark.parametrize("arm", sorted(PS185_LAYER_ON))
def test_a_uniform_draw_at_realistic_n_is_not_flagged_by_the_bias_term(
    arm, monkeypatch
):
    # DoD 1 and 2. Every one of PS-185's layer-ON readings is consistent with
    # uniform selection, and NONE may be flagged. Under the old comparison
    # THREE of these four were TOO_NARROW.
    counts, pool = PS185_LAYER_ON[arm]
    entry = _classify_at_pool_size(counts, pool, monkeypatch, arm=arm)
    assert entry["verdict"] == "OK", (
        f"{arm} layer-ON is consistent with a uniform draw (PS-185 measured "
        f"p={PS185_PUBLISHED_P_LAYER_ON[arm]:.3f}) and must not be flagged"
    )
    assert entry["uniform_p_value"] > v.ALPHA


def test_android_scored_better_than_uniform_predicts_and_was_still_flagged(
    monkeypatch
):
    # ⚠️ THE AIRTIGHT CASE — the one arm whose artefact is provable without any
    # judgement call, and the reason this ticket exists at all.
    #
    # android scored 0.2743 on the plug-in statistic. A PERFECTLY uniform draw
    # from the same 4-entry pool at N=24 is EXPECTED to score 0.2812. So the
    # arm did BETTER than uniform predicts — and the old gate still called it
    # TOO_NARROW, because it compared that 0.2743 against the LIMIT value of
    # 0.2500. An arm cannot be worse than uniform while scoring better than
    # uniform predicts; that is a defect in the comparison, not a finding.
    counts, pool = PS185_LAYER_ON["android"]
    entry = _classify_at_pool_size(counts, pool, monkeypatch, arm="android")

    measured = entry["collision_probability"]
    expected_if_uniform = entry["expected_collision_under_uniform"]
    bar = entry["bar_collision_probability"]

    assert measured == pytest.approx(0.2743, abs=1e-4)
    assert expected_if_uniform == pytest.approx(0.28125, abs=1e-6)
    assert bar == pytest.approx(0.25)

    # The contradiction that proves the old comparison unsound, stated as an
    # assertion rather than as a comment: BELOW uniform expectation, ABOVE the
    # limit bar. Both at once.
    assert measured < expected_if_uniform
    assert measured > bar
    assert entry["meets_bar"] is False       # the old gate's flag, still visible
    assert entry["verdict"] == "OK"          # the corrected verdict


@pytest.mark.parametrize("arm", ["linux", "android"])
def test_a_genuinely_constant_arm_is_still_caught_after_the_fix(arm, monkeypatch):
    # DoD 3. THE OTHER HALF OF THE PROPERTY, and the half that stops this fix
    # from being "make the gate stop complaining". With the masking layer OFF,
    # linux and android hand all 24 profiles the SAME identity — a flat Level 2
    # breach, PS-185 p=0.000. A correction that greened these would be worse
    # than the defect it replaced.
    counts, pool = PS185_LAYER_OFF[arm]
    entry = _classify_at_pool_size(counts, pool, monkeypatch, arm=arm)
    assert entry["verdict"] == "CONSTANT"
    assert entry["collision_probability"] == pytest.approx(1.0)
    assert entry["uniform_p_value"] == pytest.approx(0.0, abs=1e-9)
    # It must name the remedy, not merely report the badness.
    assert "ENGINE_AUTHORED_IDENTITY_ARMS" in entry["detail"]


def test_the_constant_path_does_not_depend_on_the_bar_comparison(monkeypatch):
    # DoD 6-adjacent, and the ticket asked for this to be CONFIRMED rather than
    # assumed. `classify` reaches CONSTANT before it ever consults the bar, so
    # an arm whose every profile shares one identity is a finding even when the
    # bar cannot be read at all. Pinned because the fix moved code around that
    # branch and a reordering would silently downgrade the most severe verdict
    # this gate has into INCONCLUSIVE.
    monkeypatch.setattr(v, "fallback_pool_size", lambda a, g=None: 0)
    entry = v.classify(_arm(["ANGLE (Google, Vulkan 1.3.0 (SwiftShader …"] * 10))
    entry = entry["per_arm"]["windows"]
    assert entry["verdict"] == "CONSTANT"
    assert entry["bar_collision_probability"] is None
    assert entry["uniform_p_value"] is None


def test_the_exact_null_reproduces_ps185s_independent_monte_carlo(monkeypatch):
    # A cross-method check. PS-185 estimated these p-values by sampling
    # (200,000 trials, seed 20260826); this module derives them by exhaustive
    # enumeration in integer arithmetic. Two different methods landing on the
    # same four numbers is evidence the null model itself is right — which no
    # amount of re-running either method alone would give.
    for arm, (counts, pool) in sorted(PS185_LAYER_ON.items()):
        got = v.uniform_collision_p_value(_values_from_counts(counts), pool)
        assert got == pytest.approx(
            PS185_PUBLISHED_P_LAYER_ON[arm], abs=5e-3
        ), f"{arm}: exact {got} disagrees with PS-185's sampled estimate"


def test_a_real_narrowing_is_still_caught_at_the_sample_sizes_we_run():
    # ⚠️ THE MUTATION PROOF, and the direct answer to PS-14's warning that a
    # corrected gate which suddenly passes everything is as suspect as one that
    # flags everything. If the only evidence for this fix were that the four
    # measured arms turned green, a `return "OK"` stub would satisfy it.
    #
    # So: hold the pool at 8 and N at 24 (PS-185's linux cell, which the fix
    # turns green) and collapse the ENGINE's effective variety step by step.
    # The gate must keep passing what is genuinely uniform and start failing
    # where the narrowing is real.
    def p_for(effective_pool):
        base, rem = divmod(24, effective_pool)
        counts = [base + 1] * rem + [base] * (effective_pool - rem)
        return v.uniform_collision_p_value(_values_from_counts(counts), 8)

    # A full 8-wide draw and a 7-wide one are ordinary sampling outcomes.
    assert p_for(8) > v.ALPHA
    assert p_for(7) > v.ALPHA
    # Collapsing to 5 of 8 or fewer is caught — including the 1-entry extreme.
    for effective in (5, 4, 3, 2, 1):
        assert p_for(effective) <= v.ALPHA, (
            f"an engine using only {effective} of 8 identities went unflagged"
        )
    # And the verdict is MONOTONE: narrower is never less suspicious.
    ps = [p_for(j) for j in range(1, 9)]
    assert ps == sorted(ps), f"p-value is not monotone in variety: {ps}"

    # ⚠️ THE ABOVE EXPLORES ONLY HALF THE SPACE, AND THE DEFECT LIVED IN THE
    # OTHER HALF (PS-191 review). Every collapse `p_for` builds is EVENLY
    # SPLIT onto `j <= 8` identities against a pool of 8, so
    # `len(counts) <= pool_size` holds throughout and the deleted
    # `len(counts) > pool_size` short-circuit was never reached. A mutation
    # proof that only walks the region where the code is correct is evidence
    # for nothing, and this one passed while that branch shipped.
    #
    # Two axes were missing, and both are now walked: SKEW at a fixed distinct
    # count, and observations holding MORE distinct identities than the pool.
    def p_skewed(counts, pool):
        return v.uniform_collision_p_value(_values_from_counts(counts), pool)

    # SKEW AXIS. Same 6 distinct identities every time, same N=24, same pool —
    # only the concentration changes. An even split is an ordinary outcome; a
    # dominant identity is a breach. Nothing about the distinct COUNT can tell
    # these apart, which is precisely why the statistic has to be the skew.
    even_six = [4, 4, 4, 4, 4, 4]
    assert p_skewed(even_six, 8) > v.ALPHA, (
        "an even 6-way split of 24 draws is an ordinary sampling outcome"
    )
    for counts in ([9, 3, 3, 3, 3, 3], [14, 2, 2, 2, 2, 2], [19, 1, 1, 1, 1, 1]):
        assert len(counts) == len(even_six), "skew axis must hold distinct fixed"
        assert p_skewed(counts, 8) <= v.ALPHA, (
            f"{counts} concentrates "
            f"{max(counts)}/24 profiles on ONE identity yet went unflagged — "
            "the gate is reading the distinct count, not the skew"
        )

    # DISTINCT > POOL AXIS — the region the deleted branch answered blind.
    # These hold 6 or 7 identities against a 5-entry pool, so every one of
    # them returned p=1.0 before the fix regardless of how collided it was.
    for counts in ([19, 1, 1, 1, 1, 1], [16, 2, 2, 2, 1, 1], [12, 3, 3, 2, 2, 1, 1]):
        assert len(counts) > 5, "this axis requires distinct > pool_size"
        p = p_skewed(counts, 5)
        assert p <= v.ALPHA, (
            f"{counts} holds {len(counts)} distinct identities against a "
            "5-entry pool and was passed on that basis alone — a "
            "distinct-count pass has been reintroduced"
        )


def test_the_suite_rejects_the_stub_that_shipped_past_it():
    # ⚠️ THE PR THIS REPLACES ARGUED ITS OWN SOUNDNESS PARTLY FROM "a
    # `return \"OK\"` stub fails the suite". That was true and insufficient:
    # a stub returning 1.0 *whenever `len(counts) > pool_size`* ALSO passed
    # the suite, and that stub is exactly what shipped and was rejected.
    #
    # So the discrimination claim is executed rather than asserted. This
    # re-introduces the deleted branch as a wrapper and requires the checks
    # above to reject it. A guard that still passes when you reinstate the
    # defect it guards against is decoration (PS-11).
    real = v.uniform_collision_p_value

    def with_the_deleted_branch(values, pool_size):
        counts = v._identity_counts(values)
        if pool_size > 0 and values and len(counts) > pool_size:
            return 1.0
        return real(values, pool_size)

    # The severe reading the old branch waved through, re-scored by the stub.
    severe = _values_from_counts([19, 1, 1, 1, 1, 1])
    assert v.collision_probability(severe) > 0.6, "fixture must be severely collided"

    assert with_the_deleted_branch(severe, 5) == 1.0, (
        "the stub does not reproduce the shipped defect, so this test is not "
        "checking what it claims to check"
    )
    assert real(severe, 5) <= v.ALPHA, (
        "the real implementation no longer flags a 63.5%-collision reading — "
        "the fix has regressed"
    )


def test_a_pass_on_an_arm_above_its_bar_does_not_read_as_a_clean_bill(
    monkeypatch
):
    # ⛔ THE LAUNDERING GUARD. macos passes the corrected test (p=0.31) while
    # colliding 53.1% of the time, because a 2-entry pool drawn PERFECTLY still
    # links half the fleet. That pass is a statement about SELECTION, not about
    # SAFETY, and the two are easiest to conflate exactly here.
    #
    # PS-191 is explicit that turning macos green must not launder the real
    # weakness PS-183 owns. This pins the mechanism that prevents it: the
    # absolute collision rate and the bar survive into the record, `meets_bar`
    # stays FALSE, and the printed output says so out loud.
    counts, pool = PS185_LAYER_ON["macos"]
    monkeypatch.setattr(v, "fallback_pool_size", lambda a, g=None: pool)
    result = v.classify({"macos": dict(enumerate(_values_from_counts(counts)))})
    entry = result["per_arm"]["macos"]

    assert entry["verdict"] == "OK"
    # The uncomfortable numbers are NOT dropped just because the verdict is OK.
    assert entry["collision_probability"] == pytest.approx(0.53125)
    assert entry["meets_bar"] is False
    assert entry["collision_probability"] > entry["bar_collision_probability"]

    out = v.format_result(result)
    assert "53.1%" in out, "the real collision rate must stay in the output"
    # It must not read as an unqualified green.
    assert "does NOT say the POOL is wide enough" in out
    assert "ABOVE" in out


def test_an_undersampled_arm_is_still_inconclusive_rather_than_passed(
    monkeypatch
):
    # DoD 6. MIN_SEEDS is evaluated BEFORE the bar and before the p-value, so a
    # thin sample can never be certified — it fails safe with a wide null
    # instead of quietly passing. Pinned because the fix added a new branch
    # after that check and reordering would let "we failed to look" wear the
    # code that means "we looked and it was fine".
    monkeypatch.setattr(v, "fallback_pool_size", lambda a, g=None: 8)
    result = v.classify({"windows": {1: "A", 2: "B", 3: "C"}})
    entry = result["per_arm"]["windows"]
    assert entry["verdict"] == "INCONCLUSIVE"
    assert entry["collision_probability"] is None
    # No p-value is computed for a sample too thin to judge, so nothing here
    # can be mistaken for evidence.
    assert entry.get("uniform_p_value") is None
    assert v.exit_code_for(result) == v.EXIT_CANNOT_RUN
    assert v.exit_code_for(result) != v.EXIT_PASS


def test_an_engine_wider_than_our_own_pool_is_reached_by_the_arithmetic():
    # ⚠️ THIS TEST PINNED THE DEFECT AS THE SPECIFICATION until PS-191's second
    # review. It used to assert that `len(counts) > pool_size` short-circuits
    # to p=1.0, which is a DISTINCT-COUNT pass — the exact fallacy this
    # module's header refuses (PS-161). Measured at pool=5/N=24, that branch
    # passed [19,1,1,1,1,1]: 79% of the fleet on one card, 63.5% collision,
    # a reading the OLD broken gate caught.
    #
    # The conclusion was right and the route to it was wrong. A genuinely
    # wider-than-our-pool draw IS strictly good news and must never be
    # flagged — but it earns that verdict from its own sum-of-squares, which
    # is lower than anything the null over k cells can produce. So the honest
    # test is that the arithmetic reaches p=1.0 unaided, with no special case
    # to guess on its behalf.
    #
    # windows layer-ON is exactly this shape: 9 distinct identities against a
    # 5-entry pool, and it is the cell that took the old branch on every run.
    values = _values_from_counts([5, 4, 4, 3, 2, 2, 2, 1, 1])
    assert len(set(values)) == 9
    assert v.uniform_collision_p_value(values, 5) == 1.0

    # And the point the deleted branch got wrong: MORE DISTINCT THAN THE POOL
    # IS NOT A LICENCE TO PASS. These also hold more identities than the pool,
    # and they are concentrated rather than spread.
    for counts in ([19, 1, 1, 1, 1, 1], [16, 2, 2, 2, 1, 1]):
        skewed = _values_from_counts(counts)
        assert len(set(skewed)) > 5, "fixture must exceed the pool size"
        p = v.uniform_collision_p_value(skewed, 5)
        assert p <= v.ALPHA, (
            f"{counts} holds {len(counts)} distinct identities against a "
            f"5-entry pool and collides "
            f"{v.collision_probability(skewed):.1%} of the time, yet scored "
            f"p={p} — a distinct-count pass has been reintroduced"
        )


def test_the_verdict_is_monotone_in_the_statistic_it_polices():
    # The deleted short-circuit made the gate NON-MONOTONE: [8,8,8] at 33.3%
    # collision was flagged while [19,1,1,1,1,1] at 63.5% passed, because the
    # second happened to hold more distinct identities than the pool. An
    # operator cannot be asked to act on a verdict where colliding MORE makes
    # a reading look BETTER.
    #
    # Pinned across the distinct > pool_size boundary specifically, since that
    # is where the old branch discontinuity sat.
    #
    # ⚠️ `[8, 8, 8]` IS LOAD-BEARING AND MUST NOT BE DROPPED. It is the only
    # reading here with distinct (3) <= pool (5), and without it this test is
    # DECORATION: every other fixture exceeds the pool, so under the deleted
    # branch they all returned 1.0, and `[1.0] * 5` is trivially sorted — the
    # assertion could not fail against the very defect it exists to catch.
    # Caught by reinstating the branch and watching this test stay green.
    #
    # That is the degenerate-fixture trap (PS-54): a test built where the two
    # implementations are byte-identical proves nothing. The discriminating
    # question is "if I put the defect back, would this fixture set change?"
    # With [8, 8, 8] the defect scores it 0.0028 while everything above and
    # below it reads 1.0 — a visible inversion, and the sequence stops being
    # monotone.
    readings = [
        [5, 4, 4, 3, 2, 2, 2, 1, 1],   # 9 distinct, 13.9% — healthy
        [6, 5, 4, 4, 3, 1, 1],         # 7 distinct, 18.1%
        [12, 3, 3, 2, 2, 1, 1],        # 7 distinct, 29.9%
        [8, 8, 8],                     # 3 distinct, 33.3% — distinct <= pool
        [16, 2, 2, 2, 1, 1],           # 6 distinct, 46.9%
        [19, 1, 1, 1, 1, 1],           # 6 distinct, 63.5% — severe
    ]
    assert any(len(counts) <= 5 for counts in readings), (
        "every fixture exceeds the pool, so this test cannot distinguish the "
        "distinct-count short-circuit from a correct implementation"
    )
    scored = []
    for counts in readings:
        values = _values_from_counts(counts)
        scored.append(
            (v.collision_probability(values),
             v.uniform_collision_p_value(values, 5),
             counts)
        )

    # Sanity: the fixtures really are ordered by increasing collision.
    assert scored == sorted(scored, key=lambda row: row[0])

    ps = [p for _, p, _ in scored]
    assert ps == sorted(ps, reverse=True), (
        "p-value is not monotone decreasing as collision rises: "
        + ", ".join(f"{c!r} collision={col:.3f} p={p:.3e}"
                    for col, p, c in scored)
    )


def test_no_pool_yields_no_p_value_rather_than_a_confident_one():
    # A missing bar must not produce a number a caller could read as a pass —
    # the same "we failed to look ≠ we looked and it was fine" discipline the
    # rest of this module keeps, applied to the new statistic.
    assert v.uniform_collision_p_value(["A", "B", "C"], 0) is None
    assert v.uniform_collision_p_value([], 5) is None


def test_the_exact_null_is_a_real_distribution_not_an_approximation():
    # The enumeration's own soundness invariant, asserted directly: the weights
    # it assigns must total k**N, because it partitions exactly that many
    # equally likely assignments. If this ever fails the p-values above are
    # meaningless, so it is pinned rather than trusted.
    for n, k in ((6, 3), (8, 4), (10, 2)):
        weights = v._sum_of_squares_null_weights(n, k)
        assert sum(weights.values()) == k ** n
    # And it is a probability: the most collided outcome possible has p > 0,
    # and every outcome is at least as likely as the degenerate one.
    assert v.uniform_collision_p_value(["A"] * 6, 3) == pytest.approx(
        3 / 3 ** 6
    )


def test_the_verdict_is_deterministic_so_a_rerun_cannot_flip_it():
    # A gate whose red/green flickers teaches its readers to re-run it until it
    # is green. The exact path has no randomness at all; this pins that the
    # answer is byte-identical across repeated calls.
    values = _values_from_counts([15, 9])
    first = v.uniform_collision_p_value(values, 2)
    assert all(v.uniform_collision_p_value(values, 2) == first for _ in range(5))
