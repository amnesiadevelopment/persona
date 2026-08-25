"""Tests for the runtime-fingerprint baseline wiring in .github/workflows/release.yml (PS-173).

WHAT THIS EXISTS TO CLOSE. `requires_full_install` decides whether a Windows
update swaps a 5.02 MiB app.zip (seconds) or re-runs the 64.84 MiB Inno
installer (the owner measured 40+s). It is computed in `build-windows` by
comparing the build's runtime fingerprint against the one COMMITTED in
runtime-fingerprint.txt. That comparison was always correct. Its INPUT was a
hand-maintained file — "Bump runtime-fingerprint.txt only when the runtime
actually changes" — and nothing enforced the bump.

So one dependency change did not cost ONE full install. It cost EVERY
subsequent release a full install until a human remembered a file. Measured on
the published manifests at the time of writing:

    v2.9.13  15f928a3  requires_full_install=False   <- == the committed baseline
    v2.9.14  5c35e3f7  True
    v2.9.15  40ab98da  True
    v2.9.16  40ab98da  True     <- SAME fingerprint as v2.9.15, still full
    v2.9.17  f2cfee90  True
    v3.0.0   c91f182c  True

v2.9.16 is the proof: `git diff v2.9.15..v2.9.16 -- requirements.txt
pyproject.toml` is EMPTY and its fingerprint is byte-identical to v2.9.15's, yet
it shipped the full installer — a 12.9x payload for a runtime that provably did
not move.

This is the workflow's THIRD stale-fingerprint incident (:811 hashed build bytes
and pinned the flag true forever; #238 read prevFp from the releases API and
raced the publish). Both earlier fixes corrected how the fingerprint is COMPUTED
or FETCHED; neither closed the case where the mechanism is right and the
baseline is never refreshed. Prose in a workflow comment does not enforce that,
so what follows pins it.

These tests read the YAML as data. They deliberately do NOT run the workflow —
that needs a real tag, a Windows runner and a publish token, none of which exist
in this container (the same constraint test_engine_autoupdate_workflow.py
documents).
"""

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
BASELINE = ROOT / "runtime-fingerprint.txt"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def publish_steps(workflow) -> list:
    return workflow["jobs"]["publish"]["steps"]


@pytest.fixture(scope="module")
def refresh_step(publish_steps) -> dict:
    for step in publish_steps:
        name = (step.get("name") or "").lower()
        if "fingerprint" in name and "baseline" in name:
            return step
    raise AssertionError(
        "no baseline-refresh step in the publish job — the baseline is "
        "hand-maintained again and will go stale, pinning requires_full_install "
        "true for every release after the next dependency change"
    )


def _strip_comments(text: str) -> str:
    """Drop whole-line `#` comments.

    These workflows carry long rationale comments that NAME the mechanisms they
    deliberately removed — the prevFp block explains at length why reading
    `releases/latest` was abandoned (#238). A naive scan for that string finds
    the explanation and reports the mechanism as present, which is a bug in the
    reader rather than in the workflow. Assert on CODE, not on prose.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


@pytest.fixture(scope="module")
def windows_run_text(workflow) -> str:
    """Every `run:` body in build-windows, line-continuations folded."""
    runs = "\n".join(
        s.get("run") or "" for s in workflow["jobs"]["build-windows"]["steps"]
    )
    return re.sub(r"\\\n\s*", " ", runs)


@pytest.fixture(scope="module")
def windows_code(windows_run_text) -> str:
    """build-windows `run:` bodies with the rationale comments removed."""
    return _strip_comments(windows_run_text)


# --- the safety condition is NOT weakened ------------------------------------
# The ticket is explicit that safety outranks speed: swapping code without the
# libraries it imports produces an app that starts and then fails somewhere the
# user does not expect. Making the fast path fire MORE OFTEN by relaxing what
# counts as a dependency change is out of scope. These pin that it wasn't.


def test_the_fingerprint_still_covers_every_runtime_input(windows_run_text):
    """requirements.txt + flet + Python + Flutter + the invisible_* pins.

    Dropping any one of them makes a real dependency change invisible and lets
    the fast path swap code onto a runtime that moved underneath it — the #405
    driver/engine split, which is the failure class this flag exists to prevent.
    """
    assert '$fpInput = "$reqHash|$fletVer|$pyVer|$fluVer|$invPins"' in windows_run_text
    assert "Get-FileHash requirements.txt -Algorithm SHA256" in windows_run_text
    assert "pip show flet" in windows_run_text
    assert "python --version" in windows_run_text
    assert "flutter --version" in windows_run_text
    # the engine/driver pins live in pyproject, not requirements.txt
    assert "invisible_(playwright" in windows_run_text


def test_a_differing_runtime_fingerprint_still_forces_a_full_install(windows_run_text):
    """The decision itself is untouched: empty baseline OR any difference => full.

    PS-173 fixes the stale INPUT to this comparison, never the comparison.
    """
    assert (
        '$requiresFull = ($prevFp -eq "") -or ($prevFp -ne $runtimeFp)'
        in windows_run_text
    )


def test_the_baseline_is_still_read_from_the_committed_file_not_the_api(
    windows_code,
):
    """#238: reading prevFp from releases/latest raced the publish and GitHub's
    cache, so a build read an empty/stale prev fingerprint and pinned the flag
    true. The committed file is deterministic. Do not reintroduce the API read.
    """
    assert "Test-Path runtime-fingerprint.txt" in windows_code
    assert "releases/latest" not in windows_code


# --- the baseline is refreshed automatically ---------------------------------


def test_the_refresh_writes_the_baseline_file(refresh_step):
    run = refresh_step.get("run") or ""
    assert "runtime-fingerprint.txt" in run
    assert re.search(r">\s*runtime-fingerprint\.txt", run), (
        "the step must WRITE the baseline, not merely mention it"
    )


def test_the_refresh_takes_the_fingerprint_from_the_published_manifest(refresh_step):
    """Not a recomputation and not an API read.

    Recomputing here is impossible: the inputs include `pip show flet`,
    `python --version` and `flutter --version` as resolved ON THE WINDOWS
    RUNNER, which this ubuntu job cannot reproduce. Re-reading the release over
    the API is the #238 race. The manifest this run just published is already on
    disk and is the authoritative value.
    """
    run = refresh_step.get("run") or ""
    env = refresh_step.get("env") or {}
    assert "update-manifest.json" in (env.get("MANIFEST", "") + run)
    assert "runtime_fingerprint" in run
    # must not re-derive the runtime inputs on the wrong OS
    for forbidden in ("pip show flet", "flutter --version", "Get-FileHash"):
        assert forbidden not in run, f"{forbidden!r} cannot be resolved in publish"
    # must not reintroduce the #238 API read
    assert "releases/latest" not in run
    assert "api.github.com" not in run


def test_a_missing_or_malformed_fingerprint_fails_instead_of_emptying_the_baseline(
    refresh_step,
):
    """Fail CLOSED. An empty baseline makes `$prevFp -eq ""` true, which forces
    requires_full_install true for EVERY future release — the exact defect being
    fixed. Writing a blank file would turn this fix into the bug.
    """
    run = refresh_step.get("run") or ""
    assert "::error::" in run, "must fail loudly on a bad fingerprint"
    assert '"${#fp}" -eq 64' in run, "must validate the sha256 length"
    # the write must be guarded by the validation, not unconditional
    guard = run.index("${#fp}")
    write = run.index("> runtime-fingerprint.txt")
    assert guard < write, "the fingerprint is written before it is validated"


def test_the_refresh_is_a_no_op_when_the_baseline_is_already_current(refresh_step):
    """A release that changed no dependency must not push an empty commit on
    every tag — the baseline only moves when the runtime actually moved."""
    run = refresh_step.get("run") or ""
    assert '[ "$prev" = "$fp" ]' in run
    assert "nothing to commit" in run


def test_the_refresh_only_runs_for_a_real_tagged_release(refresh_step, workflow):
    """A workflow_dispatch build publishes nothing; refreshing the baseline from
    one would move it without a release to match."""
    job_if = workflow["jobs"]["publish"].get("if", "")
    step_if = refresh_step.get("if", "")
    assert "refs/tags/" in (job_if + step_if)


def test_pushing_the_baseline_cannot_retrigger_a_release(workflow):
    """The refresh commits to main. release.yml must stay tag-only, or every
    release would trigger a release."""
    on = workflow[True] if True in workflow else workflow["on"]
    assert "branches" not in (on.get("push") or {}), (
        "release.yml must not run on branch pushes — the baseline commit would loop"
    )
    assert on["push"]["tags"] == ["v*"]


def test_a_failed_baseline_push_does_not_fail_a_published_release(refresh_step):
    """The assets are already live and correct at this point. The cost of a
    missed refresh is one more full-install release, which the next run retries
    — far cheaper than reporting a good release as failed."""
    run = refresh_step.get("run") or ""
    assert "::warning::" in run
    tail = run[run.index("git push origin main") :]
    assert "::error::" not in tail


# --- the committed baseline itself -------------------------------------------


def test_the_committed_baseline_is_a_well_formed_sha256():
    """A malformed baseline can never equal a computed fingerprint, so it would
    silently pin requires_full_install true forever."""
    assert BASELINE.exists(), "runtime-fingerprint.txt is missing — prevFp would be ''"
    value = BASELINE.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", value), f"not a sha256: {value!r}"
