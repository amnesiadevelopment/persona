"""PS-218: the trial-build workflow cannot damage the owner's machine or the CI gate.

This workflow is unlike every other one in this repo: it runs on the OWNER'S
PERSONAL WORKSTATION. That makes two ordinary-looking mistakes expensive in ways
a reviewer cannot see by reading the YAML quickly, and both are pinned here.

1. A `push:` trigger. A self-hosted runner picks up ANY workflow whose labels it
   matches. A build triggered on push would start a multi-hour Chromium compile
   on his workstation on every commit, pegging 32 cores, repeatedly, with nobody
   asking. The planner called this "the one that damages his machine".

2. The wrong runner label. `persona-build-linux` was recommended in one comment
   and CORRECTED to `persona-build` in a later one. A job targeting a label no
   runner carries queues FOREVER WITH NO ERROR — indistinguishable from a
   workflow that was never triggered. That is a silent failure, so it gets a
   test rather than a comment.

The third assertion is about blast radius rather than the machine: this workflow
must stay OUT of ci.yml, so the owner closing his laptop cannot fail the checks
that gate every other ticket in the project.

These are cheap, structural assertions over the YAML. They are NOT a claim that
the build works — nothing here compiles anything, and this suite cannot: the
compile happens on hardware this container does not have and must never attempt
(8 cores / 15 GB here, and memory is the binding constraint). What these tests
pin is the set of properties that must hold BEFORE anyone presses the button.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"

# The label the owner actually registered. The earlier recommendation
# (`persona-build-linux`) is the WRONG one and is asserted against explicitly
# below, because copying it out of the older comment is the exact mistake the
# correction was issued to prevent.
RUNNER_LABEL = "persona-build"
WRONG_LABEL = "persona-build-linux"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def triggers(workflow: dict) -> dict:
    # PyYAML parses a bare `on:` key as the BOOLEAN True (the YAML 1.1 truthy
    # rule), not as the string "on". Handling both is not defensive padding: a
    # test that looked only for the string would silently find nothing and pass
    # while asserting about an empty dict — the failure mode this whole file
    # exists to catch, reproduced inside the test itself.
    return workflow.get(True, workflow.get("on"))


def test_workflow_never_triggers_on_push_or_pull_request(triggers):
    """The trigger that would peg his workstation on every commit.

    Asserts the ABSENCE of push/pull_request, and that dispatch is the only way
    in — so adding a push trigger later fails this test rather than quietly
    costing the owner a multi-hour build per commit.
    """
    assert "push" not in triggers, (
        "engine-trial-build must NEVER trigger on push: it would start a "
        "multi-hour Chromium compile on the owner's personal workstation on "
        "every commit."
    )
    assert "pull_request" not in triggers, (
        "engine-trial-build must NEVER trigger on pull_request, for the same reason."
    )
    assert "schedule" not in triggers, (
        "engine-trial-build must not run on a schedule: the owner's machine may "
        "be asleep, and an unattended multi-hour build was never asked for."
    )
    assert set(triggers) == {"workflow_dispatch"}, (
        f"workflow_dispatch must be the ONLY trigger; found {sorted(map(str, triggers))}"
    )


def test_every_job_targets_the_label_the_owner_registered(workflow):
    """The wrong label queues forever with no error — so it is asserted, not trusted."""
    jobs = workflow["jobs"]
    assert jobs, "workflow declares no jobs"

    for name, job in jobs.items():
        runs_on = job["runs-on"]
        assert isinstance(runs_on, list), (
            f"job {name!r}: runs-on must be a label list targeting the self-hosted runner"
        )
        assert "self-hosted" in runs_on, f"job {name!r} does not target the self-hosted runner"
        assert RUNNER_LABEL in runs_on, (
            f"job {name!r} must carry the {RUNNER_LABEL!r} label the owner registered"
        )
        assert WRONG_LABEL not in runs_on, (
            f"job {name!r} uses {WRONG_LABEL!r}. No runner carries that label, so the "
            "job would queue FOREVER WITH NO ERROR, which looks exactly like a "
            "workflow that was never triggered."
        )


def test_patched_build_cannot_run_before_its_own_control(workflow):
    """Step 1 gates step 2 through `needs:`, so the ordering is structural.

    The instrument check is not a formality: a compile failure on a patched tree
    has two possible causes — our patches, or an environment that cannot build
    Chromium at all — and one run cannot separate them. Enforcing that ordering
    in the job graph means it holds whether or not anyone remembers it.
    """
    jobs = workflow["jobs"]
    assert "unmodified" in jobs, "the instrument-check job is missing"
    assert "patched" in jobs, "the patched-tree job is missing"

    needs = jobs["patched"].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "unmodified" in needs, (
        "the patched build MUST declare `needs: unmodified` so it cannot run "
        "ahead of its own control."
    )
    assert not jobs["unmodified"].get("needs"), (
        "the instrument check must not depend on anything — it establishes the baseline."
    )


def test_trial_build_is_separate_from_the_ci_gate():
    """An offline workstation must not fail the checks that gate every other ticket."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "self-hosted" not in ci, (
        "ci.yml must not reference a self-hosted runner: if the owner's machine is "
        "off, every merge in the project would block."
    )
    assert "engine-trial-build" not in ci, (
        "the trial build must not be wired into the CI gate."
    )


def test_all_sixteen_fingerprint_patches_are_vendored():
    """The build must measure OUR 16 — a build of some other number measures nothing.

    The ticket forbids making the build succeed by quietly dropping a patch, so
    the count is pinned here as well as guarded at staging time. `000` is called
    out because it defines the command-line switches every later patch reads:
    without it the others compile against symbols that do not exist.
    """
    patches = sorted(p.name for p in PATCH_DIR.glob("*.patch"))
    assert len(patches) == 16, f"expected 16 fingerprint patches, found {len(patches)}: {patches}"
    assert patches[0].startswith("000-"), (
        "000-add-fingerprint-switches must sort first: it declares the switches "
        "every later patch reads."
    )
    for p in PATCH_DIR.glob("*.patch"):
        assert p.stat().st_size > 0, f"{p.name} is empty"


def test_the_gpu_patch_still_hooks_only_the_two_getparameter_cases():
    """Pins the premise step 3 rests on, so a silent upstream change is visible.

    PS-218 states the GPU patch hooks EXACTLY TWO switch cases in
    `WebGLRenderingContextBase::getParameter`, and that the spoofed identity is
    read from PROCESS-GLOBAL command-line state rather than per-realm context —
    which is why covering another realm needs no plumbing to carry identity
    across a realm boundary.

    That second half is the load-bearing claim, and it is what this asserts: the
    hook reads `base::CommandLine::ForCurrentProcess()`. If a future rebase made
    the spoof context-dependent, this fails and the step-3 reasoning must be
    re-derived rather than inherited.
    """
    gpu = (PATCH_DIR / "011-gpu-info.patch").read_text(encoding="utf-8", errors="replace")

    assert "kUnmaskedRendererWebgl" in gpu
    assert "kUnmaskedVendorWebgl" in gpu
    assert "base::CommandLine::ForCurrentProcess()" in gpu, (
        "the spoof must read process-global command-line state; step 3's "
        "'no new plumbing needed' reasoning depends on it."
    )

    # Zero references to the service-worker realm in ANY of the 16 — the gap
    # the ticket describes. Asserted across the whole set rather than one file
    # so a patch gaining coverage elsewhere is noticed.
    for name in ("ServiceWorkerGlobalScope", "service_worker"):
        hits = [p.name for p in PATCH_DIR.glob("*.patch")
                if name in p.read_text(encoding="utf-8", errors="replace")]
        assert not hits, f"unexpected {name!r} reference in {hits} — re-derive PS-218 step 3"


# ─────────────────────────────────────────────────────────────────────────────
# The OOM-at-link path, pinned because it is the one that breaks INVISIBLY
# ─────────────────────────────────────────────────────────────────────────────
# Everything above asserts over static YAML and patch files. The two below run
# the attribution script for real, because the defect they pin was introduced by
# an otherwise-correct fix and every static check stayed green while it shipped.
#
# The ticket names memory-at-link as the KNOWN failure mode of this build. An
# OOM kill emits ninja `FAILED:` edges and a `Killed` — and NO clang
# diagnostics. So the single most likely real failure is precisely the shape
# that takes the script's no-diagnostics early exit. When that exit sat above
# the edge-reporting section, a build that died reported its failing edges
# NOWHERE: the operator holding a red build read "no compile errors found".
#
# That is a worse outcome than the wrong number it replaced, and no assertion in
# this file could see it. Hence these.

ATTRIBUTE_SH = REPO_ROOT / "scripts" / "ps218_attribute.sh"

# A log with ninja edges and a `Killed`, and deliberately no clang diagnostics.
OOM_AT_LINK_LOG = """\
[49998/50000] CXX obj/content/browser/browser.o
FAILED: obj/third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.o
FAILED: obj/chrome/chrome
../../build/toolchain/gcc_link_wrapper.py: line 1: 12345 Killed
ninja: build stopped: subcommand failed.
"""


def _run_attribution(tmp_path, log_text: str):
    """Run the real script against a synthetic compile log."""
    import subprocess

    (tmp_path / "record").mkdir()
    (tmp_path / "record" / "compile-patched.log").write_text(log_text, encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(ATTRIBUTE_SH)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "UCPL_DIR": str(tmp_path),
            "PATCH_DIR": str(PATCH_DIR),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def test_a_build_that_died_at_link_still_reports_its_failing_edges(tmp_path):
    """The known failure mode must not take a silent path.

    A build killed by the OOM reaper produces edges and no diagnostics. Before
    this was pinned, that log produced the sentence "No compile errors found"
    and nothing else — no edge list, no count, no summary — for a build that
    had in fact failed.
    """
    out = _run_attribution(tmp_path, OOM_AT_LINK_LOG)

    # The edges are the ONLY evidence of failure in this log. They must appear.
    assert "obj/chrome/chrome" in out, (
        "the failing link edge is absent from the report: a build that died at "
        "link would be reported as having no problems."
    )
    assert "failing build edges (ninja `FAILED:`): 2" in out, (
        "the failing-edge COUNT must be reported on the no-diagnostics path"
    )

    # The summary must still print — it is where a reader looks for the totals.
    assert "== summary ==" in out, (
        "the summary block must not be skipped when a build dies at link"
    )

    # And the claim must be the true one. "No compile errors" beside two failing
    # edges reads as a green to someone holding a red.
    assert "No clang diagnostics found" in out
    assert "No compile errors found" not in out, (
        "a build with failing edges did NOT compile cleanly; the honest claim is "
        "the narrower 'no clang diagnostics'."
    )


def test_a_clean_log_is_not_described_as_a_crash(tmp_path):
    """The opposite zero-diagnostic case must read differently.

    No diagnostics AND no edges is a log recording no compile failure. Telling
    that reader to go hunt for an OOM kill manufactures a suspicion the evidence
    does not support — the same class of error as the one above, inverted.
    """
    out = _run_attribution(tmp_path, "[50000/50000] LINK ./chrome\nninja: build completed successfully.\n")

    assert "failing build edges (ninja `FAILED:`): 0" in out
    assert "OOM kill or timeout" not in out, (
        "a log with no failing edges must not be described as a crash"
    )
    # Matched on a single line: the script's prose is hard-wrapped, so
    # "records no compile failure" spans a newline and would never match.
    # Asserting the wrapped fragment keeps this test about the CLAIM rather
    # than about where the paragraph happens to break.
    assert "records no compile" in out


# ─────────────────────────────────────────────────────────────────────────────
# Toolchain/driver diagnostics: the SAME defect as `FAILED:`, different shape
# ─────────────────────────────────────────────────────────────────────────────
# The two tests above pin the PURE OOM shape: ninja edges plus a bare `Killed`,
# with no `: error: ` line anywhere. That is not the only shape an OOM at link
# takes, and it is not the common one.
#
# When the reaper kills the linker subprocess, the clang DRIVER reports it, and
# those lines DO match `extract_errors`:
#
#   clang++: error: unable to execute command: Killed
#   clang++: error: linker command failed due to signal (use -v to see invocation)
#   ld.lld: error: out of memory
#
# They carry no file:line:col, so `error_file()` — which takes whitespace field
# 1 — reduces them to the TOOL NAME (`clang++:`), which can never join the
# `+++ b/` source-path ownership map. Without a separate population they fall to
# UNATTRIBUTED, whose text tells the reader an entry there is "a REAL finding,
# not noise" about our 16 patches. It is nothing of the kind: it is the build
# machine running out of memory, which the ticket names as the KNOWN failure
# mode and explicitly treats as a property of the hardware.
#
# This is the same error as attributing `FAILED:` lines, arriving through a
# different line shape, and it fires in exactly the case most likely to happen.

OOM_WITH_DRIVER_DIAGNOSTICS_LOG = """\
[49998/50000] SOLINK ./libblink_core.so
FAILED: ./libblink_core.so
clang++: error: unable to execute command: Killed
clang++: error: linker command failed due to signal (use -v to see invocation)
ninja: build stopped: subcommand failed.
"""


def test_driver_diagnostics_are_not_attributed_as_findings_about_our_patches(tmp_path):
    """An OOM at link must not be reported as evidence against the patch layer."""
    out = _run_attribution(tmp_path, OOM_WITH_DRIVER_DIAGNOSTICS_LOG)

    # THE DEFECT: these lines used to land in UNATTRIBUTED, which the script
    # tells the reader is "a REAL finding, not noise".
    assert "unattributed (file not touched by us): 0" in out, (
        "a `clang++: error:` line names a TOOL, not a source file. Counting it "
        "as UNATTRIBUTED asserts it is a real finding about our 16 patches, "
        "when it is the signature of the machine running out of memory."
    )
    assert "toolchain/driver (name no source file, not attributed): 2" in out, (
        "driver/linker diagnostics must be counted as their own population"
    )

    # They must still be VISIBLE — counted separately, never dropped. Silence
    # about a failed build is the defect the round before this one fixed.
    assert "Killed" in out, "the driver diagnostic must still be reported"

    # The three attribution counts describe the ATTRIBUTABLE population only.
    assert "of which attributable (name a source file): 0" in out


def test_the_error_heading_never_stands_empty(tmp_path):
    """A bare heading reads as 'no errors' to someone holding a failed build.

    When every diagnostic is a driver line, nothing prints under the errors
    heading. The report must say why rather than leaving a silence that looks
    like a pass.
    """
    out = _run_attribution(tmp_path, OOM_WITH_DRIVER_DIAGNOSTICS_LOG)

    body = out.split("== compile errors in the PATCHED tree ==", 1)[1]
    body = body.split("== failing build edges", 1)[0]
    assert body.strip(), (
        "the errors heading printed with nothing under it: a reader sees a bare "
        "heading and reads it as a clean compile."
    )
    assert "NOT the same as a clean compile" in body


def test_a_real_source_diagnostic_is_still_attributed(tmp_path):
    """Regression guard: the toolchain split must not swallow real errors.

    The whole point of the script is attributing a source diagnostic to the
    patch owning that file. A discriminator that rejected real diagnostics
    would make the instrument useless while every OOM test above still passed.
    """
    log = (
        "FAILED: obj/third_party/blink/renderer/modules/webgl/"
        "webgl_rendering_context_base.o\n"
        "../../third_party/blink/renderer/modules/webgl/"
        "webgl_rendering_context_base.cc:1234:5: error: no matching function\n"
    )
    out = _run_attribution(tmp_path, log)

    assert "[ATTRIBUTED ->" in out, (
        "a normal `path:line:col: error:` diagnostic must still be attributed "
        "to the patch that owns the file"
    )
    assert "of which attributable (name a source file): 1" in out
    assert "toolchain/driver (name no source file, not attributed): 0" in out
