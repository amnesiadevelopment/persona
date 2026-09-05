"""Pin the SHAPE of the behavioural-checks CI gate PS-315 added.

This gate exists because `behaviour_cli` was a working instrument that nothing
executed, so its check bodies could rot while the suite stayed green. A gate
added to fix that can be un-fixed in four quiet ways, each of which leaves a
green check that proves nothing:

  * the step is removed, or stops invoking the harness at all;
  * the step drifts to AFTER `Run the test suite`, where it would be silently
    skipped on Windows forever (that step carries no `if: always()` and the
    Windows leg is red at its measured floor) — this gate's own defect, a check
    that never executes, re-created one level up;
  * the step drifts to BEFORE `Install deps`, where the lane's `cryptography`
    import is unavailable and the harness degrades to exit 2 on every run — a
    permanent red saying "nothing was measured";
  * the three exit codes get collapsed into "non-zero", or exit 2 stops failing
    the job, so a run that COULD NOT LOOK starts reading as a pass.

So these assert the properties that make the gate MEAN something, in the spirit
of tests/test_ci_verification_gates.py, rather than that a YAML key exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RUNNER_SCRIPT = REPO_ROOT / ".github" / "scripts" / "run_behaviour_checks.py"

STEP_NAME = "Behavioural checks, no-launch lane (gating)"
SUITE_STEP_NAME = "Run the test suite"
INSTALL_STEP_NAME = "Install deps"


@pytest.fixture(scope="module")
def ci_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tests_steps(ci_yaml) -> list[dict]:
    return ci_yaml["jobs"]["tests"]["steps"]


@pytest.fixture(scope="module")
def script_text() -> str:
    return RUNNER_SCRIPT.read_text(encoding="utf-8")


def _index_of(steps: list[dict], name: str) -> int:
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i
    raise AssertionError(f"no step named {name!r} in the tests job")


# --- the gate runs at all ---------------------------------------------------


def test_the_runner_script_exists() -> None:
    assert RUNNER_SCRIPT.is_file(), f"{RUNNER_SCRIPT} is missing"


def test_the_tests_job_invokes_the_behavioural_runner(tests_steps) -> None:
    step = tests_steps[_index_of(tests_steps, STEP_NAME)]

    assert "run_behaviour_checks.py" in step["run"], (
        "the behavioural step no longer invokes the runner script — the "
        "harness would be unwired again, which is the defect PS-315 closed"
    )


def test_the_runner_actually_invokes_the_behaviour_cli(script_text) -> None:
    """The script must drive the real harness, not a stand-in."""
    assert "src.services.verify.behaviour_cli" in script_text, (
        "the runner does not invoke behaviour_cli — it cannot be observing "
        "anything the harness checks"
    )
    assert "--skip-launch" in script_text, (
        "the no-launch lane selector is gone; without it the harness needs a "
        "display and would exit 2 on every runner"
    )


# --- the gate runs WHERE it can actually execute -----------------------------


def test_the_gate_runs_before_the_suite_or_survives_its_failure(tests_steps) -> None:
    """The Windows leg is red at its floor, so ordering is load-bearing.

    `Run the test suite` carries no `if: always()`. A step placed after it is
    therefore skipped on every Windows run, permanently — a gate that never
    executes on 1 of 3 platforms. Either ordering fixes it; this asserts one of
    them holds rather than pinning a choice.
    """
    gate = _index_of(tests_steps, STEP_NAME)
    suite = _index_of(tests_steps, SUITE_STEP_NAME)

    if gate > suite:
        assert tests_steps[gate].get("if") == "always()", (
            "the behavioural gate sits AFTER `Run the test suite`, which "
            "carries no `if: always()`, so it would be silently skipped on "
            "windows-latest forever — that leg is red at its measured floor. "
            "Move it before the suite, or give it `if: always()`."
        )


def test_the_gate_runs_after_the_project_is_installed(tests_steps) -> None:
    """The lane imports `cryptography`; without it the harness exits 2.

    Not a clean failure — it DEGRADES: two of the three checks report CANNOT
    RUN with ModuleNotFoundError. A gate that is permanently red saying
    "nothing was measured" is the exact failure this gate exists to remove.
    """
    gate = _index_of(tests_steps, STEP_NAME)
    install = _index_of(tests_steps, INSTALL_STEP_NAME)

    assert gate > install, (
        "the behavioural gate runs before `Install deps`, so `cryptography` "
        "is absent and the harness would degrade to exit 2 on every run"
    )


def test_the_gate_lives_in_the_job_that_installs_the_project(ci_yaml) -> None:
    """The `types` job installs requirements-dev.txt only — never the project."""
    for job_name, job in ci_yaml["jobs"].items():
        if job_name == "tests":
            continue
        for step in job.get("steps", []):
            assert "run_behaviour_checks.py" not in (step.get("run") or ""), (
                f"the behavioural gate appears in job {job_name!r}, which does "
                "not install the project — it would exit 2 on every run"
            )


# --- the gate's verdict cannot be swallowed ---------------------------------


def test_the_gate_result_cannot_be_swallowed(tests_steps) -> None:
    step = tests_steps[_index_of(tests_steps, STEP_NAME)]

    assert step.get("continue-on-error") is not True, (
        "the behavioural gate is continue-on-error — its red would block nothing"
    )
    for banned in ("|| true", "|| exit 0"):
        assert banned not in step["run"], (
            f"the behavioural gate discards its failure with {banned!r}"
        )


def test_the_gate_step_is_not_conditioned_away(tests_steps) -> None:
    """`if:` is allowed only as `always()` — never as a platform opt-out."""
    step = tests_steps[_index_of(tests_steps, STEP_NAME)]
    condition = step.get("if")

    assert condition in (None, "always()"), (
        f"the behavioural gate carries a condition ({condition!r}) that could "
        "exclude a platform — a gate that runs on some runners is not a gate"
    )


# --- the three exit codes stay three ----------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_the_runner_exits_with_the_harness_own_code(tmp_path, code: int) -> None:
    """The child's code is propagated, never flattened to 0/1.

    Driven by substituting the command for one that exits deliberately, so this
    observes the propagation rather than asserting it of the source text. Code 3
    is included because an UNEXPECTED code must also fail — a harness that
    crashed certified nothing.
    """
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_behaviour_checks as r\n"
        f"r.COMMAND = [sys.executable, '-c', 'raise SystemExit({code})']\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True
    )

    assert result.returncode == code, (
        f"the runner turned a harness exit {code} into {result.returncode} — "
        "the three verdicts must not be collapsed"
    )


def test_a_harness_that_could_not_measure_fails_the_job(tmp_path) -> None:
    """Exit 2 is NOT a pass. This is the whole reason the split exists.

    A run that could not look reporting success is the defect the behavioural
    harness was built to catch; letting it through here would reintroduce it in
    the gate meant to prevent it.
    """
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_behaviour_checks as r\n"
        "r.COMMAND = [sys.executable, '-c', 'raise SystemExit(2)']\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True
    )

    assert result.returncode != 0, "exit 2 (nothing was measured) passed the job"
    assert result.returncode == 2


def test_an_unexpected_zero_is_not_reported_as_a_pass(tmp_path) -> None:
    """A harness that exits 0 without speaking its vocabulary still fails.

    Guards the one direction the parametrised test above cannot: a code outside
    {0,1,2} is mapped to 2, and 0 must never be reachable that way.
    """
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_behaviour_checks as r\n"
        "r.VERDICTS = {}\n"  # nothing is a recognised verdict
        "r.COMMAND = [sys.executable, '-c', 'raise SystemExit(0)']\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(driver)], capture_output=True, text=True
    )

    assert result.returncode == 2, (
        "an unrecognised verdict was reported as a pass; it certifies nothing"
    )


# --- the safety guard is honoured, not defeated ------------------------------


def test_the_runner_provisions_a_scratch_home_it_does_not_reuse(script_text) -> None:
    """`wipe_all_profiles` makes a reused or real home unrecoverable.

    Also a correctness matter, not only a safety one: the checks create
    fixed-name profiles, so a REUSED home collides with the previous run's
    leftovers and the second run reports CANNOT RUN.
    """
    assert "tempfile.mkdtemp" in script_text, (
        "the runner no longer provisions a fresh scratch home"
    )
    assert '"--home"' not in script_text and "'--home'" not in script_text, (
        "the runner passes --home; it must provision a throwaway directory "
        "rather than point the checks at a named store"
    )


def test_the_runner_does_not_disable_the_scratch_home_refusal(script_text) -> None:
    """Skipping the re-exec is allowed; skipping the GUARD is not.

    `require_scratch_home` still runs and still refuses an unset PERSONA_HOME
    or the default store — verified by running it, not assumed. Nothing here
    may set an env var that turns that refusal off.
    """
    assert "PERSONA_HOME" in script_text, "the runner does not set a scratch home"
    for banned in ("SKIP_GUARD", "FORCE", "--force", "ALLOW_DEFAULT_HOME"):
        assert banned not in script_text, (
            f"the runner appears to defeat the safety guard via {banned!r}"
        )


def test_the_real_harness_passes_through_the_runner() -> None:
    """The end-to-end path this CI step actually executes, run for real.

    Everything above pins shape; this one observes the gate doing its job. It
    costs well under a second because the no-launch lane launches no browser.
    """
    result = subprocess.run(
        [sys.executable, str(RUNNER_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "the no-launch behavioural lane did not pass through the runner:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "0 could not run" in result.stdout, (
        "a check could not run, so nothing was certified"
    )
