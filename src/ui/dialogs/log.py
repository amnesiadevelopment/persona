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

The row itself is deliberately the SAME :func:`event_row` the dock uses. One
row renderer means the log he scans in the dock and the log he searches at full
screen have their columns in the same places, so his eye does not have to
re-learn the layout when he opens it.
"""

from __future__ import annotations

import flet as ft

from ..log_console import SEV_COLOR, SEV_FAIL, SEV_INFO, SEV_OK, event_row, parse_event
from ..theme.colors import COLORS

MONO = "monospace"

#: The severity filters offered, in the order they are shown. "all" is not a
#: severity — it is the cleared state, and it is first because it is the one
#: the view opens in.
_FILTERS = (
    ("all", "all"),
    (SEV_FAIL, "failures"),
    (SEV_OK, "ok"),
    (SEV_INFO, "info"),
)


def open_log_dialog(page: ft.Page, log_lines: list[str], profiles=None) -> None:
    """Open the Activity Log at full size, with the tools that size deserves."""
    profiles = frozenset(profiles or ())
    state = {"sev": "all", "profile": "", "query": ""}

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

    def repaint(_=None) -> None:
        rows = matching()
        body.controls = (
            [event_row(ln, profiles) for ln in rows]
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
    win_w = getattr(page, "width", None) or 1280
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
