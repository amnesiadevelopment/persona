"""PS-359: the trial build's artifact must be runnable by somebody who did not build it.

WHAT WENT WRONG, AND WHY IT WAS INVISIBLE
─────────────────────────────────────────
`engine-trial-build.yml` uploaded exactly two paths per arm — the `chrome` and
`chromedriver` executables out of `out/Default`. Those are two files out of a
RUNTIME tree. Launched anywhere else the binary dies immediately with

    ERROR:base/i18n/icu_util.cc:232] Invalid file descriptor to ICU data received.
    rc=133

which reaches our harness as *"persona's chromium exited before opening a debug
port"* — a message that reads like a broken compile and is not one. PS-301 hit
exactly this and had to hand-stage the binary into a foreign, version-skewed
Chrome-for-Testing resource tree to measure anything at all.

The defect's whole character is that a GREEN BUILD PRODUCED AN OUTPUT THAT DOES
NOT RUN. That is what these tests are aimed at, and it is why several of them
assert about the *absence* of something rather than the presence of a feature.

THE CENTRAL CONSTRAINT: WE DO NOT OWN A FILE LIST
─────────────────────────────────────────────────
The obvious repair — widen the `path:` block with the runtime files — is
forbidden, and `test_our_repository_does_not_own_a_list_of_upstreams_runtime_files`
is what forbids it mechanically rather than in prose. A list of upstream's
runtime files living in our repo is a SECOND INVENTORY of somebody else's build
output: right the day it is written, and silently wrong the first time upstream
changes theirs — with the same ICU-shaped death months later as the symptom.

WHAT THESE TESTS ARE AND ARE NOT
────────────────────────────────
The script tests DRIVE `ps359_package.sh` for real, with a stub standing in for
upstream's packager, because the properties worth pinning are behavioural: does
it refuse an exit code that lies, does it destroy stale output, does it name the
arm. Nothing here compiles Chromium or runs a packager, and it cannot: that
happens on hardware this container does not have. That the artifact LAUNCHES is
established by launching it (this ticket's AC2) and is not a claim any test in
this file makes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.posix_shell import find_posix_shell, shell_env

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "ps359_package.sh"

# The two arms, and the artifact-name prefix each one's output must carry.
ARMS = ("unmodified", "patched")


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _step_named(workflow: dict, job: str, needle: str) -> dict:
    for step in _steps(workflow, job):
        if needle in step.get("name", ""):
            return step
    raise AssertionError(f"no step matching {needle!r} in job {job!r}")


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — upstream's packager is invoked, and NO file list is ours
# ─────────────────────────────────────────────────────────────────────────────

# Upstream's runtime file list, reproduced here ONLY as a set of names to search
# FOR and prove ABSENT. This is the one place in the repository where these
# strings may appear, and they appear as a prohibition rather than as an
# inventory: nothing reads this tuple to decide what to copy, and if upstream
# changes its list, this test going stale costs nothing, whereas a real list
# going stale ships an artifact that does not run.
UPSTREAM_RUNTIME_FILES = (
    "icudtl.dat",
    "resources.pak",
    "chrome_100_percent",
    "chrome_200_percent",
    "v8_context_snapshot",
    "libEGL.so",
    "libGLESv2",
    "libvk_swiftshader",
    "libvulkan.so",
    "vk_swiftshader_icd",
    "chrome_crashpad_handler",
    "libqt5_shim",
    "libqt6_shim",
    "product_logo_48",
)


def test_our_repository_does_not_own_a_list_of_upstreams_runtime_files():
    """THE central constraint of PS-359, asserted mechanically.

    Copying upstream's 20-file runtime list into our repo would work on the day
    it was written and rot invisibly afterwards: upstream adds or renames a file,
    our list does not, and the artifact silently stops being self-contained. The
    symptom is an ICU error on somebody else's machine months later, which reads
    like a broken compile.

    So: the file names must appear NOWHERE in our tracked source. `readings/` is
    excluded because it holds PS-301's historical report, which quotes the files
    it had to hand-stage — that is a record of the defect, not an inventory the
    build reads. This file is excluded for the same reason: the names above are
    a prohibition, and a test that searched itself could never pass.
    """
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split()

    offenders: dict[str, list[str]] = {}
    for rel in tracked:
        if rel.startswith("readings/") or rel.endswith(Path(__file__).name):
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: not a place a file list is authored
        for name in UPSTREAM_RUNTIME_FILES:
            if name in text:
                offenders.setdefault(name, []).append(rel)

    assert not offenders, (
        "PS-359: our repository must own NO list of upstream's runtime files. "
        f"Found: {offenders}. Widening the workflow's `path:` block (or a script) "
        "with these names creates a second inventory of somebody else's build "
        "output, which rots silently. Call upstream's `package/docker-package.sh` "
        "instead — its list travels with the tag we build."
    )


@pytest.mark.parametrize("job", ARMS)
def test_each_arm_packages_through_upstreams_containerised_driver(workflow, job):
    """`package/docker-package.sh`, never `scripts/package.sh` directly.

    The inner script pipes its tarball through `pv` and builds the AppImage with
    `appimagetool`. NEITHER is in `chromium-builder:trixie-slim`; both live only
    in upstream's separate packager image, which `docker-package.sh` builds. A
    direct call to `package.sh` looks simpler and dies at `pv` AFTER a multi-hour
    compile — the most expensive possible moment to discover a missing tool.
    """
    step = _step_named(workflow, job, "Package the compiled tree")
    assert step.get("id") == "package", (
        "the packaging step needs `id: package` so its outcome can be reported "
        "as a distinct result"
    )
    assert f"ps359_package.sh {job}" in step["run"], (
        f"the {job} arm must package its own tree, naming the arm explicitly"
    )

    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert "package/docker-package.sh" in body, (
        "packaging must go through upstream's CONTAINERISED driver"
    )
    # The dangerous "simplification" this test exists to prevent: reaching past
    # the driver to the inner script, which cannot run in the builder image.
    assert 'DRIVER="${UCPL_ABS}/package/docker-package.sh"' in body, (
        "the driver path must be `package/docker-package.sh`. Calling "
        "`scripts/package.sh` directly fails at `pv` after the compile."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Packaging must not run on a tree that did not compile, and must not mask it
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("job", ARMS)
def test_packaging_only_happens_after_a_compile_that_succeeded(workflow, job):
    """A tree that did not compile has nothing to package.

    Gating on the compile's outcome also keeps the results ordered: packaging
    can never produce an artifact attributed to a build that did not happen.
    """
    step = _step_named(workflow, job, "Package the compiled tree")
    assert step.get("if") == "steps.compile.outcome == 'success'", (
        "packaging must be gated on a SUCCESSFUL compile, not on `always()` and "
        f"not on the job status. Found: {step.get('if')!r}"
    )


@pytest.mark.parametrize("job", ARMS)
def test_a_packaging_failure_cannot_swallow_the_compile_result(workflow, job):
    """`continue-on-error` on packaging, so the evidence still ships.

    Same posture as the compile steps: the flag is NOT there to hide failure but
    so the diagnostics are collected and uploaded, with the failure re-raised at
    the end. Without it a packaging failure would abort the job before the
    manifest and record uploads — losing the compile result, which is the number
    PS-218 exists to produce.
    """
    step = _step_named(workflow, job, "Package the compiled tree")
    assert step.get("continue-on-error") is True, (
        "packaging must not abort the job before the record is uploaded"
    )


@pytest.mark.parametrize("job", ARMS)
def test_the_record_uploads_still_run_unconditionally(workflow, job):
    """A failed build is a result this workflow pays for; its evidence must ship.

    PS-359 must not have narrowed these. Pinned because the packaging change
    touches the steps immediately around them.
    """
    for needle in ("Upload the record", "Write the manifest"):
        step = _step_named(workflow, job, needle)
        assert step.get("if") == "always()", (
            f"{needle!r} in job {job!r} must stay `if: always()` — a result whose "
            f"evidence was thrown away is not a result. Found: {step.get('if')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — a packaging failure is reported AS a packaging failure
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("job", ARMS)
def test_a_packaging_failure_is_a_third_verdict_not_a_relabelled_compile_failure(
    workflow, job
):
    """"Compiled fine, packaging failed" is a THIRD outcome.

    This workflow's entire design is that distinguishable outcomes stay
    distinguishable — it refuses to let "it compiled" and "it was never compiled"
    collapse into each other. Packaging adds a third state to keep apart, and the
    stakes are concrete: building upstream's packager image reaches the network
    unauthenticated, so a rate limit lands here. Reporting that as a build
    failure would send a reader looking for a defect in the patch layer.
    """
    step = _step_named(workflow, job, "Re-raise a failure")
    condition = step["if"]
    assert "steps.package.outcome == 'failure'" in condition, (
        "a packaging failure must make the job's verdict honest, like a prepare "
        f"or compile failure does. Found: {condition!r}"
    )

    run = step["run"]
    # The message must SAY the tree compiled. A reader who only sees a red job
    # and a generic message will attribute it to the build.
    assert "PS-359" in run, "the packaging branch must be attributable to its ticket"
    assert "COMPILED" in run, (
        "the packaging-failure message must state that the tree DID compile — "
        "that is the whole distinction being drawn"
    )
    # And it must point at the likely real cause rather than leaving a reader to
    # guess, because the likely cause is not in this repository at all.
    assert "UNAUTHENTICATED" in run, (
        "the message must name the unauthenticated network reach in upstream's "
        "packager image build, which is where a rate limit would surface"
    )


def test_the_manifest_reports_packaging_as_its_own_row():
    """The manifest's one job is never to let two results collapse into one."""
    manifest = (REPO_ROOT / "scripts" / "ps218_manifest.sh").read_text(encoding="utf-8")
    assert "PACKAGE_RESULT" in manifest, (
        "the manifest must read the packaging outcome"
    )
    assert "Artifact PACKAGED" in manifest, (
        "the manifest must state packaging as its own row, beside APPLIED and COMPILED"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — the two arms' artifacts are distinguishable once BOTH are downloaded
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("job", ARMS)
def test_the_uploaded_artifact_is_the_packaged_tree(workflow, job):
    """The binary upload must ship the package, not two files out of a runtime tree.

    This is the defect itself: `out/Default/chrome` alone cannot be launched by
    anyone who did not build it.
    """
    step = _step_named(workflow, job, "Upload the binary")
    path = step["with"]["path"]
    assert f"package-out/{job}/" in path, (
        f"the {job} arm must upload its packaged output. Found: {path!r}"
    )
    assert "out/Default/chrome\n" not in path, (
        "uploading the bare `chrome` executable is the defect PS-359 closes: it "
        "ships two files out of a runtime tree and dies with an ICU error on any "
        "machine that did not build it."
    )


def test_the_two_arms_artifacts_can_be_told_apart_by_a_reader_holding_both():
    """Upstream names its output from the tag alone, and both arms build one tag.

    So both emit an identically-named file. The GITHUB ARTIFACT names differ,
    which is not enough: a reader who downloads both ends up with two files with
    the same name in two directories and no way to tell the subject from the
    control. That is precisely the confusion PS-244's artifact-name provenance
    rules exist to prevent.

    The remedy is a prefix applied in OUR staging layer. Upstream's script and
    upstream's own name are untouched — renaming upstream's output would drift
    toward the publication name, which belongs to RELEASING.md and PS-319.
    """
    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert 'dest="${STAGE}/ps218-${TREE}-${base}"' in body, (
        "each staged artifact must carry its arm in the filename, so a reader "
        "holding both downloads can tell the patched tree from the control"
    )


def test_packaging_does_not_drift_into_publication():
    """⛔ Out of scope, and worth pinning because the drift is one rename away.

    "Make it runnable" and "make it publishable" are different tickets. PS-319
    owns publication and the `personium-` name; conflating them is how this
    ticket would grow into that one.
    """
    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    workflow_text = WORKFLOW.read_text(encoding="utf-8")

    # The staged name must never BE the release name. `personium-` appears in
    # both files only inside prose forbidding it, so assert on the shape a real
    # rename would take rather than on the bare word.
    for label, text in (("ps359_package.sh", body), ("the workflow", workflow_text)):
        assert 'personium-${' not in text and "personium-$(" not in text, (
            f"{label} must not construct a `personium-` name: that is the "
            "publication name, owned by RELEASING.md and PS-319."
        )

    for forbidden in ("gh release create", "softprops/action-gh-release", "actions/create-release"):
        assert forbidden not in workflow_text, (
            f"PS-359 publishes nothing — found {forbidden!r}. The workflow's "
            "no-release stance and read-only token are deliberate."
        )


# ─────────────────────────────────────────────────────────────────────────────
# The script's own behaviour, driven for real
# ─────────────────────────────────────────────────────────────────────────────


def _fixture_tree(tmp_path: Path, stub: str) -> Path:
    """A workspace shaped like the runner's, with a stub for upstream's packager.

    The stub stands in for `package/docker-package.sh`, which needs docker and a
    compiled Chromium tree. What is under test is OUR wrapper's behaviour around
    that call, and the stub is what makes each of upstream's possible behaviours
    reachable — including the two that matter most and are hardest to provoke
    for real: exiting 0 having produced nothing.
    """
    ws = tmp_path / "ws"
    (ws / "ucpl" / "package").mkdir(parents=True)
    (ws / "ucpl" / "build").mkdir(parents=True)
    # Populated, matching the workflow's `submodules: recursive` checkout.
    (ws / "ucpl" / "ungoogled-chromium").mkdir(parents=True)
    (ws / "ucpl" / "ungoogled-chromium" / "chromium_version.txt").write_text(
        "152\n", encoding="utf-8"
    )

    driver = ws / "ucpl" / "package" / "docker-package.sh"
    driver.write_text(stub, encoding="utf-8")
    driver.chmod(0o755)
    return ws


def _run_package(ws: Path, tree: str = "patched") -> subprocess.CompletedProcess:
    shell = find_posix_shell()
    return subprocess.run(
        [shell, str(PACKAGE_SCRIPT), tree],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **shell_env(),
            "UCPL_DIR": "ucpl",
            "UNGOOGLED_TAG": "152.0.7977.75-1",
            "GITHUB_RUN_ID": "TESTRUN",
        },
    )


GOOD_STUB = """#!/bin/bash
set -euo pipefail
mkdir -p build/release
printf 'appimage\\n' > build/release/ungoogled-chromium-152.0.7977.75-1-x86_64.AppImage
printf 'tarball\\n' > build/release/ungoogled-chromium-152.0.7977.75-1-x86_64_linux.tar.xz
"""


def test_a_successful_package_stages_every_artifact_under_its_arms_prefix(tmp_path):
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    result = _run_package(ws)

    assert result.returncode == 0, result.stdout + result.stderr
    staged = sorted(p.name for p in (ws / "package-out" / "patched").iterdir())
    assert staged == [
        "PROVENANCE.txt",
        "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64.AppImage",
        "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64_linux.tar.xz",
    ], staged

    # The report must not claim more than it established. "A file was produced"
    # is precisely the claim this ticket refuses to accept as evidence of a
    # runnable artifact, so the script says so itself.
    report = (ws / "record" / "package-patched.txt").read_text(encoding="utf-8")
    assert "DOES NOT" in report and "LAUNCHES" in report, (
        "the report must state that producing a file is not the same claim as "
        "producing a runnable one"
    )


def test_a_stale_release_directory_cannot_be_mistaken_for_this_runs_output(tmp_path):
    """HAZARD 4, and the reason it is removal rather than a freshness check.

    `build/` survives between dispatches by design (PS-307's `clean: false`), and
    upstream's own cleanup leaves its release directory behind. So a previous
    dispatch's artifact IS sitting there when this runs. Verifying by timestamp
    would be the weaker answer; destroying the directory makes everything present
    afterwards THIS run's by construction rather than by inspection.

    Same posture PS-244 established for the borrowed control and PS-307 for the
    tree: an input from a previous run is VERIFIED, not trusted.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    stale_dir = ws / "ucpl" / "build" / "release"
    stale_dir.mkdir(parents=True)
    stale = stale_dir / "ungoogled-chromium-999.9.9-1-x86_64.AppImage"
    stale.write_text("BYTES FROM A PREVIOUS DISPATCH\n", encoding="utf-8")

    result = _run_package(ws)
    assert result.returncode == 0, result.stdout + result.stderr

    staged = sorted(p.name for p in (ws / "package-out" / "patched").iterdir())
    assert not any("999.9.9" in name for name in staged), (
        f"a previous dispatch's artifact reached this run's output: {staged}"
    )

    # Removed, but never silently: the inventory is recorded first, so nothing
    # is destroyed unread.
    report = (ws / "record" / "package-patched.txt").read_text(encoding="utf-8")
    assert "ungoogled-chromium-999.9.9-1-x86_64.AppImage" in report, (
        "the stale artifact must be INVENTORIED before removal — destroying "
        "evidence unread is the opposite of this workflow's posture"
    )


def test_an_exit_code_of_zero_with_no_output_is_refused(tmp_path):
    """Trust the filesystem over the exit code.

    ps218_build.sh already applies this rule to the chrome binary ("ninja can
    exit 0 having produced nothing usable"). It matters more here: the whole
    defect being closed is a green step whose output does not work, so a
    packaging step that reports success and stages nothing must be a failure and
    not a silently empty artifact.
    """
    ws = _fixture_tree(tmp_path, "#!/bin/bash\necho 'packaging complete!'\nexit 0\n")
    result = _run_package(ws)

    assert result.returncode != 0, (
        "a packager that exited 0 and produced no output directory must be "
        "refused, not believed"
    )
    assert "Trust the filesystem over the exit code" in result.stdout


def test_an_empty_release_directory_is_refused(tmp_path):
    """The near-miss of the case above: the directory exists and holds nothing.

    Distinguished from it deliberately — a wrapper that only checked for the
    directory would ship an artifact containing nothing but a provenance file,
    which reads as a successful package.
    """
    ws = _fixture_tree(tmp_path, "#!/bin/bash\nmkdir -p build/release\nexit 0\n")
    result = _run_package(ws)

    assert result.returncode != 0
    assert "staged nothing" in result.stdout


def test_a_packaging_failure_says_it_is_not_a_build_failure(tmp_path):
    """The message a human reads must not send them looking for a compile defect."""
    ws = _fixture_tree(
        tmp_path,
        "#!/bin/bash\necho 'API rate limit exceeded' >&2\nexit 1\n",
    )
    result = _run_package(ws)

    assert result.returncode != 0
    assert "NOT A COMPILE FAILURE" in result.stdout.upper()
    # And it must name the unauthenticated network reach, which is the likely
    # cause and is not in this repository.
    assert "UNAUTHENTICATED" in result.stdout


def test_a_missing_upstream_driver_is_reported_as_a_fact_about_the_tag(tmp_path):
    """Upstream moving its entry point is not a packaging crash.

    And the recovery instruction matters: the tempting response to "upstream's
    packager is gone" is to hand-roll the file list, which is the one thing this
    ticket forbids.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    (ws / "ucpl" / "package" / "docker-package.sh").unlink()

    result = _run_package(ws)
    assert result.returncode != 0
    assert "statement about the TAG" in result.stdout
    # Matched with whitespace collapsed: the message is hard-wrapped, so this
    # phrase spans a newline in the real output.
    assert "hand-rolling a file list" in " ".join(result.stdout.split())


def test_upstreams_conditional_submodule_init_is_measured_not_assumed(tmp_path):
    """HAZARD 5.

    `docker-package.sh` runs `git submodule update --init --recursive` when it
    finds the submodule directory empty. Our checkout uses `submodules:
    recursive`, so it should be a no-op — but "should" is not a measurement, and
    a submodule update reaching the network mid-package is worth seeing.

    Both branches are asserted, because a report that can only ever say "no-op"
    would say it whether or not it was true.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    assert _run_package(ws).returncode == 0
    report = (ws / "record" / "package-patched.txt").read_text(encoding="utf-8")
    assert "POPULATED" in report and "NO-OP" in report

    # The negative control: with the submodule directory emptied, the report must
    # say the opposite. Without this, the assertion above could not have failed.
    ws2 = _fixture_tree(tmp_path / "second", GOOD_STUB)
    (ws2 / "ucpl" / "ungoogled-chromium" / "chromium_version.txt").unlink()
    assert _run_package(ws2).returncode == 0
    report2 = (ws2 / "record" / "package-patched.txt").read_text(encoding="utf-8")
    assert "EMPTY" in report2 and "WILL run" in report2


@pytest.mark.parametrize("tree", ARMS)
def test_each_arm_stages_into_its_own_directory(tmp_path, tree):
    """The two arms must not be able to overwrite each other's output.

    Both run on the same self-hosted runner against the same preserved workspace,
    so a shared staging directory would let the patched arm's artifact land under
    the control's name — the attribution failure this workflow exists to prevent,
    arriving through the artifact layer.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    assert _run_package(ws, tree).returncode == 0

    assert (ws / "package-out" / tree).is_dir()
    other = "patched" if tree == "unmodified" else "unmodified"
    assert not (ws / "package-out" / other).exists(), (
        f"packaging the {tree} arm must not touch the {other} arm's output"
    )
