"""PS-292 — turning a profile filter OFF returns the full list, and it is
unambiguous WHICH control does that.

THE REPORT, 2026-09-03: "есть баг когда лог на весь екран при выборе сверху
фильтр по определнному профилю то обратно не возварщает на общий список при
выключении фильтрации."

WHAT WAS ACTUALLY WRONG, reproduced headlessly before anything was changed.
Not the predicate: :func:`~src.ui.dialogs.log.open_log_dialog`'s ``matching()``
tests ``if state["profile"] and prof != ...``, and the clear control writes the
empty string that disables it — that pair was always correct, and
``test_the_cleared_state_really_is_the_full_list`` pins it. What was wrong is
that the operator could not TELL WHICH CONTROL TO PRESS:

* the header lays out ``*filter_row, ft.Container(width=8), *profile_row``, so
  the severity row's "all" and the profile row's clear control rendered as the
  SAME WORD eight pixels apart; and
* only ``paint_filters()`` ran at build — ``paint_profiles()`` did not — so on
  open the severity "all" was painted ACTIVE (accent, bold) while every profile
  control had ``color=None`` and ``weight=None``.

So the one that looked selected was the severity filter, and pressing it does
nothing to the profile filter. That is the reported symptom exactly: the filter
appears not to come back off.

THESE TESTS ARE ABOUT THE OPERATOR'S QUESTION ("which of these clears the
profile filter?"), not only about the state dict. Two of them would pass
against the defect if they only asserted that ``state["profile"]`` can be reset
— which it always could. The load-bearing ones are the two that assert the
controls are DISTINGUISHABLE: by label and by paint. Each was confirmed red
against the code as it stood.
"""

from __future__ import annotations

import flet as ft

from src.ui.dialogs.log import (
    _ALL_PROFILES_LABEL,
    _ALL_PROFILES_TIP,
    _ALL_SEVERITIES_TIP,
    open_log_dialog,
)
from src.ui.log_console import SEV_COLOR, SEV_FAIL, parse_event
from src.ui.theme.colors import COLORS

_PROFILES = frozenset({"shop-de-03", "mail-us-011"})

#: Four events over two profiles, in the format the ring really stores
#: (``"HH:MM:SS  > message"`` — the delimiter :func:`parse_event` partitions
#: on). Deliberately NOT one line per profile: the whole claim under test is
#: that clearing goes back to ALL of them, which a fixture where every filtered
#: view is already the full view could not show.
#:
#: The severities are the parser's, not asserted by eye: line 3 is the only
#: failure and it belongs to mail-us-011, which is what makes
#: "profile shop-de-03 AND severity failures" a genuinely EMPTY view — the
#: state the severity/profile independence tests need in order to distinguish
#: which axis a control cleared.
_LINES = [
    "10:00:01  > Launching shop-de-03",
    "10:00:02  > Launching mail-us-011",
    "10:00:03  > Error starting process: mail-us-011 refused to start",
    "10:00:04  > Loaded 6 bookmarks for shop-de-03",
]


def _parsed(line: str):
    return parse_event(line, _PROFILES)


def _messages_for(predicate) -> list[str]:
    """The messages the view SHOULD paint for a predicate over ``_LINES``.

    Derived through the real :func:`parse_event`, not hand-split: the row
    renders the PARSED message ("Launching"), not the raw line, and an
    expectation built by string surgery would drift from the parser the moment
    either changed.
    """
    return [_parsed(ln)[2] for ln in _LINES if predicate(_parsed(ln))]


_SHOP_DE_03_LINES = len(_messages_for(lambda p: p[1] == "shop-de-03"))
_FAILURE_LINES = len(_messages_for(lambda p: p[3] == SEV_FAIL))


class _FakePage:
    """The minimum surface :func:`open_log_dialog` reads.

    ``width``/``height`` are set because the dialog pins its container to them
    and the row budget reads the same number; leaving them unset would exercise
    the fallback path, which is PS-266's subject and not this ticket's.
    """

    width = 1280
    height = 800

    def __init__(self) -> None:
        self.shown = None

    def show_dialog(self, dlg) -> None:
        self.shown = dlg

    def pop_dialog(self) -> None:
        self.shown = None

    def update(self) -> None:
        pass


def _walk(control):
    yield control
    for attr in ("title", "content", "controls", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for c in items:
            if c is not None and hasattr(c, "__dict__"):
                yield from _walk(c)


def _open():
    page = _FakePage()
    open_log_dialog(page, list(_LINES), _PROFILES)
    return page


def _filter_controls(page) -> list[ft.Container]:
    """Every clickable text control in the header — severities AND profiles.

    Found by SHAPE (a Container with an on_click wrapping a Text) rather than
    by position, because the two rows are spliced into one header Row and an
    index would silently move onto the other row's controls.
    """
    return [
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Container)
        and getattr(c, "on_click", None) is not None
        and isinstance(getattr(c, "content", None), ft.Text)
    ]


def _by_label(page, label: str) -> ft.Container:
    hits = [c for c in _filter_controls(page) if c.content.value == label]
    assert len(hits) == 1, (
        f"expected exactly one header control labelled {label!r}, found "
        f"{len(hits)} — two identically-labelled filter controls is the "
        "PS-292 defect itself"
    )
    return hits[0]


def _rows(page) -> list[str]:
    """The messages currently painted in the list."""
    body = next(c for c in _walk(page.shown) if isinstance(c, ft.ListView))
    out = []
    for row in body.controls:
        for cell in _walk(row):
            if isinstance(cell, ft.Text) and cell.expand:
                out.append(cell.value)
    return out


def _count_readout(page) -> str:
    """The "N of M" label, which is the OTHER thing the operator reads to
    decide whether the filter came off."""
    labels = [
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Text) and isinstance(c.value, str) and " of " in c.value
    ]
    assert labels, "no 'N of M' readout in the header"
    return labels[0].value


def _is_painted_active(text: ft.Text, *, accent: str | None = None) -> bool:
    """Whether this filter control is painted as the SELECTED one.

    The view marks selection with colour AND weight and never with a border
    (that is the "рамки" objection the whole dialog was rebuilt around), so
    both are read. ``accent`` is the expected colour: profile controls and the
    severity "all" use the theme accent, while each severity word paints in
    that severity's OWN colour — so a caller checking "failures" passes
    ``SEV_COLOR[SEV_FAIL]`` rather than the accent.
    """
    expected = accent or COLORS["accent"]
    return text.color == expected and text.weight == ft.FontWeight.BOLD


# --- the premise, asserted rather than assumed ----------------------------


def test_the_fixture_really_does_narrow_when_filtered():
    """If filtering did not change the list, every behaviour test below would
    pass against a control that does nothing.

    Also pins that one profile-filtered view is genuinely EMPTY of failures,
    which is the state the two independence tests read.
    """
    assert 0 < _SHOP_DE_03_LINES < len(_LINES)
    assert 0 < _FAILURE_LINES < len(_LINES)
    assert (
        _messages_for(lambda p: p[1] == "shop-de-03" and p[3] == SEV_FAIL) == []
    ), "shop-de-03 must have no failures, or the independence tests prove nothing"


# --- the defect: the two clear controls were indistinguishable ------------


def test_only_one_header_control_is_labelled_all():
    """RED against the code as it stood: the severity row and the profile row
    each offered a control reading exactly "all", eight pixels apart.

    This is the assertion that actually pins the reported bug. The state dict
    was never wrong; the operator was pressing the other one.
    """
    labels = [c.content.value for c in _filter_controls(_open())]
    assert labels.count("all") == 1, (
        "two controls labelled 'all' sit 8px apart in the header — the "
        "operator cannot tell which one clears the profile filter"
    )


def test_the_profile_clear_control_says_what_it_clears():
    page = _open()
    ctl = _by_label(page, _ALL_PROFILES_LABEL)
    assert _ALL_PROFILES_LABEL == "all profiles"
    assert ctl.tooltip == _ALL_PROFILES_TIP
    # And the severity one names its own axis, so the disambiguation does not
    # rest on the label alone (a tooltip is also the control's accessible name).
    assert _by_label(page, "all").tooltip == _ALL_SEVERITIES_TIP
    assert _ALL_SEVERITIES_TIP != _ALL_PROFILES_TIP


def test_on_open_the_profile_row_is_painted_and_says_it_is_unfiltered():
    """RED against the code as it stood: ``paint_profiles()`` was never called
    at build, so every profile control opened with ``color=None`` and
    ``weight=None`` — unpainted beside a severity "all" painted ACTIVE.

    The view opens unfiltered, so the control that means "unfiltered" must be
    the one that looks selected.
    """
    page = _open()
    cleared = _by_label(page, _ALL_PROFILES_LABEL).content
    assert cleared.color is not None and cleared.weight is not None, (
        "the profile row is unpainted on open"
    )
    assert _is_painted_active(cleared)
    for name in sorted(_PROFILES):
        assert not _is_painted_active(_by_label(page, name).content)


# --- the behaviour the owner reported: filter on, then off ----------------


def test_clicking_a_profile_filters_the_list_to_that_profile():
    page = _open()
    _by_label(page, "shop-de-03").on_click(None)

    assert _rows(page) == _messages_for(lambda p: p[1] == "shop-de-03")
    assert _count_readout(page) == f"{_SHOP_DE_03_LINES} of {len(_LINES)}"
    assert _is_painted_active(_by_label(page, "shop-de-03").content)
    assert not _is_painted_active(_by_label(page, _ALL_PROFILES_LABEL).content)


def test_the_cleared_state_really_is_the_full_list():
    """The ticket's headline: filter by a profile, clear it, get everything
    back — asserted on the ROWS and on the readout, not on the state dict.

    Both are checked because they are what the operator looks at, and because
    ``repaint()`` swallows update failures in a ``suppress(Exception)``: a fix
    that restored the state but left the painted list stale would look
    identical to the operator and would pass a state-only assertion.
    """
    page = _open()
    _by_label(page, "shop-de-03").on_click(None)
    assert len(_rows(page)) == _SHOP_DE_03_LINES  # premise for the clear below

    _by_label(page, _ALL_PROFILES_LABEL).on_click(None)

    # Every line, in order — not merely the right COUNT: a fix that restored
    # the length while painting the wrong rows would satisfy a count check.
    assert _rows(page) == _messages_for(lambda _p: True)
    assert _count_readout(page) == f"{len(_LINES)} of {len(_LINES)}"
    assert _is_painted_active(_by_label(page, _ALL_PROFILES_LABEL).content)
    assert not _is_painted_active(_by_label(page, "shop-de-03").content)


def test_switching_between_two_profiles_then_clearing_still_returns_everything():
    """The clear control is not a one-shot undo of the LAST selection."""
    page = _open()
    _by_label(page, "shop-de-03").on_click(None)
    _by_label(page, "mail-us-011").on_click(None)
    assert _rows(page) == _messages_for(lambda p: p[1] == "mail-us-011")

    _by_label(page, _ALL_PROFILES_LABEL).on_click(None)
    assert _rows(page) == _messages_for(lambda _p: True)


def test_the_severity_all_still_clears_only_the_severity_filter():
    """The fix is DISAMBIGUATION, not making the severity control clear
    everything.

    Pressing "all" on the severity row while a profile filter is on must leave
    that profile filter exactly where it was — the two axes stay independent.
    If this ever went green because "all" started clearing profiles too, the
    header would be lying about what it does.
    """
    page = _open()
    _by_label(page, "shop-de-03").on_click(None)
    _by_label(page, "failures").on_click(None)
    assert _rows(page) == [], "shop-de-03 has no failures — premise for the clear"

    _by_label(page, "all").on_click(None)

    assert _rows(page) == _messages_for(lambda p: p[1] == "shop-de-03"), (
        "the severity 'all' must restore every severity and nothing else — "
        "the profile filter it does NOT own must survive it"
    )
    assert _is_painted_active(_by_label(page, "shop-de-03").content)
    assert not _is_painted_active(_by_label(page, _ALL_PROFILES_LABEL).content)


def test_clearing_the_profile_filter_leaves_the_severity_filter_alone():
    """The mirror of the test above: independence in the other direction."""
    page = _open()
    _by_label(page, "failures").on_click(None)
    _by_label(page, "shop-de-03").on_click(None)
    assert _rows(page) == []

    _by_label(page, _ALL_PROFILES_LABEL).on_click(None)

    # Every FAILURE line, across all profiles — the severity filter survived.
    assert _rows(page) == _messages_for(lambda p: p[3] == SEV_FAIL)
    assert len(_rows(page)) == _FAILURE_LINES
    assert _count_readout(page) == f"{_FAILURE_LINES} of {len(_LINES)}"
    # A severity word paints in its OWN colour, not the accent — see
    # :func:`_is_painted_active`.
    assert _is_painted_active(
        _by_label(page, "failures").content, accent=SEV_COLOR[SEV_FAIL]
    )


def test_a_log_with_no_profiles_still_offers_the_clear_control():
    """The clear control is built from a hardcoded head entry, not from the
    roster, so an empty roster must not remove it — and it must still be the
    control that is painted active on open."""
    page = _FakePage()
    open_log_dialog(page, list(_LINES), frozenset())

    ctl = _by_label(page, _ALL_PROFILES_LABEL)
    assert _is_painted_active(ctl.content)
    assert [c.content.value for c in _filter_controls(page)].count("all") == 1


# --- the exit, which the longer label very nearly cost --------------------
#
# THESE ARE A CHEAP GUARD, NOT THE EVIDENCE. What is actually wrong when they
# fail is a rendered geometry these assertions cannot see: a control's
# properties are identical whether it is painted at x=970 with a 40px box or at
# x=1024 with a box zero pixels wide, which is exactly the regression the first
# attempt at this fix shipped and this suite could not catch. The measurement
# lives in tests/ui_driver/live_ps292.py, which drives the real app at
# 1024x680 across four roster sizes and falsifies itself by reverting
# log_header. What these two pin is the STRUCTURE that measurement depends on,
# so a later edit that quietly puts the close button back inside the tools
# group fails in two seconds instead of in a browser.


def _close_button(page) -> ft.IconButton:
    hits = [
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.IconButton) and c.tooltip == "Back to the dock"
    ]
    assert len(hits) == 1, f"expected exactly one close button, found {len(hits)}"
    return hits[0]


def test_the_close_button_is_not_inside_the_group_that_overflows():
    """The log's ONLY exit must not be laid out at the end of the filter run.

    ``open_log_dialog`` builds a ``modal=True`` AlertDialog with no Escape
    handler, no scrim dismissal and no actions row — all three driven and
    confirmed inert — so this button is the only way out. While it was the last
    child of the tools Row it was laid out after a run of intrinsically-sized
    controls with no wrap and no scroll, and a long enough roster pushed it off
    the right edge: at FIVE profiles on a 1024px window the pre-fix build
    rendered it zero pixels wide and the operator was stuck in the log.
    """
    page = _open()
    close = _close_button(page)

    tool_rows = [
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Row) and any(x is close for x in (c.controls or []))
    ]
    assert len(tool_rows) == 1, "the close button should have exactly one parent Row"
    parent = tool_rows[0]

    # Its parent must be the OUTER header row — the one holding the flexible
    # tools group — and not the tools group itself.
    assert parent.alignment == ft.MainAxisAlignment.SPACE_BETWEEN, (
        "the close button's parent is not the outer header row, so it is back "
        "inside the group that overflows"
    )
    # And it is laid out AFTER the tools, so nothing about the reading order
    # changed for anyone who was not measuring pixels.
    assert parent.controls[-1] is close


def test_the_tools_group_flexes_and_scrolls_so_it_cannot_push_its_siblings():
    """Reparenting the exit alone is not the fix.

    Move the close button out and an overflowing header simply eats the LAST
    PROFILE instead — a filter clipped off the edge is still a filter the
    operator cannot press, which is this ticket's own complaint one control
    along. The tools group therefore takes the space that is left
    (``expand``) and makes the remainder REACHABLE (``scroll``).
    """
    page = _open()
    close = _close_button(page)
    header = next(
        c
        for c in _walk(page.shown)
        if isinstance(c, ft.Row) and any(x is close for x in (c.controls or []))
    )
    # By CONTENT, not by index: the header is [brand, tools, close] and picking
    # positionally would silently move onto the brand row if that order ever
    # changed — which is how the assertion below would start reading the wrong
    # control's properties and pass for the wrong reason.
    tools = next(
        c
        for c in header.controls
        if isinstance(c, ft.Row)
        and any(
            isinstance(t, ft.Text) and t.value == _ALL_PROFILES_LABEL
            for ctl in (c.controls or [])
            for t in _walk(ctl)
        )
    )

    assert tools.expand, "the tools group must take the space the exit leaves"
    assert tools.scroll is not None, (
        "without a scroll the overflow moves off the exit and onto the last "
        "profile filter, which is no better"
    )
    # The premise: the controls that used to crowd the exit really are in here.
    labels = [
        c.value
        for ctl in tools.controls
        for c in _walk(ctl)
        if isinstance(c, ft.Text)
    ]
    assert "all" in labels and _ALL_PROFILES_LABEL in labels
