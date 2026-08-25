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


class _StrictLoader(yaml.SafeLoader):
    """A SafeLoader that REFUSES a mapping with a repeated key.

    yaml.safe_load is last-key-wins, so a step that declares `env:` twice parses
    happily and silently drops everything in the first block. That is not
    hypothetical: the first cut of the refresh step below did exactly that, and
    the dropped variable was MANIFEST — the one the step reads on its second
    line under `set -euo pipefail`, so the step died with "unbound variable"
    ABOVE its own ::warning:: retry net and would have hard-failed the publish
    job on every tagged release, after the assets were already live.

    Every test in this file reads a strictly-parsed document, so the next
    duplicate `env:`/`with:`/`run:` anywhere in this workflow is a test failure
    rather than a silently-dropped key.
    """


def _no_duplicate_keys(loader, node, deep=False):
    seen: dict = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise AssertionError(
                f"duplicate key {key!r} in {WORKFLOW.name} at line "
                f"{key_node.start_mark.line + 1} (first seen at line {seen[key]}) — "
                "YAML is last-key-wins, so the earlier block is silently dropped"
            )
        seen[key] = key_node.start_mark.line + 1
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), _StrictLoader)


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
    # Assert on the PARSED env, not on `env + run`. The concatenated form is
    # satisfied by the step's own failure-message prose
    # (`::error::update-manifest.json missing`), so it passed green against a
    # step whose MANIFEST had been dropped by a duplicate `env:` key and which
    # could not execute at all. Prose the code itself generated must never be
    # allowed to stand in for the wiring (PS-11).
    assert env.get("MANIFEST", "").endswith("update-manifest.json"), (
        "the step must locate the published manifest through env.MANIFEST; "
        f"got {env.get('MANIFEST')!r}"
    )
    assert "$MANIFEST" in run, "the body must actually read the manifest it was given"
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


def test_no_git_failure_before_the_warning_net_can_red_a_published_release(
    refresh_step,
):
    """The step runs under `set -euo pipefail`, AFTER the release is published.

    The previous version of this test sliced from `git push origin main`
    onward and asserted no `::error::` in the tail. That inspects only the
    region that is ALREADY guarded by the retry loop — every command that can
    actually red a published release sits ABOVE the slice point and was
    structurally invisible to it. It passed green while `git clone`, `git
    config`, `git add` and `git commit` all exited 128 under `set -e` and
    failed the publish job (measured, by running the extracted body against a
    stubbed git).

    That matters because of the ORDERING: this is step [3] of the publish job
    and step [2] already published the GitHub Release. A network blip, a
    token hiccup or a runner DNS flake during the clone would report a release
    whose assets are live and correct as a FAILED build.

    So pin the property the step's own closing comment promises — that no git
    command before the warning net can abort — rather than a symptom of it.
    """
    # Fold line continuations first: the guards are written as
    #     git add runtime-fingerprint.txt \
    #       || { echo "::warning::..."; exit 0; }
    # and a naive per-line scan would see a bare `git add ...` and report a
    # guarded command as unguarded.
    code = re.sub(r"\\\n\s*", " ", _strip_comments(refresh_step.get("run") or ""))
    assert "set -euo pipefail" in code, "this test's premise is `set -e`"

    head = code[: code.index("git push origin main")]
    unguarded = [
        line.strip()
        for line in head.splitlines()
        # `if ! git clone ...; then` handles its own failure; `||` degrades.
        if line.strip().startswith("git ")
        and "||" not in line
        and not line.strip().startswith("if !")
    ]
    assert not unguarded, (
        "these run under `set -e` BEFORE the ::warning:: net, so any one of "
        "them failing reds a release that already published successfully: "
        f"{unguarded}"
    )


def test_a_bad_fingerprint_still_fails_closed(refresh_step):
    """The counterweight to the test above: making git failures harmless must
    NOT make everything harmless.

    A missing/malformed fingerprint must still `exit 1` loudly. Writing an
    empty baseline sets prevFp == "" for the next build, which forces
    requires_full_install true for EVERY future release (:855) — the exact
    failure this ticket exists to fix, so it must never be silently swallowed.
    This is why the fix is explicit per-command guards rather than
    `continue-on-error: true` on the step, which would swallow these too.
    """
    code = _strip_comments(refresh_step.get("run") or "")

    # NOT `assert "exit 1" in code`: the step has three fail-closed paths, so
    # that assertion stays green while any ONE of them is softened. It passed
    # against a deliberately broken arm (catch-all changed to `::warning::` /
    # `exit 0`) and therefore verified nothing. Pin the validation BLOCK.
    block = code[code.index('case "$fp" in') : code.index("esac")]
    assert "exit 0" not in block, (
        "the fingerprint validation must never continue on a bad value — "
        "an empty baseline pins requires_full_install true for every future "
        f"release:\n{block}"
    )
    # both non-hex and wrong-length must abort, not just one of them
    assert block.count("exit 1") >= 2, (
        f"every invalid-fingerprint branch must exit 1:\n{block}"
    )
    assert "$MANIFEST" in code and "exit 1" in code[: code.index('case "$fp" in')], (
        "a missing manifest must still fail closed"
    )

    # `continue-on-error: true` would make the step's exit code moot and
    # swallow the fail-closed exits above along with the git failures.
    assert not refresh_step.get("continue-on-error"), (
        "continue-on-error would swallow the fail-closed exits too — the git "
        "commands are guarded individually for exactly this reason"
    )
    # the guarded git commands degrade to a warning, never to a silent success
    assert code.count("::warning::") >= 2


def test_the_push_retry_has_the_history_it_needs_to_rebase(refresh_step):
    """The retry rebases when a concurrent commit lands on main, and a rebase
    needs a merge base. A `--depth 1` clone does not have one, so the rebase
    would fail for want of history rather than for a real conflict, and the
    refresh would degrade to the ::warning:: path for a reason that has nothing
    to do with the baseline — leaving the flag pinned true for another release.

    Asserted on COMMENT-STRIPPED code: the rationale comments in this step name
    the mechanisms they replaced, so a naive scan finds `git pull --rebase` in
    the prose explaining why it is gone and reports the old code as present.
    That is the PS-11 pattern (asserting on text the code itself generated),
    and it caught me writing this very fix.
    """
    code = _strip_comments(refresh_step.get("run") or "")
    assert "--depth 1 " not in code, "a depth-1 clone cannot rebase"
    assert re.search(r"--depth\s+(?!1\b)\d+", code), "the clone must carry history"
    # fetch BEFORE rebasing, and rebase on what was fetched
    assert "git fetch" in code, "must fetch (deepening) before rebasing"
    fetch = code.index("git fetch")
    rebase = code.index("git rebase")
    assert fetch < rebase, "the rebase must follow the fetch that deepens history"


# --- the committed baseline itself -------------------------------------------


def test_the_committed_baseline_is_a_well_formed_sha256():
    """A malformed baseline can never equal a computed fingerprint, so it would
    silently pin requires_full_install true forever."""
    assert BASELINE.exists(), "runtime-fingerprint.txt is missing — prevFp would be ''"
    value = BASELINE.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", value), f"not a sha256: {value!r}"
