from collections.abc import Callable

import flet as ft

from ...services.profile.bulk import parse_names
from ..theme.colors import COLORS
from ..theme.page import build_os_dropdown
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)


def open_bulk_dialog(
    page: ft.Page,
    on_create: Callable[[str, str, str, list[str]], str | None],
) -> None:
    _hint = ft.TextStyle(color=COLORS["text_dim"], font_family=MONO)
    names_field = ft.TextField(
        hint_text="one per line or comma-separated",
        hint_style=_hint,
        multiline=True,
        min_lines=6,
        max_lines=12,
        **DLG_INPUT_KWARGS,
    )
    os_dropdown = build_os_dropdown("windows")
    os_dropdown.expand = True
    tags_field = ft.TextField(
        hint_text="e.g. work, batch-1",
        hint_style=_hint,
        **DLG_INPUT_KWARGS,
    )
    error_text = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def on_submit(_: ft.ControlEvent) -> None:
        names_text = names_field.value or ""
        os_type = os_dropdown.value or "windows"
        tags_text = tags_field.value or ""
        error_text.visible = False

        if not parse_names(names_text):
            error_text.value = "Enter at least one profile name"
            error_text.visible = True
            page.update()
            return

        error = on_create(names_text, os_type, tags_text, [])
        if error:
            error_text.value = error
            error_text.visible = True
            page.update()
        else:
            page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        bgcolor=COLORS["card_bg"],
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.LIBRARY_ADD_OUTLINED, size=22, color=COLORS["accent"]),
                ft.Text(
                    "Bulk Create Profiles",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
            ],
        ),
        content=ft.Container(
            width=460,
            padding=ft.Padding.only(left=4, top=4, bottom=4, right=14),
            content=ft.Column(
                tight=True,
                spacing=16,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Text(
                        "Create many profiles at once.",
                        size=13,
                        color=COLORS["text_sub"],
                    ),
                    section_header("PROFILES", icon=ft.Icons.PERSON_OUTLINE),
                    labeled("Profile names", names_field, icon=ft.Icons.LIST_ALT),
                    error_text,
                    labeled(
                        "Operating system", os_dropdown, icon=ft.Icons.COMPUTER
                    ),
                    labeled(
                        "Tags (optional)", tags_field, icon=ft.Icons.LABEL_OUTLINE
                    ),
                ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.END,
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                height=40,
                style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ create ]",
                height=40,
                style=ACCENT_STYLE,
                on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
