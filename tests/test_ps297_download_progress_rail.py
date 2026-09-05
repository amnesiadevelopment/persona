"""PS-297: the DOWNLOAD-PROGRESS cluster must fit the same 200px rail too.

WHAT THIS ADDS, AND WHY IT IS A THIRD FILE. PS-229 (``087d7bd``) established a
width + lines bound for the sidebar rail and shipped ``sidebar_status_text`` /
``rollback_row`` to carry it, converting the three ENGINE rows. PS-271
(``a756ad1``) found the APP VERSION panel had been missed and converted three
sites inside ``_build_version_panel`` — the held row, the retained row and the
rollback status line. NEITHER commit has a hunk touching the download-progress
branch (``git show`` on both, grepped for ``updating to`` / ``fmt_line``,
produces nothing), and PS-271's own test file says so in writing: *"the
download-progress rows belong to a different surface"*. This file is that
surface, and the reading is scope by omission — nothing in the tree records an
argument for the progress lines being allowed to overflow.

``tests/test_ps229_engines_rail.py`` and ``tests/test_ps271_version_panel_rail.py``
are left UNMODIFIED. This widens the bound to a fourth site; it does not
redefine it. The three files assert the same property about different surfaces.

THE STRUCTURAL POINT — WHY THIS GUARD ITERATES. Nothing below measures a
literal list of strings. The download branch is DRIVEN into each of its
reachable states and whatever the panel builds in that state is what gets read,
so a state added later is caught without anyone remembering to come here:

  * the LABEL states (``connecting…`` / percent / bytes-only) are DISCOVERED by
    walking the ``label = ...`` assignments in ``_build_version_panel``'s
    download branch in the source, and the panel is then really built with the
    ``(done, total)`` that reaches each one;
  * the DETAIL line is read out of a panel built with both of ``fmt_line``'s
    shapes — known total (the "X of Y   speed   ETA" form) and unknown total
    (the fallback that is the ONE state that already fitted).

THE TWO SITES ARE BOUNDED DIFFERENTLY, ON PURPOSE, and the asymmetry is the
substance of this ticket rather than an inconsistency:

  * THE HEADLINE is bounded BY RELOCATION. Its informative half sits at the
    TAIL — ellipsised at the budget, ``"updating to 3.0.2 · connecting…"``
    reaches the operator as ``"updating to 3.0.2 · c…"`` and the PROGRESS is
    what gets eaten. So the target version moves into the tooltip, exactly as
    ``_ROLLBACK_LABEL`` moved a 30-character build identifier, and what stays
    on screen FITS: 22 characters at its widest. Held to the BUDGET here.
  * THE DETAIL LINE is bounded BY ELLIPSIS. Its primary reading is its HEAD
    (``52.4 MB of 123.7 MB``); the speed and ETA behind it are elaboration, so
    truncation costs the secondary half — the right way round. It reports an
    outcome and must say what it says, so it is held to the BOUND and NOT to a
    character count, exactly as the rollback status line is. Its tail is not
    dropped either: the full line is in the row's tooltip.

WHAT THIS GUARD DELIBERATELY DOES NOT MODEL, stated rather than left implied:

  1. It does not render pixels. ``flet`` builds the control tree here; nothing
     lays it out. The bound is asserted as the PARAMETER SET PS-229
     established — ``expand`` + ``no_wrap`` + ``max_lines`` + ``overflow`` —
     which is the same evidence both sibling guards rest on. It is not a
     screenshot. ``tests/ui_driver/live_ps297.py`` is the screenshot.
  2. It does not re-derive the 22-character budget. That number is PS-229's,
     adopted so the panels sharing one rail are held to ONE bound rather than
     to several. (``_VERSION_MAX_CHARS = 17`` is a different, narrower budget
     for the ~110px engine version cell — do not conflate them.)
  3. The DETAIL line is bounded, NOT budgeted — see the asymmetry above. At
     ``size=9`` its true character budget is somewhat larger than the
     headline's anyway, but at 41 characters it overflows on any reading,
     which is why it is in scope rather than deferred.
  4. It covers the download branch ONLY. The version line, the auto-update
     toggle (a fixed-width bracket with its own ``no_wrap``), the rollback row
     and the rollback status line are PS-271's, asserted there and not
     re-governed here — a guard that fails for a reason outside its own
     ticket gets weakened rather than obeyed.
  5. THE LABEL DISCOVERY WALK READS THE BRANCH'S ``label`` ASSIGNMENTS, not
     every string the panel can render. It proves the three label states this
     file drives are the three the source can produce, so a FOURTH added later
     goes red HERE rather than silently escaping the budget. It cannot know
     what an interpolated label would evaluate to at runtime, which is why the
     budget assertion measures a panel that was really BUILT.
"""

import ast
import inspect
import os
import tempfile
import textwrap
from pathlib import Path

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import flet as ft  # noqa: E402

from src.ui import app as app_mod  # noqa: E402
from src.ui import progress_fmt as pf  # noqa: E402
from tests.test_app_ui import _walk_texts, make_app  # noqa: E402


#: PS-229's number, adopted rather than re-derived: the rail's content width is
#: about 22 monospace characters. Every panel in the rail is held to it.
BUDGET = app_mod._RAIL_MAX_CHARS

#: A hostile target, deliberately — the fit is measured under the load that
#: breaks it. A real persona pre-release tag, longer than the "3.0.2" the
#: shipped release happens to be.
LONG_TARGET = "3.0.10-beta.1"

#: A realistic app installer: ~124 MB, read a third of the way through.
TOTAL = 123_700_000
PART = 52_400_000


def _texts(control) -> list[ft.Text]:
    """Every Text in a control tree, so the cluster can be inspected whole."""
    found: list[ft.Text] = []

    def walk(node) -> None:
        if isinstance(node, ft.Text):
            found.append(node)
        for attr in ("content", "controls"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in child if isinstance(child, list) else [child]:
                walk(c)

    walk(control)
    return found


def _tooltips(control) -> list[str]:
    """Every tooltip string in a control tree, so a RELOCATED value can be
    shown to have landed somewhere rather than merely to have left."""
    found: list[str] = []

    def walk(node) -> None:
        tip = getattr(node, "tooltip", None)
        if isinstance(tip, str) and tip:
            found.append(tip)
        for attr in ("content", "controls"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in child if isinstance(child, list) else [child]:
                walk(c)

    walk(control)
    return found


def _downloading_app(monkeypatch, *, done, total, target=LONG_TARGET):
    """An App stub whose version panel is genuinely IN the download branch.

    The branch's own condition is set — ``_app_update_status == "downloading"``
    — rather than any render-time shortcut, so what is measured is the panel
    the ordinary unattended update path produces (``app.py`` sets exactly this
    in the download worker, and ``is_auto_update_enabled()`` defaults True).
    """
    import time

    monkeypatch.setattr(app_mod.app_update, "held_version", lambda: "")
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: "")
    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app = make_app(None)
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_rollback_status = ""
    app._update_staged = ""
    app._app_latest = target
    app._app_update_status = "downloading"
    app._app_update_done = done
    app._app_update_total = total
    # A real elapsed time, so fmt_line produces a real speed and ETA rather
    # than the degenerate 0.0 MB/s a zero would give — the long form is the
    # one that overflows, and measuring the short one would be measuring the
    # state this ticket is not about.
    app._update_start_t = time.monotonic() - 25.0
    return app


# --- the states the branch can be in, enumerated by BUILDING it -----------
#
# Not a list of strings: the panel is constructed in each state and whatever
# it renders is what gets measured. A label that grows a runtime value back is
# caught because the value is really interpolated, not because someone
# remembered to add its length to a tuple.


def _cluster_texts(panel, quiet_values: set[str]) -> list[ft.Text]:
    """The DOWNLOAD CLUSTER's own Texts, DISCOVERED rather than listed.

    Scoping matters and is limitation 4: ``_build_version_panel`` also renders
    the version line and the auto-update toggle, which are PS-271's surface and
    are asserted there. A guard that swept them in would fail for reasons
    outside this ticket, and a guard that fails for the wrong reason gets
    weakened rather than obeyed.

    So the cluster is taken as a DIFFERENCE: the lines a downloading panel
    renders that the SAME app renders without the download branch entered.
    That is structural — it needs no list of strings and no prefix match, so a
    line added to the branch tomorrow is in scope automatically.
    """
    return [t for t in _texts(panel) if (t.value or "") not in quiet_values]


def _quiet_values(app) -> set[str]:
    """What this same app's panel renders with the download branch NOT
    entered — the subtrahend of the difference above. Restored afterwards, so
    reading the baseline cannot disturb the state under test."""
    status, done, total = (
        app._app_update_status, app._app_update_done, app._app_update_total,
    )
    app._app_update_status = ""
    try:
        return set(_walk_texts(app._build_version_panel()))
    finally:
        app._app_update_status = status
        app._app_update_done, app._app_update_total = done, total


def _download_states(monkeypatch, *, target=LONG_TARGET):
    """(name, built panel) for every state the download branch can render."""
    return [(name, panel) for name, _app, panel in _download_apps(monkeypatch, target=target)]


def _download_apps(monkeypatch, *, target=LONG_TARGET):
    """(name, app, built panel) — the same three states, keeping the app so
    the quiet baseline above can be taken from it."""
    return [
        (name, app, app._build_version_panel())
        for name, app in _download_state_apps(monkeypatch, target=target)
    ]


def _download_state_apps(monkeypatch, *, target=LONG_TARGET):
    return [
        # no bytes yet -> "connecting…", and the unknown-total fmt_line
        ("connecting", _downloading_app(
            monkeypatch, done=0, total=TOTAL, target=target
        )),
        # bytes and a known total -> a percent, and the LONG fmt_line
        ("percent", _downloading_app(
            monkeypatch, done=PART, total=TOTAL, target=target
        )),
        # bytes but NO total -> a bare MB count, and fmt_line's fallback
        ("bytes-only", _downloading_app(
            monkeypatch, done=5_200_000, total=0, target=target
        )),
    ]


def test_the_three_download_states_are_all_reachable_from_this_fixture(
    monkeypatch,
):
    """The positive control for every test below. A fixture that silently
    never entered the download branch — or that produced one state three
    times — would satisfy a budget assertion vacuously, which is the exact
    shape PS-11 keeps catching in this repo.

    So: each state must render text, and the three must genuinely DIFFER.
    """
    seen: dict[str, str] = {}
    for name, panel in _download_states(monkeypatch):
        values = [t.value or "" for t in _texts(panel)]
        assert values, f"the {name} state rendered no text at all"
        headline = [v for v in values if v.startswith("updating")]
        assert headline, (name, values, "the download branch was never entered")
        seen[name] = headline[0]

    assert len(set(seen.values())) == 3, seen
    assert "connecting" in seen["connecting"], seen
    assert "%" in seen["percent"], seen
    assert "MB" in seen["bytes-only"], seen


def test_every_line_the_download_branch_renders_is_bounded(monkeypatch):
    """AC1 — BOTH halves, because either alone is not a fix.

    ``max_lines``/``overflow`` bound the text's own layout; ``expand=True`` is
    what bounds its WIDTH inside a Row — a Text that did not ask to flex is
    granted its intrinsic width and overflows with the ellipsis never
    engaging. Bounding lines without bounding width is precisely the fix that
    looks right and changes nothing on screen (``sidebar_status_text``'s own
    docstring records this, and the no-``expand`` mutation is one of the two
    falsifications ``live_ps297.py`` runs).

    Every line THE DOWNLOAD CLUSTER renders is held to it, and the cluster is
    discovered as a difference against the same app's quiet panel rather than
    by matching a prefix — so a line added to the branch tomorrow is in scope
    without anyone remembering. It is scoped to the cluster and not to the
    whole panel for the reason limitation 4 gives.
    """
    for name, app, panel in _download_apps(monkeypatch):
        quiet = _quiet_values(app)
        cluster = _cluster_texts(panel, quiet)
        assert cluster, (name, "the download branch contributed no text at all")
        for t in cluster:
            assert t.max_lines == 1, (name, t.value, "an unbounded line")
            assert t.no_wrap is True, (name, t.value, "an unbounded line")
            assert t.overflow == ft.TextOverflow.ELLIPSIS, (name, t.value)
            assert t.expand is True, (
                name, t.value, "bounded lines but not width — the no-op fix",
            )


def test_the_progress_headline_fits_the_rail_in_every_state(monkeypatch):
    """AC3 — THE BUDGET, and it is asserted rather than approximated because
    the whole argument for relocating the version is that this line FITS
    afterwards.

    A plain ellipsis here would have eaten the tail, and the tail is the
    progress: ``"updating to 3.0.2 · connecting…"`` truncated at 22 reads
    ``"updating to 3.0.2 · c…"``. Measured on the shipped form, the widest
    state is ``"updating · connecting…"`` at exactly the budget, so nothing is
    truncated at all — which is what makes the relocation a fix rather than a
    different way of losing the same information.

    Under the HOSTILE target too: the point of moving the version out is that
    no runtime string can widen this line, so a longer tag must not move the
    number. Asserted with the pre-release tag by default and re-asserted with
    the "new version" fallback, which is the longer of the two real targets.
    """
    for target in (LONG_TARGET, "", "3.0.2"):
        for name, panel in _download_states(monkeypatch, target=target):
            headline = [
                t for t in _texts(panel) if (t.value or "").startswith("updating")
            ]
            assert headline, (target, name, "no progress headline was rendered")
            for t in headline:
                assert len(t.value) <= BUDGET, (
                    target, name, t.value, len(t.value),
                )


def test_the_target_version_is_relocated_to_a_tooltip_and_not_dropped(
    monkeypatch,
):
    """AC3 — RELOCATED, NOT DROPPED. The same trade PS-229 made for the engine
    build identifier, and the reason this does not violate AC2: the tooltip
    carries the ORIGINAL string's own words, nothing new is written.

    Asserted in BOTH directions, because an implementation that simply deleted
    the version would satisfy the first half and lose the operator the answer
    to "which version am I being moved to?".
    """
    for name, panel in _download_states(monkeypatch):
        visible = " ".join((t.value or "") for t in _texts(panel))
        assert LONG_TARGET not in visible, (
            name, visible, "the target version is back in the visible line",
        )
        tips = " ".join(_tooltips(panel))
        assert f"updating to {LONG_TARGET}" in tips, (
            name, tips, "the target version was lost rather than relocated",
        )


def test_the_headline_still_names_the_gesture_and_the_progress(monkeypatch):
    """The shortening must not cost the operator WHAT IS HAPPENING or HOW FAR
    ALONG it is. Without this, a headline rendering "updating" alone would
    pass every budget assertion above while being the exact information loss
    the relocation exists to avoid."""
    states = dict(_download_states(monkeypatch))

    def headline(panel: ft.Control) -> str:
        return next(
            t.value for t in _texts(panel) if (t.value or "").startswith("updating")
        )

    assert headline(states["connecting"]).endswith("connecting\u2026"), states
    assert headline(states["percent"]).endswith("%"), states
    assert headline(states["bytes-only"]).endswith("MB"), states


def test_the_detail_line_reaches_the_panel_verbatim(monkeypatch):
    """AC2/out-of-scope, asserted rather than trusted: ``fmt_line``'s output is
    correct and shared with the engine panel, so this ticket changes how the
    rail LAYS IT OUT and never what it says.

    Read through the same ``.value`` walk every other spec here uses
    (``tests/test_app_ui.py::_walk_texts``), so a conversion that wrapped the
    line in anything without a ``.value`` would satisfy the boundedness
    assertions above while making the progress unreadable to the operator and
    invisible to every existing test.
    """
    import time

    app = _downloading_app(monkeypatch, done=PART, total=TOTAL)
    elapsed = max(time.monotonic() - app._update_start_t, 0.001)
    expected = pf.fmt_line(PART, TOTAL, elapsed)

    rendered = _walk_texts(app._build_version_panel())

    # The elapsed time advances by microseconds between the two calls, which
    # can move the last decimal of the speed — so the stable head is compared
    # exactly and the whole line is required to be present as ONE string.
    head = expected.split("   ")[0]
    matching = [v for v in rendered if v.startswith(head)]
    assert matching, (head, rendered, "the detail line never reached the panel")
    assert len(matching[0]) > BUDGET, (
        matching[0],
        "the detail line no longer exceeds the budget — this guard's premise "
        "is gone, so re-derive it rather than deleting the assertion",
    )


def test_the_detail_lines_truncated_tail_is_recoverable(monkeypatch):
    """THE SECOND HALF OF PS-229'S TREATMENT, applied to the one line here
    that really is ellipsised.

    Bounded is not enough on its own: 41 characters cut to ~22 with no way to
    read the rest trades a visible overflow for an invisible amputation. The
    status line next door answers that with a reveal chevron; a progress line
    that reflows on every tick is the wrong host for a click target, so it
    answers it the other way PS-229 sanctioned — verbatim in the tooltip.

    The UNKNOWN-TOTAL state is the negative control built into the same
    assertion: ``"5.2 MB   0.2 MB/s"`` is 17 characters and already fits, and
    a tooltip that merely repeated it would be harmless — what must not happen
    is an over-budget line with its tail nowhere.
    """
    for name, app, panel in _download_apps(monkeypatch):
        detail = [
            (t.value or "")
            for t in _cluster_texts(panel, _quiet_values(app))
            if not (t.value or "").startswith("updating")
        ]
        assert detail, (name, "no detail line was rendered")
        for line in detail:
            if len(line) <= BUDGET:
                continue
            assert line in _tooltips(panel), (
                name, line,
                "an over-budget detail line whose tail is unreachable",
            )


# --- the sites themselves --------------------------------------------------


def _text_calls(node) -> list[ast.Call]:
    """Every ``ft.Text(...)`` construction in an AST subtree."""
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == "Text"
    ]


def _method_ast(method) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(method)))


def _download_branch() -> ast.If:
    """The ``if self._app_update_status == "downloading":`` branch, out of the
    source. Scoped by its own condition, so a rename has to come here too —
    and the test below asserts the branch was FOUND rather than iterating an
    empty list."""
    found = [
        node
        for node in ast.walk(_method_ast(app_mod.App._build_version_panel))
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and node.test.left.attr == "_app_update_status"
        and any(
            isinstance(c, ast.Constant) and c.value == "downloading"
            for c in node.test.comparators
        )
    ]
    assert found, (
        "the download branch's conditional was not found — this guard is "
        "scoped by that condition, so a rename must come here too"
    )
    return found[0]


def test_the_download_branch_builds_no_bare_text(monkeypatch):
    """AC1 ON THE SOURCE, because it is a statement about how the controls are
    BUILT rather than about one state's output.

    The behavioural assertions above are the primary guard and this is the
    backstop for the case they cannot see: a bare ``ft.Text`` on a branch no
    fixture happens to reach still overflows the rail for the operator who
    reaches it.

    SCOPED TO THE DOWNLOAD BRANCH, deliberately. ``_build_version_panel``
    builds several other Texts this ticket does not own and must not silently
    re-govern — the version line and the auto-update toggle (a fixed-width
    bracket with its own ``no_wrap``). Widening this guard over them would
    make it fail for reasons that have nothing to do with the rail bound, and
    a guard that fails for the wrong reason gets weakened rather than obeyed.
    """
    calls = _text_calls(_download_branch())

    assert not calls, [ast.unparse(c)[:90] for c in calls]


def _label_expressions_in_the_download_branch() -> list[ast.AST]:
    """Every right-hand side assigned to ``label`` inside the branch.

    THIS IS THE ANTI-HARDCODING MECHANISM, and the reason it is an AST walk
    rather than a literal tuple: a FOURTH label state added tomorrow is picked
    up by this guard without anyone remembering to come here. A tuple is a
    narrow grep one level up — it finds precisely the states someone already
    thought of, which is how these two sites sat outside PS-229's bound for
    two tickets running.
    """
    return [
        node.value
        for node in ast.walk(_download_branch())
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "label" for t in node.targets
        )
    ]


def test_the_three_driven_label_states_are_all_the_source_can_produce():
    """The FIXTURE'S OWN COMPLETENESS, asserted against the source rather than
    assumed. Every assertion in this file measures panels built in three
    states; if the branch grew a fourth, they would all stay green while the
    new state overflowed unmeasured.

    A COUNT and a shape rather than an exact expression list: pinning the
    right-hand sides here would re-create by the back door the hardcoded tuple
    this file exists to replace, and would go red for a refactor that is not a
    defect. What it must catch is a state being ADDED.
    """
    labels = _label_expressions_in_the_download_branch()

    assert len(labels) == 3, (
        [ast.unparse(n) for n in labels],
        "the download branch's label states changed — drive the new one in "
        "_download_states before this guard can speak for it",
    )
