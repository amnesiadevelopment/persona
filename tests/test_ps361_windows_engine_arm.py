"""PS-361: the WINDOWS arm — and, as with PS-299, the thing to test is that it FAILS.

`.github/workflows/engine-trial-build-windows.yml` measures whether our 16
fingerprint patches compose with upstream's Windows patch layer. Like the PS-299
probe it guards, it is a GATE: a green run is what tells a reader the Windows
engine has a reproducible origin in this repository.

WHY THIS FILE EXISTS
────────────────────
This project has recorded the "arm that could only ever pass" shape TWICE:

  * the PS-299 rebase probe reporting ``81/81 hunks, 0 rejects, ✅`` **against an
    empty directory**, because it scraped stdout for ``Hunk #N FAILED`` and never
    consulted ``returncode``;
  * the runner-capability check in the Linux arm, added only after two dispatches
    had silently landed on a Mac that cannot build Chromium at all.

So the PS-361 ticket makes falsification non-waivable, and these tests are the
standing version of it: the CI switch demonstrates the arm going red ONCE, and
this file asserts the properties that made it able to, every run thereafter.

WHAT IS ASSERTED, AND WHY EACH ONE
──────────────────────────────────
  1. THE PARAMETERISATION IS ADDITIVE. `--platform` defaults to linux, so every
     pre-PS-361 caller (chromium-upstream-watch.yml, REBASING.md, the tests)
     keeps its exact behaviour. A default flip is the cheapest way to break the
     Linux arm from a "Windows" change, which AC5 forbids.
  2. THE TAG GRAMMARS ARE DISTINCT. portablelinux is `-1`, windows is `-1.1`.
     Sharing one regex silently matches nothing on the other platform.
  3. THE STAGING IS LOAD-BEARING. Our patches are read back OUT of upstream's
     own `patches/series`. If that stopped being true, AC2's mechanism would be
     decorative: the series could be empty and the measurement identical.
  4. THE 16-COUNT GUARD SURVIVES A PATH THROUGH THE PROBE. `apply_ours` must
     refuse a set that is not exactly 16, whatever the caller hands it.
  5. TRANSIENT RETRY DID NOT WEAKEN FAIL-CLOSED. 429/5xx retry; a 404 must NOT,
     because a 404 is a statement about the FILE ("an absent path is not a
     deleted path") and retrying it just reaches the same answer slowly.
  6. THE CONTROL COMES FIRST, ENFORCED BY `needs:`. Prose does not enforce
     ordering; the job graph does.
  7. THE ARM ASSERTS NOTHING ABOUT DOCKER. There is no docker path on the
     Windows sibling; copying the Linux guard across would refuse a runner that
     is perfectly capable of this measurement.
  8. NOTHING PUBLISHES. AC6, asserted rather than trusted.

NO NETWORK. Every test here drives the probe's functions against fixture trees
on disk or parses the workflow as YAML. Nothing clones, fetches, or touches
googlesource — so this is a gate on the gate that runs anywhere, offline CI
included.
"""

import importlib.util
import re
import shutil
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "ps299_rebase_probe.py"
STAGE = REPO_ROOT / "scripts" / "ps218_stage_patches.sh"
WINDOWS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build-windows.yml"
LINUX_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-trial-build.yml"
PATCH_DIR = REPO_ROOT / "engine" / "patches" / "fingerprint"
# PS-390. The arm's tag default is the COUNTERPART of the pin in
# `CURRENT_TAG.txt`, and the derivation lives in ONE named place — the watcher,
# which already owns `read_current_tag()`. Importing it here rather than
# re-implementing `tag + ".1"` is deliberate: a second copy of a naming rule is
# a second place a grammar change has to be found.
WATCH = REPO_ROOT / "scripts" / "ps342_chromium_watch.py"

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to parse the workflow")


def load_probe():
    """Import the probe as a module without executing its CLI."""
    spec = importlib.util.spec_from_file_location("ps299_probe_ps361", PROBE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ps299_probe_ps361"] = mod
    spec.loader.exec_module(mod)
    return mod


def windows_workflow():
    return yaml.safe_load(WINDOWS_WORKFLOW.read_text(encoding="utf-8"))


def load_watch():
    """The watcher module, for its `read_current_tag` / `windows_counterpart_tag`."""
    spec = importlib.util.spec_from_file_location("ps342_watch_ps361", WATCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ps342_watch_ps361"] = mod
    spec.loader.exec_module(mod)
    return mod


def expected_windows_tag():
    """The tag this arm must default to: the counterpart of our pinned target.

    ⛔ PS-390 — DERIVED, NOT A LITERAL. This used to be the string
    `"152.0.7977.75-1.1"` written here, and that was wrong in BOTH directions: a
    correct rebase (which moves `CURRENT_TAG.txt` and this arm together) went RED
    for the wrong reason, and a stale arm left behind by a Linux-only bump stayed
    GREEN. A check that flags every correct bump is worse than no check, because
    it teaches the next person to edit the assertion.
    """
    watch = load_watch()
    return watch.windows_counterpart_tag(watch.read_current_tag())


# ── 1. the parameterisation is ADDITIVE ──────────────────────────────────────

def test_platform_defaults_to_linux_so_existing_callers_are_unchanged():
    """AC5. The Windows work must not alter the Linux arm's behaviour.

    `chromium-upstream-watch.yml` invokes the probe with no `--platform`, and
    bumps our pinned tag on its say-so. If the default ever flipped, that
    automation would start measuring the WRONG REPOSITORY while looking
    identical — the exact silent-substitution shape this project keeps paying
    for. Assert the default rather than trusting it.

    ⚠️ THIS READS THE REAL PARSER, and that is the whole point. The first
    version of this test REBUILT a parser carrying the same flag and asserted
    against its own copy — a mutation flipping the real default to "windows"
    passed the entire suite. `build_parser()` exists so this test can hold the
    object every caller actually gets.
    """
    mod = load_probe()
    assert mod.build_parser().parse_args([]).platform == "linux"
    assert mod.PLATFORMS["linux"]["repo"] == mod.UCPL_REPO, (
        "the linux platform entry must resolve to the SAME repository the "
        "pre-PS-361 module constant named, or existing callers silently move"
    )


def test_trees_defaults_to_patched_so_the_probes_own_contract_is_unchanged():
    """The pre-PS-361 probe measured the patched tree. `--trees` must default
    to that, or `chromium-upstream-watch.yml` starts reporting a CONTROL result
    as though it were a patch measurement — a green that means nothing.
    """
    mod = load_probe()
    assert mod.build_parser().parse_args([]).trees == "patched"


def test_staged_defaults_off_so_existing_callers_do_not_mutate_a_checkout():
    mod = load_probe()
    assert mod.build_parser().parse_args([]).staged is False


def test_watch_workflow_does_not_pass_a_platform_and_therefore_gets_linux():
    """The Linux automation must keep measuring portablelinux, by omission."""
    watch = REPO_ROOT / ".github" / "workflows" / "chromium-upstream-watch.yml"
    text = watch.read_text(encoding="utf-8")
    assert "ps299_rebase_probe.py" in text
    assert "--platform windows" not in text, (
        "the chromium upstream watch bumps our LINUX pin; pointing it at the "
        "windows sibling would bump the linux tag on a windows measurement"
    )


# ── 2. the tag grammars are distinct ─────────────────────────────────────────

def test_tag_grammars_do_not_match_each_others_tags():
    """`-1` vs `-1.1` — reusing one regex matches NOTHING on the other platform.

    That failure mode is quiet: `newest_tag` would raise IndexError off an empty
    list rather than say why, so this is asserted where it can be read.
    """
    import re

    mod = load_probe()
    linux_re = mod.PLATFORMS["linux"]["tag_re"]
    windows_re = mod.PLATFORMS["windows"]["tag_re"]

    assert re.match(linux_re, "152.0.7977.75-1")
    assert re.match(windows_re, "152.0.7977.75-1.1")

    assert not re.match(linux_re, "152.0.7977.75-1.1"), (
        "the linux grammar must not swallow a windows tag"
    )
    assert not re.match(windows_re, "152.0.7977.75-1"), (
        "the windows grammar must not swallow a linux tag"
    )


def test_the_two_platforms_name_different_repositories():
    mod = load_probe()
    assert (
        mod.PLATFORMS["linux"]["repo"] != mod.PLATFORMS["windows"]["repo"]
    )
    assert "portablelinux" in mod.PLATFORMS["linux"]["repo"]
    assert mod.PLATFORMS["windows"]["repo"].endswith("ungoogled-chromium-windows.git")


# ── 3. the staging is LOAD-BEARING ───────────────────────────────────────────

def _fake_checkout(tmp_path, entries):
    """A checkout-shaped tree with a `patches/series` and the named files."""
    patches = tmp_path / "patches"
    (patches / "fingerprint").mkdir(parents=True)
    lines = ["ungoogled-chromium/windows/some-upstream.patch"]
    for name in entries:
        (patches / "fingerprint" / name).write_text("stub\n", encoding="utf-8")
        lines.append("fingerprint/%s" % name)
    (patches / "series").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_staged_list_reads_our_patches_back_out_of_upstreams_series(tmp_path):
    """AC2's mechanism must be the one the measurement depends on.

    If the probe read `engine/patches/fingerprint` directly while a separate
    step wrote the series, the series could be malformed, mis-ordered or EMPTY
    and the result would be byte-identical. Reading it back is what couples
    them.
    """
    mod = load_probe()
    names = ["000-a.patch", "001-b.patch", "018-z.patch"]
    _fake_checkout(tmp_path, names)

    paths, err = mod.staged_patch_list(str(tmp_path))
    assert err is None
    assert [Path(p).name for p in paths] == names, (
        "order matters: 000 declares the switches every later patch reads, so "
        "the series' order is the applying order"
    )


def test_staged_list_ignores_upstreams_own_series_entries(tmp_path):
    """Only OUR entries are ours. Upstream's prerequisites are applied elsewhere."""
    mod = load_probe()
    _fake_checkout(tmp_path, ["000-a.patch"])
    paths, err = mod.staged_patch_list(str(tmp_path))
    assert err is None
    assert all("fingerprint/" in p or "fingerprint" in Path(p).parts for p in paths)
    assert not any("some-upstream" in p for p in paths)


def test_staged_list_refuses_a_series_naming_an_absent_file(tmp_path):
    """A series entry with no file behind it must be an ERROR, not a shorter list.

    Silently skipping it is how a run measures 15 patches and reports on 16.
    """
    mod = load_probe()
    _fake_checkout(tmp_path, ["000-a.patch"])
    series = tmp_path / "patches" / "series"
    series.write_text(series.read_text(encoding="utf-8") + "fingerprint/999-missing.patch\n", encoding="utf-8")

    paths, err = mod.staged_patch_list(str(tmp_path))
    assert paths is None
    assert err is not None and "999-missing.patch" in err


def test_staged_list_refuses_a_checkout_with_no_series(tmp_path):
    mod = load_probe()
    paths, err = mod.staged_patch_list(str(tmp_path))
    assert paths is None and err is not None


# ── 4. the 16-count guard survives the probe path ────────────────────────────

@pytest.mark.skipif(shutil.which("patch") is None, reason="GNU patch needed")
def test_apply_ours_refuses_a_set_that_is_not_exactly_sixteen(tmp_path, capsys):
    """"A build made to succeed by quietly dropping a patch measures nothing."

    The guard lives in ps218_stage_patches.sh, but PS-361 lets the probe take a
    caller-supplied list — so the SAME refusal has to hold on that path, or the
    new parameter is a way around the guard.
    """
    mod = load_probe()
    fifteen = [str(p) for p in sorted(PATCH_DIR.glob("*.patch"))][:15]
    assert len(fifteen) == 15

    result = mod.apply_ours(str(tmp_path), 0, paths=fifteen)
    assert result is None, "a set of 15 must be refused, not measured"
    assert "expected %d patches, found 15" % mod.EXPECTED_PATCHES in capsys.readouterr().out


def test_the_repo_really_holds_sixteen_patches():
    """The guard's premise. If this changes, the guard's number must change WITH it."""
    assert len(list(PATCH_DIR.glob("*.patch"))) == 16


def test_stage_script_still_carries_the_sixteen_count_guard():
    """AC2 requires the guard ACTIVE on the Windows path, and that path reuses
    this script unmodified — so the guard must still be in it."""
    text = STAGE.read_text(encoding="utf-8")
    assert '"$count" -ne 16' in text
    assert "measures nothing" in text
    assert "sha256sum" in text, "AC2 also requires the staged set recorded with checksums"


# ── 5. transient retry did NOT weaken fail-closed ────────────────────────────

def test_a_404_is_never_retried_because_it_is_a_statement_about_the_file(monkeypatch):
    """"An absent path is not a deleted path" — 8 of our 38 paths are absent
    upstream because our own patches CREATE them. A 404 is the expected answer
    there, and retrying it would be slow before reaching the same result.
    """
    mod = load_probe()
    calls = {"n": 0}

    def boom(*a, **kw):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    with pytest.raises(urllib.error.HTTPError):
        mod.fetch_text("chromium/src", "ref", "some/path")
    assert calls["n"] == 1, "a 404 must be raised on the FIRST attempt, never retried"


def test_a_429_is_retried_because_it_is_a_statement_about_the_server(monkeypatch):
    """Measured on PS-361: googlesource answered 429 on 18 of 38 paths in one run.

    The probe correctly refused to report a pass (exit 2) — that behaviour is
    right and is NOT what retry changes. Retry only reduces how often a
    transient answer costs a whole measurement.
    """
    mod = load_probe()
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import base64

                return base64.b64encode(b"content")

        return R()

    monkeypatch.setattr(mod.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    assert mod.fetch_text("chromium/src", "ref", "p") == b"content"
    assert calls["n"] == 3


def test_retry_gives_up_and_raises_rather_than_returning_empty(monkeypatch):
    """Exhausted retries must RAISE. Returning None/b"" would be read by the
    caller as a legitimately-absent file and let a patch "apply" against a tree
    missing its target — the exact combination that once produced a green ✅.

    ⚠️ BOTH EXHAUSTION PATHS ARE COVERED, and the second is why this test grew.
    An HTTPError-only version of it SURVIVED a mutation that made the generic
    `except Exception` arm return b"" on give-up: a transport failure (URLError,
    timeout, incomplete read) never raises HTTPError, so that arm was untested.
    A network blip is at least as likely as a 5xx.
    """
    mod = load_probe()
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def always_http(*a, **kw):
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", None, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", always_http)
    with pytest.raises(urllib.error.HTTPError):
        mod.fetch_text("chromium/src", "ref", "p")

    def always_transport(*a, **kw):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(mod.urllib.request, "urlopen", always_transport)
    with pytest.raises(urllib.error.URLError):
        mod.fetch_text("chromium/src", "ref", "p")


# ── 6/7/8. the workflow's own structural guarantees ──────────────────────────

def test_patched_job_needs_the_control_so_ordering_is_a_graph_property():
    """AC5's two-tree discipline. `ps218_verify_control.sh` states the case: a
    failure on a patched tree has two possible causes and one run cannot
    separate them. Prose does not enforce order; `needs:` does.
    """
    wf = windows_workflow()
    assert wf["jobs"]["patched"]["needs"] == "unmodified"


def test_the_control_job_applies_none_of_our_patches():
    wf = windows_workflow()
    steps = wf["jobs"]["unmodified"]["steps"]
    run_text = "\n".join(s.get("run", "") for s in steps)
    assert "--trees unmodified" in run_text
    assert "--staged" not in run_text, (
        "the control must not stage our patches — that is what makes it a control"
    )


def test_the_patched_job_stages_through_upstreams_series():
    wf = windows_workflow()
    run_text = "\n".join(s.get("run", "") for s in wf["jobs"]["patched"]["steps"])
    assert "--staged" in run_text and "--trees patched" in run_text


def test_the_windows_arm_asserts_nothing_about_docker():
    """There is NO docker path on the windows sibling (measured: 0 hits at
    152.0.7977.75-1.1, with msvc/ninja/python controls firing). Copying the
    Linux guard across would refuse a runner that can make this measurement.
    """
    wf = windows_workflow()
    for job in wf["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "command -v docker" not in run
            assert "docker --version" not in run


def test_the_windows_arm_publishes_nothing():
    """AC6, asserted rather than trusted."""
    text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    wf = windows_workflow()
    for job in wf["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses", "")
            assert "release" not in uses.lower(), "no release action may appear"
            run = step.get("run", "")
            for forbidden in ("gh release", "git tag", "git push"):
                assert forbidden not in run, "%s must not appear in this arm" % forbidden
    assert "softprops/action-gh-release" not in text


def test_the_windows_arm_is_not_broadly_triggered():
    """A `paths`-less or push trigger would fetch ~38 googlesource paths on
    every commit and earn exactly the 429s this ticket measured.

    ⚠️ THIS ARM IS DELIBERATELY NOT DISPATCH-ONLY, unlike the Linux one, and the
    reason is a hard GitHub constraint rather than a preference:
    `workflow_dispatch` can only be fired from the DEFAULT BRANCH, so a
    dispatch-only arm cannot be run until after it is merged — its first
    execution would be on `main`, and AC1 ("a completed run's own output, not an
    assertion that a YAML key exists") could not be met before review.

    So what is asserted is NARROWNESS, not dispatch-only: no `push` trigger at
    all, and a `pull_request` trigger confined by `paths:` to this arm and the
    things it drives.
    """
    wf = windows_workflow()
    triggers = wf[True] if True in wf else wf["on"]

    assert "push" not in triggers, (
        "a push trigger would fetch ~38 googlesource paths on every commit"
    )
    assert set(triggers) == {"workflow_dispatch", "pull_request"}

    pr = triggers["pull_request"]
    assert "paths" in pr and pr["paths"], (
        "the pull_request trigger MUST be paths-confined; without it this arm "
        "runs on every PR in the repository"
    )
    # It may only fire for things that can actually change its answer.
    assert set(pr["paths"]) == {
        ".github/workflows/engine-trial-build-windows.yml",
        "scripts/ps299_rebase_probe.py",
        "scripts/ps218_stage_patches.sh",
        "engine/patches/fingerprint/*.patch",
    }


def test_inputs_have_workflow_level_fallbacks_because_a_pr_run_has_no_inputs():
    """On `pull_request` the `inputs.*` context is EMPTY.

    An empty tag reaches the probe as a bare `--tag ""`, which does NOT fail
    loudly — it clones something else. Centralising the fallbacks means no use
    site can forget one, so assert they exist and that the steps read the
    resolved variables rather than `inputs.*` directly.
    """
    wf = windows_workflow()
    env = wf["env"]
    # ⛔ PS-390 — DERIVED FROM `CURRENT_TAG.txt`, NOT A LITERAL.
    #
    # This assertion's JOB is "the fallbacks exist and the steps read the
    # resolved variables rather than `inputs.*`", and that job is unchanged. What
    # changed is what it compares the tag against. Pinned to the string
    # `"152.0.7977.75-1.1"` it also — silently, and as a side effect nobody
    # chose — pinned the arm to ONE TAG FOREVER: a coherent bump that moved
    # `CURRENT_TAG.txt` and this arm together failed HERE, while a Linux-only
    # bump that left the arm stale passed. Both directions wrong, and the red one
    # is the more dangerous, because a red on every correct rebase teaches the
    # next person that editing the assertion is part of the procedure.
    want_tag = expected_windows_tag()
    assert want_tag in env["UNGOOGLED_TAG"], (
        "the Windows arm's env fallback is %r but CURRENT_TAG.txt's counterpart "
        "is %r — move the pin and this arm in the SAME change (see "
        "engine/patches/fingerprint/REBASING.md, \"the pin is written on a "
        "second platform\")" % (env["UNGOOGLED_TAG"], want_tag)
    )
    # The BASE is deliberately NOT derived: it is a 40-char submodule sha and no
    # rule turns a tag into one. What is asserted is that the fallback is WIRED
    # and holds a real commit — a blank would disable `--expect-base` on the
    # pull_request trigger, which is the trigger that fires automatically. The
    # base's live correctness is the probe's `--expect-base` assertion, which
    # exits 2 on a mismatch; see `tests/test_ps342_chromium_watch.py`'s PS-390
    # section for why that split is the choice rather than an omission.
    base_default = (
        wf[True] if True in wf else wf["on"]
    )["workflow_dispatch"]["inputs"]["expect_base"]["default"]
    assert base_default in env["EXPECT_BASE"], (
        "the env fallback (%r) does not carry the same base commit as the "
        "workflow_dispatch default (%r) — a PR run and a hand-dispatched run "
        "would assert DIFFERENT bases" % (env["EXPECT_BASE"], base_default)
    )
    assert "'none'" in env["FALSIFY"], (
        "falsify must fall back to 'none' so a PR run measures the REAL thing "
        "and can never go red for a breakage nobody asked for"
    )

    run_text = "\n".join(
        s.get("run", "") for job in wf["jobs"].values() for s in job["steps"]
    )
    assert "inputs.ungoogled_tag" not in run_text, (
        "steps must read ${UNGOOGLED_TAG}, not inputs.* — the latter is empty "
        "on the pull_request trigger"
    )
    assert "inputs.expect_base" not in run_text
    assert "${UNGOOGLED_TAG}" in run_text and "${EXPECT_BASE}" in run_text


def test_the_falsification_switch_offers_both_failure_shapes():
    """AC4. The two shapes are NOT redundant: a count-only guard cannot catch a
    corrupted hunk, and an apply-stage check does not exercise the count guard.
    """
    wf = windows_workflow()
    triggers = wf[True] if True in wf else wf["on"]
    options = triggers["workflow_dispatch"]["inputs"]["falsify"]["options"]
    assert set(options) == {"none", "drop_patch", "corrupt_hunk"}
    assert triggers["workflow_dispatch"]["inputs"]["falsify"]["default"] == "none", (
        "the real measurement must be the default; a falsification run is opt-in"
    )


def test_the_arm_reports_exit_2_as_distinct_from_exit_1():
    """"Nothing was measured" must not be reported as "our patches failed".

    Collapsing them sends a reader to rebase patches that were never tested —
    and this repo already documents that trap for the Linux watch workflow.
    """
    text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    assert "NOTHING WAS MEASURED" in text
    assert "MEASURED FAILURE" in text


def test_the_linux_workflow_is_not_modified_by_the_windows_arm():
    """AC5, stated as an assertion a future change has to keep true."""
    text = LINUX_WORKFLOW.read_text(encoding="utf-8")
    assert "[self-hosted, Linux, X64, persona-build]" in text
    assert "ungoogled-software/ungoogled-chromium-portablelinux" in text
    assert "ungoogled-chromium-windows" not in text, (
        "the linux arm must not acquire a windows dependency"
    )


def test_base_commit_assertion_is_wired_and_defaults_to_the_shared_base():
    """The symmetry argument — our patches overlap only the SHARED base layer —
    holds only while both siblings pin the same base commit. Guard, not comment.

    ⛔ PS-390 — THE SHAPE ASSERTED, NOT THE VALUE. This read
    `default.startswith("cacf0f0")`, which pinned the arm to the base of ONE
    tag: a coherent bump to a new tag pairing (whose siblings pin a different
    shared commit) failed here, correctly moving the arm and being told it was
    wrong. That is the same defect as the tag literal above and the same remedy.

    The base is NOT derived — a 40-char submodule sha follows from no rule about
    a tag, and deriving it would need a network read this suite deliberately does
    not make. So what is asserted is what CAN be asserted in-tree: the assertion
    exists, it is wired into the run, and the default is a real full commit sha
    rather than a placeholder or a blank (a blank DISABLES the assertion, which
    the input's own description says to do only deliberately). Whether that sha
    is the RIGHT one is the probe's `--expect-base` question, answered live at
    the only moment it can be answered honestly: it exits 2 on a mismatch, which
    the arm reports as "nothing was measured" rather than as a pass.
    """
    wf = windows_workflow()
    triggers = wf[True] if True in wf else wf["on"]
    default = triggers["workflow_dispatch"]["inputs"]["expect_base"]["default"]
    assert re.fullmatch(r"[0-9a-f]{40}", default or ""), (
        "expect_base's default is %r — it must be a full 40-char commit sha. A "
        "blank disables the base assertion, and an abbreviated one is not what "
        "the probe compares against." % (default,)
    )

    run_text = "\n".join(
        s.get("run", "")
        for job in wf["jobs"].values()
        for s in job["steps"]
    )
    assert "--expect-base" in run_text
