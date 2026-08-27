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


def test_smoke_refuses_to_guess_between_ambiguous_candidates(tmp_path) -> None:
    """rglob order is filesystem-dependent, so picking [0] out of several
    matches silently smoke-tests an arbitrary payload and reports on the whole
    bundle. This script fails closed everywhere else.

    PS-158 REWROTE THIS TEST, AND THE REASON MATTERS. It used to assert
    `smoke_text.count("Refusing to guess") >= 2` — counting a phrase in the
    source. That made it a test of PROSE: it could be satisfied by a comment and
    broken by a rewording, while saying nothing about what the script does.

    The two call sites it lumped together turned out to need OPPOSITE answers:

      * find_app_payload — ambiguity is still fatal. Two app trees means two
        candidate apps and only one ships; guessing reports on the wrong one.
        Still asserted below, now by DRIVING it.
      * find_site_packages — the refusal was itself the Windows bug. flet ships
        TWO there (the app's, plus the embedded interpreter's own
        Lib/site-packages) and the real app imports from BOTH, so "which one
        does it ship with?" had no answer and the job died on a false dilemma.

    Using every bundled site-packages is not leniency — see
    test_using_both_site_packages_is_not_leniency and
    test_no_bundled_site_packages_still_fails_closed, which pin that a module
    present in NONE of them still fails and that an empty tree still errors.
    """
    smoke = _load_smoke()

    root = tmp_path / "bundle"
    for name in ("first", "second"):
        payload = root / name / "flutter_assets" / "app"
        payload.mkdir(parents=True)
        (payload / "main.py").write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        smoke.find_app_payload(root, tmp_path / "workdir")
    assert exc.value.code != 0, (
        "find_app_payload picked one of two candidate app trees instead of "
        "failing closed — it would smoke-test an arbitrary payload and report "
        "on the whole bundle"
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


# --------------------------------------------------------------------------
# PS-158: the check runs under the interpreter the BUNDLE was frozen for.
#
# These tests exist because this gate's first ever release run blocked v3.0.0 by
# reporting five dependencies "missing" from a bundle that contained all of them.
# The bundle was fine; the check was importing 3.12-built extension modules with
# the runner's 3.13, which searches only for its own ABI tag and so cannot see a
# `-312-` file sitting right there.
#
# MEASURED against the real, published v2.9.17 AppImage — a build users are
# running successfully today:
#   under 3.13 -> fastapi, pydantic, paramiko, mcp, invisible_playwright "missing"
#   under 3.12 -> every import resolved, entry point printed SELFTEST_OK
#
# They drive the script's real functions against real directory trees rather
# than asserting on its source text, because the defect being pinned was
# BEHAVIOURAL: the old code read perfectly and still condemned a healthy bundle.
# --------------------------------------------------------------------------


def _load_smoke():
    """Import the smoke script as a module so tests exercise what it DOES."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ps158_smoke", SMOKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def smoke():
    return _load_smoke()


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


# --------------------------------------------------------------------------
# A real bundle, driven end-to-end through the real script.
#
# The tests below spawn `smoke_frozen_bundle.py` as a subprocess and assert on
# its EXIT CODE and what it NAMES, because that is the only thing a source-level
# assertion cannot fake. The gate this ticket exists to repair was previously
# pinned by `assert "exc.__class__.__name__, exc" in smoke_text` — a grep, which
# fails when someone REWORDS the line and passes when someone DISABLES it. The
# reviewer demonstrated exactly that: `if missing and False:` left the greppable
# literal intact, the whole suite stayed green, and the checker green-lit a
# bundle with no paramiko in it. These tests are the replacement.
# --------------------------------------------------------------------------


def _bundle_ext_suffix() -> str:
    """The RUNNING interpreter's real extension suffix.

    Not a hardcoded `.cpython-312-...so`: the point of detect_bundle_abi is that
    the bundle states its own ABI, so the fixture must stamp itself for whichever
    Python is running the suite (3.12 on Linux, .pyd on Windows). A fixture
    hardcoded to one ABI would make these tests assert "the interpreter is
    mismatched" on every other platform instead of what they mean to assert.
    """
    import sysconfig

    return sysconfig.get_config_var("EXT_SUFFIX") or ".so"


def _make_bundle(root: Path, omit: set[str] = frozenset(), split: bool = False) -> Path:
    """A minimal frozen bundle the real script accepts: app payload, the
    required asset, a version-stating updater, a selftest-gated entry point, and
    every REQUIRED_IMPORTS module as an importable package.

    `omit` leaves modules out — that is the genuinely-missing-dependency case.
    `split` reproduces the Windows two-site-packages shape, spreading the modules
    across BOTH directories so neither alone is sufficient.
    """
    smoke = _load_smoke()
    version = re.search(
        r'APP_VERSION\s*=\s*"([^"]+)"',
        (REPO_ROOT / "src" / "services" / "app_update" / "updater.py").read_text(
            encoding="utf-8"
        ),
    ).group(1)

    app = root / "flutter_assets" / "app"
    _touch(app / "assets" / "icon.png")
    updater = app / "src" / "services" / "app_update"
    updater.mkdir(parents=True, exist_ok=True)
    for pkg in (app / "src", app / "src" / "services", updater):
        _touch(pkg / "__init__.py")
    (updater / "updater.py").write_text(f'APP_VERSION = "{version}"\n', encoding="utf-8")
    # Mirrors src/main.py's real gate: pre-GUI, pre-port-bind, prints the token
    # the self-updater waits for and hard-exits.
    (app / "main.py").write_text(
        'import os\n'
        'if __name__ == "__main__":\n'
        '    if os.environ.get("PERSONA_SELFTEST") == "1":\n'
        '        print("SELFTEST_OK", flush=True)\n'
        '        os._exit(0)\n',
        encoding="utf-8",
    )

    dirs = [root / "site-packages"]
    if split:
        dirs.append(root / "Lib" / "site-packages")
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(n for n in smoke.REQUIRED_IMPORTS if n not in omit):
        # Round-robin so a split bundle needs BOTH directories to import cleanly.
        _touch(dirs[i % len(dirs)] / name / "__init__.py")

    # The bundle must state which interpreter it was frozen for, or the script
    # correctly refuses to guess one (test_a_bundle_with_no_compiled_extensions_
    # fails_closed). Stamp it for the interpreter running this suite.
    _touch(dirs[0] / "_speedup" / f"_core{_bundle_ext_suffix()}")
    return root


def _run_smoke(bundle_root: Path):
    import subprocess
    import sys as _sys

    proc = subprocess.run(
        [
            _sys.executable,
            str(SMOKE_SCRIPT),
            str(bundle_root),
            "--repo-root",
            str(REPO_ROOT),
            "--python",
            _sys.executable,
        ],
        capture_output=True,
        text=True,
        timeout=300,
     encoding="utf-8")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_the_fixture_bundle_is_actually_accepted(tmp_path) -> None:
    """The control for every red case below.

    Without this, a fixture broken for some unrelated reason would make the
    failing tests pass for the wrong reason — they would be asserting "the
    script rejects my malformed fixture", not "the script catches a missing
    dependency". This proves the only difference in those cases is the omission.
    """
    code, out = _run_smoke(_make_bundle(tmp_path / "bundle"))
    assert code == 0, f"the healthy fixture bundle was rejected:\n{out}"
    assert "PERSONA_BUNDLE_IMPORTS_OK" in out
    assert "SELFTEST_OK" in out


def test_a_genuinely_missing_dependency_fails_the_build(tmp_path) -> None:
    """THE boundary between a repaired check and a removed one, DRIVEN.

    This is the guarantee the whole ticket exists to protect: the smoke check
    was repaired so it stops condemning healthy bundles, and the one thing that
    repair must not cost is its ability to condemn a genuinely broken one.

    Asserted as an OUTCOME (non-zero exit, the module named in the output) and
    deliberately not as a property of the source, so that disabling the gate
    without touching a single greppable literal — `if missing and False:` —
    turns this red. Deleting paramiko is the measured case, not a hypothetical:
    the app imports it lazily on the SSH path, so the bundle still opens and
    still prints its correct version. Opening is not evidence of completeness.
    """
    bundle = _make_bundle(tmp_path / "bundle", omit={"paramiko"})
    code, out = _run_smoke(bundle)
    assert code != 0, (
        "the smoke check passed a bundle with NO paramiko in it — the gate has "
        f"been removed rather than repaired:\n{out}"
    )
    assert "paramiko" in out, (
        f"the build failed but never named the missing module:\n{out}"
    )


def test_a_failed_import_reports_the_module_that_actually_failed(tmp_path) -> None:
    """A TRANSITIVE import failure must name the module that really could not be
    found, not the package being checked.

    Recording only `exc.__class__.__name__` printed "fastapi
    (ModuleNotFoundError)", which reads as "fastapi is not in the bundle" — and
    that sentence was false and cost a release. What had actually failed was a
    transitive import of `pydantic_core._pydantic_core`, a 3.12-built extension,
    under 3.13.

    That distinction is the ticket's per-module discriminator, so it is asserted
    on OUTPUT rather than on source text:

        names ITSELF       -> the module is genuinely absent
        names a DEPENDENCY -> present, but built for another interpreter

    This REPLACES a `assert "exc.__class__.__name__, exc" in smoke_text` grep,
    which broke on a rewording and survived a disabling.
    """
    bundle = _make_bundle(tmp_path / "bundle")
    present = bundle / "site-packages" / "mcp" / "__init__.py"
    assert present.is_file(), "fixture drift: mcp is not where this test patches it"
    present.write_text("import _persona_absent_backend\n", encoding="utf-8")

    code, out = _run_smoke(bundle)
    assert code != 0, f"an unimportable dependency was waved through:\n{out}"
    assert "_persona_absent_backend" in out, (
        "the report discards the exception MESSAGE, so a transitive import "
        f"failure is misreported as the top-level package being absent:\n{out}"
    )


def test_bundle_abi_is_read_from_the_bundles_own_extensions(smoke, tmp_path) -> None:
    """The required interpreter is MEASURED from the artifact, not configured.

    A constant would be a second source of truth that keeps saying 3.12 while
    flet quietly moves to 3.13 — and that failure looks exactly like the one
    this fixes, so it would be diagnosed as "missing dependencies" all over again.
    """
    sp = tmp_path / "site-packages"
    _touch(sp / "pydantic_core" / "_pydantic_core.cpython-312-x86_64-linux-gnu.so")
    assert smoke.detect_bundle_abi([sp]) == (3, 12)

    win = tmp_path / "win" / "site-packages"
    _touch(win / "pydantic_core" / "_pydantic_core.cp313-win_amd64.pyd")
    assert smoke.detect_bundle_abi([win]) == (3, 13)


def test_stable_abi_extensions_do_not_decide_the_version(smoke, tmp_path) -> None:
    """`foo.abi3.so` imports on EVERY version, so it is evidence of nothing.

    This is not hypothetical: cryptography ships abi3 and imported fine under
    the wrong interpreter, which is part of why the failure looked like five
    arbitrary packages rather than one systematic cause. Letting an abi3 file
    vote would let the check pick an interpreter that cannot load the
    version-specific extensions sitting beside it.
    """
    sp = tmp_path / "site-packages"
    _touch(sp / "cryptography" / "_rust.abi3.so")
    with pytest.raises(SystemExit):
        smoke.detect_bundle_abi([sp])  # abi3 alone determines nothing

    _touch(sp / "pydantic_core" / "_pydantic_core.cpython-312-x86_64-linux-gnu.so")
    assert smoke.detect_bundle_abi([sp]) == (3, 12)


def test_a_bundle_with_no_compiled_extensions_fails_closed(smoke, tmp_path) -> None:
    """persona's bundle carries ~24 compiled extensions. Finding none means we
    are looking at the wrong tree — and continuing would mean choosing an
    interpreter at random, which is the whole defect."""
    sp = tmp_path / "site-packages"
    _touch(sp / "somepkg" / "__init__.py")
    with pytest.raises(SystemExit):
        smoke.detect_bundle_abi([sp])


def test_a_bundle_built_for_two_pythons_is_a_real_defect(smoke, tmp_path) -> None:
    """No single interpreter can import both, so this is the bundle's problem
    and must be reported rather than resolved by picking a side."""
    sp = tmp_path / "site-packages"
    _touch(sp / "a" / "x.cpython-312-x86_64-linux-gnu.so")
    _touch(sp / "b" / "y.cpython-313-x86_64-linux-gnu.so")
    with pytest.raises(SystemExit):
        smoke.detect_bundle_abi([sp])


def test_every_bundled_site_packages_is_used(smoke, tmp_path) -> None:
    """Windows ships TWO — the app's, and the embedded interpreter's own
    Lib/site-packages — and the real app imports from both.

    The old code refused outright ("Refusing to guess which one the app ships
    with"), which is what failed the Windows job. The question had no answer
    because the premise was wrong: it ships both.
    """
    root = tmp_path / "bundle"
    _touch(root / "site-packages" / "flet" / "__init__.py")
    _touch(root / "Lib" / "site-packages" / "paramiko" / "__init__.py")
    found = smoke.find_site_packages(root)
    assert {p.name for p in found} == {"site-packages"}
    assert len(found) == 2, f"only {found} used — a dependency in the other is invisible"


def test_using_both_site_packages_is_not_leniency(smoke, tmp_path) -> None:
    """The search got COMPLETE, not forgiving — asserted as an OUTCOME.

    Using every bundled site-packages instead of refusing to choose between them
    is the fix for the Windows job, and it is also the change most easily
    mistaken for a relaxation. So the boundary is DRIVEN here, both directions,
    on the Windows two-directory shape:

      * a dependency in ONE of the two directories -> the build passes, because
        the real app imports from both and refusing was a false dilemma
      * a dependency in NEITHER -> the build still FAILS and names it

    This test previously stated exactly that property in its docstring and then
    asserted only that the returned paths were inside the bundle. The docstring
    described the case; the code never ran it, so the name promised a boundary
    that nothing pinned.
    """
    root = tmp_path / "paths"
    _touch(root / "site-packages" / "flet" / "__init__.py")
    _touch(root / "Lib" / "site-packages" / "paramiko" / "__init__.py")
    for found in smoke.find_site_packages(root):
        assert root in found.parents or found == root, (
            f"{found} is outside the bundle — imports could resolve from the runner"
        )

    # Split across BOTH directories: neither alone carries every dependency, so
    # this passes only if all of them are really used.
    code, out = _run_smoke(_make_bundle(tmp_path / "split", split=True))
    assert code == 0, (
        "a bundle whose dependencies are spread across the two site-packages "
        f"directories flet ships on Windows was rejected:\n{out}"
    )

    # Same two-directory shape, one module in NEITHER. The completeness above
    # must not have cost the check its ability to say no.
    code, out = _run_smoke(_make_bundle(tmp_path / "gap", split=True, omit={"mcp"}))
    assert code != 0, (
        "a module present in NEITHER site-packages was waved through — using "
        f"both directories has become leniency:\n{out}"
    )
    assert "mcp" in out, f"the build failed but never named the missing module:\n{out}"


def test_no_bundled_site_packages_still_fails_closed(smoke, tmp_path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(SystemExit):
        smoke.find_site_packages(root)


def test_a_mismatched_interpreter_is_refused_not_substituted(smoke) -> None:
    """"Could not find the right Python, so I used the one I had" is exactly how
    a healthy bundle got reported as missing five dependencies.

    An explicit --python that does not match must be REFUSED, because it means
    the workflow is wired wrong and silently substituting would hide that.
    """
    import sys as _sys

    running = _sys.version_info[:2]
    wrong = (running[0], running[1] + 1)
    with pytest.raises(SystemExit):
        smoke.resolve_interpreter(wrong, _sys.executable)


def test_a_matching_interpreter_is_accepted(smoke) -> None:
    import sys as _sys

    assert smoke.resolve_interpreter(_sys.version_info[:2], _sys.executable) == _sys.executable


def test_an_unobtainable_interpreter_errors_rather_than_falling_back(smoke) -> None:
    """A version that cannot exist must produce a RED build, not a quiet
    downgrade to whatever is on PATH."""
    with pytest.raises(SystemExit):
        smoke.resolve_interpreter((3, 99), None)


@pytest.mark.parametrize("job", BUILD_JOBS)
def test_every_build_job_checks_with_the_bundles_own_python(release_yaml, job) -> None:
    """The gate must not run under whatever Python the job happens to have.

    Pinned per job because this failed on all three platforms at once: a fix
    applied to one is not a fix.
    """
    steps = release_yaml["jobs"][job]["steps"]
    smoke_idx = next(
        i for i, s in enumerate(steps) if "smoke_frozen_bundle.py" in str(s.get("run", ""))
    )
    provision = [
        i for i, s in enumerate(steps)
        if "setup-python" in str(s.get("uses", ""))
        and str(s.get("with", {}).get("python-version", "")).startswith("3.12")
    ]
    assert provision, (
        f"{job} never provisions the 3.12 interpreter the bundle is frozen for, "
        "so the check would run under the job's 3.13 and report every "
        "version-specific extension as missing"
    )
    assert min(provision) < smoke_idx, (
        f"{job} provisions the bundle interpreter AFTER the smoke step"
    )
    assert "--python" in str(steps[smoke_idx].get("run", "")), (
        f"{job} does not tell the smoke check which interpreter to use"
    )


@pytest.mark.parametrize("job", BUILD_JOBS)
def test_the_bundle_interpreter_does_not_become_the_jobs_default(release_yaml, job) -> None:
    """update-environment: false is load-bearing.

    Everything after this step (flet, appimagetool, Inno Setup, hdiutil) must
    keep using the job's own interpreter. Letting 3.12 take over the PATH would
    fix the smoke check by breaking the packaging steps behind it.
    """
    steps = release_yaml["jobs"][job]["steps"]
    provisioning = [
        s for s in steps
        if "setup-python" in str(s.get("uses", ""))
        and str(s.get("with", {}).get("python-version", "")).startswith("3.12")
    ]
    assert provisioning, f"{job} does not provision the bundle interpreter at all"
    for step in provisioning:
        assert step.get("with", {}).get("update-environment") is False, (
            f"{job} lets the bundle's 3.12 become the job default, which would "
            "change the interpreter every later packaging step runs under"
        )


# ==========================================================================
# PS-163: the frozen-bundle gate's REQUIRED_IMPORTS list vs the DECLARED deps.
#
# smoke_frozen_bundle.py:118-127 documents a blind spot in its own words: the
# list "IS A SNAPSHOT, AND IT DOES NOT UPDATE ITSELF", and a dependency missing
# from it "is simply never checked inside the bundle, so a lazily-imported one
# can go missing from the frozen tree and still sail through this gate green."
# Nothing enforced that maintenance, and it had ALREADY drifted four times over
# (python-dotenv, aiohttp, psutil, pywin32) by the time this was written.
#
# These tests make the drift LOUD. They do NOT derive the list: that
# alternative is declined at :118-127 and the decision stands — an import scan
# that cannot resolve conditional and in-function imports under-reports
# invisibly, which is worse than a hand-list that is visibly a hand-list.
# REQUIRED_IMPORTS stays hand-written; forgetting to write it is what is caught.
#
# The comparison is a PURE FUNCTION over (declared, required_imports,
# dist_import_names, exempt, undeclared_allowed). That shape is load-bearing:
# a check that can only ever run against the real files cannot be SHOWN to
# fail, and a check that has never been shown to fail has not been built. The
# falsification tests below inject synthetic declaration sets to drive it red
# in both directions.
#
# Asserted on OUTCOME (the real list, as data, imported via _load_smoke) and
# never on the script's source text — see PS-11 and PS-158's round-2 reject.
# ==========================================================================


def _normalize_dist(name: str) -> str:
    """PEP 503 normalization, so PySocks/pysocks/py_socks are one key."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _default_import_name(dist: str) -> str:
    """The import name a distribution has when nothing says otherwise."""
    return _normalize_dist(dist).replace("-", "_")


class CrossCheckResult:
    """What the two directions of the comparison found.

    `unchecked`   -- declared, but no module of it is verified in the bundle.
    `unexplained` -- verified in the bundle, but nothing declares it and no
                     allow-list entry explains where it comes from.
    """

    def __init__(self, unchecked: dict[str, list[str]], unexplained: list[str]) -> None:
        self.unchecked = unchecked
        self.unexplained = unexplained

    @property
    def ok(self) -> bool:
        return not self.unchecked and not self.unexplained


def cross_check(
    declared: set[str],
    required_imports: list[str],
    dist_import_names: dict[str, tuple[str, ...]],
    exempt: dict[str, str],
    undeclared_allowed: dict[str, str],
) -> CrossCheckResult:
    """Compare DECLARED dependencies against the modules the bundle verifies.

    Pure: every input is a parameter, nothing is read from disk, no marker is
    evaluated against the running platform. Injecting a synthetic `declared`
    is how this is driven red on demand.

    FORWARD  (the direction the maintenance comment says fails silently):
      every declared distribution must have at least one of its import names in
      REQUIRED_IMPORTS, unless it is exempt with a written reason.
    REVERSE:
      every entry in REQUIRED_IMPORTS must be traceable to a declared
      distribution, unless an allow-list entry records the transitive edge.
    """
    exempt_keys = {_normalize_dist(k) for k in exempt}
    names_for: dict[str, tuple[str, ...]] = {}
    for dist in declared:
        key = _normalize_dist(dist)
        names_for[key] = dist_import_names.get(key, (_default_import_name(key),))

    required = set(required_imports)

    unchecked: dict[str, list[str]] = {}
    for key, modules in names_for.items():
        if key in exempt_keys:
            continue
        if not required.intersection(modules):
            unchecked[key] = sorted(modules)

    explained = set(undeclared_allowed)
    covered = {m for key, mods in names_for.items() for m in mods}
    unexplained = sorted(m for m in required if m not in covered and m not in explained)

    return CrossCheckResult(unchecked, unexplained)


def _declared_dependencies() -> set[str]:
    """The distribution names pyproject.toml's [project].dependencies declares.

    pyproject is the AUTHORITY for what the bundle contains, because `flet
    build` resolves the bundle's dependencies from it. requirements.txt is NOT
    read here: the two genuinely disagree (it omits invisible_playwright and
    invisible_core), and letting it define the bundle claim would be a second,
    separate concern silently taking over this one.

    Extras (`uvicorn[standard]`), version specifiers, PEP 508 environment
    markers (`; sys_platform == 'win32'`) and direct references
    (`invisible_playwright @ git+https://...`) all have to come off, which is
    why this parses with packaging rather than splitting on punctuation.
    """
    import tomllib

    from packaging.requirements import Requirement

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {Requirement(dep).name for dep in data["project"]["dependencies"]}


# ---- The real tree. These are the ones that go red when the list drifts. ----


def test_every_declared_dependency_is_checked_inside_the_bundle(smoke) -> None:
    """FORWARD direction — the one :118-127 says fails silently.

    Add a dependency to pyproject without adding it to REQUIRED_IMPORTS (or to
    the exemption map with a reason) and this goes red NAMING the module. That
    is the whole point: the failure mode being closed is a lazily-imported
    dependency going missing from the frozen tree while the gate stays green.
    """
    result = cross_check(
        declared=_declared_dependencies(),
        required_imports=smoke.REQUIRED_IMPORTS,
        dist_import_names=smoke.DISTRIBUTION_IMPORT_NAMES,
        exempt=smoke.EXEMPT_FROM_BUNDLE_IMPORT_CHECK,
        undeclared_allowed=smoke.UNDECLARED_REQUIRED_IMPORTS,
    )
    assert not result.unchecked, (
        "pyproject declares these dependencies, but the frozen-bundle gate "
        "never imports them inside the bundle, so one could go missing from the "
        "frozen tree and still pass green:\n"
        + "\n".join(
            f"  - {dist} (imports as: {', '.join(mods)})"
            for dist, mods in sorted(result.unchecked.items())
        )
        + "\n\nAdd the import name to REQUIRED_IMPORTS in "
        ".github/scripts/smoke_frozen_bundle.py, or — only if it is genuinely "
        "optional-by-design at every call site — add it to "
        "EXEMPT_FROM_BUNDLE_IMPORT_CHECK with a specific written reason. "
        "Do NOT derive the list (declined at :118-127)."
    )


def test_every_checked_module_is_traceable_to_a_declaration(smoke) -> None:
    """REVERSE direction, which must not fire naively.

    pydantic and httpx are each in REQUIRED_IMPORTS while being declared
    NOWHERE (zero occurrences across all three requirement files); they arrive
    transitively. They are handled by an allow-list carrying a reason per entry
    — not by a bare exclusion, and not by dropping the assertion.
    """
    result = cross_check(
        declared=_declared_dependencies(),
        required_imports=smoke.REQUIRED_IMPORTS,
        dist_import_names=smoke.DISTRIBUTION_IMPORT_NAMES,
        exempt=smoke.EXEMPT_FROM_BUNDLE_IMPORT_CHECK,
        undeclared_allowed=smoke.UNDECLARED_REQUIRED_IMPORTS,
    )
    assert not result.unexplained, (
        "the frozen-bundle gate checks these modules, but nothing declares "
        f"them and no allow-list entry says where they come from: "
        f"{', '.join(result.unexplained)}. Establish the transitive edge and "
        "record it in UNDECLARED_REQUIRED_IMPORTS, or declare the dependency."
    )


def test_the_four_dependencies_that_had_already_drifted_are_resolved(smoke) -> None:
    """The four gaps PS-163 found, pinned individually so a future edit that
    quietly drops one is named rather than absorbed into a set-difference.

    Each is EITHER checked in the bundle OR exempt with a reason — decided on
    its own merits, not by a blanket rule.
    """
    required = set(smoke.REQUIRED_IMPORTS)

    # Added: silent-wrong when absent (the operator's .env is ignored, quietly).
    assert "dotenv" in required, "python-dotenv is declared but not checked"
    # Added: fails closed at use, but the proxy-checking feature is dead.
    assert "aiohttp" in required, "aiohttp is declared but not checked"
    # Added: pyproject calls >=6.0 a SECURITY floor; absent, both loopback
    # listeners stop authenticating their caller (peerauth.py:120-133).
    assert "psutil" in required, (
        "psutil is declared but not checked — a bundle without it silently "
        "drops peer authentication on both loopback listeners"
    )

    # Exempt, because REQUIRED_IMPORTS has no notion of platform and is imported
    # unconditionally by the bootstrap: listing pywintypes would break the gate
    # on Linux and macOS. The reason must be RECORDED, not implied.
    exempt = {_normalize_dist(k): v for k, v in smoke.EXEMPT_FROM_BUNDLE_IMPORT_CHECK.items()}
    assert "pywin32" in exempt, "pywin32 must be resolved explicitly, not ignored"
    reason = exempt["pywin32"]
    assert len(reason) > 80, "an exemption needs a specific reason, not a label"


def test_no_windows_only_module_is_imported_unconditionally(smoke) -> None:
    """Platform correctness: the gate runs on Linux, macOS AND Windows.

    REQUIRED_IMPORTS is injected into the bootstrap at run_bundle() and every
    entry is imported unconditionally, so a Windows-only module in that list
    would make the gate fail on the two platforms where the module is correctly
    absent. This is the test that would catch the naive fix.
    """
    required = set(smoke.REQUIRED_IMPORTS)
    for dist, (_platform, modules) in smoke.PLATFORM_CONDITIONAL_IMPORTS.items():
        leaked = required.intersection(modules)
        assert not leaked, (
            f"{dist} is platform-conditional, but {', '.join(sorted(leaked))} "
            "is in the flat REQUIRED_IMPORTS list, which the bootstrap imports "
            "unconditionally — this fails the gate on Linux and macOS"
        )


def test_the_import_name_map_covers_every_distribution_that_needs_one(smoke) -> None:
    """The dist-name != import-name cases are mapped EXPLICITLY.

    A naive set-difference is wrong on nearly every interesting entry here
    (PySocks -> socks, python-dotenv -> dotenv, pywin32 -> four modules). If a
    mapping silently went missing the forward check would report a false gap,
    so pin the ones this repo actually depends on.
    """
    mapped = {_normalize_dist(k): v for k, v in smoke.DISTRIBUTION_IMPORT_NAMES.items()}
    assert mapped.get("pysocks") == ("socks",)
    assert mapped.get("python-dotenv") == ("dotenv",)
    assert set(mapped.get("pywin32", ())) == {
        "pywintypes", "win32api", "win32con", "win32job",
    }


def test_declared_dependencies_parse_past_extras_markers_and_direct_refs() -> None:
    """The parsing step itself, on the real file's genuinely awkward entries.

    `uvicorn[standard]>=0.30.0` (extra), `pywin32>=311; sys_platform ==
    'win32'` (marker) and `invisible_playwright @ git+https://...` (direct
    reference) must all reduce to bare distribution names. A regex that
    "mostly works" under-reports, which is the failure this whole check exists
    to prevent.
    """
    declared = {_normalize_dist(d) for d in _declared_dependencies()}
    assert "uvicorn" in declared, "the [standard] extra was not stripped"
    assert "pywin32" in declared, "the environment marker was not parsed off"
    assert "invisible-playwright" in declared, "the direct reference did not resolve"
    assert not any(c in d for d in declared for c in "[]<>=@; "), (
        f"a declared name kept specifier punctuation: {sorted(declared)}"
    )


# ---- Falsification. A check that passes on everything has not been built. ----
#
# Every test below drives `cross_check` RED with a synthetic declaration set.
# This is why the comparison is a pure function: against the real files alone
# these cases are unreachable, and a check that cannot be shown to fail is not
# evidence of anything. Each mirrors a drift that has actually happened here.


def _real(smoke, **override):
    """cross_check against the real list/maps, with one input swapped out."""
    kwargs = dict(
        declared=_declared_dependencies(),
        required_imports=smoke.REQUIRED_IMPORTS,
        dist_import_names=smoke.DISTRIBUTION_IMPORT_NAMES,
        exempt=smoke.EXEMPT_FROM_BUNDLE_IMPORT_CHECK,
        undeclared_allowed=smoke.UNDECLARED_REQUIRED_IMPORTS,
    )
    kwargs.update(override)
    return cross_check(**kwargs)


def test_falsify_a_new_dependency_added_to_pyproject_but_not_to_the_list(smoke) -> None:
    """THE headline case: someone adds a dependency and forgets the list.

    This is the exact scenario :118-127 says goes silently unchecked today.
    """
    result = _real(smoke, declared=_declared_dependencies() | {"tenacity"})

    assert not result.ok, "adding an unlisted dependency did NOT go red"
    assert "tenacity" in result.unchecked, "the drifting module was not NAMED"
    assert result.unchecked["tenacity"] == ["tenacity"]


def test_falsify_each_of_the_four_real_gaps_by_removing_it_again(smoke) -> None:
    """Re-open each closed gap one at a time and confirm it is caught.

    This is what proves the check would have named the four rather than that
    the four merely happen to be present now.
    """
    for module, dist in (
        ("dotenv", "python-dotenv"),
        ("aiohttp", "aiohttp"),
        ("psutil", "psutil"),
    ):
        shrunk = [m for m in smoke.REQUIRED_IMPORTS if m != module]
        result = _real(smoke, required_imports=shrunk)
        assert dist in result.unchecked, (
            f"dropping {module} from REQUIRED_IMPORTS was not caught — this is "
            "precisely the silent drift the check exists to make loud"
        )
        assert result.unchecked[dist] == [module]

    # pywin32 is the fourth, and it is resolved by EXEMPTION rather than by
    # listing. Drop the exemption and it must surface as unchecked.
    result = _real(smoke, exempt={})
    assert "pywin32" in result.unchecked, (
        "pywin32 is neither checked nor exempt, and nothing noticed"
    )


def test_falsify_an_unexplained_module_in_the_list(smoke) -> None:
    """REVERSE direction red: a module checked in the bundle that nothing
    declares and no allow-list entry explains."""
    result = _real(smoke, required_imports=[*smoke.REQUIRED_IMPORTS, "boto3"])

    assert not result.ok, "an undeclared, unexplained module did NOT go red"
    assert "boto3" in result.unexplained, "the unexplained module was not NAMED"


def test_falsify_dropping_the_transitive_allow_list_entries(smoke) -> None:
    """Removing the reasons for pydantic/httpx must make the reverse direction
    fire on BOTH — the check is not quietly hard-coded to tolerate them, and a
    reverse assertion built for one anomaly would miss the other."""
    result = _real(smoke, undeclared_allowed={})

    assert "pydantic" in result.unexplained
    assert "httpx" in result.unexplained, (
        "httpx is the SECOND reverse anomaly; a check built around pydantic "
        "alone silently under-reports here"
    )


def test_falsify_a_missing_import_name_mapping(smoke) -> None:
    """Without the explicit map, the dist-name != import-name entries report a
    FALSE gap — which is why the mapping is explicit rather than regex-guessed.

    PySocks is declared and `socks` IS checked; drop the mapping and the naive
    comparison claims PySocks is unchecked while calling `socks` unexplained.
    """
    result = _real(smoke, dist_import_names={})

    assert "pysocks" in result.unchecked, "the naive comparison did not misfire"
    assert "socks" in result.unexplained
    # ...and with the real map, neither happens.
    assert _real(smoke).ok


def test_the_check_is_green_on_the_real_tree(smoke) -> None:
    """The other side of falsification: green here is only meaningful because
    every test above showed the same function red on a constructed case."""
    result = _real(smoke)
    assert result.ok, (
        f"unchecked={result.unchecked} unexplained={result.unexplained}"
    )


# --------------------------------------------------------------------------
# PS-168: pywin32 must be IMPORTABLE from the Windows bundle, not merely present
#
# The 3.0.0 release failed three times on a Windows bundle that CONTAINED
# pywin32 and still could not import it. `pywintypes` ships at win32/lib/ and
# is put on the path only by pywin32.pth — and a .pth is executed by the `site`
# module, only for directories site derives from sys.prefix. Neither consumer
# qualifies: the shipped app puts site-packages on PYTHONPATH, and the smoke
# check runs -S -E. So the .pth is inert in the product AND in the gate.
#
# These tests pin the property that makes the fix meaningful — that the bundle
# is IMPORTABLE — rather than that a step exists. Each red case is constructed
# and shown to fail before the green case is trusted.
# --------------------------------------------------------------------------

FLATTEN_SCRIPT = REPO_ROOT / ".github" / "scripts" / "flatten_pywin32.py"


def _run_flatten(bundle_root: Path):
    import subprocess
    import sys as _sys

    proc = subprocess.run(
        [_sys.executable, str(FLATTEN_SCRIPT), str(bundle_root)],
        capture_output=True,
        text=True,
        timeout=120,
     encoding="utf-8")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _pywin32_bundle(tmp_path: Path) -> Path:
    """A bundle shaped like the one that FAILED: pywin32 present, unreachable.

    Mirrors the real wheel layout rather than inventing one — pywintypes.py at
    win32/lib/, the ABI-tagged DLL in pywin32_system32/, a pywin32.pth naming
    the directories, and a .pyd at win32/ top level. The bytes do not matter;
    the LAYOUT is the whole subject.
    """
    sp = tmp_path / "build" / "windows" / "site-packages"
    (sp / "win32" / "lib").mkdir(parents=True)
    (sp / "pywin32_system32").mkdir(parents=True)

    (sp / "win32" / "lib" / "pywintypes.py").write_text("# magic redirector\n", encoding="utf-8")
    (sp / "win32" / "lib" / "win32con.py").write_text("VALUE = 1\n", encoding="utf-8")
    (sp / "win32" / "win32api.pyd").write_bytes(b"MZ fake extension")
    (sp / "pywin32_system32" / "pywintypes312.dll").write_bytes(b"MZ fake dll")
    (sp / "pywin32.pth").write_text("win32\nwin32\\lib\npythonwin\n", encoding="utf-8")
    return tmp_path / "build" / "windows"


def _resolve_from(root: Path, module: str):
    """Ask the import system to RESOLVE `module` with `root` as the only path.

    This is what both consumers actually do — the app via PYTHONPATH, the smoke
    check via `sys.path[:0]` under `-S` — so resolution against a single
    directory is the honest question, not "does a file exist somewhere".
    `.pyd` is appended to the extension suffixes because the HOST running this
    test is Linux, whose importer does not recognise Windows extensions; that
    is a property of the test host, not of the bundle.
    """
    import importlib.machinery as machinery

    finder = machinery.FileFinder(
        str(root),
        (machinery.ExtensionFileLoader, [*machinery.EXTENSION_SUFFIXES, ".pyd"]),
        (machinery.SourceFileLoader, machinery.SOURCE_SUFFIXES),
        (machinery.SourcelessFileLoader, machinery.BYTECODE_SUFFIXES),
    )
    return finder.find_spec(module)


def test_falsify_the_unfixed_bundle_cannot_import_pywintypes(tmp_path) -> None:
    """RED FIRST — the precondition, established by RESOLUTION rather than shape.

    On the shape that shipped, `pywintypes` is not RESOLVABLE from the
    site-packages root, which is the only directory either consumer puts on the
    path. Asking the import system — instead of asserting where files sit — is
    what makes this test prove the thing its name claims. This is the exact
    state the smoke check reported as "No module named 'pywintypes'".
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"

    assert (sp / "win32" / "lib" / "pywintypes.py").is_file(), "fixture is wrong"
    assert _resolve_from(sp, "pywintypes") is None, (
        "the unfixed bundle already resolves pywintypes from the site-packages "
        "root — this test would then prove nothing"
    )


def test_flatten_makes_pywintypes_importable(tmp_path) -> None:
    """GREEN AFTER — and green only because the case above is red.

    The module and its companion DLL must land at the site-packages root: that
    is the directory the app reaches via PYTHONPATH and the smoke reaches via
    sys.path[:0], and it is the one place that does not depend on `site`.
    """
    root = _pywin32_bundle(tmp_path)
    rc, out = _run_flatten(root)
    assert rc == 0, f"flatten failed: {out}"

    sp = root / "site-packages"
    assert (sp / "pywintypes.py").is_file(), f"pywintypes.py not flattened: {out}"
    assert (sp / "win32con.py").is_file(), "other win32/lib modules not flattened"
    assert (sp / "win32api.pyd").is_file(), "win32 extensions not flattened"
    # pywintypes.py finds its DLL via os.path.dirname(__file__) among other
    # branches; that branch is the one this layout relies on, so the DLL has to
    # be a SIBLING of the module, not merely somewhere in the tree.
    assert (sp / "pywintypes312.dll").is_file(), (
        "the companion DLL did not land beside the module — the import would "
        "resolve the module and then fail loading its DLL"
    )


def test_flatten_leaves_the_original_layout_intact(tmp_path) -> None:
    """Copy, don't move. Anything that already resolved must keep resolving."""
    root = _pywin32_bundle(tmp_path)
    assert _run_flatten(root)[0] == 0

    sp = root / "site-packages"
    assert (sp / "win32" / "lib" / "pywintypes.py").is_file()
    assert (sp / "pywin32_system32" / "pywintypes312.dll").is_file()


def test_flatten_is_idempotent(tmp_path) -> None:
    """It runs on every build; a second run must not fail or duplicate work."""
    root = _pywin32_bundle(tmp_path)
    assert _run_flatten(root)[0] == 0
    rc, out = _run_flatten(root)
    assert rc == 0, f"second run failed: {out}"
    assert (root / "site-packages" / "pywintypes.py").is_file()


def test_flatten_is_a_noop_without_pywin32(tmp_path) -> None:
    """Non-Windows bundles have no pywin32 and that is CORRECT, not an error.

    Linux and macOS build clean today; this must not become a reason for them
    to go red.
    """
    sp = tmp_path / "build" / "linux" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "flet").mkdir()

    rc, out = _run_flatten(tmp_path / "build" / "linux")
    assert rc == 0, f"a bundle without pywin32 must be accepted: {out}"
    assert "nothing to flatten" in out


def test_flatten_refuses_a_pywin32_it_cannot_make_importable(tmp_path) -> None:
    """FAIL CLOSED on the state that actually shipped the bug.

    pywin32 present but yielding no importable payload is precisely the
    "looks installed, cannot import" condition this ticket exists to remove. It
    must stop the build rather than be reported and stepped over.
    """
    sp = tmp_path / "build" / "windows" / "site-packages"
    (sp / "win32" / "lib").mkdir(parents=True)
    (sp / "pywin32.pth").write_text("win32\nwin32\\lib\n", encoding="utf-8")
    # the marker and the directory exist, but there is no payload at all

    rc, out = _run_flatten(tmp_path / "build" / "windows")
    assert rc != 0, f"a present-but-unflattenable pywin32 was accepted: {out}"
    assert "refusing to ship" in out.lower()


# --------------------------------------------------------------------------
# PS-168 rework: the verification must be IDENTITY-shaped, not PRESENCE-shaped.
#
# The first version of this script asked "does a file with this name exist?",
# which is satisfied by the very file its collision branch had just refused to
# overwrite. A foreign pywintypes.py at the root produced: 0 files copied, both
# sentinels "verified", exit 0 — success reported over a bundle that imports an
# ImportError bomb as `pywintypes`. That is this ticket's OWN named failure
# mode: "a fix that makes the check pass without pywintypes actually importing
# from the bundle". These pin it shut.
# --------------------------------------------------------------------------


def test_a_foreign_pywintypes_squatting_the_name_is_fatal(tmp_path) -> None:
    """The demonstrated exploit. Presence-shaped verification passed this.

    Nothing gets copied (the name is taken), so a check that asks only "is
    there a pywintypes.py at the root" is satisfied BY THE IMPOSTOR. The build
    must stop instead: the bundle would import a foreign module as pywintypes,
    which is the 3.0.0 failure wearing a different hat.
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    sp.joinpath("pywintypes.py").write_text(
        'raise ImportError("I am NOT pywin32 pywintypes")\n'
    , encoding="utf-8")

    rc, out = _run_flatten(root)
    assert rc != 0, (
        "a bundle whose pywintypes is a FOREIGN file was accepted — the "
        f"verification is satisfied by a file it did not write: {out}"
    )
    assert "refusing to ship" in out.lower()
    assert "pywintypes.py" in out, "the error does not name the offending file"


def test_a_foreign_companion_dll_squatting_the_name_is_fatal(tmp_path) -> None:
    """Same hole, the other sentinel: the DLL glob only asked "any match?"."""
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    sp.joinpath("pywintypes312.dll").write_bytes(b"NOT A DLL AT ALL")

    rc, out = _run_flatten(root)
    assert rc != 0, f"a FOREIGN companion DLL was accepted as verification: {out}"
    assert "refusing to ship" in out.lower()


def test_identity_not_size_decides_already_flattened(tmp_path) -> None:
    """`st_size` equality was standing in for identity.

    A same-size foreign file read as "already flattened by a previous run" and
    was silently stepped over. Size is not identity; a bundle must not ship on
    the strength of a coincidence.
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    real = (sp / "win32" / "lib" / "pywintypes.py").read_text(encoding="utf-8")
    # byte-for-byte the same LENGTH, entirely different content
    impostor = "#" + "x" * (len(real) - 2) + "\n"
    assert len(impostor) == len(real), "the fixture must hold size constant"
    sp.joinpath("pywintypes.py").write_text(impostor, encoding="utf-8")

    rc, out = _run_flatten(root)
    assert rc != 0, (
        "a same-SIZE but different-BYTES file was accepted as already "
        f"flattened — size is standing in for identity: {out}"
    )


def test_a_load_bearing_extension_collision_is_fatal(tmp_path) -> None:
    """win32api.pyd is one of the four modules mcp reaches for.

    A collision here was printed as one line into a verbose CI log and passed.
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    sp.joinpath("win32api.pyd").write_bytes(b"MZ some other extension entirely")

    rc, out = _run_flatten(root)
    assert rc != 0, f"a shadowed win32api.pyd was accepted: {out}"
    assert "win32api.pyd" in out


def test_a_genuine_flatten_still_succeeds_and_stays_idempotent(tmp_path) -> None:
    """The fatal path must not fire on the honest case, including a RE-RUN.

    Measured against the real cp312 wheel: 70 flattened names, zero internal
    collisions — so making collisions fatal cannot spuriously block a
    legitimate release. A second run copies nothing and must still exit 0,
    because every destination is now byte-identical to its source rather than
    merely the same size.
    """
    root = _pywin32_bundle(tmp_path)
    rc, out = _run_flatten(root)
    assert rc == 0, f"the honest case was rejected: {out}"

    rc2, out2 = _run_flatten(root)
    assert rc2 == 0, f"an idempotent re-run was rejected: {out2}"
    assert "0 file(s) copied" in out2, (
        f"a re-run re-copied files instead of recognising identity: {out2}"
    )
    assert _resolve_from(root / "site-packages", "pywintypes") is not None


def test_a_stale_pywintypes_survives_a_wheel_layout_change_and_is_fatal(tmp_path) -> None:
    """The provenance check, exercised on its OWN — no collision to mask it.

    Constructed after a mutation test showed the collision guard fires first in
    every other case, leaving this branch unproven. Here pywin32's payload no
    longer SUPPLIES pywintypes.py (a wheel-layout change), while a stale foreign
    file already owns that name at the root. There is no collision to detect —
    the loop never sees the name — so only a provenance check can catch it. A
    presence-shaped check reports success over a bundle that imports the stale
    file as `pywintypes`.
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    # pywin32 no longer ships the module where this script looks for it...
    (sp / "win32" / "lib" / "pywintypes.py").unlink()
    # ...but something else already occupies the name at the importable level.
    sp.joinpath("pywintypes.py").write_text(
        'raise ImportError("stale file from an earlier, different build")\n'
    , encoding="utf-8")

    rc, out = _run_flatten(root)
    assert rc != 0, (
        "a STALE foreign pywintypes.py was accepted because the payload no "
        f"longer supplied one — presence is not provenance: {out}"
    )
    assert "refusing to ship" in out.lower()


def test_a_stale_companion_dll_not_supplied_by_the_payload_is_fatal(tmp_path) -> None:
    """Same branch for the DLL: it must be one THIS run placed, not any match.

    The original glob asked `pywintypes*.dll` of the whole directory, which a
    leftover from an earlier build satisfies while the real DLL is absent.
    """
    root = _pywin32_bundle(tmp_path)
    sp = root / "site-packages"
    (sp / "pywin32_system32" / "pywintypes312.dll").unlink()
    sp.joinpath("pywintypes312.dll").write_bytes(b"stale leftover, not from this wheel")

    rc, out = _run_flatten(root)
    assert rc != 0, f"a STALE companion DLL satisfied the verification: {out}"
    assert "refusing to ship" in out.lower()


def test_release_flattens_before_it_smoke_tests_the_bundle(release_yaml) -> None:
    """Order is the whole point: the smoke check is what PROVES the fix worked.

    If the flatten ran after it, the gate would test the broken tree; if it ran
    after Inno Setup, the installer would wrap the broken tree.
    """
    steps = release_yaml["jobs"]["build-windows"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]

    flatten_at = next(i for i, r in enumerate(runs) if "flatten_pywin32.py" in r)
    smoke_at = next(i for i, r in enumerate(runs) if "smoke_frozen_bundle.py" in r)
    build_at = next(i for i, r in enumerate(runs) if "flet build windows" in r)
    inno_at = next(
        i for i, s in enumerate(steps) if "Inno Setup" in str(s.get("name", ""))
    )

    assert build_at < flatten_at < smoke_at, (
        f"flatten must sit between the build and the smoke check "
        f"(build={build_at}, flatten={flatten_at}, smoke={smoke_at})"
    )
    assert flatten_at < inno_at, "the installer would wrap an unfixed bundle"


def test_windows_assertion_checks_pywin32_by_module_not_by_directory(
    release_yaml,
) -> None:
    """The bundled-packages assertion must be able to FAIL on the shipped bug.

    A `find -type d -name pywin32` style check passes on the exact tree that
    failed — the directory was there all along. Asserting the flattened module
    and its DLL is what makes this step mean "importable" rather than "present".
    """
    steps = release_yaml["jobs"]["build-windows"]["steps"]
    step = next(
        s for s in steps if s.get("name") == "Assert required packages are bundled"
    )
    run = str(step.get("run", ""))

    assert "pywintypes.py" in run, "the assertion does not mention the module"
    assert 'not -path "*/win32/lib/*"' in run, (
        "the assertion would be satisfied by the UNREACHABLE win32/lib copy, "
        "which is exactly the state that shipped the failure"
    )
    assert "pywintypes*.dll" in run, "the companion DLL is not asserted"


def test_pywintypes_is_not_added_to_the_unconditional_import_list(smoke_text) -> None:
    """Guard the OTHER fix, the one that breaks two green platforms.

    REQUIRED_IMPORTS is flat and is imported UNCONDITIONALLY by the bootstrap,
    so listing a Windows-only module there turns Linux and macOS red — where
    the module is CORRECTLY absent. The script documents this in
    EXEMPT_FROM_BUNDLE_IMPORT_CHECK; this pins it so a later reader cannot
    "fix the missing entry" and take the release backwards.
    """
    body = smoke_text.split("REQUIRED_IMPORTS = [", 1)[1].split("]", 1)[0]
    for mod in ("pywintypes", "win32api", "win32con", "win32job"):
        assert f'"{mod}"' not in body, (
            f"{mod} was added to REQUIRED_IMPORTS — that list is imported "
            "unconditionally, so this turns Linux and macOS red. Windows-only "
            "coverage needs a platform-aware bootstrap, not an entry here."
        )
    assert "pywin32" in smoke_text and "EXEMPT_FROM_BUNDLE_IMPORT_CHECK" in smoke_text
