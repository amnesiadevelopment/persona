"""Pin the SHAPE of the protocol-conformance gate PS-165 added.

This gate exists because a protocol can silently stop describing its
implementation. The test file exists because THE GATE ITSELF can silently stop
checking — and a conformance gate is unusually easy to hollow out, since the
tree it runs against is clean, so a gate that checks nothing at all produces
exactly the same green.

So the central test here does NOT assert "there are no conformance errors
today". That assertion passes identically against a script whose body is
`return 0`. It INDUCES a disagreement, asserts the gate goes red, restores, and
asserts it goes green again — which is the only way to show the red is caused
by the drift rather than by nothing.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / ".github" / "scripts" / "check_protocol_conformance.py"
PROTOCOLS_REL = Path("src") / "interfaces" / "protocols.py"
PROTOCOLS = REPO_ROOT / PROTOCOLS_REL
MYPY_INI = REPO_ROOT / "mypy.ini"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def run_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    """Run the gate, optionally against a throwaway copy of the tree."""
    cmd = [sys.executable, str(GATE)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A DISPOSABLE copy of src/ to induce drift in.

    Verifying that this gate goes red requires editing a protocol so it
    disagrees with its implementation. Doing that in the working tree would
    leave the repo corrupted if the run were interrupted between the edit and
    the restore — on a suite with a watchdog that is a real possibility, not a
    theoretical one. So the mutation happens on a copy under tmp_path and the
    real tree is never written to at all.
    """
    shutil.copytree(REPO_ROOT / "src", tmp_path / "src")
    return tmp_path


def protocols_in(root: Path) -> Path:
    return root / PROTOCOLS_REL


# --------------------------------------------------------------------------
# The central test: the gate is RED for a REASON.
# --------------------------------------------------------------------------


def test_gate_goes_red_when_a_protocol_falls_behind_its_implementation(
    sandbox: Path,
) -> None:
    """Induce drift -> assert red -> restore -> assert green.

    The precondition is established INSIDE the test. Observing that the tree is
    clean today would pass against a gate that checks nothing; only showing the
    verdict CHANGE demonstrates the gate is wired to the thing it claims to
    watch.
    """
    target = protocols_in(sandbox)
    pristine = target.read_bytes()

    # Green before, so the red below cannot be blamed on a pre-existing state.
    before = run_gate(sandbox)
    assert before.returncode == 0, (
        "the gate is already failing before this test induced anything, so its "
        f"red proves nothing:\n{before.stdout}\n{before.stderr}"
    )

    # Induce the exact PS-165 drift: remove a parameter from the PROTOCOL that
    # the implementation still accepts.
    drifted, count = re.subn(
        r"\n *certificate: str \| None = None,",
        "",
        target.read_text(encoding="utf-8"),
        count=1,
    )
    assert count == 1, (
        "could not find IProfileManager.add_profile's `certificate` parameter "
        "to remove. If the protocol was legitimately restructured, re-point "
        "this test at another parameter — do NOT delete the test, or the gate "
        "stops being verified."
    )
    target.write_text(drifted, encoding="utf-8")

    during = run_gate(sandbox)
    assert during.returncode != 0, (
        "THE GATE IS VACUOUS: the protocol no longer declares a parameter its "
        "implementation accepts, and the gate still passed. It would not catch "
        "the drift it exists to catch.\n"
        f"stdout:\n{during.stdout}\nstderr:\n{during.stderr}"
    )
    assert "certificate" in during.stdout, (
        "the gate failed but never named the offending parameter, so a reader "
        f"cannot act on it:\n{during.stdout}"
    )
    assert "add_profile" in during.stdout, (
        f"the gate failed but never named the offending method:\n{during.stdout}"
    )

    # Restore and confirm the red was caused by the drift, not by the edit.
    target.write_bytes(pristine)
    after = run_gate(sandbox)
    assert after.returncode == 0, (
        "the gate stayed red after the induced drift was reverted, so its red "
        f"is not attributable to the drift:\n{after.stdout}\n{after.stderr}"
    )


def test_gate_goes_red_when_a_protocol_declares_a_method_twice(
    sandbox: Path,
) -> None:
    """The duplicate-declaration arm, induced the same way.

    IProfileManager really did declare `set_cookie_status` twice until PS-165.
    On a Protocol that creates a phantom member, so conformance failures
    elsewhere surface under the baffling name "set_cookie_status-redefinition".
    """
    target = protocols_in(sandbox)
    text = target.read_text(encoding="utf-8")
    needle = "    def set_cookie_status(self, name: str, status: str) -> bool: ...\n"
    assert text.count(needle) == 1, (
        "expected exactly one set_cookie_status declaration to duplicate; "
        f"found {text.count(needle)}"
    )
    target.write_text(text.replace(needle, needle + "\n" + needle, 1), encoding="utf-8")

    during = run_gate(sandbox)
    assert during.returncode != 0, (
        "the gate tolerates a method declared twice on a Protocol:\n"
        f"{during.stdout}"
    )
    assert "set_cookie_status" in during.stdout
    assert "more than once" in during.stdout


def test_gate_goes_red_when_a_protocol_class_goes_missing(
    sandbox: Path,
) -> None:
    """A protocol that describes nothing conforms to everything.

    Renaming or emptying a protocol must be a FAILURE, not a quiet pass — that
    is the difference between a gate and a decoration.
    """
    target = protocols_in(sandbox)
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(
            "class IProfileManager(Protocol):",
            "class IProfileManagerRenamed(Protocol):",
            1,
        ),
        encoding="utf-8",
    )

    during = run_gate(sandbox)
    assert during.returncode != 0, (
        "the gate passed while the protocol it names does not exist — it would "
        f"go green on a repo where the contract was deleted:\n{during.stdout}"
    )


def test_gate_passes_on_the_intended_tree() -> None:
    """The other half of AC5: green on the tree we actually ship.

    Weak ALONE (it is the assertion the tests above exist to compensate for),
    but load-bearing TOGETHER with them: it is what makes the induced reds
    above meaningful rather than a gate that is simply always red.
    """
    result = run_gate()
    assert result.returncode == 0, (
        "protocol conformance is failing on the current tree:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "IProfileManager" in result.stdout, (
        "the gate reported clean without naming the pairs it checked — an empty "
        f"check set would also print a clean result:\n{result.stdout}"
    )


def test_gate_checks_every_declared_protocol() -> None:
    """The scope cannot silently shrink to nothing.

    A gate whose pair list is emptied still exits 0 and still prints "clean".
    """
    checked = re.findall(r"^\[(?:ok|DRIFTED)\] (\w+)", run_gate().stdout, re.MULTILINE)
    declared = re.findall(r"^class (I\w+)\(Protocol\):", PROTOCOLS.read_text(encoding="utf-8"), re.MULTILINE)
    assert declared, "no Protocol classes found in protocols.py"
    missing = [p for p in declared if p not in checked]
    assert not missing, (
        f"these protocols are declared but the gate does not check them: {missing}. "
        "Add them to PAIRS in check_protocol_conformance.py."
    )


# --------------------------------------------------------------------------
# The config and the declaration (AC1, AC2).
# --------------------------------------------------------------------------


def test_mypy_ini_declares_each_section_exactly_once() -> None:
    sections = re.findall(r"^\[([^\]]+)\]", MYPY_INI.read_text(encoding="utf-8"), re.MULTILINE)
    duplicates = {s for s in sections if sections.count(s) > 1}
    assert not duplicates, f"mypy.ini declares these sections more than once: {duplicates}"


def test_mypy_ini_parses_without_raising() -> None:
    import configparser

    configparser.ConfigParser().read(MYPY_INI)  # raises DuplicateSectionError if broken


def test_mypy_ini_carries_the_flags_the_run_requires() -> None:
    """Local and CI must get the SAME answer.

    Without `no_site_packages` mypy follows imports into installed third-party
    code, hits a syntax error in a file that is not ours, and aborts the whole
    run with rc=2 having checked almost nothing. Encoded here rather than in a
    workflow line so a developer running mypy locally is not silently omitting
    the flag CI passes.
    """
    import configparser

    parser = configparser.ConfigParser()
    parser.read(MYPY_INI)
    assert parser.getboolean("mypy", "no_site_packages", fallback=False), (
        "mypy.ini does not set no_site_packages — the run will abort on a "
        "third-party syntax error instead of checking this repo"
    )
    assert parser.getboolean("mypy", "ignore_missing_imports", fallback=False), (
        "no_site_packages without ignore_missing_imports fills the log with "
        "import-not-found noise; the two must move together"
    )


def test_mypy_is_declared_with_a_version_bound() -> None:
    """An unpinned checker can change its verdict with no change to this repo."""
    line = next(
        (
            ln
            for ln in REQUIREMENTS_DEV.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("mypy")
        ),
        None,
    )
    assert line is not None, "mypy is not declared in requirements-dev.txt"
    assert re.search(r"[<>=]", line), f"mypy is declared with no version constraint: {line!r}"
    assert "<" in line, (
        f"mypy has no UPPER bound: {line!r}. mypy's diagnostics are "
        "version-sensitive, so a bare floor lets a fresh release change the "
        "gate's verdict with no change to this repo."
    )


# --------------------------------------------------------------------------
# The CI wiring (AC3, AC4): advisory is advisory, gating is gating.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _steps(ci_yaml) -> list[dict]:
    return [s for job in ci_yaml["jobs"].values() for s in job.get("steps", []) or []]


def _step_named(ci_yaml, fragment: str) -> dict:
    """Find the ONE step whose name contains `fragment`, case-insensitively.

    Case-insensitive on purpose: the fragment describes the step's JOB, not its
    exact prose. Pinning capitalisation would make renaming a step title a test
    failure, which trains people to edit the test.

    AMBIGUITY IS AN ERROR, not a first-match. "type check" also appears in
    "Install the type checker", and silently taking the first hit made these
    tests assert against the install step — they failed while the wiring they
    were checking was in fact correct. A test that quietly examines the wrong
    object is worse than one that fails, so say so instead.
    """
    needle = fragment.lower()
    hits = [s for s in _steps(ci_yaml) if needle in (s.get("name") or "").lower()]
    if not hits:
        raise AssertionError(
            f"no CI step whose name contains {fragment!r}; steps are "
            f"{[s.get('name') for s in _steps(ci_yaml)]}"
        )
    if len(hits) > 1:
        raise AssertionError(
            f"{fragment!r} matches more than one step "
            f"({[s.get('name') for s in hits]}) — narrow the fragment so this "
            f"test pins the step it means"
        )
    return hits[0]


def test_ci_runs_mypy_repo_wide(ci_yaml) -> None:
    step = _step_named(ci_yaml, "repo-wide")
    assert "mypy" in step.get("run", ""), f"the advisory step does not invoke mypy: {step}"


def test_ci_advisory_mypy_run_cannot_fail_the_build(ci_yaml, tmp_path: Path) -> None:
    """AC4: the repo-wide run must PRINT, not BLOCK.

    97 pre-existing errors remain on this tree. A gating repo-wide run would be
    red permanently, and the reflex fix for that (a blanket `ignore_errors`) is
    the exact defect this ticket removed.

    ASSERTED BEHAVIOURALLY, by RUNNING the step's script, not by looking for
    the string `continue-on-error` in it. Two reasons, and the second is the
    one that matters:

      * this workflow is forbidden from using `continue-on-error` at all
        (tests/test_ci_verification_gates.py) — at job scale it is how a whole
        platform stops being verified — so the step is made non-gating by
        CATCHING mypy's exit status instead;
      * a scan for a known-good spelling passes on any FUTURE spelling that
        merely looks similar. Executing the script proves the property itself:
        mypy fails, the step still exits 0.
    """
    step = _step_named(ci_yaml, "repo-wide")
    script = step.get("run", "")
    assert "mypy" in script, f"the advisory step does not invoke mypy: {script!r}"

    # Substitute a stub `mypy` that always fails, so this test measures the
    # SCRIPT's error handling rather than today's error count. If the tree were
    # ever clean, a real invocation would exit 0 for the wrong reason and this
    # test would pass vacuously.
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "python"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "stub mypy: pretending to find pre-existing errors" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = subprocess.run(
        ["bash", "-e", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, (
        "the repo-wide mypy step FAILS when mypy reports errors, but the tree "
        "still carries pre-existing ones — this step would be red permanently, "
        "and the reflex cure for that is the blanket ignore this ticket "
        f"removed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "exit status 1" in result.stdout, (
        "the step swallowed mypy's failure without reporting it — an advisory "
        f"run that hides its own result advises nobody:\n{result.stdout}"
    )


def test_ci_runs_the_conformance_gate(ci_yaml) -> None:
    step = _step_named(ci_yaml, "protocol conformance")
    assert "check_protocol_conformance.py" in step.get("run", "")


def test_ci_conformance_gate_result_is_not_swallowed(ci_yaml) -> None:
    """The narrow gate is the one that MUST be able to fail the build.

    This is the asymmetry that makes the pair honest: advisory-wide, gating on
    the one class that is actually clean. If this step is ever marked
    continue-on-error, both halves become decorative.
    """
    step = _step_named(ci_yaml, "protocol conformance")
    assert step.get("continue-on-error") is not True, (
        "the conformance gate is continue-on-error — its red would block nothing"
    )
    run = step.get("run", "")
    for banned in ("|| true", "|| exit 0", "| true"):
        assert banned not in run, f"the conformance gate's failure is discarded: {run!r}"
