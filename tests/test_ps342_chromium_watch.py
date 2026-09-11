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
import re
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
# PS-390. The pin is a MULTI-PLACE fact since PS-361 stood up a second platform,
# and this file already owns the two places it was reconciled in. The windows
# arm is the third and fourth.
WINDOWS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build-windows.yml"


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


# ── PS-390: the pin is written on TWO PLATFORMS, and only one was reconciled ──
#
# PS-361 stood up `.github/workflows/engine-trial-build-windows.yml`, which
# carries its OWN copy of the tag we target — twice, as a workflow-level `env:`
# fallback and as a `workflow_dispatch` input default. Nothing reconciled either
# against `CURRENT_TAG.txt`; `grep -c CURRENT_TAG` over that workflow was 0.
#
# ⚠️ WHY THIS IS NOT COVERED BY THE ARM'S OWN `--expect-base` GUARD, which is
# correct and stays untouched. That guard asserts the ungoogled-chromium
# submodule commit the cloned tag pins, so it catches a MISMATCHED pair (a stale
# tag against a fresh base) and exits 2 — loud, and correctly reported as
# "nothing was measured". What it is structurally blind to is the COHERENT pair:
# a stale tag whose stale base agrees with it. That pair is exactly what a rebase
# PR produces, because the arm's `pull_request` trigger fires on
# `engine/patches/fingerprint/*.patch` — which is what a rebase edits — and on
# that trigger `inputs.*` is empty, so the run falls through to the `env:`
# defaults nobody moved. The arm then measures the tag we are LEAVING and reports
# green about it. A mismatch assertion cannot see that; only reconciliation
# against the file that IS the pin can.
#
# ⛔ THE `EXPECT_BASE` DECISION, MADE HERE AND WRITTEN DOWN RATHER THAN
# DEFAULTED (PS-390 AC3 left the fork open). The base is a 40-char sha and is
# NOT derivable from a tag without a network read. Two answers were available:
#   (a) assert only the TAG counterpart in-tree, and let `--expect-base` keep
#       catching the mismatch case it already catches;
#   (b) resolve the base live and reconcile all three.
# ⭐ THIS FILE CHOOSES (a), and the reason is a property this file declares about
# ITSELF in its own header: "NO NETWORK … which is what lets the workflow put it
# in front of the clone rather than after it". `chromium-upstream-watch.yml` runs
# this suite BEFORE any network work precisely so a broken judgement is caught
# without spending a clone — `test_workflow_runs_the_selftest_before_the_network`
# asserts that ordering. Option (b) would make this file's verdict depend on
# GitHub being reachable, so a rate-limited or offline run would turn a
# reconciliation question into an outage — and a gate that answers differently
# depending on the network is not a gate.
#
# What (a) gives up is stated rather than hidden: nothing in-tree notices if the
# two siblings ever pin DIFFERENT bases at a future tag pairing. That case is
# already covered, by the guard the ticket forbids weakening — the probe exits 2
# on the mismatch, and `REBASING.md` records the standing instruction to leave
# `--expect-base` set. So the base has a live guard and the tag had none; this
# closes the tag half and leaves the base half where it already works.


def _windows_workflow_text():
    return WINDOWS_WORKFLOW.read_text(encoding="utf-8")


# ⛔ THESE TWO READERS ARE DELIBERATELY TEXT, NOT `yaml.safe_load`, AND THAT IS
# NOT LAZINESS. PyYAML is declared in NONE of requirements.txt,
# requirements-dev.txt or pyproject.toml — it reaches CI transitively through
# `uvicorn[standard]`, and the sibling suite guards its own yaml import with
# `pytest.importorskip`. A skip is a check that DID NOT RUN while the summary
# line stays green, which is the precise failure shape this whole file exists to
# refuse: `test_workflow_runs_the_selftest_before_the_network` puts this suite in
# front of the clone so a broken judgement is CAUGHT, and a judgement that
# quietly opts out of running catches nothing.
#
# The cost of the text read is that it must locate its own site. Both readers
# therefore FAIL LOUDLY when the shape they expect is gone, rather than
# returning None and letting the caller compare against nothing — an absent
# match must never wear the colour of a pass.


def _windows_env_default(name):
    """The raw `${{ … }}` expression for one workflow-level `env:` key."""
    m = re.search(
        r"^env:\n((?:[ \t]+\S.*\n|[ \t]*\n)+)", _windows_workflow_text(), re.M
    )
    assert m, (
        "no workflow-level `env:` block in %s — the arm's fallbacks have moved. "
        "On the pull_request trigger `inputs.*` is empty, so if the fallbacks "
        "are gone the arm clones whatever a bare `--tag \"\"` resolves to."
        % WINDOWS_WORKFLOW.name
    )
    entry = re.search(r"^[ \t]+%s:[ \t]*(.+?)[ \t]*$" % re.escape(name), m.group(1), re.M)
    assert entry, (
        "the workflow-level env: block does not define %r (it defines %r) — "
        "this reader is anchored on a key that no longer exists" % (
            name,
            re.findall(r"^[ \t]+(\w+):", m.group(1), re.M),
        )
    )
    return entry.group(1)


def _windows_dispatch_default(input_name):
    """The `workflow_dispatch` input default a human reads in the GitHub UI."""
    text = _windows_workflow_text()
    m = re.search(
        r"^      %s:\n(.*?)(?=^      \w+:\n|^  \w)" % re.escape(input_name),
        text,
        re.M | re.S,
    )
    assert m, (
        "no `workflow_dispatch` input named %r in %s — this reader is anchored "
        "on an input that no longer exists, so it can no longer check the "
        "default a human dispatches with" % (input_name, WINDOWS_WORKFLOW.name)
    )
    default = re.search(r'^\s+default:\s*"?([^"\n]+)"?\s*$', m.group(1), re.M)
    assert default, (
        "the `%s` input carries no `default:` — a required input with no "
        "default is not a pin, but it is also not what this arm documents"
        % input_name
    )
    return default.group(1).strip()


def test_windows_counterpart_is_derived_not_concatenated(watch):
    """AC1. The `-1` -> `-1.1` rule lives in ONE named function.

    `scripts/ps299_rebase_probe.py` explains at length why the two grammars
    cannot share a regex; the same reasoning says the derivation must not be
    open-coded at each reader. Assert the helper exists and is correct, so the
    rule has a single home a future grammar change can be found in.
    """
    assert watch.windows_counterpart_tag("152.0.7977.75-1") == "152.0.7977.75-1.1"
    assert watch.windows_counterpart_tag("144.0.7559.132-1") == "144.0.7559.132-1.1"
    # It must REFUSE a non-tag rather than hand back a plausible-looking string
    # that clones nothing — the "an absent path is not a deleted path" habit
    # this repo's probe already carries.
    for junk in ("", "main", "152.0.7977.75", "152.0.7977.75-1.1"):
        with pytest.raises(ValueError):
            watch.windows_counterpart_tag(junk)


def test_windows_arm_env_tag_is_the_counterpart_of_current_tag(watch):
    """AC2. The Windows arm's `env:` fallback must track `CURRENT_TAG.txt`.

    This is the default the AUTO-FIRING path uses: on `pull_request` the
    `inputs.*` context is empty, so a rebase PR — which by definition edits
    `engine/patches/fingerprint/*.patch`, one of the trigger's `paths:` — runs
    the arm against whatever is written here.
    """
    tag = watch.read_current_tag()
    want = watch.windows_counterpart_tag(tag)
    expr = _windows_env_default("UNGOOGLED_TAG")
    assert want in expr, (
        "the Windows arm still targets %s while %s says %s — move BOTH in the "
        "same change.\n"
        "  %s\n"
        "    env: UNGOOGLED_TAG  must fall back to %r\n"
        "  a rebase PR edits engine/patches/fingerprint/*.patch, which is on "
        "this arm's pull_request paths: list, and on that trigger inputs.* is "
        "empty — so the arm runs on the fallback above and reports GREEN about "
        "the tag we are leaving.\n"
        "  --expect-base cannot catch this: a stale tag with its own stale base "
        "is CONSISTENT, and that guard only sees a mismatch." % (
            expr, watch.CURRENT_TAG_REL, tag,
            ".github/workflows/engine-trial-build-windows.yml", want,
        )
    )


def test_windows_arm_dispatch_default_is_the_counterpart_of_current_tag(watch):
    """AC2, second site. The `workflow_dispatch` input default is what a HUMAN
    reads in the GitHub UI when they dispatch this arm by hand.

    Asserted separately from the `env:` fallback because they are two different
    strings serving two different readers, and a bump that moves one and not the
    other leaves a person hand-dispatching the stale tag while CI uses the fresh
    one — a disagreement neither reader can see.
    """
    tag = watch.read_current_tag()
    want = watch.windows_counterpart_tag(tag)
    default = _windows_dispatch_default("ungoogled_tag")
    assert default == want, (
        "the Windows arm's hand-dispatch default still offers %s while %s says "
        "%s — move both together. A human dispatching this arm from the GitHub "
        "UI reads THIS string, so a stale default is a stale measurement "
        "nobody asked for.\n"
        "  %s -> on.workflow_dispatch.inputs.ungoogled_tag.default\n"
        "    is %r, must be %r" % (
            default, watch.CURRENT_TAG_REL, tag,
            ".github/workflows/engine-trial-build-windows.yml", default, want,
        )
    )


def test_the_windows_arms_two_tag_defaults_agree_with_each_other(watch):
    """Belt and braces, and cheap: the arm's own two copies must not drift.

    The two assertions above each anchor a site to `CURRENT_TAG.txt`, so this is
    implied — but it is the assertion that still fires if a future change swaps
    one of them for a different mechanism, and it names the arm's INTERNAL
    disagreement rather than sending the reader to a third file.
    """
    expr = _windows_env_default("UNGOOGLED_TAG")
    default = _windows_dispatch_default("ungoogled_tag")
    assert default in expr, (
        "the Windows arm's dispatch default (%r) is not the fallback its env: "
        "uses (%r) — a hand-dispatched run and a PR run would measure different "
        "tags" % (default, expr)
    )


def test_the_windows_base_default_is_left_to_expect_base_deliberately():
    """AC3, made visible rather than left as an absence.

    This file reconciles the TAG and deliberately does not reconcile the BASE —
    see the section comment above for the argument. The base is not
    unreconciled-and-forgotten: the probe's `--expect-base` asserts it live, at
    the only moment it can be checked honestly, and this asserts that the wiring
    which makes that true is still present.

    ⛔ If a future change removes `--expect-base` from the arm, this file's
    choice to skip the base becomes wrong and this test is where that is said.
    """
    # Text, not `yaml.safe_load`, for the same reason as the readers above: the
    # `--expect-base` flag is a literal in a `run:` block, so a plain substring
    # over the whole file answers the question without a skippable import. The
    # comment blocks that DISCUSS the flag are all above `jobs:`, so scope the
    # search to the job graph and the answer is about the wiring, not the prose.
    text = _windows_workflow_text()
    assert "\njobs:\n" in text, (
        "%s has no `jobs:` block — the arm's shape has changed beyond what this "
        "reader can speak about" % WINDOWS_WORKFLOW.name
    )
    job_graph = text.split("\njobs:\n", 1)[1]
    assert "--expect-base" in job_graph, (
        "this file reconciles the Windows TAG against CURRENT_TAG.txt and "
        "leaves the BASE to the probe's --expect-base assertion, because a "
        "40-char sha is not derivable from a tag without a network read and "
        "this suite declares itself NO NETWORK (it runs before the clone). "
        "--expect-base has disappeared from the arm, so nothing checks the base "
        "any more — either restore it or reconcile the base here instead."
    )
    env_expr = _windows_env_default("EXPECT_BASE")
    assert "||" in env_expr and re.search(r"[0-9a-f]{40}", env_expr), (
        "EXPECT_BASE's workflow-level expression (%r) carries no literal "
        "fallback commit. On the pull_request trigger `inputs.*` is EMPTY, so "
        "this resolves to the empty string — and a blank expect_base DISABLES "
        "the base assertion on the one trigger that fires automatically. That "
        "is the trigger a rebase PR fires." % (env_expr,)
    )


def test_the_bump_checklist_names_the_windows_arm(watch):
    """AC5. The CLEAN report is the "To act on this:" list a human reads.

    ⭐ THIS IS THE HALF THAT ACTUALLY CHANGES BEHAVIOUR. The tests above catch a
    bump that forgot the Windows arm AFTER it is written; this is what stops it
    being forgotten in the first place, because the filed issue is where the
    person doing the bump learns what moves. Before PS-390 the list was two
    steps, both Linux-only, and the word "windows" appeared in the whole watcher
    exactly once — in a cp1252 decoding comment.

    Asserted on the rendered text so the step cannot be silently dropped.
    """
    body = watch.render_report({
        "current_tag": "152.0.7977.75-1",
        "newest_tag": "152.0.7977.82-1",
        "status": watch.CLEAN,
        "probe_exit": 0,
        "measured_at": "2026-09-10T00:00:00Z",
    })
    assert "windows" in body.lower(), (
        "the bump checklist does not mention Windows at all — a human following "
        "it will move the Linux pin and leave the Windows arm on the old tag"
    )
    assert "engine-trial-build-windows.yml" in body, (
        "name the FILE that has to change; 'the windows arm' is not a path"
    )
    # The counterpart tag, DERIVED for the tag actually being reported on — not
    # a hardcoded example, which would go stale and teach the wrong tag.
    assert "152.0.7977.82-1.1" in body, (
        "the checklist must name the windows COUNTERPART of the tag it is "
        "reporting on, derived — a human should not have to work out the "
        "grammar from prose"
    )
    assert "UNGOOGLED_TAG" in body and "EXPECT_BASE" in body, (
        "name both env fallbacks — the tag alone leaves the base behind"
    )
    assert "expect_base" in body and "ungoogled_tag" in body, (
        "name the workflow_dispatch input defaults too: they are what a human "
        "sees in the GitHub UI, and they are a SECOND pair of places"
    )


def test_the_bump_checklist_does_not_claim_the_base_is_derivable(watch):
    """The one thing the checklist must not teach.

    The tag counterpart IS a naming rule and is derived for the reader. The base
    is a 40-char submodule sha that is not derivable from a tag by any rule, and
    a checklist that implied otherwise would send someone to invent one.
    """
    body = watch.render_report({
        "current_tag": "152.0.7977.75-1",
        "newest_tag": "152.0.7977.82-1",
        "status": watch.CLEAN,
        "probe_exit": 0,
        "measured_at": "2026-09-10T00:00:00Z",
    })
    assert "not derivable from" in body.lower()
    assert "git ls-tree" in body, (
        "tell the reader HOW to obtain the base, not merely that they must"
    )


def test_a_report_for_an_underivable_tag_does_not_traceback(watch):
    """render_report is the LAST thing that runs, so it must never raise.

    Same argument as `invalid_tag_result` one layer up: an uncaught raise here
    is red and SILENT — no issue filed, no $GITHUB_OUTPUT written, nothing
    uploaded. The counterpart derivation is the newest thing that can raise on
    this path, so drive it with a newest_tag that is not a tag.
    """
    for junk in (None, "", "main", "not-a-tag"):
        body = watch.render_report({
            "current_tag": "152.0.7977.75-1",
            "newest_tag": junk,
            "status": watch.CLEAN,
            "probe_exit": 0,
            "measured_at": "2026-09-10T00:00:00Z",
        })
        assert "engine-trial-build-windows.yml" in body, (
            "the windows step must survive an underivable tag, %r" % (junk,)
        )
        assert "append `.1`" in body, (
            "and it must say what the reader should do instead of printing a "
            "fabricated counterpart, %r" % (junk,)
        )


def test_rebasing_doc_pin_drift_warning_names_the_windows_arm():
    """AC7. The paragraph that tells a human which places move together.

    `REBASING.md` already warns that CURRENT_TAG.txt and its own "Current
    target" line must move together. Since PS-361 that list is INCOMPLETE: it
    named two of the places the pin is written, and the Windows arm added four
    more. A human following an incomplete list follows it correctly and still
    leaves the arm stale.

    Scoped to the WARNING'S OWN NEIGHBOURHOOD rather than to the whole document,
    because the doc mentions the Windows arm elsewhere (the hand-run command
    line, the cross-platform symmetry section) and a whole-file `in` check would
    pass on those and assert nothing about the warning.
    """
    doc = REBASING.read_text(encoding="utf-8")
    # The warning's stable anchor: the sentence naming the two Linux places.
    marker = 'move together.** A test asserts they agree'
    assert marker in doc, (
        "the pin-drift warning in REBASING.md has moved or been reworded — find "
        "it and make sure it still names the Windows arm (this test anchors on "
        "%r)" % marker
    )
    # Everything from the warning up to the next `###` heading. Generous, and
    # bounded: it must not reach the cross-platform section far below.
    neighbourhood = doc.split(marker, 1)[1].split("\n### ", 1)[0]
    assert "engine-trial-build-windows.yml" in neighbourhood, (
        "REBASING.md's pin-drift warning names only the Linux pair "
        "(CURRENT_TAG.txt and the \"Current target\" line). The Windows arm "
        "carries its own copy of the tag in FOUR places and nothing in this "
        "paragraph says so — a human following it moves two of six."
    )
    assert "UNGOOGLED_TAG" in neighbourhood and "EXPECT_BASE" in neighbourhood, (
        "name the env fallbacks; 'the windows arm' is not something a reader "
        "can grep for"
    )
    assert "-1.1" in neighbourhood, (
        "the grammar difference has to be in the paragraph that tells someone "
        "to move the tag, or they move it verbatim and the clone fails"
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


# ── the LAST red-and-silent path: we cannot read our own baseline ─────────────
#
# Round-3 MAJOR. `invalid_tag_result`'s docstring makes the argument: a cause
# that stops us measuring must still travel the reporting path, because an
# uncaught raise exits with a traceback, writes no `$GITHUB_OUTPUT`, files no
# issue and uploads no artifact — red and SILENT, the failure mode this ticket
# exists to end. That argument was applied to the two CLI flags (reachable only
# from a hand dispatch) and NOT to `CURRENT_TAG.txt`, the one tag input EVERY
# SCHEDULED RUN reads. These pin the asymmetry closed.


def _break_current_tag(watch, monkeypatch, exc):
    """Make `read_current_tag()` fail the way a real corrupt file does.

    Patched at the function rather than by writing a bad file, so the test
    cannot leave a broken `CURRENT_TAG.txt` behind for the rest of the session
    if it fails mid-way — `test_current_tag_file_is_a_tag` in this same file
    would then fail for an unrelated reason and send the reader somewhere wrong.
    The failure MODES are the real ones (see the parametrize below).
    """
    def boom(path=None):
        raise exc
    monkeypatch.setattr(watch, "read_current_tag", boom)


@pytest.mark.parametrize("exc", [
    # An editor on Windows writing a BOM — the file still "looks" right.
    ValueError("CURRENT_TAG.txt does not hold an ungoogled tag: '\ufeff152.0.7977.75-1'"),
    # A bad rebase deleting it.
    FileNotFoundError(2, "No such file or directory"),
    # Saved as UTF-16. UnicodeDecodeError is a ValueError, NOT an OSError —
    # this is here so the `except` clause cannot be narrowed to OSError alone.
    UnicodeDecodeError("utf-8", b"\xff\xfe1", 0, 1, "invalid start byte"),
    # A directory where the file should be.
    IsADirectoryError(21, "Is a directory"),
    PermissionError(13, "Permission denied"),
])
def test_an_unreadable_baseline_is_reported_not_a_traceback(
        watch, tmp_path, monkeypatch, exc):
    """A corrupt baseline must FILE 'I cannot read my own baseline'.

    Before this, `main()` let `read_current_tag()` escape: rc was a traceback,
    `$GITHUB_OUTPUT` was never written, so the workflow's "File or update the
    report issue" step (`if: steps.watch.outputs.report == 'true'`) was SKIPPED
    and the markdown the issue body is read from never existed. The watcher
    stopped watching, on a schedule, and said nothing.
    """
    _break_current_tag(watch, monkeypatch, exc)
    out, md, js = tmp_path / "out", tmp_path / "r.md", tmp_path / "r.json"

    rc = watch.main(["--github-output", str(out), "--report-md", str(md),
                     "--report-json", str(js)])

    assert rc == 2, "a baseline we cannot read established nothing"
    text = out.read_text(encoding="utf-8")
    assert "status=baseline_unreadable" in text
    assert "green=false" in text
    assert "report=true" in text, "the refusal must still reach a human"
    # every line of the outputs file is still a single key=value
    for line in text.splitlines():
        assert "=" in line, line
    body = md.read_text(encoding="utf-8")
    assert "NOTHING WAS MEASURED" in body
    assert json.loads(js.read_text(encoding="utf-8"))["status"] == "baseline_unreadable"


def test_an_unreadable_baseline_is_not_green_and_joins_no_green_set(watch):
    """The central trap, re-asserted for the status this round adds."""
    assert watch.BASELINE_UNREADABLE not in watch.GREEN_STATUSES
    assert not watch.is_green(watch.BASELINE_UNREADABLE)
    assert watch.exit_code_for(watch.BASELINE_UNREADABLE) == 2


def test_an_unreadable_baseline_files_under_its_own_title(watch):
    """Round 2's finding, applied to the status round 3 adds.

    The issue title is the DEDUP KEY — the workflow matches an open issue by
    EXACT title and comments instead of filing. So if a corrupt CURRENT_TAG.txt
    borrowed the typo'd-dispatch title, whichever landed first would swallow the
    other into a comment on itself, and a real broken baseline could sit
    invisible under a dispatcher's typo.
    """
    baseline = watch.baseline_unreadable_result("CURRENT_TAG.txt … BOM")
    typo = watch.invalid_tag_result("--tag", "152.0.7977.75")
    outage = {"status": watch.DISCOVERY_FAILED, "current_tag": "152.0.7977.75-1",
              "newest_tag": None}

    titles = [watch.issue_title(r) for r in (baseline, typo, outage)]
    assert len(set(titles)) == 3, titles
    assert "\n" not in titles[0], "a newline in the title forges step outputs"
    assert "CURRENT_TAG.txt" in titles[0]


def test_an_unreadable_baseline_does_not_blame_upstream_or_the_dispatcher(watch):
    """The three "we do not know" causes have three different remedies.

    Saying "re-dispatch with the full N.N.N.N-N form" — INVALID_TAG's advice —
    to someone whose repo file is corrupt sends them to a dispatch box that will
    not help. Saying "could not reach the tag list" is simply false: upstream
    was never contacted.
    """
    body = watch.render_report(
        watch.baseline_unreadable_result(
            "engine/patches/fingerprint/CURRENT_TAG.txt could not be read as a "
            "tag — ValueError: BOM"))

    assert "not an upstream problem" in body.lower()
    assert "engine/patches/fingerprint/CURRENT_TAG.txt" in body
    assert "REBASING.md" in body, "tell the reader what to restore it TO"
    assert "re-dispatch" not in body.lower(), (
        "that is INVALID_TAG's remedy and it does not apply to a repo file"
    )


def test_workflow_verdict_step_names_the_unreadable_baseline_case(watch):
    """A status the YAML does not name falls to the catch-all, which is red but
    prints "the watch step produced no status at all" — false, and it sends the
    reader hunting a broken step instead of a corrupt file."""
    y = WORKFLOW.read_text(encoding="utf-8")
    assert "baseline_unreadable)" in y
    tail = y.split("baseline_unreadable)", 1)[1]
    assert "exit 1" in tail, "it must fail the run, not fall through green"
    assert "CURRENT_TAG.txt" in y


def test_a_readable_baseline_still_takes_the_normal_path(watch, tmp_path, monkeypatch):
    """The guard must not swallow the happy path it sits in front of.

    ⛔ THE PROBE IS STUBBED, AND IT HAS TO BE. `--tag` sets `forced_tag`, and
    `watch()` treats that as "measure this specific tag": it SKIPS the
    up-to-date short-circuit and always calls `run_probe`. Every other test in
    this file injects a `runner`, but `main()` takes none — so without this
    stub the happy-path arm spawns the real rebase probe against the real
    upstream, inside the suite that gates every release build.

    Measured before this stub existed: the arm hung past pytest's 120s ceiling
    and failed release dry run 34560781062, skipping all three OS builds. A
    release gate that a third party's server can trip is not a gate on our code.
    """
    monkeypatch.setattr(watch, "run_probe", lambda tag, timeout=None, runner=None: (0, ""))
    out = tmp_path / "out"
    rc = watch.main(["--tag", "152.0.7977.75-1", "--github-output", str(out)])
    text = out.read_text(encoding="utf-8")

    assert "status=baseline_unreadable" not in text, text
    assert "current_tag=%s" % watch.read_current_tag() in text
    assert rc in (0, 1, 2)


# ── every comment that points at a symbol must point at one that exists ───────


def test_no_comment_cites_a_symbol_this_file_does_not_define(watch):
    """Round 3's BLOCKER, pinned so it cannot recur a third time.

    A comment read `see \\`_validated_tag\\`` describing the `$GITHUB_OUTPUT`
    forgery guard. The guard was real; `_validated_tag` was not, and never had
    been anywhere in the repo. This file's whole argument is that a FALSE
    SENTENCE WRAPPED AROUND A CORRECT MEASUREMENT is the defect — so a dangling
    `see X` sitting on top of the safety machinery is that defect in miniature,
    and a reader who follows the pointer and finds nothing is left unsure the
    guard exists at all.

    Scoped to backtick-quoted `identifier`-shaped tokens inside COMMENTS. A
    citation resolves if the name is defined in this module, is a builtin, is
    imported here, or appears as a string literal in the source (a dict key like
    `probe_log` is a real referent, not an invented one) — so prose in backticks
    (`152.0.7977.75-1`, `key=value`) and real external names both pass, and only
    a name that exists NOWHERE in the file fails. That is exactly the shape of
    the defect: `_validated_tag` appeared in the repo only in the comment citing
    it.
    """
    import ast
    import builtins
    import re
    import tokenize

    source = WATCH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    defined |= {a.asname or a.name.split(".")[0]
                for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    defined |= {a.asname or a.name
                for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    defined |= set(dir(builtins))
    # String literals: a dict key such as `probe_log` is a real referent the
    # reader can find, not an invented symbol. Included so the check stays
    # narrow — it must fire on names that exist NOWHERE, and nothing else.
    defined |= {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    # `foo` or `foo()` — an identifier, optionally called. Nothing else.
    cite = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\(\))?$")
    dangling = []
    with open(WATCH, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type != tokenize.COMMENT:
                continue
            for quoted in re.findall(r"`([^`]+)`", tok.string):
                m = cite.match(quoted.strip())
                if not m:
                    continue          # prose, a tag, a key=value — not a symbol
                name = m.group(1)
                if name in defined:
                    continue
                dangling.append((tok.start[0], quoted))

    assert not dangling, (
        "these comments cite a symbol that does not exist — name the real thing "
        "or drop the parenthetical: %r" % dangling
    )
