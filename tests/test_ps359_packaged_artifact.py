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

import shutil
import subprocess
import sys
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

    So: the file names must appear NOWHERE in our tracked source that a BUILD
    STEP READS. `readings/` is excluded because it holds PS-301's historical
    report, which quotes the files it had to hand-stage — that is a record of the
    defect, not an inventory the build reads. This file is excluded for the same
    reason: the names above are a prohibition, and a test that searched itself
    could never pass.

    ⚠️ COMMENT LINES ARE EXCLUDED, AND THE REASON IS THE MECHANISM, NOT
    CONVENIENCE. This test fired on `scripts/ps374_runtime_enable_probe.py:561`,
    which mentions `chrome_crashpad_handler` in a comment explaining that an
    earlier draft of that probe LEAKED 380 such PROCESSES. That is prose about a
    process leak; it is not a file list, nothing reads it, and no drift in
    upstream's runtime tree can make it wrong.

    The defect being prevented is an INVENTORY THAT ROTS: a list our build reads,
    correct the day it is written and silently wrong once upstream changes
    theirs. A comment cannot rot into an artifact that does not run, because no
    build step consults it — and the moment anybody UNCOMMENTS one, it becomes
    code and this test fires. So the ratchet still bites exactly when it matters,
    and `test_a_commented_out_file_list_is_caught_the_moment_it_becomes_code`
    below is the positive control proving that, rather than leaving it asserted.

    Narrowing a prohibition is exactly the move that quietly guts a guard, so the
    narrowing is bounded to lines that cannot execute — never to a file, and
    never to a directory. Excluding `ps374_runtime_enable_probe.py` itself would
    have been the easy repair and the wrong one: it would have blinded this test
    to any real list that file might carry later.
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
        executable = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        )
        for name in UPSTREAM_RUNTIME_FILES:
            if name in executable:
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


def test_a_commented_out_file_list_is_caught_the_moment_it_becomes_code():
    """The positive control for the comment-line narrowing above.

    A prohibition that was narrowed is a prohibition that might have been gutted,
    and the difference is not visible by reading the exclusion — the test above
    passes either way. So this drives the same rule over two synthetic files: one
    where the runtime names sit behind `#`, and the SAME names uncommented.

    The first must be tolerated (a comment is read by no build step and cannot
    rot into an artifact that does not run) and the second must be caught (that
    is a real second inventory of upstream's build output). Without this, the
    narrowing would be an assertion about itself.
    """
    names = ("icudtl.dat", "resources.pak", "chrome_crashpad_handler")

    def offenders_in(text: str) -> set[str]:
        # The same rule the test above applies, exercised directly.
        executable = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("#")
        )
        return {n for n in names if n in executable}

    commented = "\n".join("# stages {} beside the binary".format(n) for n in names)
    assert offenders_in(commented) == set(), (
        "prose naming a runtime file must be tolerated: nothing reads a comment, "
        "so it cannot ship an artifact that does not run"
    )

    as_code = "\n".join('cp "$OUT/{}" "$STAGE/"'.format(n) for n in names)
    assert offenders_in(as_code) == set(names), (
        "the moment a file list becomes executable it MUST be caught — that is "
        "the second inventory this ticket's central constraint forbids"
    )

    # The mixed case, which is how a real regression would actually arrive:
    # someone uncomments one line of an otherwise-commented block.
    mixed = commented + '\ncp "$OUT/icudtl.dat" "$STAGE/"'
    assert offenders_in(mixed) == {"icudtl.dat"}


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


# ─────────────────────────────────────────────────────────────────────────────
# OUR OWN STAGING ROOT IS AN INPUT FROM A PREVIOUS RUN TOO
# ─────────────────────────────────────────────────────────────────────────────
#
# THE DEFECT THESE PIN, in the three facts that produce it:
#
#   1. `package-out/` lives in $GITHUB_WORKSPACE, outside both checkouts, in
#      exactly the place the workflow itself says nothing cleans: "a self-hosted
#      runner does not wipe _work between runs". The one thing that zeroes that
#      root — `ps289_journal.sh salvage` — zeroes `record/` and only `record/`.
#   2. Packaging is gated on `steps.compile.outcome == 'success'`.
#   3. The binary upload is `if: always()` and its path names this directory.
#
# So a dispatch whose COMPILE FAILED runs no packaging, removes nothing, and
# ships the PREVIOUS dispatch's AppImage under this run's artifact name beside a
# manifest saying the tree did not compile. `if-no-files-found: ignore` cannot
# help — files are found.
#
# ⚠️ EVERY OTHER TEST IN THIS FILE READS `package-out/` ONLY AFTER THE RUN.
# That is precisely why the suite was green against a defect reproducible in
# three lines: a directory that is never populated BEFORE a run can never be
# observed to survive one. These plant first.


def test_a_stale_artifact_in_our_staging_directory_cannot_ship_as_this_runs(tmp_path):
    """The reviewer's case (a): a foreign tag ships, and the sidecar disagrees.

    Upstream's release directory is protected by the removal further up this
    script; that protection stops at upstream's edge and did not extend to ours.
    A previous dispatch at a DIFFERENT tag leaves a differently-named file, so
    the copy that survives sits beside this run's output and is uploaded with
    it — while `PROVENANCE.txt` counts only what this run staged, and therefore
    contradicts the directory it describes.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    stage = ws / "package-out" / "patched"
    stage.mkdir(parents=True)
    stale = stage / "ps218-patched-ungoogled-chromium-999.9.9-1-x86_64.AppImage"
    stale.write_text("A BROWSER FROM A PREVIOUS DISPATCH\n", encoding="utf-8")

    result = _run_package(ws)
    assert result.returncode == 0, result.stdout + result.stderr

    staged = sorted(p.name for p in stage.iterdir())
    assert not stale.exists(), (
        "a previous dispatch's artifact survived into this run's upload "
        f"directory: {staged}. Everything under this directory must be THIS "
        "run's by construction, not by inspection."
    )

    # And the sidecar must describe the directory that ships, exactly.
    provenance = (stage / "PROVENANCE.txt").read_text(encoding="utf-8")
    assert "artifact_count=2" in provenance, provenance
    assert len([n for n in staged if n != "PROVENANCE.txt"]) == 2, staged


def test_a_stale_artifact_with_this_runs_name_is_also_removed(tmp_path):
    """The reviewer's case (b): the same tag, where the leftover is INVISIBLE.

    Case (a) is at least legible — a reader who looks sees a version that does
    not belong. When the previous dispatch built the SAME tag, a leftover file
    it produced and this run did not (a `.zsync`, a format upstream dropped)
    carries this run's exact name pattern and nothing tips the reader off. A
    removal is the only thing that catches this; no inspection would.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    stage = ws / "package-out" / "patched"
    stage.mkdir(parents=True)
    invisible = stage / (
        "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64.AppImage.zsync"
    )
    invisible.write_text("LEFTOVER FROM A PREVIOUS DISPATCH AT THE SAME TAG\n",
                         encoding="utf-8")

    result = _run_package(ws)
    assert result.returncode == 0, result.stdout + result.stderr

    assert not invisible.exists(), (
        "a leftover carrying this run's own name pattern survived — the case no "
        "amount of reading the directory would have caught"
    )


def test_a_dispatch_that_never_packages_still_zeroes_the_staging_root(tmp_path):
    """The reviewer's case (c), and the sharp one: a FAILED COMPILE ships a browser.

    This is why the removal cannot live only inside the packaging path. On a
    dispatch whose compile failed, `ps359_package.sh <arm>` is never invoked at
    all — the workflow's `if: steps.compile.outcome == 'success'` sees to that,
    correctly — so any removal inside it is unreachable. The `if: always()`
    upload then ships the previous dispatch's AppImage under this run's name.

    `reset` is the separate, unconditional mode that closes it, and this test
    drives that mode directly: nothing here packages anything.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    for arm in ARMS:
        stage = ws / "package-out" / arm
        stage.mkdir(parents=True)
        (stage / f"ps218-{arm}-ungoogled-chromium-999.9.9-1-x86_64.AppImage").write_text(
            "A BROWSER FROM A PREVIOUS DISPATCH\n", encoding="utf-8"
        )

    result = _run_package(ws, "reset")
    assert result.returncode == 0, result.stdout + result.stderr

    assert not (ws / "package-out").exists(), (
        "the staging ROOT must be gone after a reset. It is removed at the root "
        "rather than per-arm so an arm renamed in a later edit cannot orphan a "
        "directory that still matches the upload's path."
    )

    # Nothing is destroyed unannounced: the reset says what it removed.
    assert "999.9.9" in result.stdout, (
        "the reset must report what it removed — a silent removal is a different "
        f"posture from this workflow's. Got: {result.stdout!r}"
    )


def test_the_reset_is_a_no_op_on_a_cold_runner(tmp_path):
    """The positive control for the two tests above.

    A `reset` that failed, or that reported a removal on an empty runner, would
    make the assertions above unreadable — the first dispatch on a fresh machine
    is the common case and must be quiet and green.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    result = _run_package(ws, "reset")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "absent" in result.stdout, result.stdout
    assert "removed: yes" not in result.stdout, result.stdout


def test_the_reset_runs_before_anything_that_can_fail_in_both_arms(workflow):
    """The wiring, without which the script's `reset` mode is dead code.

    Two properties, and the ORDER is the one that matters: the reset must be
    unconditional (no `if:`), and it must sit ahead of every step whose failure
    would skip it — the prepare, the compile, and the packaging step itself.
    A reset placed after the compile is a reset that does not run on precisely
    the dispatch it exists for.
    """
    for job in ARMS:
        steps = _steps(workflow, job)
        names = [s.get("name", "") for s in steps]

        reset_at = next(
            (i for i, n in enumerate(names) if "Zero the packaging staging root" in n),
            None,
        )
        assert reset_at is not None, (
            f"job {job!r} has no staging-root reset step, so a previous "
            f"dispatch's artifact can ship under this run's name"
        )

        step = steps[reset_at]
        assert "ps359_package.sh reset" in step["run"], step["run"]
        assert "if" not in step, (
            "the reset must be UNCONDITIONAL. Gating it on anything reintroduces "
            f"the defect on whichever dispatch the gate excludes. Found: {step.get('if')!r}"
        )

        for later in ("Apply patches", "Compile", "Package the compiled tree"):
            at = next((i for i, n in enumerate(names) if later in n), None)
            assert at is not None and at > reset_at, (
                f"in job {job!r} the reset must precede {later!r} — a reset that "
                "runs after a step that can fail does not run at all on the "
                "dispatch that needs it"
            )


def test_the_staging_removal_is_also_local_to_the_code_that_relies_on_it():
    """Defence in depth: the packaging path zeroes its own directory too.

    The workflow-level reset is what covers a dispatch that never packages. This
    is what keeps the guarantee LOCAL — a later edit that moves, reorders or
    drops that step cannot silently reintroduce a stale artifact at the point
    where files are staged.
    """
    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert 'rm -rf "$STAGE"\nmkdir -p "$STAGE"' in body, (
        "the staging directory must be removed at the point of creation, not "
        "merely created — `mkdir -p` over a populated directory keeps whatever "
        "was in it"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PORTABILITY — the script must RUN on every platform that executes it
# ─────────────────────────────────────────────────────────────────────────────
#
# THE DEFECT THESE PIN. The first draft of `ps359_package.sh` used four GNU-only
# constructs — `stat -c %s`, `sha256sum`, `find -printf` and `date -Is`. Under
# `set -euo pipefail` the first of those ABORTED THE SCRIPT on macOS:
#
#     stat: illegal option -- c
#
# and five tests in this file went red. They went red rather than vacuously
# green only because they assert on `returncode == 0` and on the script's own
# output — the property `tests/posix_shell.py` was written to protect.
#
# ⛔ THE FIX WAS NOT TO SKIP OFF-LINUX. The script runs for real on a Linux
# self-hosted runner, so skipping elsewhere would be defensible-sounding and
# would restore exactly the blindness `posix_shell.py` exists to end: a script
# under test that never executes makes every assertion about it vacuous.
#
# ⚠️ AND ONE OF THE FOUR WAS WORSE THAN A CRASH. `find -printf` was written with
# `2>/dev/null || true`, so off-GNU it produced NOTHING while the `rm -rf` below
# it still ran. The stale-release INVENTORY silently vanished while the REMOVAL
# kept working — hazard 4's "nothing is destroyed unread" quietly stopped being
# true, invisibly, on the platform nobody was watching. A guard that converts a
# loud failure into a quiet loss of evidence is worse than the failure, so the
# repair COUPLES the two: entries are counted independently of the inventory
# describing them, and a mismatch refuses the removal.
#
# These tests force the non-GNU branches ON A LINUX RUNNER, by constructing a
# PATH — the same technique `test_ps249_host_identity_scrub.py` uses to pin the
# digest ladder in `ps218_host_id.sh`, and for the same reason: a Linux-only run
# structurally cannot see a macOS-only defect, so it must be provoked.

# Enough of a toolchain for the script to run at all, so a restricted PATH can
# hide a specific tool without breaking everything around it.
_SHELL_ESSENTIALS = (
    "bash", "sh", "cat", "head", "tail", "grep", "sed", "awk", "cut", "tr",
    "sort", "wc", "mkdir", "rm", "cp", "mv", "ls", "chmod", "env", "printf",
    "basename", "dirname", "tee", "find", "date", "stat", "touch", "uname",
)

# The GNU-only spellings, and what a BSD/macOS host offers instead.
_BSD_STAT = """#!/bin/bash
# BSD/macOS `stat`, which has no `-c`. Reproduces the macOS failure verbatim.
for a in "$@"; do
  case "$a" in
    -c*) echo "stat: illegal option -- c" >&2
         echo "usage: stat [-FLnq] [-f format | -l | -r | -s | -x] [file ...]" >&2
         exit 1 ;;
  esac
done
if [ "${1:-}" = "-f" ] && [ "${2:-}" = "%z" ]; then shift 2; exec REAL_STAT -c %s "$@"; fi
exec REAL_STAT "$@"
"""

_BSD_DATE = """#!/bin/bash
# BSD `date`, which has no `-I` in any form.
for a in "$@"; do
  case "$a" in -I*) echo "date: illegal option -- I" >&2; exit 1 ;; esac
done
exec REAL_DATE "$@"
"""

_BSD_FIND = """#!/bin/bash
# BSD `find`, which has no `-printf`.
for a in "$@"; do
  case "$a" in -printf) echo "find: -printf: unknown primary or operator" >&2; exit 1 ;; esac
done
exec REAL_FIND "$@"
"""


def _bsd_path(tmp_path: Path, *, digest_tools: tuple[str, ...]) -> str:
    """A PATH shaped like a macOS host: BSD stat/date/find, and no `sha256sum`.

    `digest_tools` names which digest commands are visible, because their
    ABSENCE is what the resolver branches on — a failing stub would still be
    found by `command -v` and would exercise the wrong path entirely.
    """
    binroot = tmp_path / "bsdbin"
    binroot.mkdir(parents=True, exist_ok=True)

    shadowed = {"stat", "date", "find"}
    for tool in _SHELL_ESSENTIALS:
        if tool in shadowed:
            continue
        found = shutil.which(tool)
        if found and not (binroot / tool).exists():
            (binroot / tool).symlink_to(found)

    for tool in digest_tools:
        found = shutil.which(tool)
        if found and not (binroot / tool).exists():
            (binroot / tool).symlink_to(found)

    for name, body, real in (
        ("stat", _BSD_STAT, "REAL_STAT"),
        ("date", _BSD_DATE, "REAL_DATE"),
        ("find", _BSD_FIND, "REAL_FIND"),
    ):
        underlying = shutil.which(name)
        if not underlying:
            pytest.skip(f"no real {name} on this host to wrap")
        stub = binroot / name
        stub.write_text(body.replace(real, underlying), encoding="utf-8")
        stub.chmod(0o755)

    return str(binroot)


def _run_on_bsd_path(ws: Path, bsd_path: str, tree: str = "patched"):
    shell = find_posix_shell()
    return subprocess.run(
        [shell, str(PACKAGE_SCRIPT), tree],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            "PATH": bsd_path,
            "UCPL_DIR": "ucpl",
            "UNGOOGLED_TAG": "152.0.7977.75-1",
            "GITHUB_RUN_ID": "BSDSIM",
        },
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the BSD stubs are POSIX shell scripts wrapping real GNU tools; the "
    "Windows lane already executes the script itself through Git Bash",
)
@pytest.mark.parametrize(
    "digest_tools",
    [
        pytest.param(("shasum",), id="macos-shasum-only"),
        pytest.param(("openssl",), id="openssl-only"),
        pytest.param((), id="no-digest-tool-at-all"),
    ],
)
def test_the_script_completes_on_a_host_without_gnu_coreutils(tmp_path, digest_tools):
    """The blocker itself: no `stat -c`, no `date -I`, no `find -printf`, no `sha256sum`.

    A macOS runner has all four of those absences at once, and the first draft
    died on the first one. Each digest case is parametrised separately because
    the ladder has three rungs and only the one this host happens to have would
    otherwise ever be exercised.

    The no-digest case asserts the script still SUCCEEDS: a digest is provenance
    ABOUT the artifact rather than the artifact, so a host that cannot compute
    one must record that it could not — never abort a completed package over it,
    and never write a value it does not have.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    result = _run_on_bsd_path(ws, _bsd_path(tmp_path, digest_tools=digest_tools))

    assert result.returncode == 0, (
        "the script must run on a host without GNU coreutils.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "stat: illegal option" not in result.stderr
    assert "verdict:          PACKAGED" in result.stdout

    staged = sorted(p.name for p in (ws / "package-out" / "patched").iterdir())
    assert staged == [
        "PROVENANCE.txt",
        "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64.AppImage",
        "ps218-patched-ungoogled-chromium-152.0.7977.75-1-x86_64_linux.tar.xz",
    ], staged

    report = (ws / "record" / "package-patched.txt").read_text(encoding="utf-8")

    # A real byte count, not the `?` the fallbacks emit when every rung fails.
    assert "bytes:      9" in report, report

    if digest_tools:
        assert "unavailable" not in report, (
            f"a host with {digest_tools} must produce a real digest, not a placeholder"
        )
    else:
        assert "unavailable" in report, (
            "a host with no digest tool must SAY it could not compute one, "
            "rather than omitting the field or inventing a value"
        )


@pytest.mark.skipif(sys.platform == "win32", reason="see above")
def test_the_stale_inventory_survives_a_host_without_gnu_find(tmp_path):
    """The subtle one, and the reason a crash would have been the kinder failure.

    `find -printf` is GNU-only and was guarded with `|| true`, so off-GNU the
    stale-release inventory produced NOTHING while the removal below it still
    ran. Hazard 4's promise — that a previous dispatch's output is read before it
    is destroyed — silently stopped holding, on the one platform where nobody was
    looking, with no error anywhere to say so.

    So this asserts the inventory is REAL on a BSD-shaped host, not merely that
    the script survives it. A test that only checked the exit code would have
    passed against the broken version.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    stale_dir = ws / "ucpl" / "build" / "release"
    stale_dir.mkdir(parents=True)
    (stale_dir / "ungoogled-chromium-999.9.9-1-x86_64.AppImage").write_text(
        "BYTES FROM A PREVIOUS DISPATCH\n", encoding="utf-8"
    )
    (stale_dir / "leftover-scratch").mkdir()

    result = _run_on_bsd_path(ws, _bsd_path(tmp_path, digest_tools=("sha256sum",)))
    assert result.returncode == 0, result.stdout + result.stderr

    report = (ws / "record" / "package-patched.txt").read_text(encoding="utf-8")
    assert "ungoogled-chromium-999.9.9-1-x86_64.AppImage" in report, (
        "the stale artifact was destroyed WITHOUT being inventoried on a host "
        "with BSD find — the exact silent-evidence-loss the `|| true` guard caused"
    )
    assert "leftover-scratch" in report, (
        "a leftover directory must be inventoried too; `find -printf %y` "
        "distinguished file from directory and the replacement must as well"
    )
    # And the stale artifact must not have reached this run's output.
    staged = sorted(p.name for p in (ws / "package-out" / "patched").iterdir())
    assert not any("999.9.9" in name for name in staged), staged


def test_the_inventory_and_the_removal_are_coupled(tmp_path):
    """Removal is REFUSED when the directory could not be fully described.

    The repair is not merely "use a portable inventory" — it is that an
    unreadable inventory can no longer coexist with a successful removal. Forced
    by sabotaging `inventory_dir` alone, leaving the independent count honest:
    exactly the shape `find -printf || true` produced on a BSD host.

    Without this, a future portability slip in the inventory would silently
    reintroduce the same evidence loss, and every other test here would pass.
    """
    ws = _fixture_tree(tmp_path, GOOD_STUB)
    stale_dir = ws / "ucpl" / "build" / "release"
    stale_dir.mkdir(parents=True)
    stale = stale_dir / "ungoogled-chromium-999.9.9-1-x86_64.AppImage"
    stale.write_text("BYTES FROM A PREVIOUS DISPATCH\n", encoding="utf-8")

    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert "\ninventory_dir() {" in body, "inventory_dir must exist to be sabotaged"
    sabotaged = body.replace(
        "\ninventory_dir() {",
        "\ninventory_dir() { printf ''; return 0; }\n_dead_inventory_dir() {",
        1,
    )
    saboteur = tmp_path / "sabotaged.sh"
    saboteur.write_text(sabotaged, encoding="utf-8")

    shell = find_posix_shell()
    result = subprocess.run(
        [shell, str(saboteur), "patched"],
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**shell_env(), "UCPL_DIR": "ucpl", "UNGOOGLED_TAG": "t", "GITHUB_RUN_ID": "SAB"},
    )

    assert result.returncode != 0, (
        "an unreadable inventory must REFUSE the removal, not proceed blind"
    )
    assert "NOTHING WAS REMOVED" in result.stdout, result.stdout
    assert stale.is_file(), (
        "the stale artifact was destroyed despite being unreadable — hazard 4's "
        "promise is that nothing is destroyed unread"
    )


def test_no_gnu_only_construct_returns_to_this_script():
    """A ratchet, in the spirit of the repo's existing encoding ratchet.

    Each of these is a real failure that reached the merge gate once. The point
    is not that these four spellings are uniquely dangerous, but that reaching
    for one is the natural thing to do while editing a Linux-targeted script —
    and the consequence is invisible until a non-Linux lane runs it.

    SCOPED TO THE CALL SITES, NOT THE WHOLE FILE. The four resolvers at the top
    are the sanctioned home for these spellings — `file_bytes` NAMES `stat -c`
    as its first rung, and naming it there is the fix rather than the defect. So
    the resolver block is excluded and everything below it is scanned: what this
    forbids is a GNU-only construct in the script's BODY, which is where every
    one of the four failures actually lived.
    """
    body = PACKAGE_SCRIPT.read_text(encoding="utf-8")

    marker = "\nTREE=\"${1:?usage:"
    assert marker in body, "the resolver block's end marker moved; re-anchor this test"
    resolvers, _, main = body.partition(marker)

    # The resolvers must actually be the resolvers, or excluding them would
    # excuse the very thing this test forbids.
    for required in ("now_iso()", "file_bytes()", "file_sha256()", "inventory_dir()"):
        assert required in resolvers, f"{required} must be defined in the resolver block"

    # Comments explain WHY each is forbidden and must stay readable, so only
    # executable lines are scanned.
    code = [
        line for line in main.replace("\\\n", " ").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    forbidden = {
        "stat -c": "GNU-only; BSD is `stat -f %z`. Use file_bytes().",
        "stat -f": "BSD-only. Use file_bytes(), which tries both.",
        "-printf": "GNU-only find. Use inventory_dir().",
        "date -I": "GNU-only. Use now_iso().",
        "sha256sum": "GNU coreutils; absent on macOS. Use file_sha256().",
        "shasum": "BSD-only. Use file_sha256(), which resolves the ladder.",
    }
    for needle, why in forbidden.items():
        offenders = [ln for ln in code if needle in ln]
        assert not offenders, f"{needle}: {why}\noffending lines: {offenders}"
