from collections.abc import Callable

import flet as ft

from ..theme.colors import COLORS
from ..theme.styles import ERROR_STYLE, MONO, OUTLINE_STYLE

_CONFIRM_WORD = "DELETE"


def open_wipe_confirm_dialog(
    page: ft.Page,
    profile_count: int,
    on_confirm: Callable[[], None],
) -> None:
    """Typed-confirmation gate for the panic wipe. Deleting every profile is
    irreversible (each data dir is rmtree'd), so the destructive button stays
    disabled until the operator types DELETE — a reflex click can't wipe every
    logged-in profile."""

    confirm_btn = ft.Button(
        f"[ wipe {profile_count} ]",
        height=38,
        style=ERROR_STYLE,
        disabled=True,
    )

    field = ft.TextField(
        hint_text=f"type {_CONFIRM_WORD} to confirm",
        autofocus=True,
        height=44,
        border_color=COLORS["error"],
        text_style=ft.TextStyle(font_family=MONO, size=14),
        hint_style=ft.TextStyle(font_family=MONO, size=13),
    )

    def _on_change(_: ft.ControlEvent) -> None:
        confirm_btn.disabled = (field.value or "").strip() != _CONFIRM_WORD
        page.update()

    def _on_confirm(_: ft.ControlEvent) -> None:
        if (field.value or "").strip() != _CONFIRM_WORD:
            return
        on_confirm()
        page.pop_dialog()

    field.on_change = _on_change
    confirm_btn.on_click = _on_confirm

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3, side=ft.BorderSide(1, COLORS["error"])
        ),
        title=ft.Text(
            f"Wipe all {profile_count} profiles?",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Column(
            tight=True,
            spacing=12,
            controls=[
                ft.Text(
                    "Every profile and all its data (cookies, logins, history) is "
                    "deleted permanently. This cannot be undone.",
                    size=13,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
                field,
            ],
        ),
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                height=38,
                style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            confirm_btn,
        ],
    )
    page.show_dialog(dlg)
