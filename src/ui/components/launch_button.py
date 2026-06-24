from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, ERROR_STYLE


def resolve_status(profile: Profile, is_running: bool) -> tuple[str, str, str]:
    """Return (icon, label, color) based on the profile's current state."""
    if is_running:
        return ft.Icons.CIRCLE, "running", COLORS["success"]
    if profile.proxy:
        return ft.Icons.CIRCLE, "proxy", COLORS["accent"]
    return ft.Icons.CIRCLE_OUTLINED, "direct", COLORS["text_dim"]


def build_launch_button(
    name: str,
    is_loading: bool,
    is_running: bool,
    on_launch: Callable,
) -> ft.Button:
    """Create the context-aware Launch / Stop / Loading button."""
    if is_loading:
        return ft.Button(
            "[ ... ]",
            width=120,
            height=38,
            disabled=True,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=3),
                bgcolor=COLORS["card_hover"],
                color=COLORS["text_dim"],
                side=ft.BorderSide(1, COLORS["card_border"]),
                padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                text_style=ft.TextStyle(font_family="monospace", size=13),
            ),
        )
    if is_running:
        return ft.Button(
            "[ stop ]",
            width=120,
            height=38,
            style=ERROR_STYLE,
            on_click=lambda _, n=name: on_launch(n),
        )
    return ft.Button(
        "[ launch ]",
        width=120,
        height=38,
        style=ACCENT_STYLE,
        on_click=lambda _, n=name: on_launch(n),
    )
