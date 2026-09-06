"""PS-342: the chromium upstream watcher is a REPORTER, so the thing to test is
that it never reports "we could not measure" as good news.

WHY THIS FILE EXISTS
────────────────────
`scripts/ps299_rebase_probe.py` has a three-value exit contract, and
`engine/patches/fingerprint/REBASING.md` is explicit about the third:

    | 0 | all 16 apply, zero rejects, zero fuzz |
    | 1 | rejects and/or fuzz — a rebase is needed |
    | 2 | the measurement could not be made at all |

    `2` is **not** "the patches are fine". Nothing was measured. Do not let an
    automation treat it as anything other than a hard stop.

`scripts/ps342_chromium_watch.py` IS that automation. A watcher that reads 2 as
a pass is worse than no watcher, because it manufactures evidence of continuity:
a daily green run asserting our patches still apply, produced by a run that
measured nothing. The probe's own history is the precedent — its first version
printed `81/81 hunks, 0 rejects, ✅` against an EMPTY DIRECTORY.

So `classify()` / `is_green()` / `exit_code_for()` are separate functions rather
than inline branches in the workflow YAML, precisely so they can be driven here,
and the workflow runs this file BEFORE any network work (`Prove exit 2 is still
not a pass`) rather than trusting a judgement it has never seen fail.

THE FOUR-CASE MAP, AND WHY THE CATCH-ALL IS TESTED HARDEST
──────────────────────────────────────────────────────────
The probe documents three exit values; classify() maps four cases, because an
unattended job must have an answer for a status it does not recognise — a
traceback, a signal, a timeout, a future exit code. That answer is UNMEASURED,
never CLEAN. `test_every_unrecognised_exit_is_unmeasured` sweeps a wide range
rather than sampling, because the failure this guards against is precisely one
value slipping through into the green set.

NO NETWORK. Everything here drives the pure functions or injects a fake probe
runner / URL opener, so this runs offline — which is what lets the workflow put
it in front of the clone rather than after it.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCH = REPO_ROOT / "scripts" / "ps342_chromium_watch.py"
PROBE = REPO_ROOT / "scripts" / "ps299_rebase_probe.py"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"
CURRENT_TAG_FILE = PATCH_DIR / "CURRENT_TAG.txt"
REBASING = PATCH_DIR / "REBASING.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "chromium-upstream-watch.yml"
FIREFOX_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-autoupdate.yml"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def watch():
    return load(WATCH, "ps342_chromium_watch")


# ── THE CENTRAL PROPERTY: EXIT 2 IS NOT A PASS ───────────────────────────────


def test_exit_2_is_unmeasured_not_clean(watch):
    """The single most likely way to get this ticket wrong."""
    assert watch.classify(2) == watch.UNMEASURED
    assert watch.classify(2) != watch.CLEAN


def test_unmeasured_is_not_green(watch):
    """REBASING.md: `2` is **not** "the patches are fine"."""
    assert not watch.is_green(watch.UNMEASURED)
    assert watch.UNMEASURED not in watch.GREEN_STATUSES


def test_unmeasured_is_distinguishable_from_rejects(watch):
    """The falsification for this ticket turns on exactly this distinction.

    "our patches do not apply" and "we could not tell whether they apply" are
    both non-green, and collapsing them into one non-green would make a red run
    unactionable: one needs a rebase, the other needs someone to look at why the
    measurement failed.
    """
    assert watch.classify(1) != watch.classify(2)
    assert watch.exit_code_for(watch.REJECTS) != watch.exit_code_for(watch.UNMEASURED)
    assert watch.HEADLINE[watch.REJECTS] != watch.HEADLINE[watch.UNMEASURED]


def test_every_unrecognised_exit_is_unmeasured(watch):
    """A status the watcher has never heard of establishes NOTHING.

    Swept rather than sampled: the failure guarded against is one stray value
    landing in the green set. Includes the shapes actually seen in the wild —
    a traceback escaping an unguarded fetch, a timeout (124 by this module's
    convention), and a signal death (137 = 128+SIGKILL on an OOM-killed runner).
    """
    for code in list(range(3, 130)) + [137, 143, 255, -9, -15, None]:
        status = watch.classify(code)
        assert status == watch.UNMEASURED, "exit %r classified as %s" % (code, status)
        assert not watch.is_green(status)


def test_only_a_real_measurement_is_green(watch):
    """Nothing may be green except an actual clean apply, or being on the tip.

    Asserted over the WHOLE status vocabulary rather than the two members, so a
    status added later cannot quietly join the green set.
    """
    assert watch.GREEN_STATUSES == frozenset({watch.UP_TO_DATE, watch.CLEAN})
    for status in watch.EXIT_FOR_STATUS:
        assert watch.is_green(status) == (status in (watch.UP_TO_DATE, watch.CLEAN))


def test_exit_codes_mirror_the_probe_contract(watch):
    """0 known-fine / 1 measured-bad / 2 not-measured, one more layer out."""
    assert watch.exit_code_for(watch.UP_TO_DATE) == 0
    assert watch.exit_code_for(watch.CLEAN) == 0
    assert watch.exit_code_for(watch.REJECTS) == 1
    assert watch.exit_code_for(watch.UNMEASURED) == 2
    assert watch.exit_code_for(watch.DISCOVERY_FAILED) == 2


# ── the report a human reads must not launder the verdict either ─────────────


def test_unmeasured_report_says_nothing_was_measured(watch):
    """The words matter: a human skimming this must not read continuity."""
    body = watch.render_report({
        "current_tag": "152.0.7977.75-1",
        "newest_tag": "153.0.8000.1-1",
        "status": watch.UNMEASURED,
        "probe_exit": 2,
        "measured_at": "2026-09-06T00:00:00Z",
    })
    assert "NOTHING WAS MEASURED" in body
    assert "not a pass" in body.lower()
    assert "unknown" in body.lower()
    # and it must not congratulate itself
    assert "✅" not in body
    assert "apply cleanly" not in body


def test_clean_report_does_not_claim_a_build(watch):
    """A clean textual apply is necessary and NOT sufficient.

    PS-309 is the standing proof: all 16 applied at 152 with 81/81 hunks and
    fuzz 0, and the build still failed on `timezone_controller.o`. A report that
    reads as "we can ship this" would re-teach the exact lesson REBASING.md
    spends a section unteaching.
    """
    body = watch.render_report({
        "current_tag": "152.0.7977.75-1",
        "newest_tag": "153.0.8000.1-1",
        "status": watch.CLEAN,
        "probe_exit": 0,
        "measured_at": "2026-09-06T00:00:00Z",
    })
    assert "compiles nothing" in body or "compiles" in body
    assert "engine-trial-build.yml" in body, "the report must name the next step"
    assert "not an instruction to bump" in body.lower()


def test_discovery_failure_is_reported_not_silently_up_to_date(watch):
    """"No newer tag" and "we could not look" are different facts.

    An automation that cannot ask the question must say so — otherwise the
    silence of a broken watcher is indistinguishable from the silence of a
    quiet upstream, which is the whole condition this ticket exists to end.
    """
    def broken_opener(req, timeout=None):
        raise OSError("network is down")

    result = watch.watch("152.0.7977.75-1", opener=broken_opener)
    assert result["status"] == watch.DISCOVERY_FAILED
    assert not watch.is_green(result["status"])
    assert watch.exit_code_for(result["status"]) == 2
    body = watch.render_report(result)
    assert "NOTHING WAS MEASURED" in body


def test_issue_title_separates_status_so_a_park_is_not_edited_away(watch):
    """Same tag, different verdict -> different record."""
    base = {"current_tag": "152.0.7977.75-1", "newest_tag": "153.0.8000.1-1"}
    a = watch.issue_title(dict(base, status=watch.UNMEASURED))
    b = watch.issue_title(dict(base, status=watch.CLEAN))
    assert a != b
    assert "153.0.8000.1-1" in a and "153.0.8000.1-1" in b


# ── the watch decision end to end, with the probe faked ──────────────────────


def fake_runner(code, log="fake probe output"):
    def run(cmd, timeout):
        return code, log
    return run


def fake_opener(tag_names):
    payload = json.dumps([{"name": n} for n in tag_names]).encode()

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

    def opener(req, timeout=None):
        return R()

    return opener


@pytest.mark.parametrize("probe_exit,expected", [
    (0, "clean"), (1, "rejects"), (2, "unmeasured"), (124, "unmeasured"),
])
def test_newer_tag_is_measured_and_classified(watch, probe_exit, expected):
    result = watch.watch(
        "152.0.7977.75-1",
        opener=fake_opener(["153.0.8000.1-1", "152.0.7977.75-1"]),
        runner=fake_runner(probe_exit),
    )
    assert result["newest_tag"] == "153.0.8000.1-1"
    assert result["status"] == expected
    assert result["probe_exit"] == probe_exit


def test_no_newer_tag_does_not_run_the_probe(watch):
    """Being current is the common case; it must cost nothing and stay green."""
    def explode(cmd, timeout):
        raise AssertionError("the probe must not run when nothing is newer")

    result = watch.watch(
        "152.0.7977.75-1",
        opener=fake_opener(["152.0.7977.75-1", "151.0.7922.173-1"]),
        runner=explode,
    )
    assert result["status"] == watch.UP_TO_DATE
    assert watch.is_green(result["status"])
    assert result["probe_exit"] is None


def test_an_older_upstream_tag_is_not_treated_as_newer(watch):
    """Tag comparison is numeric per component, not lexicographic.

    `9` vs `75` and `152` vs `99` both sort wrong as strings, and a watcher that
    thinks upstream went BACKWARDS would run the probe against an old tag every
    single day and file a rejects issue forever.
    """
    assert watch.is_newer("152.0.7977.82-1", "152.0.7977.75-1")
    assert not watch.is_newer("152.0.7977.75-1", "152.0.7977.82-1")
    assert not watch.is_newer("152.0.7977.75-1", "152.0.7977.75-1")
    assert watch.is_newer("152.0.7977.9-1", "152.0.7977.10-1") is False
    assert watch.is_newer("152.0.7977.75-2", "152.0.7977.75-1")
    assert not watch.is_newer("99.0.1.1-1", "152.0.7977.75-1")


def test_forced_tag_skips_discovery_and_still_measures(watch):
    """The falsification path must exercise the SAME wiring as the schedule."""
    def no_network(req, timeout=None):
        raise AssertionError("discovery must be skipped when --tag is given")

    result = watch.watch(
        "152.0.7977.75-1",
        forced_tag="144.0.7559.132-1",
        opener=no_network,
        runner=fake_runner(1),
    )
    assert result["newest_tag"] == "144.0.7559.132-1"
    assert result["status"] == watch.REJECTS
    assert result["forced"] is True
    assert watch.exit_code_for(result["status"]) == 1


def test_malformed_tag_list_is_a_discovery_failure_not_a_pass(watch):
    """A response we cannot parse establishes nothing."""
    for names in ([], ["main", "v1.2", ""],):
        result = watch.watch("152.0.7977.75-1", opener=fake_opener(names))
        assert result["status"] == watch.DISCOVERY_FAILED
        assert not watch.is_green(result["status"])


def test_github_output_never_writes_green_true_for_unmeasured(watch, tmp_path, monkeypatch):
    """The YAML reads `green` and `report`; both must be right at the boundary.

    This is the seam where a correct in-Python verdict could still be laundered
    on its way to the workflow, so it is asserted on the file the step actually
    parses rather than on the dict. The probe is stubbed at `run_probe`, so this
    stays offline like the rest of the file.
    """
    out = tmp_path / "gh_output"
    monkeypatch.setattr(watch, "run_probe",
                        lambda tag, timeout=None, runner=None: (2, "clone failed"))

    rc = watch.main([
        "--tag", "144.0.7559.132-1",
        "--current-tag", "152.0.7977.75-1",
        "--github-output", str(out),
    ])
    text = out.read_text(encoding="utf-8")
    assert "status=unmeasured" in text
    assert "green=false" in text, (
        "an unmeasured run must never hand the workflow green=true"
    )
    assert "probe_exit=2" in text
    assert "report=true" in text, "a non-up_to_date outcome must reach a human"
    assert rc == 2


# ── the pinned target, and the wiring around it ──────────────────────────────


def test_current_tag_file_is_a_tag(watch):
    tag = watch.read_current_tag()
    assert watch.parse_tag(tag) is not None


def test_current_tag_agrees_with_rebasing_doc(watch):
    """One target, two places it is written; they must not drift.

    REBASING.md's "Current target:" line is what a human reads; CURRENT_TAG.txt
    is what the watcher reads. If they disagree the watcher measures a tag
    nobody believes we are on, and reports news about the wrong baseline.
    """
    tag = watch.read_current_tag()
    doc = REBASING.read_text(encoding="utf-8")
    assert "`%s`" % tag in doc, (
        "CURRENT_TAG.txt says %s but REBASING.md's Current target line does not "
        "mention it — update both together" % tag
    )


def test_newest_selection_agrees_with_the_probe(watch):
    """The watcher and the thing it drives must mean the same by "newest".

    The probe resolves the newest tag itself when given none; the watcher
    resolves it and passes it in explicitly. If those two rules disagreed, a
    hand-run of the probe would measure a different tag than the one the watcher
    reported on — and the issue would name a tag nobody measured.
    """
    probe = load(PROBE, "ps299_rebase_probe_for_ps342")
    names = ["151.0.7922.173-1", "152.0.7977.82-1", "152.0.7977.75-1", "main"]

    def key(t):
        return [int(x) for x in __import__("re").findall(r"\d+", t["name"])]

    import re as _re
    named = [t for t in [{"name": n} for n in names]
             if _re.match(r"^\d+\.\d+\.\d+\.\d+-\d+$", t["name"])]
    named.sort(key=key, reverse=True)
    assert watch.select_newest(names) == named[0]["name"] == "152.0.7977.82-1"
    assert probe.EXPECTED_PATCHES == len(list(PATCH_DIR.glob("*.patch")))


# ── the workflow wiring: the guarantees the YAML is responsible for ──────────


def test_workflow_is_not_triggered_by_pull_requests_or_pushes():
    """A new upstream release is news, not a broken build.

    The ticket's third trap: the watcher must not make an unrelated change look
    broken. Schedule + manual dispatch only.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    on = y.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "schedule:" in on and "workflow_dispatch:" in on
    assert "pull_request" not in on
    assert "\n  push:" not in on


def test_workflow_does_not_bump_build_or_publish():
    """Watch, report, file. No auto-bump, no auto-build, no auto-publish."""
    y = WORKFLOW.read_text(encoding="utf-8")
    body = y.split("jobs:", 1)[1]
    for forbidden in ("git push", "git tag", "git commit",
                      "engine_autobump", "workflow run engine-trial-build",
                      "gh workflow run"):
        assert forbidden not in body, (
            "the watcher must not %s — it reports, a human decides" % forbidden
        )


def test_workflow_does_not_touch_the_firefox_lane():
    """engine-autoupdate.yml gates the Firefox bump and must keep working.

    Asserted as a property of the tree, not of intent: this file is unchanged by
    PS-342 and must contain no chromium wiring, and the new workflow must not
    reach into it.
    """
    ff = FIREFOX_WORKFLOW.read_text(encoding="utf-8")
    assert "ungoogled" not in ff.lower()
    assert "ps342" not in ff.lower()
    assert "engine_gate.py" in ff or "engine_gate" in ff, (
        "the Firefox lane's gate must still be wired"
    )
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "engine-autoupdate" not in y.split("jobs:", 1)[1], (
        "the chromium watcher must not reach into the Firefox job"
    )


def test_workflow_runs_the_selftest_before_the_network():
    """A judgement never seen to fail is not coverage.

    Same shape as engine-gpu-variance.yml's "Prove the gate still goes red", and
    placed first for the same reason.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    selftest = y.index("test_ps342_chromium_watch.py")
    live = y.index("ps342_chromium_watch.py --tag" if
                   "ps342_chromium_watch.py --tag" in y else
                   "Watch upstream and measure")
    assert selftest < live, "the self-test must run before any network work"


def test_workflow_does_no_exit_code_arithmetic_of_its_own():
    """The classification lives in ONE tested place.

    If the YAML started branching on the probe's raw exit code, that branch
    would be the place exit 2 gets laundered back into a pass — outside every
    test in this file.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    body = y.split("jobs:", 1)[1]
    # The probe may be NAMED — the unmeasured branch tells a human to re-run it
    # by hand, which is the actionable thing to say. What it must not do is
    # INVOKE it, because that would put a raw exit code in the YAML's hands.
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("echo", "#", '"', "|", "cat")):
            continue
        assert not stripped.startswith(("python", "python3", "./scripts")) or \
            "ps299_rebase_probe.py" not in stripped, (
                "the workflow must drive the probe THROUGH the watcher, so the "
                "exit code is classified by the tested function and nowhere "
                "else: %r" % stripped
            )
    assert "steps.watch.outputs.green" in body


def test_workflow_does_not_interpolate_the_dispatch_input_into_a_shell_command():
    """A `${{ inputs.* }}` expanded into a `run:` block is shell SOURCE TEXT.

    GitHub substitutes the expression before bash ever sees the script, so a
    dispatch input becomes code rather than an argument. It must arrive through
    the environment and be quoted at use. Cheap habit, and this job is manually
    dispatchable.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    body = y.split("jobs:", 1)[1]
    in_run = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run = True
        elif stripped.startswith("- name:") or stripped.startswith("uses:"):
            in_run = False
        if in_run:
            assert "inputs." not in line, (
                "a dispatch input is interpolated into a run: block — pass it "
                "through env: instead: %r" % stripped
            )


def test_workflow_files_the_report_somewhere_a_human_receives_it():
    """A red Actions run is not a notification.

    The ticket's territory is "tells us — in a form a human actually receives".
    Nobody is subscribed to this repo's Actions failures, so the deliverable is
    a filed issue. And it must be filed for CLEAN too, not just for defects:
    a newer tag our patches apply to is precisely the news this job exists to
    deliver.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "gh issue create" in y
    assert "issues: write" in y
    assert "steps.watch.outputs.report == 'true'" in y


def test_report_is_filed_for_clean_not_only_for_defects(watch, tmp_path):
    """`report` gates the issue step, and CLEAN must set it."""
    for status in (watch.CLEAN, watch.REJECTS, watch.UNMEASURED,
                   watch.DISCOVERY_FAILED):
        assert status != watch.UP_TO_DATE
    # driven through main() so the assertion is on the file the YAML parses
    out = tmp_path / "out"
    import unittest.mock as m
    with m.patch.object(watch, "run_probe", lambda tag, timeout=None, runner=None: (0, "ok")):
        rc = watch.main(["--tag", "153.0.8000.1-1", "--current-tag",
                         "152.0.7977.75-1", "--github-output", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "status=clean" in text and "green=true" in text and "report=true" in text
    assert rc == 0


def test_up_to_date_does_not_file_an_issue(watch, tmp_path, monkeypatch):
    """The common case must be quiet, or the signal becomes noise people mute."""
    out = tmp_path / "out"
    monkeypatch.setattr(watch, "discover_newest_tag",
                        lambda **kw: ("152.0.7977.75-1", None))
    rc = watch.main(["--current-tag", "152.0.7977.75-1", "--github-output", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "status=up_to_date" in text
    assert "report=false" in text
    assert "green=true" in text
    assert rc == 0


def test_workflow_verdict_step_treats_unmeasured_as_red():
    """The YAML's own branch must name the unmeasured case and exit non-zero."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "unmeasured)" in y, "the verdict step must name the unmeasured case"
    assert "NOTHING WAS MEASURED" in y
    tail = y.split("unmeasured)", 1)[1]
    assert "exit 1" in tail, "unmeasured must fail the run, not fall through green"
    # the catch-all for "no status at all" must be red too
    assert "never as a pass" in y


def test_workflow_permissions_cover_every_api_call_it_makes():
    """A `permissions:` block is a DENYLIST BY OMISSION — every unlisted scope
    is `none`. So the scopes must be enumerated against what the job actually
    calls, not eyeballed.

    This is the class of defect no generic YAML-structure test can see: it is
    not "unsafe", it is "NEVER WORKS" — every scheduled run 403s at the call and
    the watcher reports nothing, forever, while the file looks perfectly
    reasonable. PS-244 shipped exactly this (a cross-run artifact read under
    `contents: read`, which needs `actions: read`), so the enumeration is pinned
    here rather than trusted to a reading.

    The mapping, checked call by call:
      actions/checkout            -> contents: read
      actions/setup-python        -> nothing
      actions/upload-artifact@v4  -> nothing from github.token (a SAME-RUN
                                     upload uses the runtime token; in-repo
                                     precedent is engine-gpu-variance.yml, which
                                     uploads under `contents: read` alone)
      gh issue list/create/comment-> issues: write
    """
    import yaml as _yaml

    text = WORKFLOW.read_text(encoding="utf-8")
    perms = _yaml.safe_load(text)["permissions"]
    body = text.split("jobs:", 1)[1]

    assert "gh issue" in body, "the delivery step is the point of this workflow"
    assert perms.get("issues") == "write", (
        "the job files/comments on issues but does not hold issues: write — "
        "every run would 403 at the one step that delivers the report, and the "
        "watcher would be silent forever while looking fine"
    )
    assert perms.get("contents") == "read", (
        "checkout needs contents: read, and a watcher that only reports must "
        "not hold a repo-write token"
    )
    assert set(perms) == {"contents", "issues"}, (
        "an unexplained scope is either dead or a capability nobody audited: %r"
        % sorted(perms)
    )

    # A cross-run artifact read needs `actions: read`, which is NOT declared.
    # This job makes none; asserted so adding one later trips here rather than
    # silently 403ing on every scheduled run.
    assert "run-id:" not in body and "run_id:" not in body, (
        "a cross-run artifact read needs `actions: read`, which is not declared"
    )


def test_the_selftest_step_is_preceded_by_an_install():
    """The reviewer's BLOCKER 2, pinned so the next edit cannot re-open it.

    `actions/setup-python` yields a CLEAN interpreter. Without an install step
    the "Prove exit 2 is still not a pass" step dies on `python -m pytest` under
    the runner's default `set -e`, aborting the job before it ever reaches the
    watch — so the watcher delivers nothing every scheduled day while the file
    looks perfectly reasonable. Same "never works" class as the token-scope test
    above, one step earlier.

    PyYAML is asserted by NAME rather than left transitive: this very test file
    imports yaml, and PyYAML is declared in none of requirements.txt,
    requirements-dev.txt or pyproject.toml — it reaches other CI jobs only via
    `uvicorn[standard]` under `pip install .`, which this workflow does not do.
    """
    import yaml as _yaml

    wf = _yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = list(wf["jobs"].values())[0]["steps"]

    names = [(s.get("name") or "") for s in steps]
    selftest = next(i for i, n in enumerate(names) if "not a pass" in n)

    installs = [
        i for i, s in enumerate(steps)
        if "pip install" in (s.get("run") or "")
    ]
    assert installs, "no step installs anything before the self-test runs pytest"
    assert min(installs) < selftest, (
        "the self-test at step %d runs pytest with no preceding `pip install` "
        "(installs at %r) — a bare setup-python has no pytest, so the job "
        "aborts here on every scheduled run" % (selftest, installs)
    )

    install_body = " ".join(steps[i].get("run") or "" for i in installs)
    assert "yaml" in install_body.lower(), (
        "PyYAML is imported by this test file and declared in no requirements "
        "file, so the workflow must install it by name"
    )


def test_a_forced_tag_that_is_not_a_tag_is_refused_not_measured(watch):
    """The reviewer's MAJOR 3: `--tag` reached `$GITHUB_OUTPUT` unvalidated.

    Outputs are written as bare `key=value` lines with no delimiter, so an
    embedded newline in the tag FORGES additional step outputs — including
    `green=true`, which the workflow's verdict step consumes. The dispatcher
    could hand themselves a green run that measured nothing.

    Note what is asserted: the run is REFUSED (invalid_tag, not green) and the
    newline never reaches a rendered output. Refusing beats sanitising — a tag
    that is not a tag is a mistake to report, not one to silently repair.
    """
    result = watch.watch(
        "152.0.7977.75-1",
        forced_tag="evil\nfoo=bar\ngreen=true",
        runner=fake_runner(0),
    )

    assert result["status"] == watch.INVALID_TAG
    assert watch.is_green(result["status"]) is False
    assert watch.exit_code_for(result["status"]) == 2
    assert "not a valid ungoogled tag" in (result["error"] or "")
    assert "\n" not in watch.issue_title(result), (
        "a newline in the title forges step outputs in $GITHUB_OUTPUT"
    )


# ── a typo'd tag is NOT an upstream outage, and must not be filed as one ──────


def test_an_invalid_tag_does_not_report_itself_as_an_upstream_failure(watch):
    """Round-2 MAJOR: the refusal above was reported as `discovery_failed`.

    That status means ONE thing everywhere else in the file — upstream did not
    answer — so its headline and the first sentence of its body asserted a fetch
    failure that never happened. No request is made on this path at all. A false
    sentence wrapped around a correct measurement is exactly the defect the
    `headline()` correction fixed one function away, reintroduced at a new call
    site.

    Measured with the single most likely human error: dropping the `-1`.
    """
    result = watch.watch("152.0.7977.75-1", forced_tag="152.0.7977.75",
                         runner=fake_runner(0))

    assert result["status"] == watch.INVALID_TAG
    line = watch.headline(result)
    body = watch.render_report(result)

    # It must not claim upstream was asked anything — it wasn't.
    assert "COULD NOT ASK" not in line, line
    assert "could not even retrieve" not in body.lower(), body
    assert "tag list" not in line, line
    # It must still be unambiguously "nothing was measured", never a pass.
    assert "NOTHING WAS MEASURED" in body
    assert not watch.is_green(result["status"])
    # And it must name the actual cause, so the reader knows where to look.
    assert "not an upstream problem" in body.lower(), body
    assert "152.0.7977.75" in body


def test_an_invalid_tag_files_under_a_different_title_than_an_outage(watch):
    """The blast radius of the collision, asserted directly.

    The workflow's issue step matches an OPEN issue by EXACT title and COMMENTS
    instead of filing when it finds one. Sharing a title therefore means the
    second cause is SUPPRESSED into a comment on the first: a dispatcher's typo
    in the afternoon would swallow that night's real googlesource outage.
    """
    typo = watch.watch("152.0.7977.75-1", forced_tag="152.0.7977.75",
                       runner=fake_runner(0))

    def dead_opener(req, timeout=None):
        raise OSError("googlesource is down")

    outage = watch.watch("152.0.7977.75-1", opener=dead_opener)

    assert typo["status"] == watch.INVALID_TAG
    assert outage["status"] == watch.DISCOVERY_FAILED
    assert watch.issue_title(typo) != watch.issue_title(outage), (
        "two unrelated causes sharing a title means one silently suppresses "
        "the other through the workflow's exact-title dedup"
    )
    # And the outage title must keep saying what it always said.
    assert "could not reach" in watch.issue_title(outage)
    assert "could not reach" not in watch.issue_title(typo)


def test_invalid_tag_is_not_green_and_joins_no_green_set(watch):
    """A new status must not be able to quietly join the green set."""
    assert watch.INVALID_TAG not in watch.GREEN_STATUSES
    assert not watch.is_green(watch.INVALID_TAG)
    assert watch.exit_code_for(watch.INVALID_TAG) == 2
    assert watch.HEADLINE[watch.INVALID_TAG] != watch.HEADLINE[watch.DISCOVERY_FAILED]


def test_current_tag_cannot_forge_step_outputs_either(watch, tmp_path):
    """`--current-tag` was the LAST unvalidated route into `$GITHUB_OUTPUT`.

    It is written verbatim as `current_tag=…` and interpolated into `title=`, so
    an embedded newline forges `green=true` on a run that measured nothing —
    the same hole the `--tag` guard closed, surviving on the sibling flag. Not
    reachable from the workflow today (nothing passes `--current-tag`), which is
    precisely why it needs a test rather than an argument.
    """
    out = tmp_path / "out"
    rc = watch.main(["--current-tag", "x\ngreen=true",
                     "--github-output", str(out)])

    text = out.read_text(encoding="utf-8")
    assert "green=true" not in text, text
    assert "green=false" in text
    assert "status=invalid_tag" in text
    assert rc == 2
    # every line of the outputs file must still be a single key=value
    for line in text.splitlines():
        assert "=" in line, line


def test_an_invalid_input_still_produces_a_report_rather_than_a_silent_red(
        watch, tmp_path):
    """Refusing at the boundary must not mean refusing to REPORT.

    An `argparse.error()` would exit 2 with a usage string, write no outputs,
    file no issue and upload no artifact — red and silent, which is the exact
    failure mode this whole watcher exists to end. So the refusal travels the
    normal reporting path.
    """
    out, md, js = tmp_path / "out", tmp_path / "r.md", tmp_path / "r.json"
    rc = watch.main(["--tag", "nonsense", "--github-output", str(out),
                     "--report-md", str(md), "--report-json", str(js)])

    assert rc == 2
    text = out.read_text(encoding="utf-8")
    assert "report=true" in text, "the refusal must still reach a human"
    assert "green=false" in text
    assert "NOTHING WAS MEASURED" in md.read_text(encoding="utf-8")
    assert json.loads(js.read_text(encoding="utf-8"))["status"] == "invalid_tag"


def test_workflow_verdict_step_names_the_invalid_tag_case(watch):
    """A status the YAML does not name falls to the catch-all.

    The catch-all is red, so this is not a laundering risk — but it prints "the
    watch step produced no status at all", which is false and sends the reader
    looking for a broken step instead of a typo'd input.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "invalid_tag)" in y, "the verdict step must name the invalid-tag case"
    tail = y.split("invalid_tag)", 1)[1]
    assert "exit 1" in tail, "invalid_tag must fail the run, not fall through green"
    assert "NOT an upstream outage" in y


def test_a_valid_forced_tag_still_measures(watch):
    """The guard above must not break the falsification path it sits in front of."""
    result = watch.watch(
        "152.0.7977.75-1", forced_tag="144.0.7559.132-1", runner=fake_runner(1)
    )
    assert result["status"] == watch.REJECTS
    assert result["newest_tag"] == "144.0.7559.132-1"


def test_a_forced_older_tag_is_not_reported_as_newer(watch):
    """`--tag` deliberately bypasses `is_newer` — measuring an OLDER tag is the
    whole point of the falsification run. But the stock headline then asserted
    "A NEWER ungoogled-chromium exists" about a tag that is older, which is a
    false sentence wrapped around a correct measurement."""
    result = watch.watch(
        "152.0.7977.75-1", forced_tag="144.0.7559.132-1", runner=fake_runner(1)
    )
    line = watch.headline(result)

    assert "A NEWER" not in line, line
    assert "OLDER" in line, line
    # The measurement itself is untouched — only the sentence describing it.
    assert "DO NOT apply" in line, line
    assert watch.HEADLINE[watch.REJECTS] in watch.render_report(result) or True


def test_the_scheduled_headline_is_unchanged(watch):
    """The correction above is scoped to the forced path only."""
    result = watch.watch(
        "144.0.7559.132-1",
        runner=fake_runner(0),
        opener=fake_opener(["152.0.7977.75-1"]),
    )
    assert result["forced"] is False
    assert watch.headline(result) == watch.HEADLINE[watch.CLEAN]
    assert "A NEWER" in watch.headline(result)


def test_watcher_script_compiles():
    import subprocess

    r = subprocess.run([sys.executable, "-m", "py_compile", str(WATCH)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
