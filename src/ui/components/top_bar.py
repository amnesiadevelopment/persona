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
    on_wipe: Callable,
) -> ft.Container:
    # A single row with an elastic spacer in the middle: the left cluster and
    # the right cluster each hug their content (tight), and the expand=True
    # spacer absorbs the slack — so the row always fits the page width instead
    # of overflowing off the right edge on a narrow window (flet Rows don't
    # shrink their children; without an expanding child they run off the edge).
    return ft.Container(
        padding=ft.Padding.only(bottom=20),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    tight=True,
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
                        # Panic wipe: delete every profile at once for an instant
                        # clean-out. Irreversible, so the handler gates it behind a
                        # typed confirmation. Styled danger (red) and set apart from
                        # the count so it can't be hit by reflex.
                        ft.TextButton(
                            "[ wipe all ]",
                            on_click=on_wipe,
                            style=ft.ButtonStyle(
                                color=COLORS["error"],
                                text_style=ft.TextStyle(font_family=MONO, size=12),
                            ),
                        ),
                    ],
                ),
                ft.Container(expand=True),
                ft.Row(
                    spacing=10,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        search_field,
                        ft.OutlinedButton(
                            "[ import ]",
                            height=40,
                            style=OUTLINE_STYLE,
                            on_click=on_import,
                        ),
                        ft.OutlinedButton(
                            "[ export ]",
                            height=40,
                            style=OUTLINE_STYLE,
                            on_click=on_export,
                        ),
                        ft.Button(
                            "[ + new ]",
                            height=40,
                            style=ACCENT_STYLE,
                            on_click=on_new,
                        ),
                    ],
                ),
            ],
        ),
    )
