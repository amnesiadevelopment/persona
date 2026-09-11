"""PS-321: the Chromium known-bad list must reach the build that is ALREADY
INSTALLED, not only the one persona is about to fetch.

THE DEFECT, IN ONE SENTENCE. Every consumer of ``engine/policy.py`` takes a
FETCHED tag — ``fetch_latest_checked``, ``ensure_engine``'s first-install lane,
``_engine_update_available`` and ``_record_engine_check`` — so blocklisting the
tag an operator is already running refuses nothing, says nothing, and the
sidebar row goes on rendering that build exactly as it renders a healthy one.
``ensure_engine`` short-circuits on ``is_installed()`` and the launch path
(``browser/process.py``) never consults the policy module at all, so no lane
re-examines a build on disk.

WHY THE CONTROL IN THIS FILE IS LOAD-BEARING, AND NOT CEREMONY. Without it the
finding reads as "the blocklist is weak" — an opinion. With it the SAME
blocklist entry is shown fully effective one moment BEFORE the build is
installed and completely inert one moment AFTER, which locates the defect at
the acquisition boundary rather than describing a mechanism as inadequate.
``test_the_same_entry_is_effective_one_moment_before_install`` is that control;
it passes both before and after the fix, and a run in which it fails means the
harness is not exercising the real policy at all.

WHAT WAS DECIDED, AND WHY IT IS NOT THE OBVIOUS SYMMETRY (branch (b)).
Firefox's ``BROKEN_VERSIONS`` retires an installed build at launch resolution:
``engine_install.installed_builds()`` skips it and ``active_build()`` resolves
out of what remains. That is only possible because Firefox keeps MANY builds,
one directory per tag, so retiring one falls back to the next. Chromium keeps
ONE un-versioned tree. Refusing to launch a blocklisted Chromium build is
therefore STRICTLY MORE DESTRUCTIVE than the reference implementation it would
be imitating: it leaves an operator with no browser whenever ``rollback_target()``
is empty — which ``_engine_rollback_pending_row``'s own docstring measures as
the LARGER population, not an edge case. The tree already rules against that
outcome in its own words ("an app with no engine at all is worse than one with
an untested engine"; "a pin naming a build that is NOT installed is IGNORED
rather than honoured-into-nothing"). So the launch is never refused here, and
``tests/test_launcher_engine.py`` / ``browser/process.py`` are deliberately
untouched — see ``test_the_launch_path_still_never_consults_the_policy``.

WHAT IS ASSERTED. The RENDERED value (``engine_text.value``), driven through
the real ``_refresh_engine_text``, never "a helper was called" — the same
distinction PS-49 records as load-bearing, because the status assignment it
first shipped was computed and then painted over, and an assertion on the
attribute passed against the defect.
"""

import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import src.ui.app as app_mod  # noqa: E402
from src.services.engine import policy  # noqa: E402

BAD = "148.0.7778.215"
GOOD_OLDER = "148.0.1.1"
GOOD_NEWER = "149.0.8000.10"


def _blocklist(tmp_path, monkeypatch, *tags: str) -> None:
    """Put ``tags`` on the known-bad list the way an OPERATOR does — the local
    ``engine-policy.json`` escape hatch, read at call time.

    Deliberately the local file rather than monkeypatching the frozenset: this
    is the exact gesture ``engine-gpu-variance.yml`` instructs after a red run,
    and it exercises ``known_bad_versions()``'s file read rather than replacing
    the module constant the read is supposed to union with.
    """
    pf = tmp_path / "engine-policy.json"
    pf.write_text(json.dumps({"known_bad_versions": list(tags)}), encoding="utf-8")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))


def _installed(monkeypatch, tag: str) -> None:
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: tag)
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)


def _row(**over):
    """An App-shaped stub wired to the REAL ``_refresh_engine_text``.

    Everything the render touches is present and inert; ``_ui`` runs the
    closure inline and there is no sidebar host to rebuild, so what an operator
    would read lands in ``engine_text.value`` and nowhere else.
    """
    base = dict(
        engine_text=SimpleNamespace(value=""),
        _engine_latest="",
        _engine_status="",
        _engine_unverifiable_tag="",
        _ui=lambda fn: fn(),
        _sidebar_host=None,
        _safe_update=lambda: None,
        _log=lambda m: None,
    )
    base.update(over)
    stub = SimpleNamespace(**base)
    stub._engine_update_available = lambda: app_mod.App._engine_update_available(stub)
    # The REAL method, bound off the class — it is the thing under test in
    # every assertion here, so stubbing it would test the test.
    stub._installed_build_refusal = lambda: app_mod.App._installed_build_refusal(stub)
    return stub


def _render(stub, status: str = "") -> str:
    app_mod.App._refresh_engine_text(stub, status)
    return stub.engine_text.value


# ---------------------------------------------------------------------------
# AC1 — the defect, and the control that locates it
# ---------------------------------------------------------------------------


def test_a_blocklisted_installed_build_does_not_render_as_a_healthy_engine(
    tmp_path, monkeypatch
):
    """THE DEFECT. The GPU-variance gate goes red, the operator adds the tag to
    the known-bad list — and they had already installed it inside the ~24h
    window the workflow itself names (detection daily, installation hourly).

    persona KNOWS the build is bad: ``policy.check`` answers ``known_bad`` for
    it. The row rendered the version string anyway, byte-identical to a healthy
    engine, because ``_refresh_engine_text``'s final arm falls through to
    ``current_version()`` and no arm above it had anything to say — the offer is
    suppressed (nothing newer exists), so ``_engine_update_available`` is False,
    and ``_record_engine_check``'s refusal branch is gated on
    ``is_newer(tag, current_version())``, which is False because the bad build
    is not newer than itself.
    """
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)

    # PREMISE, not decoration: if this were OK the rest of the test would be
    # asserting about a build persona has no opinion on.
    assert policy.check(BAD)[0] == policy.KNOWN_BAD
    assert policy.is_installable(BAD) is False

    stub = _row(_engine_latest=BAD)
    rendered = _render(stub)

    assert rendered != BAD, (
        "the row rendered the blocklisted installed build exactly as it renders "
        "a healthy engine — the operator's own instruction reached nothing"
    )
    assert "known bad" in rendered.lower(), (
        f"the row must name the state persona is in; it read {rendered!r}"
    )


def test_the_same_entry_is_effective_one_moment_before_install(
    tmp_path, monkeypatch
):
    """THE CONTROL, AND IT IS NON-OPTIONAL.

    The IDENTICAL blocklist entry, one moment earlier: the operator is still on
    an older build and the bad one is merely on offer. Here the mechanism works
    perfectly — the offer is withheld and the refusal reaches the row.

    That pair is the whole evidential value of this file. A blocklist entry
    that fires before install and goes inert after it is a defect located AT A
    SEAM; the same finding without this control is the claim "the blocklist is
    inadequate", which is an opinion. It also proves the harness drives the real
    policy: if this ever fails, nothing else in this file is measuring anything.
    """
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, GOOD_OLDER)
    monkeypatch.setattr(app_mod.engine, "pinned_build", lambda: "")

    stub = _row(_engine_latest=BAD)

    assert stub._engine_update_available() is False, (
        "a build on the known-bad list must never be advertised as available"
    )

    line = app_mod.App._record_engine_check(stub, BAD)
    assert stub._engine_status == "engine update blocked", (
        "before install the refusal reaches the row — this is the behaviour the "
        "installed-build case is measured against"
    )
    assert "known-bad" in line, "and the operator is told why"

    assert _render(stub) == "engine update blocked"


def test_a_clean_installed_build_still_renders_its_own_version(monkeypatch):
    """The negative control. With nothing blocklisted the row is unchanged —
    a guard that repaints the ordinary case would be a regression dressed as a
    fix, and ``KNOWN_BAD_VERSIONS`` ships EMPTY, so this is what essentially
    every operator sees."""
    monkeypatch.setattr(policy, "POLICY_FILE", "/nonexistent/engine-policy.json")
    _installed(monkeypatch, GOOD_NEWER)

    assert policy.check(GOOD_NEWER)[0] == policy.OK

    stub = _row(_engine_latest=GOOD_NEWER)
    assert _render(stub) == GOOD_NEWER


def test_the_shipped_list_reaches_an_installed_build_too(monkeypatch):
    """The workflow's own instruction is to add the tag to ``KNOWN_BAD_VERSIONS``
    in ``policy.py`` — the COMMITTED list, not the operator's file. Both lanes
    funnel through ``known_bad_versions()``, and this pins that the committed
    one reaches an installed build as well, so the remediation the CI workflow
    documents verbatim is the one that is actually tested."""
    monkeypatch.setattr(policy, "POLICY_FILE", "/nonexistent/engine-policy.json")
    monkeypatch.setattr(policy, "KNOWN_BAD_VERSIONS", frozenset({BAD}))
    _installed(monkeypatch, BAD)

    stub = _row(_engine_latest=BAD)
    rendered = _render(stub)

    assert rendered != BAD
    assert "known bad" in rendered.lower()


# ---------------------------------------------------------------------------
# AC3 — the no-rollback-target case, which is the LARGER population
# ---------------------------------------------------------------------------


def test_with_nothing_to_go_back_to_the_row_still_tells_the_truth(
    tmp_path, monkeypatch
):
    """AC3, and the case that rules option (a) out.

    A machine that upgraded INTO v3.0.0 with an engine already present has a
    ``version.txt`` and no build record — ``ensure_engine`` short-circuited on
    ``is_installed()`` and wrote nothing — so ``rollback_target()`` is empty.
    ``_engine_rollback_pending_row``'s docstring measures that as the LARGER
    population.

    Refusing the launch there would leave those operators with no browser at
    all. So the launch proceeds, the row states the refusal, and no gesture is
    fabricated: the truth is told with or without a way back, and the row text
    does not change depending on whether one exists — a status that promised
    "go back" on a machine with nothing to go back to would be the
    gesture-less-remedy defect this project has already shipped once.
    """
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)
    monkeypatch.setattr(app_mod.engine, "rollback_target", lambda: ("", ""))

    stub = _row(_engine_latest=BAD)
    rendered = _render(stub)

    assert rendered != BAD
    assert "known bad" in rendered.lower()
    assert "go back" not in rendered.lower(), (
        "with no rollback target the row must not name a gesture that cannot "
        "work — a remedy naming no reachable action is worse than none"
    )


def test_a_way_back_does_not_change_what_the_row_says(tmp_path, monkeypatch):
    """The other half of AC3. The way back is offered by
    ``_engine_rollback_row`` — a row of its own, directly beneath this one,
    which already renders whenever ``rollback_target()`` is non-empty. The
    status line therefore states the STATE and never duplicates the gesture,
    so the two cannot drift into describing the same situation differently."""
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)
    monkeypatch.setattr(
        app_mod.engine, "rollback_target", lambda: (GOOD_OLDER, "sha256:a")
    )

    stub = _row(_engine_latest=BAD)
    assert "known bad" in _render(stub).lower()


def test_a_good_newer_build_is_still_offered_over_a_blocklisted_installed_one(
    tmp_path, monkeypatch
):
    """AC4's other state, pinned rather than left to be discovered.

    A WAY FORWARD OUTRANKS A STANDING COMPLAINT. When an acceptable newer build
    exists, ``_engine_update_available`` is True and the row renders that tag
    with the accent dot lit — so the operator is shown the thing they can act
    on, and one click leaves the blocklisted build behind. AC4 is satisfied
    literally here: what renders is the UPSTREAM tag, never the installed one,
    so a blocklisted build is not presented as a healthy engine in this state
    either.

    This is also why option (c) ("treat a blocklisted installed build as making
    any build an update") was rejected as the primary mechanism rather than as
    wrong: the forward offer already happens whenever a forward build exists,
    with no change to ``is_newer`` — which four consumers share. (c) would have
    altered a shared predicate's meaning to reach the ONE case where there is
    nothing newer, which is precisely the case this ticket is about and the one
    ``_installed_build_refusal`` answers.
    """
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)
    monkeypatch.setattr(app_mod.engine, "pinned_build", lambda: "")

    stub = _row(_engine_latest=GOOD_NEWER)

    assert stub._engine_update_available() is True, (
        "an acceptable newer build must still be offered — the operator's exit "
        "from a blocklisted build is the ordinary update path"
    )
    rendered = _render(stub)
    assert rendered == GOOD_NEWER
    assert rendered != BAD, "the blocklisted installed build is never rendered"


def test_the_launch_path_still_never_consults_the_policy():
    """The launch is NOT refused, and that is a decision rather than an
    omission — see this module's docstring for why Chromium's single
    un-versioned tree makes Firefox's retire-the-build gesture strictly more
    destructive here than in the engine it would be copied from.

    Asserted on the PARSED module, not on a substring of its source: the word
    "policy" appears in ``process.py`` in a prose comment and inside the
    Chromium flag ``--force-webrtc-ip-handling-policy``, neither of which is a
    consultation, and it legitimately imports the unrelated ``env_policy`` /
    ``launch_policy`` modules next door. What must stay absent is an import of
    ``services.engine.policy`` and any call into it.
    """
    import ast

    from src.services.browser import process as proc

    tree = ast.parse(open(proc.__file__, encoding="utf-8").read())

    imported = [
        n.module or ""
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    ] + [
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for a in n.names
    ]
    assert not [m for m in imported if m.split(".")[-1:] == ["policy"]], (
        "the launch path imported the engine policy module — a blocklisted "
        "build must still start, because Chromium keeps ONE engine tree and "
        "refusing it leaves an operator with no browser"
    )

    called = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not called & {"check", "is_installable", "known_bad_versions"}, (
        f"the launch path grew a policy consultation "
        f"({sorted(called & {'check', 'is_installable', 'known_bad_versions'})})"
    )


# ---------------------------------------------------------------------------
# AC5 — a refusal must read as a decision persona made
# ---------------------------------------------------------------------------


def test_the_installed_build_refusal_is_never_worded_as_a_transfer_failure(
    tmp_path, monkeypatch
):
    """The refuse/failed vocabulary is a correctness gate on this lane, not
    ceremony: an operator told a download failed retries forever, and no retry
    can change a decision persona made. Nothing here moved a byte — the build
    is already on disk — so any wording implying a transfer is a plain
    falsehood as well as a trap."""
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)

    logs: list[str] = []
    stub = _row(_engine_latest=BAD, _log=logs.append)
    rendered = _render(stub)

    blob = f"{rendered} {' '.join(logs)}".lower()
    for forbidden in ("download failed", "update failed", "downloading", "retry"):
        assert forbidden not in blob, (
            f"{forbidden!r} reads as a transfer problem; this is a governance "
            "refusal about a build that is already installed"
        )


def test_a_ceiling_the_operator_lowered_below_their_own_build_reads_differently(
    tmp_path, monkeypatch
):
    """``check()`` answers two refusals and they must not collapse into one
    message — the same rule
    ``test_a_known_bad_refusal_reads_differently_from_a_ceiling_refusal``
    already holds the acquisition lane to.

    ABOVE_CEILING is reachable on an installed build the moment an operator
    lowers ``max_tested_major`` below the engine they are running, and unlike a
    known-bad entry that one IS locally reversible — so it must point at their
    own policy file rather than at persona.
    """
    pf = tmp_path / "engine-policy.json"
    pf.write_text(json.dumps({"max_tested_major": 147}), encoding="utf-8")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    _installed(monkeypatch, BAD)

    assert policy.check(BAD)[0] == policy.ABOVE_CEILING

    stub = _row(_engine_latest=BAD)
    rendered = _render(stub)

    assert rendered != BAD
    assert "known bad" not in rendered.lower(), (
        "an operator's own ceiling is not persona calling their build broken"
    )
    assert "policy" in rendered.lower(), (
        f"the ceiling refusal must point at their own policy file; read "
        f"{rendered!r}"
    )
    assert "above" in rendered.lower() and "below" not in rendered.lower(), (
        "ABOVE_CEILING means the INSTALLED build exceeds the ceiling the "
        f"operator set beneath it — saying 'below' inverts the fact and points "
        f"the remedy the wrong way; read {rendered!r}"
    )


def test_the_refusal_names_no_gesture_and_promises_no_log_line(
    tmp_path, monkeypatch
):
    """A remedy that names an action the product does not perform is the defect
    this project has already shipped once (``UNSUPPORTED_COUNTRY_NOTE``), and
    "see the log" is that shape in miniature: ``_refresh_engine_text`` runs on
    every sidebar rebuild, so NOTHING may log from this path — a line would
    repeat for as long as the blocklist entry stands. A row telling an operator
    to read a log that says nothing is worse than one that simply states the
    state.

    The gesture, where one exists, is ``_engine_rollback_row`` directly beneath
    this line, which owns it in both states.
    """
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)

    logs: list[str] = []
    stub = _row(_engine_latest=BAD, _log=logs.append)
    rendered = _render(stub).lower()

    assert logs == [], (
        "the row's refresh must not log — it runs on every sidebar rebuild, so "
        f"a line here repeats forever: {logs!r}"
    )
    for promised in ("see the log", "go back", "click", "restart", "reinstall"):
        assert promised not in rendered, (
            f"{promised!r} names an action this line does not perform"
        )


# ---------------------------------------------------------------------------
# ordering — the standing refusal must not swallow a gesture's answer
# ---------------------------------------------------------------------------


def test_an_answer_to_an_operator_gesture_still_outranks_the_standing_refusal(
    tmp_path, monkeypatch
):
    """``_engine_status`` carries the answer to something the operator just
    DID — "couldn't go back — see the log" after a failed revert, or PS-49's
    "engine could not be verified". The installed-build refusal is a standing
    fact that will still be true a second later, so it must not paint over a
    reply to a gesture; an operator who clicks and gets no answer clicks
    again."""
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)

    stub = _row(_engine_latest=BAD, _engine_status="couldn't go back — see the log")
    assert _render(stub) == "couldn't go back — see the log"


def test_an_in_flight_status_still_outranks_the_standing_refusal(
    tmp_path, monkeypatch
):
    """The transient argument (``"checking..."``, ``"downloading..."``) is the
    row's live state and wins over everything — a spinner replaced by a
    standing sentence is the wedged-row defect
    ``test_a_refusal_actually_paints_the_row_instead_of_wedging_on_downloading``
    exists to forbid, arrived at from the other side."""
    _blocklist(tmp_path, monkeypatch, BAD)
    _installed(monkeypatch, BAD)

    stub = _row(_engine_latest=BAD)
    assert _render(stub, "checking...") == "checking..."


def test_an_unreadable_installed_version_is_not_reported_as_known_bad(
    tmp_path, monkeypatch
):
    """``current_version()`` answers ``""`` for a missing, torn or undecodable
    ``version.txt``, and ``policy.check("")`` is OK by contract — "no tag" is a
    read failure the caller already reports, and turning it into a governance
    refusal would mislabel it. The row must fall through to its ordinary
    unknown-version rendering rather than accusing a build it could not read."""
    _blocklist(tmp_path, monkeypatch, BAD)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "")
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: False)

    stub = _row(_engine_latest="")
    assert _render(stub) == "unknown"


def test_an_unreadable_policy_file_does_not_take_the_row_down(
    tmp_path, monkeypatch
):
    """The row must render even when the policy read raises. It is consulted on
    every refresh, so a raise here would blank the whole sidebar — the same
    fail-quiet direction ``_engine_rollback_row`` already takes for an
    unreadable build record."""
    _installed(monkeypatch, GOOD_NEWER)

    def boom(_tag):
        raise OSError("policy file is a directory")

    monkeypatch.setattr(app_mod.engine_policy, "check", boom)

    stub = _row(_engine_latest=GOOD_NEWER)
    assert _render(stub) == GOOD_NEWER
