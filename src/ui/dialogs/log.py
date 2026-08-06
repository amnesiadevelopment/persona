import flet as ft

from ..log_format import log_message_color
from ..theme.colors import COLORS
from ..theme.styles import MONO


def _log_row(line: str) -> ft.Control:
    """One terminal-style log row for the fullscreen view: a fixed-width dim
    timestamp column, a thin colour bar keyed to the line's type, then the
    message in that colour. The aligned timestamp column and the left bar give
    the wall of lines a scannable structure the raw sidebar text lacked."""
    stamp, sep, rest = line.partition("  > ")
    if not sep:
        stamp, rest = "", line
    colour = log_message_color(rest)
    return ft.Row(
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.START,
        controls=[
            ft.Container(
                width=90,
                content=ft.Text(
                    stamp, size=12, color=COLORS["text_dim"], font_family=MONO,
                ),
            ),
            ft.Container(
                width=3,
                height=16,
                bgcolor=colour,
                border_radius=2,
                margin=ft.Margin.only(right=12),
            ),
            ft.Text(
                rest,
                size=12,
                color=colour,
                font_family=MONO,
                selectable=True,
                expand=True,
            ),
        ],
    )


def open_log_dialog(page: ft.Page, log_lines: list[str]) -> None:
    rows: list[ft.Control] = (
        [_log_row(ln) for ln in log_lines]
        if log_lines
        else [ft.Text("No activity yet.", size=12, color=COLORS["text_dim"], font_family=MONO)]
    )

    header = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.TERMINAL, size=18, color=COLORS["accent"]),
                    ft.Text(
                        "Activity Log",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["accent"],
                        font_family=MONO,
                    ),
                ],
            ),
            ft.Text(
                f"{len(log_lines)} entries",
                size=12,
                color=COLORS["text_dim"],
                font_family=MONO,
            ),
        ],
    )

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        content=ft.Container(
            width=1000,
            height=600,
            border_radius=4,
            bgcolor=COLORS["log_bg"],
            padding=ft.Padding.symmetric(horizontal=20, vertical=16),
            content=ft.Column(
                expand=True,
                spacing=0,
                controls=[
                    header,
                    ft.Container(
                        height=1,
                        bgcolor=COLORS["border"],
                        margin=ft.Margin.symmetric(vertical=12),
                    ),
                    # A ListView (not a Column) takes the wheel and drag scroll.
                    # The user browses history, so don't auto-pin to the bottom.
                    ft.ListView(controls=rows, expand=True, spacing=6),
                ],
            ),
        ),
        actions=[
            ft.TextButton("Close", on_click=lambda _: page.pop_dialog()),
        ],
    )
    page.show_dialog(dlg)
