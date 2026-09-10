"""PS-385: the audit that asks whether every PUBLISHED engine release has a
provenance record — and the wiring that makes it run.

WHAT THESE TESTS ARE FOR
────────────────────────
PS-343 built the record, the verifier and the suite. None of the three asks the
sentence `engine/releases/README.md` opens with — *"For every Personium engine
we have **published**, a record here says…"*. Measured at the commit this was
written on:

* the verifier's `load_records()` GLOBS `engine/releases/personium-*.json` and
  `main()` iterates that list, so the RECORD SET is the denominator and a
  published release with no record is not red — it is absent from the question;
* the suite's territory assertion read a compile-time literal, so it asserted
  ONE hardcoded release has a record;
* `git grep -l "ps343" -- .github/` returned ZERO against four firing positive
  controls, so nothing ran any of it on a schedule.

⭐ THE LOAD-BEARING TESTS ARE THE ONES THAT DRIVE A PUBLISHED SET WITH NO RECORD
────────────────────────────────────────────────────────────────────────────────
A gate that has only ever been seen to pass is not known to work, and this
project has recorded that exact shape three times: the PS-299 rebase probe
printing "81/81 hunks, 0 rejects, ✅" **against an empty directory**;
`ps301_repro.sh`, which had no non-zero exit path at all and exited 0 on both
arms; and PS-341's `--dump-dom` reading "8 of 8 moved" — a clean, confident and
completely false result produced by the instrument rather than the engine.

So the red arm is pinned (`test_a_published_release_with_no_record_is_named`),
⛔ **and so is the quiet arm** — because a gate that flagged EVERY release would
satisfy the red arm perfectly while discriminating nothing. The specific way
this gate could do that is real and is pinned on its own
(`test_the_bare_version_tag_seam_is_crossed_not_sliced`): the published side
speaks BARE VERSIONS and the record side speaks TAGS, so a naive comparison of
the two raw lists reports every release as unrecorded and looks like a working
red arm.

⭐ AND THE THIRD OUTCOME IS ASSERTED AS A THIRD OUTCOME
───────────────────────────────────────────────────────
"The published set could not be established" is neither a pass nor a finding
about any record. `engine_versions_newest_first()` answers `[]` both on failure
and on a repository with no engine tag — the two are indistinguishable at that
boundary — so this gate reads `[]` as UNESTABLISHED (exit 2), which is the
OPPOSITE of the reading the operator's update path takes and is correct for the
opposite reason. `test_an_empty_published_list_is_unestablished_not_quiet` pins
it, and `test_the_empty_reading_is_stated_in_the_file` pins that the choice is
written down rather than implied.

WHY THE FIXTURES ARE SYNTHESISED
─────────────────────────────────
`classify()` takes both sets as arguments, so every outcome is driven without a
network call. What is pinned here is the JUDGEMENT and the WIRING — the parts
that can silently rot between releases. The real run is the ticket's own AC and
is recorded there.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
AUDIT_SCRIPT = REPO / ".github" / "scripts" / "ps343_release_audit.py"
WORKFLOW = REPO / ".github" / "workflows" / "engine-release-provenance-audit.yml"
RECORDS_DIR = REPO / "engine" / "releases"
VERIFIER = REPO / "scripts" / "ps343_verify_release_provenance.py"


def _load(path: pathlib.Path, name: str):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load(AUDIT_SCRIPT, "ps343_release_audit")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow_yaml) -> list:
    return workflow_yaml["jobs"]["audit"]["steps"]


# ---------------------------------------------------------------------------
# THE JUDGEMENT. Three outcomes, and they must stay three.
# ---------------------------------------------------------------------------


def test_a_published_release_with_no_record_is_named(audit):
    """⭐ THE RED ARM. The whole point: the published set is the denominator, so
    a release with no record is a NAMED red row rather than an absence."""
    code, body = audit.classify(
        ["152.0.7977.82", "152.0.7977.75"],
        ["personium-152.0.7977.75"],
    )
    assert code == audit.UNRECORDED_RELEASE
    assert code == 1
    assert body["unrecorded"] == ["personium-152.0.7977.82"]
    # NAMED, not merely counted — the remedy is writing THAT release's record.
    assert "personium-152.0.7977.82" in body["reason"]


def test_the_gate_is_quiet_when_every_published_release_is_recorded(audit):
    """⛔ THE QUIET ARM, and it is not optional. The red arm alone shows the
    gate can FIRE; only this shows it DISCRIMINATES. A gate that flagged every
    release would satisfy the red arm perfectly."""
    code, body = audit.classify(["152.0.7977.75"], ["personium-152.0.7977.75"])
    assert code == audit.QUIET
    assert code == 0
    assert body["unrecorded"] == []


def test_the_bare_version_tag_seam_is_crossed_not_sliced(audit):
    """The SPECIFIC way this gate would flag everything and look like a working
    red arm. `engine_versions_newest_first` returns BARE versions
    (`152.0.7977.75`); the records are named by TAG
    (`personium-152.0.7977.75.json`). A naive comparison of the raw lists
    reports every release as unrecorded.

    Driven at BOTH vocabularies on both sides, so a comparison that happens to
    work in one direction only is caught."""
    for published in (["152.0.7977.75"], ["personium-152.0.7977.75"]):
        for records in (["personium-152.0.7977.75"], ["152.0.7977.75"]):
            code, body = audit.classify(published, records)
            assert code == audit.QUIET, (published, records, body)
            assert body["unrecorded"] == [], (published, records)


def test_an_empty_published_list_is_unestablished_not_quiet(audit):
    """⭐ THE THIRD OUTCOME, and the `[]` decision.

    `enumerate_published()` turns `[]` into `None` — see the script's module
    docstring for why that reinterpretation lives at the CALL SITE and not
    inside `updater`. Reading `[]` as "zero published releases, therefore zero
    unrecorded ones, green" would turn every rate limit into a permanent silent
    pass."""
    code, body = audit.classify(None, ["personium-152.0.7977.75"], reason="empty")
    assert code == audit.CANNOT_ENUMERATE
    assert code == 2
    assert body["outcome"] == "CANNOT_ENUMERATE"
    assert body["published_count"] is None


def test_the_empty_answer_reaches_cannot_enumerate_through_the_real_call_site(audit, monkeypatch):
    """The reinterpretation is asserted where it actually happens, not only at
    `classify()`'s door — an `enumerate_published` that passed `[]` straight
    through would make the test above green and the gate wrong."""
    from src.services.engine import updater

    monkeypatch.setattr(updater, "engine_versions_newest_first", lambda timeout=30: [])
    published, reason = audit.enumerate_published()
    assert published is None
    assert "EMPTY" in reason
    code, _ = audit.classify(published, ["personium-152.0.7977.75"], reason=reason)
    assert code == audit.CANNOT_ENUMERATE


def test_a_raised_enumeration_is_unestablished_not_quiet(audit, monkeypatch):
    from src.services.engine import updater

    def boom(timeout=30):
        raise OSError("upstream said no")

    monkeypatch.setattr(updater, "engine_versions_newest_first", boom)
    published, reason = audit.enumerate_published()
    assert published is None
    assert "OSError" in reason
    code, _ = audit.classify(published, ["personium-152.0.7977.75"], reason=reason)
    assert code == audit.CANNOT_ENUMERATE


def test_the_three_outcomes_are_distinct_and_only_one_is_success(audit):
    codes = {audit.QUIET, audit.UNRECORDED_RELEASE, audit.CANNOT_ENUMERATE}
    assert len(codes) == 3
    assert audit.QUIET == 0
    assert audit.UNRECORDED_RELEASE != 0
    assert audit.CANNOT_ENUMERATE != 0
    # 0/1/2 is the verifier's OWN vocabulary (`Report.exit_code`), and
    # ps299_rebase_probe's, and ps344_verdict's. A second vocabulary for one
    # idea is a second thing to keep in sync.
    verifier_src = VERIFIER.read_text(encoding="utf-8")
    assert "if self.red:\n            return 1" in verifier_src
    assert "if self.unmeasured:\n            return 2" in verifier_src


def test_every_published_release_unrecorded_names_them_all(audit):
    code, body = audit.classify(["152.0.7977.82", "152.0.7977.75"], [])
    assert code == audit.UNRECORDED_RELEASE
    assert body["unrecorded"] == [
        "personium-152.0.7977.75",
        "personium-152.0.7977.82",
    ]


def test_an_orphan_record_is_noted_and_scores_nothing(audit):
    """A record with no matching published tag — a yanked release, or one
    written ahead of publication. The quantifier being evaluated is *every
    published release has a record*, which says nothing about the converse, so
    reporting it is useful and scoring it would invent a finding."""
    code, body = audit.classify(
        ["152.0.7977.75"],
        ["personium-152.0.7977.75", "personium-151.0.0.1"],
    )
    assert code == audit.QUIET
    assert body["orphan_records"] == ["personium-151.0.0.1"]


def test_an_orphan_record_does_not_mask_an_unrecorded_release(audit):
    """The two are independent: an orphan must not soak up a real red."""
    code, body = audit.classify(
        ["152.0.7977.82"],
        ["personium-151.0.0.1"],
    )
    assert code == audit.UNRECORDED_RELEASE
    assert body["unrecorded"] == ["personium-152.0.7977.82"]
    assert body["orphan_records"] == ["personium-151.0.0.1"]


# ---------------------------------------------------------------------------
# THE RECORD SIDE. Read from the SAME glob the verifier iterates.
# ---------------------------------------------------------------------------


def test_the_record_side_is_the_verifiers_own_glob(audit, tmp_path):
    (tmp_path / "personium-1.2.3.4.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a record", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    assert audit.record_tags(tmp_path) == ["personium-1.2.3.4"]


def test_a_record_that_does_not_parse_still_counts_as_existing(audit, tmp_path):
    """This gate asks whether a record EXISTS. Whether it AGREES WITH THE BYTES
    is `ps343_verify_release_provenance.py`'s separate answer, and conflating
    them would make a malformed record read as an unrecorded release — the
    wrong red, pointing at the wrong remedy."""
    (tmp_path / "personium-1.2.3.4.json").write_text("{ not json", encoding="utf-8")
    assert audit.record_tags(tmp_path) == ["personium-1.2.3.4"]
    code, _ = audit.classify(["1.2.3.4"], audit.record_tags(tmp_path))
    assert code == audit.QUIET


def test_a_missing_records_directory_is_not_a_crash(audit, tmp_path):
    assert audit.record_tags(tmp_path / "nope") == []


def test_the_real_repository_reconciles_quiet_against_its_own_records(audit):
    """The QUIET arm against the REAL record directory, with a synthesised
    published set matching what this repository has actually published. Hermetic
    — no network call — and it is the control the injected red arm is measured
    against."""
    records = audit.record_tags(RECORDS_DIR)
    assert records, "engine/releases/ carries no provenance record at all"
    published = [t[len("personium-"):] for t in records]
    code, body = audit.classify(published, records)
    assert code == audit.QUIET
    assert body["unrecorded"] == []


# ---------------------------------------------------------------------------
# THE CLI. The exit codes the workflow actually reads.
# ---------------------------------------------------------------------------


def _run(*args, cwd=REPO):
    return subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def test_the_selftest_runs_and_passes_with_no_network(audit):
    proc = _run("--selftest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "the judgement still distinguishes" in proc.stdout


def test_the_selftest_covers_all_three_outcomes(audit):
    seen = {audit.classify(p, r, reason=reason or "")[0] for _, p, r, reason, _, _ in audit.SELFTEST_CASES}
    assert seen == {audit.QUIET, audit.UNRECORDED_RELEASE, audit.CANNOT_ENUMERATE}


def test_the_selftest_fails_when_the_judgement_is_broken(audit, monkeypatch):
    """The selftest's own falsification: break `classify` and the selftest must
    go non-zero. A selftest that cannot fail is decoration."""
    monkeypatch.setattr(audit, "classify", lambda *a, **k: (audit.QUIET, {"unrecorded": []}))
    assert audit.selftest() == 1


def test_the_cli_exits_one_and_names_the_release_when_injected(tmp_path):
    """The RED arm through the CLI, against the REAL record directory — the same
    call the workflow's falsification step makes."""
    proc = _run("--inject-published", "personium-999.0.0.0")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "personium-999.0.0.0" in proc.stdout


def test_the_injection_can_only_add_to_the_published_set(audit):
    """The falsification arm must not be able to manufacture a GREEN. It widens
    the published set, and a wider published set can only produce the same or
    more unrecorded releases."""
    src = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "published = sorted(set((published or []) + injected)" in src
    base_code, _ = audit.classify(["152.0.7977.75"], ["personium-152.0.7977.75"])
    wider_code, _ = audit.classify(
        ["152.0.7977.75", "999.0.0.0"], ["personium-152.0.7977.75"]
    )
    assert base_code == audit.QUIET
    assert wider_code == audit.UNRECORDED_RELEASE


def test_the_github_output_contract_carries_the_verdict(audit, tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = audit.main(
        ["--inject-published", "personium-999.0.0.0", "--out", str(tmp_path / "a.json")]
    )
    assert code == 1
    body = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    assert body["outcome"] == "UNRECORDED_RELEASE"
    assert body["green"] == "false"
    assert body["report"] == "true"
    assert "personium-999.0.0.0" in body["unrecorded"]
    assert "personium-999.0.0.0" in body["title"]


def test_the_issue_title_is_exact_matchable_and_carries_the_subject(audit):
    """`chromium-upstream-watch.yml`'s discipline: the title carries the status
    AND the subject, so re-running comments rather than filing the same news
    twice, and a tag moving from unrecorded to recorded does not quietly edit
    away the fact that it was once unrecorded."""
    _, red = audit.classify(["152.0.7977.82"], [])
    _, quiet = audit.classify(["152.0.7977.75"], ["personium-152.0.7977.75"])
    _, unmeasured = audit.classify(None, [], reason="x")
    titles = {
        audit.issue_title(audit.UNRECORDED_RELEASE, red),
        audit.issue_title(audit.QUIET, quiet),
        audit.issue_title(audit.CANNOT_ENUMERATE, unmeasured),
    }
    assert len(titles) == 3
    assert "personium-152.0.7977.82" in audit.issue_title(audit.UNRECORDED_RELEASE, red)


def test_the_markdown_report_names_the_missing_record_file(audit, tmp_path):
    _, body = audit.classify(["152.0.7977.82", "152.0.7977.75"], ["personium-152.0.7977.75"])
    md = audit.render(audit.UNRECORDED_RELEASE, body)
    assert "engine/releases/personium-152.0.7977.82.json" in md
    assert "NONE" in md


def test_the_unmeasured_report_refuses_to_read_as_a_pass(audit):
    _, body = audit.classify(None, [], reason="rate limited")
    md = audit.render(audit.CANNOT_ENUMERATE, body)
    assert "NOTHING WAS MEASURED" in md
    assert "not a pass" in md


# ---------------------------------------------------------------------------
# THE ENUMERATOR SEAM. `updater` keeps its contract.
# ---------------------------------------------------------------------------


def test_the_published_set_comes_from_the_shipped_enumerator(audit):
    src = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "engine_versions_newest_first" in src
    # And NOT a second copy of the tag prefix / the refs endpoint. PS-385's
    # coordination note names that drift explicitly: three neighbouring gates
    # resolving engine tags three different ways.
    assert "matching-refs" not in src.replace("`git/matching-refs/tags/personium-`", "")
    assert "ENGINE_TAG_PREFIX" not in src


def test_the_tag_seam_is_crossed_with_the_updaters_own_helpers(audit):
    src = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "updater.engine_tag(" in src
    assert "updater.version_from_tag(" in src
    # No hand-rolled slice of the prefix anywhere.
    assert 'startswith("personium-")' not in src
    assert "[len(" not in src


def test_the_enumerators_contract_is_not_modified():
    """⛔ OUT OF SCOPE, asserted rather than trusted. The operator's hourly
    engine-update path depends on `engine_versions_newest_first`'s `[]`
    semantics, and this gate reinterprets that value at its OWN call site."""
    src = (REPO / "src" / "services" / "engine" / "updater.py").read_text(encoding="utf-8")
    assert "`[]` on any failure, and on a repository with no engine tag yet" in src
    assert 'never as "GitHub is unreachable"' in src


# ---------------------------------------------------------------------------
# THE WORKFLOW. The properties that make it MEAN something.
# ---------------------------------------------------------------------------


def test_the_instrument_now_has_a_caller_in_dot_github():
    """AC 1, as the exact inverse of the finding: `git grep -l "ps343" --
    .github/` returned ZERO at the commit this was written on."""
    hits = [
        p
        for p in (REPO / ".github").rglob("*")
        if p.is_file()
        and p.suffix in {".yml", ".yaml", ".py"}
        and "ps343" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, "the PS-343 territory still has no caller under .github/"
    names = {p.name for p in hits}
    assert "engine-release-provenance-audit.yml" in names
    assert "ps343_release_audit.py" in names


def test_the_workflow_invokes_the_audit(workflow_text):
    assert "ps343_release_audit.py" in workflow_text


def test_the_selftest_runs_before_any_network_call(workflow_yaml):
    """`engine-gpu-variance.yml`'s ordering, and its stated reason: if the
    judgement is broken, a live reading that produced a green would be
    meaningless and the red this job reported would be the wrong red."""
    steps = _steps(workflow_yaml)
    names = [s.get("name", "") for s in steps]
    selftest = next(i for i, s in enumerate(steps) if "--selftest" in (s.get("run") or ""))
    live = next(
        i
        for i, s in enumerate(steps)
        if "--out" in (s.get("run") or "") and "--selftest" not in (s.get("run") or "")
    )
    assert selftest < live, names
    # And nothing before it reaches the network either.
    for step in steps[:selftest]:
        run = step.get("run") or ""
        assert "ps343_release_audit.py" not in run


def test_the_falsification_arm_runs_on_every_run_and_requires_red(workflow_yaml):
    """⛔ NON-WAIVABLE. Without it this job is a gate that has only ever been
    seen to pass — the live arm is expected to be QUIET forever."""
    steps = _steps(workflow_yaml)
    arm = next(s for s in steps if "falsification" in s.get("name", "").lower())
    assert "if" not in arm, "the falsification arm must not be conditional"
    run = arm["run"]
    assert "--inject-published" in run
    assert 'if [ "$code" -ne 1 ]' in run
    assert "::error::" in run


def test_the_falsification_arm_asserts_the_name_and_not_only_the_code(workflow_yaml):
    """An exit code alone would pass for a gate that went red for an unrelated
    reason and named nothing. The remedy for a red run is writing THAT
    release's record, which is impossible if the run does not print it."""
    arm = next(
        s for s in _steps(workflow_yaml) if "falsification" in s.get("name", "").lower()
    )
    run = arm["run"]
    assert "grep -q" in run
    assert "personium-999.0.0.0" in run


def test_the_quiet_arm_is_the_live_step_against_the_real_records(workflow_yaml):
    """The other half of the discrimination: the live step runs against the real
    record directory with NOTHING injected, and must be quiet. A gate that
    flagged every release would fail here and pass the falsification arm."""
    steps = _steps(workflow_yaml)
    live = next(s for s in steps if s.get("id") == "audit")
    run = live["run"]
    assert "--inject-published" in run  # only via the dispatch input
    assert 'if [ -n "${INJECT:-}" ]' in run, (
        "the injection must be conditional on the dispatch input, so a scheduled "
        "run reconciles the REAL published set"
    )


def test_unmeasured_fails_the_job(workflow_yaml):
    """⚠️ "We failed to look" must never wear the colour of "we looked and it
    was fine" — the same discipline `engine-gpu-variance.yml` and
    `chromium-upstream-watch.yml` apply to their own exit 2."""
    verdict = next(s for s in _steps(workflow_yaml) if s.get("name") == "Report the verdict")
    run = verdict["run"]
    assert "CANNOT_ENUMERATE)" in run
    assert "NOTHING WAS MEASURED" in run
    # Exactly one green path, and it is the QUIET one.
    assert 'if [ "${GREEN:-false}" = "true" ]' in run
    assert run.rstrip().endswith("exit 1")


def test_an_unknown_outcome_fails_rather_than_guessing(workflow_yaml):
    verdict = next(s for s in _steps(workflow_yaml) if s.get("name") == "Report the verdict")
    assert "does not know how to read" in verdict["run"]


def test_the_verdict_is_not_swallowed_by_continue_on_error(workflow_yaml):
    """`continue-on-error` on the audit step exists so the report is filed and
    the artifact uploaded even on a red reading — not to swallow the verdict.
    The verdict step re-asserts it."""
    steps = _steps(workflow_yaml)
    live = next(s for s in steps if s.get("id") == "audit")
    assert live.get("continue-on-error") is True
    verdict = next(s for s in steps if s.get("name") == "Report the verdict")
    assert "continue-on-error" not in verdict
    assert steps.index(verdict) > steps.index(live)


def test_the_job_writes_only_issues(workflow_yaml):
    perms = workflow_yaml["permissions"]
    assert perms["contents"] == "read"
    assert perms["issues"] == "write"
    assert set(perms) == {"contents", "issues"}


def test_the_report_is_not_filed_from_a_pull_request(workflow_yaml):
    """A PR arm that filed issues would file one per push."""
    step = next(s for s in _steps(workflow_yaml) if "report issue" in s.get("name", "").lower())
    assert "github.event_name != 'pull_request'" in step["if"]


def test_the_issue_is_matched_by_exact_title_not_the_search_index(workflow_yaml):
    """`gh issue list --search` is fuzzy and eventually-consistent, so a
    near-miss would file a duplicate every single day."""
    step = next(s for s in _steps(workflow_yaml) if "report issue" in s.get("name", "").lower())
    run = step["run"]
    # Comment lines stripped: the block's own prose NAMES `--search` as the
    # thing it refuses to use, and a raw-text assertion would read that
    # explanation as the defect it warns about.
    executable = "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--search" not in executable
    assert "select(.title == $t)" in executable
    assert "--arg t" in executable


def test_the_published_set_is_not_pinned(workflow_yaml):
    """The risk is whatever the newest published release is, which is exactly
    what a pin hides. Asserted over the parsed EXECUTABLE lines, so a version
    cited in prose (the header names the releases this was written against; the
    dispatch input's description gives an example) cannot be mistaken for a pin,
    and a pin cannot hide inside a comment-looking line."""
    version_re = re.compile(r"\b\d+\.\d+\.\d{3,}\.\d+\b")
    for step in _steps(workflow_yaml):
        for key in ("run", "with", "env"):
            body = step.get(key)
            if body is None:
                continue
            text = body if isinstance(body, str) else json.dumps(body)
            executable = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            found = version_re.findall(executable)
            assert not found, (step.get("name"), key, found)


def test_no_run_body_carries_an_unintended_expression_delimiter(workflow_yaml):
    """⭐ MEASURED, NOT ANTICIPATED. The first push of this workflow FAILED TO
    PARSE, with zero jobs and no log — GitHub expands expressions in a `run:`
    body as TEXT before any shell sees it, so a `#` comment does NOT protect
    one, and this file's own prose about not interpolating the dispatch input
    was written using the literal delimiters.

    A workflow that never executed is exactly the artifact class this job exists
    to eliminate, so the regression is pinned rather than remembered. Every
    interpolation this job actually needs lives in `env:`, where the value is
    passed rather than pasted."""
    delimiter = "$" + "{{"
    for step in _steps(workflow_yaml):
        run = step.get("run")
        if not run:
            continue
        assert delimiter not in run, (
            step.get("name"),
            "an expression delimiter in a `run:` body is expanded as text even "
            "inside a shell comment — pass the value through `env:` instead",
        )


def test_the_workflow_states_its_reasoning_its_cadence_its_cost_and_the_empty_reading(
    workflow_text, workflow_yaml
):
    """AC 7. Four claims, each asserted rather than assumed — a comment block
    that stopped saying one of them would be a job nobody could re-decide."""
    # Every existing gate addressed BY NAME.
    for other in (
        "chromium-upstream-watch.yml",
        "engine-gpu-variance.yml",
        "engine-autoupdate.yml",
        "engine-trial-build.yml",
        "published-engine-verdict.yml",
        "ci.yml",
    ):
        assert other in workflow_text, other
    # The cadence, and the reason it differs from its neighbours.
    assert "cron: \"40 8 * * *\"" in workflow_text
    assert "DAILY RATHER THAN WEEKLY" in workflow_text
    # The cost, stated rather than copied.
    assert "45 minutes" in workflow_text and "188 MB" in workflow_text
    assert "NO engine download" in workflow_text
    # The `[]` reading.
    assert "CANNOT_ENUMERATE" in workflow_text
    assert "NEVER QUIET" in workflow_text


def test_the_empty_reading_is_stated_in_the_script(audit):
    """⛔ §3: an unstated choice is not a choice. The decision and its cost are
    written down where the code that makes it lives."""
    src = AUDIT_SCRIPT.read_text(encoding="utf-8")
    assert "THIS GATE READS `[]` AS `CANNOT_ENUMERATE`" in src
    # And the cost of the choice, not only the choice.
    assert "reads exit 2 here forever" in src


def test_the_job_does_not_download_an_engine(workflow_yaml):
    """The cost claim, asserted. This is a set difference; the moment it grows a
    download it is a different job with a different cadence argument."""
    for step in _steps(workflow_yaml):
        run = step.get("run") or ""
        executable = "\n".join(
            line for line in run.splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in ("gh release download", "xvfb", "requirements.txt", "playwright"):
            assert forbidden not in executable, (step.get("name"), forbidden)
    assert workflow_yaml["jobs"]["audit"]["timeout-minutes"] <= 15


def test_the_neighbouring_workflows_are_untouched():
    """⛔ OUT OF SCOPE, asserted rather than trusted: PS-385 forbids modifying
    the three neighbours. Their identifying comment blocks are pinned, so a
    later edit that rewrote one of them would take this down."""
    for name, marker in (
        ("chromium-upstream-watch.yml", "ungoogled-chromium"),
        ("engine-gpu-variance.yml", "Prove the gate still goes red"),
        ("engine-autoupdate.yml", "engine-baseline.txt"),
    ):
        text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert marker in text, name
        assert "ps343_release_audit" not in text, name
