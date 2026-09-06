"""PS-331: an engine status reveal must not outlive the message it was opened on.

WHAT THIS ADDS, AND WHY IT IS A THIRD FILE. PS-229 gave the engine rows a
reveal and held it as a plain ``bool``. PS-271 built the app version panel's
copy of the same affordance and deliberately held it as the STRING it was
opened on, comparing rather than trusting — its
``test_the_reveal_is_bound_to_the_message_it_was_opened_on`` is the only place
that property was ever asserted, and it asserts it about the VERSION PANEL.
``git grep`` for that property across ``tests/`` before this file returned that
one test and nothing on the engine side, which is precisely why the divergence
survived a ticket that argued the rule out loud.

So this file asserts the SAME property about the ENGINE rows. It does not
redefine the rule; it holds the third arm of one mechanism to it.

THE DEFECT IT PINS, driven through the real builders before the fix:

    engine: after reveal        -> True
    engine: after new status    -> True    <-- status-independent

Nothing in ``src/ui/app.py`` ever cleared the bool: the flag was set at
``__init__`` and read by the accessor, and no refresh path, writer site or
download callback reset it — while ``engine_text.value`` is written from ~38
sites. The reachable operator sequence: a revert is refused, the status becomes
"couldn't go back — see the log", the operator reveals it to read the
actionable tail, and the startup pin check or the first-install lane overwrites
it. The new sentence arrives already open — and ``"downloading..."`` is 14
characters against a 17-character cell, so it not only renders at three lines
it never asked for, it also grows a chevron on a line that FITS. That is
exactly what ``test_a_status_that_already_fits_draws_no_reveal_control`` calls
"noise, and worse, it invites a click that visibly does nothing".

WHAT THIS GUARD DELIBERATELY DOES NOT MODEL, on the same terms the two sibling
files state:

  1. It does not render pixels. ``flet`` builds the control tree here; nothing
     lays it out. The evidence is the BUILDER'S RETURNED CONTROL TREE — what
     ``_build_engines_panel`` actually hands back — driven through the real
     methods. Never a substring of source, and never "a method was called".
     PS-229's own history is why that distinction is load-bearing: it records a
     case where the semantics tree reported a toggle as successful and only the
     pixels disagreed.
  2. It does not re-derive the character budgets. ``_VERSION_MAX_CHARS`` (17)
     is PS-229's and is asserted against, not re-argued.
"""

import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import flet as ft  # noqa: E402
import pytest  # noqa: E402

from src.ui import app as app_mod  # noqa: E402
from tests.test_app_ui import make_app  # noqa: E402


# A refusal long enough to be truncated in the ~110px version cell, and a
# SECOND status that FITS it. The second one is the point of the whole file:
# inheriting a reveal onto it is visible twice over — three lines where one was
# asked for, and a chevron on a whole line.
LONG_STATUS = "couldn't go back — see the log"
SHORT_STATUS = "downloading..."


def _texts(control) -> list[ft.Text]:
    """Every Text in a built tree, so a row can be inspected whole."""
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


def _reveal_buttons(control) -> list[ft.Control]:
    """Every clickable reveal chevron in a built tree.

    Identified by its ICON rather than by its click handler, for the reason
    ``test_ps271_version_panel_rail.py`` gives: a Container's ``on_click`` is a
    lambda that says nothing about what it does, and the engines panel
    legitimately carries other clickable containers (the header, both engine
    rows, both rollback rows).
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


def _engines_app(*, which: str, status: str):
    """An App whose engines panel is OPEN with one engine carrying `status`.

    Built through ``App.__new__(App)`` like every other panel spec in this
    tree — the panel must stay reachable from a partially constructed app.
    The OTHER engine is left empty so exactly one reveal can be in the tree,
    which is what makes the counts below unambiguous.
    """
    app = make_app(None)
    app._log = lambda *a, **k: None
    app._refresh_sidebar = lambda *a, **k: None
    app._engines_open = True
    app._engine_busy = False
    app._engine_checking = False
    app._engine2_busy = False
    app._engine2_checking = False
    # The panel's neighbours are not this file's subject: stub the predicates
    # and the two rollback rows so what is built is the two engine rows.
    app._engine_update_available = lambda: False
    app._engine2_update_available = lambda: False
    app._engine_rollback_row = lambda: ft.Container()
    app._engine2_rollback_row = lambda: ft.Container()
    app._engine_status = ""
    app._engine2_status = ""
    app._engine_status_revealed = ""
    app._engine2_status_revealed = ""
    app.engine_text = ft.Text(
        "", size=12, no_wrap=True, max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.RIGHT,
    )
    app._engine2_text = ft.Text(
        "", size=12, no_wrap=True, max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.RIGHT,
    )
    # `_build_engines_panel` reassigns the Firefox line from this hook every
    # rebuild, so the firefox arm's status has to be driven through it rather
    # than written once onto the control.
    app._engine2_status_text = lambda: app._engine2_status
    _write(app, which, status)
    return app


def _write(app, which: str, status: str) -> None:
    """Write a status the way the real writer sites do, for one engine.

    The Chromium line is written straight onto the long-lived control (that is
    what the download callback does); the Firefox line is recomputed from
    ``_engine2_status_text`` on every rebuild, so its source attribute is the
    one to set. Both are the value the OPERATOR then sees.
    """
    if which == "chromium":
        app.engine_text.value = status
        app._engine_status = status
    else:
        app._engine2_status = status
        app._engine2_text.value = status


def _status_lines(app, status: str) -> list[ft.Text]:
    """The rendered Texts carrying `status`, read out of a BUILT panel."""
    return [
        t for t in _texts(app._build_engines_panel())
        if (t.value or "") == status
    ]


BOTH_ENGINES = pytest.mark.parametrize("which", ["chromium", "firefox"])


# --- the property this ticket exists for -----------------------------------


@BOTH_ENGINES
def test_the_engine_reveal_is_bound_to_the_message_it_was_opened_on(which):
    """A REVEAL MUST NOT OUTLIVE ITS OWN SENTENCE — the engine arm of the rule
    ``test_the_reveal_is_bound_to_the_message_it_was_opened_on`` already holds
    the version panel to.

    The operator reveals "couldn't go back — see the log" to read the tail;
    one of the ~38 writer sites then puts a different sentence on the same
    line. That new sentence must arrive COLLAPSED. When the flag was a bool
    nothing ever cleared it, so it arrived open — a height change with no
    gesture behind it, on a message nobody asked to expand.

    ASSERTED ON WHAT THE BUILDER RETURNS, not on the flag alone: the flag
    read is the mechanism, the rendered bounds are the consequence.
    """
    app = _engines_app(which=which, status=LONG_STATUS)

    _reveal_buttons(app._build_engines_panel())[0].on_click(None)
    assert app._status_expanded(which) is True, "the reveal did not open"

    _write(app, which, SHORT_STATUS)

    assert app._status_expanded(which) is False, (
        "the reveal survived onto a different message"
    )
    rendered = _status_lines(app, SHORT_STATUS)
    assert rendered, "the new status left the panel"
    for t in rendered:
        assert t.max_lines == 1, (
            "the new sentence inherited the previous one's reveal and "
            f"rendered at {t.max_lines} lines"
        )
        assert t.text_align == ft.TextAlign.RIGHT, (
            "a collapsed status is a value at the end of a row and stays "
            "right-aligned; LEFT means it rendered as revealed prose"
        )


@BOTH_ENGINES
def test_an_inherited_reveal_would_draw_a_chevron_on_a_line_that_fits(which):
    """THE SECOND, SHARPER CONSEQUENCE, and the one that is a rule rather than
    a preference. ``"downloading..."`` is 14 characters against
    ``_VERSION_MAX_CHARS`` (17): it FITS, so collapsed it draws no chevron at
    all. An inherited reveal makes ``_status_needs_reveal`` return True
    regardless of length — which is the affordance
    ``test_a_status_that_already_fits_draws_no_reveal_control`` exists to
    forbid, in its own words "noise, and worse, it invites a click that
    visibly does nothing".

    The length premise is asserted rather than assumed, so this test cannot go
    quietly vacuous if the budget or the string ever moves.
    """
    assert len(SHORT_STATUS) < app_mod._VERSION_MAX_CHARS, (
        "this test's premise is that the second status FITS; it no longer does"
    )

    app = _engines_app(which=which, status=LONG_STATUS)
    assert len(_reveal_buttons(app._build_engines_panel())) == 1, (
        "the positive control: an over-budget status must offer the reveal, "
        "or everything below passes vacuously"
    )
    _reveal_buttons(app._build_engines_panel())[0].on_click(None)

    _write(app, which, SHORT_STATUS)

    assert _reveal_buttons(app._build_engines_panel()) == [], (
        "a status that fits grew a chevron, inherited from the previous "
        "message's reveal"
    )


# --- the mechanism stays reversible, and stays the app panel's ---------------


@BOTH_ENGINES
def test_the_engine_reveal_toggles_back_closed(which):
    """Reversible, like the panel one. A reveal that cannot be re-collapsed
    leaves the row permanently taller after a single click — and the chevron
    has to still be in the tree to make the second click reachable."""
    app = _engines_app(which=which, status=LONG_STATUS)

    _reveal_buttons(app._build_engines_panel())[0].on_click(None)
    assert app._status_expanded(which) is True

    buttons = _reveal_buttons(app._build_engines_panel())
    assert len(buttons) == 1, "the reveal became a one-way door"
    buttons[0].on_click(None)
    assert app._status_expanded(which) is False


@BOTH_ENGINES
def test_the_same_message_arriving_again_is_still_revealed(which):
    """THE NEGATIVE CONTROL for the binding, and it is what stops the fix
    being "collapse on every rebuild". The flag is COMPARED, not cleared: a
    rebuild that re-renders the SAME sentence must leave the reveal open, or
    the reveal would shut itself the moment anything refreshed the sidebar."""
    app = _engines_app(which=which, status=LONG_STATUS)
    _reveal_buttons(app._build_engines_panel())[0].on_click(None)

    _write(app, which, LONG_STATUS)

    assert app._status_expanded(which) is True, (
        "re-writing the same sentence collapsed the reveal"
    )
    rendered = _status_lines(app, LONG_STATUS)
    assert rendered and all(
        t.max_lines == app_mod._STATUS_EXPANDED_MAX_LINES for t in rendered
    ), [t.max_lines for t in rendered]


@BOTH_ENGINES
def test_the_engine_reveal_survives_a_partially_constructed_app(which):
    """``_build_engines_panel`` is reachable from construction paths that never
    run ``__init__`` — ``tests/test_engine_progress.py`` builds the app with
    ``App.__new__(App)`` and this file's own fixture does too. Reading the
    reveal flag off the attribute directly would raise ``AttributeError`` on
    all of them while working fine in the real app, which is the coupling
    ``_status_expanded``'s docstring records. Asserted by DELETING the
    attribute the constructor would have set, exactly as
    ``test_the_panel_reveal_survives_a_partially_constructed_app`` does."""
    app = _engines_app(which=which, status=LONG_STATUS)
    app.__dict__.pop(app._status_expanded_attr(which), None)

    assert app._status_expanded(which) is False
    assert len(_reveal_buttons(app._build_engines_panel())) == 1


def test_the_reveals_are_still_per_engine_not_one_shared_flag():
    """Revealing Firefox must not change the Chromium row's height underneath
    an operator reading it — the reason ``__init__`` holds two attributes and
    not one. Worth pinning here because the fix routes both arms through one
    accessor, which is exactly the shape that could collapse them into a
    single piece of state."""
    app = _engines_app(which="chromium", status=LONG_STATUS)
    _write(app, "firefox", "engine failed: signature check failed")

    assert len(_reveal_buttons(app._build_engines_panel())) == 2
    app._toggle_engine_status("firefox")

    assert app._status_expanded("firefox") is True
    assert app._status_expanded("chromium") is False, (
        "one engine's reveal opened the other engine's row"
    )


# --- one mechanism, three rows ---------------------------------------------


def test_all_three_reveal_chevrons_are_built_by_one_function():
    """THE UNIFICATION, asserted on behaviour rather than on the diff. The
    engine and app reveal buttons were byte-identical bodies (AST-compared)
    differing only in which toggle the click called. Two copies that agree
    today are the mechanism by which two panels in one rail come to recover
    differently tomorrow — which is the defect the rest of this file pins.

    Asserted as: the panel's own named gesture returns what the shared builder
    returns, for the same expanded state, and the shared builder takes the
    toggle as an argument rather than choosing it by string.
    """
    import inspect

    app = make_app(None)
    app._refresh_sidebar = lambda *a, **k: None

    params = list(
        inspect.signature(app_mod.App._status_reveal_button).parameters
    )
    assert params == ["self", "expanded", "on_toggle"], params

    for expanded in (False, True):
        shared = app._status_reveal_button(expanded, lambda: None)
        panel = app._app_status_reveal_button(expanded)
        assert panel.content.icon == shared.content.icon
        assert panel.tooltip == shared.tooltip
        assert (panel.width, panel.height) == (shared.width, shared.height)
        assert panel.border_radius == shared.border_radius
        assert panel.ink == shared.ink
        assert panel.alignment == shared.alignment


def test_the_shared_button_fires_the_toggle_it_was_handed():
    """The one thing the two copies differed on is now a parameter, so it is
    the one thing worth asserting is really wired: a chevron that renders
    perfectly and calls nothing is the failure this replaces two copies with
    one function to avoid."""
    app = make_app(None)
    fired: list[str] = []

    button = app._status_reveal_button(False, lambda: fired.append("clicked"))
    button.on_click(None)

    assert fired == ["clicked"]


# --- the comparand choice, exercised on the branch that discriminates -------


def test_the_reveal_is_bound_to_the_RENDERED_line_not_to_engine2_status():
    """THE COMPARAND CHOICE, ASSERTED WHERE IT ACTUALLY DIFFERS — and the only
    test in this file that would still pass if the fix had compared against
    ``_engine2_status`` instead of the rendered value.

    Every other test here writes a status and reads it back, so the rendered
    line and ``_engine2_status`` carry the SAME string and either comparand
    answers identically. That makes them all silent about the one design
    decision the ticket asked to be justified in the PR. This test drives the
    state where the two DISAGREE.

    ``_build_engines_panel`` does not render ``_engine2_status``; it recomputes
    the Firefox line every rebuild from :meth:`_engine2_status_text`, which has
    four branches — ``"checking..."``, ``_engine2_status``, a shortened latest
    version, or the current version. Only the second is that attribute. So a
    row can be displaying ``"checking..."`` while ``_engine2_status`` still
    holds the refusal the operator revealed a moment ago:

        rendered            'checking...'
        _engine2_status     "couldn't go back — see the log"   <-- unchanged

    A reveal compared against ``_engine2_status`` matches there and stays open,
    inheriting onto a line the operator never revealed — the same defect this
    file exists to close, in a smaller box. Compared against the RENDERED
    value it collapses, which is what the operator's eyes actually justify.

    ⚠️ This test deliberately DELETES the fixture's ``_engine2_status_text``
    stub and runs the real method. The stub is what makes every other test in
    this file readable, and it is also what would hide this branch entirely.

    The final step is the negative control: coming back off the ``checking``
    branch restores the very same sentence, and the reveal legitimately
    re-applies — the flag is COMPARED, not cleared, so returning to a message
    still carrying its own reveal is correct rather than a leak.
    """
    app = _engines_app(which="firefox", status=LONG_STATUS)
    # Unstub: exercise the shipped recompute, not the fixture's stand-in.
    del app._engine2_status_text
    app._engine2_latest = ""
    app._engine2_version = ""
    app._engine2_status = LONG_STATUS

    _reveal_buttons(app._build_engines_panel())[0].on_click(None)
    assert app._status_expanded("firefox") is True
    assert app._engine2_text.value == LONG_STATUS

    # Flip to the "checking..." branch WITHOUT touching _engine2_status.
    app._engine2_checking = True
    tree = app._build_engines_panel()

    assert app._engine2_text.value == "checking...", (
        "the premise: the rendered line must have left _engine2_status behind"
    )
    assert app._engine2_status == LONG_STATUS, (
        "the premise: _engine2_status must be UNCHANGED, or the two comparands "
        "do not disagree here and this test proves nothing"
    )
    assert app._engine2_status_revealed == app._engine2_status, (
        "the counterfactual: comparing against _engine2_status WOULD have "
        "matched here and inherited the reveal"
    )

    assert app._status_expanded("firefox") is False, (
        "the reveal inherited onto a line the operator never revealed — the "
        "comparand is following _engine2_status rather than the rendered value"
    )
    assert _reveal_buttons(tree) == [], (
        f"'checking...' is {len('checking...')} characters against a "
        f"{app_mod._VERSION_MAX_CHARS}-character cell: it fits, so it must "
        "draw no chevron"
    )

    # Negative control: the same sentence returns, and so does its reveal.
    app._engine2_checking = False
    app._build_engines_panel()
    assert app._engine2_text.value == LONG_STATUS
    assert app._status_expanded("firefox") is True, (
        "returning to the revealed message collapsed it — the flag is being "
        "cleared somewhere rather than compared"
    )
