"""PS-203: pool depth — how many identities a must-differ vector ACTUALLY has.

WHAT IS ASSERTED HERE, AND WHAT IS DELIBERATELY NOT
------------------------------------------------------
Every headline test below drives the REAL lane over the REAL committed record
files in ``readings/ps135-2026-08-24/`` and asserts on the returned REPORT.
Nothing here asserts on source text, and nothing asserts that a helper "was
called" — those pass against an implementation that computes the wrong number,
which is the failure mode this ticket's AC8 names explicitly. Revert the lane
and :func:`test_ac1_chromium_audio_digest_is_two_distinct_across_five_seeds`
goes RED, because there is no other way to obtain the sentence it asserts.

The corpus is the evidence, so the tests read it rather than restating it:
`ps135` holds five chromium product profiles, three firefox ones, two no-flag
controls whose headers LIE about which seed they were launched with, and two
reruns. Every trap this lane has to survive is already sitting in that
directory, which is why these tests point at it instead of at fixtures.

Most of what follows is negative. A report that has only ever been checked
against good data is a report that could not have been wrong.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from src.services.verify import pool_depth as pd
from src.services.verify.engine_gpu_variance import collision_probability
from src.services.verify.probes import must_differ_ids


REPO = pathlib.Path(__file__).resolve().parents[1]
CORPUS = REPO / "readings" / "ps135-2026-08-24"

CHROMIUM = "chromium"
FIREFOX = "firefox"


@pytest.fixture(scope="module")
def report():
    """The real lane over the real corpus. Every headline AC reads this."""
    return pd.report_for_directory(str(CORPUS))


def test_the_corpus_this_suite_rests_on_is_actually_present():
    # If the records move or are renamed, every assertion below would pass
    # vacuously or fail for the wrong reason. Say so plainly instead.
    assert CORPUS.is_dir(), f"missing corpus: {CORPUS}"
    names = {p.name for p in CORPUS.glob("*.json")}
    for required in (
        "reading.chromium.seed111.json",
        "reading.chromium.seed1337.rerun.json",
        "counterfactual.chromium.no-fingerprint-flag.seedarg1337.json",
        "reading.firefox.seed4242.json",
        "reading.firefox.seed1337.rerun.json",
    ):
        assert required in names, f"corpus is missing {required}"


# --------------------------------------------------------------------------
# AC1 — the headline measurement, over the product arm
# --------------------------------------------------------------------------

def test_ac1_chromium_audio_digest_is_two_distinct_across_five_seeds(report):
    """THE SENTENCE THAT DID NOT EXIST BEFORE THIS LANE.

    ``compare_profiles`` can report that a PAIR collided. It cannot report that
    this vector only ever takes two values across the campaign, because it
    holds exactly two snapshots. This is that fact, and it is asserted on the
    lane's own output over the committed files — revert the lane and there is
    no way to obtain it.
    """
    chromium = report.engine_report(CHROMIUM)
    audio = chromium.vector("audio.digest")

    assert audio.distinct == 2
    assert audio.identities == 5
    assert audio.collides

    assert audio.colliding_groups == (
        ("111", "333"),
        ("222", "1337", "4242"),
    )


def test_ac1_chromium_siblings_are_five_of_five_the_positive_control(report):
    # Same corpus, same engine, same seeds, same INDEPENDENT grade as
    # audio.digest. They are what rules out "the reader is broken" as an
    # explanation for the outlier above: a reader that could not tell profiles
    # apart would flatten these too.
    chromium = report.engine_report(CHROMIUM)
    for probe_id in ("webgl.readback", "canvas.readback"):
        vector = chromium.vector(probe_id)
        assert vector.distinct == 5, probe_id
        assert vector.identities == 5, probe_id
        assert not vector.collides, probe_id
        assert vector.colliding_groups == (), probe_id


def test_ac1_the_two_audio_values_are_the_recorded_readings_not_a_relabelling(
    report,
):
    # Pin WHAT collides, not merely that something did. A lane that grouped by
    # the wrong key could produce 2-and-5 with the identities shuffled; these
    # are the readings the files actually carry.
    audio = report.engine_report(CHROMIUM).vector("audio.digest")
    shared = {
        group: json.loads(audio.display_value(group))
        for group in audio.colliding_groups
    }
    assert shared[("111", "333")]["sum"] == 124.043475
    assert shared[("222", "1337", "4242")]["sum"] == 124.036605
    # And the two groups are genuinely different readings — the collision is a
    # property of the data, not of the grouping.
    assert shared[("111", "333")] != shared[("222", "1337", "4242")]


# --------------------------------------------------------------------------
# AC2 — the control arm is excluded by PROVENANCE, and its headers lie
# --------------------------------------------------------------------------

def test_ac2_the_counterfactual_headers_really_do_contradict_their_filenames():
    """The trap is real, and this test proves it before asserting the guard.

    This is what refuted PS-89. Both no-flag control records carry a ``flag``
    header naming a ``--fingerprint=<seed>`` they were NOT launched with, plus
    a matching ``seed`` field. If this test ever goes RED because the headers
    were cleaned up, the guard below stops being load-bearing and should be
    re-argued rather than silently kept.
    """
    for seed in (1337, 4242):
        raw = json.loads(
            (
                CORPUS
                / f"counterfactual.chromium.no-fingerprint-flag.seedarg{seed}.json"
            ).read_text(encoding="utf-8")
        )
        assert raw["seed"] == seed
        assert f"--fingerprint={seed}" in raw["flag"]


def test_ac2_a_no_flag_control_does_not_enter_the_product_statistic(report):
    chromium = report.engine_report(CHROMIUM)
    # Five product identities, not seven. The two controls' stale headers name
    # seeds 1337 and 4242, so a lane grouping on the header would either
    # inflate this to 7 or silently merge a control into an existing seed.
    assert chromium.identities == ("111", "222", "333", "1337", "4242")
    assert len(chromium.identities) == 5

    excluded = dict(report.excluded)
    for seed in (1337, 4242):
        path = str(
            CORPUS
            / f"counterfactual.chromium.no-fingerprint-flag.seedarg{seed}.json"
        )
        assert path in excluded
        assert "control arm" in excluded[path]


def test_ac2_counting_the_control_would_change_the_answer(report):
    """The exclusion is not cosmetic — it moves the number.

    The no-flag control reads ``sum = 124.043475``, byte-identical to product
    seeds 111 and 333. Admitting it would grow the {111,333} group and change
    the collision probability, so a lane that silently counted it would report
    a DIFFERENT and wrong figure rather than the same one.
    """
    audio = report.engine_report(CHROMIUM).vector("audio.digest")
    honest = audio.collision_p

    counted = [v for group in audio.groups for v in [group] for _ in group]
    assert len(counted) == 5
    # Add the two controls to the larger-sharing value, as a header-grouping
    # lane would have.
    corrupted = collision_probability(
        ["a", "a", "b", "b", "b"] + ["a", "a"]
    )
    assert corrupted != pytest.approx(honest), (
        "counting the control arm must not be a no-op, or AC2 would be "
        "unfalsifiable"
    )


def test_ac2_classification_is_from_the_filename_not_from_any_header():
    # The unit-level statement of the same rule: classify_source is handed a
    # path and nothing else, so a header cannot reach it even in principle.
    arm, is_rerun, identity = pd.classify_source(
        "/anywhere/counterfactual.chromium.no-fingerprint-flag.seedarg1337.json"
    )
    assert arm == pd.COUNTERFACTUAL
    assert not is_rerun

    arm, is_rerun, identity = pd.classify_source("/anywhere/reading.chromium.seed222.json")
    assert (arm, is_rerun, identity) == (pd.PRODUCT, False, "222")


# --------------------------------------------------------------------------
# AC3 — a rerun is one identity recorded twice
# --------------------------------------------------------------------------

def test_ac3_a_rerun_is_excluded_and_the_firefox_denominator_is_three(report):
    """FOUR firefox files, THREE identities.

    ``reading.firefox.seed1337.rerun.json`` re-records seed1337. Counting it
    adds a guaranteed-duplicate value, which overstates collision — the same
    corruption as the control arm, arriving by a different route.
    """
    firefox = report.engine_report(FIREFOX)
    assert len(list(CORPUS.glob("reading.firefox.*.json"))) == 4
    assert firefox.identities == ("111", "1337", "4242")
    assert len(firefox.identities) == 3

    excluded = dict(report.excluded)
    assert "rerun" in excluded[str(CORPUS / "reading.firefox.seed1337.rerun.json")]
    assert "rerun" in excluded[str(CORPUS / "reading.chromium.seed1337.rerun.json")]


def test_ac3_counting_the_rerun_would_overstate_collision_on_a_healthy_vector():
    # Stated as arithmetic on the shipped function so the claim is checkable:
    # three distinct identities collide 1/3 of the time; recording one of them
    # twice and counting both reports 0.389 — a vector made to look worse than
    # it is. This is WHY the exclusion exists, not merely THAT it does.
    honest = collision_probability(["a", "b", "c"])
    with_rerun = collision_probability(["a", "b", "c", "b"])
    assert honest == pytest.approx(1 / 3)
    assert with_rerun > honest


def test_ac3_a_rerun_is_named_for_the_identity_it_duplicates():
    arm, is_rerun, identity = pd.classify_source(
        "/anywhere/reading.firefox.seed1337.rerun.json"
    )
    assert arm == pd.PRODUCT and is_rerun
    assert "1337" in identity


def test_ac3_the_same_identity_arriving_twice_does_not_collide_with_itself(tmp_path):
    """THE REGRESSION. A duplicate that does not ADMIT to being a rerun.

    ``classify_source`` takes identity from the filename and excludes a rerun
    only on the literal ``rerun`` token. A copy under another name, a glob over
    two campaign directories, or a path simply listed twice carries no such
    token and passes every PER-RECORD rule — ``Record.counts`` cannot see the
    rest of the set. Counted, seed1 lands in ``by_value`` twice and the report
    says the two positive-control vectors COLLIDE, on a group ``{1, 1}`` that
    is one profile agreeing with itself.

    Asserted on the RETURNED REPORT rather than on source text, because the
    claim is about what the instrument SAYS, not about how it is written.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"value": {"sum": 1.0}}))
    # A PRODUCT-shaped name carrying no ``rerun`` token — verified below, so
    # this cannot pass by being excluded as unrecognised instead.
    dup = _write(tmp_path, "reading.chromium.trial2.seed1.json",
                 _chromium_like(1, {"value": {"sum": 1.0}}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"value": {"sum": 2.0}}))
    assert pd.classify_source(str(dup)) == (pd.PRODUCT, False, "1"), (
        "the duplicate must reach the identity rule as a PRODUCT record"
    )

    report = pd.report_for_directory(str(tmp_path))
    arm = report.engine_report(CHROMIUM)

    # One profile is one identity, however many files carry it.
    assert arm.identities == ("1", "2"), "a duplicate must not inflate the denominator"

    for probe_id in ("webgl.readback", "canvas.readback", "audio.digest"):
        vector = arm.vector(probe_id)
        assert vector.identities == 2, f"{probe_id} counted a file, not an identity"
        assert vector.distinct == 2
        assert not vector.collides, (
            f"{probe_id} reported a collision between a profile and ITSELF"
        )
        for group in vector.groups:
            assert len(set(group)) == len(group), f"{probe_id} group repeats an identity"

    assert not report.findings, "a duplicated file is not a leak"


def test_ac3_the_duplicate_is_excluded_by_provenance_naming_what_it_duplicates(tmp_path):
    """Excluded, not silently dropped — and the reason names BOTH files.

    A corrupted grouping still produces a plausible-looking report, so the
    operator has to be able to see WHICH pair collapsed. The kept file is the
    one that arrived first; the second is reported against it by name.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"value": {"sum": 1.0}}))
    dup = _write(tmp_path, "reading.chromium.trial2.seed1.json",
                 _chromium_like(1, {"value": {"sum": 1.0}}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"value": {"sum": 2.0}}))
    assert pd.classify_source(str(dup)) == (pd.PRODUCT, False, "1"), (
        "the duplicate must reach the identity rule as a PRODUCT record, so "
        "this cannot pass by being excluded as unrecognised instead"
    )

    report = pd.report_for_directory(str(tmp_path))
    excluded = dict(report.excluded)

    assert str(dup) in excluded, "the duplicate must be REPORTED, not quietly dropped"
    reason = excluded[str(dup)]
    assert "duplicate" in reason
    assert "reading.chromium.seed1.json" in reason, (
        "the reason must name the file it duplicates"
    )


def test_ac3_the_duplicate_rule_is_per_engine_not_global(tmp_path):
    """Two engines may legitimately share a seed number.

    chromium seed1 and firefox seed1 are two different profiles that happen to
    carry one seed. Deduping on the identity ALONE would delete a real arm, so
    the key is (engine, identity) — the same partition ``_require_controlled``
    insists on, held on the set axis.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"value": {"sum": 1.0}}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"value": {"sum": 2.0}}))
    firefox_1 = _chromium_like(1, {"value": {"sum": 9.0}})
    firefox_1["engine"] = FIREFOX
    firefox_2 = _chromium_like(2, {"value": {"sum": 8.0}})
    firefox_2["engine"] = FIREFOX
    _write(tmp_path, "reading.firefox.seed1.json", firefox_1)
    _write(tmp_path, "reading.firefox.seed2.json", firefox_2)

    report = pd.report_for_directory(str(tmp_path))
    assert report.engine_report(CHROMIUM).identities == ("1", "2")
    assert report.engine_report(FIREFOX).identities == ("1", "2"), (
        "a seed shared across engines is two profiles, not one duplicate"
    )
    assert not report.excluded, "nothing here is a duplicate"


def test_ac3_a_duplicate_reaching_the_lane_by_path_list_is_also_excluded():
    """``report_for_paths`` and the CLI are the exposed, unguarded routes.

    ``report_for_directory`` is safe only by accident — ``os.listdir`` cannot
    yield one name twice. The rule therefore lives in ``build_report``, so it
    holds for every entry point rather than for the one that cannot break.
    """
    seed111 = str(CORPUS / "reading.chromium.seed111.json")
    report = pd.report_for_paths(
        [seed111, seed111, str(CORPUS / "reading.chromium.seed222.json")]
    )
    arm = report.engine_report(CHROMIUM)

    assert arm.identities == ("111", "222")
    assert not arm.vector("webgl.readback").collides
    assert not arm.vector("canvas.readback").collides
    assert pd.exit_code_for(report) == pd.EXIT_OK, (
        "a listed-twice path must not raise the exit code to a finding"
    )


# --------------------------------------------------------------------------
# AC4 — firefox is reported SEPARATELY, and totally collides on canvas
# --------------------------------------------------------------------------

def test_ac4_firefox_canvas_readback_is_one_distinct_across_three(report):
    firefox = report.engine_report(FIREFOX)
    canvas = firefox.vector("canvas.readback")

    assert canvas.distinct == 1
    assert canvas.identities == 3
    assert canvas.collides
    assert canvas.colliding_groups == (("111", "1337", "4242"),)

    shared = json.loads(canvas.display_value(("111", "1337", "4242")))
    assert shared["digest"] == 4242351214

    # A total collision: every profile shares one value, so two profiles chosen
    # at random ALWAYS collide.
    assert canvas.collision_p == pytest.approx(1.0)


def test_ac4_blending_the_engines_would_hide_both_findings(report):
    """Why the report is partitioned rather than pooled.

    chromium's canvas.readback is 5/5 and firefox's is 1/3. Pooled, the vector
    reads 6 distinct over 8 — healthy-looking, and BOTH findings vanish: the
    firefox total collision is diluted by chromium's healthy readings, and the
    chromium audio finding is diluted by firefox's.
    """
    assert len(report.engines) == 2
    engines = {e.engine for e in report.engines}
    assert len(engines) == 2, "engines must not be merged into one arm"

    chromium_canvas = report.engine_report(CHROMIUM).vector("canvas.readback")
    firefox_canvas = report.engine_report(FIREFOX).vector("canvas.readback")
    assert chromium_canvas.distinct == 5 and not chromium_canvas.collides
    assert firefox_canvas.distinct == 1 and firefox_canvas.collides

    # The pooled figure the partition avoids reporting.
    pooled_distinct = chromium_canvas.distinct + firefox_canvas.distinct
    assert pooled_distinct == 6
    assert pooled_distinct > firefox_canvas.distinct, (
        "pooling would report a healthier number than the firefox arm's own"
    )


def test_ac4_firefox_audio_digest_does_not_collide(report):
    # The finding is canvas-specific on this engine. Asserting the neighbours
    # keeps a lane that flattened everything from passing AC4 by accident.
    firefox = report.engine_report(FIREFOX)
    for probe_id in ("audio.digest", "webgl.readback"):
        vector = firefox.vector(probe_id)
        assert vector.distinct == 3, probe_id
        assert not vector.collides, probe_id


# --------------------------------------------------------------------------
# AC5 — both numbers, and the Simpson index is REUSED not re-derived
# --------------------------------------------------------------------------

def test_ac5_every_vector_reports_a_distinct_count_and_a_collision_p(report):
    for arm in report.engines:
        assert arm.vectors, arm.engine
        for vector in arm.vectors:
            assert isinstance(vector.distinct, int)
            assert isinstance(vector.collision_p, float)
            assert 0.0 <= vector.collision_p <= 1.0


def test_ac5_the_reported_collision_p_is_the_shipped_function_s_own_answer(
    report,
):
    """Reuse asserted by AGREEMENT with the shipped function, not by import.

    Asserting that ``collision_probability`` was imported would pass against a
    module that imported it and then computed something else. This recomputes
    the index from the report's own groups and requires the answer to match,
    which a re-derivation that drifted would fail.
    """
    for arm in report.engines:
        for vector in arm.vectors:
            if not vector.measured:
                continue
            rebuilt = [
                value
                for group, value in zip(vector.groups, vector.group_values)
                for _ in group
            ]
            assert len(rebuilt) == vector.identities
            assert vector.collision_p == pytest.approx(
                collision_probability(rebuilt)
            ), f"{arm.engine} {vector.probe_id}"


def test_ac5_the_two_numbers_are_not_redundant_skew_is_why_both_are_reported():
    # The measured argument from collision_probability's own docstring, pinned
    # here because it is the reason a distinct count alone is not enough: two
    # values split 87/13 and 50/50 have the SAME distinct count and very
    # different collision probabilities.
    skewed = collision_probability(["a"] * 26 + ["b"] * 4)
    even = collision_probability(["a"] * 15 + ["b"] * 15)
    assert len({*["a"] * 26, *["b"] * 4}) == len({*["a"] * 15, *["b"] * 15}) == 2
    assert skewed > even
    assert skewed == pytest.approx(0.769, abs=0.005)


def test_ac5_chromium_collision_probabilities_are_the_expected_figures(report):
    chromium = report.engine_report(CHROMIUM)
    # (2/5)^2 + (3/5)^2 = 0.52 — the two-value pool.
    assert chromium.vector("audio.digest").collision_p == pytest.approx(0.52)
    # 5 * (1/5)^2 = 0.20 — five evenly-used identities.
    assert chromium.vector("webgl.readback").collision_p == pytest.approx(0.20)
    assert chromium.vector("canvas.readback").collision_p == pytest.approx(0.20)


# --------------------------------------------------------------------------
# AC6 — the evidence floor: a set of one is REFUSED, never reported clean
# --------------------------------------------------------------------------

def test_ac6_a_single_record_is_refused_rather_than_reported_clean():
    """A one-record set trivially scores every vector at full depth.

    That is the tool at its most confident with the least evidence, and it is
    what the PS-92/PS-55 evidence floor exists to prevent. It must REFUSE, not
    return a report that happens to look perfect.
    """
    with pytest.raises(pd.NotEnoughProfiles):
        pd.report_for_paths([str(CORPUS / "reading.chromium.seed111.json")])


def test_ac6_an_empty_set_is_refused():
    with pytest.raises(pd.NotEnoughProfiles):
        pd.report_for_paths([])


def test_ac6_a_set_that_is_only_excluded_records_is_refused(tmp_path):
    # The subtle case: two FILES, zero counted profiles. A lane that applied
    # the floor to the file count rather than the counted count would sail past
    # this and report over an empty set.
    for seed in (1337, 4242):
        name = f"counterfactual.chromium.no-fingerprint-flag.seedarg{seed}.json"
        (tmp_path / name).write_bytes((CORPUS / name).read_bytes())

    with pytest.raises(pd.NotEnoughProfiles) as excinfo:
        pd.report_for_directory(str(tmp_path))
    # And the refusal must SAY the records were excluded, or an operator reads
    # it as "you gave me nothing" and goes looking for a missing directory.
    assert "control arm" in str(excinfo.value)


def test_ac6_the_refusal_names_the_floor_it_is_enforcing():
    with pytest.raises(pd.NotEnoughProfiles) as excinfo:
        pd.report_for_paths([str(CORPUS / "reading.firefox.seed111.json")])
    message = str(excinfo.value)
    assert "at least 2" in message
    assert "pool depth" in message


def test_ac6_an_unrecognised_file_is_excluded_rather_than_assumed_product(
    tmp_path,
):
    # Conservative direction: a file wrongly counted corrupts the statistic
    # invisibly; one wrongly excluded shows up where an operator can see it.
    (tmp_path / "something-else.json").write_bytes(
        (CORPUS / "reading.chromium.seed111.json").read_bytes()
    )
    for seed in (222, 333):
        name = f"reading.chromium.seed{seed}.json"
        (tmp_path / name).write_bytes((CORPUS / name).read_bytes())

    report = pd.report_for_directory(str(tmp_path))
    assert report.engine_report(CHROMIUM).identities == ("222", "333")
    assert any("not recognised" in why for _, why in report.excluded)


# --------------------------------------------------------------------------
# AC7 — inconclusive is never distinctness, and never a collision
# --------------------------------------------------------------------------

def _write(tmp_path, name, snapshot):
    path = tmp_path / name
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


def _chromium_like(seed, audio_cell):
    return {
        "engine": "fingerprint-chromium (persona engine binary)",
        "seed": seed,
        "probes": {
            "window": {
                "audio.digest": audio_cell,
                "canvas.readback": {"value": {"digest": seed}},
                "webgl.readback": {"value": {"digest": seed}},
            },
            "worker": {"canvas.readback": {"value": {"digest": seed}}},
        },
    }


def test_ac7_two_errored_readings_are_not_two_different_profiles(tmp_path):
    """The PS-21 rule on this axis. Two failures to read are not two identities.

    A lane that counted them would report distinct == 2 over a vector nobody
    read — a clean bill of health issued on no evidence at all.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"error": "boom"}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"error": "different boom"}))

    audio = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "audio.digest"
    )
    assert audio.distinct == 0
    assert audio.identities == 0
    assert audio.inconclusive_identities == ("1", "2")
    assert not audio.measured, "a vector nobody read has no verdict"
    assert not audio.collides, "and it is not a collision either"


def test_ac7_a_null_reading_counts_as_unread_on_this_axis(tmp_path):
    """``null`` is the absence of a reading wearing a reading's clothes.

    ``{"value": null}`` carries ``value``, so the plain ``_unread`` predicate
    reads it as OBTAINED and two such profiles compare EQUAL — a false
    collision on every pair. This lane uses ``_unread_for_unlinkability``,
    where a null identity vector is unread.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"value": None}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"value": None}))

    audio = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "audio.digest"
    )
    assert not audio.collides, (
        "two profiles that both failed to read must NOT be reported colliding"
    )
    assert audio.identities == 0
    assert audio.inconclusive_identities == ("1", "2")


def test_ac7_an_absent_probe_is_inconclusive_not_a_shared_value(tmp_path):
    for seed in (1, 2):
        snapshot = _chromium_like(seed, {"value": {"sum": 1.0}})
        del snapshot["probes"]["window"]["audio.digest"]
        _write(tmp_path, f"reading.chromium.seed{seed}.json", snapshot)

    audio = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "audio.digest"
    )
    assert audio.identities == 0
    assert not audio.collides


def test_ac7_an_inconclusive_identity_leaves_the_denominator_honest(tmp_path):
    """One unread profile out of three: the vector reports 2, not 3.

    The denominator must say how many identities CONTRIBUTED a reading, not how
    many files were opened — otherwise a partially-read set silently reports
    better depth than it measured.
    """
    _write(tmp_path, "reading.chromium.seed1.json", _chromium_like(1, {"value": {"sum": 1.0}}))
    _write(tmp_path, "reading.chromium.seed2.json", _chromium_like(2, {"value": {"sum": 2.0}}))
    _write(tmp_path, "reading.chromium.seed3.json", _chromium_like(3, {"error": "nope"}))

    audio = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "audio.digest"
    )
    assert audio.identities == 2
    assert audio.distinct == 2
    assert audio.inconclusive_identities == ("3",)
    assert audio.collision_p == pytest.approx(0.5)


def test_ac7_the_real_corpus_reports_no_inconclusive_cells(report):
    # The committed records are complete on every must-differ vector, so any
    # inconclusive entry here means the reader failed to find a reading that IS
    # present — a silent under-count wearing the "we didn't look" label.
    for arm in report.engines:
        for vector in arm.vectors:
            assert vector.inconclusive_identities == (), (
                f"{arm.engine} {vector.probe_id} should have been readable"
            )
            assert vector.measured


# --------------------------------------------------------------------------
# The realm-folding trap: the unit of counting is the IDENTITY, not the cell
# --------------------------------------------------------------------------

def test_a_two_realm_vector_contributes_one_value_per_identity(report):
    """``canvas.readback`` declares two realms and carries one digest in both.

    A reader that appended every ``probes[realm][probe_id]`` cell would collect
    6 values for firefox's 3 identities — inflating the denominator, and on a
    healthier vector diluting a real duplicate into a false pass.
    ``collision_probability``'s Simpson index assumes one independently drawn
    profile per element, so it must be handed exactly one.
    """
    firefox = report.engine_report(FIREFOX)
    canvas = firefox.vector("canvas.readback")
    assert canvas.realms == ("window", "worker")
    assert canvas.identities == len(firefox.identities) == 3, (
        "a two-realm vector must not double the denominator"
    )

    chromium = report.engine_report(CHROMIUM)
    assert chromium.vector("canvas.readback").identities == 5


def test_a_vector_whose_realms_disagree_is_not_silently_halved(tmp_path):
    # Two profiles that agree in `window` but differ in `worker` are DISTINCT
    # identities. Folding must preserve that, not collapse to the window value.
    for seed, worker_digest in ((1, 111), (2, 222)):
        snapshot = _chromium_like(seed, {"value": {"sum": float(seed)}})
        snapshot["probes"]["window"]["canvas.readback"] = {"value": {"digest": 9}}
        snapshot["probes"]["worker"]["canvas.readback"] = {
            "value": {"digest": worker_digest}
        }
        _write(tmp_path, f"reading.chromium.seed{seed}.json", snapshot)

    canvas = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "canvas.readback"
    )
    assert canvas.distinct == 2, "a worker-realm difference is a real difference"
    assert not canvas.collides


def test_a_vector_unread_in_one_declared_realm_is_inconclusive(tmp_path):
    # Conservative and in the safe direction: admitting the readable half would
    # let one realm stand in for a profile whose other realm nobody obtained.
    for seed in (1, 2):
        snapshot = _chromium_like(seed, {"value": {"sum": float(seed)}})
        snapshot["probes"]["worker"]["canvas.readback"] = {"error": "no worker"}
        _write(tmp_path, f"reading.chromium.seed{seed}.json", snapshot)

    canvas = pd.report_for_directory(str(tmp_path)).engine_report(CHROMIUM).vector(
        "canvas.readback"
    )
    assert canvas.identities == 0
    assert canvas.inconclusive_identities == ("1", "2")


def test_a_window_only_vector_is_not_reported_absent_in_the_worker_realm(report):
    # audio.digest and webgl.readback declare `window` ONLY, and the records
    # carry no worker cell for them. The inventory never asked, so that is not
    # a missing reading — a lane walking the FILE's realms instead of the
    # INVENTORY's would report every one of these inconclusive.
    chromium = report.engine_report(CHROMIUM)
    for probe_id in ("audio.digest", "webgl.readback"):
        vector = chromium.vector(probe_id)
        assert vector.realms == ("window",), probe_id
        assert vector.identities == 5, probe_id
        assert vector.inconclusive_identities == (), probe_id


# --------------------------------------------------------------------------
# The lane tracks the inventory, and both record shapes are readable
# --------------------------------------------------------------------------

def test_the_lane_reports_exactly_the_must_differ_inventory(report):
    # Driven off must_differ_probes() so classifying a probe stays a matter of
    # editing its inventory record. A hardcoded list here would let the two
    # drift apart, which is the drift the inventory's own docstring warns of.
    expected = must_differ_ids()
    assert expected  # the inventory is not empty, or this asserts nothing
    for arm in report.engines:
        assert {v.probe_id for v in arm.vectors} == set(expected), arm.engine


def test_both_record_shapes_are_read_not_just_the_richer_one(report):
    """chromium and firefox records are NOT the same shape.

    chromium carries no ``realms`` key and no ``profile``; firefox carries
    both, and its ``realms`` is a list of names while the readings live under
    ``probes``. A naive reader keyed on ``realms`` returns None for every
    chromium vector — which would look like a clean, quiet, entirely empty
    chromium arm.
    """
    chromium_raw = json.loads(
        (CORPUS / "reading.chromium.seed111.json").read_text(encoding="utf-8")
    )
    firefox_raw = json.loads(
        (CORPUS / "reading.firefox.seed111.json").read_text(encoding="utf-8")
    )
    assert "realms" not in chromium_raw and "profile" not in chromium_raw
    assert isinstance(firefox_raw["realms"], list) and "profile" in firefox_raw

    # Both arms produced real readings anyway.
    for engine in (CHROMIUM, FIREFOX):
        arm = report.engine_report(engine)
        assert all(v.measured for v in arm.vectors), engine


def test_the_profile_less_chromium_records_are_read_without_a_profile_header():
    # The premise of this whole lane: these records are unusable by
    # compare_profiles (correctly — see AC9 below) and readable here.
    record = pd.load_record(str(CORPUS / "reading.chromium.seed222.json"))
    assert record.snapshot.get("profile") is None
    assert record.counts
    assert record.identity == "222"


def test_a_record_with_no_engine_header_is_excluded_not_lumped_together(tmp_path):
    # Partitioning is the premise _require_controlled protects, one axis over:
    # an unrecorded engine gives nothing to partition on, and "None == None" is
    # "no idea", not "same engine".
    for seed in (1, 2):
        snapshot = _chromium_like(seed, {"value": {"sum": float(seed)}})
        del snapshot["engine"]
        _write(tmp_path, f"reading.chromium.seed{seed}.json", snapshot)
    _write(tmp_path, "reading.chromium.seed3.json", _chromium_like(3, {"value": {"sum": 3.0}}))
    _write(tmp_path, "reading.chromium.seed4.json", _chromium_like(4, {"value": {"sum": 4.0}}))

    report = pd.report_for_directory(str(tmp_path))
    assert report.engine_report(CHROMIUM).identities == ("3", "4")
    assert any("no engine recorded" in why for _, why in report.excluded)


def test_engines_are_partitioned_on_the_raw_header_not_a_normalisation(report):
    # Two arms, each named by the record's own account of itself. chromium's
    # header is not a tidy token, and inventing an equivalence to tidy it is
    # exactly how two engines get merged.
    names = sorted(e.engine for e in report.engines)
    assert names == [
        "fingerprint-chromium (persona engine binary)",
        "firefox",
    ]


def test_asking_for_an_absent_engine_raises_rather_than_returning_empty(report):
    with pytest.raises(KeyError):
        report.engine_report("safari")


# --------------------------------------------------------------------------
# AC9 — nothing existing was changed, and the guard was NOT relaxed
# --------------------------------------------------------------------------

def test_ac9_require_controlled_still_refuses_the_profile_less_records():
    """The guard this lane routes AROUND rather than through, still refusing.

    AC9's specific instruction: ``_require_controlled``'s refusal of these
    records is CORRECT and must not be relaxed to make pool depth run. If this
    test ever goes green-by-relaxation, this lane was built the wrong way.
    """
    from src.services.verify.diff import ComparisonNotControlled, compare_profiles
    from src.services.verify.snapshot import load

    a = load(str(CORPUS / "reading.chromium.seed111.json"))
    b = load(str(CORPUS / "reading.chromium.seed222.json"))
    with pytest.raises(ComparisonNotControlled):
        compare_profiles(a, b)


def test_ac9_the_pairwise_comparator_still_works_where_it_always_did():
    # firefox records DO carry profile headers, and the pairwise gate correctly
    # reports the canvas collision there. This lane adds a question; it must not
    # have changed this answer.
    from src.services.verify.diff import compare_profiles
    from src.services.verify.snapshot import load

    entries = compare_profiles(
        load(str(CORPUS / "reading.firefox.seed111.json")),
        load(str(CORPUS / "reading.firefox.seed4242.json")),
    )
    reported = {e.get("probe") or e.get("probe_id") or e.get("id") for e in entries}
    assert any(
        r and "canvas.readback" in str(r) for r in reported
    ), f"expected a canvas.readback collision, got {entries}"


# --------------------------------------------------------------------------
# The operator-facing surfaces
# --------------------------------------------------------------------------

def test_the_formatted_report_prints_the_denominator_with_every_figure(report):
    text = pd.format_report(report)
    assert "2 distinct / 5 identities" in text
    assert "1 distinct / 3 identities" in text
    # A bare count read as a population estimate is the misuse this measurement
    # is most exposed to at n=5/3, so the caveat is part of the output.
    assert "not a population estimate" in text


def test_the_formatted_report_names_what_was_excluded_and_why(report):
    text = pd.format_report(report)
    assert "excluded by provenance" in text
    assert "counterfactual.chromium.no-fingerprint-flag.seedarg1337.json" in text
    assert "reading.firefox.seed1337.rerun.json" in text


def test_the_exit_code_is_a_finding_on_this_corpus(report):
    # Two vectors collide, so this must not be a pass. A gate that could only
    # ever return 0 would be no gate at all.
    assert pd.exit_code_for(report) == pd.EXIT_FINDING


def test_the_exit_code_is_clean_when_no_vector_collides(tmp_path):
    # The gate can also go GREEN — asserted so the finding above is a verdict
    # rather than a constant.
    for seed in (1, 2, 3):
        _write(
            tmp_path,
            f"reading.chromium.seed{seed}.json",
            _chromium_like(seed, {"value": {"sum": float(seed)}}),
        )
    report = pd.report_for_directory(str(tmp_path))
    assert not report.findings
    assert pd.exit_code_for(report) == pd.EXIT_OK


def test_the_exit_code_is_inconclusive_when_nothing_was_measured(tmp_path):
    for seed in (1, 2):
        snapshot = {
            "engine": "fingerprint-chromium (persona engine binary)",
            "probes": {"window": {}, "worker": {}},
        }
        _write(tmp_path, f"reading.chromium.seed{seed}.json", snapshot)
    report = pd.report_for_directory(str(tmp_path))
    assert pd.exit_code_for(report) == pd.EXIT_INCONCLUSIVE
    assert not report.findings, "unread is not a finding"


def test_the_cli_subcommand_runs_the_real_lane_and_exits_one():
    """End to end, as an operator runs it. AC1's numbers must reach stdout."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.services.verify.cli",
            "pool-depth",
            str(CORPUS),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1, result.stderr
    assert "2 distinct / 5 identities" in result.stdout
    assert "1 distinct / 3 identities" in result.stdout
    assert "collision_p=0.5200" in result.stdout


def test_the_cli_refuses_a_single_record_with_exit_two_not_a_clean_zero():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.services.verify.cli",
            "pool-depth",
            str(CORPUS / "reading.chromium.seed111.json"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2, result.stdout
    assert "error:" in result.stderr


def test_the_json_output_carries_the_same_figures_as_the_text(report):
    payload = report.to_dict()
    chromium = next(
        e for e in payload["engines"] if "chromium" in e["engine"]
    )
    audio = next(v for v in chromium["vectors"] if v["probe_id"] == "audio.digest")
    assert audio["distinct"] == 2
    assert audio["identities"] == 5
    assert audio["collision_p"] == pytest.approx(0.52)
    assert audio["colliding_groups"] == [["111", "333"], ["222", "1337", "4242"]]
    # Round-trips as JSON, since a report an operator cannot pipe is a report
    # that gets re-derived by hand.
    assert json.loads(json.dumps(payload))["engines"]
