"""PS-306: the trial build survives an intermittent `update_rust.py` segfault.

WHAT THIS IS ABOUT
──────────────────
Three consecutive PS-299 dispatches of Chromium 152.0.7977.75-1 died in the
`prepare` phase, each after ~12 minutes, and the compile step never ran in any
of them. The infra operator diagnosed it live on the runner:
`tools/rust/update_rust.py` SEGFAULTS INTERMITTENTLY UNDER BUILD LOAD. In
isolation it is fine — 14 runs in the same container image, every one exit 0 —
and one dispatch did get past the same point. The script is not broken; the
invocation does not tolerate a transient crash.

The call is upstream's, inside `ucpl/scripts/shared.sh`, and `setup_toolchain`
is reached from inside the container, so it cannot be wrapped from outside. And
`ucpl/` is checked out fresh on every run, so it cannot be committed once. The
repair is therefore a per-run rewrite of the checked-out tree —
`scripts/ps306_harden_toolchain.sh`, in the shape `ps218_stage_patches.sh`
established.

WHY MOST OF THIS FILE EXERCISES THE SCRIPT RATHER THAN READING IT
─────────────────────────────────────────────────────────────────
A retry loop is exactly the kind of construct that reads correctly and behaves
wrongly. Upstream's scripts are `set -euo pipefail`, so the single likeliest
defect is a retry that never retries — the first failed attempt aborts the
script and the loop's second iteration is unreachable. That defect is invisible
to any static reading of the YAML or of the injected text; it only shows up when
a genuinely failing command runs inside a genuinely `set -e` shell. So the tests
below BUILD the patched `shared.sh`, source it, and run the function against
fake `update_rust.py` scripts that fail on purpose.

The three behaviours that must hold together, and each of which is separately
falsifiable here:

1. A transient failure is RETRIED (and the exit code of each failed attempt
   reaches the log — a silent retry converts an intermittent failure into an
   invisible chronic one).
2. A toolchain that never materialises FAILS the run — a retry loop that
   exhausts its attempts and lets the build continue toward a confusing
   downstream error is worse than the failure it replaces.
3. The verification rests on the ARTIFACT ON DISK, not on the exit code, since
   the ticket states the two can disagree. `test_exit_zero_without_the_artifact_still_fails`
   is the one that pins this: a naive implementation trusting `$?` passes every
   other test in this file and fails that one.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
HARDEN_SH = REPO_ROOT / "scripts" / "ps306_harden_toolchain.sh"

# `setup_toolchain()` at tag 152.0.7977.75-1, reproduced verbatim from
# ungoogled-chromium-portablelinux. This is the tree the repair is keyed to, and
# writing it out here rather than fetching it means these tests state exactly
# which upstream shape they were verified against.
UPSTREAM_SHARED = """\
#!/bin/bash
set -euo pipefail

# shared build functions used by local and CI scripts

fix_tool_downloading() {
    echo "fix_tool_downloading"
}

setup_toolchain() {
    # Chromium currently has no non-x86 llvm/rust builds on
    # Linux, so we have to build it ourselves.
    if [ "$_host_arch" = x64 ]; then
        "${_src_dir}/tools/rust/update_rust.py"
        "${_src_dir}/tools/clang/scripts/update.py"
    else
        "${_src_dir}/tools/clang/scripts/build.py" \\
            --without-fuchsia --without-android --disable-asserts

        export CARGO_HOME="${_src_dir}/third_party/rust-src/cargo-home"
        "${_src_dir}/tools/rust/build_rust.py" --skip-test
        "${_src_dir}/tools/rust/build_bindgen.py"
    fi

    mkdir -p "${_src_dir}/third_party/node/linux/node-linux-x64/bin"
}

gn_gen() {
    echo "gn_gen"
}
"""

# The scripts under test are bash and run on a `[self-hosted, persona-build]`
# Linux runner. On windows-latest `bash` is the WSL launcher with no distro
# installed: it would exit non-zero without executing a line, making every
# "must fail" assertion below pass for entirely the wrong reason — the
# dead-probe shape this project has been bitten by. Skip honestly instead.
requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32",
    reason="bash scripts; on windows-latest `bash` is the WSL launcher with no distro installed",
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# THE WORKFLOW WIRING — the repair has to be reachable, in BOTH jobs, before
# the prepare phase.
# ─────────────────────────────────────────────────────────────────────────────

def _steps(workflow: dict, job: str) -> list:
    return workflow["jobs"][job]["steps"]


def _index_of(steps: list, needle: str) -> int:
    for i, step in enumerate(steps):
        if needle in (step.get("run") or ""):
            return i
    raise AssertionError(f"no step runs {needle!r}; steps: {[s.get('name') for s in steps]}")


@pytest.mark.parametrize("job,tree", [("unmodified", "unmodified"), ("patched", "patched")])
def test_both_jobs_harden_the_toolchain_before_the_prepare_phase(workflow, job, tree):
    """Both trees, or the comparison between them is worthless.

    Both jobs run the same prepare phase and both are exposed to the same flake.
    Hardening only one would leave the control and the subject running different
    code — the control would keep failing on the segfault while the patched tree
    sailed past it, and a difference between the two would then measure the
    harness rather than the patches. That is the exact thing this workflow
    exists to protect, so it is asserted for each job rather than for one.
    """
    steps = _steps(workflow, job)
    harden = _index_of(steps, "ps306_harden_toolchain.sh")
    prepare = _index_of(steps, "ps218_build.sh prepare")

    assert harden < prepare, (
        f"in job {job!r} the PS-306 repair must run BEFORE the prepare phase: "
        "setup_toolchain() is called from inside the container, so a repair "
        "applied afterwards would never be read."
    )

    # It must also be after the checkout, or it patches a tree that is about to
    # be wiped. `actions/checkout` defaults `clean` to true.
    checkout = next(
        i for i, s in enumerate(steps)
        if "checkout" in (s.get("uses") or "") and (s.get("with") or {}).get("path") == "ucpl"
    )
    assert checkout < harden, (
        f"in job {job!r} the repair must be applied AFTER the ucpl checkout: the "
        "tree is re-cloned per run, so anything applied before it is discarded."
    )

    assert f"ps306_harden_toolchain.sh {tree}" in steps[harden]["run"], (
        f"job {job!r} must label its record with its own tree name"
    )


@pytest.mark.parametrize("job", ["unmodified", "patched"])
def test_the_repair_step_is_not_continue_on_error(workflow, job):
    """A repair that silently no-ops would produce an unattributable build.

    `continue-on-error` is set on the prepare and compile steps deliberately, so
    that diagnostics are still collected when they fail. It must NOT be set
    here: this step only rewrites a shell function, and if it cannot, the tree
    is not the shape the repair was verified against. Continuing from there
    means building something nobody has checked.
    """
    steps = _steps(workflow, job)
    step = steps[_index_of(steps, "ps306_harden_toolchain.sh")]
    assert not step.get("continue-on-error"), (
        "the PS-306 repair must stop the job when it cannot apply — a build that "
        "proceeded past a failed repair would carry an unverified modification."
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE REWRITE — keyed to the CALL, never to a line number.
# ─────────────────────────────────────────────────────────────────────────────

def _make_ucpl(tmp_path: Path, shared: str = UPSTREAM_SHARED) -> Path:
    ucpl = tmp_path / "ucpl"
    (ucpl / "scripts").mkdir(parents=True, exist_ok=True)
    (ucpl / "scripts" / "shared.sh").write_text(shared, encoding="utf-8")
    return ucpl


def _run_harden(tmp_path: Path, tree: str = "unmodified"):
    proc = subprocess.run(
        ["bash", str(HARDEN_SH), tree],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "UCPL_DIR": "ucpl"},
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


@requires_posix_shell
def test_the_upstream_call_is_replaced_and_the_result_parses(tmp_path):
    """The happy path: the bare call goes, the retry arrives, bash still parses it."""
    _make_ucpl(tmp_path)
    rc, out = _run_harden(tmp_path)
    assert rc == 0, out

    patched = (tmp_path / "ucpl" / "scripts" / "shared.sh").read_text(encoding="utf-8")
    assert "persona_update_rust_with_retry" in patched
    assert '"${_src_dir}/tools/rust/update_rust.py"\n' not in patched.replace(
        'local _rust_script="${_src_dir}/tools/rust/update_rust.py"\n', ""
    ), "the bare upstream call must be gone; only the function's own reference may remain"

    # It must still be a valid shell script. A syntax error here would surface
    # ~12 minutes later as an incomprehensible failure inside the container.
    assert subprocess.run(["bash", "-n", str(tmp_path / "ucpl" / "scripts" / "shared.sh")]).returncode == 0

    # And the record has to exist, like every other step in this workflow.
    assert (tmp_path / "record" / "ps306-toolchain-retry-unmodified.txt").is_file()


@requires_posix_shell
def test_a_tree_without_the_call_is_REFUSED_rather_than_patched_blindly(tmp_path):
    """The anchor is the CALL. A different tag must fail HERE, loudly.

    A repair keyed to a line number silently targets the wrong line on a
    different tag, producing a build that ran with an unknown modification and
    reported a number for it. So a tree whose `setup_toolchain()` does not carry
    the call this repair was verified against must stop the run.
    """
    moved = UPSTREAM_SHARED.replace(
        '        "${_src_dir}/tools/rust/update_rust.py"\n',
        '        "${_src_dir}/tools/rust/update_rust_v2.py" --new-flag\n',
    )
    _make_ucpl(tmp_path, moved)
    rc, out = _run_harden(tmp_path)

    assert rc != 0, f"an unrecognised upstream shape must STOP the run:\n{out}"
    assert "::error::" in out, "the refusal must be loud on the workflow's own error channel"
    assert "found 0" in out

    # And it must not have written anything.
    assert "persona_update_rust_with_retry" not in (
        tmp_path / "ucpl" / "scripts" / "shared.sh"
    ).read_text(encoding="utf-8"), "a refused repair must leave the tree untouched"


@requires_posix_shell
def test_an_ambiguous_anchor_is_refused(tmp_path):
    """Two matching calls means the anchor no longer identifies one site."""
    ambiguous = UPSTREAM_SHARED.replace(
        '        "${_src_dir}/tools/rust/update_rust.py"\n',
        '        "${_src_dir}/tools/rust/update_rust.py"\n'
        '        "${_src_dir}/tools/rust/update_rust.py"\n',
    )
    _make_ucpl(tmp_path, ambiguous)
    rc, out = _run_harden(tmp_path)
    assert rc != 0, f"an ambiguous anchor must STOP the run:\n{out}"
    assert "found 2" in out


@requires_posix_shell
def test_applying_twice_is_a_no_op_rather_than_a_nested_retry(tmp_path):
    """Idempotent. A second application would nest the retry inside itself."""
    _make_ucpl(tmp_path)
    assert _run_harden(tmp_path)[0] == 0
    first = (tmp_path / "ucpl" / "scripts" / "shared.sh").read_text(encoding="utf-8")

    rc, out = _run_harden(tmp_path)
    assert rc == 0, out
    assert "already carries the retry" in out
    assert (tmp_path / "ucpl" / "scripts" / "shared.sh").read_text(encoding="utf-8") == first


# ─────────────────────────────────────────────────────────────────────────────
# THE BEHAVIOUR — the retry is EXECUTED, in a `set -euo pipefail` shell, against
# a script that really fails. This is the half a static reading cannot reach.
# ─────────────────────────────────────────────────────────────────────────────

FAKE_SEGFAULT_THEN_OK = """\
#!/bin/bash
n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNTER"
if [ "$n" -lt {succeed_on} ]; then kill -SEGV $$; fi
mkdir -p "$(dirname "$VERSION_FILE")"
echo "rustc 1.90.0-dev" > "$VERSION_FILE"
"""

FAKE_ALWAYS_SEGFAULTS = """\
#!/bin/bash
n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNTER"
kill -SEGV $$
"""

FAKE_EXITS_ZERO_PRODUCING_NOTHING = """\
#!/bin/bash
n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNTER"
exit 0
"""


def _exercise(tmp_path: Path, fake: str):
    """Patch a tree, plant a fake update_rust.py, and RUN the injected function.

    The harness is `set -euo pipefail` on purpose — upstream's entrypoint is
    `set -euxo pipefail` and `shared.sh` is `set -euo pipefail`. A retry that
    aborts the script on its first failed attempt is the single likeliest defect
    here and it is only reachable in a shell configured this way.
    """
    _make_ucpl(tmp_path)
    rc, out = _run_harden(tmp_path)
    assert rc == 0, out

    src = tmp_path / "src"
    (src / "tools" / "rust").mkdir(parents=True, exist_ok=True)
    rust = src / "tools" / "rust" / "update_rust.py"
    rust.write_text(fake, encoding="utf-8")
    rust.chmod(0o755)

    version_file = src / "third_party" / "rust-toolchain" / "VERSION"

    harness = tmp_path / "harness.sh"
    harness.write_text(textwrap.dedent(f"""\
        set -euo pipefail
        . ucpl/scripts/shared.sh
        _src_dir="{src}"
        persona_update_rust_with_retry
    """), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(harness)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "COUNTER": str(tmp_path / "counter"),
            "VERSION_FILE": str(version_file),
            # The real pause is 15s (the operator's request, asserted separately
            # against the injected source). Zeroed here so three attempts do not
            # cost 30s of wall-clock in the merge gate.
            "PS306_RETRY_PAUSE_SECONDS": "0",
        },
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    attempts = int((tmp_path / "counter").read_text(encoding="utf-8").strip() or 0)
    return proc.returncode, proc.stdout + proc.stderr, attempts


@requires_posix_shell
def test_a_transient_segfault_is_retried_and_the_build_survives(tmp_path):
    """THE POINT OF THE TICKET.

    A first invocation that segfaults must not fail the job. This is executed,
    not read: upstream's `set -e` would abort on the first failed attempt if the
    retry were written as a bare command, and no static check can see that.
    """
    rc, out, attempts = _exercise(tmp_path, FAKE_SEGFAULT_THEN_OK.format(succeed_on=2))

    assert rc == 0, f"a transient segfault must be survived, not fatal:\n{out}"
    assert attempts == 2, (
        f"expected the second attempt to run and succeed; the script was invoked "
        f"{attempts} time(s). One invocation means `set -e` killed the loop.\n{out}"
    )


@requires_posix_shell
def test_each_failed_attempt_reports_its_exit_code(tmp_path):
    """A silent retry converts an intermittent failure into an invisible chronic one.

    The ticket is explicit that the frequency of the flake must be readable off
    the build log later. So each failed attempt must name ITSELF and its EXIT
    CODE — 139 for a segfault — rather than being swallowed.
    """
    rc, out, attempts = _exercise(tmp_path, FAKE_SEGFAULT_THEN_OK.format(succeed_on=3))

    assert rc == 0, out
    assert attempts == 3
    assert "attempt 1/3 FAILED with exit code 139" in out, out
    assert "attempt 2/3 FAILED with exit code 139" in out, out
    # On GitHub's own channel too, so it surfaces in the run summary rather than
    # only in a log a reader has to go looking for.
    assert out.count("::warning::PS-306") >= 2, out


@requires_posix_shell
def test_three_attempts_is_the_ceiling_and_then_the_build_FAILS(tmp_path):
    """Bounded, and then loud.

    Exactly three attempts — not two, not an unbounded loop — and a run that
    exhausts them must FAIL rather than continue toward a confusing downstream
    error. "Making the build go green by other means" is explicitly out of
    scope: if the toolchain cannot be produced, the run must fail.
    """
    rc, out, attempts = _exercise(tmp_path, FAKE_ALWAYS_SEGFAULTS)

    assert attempts == 3, f"expected exactly 3 attempts, got {attempts}:\n{out}"
    assert rc != 0, f"exhausted retries must FAIL the run:\n{out}"
    assert "::error::" in out
    assert "did NOT materialise" in out


@requires_posix_shell
def test_exit_zero_without_the_artifact_still_fails(tmp_path):
    """THE CRITERION MOST EASILY SATISFIED FALSELY.

    The ticket states the exit code and the artifact can disagree, and asks for
    the check to rest on the artifact on disk. An implementation that retried
    and then trusted `$?` would pass every other test in this file and fail
    here: this fake exits 0 on its first attempt and produces no toolchain at
    all. The build must stop, naming the missing VERSION file.
    """
    rc, out, attempts = _exercise(tmp_path, FAKE_EXITS_ZERO_PRODUCING_NOTHING)

    assert attempts == 1, "a clean exit must not be retried"
    assert rc != 0, (
        "a zero exit code with NO toolchain on disk must still fail. Trusting "
        f"$? here is the defect this test exists to catch:\n{out}"
    )
    assert "third_party/rust-toolchain/VERSION" in out
    assert "did NOT materialise" in out


@requires_posix_shell
def test_the_pause_between_attempts_is_the_operator_s_fifteen_seconds(tmp_path):
    """15s was the operator's request: long enough for transient load to subside.

    Asserted against the INJECTED SOURCE rather than by timing a run, because
    timing it would mean spending 30 seconds of the merge gate to observe a
    constant. The behavioural tests above override the pause to 0 through
    `PS306_RETRY_PAUSE_SECONDS`; nothing in the workflow sets that variable, so
    the production value is the default asserted here.
    """
    _make_ucpl(tmp_path)
    assert _run_harden(tmp_path)[0] == 0
    patched = (tmp_path / "ucpl" / "scripts" / "shared.sh").read_text(encoding="utf-8")

    assert '_pause="${PS306_RETRY_PAUSE_SECONDS:-15}"' in patched, (
        "the default pause must be 15s"
    )
    assert "_attempts=3" in patched, "the ticket asks for up to three attempts"
    assert "PS306_RETRY_PAUSE_SECONDS" not in WORKFLOW.read_text(encoding="utf-8"), (
        "the workflow must not override the pause — the override exists only so "
        "the test suite can exercise the loop without sleeping."
    )
