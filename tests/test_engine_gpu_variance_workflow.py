"""Tests for .github/workflows/engine-gpu-variance.yml — the WIRING itself.

WHY THIS FILE EXISTS (PS-338)
-----------------------------
The variance MODULE has 1700 lines of tests
(``tests/test_verify_engine_gpu_variance.py``) and the workflow that runs it
had none — the same "the observer is connected by YAML nobody checks" gap that
``tests/test_engine_autoupdate_workflow.py`` was written to close for the
Firefox bump job. And, exactly as there, the defect that made this file
necessary lived in the YAML rather than in the module.

THE DEFECT, as measured on runs 33961825089 (2026-09-05) and 34029820054
(2026-09-06), both on ``main``:

  * The step that DOWNLOADS the engine failed, because at that moment the
    repository served no ``personium-*`` engine release at all —
    ``fetch_latest_full`` answered ``('','','')``. (The first engine release,
    ``personium-152.0.7977.75``, was published at 11:30:58Z; the 2026-09-06 run
    resolved its tag at 11:18:06Z, twelve minutes too early.)
  * ``policy.check('')`` returns OK **by design** — its docstring is explicit
    that "no tag" is a FETCH FAILURE and that turning it into a governance
    refusal "would mislabel a network problem". So the verdict was ``ok``, the
    URL was blank, and ``download_engine("")`` returned False.
  * The measurement step therefore NEVER RAN.
  * ...and the reporting step, guarded on nothing but ``failure()``, announced:

        ::error::Chromium engine <unresolved> did not clear the GPU
        seed-variance bar.

    followed by an instruction to add ``"<the tag>"`` — an EMPTY tag — to
    ``policy.KNOWN_BAD_VERSIONS``.

An ACQUISITION failure was rendered as a Level 2 unlinkability FINDING against
a build that was never measured, and the remedy offered was to blocklist a
version string that does not exist. That is precisely the inversion the
workflow's own comment forbids for the exit codes — "we failed to look must
never wear the colour of we looked and it was fine" — reflected through the
mirror: here, "we failed to look" wore the colour of "we looked and it was
BROKEN". Both directions are the same bug, which is a report that does not
match what the job actually did.

WHAT THESE TESTS PIN
--------------------
That the two outcomes stay TOLD APART, that exactly one of them always speaks
(a red run is never silent), and that neither the artifact upload nor the
blocklist instruction can be reached by a run in which nothing was measured.

Read as data. These tests deliberately do NOT run the workflow — that needs a
runner, a display and a real engine, none of which exist in this container, the
same constraint ``tests/test_engine_autoupdate_workflow.py`` and
``tests/test_verify_baseline.py`` record.
"""

import pathlib
import re

import pytest
import yaml

WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "engine-gpu-variance.yml"
)

# The step ids the reporting arms are partitioned on. Named once so a rename
# fails in ONE obvious place rather than scattering string literals.
ENGINE_STEP_ID = "engine"
MEASURE_STEP_ID = "measure"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow) -> list:
    return workflow["jobs"]["variance"]["steps"]


def _by_id(steps: list, step_id: str) -> dict:
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise AssertionError(
        f"no step with id={step_id!r} — the reporting arms are partitioned on "
        "this id, so removing it silently collapses the partition"
    )


def _named(steps: list, fragment: str) -> list:
    return [s for s in steps if fragment.lower() in (s.get("name") or "").lower()]


def _runs(steps: list) -> str:
    """Every `run:` body in the job, concatenated."""
    return "\n".join(s.get("run") or "" for s in steps)


def _prescribes_blocklisting(step: dict) -> bool:
    """True when a step tells the reader to ADD a build to KNOWN_BAD_VERSIONS.

    The discriminator is the TAG INTERPOLATION beside the list's name — the
    verdict arm's ``add "${TAG:-<the tag>}" to KNOWN_BAD_VERSIONS``. A bare
    mention is not enough and must not be: the never-measured arm names the
    same list in order to FORBID using it, and a reader that cannot tell a
    prescription from a prohibition would flag the fix as the defect.
    """
    body = step.get("run") or ""
    if "KNOWN_BAD_VERSIONS" not in body:
        return False
    return bool(re.search(r'add\s+"\$\{TAG[^"]*"\s*\n?\s*to\s+KNOWN_BAD_VERSIONS', body))


def _reporting_arms(steps: list) -> list[dict]:
    """Steps that SAY something about a red run, gated on the measurement.

    Scoped to steps carrying a ``run:`` body on purpose. The artifact upload is
    also gated on the same outcome, but it retains evidence rather than
    explaining anything — folding it in would make the partition read as three
    arms and hide whether the two that SPEAK are actually complements.
    """
    return [
        s
        for s in steps
        if s.get("run") and f"steps.{MEASURE_STEP_ID}.outcome" in (s.get("if") or "")
    ]


# --- the file is wired in at all --------------------------------------------


def test_the_workflow_exists_and_parses():
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing — the gate is connected to nothing"
    assert yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_variance_module_is_actually_invoked(steps):
    """If this ever stops matching, the daily reading is measuring nothing."""
    assert "src.services.verify.engine_gpu_variance" in _runs(steps)


def test_the_selftest_still_runs_before_the_engine_is_downloaded(steps):
    """`selftest` proves the judgement can go red and needs no engine. It must
    stay AHEAD of the 188 MB download, so a broken judgement costs nothing and
    the red this job reports is the right red."""
    names = [(s.get("name") or "") for s in steps]
    bodies = [(s.get("run") or "") for s in steps]
    selftest = next(i for i, b in enumerate(bodies) if "selftest" in b)
    download = next(
        i for i, s in enumerate(steps) if s.get("id") == ENGINE_STEP_ID
    )
    assert selftest < download, (
        "the selftest must run BEFORE the engine download. Order was: "
        f"{names}"
    )


# --- ACQUISITION AND MEASUREMENT ARE DIFFERENT OUTCOMES ---------------------
#
# The heart of PS-338. A run that could not obtain an engine has established
# NOTHING about seed variance, and must never be reported as though it had.


def test_the_measurement_step_carries_an_id(steps):
    """Without an id, `steps.<id>.outcome` is unavailable and the two failure
    modes are indistinguishable to every later step — which is exactly how an
    acquisition failure came to be announced as a variance finding."""
    step = _by_id(steps, MEASURE_STEP_ID)
    assert "engine_gpu_variance" in (step.get("run") or ""), (
        f"the step with id={MEASURE_STEP_ID!r} must be the one that MEASURES; "
        "pinning the id to a different step makes the partition lie"
    )


def test_the_variance_verdict_report_is_gated_on_the_measurement_failing(steps):
    """THE REGRESSION TEST. "did not clear the GPU seed-variance bar" is a
    claim about a MEASUREMENT. It may only be made when the measurement ran and
    returned a non-zero exit — never merely because the job is red."""
    verdict_steps = [
        s
        for s in steps
        if "seed-variance bar" in (s.get("run") or "")
    ]
    assert len(verdict_steps) == 1, (
        "expected exactly one step to render the seed-variance verdict; found "
        f"{[s.get('name') for s in verdict_steps]}"
    )
    condition = verdict_steps[0].get("if") or ""
    assert f"steps.{MEASURE_STEP_ID}.outcome" in condition, (
        "the seed-variance verdict must be gated on the MEASUREMENT's own "
        "outcome. Gated on bare failure(), a failed download renders as "
        '"the engine did not clear the bar" against a build that was never '
        f"launched. Found: {condition!r}"
    )
    assert "failure" in condition, (
        f"the verdict arm must require the measurement to have FAILED: {condition!r}"
    )


def test_the_blocklist_instruction_cannot_be_reached_without_a_measurement(steps):
    """The remedy this job prescribes is to add a tag to KNOWN_BAD_VERSIONS —
    a refusal that reaches operators' machines. It must be unreachable from a
    run that measured nothing, where it named an EMPTY tag."""
    prescribing = [s for s in steps if _prescribes_blocklisting(s)]
    assert len(prescribing) == 1, (
        "expected exactly one step to prescribe blocklisting a build; found "
        f"{[s.get('name') for s in prescribing]}"
    )
    condition = prescribing[0].get("if") or ""
    assert f"steps.{MEASURE_STEP_ID}.outcome" in condition, (
        f"step {prescribing[0].get('name')!r} tells the reader to blocklist a "
        "build, but is not gated on the measurement having run. On runs "
        "33961825089 and 34029820054 this printed "
        '\'add "" to KNOWN_BAD_VERSIONS\'.'
    )
    assert re.search(r"==\s*'failure'", condition), (
        "the blocklist instruction must be on the arm where the measurement "
        f"RAN AND FAILED: {condition!r}"
    )


def test_there_is_an_arm_for_a_run_that_never_measured_anything(steps):
    """A red run must still SAY something. Gating the verdict arm without
    adding this one would leave an acquisition failure reported by nothing but
    a bare non-zero exit — quieter than before, and no more diagnosable."""
    arms = [
        s
        for s in _reporting_arms(steps)
        if re.search(r"!=\s*'failure'", s.get("if") or "")
    ]
    assert len(arms) == 1, (
        "expected exactly one arm handling 'the job is red but the measurement "
        "never ran'. Exactly that run happened twice in two days and the only "
        "explanation offered was a variance finding about an unresolved tag. "
        f"Found: {[s.get('name') for s in arms]}"
    )
    arm = arms[0]
    assert not _prescribes_blocklisting(arm), (
        "the never-measured arm must NOT prescribe blocklisting a build: "
        "nothing was measured, so no build has been shown to be bad"
    )
    body = arm.get("run") or ""
    assert "${{" not in body and "TAG" not in (arm.get("env") or {}), (
        "the never-measured arm must not name an engine tag at all — the tag "
        "is exactly what a run that resolved nothing does not have"
    )


def test_exactly_one_reporting_arm_can_speak_on_any_red_run(steps):
    """The two arms must PARTITION the red space — every red run gets exactly
    one explanation. Both firing is a contradictory report; neither firing is
    an unexplained red."""
    arms = _reporting_arms(steps)
    assert len(arms) == 2, (
        "expected exactly two reporting arms partitioned on the measurement's "
        f"outcome; found {[s.get('name') for s in arms]}"
    )
    conditions = [s["if"] for s in arms]
    positive = [c for c in conditions if re.search(r"==\s*'failure'", c)]
    negative = [c for c in conditions if re.search(r"!=\s*'failure'", c)]
    assert len(positive) == 1 and len(negative) == 1, (
        "the two arms must be complements of one another — one on "
        f"== 'failure', one on != 'failure'. Found: {conditions!r}"
    )
    for condition in conditions:
        assert "failure()" in condition, (
            "each arm must also carry failure(), or it would run on a GREEN "
            f"job: {condition!r}"
        )


def test_the_two_arms_are_complements_and_nothing_else(steps):
    """The partition must rest on the SAME term on both sides.

    Complementarity is what makes "exactly one arm speaks" true for outcomes
    nobody enumerated — a SKIPPED measurement reports `skipped`, and a step the
    job never reached leaves the context empty. Both are `!= 'failure'`, so both
    are covered without either being named. Add a second term to one arm and
    that stops holding: some red run then satisfies neither, and is reported by
    nothing at all.
    """
    arms = _reporting_arms(steps)
    normalised = set()
    for arm in arms:
        condition = arm["if"]
        # Strip the shared failure() term and the operator, leaving the subject
        # each arm tests. If the two arms disagree here they are not complements.
        rest = re.sub(r"failure\(\)\s*&&\s*", "", condition).strip()
        subject = re.split(r"\s*[!=]=\s*", rest)[0].strip()
        normalised.add(subject)
        assert re.fullmatch(
            r"steps\.\w+\.outcome\s*[!=]=\s*'failure'", rest
        ), (
            "each arm must be exactly `failure() && steps.<id>.outcome "
            f"<op> 'failure'` and nothing more, or the two stop covering every "
            f"red run between them. Found: {condition!r}"
        )
    assert len(normalised) == 1, (
        "both arms must test the SAME step's outcome, or they are not "
        f"complements: {normalised}"
    )


def test_the_artifact_upload_is_gated_on_the_measurement_too(steps):
    """The artifact IS the reading. A run that never measured produces no file,
    and uploading unconditionally emits a "No files were found" warning that
    reads as a missing artifact rather than an absent measurement — which is
    what run 34029820054 actually printed."""
    uploads = [
        s for s in steps if "upload-artifact" in (s.get("uses") or "")
    ]
    assert len(uploads) == 1, "expected exactly one artifact upload step"
    condition = uploads[0].get("if") or ""
    assert f"steps.{MEASURE_STEP_ID}.outcome" in condition, (
        "the reading upload must be gated on the measurement having run; "
        f"found: {condition!r}"
    )


# --- THE ACQUISITION STEP MUST NAME ITS OWN FAILURE -------------------------


def test_the_download_step_distinguishes_no_release_from_a_failed_download(steps):
    """`could not download engine build ` — with a blank where the tag goes —
    was the entire diagnostic run 34029820054 offered for the download step. An
    empty tag means NO ENGINE RELEASE RESOLVED, which is a different fact from
    a download that was attempted and failed, and the two have different
    remedies."""
    body = _by_id(steps, ENGINE_STEP_ID).get("run") or ""
    assert "if not tag" in body or "if tag ==" in body, (
        "the download step must test for an EMPTY tag explicitly. Without it, "
        'the operator is told "could not download engine build" with nothing '
        "after it, and no way to tell an empty release list from a network "
        "failure."
    )


def test_the_download_step_still_refuses_a_build_policy_rejects(steps):
    """Unchanged behaviour, pinned so the PS-338 rework cannot drop it: a build
    already on the known-bad list is refused here rather than measured."""
    body = _by_id(steps, ENGINE_STEP_ID).get("run") or ""
    assert "fetch_latest_checked" in body, (
        "the download must go through the GOVERNED fetch, so a known-bad build "
        "is refused rather than measured"
    )
    assert 'verdict != "ok"' in body, "the policy verdict must still be honoured"


# --- THE THINGS THE WORKFLOW'S OWN COMMENTS FORBID --------------------------


def test_the_engine_is_still_not_pinned(steps):
    """"⚠️ AND DELIBERATELY NOT PINNED" — the risk IS whatever the newest
    published engine is. A pin makes this job green forever while the build
    users receive goes bad."""
    body = _by_id(steps, ENGINE_STEP_ID).get("run") or ""
    assert "fetch_latest" in body, (
        "the engine tag must still be RESOLVED at run time, not pinned"
    )
    assert not re.search(r"tag\s*=\s*['\"]\d+\.", body), (
        "a literal version tag appeared in the download step — the job must "
        "measure whatever upstream publishes"
    )


def test_the_measurement_exit_code_is_never_branched_on(steps):
    """"Letting a bare `run:` propagate the exit code is what keeps that true —
    any branching on the code here would be the place that laundering sneaks
    back in." Exit 2 (CANNOT_RUN) must keep failing the job."""
    body = _by_id(steps, MEASURE_STEP_ID).get("run") or ""
    assert "||" not in body, (
        "the measurement step must let its exit code propagate; `||` is how an "
        f"INCONCLUSIVE run gets laundered into a pass. Found: {body!r}"
    )
    assert "continue-on-error" not in str(_by_id(steps, MEASURE_STEP_ID)), (
        "continue-on-error on the measurement turns a finding into a green job"
    )


def test_no_step_is_allowed_to_fail_silently(steps):
    """A `continue-on-error` anywhere in this job means a red reading can end
    with a green tick."""
    offenders = [
        s.get("name") for s in steps if s.get("continue-on-error")
    ]
    assert not offenders, f"continue-on-error found on: {offenders}"
