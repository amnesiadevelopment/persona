import os
from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ..theme.colors import COLORS
from ..theme.styles import MONO

from ...core.assets import asset_path

_ICON = asset_path("icon.png")

_NAV_ITEMS = [
    ("profiles", ft.Icons.PERSON_OUTLINE, "profiles"),
    ("network", ft.Icons.LAN_OUTLINED, "network"),
    ("bookmarks", ft.Icons.BOOKMARK_BORDER, "bookmarks"),
    ("tags", ft.Icons.LABEL_OUTLINE, "tags"),
    ("connect", ft.Icons.SMART_TOY_OUTLINED, "connect"),
]


def _nav_button(
    key: str,
    icon: str,
    label: str,
    active: bool,
    on_navigate: Callable[[str], None],
) -> ft.Container:
    color = COLORS["accent"] if active else COLORS["text_sub"]
    return ft.Container(
        border_radius=3,
        bgcolor=COLORS["card_hover"] if active else "transparent",
        border=ft.Border.all(
            1,
            COLORS["accent"] if active else "transparent",
        ),
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        on_click=lambda _, k=key: on_navigate(k),
        ink=True,
        content=ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=18, color=color),
                ft.Text(label, size=14, color=color, font_family=MONO),
            ],
        ),
    )


def build_sidebar(
    active_page: str,
    on_navigate: Callable[[str], None],
    log_panel: ft.Control,
    engine_panel: ft.Control | None = None,
) -> ft.Container:
    nav = ft.Column(
        spacing=6,
        controls=[
            _nav_button(key, icon, label, active_page == key, on_navigate)
            for key, icon, label in _NAV_ITEMS
        ],
    )
    return ft.Container(
        width=200,
        bgcolor=COLORS["sidebar"],
        padding=ft.Padding.symmetric(horizontal=16, vertical=22),
        content=ft.Column(
            spacing=0,
            expand=True,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        *(
                            [ft.Image(src=_ICON, width=28, height=28)]
                            if os.path.exists(_ICON)
                            else []
                        ),
                        ft.Text(
                            get_string("app_name"),
                            size=22,
                            weight=ft.FontWeight.BOLD,
                            color=COLORS["accent"],
                            font_family=MONO,
                        ),
                    ],
                ),
                ft.Text(
                    get_string("app_subtitle"),
                    size=10,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
                ft.Divider(height=24, color=COLORS["border"]),
                nav,
                ft.Container(expand=True),
                *([engine_panel] if engine_panel is not None else []),
                log_panel,
            ],
        ),
    )
