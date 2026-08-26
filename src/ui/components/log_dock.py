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
    severity,
)
from ..theme.colors import COLORS

MONO = "monospace"

#: Height of the open console. Sits between the clamps below, and is what the
#: operator gets before he ever touches the grip.
OPEN_HEIGHT = 236

#: The grip's clamps. Below MIN the console cannot show enough rows to be worth
#: reading; above MAX it starts eating the page it is supposed to sit under.
MIN_HEIGHT = 120
MAX_HEIGHT = 520

#: The collapsed strip: tall enough for one line of live text and nothing else.
COLLAPSED_HEIGHT = 34

#: How many rows the console keeps painted. Materially more than the 6 the old
#: panel managed — at ROW_HEIGHT this is ~13000px of scrollback against a
#: console that is at most 520px tall, so there is genuinely something to
#: scroll. Bounded because every row is a live flet control.
MAX_ROWS = 600


class LogDock:
    """One Activity Log console: its controls, its stream state, its geometry.

    Owns its flet controls across flushes rather than rebuilding them, which is
    the fix for the original complaint as much as it is a performance
    property — see the module docstring.
    """

    def __init__(self, on_fullscreen=None) -> None:
        self.state = StreamState()
        self._on_fullscreen = on_fullscreen
        self.profiles: frozenset = frozenset()
        self.height = OPEN_HEIGHT

        #: How many lines the app has EVER produced, as of the last paint. The
        #: dock appends the difference rather than diffing text, so repeated
        #: identical lines cannot confuse it into re-rendering the tail.
        self._seq = 0
        #: Trimming the front of the list moves every row below it. That is
        #: exactly what must not happen under someone who is reading, so an
        #: overflowing list is left overflowing until the follow resumes.
        self._trim_deferred = False
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
        """The draggable top edge.

        A real ``on_pan_update``: dragging up grows the console, dragging down
        shrinks it, clamped so it can neither vanish nor eat the page. The
        visible grip is a short bar because an invisible drag target is a
        feature nobody finds.
        """

        def on_drag(e: ft.DragUpdateEvent) -> None:
            self.set_height(self.height - e.delta_y)

        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN,
            on_pan_update=on_drag,
            content=ft.Container(
                height=9,
                bgcolor=COLORS["log_bg"],
                alignment=ft.Alignment.CENTER,
                content=ft.Container(
                    width=54, height=3, border_radius=2, bgcolor="#333333"
                ),
            ),
        )

    def set_height(self, height: float) -> None:
        """Apply a new open height, clamped. The grip's whole effect.

        Kept separate from the drag handler so the clamp is one testable thing
        rather than a expression buried in a gesture callback.
        """
        self.height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(height)))
        self.body.height = self.height
        self._safe_update(self.body)

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
        sev = severity(self.state.last_line)
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
            self._trim_deferred = len(self.list.controls) > MAX_ROWS
            return
        excess = len(self.list.controls) - MAX_ROWS
        if excess > 0:
            del self.list.controls[:excess]
        self._trim_deferred = False

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
