"""PS-266 — a long Activity Log line can be READ and COPIED in the fullscreen view.

WHAT THESE TESTS ARE, AND WHAT THEY ARE NOT. They pin the STRUCTURE of the
fullscreen row: that a revealed message is bounded in lines AND in width, that
the affordance appears only on a line that is actually cut, that the cell is
selectable, and that the dock's own row is untouched. They are NOT coverage of
AC1 — "the operator can read the end of the sentence" is a claim about a
rendered surface, and an assertion that ``max_lines == 5`` passes just as
happily against an implementation nobody can use. That claim is driven live in
``tests/ui_driver/live_ps266.py``; this file exists so a future refactor that
silently drops ``expand`` or ``selectable`` fails in the fast suite.

The `expand`/`no_wrap` pair in particular is the trap PS-229 recorded and
``tests/test_ps229_engines_rail.py:74-104`` pins for the engines rail: bounding
the lines without bounding the WIDTH is the fix that looks right and changes
nothing.
"""

from __future__ import annotations

import flet as ft

from src.ui.dialogs.log import (
    _FALLBACK_WINDOW_WIDTH,
    _MESSAGE_EXPANDED_MAX_LINES,
    _HIDE_TIP,
    _REVEAL_TIP,
    fullscreen_event_row,
    fullscreen_message_text,
    message_char_budget,
    message_needs_reveal,
    page_width,
)
from src.ui.log_console import ROW_HEIGHT, event_row

ROSTER = frozenset({"shop-de-03", "mail-us-011", "shop-us-01"})

#: The real worst-case refusal, as it LANDS IN THE LOG — composed by
#: ``src/services/browser/process.py:233-241`` and carried through
#: ``launcher.py`` with the "Error starting process: " prefix. 460 characters.
#: Built here from the same string the product composes rather than pasted, so
#: a reworded refusal changes this fixture instead of silently outdating it.
_REFUSAL = (
    "Error starting process: "
    "Profile 'shop-de-03' has proxy 'de-residential-01' assigned and its "
    "exit country is known (DE), "
    "but no timezone is known for that country and its last check recorded "
    "none. Refusing to launch: falling back to UTC would declare a clock "
    "that contradicts the exit's own country — the 'spoofed location' tell "
    "this product exists to avoid. Re-checking will NOT help; add a row for "
    "that country to _COUNTRY_TZ (launch_policy.py) to resolve it."
)

#: The payload the operator currently cannot reach. It is at the END of the
#: sentence, which is the whole reason a one-line ellipsis destroys it.
_TAIL = "add a row for that country to _COUNTRY_TZ (launch_policy.py)"

_LONG_LINE = f"10:00:07  > {_REFUSAL}"
_SHORT_LINE = "10:00:01  > Launching shop-de-03"


def _row_parts(row):
    return row.content.controls


def _message_text(row):
    """The message cell's ``Text`` — found by the one property that identifies
    it, ``expand=True``, rather than by position (the reveal control changes
    the row's arity)."""
    for c in _row_parts(row):
        inner = getattr(c, "content", None)
        if isinstance(inner, ft.Text) and inner.expand:
            return inner
    raise AssertionError("no expanding message cell in the row")


def _reveal(row):
    for c in _row_parts(row):
        inner = getattr(c, "content", None)
        if getattr(inner, "tooltip", None) in (_REVEAL_TIP, _HIDE_TIP):
            return inner
    return None


# --- AC1: the reveal is real, and it is BOUNDED ---------------------------


def test_ac1_the_460_char_refusal_needs_more_than_one_line_at_the_app_minimum():
    """The premise, asserted rather than assumed: this string really is cut.

    If a future reword makes the worst refusal fit on one line, this test fails
    and the fixture — not the bound — is what is stale.
    """
    assert len(_REFUSAL) == 460
    assert _TAIL in _REFUSAL
    assert _REFUSAL.rstrip().endswith("to resolve it.")
    # At the app minimum (1024px) the budget is ~105 chars, so 460 characters
    # is 5 lines. That is the arithmetic _MESSAGE_EXPANDED_MAX_LINES is set to.
    budget = message_char_budget(1024)
    assert budget < len(_REFUSAL)
    assert -(-len(_REFUSAL) // budget) == _MESSAGE_EXPANDED_MAX_LINES


def test_ac1_the_reveal_bound_is_five_lines_not_unbounded():
    """Five clears the worst REAL refusal at 1024px; unbounded would be the
    original defect deferred by one click."""
    assert _MESSAGE_EXPANDED_MAX_LINES == 5


def test_ac1_a_revealed_message_wraps_and_is_still_bounded_in_lines_and_width():
    t = fullscreen_message_text(_REFUSAL, "#fff", expanded=True)

    assert t.max_lines == _MESSAGE_EXPANDED_MAX_LINES
    assert t.no_wrap is False, "a reveal that cannot wrap reveals nothing"
    assert t.overflow == ft.TextOverflow.ELLIPSIS
    assert t.expand is True, "bounded lines but not width — the no-op fix"


def test_ac1_a_collapsed_message_is_the_same_single_line_cell_as_before():
    t = fullscreen_message_text(_REFUSAL, "#fff", expanded=False)

    assert t.max_lines == 1
    assert t.no_wrap is True
    assert t.overflow == ft.TextOverflow.ELLIPSIS
    assert t.expand is True


def test_ac1_a_revealed_row_is_allowed_to_be_taller_than_one_dock_row():
    """The bound lives in the TEXT; the row must not re-impose ROW_HEIGHT on
    top of it, or five permitted lines are clipped back to one by the box."""
    collapsed = fullscreen_event_row(_LONG_LINE, ROSTER, window_width=1024)
    revealed = fullscreen_event_row(
        _LONG_LINE, ROSTER, expanded=True, window_width=1024
    )

    assert collapsed.height == ROW_HEIGHT
    assert revealed.height is None


def test_ac1_a_revealed_row_carries_the_whole_sentence_including_its_tail():
    """The payload is at the END. A reveal that carried a truncated VALUE would
    satisfy every layout assertion above and still not show the remedy."""
    t = _message_text(
        fullscreen_event_row(_LONG_LINE, ROSTER, expanded=True, window_width=1024)
    )
    assert _TAIL in t.value


# --- AC2: the affordance only on a line that is actually cut ---------------


def test_ac2_a_short_line_gets_no_reveal_control():
    row = fullscreen_event_row(_SHORT_LINE, ROSTER, window_width=1280)
    assert _reveal(row) is None, (
        "an affordance on a line that is already whole is noise, and worse, it "
        "invites a click that visibly does nothing"
    )


def test_ac2_the_460_char_line_gets_one():
    row = fullscreen_event_row(_LONG_LINE, ROSTER, window_width=1280)
    assert _reveal(row) is not None


def test_ac2_the_budget_is_tighter_at_the_app_minimum_than_at_1280():
    assert message_char_budget(1024) < message_char_budget(1280)
    # ~105 and ~142 respectively — asserted as a band, because the 0.6em
    # advance is an estimate and pinning an exact integer would be pinning the
    # estimate rather than the behaviour.
    assert 95 <= message_char_budget(1024) <= 115
    assert 130 <= message_char_budget(1280) <= 155


def test_ac2_an_unknown_width_budgets_AS_THE_CELL_IS_ACTUALLY_LAID_OUT():
    """One width read, one fallback — the budget and the box it budgets.

    This is the invariant that broke in review: the dialog pinned its container
    to 1280px when ``page.width`` was unset while the budget floored at 1024,
    so a 120-char line was rendered in a cell that fits ~142 chars and told it
    needed a reveal. Both callers now go through :func:`page_width`, so an
    unreported width budgets for the width the cell is genuinely given.
    """
    assert message_char_budget(None) == message_char_budget(_FALLBACK_WINDOW_WIDTH)
    assert message_char_budget(0) == message_char_budget(_FALLBACK_WINDOW_WIDTH)
    assert _FALLBACK_WINDOW_WIDTH == 1280.0


def test_ac2_the_two_readers_of_the_page_width_cannot_disagree():
    """The FUNCTION, not the number: both the container and the budget resolve
    their width here, so there is no second fallback to drift."""

    class _NoWidth:
        width = None

    class _Wide:
        width = 1600

    assert page_width(_NoWidth()) == _FALLBACK_WINDOW_WIDTH
    assert page_width(object()) == _FALLBACK_WINDOW_WIDTH
    assert page_width(_Wide()) == 1600.0
    # And the budget derived from it is the budget for THAT box.
    assert message_char_budget(page_width(_NoWidth())) == message_char_budget(1280)


def test_ac2_a_narrower_window_gets_a_TIGHTER_budget_all_the_way_down():
    """No floor in the arithmetic. Floors belong to the widths the app can be
    at; over-estimating the budget withholds the reveal from a line that IS
    cut, which is the dangerous direction."""
    assert message_char_budget(900) < message_char_budget(1024)
    assert message_char_budget(1024) < message_char_budget(1280)


def test_ac2_a_line_that_fits_at_1280_but_not_at_1024_gets_the_reveal_only_there():
    """The case the width argument exists for. 120 chars fits the ~142-char
    cell at 1280 and overruns the ~105-char cell at the app minimum."""
    msg = "x" * 120
    assert message_needs_reveal(msg, 1280) is False
    assert message_needs_reveal(msg, 1024) is True


def test_ac2_the_reveal_carries_a_tooltip_so_it_paints_a_semantics_node():
    """Not decoration: a control with no tooltip emits no accessibility node,
    which is exactly why PS-179's resize grip could not be driven at all."""
    row = fullscreen_event_row(_LONG_LINE, ROSTER, window_width=1280)
    assert _reveal(row).tooltip == _REVEAL_TIP

    opened = fullscreen_event_row(
        _LONG_LINE, ROSTER, expanded=True, window_width=1280
    )
    assert _reveal(opened).tooltip == _HIDE_TIP


def test_ac2_the_reveal_stays_addressable_while_it_is_open():
    """A revealed row must keep its control, or the reveal is one-way and the
    row can never be collapsed again."""
    opened = fullscreen_event_row(
        _SHORT_LINE, ROSTER, expanded=True, window_width=1280
    )
    assert _reveal(opened) is not None


def test_ac2_clicking_the_reveal_calls_back_with_the_line():
    seen: list[str] = []
    row = fullscreen_event_row(
        _LONG_LINE, ROSTER, window_width=1280, on_toggle=seen.append
    )
    _reveal(row).on_click(None)
    assert seen == [_LONG_LINE]


# --- AC3: selection ---------------------------------------------------------


def test_ac3_the_fullscreen_message_is_selectable_in_both_states():
    """The capability v2.8.4 had and PS-229 removed with no recorded argument.
    Selection costs no vertical metric, so it is on in BOTH states — a line
    short enough to need no reveal is still worth copying."""
    assert fullscreen_message_text("x", "#fff", expanded=False).selectable is True
    assert fullscreen_message_text("x", "#fff", expanded=True).selectable is True


def test_ac3_the_row_built_by_the_view_is_selectable_too():
    """Through the real row builder, not just the cell helper — the wiring is
    what regressed last time."""
    assert _message_text(fullscreen_event_row(_LONG_LINE, ROSTER)).selectable is True


def test_ac3_the_selectable_cell_still_names_itself_to_a_screen_reader():
    """MEASURED live, not assumed: a ``selectable`` Text is a canvas-level
    SelectableText and paints an accessibility node with an EMPTY string — so
    restoring selection silently removed the message column from the semantics
    tree, leaving the row reading as "shop-de-03 / 18:07:20" with the refusal
    missing. ``semantics_label`` puts the sentence back."""
    t = fullscreen_message_text(_REFUSAL, "#fff", expanded=False)
    assert t.semantics_label == _REFUSAL
    assert _TAIL in t.semantics_label


# --- AC4: the dock is untouched --------------------------------------------


def test_ac4_the_dock_row_still_forces_one_ellipsised_line():
    """The dock's contract, re-asserted from THIS ticket's side so a future
    change here cannot quietly loosen it. tests/test_log_dock.py pins the same
    property and must pass unmodified."""
    row = event_row(_LONG_LINE, ROSTER)
    _dot, profile, message, time_col = row.content.controls

    assert row.height == ROW_HEIGHT
    for col in (profile, message, time_col):
        assert col.content.no_wrap is True
        assert col.content.max_lines == 1


def test_ac4_the_dock_row_has_no_reveal_control():
    row = event_row(_LONG_LINE, ROSTER)
    assert len(row.content.controls) == 4
    assert _reveal(row) is None


def test_ac4_the_dock_row_is_the_same_height_whatever_the_message():
    heights = {
        event_row(_SHORT_LINE, ROSTER).height,
        event_row(_LONG_LINE, ROSTER).height,
    }
    assert heights == {ROW_HEIGHT}


def test_ac4_a_collapsed_fullscreen_row_is_the_dock_ruler_column_for_column():
    """The row's docstring claims the columns are "the dock's, at the dock's
    widths". A collapsed row is where that claim has to hold literally — same
    height, same column widths, same timestamp alignment. The stamp moves to
    the TOP only when the row is tall enough for the distinction to exist."""
    dock = event_row(_SHORT_LINE, ROSTER)
    full = fullscreen_event_row(_SHORT_LINE, ROSTER, window_width=1280)

    d_dot, d_profile, _d_msg, d_time = dock.content.controls
    f_dot, f_profile, _f_msg, f_time = full.content.controls

    assert full.height == dock.height == ROW_HEIGHT
    assert f_profile.width == d_profile.width
    assert f_time.width == d_time.width
    assert f_dot.width == d_dot.width
    assert f_time.alignment.y == d_time.alignment.y  # CENTER, not TOP
    assert f_time.alignment.x == d_time.alignment.x == 1.0  # right


def test_ac1_a_revealed_row_moves_the_timestamp_to_the_top():
    """On five wrapped lines the stamp belongs beside the message's FIRST line,
    not floating in the middle of the block."""
    revealed = fullscreen_event_row(
        _LONG_LINE, ROSTER, expanded=True, window_width=1024
    )
    time_col = revealed.content.controls[-1]
    assert time_col.alignment.y == -1.0  # TOP
    assert time_col.alignment.x == 1.0  # RIGHT


# --- AC5: the docstring is true again ---------------------------------------


def test_ac5_the_dock_docstring_no_longer_promises_something_the_code_denies():
    """The promise justifies the truncation, so it is load-bearing prose. It
    must name the fullscreen escape hatch AND that hatch must exist — the
    reveal function it points at is imported at the top of this module, so an
    unresolvable promise fails at import."""
    import src.ui.log_console as lc

    doc = lc.__doc__ or ""
    assert "fullscreen Activity Log" in doc
    assert "reveal" in doc
    assert "selectable" in doc
    assert callable(fullscreen_event_row)
