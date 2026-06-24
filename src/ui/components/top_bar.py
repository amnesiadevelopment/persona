from collections.abc import Callable

import flet as ft

from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO, OUTLINE_STYLE


def build_top_bar(
    count_text: ft.Text,
    search_field: ft.TextField,
    on_new: Callable,
    on_import: Callable,
    on_export: Callable,
) -> ft.Container:
    return ft.Container(
        padding=ft.Padding.only(bottom=20),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            "all",
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=COLORS["text_main"],
                            font_family=MONO,
                        ),
                        count_text,
                    ],
                ),
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        search_field,
                        ft.OutlinedButton(
                            "[ import ]",
                            width=110,
                            height=40,
                            style=OUTLINE_STYLE,
                            on_click=on_import,
                        ),
                        ft.OutlinedButton(
                            "[ export ]",
                            width=110,
                            height=40,
                            style=OUTLINE_STYLE,
                            on_click=on_export,
                        ),
                        ft.Button(
                            "[ + new ]",
                            width=110,
                            height=40,
                            style=ACCENT_STYLE,
                            on_click=on_new,
                        ),
                    ],
                ),
            ],
        ),
    )
