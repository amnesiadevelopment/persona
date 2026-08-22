"""Tests for .github/workflows/engine-autoupdate.yml — the WIRING itself.

This ticket's defect was "the observer is built and connected to nothing". The
connection IS the deliverable, and until this file existed it was the one part
of the change with no automated check on it: `grep -rn engine-autoupdate.yml
tests/` returned nothing while every other piece had a suite.

That gap is not theoretical. Every defect found in this change so far lived in
YAML, not in the module:

  * the daily job provisioned a browser even when there was nothing to bump;
  * the commit+tag step was guarded only on `bump.outputs.version`, so a
    SKIPPED gate step would still tag — a skipped step does not fail a job;
  * the "after" side installed the bumped driver with `--no-deps` and never
    installed `invisible_core`, so the recording was taken on new binary + new
    driver + OLD core: a stack nobody ships, invisible to the gate's own
    engine-build guard because the binary genuinely did move.

Prose in a workflow comment does not enforce any of that. What follows pins the
invariants that keep the gate CONNECTED, so a future refactor that silently
disconnects it fails here instead of at 06:00 UTC on a morning nobody is
watching.

These tests read the YAML as data. They deliberately do NOT run the workflow —
that needs a real runner, a display and a real engine, none of which exist in
this container (the same constraint test_verify_baseline.py documents).
"""

import pathlib
import re

import pytest
import yaml

WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "engine-autoupdate.yml"
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow) -> list:
    return workflow["jobs"]["check-and-bump"]["steps"]


def _by_id(steps: list, step_id: str) -> dict:
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id={step_id!r}")


def _named(steps: list, fragment: str) -> list:
    return [s for s in steps if fragment.lower() in (s.get("name") or "").lower()]


def _runs(steps: list) -> str:
    """Every `run:` body in the job, concatenated."""
    return "\n".join(s.get("run") or "" for s in steps)


def _joined(steps: list) -> str:
    """Every `run:` body with shell line-continuations folded onto one line.

    The record invocations are written across two lines with a trailing ``\\``,
    so a naive per-line scan sees ``engine_gate record \\`` and concludes the
    ``--output`` is missing — which is a bug in the reader, not in the workflow.
    """
    return re.sub(r"\\\n\s*", " ", _runs(steps))


# --- the file is wired in at all --------------------------------------------


def test_the_workflow_exists_and_parses():
    assert WORKFLOW.exists(), f"{WORKFLOW} is missing — the gate is connected to nothing"
    assert yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_gate_module_is_actually_invoked(steps):
    """The whole point of the ticket. `grep -rn verify .github/` returned no hit
    before this change; if it ever does again, the observer is disconnected."""
    assert "src.services.verify.engine_gate" in _runs(steps)


# --- THE TAG IS GATED ON THE GATE'S OWN VERDICT -----------------------------
#
# A SKIPPED step does not fail a job. So guarding the tag on "a bump exists"
# lets a gate whose `if:` stopped matching be silently skipped while the tag is
# still cut and the job reads green — the same class of failure the gate itself
# is built against: a confident pass over a comparison that never happened.


def test_the_tag_step_is_guarded_on_the_gate_verdict(steps):
    tag_steps = _named(steps, "commit + tag")
    assert len(tag_steps) == 1, "expected exactly one commit+tag step"
    condition = tag_steps[0].get("if") or ""
    assert "steps.gate.outputs.verdict" in condition, (
        "the tag step must be guarded on the GATE'S OWN VERDICT. Guarding it on "
        "the bump alone lets a skipped gate tag an unverified release while the "
        f"job reads green. Found: {condition!r}"
    )


def test_the_tag_step_is_not_guarded_on_the_bump_alone(steps):
    condition = _named(steps, "commit + tag")[0].get("if") or ""
    assert condition.strip() != "", "the tag step is unguarded"
    # A guard mentioning only the bump is exactly the defect this replaced.
    assert not re.fullmatch(
        r"\s*steps\.bump\.outputs\.version\s*!=\s*'none'\s*", condition
    ), "the tag step regressed to being guarded on the bump alone"


def test_the_verdict_token_is_written_by_exactly_one_step(steps):
    """If any other step could write it, the tag's guard stops meaning
    "the comparison ran and passed"."""
    writers = [s for s in steps if "verdict=pass" in (s.get("run") or "")]
    assert len(writers) == 1, (
        "exactly one step may write verdict=pass — otherwise the tag guard no "
        f"longer proves the gate ran. Writers: {[s.get('name') for s in writers]}"
    )
    assert writers[0].get("id") == "gate"


def test_the_verdict_is_the_last_line_of_the_gate_step(steps):
    """It must be unreachable unless the compare above it exited 0. A line
    AFTER it would run even when the comparison failed."""
    body = _by_id(steps, "gate")["run"]
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    assert "verdict=pass" in lines[-1], (
        "verdict=pass must be the LAST line of the gate step, so it is only "
        f"reached when the comparison exited 0. Last line: {lines[-1]!r}"
    )


def test_the_gate_step_runs_the_compare_subcommand(steps):
    body = _by_id(steps, "gate")["run"]
    assert "engine_gate compare" in body


# --- THE RE-RECORD TRAP, closed in the WIRING as well as in the module ------
#
# A job that re-records the reference until it goes green destroys the artifact
# it exists to defend. The module has no accept path; this pins that the
# WORKFLOW cannot reach one either.


def test_no_step_records_over_the_committed_reference(steps):
    """`baseline_cli record` defaults its --output to the committed artifact."""
    assert "baseline_cli" not in _runs(steps), (
        "no step may invoke baseline_cli: its default --output IS the committed "
        "reference, so a stray CI invocation would silently overwrite it. "
        "Accepting a move is a deliberate human act."
    )


def test_every_record_invocation_passes_an_explicit_output(steps):
    """`engine_gate record` requires --output and has no default, but a caller
    could still point it somewhere wrong — pin that both sides name a throwaway."""
    invocations = re.findall(r"engine_gate record[^\n]*", _joined(steps))
    assert len(invocations) == 2, f"expected two record calls, found {len(invocations)}"
    for call in invocations:
        assert "--output" in call, f"record without an explicit --output: {call!r}"
        assert "baseline" not in call, (
            f"a record call points at something baseline-shaped: {call!r}"
        )


def test_the_two_recordings_go_to_different_files(steps):
    outputs = re.findall(r"--output\s+(\S+)", _joined(steps))
    assert len(outputs) == 2
    assert outputs[0] != outputs[1], (
        "both sides recorded to the same path — the second would overwrite the "
        "first and the gate would compare a file with itself"
    )


# --- BOTH SIDES ARE RECORDED IN ONE JOB ON ONE RUNNER -----------------------


def test_the_workflow_has_exactly_one_job(workflow):
    """Two jobs are two runners. Several probes read the host's GL stack and
    installed fonts, so a split would report host variance as engine drift and
    the gate would be permanently red — a gate people learn to ignore."""
    assert list(workflow["jobs"]) == ["check-and-bump"]


def test_both_recordings_need_a_display(steps):
    """No display server on bare ubuntu-24.04; a record without xvfb-run cannot
    launch a browser at all."""
    for call in re.findall(r"[^\n]*engine_gate record[^\n]*", _runs(steps)):
        assert "xvfb-run" in call, f"record without a display: {call!r}"


def test_the_before_recording_precedes_the_bump(steps):
    """The "before" side must read the engine being bumped AWAY from. Once the
    pins are rewritten the old engine is no longer resolvable from the tree, so
    there is no later point at which this recording could be taken."""
    names = [s.get("name") or "" for s in steps]
    before = next(i for i, n in enumerate(names) if "OLD engine" in n)
    bump = next(i for i, n in enumerate(names) if "Detect newer engine" in n)
    after = next(i for i, n in enumerate(names) if "BUMPED engine" in n)
    assert before < bump < after


# --- THE ENGINE STACK MOVES IN LOCKSTEP -------------------------------------
#
# The defect this round fixed. `--no-deps` means invisible_core is NOT pulled
# transitively, and nothing else in the job supplies it: requirements.txt does
# not declare it, and no step installs from pyproject's `dependencies` — which
# is the only place the pin lives. Without an explicit install the "after" side
# records new binary + new driver + OLD core, a stack that will never ship.
#
# `require_engine_moved` CANNOT catch this: the binary genuinely moved, so the
# guard is satisfied and the gate proceeds to certify.


def _provision_steps(steps: list) -> list:
    return [s for s in steps if "Provision" in (s.get("name") or "")]


def test_there_are_two_provision_steps(steps):
    assert len(_provision_steps(steps)) == 2


@pytest.mark.parametrize("side", ["CURRENT", "BUMPED"])
def test_each_side_installs_invisible_core_explicitly(steps, side):
    """The regression test for this round's blocking defect."""
    step = next(s for s in _provision_steps(steps) if side in s["name"])
    body = step["run"]
    assert "invisible_core" in body, (
        f"the {side} side never installs invisible_core. `--no-deps` does not "
        "pull it, requirements.txt does not declare it, and no step installs "
        "from pyproject's dependencies — so the recording would be taken "
        "against a stale core: a stack nobody ships, and one the engine-build "
        "guard cannot see because the binary really did move."
    )
    assert re.search(r'pip install[^\n]*"\$core"', body), (
        f"the {side} side reads an invisible_core pin but never installs it"
    )


@pytest.mark.parametrize("side", ["CURRENT", "BUMPED"])
def test_each_side_reads_the_core_pin_from_pyproject(steps, side):
    """pyproject is the ONLY file carrying invisible_core — requirements.txt's
    single occurrence is prose in a psutil comment."""
    body = next(s for s in _provision_steps(steps) if side in s["name"])["run"]
    core_line = next(ln for ln in body.splitlines() if ln.strip().startswith("core="))
    assert "pyproject.toml" in core_line
    assert "invisible_core==" in core_line


def test_the_core_pin_is_not_declared_in_requirements():
    """Pins the PREMISE of the tests above rather than the workflow itself. If
    requirements.txt ever grows a real invisible_core pin, the explicit installs
    stop being load-bearing — and, worse, the two files could then disagree
    about which core ships. Fail here and re-reason, rather than silently
    keeping a guard whose justification has evaporated."""
    root = pathlib.Path(__file__).resolve().parents[1]
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "invisible_core" not in stripped, (
            "requirements.txt now declares invisible_core: re-read the explicit "
            "core installs in engine-autoupdate.yml, they may now conflict"
        )


def test_the_bumped_side_reinstalls_rather_than_reusing_the_old_resolution(steps):
    """pip leaves an already-satisfied requirement alone, so the bumped side
    has to force it: the old core is already installed at that point."""
    body = next(s for s in _provision_steps(steps) if "BUMPED" in s["name"])["run"]
    assert re.search(r'pip install --force-reinstall[^\n]*"\$core"', body), (
        "the bumped core must be force-reinstalled — the old one is already "
        "present from the CURRENT side, and pip would consider it satisfied"
    )


# --- provisioning is not paid for on the ~365 days a year with no bump ------


def test_the_browser_is_provisioned_only_when_a_bump_is_available(steps):
    """A job that downloads a browser every morning to discover it had nothing
    to do is a job with a daily chance of failing on a flaky download — which
    trains the operator to ignore a red run, exactly like an always-red gate."""
    for step in _provision_steps(steps):
        condition = step.get("if") or ""
        assert condition, f"{step['name']!r} runs unconditionally"
        assert "precheck" in condition or "bump.outputs" in condition


def test_the_recordings_are_kept_when_the_gate_stops_the_bump(steps):
    """On a red gate the two recordings are the whole evidence base for
    deciding "benign or leak?", and they live on a runner about to be
    destroyed."""
    uploads = [s for s in steps if "upload-artifact" in (s.get("uses") or "")]
    assert len(uploads) == 1
    assert "failure()" in (uploads[0].get("if") or "")
