"""PS-355: the out-of-perimeter launch-artifact inventory and the gate over it.

WHAT THIS FILE DEFENDS, AND WHY THE GATE NEEDS DEFENDING
---------------------------------------------------------
The deliverable of PS-355 is not the list in ``launch_perimeter.py`` — it is the
CHECK that fails when the tree and the list disagree. A list nobody checks is
the "check that cannot fail" PS-8's own caution names, and the whole argument
for this ticket was that six defects of one class were each found by a person
reading code and none by a gate.

So the tests below are about the ways that gate can be quietly un-built:

* the inventory grows an entry with no reason, or loses its platform column, so
  a "decided exception" becomes scope-by-omission wearing a label;
* the scanner stops parsing, and reports a clean tree because it sees nothing;
* the scanner fires on EVERY write rather than on an out-of-perimeter one, so
  its red means nothing and its green was never at risk;
* the reach reader stops noticing a DELETED removal call — the half no write
  scan can see, and the half that catches PS-16;
* an entry is excused from the static cross-check by flipping ``detected``.

⭐ THE MEASUREMENT BEHIND THE INVENTORY, recorded here because it is what makes
the list a reading rather than an assertion. A real firefox launch was run
under ``xvfb`` against a scratch ``PERSONA_HOME`` (personium ``firefox-20``),
with the host watched across the launch:

* OUTSIDE ``PERSONA_HOME`` the launch created exactly ONE path — the
  ``.desktop`` entry — and ``delete_profile`` removed it. Nothing else on the
  host moved.
* INSIDE ``PERSONA_HOME``, outside the profile's own directory, it created
  ``running_sessions.json`` and ``bookmarks.json``, and appended the profile
  name to the day's log file.
* ``bookmarks.json`` was NOT on the ticket's table. It is the correction the
  measurement produced, and it is why "confirm the table against a real launch"
  was the implementer's first task rather than an inherited premise.

⛔ WHAT THESE TESTS DO NOT DO. They do not launch a browser. The gate's surface
is the SOURCE, so a test of it needs no engine — and the launch that produced
the inventory is recorded above rather than re-run here, because a suite that
needs a display to check a parser would simply be skipped everywhere it matters.
"""

from __future__ import annotations

import ast

import pytest

from src.services.verify import launch_perimeter as LP
from src.services.verify import launch_perimeter_scan as SCAN
from src.services.verify.behaviour import CANNOT_RUN, FINDING, PASS, Context, run_check
from src.services.verify.behaviour_checks import CHECKS


@pytest.fixture()
def check():
    return next(c for c in CHECKS if c.name == "launch-perimeter-inventory")


@pytest.fixture()
def ctx(tmp_path):
    """A Context the check never touches.

    This check reads the tree, not a store, so the home is a formality — and
    that is worth stating: it is why the check is ``needs_launch=False``.
    """
    return Context(home=str(tmp_path))


# --- the inventory is well-formed -------------------------------------------


def test_the_shipped_inventory_is_structurally_valid() -> None:
    assert LP.validate_inventory() == []


def test_every_entry_names_the_platforms_it_is_expected_on() -> None:
    """The column that stops a one-OS gate lying about the other two.

    The desktop entry is Linux-only (``supports_linux_desktop_integration()``
    returns ``IS_LINUX``), and the three fork-path gaps are the inverse. An
    inventory with no platform column, read by a gate running on one OS, either
    reports a phantom missing artifact or passes in silence.
    """
    for artifact in LP.PERIMETER_ARTIFACTS:
        assert artifact.platforms, artifact.site
        for platform in artifact.platforms:
            assert platform in LP.ALL_PLATFORMS, (artifact.site, platform)


def test_the_desktop_entry_is_recorded_as_linux_only() -> None:
    entry = next(
        a for a in LP.PERIMETER_ARTIFACTS if a.site.endswith("window_entry.py:_entry_dir")
    )
    assert entry.platforms == LP.LINUX_ONLY
    assert entry.scope == LP.SCOPE_HOST


def test_an_exception_without_a_reason_is_rejected() -> None:
    """DoD#3 asks for a decided exception WITH ITS REASON.

    Without this, an entry can be added that looks considered and says nothing —
    which is scope-by-omission with a label on it, the exact thing the ticket's
    non-waivable stop condition was checking for in the tree.
    """
    bad = (
        LP.Artifact(
            artifact="something",
            site="src/x.py:f",
            scope=LP.SCOPE_HOST,
            platforms=LP.ALL_PLATFORMS,
            disposition=LP.DISPOSITION_EXCEPTION,
            reason="",
        ),
    )
    problems = LP.validate_inventory(bad)
    assert any("no reason" in p for p in problems), problems


def test_a_removed_entry_must_name_removal_sites_not_only_prose() -> None:
    """Prose alone is what PS-16 had.

    ``reached_by`` described the reach correctly while one caller was missing,
    and nothing could tell. A ``removed`` entry therefore has to name edges the
    gate can check.
    """
    bad = (
        LP.Artifact(
            artifact="something",
            site="src/x.py:f",
            scope=LP.SCOPE_HOST,
            platforms=LP.ALL_PLATFORMS,
            disposition=LP.DISPOSITION_REMOVED,
            reached_by="something removes it, honest",
        ),
    )
    problems = LP.validate_inventory(bad)
    assert any("removal_sites" in p for p in problems), problems


def test_detected_false_cannot_be_used_to_excuse_a_site_silently() -> None:
    """``detected=False`` turns off the static cross-check for one entry.

    An unreasoned one is a site excused from the gate by assertion, so it is
    refused — the flag has to say WHY no static site corresponds to the entry.
    """
    bad = (
        LP.Artifact(
            artifact="something",
            site="src/x.py:f",
            scope=LP.SCOPE_HOST,
            platforms=LP.ALL_PLATFORMS,
            disposition=LP.DISPOSITION_REMOVED,
            reached_by="a remover",
            removal_sites=("src/y.py:a->b",),
            detected=False,
        ),
    )
    problems = LP.validate_inventory(bad)
    assert any("detected=False" in p for p in problems), problems


def test_the_thread_path_gaps_are_recorded_as_decided_exceptions() -> None:
    """AC2, and the entries that invent nothing.

    Each reason is lifted from a comment already at that site, and each carries
    the two things that comment carries: the process-global-state argument, and
    the counterpart that IS covered everywhere (the chromium seam's ``Popen``
    kwargs). ⛔ This RECORDS them; nothing here closes or weakens them.

    ⭐ WAS THREE, IS FOUR (PS-360). The fourth is the Wayland app_id
    (``MOZ_APP_REMOTINGNAME``), which shipped WITHOUT the ``not in_thread``
    term its three siblings carried and was guarded to match. Growing this
    census is the correct outcome of that ticket, not a side effect: a
    process-global mutation moved from "silently wrong on the thread path" to
    "a recorded absence", which is precisely the state this inventory exists
    to hold.

    ⚠️ THE FOURTH ENTRY'S ``platforms`` DIFFERS AND MUST, so the assertion
    below is per-entry rather than uniform. The first three are guarded on
    ``not in_thread and IS_LINUX`` AND are only reachable as absences off
    Linux, so ("windows", "macos") describes them. The fourth carries the same
    guard but its absence is reachable on LINUX too, because
    ``in_process=True`` forces the thread arm there (verify/baseline.py's
    recorder). Asserting ("windows", "macos") for it would re-state the exact
    reading error PS-360 was raised to correct.
    """
    gaps = {
        a.site: a
        for a in LP.PERIMETER_ARTIFACTS
        if a.site.startswith("src/services/browser/invisible_launch.py:")
        and a.disposition == LP.DISPOSITION_EXCEPTION
    }
    windows_macos_only = {
        "src/services/browser/invisible_launch.py:_apply_child_cwd",
        "src/services/browser/invisible_launch.py:_pin_tmpdir_here",
        "src/services/browser/invisible_launch.py:scrub_current_process_environ",
    }
    every_platform = {
        "src/services/browser/invisible_launch.py:_remoting_name",
    }
    assert set(gaps) == windows_macos_only | every_platform

    for site, artifact in gaps.items():
        assert "recorded absence" in artifact.reason, site
        assert "Popen" in artifact.reason, site
        if site in windows_macos_only:
            assert artifact.platforms == ("windows", "macos"), site
        else:
            assert artifact.platforms == LP.ALL_PLATFORMS, site


def test_the_thread_path_gaps_still_carry_their_guards_in_the_source() -> None:
    """The inventory records an absence; this pins that the absence is real.

    If someone "closed" a gap by widening its guard, the entry above would be
    describing a tree that no longer exists — a decided exception recorded
    against a decision that was reversed. ⛔ Out of scope for PS-355 is CLOSING
    these; this is what makes that boundary observable rather than trusted.

    ⭐ THE COUNT WENT 3 -> 4 WITH PS-360, in the opposite direction from the
    one this test was written to catch. It guards against a guard being
    REMOVED; a guard being ADDED to a fifth mutation that never had one is the
    inventory gaining an entry, which the test above pins. Both numbers are
    asserted here rather than one, so neither direction is silent.

    ⛔ The two counts are DIFFERENT SHAPES and are not interchangeable — the
    bare ``if not in_thread:`` guard on ``start_own_session`` carries no
    platform term at all, so a single total would blur three facts into one.
    See tests/test_browser_process_global_guards.py, which derives the whole
    set by AST rather than counting substrings.

    ⚠️ THE SECOND COUNT IS NOW DERIVED, NOT COUNTED. It used to be
    ``text.count("not in_thread") == 8``, a plain substring tally that included
    the one occurrence sitting inside a COMMENT — so rewording a comment fired
    a tripwire whose message talks about guards moving. The total is read off
    the AST instead: every ``if``/``while`` test and every assigned expression
    inside ``_child``/``_launch_and_watch`` whose source mentions ``in_thread``.
    That counts CODE and only code, so prose is free to change and a guard is
    not.
    """
    source = SCAN.repo_root() + "/src/services/browser/invisible_launch.py"
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("not in_thread and _platform.IS_LINUX") == 4, (
        "a fork-path guard has moved, been widened, or been added without "
        "the inventory entry that records the absence it creates. PS-355 "
        "RECORDS these gaps; it does not close them, and an inventory entry "
        "describing a guard that is gone is worse than no entry."
    )

    tree = ast.parse(text)
    _rows = []
    for fn in ast.walk(tree):
        if not (
            isinstance(fn, ast.FunctionDef)
            and fn.name in ("_child", "_launch_and_watch")
        ):
            continue
        for node in ast.walk(fn):
            if isinstance(node, (ast.If, ast.While)):
                expr = node.test
            elif isinstance(node, ast.Assign):
                expr = node.value
            else:
                continue
            if any(
                isinstance(n, ast.Name) and n.id == "in_thread"
                for n in ast.walk(expr)
            ):
                _rows.append((node.lineno, ast.unparse(expr), expr))
    in_thread_expressions = [(ln, src) for ln, src, _e in _rows]

    assert len(in_thread_expressions) == 9, (
        "the number of CODE expressions reading `in_thread` in the launch "
        "child changed. Every one of them is a fork/thread split, and a new "
        "one is an absence on the thread path that needs an inventory entry "
        "or a reason it needs none:\n"
        + "\n".join(f"  line {ln}  {src}" for ln, src in in_thread_expressions)
    )
    # ⭐ NINE, WHICH IS NOT THE OLD SUBSTRING TALLY OF EIGHT AND SHOULD NOT BE.
    # The old count was `text.count("not in_thread")`, so it (a) INCLUDED one
    # occurrence in a comment and (b) EXCLUDED the POSITIVE-form reads
    # (`if in_thread:`) that pick the thread arm's own behaviour — the worker
    # enter seam and the thread close-watch. Both directions of the split are
    # code, both are thread-path facts, and only one of them was being
    # counted. Split by shape so a change says WHICH kind moved.
    #
    # ⚠️ Split on the TREE, not on the unparsed text: `ast.unparse` renders the
    # third guard as `name and (not in_thread) and _platform.IS_LINUX`, so a
    # substring split re-introduces exactly the code-vs-prose confusion this
    # rewrite removed. A guard is "fork-only" iff `in_thread` appears under a
    # `not`.
    def _reads_in_thread_negated(expr) -> bool:
        for node in ast.walk(expr):
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                if any(
                    isinstance(n, ast.Name) and n.id == "in_thread"
                    for n in ast.walk(node.operand)
                ):
                    return True
        return False

    negative = [(ln, s) for ln, s, e in _rows if _reads_in_thread_negated(e)]
    positive = [(ln, s) for ln, s, e in _rows if not _reads_in_thread_negated(e)]
    # SEVEN fork-only guards (`not in_thread`, in three shapes — the bare `if`,
    # the `and IS_LINUX` form, and the computed booleans) and TWO thread-arm
    # branches (`if in_thread:` — the worker enter seam at the launch, and the
    # thread close-watch). Only FOUR of the seven are the perimeter entries the
    # test above censuses; the other three guard state this inventory does not
    # track. Both numbers are asserted so neither direction is silent.
    assert len(negative) == 7, f"the fork-only guards changed: {negative}"
    assert len(positive) == 2, f"the thread-arm branches changed: {positive}"


# --- the scanner discriminates ----------------------------------------------


def _module(tmp_path, name: str, body: str) -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


def test_a_tempfile_call_with_no_dir_is_an_out_of_perimeter_write(tmp_path) -> None:
    """PS-57's defect shape, which no grep can tell from its fix."""
    rel = _module(
        tmp_path,
        "defect.py",
        "import tempfile\n\n\ndef f():\n"
        "    return tempfile.mkstemp(prefix='persona-mtls-nsspw-')\n",
    )
    sites = SCAN.scan_module(rel, root=str(tmp_path))
    assert [s.kind for s in sites] == [SCAN.KIND_HOST_TEMP]
    assert sites[0].symbol == "f"


def test_the_same_call_with_dir_is_not_reported(tmp_path) -> None:
    """PS-57's FIX shape. Without this the scanner would fire on any write.

    The two modules differ by one keyword argument and share every token a
    line-oriented probe would match on, which is why this check is a parse.
    """
    rel = _module(
        tmp_path,
        "fixed.py",
        "import tempfile\n\n\ndef f(profile_dir):\n"
        "    return tempfile.mkstemp(prefix='p-', dir=profile_dir)\n",
    )
    assert SCAN.scan_module(rel, root=str(tmp_path)) == []


def test_a_host_home_path_is_reported(tmp_path) -> None:
    rel = _module(
        tmp_path,
        "host.py",
        "import os\n\n\ndef f():\n"
        "    return os.path.expanduser('~/.local/share/applications')\n",
    )
    sites = SCAN.scan_module(rel, root=str(tmp_path))
    assert [s.kind for s in sites] == [SCAN.KIND_HOST_HOME]


def test_defining_a_config_constant_is_not_a_write_site(tmp_path) -> None:
    """``core.config`` DEFINES the store paths; every consumer LOADS them.

    Counting the definition would report the one module that owns every path as
    eight out-of-perimeter writes — noise that would have to be silenced with a
    per-file exclusion, and a per-file exclusion on THAT module is precisely
    the blind spot this scan must not have.
    """
    rel = _module(tmp_path, "defs.py", "SESSIONS_FILE = _under_home('x', 'Y')\n")
    assert SCAN.scan_module(rel, root=str(tmp_path)) == []

    rel2 = _module(
        tmp_path,
        "uses.py",
        "def f():\n    return SessionRegistry(SESSIONS_FILE)\n",
    )
    sites = SCAN.scan_module(rel2, root=str(tmp_path))
    assert [s.kind for s in sites] == [SCAN.KIND_HOME_STORE]


def test_a_linux_guarded_write_is_marked_as_guarded(tmp_path) -> None:
    """The guard is a property of the enclosing block, not of the line.

    ``write_window_entry`` inside ``if supports_linux_desktop_integration():``
    is a Linux-only artifact; the identical call outside it is an unguarded
    host write. That difference is the platform column's whole basis.
    """
    rel = _module(
        tmp_path,
        "guarded.py",
        "import os\n\n\ndef f():\n"
        "    if supports_linux_desktop_integration():\n"
        "        return os.path.expanduser('~/.local/share/applications')\n"
        "    return None\n",
    )
    sites = SCAN.scan_module(rel, root=str(tmp_path))
    assert len(sites) == 1
    assert sites[0].linux_guarded is True


def test_the_else_arm_of_a_linux_guard_is_not_guarded(tmp_path) -> None:
    """A write in the ``else`` runs on the platforms the test EXCLUDED.

    Marking it guarded would be the opposite claim, and would hide a host write
    on exactly the two platforms the inventory is weakest about.
    """
    rel = _module(
        tmp_path,
        "elsearm.py",
        "import os\n\n\ndef f():\n"
        "    if supports_linux_desktop_integration():\n"
        "        return None\n"
        "    return os.path.expanduser('~/leak')\n",
    )
    sites = SCAN.scan_module(rel, root=str(tmp_path))
    assert len(sites) == 1
    assert sites[0].linux_guarded is False


def test_a_missing_surface_module_is_a_refusal_not_an_empty_result(tmp_path) -> None:
    """A surface that silently shrank would report a clean tree.

    That is the vacuous green this whole subsystem exists to refuse, so a
    module that moved or was renamed is reported rather than skipped.
    """
    with pytest.raises(FileNotFoundError):
        SCAN.scan_launch_surface(root=str(tmp_path), surface=("nope.py",))


def test_an_empty_surface_is_a_refusal(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        SCAN.scan_launch_surface(root=str(tmp_path), surface=())


# --- the reach reader (the half that catches PS-16) --------------------------


PS16_FIXED = (
    "class ProfileManager:\n"
    "    def update_profile(self, original_name, new_name):\n"
    "        self._rename_data_dir(original_name, new_name)\n"
    "        self._remove_window_entry(original_name)\n"
    "        return True\n"
)
PS16_REVERTED = PS16_FIXED.replace(
    "        self._remove_window_entry(original_name)\n", ""
)
PS16_EDGE = "update_profile->_remove_window_entry"


def test_a_present_removal_call_is_not_reported_missing(tmp_path) -> None:
    _module(tmp_path, "mgr.py", PS16_FIXED)
    assert SCAN.missing_removal_sites((f"mgr.py:{PS16_EDGE}",), root=str(tmp_path)) == []


def test_a_deleted_removal_call_is_reported_missing(tmp_path) -> None:
    """PS-16 exactly: the defect adds no write site and removes none.

    ⭐ THIS IS THE TEST THAT JUSTIFIES THE REACH READER EXISTING. Before it, the
    gate was built with the write scan alone, PS-16 was reverted in the real
    tree, and the gate passed — measured, not reasoned. Three of the six
    historical defects have this shape.
    """
    _module(tmp_path, "mgr.py", PS16_REVERTED)
    missing = SCAN.missing_removal_sites(
        (f"mgr.py:{PS16_EDGE}",), root=str(tmp_path)
    )
    assert missing == [f"mgr.py:{PS16_EDGE}"]


def test_a_removal_site_in_a_vanished_module_is_reported_missing(tmp_path) -> None:
    """A removal path in a module that was renamed away is exactly as gone.

    Skipping an unreadable file would let a whole reach path disappear in
    silence, which is the failure mode being guarded rather than a nicety.
    """
    missing = SCAN.missing_removal_sites(("gone.py:a->b",), root=str(tmp_path))
    assert missing == ["gone.py:a->b (module not found)"]


def test_the_rename_removal_call_is_declared_by_the_desktop_entry() -> None:
    """PS-16's own fix is pinned by the shipped inventory, not just by prose.

    ``update_profile -> _remove_window_entry`` is the one caller a reader would
    not predict, and dropping it is how the old name was stranded on the host.
    """
    entry = next(
        a for a in LP.PERIMETER_ARTIFACTS if a.site.endswith("window_entry.py:_entry_dir")
    )
    assert any(
        s.endswith("update_profile->_remove_window_entry")
        for s in entry.removal_sites
    ), entry.removal_sites


# --- the gate itself ---------------------------------------------------------


def test_the_gate_is_green_on_the_shipped_tree(check, ctx) -> None:
    outcome = run_check(check, ctx)
    assert outcome.status == PASS, outcome.detail
    # The harness refuses a PASS with no falsification; this asserts the
    # falsification actually produced a sentence rather than an empty string
    # that happened not to trip it.
    assert outcome.falsification
    assert "PS-57" in outcome.falsification


def test_the_gates_falsification_is_real(check, ctx) -> None:
    """``falsify`` must PROVE the check catches its class, not describe it.

    It plants a real module with PS-57's shape, scans it, and separately drives
    the reach reader over a planted PS-16 pair. A sentence naming both is the
    evidence the harness requires before publishing a green.
    """
    proven = check.falsify(ctx)
    assert "host-temp" in proven
    assert "PS-16" in proven


def test_the_gate_goes_red_on_an_unaccounted_write_site(check, ctx, monkeypatch) -> None:
    """OBSERVATION 1, pinned.

    The inventory is narrowed rather than the tree widened — same arithmetic,
    no source mutated in a test run — so a real site becomes unaccounted and
    the check must NAME it.
    """
    kept = tuple(
        a
        for a in LP.PERIMETER_ARTIFACTS
        if not a.site.endswith("session_registry.py:default_registry")
    )
    monkeypatch.setattr(LP, "PERIMETER_ARTIFACTS", kept)
    outcome = run_check(check, ctx)
    assert outcome.status == FINDING
    assert any(
        "UNACCOUNTED" in e and "session_registry.py" in e for e in outcome.evidence
    ), outcome.evidence


def test_the_gate_goes_red_when_an_entry_names_a_site_that_is_gone(
    check, ctx, monkeypatch
) -> None:
    """The other direction, and it is why the check reads both.

    An inventory that could be satisfied by DELETING its own entries certifies
    nothing — so an entry claiming a static site the scan cannot find is a
    finding just as loudly as an unlisted one.
    """
    ghost = LP.Artifact(
        artifact="a site that does not exist",
        site="src/services/browser/process.py:_ps355_no_such_symbol",
        scope=LP.SCOPE_HOST,
        platforms=LP.ALL_PLATFORMS,
        disposition=LP.DISPOSITION_EXCEPTION,
        reason="planted by a test",
    )
    monkeypatch.setattr(LP, "PERIMETER_ARTIFACTS", LP.PERIMETER_ARTIFACTS + (ghost,))
    outcome = run_check(check, ctx)
    assert outcome.status == FINDING
    assert any("VANISHED" in e for e in outcome.evidence), outcome.evidence


def test_the_gate_goes_red_when_a_declared_removal_path_is_gone(
    check, ctx, monkeypatch
) -> None:
    """OBSERVATION 3's mechanism, pinned without editing the real source."""
    broken = tuple(
        LP.Artifact(
            artifact=a.artifact,
            site=a.site,
            scope=a.scope,
            platforms=a.platforms,
            disposition=a.disposition,
            reached_by=a.reached_by,
            removal_sites=(
                a.removal_sites
                + ("src/services/profile/manager.py:update_profile->_ps355_gone",)
                if a.removal_sites
                else a.removal_sites
            ),
            reason=a.reason,
            detected=a.detected,
        )
        for a in LP.PERIMETER_ARTIFACTS
    )
    monkeypatch.setattr(LP, "PERIMETER_ARTIFACTS", broken)
    outcome = run_check(check, ctx)
    assert outcome.status == FINDING
    assert any("UNREACHED" in e for e in outcome.evidence), outcome.evidence


def test_a_malformed_inventory_is_cannot_run_and_never_a_finding(
    check, ctx, monkeypatch
) -> None:
    """"This check is broken" and "the product is broken" are different messages.

    An inventory that cannot be read is the first, and reporting it as a
    FINDING would raise a false alarm about the product on the strength of a
    typo in a list.
    """
    monkeypatch.setattr(
        LP,
        "PERIMETER_ARTIFACTS",
        (
            LP.Artifact(
                artifact="broken",
                site="no-colon-here",
                scope="nonsense",
                platforms=(),
                disposition="???",
            ),
        ),
    )
    outcome = run_check(check, ctx)
    assert outcome.status == CANNOT_RUN
    assert outcome.status != FINDING


# --- the gate is load-bearing, not merely present ---------------------------


def test_the_check_is_in_the_registry_and_needs_no_launch() -> None:
    entry = next(c for c in CHECKS if c.name == "launch-perimeter-inventory")
    assert entry.needs_launch is False


def test_the_check_is_required_by_the_ci_lane() -> None:
    """Adding a no-launch check does not break the runner; NAMING it is what
    makes it load-bearing. Without this the gate ships present and optional.
    """
    import importlib.util
    from pathlib import Path

    script = (
        Path(SCAN.repo_root()) / ".github" / "scripts" / "run_behaviour_checks.py"
    )
    spec = importlib.util.spec_from_file_location("_ps355_runner", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "launch-perimeter-inventory" in module.EXPECTED_CHECKS


def test_the_narrowness_of_the_scan_is_admitted_not_implied() -> None:
    """AC5's spirit: a surface this check does NOT observe is stated.

    The scan is static and its population is ``LAUNCH_SURFACE``, so a
    dynamically-reached write and a write in an unlisted module are both
    invisible. An unadmitted gap reads as covered, which is worse than no gate.
    """
    from src.services.verify.behaviour import UNCOVERED_SURFACES

    surfaces = " ".join(s for s, _ in UNCOVERED_SURFACES)
    whys = " ".join(w for _, w in UNCOVERED_SURFACES)
    assert "LAUNCH-PERIMETER INVENTORY" in surfaces
    assert "DYNAMICALLY" in whys
    assert "LAUNCH_SURFACE" in whys
