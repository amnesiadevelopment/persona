"""PS-1 round 2 — the Activity Log console, in three PLACEMENTS.

The owner kept direction A's bottom placement and asked for three genuinely
different answers to *where the log lives and how big it is*, with the same
three behaviours in all of them. So the behaviour lives here once and the
three variants differ only in geometry:

``D`` — **Full-width dock.** The log spans the whole window under the sidebar
and the page, and its top edge is draggable. Widest reading line of the three;
costs page height while open.

``E`` — **Split dock.** The same bottom strip, shared with a live run digest
on the right. The stream gives up ~300px of width to a standing answer to
"what is happening overall", so the operator does not have to reconstruct it
by reading the stream.

``F`` — **Overlay sheet.** The log floats OVER the page instead of displacing
it, anchored bottom-right. The page never loses a pixel of height; the sheet
covers content while open, and collapses to a slim bar on the same edge.

Three behaviours, identical in all three (the round-1 must-haves):

1. **Collapsible.** Every variant has a chevron, and every collapsed state is
   a LIVE strip — newest event, an arrival counter and a pulse — so collapsing
   is not the same as switching the log off.
2. **A scannable row.** Delegated to ``log_console.event_row``; the variants
   pass the column order that suits their width.
3. **Scroll that behaves.** Follow while at the bottom, stop on scroll-up,
   and an explicit "N new ↓" control back to the tail. ``auto_scroll`` is
   turned OFF the moment the operator scrolls up, which is what stops new
   events from yanking the page out from under a line he is reading.
"""

from __future__ import annotations

import os

import flet as ft

from ..log_console import SEV_COLOR, SEV_FAIL, StreamState, event_row, severity
from ..theme.colors import COLORS

MONO = "monospace"

#: Where the capture harness writes which placement + state to render.
#: A FILE rather than an env var, and read at CONSTRUCTION rather than at
#: import, for one practical reason: flet calls ``App._main`` per browser
#: SESSION, so a page reload rebuilds the console. Reading here means the
#: three placements and their collapsed / paused states can all be captured
#: from ONE running server by reloading, instead of a ~70s app restart per
#: frame. Throwaway viz-branch scaffolding; nothing production reads it.
CONTROL_FILE = os.getenv("PS1_VIZ_CONTROL", "/tmp/ps1-viz-control.json")


def capture_control() -> tuple:
    """(placement, start_state) for this render — defaults to ("D", "")."""
    variant, state = os.getenv("PS1_VIZ_VARIANT", "E"), ""
    try:
        import json

        with open(CONTROL_FILE) as fh:
            data = json.load(fh)
        variant = str(data.get("variant", variant)) or variant
        state = str(data.get("state", "") or "")
    except Exception:
        pass
    return variant, state


#: Kept so an importer reading a module-level value still gets a sane answer.
VARIANT = capture_control()[0]

#: Open heights. Chosen per variant rather than shared, because these ARE the
#: design difference the owner is comparing.
OPEN_HEIGHT = {"D": 236, "E": 236, "F": 320}

#: The collapsed strip. Deliberately the same in all three: the cost of a
#: collapsed log must not be one of the variables under comparison.
COLLAPSED_HEIGHT = 34


class LogDock:
    """One Activity Log console: its controls, its stream state, its geometry.

    Holds the flet controls rather than rebuilding them per flush — which is
    also the fix for the original complaint. The old panel replaced its whole
    child list on every flush, so any scroll position the operator had
    established pointed at children that no longer existed. Here the ListView
    is built once and events are APPENDED, so a scroll position survives the
    next event.
    """

    def __init__(self, variant: str = "", on_fullscreen=None) -> None:
        _variant, _start = capture_control()
        self.variant = variant or _variant
        self.state = StreamState()
        # CAPTURE AID (throwaway viz branch only). Start the console in a given
        # state so a still frame can show the collapsed strip or the
        # paused/jump-to-newest state without scripting a wheel gesture into a
        # Flutter canvas — which paints to a <canvas>, so there is no scrollable
        # DOM element to drive. Nothing is faked in the render: this sets the
        # REAL state object the real widgets paint from, and the same state is
        # reached by hand via toggle() or by scrolling.
        if _start == "collapsed":
            self.state.collapsed = True
        elif _start == "paused":
            self.state.following = False
            self.state.missed = 7
        self._on_fullscreen = on_fullscreen
        self.profiles: frozenset = frozenset()
        self.height = OPEN_HEIGHT.get(self.variant, 236)

        self.row_layout = {
            "D": "profile_first",
            "E": "status_first",
            "F": "profile_first",
        }.get(variant, "profile_first")

        self.list = ft.ListView(
            controls=[],
            spacing=0,
            padding=ft.Padding.symmetric(vertical=6),
            expand=True,
            # Mirrors the follow decision from the very first frame. Hardcoding
            # True here re-armed following on the initial auto-scroll to the
            # bottom, which fired _on_scroll and undid a paused start.
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
            content=ft.Row(
                spacing=6,
                tight=True,
                controls=[
                    ft.Icon(ft.Icons.ARROW_DOWNWARD, size=12, color="#000000"),
                    self._jump_label,
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

        # --- digest (variant E only) ---------------------------------------
        self.digest = ft.Column(spacing=6, controls=[])

        self.body = ft.Container(expand=True, content=self._build_body())
        self.collapsed_strip = ft.Container(
            visible=False, content=self._build_collapsed_strip()
        )
        self.root = ft.Container(content=self._build_root())

    # ------------------------------------------------------------------ UI

    def _header(self) -> ft.Control:
        """The one row the operator uses to control the console.

        Left is IDENTITY and the collapse gesture, right is STATE — whether
        the stream is following, and the way back when it is not. Splitting it
        that way means the control he reaches for and the status he reads are
        never in the same place competing for the same glance.
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
        """The draggable top edge (must-have 1's "if it fits" half).

        A real ``on_pan_update``: dragging up grows the console, dragging down
        shrinks it, clamped so it can neither vanish nor eat the page. The
        visible grip is a short bar because an invisible drag target is a
        feature nobody finds.
        """

        def on_drag(e: ft.DragUpdateEvent) -> None:
            self.height = max(120, min(520, self.height - e.delta_y))
            self.body.height = self.height
            self._safe_update(self.body)

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

    def _build_collapsed_strip(self) -> ft.Control:
        """Collapsed, but NOT off.

        Carries the newest line, a pulse and a count of what arrived while it
        was shut — so the strip answers "is anything still happening?" without
        being reopened, which is the whole point of collapsing it.
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
                spacing=0, controls=[self._header(), ft.Container(expand=True, content=self.list)]
            ),
        )

    def _digest_pane(self) -> ft.Control:
        """Variant E's right-hand column: the standing answer.

        The stream tells you what JUST happened; this tells you where things
        STAND. It is the reason E is a different design and not a narrower D —
        the operator trades reading width for not having to reconstruct
        overall state from the scrollback.
        """
        return ft.Container(
            width=286,
            bgcolor="#050505",
            border=ft.Border.only(left=ft.BorderSide(1, COLORS["border"])),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Text(
                        "THIS SESSION",
                        size=10,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                    self.digest,
                ],
            ),
        )

    def _build_body(self) -> ft.Control:
        if self.variant == "E":
            inner = ft.Row(
                spacing=0,
                expand=True,
                controls=[self._stream_pane(), self._digest_pane()],
            )
        else:
            inner = self._stream_pane()
        return inner

    def _build_root(self) -> ft.Control:
        self.body.height = self.height
        if self.variant == "F":
            # An overlay: rounded, bordered and inset, so it reads as floating
            # ABOVE the page rather than as another band of chrome welded to
            # the window edge. Placement into the Stack is the caller's job.
            return ft.Container(
                bgcolor="#0A0A0A",
                border=ft.Border.all(1, "#2A2A2A"),
                border_radius=10,
                padding=0,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Column(
                    spacing=0,
                    controls=[self._grip(), self.body, self.collapsed_strip],
                ),
            )
        return ft.Container(
            bgcolor=COLORS["log_bg"],
            border=ft.Border.only(top=ft.BorderSide(1, COLORS["border"])),
            content=ft.Column(
                spacing=0, controls=[self._grip(), self.body, self.collapsed_strip]
            ),
        )

    # ------------------------------------------------------------- behaviour

    def _safe_update(self, control) -> None:
        try:
            control.update()
        except Exception:
            pass

    def _on_scroll(self, e) -> None:
        """The whole of must-have 3, in one handler.

        A scroll AWAY from the bottom is read as "I am reading" and stops the
        follow; a scroll back to the bottom resumes it. ``auto_scroll`` is
        flipped in step, because that is the flet property that actually
        yanks the viewport when an event lands.
        """
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
            self._paint_stream_state()

    def resume(self) -> None:
        """Back to the newest entry — the explicit way home."""
        self.state.resume()
        self.list.auto_scroll = True
        self._paint_stream_state()
        self._safe_update(self.root)

    def toggle(self) -> None:
        self.state.collapsed = not self.state.collapsed
        if not self.state.collapsed:
            # Reopening is a return to the tail: the counter has done its job.
            self.state.missed = 0
            self.state.following = True
            self.list.auto_scroll = True
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
        self._follow_label.color = (
            COLORS["warning"] if paused else COLORS["success"]
        )
        self._total_label.value = f"· {self.state.total} events"
        self._counter.value = f"+{n}" if n else ""
        sev = severity(self.state.last_line)
        self._pulse.bgcolor = SEV_COLOR.get(sev, COLORS["success"])

    # ----------------------------------------------------------------- feed

    def set_profiles(self, names) -> None:
        self.profiles = frozenset(names or ())

    def render(self, lines: list[str], digest_rows=None) -> None:
        """Paint the console from the current tail.

        Rebuilds the row list (the app's own flush contract), but the LISTVIEW
        is the same object across flushes and its ``auto_scroll`` reflects the
        follow decision — so an operator who has scrolled up is not dragged
        back to the bottom by the next arrival.
        """
        prev = self.state.total
        self.state.total = len(lines)
        arrived = max(0, self.state.total - prev)
        if arrived:
            self.state.on_events(0, lines[-1] if lines else "")
            if not self.state.following or self.state.collapsed:
                self.state.missed += arrived
        elif lines:
            self.state.last_line = lines[-1]

        self.list.controls = [
            event_row(ln, self.profiles, self.row_layout) for ln in lines
        ]
        if lines:
            stamp = lines[-1].partition("  > ")[0].strip()
            msg = lines[-1].partition("  > ")[2].strip()
            self._peek.value = f"{stamp}   {msg}" if stamp else msg

        if self.variant == "E":
            # The digest is keyed on the profiles this console KNOWS (set from
            # the real profile manager via set_profiles), not on a per-call
            # argument — the app's flush has no reason to carry a second copy
            # of the roster, and passing none left the digest permanently
            # empty ("no profile activity yet" beside a stream full of named
            # events).
            self.digest.controls = self._digest_controls(
                list(digest_rows or self.profiles), lines
            )
        self._apply_collapse()
        self._paint_stream_state()

    def _digest_controls(self, profiles: list[str], lines: list[str]) -> list:
        """Variant E's digest: per-profile standing state, newest first.

        Derived from the stream itself rather than from a new data source —
        this is a layout draft, so the digest has to be honest about being a
        projection of the same events, not a promise of new telemetry.
        """
        latest: dict[str, str] = {}
        for ln in lines:
            msg = ln.partition("  > ")[2].strip()
            for name in profiles:
                if name and name in msg:
                    latest[name] = msg
        rows = []
        for name, msg in list(latest.items())[-6:]:
            sev = severity(msg)
            rows.append(
                ft.Row(
                    spacing=9,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Container(
                            width=7,
                            height=7,
                            border_radius=4,
                            bgcolor=SEV_COLOR[sev],
                            margin=ft.Margin.only(top=4),
                        ),
                        ft.Container(
                            expand=True,
                            content=ft.Column(
                                spacing=1,
                                controls=[
                                    ft.Text(
                                        name,
                                        size=11,
                                        color=COLORS["accent"],
                                        font_family=MONO,
                                    ),
                                    # Wraps rather than clamps: a longer
                                    # status makes this card taller.
                                    ft.Text(
                                        msg,
                                        size=10,
                                        color=(
                                            COLORS["error"]
                                            if sev == SEV_FAIL
                                            else COLORS["text_dim"]
                                        ),
                                        font_family=MONO,
                                    ),
                                ],
                            ),
                        ),
                    ],
                )
            )
        return rows or [
            ft.Text(
                "no profile activity yet",
                size=10,
                color=COLORS["text_sub"],
                font_family=MONO,
            )
        ]
