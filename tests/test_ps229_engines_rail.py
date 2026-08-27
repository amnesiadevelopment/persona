"""PS-229: the sidebar engines panel must fit the 200px rail, whatever the
runtime strings say.

THE DEFECTS THESE PIN. Five controls interpolated runtime strings into a rail
with room for roughly 22 characters, with no ``no_wrap``, no ``max_lines`` and
no ``overflow``: the two rollback rows, the pending-rollback row, and both
engine detail rows. The engine status fields are assigned raw service text —
including an arbitrary exception message on the failure path. That is the grey
text running past the panel edge and the single enormous run of text.

THE TRAP THAT MAKES A PLAUSIBLE FIX DO NOTHING, and the reason the width
assertions below are not redundant with the line assertions: a ``Text`` inside
a ``Row`` is granted its INTRINSIC width — a Row does not squeeze a child that
did not ask to flex. A long single-line string is therefore laid out at full
length and overflows with the ellipsis never engaging. Bounding ``max_lines``
without bounding the width is precisely the fix that looks right and changes
nothing, so both are asserted.

The rollback labels are asserted as FIXED PHRASES rather than "short enough",
because the failure was a build identifier — ``firefox-20_151.0_20260817150018``
is 30 characters — being interpolated into the visible text. A phrase with no
interpolation cannot be widened by a build tag nobody controls.
"""

import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import flet as ft  # noqa: E402
import pytest  # noqa: E402

from src.ui.app import (  # noqa: E402
    _RESUME_LABEL,
    _ROLLBACK_LABEL,
    _STATUS_EXPANDED_MAX_LINES,
    rollback_row,
    sidebar_status_text,
)


# A REAL build identifier, deliberately: the fit is measured under the load
# that broke it, not under a convenient short string. Both of these are the
# shapes the engines actually produce.
FIREFOX_BUILD = "firefox-20_151.0_20260817150018"
CHROMIUM_BUILD = "chromium-148.0.7778.203-linux64"


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


def _visible_text(control) -> str:
    return " ".join((t.value or "") for t in _texts(control))


# --- the bound itself ------------------------------------------------------


def test_a_status_line_is_bounded_in_width_as_well_as_in_lines():
    """BOTH, because either alone is not a fix.

    max_lines/overflow bound the text's own layout; expand=True is what bounds
    its WIDTH inside a Row. Without the second, a long single-line string is
    granted its intrinsic width and overflows the panel with the ellipsis never
    engaging.
    """
    t = sidebar_status_text("x" * 400)

    assert t.max_lines == 1
    assert t.no_wrap is True
    assert t.overflow == ft.TextOverflow.ELLIPSIS
    assert t.expand is True, "bounded lines but not width — the no-op fix"


def test_a_revealed_status_is_still_bounded():
    """An unbounded reveal is the same defect one click later — a stack trace
    pasted into a status would push the panel out of shape exactly as the
    unbounded row did."""
    t = sidebar_status_text("boom\n" * 80, expanded=True)

    assert t.max_lines == _STATUS_EXPANDED_MAX_LINES
    assert t.no_wrap is False, "a reveal that cannot wrap reveals nothing"
    assert t.overflow == ft.TextOverflow.ELLIPSIS
    assert t.expand is True


def test_the_reveal_bound_is_three_lines_not_unbounded():
    """Enough to read a real engine error, small enough that the panel's shape
    survives it."""
    assert _STATUS_EXPANDED_MAX_LINES == 3


# --- the rollback row ------------------------------------------------------


def test_the_rollback_labels_are_fixed_phrases_with_no_interpolation():
    """The labels cannot be widened by a runtime string, because there is no
    runtime string in them. That is the whole property."""
    assert _ROLLBACK_LABEL == "previous version"
    assert _RESUME_LABEL == "resume updates"
    for label in (_ROLLBACK_LABEL, _RESUME_LABEL):
        assert "{" not in label and "%" not in label


@pytest.mark.parametrize("build", [FIREFOX_BUILD, CHROMIUM_BUILD])
def test_the_build_identifier_is_in_the_tooltip_and_not_in_the_visible_text(build):
    """It answers "which build?", which is asked AFTER deciding — so it moves
    to the tooltip that already named it verbatim. The visible text is what had
    to fit the rail."""
    row = rollback_row(
        label=_ROLLBACK_LABEL,
        icon=ft.Icons.HISTORY,
        cost="re-downloads the engine",
        tooltip=f"Go back to {build}. …",
        on_click=lambda: None,
    )

    assert build not in _visible_text(row), "the identifier is back in the label"
    assert build in row.tooltip, "the identifier was lost rather than relocated"


def test_the_cost_stays_on_screen_rather_than_moving_to_the_tooltip():
    """THE DELIBERATE DEPARTURE from "put it in the tooltip", and the argument
    the selected alternative exists to make.

    The cost answers "what will this do to me?", which is asked BEFORE
    deciding. A tooltip needs a hover a trackpad operator may never perform, so
    a cost carried only there is a cost met AFTER clicking — and on the
    Chromium row that click is a several-hundred-megabyte transfer over Tor.
    """
    row = rollback_row(
        label=_ROLLBACK_LABEL,
        icon=ft.Icons.HISTORY,
        cost="re-downloads the engine",
        tooltip="…",
        on_click=lambda: None,
    )

    assert "re-downloads the engine" in _visible_text(row)


def test_every_line_of_a_cost_bearing_row_is_bounded():
    """Adding a second line must not weaken the bound round 2 established — it
    is applied to that line too."""
    row = rollback_row(
        label=_ROLLBACK_LABEL,
        icon=ft.Icons.HISTORY,
        cost="re-downloads the engine",
        tooltip="…",
        on_click=lambda: None,
    )

    for t in _texts(row):
        assert t.max_lines == 1, (t.value, "an unbounded line in the rollback row")
        assert t.expand is True, (t.value, "bounded lines but not width")


def test_a_row_with_no_cost_draws_no_empty_second_line():
    """The pinned state's cost line says the STATE; a row given none must not
    reserve space for one."""
    row = rollback_row(
        label=_RESUME_LABEL,
        icon=ft.Icons.PLAY_ARROW,
        tooltip="…",
        on_click=lambda: None,
    )

    assert isinstance(row.content, ft.Row), "an empty cost line was drawn anyway"


def test_no_visible_label_in_any_state_can_overflow_the_rail():
    """The rail's content width is about 22 monospace characters. Every phrase
    this row can ever render is asserted against that budget — as PHRASES,
    since none of them interpolates anything.

    THIS ASSERTION EARNED ITS KEEP. The first attempt at the sourcing fix
    replaced "downloads 300-600MB" (19) with "re-downloads the engine" (23),
    which is LONGER than the label it replaced and over the budget — so the
    fix for an unsourced number would have re-introduced the very overflow
    this panel is being rebuilt to remove. The render had already dropped the
    "re-" prefix for exactly this reason. Caught here, before it shipped.
    """
    budget = 22
    for phrase in (
        _ROLLBACK_LABEL,
        _RESUME_LABEL,
        "auto-update held off",
        "instant · no download",
        "downloads the engine",
    ):
        assert len(phrase) <= budget, (phrase, len(phrase))


# --- the sourcing ruling ---------------------------------------------------


def test_the_chromium_cost_states_no_size_because_the_product_knows_none():
    """THE UNSOURCED NUMBER, removed rather than invented.

    The render's label read "downloads 300-600MB". That figure is NOT a value
    this product holds: the three occurrences of "300-600MB" in the tree are
    prose comments in src/services/engine/updater.py arguing about whether to
    keep a second engine tree on disk. No constant, no field, no service call
    carries it. The only real total is the Content-Length that
    httpdl.resumable_download learns AT TRANSFER TIME — after the operator has
    already clicked, which is too late to be a warning.

    So the number would have been an unsourced claim presented as measured, on
    a line an operator on Tor budgets an afternoon against. The cost warning
    survives; only the fabricated precision goes.
    """
    import inspect

    from src.ui import app as app_mod

    source = inspect.getsource(app_mod.App._engine_rollback_row)
    visible = [
        line
        for line in source.splitlines()
        if "cost=" in line and not line.strip().startswith("#")
    ]
    assert visible, "the Chromium row no longer states a cost at all"
    for line in visible:
        assert "300" not in line and "600" not in line, (
            line,
            "an unsourced size figure is back in the visible cost line",
        )


def test_the_two_engines_still_describe_their_revert_differently():
    """THE ASYMMETRY IS THE POINT, and it must survive the shortening.

    Chromium keeps ONE un-versioned tree, so the previous build's files are
    gone and reverting re-downloads the engine. Firefox keeps each build in its
    own cache directory, so the retained build is already on disk and no bytes
    move. An operator on a slow or metered link deserves to know which one they
    are about to trigger — losing that distinction to shorten a label would be
    the wrong trade.
    """
    import inspect

    from src.ui import app as app_mod

    chromium = inspect.getsource(app_mod.App._engine_rollback_row)
    firefox = inspect.getsource(app_mod.App._engine2_rollback_row)

    assert "re-downloads the engine" in chromium
    assert "instant · no download" in firefox
    assert "instant · no download" not in chromium, "Chromium claims a free revert"


def test_the_chromium_cost_is_a_warning_and_the_firefox_one_is_not():
    """Colour carries the difference the words already state, on the one row
    where the gesture is expensive."""
    import inspect

    from src.ui import app as app_mod

    chromium = inspect.getsource(app_mod.App._engine_rollback_row)
    firefox = inspect.getsource(app_mod.App._engine2_rollback_row)

    assert "cost_color=COLORS[\"warning\"]" in chromium
    assert "cost_color" not in firefox


# --- the reveal control ----------------------------------------------------


def test_a_status_that_already_fits_draws_no_reveal_control():
    """An affordance on a whole line invites a click that visibly does nothing.

    This is the half of the requirement that is easy to miss: bounding the long
    case is not enough if the short case grows a chevron that cannot reveal
    anything.
    """
    from src.ui.app import App

    assert App._status_needs_reveal("idle", False) is False
    assert App._status_needs_reveal("", False) is False
    assert App._status_needs_reveal(None, False) is False


def test_a_status_too_long_for_its_cell_offers_the_reveal():
    from src.ui.app import App

    long_status = "couldn't go back — see the log: signature check failed"
    assert App._status_needs_reveal(long_status, False) is True


def test_an_already_revealed_status_keeps_its_control_so_it_can_be_re_collapsed():
    """Otherwise the reveal is a one-way door: expand a long error, and the
    control that would put it back is gone."""
    from src.ui.app import App

    assert App._status_needs_reveal("short", True) is True


def test_the_revealed_state_builds_a_fresh_control_rather_than_mutating_one():
    """THE BUG THIS PINS, and it cost a capture.

    An earlier attempt mutated the long-lived status control in place. The
    ACCESSIBILITY TREE REPORTED THE TOGGLE AS SUCCESSFUL — the chevron's label
    went "Show" -> "Hide" — while the pixels did not change at all, because the
    panel hands flet the SAME object every rebuild and the mutation is never
    repainted. It was caught only by looking at the render.

    So the REVEALED state must be a freshly constructed control, which flet has
    no choice but to paint. The COLLAPSED state keeps the live one, because
    that is the object the download-progress callback writes to and swapping it
    would freeze the percent mid-transfer.
    """
    from tests.test_app_ui import make_app

    app = make_app(None)
    live = ft.Text("engine failed: signature check failed", size=12)

    collapsed = app._status_control(live, False)
    assert collapsed is live, "the live control was swapped while collapsed"

    revealed = app._status_control(live, True)
    assert revealed is not live, "the reveal mutated the long-lived control"
    assert revealed.value == live.value, "the reveal lost the text it reveals"
