"""PS-289: a run that loses its runner mid-step must still leave readable evidence.

WHAT THIS IS ABOUT
──────────────────
`engine-trial-build.yml` reads as though its evidence is protected: the long
steps carry `continue-on-error: true` and every record-upload step carries
`if: always()`. Both are real, and NEITHER fires when the RUNNER dies — a dead
runner never reaches an upload step, and GitHub never flushes the job's log.

Measured twice. Runs 33170172175 (2026-08-28) and 33748889046 (2026-09-03) both
lost the runner inside `prepare`. Afterwards: `artifacts total_count = 0` and
`gh run view --log` → `log not found: 100627638148`. Ten minutes of work, eight
completed steps including a 5 GB checkout, and nothing readable.

⛔ THE ASSERTION THAT WOULD HAVE BEEN VACUOUS HERE
─────────────────────────────────────────────────
The tempting test is "the script writes a journal line" — run it, read the file,
assert the text. That would pass against an implementation that buffers
everything and flushes at exit, which is EXACTLY the defect: `if: always()` also
"writes the evidence", right up until the process is killed. A green from that
test would be a green about an unchanged system.

So the load-bearing test here KILLS THE BUILD PROCESS GROUP WITH SIGKILL
mid-phase and then reads what is on disk from outside it. Nothing about that
assertion can be satisfied by an end-of-run flush: the end of the run never
happens. `test_a_sigkilled_phase_still_says_how_far_it_got` is the one to keep
honest if this file is ever trimmed.

The second half of the feature is SALVAGE — carrying a dead dispatch's stranded
`record/` out on the NEXT dispatch's artifact. Its tests are mostly about what
salvage must NOT do: it must not weaken the zeroing that keeps a stale control
from being read as this run's (the PS-192 shape), and it must not lose files
while appearing to work (it did exactly that in development — see
`test_salvage_recovers_every_file_not_only_the_first`).
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.posix_shell import find_posix_shell, shell_env

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
JOURNAL_SH = REPO_ROOT / "scripts" / "ps289_journal.sh"
BUILD_SH = REPO_ROOT / "scripts" / "ps218_build.sh"

# Same reasoning as tests/test_ps244_borrowed_control.py: these are bash scripts
# that only ever execute on the `[self-hosted, persona-build]` Linux runner. On
# windows-latest `bash` is the WSL launcher with no distro installed, so every
# assertion would be about a script that never started.
requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32",
    reason="bash scripts; on windows-latest `bash` is the WSL launcher with no distro installed",
)


def run_journal(*args, cwd, journal_root, run_id="2000", **env_extra):
    """Invoke ps289_journal.sh with a pinned toolchain and an isolated root."""
    shell = find_posix_shell()
    assert shell, "no POSIX shell on this host"
    env = shell_env(
        PS289_JOURNAL_ROOT=str(journal_root),
        GITHUB_RUN_ID=run_id,
        GITHUB_RUN_ATTEMPT="1",
        HOME=str(cwd),
        **env_extra,
    )
    return subprocess.run(
        [shell, str(JOURNAL_SH), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE LOAD-BEARING TEST: survive an actual kill
# ─────────────────────────────────────────────────────────────────────────────

@requires_posix_shell
def test_a_sigkilled_phase_still_says_how_far_it_got(tmp_path):
    """Kill the build's whole process group mid-phase; read the evidence anyway.

    This is the ONE assertion that cannot be satisfied by writing evidence at
    the end, because there is no end: SIGKILL is uncatchable and takes the
    process group with it, which is what a runner dying underneath the job
    looks like from inside the job.

    Asserts three separate things, because "a file exists" is not the claim:
      1. a BEGIN line is present — the phase started,
      2. at least one ALIVE heartbeat is present with a non-zero elapsed AND
         the last line the build had printed — the run's progress is BOUNDED,
      3. NO END line — which is what distinguishes a death from a failure.
    """
    shell = find_posix_shell()
    assert shell, "no POSIX shell on this host"

    ws = tmp_path / "ws"
    (ws / "ucpl" / "scripts").mkdir(parents=True)
    # Stand in for upstream's entrypoint: prints progress slowly, never returns
    # within the life of this test.
    (ws / "ucpl" / "scripts" / "docker-build.sh").write_text(
        "#!/bin/bash\nfor i in $(seq 1 300); do echo \"applying patch $i\"; sleep 1; done\n",
        encoding="utf-8",
    )
    (ws / "ucpl" / "scripts" / "docker-build.sh").chmod(0o755)

    journal_root = tmp_path / "journal"
    env = shell_env(
        PS289_JOURNAL_ROOT=str(journal_root),
        PS289_HEARTBEAT_SECONDS="1",
        UCPL_DIR="ucpl",
        UNGOOGLED_TAG="144.0.7559.132-1",
        GITHUB_RUN_ID="8888",
        GITHUB_RUN_ATTEMPT="1",
        HOME=str(tmp_path),
    )

    proc = subprocess.Popen(
        [shell, str(BUILD_SH), "prepare", "patched"],
        cwd=str(ws), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,          # its own process group, so the kill is total
    )
    journal = journal_root / "8888-1" / "journal.txt"
    try:
        # Wait for at least two heartbeats to have been written.
        deadline = time.time() + 45
        while time.time() < deadline:
            if journal.is_file() and journal.read_text(encoding="utf-8").count("  ALIVE  ") >= 2:
                break
            time.sleep(0.25)
        # THE RUNNER DIES. Whole group, SIGKILL, no cleanup, no exit handler.
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:                       # pragma: no cover - cleanup
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=30)

    assert journal.is_file(), (
        "the durable journal does not exist after a SIGKILLed phase — the whole "
        "point of PS-289 is that this file outlives the process that wrote it"
    )
    text = journal.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]

    begins = [ln for ln in lines if "  BEGIN  " in ln]
    alives = [ln for ln in lines if "  ALIVE  " in ln]
    ends = [ln for ln in lines if "  END    " in ln]

    assert begins, f"no BEGIN line survived the kill; journal was:\n{text}"
    assert alives, (
        "no ALIVE heartbeat survived the kill. Without one the journal says the "
        "phase started and nothing else, which is the blackout this ticket is "
        f"about. Journal was:\n{text}"
    )
    assert not ends, (
        "an END line is present after a SIGKILL. END carries the phase's exit "
        "code and must appear ONLY when the phase actually finished — it is the "
        "sole thing distinguishing a dead runner from a failed build."
    )

    last_alive = alives[-1]
    assert "elapsed=" in last_alive and "elapsed=0s" not in last_alive, (
        f"the heartbeat does not bound how far the phase got: {last_alive!r}"
    )
    assert "applying patch" in last_alive, (
        "the heartbeat does not carry the build's last output line, so a reader "
        f"cannot tell what the build was doing when it died: {last_alive!r}"
    )


@requires_posix_shell
def test_a_phase_that_finishes_records_its_exit_code(tmp_path):
    """The other side of the discriminator: a FAILED build is not a dead one.

    A build that exits non-zero must record `END … rc=<n>`. Without this the
    absence of END would mean nothing — every run would look dead.
    """
    shell = find_posix_shell()
    ws = tmp_path / "ws"
    (ws / "ucpl" / "scripts").mkdir(parents=True)
    (ws / "ucpl" / "scripts" / "docker-build.sh").write_text(
        "#!/bin/bash\necho 'patch 003 FAILED to apply'\nexit 3\n", encoding="utf-8"
    )
    (ws / "ucpl" / "scripts" / "docker-build.sh").chmod(0o755)

    journal_root = tmp_path / "journal"
    env = shell_env(
        PS289_JOURNAL_ROOT=str(journal_root),
        PS289_HEARTBEAT_SECONDS="1",
        UCPL_DIR="ucpl",
        UNGOOGLED_TAG="144.0.7559.132-1",
        GITHUB_RUN_ID="9001",
        GITHUB_RUN_ATTEMPT="1",
        HOME=str(tmp_path),
    )
    result = subprocess.run(
        [shell, str(BUILD_SH), "prepare", "patched"],
        cwd=str(ws), env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 3, (
        "ps218_build.sh must still propagate the phase's exit code; journaling "
        f"changed the script's verdict. stdout:\n{result.stdout}\n{result.stderr}"
    )

    text = (journal_root / "9001-1" / "journal.txt").read_text(encoding="utf-8")
    assert "  END    patched/prepare  rc=3" in text, (
        "a phase that FAILED must record END with its real exit code, so a "
        f"failure is never read as a runner death. Journal was:\n{text}"
    )


@requires_posix_shell
def test_journaling_never_fails_the_build_it_only_describes(tmp_path):
    """An unwritable journal root must cost a build nothing.

    The journal exists to describe a build. If it could break one, this ticket
    would have made a low-frequency evidence defect into a high-frequency
    build defect — a strictly worse trade on a multi-hour compile.
    """
    shell = find_posix_shell()
    ws = tmp_path / "ws"
    (ws / "ucpl" / "scripts").mkdir(parents=True)
    (ws / "ucpl" / "scripts" / "docker-build.sh").write_text(
        "#!/bin/bash\necho ok\n", encoding="utf-8"
    )
    (ws / "ucpl" / "scripts" / "docker-build.sh").chmod(0o755)

    # A path that cannot be created: an existing FILE where a directory is needed.
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    env = shell_env(
        PS289_JOURNAL_ROOT=str(blocked / "journal"),
        PS289_HEARTBEAT_SECONDS="1",
        UCPL_DIR="ucpl",
        UNGOOGLED_TAG="t",
        GITHUB_RUN_ID="9002",
        GITHUB_RUN_ATTEMPT="1",
        HOME=str(tmp_path),
    )
    result = subprocess.run(
        [shell, str(BUILD_SH), "prepare", "patched"],
        cwd=str(ws), env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, (
        "an unwritable journal root failed the build. Recording progress must "
        f"never be the reason a compile stops. stderr:\n{result.stderr}"
    )


@requires_posix_shell
def test_the_journal_is_written_outside_the_workspace(tmp_path):
    """`record/` is zeroed by the next dispatch — so the durable copy cannot live there.

    This is the property that makes the journal recoverable at all. `record/`
    genuinely survives a runner death ON DISK; what removes it is the NEXT
    dispatch's zeroing step, which is correct and must stay. A journal inside
    the workspace would be deleted by exactly the step that is supposed to
    rescue it.
    """
    ws = tmp_path / "ws"
    (ws / "record").mkdir(parents=True)
    result = run_journal("root", cwd=ws, journal_root=tmp_path / "journal")
    assert result.returncode == 0, result.stderr
    root = Path(result.stdout.strip())
    assert ws not in root.parents and root != ws, (
        f"the journal root {root} is inside the workspace {ws}; the next "
        "dispatch's zeroing step would delete the very evidence this exists to keep"
    )

    # And the default (no override) is a HOME-relative path, not a workspace one.
    shell = find_posix_shell()
    env = shell_env(HOME=str(tmp_path / "home"), GITHUB_RUN_ID="1")
    default_root = subprocess.run(
        [shell, str(JOURNAL_SH), "root"],
        cwd=str(ws), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
    ).stdout.strip()
    assert str(ws) not in default_root, (
        f"the DEFAULT journal root {default_root} resolves inside the workspace"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SALVAGE — recovering a dead dispatch's stranded record/
# ─────────────────────────────────────────────────────────────────────────────

def seed_dead_record(ws, run_id="1001"):
    """A previous dispatch's leftovers, in the shape the real scripts write them."""
    rec = ws / "record"
    (rec / "sub").mkdir(parents=True, exist_ok=True)
    (rec / "prepare-patched.provenance").write_text(
        f"phase=prepare\ntree=patched\nungoogled_tag=144.0.7559.132-1\n"
        f"github_run_id={run_id}\ngithub_run_attempt=1\n",
        encoding="utf-8",
    )
    (rec / "prepare-patched.log").write_text("applying 003\napplying 004\n", encoding="utf-8")
    (rec / "environment-patched.txt").write_text("nproc: 32\n", encoding="utf-8")
    (rec / "patches-staged.txt").write_text("# count: 16\n", encoding="utf-8")
    (rec / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
    return rec


@requires_posix_shell
def test_salvage_recovers_every_file_not_only_the_first(tmp_path):
    """The regression that made salvage LOOK like it worked while losing files.

    `_salvage_file` originally assigned `dest="$2"` without `local`. Bash
    variables are global, so it overwrote the CALLER's `dest` — the salvage
    loop's destination directory — and every file after the first was written
    inside the previous file's path (`…/b.log/a.provenance/c.txt`). The first
    file arrived intact, so a spot-check passed while a dead run's whole record
    collapsed to one file.

    So this asserts EVERY seeded file arrives at its own path, not that salvage
    "produced output".
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="1001")

    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002")
    assert result.returncode == 0, result.stderr

    dest = ws / "record" / "salvaged" / "record-from-run-1001"
    expected = {
        "prepare-patched.provenance": "github_run_id=1001",
        "prepare-patched.log": "applying 004",
        "environment-patched.txt": "nproc: 32",
        "patches-staged.txt": "# count: 16",
        "sub/nested.txt": "nested",
    }
    for rel, needle in expected.items():
        f = dest / rel
        assert f.is_file(), (
            f"salvage lost {rel}. Recovered set was: "
            f"{sorted(p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file())}"
        )
        assert needle in f.read_text(encoding="utf-8"), f"{rel} arrived but its content did not"


@requires_posix_shell
def test_salvage_still_zeroes_the_top_level_so_a_stale_control_cannot_be_read(tmp_path):
    """Salvage replaced `rm -rf record`; it must not have weakened it.

    ps218_attribute.sh and ps218_verify_control.sh read EXACT top-level paths
    (`record/compile-unmodified.log`, `record/environment-patched.txt`). A
    leftover from an earlier dispatch sitting at one of those paths is read as
    THIS run's — errors billed "PRE-EXISTING — NOT ours" against a tree that was
    never built here. That is the PS-192 shape the zeroing exists to prevent,
    and durability must not be bought with it.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="1001")

    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002")
    assert result.returncode == 0, result.stderr

    rec = ws / "record"
    stale_at_top = [
        p.name for p in rec.iterdir()
        if p.is_file() and not p.name.startswith("journal-")
    ]
    assert not stale_at_top, (
        f"salvage left the previous dispatch's files at the top level of record/: "
        f"{stale_at_top}. Every consumer reads exact top-level paths, so these "
        "would be read as THIS run's."
    )
    assert not (rec / "prepare-patched.log").exists()
    assert not (rec / "environment-patched.txt").exists()
    # …and the recovered copies are quarantined under salvaged/ instead.
    assert (rec / "salvaged" / "record-from-run-1001" / "environment-patched.txt").is_file()


@requires_posix_shell
def test_salvage_does_not_carry_off_the_current_runs_own_record(tmp_path):
    """Material stamped with THIS run id is not salvage — it is this run's work.

    Without this the second job of a `both` dispatch would file its own sibling's
    output under `salvaged/` and label it as belonging to an earlier dispatch,
    which is a false provenance claim rather than a recovery.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="1002")   # SAME id as the salvaging run

    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002")
    assert result.returncode == 0, result.stderr
    assert not (ws / "record" / "salvaged").exists(), (
        "this run's own material was filed as salvage from an earlier dispatch"
    )


@requires_posix_shell
def test_a_record_with_no_provenance_is_labelled_unknown_never_guessed(tmp_path):
    """A dispatch that died BEFORE prepare wrote no provenance stamp at all.

    That case must be recovered and labelled `unknown` — never attributed to a
    run id nobody established. An invented provenance on recovered evidence is
    worse than no evidence, because it is believable.
    """
    ws = tmp_path / "ws"
    (ws / "record").mkdir(parents=True)
    (ws / "record" / "environment-patched.txt").write_text("nproc: 32\n", encoding="utf-8")

    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002")
    assert result.returncode == 0, result.stderr
    dest = ws / "record" / "salvaged" / "record-from-run-unknown"
    assert (dest / "environment-patched.txt").is_file(), (
        "a record with no provenance stamp was not recovered at all; that is the "
        "run that died EARLIEST, and the one whose loss is hardest to reason about"
    )


@requires_posix_shell
def test_salvage_is_a_no_op_on_a_clean_runner_and_never_fails(tmp_path):
    """The common case: nothing to salvage. It must exit 0 and leave a clean record/.

    Every dispatch pays this step, so a non-zero here would fail every build on
    a freshly-provisioned runner.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002")
    assert result.returncode == 0, f"salvage failed on a clean runner:\n{result.stderr}"
    assert (ws / "record").is_dir(), "salvage must leave record/ existing and empty"
    assert not (ws / "record" / "salvaged").exists()


@requires_posix_shell
def test_an_oversized_log_is_truncated_head_and_TAIL_with_the_drop_stated(tmp_path):
    """A Chromium compile log runs to tens of MB. Keep it bounded — and keep the TAIL.

    The tail is where a dead run stopped, so a truncation that kept only the head
    would discard the single most informative part. The drop is stated in-line so
    a reader is never silently handed a partial log.
    """
    ws = tmp_path / "ws"
    (ws / "record").mkdir(parents=True)
    (ws / "record" / "p.provenance").write_text("github_run_id=999\n", encoding="utf-8")
    (ws / "record" / "compile-patched.log").write_text(
        "".join(f"ninja line {i}\n" for i in range(5000)), encoding="utf-8"
    )

    result = run_journal(
        "salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1000",
        PS289_SALVAGE_MAX_BYTES="2000",
    )
    assert result.returncode == 0, result.stderr

    out = (ws / "record" / "salvaged" / "record-from-run-999" / "compile-patched.log").read_text(
        encoding="utf-8"
    )
    assert "ninja line 0\n" in out, "the head was not kept"
    assert "ninja line 4999\n" in out, (
        "the TAIL was not kept — that is where a dead run stopped, and it is the "
        "part a reader needs most"
    )
    assert "TRUNCATED" in out and "lines were dropped" in out, (
        "the log was truncated without saying so; a silently partial log reads as "
        "a complete one that simply ended"
    )


@requires_posix_shell
def test_only_UNFINISHED_journals_ride_out_on_the_next_dispatch(tmp_path):
    """A journal whose phases all ENDED describes a run that shipped its own artifact.

    Carrying those out too would bury the one that matters. The discriminator is
    exactly the BEGIN-without-END signature the kill test above pins.
    """
    journal_root = tmp_path / "journal"
    (journal_root / "5001-1").mkdir(parents=True)
    (journal_root / "5001-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:05:00Z  ALIVE  patched/prepare  elapsed=300s\n",
        encoding="utf-8",
    )
    (journal_root / "5002-1").mkdir(parents=True)
    (journal_root / "5002-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:05:00Z  END    patched/prepare  rc=0\n",
        encoding="utf-8",
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_journal("salvage", cwd=ws, journal_root=journal_root, run_id="6000")
    assert result.returncode == 0, result.stderr

    journals = ws / "record" / "salvaged" / "journals"
    assert (journals / "journal-5001-1.txt").is_file(), (
        "the UNFINISHED journal — the dead run — was not carried out"
    )
    assert not (journals / "journal-5002-1.txt").exists(), (
        "a journal whose phases all ENDED was carried out too; that run uploaded "
        "its own artifact, and re-shipping it buries the dead one"
    )


@requires_posix_shell
def test_the_recovery_is_announced_on_the_run_summary_not_only_inside_an_artifact(tmp_path):
    """A reader looking for the lost run is looking at GitHub, not at a file listing.

    An artifact somebody has to already know to download is only half a remedy.
    When a dispatch recovers a dead one, the run's summary page must say so AND
    carry the last recorded position — which is the ticket's stated outcome in
    one screen: whether patch application had started, finished, or failed,
    without re-running anything.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="4444")

    journal_root = tmp_path / "journal"
    (journal_root / "4444-1").mkdir(parents=True)
    (journal_root / "4444-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-03T00:00:00Z  BEGIN  patched/prepare\n"
        '2026-09-03T00:09:40Z  ALIVE  patched/prepare  elapsed=580s  last="[4120/50000] CXX gpu_shim.o"\n',
        encoding="utf-8",
    )

    summary = tmp_path / "summary.md"
    result = run_journal(
        "salvage", cwd=ws, journal_root=journal_root, run_id="4445",
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert result.returncode == 0, result.stderr

    text = summary.read_text(encoding="utf-8")
    assert "4444" in text, "the summary does not name the run whose evidence was recovered"
    assert "BEGIN  patched/prepare" in text, (
        "the summary does not carry the dead run's last recorded position, so a "
        "reader still has to download an artifact to learn anything"
    )
    assert "gpu_shim.o" in text, (
        "the summary omits what the build was actually doing when it died"
    )


@requires_posix_shell
def test_no_recovery_notice_is_written_when_nothing_was_salvaged(tmp_path):
    """Every dispatch runs this step. A clean one must not claim a recovery.

    A summary announcing a recovery on a run that recovered nothing is a false
    signal on the common path, and it would train a reader to ignore the notice
    on the rare run where it is true.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")

    result = run_journal(
        "salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="1002",
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert result.returncode == 0, result.stderr
    assert "Recovered evidence" not in summary.read_text(encoding="utf-8"), (
        "a clean dispatch announced a recovery it did not perform"
    )


@requires_posix_shell
def test_a_previous_run_that_FINISHED_is_not_filed_as_a_dead_one(tmp_path):
    """A healthy predecessor already uploaded its own record. Do not re-ship it.

    Without this gate every dispatch salvages its predecessor, including one
    that completed normally — the evidence would exist twice, and the second
    copy would sit under `salvaged/`, the directory that means "this run died".
    A recovery notice on every ordinary run trains a reader to ignore the one
    run where it is true.

    The discriminator is the same BEGIN-without-END signature the kill test
    pins, so this asserts the two directions TOGETHER: a finished predecessor is
    left alone, an unfinished one is recovered.
    """
    journal_root = tmp_path / "journal"

    # ── the healthy predecessor ──
    ws_ok = tmp_path / "ws_ok"
    ws_ok.mkdir()
    seed_dead_record(ws_ok, run_id="7001")
    (journal_root / "7001-1").mkdir(parents=True)
    (journal_root / "7001-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:05:00Z  END    patched/prepare  rc=0\n",
        encoding="utf-8",
    )
    result = run_journal("salvage", cwd=ws_ok, journal_root=journal_root, run_id="7002")
    assert result.returncode == 0, result.stderr
    assert not (ws_ok / "record" / "salvaged").exists(), (
        "a predecessor that FINISHED its phases — and therefore reached its own "
        "upload step — was filed under salvaged/ as though it had died"
    )
    # …and the zeroing still happened, which is the non-negotiable half.
    assert not (ws_ok / "record" / "prepare-patched.log").exists()

    # ── the dead one, same fixture shape, opposite verdict ──
    ws_dead = tmp_path / "ws_dead"
    ws_dead.mkdir()
    seed_dead_record(ws_dead, run_id="7003")
    (journal_root / "7003-1").mkdir(parents=True)
    (journal_root / "7003-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:09:00Z  ALIVE  patched/prepare  elapsed=540s\n",
        encoding="utf-8",
    )
    result = run_journal("salvage", cwd=ws_dead, journal_root=journal_root, run_id="7004")
    assert result.returncode == 0, result.stderr
    assert (ws_dead / "record" / "salvaged" / "record-from-run-7003" /
            "prepare-patched.log").is_file(), (
        "an UNFINISHED predecessor was not recovered — that is the whole feature"
    )


@requires_posix_shell
def test_the_finished_gate_reads_every_attempt_not_just_the_first(tmp_path):
    """A run has ATTEMPTS. Reading one of them and stopping loses the dead one.

    The provenance stamp carries `github_run_id` ONLY — no attempt number — so
    the gate globs every `<run>-*` journal directory. An earlier version ended
    its loop body with an unconditional `return 0`, which meant only the FIRST
    directory the glob yielded was ever examined. Glob order is lexical, so on
    a re-run where attempt 1 finished and attempt 2 DIED, attempt 1 was read,
    the run was declared finished, and the dead attempt's record was deleted
    unread by the salvage's own `rm -rf` — while the log printed "finished its
    phases and uploaded its own record". The gate failed destructively and
    announced the opposite.

    A re-dispatched long build is the single most likely shape here, so this is
    not an exotic case. Both orderings are asserted, because a gate that is
    correct only when the dead attempt sorts first is correct by accident:

      555-1 finished, 555-2 DIED   → recover  (the case that regressed)
      556-1 DIED,     556-2 finished → recover (any unfinished attempt counts)

    and the assertion is on the dead attempt's BYTES arriving under `salvaged/`,
    not on a message, because the orphan-journal sweep would still recover the
    journal and make a message-level assertion pass while the record was gone.
    """
    journal_root = tmp_path / "journal"

    FINISHED = (
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:05:00Z  END    patched/prepare  rc=0\n"
    )
    DIED = (
        "# header\n"
        "2026-09-01T01:00:00Z  BEGIN  patched/compile\n"
        "2026-09-01T04:59:00Z  ALIVE  patched/compile  elapsed=14340s\n"
    )

    for run_id, attempts in (("555", (FINISHED, DIED)), ("556", (DIED, FINISHED))):
        ws = tmp_path / f"ws_{run_id}"
        ws.mkdir()
        seed_dead_record(ws, run_id=run_id)
        # The bytes that matter: four hours of compile output from the attempt
        # that died, which is exactly what the destructive gate deleted unread.
        (ws / "record" / "compile-patched.log").write_text(
            "[49999/50000] LINK ./chrome\n", encoding="utf-8"
        )
        for n, body in enumerate(attempts, start=1):
            (journal_root / f"{run_id}-{n}").mkdir(parents=True)
            (journal_root / f"{run_id}-{n}" / "journal.txt").write_text(body, encoding="utf-8")

        result = run_journal(
            "salvage", cwd=ws, journal_root=journal_root, run_id=f"{run_id}9"
        )
        assert result.returncode == 0, result.stderr

        recovered = ws / "record" / "salvaged" / f"record-from-run-{run_id}" / "compile-patched.log"
        assert recovered.is_file(), (
            f"run {run_id}: one attempt finished and another DIED, and the dead "
            "attempt's compile log was not recovered — the gate stopped at the "
            "first attempt it read. Salvage said:\n"
            f"{result.stdout}"
        )
        assert "[49999/50000] LINK ./chrome" in recovered.read_text(encoding="utf-8")
        assert "nothing to salvage" not in result.stdout, (
            f"run {run_id}: salvage reported the run as finished and uploaded "
            "while a dead attempt's evidence was sitting in record/ — the "
            "message a reader gets is the opposite of the truth"
        )


@requires_posix_shell
def test_leftovers_from_several_dispatches_are_not_labelled_as_one_of_them(tmp_path):
    """`record-from-run-554` must not be a directory that also holds 555's bytes.

    `_owning_run_of_record` reads the provenance stamps to name the salvage
    directory. Reading the FIRST stamp the glob yields makes that name a
    coin-flip in exactly the mixed-leftover situation salvage exists to meet,
    and the name is a provenance CLAIM — a reader trusts it. `mixed` is the
    honest answer, and it is also the safe one for the gate: it matches no
    journal directory, so the material is kept rather than discarded.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="8101")
    (ws / "record" / "compile-patched.provenance").write_text(
        "phase=compile\ntree=patched\ngithub_run_id=8102\ngithub_run_attempt=1\n",
        encoding="utf-8",
    )

    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="8199")
    assert result.returncode == 0, result.stderr

    salvaged = ws / "record" / "salvaged"
    named = sorted(p.name for p in salvaged.iterdir() if p.is_dir() and p.name != "journals")
    assert named == ["record-from-run-mixed"], (
        "leftovers stamped by TWO different dispatches were filed under one "
        f"run's name, which is a false provenance claim. Got: {named}"
    )
    assert (salvaged / "record-from-run-mixed" / "prepare-patched.log").is_file()


@requires_posix_shell
def test_a_reformatted_journal_line_does_not_read_as_a_finished_run(tmp_path):
    """The BEGIN/END readers must key off STRUCTURE, not the writer's padding.

    Two readers count these lines, and both used to hard-code `_append`'s exact
    two-space padding. That coupling fails in the destructive direction: change
    the spacing and every journal reads as zero BEGINs and zero ENDs, so every
    dead run reads as "finished" and its record is deleted unread. A single
    space must therefore still parse.

    The other half is that the match stays ANCHORED. An ALIVE heartbeat carries
    the build's last output line verbatim, so a compiler printing the word END
    must not count as a phase that ended.
    """
    journal_root = tmp_path / "journal"

    ws = tmp_path / "ws_spaced"
    ws.mkdir()
    seed_dead_record(ws, run_id="8201")
    (journal_root / "8201-1").mkdir(parents=True)
    (journal_root / "8201-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z BEGIN patched/compile\n"
        "2026-09-01T00:09:00Z ALIVE patched/compile elapsed=540s\n",
        encoding="utf-8",
    )
    result = run_journal("salvage", cwd=ws, journal_root=journal_root, run_id="8299")
    assert result.returncode == 0, result.stderr
    assert (ws / "record" / "salvaged" / "record-from-run-8201" /
            "prepare-patched.log").is_file(), (
        "a DEAD run written with different padding was read as FINISHED and its "
        "record was deleted unread — the reader is coupled to the writer's "
        "cosmetics, and it fails in the direction that destroys evidence. "
        f"Salvage said:\n{result.stdout}"
    )

    ws2 = tmp_path / "ws_echoed"
    ws2.mkdir()
    seed_dead_record(ws2, run_id="8202")
    (journal_root / "8202-1").mkdir(parents=True)
    (journal_root / "8202-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/compile\n"
        '2026-09-01T00:09:00Z  ALIVE  patched/compile  elapsed=540s  last="[9/50] END OF FILE"\n',
        encoding="utf-8",
    )
    result = run_journal("salvage", cwd=ws2, journal_root=journal_root, run_id="8298")
    assert result.returncode == 0, result.stderr
    assert (ws2 / "record" / "salvaged" / "record-from-run-8202" /
            "prepare-patched.log").is_file(), (
        "a build that PRINTED the word END inside a heartbeat was counted as a "
        "phase that ended, so a dead run was read as finished"
    )


@requires_posix_shell
def test_a_predecessor_with_no_journal_at_all_is_still_recovered(tmp_path):
    """The default must lean toward keeping, not toward discarding.

    Two real cases have no journal: a dispatch from BEFORE this feature existed,
    and a run that died so early it never wrote one. Both are exactly when
    losing the record costs most, so "no journal" must not be read as "finished
    cleanly, discard it".
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    seed_dead_record(ws, run_id="8001")          # no journal seeded for 8001
    result = run_journal("salvage", cwd=ws, journal_root=tmp_path / "journal", run_id="8002")
    assert result.returncode == 0, result.stderr
    assert (ws / "record" / "salvaged" / "record-from-run-8001" /
            "prepare-patched.log").is_file(), (
        "a predecessor with no journal was discarded; a pre-PS-289 dispatch and "
        "a run that died before writing anything both look like this"
    )


@requires_posix_shell
def test_a_run_that_died_before_any_phase_began_is_not_read_as_finished(tmp_path):
    """The dead run that reaches the MARK milestones and never reaches BEGIN.

    ⚠️ THE FIXTURE IS THE POINT, AND IT IS WHY EVERY OTHER TEST IN THIS FILE
    MISSED THIS. Every journal fixture above is hand-written with a bare
    `"# header\\n"` first line. The REAL header `_header_if_new` writes is 19
    lines and contains a LEGEND — `#   BEGIN <tree>/<label>  the phase started`
    and a matching `#   END   …` line. `#` is a non-space character, so an
    anchor of `^[^[:space:]]+` matched both legend lines and every journal ever
    written began life at begins=1, ends=1.

    That is invisible while a real phase is running, because BEGIN and END each
    gain one and the comparison is `>`. It bites on the journal with NO real
    phase lines — and that journal is written by the exact failure this ticket
    cites twice: `ps218_record_env.sh` and `ps218_stage_patches.sh` both call
    `journal mark`, so the journal exists (with its header) before
    `ps218_build.sh` ever runs, and runs 33170172175 and 33748889046 both died
    inside `prepare` after those milestones. `1 > 1` is false, the run reads as
    FINISHED, and `rm -rf record/` destroys the environment record, the staged
    patch set and the verified borrowed control unread — while printing
    "finished its phases and uploaded its own record".

    So this fixture is built by the SCRIPT'S OWN `mark` command rather than by
    hand. On a reader/writer pair, a hand-written fixture only ever tests the
    reader against the fixture author's idea of the writer.
    """
    journal_root = tmp_path / "journal"
    ws = tmp_path / "ws"
    (ws / "record").mkdir(parents=True)

    # ── the dead run, writing its journal through the real code path ──
    for text in (
        "environment recorded (pre-prepare) -> record/environment-patched.txt",
        "staged 16 fingerprint patches into ungoogled's series",
    ):
        r = run_journal("mark", "patched", text, cwd=ws, journal_root=journal_root, run_id="9100")
        assert r.returncode == 0, r.stderr

    journal = journal_root / "9100-1" / "journal.txt"
    assert journal.is_file()
    # The header legend really is there — if this ever stops being true the test
    # below stops testing anything, so assert it rather than assume it.
    assert "#   BEGIN <tree>/<label>" in journal.read_text(encoding="utf-8")

    # …and the eight steps' worth of evidence it left in record/.
    (ws / "record" / "prepare-patched.provenance").write_text(
        "phase=prepare\ntree=patched\ngithub_run_id=9100\ngithub_run_attempt=1\n",
        encoding="utf-8",
    )
    (ws / "record" / "environment-patched.txt").write_text("nproc: 32\n", encoding="utf-8")
    (ws / "record" / "patches-staged.txt").write_text("# count: 16\n", encoding="utf-8")
    (ws / "record" / "control-borrow-verification.txt").write_text(
        "borrowed control VERIFIED\n", encoding="utf-8"
    )

    result = run_journal("salvage", cwd=ws, journal_root=journal_root, run_id="9101")
    assert result.returncode == 0, result.stderr

    assert "nothing to salvage" not in result.stdout, (
        "a run that died BEFORE its first phase began was reported as having "
        "finished its phases and uploaded its own record — the message a reader "
        f"gets is the opposite of the truth. Salvage said:\n{result.stdout}"
    )

    recovered = ws / "record" / "salvaged" / "record-from-run-9100"
    for name, needle in (
        ("environment-patched.txt", "nproc: 32"),
        ("patches-staged.txt", "# count: 16"),
        ("control-borrow-verification.txt", "borrowed control VERIFIED"),
    ):
        assert (recovered / name).is_file(), (
            f"{name} was deleted unread. This is the ticket's own scenario: "
            "eight completed steps including a verified borrowed control, and a "
            "total blackout afterwards."
        )
        assert needle in (recovered / name).read_text(encoding="utf-8")

    # The orphan sweep must reach the same verdict — it asks the same question,
    # and a journal left behind is the run's last recorded position lost.
    assert (ws / "record" / "salvaged" / "journals" / "journal-9100-1.txt").is_file(), (
        "the dead run's own journal was not carried out either, so nothing says "
        "how far it got"
    )


@requires_posix_shell
def test_the_recovery_notice_fires_once_not_on_every_later_dispatch(tmp_path):
    """A journal is carried out ONCE. Otherwise the notice is false for 30 days.

    The orphan sweep re-finds every unfinished journal on every dispatch until
    the retention prune. Without a sentinel, a dispatch whose predecessor was
    perfectly HEALTHY still creates `record/salvaged/` and still posts
    "♻️ Recovered evidence from an earlier dispatch" to its run summary — and
    the table then names `previous run: none`, because `prev_run` was reset on
    the healthy path. So the notice is simultaneously false on the common path
    and uninformative on the path where it is true, which is precisely the
    signal-destroying behaviour the finished-gate exists to prevent.

    `test_no_recovery_notice_is_written_when_nothing_was_salvaged` cannot catch
    this: its journal root is empty, so it never exercises the orphan path.
    """
    journal_root = tmp_path / "journal"

    # ── dispatch 1: predecessor 9200 DIED. The notice SHOULD fire. ──
    ws1 = tmp_path / "ws1"
    ws1.mkdir()
    seed_dead_record(ws1, run_id="9200")
    (journal_root / "9200-1").mkdir(parents=True)
    (journal_root / "9200-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
        "2026-09-01T00:09:00Z  ALIVE  patched/prepare  elapsed=540s\n",
        encoding="utf-8",
    )
    s1 = tmp_path / "s1.md"
    r = run_journal(
        "salvage", cwd=ws1, journal_root=journal_root, run_id="9201",
        GITHUB_STEP_SUMMARY=str(s1),
    )
    assert r.returncode == 0, r.stderr
    text1 = s1.read_text(encoding="utf-8")
    assert "Recovered evidence" in text1, "the real recovery was not announced"
    assert "9200" in text1, "the notice does not name the run whose evidence was recovered"

    # ── dispatches 2 and 3: predecessors were HEALTHY. Silence, both times. ──
    for n, (prev, run) in enumerate(((9201, 9202), (9202, 9203)), start=2):
        ws = tmp_path / f"ws{n}"
        (ws / "record").mkdir(parents=True)
        (ws / "record" / "prepare-patched.provenance").write_text(
            f"phase=prepare\ntree=patched\ngithub_run_id={prev}\ngithub_run_attempt=1\n",
            encoding="utf-8",
        )
        (journal_root / f"{prev}-1").mkdir(parents=True, exist_ok=True)
        (journal_root / f"{prev}-1" / "journal.txt").write_text(
            "# header\n"
            "2026-09-01T00:00:00Z  BEGIN  patched/prepare\n"
            "2026-09-01T00:05:00Z  END    patched/prepare  rc=0\n",
            encoding="utf-8",
        )
        summary = tmp_path / f"s{n}.md"
        summary.write_text("", encoding="utf-8")
        r = run_journal(
            "salvage", cwd=ws, journal_root=journal_root, run_id=str(run),
            GITHUB_STEP_SUMMARY=str(summary),
        )
        assert r.returncode == 0, r.stderr
        assert "Recovered evidence" not in summary.read_text(encoding="utf-8"), (
            f"dispatch {run} announced a recovery although its predecessor "
            f"{prev} was healthy — the dead run 9200's journal was carried out "
            "again. That notice repeats until the 30-day prune and teaches a "
            "reader to ignore the one that is true."
        )
        assert not (ws / "record" / "salvaged").exists(), (
            f"dispatch {run} created record/salvaged/ although nothing new was "
            "recovered, so its artifact is filed as containing a dead run's "
            "evidence when it does not"
        )


@requires_posix_shell
def test_the_notice_never_reports_a_recovery_from_a_run_called_none(tmp_path):
    """When only a JOURNAL is recovered, the table must not say `previous run: none`.

    `prev_run` is reset to `none` on the healthy-predecessor and own-material
    paths. Reusing it in the summary tells a reader a recovery happened from a
    run called "none" with the dead run's identity nowhere on the page — the
    notice fires, and following it leads nowhere.
    """
    journal_root = tmp_path / "journal"
    # A dead run whose record/ is already gone; only its journal survives.
    (journal_root / "9300-1").mkdir(parents=True)
    (journal_root / "9300-1" / "journal.txt").write_text(
        "# header\n"
        "2026-09-01T00:00:00Z  BEGIN  patched/compile\n"
        '2026-09-01T03:00:00Z  ALIVE  patched/compile  elapsed=10800s  last="[41200/50000] CXX gpu_shim.o"\n',
        encoding="utf-8",
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    summary = tmp_path / "summary.md"

    r = run_journal(
        "salvage", cwd=ws, journal_root=journal_root, run_id="9301",
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert r.returncode == 0, r.stderr

    text = summary.read_text(encoding="utf-8")
    assert "Recovered evidence" in text
    assert "`none`" not in text, (
        "the summary reports a recovery from a run called `none`; `prev_run` "
        f"was reused where it does not apply. Summary was:\n{text}"
    )
    assert "9300-1" in text, (
        "the notice fired but never names the dead run whose journal it "
        "recovered, so a reader following it learns nothing"
    )
    assert "gpu_shim.o" in text, "the last recorded position is missing"


@requires_posix_shell
def test_salvaged_material_does_not_nest_one_level_deeper_every_dispatch(tmp_path):
    """`record/salvaged/` must not be carried into the NEXT salvage.

    `cp -R record/.` copies `salvaged/` too, so consecutive dispatches that each
    leave an unprovenanced `record/` behind produce
    `salvaged/record-from-run-unknown/salvaged/record-from-run-unknown/…`. That
    is not exotic: the PS-244 borrowed-control refusal exits before any
    provenance stamp is written, so a repeated refusal is a DESIGNED outcome.
    Nothing is lost by excluding it — the earlier material already rode out on
    the artifact of the dispatch that recovered it.
    """
    journal_root = tmp_path / "journal"
    ws = tmp_path / "ws"
    ws.mkdir()

    for run in ("9401", "9402", "9403", "9404"):
        (ws / "record").mkdir(exist_ok=True)
        (ws / "record" / f"refusal-{run}.txt").write_text(
            "the borrowed control was REFUSED\n", encoding="utf-8"
        )
        r = run_journal("salvage", cwd=ws, journal_root=journal_root, run_id=run)
        assert r.returncode == 0, r.stderr

    nested = ws / "record" / "salvaged" / "record-from-run-unknown" / "salvaged"
    assert not nested.exists(), (
        "salvaged material was carried into the next salvage, so the recovered "
        "tree nests one level deeper on every dispatch and becomes unreadable"
    )
    # …and the LAST dispatch's own leftovers ARE still recovered: excluding
    # `salvaged/` from the staging copy must not weaken the recovery itself.
    assert (ws / "record" / "salvaged" / "record-from-run-unknown" /
            "refusal-9404.txt").is_file()


# ─────────────────────────────────────────────────────────────────────────────
# The workflow wiring — the scripts are useless if no step invokes them
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("job_name", ["unmodified", "patched"])
def test_both_jobs_salvage_before_they_zero(workflow, job_name):
    """The salvage step replaced the bare `rm -rf record` in BOTH jobs.

    A `rm -rf record` surviving anywhere would delete a dead dispatch's evidence
    unread — which is the pre-PS-289 behaviour, restored by omission.
    """
    steps = workflow["jobs"][job_name]["steps"]
    runs = [str(s.get("run") or "") for s in steps]

    assert any("ps289_journal.sh salvage" in r for r in runs), (
        f"job `{job_name}` never invokes the salvage step, so a dead dispatch's "
        "record/ is still deleted unread"
    )
    for r in runs:
        assert "rm -rf record" not in r, (
            f"job `{job_name}` still deletes record/ directly. That is the "
            "behaviour PS-289 replaces: it destroys the only copy of a dead "
            "dispatch's evidence before anything has read it."
        )


@pytest.mark.parametrize("job_name", ["unmodified", "patched"])
def test_the_persona_checkout_precedes_the_salvage_that_needs_it(workflow, job_name):
    """Ordering, asserted rather than hoped for.

    The salvage script lives in THIS repository, so it cannot run before the
    persona checkout. Getting this backwards fails at runtime on a step that
    every dispatch runs — and only on the self-hosted runner, where nobody sees
    it until a build is already queued.
    """
    steps = workflow["jobs"][job_name]["steps"]
    checkout_idx = next(
        i for i, s in enumerate(steps)
        if "checkout" in str(s.get("uses", "")) and (s.get("with") or {}).get("path") == "persona"
    )
    salvage_idx = next(
        i for i, s in enumerate(steps) if "ps289_journal.sh salvage" in str(s.get("run") or "")
    )
    assert checkout_idx < salvage_idx, (
        f"job `{job_name}` runs the salvage step (index {salvage_idx}) before "
        f"checking out persona (index {checkout_idx}); the script does not exist yet"
    )


@pytest.mark.parametrize("job_name", ["unmodified", "patched"])
def test_the_record_upload_still_ships_everything_including_salvaged(workflow, job_name):
    """Salvaged evidence reaches GitHub through the EXISTING `if: always()` upload.

    The whole recovery route is: dead run's bytes → next dispatch's
    `record/salvaged/` → that dispatch's record artifact. If the upload ever
    narrowed from `record/` to named files, the salvage would still run and
    still produce nothing anybody outside the owner's machine can read.
    """
    steps = workflow["jobs"][job_name]["steps"]
    uploads = [
        s for s in steps
        if "upload-artifact" in str(s.get("uses", ""))
        and "record/" in str((s.get("with") or {}).get("path", ""))
    ]
    assert uploads, f"job `{job_name}` has no upload step shipping record/"
    for step in uploads:
        assert step.get("if") == "always()", (
            "the record upload must stay `if: always()` — it is what carries "
            "salvaged evidence out on a run that itself failed"
        )
