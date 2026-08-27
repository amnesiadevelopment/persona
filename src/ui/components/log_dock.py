"""The Activity Log as a full-width console dock along the bottom of the window.

The log used to live in the 200px sidebar rail, which is where both of the
owner's complaints came from: it made the rail crush at small window sizes, and
it gave the stream a reading line so narrow that a wide row read as "mostly
empty space with the text lost in it". So the log LEAVES the rail entirely and
takes the whole bottom edge, under both the rail and the page.

Four behaviours, and the last one is the original complaint:

1. **Collapsible, but never switched off.** The chevron drops the console to a
   34px strip that still carries the newest line, a severity-coloured pulse and
   a count of what arrived while it was shut. Collapsing costs height, not
   awareness.
2. **A resizable top edge.** A real ``on_pan_update`` grip, clamped 120-520px,
   with the chosen height surviving a collapse/expand round trip.
3. **A scannable row.** Delegated to :func:`..log_console.event_row`: severity
   dot, profile ruler, message, right-aligned timestamp, all at a fixed height.
4. **Scroll that behaves.** Follow the tail while the operator is at the
   bottom; stop the instant he scrolls up to read, and say so; offer "N new" as
   the one click back.

THE DEFECT THIS CLASS EXISTS TO FIX. The old panel rebuilt its child list from
scratch on every flush — up to every ~0.15s while profiles are launching — and
only ever put the last 6 lines into a fixed 150px box. So there was almost
nothing to scroll, and any scroll position the operator established pointed at
children that no longer existed by the next event. That is the mechanism behind
"не адекватно скролится". Here the ListView is built ONCE and events are
APPENDED to it: a scroll position established before an event still points at
the same entry after it, and the retained tail is deep enough to be worth
scrolling.
"""

from __future__ import annotations

import flet as ft

from ..log_console import (
    ROW_HEIGHT,
    SEV_COLOR,
    StreamState,
    event_row,
    parse_event,
)
from ..theme.colors import COLORS

MONO = "monospace"

#: The console's chrome: the grip bar plus the header row, i.e. everything that
#: is NOT a log row. Every height below is this plus a whole number of rows, so
#: a height is always "N rows" rather than a pixel count that happens to fit.
CHROME_HEIGHT = 9 + 33

#: The vertical padding the ListView adds above and below the row stack
#: (``Padding.symmetric(vertical=6)``).
LIST_PADDING = 12


def height_for_rows(rows: int) -> int:
    """The console height that shows exactly ``rows`` whole log rows.

    THE COMPLAINT THIS ANSWERS. The dock opened at a height derived from the
    WINDOW ("236px, or whatever the rail can spare"), so on his machine it
    opened with more height than it had content to fill — "пустого места".
    A height chosen in pixels can only accidentally be a whole number of rows;
    this makes the row the unit, so the console opens full of log and its
    bottom edge lands where a row ends rather than through the middle of one.
    """
    return CHROME_HEIGHT + LIST_PADDING + rows * ROW_HEIGHT


#: What the console opens at, in ROWS — his number, and the whole point of
#: rows-not-pixels: "нужно что бы изначально он был на 6 строчек".
DEFAULT_ROWS = 6

#: Height of the open console. Sits between the clamps below, and is what the
#: operator gets before he ever touches the grip.
OPEN_HEIGHT = height_for_rows(DEFAULT_ROWS)

#: The grip's clamps, both expressed in rows.
#:
#: MIN is ONE ROW, not a floor of "enough to be worth reading". He asked to be
#: able to take it down to a single line — "либо скрыть до 1 строчки" — and the
#: old 120px floor is what made the smallest state a fixed strip he had to
#: reach a different control for. The grip now reaches every state he named:
#: bigger, smaller, and one line.
MIN_ROWS = 1
MAX_ROWS_VISIBLE = 20
MIN_HEIGHT = height_for_rows(MIN_ROWS)
MAX_HEIGHT = height_for_rows(MAX_ROWS_VISIBLE)

#: The collapsed strip: tall enough for one line of live text and nothing else.
COLLAPSED_HEIGHT = 34

#: How tall the grip's HIT region is. The painted bar stays a 3px line — this
#: is the region that accepts the drag, and it is the second half of why the
#: grip shipped unusable. See :meth:`LogDock._grip`.
GRIP_HIT_HEIGHT = 14

#: How many rows the console keeps painted. Materially more than the 6 the old
#: panel managed — at ROW_HEIGHT this is ~13000px of scrollback against a
#: console that is at most 520px tall, so there is genuinely something to
#: scroll. Bounded because every row is a live flet control.
MAX_ROWS = 600


#: What the sidebar rail needs to show ALL of itself: the header block, seven
#: nav entries, and the engines + version cluster. Measured on the running app
#: at the minimum window size (1024x680), where the rail's own content runs to
#: ~560px (re-measured on the running app after the first estimate of 545 still
#: left the nav ~11px short and clipped `trash`). The console reserves this before choosing its default height —
#: otherwise a fixed 236px dock in a 680px window leaves the rail 444px, the
#: nav is forced to scroll, and `trash` drops below the fold beside an engines
#: dropdown that is itself clipped. The dock is the element that should yield
#: there, because it is the one the operator can drag back.
RAIL_CONTENT_HEIGHT = 560


def affordable_height(window_height: float | None) -> int:
    """The TALLEST console this window can afford without starving the rail.

    The budget itself, with no opinion about what the operator wants: whatever
    is left once the rail has been given the height it needs to show all of
    itself, clamped to the grip's own bounds. A window whose height is unknown
    constrains nothing, so it affords the maximum.

    Split out of :func:`default_height` because the two questions are genuinely
    different once the window can RESIZE: "what should it open at" is asked
    once, but "what may it be right now" is asked on every resize, and an
    operator who dragged the console to 400px must not have that silently
    rewritten to the 236px opening default just because he nudged the window.
    """
    if not window_height or window_height <= 0:
        return MAX_HEIGHT
    return quantize(int(window_height) - RAIL_CONTENT_HEIGHT)


def _drag_delta_y(e) -> float | None:
    """The vertical distance one drag frame moved, from wherever flet put it.

    THE DEFECT THIS FUNCTION IS. The dock's grip handler read ``e.delta_y``,
    and on the flet version this app ships (0.85) that attribute DOES NOT
    EXIST — the event carries ``local_delta``/``global_delta`` (``Offset``
    objects) instead. ``e.delta_y`` therefore evaluated to ``None`` on every
    frame and ``self.height - None`` raised a TypeError inside the gesture
    callback, where flet swallows it. The grip received the gesture perfectly
    and then threw it away, which is exactly the reported symptom: "я не могу
    ползунком менять высоту активити лога".

    MEASURED, not guessed. A GestureDetector driven with a real pointer in a
    served flet app reports, per frame:

        delta_y=None  local_delta=Offset(x=0, y=-9.0)  global_delta.y=-81.0

    So ``local_delta.y`` is the per-frame delta and ``global_delta.y`` is the
    cumulative one — the local value is what a per-frame ``height - delta``
    must use.

    Every candidate is tried in order rather than pinning the one that works
    today: this is precisely the kind of attribute rename that shipped the bug,
    and a grip that silently stops working on a flet upgrade is the failure
    being fixed. ``delta_y`` is kept FIRST so a flet that restores it is
    honoured natively.
    """
    direct = getattr(e, "delta_y", None)
    if isinstance(direct, (int, float)):
        return float(direct)
    for name in ("local_delta", "delta", "global_delta"):
        offset = getattr(e, name, None)
        y = getattr(offset, "y", None)
        if isinstance(y, (int, float)):
            return float(y)
    primary = getattr(e, "primary_delta", None)
    if isinstance(primary, (int, float)):
        return float(primary)
    return None


def rows_for_height(height: float) -> int:
    """How many WHOLE log rows a console of this height shows.

    The inverse of :func:`height_for_rows`, and the reason the grip can report
    its own state in his units: a drag reads out as "8 rows", not "289px".
    """
    usable = int(height) - CHROME_HEIGHT - LIST_PADDING
    return max(MIN_ROWS, min(MAX_ROWS_VISIBLE, usable // ROW_HEIGHT))


def quantize(height: float) -> int:
    """Snap a height to the nearest whole number of rows, within the clamps.

    EVERY height the console can hold passes through here — the opening
    default, a drag, and a window resize alike — so the console's bottom edge
    always lands where a row ends. That is what removes the "пустое место" he
    reported: a console sized in pixels shows five rows and a sliver of a
    sixth, and the sliver reads as the panel being taller than its content.
    """
    return height_for_rows(rows_for_height(height))


def default_height(window_height: float | None) -> int:
    """The console's opening height for a window of this height.

    A CONSTANT default is what broke the rail at the app's own minimum size, so
    this is a budget rather than a number: take the preferred height when the
    window can afford it, and give the difference back to the rail when it
    cannot. Still clamped to the same bounds the grip uses, so the console can
    never open smaller than it is usable.
    """
    return min(OPEN_HEIGHT, affordable_height(window_height))


class LogDock:
    """One Activity Log console: its controls, its stream state, its geometry.

    Owns its flet controls across flushes rather than rebuilding them, which is
    the fix for the original complaint as much as it is a performance
    property — see the module docstring.
    """

    def __init__(self, on_fullscreen=None, window_height: float | None = None) -> None:
        self.state = StreamState()
        self._on_fullscreen = on_fullscreen
        self.profiles: frozenset = frozenset()
        # Sized against the window rather than a constant, so a short window
        # does not cost the sidebar its bottom cluster — see default_height.
        self.height = default_height(window_height)
        #: The UNSNAPPED height the drag is accumulating into.
        #:
        #: THE BUG THIS EXISTS TO FIX, measured on the running app: a drag
        #: arrives as ~10px frames, and snapping EACH FRAME to whole rows makes
        #: the gesture asymmetric. Quantizing floors, so growing by 10px from a
        #: 6-row console lands back inside row 6 (186 -> 186) and the console
        #: never grows however far he drags; shrinking by 10px crosses a
        #: boundary immediately (186 -> 164) and it does. Up was dead, down
        #: worked — which reads as the grip being broken again.
        #:
        #: So the raw height accumulates the deltas and the SNAP is applied
        #: only to what is displayed. The console still lands on whole rows;
        #: the gesture is just no longer forced to reach a full row within a
        #: single frame.
        self._raw_height = float(self.height)
        #: The window height the budget is computed against, remembered.
        #:
        #: WHY THE DOCK HAS TO HOLD THIS. ``affordable_height()`` is a function
        #: of the window, and until now only ``apply_window_height()`` ever
        #: called it — because only the resize path was HANDED a window height.
        #: The drag path has no event carrying one, so a grip that wants to
        #: respect the same budget needs the last known value kept here. See
        #: :meth:`drag_by`.
        #:
        #: ``None`` means "not measured" and constrains nothing, which is the
        #: same direction ``affordable_height`` already takes: a headless or
        #: served session that reports no height must not have its console
        #: silently clamped to the minimum.
        self._window_height: float | None = (
            float(window_height) if window_height else None
        )
        #: The height the OPERATOR wants, which is not always the height he can
        #: have. The applied height is min(this, whatever the window can
        #: afford), so a window that shrinks takes height away and a window
        #: that grows back HANDS IT BACK — without a resize ever overwriting a
        #: deliberate drag. See apply_window_height.
        self._desired_height = OPEN_HEIGHT

        #: How many lines the app has EVER produced, as of the last paint. The
        #: dock appends the difference rather than diffing text, so repeated
        #: identical lines cannot confuse it into re-rendering the tail.
        self._seq = 0
        #: True only between a USER scroll notification and the END that closes
        #: it. Auto-scroll's own animation emits UPDATE frames whose position
        #: lags the extent it is animating toward, and reading those as a
        #: gesture made the console pause itself with nobody touching it — see
        #: _on_scroll.
        self._user_scrolling = False

        self.list = ft.ListView(
            controls=[],
            spacing=0,
            padding=ft.Padding.symmetric(vertical=6),
            expand=True,
            # Mirrors the follow decision from the very first frame. Hardcoding
            # True re-armed following on the initial auto-scroll to the bottom,
            # which fired _on_scroll and undid a paused start.
            auto_scroll=self.state.following,
            on_scroll=self._on_scroll,
        )

        # --- collapsed strip pieces ---------------------------------------
        self._pulse = ft.Container(
            width=7, height=7, border_radius=4, bgcolor=COLORS["success"]
        )
        self._peek = ft.Text(
            "waiting for events…",
            size=11,
            color=COLORS["text_dim"],
            font_family=MONO,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.FADE,
        )
        self._counter = ft.Text(
            "", size=10, color=COLORS["accent"], font_family=MONO, no_wrap=True
        )

        # --- "jump to newest" ---------------------------------------------
        self._jump_label = ft.Text(
            "", size=10.5, color="#000000", font_family=MONO, weight=ft.FontWeight.BOLD
        )
        self._jump = ft.Container(
            visible=False,
            bgcolor=COLORS["accent"],
            border_radius=14,
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            on_click=lambda _: self.resume(),
            ink=True,
            tooltip="Back to the newest entry",
            content=ft.Row(
                spacing=6,
                tight=True,
                controls=[
                    self._jump_label,
                    ft.Icon(ft.Icons.ARROW_DOWNWARD, size=12, color="#000000"),
                ],
            ),
        )

        # --- header pieces -------------------------------------------------
        self._chevron = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_DOWN, size=17, color=COLORS["text_sub"]
        )
        self._follow_label = ft.Text(
            "following", size=10, color=COLORS["success"], font_family=MONO
        )
        self._total_label = ft.Text(
            "", size=10, color=COLORS["text_sub"], font_family=MONO
        )
        #: The size readout, in HIS unit. "8 rows" is checkable against what he
        #: can see; "289px" is not. It is also what makes the size controls
        #: legible as a set rather than as two anonymous arrows.
        self._size_label = ft.Text(
            "", size=10, color=COLORS["text_sub"], font_family=MONO
        )

        self.body = ft.Container(expand=True, content=self._stream_pane())
        self.collapsed_strip = ft.Container(
            visible=False, content=self._build_collapsed_strip()
        )
        self.root = ft.Container(content=self._build_root())

    # ------------------------------------------------------------------ UI

    def _header(self) -> ft.Control:
        """The one row the operator uses to control the console.

        Left is IDENTITY and the collapse gesture, right is STATE — whether the
        stream is following, and the way back when it is not. Splitting it that
        way means the control he reaches for and the status he reads are never
        in the same place competing for the same glance.
        """
        title = ft.Row(
            spacing=9,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._chevron,
                ft.Icon(ft.Icons.TERMINAL, size=14, color=COLORS["accent"]),
                ft.Text(
                    "ACTIVITY",
                    size=11,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
                self._total_label,
            ],
        )
        # The whole left cluster is the collapse target, not just the 17px
        # chevron — a comfortable hit area beats a precise one.
        title_hit = ft.Container(
            on_click=lambda _: self.toggle(),
            ink=True,
            border_radius=4,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            tooltip="Collapse the Activity Log",
            content=title,
        )
        right = ft.Row(
            spacing=10,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._jump,
                self._follow_label,
                self._size_controls(),
                ft.IconButton(
                    icon=ft.Icons.OPEN_IN_FULL,
                    icon_size=13,
                    icon_color=COLORS["text_sub"],
                    tooltip="Open full Activity Log",
                    on_click=lambda _: (
                        self._on_fullscreen() if self._on_fullscreen else None
                    ),
                ),
            ],
        )
        return ft.Container(
            padding=ft.Padding.only(left=6, right=8),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[title_hit, right],
            ),
        )

    def _grip(self) -> ft.Control:
        """The draggable top edge — the control that shipped broken.

        WHAT WAS WRONG, AND IT WAS TWO THINGS.

        The user's report was "я не могу ползунком менять высоту активити лога".
        PS-179's own acceptance record had already marked this criterion NOT
        COVERED: the grip is a ``GestureDetector``, which paints NO semantics
        node, so nothing could address it — six coordinate offsets across the
        band each with a real press/move/release moved ``height`` by nothing.
        A criterion recorded as unverified is the one he hit within minutes.

        1. **It was not addressable, so it could not be verified.** The fix is
           the ``tooltip`` below. A tooltip makes Flutter emit a real semantics
           node for the control, which means a pointer — an operator's or a
           driver's — can find the grip and land on it. This is not decoration:
           it is what turns "AC3 could not be driven" into a criterion that is
           demonstrated by a real gesture in the capture.

        2. **The hit area was 9px tall.** Even found, a 9px target is a
           precision task with the window edge nearby. The grip is now
           :data:`GRIP_HIT_HEIGHT` tall — the PAINTED bar stays a thin 3px line
           so the design does not change, but the region that accepts the drag
           is comfortable. A drag target you must aim at is a drag target that
           reads as broken.

        ``on_pan_update`` is kept (it is the gesture that works once the target
        can be hit) and joined by ``on_vertical_drag_update``: Flutter's arena
        can award a clean vertical drag to the vertical recognizer, in which
        case a pan-only detector never fires. Registering both means the grip
        responds to the gesture the user actually makes rather than to the one
        the arena happened to pick.
        """

        def on_drag(e) -> None:
            delta = _drag_delta_y(e)
            if delta is None:
                return
            # Dragging UP (negative delta) grows the console. Through
            # drag_by, NOT set_height: a single frame is smaller than a row,
            # so the delta has to accumulate before it is snapped — see
            # drag_by and _raw_height.
            self.drag_by(delta)

        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN,
            on_pan_update=on_drag,
            on_vertical_drag_update=on_drag,
            content=ft.Container(
                height=GRIP_HIT_HEIGHT,
                bgcolor=COLORS["log_bg"],
                alignment=ft.Alignment.CENTER,
                # The tooltip is what makes the grip exist to a pointer at all
                # — see the docstring. It also finally SAYS what the bar does,
                # which no label ever did.
                tooltip="Drag to resize the Activity Log",
                content=ft.Container(
                    width=54, height=3, border_radius=2, bgcolor="#333333"
                ),
            ),
        )

    def _size_controls(self) -> ft.Control:
        """Smaller / bigger / one line — the size gestures, said out loud.

        THE POINT OF THIS DIRECTION. The grip is fixed and it works (see
        :meth:`_grip`), but a drag handle is a DISCOVERED control: nothing on
        screen says it is there, nothing says what range it has, and the one
        thing he asked for by name — "скрыть до 1 строчки" — is a gesture you
        have to already know about. So the sizes he named become three visible,
        one-click controls, and the drag stays as the continuous way to land
        anywhere between them.

        The readout beside them is in ROWS, which is the unit he used
        ("на 6 строчек"). It is what tells him the console is at the size he
        thinks it is without counting lines.
        """
        def step(rows: int):
            def go(_):
                self.set_rows(self.rows + rows)

            return go

        def button(icon: str, tip: str, on_click) -> ft.Control:
            return ft.Container(
                on_click=on_click,
                ink=True,
                border_radius=3,
                padding=ft.Padding.all(4),
                tooltip=tip,
                content=ft.Icon(icon, size=14, color=COLORS["text_sub"]),
            )

        return ft.Row(
            spacing=2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                button(ft.Icons.REMOVE, "Smaller (one row less)", step(-1)),
                self._size_label,
                button(ft.Icons.ADD, "Bigger (one row more)", step(+1)),
                button(
                    ft.Icons.MINIMIZE,
                    "Shrink to a single line",
                    lambda _: self.set_rows(MIN_ROWS),
                ),
            ],
        )

    @property
    def rows(self) -> int:
        """How many whole log rows the console is currently showing."""
        return rows_for_height(self.height)

    def set_rows(self, rows: int) -> int:
        """Size the console to exactly ``rows`` rows. Returns what it applied.

        The size controls' whole effect, and the reason they can be trusted:
        they move the console in the same unit they report, so "smaller" always
        means exactly one row less rather than an arbitrary pixel step that
        may or may not change what is visible.
        """
        target = max(MIN_ROWS, min(MAX_ROWS_VISIBLE, int(rows)))
        self.set_height(height_for_rows(target))
        return self.rows

    def drag_by(self, delta: float) -> int:
        """Grow (or shrink) the console by one drag frame's worth of movement.

        THE ACCUMULATOR IS THE WHOLE POINT — see :attr:`_raw_height`. A drag
        arrives as ~10px frames, and a row is 22px, so no single frame is a
        whole row. Feeding each frame through the snap independently made
        growing impossible (10px up floors straight back to the row it started
        in) while shrinking worked, which is precisely the "the grip does
        nothing" report. Accumulating first and snapping the RESULT means every
        frame counts, and the console still only ever rests on whole rows.

        THE RAIL'S BUDGET BOUNDS THE DRAG, AND THAT IS A DELIBERATE OWNER
        DECISION rather than an implementation detail. Two paths could change
        this dock's height and only ONE of them respected the sidebar's
        budget:

            window resize -> apply_window_height() -> affordable_height()  ✅
            pointer drag  -> drag_by()             -> MAX_HEIGHT only      ❌

        Measured at the app minimum (a 680px window): the dock opened at 120px
        leaving 560px for the rail, but the grip would happily drag it to
        494px — leaving 186px for a fixed cluster that needs 560px, starving it
        by 374px. Then any window event silently healed it, which is why this
        was reported as "the ACTIVITY panel wrecks the layout generally"
        rather than as a reproducible step. It is also the whole of Mars's
        animation report: elements animate inside a rail whose height is being
        rewritten underneath them mid-gesture.

        THE TRADE, AND WHO MADE IT. Respecting the budget here bounds the
        owner's own "the size is his, continuously" requirement — on a 680px
        window the grip now stops at roughly 3 rows rather than 20. That was
        put to the owner rather than chosen silently, and his answer was
        explicit: "боковая панель важнее — ограничить лог". The sidebar rail
        wins; the dock's drag is bounded by the rail's budget. So the
        requirement now reads "continuously, from 1 row up to whatever the
        window affords". The cap is NOT a defect to work around and NOT a
        number to quietly raise.

        Returns the row count now displayed, so a caller can assert on it.
        """
        # The SAME budget the resize path uses, from the same function — the
        # fault was never that the budget was wrong, only that this call site
        # did not consult it. Never below one row: a window too short to
        # afford even that still owes the operator a usable console.
        ceiling = float(max(MIN_HEIGHT, affordable_height(self._window_height)))
        self._raw_height = max(
            float(MIN_HEIGHT),
            min(ceiling, self._raw_height - float(delta)),
        )
        self._apply_height(quantize(self._raw_height))
        return self.rows

    def set_height(self, height: float) -> None:
        """Apply a new open height, SNAPPED to whole rows and clamped.

        The size controls' and the default's path. A DRAG does not come through
        here — it goes through :meth:`drag_by`, which accumulates — because
        snapping a per-frame delta is what broke the gesture.

        THE SNAP IS THE DIRECTION'S ARGUMENT. A console sized in pixels lands
        mid-row, showing five rows and a two-pixel sliver of a sixth; that
        sliver is exactly the "пустое место" he reported, and it reappears on
        every drag no matter how good the default is. Quantizing means the
        bottom edge always falls where a row ends, so the console is full of
        log at every size he can put it in — and a drag reads out as "8 rows"
        rather than as 289 pixels.
        """
        snapped = quantize(height)
        # Keep the accumulator in step, so a click on "smaller" followed by a
        # drag continues from where the button left the console rather than
        # from wherever the last drag happened to end.
        self._raw_height = float(snapped)
        self._apply_height(snapped)

    def _apply_height(self, height: int) -> None:
        """Put an already-snapped height on screen."""
        self.height = height
        # A height the operator chose HIMSELF is what he wants back when the
        # window has room again — so the grip moves the desire, not just the
        # applied value. apply_window_height moves only the applied one.
        self._desired_height = self.height
        self.body.height = self.height
        self._paint_size()
        # ONE update, and it is the BODY. Two other things were tried here and
        # both broke the gesture, so this is measured rather than chosen:
        #
        #   * updating `self.root` re-materialises the whole console INCLUDING
        #     the grip, and replacing a GestureDetector mid-gesture makes
        #     Flutter's arena drop the drag it is tracking — 0px of travel;
        #   * updating `self._size_label` as well (it lives inside the body,
        #     which is already being updated) produced the same 0px, because
        #     the second patch lands while the first is still being applied.
        #
        # The label is a child of the body, so one body update carries both the
        # new height and the new readout.
        self._safe_update(self.body)

    def _paint_size(self) -> None:
        n = self.rows
        self._size_label.value = f"{n} row" if n == 1 else f"{n} rows"

    def apply_window_height(self, window_height: float | None) -> int:
        """Re-apply the rail budget for a window that just changed size.

        THE FAULT THIS EXISTS FOR: the budget used to be computed exactly once,
        at build time, from the startup window height. The app opens at
        1280x820 and its minimum is 1024x680, so dragging DOWN to the minimum
        is an ordinary supported gesture — and it left a 236px dock in a 680px
        window, i.e. 444px for a rail that needs 560px. The rail then starved
        by 116px exactly as it did before any of this was fixed, only reached
        through the resize path instead of the launch path. AC8 is a property
        of the window SIZE, not of how the window got to that size.

        Yields height when the window cannot afford the current one, and hands
        it back (never past what he asked for) when it can — so an operator's
        deliberate 400px drag survives a shrink-and-restore round trip instead
        of being permanently rewritten to the opening default.

        Returns the applied height so a caller can assert on it.
        """
        # REMEMBERED, so the GRIP can consult the same budget. drag_by has no
        # event carrying a window height, so without this the drag would keep
        # sizing itself against whatever the window measured at LAUNCH — and an
        # operator who shrank the window and then dragged would starve exactly
        # the rail this budget exists to protect. See drag_by.
        if window_height and float(window_height) > 0:
            self._window_height = float(window_height)
        allowed = affordable_height(window_height)
        target = min(self._desired_height, allowed)
        desired = self._desired_height
        self.height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(target)))
        # Deliberately NOT through set_height: this is the window speaking, not
        # the operator, and it must not overwrite what he asked for.
        self._desired_height = desired
        self.body.height = self.height
        self._safe_update(self.body)
        return self.height

    def _build_collapsed_strip(self) -> ft.Control:
        """Collapsed, but NOT off.

        Carries the newest line, a pulse coloured by that event's severity, and
        a count of what arrived while it was shut — so the strip answers "is
        anything still happening?" without being reopened, which is the whole
        point of being able to collapse it.
        """
        return ft.Container(
            height=COLLAPSED_HEIGHT,
            bgcolor=COLORS["log_bg"],
            border=ft.Border.only(top=ft.BorderSide(1, COLORS["border"])),
            padding=ft.Padding.only(left=14, right=10),
            content=ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        on_click=lambda _: self.toggle(),
                        ink=True,
                        border_radius=4,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=5),
                        tooltip="Expand the Activity Log",
                        content=ft.Row(
                            spacing=8,
                            tight=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(
                                    ft.Icons.KEYBOARD_ARROW_UP,
                                    size=16,
                                    color=COLORS["text_sub"],
                                ),
                                self._pulse,
                            ],
                        ),
                    ),
                    ft.Container(width=4),
                    ft.Container(expand=True, content=self._peek),
                    self._counter,
                ],
            ),
        )

    def _stream_pane(self) -> ft.Control:
        return ft.Container(
            expand=True,
            content=ft.Column(
                spacing=0,
                controls=[
                    self._header(),
                    ft.Container(expand=True, content=self.list),
                ],
            ),
        )

    def _build_root(self) -> ft.Control:
        self.body.height = self.height
        # The readout must be right on the FIRST frame, not only after the
        # first resize — it is part of how the console explains its own size.
        self._paint_size()
        return ft.Container(
            bgcolor=COLORS["log_bg"],
            border=ft.Border.only(top=ft.BorderSide(1, COLORS["border"])),
            content=ft.Column(
                spacing=0,
                controls=[self._grip(), self.body, self.collapsed_strip],
            ),
        )

    # ------------------------------------------------------------- behaviour

    def _safe_update(self, control) -> None:
        try:
            control.update()
        except Exception:
            pass

    def _on_scroll(self, e) -> None:
        """The follow/pause decision, in one handler.

        A scroll AWAY from the bottom is read as "I am reading" and stops the
        follow; a scroll back to the bottom resumes it. ``auto_scroll`` is
        flipped in step, because that is the flet property that actually yanks
        the viewport when an event lands.

        ONLY A USER GESTURE MAY CHANGE THE DECISION, and that qualifier is the
        whole correctness of this handler rather than a detail of it. Measured
        against the running app: appending a row grows ``max_scroll_extent``
        IMMEDIATELY while ``auto_scroll`` ANIMATES ``pixels`` toward the new
        bottom, so every frame of that animation reports a position well short
        of the end — e.g. ``pixels=1048`` against ``max=1334``. A position test
        alone reads that as "he scrolled up" and pauses the stream, so with
        events arriving every ~0.35s the console paused ITSELF within seconds
        of the first paint, showing "paused — reading" and a rising "N new" to
        an operator who had touched nothing. 372 such notifications arrived in
        14 idle seconds.

        Flet distinguishes the two: a real wheel or drag emits a ``USER``
        notification (carrying a direction) before the ``UPDATE`` that applies
        it, and the whole sequence closes with ``END``. Programmatic
        auto-scrolling emits ``START``/``UPDATE``/``END`` with no ``USER`` at
        all. So a ``USER`` event ARMS the decision, the ``UPDATE`` that follows
        makes it against a real position, and ``END`` disarms it again — which
        leaves the animation unable to speak for the operator.
        """
        etype = getattr(e, "event_type", None)
        kind = str(getattr(etype, "value", etype) or "").lower()

        if kind == "user":
            # A real gesture is starting. Its own `pixels` is the position
            # BEFORE the gesture applies, so nothing is decided here — this
            # only arms the UPDATE that carries the new one.
            self._user_scrolling = True
            return
        if kind == "end":
            self._user_scrolling = False
            return
        if kind != "update" or not self._user_scrolling:
            return

        try:
            pixels = float(getattr(e, "pixels", 0.0) or 0.0)
            extent = float(getattr(e, "max_scroll_extent", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        # A list with nothing to scroll reports extent 0, and "0 >= 0 - slack"
        # is trivially at-the-bottom — so an un-scrollable list would re-arm
        # following on every layout pass. Nothing to scroll is not a decision.
        if extent <= 0.0:
            return
        if self.state.on_scroll(pixels, extent):
            self.list.auto_scroll = self.state.following
            if self.state.following:
                self._apply_trim()
            self._paint_stream_state()
            self._safe_update(self.root)

    def resume(self) -> None:
        """Back to the newest entry — the explicit way home."""
        self.state.resume()
        self.list.auto_scroll = True
        self._apply_trim()
        self._paint_stream_state()
        self._safe_update(self.root)

    def toggle(self) -> None:
        self.state.collapsed = not self.state.collapsed
        if not self.state.collapsed:
            # Reopening is a return to the tail: the counter has done its job.
            self.state.missed = 0
            self.state.following = True
            self.list.auto_scroll = True
            # The height the operator chose with the grip is deliberately NOT
            # reset here — a collapse/expand round trip returns his console,
            # not the default one.
            self.body.height = self.height
        self._apply_collapse()
        self._paint_stream_state()
        self._safe_update(self.root)

    def _apply_collapse(self) -> None:
        collapsed = self.state.collapsed
        self.body.visible = not collapsed
        self.collapsed_strip.visible = collapsed
        self._chevron.icon = (
            ft.Icons.KEYBOARD_ARROW_UP if collapsed else ft.Icons.KEYBOARD_ARROW_DOWN
        )

    def _paint_stream_state(self) -> None:
        paused = not self.state.following
        self._jump.visible = paused
        n = self.state.missed
        self._jump_label.value = f"{n} new" if n else "jump to newest"
        self._follow_label.value = "paused — reading" if paused else "following"
        self._follow_label.color = COLORS["warning"] if paused else COLORS["success"]
        self._total_label.value = f"· {self.state.total} events"
        self._counter.value = f"+{n}" if n else ""
        # Classify the SAME TEXT THE ROW DOES, not the raw stored line. The row
        # renders parse_event()'s message ("Session ended"); the raw line still
        # carries its timestamp ("10:00:04  > Session ended: mail-us-011"), and
        # severity() has anchored rules — `startswith("session ended")` — that
        # a leading timestamp silently defeats. Classifying the raw line made
        # the collapsed pulse report IDLE/grey for an event the open console
        # painted FAIL/red. Collapsed is the one state where the pulse is the
        # only signal the operator gets, so it must not disagree with the row
        # it stands for. Any future anchored rule would break the same way.
        _, _, _, sev = parse_event(self.state.last_line, self.profiles)
        self._pulse.bgcolor = SEV_COLOR.get(sev, COLORS["success"])

    # ----------------------------------------------------------------- feed

    def set_profiles(self, names) -> None:
        self.profiles = frozenset(names or ())

    def _apply_trim(self) -> None:
        """Drop rows above :data:`MAX_ROWS`, but never under a reader.

        Removing rows from the front shifts everything below them, which is
        precisely the "my scroll position moved" complaint. So while the stream
        is paused the list is allowed to overrun its cap, and the trim happens
        when the operator returns to the tail — where a shift is invisible
        because he is pinned to the bottom anyway.
        """
        if not self.state.following:
            return
        excess = len(self.list.controls) - MAX_ROWS
        if excess > 0:
            del self.list.controls[:excess]

    def render(self, lines: list[str], seq: int | None = None) -> None:
        """Paint the console from the current tail — by APPENDING.

        ``lines`` is the retained tail and ``seq`` is how many lines the app has
        ever produced. The difference between ``seq`` and the last one painted
        is exactly how many rows are new, so this appends that many and leaves
        every existing row — and therefore every scroll position — untouched.
        Diffing by seq rather than by text is what makes a repeated identical
        line (two profiles failing the same way) append once rather than
        re-render the tail.

        A ``seq`` that went BACKWARDS means the ring was cleared underneath us
        (the panic wipe does this), which is the one case that legitimately
        rebuilds the whole list.
        """
        if seq is None:
            seq = self._seq + max(0, len(lines) - len(self.list.controls))

        rebuild = seq < self._seq or not self.list.controls
        arrived = 0

        if rebuild:
            self.list.controls = [event_row(ln, self.profiles) for ln in lines]
            arrived = len(lines)
        else:
            new = seq - self._seq
            if new > 0:
                # Never take more than the tail actually holds: the ring drops
                # old lines, so a burst larger than the retained tail means the
                # dropped ones are simply not available to paint.
                fresh = lines[-new:] if new < len(lines) else list(lines)
                self.list.controls.extend(
                    event_row(ln, self.profiles) for ln in fresh
                )
                arrived = len(fresh)

        self._seq = seq
        self.state.total = seq

        if arrived:
            self.state.last_line = lines[-1] if lines else self.state.last_line
            if not self.state.following or self.state.collapsed:
                self.state.missed += arrived
        elif lines:
            self.state.last_line = lines[-1]

        self._apply_trim()

        if lines:
            stamp, _, msg = lines[-1].partition("  > ")
            stamp, msg = stamp.strip(), msg.strip()
            self._peek.value = f"{stamp}   {msg}" if stamp else (msg or lines[-1])

        self._apply_collapse()
        self._paint_stream_state()

    # Kept so a caller can ask what the console is actually holding — the
    # scrollback depth is an acceptance criterion, so it needs to be readable
    # rather than inferred from the widget tree.
    @property
    def row_count(self) -> int:
        return len(self.list.controls)

    @property
    def scrollback_px(self) -> int:
        return self.row_count * ROW_HEIGHT
