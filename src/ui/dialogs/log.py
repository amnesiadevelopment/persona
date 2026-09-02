"""The fullscreen Activity Log.

WHAT WAS WRONG, IN HIS WORDS: "полноэкранный режим активити лога это ваще беда
бедой… как он должен разворачиваться и что он должен показывать без этих
дебильных рамок".

Three separate faults, and the borders were only the one he could name.

1. **The frames.** The old view was an ``AlertDialog`` with a
   ``RoundedRectangleBorder``, an accent-coloured ``BorderSide``, a rounded
   inner container AND a modal scrim — four nested edges between him and the
   log. They are GONE, not restyled: this fills the window and separates its
   regions with spacing and colour weight instead of with lines.

2. **It was not fullscreen.** It was a fixed 1000x600 box. On a wide monitor
   that is a small window floating in a dimmed screen — which is what makes
   "как он должен разворачиваться" a real question. It now takes the whole
   page.

3. **It showed the same thing, bigger.** A full screen of log is a different
   READING TASK from a six-line tail: at six lines you watch, at full screen
   you look something up. So the size buys tools that only make sense at that
   size — severity filters, a profile filter, and a search — rather than the
   same six columns stretched wider.

The row keeps the dock's COLUMNS — severity, profile, message, time, in the
same places and at the same widths — so his eye does not have to re-learn the
layout when he opens it. What it does NOT keep is the dock's hard one-line
message cell (PS-266): a message that is cut carries a reveal here, because
this is where the dock's own docstring sends him to read the whole line. The
columns are the shared property; the message cell is where the two reading
tasks genuinely differ. See :func:`fullscreen_event_row`.
"""

from __future__ import annotations

import flet as ft

from ..log_console import (
    NO_PROFILE,
    PROFILE_COL_WIDTH,
    ROW_HEIGHT,
    SEV_COLOR,
    SEV_FAIL,
    SEV_INFO,
    SEV_OK,
    TEXT_SIZE,
    TIME_COL_WIDTH,
    _dot,
    parse_event,
)
from ..theme.colors import COLORS

MONO = "monospace"

#: How many wrapped lines a REVEALED message may occupy before it is cut.
#:
#: The reveal has to be bounded too, or "expand" just re-creates the defect one
#: click later — the same argument :data:`~src.ui.app._STATUS_EXPANDED_MAX_LINES`
#: records for the engines rail (PS-229), which this applies to the log.
#:
#: FIVE, and the arithmetic is the app's MINIMUM window, not a comfortable one.
#: The message column is the window width minus 297px of fixed chrome
#: (PROFILE_COL_WIDTH 132 + TIME_COL_WIDTH 62 + the 7px dot + 14x2 row padding
#: + 12x3 row spacing + 18+14 dialog padding), and monospace at TEXT_SIZE=11.5
#: advances ~0.6em per character:
#:
#:     1280px -> (1280-297)/6.9 = ~142 chars/line
#:     1024px -> (1024-297)/6.9 = ~105 chars/line     (window.min_width)
#:
#: The longest refusal this product composes is TimezoneUnderivableError
#: (process.py:233-241), which reaches the log as
#: "Error starting process: ..." at 460 characters -> ceil(460/142) = 4 lines
#: at 1280px and ceil(460/105) = 5 at 1024px. Five therefore clears the worst
#: REAL message at the smallest window the app can be at, and stops well short
#: of turning one pasted stack trace into a screenful.
#:
#: THOSE TWO NUMBERS ARE A CEILING, NOT A PREDICTION, and the driven run says
#: so: the live screenshots show that refusal wrapping over THREE lines at
#: 1280px and FOUR at 1024px, one fewer than the arithmetic each time. The
#: 0.6em advance below is the conservative end of the monospace range, so the
#: budget under-counts how much fits and the line count over-counts. That is
#: the safe direction for a BOUND — a bound one line too generous shows the
#: whole sentence, a bound one line too tight cuts the payload off the end —
#: but the comment should not be read as a claim about what renders.
_MESSAGE_EXPANDED_MAX_LINES = 5

#: The fixed chrome the message column does NOT get, in pixels. Kept as one
#: named number because the budget above and :func:`message_char_budget` must
#: not drift apart.
_ROW_CHROME_PX = 297

#: The window width assumed when the page has not reported one.
#:
#: ONE fallback, used by BOTH the cell's layout and the cell's budget. It is
#: not a preference: the fullscreen dialog pins its own container to this width
#: when ``page.width`` is unset (see :func:`open_log_dialog`), so this IS the
#: width the message cell is laid out at in that case. Budgeting against any
#: other number would mean the budget and the box it budgets disagree — which
#: is exactly how the reveal ends up on a line that is already whole, "an
#: affordance ... that invites a click that visibly does nothing".
_FALLBACK_WINDOW_WIDTH = 1280.0

#: Monospace advance per character at :data:`~src.ui.log_console.TEXT_SIZE`.
#: The standard 0.6em ratio — an ESTIMATE, and the only estimated term here:
#: flet returns no text metrics, so the cell cannot be measured, only budgeted.
_CHAR_ADVANCE = TEXT_SIZE * 0.6

#: The reveal's tooltips. They are the control's LABEL as far as the
#: accessibility tree is concerned — a bare GestureDetector paints no semantics
#: node, so without a tooltip the affordance is unaddressable by the live
#: driver and untestable by anything but a source grep.
_REVEAL_TIP = "Show the full message"
_HIDE_TIP = "Hide the full message"

#: The severity filters offered, in the order they are shown. "all" is not a
#: severity — it is the cleared state, and it is first because it is the one
#: the view opens in.
_FILTERS = (
    ("all", "all"),
    (SEV_FAIL, "failures"),
    (SEV_OK, "ok"),
    (SEV_INFO, "info"),
)


def page_width(page) -> float:
    """The width this view is laid out at — read ONCE, in one place.

    THE POINT OF THIS FUNCTION IS THAT THERE IS ONLY ONE OF IT. Two readers of
    ``page.width`` live in this file: the dialog's container, which is what
    actually makes the message cell that wide, and :func:`message_char_budget`,
    which decides whether a line fits in it. An earlier revision gave them
    different fallbacks — ``None``-floored-to-1024 for the budget, 1280 for the
    container — so with the width unreported the cell was laid out 1280px wide
    and budgeted as if it were 1024px, and a 120-character line that fits
    perfectly well got a chevron whose click does nothing visible. That is
    verbatim what :func:`message_needs_reveal` exists to prevent.

    So: one read, one fallback, both callers. Whatever number comes out of
    here, the box and the budget are talking about the same box.

    The fallback is :data:`_FALLBACK_WINDOW_WIDTH`, which is not a guess at the
    operator's monitor — it is the width the container itself falls back to, so
    the two agree by construction rather than by coincidence.
    """
    return float(getattr(page, "width", None) or 0) or _FALLBACK_WINDOW_WIDTH


def message_char_budget(window_width_px: float | None) -> int:
    """How many characters of message fit on ONE line at this window width.

    Character-budgeted rather than measured, and that is not a shortcut: flet
    returns no text metrics back (``src/ui/app.py:2545-2551`` says so and
    budgets for the same reason), so a measured approach is not available. The
    TRUNCATION itself is certain from ``max_lines=1`` + ``ELLIPSIS`` regardless
    of where exactly the cut lands; only the exact column count is an estimate.

    NO FLOOR. A narrower window means a TIGHTER cell and therefore a smaller
    budget, all the way down — floors belong to the widths the app can actually
    be at (``page.window.min_width`` = 1024, ``theme/page.py:160``), not to
    this arithmetic. An earlier revision floored here at 1024, which
    over-estimated the budget for any smaller width and so withheld the reveal
    from a line that was cut — the dangerous direction. An unreported width is
    not a narrow width; that case is :func:`page_width`'s, and it resolves to
    the same number the cell is laid out at.
    """
    width = float(window_width_px or 0) or _FALLBACK_WINDOW_WIDTH
    return max(1, int((width - _ROW_CHROME_PX) / _CHAR_ADVANCE))


def message_needs_reveal(message: str, window_width: float | None) -> bool:
    """Whether this message is longer than its one-line cell.

    Follows :meth:`~src.ui.app.App._status_needs_reveal` exactly: a line that is
    already whole gets NO affordance, because "an affordance on a line that is
    already whole is noise, and worse, it invites a click that visibly does
    nothing".
    """
    return len(message or "") > message_char_budget(window_width)


def fullscreen_message_text(
    message: str, colour: str, *, expanded: bool
) -> ft.Text:
    """The fullscreen message cell — the ONE place this view departs from the dock.

    THREE PROPERTIES, and dropping any one of them re-creates the defect:

    1. ``expanded`` swaps ``max_lines=1``/``no_wrap=True`` for
       :data:`_MESSAGE_EXPANDED_MAX_LINES` wrapped lines. Bounded, because an
       unbounded reveal is the original defect deferred by one click.
    2. ``expand=True`` in BOTH states, because bounding the lines without
       bounding the WIDTH is the fix that looks right and changes nothing — a
       ``Text`` in a ``Row`` is granted its intrinsic width unless it asks to
       flex, so the ellipsis never engages. ``app.py:92-103`` records this
       trap; it is repeated here because it is the half that gets dropped.
    3. ``selectable=True`` in both states (AC3). Selection costs no vertical
       metric, and it is what makes a refusal sentence COPYABLE — the
       capability v2.8.4's fullscreen row had (``selectable=True``, no
       ``no_wrap``) and PS-229 removed with no recorded argument.

    NOT ``log_console._cell``: that renderer is the DOCK's contract — one line,
    always, so the region cannot change extent while profiles launch — and it
    is right for the dock. Loosening it would change what the dock renders,
    which is explicitly out of scope. So the fullscreen view gets its own cell
    rather than a shared one with a flag.

    ``semantics_label`` is NOT decoration and NOT a test hook. MEASURED here:
    a ``selectable`` Text is a canvas-level SelectableText and paints an
    accessibility node with an EMPTY string — the row's group node carries the
    profile and the timestamp as its label and the message contributes nothing
    at all. So the moment selection was restored, the one column that carries
    the actual event became invisible to a screen reader, and the sentence
    reads as ``"shop-de-03 / 18:07:20"`` with the refusal missing. Naming the
    string here puts it back. That it also makes the reveal drivable is a
    consequence, not the reason — the same shape as PS-229 giving the resize
    grip a tooltip.
    """
    return ft.Text(
        message,
        size=TEXT_SIZE,
        font_family=MONO,
        color=colour,
        expand=True,
        selectable=True,
        semantics_label=message,
        no_wrap=not expanded,
        max_lines=_MESSAGE_EXPANDED_MAX_LINES if expanded else 1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )


def _message_colour(sev: str) -> str:
    return (
        COLORS["error"]
        if sev == SEV_FAIL
        else COLORS["text_main"]
        if sev == SEV_OK
        else COLORS["text_dim"]
    )


def fullscreen_event_row(
    line: str,
    profiles: frozenset[str] | set[str],
    *,
    expanded: bool = False,
    window_width: float | None = None,
    on_toggle=None,
) -> ft.Control:
    """One event, in the FULLSCREEN view: the dock's columns, a readable message.

    The columns — severity dot, profile ruler, message, right-aligned time —
    are the dock's, at the dock's widths, so the layout does not have to be
    re-learned. What differs is the message cell and the row's HEIGHT: a
    revealed row is allowed to be taller, because the uniform-height rule is a
    property of the WATCHED dock (a wrapping row changes the region's extent on
    every repaint while profiles launch) and not of a view you open to look one
    thing up.

    The reveal control is drawn only when the message actually overruns its
    cell (:func:`message_needs_reveal`), and it carries a TOOLTIP — which is
    also what makes Flutter emit a semantics node for it, so the live driver
    can address it at all (``live_log_dock.py:37-42`` recorded the bare
    ``GestureDetector`` grip as undrivable for exactly that reason).
    """
    stamp, profile, message, sev = parse_event(line, profiles)

    time_col = ft.Container(
        width=TIME_COL_WIDTH,
        # TOP_RIGHT only when the row is TALL. On a revealed row the stamp
        # belongs beside the message's FIRST line, not floating in the middle
        # of five; on a collapsed row the dock's own CENTER_RIGHT is what keeps
        # this view's ruler identical to the dock's, which is the claim this
        # row's docstring makes about the columns.
        alignment=(
            ft.Alignment.TOP_RIGHT if expanded else ft.Alignment.CENTER_RIGHT
        ),
        content=ft.Text(
            stamp,
            size=TEXT_SIZE,
            font_family=MONO,
            color=COLORS["text_sub"],
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )
    profile_col = ft.Container(
        width=PROFILE_COL_WIDTH,
        content=ft.Text(
            profile or NO_PROFILE,
            size=TEXT_SIZE,
            font_family=MONO,
            color=COLORS["accent"] if profile else COLORS["text_sub"],
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )
    message_col = ft.Container(
        expand=True,
        content=fullscreen_message_text(
            message, _message_colour(sev), expanded=expanded
        ),
    )
    dot_col = ft.Container(
        width=7,
        height=ROW_HEIGHT,
        alignment=ft.Alignment.CENTER,
        # The dock's own _dot, not a copy of it: the severity mark is a shared
        # property of both surfaces, so a future change to its size or radius
        # must land on both at once.
        content=_dot(sev),
    )

    controls: list[ft.Control] = [dot_col, profile_col, message_col]
    if message_needs_reveal(message, window_width) or expanded:
        controls.append(
            ft.Container(
                width=20,
                height=ROW_HEIGHT,
                alignment=ft.Alignment.CENTER,
                # AN ICONBUTTON, NOT A TAPPABLE CONTAINER, and this is measured
                # rather than stylistic. A bare Container-with-on_click is
                # ABSORBED into the row's merged semantics node once the
                # message carries a semantics_label: the whole 1248px row
                # becomes one button whose label is "Show the full message"
                # plus the profile plus the sentence, and the chevron has no
                # box of its own — so nothing can address it, and a click at
                # the "control's" centre lands on the message instead. A real
                # Material button paints its own node beside the row's.
                content=ft.IconButton(
                    icon=(
                        ft.Icons.UNFOLD_LESS if expanded else ft.Icons.UNFOLD_MORE
                    ),
                    icon_size=12,
                    icon_color=COLORS["text_dim"],
                    width=20,
                    height=20,
                    padding=ft.Padding.all(0),
                    style=ft.ButtonStyle(padding=ft.Padding.all(0)),
                    tooltip=(_HIDE_TIP if expanded else _REVEAL_TIP),
                    on_click=(lambda _: on_toggle(line)) if on_toggle else None,
                ),
            )
        )
    controls.append(time_col)

    return ft.Container(
        # No fixed height when revealed: the whole point is the extra lines.
        # Collapsed rows keep ROW_HEIGHT so an unrevealed list is the same
        # ruler the dock is.
        height=None if expanded else ROW_HEIGHT,
        padding=ft.Padding.symmetric(horizontal=14, vertical=0),
        content=ft.Row(
            spacing=12,
            vertical_alignment=(
                ft.CrossAxisAlignment.START
                if expanded
                else ft.CrossAxisAlignment.STRETCH
            ),
            controls=controls,
        ),
    )


def open_log_dialog(page: ft.Page, log_lines: list[str], profiles=None) -> None:
    """Open the Activity Log at full size, with the tools that size deserves."""
    profiles = frozenset(profiles or ())
    state = {"sev": "all", "profile": "", "query": ""}
    #: Which lines the operator has revealed. Keyed by the LINE ITSELF, not by
    #: an index: the log grows underneath this view while it is open, and an
    #: index would silently move the reveal onto a different event.
    expanded: set[str] = set()

    body = ft.ListView(expand=True, spacing=0)
    count_label = ft.Text("", size=11, color=COLORS["text_sub"], font_family=MONO)

    def matching() -> list[str]:
        out = []
        for line in log_lines:
            _stamp, prof, msg, sev = parse_event(line, profiles)
            if state["sev"] != "all" and sev != state["sev"]:
                continue
            if state["profile"] and prof != state["profile"]:
                continue
            q = state["query"].strip().lower()
            if q and q not in line.lower():
                continue
            out.append(line)
        return out

    def toggle(line: str) -> None:
        """Reveal or re-collapse ONE line, then repaint the list."""
        if line in expanded:
            expanded.discard(line)
        else:
            expanded.add(line)
        repaint()

    def repaint(_=None) -> None:
        rows = matching()
        # The width the list is PAINTED at. repaint() has five callers — the
        # reveal toggle, the severity filter, the search field, the profile
        # filter, and the initial paint — and a window RESIZE is not among
        # them: nothing in this file binds page.on_resize, and the app's own
        # handler (app.py:1082) adjusts the dock without rebuilding these rows.
        # So a window resized while this view is open keeps the budget it
        # opened with, and the dialog's container keeps the width it was pinned
        # to at :func:`open_log_dialog`. That is the honest description of the
        # behaviour; making the view track a resize is a separate change to
        # both the container and this list, and is not what this ticket asks
        # for.
        win_w = page_width(page)
        body.controls = (
            [
                fullscreen_event_row(
                    ln,
                    profiles,
                    expanded=ln in expanded,
                    window_width=win_w,
                    on_toggle=toggle,
                )
                for ln in rows
            ]
            if rows
            else [
                ft.Container(
                    padding=ft.Padding.symmetric(vertical=28),
                    content=ft.Text(
                        "Nothing matches these filters.",
                        size=12,
                        color=COLORS["text_dim"],
                        font_family=MONO,
                    ),
                )
            ]
        )
        count_label.value = f"{len(rows)} of {len(log_lines)}"
        with __import__("contextlib").suppress(Exception):
            body.update()
            count_label.update()

    # --- severity filters: text that changes WEIGHT, never a boxed chip ----
    # A row of bordered pills is exactly the "рамки" he objected to, so the
    # selected filter is marked by colour and weight instead of by an outline.
    filter_texts: dict[str, ft.Text] = {}

    def paint_filters() -> None:
        for key, label in _FILTERS:
            t = filter_texts[key]
            on = state["sev"] == key
            t.value = label
            t.color = (
                (SEV_COLOR.get(key) or COLORS["accent"]) if on else COLORS["text_sub"]
            )
            t.weight = ft.FontWeight.BOLD if on else ft.FontWeight.NORMAL
        with __import__("contextlib").suppress(Exception):
            for t in filter_texts.values():
                t.update()

    def pick(key: str):
        def go(_):
            state["sev"] = key
            paint_filters()
            repaint()

        return go

    filter_row: list[ft.Control] = []
    for key, label in _FILTERS:
        filter_texts[key] = ft.Text(label, size=11.5, font_family=MONO)
        filter_row.append(
            ft.Container(
                on_click=pick(key),
                ink=True,
                border_radius=3,
                padding=ft.Padding.symmetric(horizontal=9, vertical=6),
                content=filter_texts[key],
            )
        )

    search = ft.TextField(
        hint_text="search the log…",
        height=34,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        border_color="transparent",
        focused_border_color=COLORS["accent"],
        bgcolor=COLORS["input_bg"],
        border_radius=4,
        color=COLORS["text_main"],
        text_style=ft.TextStyle(font_family=MONO),
        on_change=lambda e: (state.update(query=e.control.value or ""), repaint()),
    )

    # --- profile filter: the same borderless text pattern as the severities.
    # Deliberately NOT a Dropdown. A dropdown is a bordered box that opens a
    # second bordered box, which is the "рамки" complaint again; and the
    # profiles are the log's own vocabulary, so showing them flat means he can
    # SEE which machines are in this session instead of discovering them by
    # opening a menu. It also keeps the whole header one control class: text
    # that changes weight when it is on.
    profile_texts: dict[str, ft.Text] = {}

    def paint_profiles() -> None:
        for key, t in profile_texts.items():
            on = state["profile"] == key
            t.color = COLORS["accent"] if on else COLORS["text_sub"]
            t.weight = ft.FontWeight.BOLD if on else ft.FontWeight.NORMAL
        with __import__("contextlib").suppress(Exception):
            for t in profile_texts.values():
                t.update()

    def pick_profile(key: str):
        def go(_):
            state["profile"] = key
            paint_profiles()
            repaint()

        return go

    profile_row: list[ft.Control] = []
    for key, label in [("", "all")] + [(p, p) for p in sorted(profiles)]:
        profile_texts[key] = ft.Text(
            label, size=11.5, font_family=MONO, no_wrap=True, max_lines=1
        )
        profile_row.append(
            ft.Container(
                on_click=pick_profile(key),
                ink=True,
                border_radius=3,
                padding=ft.Padding.symmetric(horizontal=9, vertical=6),
                content=profile_texts[key],
            )
        )

    header = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.TERMINAL, size=16, color=COLORS["accent"]),
                    ft.Text(
                        "ACTIVITY",
                        size=12,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    count_label,
                ],
            ),
            ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    *filter_row,
                    ft.Container(width=8),
                    *profile_row,
                    ft.Container(width=220, content=search),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_FULLSCREEN,
                        icon_size=14,
                        icon_color=COLORS["text_sub"],
                        tooltip="Back to the dock",
                        on_click=lambda _: page.pop_dialog(),
                    ),
                ],
            ),
        ],
    )

    paint_filters()
    repaint()

    # No shape, no border side, no scrim colour, no actions row: the dialog is
    # a surface, not a frame. `content_padding=0` removes the last inset that
    # would otherwise draw an implicit edge around the log.
    # The dialog is sized to the PAGE, explicitly. `expand=True` alone is not
    # enough: an AlertDialog sizes itself to its content, so the log rendered
    # as a tall column with the app still visible down both edges — a framed
    # box again, by accident rather than by decoration. Reading the page's own
    # dimensions is what makes "fullscreen" mean the window.
    #
    # THE SAME READ the row budget uses — page_width(), not a second
    # getattr with its own fallback. When the two disagreed, the cell was laid
    # out at one width and budgeted at another, and a line that fitted got a
    # chevron that did nothing. One number, one box.
    win_w = page_width(page)
    win_h = getattr(page, "height", None) or 800

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["log_bg"],
        shape=ft.RoundedRectangleBorder(radius=0),
        inset_padding=ft.Padding.all(0),
        content_padding=ft.Padding.all(0),
        content=ft.Container(
            width=win_w,
            height=win_h,
            bgcolor=COLORS["log_bg"],
            padding=ft.Padding.only(left=18, right=14, top=12, bottom=8),
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    # A gap does the separating. The old view drew a 1px rule
                    # here; spacing reads as a break without adding an edge.
                    ft.Container(height=10),
                    body,
                ],
            ),
        ),
    )
    page.show_dialog(dlg)
