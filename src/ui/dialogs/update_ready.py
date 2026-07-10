from collections.abc import Callable

import flet as ft

from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO, OUTLINE_STYLE


def open_update_ready_dialog(
    page: ft.Page,
    tag: str,
    on_install: Callable[[], None],
) -> None:
    """A verified update is downloaded and staged — ask before installing."""

    def _install(_: ft.ControlEvent) -> None:
        page.pop_dialog()
        on_install()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text(
            f"Update {tag} ready",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Text(
            "The new version is downloaded and verified.\n"
            "Install it and restart persona now?",
            size=13,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
        actions=[
            ft.OutlinedButton(
                "[ later ]",
                height=38,
                style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ install now ]",
                height=38,
                style=ACCENT_STYLE,
                on_click=_install,
            ),
        ],
    )
    page.show_dialog(dlg)
