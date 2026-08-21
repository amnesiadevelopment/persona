"""Pin the SHAPE of the two verification gates PS-47 added.

These tests exist because both gates are the kind of thing that can be hollowed
out while still looking present: a `|| true` appended to a pytest line, a
`continue-on-error: true` on a job, a smoke step quietly moved to after the
upload, an allowlist of "known" failures growing until the job is decorative.
Each of those leaves a green check that proves nothing, which is strictly worse
than no check at all — the missing one is at least visible.

So these assert the properties that make the gates MEAN something, not merely
that the files exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
SMOKE_SCRIPT = REPO_ROOT / ".github" / "scripts" / "smoke_frozen_bundle.py"

BUILD_JOBS = ["build-linux", "build-windows", "build-macos"]


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def release_text() -> str:
    return RELEASE_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def smoke_text() -> str:
    return SMOKE_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ci_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def release_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))


def _on_block(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True (the Norway problem).
    return doc.get("on", doc.get(True, {}))


# --------------------------------------------------------------------------
# Outcome one: a change is checked BEFORE it lands.
# --------------------------------------------------------------------------


def test_ci_workflow_exists() -> None:
    assert CI_WORKFLOW.is_file(), "the pre-merge gate workflow is missing entirely"


def test_ci_runs_the_suite_on_a_pull_request_against_main(ci_yaml) -> None:
    """A PR must be verified before anyone can merge it."""
    triggers = _on_block(ci_yaml)
    assert "pull_request" in triggers, (
        "no pull_request trigger — a change could still reach main with no "
        "automated verification, which is the whole gap this workflow closes"
    )
    branches = (triggers["pull_request"] or {}).get("branches", [])
    assert "main" in branches, f"pull_request does not target main: {branches}"


def test_ci_also_runs_the_suite_on_a_push_to_main(ci_yaml) -> None:
    """A path that bypasses review must not also bypass verification."""
    triggers = _on_block(ci_yaml)
    assert "push" in triggers, (
        "no push trigger — a direct push to main (bypassing review) would also "
        "bypass the suite"
    )
    branches = (triggers["push"] or {}).get("branches", [])
    assert "main" in branches, f"push trigger does not cover main: {branches}"


def test_ci_actually_invokes_pytest(ci_text) -> None:
    assert re.search(r"python -m pytest", ci_text), (
        "the gate does not run pytest — it cannot be verifying anything"
    )


def test_ci_result_cannot_be_swallowed(ci_text) -> None:
    """A gate whose failure is discarded is a decoration."""
    pytest_lines = [ln for ln in ci_text.splitlines() if "pytest" in ln and "#" not in ln.split("pytest")[0]]
    assert pytest_lines, "no uncommented pytest invocation found"
    for line in pytest_lines:
        assert "|| true" not in line, f"pytest failure discarded with `|| true`: {line!r}"
        assert "|| exit 0" not in line, f"pytest failure discarded: {line!r}"
        assert "continue-on-error" not in line, f"pytest failure discarded: {line!r}"


def test_ci_job_is_not_continue_on_error(ci_yaml) -> None:
    for name, job in ci_yaml["jobs"].items():
        assert job.get("continue-on-error") is not True, (
            f"job {name} is continue-on-error — its red result would not block anything"
        )


def _effective_lines(text: str) -> list[str]:
    """The workflow's ACTUAL directives, with YAML comments stripped.

    The comments here deliberately discuss the things that must not appear
    (an allowlist, a --deselect) in order to explain why they are absent, so a
    raw substring scan would flag the explanation as the offence. Scan what the
    runner would execute, not the prose about it.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line.split(" #", 1)[0] if " #" in line else line)
    return out


def test_ci_does_not_narrow_the_suite(ci_text) -> None:
    """No allowlist, no deselection, no 'fast subset' creeping in.

    The measured floor is zero, so every red is a real red. A `--deselect` or a
    `-k` filter here is how a genuine regression gets waved through.
    """
    effective = "\n".join(_effective_lines(ci_text))
    for banned in ("--deselect", "--ignore=", " -k ", "--lf", "--exitfirst"):
        assert banned not in effective, (
            f"the gate narrows the suite with {banned!r} — a change could then "
            "break an excluded test and still go green"
        )


def test_ci_has_no_expected_failure_allowlist(ci_text) -> None:
    effective = "\n".join(_effective_lines(ci_text)).lower()
    for banned in ("allowlist", "allowed_failures", "expected_failures", "xfail_strict = false"):
        assert banned not in effective, (
            f"an expected-failure allowlist ({banned!r}) appeared — the measured "
            "floor was zero, so this would only ever hide a real regression"
        )


def test_ci_installs_the_same_dependency_set_as_the_release_gate(ci_text, release_text) -> None:
    """Both gates must mean the same thing by "the suite".

    If the pre-merge gate installs a different dependency set, a change can pass
    here and fail at the tag — reintroducing the late discovery this exists to
    remove.
    """
    for marker in ("pip install --prefer-binary .", "pip check"):
        assert marker in ci_text, f"pre-merge gate is missing {marker!r}"
        assert marker in release_text, f"release gate is missing {marker!r}"
    assert "requirements-dev.txt" in ci_text, (
        "the pre-merge gate does not install the dev requirements the release gate does"
    )


def test_ci_installs_from_pyproject_not_requirements_txt(ci_text) -> None:
    """requirements.txt is not the set the build bundles."""
    assert not re.search(r"pip install[^\n]*-r requirements\.txt", ci_text), (
        "installing requirements.txt lets the gate go green on a dependency "
        "combination the release never ships"
    )


def test_ci_permissions_are_read_only(ci_yaml) -> None:
    """Inherit release.yml's deliberate posture rather than widening it."""
    perms = ci_yaml.get("permissions")
    assert perms == {"contents": "read"}, (
        f"expected read-only permissions, got {perms!r} — this job installs and "
        "executes untrusted third-party code and must not hold a writable token"
    )


def test_ci_documents_the_measured_failure_floor(ci_text) -> None:
    """The floor must be a recorded measurement, not folklore."""
    assert "2366" in ci_text and "2321" in ci_text, (
        "the measured floor figures are not recorded in the workflow — the next "
        "reader cannot tell a real regression from an inherited red"
    )


# --------------------------------------------------------------------------
# Outcome two: a packaged copy is opened BEFORE it ships.
# --------------------------------------------------------------------------


def test_smoke_script_exists() -> None:
    assert SMOKE_SCRIPT.is_file(), "the frozen-bundle smoke script is missing entirely"


@pytest.mark.parametrize("job", BUILD_JOBS)
def test_every_build_job_opens_its_bundle(release_yaml, job) -> None:
    steps = release_yaml["jobs"][job]["steps"]
    assert any(
        "smoke_frozen_bundle.py" in str(s.get("run", "")) for s in steps
    ), f"{job} never opens the bundle it produced — it could upload one that does not start"


@pytest.mark.parametrize("job", BUILD_JOBS)
def test_bundle_is_opened_before_it_is_uploaded(release_yaml, job) -> None:
    """A check after the upload cannot stop a broken bundle shipping."""
    steps = release_yaml["jobs"][job]["steps"]
    smoke_idx = next(
        i for i, s in enumerate(steps) if "smoke_frozen_bundle.py" in str(s.get("run", ""))
    )
    upload_idx = next(
        (i for i, s in enumerate(steps) if "upload-artifact" in str(s.get("uses", ""))),
        None,
    )
    assert upload_idx is not None, f"{job} has no upload step to order against"
    assert smoke_idx < upload_idx, (
        f"{job} uploads its artifact before opening it — a bundle that does not "
        "start would still be published"
    )


@pytest.mark.parametrize(
    "job,packaging",
    [
        ("build-linux", "appimagetool"),
        ("build-windows", "Inno Setup"),
        ("build-macos", "hdiutil"),
    ],
)
def test_bundle_is_opened_before_it_is_packaged(release_yaml, job, packaging) -> None:
    """Do not wrap a bundle that will not open."""
    steps = release_yaml["jobs"][job]["steps"]
    smoke_idx = next(
        i for i, s in enumerate(steps) if "smoke_frozen_bundle.py" in str(s.get("run", ""))
    )
    pack_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if packaging.lower() in (str(s.get("run", "")) + str(s.get("name", ""))).lower()
        ),
        None,
    )
    assert pack_idx is not None, f"{job}: no {packaging} step found to order against"
    assert smoke_idx < pack_idx, (
        f"{job} packages with {packaging} before opening the bundle"
    )


@pytest.mark.parametrize("job", BUILD_JOBS)
def test_smoke_step_failure_is_not_swallowed(release_yaml, job) -> None:
    steps = release_yaml["jobs"][job]["steps"]
    step = next(s for s in steps if "smoke_frozen_bundle.py" in str(s.get("run", "")))
    assert step.get("continue-on-error") is not True, (
        f"{job}'s smoke step is continue-on-error — it could not stop a bad build"
    )
    run = str(step.get("run", ""))
    assert "|| true" not in run, f"{job}'s smoke step discards its own failure"


def test_smoke_needs_no_display(smoke_text) -> None:
    """The check must be genuinely windowless, not incidentally so."""
    assert "DISPLAY" in smoke_text and "WAYLAND_DISPLAY" in smoke_text, (
        "the script does not scrub DISPLAY/WAYLAND_DISPLAY, so on a runner that "
        "has a screen it could pass for the wrong reason"
    )


def test_smoke_forces_imports_to_resolve_inside_the_bundle(smoke_text) -> None:
    """-S -E is what makes this a check of the BUNDLE and not of the runner."""
    assert '"-S"' in smoke_text and '"-E"' in smoke_text, (
        "the payload is not run with -S -E, so imports could silently resolve "
        "from the runner's site-packages and the check would prove nothing"
    )


def test_smoke_imports_lazy_dependencies_explicitly(smoke_text) -> None:
    """Measured: a bundle missing paramiko still opened and still printed its
    correct version, because that import is lazy. Opening is not sufficient."""
    assert "REQUIRED_IMPORTS" in smoke_text, "no explicit in-bundle import list"
    assert "paramiko" in smoke_text, (
        "paramiko is not in the required-import list — it is imported lazily, so "
        "its absence would pass an open-it-only check and fail in front of a user"
    )


def test_smoke_checks_the_bundle_version_against_app_version(smoke_text) -> None:
    assert "APP_VERSION" in smoke_text, "the bundle's version is never checked"
    assert "updater.py" in smoke_text, (
        "the version must be read from updater.py — the same value preflight "
        "already compares the tag against, not a second source of truth"
    )


def test_smoke_fails_closed_when_it_finds_nothing(smoke_text) -> None:
    """A smoke test that finds nothing to run and reports success is worse than
    a missing one."""
    assert "this check would prove nothing" in smoke_text, (
        "the script does not fail closed on a missing payload / site-packages"
    )
    assert "def fail(" in smoke_text and "sys.exit(1)" in smoke_text


def test_smoke_expects_the_selftest_token_exactly(smoke_text) -> None:
    """SELFTEST_OK is a contract with the self-updater.

    Every installed copy waits for this exact string to decide a staged build is
    safe to swap in, so renaming it would break updates in the field. Pin the
    spelling here so that breaks the BUILD instead.
    """
    assert 'SELFTEST_TOKEN = "SELFTEST_OK"' in smoke_text
    main_py = (REPO_ROOT / "src" / "main.py").read_text(encoding="utf-8")
    assert '"SELFTEST_OK"' in main_py or "'SELFTEST_OK'" in main_py, (
        "main.py no longer prints SELFTEST_OK — this breaks both the smoke check "
        "and every installed copy's next self-update"
    )


def test_release_no_longer_claims_the_bundle_goes_unexamined(release_text) -> None:
    """The 'tracked as a follow-up' note described exactly what now ships.

    Left standing it becomes the next reader's false impression.
    """
    assert "tracked as a follow-up" not in release_text, (
        "the stale follow-up note is still present although the guard it defers "
        "to has now shipped"
    )
    assert "A true guard would import from" not in release_text
