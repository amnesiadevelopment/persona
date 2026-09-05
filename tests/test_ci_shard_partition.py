"""PS-313: the two CI test shards must PARTITION the suite, never narrow it.

`ci.yml`'s `tests` job was one job running `python -m pytest -q`. It grew past
its own 30-minute cap on ubuntu and was CANCELLED on 15 consecutive runs of
`main` — which is neither a pass nor a failure, it is the absence of a verdict,
so every merge in that window landed with a third of the gate blank.

The fix splits that job into two shards selected by marker:

    main       -m "not ui_driver"
    ui-driver  -m "ui_driver"

WHY THIS FILE EXISTS
────────────────────
That split is only safe because the two expressions are COMPLEMENTARY: every
test matches exactly one of them, so their union is the whole suite and nothing
left the gate. If a test ever matched NEITHER — which is what would happen if
someone introduced a third marker and sharded on it, or mistyped an expression
— that test would silently stop running while BOTH shards stayed green.

That failure is strictly worse than the outage this split repairs. Today the
gate is VISIBLY broken: a cancelled job is obviously not a verdict, and four
tickets correctly diagnosed it as such. A test that vanishes from a green gate
is INVISIBLY broken, and `ci.yml`'s own rule (`no -k narrowing, no --deselect,
no "fast subset"`) exists to forbid exactly that. So the arithmetic is pinned
here rather than trusted to the comment in the workflow.

WHAT THIS FILE DOES NOT CLAIM
─────────────────────────────
It does not assert any timing figure. Wall-clock belongs to the runner and
varies with it; a test asserting "the suite takes under N minutes" would be
flaky by construction and would fail for reasons that have nothing to do with
the property under test. What is pinned is the SHAPE — that the shards cover
the suite and do not overlap — which is what makes the split sound at any
speed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert CI_WORKFLOW.is_file(), f"missing workflow: {CI_WORKFLOW}"
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shard_markers(workflow: dict) -> list[str]:
    """The marker expression of every shard, read from the workflow itself."""
    matrix = workflow["jobs"]["tests"]["strategy"]["matrix"]
    assert "shard" in matrix, (
        "the `tests` job declares no `shard` axis. PS-313 split this job in two so "
        "the ubuntu leg could finish inside its cap; collapsing it back to a single "
        "job restores a suite that does not fit and therefore reports nothing."
    )
    markers = [s["marker"] for s in matrix["shard"]]
    assert len(markers) >= 2, f"expected at least two shards, found {markers!r}"
    return markers


def _collect(marker: str | None) -> set[str]:
    """Collected node ids for a marker expression, from a real pytest run."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"]
    if marker is not None:
        cmd += ["-m", marker]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=900
    )
    # Collection must SUCCEED. A non-zero exit here (e.g. a collection error, or
    # an unknown marker in the expression) would otherwise yield an empty set,
    # and two empty sets satisfy "no overlap" trivially — a vacuous pass on the
    # exact question this file exists to answer.
    assert proc.returncode == 0, (
        f"collection failed for marker {marker!r} (exit {proc.returncode}). A failed "
        f"collection returns no ids, which would make the set comparisons below "
        f"vacuously true:\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"
    )
    ids = {
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and line.strip().startswith("tests/")
    }
    assert ids, f"no tests collected for marker {marker!r} — refusing to compare empty sets"
    return ids


@pytest.fixture(scope="module")
def full_suite() -> set[str]:
    return _collect(None)


def test_the_shards_cover_every_test_in_the_suite(shard_markers, full_suite):
    """The union of the shards IS the suite — no test may fall between them.

    This is the assertion that makes the split legitimate rather than a
    forbidden narrowing. A test matching no shard would stop being gated while
    both shards reported green.
    """
    union: set[str] = set()
    for marker in shard_markers:
        union |= _collect(marker)

    missing = full_suite - union
    assert not missing, (
        f"{len(missing)} test(s) are collected by the full suite but by NO shard, so "
        f"they would silently stop running while every shard stayed green:\n  "
        + "\n  ".join(sorted(missing)[:20])
    )


def test_the_shards_do_not_overlap(shard_markers):
    """No test runs twice.

    Not a correctness risk the way a gap is, but a test billed to two shards
    inflates both their wall-clocks — and the whole point of the split is that
    each shard fits its cap. An overlap would quietly erode the margin this
    change was made to create.
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for marker in shard_markers:
        for node in _collect(marker):
            if node in seen:
                duplicates.append(f"{node}  (in {seen[node]!r} and {marker!r})")
            else:
                seen[node] = marker

    assert not duplicates, (
        f"{len(duplicates)} test(s) are collected by more than one shard, so they run "
        f"twice and consume the margin the split exists to create:\n  "
        + "\n  ".join(sorted(duplicates)[:20])
    )


def test_every_shard_is_gating(workflow):
    """Neither shard may be advisory.

    `ci.yml` forbids `continue-on-error` on the test job for the reason this
    ticket demonstrates: a leg that cannot fail teaches every reader to ignore
    it. Splitting one gating job into two only preserves the gate if BOTH halves
    still block.
    """
    job = workflow["jobs"]["tests"]
    assert "continue-on-error" not in job, (
        "the `tests` job declares continue-on-error, which makes a red shard "
        "advisory. A gate that cannot fail is worse than the cancelled job this "
        "split replaced — that one was at least visibly broken."
    )
    for step in job["steps"]:
        assert "continue-on-error" not in step, (
            f"step {step.get('name')!r} declares continue-on-error; the suite step "
            f"must be able to fail the job."
        )


def test_the_matrix_still_runs_every_platform_and_never_cancels_siblings(workflow):
    """The split must not quietly cost a platform or re-introduce fail-fast.

    `fail-fast: false` was deliberate before this change and matters MORE after
    it: with two shards per platform, a fail-fast matrix would let one red
    ui-driver shard cancel the main shard that was about to say whether
    anything else broke.
    """
    strategy = workflow["jobs"]["tests"]["strategy"]
    assert strategy.get("fail-fast") is False, (
        "fail-fast must stay false: the matrix exists for the COMPARISON between "
        "platforms and shards, and cancelling siblings throws away exactly the rows "
        "that make a divergence legible."
    )
    assert set(strategy["matrix"]["os"]) == {"ubuntu-24.04", "windows-latest", "macos-latest"}, (
        "the platform axis changed. Dropping a platform to save time is the "
        "'fast subset' ci.yml forbids, wearing a different hat."
    )


def test_no_shard_narrows_with_k_or_deselect(workflow):
    """`-m` selects a shard's portion; `-k` and `--deselect` remove tests.

    The distinction is the whole legitimacy of this change and it is easy to
    erode later, because all three look like "a flag on the pytest line". A
    shard's portion is still run WHOLE.
    """
    steps = workflow["jobs"]["tests"]["steps"]
    suite_steps = [s for s in steps if "pytest" in str(s.get("run", ""))]
    assert suite_steps, "no step in the `tests` job invokes pytest"

    for step in suite_steps:
        run = str(step["run"])
        assert not re.search(r"(^|\s)-k(\s|=)", run), (
            f"step {step.get('name')!r} narrows the suite with -k. ci.yml's own rule: "
            f"'no -k narrowing, no --deselect, no fast subset'.\n{run}"
        )
        assert "--deselect" not in run, (
            f"step {step.get('name')!r} uses --deselect, which removes tests from the "
            f"gate rather than assigning them to a shard.\n{run}"
        )
        assert "--ignore" not in run, (
            f"step {step.get('name')!r} uses --ignore, which drops a path from the "
            f"gate entirely.\n{run}"
        )
