"""After-update "what's new" dialog (#215): a short bullet list of this
version's changes, shown once when the app starts on a newer build.
"""
from collections.abc import Callable

import flet as ft

from ..theme.colors import COLORS
from ..theme.styles import MONO


def open_changelog_dialog(
    page: ft.Page,
    version: str,
    notes: list[str],
    on_dismiss: Callable[[], None] | None = None,
) -> None:
    def _dismiss(_: ft.ControlEvent | None = None) -> None:
        page.pop_dialog()
        if on_dismiss is not None:
            on_dismiss()

    bullets = ft.Column(
        spacing=10,
        tight=True,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Icon(ft.Icons.CHECK, size=16, color=COLORS["accent"]),
                    ft.Text(
                        note,
                        size=12,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                        no_wrap=False,
                        expand=True,
                    ),
                ],
            )
            for note in notes
        ],
    )

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text(
            f"What's new in {version}",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(width=460, content=bullets),
        actions=[
            ft.Button(
                "[ got it ]",
                height=38,
                on_click=_dismiss,
                style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#000000"),
            ),
        ],
    )
    page.show_dialog(dlg)
