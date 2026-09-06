"""Pin the SHAPE of the behavioural-checks CI gate PS-315 added.

This gate exists because `behaviour_cli` was a working instrument that nothing
executed, so its check bodies could rot while the suite stayed green. A gate
added to fix that can be un-fixed in six quiet ways, each of which leaves a
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
    the job, so a run that COULD NOT LOOK starts reading as a pass;
  * the lane SELECTS NOTHING and the harness exits 0 over an empty world,
    printing "the behaviour held" having held nothing — `exit_code([])` is
    `EXIT_OK` because all three of its `any()` calls are false over an empty
    list, and `--skip-launch` filters the registry with no floor on what
    survives;
  * the harness never STARTS — a bad invocation or an import error exits 1,
    Python's code for an uncaught exception, which aliases `EXIT_FINDING` and
    reports "nothing was measured" as "the product is broken".

The last two are this ticket's own thesis turned on the gate built to remove
it, and both were REPRODUCED by hand before the corroboration rules existed:
flipping the three `needs_launch=False` flags gave `GATE EXIT: 0` over "0
passed", and running the script from `/tmp` gave `No module named 'src'`
announced as a FINDING at exit 1. The tests below drive those same two states,
so each rule is pinned by a test that fails without it — not by a comment.

So these assert the properties that make the gate MEAN something, in the spirit
of tests/test_ci_verification_gates.py, rather than that a YAML key exists.
"""

from __future__ import annotations

import importlib.util
import os
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

#: The three no-launch checks the lane must certify. Read from the runner in
#: `test_the_expected_checks_are_the_registry_no_launch_lane` rather than
#: trusted, so a drift between this list and the registry is a test failure
#: instead of a gate that quietly stops requiring one of them.
EXPECTED = (
    "proxy-assignment-survives-edit",
    "launch-refuses-broken-geography",
    "certificate-key-material",
)


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


@pytest.fixture(scope="module")
def runner():
    """The runner module itself, so its adjudication rules can be driven.

    Imported by path because `.github/scripts/` is not a package and must not
    become one — the CI step invokes the file directly.
    """
    spec = importlib.util.spec_from_file_location("_ps315_runner", RUNNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _index_of(steps: list[dict], name: str) -> int:
    for i, step in enumerate(steps):
        if step.get("name") == name:
            return i
    raise AssertionError(f"no step named {name!r} in the tests job")


def _report(passed: "list[str]", findings: int = 0, blocked: int = 0) -> str:
    """A report in `behaviour.format_report`'s shape.

    Only the two lines the runner reads are reproduced — the `[PASS]` badges
    and the summary line. `test_the_runner_reads_the_real_report_format` holds
    this stand-in to the real harness's output, so a format drift breaks a test
    here rather than only a CI run.
    """
    lines = [f"[PASS] {name}\n  surface: whatever\n" for name in passed]
    lines.append(
        f"{len(passed)} passed, {findings} finding(s), {blocked} could not run, "
        "0 browser launch(es)"
    )
    return "\n".join(lines)


def _drive(tmp_path, *, code: int, stdout: str = "") -> subprocess.CompletedProcess:
    """Run the real `main()` against a substituted child that exits `code`.

    The child is a real process printing a real report, so this observes the
    runner's end-to-end behaviour — including the capture and the exit — rather
    than asserting it of the source text.
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
        "import run_behaviour_checks as r\n"
        f"r.COMMAND = [sys.executable, {str(child)!r}]\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


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

    Each child prints a report CONSISTENT with the code it exits on, because
    the runner corroborates 0 and 1 against the report rather than trusting the
    number (see the corroboration section below). Propagation is the property
    under test here; feeding an inconsistent report would be testing the other
    rule and would let this test pass for the wrong reason.
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
    """Exit 2 is NOT a pass. This is the whole reason the split exists.

    A run that could not look reporting success is the defect the behavioural
    harness was built to catch; letting it through here would reintroduce it in
    the gate meant to prevent it.
    """
    result = _drive(
        tmp_path, code=2, stdout=_report(list(EXPECTED)[:-1], blocked=1)
    )

    assert result.returncode != 0, "exit 2 (nothing was measured) passed the job"
    assert result.returncode == 2


def test_an_unexpected_zero_is_not_reported_as_a_pass(tmp_path) -> None:
    """A harness that exits 0 without speaking its vocabulary still fails.

    Guards the one direction the parametrised test above cannot: a code outside
    {0,1,2} is mapped to 2, and 0 must never be reachable that way. Driven with
    a harness whose report the runner cannot recognise at all — an empty
    stdout, which is what a process that died before printing leaves behind.
    """
    result = _drive(tmp_path, code=0, stdout="")

    assert result.returncode == 2, (
        "an unrecognised verdict was reported as a pass; it certifies nothing"
    )


# --- a 0 is only a pass if the report CERTIFIES the expected checks ----------
#
# `run_checks(..., skip_launch=True)` filters the registry with no floor on what
# survives, and `exit_code([])` returns EXIT_OK because all three of its `any()`
# calls are false over an empty list. Reproduced by hand before these tests
# existed: flipping the three `needs_launch=False` flags gives
#
#     0 passed, 0 finding(s), 0 could not run, 0 browser launch(es)
#     verdict: every selected check ran ... and the behaviour held
#     GATE EXIT: 0
#
# — "the behaviour held" over NOTHING, and the job green. That is this ticket's
# own sentence turned on the fix: an expensive check that is permanently green
# because it quietly stopped looking.


def test_an_empty_lane_is_not_a_pass(tmp_path) -> None:
    """THE regression test for the audit's blocking finding.

    The harness's own words for this state are "0 passed, 0 finding(s), 0 could
    not run" at exit 0. Honouring that 0 is the defect.
    """
    result = _drive(tmp_path, code=0, stdout=_report([]))

    assert result.returncode == 2, (
        "the gate went GREEN over a lane that selected NOTHING. The harness "
        "exits 0 on an empty selection and prints 'the behaviour held' having "
        "held nothing — a permanently-green check that quietly stopped looking"
    )


def test_a_partly_certified_lane_is_not_a_pass(tmp_path) -> None:
    """Two of three is not three.

    The empty case is the dramatic one, but the likelier one is a single check
    being renamed or retired — which shrinks the lane silently while the
    remaining checks keep the summary looking healthy.
    """
    result = _drive(tmp_path, code=0, stdout=_report(list(EXPECTED)[:-1]))

    assert result.returncode == 2, (
        f"the gate honoured a 0 while {EXPECTED[-1]!r} was never certified"
    )


def test_the_names_of_the_missing_checks_are_reported(tmp_path) -> None:
    """A downgrade must say WHICH check went missing.

    This is why EXPECTED_CHECKS is a list of names and not a count: "expected 3,
    got 2" sends the reader to the registry to work out which one, and the
    runner already knows.
    """
    result = _drive(tmp_path, code=0, stdout=_report([EXPECTED[0]]))
    combined = result.stdout + result.stderr

    for missing in EXPECTED[1:]:
        assert missing in combined, (
            f"the downgrade message does not name {missing!r}, so the log says "
            "a check is missing without saying which"
        )


def test_a_fully_certified_lane_still_passes(tmp_path) -> None:
    """The corroboration must not make a genuine pass impossible.

    Without this, a rule that returned 2 unconditionally would satisfy every
    test above — a gate that is permanently RED proves as little as one that is
    permanently green, and it would be "fixed" by being disabled.
    """
    result = _drive(tmp_path, code=0, stdout=_report(list(EXPECTED)))

    assert result.returncode == 0, (
        "a lane that certified all three expected checks was refused:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_extra_checks_do_not_break_a_pass(tmp_path) -> None:
    """The rule is "at least these", so ADDING a no-launch check is not a break.

    Retiring one should force somebody to look at EXPECTED_CHECKS; adding one
    should not turn CI red on an unrelated PR.
    """
    result = _drive(
        tmp_path, code=0, stdout=_report([*EXPECTED, "some-new-no-launch-check"])
    )

    assert result.returncode == 0, (
        "adding a no-launch check turned the gate red, which would make the "
        "constant brittle in the useless direction"
    )


def test_the_expected_checks_are_the_registry_no_launch_lane(runner) -> None:
    """EXPECTED_CHECKS must BE the lane, not a list that drifted from it.

    The constant is written out by hand deliberately — deriving it from the
    registry would make it agree with an EMPTY registry by construction, which
    is the failure being guarded against. This test is what keeps the hand-
    written copy honest: it holds the constant to the real registry, so
    retiring a no-launch check fails HERE, in a test that names the check,
    rather than only on a runner.
    """
    from src.services.verify.behaviour_checks import CHECKS

    lane = {c.name for c in CHECKS if not c.needs_launch}

    assert set(runner.EXPECTED_CHECKS) == lane, (
        "EXPECTED_CHECKS has drifted from the registry's no-launch lane.\n"
        f"  constant: {sorted(runner.EXPECTED_CHECKS)}\n"
        f"  registry: {sorted(lane)}\n"
        "If a check was retired or renamed, update the constant deliberately — "
        "that edit is meant to be noticed, not absorbed."
    )


# --- a 1 is only a finding if the harness LIVED to report one -----------------
#
# Python exits 1 on an uncaught exception, which aliases EXIT_FINDING.
# `behaviour_cli` guards its own seams against that collision (it translates
# BaselineUnavailable into EXIT_CANNOT_RUN precisely so the codes cannot alias)
# but it cannot guard a failure that happens BEFORE it loads. Reproduced by
# hand before these tests existed, running the script from /tmp:
#
#     No module named 'src'
#     verdict: a check RAN and the behaviour did NOT hold — this is a FINDING
#
# The harness never started, and the gate announced a defect in the product.


def test_a_harness_that_never_started_is_not_a_finding(tmp_path) -> None:
    """No report at all means nothing was measured — code 2, never 1."""
    result = _drive(tmp_path, code=1, stdout="")

    assert result.returncode == 2, (
        "an exit 1 with NO report was reported as a finding about the product. "
        "Exit 1 is also Python's code for an uncaught exception, so a failure "
        "before the harness loads must land on 2"
    )


def test_an_exit_one_whose_report_states_no_finding_is_not_a_finding(
    tmp_path,
) -> None:
    """A code and a report that disagree certify nothing."""
    result = _drive(tmp_path, code=1, stdout=_report(list(EXPECTED)))

    assert result.returncode == 2, (
        "the runner honoured an exit 1 over a report stating 0 finding(s)"
    )


def test_a_real_finding_still_exits_one(tmp_path) -> None:
    """The corroboration must not swallow a genuine finding into 2.

    1 and 2 mean different things — the product misbehaved vs the check could
    not look — and collapsing a real finding into "nothing was measured" would
    lose exactly the distinction this gate exists to preserve.
    """
    result = _drive(
        tmp_path, code=1, stdout=_report(list(EXPECTED)[:-1], findings=1)
    )

    assert result.returncode == 1, (
        "a genuine finding about the product was downgraded to 'nothing was "
        "measured'; the two failures must stay distinguishable"
    )


def test_no_corroboration_rule_can_make_the_job_greener(runner) -> None:
    """Every correction moves a verdict TOWARDS 2, never towards 0.

    The asymmetry is the safety argument for adding adjudication at all: this
    script may make the job redder than the harness asked for, and can never
    make it greener. Swept over the report shapes the rules distinguish, so a
    future rule that returned 0 on some new input fails here.
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
            code, _why = runner.adjudicate(rc, report)
            assert code >= rc or code == 2, (
                f"adjudicate({rc}, ...) returned {code} — a correction made the "
                "verdict GREENER than the harness claimed"
            )
            if rc != 0:
                assert code != 0, (
                    f"adjudicate({rc}, ...) returned 0 — a non-pass became a pass"
                )


# --- the corroboration reads the report the harness ACTUALLY prints ----------


def test_the_runner_reads_the_real_report_format() -> None:
    """The rules above are only as good as their grip on the real output.

    Every corroboration test drives a stand-in report. If `format_report`'s
    shape ever drifts — the summary line, the `[PASS]` badges — those tests
    would keep passing against a format the runner can no longer read, and the
    gate would downgrade every real run to 2. This holds the parser to the
    harness's own output rather than to the stand-in.
    """
    from src.services.verify.behaviour import format_report

    spec = importlib.util.spec_from_file_location("_ps315_runner_fmt", RUNNER_SCRIPT)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    from src.services.verify.behaviour import PASS, Outcome

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
    assert counts is not None and counts[0] == len(EXPECTED), (
        f"the runner cannot read the real summary line (got {counts!r})"
    )


# --- the child WRITES what this parent claims to READ ------------------------
#
# `encoding="utf-8"` on subprocess.run governs the DECODE only. Nothing governs
# the child's ENCODE, and a Python process whose stdout is a PIPE on Windows
# resolves to the ANSI code page. Measured on run `34004209963`, the SAME line
# off two legs of the same commit:
#
#     ubuntu-24.04    b'behaviour \xe2\x80\x94 whether a SOCKS handshake'
#     windows-latest  b'behaviour \xef\xbf\xbd whether a SOCKS handshake'
#
# U+FFFD is not a rendering artifact — it is the record of a decode that already
# failed. These drive a child under a hostile console so the seam is exercised
# on every platform, rather than being observable only on the one leg where it
# breaks.


def _console_child(tmp_path, text: str) -> Path:
    """A child that writes `text` then a well-formed summary line."""
    child = tmp_path / "console_child.py"
    child.write_text(
        "import sys\n"
        f"sys.stdout.write({text!r} + '\\n')\n"
        "sys.stdout.write('[PASS] proxy-assignment-survives-edit\\n')\n"
        "sys.stdout.write('[PASS] launch-refuses-broken-geography\\n')\n"
        "sys.stdout.write('[PASS] certificate-key-material\\n')\n"
        "sys.stdout.write('3 passed, 0 finding(s), 0 could not run\\n')\n",
        encoding="utf-8",
    )
    return child


def _run_under_console(tmp_path, child: Path, console: str):
    """Run the real `main()` with the AMBIENT console forced to `console`.

    `PYTHONIOENCODING` is set on the PARENT's environment, which the runner
    copies into the child's — so if the runner did not pin the child's stream,
    the child inherits this hostile value. That is the Windows condition,
    reproduced on any platform.
    """
    driver = tmp_path / "console_driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(RUNNER_SCRIPT.parent)!r})\n"
        "import run_behaviour_checks as r\n"
        f"r.COMMAND = [sys.executable, {str(child)!r}]\n"
        "raise SystemExit(r.main())\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = console
    return subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True,
        env=env,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def test_a_non_ascii_report_survives_a_cp1252_console(tmp_path) -> None:
    """THE regression test for the false claim in round 2's record.

    The em-dash exists in cp1252 (0x97), so an unpinned child does not raise —
    it round-trips LOSSILY, and the log silently shows U+FFFD where the report
    said something. That is the defect measured on windows-latest.
    """
    child = _console_child(tmp_path, "certificate key material \u2014 not the session")
    result = _run_under_console(tmp_path, child, "cp1252")

    assert "\ufffd" not in result.stdout, (
        "the child's report came back with U+FFFD — the replacement character "
        "means a decode already failed and threw the original away. The child's "
        "stdout encoding is not pinned:\n"
        f"{result.stdout}"
    )
    assert "\u2014" in result.stdout, "the em-dash did not survive the round trip"
    assert result.returncode == 0


def test_a_character_outside_cp1252_does_not_crash_the_child(tmp_path) -> None:
    """The escalation, and the reason the lossy round trip is worth blocking on.

    A character cp1252 CANNOT represent does not degrade — the child's writer
    RAISES, so it exits 1 with the summary line never printed. That is
    EXIT_FINDING's code produced by a crash on a truncated report: the exact
    1-vs-2 collision this gate exists to close.

    `adjudicate` refuses it either way (no summary -> 2), and
    `test_a_harness_that_never_started_is_not_a_finding` pins that. This asserts
    the stronger property: the crash does not happen at all, so a real verdict
    is not lost to a reporting seam.
    """
    child = _console_child(tmp_path, "check \u2713 passed")
    result = _run_under_console(tmp_path, child, "cp1252")

    assert "UnicodeEncodeError" not in result.stdout + result.stderr, (
        "the child raised while ENCODING its report, so the summary was never "
        "printed and a crash wore EXIT_FINDING's code:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "3 passed" in result.stdout, (
        "the summary line never arrived — the report was truncated by the "
        "reporting seam, not by anything the product did"
    )
    assert result.returncode == 0, (
        f"a healthy lane failed under a cp1252 console (rc={result.returncode})"
    )


def test_the_parents_own_verdict_survives_a_cp1252_console(tmp_path) -> None:
    """The parent has the same problem, and pinning only the child misses it.

    `VERDICTS`, `_DOWNGRADE` and the failure banner all contain an em-dash, and
    this process's own stdout resolves to the console codec too. Measured on
    the falsification run `34000438536`, windows leg — the PARENT's line:

        b'Nothing was certified \\xef\\xbf\\xbd this is NOT a pass'

    Driven through the DOWNGRADE path, because that is where the parent emits
    the most text of its own.
    """
    child = tmp_path / "empty_lane.py"
    child.write_text(
        "import sys\n"
        "sys.stdout.write('0 passed, 0 finding(s), 0 could not run\\n')\n",
        encoding="utf-8",
    )
    result = _run_under_console(tmp_path, child, "cp1252")

    assert result.returncode == 2, "the empty lane should have been downgraded"
    assert "\ufffd" not in result.stdout + result.stderr, (
        "the PARENT's own downgrade text came back with U+FFFD, so the gate's "
        "verdict is corrupted on any platform with a non-utf-8 console:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_runner_pins_the_childs_stdout_encoding(runner) -> None:
    """The mechanism, asserted directly rather than only through its effect.

    The behavioural tests above would also pass if someone "fixed" this by
    stripping non-ASCII from the report, which would destroy the log's content
    to protect its encoding. This names the actual remedy.
    """
    source = RUNNER_SCRIPT.read_text(encoding="utf-8")

    assert 'env["PYTHONIOENCODING"] = "utf-8"' in source, (
        "the runner no longer pins the child's stdout encoding, so the child "
        "writes under the platform console while this parent decodes utf-8"
    )
    assert getattr(runner, "echo", None) is not None, (
        "the byte-safe writer is gone; the parent's own verdict text would be "
        "re-encoded through the console codec"
    )


# --- the command is anchored, so `-m` cannot resolve against the caller ------


def test_the_gate_runs_from_the_repo_root_wherever_it_is_invoked(tmp_path) -> None:
    """`python -m` resolves against the CALLER's cwd.

    CI happens to run at the repo root, so an unanchored command works there —
    which makes it latent rather than safe, and a latent gap in a gate is what
    nobody notices. Measured before the anchor existed: from /tmp the command
    gave `No module named 'src'`, and the gate announced it as a FINDING about
    the product at exit 1.

    Run from a directory that is emphatically not the repo, so a regression
    here cannot pass by accident.
    """
    result = subprocess.run(
        [sys.executable, str(RUNNER_SCRIPT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "No module named" not in (result.stdout + result.stderr), (
        "the runner could not import the harness from a foreign cwd — `-m` is "
        "resolving against the caller's directory instead of the repo root"
    )
    assert result.returncode == 0, (
        "the no-launch lane did not pass when invoked from outside the repo:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_a_gate_pointed_at_a_missing_harness_says_so(tmp_path, runner) -> None:
    """If the module file is gone, that is "nothing was measured", not a pass.

    Checked before the run so the log says the gate is pointed at nothing,
    rather than leaving an opaque child exit code to be interpreted.
    """
    assert runner.MODULE_FILE.is_file(), (
        f"{runner.MODULE_FILE} does not exist — the runner's own module path "
        "no longer resolves, so its pre-flight would refuse every run"
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

    ⚠️ THE ASSERTIONS ARE ON WHAT WAS CERTIFIED, NOT ON THE EXIT CODE ALONE.
    An earlier version asserted `returncode == 0` and `"0 could not run" in
    stdout`, and BOTH are satisfied by a run that measured nothing: zero checks
    produce zero "could not run". It passed against a tree with the lane
    flipped empty — a vacuously-green test guarding a vacuously-green gate,
    which is the exact shape this ticket's charter calls "an expensive check
    that is permanently green because it quietly stopped looking".
    """
    result = subprocess.run(
        [sys.executable, str(RUNNER_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, (
        "the no-launch behavioural lane did not pass through the runner:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    for name in EXPECTED:
        assert f"[PASS] {name}" in result.stdout, (
            f"the lane did not certify {name!r} — a green run that did not "
            "measure this check proves nothing about it:\n"
            f"{result.stdout}"
        )
    assert f"{len(EXPECTED)} passed" in result.stdout, (
        "the summary does not report every expected check as passed:\n"
        f"{result.stdout}"
    )
    assert "0 could not run" in result.stdout, (
        "a check could not run, so nothing was certified"
    )
