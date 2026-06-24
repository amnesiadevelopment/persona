from collections.abc import Callable

import flet as ft

from ...services.profile.bulk import parse_names
from ..theme.colors import COLORS
from ..theme.page import build_os_dropdown
from ..theme.styles import ACCENT_STYLE, DLG_FIELD_KWARGS, MONO


def open_bulk_dialog(
    page: ft.Page,
    on_create: Callable[[str, str, str, list[str]], str | None],
) -> None:
    names_field = ft.TextField(
        label="Profile names",
        hint_text="one per line or comma-separated",
        multiline=True,
        min_lines=6,
        max_lines=12,
        **DLG_FIELD_KWARGS,
    )
    os_dropdown = build_os_dropdown("windows")
    os_dropdown.expand = True
    tags_field = ft.TextField(
        label="Tags (optional, comma-separated)",
        hint_text="e.g. work, batch-1",
        **DLG_FIELD_KWARGS,
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
        title=ft.Text(
            "Bulk Create Profiles",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=520,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
            content=ft.Column(
                tight=True,
                spacing=16,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Text(
                        "Create many profiles at once.",
                        size=13,
                        color=COLORS["text_sub"],
                    ),
                    ft.Container(height=6),
                    ft.Row(controls=[names_field]),
                    error_text,
                    ft.Row(controls=[os_dropdown]),
                    ft.Row(controls=[tags_field]),
                    ft.Container(height=10),
                ],
            ),
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                style=ft.ButtonStyle(color=COLORS["text_sub"]),
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ create ]",
                style=ACCENT_STYLE,
                on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
