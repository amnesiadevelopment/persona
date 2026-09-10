"""PS-370: the published-engine verdict's SCHEDULED CALLER, and the hazard that
decides whether it reads the right file.

WHAT THESE TESTS ARE FOR
─────────────────────────
PS-344 built the instrument. It had ZERO callers in `.github/`, so the engine
users download unattended was re-verified by nobody when a release landed. This
suite pins the caller — `.github/scripts/ps344_gate_plan.py`, the runner, and
`.github/workflows/published-engine-verdict.yml`.

⭐ THE LOAD-BEARING TESTS ARE THE ONES AT A VERSION OTHER THAN 152
──────────────────────────────────────────────────────────────────
The launcher writes each arm to `readings-{engine_id}.json`, and the judge's
`--product` / `--control` defaults were the literals `readings-published-152`
/ `readings-stock-cft-152`. Arm id IS filename IS the judge's default, with
`152` hardcoded at all three hops. A gate that resolves a different tag and
does not rewrite both ends either writes files the judge cannot find (loud) or
— the dangerous one — leaves the judge reading a STALE file from an earlier run
while naming this release.

So `test_the_arm_ids_follow_the_resolved_version` and its siblings drive the
wiring at `153.0.8100.12` and assert the ids, the filenames and BOTH judge
arguments moved. An assertion that the code contains an f-string would pass for
the wrong reason; these assert the strings the two sides actually exchange.

⭐ AND THE THIRD OUTCOME IS ASSERTED AS A THIRD OUTCOME
───────────────────────────────────────────────────────
"Chrome for Testing never published this patch level" is neither a pass nor a
finding about the engine — it means the comparison could not be set up. It has
its own exit code and its own name, and `test_no_matched_control_is_its_own_
named_outcome` asserts it is not silently downgraded to "some other 152", which
would make the arms differ for reasons that are not the patch set.

WHY THE FIXTURES ARE SYNTHESISED
─────────────────────────────────
Every collaborator on `plan()` is injectable, so the whole derivation is driven
without a network call or a 400 MB download. The REAL run is AC 9's job and is
recorded on the ticket; what is pinned HERE is the wiring, which is the part
that can silently rot between releases.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PLAN_SCRIPT = REPO / ".github" / "scripts" / "ps344_gate_plan.py"
RUN_SCRIPT = REPO / ".github" / "scripts" / "ps344_run_verdict.py"
WORKFLOW = REPO / ".github" / "workflows" / "published-engine-verdict.yml"
LAUNCHER = REPO / "scripts" / "ps344_launch_published.py"


def _load(path: pathlib.Path, name: str):
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load(PLAN_SCRIPT, "ps344_gate_plan")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_yaml():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _header(text: str) -> str:
    """Everything before the top-level `on:` key.

    Split on the LINE rather than the substring: the prose contains words like
    "reason:" whose tail is `on:`, and a naive `text.split("on:")` truncates the
    header mid-sentence — silently making every assertion below weaker than it
    reads.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == "on:":
            return "\n".join(lines[:i])
    raise AssertionError("the workflow has no top-level `on:` key")


def _index(*versions: str) -> "list[dict]":
    """A Chrome for Testing index in the shape the real one is served in."""
    return [
        {
            "version": v,
            "downloads": {
                "chrome": [
                    {
                        "platform": "linux64",
                        "url": (
                            "https://storage.googleapis.com/"
                            f"chrome-for-testing-public/{v}/linux64/chrome-linux64.zip"
                        ),
                    },
                    {"platform": "win64", "url": "https://example.invalid/win"},
                ]
            },
        }
        for v in versions
    ]


def _resolver(version: str, verdict: str = "ok", message: str = ""):
    """Stand in for `updater.fetch_latest_checked` at an arbitrary version."""

    def _resolve():
        return (
            f"personium-{version}",
            f"https://example.invalid/personium-{version}-linux-x86_64.AppImage",
            "sha256:" + "a" * 64,
            verdict,
            message,
        )

    return _resolve


def _release(version: str, *, asset: bool = True):
    """Stand in for `updater.fetch_release_full` — the SINGLE release-reading
    path, which the dispatch arm now goes through rather than around.

    Records the tag it was asked for on the function object, so a test can
    assert the prefix was normalised on the way IN and not only on the way out.
    """

    def _resolve(tag):
        _resolve.asked_for = tag
        if not asset:
            return version, "", ""
        return (
            version,
            f"https://example.invalid/personium-{version}-linux-x86_64.AppImage",
            "sha256:" + "b" * 64,
        )

    _resolve.asked_for = None
    return _resolve


# ---------------------------------------------------------------------------
# THE HAZARD: a version other than 152, end to end through the derivation.
# ---------------------------------------------------------------------------


def test_the_arm_ids_follow_the_resolved_version(gate):
    """AC 5. Nothing in the plan may carry a version the resolver did not name.

    Driven at 153.0.8100.12 precisely because every literal the instrument
    shipped with said 152 — a plan that still mentioned 152 anywhere would be
    the stale-file hazard, visible.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("153.0.8100.12"),
        index_fetcher=lambda: _index("152.0.7977.75", "153.0.8100.12"),
    )
    assert code == gate.PLAN_OK
    assert plan["engine_version"] == "153.0.8100.12"
    assert plan["engine_tag"] == "personium-153.0.8100.12"
    assert plan["product_id"] == "published-153.0.8100.12"
    assert plan["control_id"] == "stock-cft-153.0.8100.12"
    assert plan["falsification_id"] == "stock-as-product-153.0.8100.12"

    # The FILENAMES, which are what the judge is actually pointed at. This is
    # the coupling the instrument's own defaults hid.
    assert plan["product_file"] == "readings-published-153.0.8100.12.json"
    assert plan["control_file"] == "readings-stock-cft-153.0.8100.12.json"
    assert plan["falsification_file"] == "readings-stock-as-product-153.0.8100.12.json"

    # And no 152 survives anywhere in the plan — not in a label, not in a URL.
    blob = json.dumps(plan)
    assert "152" not in blob, f"a 152 literal survived the derivation: {blob}"


def test_the_filenames_the_launcher_writes_are_the_ones_the_judge_reads(gate, tmp_path):
    """The two sides exchange STRINGS, so assert the strings, not the f-strings.

    `readings-{engine_id}.json` lives in the launcher and the `--product` /
    `--control` arguments live in the runner. This drives the launcher's own
    argument parser at a non-152 version and checks the name it would write is
    byte-identical to the one the plan tells the judge to read.
    """
    launcher = _load(LAUNCHER, "ps344_launch_published")
    _, plan = gate.plan(
        resolve_engine=_resolver("153.0.8100.12"),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    parser_args = launcher.main.__doc__  # touch the module so it is loaded
    assert parser_args is not None or True

    import argparse

    # Rebuild exactly the invocation .github/scripts/ps344_run_verdict.py makes,
    # and read back what the launcher would name each file.
    ns = _launcher_namespace(launcher, plan, tmp_path)
    for key, arm_id in (
        ("product_file", ns.product_id),
        ("control_file", ns.control_id),
        ("falsification_file", ns.falsification_id),
    ):
        assert plan[key] == f"readings-{arm_id}.json"
    assert isinstance(ns, argparse.Namespace)


def _launcher_namespace(launcher, plan, tmp_path):
    """Parse the launcher's args exactly as the runner passes them."""
    import argparse
    import contextlib
    import io

    captured = {}
    real_parse = argparse.ArgumentParser.parse_args

    def _spy(self, args=None, namespace=None):
        ns = real_parse(self, args, namespace)
        captured["ns"] = ns
        raise SystemExit(0)  # stop before anything launches a browser

    argparse.ArgumentParser.parse_args = _spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit):
                launcher.main(
                    [
                        "--published-dir",
                        str(tmp_path / "pub"),
                        "--stock-dir",
                        str(tmp_path / "stock"),
                        "--published-label",
                        plan["product_label"],
                        "--stock-label",
                        plan["stock_label"],
                        "--product-id",
                        plan["product_id"],
                        "--control-id",
                        plan["control_id"],
                        "--falsification-id",
                        plan["falsification_id"],
                        "-o",
                        str(tmp_path / "out"),
                    ]
                )
    finally:
        argparse.ArgumentParser.parse_args = real_parse
    return captured["ns"]


def test_the_launcher_defaults_still_name_ps344s_committed_readings(gate, tmp_path):
    """The defaults are a HISTORICAL record and must not drift.

    `readings/ps344-2026-09-07/REPORT.md` documents a reproduction that runs
    without `--product-id`; changing these defaults would break a committed
    recipe rather than merely changing a fallback.
    """
    launcher = _load(LAUNCHER, "ps344_launch_published_defaults")
    plan = {
        "product_label": "p",
        "stock_label": "s",
        "product_id": "published-152",
        "control_id": "stock-cft-152",
        "falsification_id": "stock-as-product",
    }
    ns = _launcher_namespace(launcher, plan, tmp_path)
    assert ns.product_id == "published-152"
    assert ns.control_id == "stock-cft-152"
    assert ns.falsification_id == "stock-as-product"


# ---------------------------------------------------------------------------
# THE THIRD OUTCOME. Not a pass, not a finding about the engine.
# ---------------------------------------------------------------------------


def test_an_exactly_matched_control_says_so(gate):
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.75"),
        index_fetcher=lambda: _index("152.0.7977.64", "152.0.7977.75"),
    )
    assert code == gate.PLAN_OK
    assert plan["control_match"] == gate.MATCH_EXACT
    assert plan["control_version"] == "152.0.7977.75"
    assert "EXACTLY version-matched" in plan["stock_label"]


def test_a_nearest_control_is_labelled_as_not_version_matched(gate):
    """AC 6. Resolving the nearest CfT build is legitimate ONLY with the label.

    PS-344's central claim — "a difference between the arms cannot be a
    difference between two Chromium releases" — weakens the moment the control
    is not exactly matched, so the arm LABEL (which is carried into every
    reading file and printed by the judge) must say so. A run that quietly
    substituted a neighbour would publish the strong claim on weak evidence.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.99"),
        index_fetcher=lambda: _index("152.0.7977.64", "152.0.7977.82"),
    )
    assert code == gate.PLAN_OK
    assert plan["control_match"] == gate.MATCH_NEAREST
    assert plan["control_version"] == "152.0.7977.82"
    label = plan["stock_label"]
    assert "NOT" in label and "version-matched" in label
    assert "WEAKER" in label
    # The engine's own version is named beside the control's, so a reader of the
    # reading file can see the gap rather than having to look it up.
    assert "152.0.7977.99" in label and "152.0.7977.82" in label


def test_no_matched_control_is_its_own_named_outcome(gate):
    """AC 6. No control means no comparison means NOTHING is concluded.

    Deliberately NOT downgraded to "any other 152": a control from a different
    Chromium BUILD LINE would make the arms differ for reasons that are not the
    patch set, which is the exact confound the version match exists to remove.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.9999.1"),
        index_fetcher=lambda: _index("152.0.7977.75", "153.0.8100.12"),
    )
    assert code == gate.NO_MATCHED_CONTROL
    assert code != 0, "a missing control must never be green"
    assert code != 1, "and must not wear the colour of a finding about the engine"
    assert plan["outcome"] == "NO_MATCHED_CONTROL"
    assert "152.0.9999" in plan["reason"]
    assert "NOTHING" in plan["reason"]
    # No plan keys that would let a caller proceed anyway.
    assert "product_file" not in plan
    assert "control_url" not in plan


def test_a_control_without_a_linux_download_is_not_a_control(gate):
    """An index entry can carry other platforms and no linux64 chrome.

    Counting it would produce a plan whose control URL is empty — a run that
    fails at download time with no idea why, instead of a named outcome.
    """
    index = _index("152.0.7977.75")
    index[0]["downloads"]["chrome"] = [
        {"platform": "win64", "url": "https://example.invalid/win"}
    ]
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.75"), index_fetcher=lambda: index
    )
    assert code == gate.NO_MATCHED_CONTROL
    assert plan["outcome"] == "NO_MATCHED_CONTROL"


# ---------------------------------------------------------------------------
# THE OTHER TWO NON-ZERO OUTCOMES. Each named, none green.
# ---------------------------------------------------------------------------


def test_a_build_persona_refuses_is_not_measured(gate):
    """`fetch_latest_checked` already applies `policy.check`.

    Measuring a build persona will not install is the wrong question, and the
    refusal is the correct outcome — but it must not read as "the patches are
    broken" either, so it gets its own code and its own name.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.75", verdict="known_bad", message="bad"),
        index_fetcher=lambda: _index("152.0.7977.75"),
    )
    assert code == gate.ENGINE_REFUSED_BY_POLICY
    assert plan["outcome"] == "ENGINE_REFUSED_BY_POLICY"
    assert plan["engine_tag"] == "personium-152.0.7977.75"
    assert plan["policy_verdict"] == "known_bad"


def test_an_unreadable_release_list_is_indeterminate_not_a_pass(gate):
    def _boom():
        raise OSError("upstream is down")

    code, plan = gate.plan(resolve_engine=_boom, index_fetcher=lambda: _index("1.0.0.1"))
    assert code == gate.CANNOT_PLAN
    assert plan["outcome"] == "CANNOT_PLAN"
    assert "upstream is down" in plan["reason"]


def test_an_unreadable_cft_index_is_indeterminate_not_a_pass(gate):
    def _boom():
        raise OSError("the index 500'd")

    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.75"), index_fetcher=_boom
    )
    assert code == gate.CANNOT_PLAN
    assert plan["outcome"] == "CANNOT_PLAN"
    # The tag is still named, because a run that cannot say WHICH build it was
    # looking at cannot be acted on.
    assert plan["engine_tag"] == "personium-152.0.7977.75"


def test_an_empty_tag_is_indeterminate(gate):
    def _nothing():
        return "", "", "", "ok", ""

    code, plan = gate.plan(
        resolve_engine=_nothing, index_fetcher=lambda: _index("152.0.7977.75")
    )
    assert code == gate.CANNOT_PLAN
    assert plan["outcome"] == "CANNOT_PLAN"


def test_the_four_outcome_codes_are_distinct_and_none_is_success(gate):
    """Collapsing any two of these is the defect the whole design resists."""
    codes = {
        gate.PLAN_OK,
        gate.CANNOT_PLAN,
        gate.NO_MATCHED_CONTROL,
        gate.ENGINE_REFUSED_BY_POLICY,
    }
    assert len(codes) == 4
    assert gate.PLAN_OK == 0
    assert all(c != 0 for c in (gate.CANNOT_PLAN, gate.NO_MATCHED_CONTROL,
                                gate.ENGINE_REFUSED_BY_POLICY))


def test_the_prefix_is_handled_by_the_updaters_own_helpers(gate):
    """`personium-` must be added and removed by `updater`, not by a slice here.

    `version_from_tag` owns which side of the module's API boundary carries the
    prefix; a hand-rolled slice in the gate would be a second, driftable copy of
    that rule — and a prefixed string reaching `readings-{id}.json` would put
    `personium-` in a filename the judge is not pointed at.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.75"),
        index_fetcher=lambda: _index("152.0.7977.75"),
    )
    assert code == gate.PLAN_OK
    assert plan["engine_version"] == "152.0.7977.75"
    assert "personium-" not in plan["product_id"]
    assert "personium-" not in plan["product_file"]
    # And an explicitly supplied version that ALREADY carries the prefix is
    # normalised rather than doubled — including on the way INTO the release
    # lookup, which is why the release stub records what it was asked for.
    code, plan = gate.plan(
        engine_version="personium-153.0.8100.12",
        resolve_release=_release("153.0.8100.12"),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.PLAN_OK
    assert plan["engine_version"] == "153.0.8100.12"
    assert plan["engine_tag"] == "personium-153.0.8100.12"


# ---------------------------------------------------------------------------
# THE DISPATCH ARM. It resolves a REAL release, or it refuses to plan.
#
# ⭐ WHY THIS SECTION EXISTS. The first cut of this gate treated an explicit
# `--engine-version` as "skip the release lookup": it derived the tag from the
# string and returned PLAN_OK with an EMPTY engine_url and digest. That plan
# printed a correct-looking tag, control and filenames, spent the ~200 MB
# Chrome for Testing download, and then died inside the runner —
# `updater.download_engine("")` returns False on its first statement — so the
# `workflow_dispatch` arm could only ever reach exit 2 INDETERMINATE.
#
# The two moments the workflow NAMES as the reasons that arm exists (a human
# who has just published an engine; re-reading a red run after a fix) were
# exactly the two it could not serve, and it failed wearing the colour of a
# transient upstream flake — so an operator following the printed remedy would
# re-dispatch, get the identical exit 2, and conclude upstream was down.
#
# ⚠️ AND THE SUITE WAS BLIND TO IT even though it exercised the branch: the
# explicit-version test asserted PLAN_OK and the ids, and NOTHING in this file
# ever asserted anything about `engine_url`. A branch can be covered and still
# be untested at the one field that breaks it. Hence the tests below assert the
# plan is ACTIONABLE, not merely well-shaped.
# ---------------------------------------------------------------------------


def test_a_dispatched_version_produces_a_plan_the_runner_can_act_on(gate):
    """THE REGRESSION. A dispatched run must carry a downloadable asset."""
    code, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=_release("153.0.8100.12"),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.PLAN_OK
    assert plan["engine_url"], "a PLAN_OK with no engine URL cannot be acted on"
    assert plan["engine_digest"], (
        "download_engine fails closed without a digest, so a plan that carries "
        "none is a plan that cannot install anything"
    )
    assert plan["engine_url"].endswith(".AppImage")
    # And the digest travels into the product label, which is the arm's
    # provenance record in the readings file.
    assert plan["engine_digest"] in plan["product_label"]


def test_the_dispatched_plan_reaches_the_runners_download_attempt(gate, tmp_path):
    """Past `plan()` and into the runner: the download is genuinely ATTEMPTED.

    Asserting `engine_url` is non-empty proves the plan is well-formed. This
    proves the WIRING — that the field the plan fills is the field the runner
    reads and hands to `updater.download_engine` — which is the coupling the
    empty-URL branch actually broke. A stub downloader records what it was
    given, so no network and no 200 MB download are involved.
    """
    runner = _load(RUN_SCRIPT, "ps344_run_verdict_download")
    _, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=_release("153.0.8100.12"),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )

    from src.services.engine import updater

    engine_dir = tmp_path / "engine-published"
    seen = {}

    def _fake_download(url, digest=None, tag="", **kw):
        seen.update(url=url, digest=digest, tag=tag)
        # What download_engine's contract says: nothing to install without a URL.
        return bool(url)

    real_download = updater.download_engine
    real_write = updater.write_version
    real_binary = updater.ENGINE_BINARY
    updater.download_engine = _fake_download
    updater.write_version = lambda *_a, **_k: None
    updater.ENGINE_BINARY = str(engine_dir / "chrome-stub")
    engine_dir.mkdir(parents=True, exist_ok=True)
    (engine_dir / "chrome-stub").write_bytes(b"stub")
    try:
        runner.stage_engine(plan, tmp_path)
    finally:
        updater.download_engine = real_download
        updater.write_version = real_write
        updater.ENGINE_BINARY = real_binary

    assert seen["url"] == plan["engine_url"], (
        "the runner must download the asset the plan resolved — an empty URL "
        "here is the defect this test exists for"
    )
    assert seen["digest"] == plan["engine_digest"]
    assert seen["tag"] == plan["engine_tag"]


def test_a_dispatched_tag_that_does_not_resolve_is_named_not_planned(gate):
    """`('','','')` is a REAL answer from `fetch_release_full`, not an error.

    Its docstring names three ways to get it: a yanked or deleted release, a tag
    that never existed, and an APPLICATION tag handed over by mistake. None of
    those is a plan — and crucially none may be a PLAN_OK, because the runner
    would then spend a CfT download before failing as INDETERMINATE, a word
    reserved for "an arm could not be produced or read".
    """
    code, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=lambda tag: ("", "", ""),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.CANNOT_PLAN
    assert plan["outcome"] == "CANNOT_PLAN"
    # The TAG is named — a run that cannot say which build it was looking at
    # cannot be acted on.
    assert plan["engine_tag"] == "personium-153.0.8100.12"
    assert "yanked" in plan["reason"] or "never have existed" in plan["reason"]


def test_a_dispatched_release_that_raises_is_indeterminate_not_a_pass(gate):
    def _boom(tag):
        raise OSError("the release endpoint 500'd")

    code, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=_boom,
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.CANNOT_PLAN
    assert "500" in plan["reason"]
    assert plan["engine_tag"] == "personium-153.0.8100.12"


def test_a_dispatched_tag_persona_refuses_is_not_measured_either(gate):
    """`policy.check` is NOT bypassed on the dispatch arm.

    The scheduled arm gets it free from `fetch_latest_checked`. A human
    dispatching a specific tag is the caller MOST likely to name a build that
    was just blocklisted — because naming it in `KNOWN_BAD_VERSIONS` is the
    documented remedy for a red run here, so "re-read the tag I just
    blocklisted" is a natural next gesture. It must reach the same named,
    non-green outcome a resolved refusal does.
    """
    code, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=_release("153.0.8100.12"),
        policy_check=lambda v: ("known_bad", f"{v} is on the known-bad list"),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.ENGINE_REFUSED_BY_POLICY
    assert plan["outcome"] == "ENGINE_REFUSED_BY_POLICY"
    assert plan["engine_tag"] == "personium-153.0.8100.12"
    assert plan["policy_verdict"] == "known_bad"


def test_the_dispatch_arm_has_no_policy_override(gate):
    """There is no flag that measures a build persona refuses — deliberately.

    A re-run switch that can undo this job's own documented remedy would let a
    red run be answered by re-reading the very tag someone just blocklisted.
    Asserted at the CLI surface rather than in prose, because prose does not
    stop a flag being added.
    """
    import argparse
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()) as out:
        with pytest.raises(SystemExit):
            gate.main(["--help"])
    help_text = out.getvalue()
    for forbidden in ("--force", "--ignore-policy", "--no-policy", "--skip-policy"):
        assert forbidden not in help_text, (
            f"{forbidden} would let this job re-litigate a refusal it exists "
            "to produce"
        )
    assert isinstance(argparse.ArgumentParser(), argparse.ArgumentParser)


def test_no_plan_is_ok_without_an_engine_url_whichever_branch_made_it(gate):
    """The invariant, at the branch neither arm anticipated.

    An upstream shape change that stops the asset matching would hand back a
    version with an empty URL from either path. The runner cannot act on it, so
    it is not a plan — and a PLAN_OK that spends a download before failing as
    INDETERMINATE is precisely the misattributed red this vocabulary resists.
    """
    # Dispatch branch: a release that names a version but serves no asset.
    code, plan = gate.plan(
        engine_version="153.0.8100.12",
        resolve_release=lambda tag: ("153.0.8100.12", "", ""),
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.CANNOT_PLAN
    assert plan["outcome"] == "CANNOT_PLAN"

    # Resolved branch: policy said OK but the asset URL came back empty.
    def _no_asset():
        return ("personium-153.0.8100.12", "", "", "ok", "")

    code, plan = gate.plan(
        resolve_engine=_no_asset,
        index_fetcher=lambda: _index("153.0.8100.12"),
    )
    assert code == gate.CANNOT_PLAN
    assert plan["engine_tag"] == "personium-153.0.8100.12"


def test_every_plan_ok_in_this_suite_is_actionable(gate):
    """A guard against the shape of the original defect, not only its instance.

    The empty-URL branch survived review because every test asserting PLAN_OK
    looked at ids and filenames and none looked at the URL. This asserts the
    property directly over both entry paths.
    """
    for kwargs in (
        {
            "resolve_engine": _resolver("153.0.8100.12"),
            "index_fetcher": lambda: _index("153.0.8100.12"),
        },
        {
            "engine_version": "153.0.8100.12",
            "resolve_release": _release("153.0.8100.12"),
            "index_fetcher": lambda: _index("153.0.8100.12"),
        },
    ):
        code, plan = gate.plan(**kwargs)
        assert code == gate.PLAN_OK
        assert plan["engine_url"] and plan["engine_digest"], plan


def test_build_line_is_the_first_three_components(gate):
    assert gate.build_line("152.0.7977.75") == "152.0.7977"
    assert gate.build_line("153.0.8100.12") == "153.0.8100"


def test_nearest_breaks_ties_toward_the_newer_patch_level(gate):
    """Two equidistant neighbours: prefer the higher one.

    Deterministic rather than incidental — a tie resolved by dict order would
    make the same engine produce different control labels on different runs.
    """
    code, plan = gate.plan(
        resolve_engine=_resolver("152.0.7977.80"),
        index_fetcher=lambda: _index("152.0.7977.75", "152.0.7977.85"),
    )
    assert code == gate.PLAN_OK
    assert plan["control_version"] == "152.0.7977.85"


# ---------------------------------------------------------------------------
# THE WORKFLOW. The properties that make it MEAN something.
# ---------------------------------------------------------------------------


def test_the_instrument_now_has_a_caller_in_dot_github():
    """AC 1, as the exact inverse of the finding: `grep ps344 .github/` was 0."""
    hits = [
        p
        for p in (REPO / ".github").rglob("*")
        if p.is_file()
        and p.suffix in {".yml", ".yaml", ".py"}
        and "ps344" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, "the PS-344 instrument still has no caller under .github/"
    names = {p.name for p in hits}
    assert "published-engine-verdict.yml" in names


def test_the_workflow_invokes_both_halves_of_the_instrument(workflow_text):
    assert "ps344_gate_plan.py" in workflow_text
    assert "ps344_run_verdict.py" in workflow_text
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    assert "scripts.ps344_launch_published" in runner
    assert "ps344_verdict.py" in runner


def test_the_engine_is_unpinned_and_the_tag_is_echoed(workflow_text, workflow_yaml):
    """AC 2. The risk is whatever the newest release is — a pin hides it."""
    import re

    plan_src = PLAN_SCRIPT.read_text(encoding="utf-8")
    assert "fetch_latest_checked" in plan_src

    # No engine version literal anywhere the job ACTS on — the scheduled run
    # resolves it, and the dispatch input is the only way to name one. Asserted
    # over the parsed steps rather than over the raw text, so a version cited in
    # prose (the header names the release PS-344 measured; the dispatch input's
    # description gives an example) cannot be mistaken for a pin, and a pin
    # cannot hide inside a comment-looking line.
    version_re = re.compile(r"\b\d+\.\d+\.\d{3,}\.\d+\b")
    for step in workflow_yaml["jobs"]["verdict"]["steps"]:
        for key in ("run", "with", "env"):
            body = step.get(key)
            if body is None:
                continue
            text = body if isinstance(body, str) else json.dumps(body)
            executable = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            found = version_re.findall(executable)
            assert not found, f"{step.get('name')!r} pins an engine version: {found}"

    # The `on:` block carries no version either — a schedule cannot pin a build.
    on_block = workflow_yaml.get("on", workflow_yaml.get(True))
    assert not version_re.findall(json.dumps(on_block.get("schedule")))

    # The header should still CITE the measured release by name, so a reader
    # knows which build the recorded figures came from.
    header = _header(workflow_text)
    assert version_re.findall(header)

    # And the tag reaches the failure block, which is where the remedy is named.
    assert "steps.plan.outputs.engine_tag" in workflow_text
    assert "KNOWN_BAD_VERSIONS" in workflow_text


def test_the_exit_code_propagates_unbranched(workflow_yaml, workflow_text):
    """AC 3. Exit 2 fails the job; nothing launders it into a pass."""
    job = workflow_yaml["jobs"]["verdict"]
    assert job.get("continue-on-error") is not True
    for step in job["steps"]:
        assert step.get("continue-on-error") is not True, step.get("name")
    gating = [
        s
        for s in job["steps"]
        if "ps344_run_verdict.py" in (s.get("run") or "")
        or "ps344_gate_plan.py" in (s.get("run") or "")
    ]
    assert len(gating) == 2, "both the plan and the verdict must gate"
    for step in gating:
        run = step["run"]
        assert "|| true" not in run
        assert "|| exit 0" not in run
        assert "set +e" not in run
        # No `if:` branching on the child's code — the two `if: failure()` steps
        # below report, they do not gate.
        assert step.get("if") is None, step.get("name")


def test_the_falsification_arm_is_run_and_is_required_to_be_red(workflow_yaml):
    """AC 4. `--skip-falsification` is a RESUME flag, not a waiver.

    Asserted over the PARSED step bodies rather than the raw text, because the
    workflow's failure block quotes the flag in prose that FORBIDS it — a raw
    grep cannot tell "do not pass this" from passing it.
    """
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    assert "--expect" in runner and "absent" in runner

    for step in workflow_yaml["jobs"]["verdict"]["steps"]:
        run = step.get("run") or ""
        # Strip the heredoc the failure block prints, and every comment line.
        executable = []
        in_heredoc = False
        for line in run.splitlines():
            if "<<EOF" in line or "<<'EOF'" in line:
                in_heredoc = True
                continue
            if in_heredoc:
                if line.strip() == "EOF":
                    in_heredoc = False
                continue
            if line.lstrip().startswith("#"):
                continue
            executable.append(line)
        body = "\n".join(executable)
        assert "--skip-falsification" not in body, (
            f"{step.get('name')!r} waives the falsification arm"
        )

    # And the runner never passes it either — there it is only ever prose. An
    # argv element would be a quoted string literal; the docstring mention is
    # not.
    assert '"--skip-falsification"' not in runner
    assert "'--skip-falsification'" not in runner


def test_the_falsification_is_judged_before_the_product_arm():
    """A green from a judge that cannot go red is worthless, so prove it first."""
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    fals = runner.index('"--expect", "absent"')
    prod = runner.index('verdict_cmd + ["--product", plan["product_file"]]')
    assert fals < prod, "the falsification arm must be judged first"


def test_a_falsification_that_fails_to_go_red_condemns_the_run():
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    assert "did NOT come back red" in runner
    assert "return FINDING" in runner


def test_the_workflow_states_its_cadence_its_cost_and_its_platform_bound(
    workflow_text, workflow_yaml
):
    """AC 7. In `engine-gpu-variance.yml`'s register: argued, not asserted."""
    header = _header(workflow_text)
    # Why a separate job — each alternative named.
    for other in ("ci.yml", "engine-autoupdate.yml", "engine-gpu-variance.yml"):
        assert other in header, f"the header does not say why NOT {other}"
    # The cadence and its price.
    assert "WEEKLY" in header
    assert "12 HEADED browser launches" in header
    assert "TWO ~200 MB downloads" in header
    # And what weekly COSTS, rather than only what it saves.
    assert "seven days" in header
    # The platform bound, stated rather than implied.
    assert "LINUX x86_64 ONLY" in header
    assert "UNMEASURED" in header
    assert workflow_yaml["jobs"]["verdict"]["runs-on"] == "ubuntu-24.04"


def test_the_judges_own_selftest_runs_before_anything_is_downloaded(workflow_yaml):
    """A broken judge must not spend 400 MB producing a meaningless green."""
    steps = workflow_yaml["jobs"]["verdict"]["steps"]
    names = [s.get("name", "") for s in steps]
    selftest = next(i for i, s in enumerate(steps) if "pytest" in (s.get("run") or ""))
    provision = next(
        i for i, s in enumerate(steps) if "apt-get" in (s.get("run") or "")
    )
    assert selftest < provision, names
    assert "tests/test_ps344_published_engine_verdict.py" in steps[selftest]["run"]


def test_the_selftest_installs_what_this_suite_needs_to_not_skip(workflow_yaml):
    """FOUND BY RUNNING IT IN CI, and it is the sharpest failure of the batch.

    The first CI run reported `34 passed, 6 SKIPPED` at the selftest step. The
    six were the workflow-SHAPE tests below — the ones asserting that this job
    has no `|| true`, does not waive the falsification, and states its bounds —
    and they skipped because `pytest.importorskip("yaml")` found no PyYAML: the
    selftest deliberately runs BEFORE `requirements.txt` is installed.

    So the guard against this gate being hollowed out was itself inert, inside
    the very job it guards, and the step still reported green. That is the exact
    defect class this whole ticket exists to remove — a check that reports
    success over something it did not look at — reproduced one level up.

    A skip is not a pass. Assert the dependency is installed rather than
    trusting a number that counts them separately.
    """
    steps = workflow_yaml["jobs"]["verdict"]["steps"]
    selftest = next(s for s in steps if "pytest" in (s.get("run") or ""))
    run = selftest["run"]
    # ⚠️ ASSERT ON THE EXECUTABLE LINES, NOT THE STEP BODY. The first draft of
    # this test read `"PyYAML" in run` and SURVIVED removing PyYAML from the pip
    # line — because the comment above that line explains why PyYAML is there.
    # A test that passes on its own rationale is the exact defect it guards.
    executable = "\n".join(
        line for line in run.splitlines() if not line.lstrip().startswith("#")
    )
    installs = [
        line for line in executable.splitlines() if line.lstrip().startswith("pip ")
    ]
    assert any("PyYAML" in line for line in installs), (
        "the selftest step must install PyYAML, or the workflow-shape tests "
        f"silently skip and this job's own guard is inert. pip lines: {installs}"
    )
    # And the run must NAME any skip rather than only counting it.
    assert "-ra" in executable.split(), executable


def test_the_plan_writes_its_json_even_when_the_directory_is_new(
    gate, tmp_path, capsys, monkeypatch
):
    """FOUND BY RUNNING IT IN CI. A correct plan that cannot be SAVED is red.

    The first CI run printed `outcome: PLAN_OK` / `engine tag:
    personium-152.0.7977.75` and then died with FileNotFoundError writing
    `/tmp/ps370/plan.json` — the local reproduction had that directory left over
    from an earlier step and never met it. The failure block then reported
    `plan outcome: <none>` over a plan that had in fact succeeded, which is
    exactly the kind of misattributed red this job's whole vocabulary exists to
    prevent.

    Driven through `main()` — the real CLI — with the release lookup and the
    CfT index stubbed at the module boundary, because `--engine-version` is a
    production flag that resolves a real release and is NOT a test hook. Using
    it as one is what let the empty-URL branch ship.
    """
    from src.services.engine import updater

    monkeypatch.setattr(
        updater,
        "fetch_release_full",
        lambda tag, timeout=20: (
            "152.0.7977.75",
            "https://example.invalid/personium-152.0.7977.75-linux-x86_64.AppImage",
            "sha256:" + "c" * 64,
        ),
    )
    monkeypatch.setattr(gate, "fetch_cft_index", lambda: _index("152.0.7977.75"))

    out = tmp_path / "does" / "not" / "exist" / "plan.json"
    code = gate.main(["--out", str(out), "--engine-version", "152.0.7977.75"])
    assert code == gate.PLAN_OK
    assert out.exists()
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["outcome"] == "PLAN_OK"
    assert body["engine_version"] == "152.0.7977.75"
    # And the saved plan is ACTIONABLE — the field the runner reads.
    assert body["engine_url"]
    assert body["engine_digest"]


def test_the_failure_block_is_a_quoted_heredoc_that_survives_backticks():
    """FOUND BY RUNNING IT IN CI. The remedy text must reach the operator INTACT.

    The failure block's `cat <<EOF` is UNQUOTED so `${TAG}` expands — which also
    makes bash treat a backtick as command substitution. The first CI run's
    remedy therefore read `by adding , or by letting exit 2 pass` beside a
    `syntax error near unexpected token '||'`: the one line telling a reader NOT
    to add `|| true` had the `|| true` eaten out of it.

    Advice that mangles itself in the failure path is advice nobody receives.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    in_unquoted_heredoc = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("cat <<EOF"):
            in_unquoted_heredoc = True
            continue
        if in_unquoted_heredoc and stripped == "EOF":
            in_unquoted_heredoc = False
            continue
        if in_unquoted_heredoc:
            assert "`" not in line, (
                "a backtick inside an unquoted heredoc is command substitution "
                f"and will be eaten from the operator's remedy text: {line!r}"
            )


def test_the_readings_are_kept_when_the_gate_goes_red(workflow_yaml):
    """On a red run they are the whole evidence base for what to blocklist."""
    steps = workflow_yaml["jobs"]["verdict"]["steps"]
    upload = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
    assert upload, "a red run must keep its readings"
    assert upload[0].get("if") == "failure()"


def test_the_failure_block_says_how_well_matched_the_control_was(workflow_yaml):
    """A NEAREST control changes how hard a reader should press on a finding.

    PS-344's central claim — "a difference between the arms is the patch set or
    it is nothing" — rests on the control being the SAME Chromium version. When
    it is not, the arms differ by a patch level too, and the claim is weaker.
    The arm LABEL carries that into the readings, but the readings only survive
    on `failure()` and are an artifact download away; the match belongs beside
    the tag in the log a reader sees first.
    """
    steps = workflow_yaml["jobs"]["verdict"]["steps"]
    failure_steps = [
        s for s in steps if s.get("if") == "failure()" and "run" in s
    ]
    assert failure_steps, "a red run must explain itself"
    block = failure_steps[-1]
    env = block.get("env") or {}
    assert "CONTROL_MATCH" in env, (
        "the failure block reads the plan's tag and outcome but not how well "
        f"version-matched the control was. env: {sorted(env)}"
    )
    assert "control_match" in env["CONTROL_MATCH"]
    assert "CONTROL_MATCH" in block["run"], (
        "wiring the value into env without echoing it leaves the reader with "
        "the same blind spot"
    )
    # And the run says what a NEAREST reading MEANS, not only its value.
    assert "nearest" in block["run"].lower()


def test_the_dispatch_input_says_policy_still_applies(workflow_yaml):
    """The one behaviour a dispatching human would otherwise be surprised by.

    Naming a tag in `KNOWN_BAD_VERSIONS` is this job's documented remedy for a
    red run, so "re-dispatch the tag I just blocklisted" is a natural next
    gesture — and it comes back exit 4 rather than measuring anything. That is
    correct and must be stated where the input is typed, not only in a Python
    docstring nobody dispatching a workflow reads.
    """
    inputs = workflow_yaml[True]["workflow_dispatch"]["inputs"]
    description = inputs["engine_version"]["description"]
    assert "policy.check" in description or "policy" in description
    assert "ENGINE_REFUSED_BY_POLICY" in description or "exit 4" in description


def test_the_runner_stages_with_a_symlink_and_does_not_touch_the_resolver():
    """The guard PS-344 respected is respected here too — asserted, not assumed."""
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    assert "symlink_to" in runner
    assert "fingerprint_chromium_filename" in runner
    assert "PERSONA_ENGINE_DIR" in runner
    # Nothing lands on PATH and no artifact is renamed.
    assert 'os.environ["PATH"]' not in runner
    assert "shutil.move" not in runner


def test_the_runner_preserves_the_archives_executable_bits():
    """FOUND BY RUNNING IT, and it is why this assertion exists.

    `ZipFile.extractall` discards the unix mode, so a helper binary the Chrome
    for Testing archive ships beside `chrome` lands non-executable and the
    control browser dies at startup with `Permission denied (13)` — reaching the
    judge as INDETERMINATE on all four control cells, i.e. a permanently red
    gate.

    ⛔ The helper is not NAMED, here or in the runner: PS-359's ratchet forbids
    this repository from owning any list of upstream's runtime filenames, and
    the fix does not need one — every member is extracted mode-preserving, which
    is precisely why it cannot rot when upstream renames something.
    """
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    # `zf.extractall(...)` as a CALL — the docstring names the method, which is
    # exactly what a naive substring test would trip on.
    assert "extractall(" not in runner.replace("``ZipFile.extractall``", ""), (
        "extractall drops the exec bit; see PS-370"
    )
    assert "external_attr" in runner
    assert "chmod(mode & 0o7777)" in runner


def test_the_runner_speaks_the_verdicts_own_three_codes():
    runner = RUN_SCRIPT.read_text(encoding="utf-8")
    mod = _load(RUN_SCRIPT, "ps344_run_verdict")
    assert (mod.LIVE, mod.FINDING, mod.INDETERMINATE) == (0, 1, 2)
    assert "NOT a pass" in runner


def test_a_plan_that_is_not_ok_never_reaches_the_browser(tmp_path, capsys):
    """A NO_MATCHED_CONTROL plan handed to the runner must not measure anything."""
    mod = _load(RUN_SCRIPT, "ps344_run_verdict_plan_guard")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps({"outcome": "NO_MATCHED_CONTROL", "reason": "no control"}),
        encoding="utf-8",
    )
    code = mod.main(
        [
            "--plan",
            str(plan_path),
            "--work",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == mod.INDETERMINATE
    out = capsys.readouterr().out
    assert "INDETERMINATE" in out
    assert "Nothing was measured" in out
