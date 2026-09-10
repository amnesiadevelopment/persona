"""PS-375: the engine-continuity gate is a REPORTER with a verdict, so what
must be tested is that it never reports "we could not look" as continuity — and
never reports an unreadable leg as a difference.

WHY THIS FILE EXISTS
────────────────────
PS-341 built the instrument and nothing called it: `git grep -l "ps341" --
.github/` returned ZERO while four positive controls in the identical probe
shape fired. PS-375 is the caller. But "call the script from a workflow" would
have been worthless on its own, because there was nothing to call that could
answer anything:

    grep -nE "def classify|exit_code_for|EXIT_" scripts/ps341_*.py  ->  nothing

`scripts/ps341_gpu_seeds.py`'s `main()` ends `return 0` UNCONDITIONALLY. It
prints a tally; it does not judge one. So the deliverable was a verdict layer,
and a verdict layer is exactly the kind of code that can be quietly wrong in the
direction of good news. This file drives it.

⛔ THE ONE PROPERTY EVERYTHING HERE ORBITS: AN UNREADABLE LEG IS NOT A MOVE
───────────────────────────────────────────────────────────────────────────
This is not a hypothetical failure mode borrowed from elsewhere. It is PS-341's
OWN instrument history, recorded in `scripts/ps341_gpu_seeds.py`'s header:

    An earlier draft read both builds with `--headless=new --dump-dom` and
    reported 8 SEEDS OF 8 MOVED — "a clean, confident, and COMPLETELY FALSE
    result". The old build's headless arm returned no reading at all for every
    seed, and "no reading" compared against a real string is unequal, so every
    row scored MOVED. THE INSTRUMENT, NOT THE ENGINE, PRODUCED THE 8/8.

So `scorable()` / `classify()` / `is_green()` / `exit_code_for()` are separate
pure functions rather than inline branches in YAML, precisely so they can be
driven here, and the workflow runs them BEFORE any download rather than trusting
a judgement it has never seen withhold a pass.

⭐ AND THE MIRROR IMAGE IS TESTED TOO, because it is the one that looks like
good news: two empty pairs compare EQUAL, so an instrument that returned `["",
""]` on both legs would score a confident `held` for a comparison that never
happened. `test_empty_pairs_are_not_held` is that arm.

NO NETWORK, NO ENGINE, NO DISPLAY. Everything here drives pure functions or
parses files, which is what lets the workflow put it in front of two 200 MB
downloads.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VERDICT = REPO_ROOT / ".github" / "scripts" / "ps341_continuity_verdict.py"
RUNNER = REPO_ROOT / ".github" / "scripts" / "ps341_run_continuity.py"
PROBE = REPO_ROOT / "scripts" / "ps341_gpu_seeds.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-continuity.yml"
GPU_VARIANCE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-gpu-variance.yml"
AUTOUPDATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-autoupdate.yml"
UPSTREAM_WATCH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "chromium-upstream-watch.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PS341_READING = REPO_ROOT / "readings" / "ps341-2026-09-07" / "gpu_seeds.json"

NVIDIA = ["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, RTX 3060 (0x00002487) D3D11)"]
INTEL = ["Google Inc. (Intel)", "ANGLE (Intel, Iris(R) Xe (0x00009A49) D3D11)"]
SEEDS = [3805799318, 12345, 1, 999983, 42424242, 777, 20260907, 88888888]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def v():
    return load(VERDICT, "ps341_continuity_verdict")


def row(seed, new, old, **extra):
    r = {"seed": seed, "new": new, "old": old}
    r.update(extra)
    return r


# ── reading the EXECUTABLE content, not the prose ────────────────────────────
#
# ⚠️ EVERY "this string must not appear" TEST BELOW HAD TO LEARN THIS, AND THE
# WAY IT LEARNED IS WORTH KEEPING. A first draft grepped the whole file for
# `--dump-dom` and failed — on the COMMENT BLOCK that explains why `--dump-dom`
# is forbidden. A file that documents its own trap necessarily contains the
# trap's name, so a whole-file grep either fails on the documentation or forces
# the documentation out, and the second outcome is worse: it would delete the
# only explanation of the most important constraint in this wiring.
#
# So the forbidden-substring tests read what would actually RUN — the `run:`
# blocks of the workflow, and the code (not the docstrings) of the scripts.


def workflow_run_blocks():
    """Every shell COMMAND this workflow would execute, joined.

    Comments are dropped, and so are HEREDOC BODIES — a `cat <<EOF` block is
    text being printed to a human, not a command being run, and this workflow's
    failure message deliberately quotes the very constructs the tests below
    forbid ("do not add a `|| true`"). Treating that prose as executable would
    fail the test on the sentence warning against the thing.
    """
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    bodies = []
    for step in wf["jobs"]["continuity"]["steps"]:
        if isinstance(step.get("run"), str):
            bodies.append(step["run"])

    kept, heredoc = [], None
    for line in "\n".join(bodies).splitlines():
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if m:
            heredoc = m.group(1)
            # The line that OPENS the heredoc is still a command.
        kept.append(line)
    return "\n".join(kept)


def python_code_only(path):
    """A python file's code with every docstring and comment removed."""
    import ast
    import io
    import tokenize

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            first = node.body[0]
            doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i not in doc_lines:
            kept.append(line)
    stripped = "\n".join(kept)
    # Drop `#` comments with the tokenizer rather than by string surgery, so a
    # `#` inside a string literal is not mistaken for one.
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(stripped).readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
    except tokenize.TokenError:
        return stripped
    return "\n".join(out)


# ── THE CENTRAL PROPERTY: AN UNREADABLE LEG IS NOT A DIFFERENCE ──────────────


def test_a_dark_leg_is_excluded_from_the_tally_not_counted_as_moved(v):
    """PS-341's own near-miss, reproduced as the thing that must be impossible.

    Every seed read fine on the NEW build and produced nothing on the OLD one —
    exactly the `--dump-dom` shape that reported a confident, completely false
    8 of 8.
    """
    result = v.classify({"rows": [row(s, NVIDIA, None) for s in SEEDS]})
    assert result["moved"] == 0, "a dark leg must never score as a difference"
    assert result["same"] == 0, "and must not score as agreement either"
    assert result["seeds_unreadable"] == len(SEEDS)
    assert result["seeds_scored"] == 0
    assert result["status"] == v.UNMEASURED
    assert result["status"] != v.MOVED


def test_a_dark_leg_is_not_green(v):
    result = v.classify({"rows": [row(s, NVIDIA, None) for s in SEEDS]})
    assert not v.is_green(result["status"])
    assert v.exit_code_for(result["status"]) == v.EXIT_UNMEASURED


def test_scorability_is_derived_not_read_from_the_instrument(v):
    """A judge that trusts the instrument's self-report cannot catch an
    instrument that is lying — which is the failure this whole module exists to
    make impossible."""
    lying = row(1, NVIDIA, None, readable=True, moved=True)
    assert v.scorable(lying) is False


def test_an_instrument_contradicting_itself_is_reported_not_believed(v):
    result = v.classify({"rows": [row(s, NVIDIA, None, readable=True) for s in SEEDS]})
    assert result["status"] == v.RECORD_INCONSISTENT
    assert not v.is_green(result["status"])


def test_empty_pairs_are_not_held(v):
    """THE MIRROR IMAGE, and the one that looks like good news.

    Two empty pairs compare EQUAL. An instrument returning `["", ""]` on both
    legs would score a confident `held` for a comparison that never happened.
    """
    result = v.classify({"rows": [row(s, ["", ""], ["", ""]) for s in SEEDS]})
    assert result["status"] == v.UNMEASURED
    assert result["seeds_scored"] == 0
    assert result["status"] != v.HELD


def test_a_partial_sample_cannot_report_held(v):
    """"Every seed held" over a partial sample is exactly the claim the missing
    seeds could falsify. `engine_gpu_variance.completeness` records why: the
    seeds that fail are the ones that ran LAST, so the survivors are
    position-biased rather than random."""
    result = v.classify({"rows": (
        [row(s, NVIDIA, NVIDIA) for s in SEEDS[:5]]
        + [row(s, NVIDIA, None) for s in SEEDS[5:]]
    )})
    assert result["status"] == v.UNMEASURED
    assert result["same"] == 5
    assert result["seeds_unreadable"] == 3


def test_truncation_can_hide_a_move_but_never_invent_one(v):
    """The ordering `engine_gpu_variance.exit_code_for` states for the same
    case: a seed that was READ and moved, moved."""
    result = v.classify({"rows": (
        [row(s, NVIDIA, INTEL) for s in SEEDS[:5]]
        + [row(s, None, None) for s in SEEDS[5:]]
    )})
    assert result["status"] == v.MOVED
    assert result["moved"] == 5


def test_no_rows_at_all_is_not_a_pass(v):
    result = v.classify({"rows": []})
    assert result["status"] == v.UNMEASURED
    assert not v.is_green(result["status"])


# ── THE SUMMARY IS RE-DERIVED, NEVER READ ────────────────────────────────────


def test_the_asserted_summary_is_cross_checked_against_the_rows(v):
    """PS-177's `coverage_section()` hardcoded "all four GPU arms returned 24/24
    readable seeds" and read no records at all; a reviewer nulled 12 of 24 and
    it still printed 24/24. A claim that cannot become false is not a check."""
    result = v.classify({
        "seeds_scored": 8, "moved": 8, "same": 0, "seeds_unreadable": 0,
        "rows": [row(s, NVIDIA, NVIDIA) for s in SEEDS],
    })
    assert result["status"] == v.RECORD_INCONSISTENT
    assert not v.is_green(result["status"])


def test_the_tally_is_computed_from_rows_alone(v):
    counted = v.tally([row(1, NVIDIA, INTEL), row(2, NVIDIA, NVIDIA),
                       row(3, NVIDIA, None)])
    assert counted == {
        "seeds_attempted": 3, "seeds_scored": 2, "seeds_unreadable": 1,
        "moved": 1, "same": 1, "moved_seeds": [1], "unreadable_seeds": [3],
    }


def test_a_consistent_summary_is_accepted(v):
    """The cross-check must not reject an honest record — otherwise the runner's
    own output would be unverdictable."""
    rows = [row(s, NVIDIA, INTEL) for s in SEEDS]
    record = {"rows": rows}
    record.update({k: val for k, val in v.tally(rows).items()
                   if k in ("seeds_attempted", "seeds_scored",
                            "seeds_unreadable", "moved", "same")})
    assert v.classify(record)["status"] == v.MOVED


# ── THE POSTURE: A MOVE REPORTS, IT DOES NOT REFUSE ──────────────────────────


def test_a_measured_move_is_green_and_reportable(v):
    """The §2 posture, asserted rather than described. The value is
    engine-authored and un-migratable, so a gate that FAILED on it would be
    permanently red — and `engine_gate.py:27` records what that costs: "A gate
    that is always red is a gate people learn to ignore, which is worse than no
    gate."
    """
    result = v.classify({"rows": [row(s, NVIDIA, INTEL) for s in SEEDS]})
    assert result["status"] == v.MOVED
    assert v.is_green(v.MOVED), "a measured move must not fail the job"
    assert v.exit_code_for(v.MOVED) == 0
    assert v.MOVED in v.REPORT_STATUSES, "but it MUST be filed as news"


def test_held_is_green_and_is_not_news(v):
    result = v.classify({"rows": [row(s, NVIDIA, NVIDIA) for s in SEEDS]})
    assert result["status"] == v.HELD
    assert v.is_green(v.HELD)
    assert v.HELD not in v.REPORT_STATUSES, (
        "filing 'the identity held' weekly forever would train the reader to "
        "ignore the reports that are news"
    )


def test_every_non_measurement_outcome_is_reportable(v):
    """A cause that stops us measuring must still reach a human. A run that is
    red and silent in an Actions tab nobody is subscribed to is the failure mode
    `chromium-upstream-watch.yml` names in its own words."""
    for status in (v.UNMEASURED, v.RECORD_INCONSISTENT, v.PREDECESSOR_UNREACHABLE,
                   v.DISCOVERY_FAILED, v.REFUSED_BY_POLICY):
        assert status in v.REPORT_STATUSES, status


def test_exit_one_is_reserved_and_unspent(v):
    """Across this repository's gates exit 1 means A FINDING — we looked and it
    is a defect to fix. Under the reporting posture no outcome here is such a
    defect, so allocating 1 would assert a repair that does not exist."""
    assert v.EXIT_FINDING == 1
    assert 1 not in set(v.EXIT_FOR_STATUS.values())


def test_an_unrecognised_status_is_never_a_pass(v):
    """An automation must have an answer for a status it does not recognise, and
    that answer is "we did not establish anything" — the catch-all discipline
    `ps342_chromium_watch.classify` applies to an unrecognised probe exit."""
    assert v.exit_code_for("something_new_nobody_wrote_a_case_for") == v.EXIT_UNMEASURED
    assert not v.is_green("something_new_nobody_wrote_a_case_for")


# ── "CANNOT OBTAIN N−1" IS ITS OWN OUTCOME, NEITHER COLOUR BORROWED ──────────


def test_no_predecessor_and_predecessor_unreachable_are_different_outcomes(v):
    """One letter apart in prose, opposite in meaning, and collapsing them is
    the mistake the vocabulary exists to prevent:

      * `no_predecessor` rests on a SUCCESSFUL read of the tag list — there is
        no second thing in the world to compare against.
      * `predecessor_unreachable` rests on a FAILURE — the version IS published
        and its release could not be resolved.
    """
    none = v.no_predecessor_result("152.0.7977.75", ["152.0.7977.75"])
    gone = v.predecessor_unreachable_result("152.0.7977.75", "148.0.7778.215")

    assert none["status"] != gone["status"]
    assert v.is_green(none["status"]), (
        "with one published release there is nothing to compare; making that "
        "red would ship an always-red gate from its first run"
    )
    assert not v.is_green(gone["status"])
    assert v.exit_code_for(gone["status"]) == v.EXIT_PREDECESSOR_UNREACHABLE
    assert v.exit_code_for(gone["status"]) != v.exit_code_for(v.UNMEASURED), (
        "a reachability failure and a measurement failure must be "
        "distinguishable in the exit code, not only in the prose"
    )


def test_predecessor_unreachable_names_the_version_and_substitutes_nothing(v):
    """`fetch_release_full`'s own docstring: "a rollback that silently installs
    something else is worse than one that refuses"."""
    gone = v.predecessor_unreachable_result("152.0.7977.75", "148.0.7778.215")
    assert "148.0.7778.215" in gone["error"]
    assert gone["old_version"] == "148.0.7778.215"
    assert gone["seeds_scored"] == 0
    body = v.render_report(gone)
    assert "not a pass" in body.lower()


def test_a_refused_build_is_not_measured_and_is_its_own_code(v):
    r = v.refused_by_policy_result("152.0.7977.75", "known_bad", "blocklisted")
    assert v.exit_code_for(r["status"]) == v.EXIT_REFUSED_BY_POLICY
    assert not v.is_green(r["status"])
    assert "re-litigate" in r["error"]


def test_the_four_non_measurement_results_all_carry_a_zeroed_tally(v):
    """Each must render without inventing counts it never took."""
    for r in (v.no_predecessor_result("152", ["152"]),
              v.predecessor_unreachable_result("152", "148"),
              v.discovery_failed_result("upstream did not answer"),
              v.refused_by_policy_result("152", "known_bad", "x")):
        assert r["seeds_attempted"] == 0
        assert r["seeds_scored"] == 0
        assert r["moved"] == 0
        assert v.render_report(r)


# ── THE FALSIFICATION ARM, ON PS-341'S REAL COMMITTED READING ────────────────


def test_ps341s_real_reading_of_two_different_builds_reports_a_move(v):
    """NON-WAIVABLE (AC 3). The gate is shown to REPORT A MOVE on a real build
    change, not merely to run.

    This is the reading whose positive control is three-axis — the version
    record, the binary sha256 AND the running page's `navigator.userAgent` major
    all moved — which is what makes it evidence rather than a transcript.

    ⛔ Read-only. PS-341's reading is never re-taken and never written.
    """
    record = json.loads(PS341_READING.read_text(encoding="utf-8"))
    result = v.classify(record)
    assert result["status"] == v.MOVED
    assert result["moved"] == 8
    assert result["same"] == 0
    assert result["seeds_unreadable"] == 0
    assert result["seeds_scored"] == 8


def test_the_same_reading_with_its_old_leg_blanked_is_unmeasured_not_moved(v):
    """THE MUTATION ARM, and the direction is what matters.

    Take the reading that genuinely moved 8/8, blank the OLD leg, and the answer
    must FLIP from `moved` to `unmeasured` — because the difference is now
    produced by the instrument rather than by the engine. Under PS-341's first
    probe this same mutation produced 8/8 MOVED.
    """
    record = json.loads(PS341_READING.read_text(encoding="utf-8"))
    for r in record["rows"]:
        r["old"] = None
        r["readable"] = False
        r["moved"] = None
    for key in ("seeds_scored", "seeds_unreadable", "moved", "same"):
        record.pop(key, None)
    result = v.classify(record)
    assert result["status"] == v.UNMEASURED
    assert result["moved"] == 0, (
        "this is the exact mutation that produced PS-341's 'clean, confident, "
        "and COMPLETELY FALSE' 8/8"
    )


def test_a_quiet_run_is_the_same_reading_with_the_old_leg_matched(v):
    """The other half of the falsification: the gate must be QUIET on an
    unchanged build. Same rows, old leg set equal to new."""
    record = json.loads(PS341_READING.read_text(encoding="utf-8"))
    for r in record["rows"]:
        r["old"] = r["new"]
        r["moved"] = False
    for key in ("seeds_scored", "seeds_unreadable", "moved", "same"):
        record.pop(key, None)
    result = v.classify(record)
    assert result["status"] == v.HELD
    assert result["same"] == 8
    assert v.HELD not in v.REPORT_STATUSES


# ── THE PROBE'S OWN CONTRACT, WHICH THIS GATE DEPENDS ON ─────────────────────


def test_read_pair_still_returns_none_rather_than_a_sentinel():
    """AC 3 asks this to be PRESERVED and ASSERTED. If `read_pair` ever returned
    `""` or `["", ""]` for an unreadable launch, the exclusion above would stop
    firing at the source — so the contract is pinned here rather than assumed.
    """
    src = PROBE.read_text(encoding="utf-8")
    assert "return None" in src
    assert "never a string, never a sentinel" in src, (
        "read_pair's documented contract is what the verdict's exclusion rests "
        "on; if the docstring changed, re-read the function before deleting me"
    )


def test_the_probe_is_imported_not_forked():
    """PS-375's out-of-scope bound: do not grow a second harness. There must be
    exactly one `read_pair` and one venue."""
    runner = RUNNER.read_text(encoding="utf-8")
    assert "ps341_gpu_seeds" in runner
    assert "probe.read_pair(" in runner
    assert "def read_pair" not in runner, "the probe must be imported, not re-implemented"


def test_the_runner_does_not_substitute_the_headless_venue():
    """⛔ The exact substitution that produced the false 8/8. The venue is
    headful under Xvfb read over CDP, which is what `read_pair` does.

    Read against the runner's CODE, not its prose — the module header names
    `--dump-dom` in order to forbid it, and a whole-file grep would either fail
    on that sentence or force it out.
    """
    code = python_code_only(RUNNER)
    assert "--dump-dom" not in code
    assert "--headless" not in code


def test_the_runner_stages_each_build_in_its_own_engine_dir():
    """`updater.ENGINE_BINARY` is bound AT IMPORT from `PERSONA_ENGINE_DIR`, so
    one import cannot stage two builds: the second download would overwrite the
    first in place, leaving both legs on the SAME binary — which compares
    perfectly equal and reports a confident `held` for a comparison that never
    happened."""
    runner = RUNNER.read_text(encoding="utf-8")
    assert "PERSONA_ENGINE_DIR" in runner
    assert "engine-new" in runner and "engine-old" in runner
    assert 'sha256"] == old_info["sha256"]' in runner, (
        "the two staged binaries must be proven DIFFERENT before either is read"
    )


# ── THE WORKFLOW CANNOT BE HOLLOWED OUT ──────────────────────────────────────


def test_the_workflow_exists_and_the_subject_zero_is_closed():
    """AC 1, as its own assertion: `git grep -l "ps341" -- .github/` must now
    return results. That grep is the exact inverse of PS-375's finding."""
    assert WORKFLOW.exists()
    hits = [p for p in (REPO_ROOT / ".github").rglob("*")
            if p.is_file() and "ps341" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits, "PS-341's instrument must have at least one caller in .github/"


def test_the_selftest_runs_before_any_download():
    """`engine-gpu-variance.yml`'s recorded ordering, and its reason: if the
    judgement is broken, a download to produce a meaningless answer is wasted
    and the answer reported would be the wrong answer."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = [s.get("name", "") for s in wf["jobs"]["continuity"]["steps"]]
    selftest = next(i for i, n in enumerate(names) if "withhold a pass" in n)
    provision = next(i for i, n in enumerate(names) if "Provision" in n)
    reading = next(i for i, n in enumerate(names) if "Read both builds" in n)
    assert selftest < provision < reading


def test_the_selftest_step_is_preceded_by_an_install():
    """`chromium-upstream-watch.yml` records this trap and
    `published-engine-verdict.yml` records the measured cost of missing it: a
    clean interpreter has no pytest and no PyYAML, so the workflow-shape tests
    that guard this job would be inert IN THE JOB THEY GUARD."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["jobs"]["continuity"]["steps"]
    names = [s.get("name", "") for s in steps]
    install = next(i for i, n in enumerate(names) if "self-test's dependencies" in n)
    selftest = next(i for i, n in enumerate(names) if "withhold a pass" in n)
    assert install < selftest
    assert "PyYAML" in steps[install]["run"]


def test_the_falsification_arm_runs_before_the_download_too():
    """It costs nothing — it reads a committed file — and a gate whose reporting
    outcome has never been observed on a real build change is not known to
    report one."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = [s.get("name", "") for s in wf["jobs"]["continuity"]["steps"]]
    fals = next(i for i, n in enumerate(names) if "REAL build change" in n)
    provision = next(i for i, n in enumerate(names) if "Provision" in n)
    assert fals < provision


def test_the_workflow_does_no_exit_code_arithmetic_of_its_own():
    """The classification lives in ONE tested place. Any branching on the exit
    code in YAML is where laundering an unmeasured run into a pass would sneak
    back in.

    Read against the RUN BLOCKS rather than the file: the comment block says
    'NOT `|| true`' in order to forbid it.
    """
    runs = workflow_run_blocks()
    assert "|| true" not in runs.replace("cat /tmp/ps375/report.md >> \"$GITHUB_STEP_SUMMARY\" || true", ""), (
        "the only tolerated `|| true` is on the summary cat, which is cosmetic "
        "and must never be able to fail a job over a missing summary file"
    )
    assert "exit-code" not in runs
    y = WORKFLOW.read_text(encoding="utf-8")
    # `continue-on-error` appears exactly once, on the read step, so the report
    # is filed and the artifact uploaded on a non-green reading — and the final
    # step re-asserts the verdict.
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(y)
    coe = [s.get("name") for s in wf["jobs"]["continuity"]["steps"]
           if s.get("continue-on-error")]
    assert coe == ["Read both builds and verdict the comparison"], coe
    assert "Report the verdict" in y


def test_the_engine_is_unpinned_on_the_n_side():
    """AC 5. `engine-gpu-variance.yml`'s recorded reasoning: the RISK IS
    WHATEVER THE NEWEST PUBLISHED RELEASE IS, which is exactly what a pin
    hides."""
    runner = RUNNER.read_text(encoding="utf-8")
    assert "fetch_latest_checked" in runner, (
        "N must be resolved the way the operator's app resolves it, so a build "
        "persona already refuses is not measured"
    )
    y = WORKFLOW.read_text(encoding="utf-8")
    # The scheduled run passes no version at all; the inputs are the manual arm.
    assert "deliberately unpinned" in y


def test_both_tags_are_echoed():
    """AC 5: a report must name the builds it compared."""
    v_src = VERDICT.read_text(encoding="utf-8")
    assert "new_version=" in v_src and "old_version=" in v_src
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "steps.read.outputs.new_version" in y
    assert "steps.read.outputs.old_version" in y


def test_the_venue_is_headful_under_xvfb():
    """AC 4. ⛔ Not `--headless=new --dump-dom`; that is the exact substitution
    that produced the false 8/8.

    Read against the RUN BLOCKS: the comment block names the substitution in
    order to forbid it.
    """
    runs = workflow_run_blocks()
    assert "xvfb-run -a python3 .github/scripts/ps341_run_continuity.py" in runs
    assert "--dump-dom" not in runs
    assert "--headless" not in runs


def test_the_workflow_files_the_report_somewhere_a_human_receives_it():
    """PS-4 asks for a decision RECORDED AGAINST A VERSION. A green run in an
    Actions tab nobody is subscribed to is not that."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "gh issue create" in y
    assert "gh issue comment" in y
    assert "steps.read.outputs.report == 'true'" in y


def test_the_issue_title_carries_both_versions_and_the_status(v):
    """The title is the DEDUP KEY — the workflow matches an OPEN issue by exact
    title and comments rather than filing. `ps342_chromium_watch.issue_title`
    records what a shared title costs: the second cause is SUPPRESSED into a
    comment on the first."""
    moved = v.classify({"rows": [row(s, NVIDIA, INTEL) for s in SEEDS]})
    moved["new_version"], moved["old_version"] = "152.0.7977.75", "148.0.7778.215"
    title = v.issue_title(moved)
    assert "152.0.7977.75" in title and "148.0.7778.215" in title
    assert "moved" in title

    unmeasured = dict(moved, status=v.UNMEASURED)
    assert v.issue_title(unmeasured) != title, (
        "the same pair going from unmeasured to moved is different news and "
        "must get its own record"
    )

    assert v.issue_title(v.discovery_failed_result("x")) != title


def test_step_outputs_cannot_be_forged_by_an_embedded_newline(v, tmp_path):
    """Outputs are bare `key=value` lines with no delimiter, so an embedded
    newline forges additional outputs. `ps342_chromium_watch` records a measured
    instance: a `--tag` carrying `\\ngreen=true` handed the dispatcher a green on
    a run that measured nothing."""
    out = tmp_path / "out"
    hostile = v.predecessor_unreachable_result("152\ngreen=true", "148")
    v.write_github_output(hostile, str(out))
    text = out.read_text(encoding="utf-8")
    assert "green=false" in text
    assert "\ngreen=true" not in text
    lines = [ln for ln in text.splitlines() if ln.startswith("green=")]
    assert lines == ["green=false"], lines


def test_the_report_body_is_passed_as_a_file_never_as_an_argument():
    """The prose can contain anything; only the file path reaches the command
    line."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "--body-file" in y
    assert "--body " not in y


def test_the_dispatch_inputs_are_not_interpolated_into_a_shell_command():
    """A `${{ inputs.* }}` expanded straight into a `run:` block is substituted
    before bash ever sees it, so a dispatch box becomes shell source text."""
    y = WORKFLOW.read_text(encoding="utf-8")
    for line in y.splitlines():
        stripped = line.strip()
        if "inputs.new_version" in stripped or "inputs.old_version" in stripped:
            assert stripped.startswith(("NEW_VERSION:", "OLD_VERSION:")), stripped


def test_the_workflow_declares_the_permissions_it_uses():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    perms = wf["permissions"]
    assert perms.get("issues") == "write", "it files an issue"
    assert perms.get("contents") == "read", "and reads nothing else"


def test_the_artifact_is_kept_even_on_a_green_moved_run():
    """A `moved` run is GREEN and is exactly the run worth keeping — an
    `if: failure()` upload would discard the evidence for the decision the
    report asks a human to make."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    upload = next(s for s in wf["jobs"]["continuity"]["steps"]
                  if "Keep the reading" in s.get("name", ""))
    assert upload["if"] == "always()"


# ── THE COVERAGE AND SEPARATION CLAIMS THE TICKET ASKS TO BE STATED ──────────


def test_the_comment_block_addresses_all_five_sibling_workflows():
    """AC 7. All four pre-existing engine gates plus PS-370's, each named."""
    y = WORKFLOW.read_text(encoding="utf-8")
    for sibling in ("engine-autoupdate.yml", "engine-gpu-variance.yml",
                    "chromium-upstream-watch.yml", "ci.yml",
                    "published-engine-verdict.yml"):
        assert sibling in y, sibling
    assert "engine_gate" in y


def test_the_comment_block_states_the_cadence_and_its_measured_cost():
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "16 HEADFUL launches" in y
    assert "202,193,400 bytes" in y
    assert "timeout-minutes: 60" in y
    assert "WEEKLY, NOT DAILY" in y
    assert "cron: \"40 8 * * 1\"" in y


def test_the_comment_block_states_the_reporting_posture_and_why():
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "REPORTS, IT DOES NOT REFUSE" in y
    assert "worse than no gate" in y
    assert "recorded, NOT fixed" in y


def test_the_comment_block_bounds_coverage_to_the_windows_arm_on_linux():
    """Bound 4: `windows` arm only, and it must not be presented as cross-arm
    coverage."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "ENGINE_AUTHORED_IDENTITY_ARMS" in y
    assert "LINUX x86_64 HOSTS ONLY" in y
    assert "cross-arm coverage" in y
    assert "runs-on: ubuntu-24.04" in y


def test_the_workflow_does_not_claim_linkability():
    """Bound 2: the move is real and measured; its HARM is not."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "UNMEASURED and is not claimed" in y


def test_the_engine_authored_arm_is_still_windows_only():
    """The bound this gate's coverage claim rests on. If a second arm is ever
    added to `ENGINE_AUTHORED_IDENTITY_ARMS`, the workflow's "windows arm only"
    sentence stops being true and this test says so."""
    from src.services.browser.gpu_ext import ENGINE_AUTHORED_IDENTITY_ARMS
    assert ENGINE_AUTHORED_IDENTITY_ARMS == frozenset({"windows"}), (
        "the workflow's coverage sentence names the windows arm; update both "
        "together"
    )


def test_the_sibling_workflows_are_untouched():
    """PS-375's out-of-scope list: read them as models, leave them
    byte-identical."""
    for wf in (GPU_VARIANCE_WORKFLOW, AUTOUPDATE_WORKFLOW, UPSTREAM_WATCH_WORKFLOW):
        assert wf.exists()
    # The wrong-axis claim, asserted rather than described: the daily variance
    # gate reads across SEEDS within one build and never compares two builds.
    y = GPU_VARIANCE_WORKFLOW.read_text(encoding="utf-8")
    assert "ps341" not in y, "the daily gate must not have grown this question"


def test_this_workflow_is_not_triggered_by_a_push():
    """A moved identity is caused by UPSTREAM PUBLISHING, not by our commits."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf[True] if True in wf else wf["on"]
    assert "push" not in on
    assert "schedule" in on and "workflow_dispatch" in on


# ── THE ROUTING LAYER: WHICH NON-MEASUREMENT OUTCOME APPLIES ─────────────────
#
# ⛔ THIS SECTION EXISTS BECAUSE ITS ABSENCE SHIPPED THREE WRONG ANSWERS.
#
# Every test above this line that mentions the runner reads its SOURCE TEXT.
# That is the right tool for "the probe is imported, not forked" and "the
# headless venue is not substituted" — properties of the file. It is the wrong
# tool for `resolve()` and `run()`, which DECIDE WHICH OF THE NON-MEASUREMENT
# OUTCOMES APPLIES, and which no test executed at all. The selftest's cases
# exercise `classify` plus the `*_result()` CONSTRUCTORS; nothing exercised the
# code that chooses between them, so the routing layer sat in exactly the state
# this module's own header condemns: a judgement whose failing outcomes have
# never been observed.
#
# Driven here with a stubbed `updater`, because the three decisions that were
# wrong are each two lines to falsify:
#
#   1. an unresolvable build N reported `discovery_failed` — AFTER the run had
#      successfully read AND PRINTED the published tag list,
#   2. the manual arm filed `N vs N` and blamed the predecessor for build N's
#      failure, and
#   3. a failed download RAISED, so the report — this job's deliverable — was
#      never written at all.
#
# NO NETWORK: only `fetch_latest_checked`, `fetch_release_full` and
# `engine_versions_newest_first` are replaced. `is_newer`, `version_from_tag`
# and `parse_version` stay REAL, because the ordering and the prefix rules are
# part of what is under test.

NEWEST = "152.0.7977.75"
PREVIOUS = "148.0.7778.215"


@pytest.fixture(scope="module")
def runner():
    return load(RUNNER, "ps341_run_continuity")


@pytest.fixture
def fake_updater(monkeypatch):
    """The real `updater` with ONLY its three network calls replaced."""
    from src.services.engine import updater as real

    state = {
        "published": [NEWEST, PREVIOUS],
        # version -> (version, url, digest); a missing key answers ('','',''),
        # which is `fetch_release_full`'s own contract for a yanked release.
        "releases": {
            NEWEST: (NEWEST, "https://example.invalid/new.AppImage", "sha256:aa"),
            PREVIOUS: (PREVIOUS, "https://example.invalid/old.AppImage", "sha256:bb"),
        },
        "latest": None,          # None -> derived from `releases`
    }

    def engine_versions_newest_first(timeout=20):
        return list(state["published"])

    def fetch_release_full(tag, timeout=20):
        # The real function accepts a prefixed tag and answers bare, so the stub
        # must too — the prefix-normalisation test below rests on it.
        return state["releases"].get(real.version_from_tag(tag), ("", "", ""))

    def fetch_latest_checked(timeout=20):
        if state["latest"] is not None:
            return state["latest"]
        newest = state["published"][0] if state["published"] else ""
        v, url, digest = state["releases"].get(newest, ("", "", ""))
        return newest, url, digest, "ok", ""

    monkeypatch.setattr(real, "engine_versions_newest_first",
                        engine_versions_newest_first)
    monkeypatch.setattr(real, "fetch_release_full", fetch_release_full)
    monkeypatch.setattr(real, "fetch_latest_checked", fetch_latest_checked)
    return state


def test_a_healthy_scheduled_run_resolves_both_builds(runner, v, fake_updater):
    """The positive control for this whole section. Without it, every test
    below could pass because `resolve` refuses everything."""
    plan, result = runner.resolve(v)
    assert result is None
    assert plan["new_version"] == NEWEST
    assert plan["old_version"] == PREVIOUS
    assert plan["new_url"] and plan["old_url"]
    assert plan["new_url"] != plan["old_url"]


def test_an_unresolvable_build_n_does_not_deny_the_tag_list_it_just_read(
        runner, v, fake_updater, capsys):
    """FINDING 1. `fetch_latest_checked` can answer a version with NO url — a
    transient API failure, or a release with no asset for this OS, both of which
    happen to a weekly unattended job.

    That used to route to `discovery_failed`, whose defined meaning is *"We
    could not even ask which engine versions are published"* — on a run that had
    just asked, been answered, and PRINTED the answer. The report was false
    about the one fact the run established.
    """
    fake_updater["latest"] = (NEWEST, "", "", "ok", "")
    plan, result = runner.resolve(v)
    printed = capsys.readouterr().out

    assert plan is None
    assert NEWEST in printed, "the run reads and prints the tag list first"
    assert result["status"] != v.DISCOVERY_FAILED, (
        "the tag list WAS read — saying otherwise contradicts this run's own "
        "output two lines earlier"
    )
    assert result["status"] == v.BUILD_UNREACHABLE
    assert result["new_version"] == NEWEST
    body = v.render_report(result)
    assert "could not be resolved" in body
    assert not v.is_green(result["status"])


def test_a_blank_version_is_never_rendered_as_a_quoted_empty_string(
        runner, v, fake_updater):
    """FINDING 1, sub-case. On a genuine network blip the version comes back
    empty too, and the old message INTERPOLATED it: `the newest engine release
    '' could not be resolved to an asset for this OS.` — a quoted empty version
    in a filed issue.

    ⚠️ Asserted against the INTERPOLATION, not against the two characters. The
    message legitimately quotes `fetch_release_full`'s literal `('','','')`
    contract — naming the API answer is what makes the error diagnosable — so a
    blanket "no empty quotes anywhere" test would fail on the documentation of
    the very thing that went wrong.
    """
    fake_updater["latest"] = ("", "", "", "ok", "")
    plan, result = runner.resolve(v)
    assert plan is None
    assert "release '' " not in result["error"]
    assert "build N ('')" not in result["error"]
    assert "could not be named" in result["error"], (
        "an unnameable version must SAY it is unnameable rather than render as "
        "an empty pair of quotes"
    )
    assert result["new_version"] is None
    # And it must not reach the issue title as an empty name either.
    assert "engine  " not in v.issue_title(result)
    assert v.render_report(result)


def test_the_manual_arm_never_files_a_comparison_of_a_version_with_itself(
        runner, v, fake_updater):
    """FINDING 2. A dispatched build N whose release will not resolve used to
    return `predecessor_unreachable_result(n_ver, n_ver)` — naming build N's
    failure as the PREDECESSOR's, asserting the predecessor was unobtainable
    when it was never asked for, and rendering an issue titled `X vs X`.

    The issue title is the dedup key, so that also filed the wrong record.
    """
    fake_updater["releases"].pop(NEWEST)
    plan, result = runner.resolve(v, new_version=NEWEST)

    assert plan is None
    assert result["status"] != v.PREDECESSOR_UNREACHABLE, (
        "what failed is build N; blaming the predecessor points the reader at "
        "the wrong build"
    )
    assert result["status"] == v.BUILD_UNREACHABLE
    assert result["new_version"] == NEWEST
    assert result["old_version"] is None
    title = v.issue_title(result)
    assert "%s vs %s" % (NEWEST, NEWEST) not in title
    assert " vs " not in title, title


def test_both_arms_name_one_real_world_event_the_same_way(
        runner, v, fake_updater):
    """FINDING 1 + 2 together, and the reason they are one defect. The SAME
    event — a published version whose asset will not resolve — produced
    `discovery_failed` on the scheduled arm and `predecessor_unreachable` on the
    manual one. Two statuses, two exit codes and two issue titles for one
    cause."""
    fake_updater["releases"].pop(NEWEST)
    fake_updater["latest"] = (NEWEST, "", "", "ok", "")

    _, scheduled = runner.resolve(v)
    _, manual = runner.resolve(v, new_version=NEWEST)

    assert scheduled["status"] == manual["status"]
    assert v.exit_code_for(scheduled["status"]) == v.exit_code_for(manual["status"])
    assert v.issue_title(scheduled) == v.issue_title(manual), (
        "the title is the dedup key: disagreeing arms file two records for one "
        "cause"
    )


def test_an_unreachable_predecessor_is_still_its_own_outcome(
        runner, v, fake_updater):
    """The control for the two tests above: widening build N's failure to its
    own status must NOT have collapsed the predecessor's, which is a different
    build and a different thing for a human to go and look at."""
    fake_updater["releases"].pop(PREVIOUS)
    plan, result = runner.resolve(v)

    assert plan is None
    assert result["status"] == v.PREDECESSOR_UNREACHABLE
    assert result["new_version"] == NEWEST
    assert result["old_version"] == PREVIOUS
    assert PREVIOUS in result["error"]


def test_a_single_published_release_is_no_predecessor_not_a_failure(
        runner, v, fake_updater):
    fake_updater["published"] = [NEWEST]
    plan, result = runner.resolve(v)
    assert plan is None
    assert result["status"] == v.NO_PREDECESSOR
    assert v.is_green(result["status"])


def test_an_empty_tag_list_is_discovery_failed(runner, v, fake_updater):
    """`discovery_failed` still has its OWN event, and this is it: the list
    itself came back empty, so we genuinely could not ask."""
    fake_updater["published"] = []
    plan, result = runner.resolve(v)
    assert plan is None
    assert result["status"] == v.DISCOVERY_FAILED
    assert not v.is_green(result["status"])


def test_a_build_persona_refuses_is_not_measured(runner, v, fake_updater):
    fake_updater["latest"] = (NEWEST, "", "", "known_bad", "blocklisted")
    plan, result = runner.resolve(v)
    assert plan is None
    assert result["status"] == v.REFUSED_BY_POLICY
    assert v.exit_code_for(result["status"]) == v.EXIT_REFUSED_BY_POLICY


def test_dispatching_the_oldest_version_says_so_rather_than_miscounting(
        runner, v, fake_updater):
    """FINDING 5. Dispatching the OLDEST published version correctly returns
    `no_predecessor` — but it used to render the scheduled path's headline,
    "Only one engine release is published", over a body that then counted TWO.
    The status was right; the sentence was a scheduled-path assumption leaking
    into the manual path."""
    plan, result = runner.resolve(v, new_version=PREVIOUS)
    assert plan is None
    assert result["status"] == v.NO_PREDECESSOR

    body = v.render_report(result)
    assert "Only one engine release is published" not in body
    assert "only one" not in body.lower()
    assert PREVIOUS in body and NEWEST in body
    # And the scheduled case must still say the thing that IS true there.
    fake_updater["published"] = [NEWEST]
    _, alone = runner.resolve(v)
    assert "no predecessor build in existence" in v.render_report(alone)


def test_a_dispatched_published_tag_is_normalised_before_it_is_echoed(
        runner, v, fake_updater):
    """FINDING 4. `fetch_release_full` deliberately ACCEPTS a `personium-`
    prefixed tag — a human pasting the published tag into the dispatch box is a
    supported gesture — and hands back the bare version.

    `version_from_tag`'s docstring is explicit that the prefix must not travel
    past that boundary, so echoing the REQUESTED string put a prefixed version
    into the report and the issue title while every other surface in the
    repository speaks bare ones.
    """
    plan, result = runner.resolve(v, new_version="personium-" + NEWEST)
    assert result is None
    assert plan["new_version"] == NEWEST, plan["new_version"]
    assert not plan["new_version"].startswith("personium-")
    assert plan["old_version"] == PREVIOUS
    # And the prefix must not survive into what a human reads either.
    moved = v.classify({"rows": [row(s, NVIDIA, INTEL) for s in SEEDS]})
    moved["new_version"], moved["old_version"] = plan["new_version"], plan["old_version"]
    assert "personium-" not in v.issue_title(moved)


def test_a_prefixed_predecessor_is_normalised_too(runner, v, fake_updater):
    plan, result = runner.resolve(
        v, new_version=NEWEST, old_version="personium-" + PREVIOUS)
    assert result is None
    assert plan["old_version"] == PREVIOUS


# ── FINDING 3: A FAILED DOWNLOAD MUST PRODUCE THE REPORT, NOT A TRACEBACK ────


class _Args:
    def __init__(self, tmp_path, **kw):
        self.work = str(tmp_path / "work")
        self.new_version = ""
        self.old_version = ""
        self.reading_json = ""
        self.report_json = str(tmp_path / "verdict.json")
        self.report_md = str(tmp_path / "report.md")
        self.github_output = str(tmp_path / "gh_output")
        for k, val in kw.items():
            setattr(self, k, val)


def test_a_failed_download_still_writes_the_report_and_the_step_outputs(
        runner, v, fake_updater, monkeypatch, tmp_path):
    """FINDING 3, and it is the sharpest of the three because the run's COLOUR
    was already right — which is exactly why nobody would notice.

    `stage_build` raised on a refused or failed `download_engine` and `run()`
    wrapped neither call, so a digest mismatch, a mid-run yank, a truncated
    transfer or a full disk produced: no step output, no report, no issue, no
    artifact. The job went red and SILENT, in an Actions tab this project has
    already recorded that nobody receives — the outcome the runner's own
    docstring forbids in its own words, on the one step whose failure is least
    surprising, since a download is this job's entire cost.
    """
    def refuse(version, url, digest, engine_dir):
        raise runner.StagingError("download_engine refused or failed for %s" % version)

    monkeypatch.setattr(runner, "stage_build", refuse)
    args = _Args(tmp_path)

    code = runner.run(args)                       # ⛔ must NOT raise

    assert code == v.EXIT_STAGING_FAILED
    assert code != 0
    report = Path(args.report_md)
    assert report.exists(), "the report is this job's deliverable"
    body = report.read_text(encoding="utf-8")
    assert "not a pass" in body.lower()
    assert NEWEST in body

    outputs = Path(args.github_output).read_text(encoding="utf-8")
    assert "status=staging_failed" in outputs
    assert "green=false" in outputs
    assert "report=true" in outputs, (
        "a cause that stops us measuring must still reach a human"
    )
    assert json.loads(Path(args.report_json).read_text(encoding="utf-8"))


def test_the_failing_leg_is_named_rather_than_guessed(
        runner, v, fake_updater, monkeypatch, tmp_path):
    """A staging failure on the PREDECESSOR must not read as build N's."""
    calls = []

    def fail_second(version, url, digest, engine_dir):
        calls.append(version)
        if len(calls) == 1:
            return {"binary": "/tmp/new", "size": 1, "sha256": "a" * 64}
        raise runner.StagingError("truncated transfer")

    monkeypatch.setattr(runner, "stage_build", fail_second)
    args = _Args(tmp_path)
    code = runner.run(args)

    assert code == v.EXIT_STAGING_FAILED
    result = json.loads(Path(args.report_json).read_text(encoding="utf-8"))["verdict"]
    assert result["unreachable_leg"] == "N−1"
    assert result["new_version"] == PREVIOUS, (
        "the report must name the build that actually failed"
    )
    assert "truncated transfer" in result["error"]


def test_a_resolution_failure_also_reaches_emit_rather_than_raising(
        runner, v, fake_updater, tmp_path):
    """The property the three findings share, asserted end to end: EVERY
    non-measurement outcome produces a report, outputs and an exit code."""
    fake_updater["releases"].pop(NEWEST)
    args = _Args(tmp_path)
    code = runner.run(args)
    assert code == v.EXIT_UNREACHABLE
    assert Path(args.report_md).exists()
    assert "status=build_unreachable" in Path(args.github_output).read_text(
        encoding="utf-8")


def test_two_identical_binaries_are_refused_rather_than_reported_as_continuity(
        runner, v, fake_updater, monkeypatch, tmp_path):
    """The sha256 positive control, driven through `run()` rather than read.

    Two legs on the SAME bytes compare perfectly equal for every seed and would
    report a confident `held` for a comparison that never happened — the mirror
    image of the false 8/8, and the one whose output looks like good news."""
    def same_bytes(version, url, digest, engine_dir):
        return {"binary": "/tmp/engine", "size": 1, "sha256": "c" * 64}

    monkeypatch.setattr(runner, "stage_build", same_bytes)
    args = _Args(tmp_path)
    code = runner.run(args)

    assert code == v.EXIT_UNMEASURED
    result = json.loads(Path(args.report_json).read_text(encoding="utf-8"))["verdict"]
    assert result["status"] == v.RECORD_INCONSISTENT
    assert not v.is_green(result["status"])


def test_every_result_resolve_can_return_carries_a_known_status(runner, v, fake_updater):
    """A status the exit table does not know lands on EXIT_UNMEASURED — which is
    the correct default, and would silently downgrade a `staging_failed` or a
    `build_unreachable` into "we did not look". So every result this routing
    layer can produce is checked to be in the table by name."""
    seen = set()
    for setup in (
        lambda: fake_updater.update(published=[]),
        lambda: fake_updater.update(published=[NEWEST]),
        lambda: fake_updater.update(latest=(NEWEST, "", "", "known_bad", "x")),
        lambda: fake_updater.update(latest=(NEWEST, "", "", "ok", "")),
        lambda: fake_updater["releases"].pop(PREVIOUS, None),
    ):
        fake_updater.update(published=[NEWEST, PREVIOUS], latest=None)
        fake_updater["releases"] = {
            NEWEST: (NEWEST, "https://example.invalid/new.AppImage", "sha256:aa"),
            PREVIOUS: (PREVIOUS, "https://example.invalid/old.AppImage", "sha256:bb"),
        }
        setup()
        _, result = runner.resolve(v)
        if result is None:
            continue
        seen.add(result["status"])
        assert result["status"] in v.EXIT_FOR_STATUS, result["status"]
    assert len(seen) >= 4, seen
