"""PS-244: a patched-only build may BORROW a control, but only a VERIFIED one.

`engine-trial-build` used to offer `unmodified` or `both` only, so every patched
build first recompiled the unmodified tree from scratch — ~1h25m of cold compile
(there is no build cache) re-establishing a control we may already hold. On the
engine-ownership rebase loop, where the cycle is *rebase → build → read failure →
fix → build again*, that tax is the dominant cost.

`trees=patched` removes it by borrowing a previously-completed run's control.
What it must NOT remove is the reasoning that made the control mandatory:

    "A patched build without its control cannot be attributed."

A compile failure on a patched tree has two possible causes — our patches, or an
environment that cannot build Chromium at all — and one run cannot separate them.
So the borrowed control is **verified, not trusted**: same ungoogled tag, same
host, and it actually compiled, each established from the control run's OWN
RECORDED EVIDENCE rather than from the operator's say-so.

WHY MOST OF THIS FILE IS REFUSALS RATHER THAN THE HAPPY PATH
────────────────────────────────────────────────────────────
The ticket is explicit: "Exercise the refusal path, not only the success path — a
guard that has never been seen to fire is the shape this project has been bitten
by repeatedly, most recently a comparator that reported agreement between two
identically-failed readings."

That was not hypothetical here. The first version of `ps218_verify_control.sh`
wrapped its checks in `{ ... } | tee "$REPORT"`. A pipeline runs its left side in
a SUBSHELL, so every `FAILURES` increment was discarded: against a
different-tag fixture the script printed its REFUSED lines, exited 0, and wrote
the borrow certificate for the control it had just refused. Every static check
was green. Only running the refusal caught it — hence these tests.
"""

import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
VERIFY_SH = REPO_ROOT / "scripts" / "ps218_verify_control.sh"
ATTRIBUTE_SH = REPO_ROOT / "scripts" / "ps218_attribute.sh"
MANIFEST_SH = REPO_ROOT / "scripts" / "ps218_manifest.sh"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"

TAG = "144.0.7559.132-1"
CONTROL_RUN = "33151144134"
THIS_RUN = "33999999999"

# The three scripts under test are bash, and they run on a `[self-hosted,
# persona-build]` Linux runner — POSIX is the only platform they are ever
# executed on in production.
#
# On a windows-latest runner `bash` resolves to the WSL launcher, which reports
# "Windows Subsystem for Linux has no installed distributions" and exits
# non-zero. That is a statement about the RUNNER, not about the scripts: it
# would make every refusal test below "pass" its `rc != 0` assertion for
# entirely the wrong reason, which is precisely the dead-probe shape this file
# exists to guard against — a check that fires without ever having read the
# thing it claims to check.
#
# So these are skipped on Windows rather than left to fail or, worse, to pass
# vacuously. `PATH` is pinned to "/usr/bin:/bin" in every runner below, which is
# itself POSIX-only and says the same thing.
requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32",
    reason="bash scripts; on windows-latest `bash` is the WSL launcher with no distro installed",
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: a borrowed control's recorded evidence, in the shape the real
# scripts write it. Every refusal test below starts from this VALID material and
# breaks exactly one thing, so a failure names the check that fired rather than
# leaving it ambiguous which of several defects the script noticed.
# ─────────────────────────────────────────────────────────────────────────────

PROVENANCE = f"""\
phase=compile
tree=unmodified
ungoogled_tag={TAG}
github_run_id={CONTROL_RUN}
github_run_attempt=1
recorded=2026-08-28T02:11:04+00:00
"""

ENV_RECORD = f"""\
# PS-218 build environment — tree: unmodified
# pass: pre-prepare
# recorded: 2026-08-28T00:37:12+00:00
# host: Linux persona-wsl-builder 6.6.87.2-microsoft-standard-WSL2 #1 SMP x86_64 GNU/Linux

== CPU as the RUNNER sees it ==
nproc:            32
nproc --all:      32
Model name:                           13th Gen Intel(R) Core(TM) i9-14900K
CPU(s):                               32

== SOURCE PROVENANCE ==
portablelinux HEAD: 9f2c1ab4de77bb0e1f0a2c3d4e5f60718293a4b5
portablelinux tag:  {TAG}
chromium version:   144.0.7559.132
"""

MANIFEST = """\
# PS-218 — build manifest: `unmodified` tree

| result | verdict |
|---|---|
| **1. Patches APPLIED** (as text) | YES |
| **2. Tree COMPILED** | YES |
| chrome binary on disk | PRESENT (412M) |
| chrome sha256 | `abc123` |
"""

CONTROL_LOG = "[50000/50000] LINK ./chrome\nninja: build completed successfully.\n"


def build_fixture(tmp_path, *, provenance=PROVENANCE, env=ENV_RECORD,
                  manifest=MANIFEST, log=CONTROL_LOG, current_env=None):
    """Lay out a borrowed control plus this run's own environment record."""
    control = tmp_path / "control"
    record = tmp_path / "record"
    control.mkdir(exist_ok=True)
    record.mkdir(exist_ok=True)

    if provenance is not None:
        (control / "compile-unmodified.provenance").write_text(provenance, encoding="utf-8")
    if env is not None:
        (control / "environment-unmodified.txt").write_text(env, encoding="utf-8")
    if manifest is not None:
        (control / "MANIFEST-unmodified.md").write_text(manifest, encoding="utf-8")
    if log is not None:
        (control / "compile-unmodified.log").write_text(log, encoding="utf-8")

    # This run's side of the host comparison. Same machine unless a test says so.
    (record / "environment-patched.txt").write_text(
        (current_env if current_env is not None else ENV_RECORD).replace(
            "tree: unmodified", "tree: patched"),
        encoding="utf-8",
    )
    return tmp_path


def run_verify(tmp_path, *, tag=TAG, control_run=CONTROL_RUN, this_run=THIS_RUN):
    """Run the real verification script. Returns (returncode, stdout+stderr)."""
    proc = subprocess.run(
        ["bash", str(VERIFY_SH)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "CONTROL_DIR": "control",
            "CURRENT_ENV": "record/environment-patched.txt",
            "UNGOOGLED_TAG": tag,
            "CONTROL_RUN_ID": control_run,
            "GITHUB_RUN_ID": this_run,
        },
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def assert_refused(tmp_path, out, rc, *, check):
    """A refusal must STOP the build and must NOT certify anything.

    All three assertions matter together. The subshell defect printed the
    REFUSED text while exiting 0 and writing the certificate — so asserting on
    the message alone would have passed against a script that certified a
    control it had just rejected.
    """
    assert rc != 0, f"a failed [{check}] check must exit non-zero; got 0:\n{out}"
    assert f"REFUSED [{check}]" in out, f"expected a [{check}] refusal:\n{out}"
    assert "VERDICT: REFUSED" in out
    assert "::error::BORROWED CONTROL REFUSED" in out, (
        "the refusal must be loud on the workflow's own error channel"
    )
    cert = tmp_path / "control" / "BORROWED-CONTROL.verified"
    assert not cert.exists(), (
        "a borrow certificate was written for a control that FAILED verification. "
        "That certificate is what authorises ps218_attribute.sh to accept another "
        "run's control, so writing one here defeats the entire safety case."
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE SUCCESS PATH
# ─────────────────────────────────────────────────────────────────────────────

@requires_posix_shell
def test_a_comparable_control_is_verified_and_certified(tmp_path):
    """The feature must actually work: a genuinely comparable control passes."""
    build_fixture(tmp_path)
    rc, out = run_verify(tmp_path)

    assert rc == 0, f"a valid control must be accepted:\n{out}"
    assert "VERDICT: control from run" in out and "VERIFIED" in out

    # Each named criterion is reported, so the record shows WHAT was checked
    # rather than only that something was.
    for check in ("artifact", "provenance", "tag", "host", "success"):
        assert f"ok       [{check}]" in out, f"the [{check}] check did not report a pass:\n{out}"

    cert = tmp_path / "control" / "BORROWED-CONTROL.verified"
    assert cert.exists(), "a verified borrow must leave the certificate attribution reads"
    body = cert.read_text(encoding="utf-8")
    assert f"verified_by_run={THIS_RUN}" in body
    assert f"control_run_id={CONTROL_RUN}" in body

    report = tmp_path / "record" / "control-borrow-verification.txt"
    assert report.is_file(), "the per-check record must be left in record/ for the artifact"


# ─────────────────────────────────────────────────────────────────────────────
# THE REFUSALS — one per criterion the ticket names
# ─────────────────────────────────────────────────────────────────────────────

@requires_posix_shell
def test_a_control_from_a_different_tag_is_refused(tmp_path):
    """Different tag = a different experiment, not a control."""
    other = "151.0.7922.173-1"
    build_fixture(
        tmp_path,
        provenance=PROVENANCE.replace(f"ungoogled_tag={TAG}", f"ungoogled_tag={other}"),
        env=ENV_RECORD.replace(f"portablelinux tag:  {TAG}", f"portablelinux tag:  {other}"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="tag")
    assert other in out and TAG in out, "the refusal must name BOTH tags so it can be acted on"


@requires_posix_shell
def test_a_tag_mismatch_in_either_witness_alone_is_refused(tmp_path):
    """The tag has TWO independent witnesses, and BOTH must agree.

    The provenance stamp comes from the dispatch input; the environment record
    comes from `git describe` in the checkout. Requiring both means a single
    mis-stamped file cannot pass a control off as the right tree — so a fixture
    that corrupts only ONE of them must still be refused.
    """
    build_fixture(
        tmp_path,
        env=ENV_RECORD.replace(f"portablelinux tag:  {TAG}", "portablelinux tag:  151.0.7922.173-1"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="tag")
    assert "git describe" in out, "the refusal should name which witness disagreed"


@requires_posix_shell
def test_a_control_from_a_different_host_is_refused(tmp_path):
    """Wall-clock, memory ceiling and toolchain belong to the machine."""
    build_fixture(
        tmp_path,
        env=ENV_RECORD.replace("persona-wsl-builder", "some-other-box"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="host")
    assert "hostname" in out


@requires_posix_shell
def test_a_control_built_on_different_hardware_is_refused(tmp_path):
    """A same-named host with a different CPU/core count is still not this machine."""
    build_fixture(
        tmp_path,
        env=ENV_RECORD.replace("nproc:            32", "nproc:            8"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="host")


@requires_posix_shell
def test_a_kernel_update_alone_does_not_refuse_the_borrow(tmp_path):
    """The guard must be strict without being useless.

    A WSL kernel bump does not make an otherwise-identical machine a different
    one. Refusing on it would make borrowing impractical and would teach the
    operator to stop reading refusals — so the kernel is REPORTED, not compared.
    This pins that deliberate choice, and would fail if someone tightened the
    host check into an exact `uname -a` match.
    """
    build_fixture(
        tmp_path,
        env=ENV_RECORD.replace("6.6.87.2-microsoft-standard-WSL2",
                               "6.6.99.1-microsoft-standard-WSL2"),
    )
    rc, out = run_verify(tmp_path)
    assert rc == 0, f"a kernel-release difference alone must not refuse the borrow:\n{out}"
    assert "kernel release recorded but NOT compared" in out


@requires_posix_shell
def test_a_control_that_failed_to_compile_is_refused(tmp_path):
    """The artifact EXISTING is not evidence the control succeeded.

    `record/` is uploaded with `if: always()`, so a FAILED control produces an
    artifact too. This is the exact trap the researcher flagged: the borrow
    check must read the verdict, not merely find a file.
    """
    build_fixture(
        tmp_path,
        manifest=MANIFEST.replace("| **2. Tree COMPILED** | YES |",
                                  "| **2. Tree COMPILED** | NO — FAILED |"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="success")


@requires_posix_shell
def test_a_control_whose_patches_never_applied_is_refused(tmp_path):
    """`NOT ATTEMPTED` is a third state, and it is not a pass."""
    build_fixture(
        tmp_path,
        manifest=MANIFEST
        .replace("| **1. Patches APPLIED** (as text) | YES |",
                 "| **1. Patches APPLIED** (as text) | NO — FAILED |")
        .replace("| **2. Tree COMPILED** | YES |",
                 "| **2. Tree COMPILED** | NOT ATTEMPTED (the phase did not run) |"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="success")


@requires_posix_shell
def test_a_green_verdict_with_no_binary_is_refused(tmp_path):
    """PS-218's own manifest says to trust the binary over the exit code."""
    build_fixture(
        tmp_path,
        manifest=MANIFEST.replace("| chrome binary on disk | PRESENT (412M) |",
                                  "| chrome binary on disk | ABSENT — no chrome binary was produced |"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="success")
    assert "trust the binary over the exit code" in out


@requires_posix_shell
def test_a_control_with_no_compile_log_is_refused(tmp_path):
    """A verified control with nothing to diff against would be a hollow pass."""
    build_fixture(tmp_path, log=None)
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="success")


@requires_posix_shell
def test_an_expired_artifact_gets_its_own_distinct_message(tmp_path):
    """Expiry is a different situation from a mismatch, and must read differently.

    Records are kept 30 days, so a control older than that is simply GONE. An
    operator told "different tag" when the real cause is expiry will debug the
    wrong thing — so this case names the retention bound and the fallback.
    """
    build_fixture(tmp_path, provenance=None, env=None, manifest=None, log=None)
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="artifact")
    assert "EXPIRED" in out and "30 days" in out, (
        "an absent artifact must explain the retention bound, not read as a mismatch"
    )
    assert "trees=both" in out, "the refusal must name the fallback the operator has"


@requires_posix_shell
def test_a_control_whose_stamp_names_another_run_is_refused(tmp_path):
    """The bytes on disk must be traceable to the run the operator NAMED.

    Otherwise "borrow run X" would accept whatever happened to be in control/,
    which on a self-hosted runner is exactly the stale-log problem PS-218 already
    guards against.
    """
    build_fixture(
        tmp_path,
        provenance=PROVENANCE.replace(f"github_run_id={CONTROL_RUN}", "github_run_id=11112222"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="provenance")


@requires_posix_shell
def test_a_patched_tree_log_cannot_pose_as_a_control(tmp_path):
    """A control is the UNMODIFIED tree. A patched log is not a control at all."""
    build_fixture(tmp_path, provenance=PROVENANCE.replace("tree=unmodified", "tree=patched"))
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="provenance")


@requires_posix_shell
def test_an_unstamped_control_is_refused(tmp_path):
    """Presence is not provenance — the invariant PS-218 already relied on."""
    build_fixture(tmp_path, provenance=None)
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="provenance")


# ─────────────────────────────────────────────────────────────────────────────
# THE COMPARATOR TRAP: two failed readings are not an agreement
# ─────────────────────────────────────────────────────────────────────────────

@requires_posix_shell
def test_two_unreadable_values_are_refused_rather_than_matched(tmp_path):
    """`[ "$a" = "$b" ]` returns TRUE when both sides are empty.

    That is the named failure mode on this ticket — "a comparator that reported
    agreement between two identically-failed readings" — and it is the most
    dangerous single defect available in this script: it would report a clean
    PASS on a control nobody actually checked.

    Both environment records here are missing the CPU and core-count lines, so a
    naive comparator compares "" against "" on every host field and agrees. The
    script must refuse instead.
    """
    stripped = "\n".join(
        line for line in ENV_RECORD.splitlines()
        if not line.startswith(("nproc:", "nproc --all:", "Model name:"))
    ) + "\n"
    build_fixture(tmp_path, env=stripped, current_env=stripped)

    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="host")
    assert "must never compare EQUAL" in out or "failed readings" in out, (
        "the refusal must say WHY an unreadable field is not a match"
    )


@requires_posix_shell
def test_a_literal_unknown_is_not_treated_as_a_value(tmp_path):
    """`git describe` writes the literal 'unknown' when it fails.

    Two 'unknown's are the same false agreement as two blanks, just harder to
    see — so the readability guard rejects that literal too.
    """
    build_fixture(
        tmp_path,
        env=ENV_RECORD.replace(f"portablelinux tag:  {TAG}", "portablelinux tag:  unknown"),
    )
    rc, out = run_verify(tmp_path)
    assert_refused(tmp_path, out, rc, check="tag")


@requires_posix_shell
def test_a_dispatch_naming_no_control_run_is_refused(tmp_path):
    """`trees=patched` without a run id has nothing to borrow."""
    build_fixture(tmp_path)
    rc, out = run_verify(tmp_path, control_run="")
    assert_refused(tmp_path, out, rc, check="artifact")


# ─────────────────────────────────────────────────────────────────────────────
# ATTRIBUTION: the stamp check is WIDENED, not removed
# ─────────────────────────────────────────────────────────────────────────────
# ps218_attribute.sh refuses any control whose stamp does not name the current
# run. Borrowing deliberately breaks that invariant, and the certificate is what
# authorises the exception. These pin that the exception is NARROW: without a
# valid certificate, another run's control is still refused exactly as before.

def run_attribution(tmp_path, *, cert=None, control_run=CONTROL_RUN, this_run=THIS_RUN):
    record = tmp_path / "record"
    record.mkdir(exist_ok=True)
    (record / "compile-patched.log").write_text(
        "../../third_party/blink/renderer/modules/webgl/"
        "webgl_rendering_context_base.cc:1234:5: error: no matching function\n",
        encoding="utf-8",
    )
    control = tmp_path / "control"
    control.mkdir(exist_ok=True)
    (control / "compile-unmodified.provenance").write_text(
        PROVENANCE.replace(f"github_run_id={CONTROL_RUN}", f"github_run_id={control_run}"),
        encoding="utf-8",
    )
    (control / "compile-unmodified.log").write_text(CONTROL_LOG, encoding="utf-8")
    if cert is not None:
        (control / "BORROWED-CONTROL.verified").write_text(cert, encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(ATTRIBUTE_SH)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "UCPL_DIR": str(tmp_path),
            "PATCH_DIR": str(PATCH_DIR),
            "CONTROL_DIR": "control",
            "UNGOOGLED_TAG": TAG,
            "GITHUB_RUN_ID": this_run,
        },
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"attribution failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


VALID_CERT = f"""\
verified_by_run={THIS_RUN}
verified_by_attempt=1
control_run_id={CONTROL_RUN}
ungoogled_tag={TAG}
verified_at=2026-08-28T03:00:00+00:00
"""


@requires_posix_shell
def test_a_certified_borrowed_control_is_accepted_and_labelled_borrowed(tmp_path):
    """The borrow works — and says so, in the document that makes the claim."""
    out = run_attribution(tmp_path, cert=VALID_CERT)

    assert "control origin:     BORROWED" in out
    assert CONTROL_RUN in out, "the attribution must name WHICH run the control came from"
    assert "must not be presented as one" in out, (
        "a borrowed-control attribution must not be presentable as in-run"
    )
    assert "control diff: NOT PERFORMED" not in out, (
        "a verified borrow must actually be USED for the control diff"
    )


@requires_posix_shell
def test_another_runs_control_without_a_certificate_is_still_refused(tmp_path):
    """The pre-existing invariant, intact. This is the regression that matters.

    If widening the stamp check had simply dropped it, every stale control log
    left on the self-hosted runner between dispatches would now be accepted —
    silently licensing "PRE-EXISTING — NOT ours" against a tree never built here.
    """
    out = run_attribution(tmp_path, cert=None)

    assert "control log REFUSED" in out
    assert "no verified-borrow certificate" in out
    assert "control origin:     BORROWED" not in out
    assert "CONTROL UNKNOWN" in out, "a refused control must fall back to CONTROL UNKNOWN"


@requires_posix_shell
def test_a_certificate_from_an_earlier_dispatch_authorises_nothing(tmp_path):
    """`control/` survives between runs on a self-hosted runner — so must this guard.

    A certificate is only meaningful for the run that wrote it. One left behind
    by yesterday's dispatch must not authorise today's.
    """
    out = run_attribution(
        tmp_path,
        cert=VALID_CERT.replace(f"verified_by_run={THIS_RUN}", "verified_by_run=1111"),
    )
    assert "control log REFUSED" in out
    assert "authorises nothing" in out
    assert "control origin:     BORROWED" not in out


@requires_posix_shell
def test_a_certificate_about_a_different_run_than_the_log_is_refused(tmp_path):
    """The certificate and the bytes must be about the SAME control run.

    Otherwise a genuine certificate for run A would launder an unrelated control
    log for run B sitting in the same directory.
    """
    out = run_attribution(tmp_path, cert=VALID_CERT, control_run="8888")
    assert "control log REFUSED" in out
    assert "different runs" in out
    assert "control origin:     BORROWED" not in out


@requires_posix_shell
def test_a_borrowed_control_from_another_tag_is_refused_despite_a_certificate(tmp_path):
    """A certificate cannot excuse a different tree — the tag gate binds both paths."""
    out = run_attribution(
        tmp_path,
        cert=VALID_CERT.replace(f"ungoogled_tag={TAG}", "ungoogled_tag=151.0.7922.173-1"),
    )
    # The log's own stamp still says TAG, so this trips the certificate's tag gate.
    assert "control log REFUSED" in out
    assert "control origin:     BORROWED" not in out


@requires_posix_shell
def test_the_in_run_control_path_is_unchanged(tmp_path):
    """`both` must behave exactly as before: no certificate, still accepted."""
    out = run_attribution(tmp_path, cert=None, control_run=THIS_RUN)

    assert "control origin:     IN-RUN" in out
    assert "control log REFUSED" not in out
    assert "BORROWED" not in out


# ─────────────────────────────────────────────────────────────────────────────
# THE MANIFEST: provenance visible without opening the workflow
# ─────────────────────────────────────────────────────────────────────────────

def run_manifest(tmp_path, *, control_run=""):
    (tmp_path / "record").mkdir(exist_ok=True)
    proc = subprocess.run(
        ["bash", str(MANIFEST_SH), "patched"],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "UCPL_DIR": str(tmp_path),
            "UNGOOGLED_TAG": TAG,
            "PREPARE_RESULT": "success",
            "COMPILE_RESULT": "failure",
            "CONTROL_RUN_ID": control_run,
        },
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"manifest failed:\n{proc.stdout}\n{proc.stderr}"
    return (tmp_path / "record" / "MANIFEST-patched.md").read_text(encoding="utf-8")


@requires_posix_shell
def test_the_manifest_says_when_the_control_was_borrowed(tmp_path):
    """A reader must not have to open the workflow to learn this."""
    body = run_manifest(tmp_path, control_run=CONTROL_RUN)

    assert "BORROWED" in body
    assert CONTROL_RUN in body, "the manifest must name WHICH run the control came from"
    assert "verified, not trusted" in body

    # The qualification must attach to the CLAIM it qualifies. "Pre-existing —
    # NOT ours" means something weaker under a borrowed control, and a reader
    # who carries that phrase away unqualified has the wrong belief.
    #
    # Matched on a single-line fragment: the prose is hard-wrapped, so any
    # assertion spanning a line break would never match. This follows the same
    # convention as the PS-218 suite for the same reason.
    assert "*a tree built alongside this one*" in body, (
        "the manifest must say explicitly that a borrowed control is NOT a tree "
        "built alongside this one, beside the pre-existing claim it qualifies"
    )
    assert "Pre-existing" in body


@requires_posix_shell
def test_the_manifest_says_in_run_on_the_default_path(tmp_path):
    """And the safe path must be positively identified, not merely silent."""
    body = run_manifest(tmp_path, control_run="")

    assert "IN-RUN" in body
    assert "BORROWED" not in body
    assert "built **by this dispatch**" in body


# ─────────────────────────────────────────────────────────────────────────────
# THE WORKFLOW WIRING
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_patched_only_is_offered_and_both_is_still_the_default(workflow):
    inputs = workflow.get(True, workflow.get("on"))["workflow_dispatch"]["inputs"]

    assert inputs["trees"]["options"] == ["both", "unmodified", "patched"]
    assert inputs["trees"]["default"] == "both", (
        "the safe path must remain the default; nothing about it becomes harder to use"
    )
    assert "control_run_id" in inputs, "the operator must be able to name the control run"


def test_the_control_job_is_skipped_only_on_the_patched_only_path(workflow):
    """That skip IS the saving — and it must not apply to `both`."""
    assert workflow["jobs"]["unmodified"]["if"] == "inputs.trees != 'patched'"


def test_the_patched_job_can_never_follow_a_FAILED_control(workflow):
    """The PS-218 invariant, restated in the one place it is enforced.

    The gate must enumerate the two legitimate entries — `both` + control
    SUCCEEDED, `patched` + control SKIPPED. A permissive gate (`always()`, or
    `result != 'failure'`) would let the patched tree run after a control that
    FAILED, which is precisely the unattributable build this workflow exists to
    prevent.
    """
    jobs = workflow["jobs"]
    needs = jobs["patched"].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "unmodified" in needs, "the `needs:` edge must survive this change"

    gate = " ".join(jobs["patched"]["if"].split())
    assert "inputs.trees == 'both' && needs.unmodified.result == 'success'" in gate
    assert "inputs.trees == 'patched' && needs.unmodified.result == 'skipped'" in gate
    assert "always()" not in gate, (
        "`always()` would run the patched build after a FAILED control — and after "
        "a cancelled dispatch, spending hours on the owner's workstation."
    )
    assert "!cancelled()" in gate, (
        "a status function is required for the skipped-dependency path to run at all"
    )


def _patched_steps(workflow):
    return workflow["jobs"]["patched"]["steps"]


def test_the_borrowed_control_is_verified_before_anything_is_compiled(workflow):
    """Placement is the difference between a cheap refusal and a wasted build."""
    names = [s.get("name", "") for s in _patched_steps(workflow)]
    verify = next(i for i, n in enumerate(names) if "Verify the borrowed control" in n)
    compile_i = next(i for i, n in enumerate(names) if n.startswith("Compile"))
    prepare_i = next(i for i, n in enumerate(names) if "Apply patches" in n)

    assert verify < prepare_i < compile_i, (
        "verification must precede prepare and compile, so an unusable control "
        "costs seconds rather than the compile this feature exists to avoid"
    )

    step = _patched_steps(workflow)[verify]
    assert step.get("continue-on-error") is not True, (
        "the verification gate must be able to STOP the build; continue-on-error "
        "would reduce a refusal to a warning and produce the unattributed build "
        "this ticket forbids."
    )


def test_the_in_run_fetch_cannot_overwrite_a_verified_borrow(workflow):
    """A TOCTOU that would silently defeat the whole verification.

    The original fetch step is unconditional. Left that way it would download
    over `control/` AFTER verification had passed — replacing checked bytes with
    unchecked ones — so it is scoped to the `both` path.
    """
    fetches = [s for s in _patched_steps(workflow)
               if "download-artifact" in str(s.get("uses", ""))]
    assert len(fetches) == 2, "expected exactly two fetch steps: the borrow and the in-run one"

    by_cond = {s.get("if"): s for s in fetches}
    assert "inputs.trees == 'both'" in by_cond, (
        "the in-run fetch must be scoped to `both`, or it would overwrite the "
        "verified borrowed control after the check passed"
    )
    assert "inputs.trees == 'patched'" in by_cond

    borrow = by_cond["inputs.trees == 'patched'"]
    assert "run-id" in borrow["with"], (
        "without run-id the download resolves against the CURRENT run, which on a "
        "patched-only dispatch has no unmodified artifact at all"
    )


def _effective_permissions(workflow, job):
    """What the job's token ACTUALLY holds.

    A job-level `permissions:` block REPLACES the file-level one — it does not
    merge with it — and a declared block sets every UNLISTED scope to `none`.
    Both facts matter here, so this resolves them rather than reading one level
    and hoping.
    """
    job_perms = workflow["jobs"][job].get("permissions")
    return job_perms if job_perms is not None else workflow.get("permissions")


def test_the_patched_job_may_actually_read_the_borrowed_runs_artifact(workflow):
    """The defect that made the whole feature unreachable, pinned.

    Round 1 shipped the borrow step with `run-id` and a token — but the token
    could not USE it. `actions/download-artifact` reading ANOTHER run's artifact
    calls `GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts`, which
    needs `actions: read`; the file-level block grants `contents: read` only,
    and a declared block zeroes every unlisted scope. So every patched-only
    dispatch 403'd at the fetch — including one naming a perfectly valid
    control — and the ticket's success criterion was unreachable.

    It failed CLOSED, so no unattributed build was ever possible. But the
    refusal path was exercised and the SUCCESS path was not, and the defect
    landed precisely there. This is that missing assertion.
    """
    perms = _effective_permissions(workflow, "patched")
    assert perms is not None, (
        "the patched job must resolve a permissions block; with none at either "
        "level the token would hold the repo default, which is not what this "
        "workflow documents"
    )
    assert perms.get("actions") == "read", (
        "the patched job's token cannot list another run's artifacts without "
        "`actions: read`, so the borrow step 403s and `trees=patched` NEVER "
        f"works. Effective permissions were {perms!r}."
    )
    assert perms.get("contents") == "read", (
        "a job-level block REPLACES the file-level one rather than merging, so "
        "`contents: read` must be restated here or the checkouts cannot read "
        "the repo"
    )


def test_borrowing_bought_no_write_scope_anywhere(workflow):
    """The security posture the widening had to preserve.

    These jobs execute untrusted third-party build code (the Chromium source,
    upstream build scripts, a toolchain fetched at build time). `actions: read`
    can only enumerate and download existing artifacts — it cannot mutate the
    repo, publish a release or move a ref. Nothing here may hold a write scope,
    and the control job — which reads no other run — must not be widened at all.
    """
    for job in workflow["jobs"]:
        perms = _effective_permissions(workflow, job)
        assert perms != "write-all", f"{job} holds write-all"
        for scope, level in (perms or {}).items():
            assert level in ("read", "none"), (
                f"job {job!r} holds {scope}: {level!r}. This workflow runs "
                "untrusted third-party code and must never carry a write-capable "
                "token."
            )

    assert _effective_permissions(workflow, "unmodified").get("actions") != "read", (
        "the control job reads no other run's artifacts, so it must NOT be "
        "widened along with the patched job"
    )


def test_the_artifact_name_reveals_a_borrowed_control(workflow):
    """Provenance must be visible in the artifact list itself."""
    upload = next(s for s in _patched_steps(workflow)
                  if "upload-artifact" in str(s.get("uses", ""))
                  and "record" in s.get("name", "").lower())
    name = upload["with"]["name"]
    assert "borrowed-control" in name
    assert "inputs.control_run_id" in name, "the artifact name must name the borrowed run"


def test_the_workstation_protection_survives_this_change(workflow):
    """The boundary the ticket draws twice: dispatch-only, no exceptions.

    Re-asserted here rather than left to the PS-218 file alone, because this
    change edits the trigger block and that is exactly when a trigger gets added
    by accident.
    """
    triggers = workflow.get(True, workflow.get("on"))
    assert set(triggers) == {"workflow_dispatch"}, (
        f"workflow_dispatch must remain the ONLY trigger; found {sorted(map(str, triggers))}"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_verification_script_is_executable():
    """A runtime-only failure that no other test in this file could see.

    The workflow invokes `persona/scripts/ps218_verify_control.sh` DIRECTLY, not
    via `bash <path>`. Committed without the executable bit it dies with
    "Permission denied" on the runner — and every test here would still pass,
    because they all run it through `bash` explicitly.

    It was in fact committed 100644 first. This is the assertion that would have
    caught it, and the one that stops it regressing.
    """
    assert VERIFY_SH.stat().st_mode & 0o111, (
        "ps218_verify_control.sh is not executable. The workflow runs it as a "
        "command, so it would fail with 'Permission denied' on the runner."
    )


def test_no_build_cache_was_introduced(workflow):
    """Explicitly out of bounds: it would make this change's own effect unmeasurable."""
    text = WORKFLOW.read_text(encoding="utf-8").lower()
    for token in ("ccache", "sccache", "actions/cache"):
        assert token not in text, (
            f"{token!r} appeared in the workflow. A build cache is a separate "
            "optimisation and mixing it in here makes the ~1h25m saving this "
            "ticket claims impossible to measure."
        )
