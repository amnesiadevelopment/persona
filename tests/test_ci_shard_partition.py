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

# The pytest INVOCATION prefix, matched so the marker expression can be read from
# what FOLLOWS it. `python -m pytest` contains its own `-m`, so a naive scan of
# the whole line reads "pytest" as the marker expression and then happily
# collects the entire suite — an assertion that passes while measuring nothing.
_INVOKES_PYTEST = re.compile(r"(?:\S*python\S*|\S*py\b)\s+-m\s+pytest\b")


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


def _suite_step(workflow: dict) -> dict:
    """The single pytest invocation in the `tests` job.

    Shared by the two assertions below so they cannot disagree about WHICH step
    they are talking about — one reads its command line, the other its `env:`.
    """
    steps = workflow["jobs"]["tests"]["steps"]
    suite_steps = [s for s in steps if _INVOKES_PYTEST.search(str(s.get("run", "")))]
    assert len(suite_steps) == 1, (
        f"expected exactly one pytest invocation in the `tests` job, found "
        f"{len(suite_steps)}: {[s.get('name') for s in suite_steps]!r}. A second "
        f"invocation would run some other portion of the suite under this job's "
        f"name, and the partition assertions above would not see it."
    )
    return suite_steps[0]


def test_the_suite_step_runs_exactly_the_shard_marker(workflow):
    """The guards above read `matrix.shard[].marker`. The runner reads THIS line.

    Every other assertion in this file derives the partition from the matrix
    DATA and then infers that the property holds of the COMMAND LINE. Those are
    the same thing only for as long as nothing is appended to the pytest
    invocation, and nothing else here pins that.

    The gap is not theoretical and it is not caught by
    `test_no_shard_narrows_with_k_or_deselect`, which scans for three literal
    flags and nothing else. Two mutations slip straight through it:

        -m "${{ matrix.shard.marker }} and not slow"
        -m "${{ matrix.shard.marker }}" tests/test_ui_driven.py

    Under either one BOTH shards still collect tests, both still pass, both
    still report green — and thousands of tests have silently left the gate,
    while the matrix (and therefore every partition assertion above) still
    reads as complete. That is precisely the invisibly-false green this file's
    docstring names as strictly worse than the outage the split repairs.

    So: the executed line must be the shard marker VERBATIM and nothing more.
    """
    run = str(_suite_step(workflow)["run"]).strip()
    # `python -m pytest` carries its own `-m`, so split on the INVOCATION prefix
    # and read only what follows it. Scanning the whole line for `-m` reads
    # "pytest" as the marker expression — a green assertion measuring nothing.
    tail = _INVOKES_PYTEST.split(run, maxsplit=1)[-1]
    assert re.fullmatch(
        r'\s*-q\s+-m\s+"\$\{\{\s*matrix\.shard\.marker\s*\}\}"\s*', tail
    ), (
        f"the suite step does not run the shard marker verbatim: {tail!r}\n"
        f"Anything appended here narrows what ACTUALLY runs while the matrix — and "
        f"so every partition test above — still reads as complete. An extra "
        f"`and not ...` clause, a positional test path, `--lf`, or any other "
        f"selector all have that effect. If this shard genuinely needs a different "
        f"invocation, change the MARKER in the matrix (where the partition tests "
        f"can see it), not this line."
    )


def test_the_ui_driver_shard_declares_the_capability_it_exists_to_run(workflow):
    """The tier's own job must REFUSE a silent skip, and only where it can run.

    The split creates a job whose entire population is the 15 ui_driver tests.
    If `/usr/bin/chromium` left the runner image those 15 would skip and that
    job would report GREEN having executed nothing — a whole named check
    passing over an empty run. Under the old single job the same disappearance
    was diluted across 5,500 other tests; the split is what makes it reachable,
    so the split owns closing it.

    `conftest.py` turns a skip into a FAILURE when the missing capability is
    declared in PERSONA_REQUIRED_CAPABILITIES. Two things must therefore hold,
    and BOTH are load-bearing:

    1. `ui_driver` is declared for the shard that runs the tier — otherwise the
       tier can vanish silently.
    2. `browser` is STILL declared for every shard. `expand_capabilities` maps
       "ui_driver" to ["ui_driver"] alone — it does NOT cover browser — so a
       naive swap would stop policing browser skips across the main shard's
       ~5,555 tests. That would move the silent-green hole rather than close
       it, which is strictly worse than leaving it where it was.
    """
    matrix = workflow["jobs"]["tests"]["strategy"]["matrix"]
    by_name = {s["name"]: s for s in matrix["shard"]}

    for name, shard in by_name.items():
        declared = str(shard.get("capabilities", "")).split(",")
        assert "browser" in declared, (
            f"shard {name!r} declares {shard.get('capabilities')!r}, which does not "
            f"include `browser`. expand_capabilities('ui_driver') is ['ui_driver'] "
            f"and does NOT imply browser, so dropping it stops policing browser "
            f"skips for every test in this shard."
        )

    ui = by_name.get("ui-driver")
    assert ui is not None, "no `ui-driver` shard — the tier has no job to be policed on"
    assert "ui_driver" in str(ui["capabilities"]).split(","), (
        f"the ui-driver shard declares {ui['capabilities']!r}. Without `ui_driver` "
        f"a vanished /usr/bin/chromium skips all 15 tests and the job — whose only "
        f"population IS those 15 — reports green having run nothing."
    )

    step = _suite_step(workflow)
    env = str(step.get("env", {}).get("PERSONA_REQUIRED_CAPABILITIES", ""))
    assert "matrix.shard.capabilities" in env, (
        f"the suite step's capability declaration is {env!r}, which does not read "
        f"the shard's own `capabilities`. A hardcoded value cannot differ between "
        f"the two shards, and the whole point is that they differ."
    )
    assert "ubuntu-24.04" in env, (
        f"the capability declaration {env!r} is not conditioned on the platform. "
        f"/usr/bin/chromium exists only on the ubuntu runner, so the 15 tests "
        f"legitimately SKIP on macOS and Windows — declaring ui_driver there turns "
        f"an honest skip into a red job, failing the gate for a fact about the "
        f"runner rather than about the code."
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
