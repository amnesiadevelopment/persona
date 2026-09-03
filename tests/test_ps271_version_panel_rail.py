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
  3. The STATUS strings are bounded-and-revealable, NOT budgeted, and that
     asymmetry is deliberate — see
     ``test_every_status_the_panel_can_render_is_bounded``.
  4. It covers the rollback row and the rollback status line. The version line
     and the auto-update toggle are fixed-width strings this ticket did not
     touch, and the download-progress rows belong to a different surface.
  5. THE STATUS DISCOVERY WALK MODELS LITERALS AND CONDITIONAL EXPRESSIONS,
     NOT INTERPOLATION. ``_status_strings_assigned_in_the_source`` reads
     ``ast.Constant`` and ``ast.IfExp``; an f-string is an ``ast.JoinedStr``
     and is modelled as its literal skeleton with a HOSTILE placeholder
     substituted for each interpolation (see ``_literals``), which measures
     the shape but NOT the true runtime length — the real value is whatever
     the interpolated expression produces, and this file cannot know it. So an
     f-string status is caught as a *bounded* line but its *budget* is only
     probed, not proven. Today every one of the seven statuses is a literal,
     and because the status line is a single call site through
     ``sidebar_status_text`` (pinned by
     ``test_the_panel_status_line_builds_no_bare_text``) an interpolated status
     added later would still be bounded in practice. Stated here so the guard's
     claim is not stronger than its mechanism.
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

#: What an interpolated value in a status string is modelled AS. The AST walk
#: below cannot evaluate an f-string's expressions, so a JoinedStr is
#: reconstructed with this standing in for each of them — long enough that a
#: status which grew an interpolation is not silently modelled as a short one.
HOSTILE_INTERPOLATION = LONG_HELD


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


def _literals_of(node) -> list[str]:
    """The string value(s) an assignment's right-hand side can produce.

    MODULE-LEVEL rather than nested inside the walk below, so the mechanism
    itself is directly testable — see
    ``test_the_status_discovery_does_not_silently_skip_an_interpolated_status``.
    A guard whose discovery step can only be exercised through the real source
    can only be proven by mutating that source.

    An ``ast.JoinedStr`` (an f-string) is reconstructed as its literal skeleton
    with :data:`HOSTILE_INTERPOLATION` standing in for each interpolated
    expression. That models the SHAPE, not the runtime value — limitation 5.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literals_of(node.body) + _literals_of(node.orelse)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append(HOSTILE_INTERPOLATION)
        return ["".join(parts)]
    return []


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

    AN F-STRING IS AN ``ast.JoinedStr`` AND IS MODELLED, NOT SKIPPED — but it
    is modelled honestly, and the difference matters. Interpolation is exactly
    the unbounded-by-construction defect class this ticket removed from the
    HELD label (see ``_ROLLBACK_LABEL``'s comment), so a walk that returned
    ``[]`` for it would silently miss the one form the ticket names as
    dangerous. What is reconstructed is the literal skeleton with
    :data:`HOSTILE_INTERPOLATION` standing in for every substituted
    expression: that measures the SHAPE and proves the string reaches the
    bound, but it does NOT prove the budget — the real value is whatever the
    expression produces at runtime and this walk cannot know it. Recorded as
    limitation 5 in the module docstring rather than left implied.
    """
    source = Path(inspect.getsourcefile(app_mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found: list[str] = []

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
        found.extend(s for s in _literals_of(node.value) if s)

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


def test_the_status_discovery_does_not_silently_skip_an_interpolated_status():
    """THE FORM THIS TICKET NAMES AS THE DANGEROUS ONE. An f-string is an
    ``ast.JoinedStr``, and a walk handling only ``Constant``/``IfExp`` returns
    ``[]`` for it — SILENTLY. A guard advertised as catching what a hardcoded
    tuple misses, which then misses interpolation, has a claim stronger than
    its mechanism: interpolation is exactly the unbounded-by-construction
    defect removed from the HELD label (``_ROLLBACK_LABEL``'s comment is
    written about this class).

    Asserted against a synthetic module rather than by mutating the real one,
    so the guard is proven WITHOUT depending on a status that does not exist
    today. What is modelled is the SHAPE with a hostile placeholder, not the
    runtime value — see limitation 5 in this module's docstring.
    """
    module = ast.parse(
        'self._app_rollback_status = f"couldn\'t go back to {target} '
        '— see the log"'
    )
    assignment = module.body[0]

    found = _literals_of(assignment.value)

    assert found, "an interpolated status was silently skipped"
    assert HOSTILE_INTERPOLATION in found[0], found
    assert found[0].startswith("couldn't go back to "), found
    assert len(found[0]) > BUDGET, found


def test_every_status_the_panel_can_render_is_bounded(monkeypatch):
    """THE STATUS LINE IS BOUNDED-AND-REVEALABLE, NOT BUDGETED — and the
    asymmetry with the labels above is deliberate rather than an omission.

    A LABEL is a fixed phrase this product chooses, so it can be made to fit and
    is held to the 22-character budget. A STATUS is prose reporting an outcome
    ("can't go back while an update is pending" is 40 characters, and it must
    say what it says), and PS-229 treated the identically-long ENGINE statuses
    exactly this way — BOTH halves of it: bounded by ellipsis
    (``sidebar_status_text``) AND made recoverable in place
    (``_status_needs_reveal`` → ``_status_reveal_button`` → ``_status_control``
    re-rendering at ``expanded=True``). Shortening the copy would be a decision
    this ticket explicitly does not own; ellipsising it with no way to read the
    rest would not be PS-229's treatment at all, it would be half of it — and
    the half it drops is the actionable tail of a refusal whose only visible
    channel this line is.

    So this asserts the FIRST half, and
    ``test_every_over_budget_status_is_recoverable_in_full`` asserts the second.
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


# --- the truncated tail must be RECOVERABLE, not silently dropped ----------
#
# The bound alone converts an overflow into a silent truncation, and for the
# status line that is not a strict improvement: "couldn't go back — see the
# log" reaches the operator as roughly "couldn't go back — s…", and `see the
# log` is the entire actionable half. _on_app_rollback's own docstring is
# explicit that this line exists BECAUSE _log is not a visible surface. PS-229
# solved exactly this next door with a reveal; these assert the app panel got
# the same half, not only the first one.


def _reveal_buttons(control) -> list[ft.Control]:
    """Every clickable reveal chevron in a built tree.

    Identified by its ICON rather than by its click handler, because a
    Container's on_click is a lambda that says nothing about what it does — and
    the panel legitimately carries other clickable containers (the rollback
    row, the update button, the auto-update toggle).
    """
    found: list[ft.Control] = []

    def walk(node) -> None:
        icon = getattr(node, "content", None)
        if (
            getattr(node, "on_click", None) is not None
            and isinstance(icon, ft.Icon)
            and icon.icon in (ft.Icons.UNFOLD_MORE, ft.Icons.UNFOLD_LESS)
        ):
            found.append(node)
        for attr in ("content", "controls"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in child if isinstance(child, list) else [child]:
                walk(c)

    walk(control)
    return found


def test_every_over_budget_status_is_recoverable_in_full(monkeypatch):
    """THE SECOND HALF OF PS-229'S TREATMENT. Bounded is not enough on its own:
    a 40-character refusal ellipsised into ~22 characters with no way to read
    the rest has traded a visible overflow for an invisible amputation, of the
    one channel the refusal has.

    Asserted end to end rather than by the presence of a helper: the chevron is
    found in the BUILT panel, clicked through its real handler, and the panel
    is rebuilt — and the full string must then be readable off the tree by the
    same ``.value`` walk every other spec here uses.
    """
    over = [
        s for s in _status_strings_assigned_in_the_source() if len(s) > BUDGET
    ]
    assert over, "no status exceeds the budget — this guard is vacuous"

    for status in over:
        app = _panel_app(monkeypatch, status=status)

        buttons = _reveal_buttons(app._build_version_panel())
        assert len(buttons) == 1, (
            status,
            f"{len(buttons)} reveal controls — an over-budget status is "
            "truncated with no way to read the rest",
        )

        buttons[0].on_click(None)
        revealed = [
            t for t in _texts(app._build_version_panel())
            if (t.value or "") == status
        ]
        assert revealed, (status, "the revealed status left the panel")
        for t in revealed:
            assert t.no_wrap is False, (status, "revealed but still one line")
            assert t.max_lines == app_mod._STATUS_EXPANDED_MAX_LINES, (
                status, t.max_lines,
            )
            # THE REVEAL IS BOUNDED TOO. An unbounded one is the original
            # defect deferred by one click — see _STATUS_EXPANDED_MAX_LINES.
            assert t.overflow == ft.TextOverflow.ELLIPSIS, status
            assert t.expand is True, status


def test_a_status_that_already_fits_gets_no_reveal_control(monkeypatch):
    """THE NEGATIVE CONTROL, and a rule rather than tidiness: an affordance on
    a line that is already whole invites a click that visibly does nothing.
    ``"nothing to go back to"`` is 21 characters — the one panel status that
    fits — so it must render the text and NOT the chevron.

    This is also what makes the test above non-vacuous: without it, drawing a
    chevron unconditionally would satisfy it.
    """
    app = _panel_app(monkeypatch, status="nothing to go back to")

    assert _reveal_buttons(app._build_version_panel()) == []


def test_the_reveal_is_bound_to_the_message_it_was_opened_on(monkeypatch):
    """A REVEAL MUST NOT OUTLIVE ITS OWN SENTENCE. The operator opens
    "couldn't go back — see the log"; the next click refuses differently. If
    the flag were a bool, the new sentence would arrive already expanded — a
    panel silently open on a message nobody asked to expand, and a height
    change with no gesture behind it.

    So the flag holds the STRING it was opened on and is compared, not trusted.
    """
    app = _panel_app(monkeypatch, status="couldn't go back — see the log")
    _reveal_buttons(app._build_version_panel())[0].on_click(None)
    assert app._app_status_expanded() is True

    app._app_rollback_status = "can't go back while an update is pending"
    assert app._app_status_expanded() is False, (
        "the reveal survived onto a different message"
    )
    collapsed = [
        t for t in _texts(app._build_version_panel())
        if (t.value or "") == app._app_rollback_status
    ]
    assert collapsed and all(t.max_lines == 1 for t in collapsed)


def test_the_reveal_toggles_back_closed(monkeypatch):
    """Reversible, like the engine one. A reveal that cannot be re-collapsed
    leaves the panel permanently taller after a single click."""
    app = _panel_app(monkeypatch, status="couldn't go back — see the log")

    _reveal_buttons(app._build_version_panel())[0].on_click(None)
    assert app._app_status_expanded() is True

    _reveal_buttons(app._build_version_panel())[0].on_click(None)
    assert app._app_status_expanded() is False


def test_the_panel_reveal_survives_a_partially_constructed_app(monkeypatch):
    """``_build_version_panel`` is reachable from construction paths that never
    run ``__init__`` — every spec in this file builds the app through
    ``App.__new__(App)``. Reading the reveal flag off the attribute directly
    would raise ``AttributeError`` on all of them while working fine in the real
    app, which is the coupling ``_status_expanded`` records for the engine side.
    Asserted by deleting the attribute the constructor would have set."""
    app = _panel_app(monkeypatch, status="couldn't go back — see the log")
    app.__dict__.pop("_app_status_revealed", None)

    assert app._app_status_expanded() is False
    assert len(_reveal_buttons(app._build_version_panel())) == 1


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
