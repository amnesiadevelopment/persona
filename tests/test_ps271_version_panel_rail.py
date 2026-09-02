"""PS-271: the APP VERSION panel must fit the same 200px rail the engines do.

WHAT THIS ADDS TO PS-229, AND WHY IT IS A SEPARATE FILE. PS-229 established a
width + lines bound for the sidebar rail, shipped ``sidebar_status_text`` and
``rollback_row`` to carry it, and converted the three ENGINE rows. The APP
version panel sits in the SAME 200px column, directly beneath the engines panel
(``src/ui/components/sidebar.py:118`` is ``width=200``; ``:153`` places
``version_panel`` in it), and was never opened by that commit — established by
diff, not assumed: ``git show 087d7bd -- src/ui/app.py`` has no hunk covering
``_app_rollback_row``'s pre-image lines. Nothing in the tree records an argument
for the version panel being allowed to overflow, so this reads as scope by
omission, not as an exemption.

``tests/test_ps229_engines_rail.py`` is left UNMODIFIED. This widens the bound
to a second panel; it does not redefine it. The two files assert the same
property about different surfaces.

THE STRUCTURAL POINT — WHY THIS GUARD ITERATES. PS-229's budget assertion runs
over a HARDCODED five-phrase tuple rather than over anything the panel renders,
so a sixth phrase can be added tomorrow and the guard stays green. That is the
real refactor target, and it is why nothing below is a literal list of strings:

  * the LABELS are read out of a row that is actually BUILT, in each of the
    three states it can be in, under a hostile runtime value;
  * the STATUSES are DISCOVERED by walking the assignments to
    ``_app_rollback_status`` in the source, so a status added later is picked
    up by this guard without anyone remembering to add it here.

WHAT THIS GUARD DELIBERATELY DOES NOT MODEL, stated rather than left implied
(following ``tests/test_encoding_discipline.py``'s precedent):

  1. It does not render pixels. ``flet`` builds the control tree here; nothing
     lays it out. The bound is asserted as the PARAMETER SET PS-229 established
     — ``expand`` + ``no_wrap`` + ``max_lines`` + ``overflow`` — which is the
     same evidence the engine-side guard rests on. It is not a screenshot.
  2. It does not re-derive the 22-character budget. That number is PS-229's,
     adopted here so the two panels in one rail are held to ONE bound rather
     than to two. (``_VERSION_MAX_CHARS = 17`` is a different, narrower budget
     for the ~110px engine version cell — do not conflate them.)
  3. The STATUS strings are bounded, NOT budgeted, and that asymmetry is
     deliberate — see ``test_every_status_the_panel_can_render_is_bounded``.
  4. It covers the rollback row and the rollback status line. The version line
     and the auto-update toggle are fixed-width strings this ticket did not
     touch, and the download-progress rows belong to a different surface.
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
from src.ui.app import _RESUME_LABEL, _ROLLBACK_LABEL  # noqa: E402
from tests.test_app_ui import _walk_texts, make_app  # noqa: E402


#: PS-229's number, adopted rather than re-derived: the rail's content width is
#: about 22 monospace characters. Both panels in the rail are held to it.
BUDGET = 22

#: A hostile runtime value, deliberately — the fit is measured under the load
#: that breaks it. A real persona pre-release tag, longer than the "3.0.2" the
#: hold tests happen to use.
LONG_HELD = "3.0.10-beta.1"


def _texts(control) -> list[ft.Text]:
    """Every Text in a control tree, so a row can be inspected whole."""
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


def _row_app(monkeypatch, *, held, retained):
    """An App stub whose rollback row is in a chosen one of its three states."""
    monkeypatch.setattr(app_mod.app_update, "held_version", lambda: held)
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: retained)
    app = make_app(None)
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_rollback_status = ""
    return app


def _panel_app(monkeypatch, *, status):
    """An App stub whose version panel carries `status` on its status line."""
    monkeypatch.setattr(app_mod.app_update, "held_version", lambda: "")
    monkeypatch.setattr(app_mod.app_update, "rollback_target", lambda: "")
    monkeypatch.setattr(
        app_mod.app_settings, "is_auto_update_enabled", lambda: False
    )
    app = make_app(None)
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._app_latest = ""
    app._app_update_status = ""
    app._update_staged = ""
    app._app_rollback_status = status
    return app


# --- the states the row can be in, enumerated by BUILDING it ---------------
#
# Not a list of phrases: the row is constructed in each state and whatever it
# renders is what gets measured. A label that grows a runtime value back is
# caught because the value is really interpolated, not because someone
# remembered to add its length to a tuple.


def _row_states(monkeypatch):
    """(name, built row) for every state _app_rollback_row can render."""
    return [
        (
            "held",
            _row_app(
                monkeypatch, held=LONG_HELD, retained="/Applications/p.app.bak"
            )._app_rollback_row(),
        ),
        (
            "retained",
            _row_app(
                monkeypatch, held="", retained="/Applications/p.app.bak"
            )._app_rollback_row(),
        ),
        (
            "nothing",
            _row_app(monkeypatch, held="", retained="")._app_rollback_row(),
        ),
    ]


def test_the_three_row_states_are_all_reachable_from_this_fixture(monkeypatch):
    """The positive control for the two tests below. Without it an
    always-None row — or a fixture that silently only ever produced one state —
    would satisfy a budget assertion vacuously, which is the exact shape PS-11
    keeps catching in this repo."""
    rendered = 0
    for name, row in _row_states(monkeypatch):
        if name == "nothing":
            assert row is None, "a row with nothing retained must render nothing"
        else:
            assert row is not None, f"the {name} state rendered nothing at all"
            assert _texts(row), f"the {name} state rendered no text at all"
            rendered += 1
    assert rendered == 2, rendered


def test_no_visible_label_the_version_panel_row_renders_can_overflow_the_rail(
    monkeypatch,
):
    """THE BUDGET, over what the row ACTUALLY RENDERS rather than over a list.

    Both over-budget labels this ticket exists to remove are caught here:
    ``"go back to the previous version"`` is 31 characters, and
    ``f"resume updates (held {held})"`` is 27 with a short tag and 35 with the
    pre-release one above — an interpolated identifier in a visible label is
    unbounded by construction, which is the property the fixed phrases have and
    it did not.
    """
    for name, row in _row_states(monkeypatch):
        if row is None:
            continue
        for t in _texts(row):
            value = t.value or ""
            assert len(value) <= BUDGET, (name, value, len(value))


def test_every_line_of_the_version_panel_row_is_bounded_in_width_and_lines(
    monkeypatch,
):
    """BOTH, because either alone is not a fix. ``max_lines``/``overflow``
    bound the text's own layout; ``expand=True`` is what bounds its WIDTH
    inside a Row — a Text that did not ask to flex is granted its intrinsic
    width and overflows with the ellipsis never engaging. Bounding lines
    without bounding width is precisely the fix that looks right and changes
    nothing on screen."""
    for name, row in _row_states(monkeypatch):
        if row is None:
            continue
        for t in _texts(row):
            assert t.max_lines == 1, (name, t.value, "an unbounded line")
            assert t.no_wrap is True, (name, t.value, "an unbounded line")
            assert t.overflow == ft.TextOverflow.ELLIPSIS, (name, t.value)
            assert t.expand is True, (
                name, t.value, "bounded lines but not width — the no-op fix",
            )


def test_the_held_version_is_in_the_tooltip_and_not_in_the_visible_label(
    monkeypatch,
):
    """RELOCATED, NOT DROPPED — the same trade PS-229 made for the engine build
    identifier. The held build answers "which version is being held?", which an
    operator asks after reading the row, so the tooltip that already named it
    verbatim is the right home. What the LABEL has to carry is which gesture
    this is, and that is a fixed phrase.

    Asserted in BOTH directions: an implementation that simply deleted the
    version would satisfy the first half and lose the operator the answer.
    """
    row = _row_app(
        monkeypatch, held=LONG_HELD, retained="/Applications/p.app.bak"
    )._app_rollback_row()

    visible = " ".join((t.value or "") for t in _texts(row))
    assert LONG_HELD not in visible, "the held version is back in the label"
    assert LONG_HELD in (row.tooltip or ""), (
        "the held version was lost rather than relocated"
    )


def test_the_row_still_names_the_gesture_in_each_state(monkeypatch):
    """The shortening must not cost the operator WHICH gesture they are being
    offered. Without this, a row rendering an empty string would pass every
    budget assertion above."""
    states = dict(_row_states(monkeypatch))
    held_text = " ".join((t.value or "") for t in _texts(states["held"]))
    retained_text = " ".join((t.value or "") for t in _texts(states["retained"]))

    assert _RESUME_LABEL in held_text, held_text
    assert _ROLLBACK_LABEL in retained_text, retained_text


# --- the statuses, DISCOVERED rather than listed ---------------------------


def _status_strings_assigned_in_the_source() -> list[str]:
    """Every string literal assigned to ``self._app_rollback_status`` in
    ``src/ui/app.py``, read out of the AST.

    THIS IS THE ANTI-HARDCODING MECHANISM, and the reason it is an AST walk
    rather than a literal tuple: a status added tomorrow is picked up by this
    guard without anyone remembering to come here. A tuple is a narrow grep one
    level up — it finds precisely the strings someone already thought of, which
    is how the version panel's seven strings sat outside PS-229's five-phrase
    budget for a week.

    It is a walk rather than a regex because the assignment at the "which
    refusal was it?" site is a conditional expression spanning four lines, with
    the two strings in its branches — the member a hand-written table drops.
    Empty clears are skipped: erasing the line is not rendering a string.
    """
    source = Path(inspect.getsourcefile(app_mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found: list[str] = []

    def _literals(node) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):
            return _literals(node.body) + _literals(node.orelse)
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [
            t
            for t in node.targets
            if isinstance(t, ast.Attribute) and t.attr == "_app_rollback_status"
        ]
        if not targets:
            continue
        found.extend(s for s in _literals(node.value) if s)

    return sorted(set(found))


def test_the_status_discovery_actually_finds_the_statuses():
    """The positive control for the discovery walk itself. An AST query that
    silently matched nothing would make every assertion below vacuous — a guard
    iterating an empty list is greener than one iterating a hardcoded tuple and
    worth considerably less.

    The floor is deliberately a COUNT and a sample rather than an exact list:
    pinning the full set here would re-create by the back door the hardcoded
    tuple this file exists to replace, and would go red for a status being
    ADDED, which is not a defect.
    """
    found = _status_strings_assigned_in_the_source()

    assert len(found) >= 6, found
    # the conditional-expression site's SECOND branch — the member a
    # hand-written table drops, and the one string here that already fits
    assert "nothing to go back to" in found, found
    # the longest one, and the reason the bound is needed at all
    assert "can't go back while an update is pending" in found, found


def test_every_status_the_panel_can_render_is_bounded(monkeypatch):
    """THE STATUS LINE IS BOUNDED, NOT BUDGETED — and the asymmetry with the
    labels above is deliberate rather than an omission.

    A LABEL is a fixed phrase this product chooses, so it can be made to fit and
    is held to the 22-character budget. A STATUS is prose reporting an outcome
    ("can't go back while an update is pending" is 40 characters, and it must
    say what it says), and PS-229 treated the identically-long ENGINE statuses
    exactly this way: bounded by ellipsis, not shortened. Truncating an
    operator-facing explanation to fit a rail would be a copy decision this
    ticket explicitly does not own.

    So what is asserted here is that every status the panel can render goes
    through the bound — which is what makes a 40-character string safe in a
    22-character rail, and what a bare ft.Text does not do.
    """
    statuses = _status_strings_assigned_in_the_source()

    for status in statuses:
        app = _panel_app(monkeypatch, status=status)
        panel = app._build_version_panel()

        rendered = [t for t in _texts(panel) if (t.value or "") == status]
        assert rendered, (
            status,
            "a status the code can assign never reaches the panel",
        )
        for t in rendered:
            assert t.max_lines == 1, (status, "an unbounded status line")
            assert t.no_wrap is True, (status, "an unbounded status line")
            assert t.overflow == ft.TextOverflow.ELLIPSIS, status
            assert t.expand is True, (
                status,
                "bounded lines but not width — the no-op fix",
            )


def test_a_status_still_reaches_the_panel_as_readable_text(monkeypatch):
    """The bound must not cost the status its DISCOVERABILITY. The panel's own
    specs read ``.value`` off the rendered tree
    (``tests/test_app_ui.py::_walk_texts``), so a conversion that wrapped the
    status in anything without a ``.value`` would satisfy the boundedness
    assertions above while making the refusal unreadable to the operator and
    invisible to every existing test."""
    app = _panel_app(monkeypatch, status="couldn't go back — see the log")

    assert "couldn't go back — see the log" in _walk_texts(
        app._build_version_panel()
    )


def test_a_quiet_panel_still_renders_no_status_line(monkeypatch):
    """The negative control: the status line stays CONDITIONAL on there being a
    status. Without this, a conversion that rendered the helper unconditionally
    would put an empty bounded line in every panel and pass everything above."""
    app = _panel_app(monkeypatch, status="")

    assert "" not in _walk_texts(app._build_version_panel())


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


def test_the_rollback_row_builds_no_text_of_its_own_at_all(monkeypatch):
    """AC1 for the ROW, asserted on the SOURCE, because it is a statement about
    how the controls are built rather than about one state's output.

    The behavioural assertions above are the primary guard and this is the
    backstop for the case they cannot see: a bare ``ft.Text`` on a branch no
    fixture happens to reach still overflows the rail for the operator who
    reaches it. The row now renders EXCLUSIVELY through ``rollback_row``, so
    the honest assertion is not "every Text here is bounded" but "there is no
    Text here" — the bound lives in one place and this row does not get to
    have a second opinion about it.
    """
    calls = _text_calls(_method_ast(app_mod.App._app_rollback_row))

    assert not calls, [ast.unparse(c)[:90] for c in calls]


def test_the_panel_status_line_builds_no_bare_text(monkeypatch):
    """AC1 for the STATUS LINE, scoped to the ``if self._app_rollback_status:``
    branch rather than to the whole method — deliberately, and the scope is the
    point rather than a shortcut.

    ``_build_version_panel`` builds several other Texts this ticket does not
    own and must not silently re-govern: the version line, the auto-update
    toggle (a fixed-width bracket string with its own ``no_wrap``), and the
    download-progress lines. Widening this guard over them would make it fail
    for reasons that have nothing to do with the rail bound, and a guard that
    fails for the wrong reason gets weakened rather than obeyed.
    """
    branches = [
        node
        for node in ast.walk(_method_ast(app_mod.App._build_version_panel))
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "_app_rollback_status"
    ]
    assert branches, (
        "the status line's conditional was not found — this guard is scoped by "
        "that condition, so a rename must come here too"
    )

    for branch in branches:
        for call in _text_calls(branch):
            kwargs = {k.arg for k in call.keywords}
            assert "expand" in kwargs, (
                ast.unparse(call)[:90],
                "a bare ft.Text on the status line — route it through "
                "sidebar_status_text",
            )
