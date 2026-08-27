"""Ask before closing persona while browsers are still open (PS-223).

The owner's decision, and the reason it is a QUESTION rather than a silent
teardown: closing persona closes the browsers with it, and an accidental click
on the window's X would otherwise destroy whatever the user had open in them.

This covers the CLEAN path only. It is deliberately not the safety catch — a
crash or a kill from Task Manager never reaches this dialog, which is why the
persisted running-session registry exists and why it must not depend on this
dialog having run.
"""

from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ..theme.colors import COLORS
from ..theme.styles import ERROR_STYLE, MONO, OUTLINE_STYLE


def open_exit_confirm_dialog(
    page: ft.Page,
    names: list[str],
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Confirm closing persona while ``names`` still have browsers open.

    NAMES THE PROFILES, not just a count. "2 profiles are open" tells the user
    a number; naming them tells them whether the one they care about is in the
    list, which is the question they actually have to answer.

    Cancel is the DEFAULT-SAFE action and is the one that runs if the dialog is
    dismissed, because the destructive answer here is the one that closes the
    windows.
    """
    count = len(names)

    def _confirm(_: ft.ControlEvent) -> None:
        page.pop_dialog()
        on_confirm()

    def _cancel(_: ft.ControlEvent) -> None:
        page.pop_dialog()
        if on_cancel:
            on_cancel()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text(
            get_string("confirm_exit_title", count=count),
            size=18,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Text(
            get_string("confirm_exit_body", names=", ".join(names)),
            size=13,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
        actions=[
            ft.OutlinedButton(
                get_string("confirm_exit_cancel"),
                height=38,
                style=OUTLINE_STYLE,
                on_click=_cancel,
            ),
            ft.Button(
                get_string("confirm_exit_close"),
                height=38,
                style=ERROR_STYLE,
                on_click=_confirm,
            ),
        ],
    )
    page.show_dialog(dlg)
