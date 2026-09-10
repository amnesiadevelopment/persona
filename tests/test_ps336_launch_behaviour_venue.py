"""Pin the SHAPE of the LAUNCH-backed behavioural venue PS-336 added.

PS-315 gave `behaviour_cli` an execution venue for the `--skip-launch` lane
only, and said so in its own step. The `needs_launch=True` bodies stayed dark:
measured at this branch's base, planting `raise AssertionError` at the head of
one body at a time and running every suite file that references the module,

    _run_restart_continuity         240 passed   <- mutant SURVIVES
    _run_benign_edit_stability      240 passed   <- mutant SURVIVES
    _run_trash_restore_and_wipe     240 passed   <- mutant SURVIVES
    _run_two_profile_unlinkability  4 failed     <- control, killed
    _run_launch_refuses_broken_geography  6 failed  <- control, killed

and PS-315's gate printed `GATE EXIT: 0` / "the behaviour held" over each of
the three. This file guards the venue that closes that, against the ways it can
be quietly un-fixed:

  * the workflow is removed, or stops invoking the launch runner;
  * the runner stops invoking the real harness, or starts using `--skip-launch`
    (which would select AWAY every check it exists to run and leave a lane that
    duplicates PS-315's);
  * the floor empties or shrinks, so a lane that certified nothing exits 0
    saying "the behaviour held" — PS-315's own hole, one lane over;
  * the floor drifts out of step with the registry's launch lane, silently
    dropping a check nobody notices is gone;
  * exit 2 stops failing the job, so "could not look" starts reading as a pass;
  * the display or the engine stops being provisioned, so every run is a
    permanent exit 2 that people learn to ignore;
  * the scratch-home guard is defeated — and THIS is the lane that actually
    wipes: `trash-restore-and-wipe` calls `wipe_all_profiles`.

⚠️ WHAT THESE TESTS CAN AND CANNOT DO, STATED SO A GREEN HERE IS NOT
OVER-READ. Nothing in this file launches a browser: this container has no
display and the suite must not spend 66 seconds and 12 browser launches per
run. So these pin the venue's SHAPE and its ADJUDICATION, driven through the
real rules with substituted reports — never that the checks pass. That the lane
BEHAVES on a runner was established by running it, and the numbers are recorded
in the PR and in the runner script's header, not asserted here.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "behaviour-launch-lane.yml"
RUNNER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "run_launch_behaviour_checks.py"
NO_LAUNCH_RUNNER = REPO_ROOT / ".github" / "scripts" / "run_behaviour_checks.py"

STEP_NAME = "Behavioural checks, launch lane (gating)"
INSTALL_STEP_NAME = "Provision a display and the project"
ENGINE_STEP_NAME = "Provision the Personium engine binary"
CHROMIUM_STEP_NAME = "Provision the Personium chromium engine binary"

#: The launch-backed checks this lane must certify. Held to the registry by
#: `test_the_floor_is_the_registry_launch_lane_minus_the_one_documented_omission`
#: rather than trusted, so a drift is a test failure and not a gate that
#: quietly stopped requiring one of them.
EXPECTED = (
    "restart-continuity",
    "benign-edit-stability",
    "trash-restore-and-wipe",
    "no-process-survives-a-closed-session",
)

#: The launch-backed check deliberately NOT in the lane — ONE of them since
#: PS-383, and this is a mapping rather than a set because an omission must say
#: WHY. A bare set would let a future omission be added with no reason at all,
#: which is exactly how a carve-out becomes a habit;
#: `test_every_omission_states_a_distinct_reason` refuses two entries that
#: share one.
#:
#: * two-profile-unlinkability — not dark (11 external callers, including a
#:   test file that drives its body directly), and it reports a FINDING on the
#:   firefox engine this project ships: a recorded, published fact since PS-135,
#:   handed to PS-2 as product work. Requiring it would make this gate
#:   permanently RED, which proves as little as permanently green.
#:
#: ⭐ THE SECOND ENTRY IS GONE (PS-383), AND THE SET SHRANK RATHER THAN GREW.
#: `no-process-survives-a-closed-session` was excluded because it launches
#: CHROMIUM on a lane that provisioned FIREFOX only — a fact about the VENUE,
#: closable by CI work, and now closed: the workflow provisions the Personium
#: chromium engine and the check is in both constants. Recorded here because
#: the two exclusions had OPPOSITE failure modes (permanently RED versus
#: permanently EXIT 2) and only the second was ever ours to fix; collapsing
#: them into one "known exclusions" set would have hidden that difference and
#: left this one sitting behind the other's argument indefinitely.
DOCUMENTED_OMISSIONS = {
    "two-profile-unlinkability": (
        "reports a FINDING on the shipped firefox engine (PS-135 §8, handed "
        "to PS-2); requiring it would make this gate permanently red"
    ),
}


@pytest.fixture(scope="module")
def workflow_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow_yaml) -> dict:
    return workflow_yaml["jobs"]["launch-lane"]


@pytest.fixture(scope="module")
def steps(job) -> list[dict]:
    return job["steps"]


@pytest.fixture(scope="module")
def script_text() -> str:
    return RUNNER_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runner():
    """The runner module itself, so its rules can be driven.

    Imported by path because `.github/scripts/` is not a package and must not
    become one — CI invokes the file directly.
    """
    spec = importlib.util.spec_from_file_location("_ps336_runner", RUNNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index_of(steps: list[dict], name: str) -> int:
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i
    raise AssertionError(f"no step named {name!r} in the launch-lane job")


def _report(passed: "list[str]", findings: int = 0, blocked: int = 0) -> str:
    """A report in `behaviour.format_report`'s shape.

    Only the two lines the runner reads are reproduced.
    `test_the_runner_reads_the_real_report_format` holds this stand-in to the
    real harness's output, so a format drift breaks a test here rather than
    only a nightly run.
    """
    lines = [f"[PASS] {name}\n  surface: whatever\n" for name in passed]
    lines.append(
        f"{len(passed)} passed, {findings} finding(s), {blocked} could not run, "
        "12 browser launch(es)"
    )
    return "\n".join(lines)


def _drive(tmp_path, *, code: int, stdout: str = "") -> subprocess.CompletedProcess:
    """Run the real `main()` against a substituted child that exits `code`.

    The child is a real process printing a real report, so this observes the
    runner end to end — capture, adjudication and exit — rather than asserting
    it of the source text. DISPLAY is declared so the preflight does not refuse
    before the substituted child is ever reached; the preflight has its own
    test below.
    """
    payload = tmp_path / "payload.txt"
    payload.write_text(stdout, encoding="utf-8")

    child = tmp_path / "child.py"
    child.write_text(
        "import sys, pathlib\n"
        f"sys.stdout.write(pathlib.Path({str(payload)!r}).read_text(encoding='utf-8'))\n"
        f"raise SystemExit({code})\n",
        encoding="utf-8",
    )

    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_launch_behaviour_checks as r\n"
        f"r.COMMAND = [sys.executable, {str(child)!r}]\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**_env_with_display()},
    )


def _env_with_display() -> dict:
    import os

    env = dict(os.environ)
    env["DISPLAY"] = ":99"  # declared, never used — no browser is launched here
    return env


# --- the venue exists and runs the real thing --------------------------------


def test_the_launch_runner_exists() -> None:
    assert RUNNER_SCRIPT.is_file(), f"{RUNNER_SCRIPT} is missing"


def test_the_workflow_exists_and_invokes_the_launch_runner(steps) -> None:
    step = steps[_index_of(steps, STEP_NAME)]

    assert "run_launch_behaviour_checks.py" in step["run"], (
        "the launch-lane step no longer invokes its runner — the launch-backed "
        "checks would be unwired again, which is the defect PS-336 closed"
    )


def test_the_runner_invokes_the_real_harness(script_text) -> None:
    assert "src.services.verify.behaviour_cli" in script_text, (
        "the runner does not invoke behaviour_cli — it cannot be observing "
        "anything the harness checks"
    )


def test_the_runner_does_not_use_skip_launch(runner) -> None:
    """`--skip-launch` would select AWAY every check this lane exists to run.

    Not a hypothetical: it is the flag the sibling gate uses, so it is the one
    a reader might copy across while "making the two runners consistent". The
    result would be a second gate over the same three no-launch checks and the
    launch bodies dark again, with a green covering it.

    ⚠️ ASSERTED ON THE COMMAND, NOT ON THE SOURCE TEXT — deliberately, and it
    is the difference between a real test and a grep. The file's header
    DISCUSSES `--skip-launch` at length (it has to: the whole argument for this
    venue is what that flag leaves out), so a text search matches the prose and
    fails on a correct runner. Worse, "fixing" that failure by deleting the
    explanation would satisfy the test while changing nothing about the gate.
    The command list is the thing that actually decides what runs.
    """
    assert "--skip-launch" not in runner.COMMAND, (
        "the launch lane passes --skip-launch, which filters out every "
        "needs_launch=True check — the lane would select away its entire reason "
        "to exist and still exit 0"
    )


def test_the_runner_actually_selects_the_launch_checks(runner) -> None:
    """The command must NAME the checks, not merely avoid excluding them."""
    for name in EXPECTED:
        assert name in runner.COMMAND, (
            f"{name!r} is not selected by the command, so the lane does not "
            "run the check its floor requires — the gate would downgrade to 2 "
            "forever, which is a permanently-red gate people learn to ignore"
        )


def test_the_selection_and_the_floor_agree(runner) -> None:
    """Asking for one set and requiring another is a gate that cannot pass.

    They are two constants because they answer different questions, and a lane
    that runs a check without requiring its pass is a legitimate future shape —
    but while they are meant to be equal, they must not drift apart silently.

    ⚠️ THIS TEST IS ONLY WORTH ANYTHING BECAUSE THE FLOOR IS WRITTEN OUT. Round
    1 of PS-336 shipped `EXPECTED_CHECKS = SELECTED_CHECKS`, and against an
    alias this assertion reads `x == x` — true for every possible value,
    forever. It is guarded by the test below, which drives the attack rather
    than trusting the constant to stay independent.
    """
    assert set(runner.SELECTED_CHECKS) == set(runner.EXPECTED_CHECKS), (
        "the lane selects one set of checks and requires another:\n"
        f"  selected: {sorted(runner.SELECTED_CHECKS)}\n"
        f"  floor:    {sorted(runner.EXPECTED_CHECKS)}\n"
        "A name in the floor but not the selection can never be certified, so "
        "the gate would be permanently red."
    )


def test_a_selection_narrowed_by_hand_cannot_narrow_the_floor_with_it(runner) -> None:
    """The floor must be INDEPENDENT of the selection, not an alias of it.

    This drives the attack the runner header calls mechanism 2, and it is the
    one attack the two constants' EQUALITY cannot detect. Mechanism 1 (`--check`
    validating its names) protects against the REGISTRY changing; nothing but
    the floor's independence protects against THIS FILE being edited to select
    fewer checks.

    Measured on PS-336 round 1, which shipped `EXPECTED_CHECKS =
    SELECTED_CHECKS` — the same tuple object, not a copy:

        aliased,     selection narrowed to 1 of 3 -> adjudicate(0, ...) = 0
        de-aliased,  selection narrowed to 1 of 3 -> adjudicate(0, ...) = 2

    A gate that exits 0 saying "the behaviour held" over one third of its lane
    is PS-315's own hole re-created inside the mechanism written to close it.
    Asserted BEHAVIOURALLY — the source is edited the way a human narrowing the
    lane would edit it, the module is re-imported, and the real adjudication is
    run over a real report. An `is`-identity check would NOT do: CPython
    de-duplicates equal literal tuples, so a correctly de-aliased floor is
    still `is`-identical to the selection and that probe reports the defect
    when there is none.

    ⭐ THE NARROWING LITERAL IS DERIVED FROM THE CONSTANT, NOT TYPED OUT
    (PS-383). It was a hand-written string, and adding a fourth check to the
    lane made `source.count(...)` zero — the guard below fired and correctly
    refused to report a green over an attack it was no longer driving. That
    refusal is the behaviour working; re-typing the literal would only postpone
    the same failure to the next widening. Building it from
    `runner.SELECTED_CHECKS` keeps the source edit REAL (the assertion below
    still proves the text was found exactly once) while making it survive a
    change to the lane's membership. ⛔ It is still matched against the FILE:
    do not weaken this into an in-memory tuple edit, which would stop testing
    that the two constants are separate TEXT.
    """
    source = RUNNER_SCRIPT.read_text(encoding="utf-8")
    body = "".join(f'    "{name}",\n' for name in runner.SELECTED_CHECKS)
    selection = f"SELECTED_CHECKS = (\n{body})"

    assert source.count(selection) == 1, (
        "the selection is no longer written in the shape this test narrows, so "
        "the attack below is not being driven — re-derive it before trusting a "
        "green here"
    )

    narrowed = source.replace(
        selection, 'SELECTED_CHECKS = (\n    "restart-continuity",\n)', 1
    )
    namespace: dict = {"__file__": str(RUNNER_SCRIPT), "__name__": "_ps336_narrowed"}
    exec(compile(narrowed, str(RUNNER_SCRIPT), "exec"), namespace)  # noqa: S102

    assert namespace["SELECTED_CHECKS"] == ("restart-continuity",), (
        "the narrowing edit did not take, so this test is not driving anything"
    )

    floor = namespace["EXPECTED_CHECKS"]
    assert set(floor) == set(EXPECTED), (
        "narrowing the SELECTION narrowed the FLOOR with it, so `EXPECTED_CHECKS` "
        "is an alias of `SELECTED_CHECKS` rather than its own constant:\n"
        f"  selected: {sorted(namespace['SELECTED_CHECKS'])}\n"
        f"  floor:    {sorted(floor)}\n"
        "The floor's independence IS the second mechanism — write it out."
    )

    code, note = namespace["adjudicate"](
        0, _report(["restart-continuity"]), floor
    )
    assert code == 2, (
        f"a lane that certified 1 of {len(EXPECTED)} checks exited 0 — 'the "
        "behaviour held' over checks that never ran. This is PS-315's hole, "
        "one lane over"
    )
    for missing in set(EXPECTED) - {"restart-continuity"}:
        assert missing in (note or ""), (
            f"the downgrade does not name {missing!r}, so the log says a check "
            "went missing without saying which"
        )


# --- the floor is real, and it is the registry's launch lane -----------------


def test_the_floor_is_not_empty(runner) -> None:
    """An empty floor is satisfied by a report certifying nothing.

    This is PS-315's hole stated at its root: `exit_code([])` is `EXIT_OK`, and
    a floor of no names makes every `missing` list empty, so the adjudication
    would honour that 0.

    ⚠️ This asserts the CONSTANT is non-empty, which under round 1's alias was
    the same claim as "the selection is non-empty" — a floor that empties only
    because the selection did. The floor's independence is driven by
    `test_a_selection_narrowed_by_hand_cannot_narrow_the_floor_with_it`; the
    refusal itself is driven by `test_an_empty_floor_cannot_certify_anything`.
    """
    assert runner.EXPECTED_CHECKS, (
        "the launch lane's floor is empty — a gate with nothing to certify "
        "exits 0 over nothing"
    )


def test_the_floor_is_the_registry_launch_lane_minus_the_documented_omissions(
    runner,
) -> None:
    """The floor must BE the launch lane, not a list that drifted from it.

    Written out by hand deliberately — deriving it from the registry would make
    it agree with an EMPTY registry by construction, which is the failure being
    guarded against. This is what keeps the hand-written copy honest.

    ⭐ EVERY OMISSION IS NAMED AND REASONED, NOT A GAP IN THE ASSERTION, and
    the two current ones are excluded for DIFFERENT reasons that must not be
    collapsed:

    * `two-profile-unlinkability` reports a FINDING on the firefox engine this
      project ships (readings/ps135-2026-08-24/EVIDENCE.md §8 predicts it
      verbatim and §7 hands it to PS-2), so requiring it would make this gate
      permanently red — which proves as little as permanently green. It is also
      the one launch-backed body that was never dark: 11 external callers.

    * `no-process-survives-a-closed-session` launches CHROMIUM on a lane that
      provisions FIREFOX only, so requiring it would make this gate permanently
      exit 2 — "nothing was measured", the failure this venue exists to remove.
      Measured by removing the chromium engine and running it under a display.

    ⚠️ WHEN PS-2 FIXES THE CANVAS COLLISION, or when a chromium engine is
    provisioned for CI, add the name to SELECTED_CHECKS and EXPECTED_CHECKS and
    delete its entry from DOCUMENTED_OMISSIONS here, in the same change. This
    test is what will remind you.
    """
    from src.services.verify.behaviour_checks import CHECKS

    lane = {c.name for c in CHECKS if c.needs_launch}

    for omitted in DOCUMENTED_OMISSIONS:
        assert omitted in lane, (
            f"{omitted!r} is no longer a launch-backed check, so the exclusion "
            "below is stale — re-derive the floor rather than keeping a "
            "carve-out for a check that no longer exists in this lane"
        )

    assert set(runner.EXPECTED_CHECKS) == lane - set(DOCUMENTED_OMISSIONS), (
        "the launch floor has drifted from the registry's launch lane.\n"
        f"  floor:    {sorted(runner.EXPECTED_CHECKS)}\n"
        f"  registry: {sorted(lane)}\n"
        f"  permitted omissions: {sorted(DOCUMENTED_OMISSIONS)}\n"
        "If a launch check was added, retired or renamed, update the constant "
        "deliberately — that edit is meant to be noticed, not absorbed. Do NOT "
        "widen the omission set to make this green."
    )


def test_every_omission_states_a_distinct_reason() -> None:
    """⛔ AN OMISSION SET IS A SLIPPERY THING, so its entries must EARN a place.

    The guard above is satisfied by ANY name in `DOCUMENTED_OMISSIONS`, which
    means the cheapest way to make a red lane green is to add a name to it —
    the very repair the runner's header forbids in capitals. This is the
    counterweight: an omission must carry a REASON, and two omissions may not
    share one.

    That matters because the two current entries are excluded for opposite
    failure modes — one would make the gate permanently RED, the other
    permanently EXIT 2 — and a set that collapsed them into "known exclusions"
    would let the third be added with no argument at all.
    """
    assert DOCUMENTED_OMISSIONS, "the mapping must not be emptied to pass"

    for name, reason in DOCUMENTED_OMISSIONS.items():
        assert reason and len(reason) > 40, (
            f"the omission {name!r} carries no real reason. An excluded check "
            "is invisible in the report, so the reason is the only record of "
            "why it is not being measured — write it out."
        )

    reasons = list(DOCUMENTED_OMISSIONS.values())
    assert len(set(reasons)) == len(reasons), (
        "two omissions share a reason. They are excluded for DIFFERENT causes "
        "(a permanent FINDING versus an unprovisioned engine); a shared reason "
        "means one of them was absorbed into the other's carve-out rather than "
        "argued on its own."
    )


def test_the_two_floors_are_disjoint_and_neither_swallowed_the_other(runner) -> None:
    """PS-315's floor is pinned by SET EQUALITY to the no-launch lane.

    Adding a launch name there turns `test_the_expected_checks_are_the_registry
    _no_launch_lane` red exactly as retiring one does (measured on this ticket:
    `1 failed, 33 deselected`), and the cheapest-looking repair — relaxing that
    `==` to `>=`, or deleting the assertion — RE-OPENS the empty-selection hole
    PS-315 was reworked to close. Two lanes, two floors, and this is the test
    that notices if somebody merges them.
    """
    spec = importlib.util.spec_from_file_location("_ps315_floor", NO_LAUNCH_RUNNER)
    assert spec is not None and spec.loader is not None
    sibling = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sibling)

    overlap = set(runner.EXPECTED_CHECKS) & set(sibling.EXPECTED_CHECKS)
    assert not overlap, (
        f"the two lanes' floors overlap on {sorted(overlap)}. The no-launch "
        "floor is pinned by set equality to the no-launch registry lane; a "
        "launch name in it turns that guard red, and relaxing that guard "
        "re-opens the hole PS-315 closed."
    )


def test_the_no_launch_floor_was_not_weakened() -> None:
    """The cheapest wrong repair, asserted against directly.

    A worker told to "extend the floor to the launch names" hits a red guard,
    and the smallest-looking way back to green is to relax the set equality or
    delete the assertion. Either one un-does a merged fix silently, so it is
    named here rather than left to code review.
    """
    guard = (REPO_ROOT / "tests" / "test_ps315_behaviour_gate.py").read_text(
        encoding="utf-8"
    )

    assert "assert set(runner.EXPECTED_CHECKS) == lane" in guard, (
        "the no-launch floor's registry-agreement assertion is gone or was "
        "relaxed from `==`. That guard is what stops the constant agreeing "
        "with an EMPTY registry by construction — the empty-selection hole "
        "PS-315 was reworked to close. Restore it; do not weaken it to make "
        "the launch lane fit."
    )


# --- the three exit codes stay three ----------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_the_runner_exits_with_the_harness_own_code(tmp_path, code: int) -> None:
    """The child's code is propagated, never flattened.

    Each child prints a report CONSISTENT with the code it exits on, because
    the adjudication corroborates 0 and 1 against the report rather than
    trusting the number. Propagation is the property under test here.
    """
    consistent = {
        0: _report(list(EXPECTED)),
        1: _report(list(EXPECTED)[:-1], findings=1),
        2: _report(list(EXPECTED)[:-1], blocked=1),
        3: "",  # a crash speaks no vocabulary at all
    }[code]

    result = _drive(tmp_path, code=code, stdout=consistent)

    assert result.returncode == code, (
        f"the runner turned a harness exit {code} into {result.returncode} — "
        "the three verdicts must not be collapsed"
    )


def test_a_harness_that_could_not_measure_fails_the_job(tmp_path) -> None:
    """Exit 2 is NOT a pass. This lane is the expensive one, so it is the one
    most likely to be quietly allowed through when it is inconvenient."""
    result = _drive(tmp_path, code=2, stdout=_report(list(EXPECTED)[:-1], blocked=1))

    assert result.returncode == 2, "exit 2 (nothing was measured) passed the job"


def test_an_empty_lane_is_not_a_pass(tmp_path) -> None:
    """The regression test for PS-315's hole, re-created one lane over.

    A selection that filtered away to nothing prints "0 passed, 0 finding(s), 0
    could not run" and exits 0. Honouring that is the defect.
    """
    result = _drive(tmp_path, code=0, stdout=_report([]))

    assert result.returncode == 2, (
        "the launch gate went GREEN over a lane that selected NOTHING — 'the "
        "behaviour held' having held nothing"
    )


def test_a_partly_certified_lane_is_not_a_pass(tmp_path) -> None:
    """Two of three is not three — the likelier failure than the empty case."""
    result = _drive(tmp_path, code=0, stdout=_report(list(EXPECTED)[:-1]))

    assert result.returncode == 2, (
        f"the gate honoured a 0 while {EXPECTED[-1]!r} was never certified"
    )


def test_the_names_of_the_missing_checks_are_reported(tmp_path) -> None:
    """A downgrade must say WHICH check went missing."""
    result = _drive(tmp_path, code=0, stdout=_report([EXPECTED[0]]))
    combined = result.stdout + result.stderr

    for missing in EXPECTED[1:]:
        assert missing in combined, (
            f"the downgrade message does not name {missing!r}, so the log says "
            "a check is missing without saying which"
        )


def test_a_fully_certified_lane_still_passes(tmp_path) -> None:
    """A GENUINE PASS MUST STAY REACHABLE.

    Without this, a rule returning 2 unconditionally satisfies every test
    above — and a permanently-RED gate proves as little as a permanently-green
    one, with the added property that it gets "fixed" by being disabled. This
    is the test that made `two-profile-unlinkability` an exclusion rather than
    a required name: with it in the floor, a clean tree exits 1 and no green is
    reachable at all (measured).
    """
    result = _drive(tmp_path, code=0, stdout=_report(list(EXPECTED)))

    assert result.returncode == 0, (
        "a lane that certified every expected check was refused:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_a_real_finding_still_exits_one(tmp_path) -> None:
    """A FINDING must stay reachable too, and must stay distinct from a 2.

    "The product misbehaved" and "we could not look" are different messages and
    the whole three-way split exists to keep them apart.
    """
    result = _drive(
        tmp_path, code=1, stdout=_report(list(EXPECTED)[:-1], findings=1)
    )

    assert result.returncode == 1, (
        "a genuine finding about the product was reported as 'nothing was "
        "measured'; the two failures must stay distinguishable"
    )


def test_no_rule_can_make_this_lane_greener(runner) -> None:
    """Every correction moves a verdict TOWARDS 2, never towards 0.

    The asymmetry is the safety argument for adjudicating at all. Swept over
    the report shapes the rules distinguish, against this lane's own floor.
    """
    reports = [
        "",
        _report([]),
        _report([EXPECTED[0]]),
        _report(list(EXPECTED)[:-1]),
        _report(list(EXPECTED)),
        _report(list(EXPECTED), findings=1),
        _report(list(EXPECTED)[:-1], findings=1),
        _report(list(EXPECTED)[:-1], blocked=1),
    ]

    for rc in (0, 1, 2, 3, 137):
        for report in reports:
            code, _why = runner.adjudicate(rc, report, runner.EXPECTED_CHECKS)
            assert code >= rc or code == 2, (
                f"adjudicate({rc}, ...) returned {code} — a correction made the "
                "verdict GREENER than the harness claimed"
            )
            if rc != 0:
                assert code != 0, (
                    f"adjudicate({rc}, ...) returned 0 — a non-pass became a pass"
                )


def test_an_empty_floor_cannot_certify_anything(runner) -> None:
    """The rule written to close the hole must not contain the hole.

    An empty `expected` makes every `missing` list empty, so a naive
    implementation honours a 0 over a report certifying nothing. Driven
    directly because no report shape can reach this state — it is a defect in
    the CALLER, and it lands on 2 like everything else this cannot corroborate.
    """
    code, why = runner.adjudicate(0, _report([]), ())

    assert code == 2, (
        "an EMPTY floor honoured a 0 — the empty-selection hole re-created "
        "inside the rule that closes it"
    )
    assert why is not None and "EMPTY" in why.upper()


def test_the_runner_reads_the_real_report_format() -> None:
    """The rules above are only as good as their grip on the real output.

    Every test here drives a stand-in report. If `format_report` drifts, those
    would keep passing against a format the runner can no longer read and every
    real run would downgrade to 2.
    """
    from src.services.verify.behaviour import PASS, Outcome, format_report

    spec = importlib.util.spec_from_file_location("_ps336_runner_fmt", RUNNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    real = format_report(
        [
            Outcome(
                name=name,
                surface="a surface",
                status=PASS,
                detail="held",
                evidence=["e"],
                falsification="shown capable of failing",
            )
            for name in EXPECTED
        ]
    )

    assert runner.passed_checks(real) == set(EXPECTED), (
        "the runner cannot read `[PASS]` badges out of the real report — every "
        "genuine run would be downgraded to 2"
    )
    counts = runner.summary(real)
    assert counts is not None and counts[0] == len(EXPECTED)


def test_the_two_lanes_share_one_adjudicator(runner) -> None:
    """One owner for the corroboration rules, not two hand-written copies.

    A copy is how two gates end up with two definitions of "a 0 was earned",
    and the copy is always the one that stops being updated.
    """
    spec = importlib.util.spec_from_file_location("_ps315_adj", NO_LAUNCH_RUNNER)
    assert spec is not None and spec.loader is not None
    sibling = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sibling)

    assert runner.adjudicate.__doc__ == sibling.adjudicate.__doc__, (
        "the launch lane no longer uses the shared adjudicator — the two gates "
        "can now drift into two different definitions of a pass"
    )


# --- the venue can actually execute -----------------------------------------


def test_a_gate_pointed_at_a_missing_harness_says_so(tmp_path, runner) -> None:
    """"The gate is pointed at nothing" is reported as such, not as an opaque
    child exit code."""
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_launch_behaviour_checks as r\n"
        "from pathlib import Path\n"
        "r.MODULE_FILE = Path('/nonexistent/behaviour_cli.py')\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env_with_display(),
    )

    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_a_missing_display_is_reported_as_cannot_run_not_as_a_finding(
    tmp_path,
) -> None:
    """Without a display every check here refuses, and that must not read as 1.

    `run_checks` calls `require_display()` as a preflight whenever any selected
    check needs a launch, and Python's default exit code for an uncaught
    exception is 1 — which aliases EXIT_FINDING and would report "nothing was
    measured" as "the product is broken". The runner says it in its own words
    and exits 2.
    """
    import os

    # ⚠️ THE SKIP MUST PRECEDE THE SPAWN, AND THIS ORDER IS THE WHOLE POINT.
    # The preflight under test is linux-only (`sys.platform.startswith("linux")`
    # in the runner's `main`), so off linux this driver does NOT short-circuit
    # at the DISPLAY check — it falls through into the real harness and launches
    # browsers, which is minutes of work whose result is then thrown away by a
    # skip. That is not merely wasteful: it exceeds pytest-timeout's 120s bound,
    # and a timeout is a FAILURE, so the skip never runs and the job goes red.
    # Measured on main @ ef87ca3: windows-latest and macos-latest both failed
    # here with `Failed: Timeout (>120.0s)` while the linux lanes passed.
    # A guard that runs after the thing it guards is not a guard.
    if not sys.platform.startswith("linux"):
        pytest.skip("the display preflight is linux-only, matching baseline's")

    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_launch_behaviour_checks as r\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    result = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 2, (
        "a run with no display did not report 'nothing was certified' — exit 1 "
        "would announce a missing display as a defect in the product"
    )
    assert "DISPLAY" in (result.stdout + result.stderr)


def test_the_workflow_provisions_a_display(steps) -> None:
    """`require_display` is an engine-blind preflight; without Xvfb this lane
    is a permanent exit 2 that people learn to ignore."""
    step = steps[_index_of(steps, INSTALL_STEP_NAME)]

    assert "xvfb" in step["run"], (
        "the launch lane does not install a display server, so every run would "
        "refuse at the preflight and report 'nothing was measured'"
    )

    gate = steps[_index_of(steps, STEP_NAME)]
    assert "xvfb-run" in gate["run"], (
        "the gate is not run under a virtual display, so the preflight refuses"
    )


def test_the_workflow_provisions_the_personium_engine_not_stock_firefox(
    steps,
) -> None:
    """WHICH engine is measured changes what a green means.

    These checks observe a MASKED identity, and every one of those properties
    is produced by persona's layer on the patched build. A pass on the stock
    playwright Firefox would certify a browser nobody ships. Verified on the
    branch: the engine resolves through invisible_playwright's cache
    (~/.cache/invisible-playwright), not playwright's (~/.cache/ms-playwright).
    """
    step = steps[_index_of(steps, ENGINE_STEP_NAME)]
    run = step["run"]

    assert "download_engine" in run, (
        "the lane does not download the Personium engine build — the product's "
        "own downloader is what fetches the bytes an operator receives"
    )
    assert "engine-baseline.txt" in run, (
        "the engine tag is not read from engine-baseline.txt, so the lane may "
        "measure a build this tree does not pin"
    )
    assert "playwright install" not in run, (
        "the lane provisions the STOCK playwright Firefox, which carries none "
        "of persona's masking layer — a pass on it certifies a browser nobody "
        "ships"
    )


def test_the_gate_runs_after_the_project_is_installed(steps) -> None:
    """The lane imports `cryptography` and the whole browser stack; without
    them it DEGRADES to CANNOT RUN rather than failing cleanly."""
    assert _index_of(steps, STEP_NAME) > _index_of(steps, INSTALL_STEP_NAME)
    assert _index_of(steps, STEP_NAME) > _index_of(steps, ENGINE_STEP_NAME)
    assert _index_of(steps, STEP_NAME) > _index_of(steps, CHROMIUM_STEP_NAME)


# --- the chromium engine, and the two ways it fails to arrive ---------------


def test_the_workflow_provisions_the_chromium_engine(steps) -> None:
    """PS-383. `no-process-survives-a-closed-session` launches CHROMIUM by
    construction, and this lane provisioned FIREFOX only — which is why a
    merged check sat in the registry with zero callers anywhere in .github/.

    Asserted on the PRODUCT's own downloader rather than on any chromium: the
    check resolves `ENGINE_DIR/fpchrome.AppImage` and `chromium_tier` refuses a
    chromium found on PATH by design, so a stock browser would not satisfy this
    lane even if a step installed one.
    """
    step = steps[_index_of(steps, CHROMIUM_STEP_NAME)]
    run = step["run"]

    assert "updater" in run and "download_engine" in run, (
        "the lane does not download the Personium chromium engine through the "
        "product's own downloader — the check would report the "
        "FileNotFoundError this venue's header quotes as its exclusion reason"
    )
    assert "write_version" in run, (
        "version.txt is not written, so the build reads 'unknown' and a red "
        "run cannot be attributed to a TAG — which is the whole remedy path"
    )
    assert "fetch_latest_checked" in run, (
        "the download does not go through the policy-checked resolver, so a "
        "build persona already refuses would be measured anyway"
    )


def test_the_chromium_engine_is_not_installed_into_the_scratch_home(
    steps, script_text
) -> None:
    """⛔ THE DEFECT THAT MAKES A CORRECT DOWNLOAD INVISIBLE (PS-383).

    `config.ENGINE_DIR` is `_under_home("engine", "PERSONA_ENGINE_DIR")`, and
    the runner hands its CHILD a fresh `PERSONA_HOME` per run. So an engine
    downloaded under the default resolution lands in the workflow's home while
    the child looks in a directory `mkdtemp` created seconds later — empty.
    Measured on this branch:

        scratch home: /tmp/persona-behaviour-launch-ci-yp34liw3
        ENGINE_BINARY= /tmp/.../engine/fpchrome.AppImage   exists= False

    which is byte-for-byte the `FileNotFoundError: .../fpchrome.AppImage` the
    exclusion cited. Both sides must pin the SAME directory, and it must be
    outside the scratch home — pinned here because a drift between them is
    silent: the download succeeds, the gate reports CANNOT RUN, and the two
    look unrelated.
    """
    step = steps[_index_of(steps, CHROMIUM_STEP_NAME)]
    gate = steps[_index_of(steps, STEP_NAME)]

    for name, s in (("chromium provisioning", step), ("the gate", gate)):
        env = s.get("env") or {}
        assert "PERSONA_ENGINE_DIR" in env, (
            f"{name} does not pin PERSONA_ENGINE_DIR, so the engine resolves "
            "under the per-run scratch PERSONA_HOME and the gate reports "
            "CANNOT RUN over a download that succeeded"
        )

    assert (step["env"]["PERSONA_ENGINE_DIR"]
            == gate["env"]["PERSONA_ENGINE_DIR"]), (
        "the download and the gate point at DIFFERENT engine directories, so "
        "the binary is installed where nothing reads it:\n"
        f"  download: {step['env']['PERSONA_ENGINE_DIR']}\n"
        f"  gate:     {gate['env']['PERSONA_ENGINE_DIR']}"
    )

    assert "PERSONA_ENGINE_DIR" in script_text, (
        "the runner script does not pin an engine directory of its own, so a "
        "hand-run (no workflow env) silently reproduces the defect this test "
        "exists to catch"
    )


def test_the_runner_scratch_home_fits_a_chromium_profile_name(runner) -> None:
    """⛔ THE SECOND WAY THE CHROMIUM ARM SILENTLY FAILS (PS-383).

    Chromium's process singleton binds a UNIX socket UNDER the profile, and the
    engine does not degrade when the path does not fit — it exits FATAL
    'Socket path too long' seconds into the launch, which from outside looks
    like a browser that started and vanished.

    `behaviour.default_scratch_home` sizes ITS home against the two
    socket-bound names and calls its `pb-` prefix "load-bearing rather than a
    style preference". The CI runner provisions its OWN home and did not, which
    was harmless while every selected check launched firefox (no socket) and is
    not now. Measured with the old prefix:

        /tmp/persona-behaviour-launch-ci-hv7nlra0  -> 41 bytes, budget  -6
        (the names need 5: 'p347a', 'p347b')

    end-to-end, engine correctly placed, under a real display:

        [CANNOT RUN] no-process-survives-a-closed-session
          … socket at 119 bytes, and the limit is 107 …   -> EXIT 2

    ⛔ THE FIX IS A SHORTER PREFIX OR A SHORTER BASE. Never a shorter profile
    name, never a looser guard, never a relaxed `_MIN_LIVE_TREE` — a budget
    checked on one side only is a budget that fails on the other.

    ⛔ THE LIMIT IS A POSIX FACT, NOT A UNIVERSAL ONE, and this test caught
    itself making the exact mistake `singleton_socket_is_bound` was written to
    prevent. On its first CI run the Windows leg failed with

        'C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\pb-ci-fyw683i1' is 51 bytes
        and leaves -16 for a profile name

    over a home the engine launches from perfectly well: Windows' process
    singleton is a NAMED MUTEX, it binds no socket, and there is no 107-byte
    wall to measure there. `behaviour.py` records the same rejection of the same
    path and calls it "a guard inventing a failure on a platform whose engine
    cannot have it, which is worse than the defect it was written for". The
    RUNTIME guard was gated correctly from the start; this test was not.
    """
    import tempfile

    from src.services.verify.behaviour import (
        profile_name_budget,
        singleton_socket_is_bound,
    )
    from src.services.verify.behaviour_checks import SOCKET_BOUND_PROFILE_NAMES

    if not singleton_socket_is_bound():
        pytest.skip(
            "no UNIX-socket singleton on this platform, so there is no "
            "sun_path budget to measure — see singleton_socket_is_bound"
        )

    needed = max(len(n) for n in SOCKET_BOUND_PROFILE_NAMES)

    home = tempfile.mkdtemp(
        prefix=runner._SCRATCH_PREFIX, dir=runner._scratch_base()
    )
    try:
        budget = profile_name_budget(home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    assert budget >= needed, (
        f"the runner's scratch home {home!r} is {len(home)} bytes and leaves "
        f"{budget} for a profile name, but this lane launches chromium under "
        f"names needing {needed}. The engine exits FATAL mid-launch, which "
        "reads from outside as a browser that started and vanished — so the "
        "lane would be permanently exit 2 for a reason that has nothing to do "
        "with the product. Shorten the prefix; do NOT touch the check."
    )


def test_the_runner_refuses_a_home_too_long_for_chromium(runner) -> None:
    """The budget is CHECKED at runtime, not merely satisfied by today's prefix.

    A green above proves the prefix fits on THIS machine. It does not protect a
    runner whose TMPDIR is long — a GitHub runner's `/home/runner/work/_temp`
    is 23 bytes, macOS's `/var/folders/…` is 53 — and on those the same
    arithmetic fails with the prefix untouched. So the script measures its own
    home and refuses with a sentence naming the cure, rather than spending 90
    seconds waiting for a tree that cannot appear.

    Driven with a home that is deliberately too long, so this asserts the
    REFUSAL rather than the happy path.
    """
    from src.services.verify.behaviour import singleton_socket_is_bound

    if not singleton_socket_is_bound():
        pytest.skip("no UNIX-socket singleton on this platform")

    too_long = "/tmp/" + ("x" * 120)
    message = runner._assert_name_budget(too_long)

    assert message is not None, (
        "the runner accepted a home no chromium profile can launch under, so "
        "the lane would report 'no browser tree was observed' instead of "
        "naming the byte budget that is actually wrong"
    )
    assert "CANNOT RUN" in message and "Nothing was certified" in message, (
        "the refusal does not speak the venue's own vocabulary, so it would "
        "read as a product finding rather than as an environment refusal"
    )

    import tempfile

    ok_home = tempfile.mkdtemp(
        prefix=runner._SCRATCH_PREFIX, dir=runner._scratch_base()
    )
    try:
        assert runner._assert_name_budget(ok_home) is None, (
            "the guard refuses the home the runner actually provisions, so it "
            "would refuse every run — a guard that always fires is not a guard"
        )
    finally:
        shutil.rmtree(ok_home, ignore_errors=True)


def test_the_install_step_installs_the_project_itself(steps) -> None:
    step = steps[_index_of(steps, INSTALL_STEP_NAME)]

    assert "pip install --prefer-binary ." in step["run"], (
        "the lane installs dependencies but not the project, so the harness "
        "would exit 2 with ModuleNotFoundError on every run"
    )


# --- the verdict cannot be swallowed ----------------------------------------


def test_the_gate_result_cannot_be_swallowed(steps) -> None:
    step = steps[_index_of(steps, STEP_NAME)]

    assert step.get("continue-on-error") is not True, (
        "the launch gate is continue-on-error — its red would block nothing"
    )
    for banned in ("|| true", "|| exit 0", "continue-on-error"):
        assert banned not in step["run"], (
            f"the launch gate discards its failure with {banned!r}"
        )


def test_the_gate_step_is_not_conditioned_away(steps) -> None:
    step = steps[_index_of(steps, STEP_NAME)]
    assert step.get("if") in (None, "always()"), (
        f"the launch gate carries a condition ({step.get('if')!r}) that could "
        "exclude it — a gate that runs sometimes is not a gate"
    )


def test_the_workflow_can_be_run_on_demand_and_on_a_schedule(workflow_yaml) -> None:
    """A scheduled venue is only useful if it also has a manual trigger.

    Without `workflow_dispatch` nobody can take a reading after a fix without
    waiting for the cron, which is how a red run stays red.
    """
    # PyYAML parses the bare key `on:` as the boolean True.
    triggers = workflow_yaml.get("on") or workflow_yaml.get(True)
    assert triggers is not None, "the workflow declares no triggers at all"
    assert "schedule" in triggers, "the venue never runs on its own"
    assert "workflow_dispatch" in triggers, (
        "the venue cannot be triggered by hand, so a fix cannot be verified "
        "without waiting for the cron"
    )


def test_the_job_has_a_timeout(job) -> None:
    """A wedged browser launch must not hang a runner for hours."""
    assert isinstance(job.get("timeout-minutes"), int), (
        "the launch lane has no timeout; a wedged launch would hang"
    )


# --- the safety guard is honoured, not defeated ------------------------------


def test_the_runner_provisions_a_scratch_home_it_does_not_reuse(script_text) -> None:
    """THIS is the lane that actually wipes.

    `trash-restore-and-wipe` calls `wipe_all_profiles`, which deletes every
    profile and purges the trash, irreversibly. It is also a correctness
    matter: the checks create fixed-name profiles, so a REUSED home collides
    with the previous run's leftovers and the second run reports CANNOT RUN.
    """
    assert "tempfile.mkdtemp" in script_text, (
        "the runner no longer provisions a fresh scratch home"
    )
    assert '"--home"' not in script_text and "'--home'" not in script_text, (
        "the runner passes --home; it must provision a throwaway directory "
        "rather than point the checks at a named store"
    )
    assert "shutil.rmtree" in script_text, (
        "the runner does not remove its scratch home, so a cached runner would "
        "carry one run's fixtures into the next"
    )


def test_the_runner_does_not_disable_the_scratch_home_refusal(script_text) -> None:
    """Skipping the re-exec is allowed; skipping the GUARD is not.

    `require_scratch_home` still runs and still refuses an unset PERSONA_HOME
    and the default store. Nothing here may set a variable that turns it off.
    """
    assert "PERSONA_HOME" in script_text, "the runner does not set a scratch home"
    for banned in ("SKIP_GUARD", "FORCE", "--force", "ALLOW_DEFAULT_HOME"):
        assert banned not in script_text, (
            f"the runner appears to defeat the safety guard via {banned!r}"
        )


def test_the_scratch_home_refusal_still_fires_with_the_reexec_flag_set() -> None:
    """The re-exec bypass must not be a guard bypass — verified by running it.

    The runner sets PERSONA_BEHAVIOUR_CLI_REEXEC so `os.execve` (spawn-and-exit
    on Windows, where a non-zero exit would not reach the runner) is skipped.
    That flag says "the home was provisioned deliberately", NOT "skip the
    check". With it set and PERSONA_HOME unset, the harness must still refuse.
    """
    import os

    env = dict(os.environ)
    env["PERSONA_BEHAVIOUR_CLI_REEXEC"] = "1"
    env.pop("PERSONA_HOME", None)
    env["DISPLAY"] = ":99"

    result = subprocess.run(
        [sys.executable, "-m", "src.services.verify.behaviour_cli", "run",
         "--check", "restart-continuity"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 2, (
        "with the re-exec skipped and PERSONA_HOME unset, the harness did not "
        "refuse — the safety guard would be defeated on the one lane that "
        "actually calls wipe_all_profiles"
    )
    assert "PERSONA_HOME is not set" in (result.stdout + result.stderr)
