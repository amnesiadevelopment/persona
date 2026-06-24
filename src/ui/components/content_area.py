import flet as ft

from ..theme.colors import COLORS


def build_content_area(
    subtitle: ft.Text,
    profile_list: ft.Column,
    prev_btn: ft.IconButton,
    next_btn: ft.IconButton,
    page_label: ft.Text,
    bulk_bar: ft.Control | None = None,
) -> ft.Container:
    """Profile list area with pagination and bulk-action bar.

    The page heading and counter live in the top bar; this area is just the
    table of profiles plus pagination.
    """
    return ft.Container(
        expand=True,
        bgcolor=COLORS["bg"],
        content=ft.Column(
            spacing=0,
            expand=True,
            controls=[
                subtitle,
                ft.Container(height=12),
                *([] if bulk_bar is None else [bulk_bar, ft.Container(height=8)]),
                profile_list,
                ft.Container(height=16),
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        prev_btn,
                        ft.Container(width=12),
                        page_label,
                        ft.Container(width=12),
                        next_btn,
                    ],
                ),
            ],
        ),
    )
