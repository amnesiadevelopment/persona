import os
from collections.abc import Callable

import flet as ft

from ...core.assets import asset_path
from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, ERROR_STYLE


def _engine_logo(engine: str, size: int = 15) -> ft.Control | None:
    fname = "engine_firefox.png" if engine == "camoufox" else "engine_chrome.png"
    path = asset_path(fname)
    if os.path.exists(path):
        return ft.Image(src=path, width=size, height=size)
    return None


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
    engine: str = "chromium",
) -> ft.Button:
    """Create the context-aware Launch / Stop / Loading button. The launch
    state carries the profile's engine logo so it's clear which browser the
    profile opens with."""
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
    logo = _engine_logo(engine)
    label: ft.Control = ft.Row(
        spacing=7,
        tight=True,
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            *([logo] if logo is not None else []),
            ft.Text(
                "[ launch ]",
                color=COLORS["accent"],
                font_family="monospace",
                size=13,
            ),
        ],
    )
    return ft.Button(
        content=label,
        width=120,
        height=38,
        style=ACCENT_STYLE,
        on_click=lambda _, n=name: on_launch(n),
    )
