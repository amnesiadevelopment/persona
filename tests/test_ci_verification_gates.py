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
    assert "2454" in ci_text and "2321" in ci_text, (
        "the measured floor figures are not recorded in the workflow — the next "
        "reader cannot tell a real regression from an inherited red"
    )


def test_ci_floor_says_where_each_figure_was_measured(ci_text) -> None:
    """A floor figure without provenance is the bug this test exists to stop.

    The first revision of this workflow reported DEV CONTAINER figures while
    describing them as measured "on this runner image", and concluded that any
    red must be the developer's own. The runner disproved that on the very first
    run. The figures being stale was survivable; the false provenance was not,
    because it is the sentence the next reader trusts when deciding whether they
    broke something.

    So the floor comment must name BOTH environments, and must not claim a
    single undifferentiated measurement.
    """
    floor = ci_text
    # Pin the SECTION HEADERS, not loose prose. An earlier version of this test
    # asserted on the phrases "on this runner" and "container", which also occur
    # in the narrative explaining the original mistake — so rewriting the headers
    # left the test green. It was mutation-tested and did not catch the mutation;
    # these exact markers do.
    assert "ON THIS RUNNER (" in floor, (
        "the floor comment does not label which figures were taken on the runner"
    )
    assert "IN A DEV CONTAINER (" in floor, (
        "the floor comment does not disclose that some figures were taken in a "
        "dev container rather than on the runner"
    )
    # The two environments collect different numbers of tests. If that stops
    # being stated, someone will read the container figure as the runner's.
    assert "THE TWO NUMBERS DO NOT MATCH" in floor, (
        "the floor comment does not warn that the container and runner counts "
        "diverge — that divergence is exactly what made the first floor wrong"
    )


def test_ci_pins_the_javascript_engine(ci_yaml) -> None:
    """Parts of the suite run generated JS through node, so node is a test
    dependency — and an unpinned one lets the gate change its answer with no
    change to this repo. That is not hypothetical here: the runner image moved
    from Node 20 to Node 24, Node 21+ made `navigator` a getter-only global, and
    a harness's plain assignment over it became a silent no-op. Green suite,
    red runner, no commit responsible.
    """
    steps = ci_yaml["jobs"]["tests"]["steps"]
    node_steps = [s for s in steps if "setup-node" in str(s.get("uses", ""))]
    assert node_steps, (
        "ci.yml does not pin a node version — the JS-driving tests run against "
        "whatever the runner image happens to ship"
    )
    version = str(node_steps[0].get("with", {}).get("node-version", ""))
    assert version.strip(), "setup-node is present but pins no explicit version"


def test_release_pins_the_javascript_engine_too(release_yaml) -> None:
    """The tag-time gate runs the same suite on the same image, so it carried
    the same exposure — it had simply not fired since the image moved. Pinning
    one and not the other would leave the two gates disagreeing about what "the
    suite" means, which is the drift the install step is kept identical to stop.
    """
    steps = release_yaml["jobs"]["tests"]["steps"]
    node_steps = [s for s in steps if "setup-node" in str(s.get("uses", ""))]
    assert node_steps, (
        "release.yml's tests job does not pin a node version, so the tag-time "
        "gate can still change its answer when the runner image drifts"
    )


def test_the_language_harness_cannot_be_silently_ignored() -> None:
    """The regression test for the bug the gate found.

    `globalThis.navigator = ...` is a SILENT no-op on Node >= 21, where the
    global is a getter-only accessor — no throw, no warning, the stub just never
    installs and the test reads the host's real locale. defineProperty works on
    every engine. This pins the fix at its source, so the harness stays correct
    even if the Node pin above is ever raised or removed.
    """
    harness = (REPO_ROOT / "tests" / "test_ff_language_override.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"^\s*globalThis\.navigator\s*=", harness, re.MULTILINE), (
        "the language harness assigns to globalThis.navigator, which is a silent "
        "no-op on Node >= 21 — the stub would never install and the test would "
        "read the host locale instead of the pinned one"
    )
    assert 'defineProperty(globalThis, "navigator"' in harness, (
        "the language harness no longer installs its navigator stub with "
        "defineProperty — the engine-independent form"
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


def test_smoke_launches_the_entry_point_instead_of_importing_it(smoke_text) -> None:
    """The regression test for a gate that could never have gone green.

    MEASURED: the bootstrap used to end in `import main`, which binds the module
    under __name__ == "main". But the PERSONA_SELFTEST gate lives inside main()'s
    BODY, reached only through main.py's `if __name__ == "__main__"` guard — so
    importing ran no entry point, printed no token, and the script failed every
    healthy bundle. Verified both directions on a real bundle: `import main`
    printed nothing, `main.main()` printed SELFTEST_OK.

    That is the worst failure shape available to this check: it fails CLOSED, so
    it would have blocked all three build jobs on every release while looking
    like a careful gate.
    """
    assert "runpy" in smoke_text and 'run_name="__main__"' in smoke_text, (
        "the bundle's entry point is not launched under __main__ — the selftest "
        "gate sits behind main.py's __main__ guard and would never fire"
    )
    assert not re.search(r"^import main\s*$", smoke_text, re.MULTILINE), (
        "the bootstrap imports main rather than running it; the gate is inside "
        "main()'s body and an import will never reach it"
    )


def test_smoke_rejects_an_entry_point_that_returns_without_the_gate(smoke_text) -> None:
    """On the selftest path main() must print the token and hard-exit. A normal
    return means the gate did not run, which must be loud rather than a quiet
    fall off the end of the bootstrap."""
    assert "PERSONA_BUNDLE_ENTRYPOINT_RETURNED" in smoke_text, (
        "nothing detects an entry point that returns without firing the gate"
    )


def test_smoke_checks_the_asset_at_its_exact_path(smoke_text) -> None:
    """An asset that survived at the WRONG path is the freezing failure being
    hunted, not an escape from it.

    MEASURED with a before/after pair: the previous check fell back to a
    whole-tree search by basename, and a RELOCATED icon.png passed it (exit 0)
    while outright deletion was caught. The exact-path form catches both.
    """
    assert "the app opens it by path, not by name" in smoke_text, (
        "the asset check does not assert the exact path — a relocated asset "
        "would be waved through by a basename search"
    )


def test_smoke_refuses_to_guess_between_ambiguous_candidates(smoke_text) -> None:
    """rglob order is filesystem-dependent, so picking [0] out of several
    matches silently smoke-tests an arbitrary payload and reports on the whole
    bundle. This script fails closed everywhere else."""
    assert smoke_text.count("Refusing to guess") >= 2, (
        "find_app_payload / find_site_packages still pick an arbitrary match "
        "instead of failing closed on ambiguity"
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


# --------------------------------------------------------------------------
# PS-60: the suite runs on every platform persona ships to, a real engine is
# launched, and what this environment cannot answer says so.
# --------------------------------------------------------------------------

GPU_SCRIPT = REPO_ROOT / ".github" / "scripts" / "report_runner_gpu.py"

#: The three runner labels release.yml already builds on. The pre-merge gate
#: must cover the same set — a platform that ships without ever running the
#: suite is the gap this matrix closes.
SHIPPED_PLATFORMS = ("ubuntu-24.04", "windows-latest", "macos-latest")


def _load_gpu_reporter():
    """Import the GPU reporter as a module so tests read what it PRODUCES.

    Asserting on its source text instead also matches its own docstring, which
    is how a mutation that gutted the printed explanation once slipped past.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ps60_gpu_reporter", GPU_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gpu_text() -> str:
    return GPU_SCRIPT.read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", SHIPPED_PLATFORMS)
def test_ci_runs_the_suite_on_every_shipped_platform(ci_yaml, platform) -> None:
    """persona ships to three operating systems; until this matrix existed
    exactly one of them had ever executed a test, and everything the project
    believed about Windows or macOS was inference from source."""
    matrix = ci_yaml["jobs"]["tests"]["strategy"]["matrix"]
    assert platform in matrix["os"], (
        f"{platform} is not in the pre-merge matrix, so a change can break it "
        "and still be merged — that platform still ships"
    )


def test_ci_matrix_covers_the_same_platforms_release_builds_for(ci_yaml, release_yaml) -> None:
    """A platform good enough to BUILD for is good enough to TEST on.

    Pinned as a relationship rather than as a hardcoded list so that adding a
    fourth build target cannot quietly leave the pre-merge gate behind.
    """
    built_on = {
        str(job.get("runs-on", ""))
        for name, job in release_yaml["jobs"].items()
        if name.startswith("build-")
    }
    tested_on = set(ci_yaml["jobs"]["tests"]["strategy"]["matrix"]["os"])
    missing = {p for p in built_on if p and "${{" not in p} - tested_on
    assert not missing, (
        f"release.yml builds on {sorted(missing)} but the pre-merge gate never "
        "runs the suite there — those platforms ship unverified"
    )


def test_ci_matrix_does_not_fail_fast(ci_yaml) -> None:
    """The interesting result is 'holds on Linux, not on Windows'.

    fail-fast cancels the sibling platforms the moment one goes red, throwing
    away exactly the rows that make a divergence legible.
    """
    strategy = ci_yaml["jobs"]["tests"]["strategy"]
    assert strategy.get("fail-fast") is False, (
        "the matrix cancels remaining platforms on the first failure, which "
        "destroys the cross-platform comparison this matrix exists to produce"
    )


def test_no_platform_is_excused_from_a_red_result(ci_yaml, ci_text) -> None:
    """The Windows floor is 22 real failures, so the temptation to mark that
    platform continue-on-error is live. It must be refused: it is the allowlist
    defect at job scale, and would hide NEW Windows regressions too."""
    for name, job in ci_yaml["jobs"].items():
        assert job.get("continue-on-error") is not True, (
            f"job {name} cannot fail the workflow"
        )
    effective = "\n".join(_effective_lines(ci_text))
    assert "continue-on-error" not in effective, (
        "a continue-on-error appeared in the gate's directives — a platform "
        "whose red does not block is a platform nobody is verifying"
    )


def test_ci_provisions_a_real_browser_engine(ci_text) -> None:
    """The realm-sweep fixture launches a genuine Firefox and had never
    executed anywhere, because the BINARY was never provisioned. `pip install .`
    supplies the playwright package but not the browser."""
    effective = "\n".join(_effective_lines(ci_text))
    assert "playwright install firefox" in effective, (
        "no step downloads the Firefox binary, so every real-browser probe "
        "skips and the suite's strongest assertion never runs"
    )


def test_ci_declares_the_browser_capability_rather_than_inferring_it(ci_yaml) -> None:
    """A job that HAS provisioned a browser says so, and in that job a skip of
    the browser probes is a failure.

    The declaration must be explicit. Inferring it ("playwright imported, so
    this machine supports browser tests") re-creates the original defect one
    level up: it concludes "unsupported here" on precisely the machine where
    support broke, which is the one case that must be loud.
    """
    steps = ci_yaml["jobs"]["tests"]["steps"]
    declaring = [
        s for s in steps
        if "browser" in str(s.get("env", {}).get("PERSONA_REQUIRED_CAPABILITIES", ""))
    ]
    assert declaring, (
        "no step declares the 'browser' capability, so a browser probe that "
        "declines to run on a provisioned machine still reads as green"
    )
    for step in declaring:
        assert "pytest" in str(step.get("run", "")), (
            "the capability is declared on a step that does not run the suite, "
            "so nothing enforces it"
        )


def test_the_capability_declaration_uses_the_projects_existing_vocabulary() -> None:
    """PS-58 owns 'did not run'. A second mechanism here would mean two
    vocabularies for the same question and a skip that satisfies neither."""
    conftest = (REPO_ROOT / "conftest.py").read_text(encoding="utf-8")
    assert "PERSONA_REQUIRED_CAPABILITIES" in conftest, (
        "the workflow declares a capability through a name the test harness "
        "does not implement — the declaration would be silently inert"
    )
    assert '"browser"' in conftest or "'browser'" in conftest, (
        "the 'browser' capability the workflow declares is not a known "
        "capability, which the harness treats as a hard usage error"
    )


def test_ci_states_the_measured_floor_for_every_platform(ci_text) -> None:
    """A floor is the sentence the next reader trusts when deciding whether
    their change broke something, so each platform's figure must be stated —
    and they differ, which is the point.

    Pinned to the MARKED floor line for each platform, not to a loose substring.
    An earlier version asserted `"2446" in ci_text`, which the narrative below
    the table also contains — so deleting the authoritative figure left the test
    green. It was mutation-tested and did not catch that; this form does.
    """
    for platform in SHIPPED_PLATFORMS:
        assert f"ON THIS RUNNER ({platform}" in ci_text, (
            f"no floor recorded for {platform}, or it is not labelled with the "
            "runner it was taken on"
        )
    for marker in ("THE LINUX FLOOR", "THE MACOS FLOOR", "THE WINDOWS FLOOR"):
        pattern = rf"(\d+) failed, (\d+) passed[^\n]*<- {marker}"
        match = re.search(pattern, ci_text)
        assert match, (
            f"the line marked {marker!r} does not state a 'N failed, N passed' "
            "figure — a floor without numbers cannot be compared against"
        )
    windows = re.search(r"(\d+) failed, (\d+) passed[^\n]*<- THE WINDOWS FLOOR", ci_text)
    assert windows and int(windows.group(1)) > 0, (
        "the Windows floor is recorded as zero failures, but it was measured at "
        "22 — a floor that understates itself makes every real regression look "
        "like an inherited red"
    )


def test_ci_records_why_the_windows_floor_is_not_zero(ci_text) -> None:
    """A bare number teaches people to ignore the job. The causes are what make
    the 22 actionable rather than scenery."""
    for cause in ("os.fork", "0o666", "charmap", "socket"):
        assert cause in ci_text, (
            f"the Windows floor does not name the {cause!r} cause — an "
            "unexplained red is indistinguishable from a broken gate"
        )


def test_ci_records_the_red_it_inherited_rather_than_caused(ci_text) -> None:
    """Every platform currently runs at its measured floor PLUS one failure
    that arrived on main after those floors were taken. Recording it is what
    lets the next reader tell an inherited red from one they just caused —
    without it, three red jobs look like this matrix broke something.

    Pinned to the marked line and to the evidence, not to a loose number: the
    claim "not ours" is only worth anything if the proof travels with it.
    """
    assert "<- THE INHERITED RED" in ci_text, (
        "no inherited failure is recorded, but all three platforms run one "
        "above their stated floor — the table understates itself, which makes "
        "every real regression look like an inherited red"
    )
    assert "test_installed_core_version_answers_empty_when_absent" in ci_text, (
        "the inherited red is described but never named, so a reader cannot "
        "check whether the red they are looking at is that one"
    )
    for evidence in ("4f94721", "fc27868"):
        assert evidence in ci_text, (
            f"the inherited red does not cite {evidence} — the commit that "
            "introduced it and the main-tip run that fails it identically are "
            "the whole basis for calling it inherited rather than ours"
        )


def test_the_inherited_red_is_reported_not_repaired(ci_text) -> None:
    """The floor is measured and reported, never engineered away. An inherited
    failure is the most tempting thing to quietly fix or allowlist, because
    doing so turns three red jobs green without touching the product."""
    assert "DELIBERATELY NOT FIXED HERE" in ci_text, (
        "the workflow does not state that the inherited red is left alone, "
        "leaving the next reader to 'helpfully' repair an unrelated test to "
        "make the matrix green"
    )
    gate = REPO_ROOT / "src" / "services" / "verify" / "engine_gate.py"
    if gate.exists():
        blame = gate.read_text(encoding="utf-8")
        assert 'CORE_DISTRIBUTION = "invisible_core"' in blame, (
            "the inherited red is attributed to invisible_core metadata "
            "resolution, but engine_gate no longer reads that distribution — "
            "the recorded cause has gone stale and would mislead"
        )


def test_the_display_question_was_answered_by_measurement(ci_text) -> None:
    """Assuming a display is needed adds a moving part nothing uses; assuming
    it is not, when it is, fails for a reason unrelated to the product. The
    workflow must record which it actually was."""
    effective = "\n".join(_effective_lines(ci_text))
    for banned in ("xvfb", "Xvfb", "xvfb-run"):
        assert banned not in effective, (
            "a virtual display is provisioned although a headless launch was "
            "measured to work on all three runners"
        )
    assert "MEASURED" in ci_text and "DISPLAY" in ci_text, (
        "the workflow does not state that the display question was settled by "
        "measurement, leaving the next reader to re-litigate it"
    )


def test_the_gpu_reading_is_taken_on_every_platform(ci_yaml) -> None:
    steps = ci_yaml["jobs"]["tests"]["steps"]
    gpu_steps = [s for s in steps if "report_runner_gpu.py" in str(s.get("run", ""))]
    assert gpu_steps, "no GPU reading is taken, so the host-fact leak goes unrecorded"
    assert any(str(s.get("if", "")).strip() == "always()" for s in gpu_steps), (
        "the GPU reading is skipped when the suite fails — but the Windows job "
        "is red at its measured floor, so the reading would never be recorded "
        "there at all"
    )


def test_the_gpu_reading_is_never_counted_as_a_pass(gpu_text) -> None:
    """THE central property. A green assertion here would enter the record as
    evidence that persona's GPU masking was verified on a machine that has no
    GPU — the precise misreading the reporter exists to prevent."""
    assert "assert " not in gpu_text, (
        "the GPU reporter asserts, so its result can be read as a verdict on "
        "persona rather than as a reading of the environment"
    )
    assert "return 0" in gpu_text and "return 1" not in gpu_text, (
        "the GPU reporter can exit non-zero, which would raise a permanent "
        "environmental fact as a new defect on every run"
    )


def test_the_gpu_reading_states_its_cause() -> None:
    """Recorded WITH ITS REASON: a reader must meet the explanation and the
    value at the same moment, or the permanent red becomes evidence about
    persona.

    Reads the explanation the operator is ACTUALLY SHOWN, by calling the
    function that produces it. Two earlier versions asserted on the module's
    source text, which also contains this vocabulary in its docstring — so
    gutting the printed explanation left the test green while the operator saw
    nothing. Both were mutation-tested and neither caught it; this form does.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ps60_gpu_reporter", GPU_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    shown = "\n".join(module.explain_software_rendering()).lower()
    assert "host-fact leak" in shown, (
        "the printed reading does not classify the software-renderer pair as a "
        "host-fact leak, which is how the masking charter already records it — "
        "explaining it only in a docstring the operator never sees is not enough"
    )
    assert "by construction" in shown, (
        "the printed reading does not say the pair is present by construction "
        "on this infrastructure, so a reader may take it for a regression"
    )
    assert "not" in shown and "defect" in shown, (
        "the printed reading does not tell the reader to refrain from filing it "
        "as a new defect"
    )
    # The classification is worthless if the detection never fires.
    assert module.looks_like_software("llvmpipe (LLVM 15.0.7, 256 bits)")
    assert module.looks_like_software("Apple Software Renderer")
    assert not module.looks_like_software("NVIDIA GeForce RTX 4090/PCIe/SSE2")


def test_absent_gpu_data_is_never_reported_as_hardware() -> None:
    """THE THREE STATES MUST STAY THREE.

    Measured on the runners: windows-latest reports a software rasteriser
    (Microsoft Basic Render Driver), macos-latest reports real hardware
    (Apple M1), and ubuntu-24.04 reports NOTHING AT ALL — headless Firefox
    exposes no WebGL context, so every parameter comes back None.

    An earlier revision had two branches, so the Linux case printed "did NOT
    report a software rasteriser" — letting an ABSENCE of data read as evidence
    the host had a GPU. That is precisely the misreading this reporter exists to
    prevent, one level up.
    """
    module = _load_gpu_reporter()

    absent = "\n".join(module.explain_no_reading()).lower()
    assert "no reading" in absent or "absence of data" in absent, (
        "the no-data branch does not say that no reading was taken"
    )
    assert "not a claim" in absent, (
        "the no-data branch does not refuse to make a claim about the host — an "
        "absent reading must never read as 'this runner has a GPU'"
    )

    hardware = "\n".join(module.explain_hardware_reading("Apple M1")).lower()
    assert "not a verification" in hardware or "do not record it as a pass" in hardware, (
        "the hardware branch reads as a pass — reporting a real GPU is a fact "
        "about the runner, not evidence that persona's masking held"
    )

    # The three explanations must be genuinely different text, or the
    # distinction is cosmetic.
    assert len({absent, hardware, "\n".join(module.explain_software_rendering()).lower()}) == 3

    # AND THE DISPATCH ITSELF, not only the three texts it dispatches to. An
    # earlier version tested the texts alone, so neutering the `renderer is
    # None` branch kept every assertion green while a runner that answered
    # NOTHING silently began reading as one that reported hardware.
    assert module.explanation_for(None, False) == module.explain_no_reading(), (
        "a reading with NO renderer string does not route to the no-data "
        "explanation — an absence of data would read as a claim about the host"
    )
    assert module.explanation_for("Apple M1", False) == module.explain_hardware_reading("Apple M1"), (
        "a hardware renderer does not route to the hardware explanation"
    )
    assert module.explanation_for("llvmpipe", True) == module.explain_software_rendering(), (
        "a software rasteriser does not route to the host-fact-leak explanation"
    )
    # The software verdict must win even when a renderer string is present.
    assert module.explanation_for(None, True) == module.explain_software_rendering()


def test_the_gpu_explanation_is_the_one_actually_printed(gpu_text) -> None:
    """Guards the seam every test above depends on.

    If `main` stops routing through `explanation_for`, those functions become
    dead code the tests still happily read — a green test describing output
    nobody receives.
    """
    assert "banner += explanation_for(renderer, software)" in gpu_text, (
        "main() no longer emits the explanation through explanation_for(), so "
        "what is tested is not what is printed"
    )


def test_the_job_summary_states_the_same_three_states_as_the_banner() -> None:
    """THE SUMMARY IS A SECOND SURFACE, AND IT MUST NOT CONTRADICT THE FIRST.

    This test exists because the two surfaces once disagreed. The banner routed
    through `explanation_for()` (three states) while the job summary carried a
    two-branch ternary, so:

      * on ubuntu-24.04 — renderer None, NO WebGL context at all — the summary
        said "no software rasteriser was detected, which is unexpected",
        reporting an ABSENCE OF DATA as a positive finding, on the one platform
        whose failure floor is zero and whose page is therefore most read;
      * on macos-latest — a real 'Apple M1' — the summary denied a detection the
        banner directly above it had just made.

    The summary is the wider-readership surface of the two: a rendered page, not
    log output. Its wording is therefore not cosmetic.
    """
    module = _load_gpu_reporter()

    absent = module.summary_note_for(None, False).lower()
    hardware = module.summary_note_for("Apple M1", False).lower()
    software = module.summary_note_for("llvmpipe (LLVM 15.0.7)", True).lower()

    # 1. THE NO-DATA ARM — the one that was missing, and that fires every
    #    ubuntu run. It must refuse to make a claim in either direction.
    assert "no reading" in absent or "absence of data" in absent, (
        "the job summary's no-data arm does not say that no reading was taken"
    )
    assert "not a claim" in absent, (
        "the job summary does not refuse to make a claim about the host when no "
        "reading exists — an absent reading must never read as 'this runner has "
        "a GPU', which is the exact misreading this reporter exists to prevent"
    )
    assert "unexpected" not in absent, (
        "the job summary calls an absent WebGL context 'unexpected', which is "
        "the two-branch wording that treated missing data as a finding"
    )

    # 2. THE HARDWARE ARM must agree with the banner, not contradict it, and
    #    must not read as a pass.
    assert "apple m1" in hardware, (
        "the job summary does not name the renderer that was actually reported, "
        "so it cannot be checked against the banner printed beside it"
    )
    assert "not" in hardware and ("verification" in hardware or "pass" in hardware), (
        "the job summary lets a real GPU reading read as a pass — reporting "
        "hardware is a fact about the runner, not evidence that masking held"
    )

    # 3. THE SOFTWARE ARM keeps the charter's classification.
    assert "host-fact leak" in software and "by construction" in software, (
        "the job summary no longer classifies the software-renderer pair as a "
        "host-fact leak present by construction"
    )

    # The three must be genuinely different text, or the distinction is
    # cosmetic and a mutation collapsing two of them has nothing to escape.
    assert len({absent, hardware, software}) == 3, (
        "two of the job summary's three states produce the same text, so the "
        "surface cannot distinguish them"
    )

    # The software verdict wins even when a renderer string is present, exactly
    # as it does in explanation_for().
    assert module.summary_note_for(None, True) == module.summary_note_for("llvmpipe", True)


def test_the_job_summary_note_is_the_one_actually_written(gpu_text) -> None:
    """Guards the summary seam, the way the banner seam is already guarded.

    Without this, `summary_note_for` can be correct, fully tested, and never
    called — which is precisely the state the previous defect was in, one
    function over: the three good explanations existed while the summary
    branched two ways on its own.
    """
    assert "+ summary_note_for(renderer, software)" in gpu_text, (
        "main() no longer writes the job summary through summary_note_for(), so "
        "what is tested is not what a reader is shown"
    )
    # And the wording that caused the defect must not come back inline.
    assert "which is unexpected on a" not in gpu_text, (
        "the two-branch summary wording is back in the reporter, reporting an "
        "absence of data as an unexpected finding"
    )


def test_nothing_fakes_hardware_the_runner_does_not_have(ci_text, gpu_text) -> None:
    """Out of scope, and actively harmful: forcing a renderer string corrupts
    the one reading this environment is genuinely unable to give."""
    effective = "\n".join(_effective_lines(ci_text)).lower()
    for banned in ("mesa_gl_version_override", "libgl_always_software",
                   "gallium_driver", "--use-gl=", "swiftshader-install"):
        assert banned not in effective, (
            f"the workflow sets {banned!r}, making the runner claim graphics "
            "behaviour it does not have"
        )
    assert "getParameter" in gpu_text, (
        "the reporter does not read the renderer from the live GL context, so "
        "it cannot be reporting what the host actually draws with"
    )


def test_no_proxy_credential_is_introduced(ci_text) -> None:
    """Explicitly out of scope: the mobile exit is metered and is itself the
    subject of measurement elsewhere. Importing it here would spend quota to
    improve a number nothing depends on, and create a credential in CI with no
    consumer."""
    effective = "\n".join(_effective_lines(ci_text)).lower()
    for banned in ("proxy_url", "socks5", "proxy_user", "proxy_pass", "http_proxy:"):
        assert banned not in effective, (
            f"{banned!r} appeared in the gate — a datacenter exit IP is an "
            "expected, IP-driven reading and must not be papered over"
        )


def test_ci_does_not_widen_the_read_only_permission_stance(ci_yaml) -> None:
    """These jobs execute untrusted third-party code. Downloading and launching
    a browser needs no token at all, so nothing added here may widen it."""
    assert ci_yaml.get("permissions") == {"contents": "read"}
    for name, job in ci_yaml["jobs"].items():
        perms = job.get("permissions")
        assert perms in (None, {"contents": "read"}), (
            f"job {name} widens the default read-only permission stance"
        )


def test_no_self_hosted_runner_is_introduced(ci_yaml) -> None:
    """Settled: the owner cannot grant machine access, which is the reason this
    work stands on hosted infrastructure."""
    for platform in ci_yaml["jobs"]["tests"]["strategy"]["matrix"]["os"]:
        assert "self-hosted" not in str(platform), (
            "a self-hosted runner appeared in the matrix"
        )
