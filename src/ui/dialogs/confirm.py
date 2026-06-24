from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ..theme.colors import COLORS
from ..theme.styles import ERROR_STYLE, MONO, OUTLINE_STYLE


def open_confirm_dialog(
    page: ft.Page,
    profile_name: str,
    on_confirm: Callable[[], None],
    *,
    title: str | None = None,
    body: str | None = None,
) -> None:

    def _on_confirm(_: ft.ControlEvent) -> None:
        on_confirm()
        page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text(
            title
            if title is not None
            else get_string("confirm_delete_msg", name=profile_name),
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Text(
            body if body is not None else "This action cannot be undone.",
            size=13,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                height=38,
                style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ delete ]",
                height=38,
                style=ERROR_STYLE,
                on_click=_on_confirm,
            ),
        ],
    )
    page.show_dialog(dlg)
