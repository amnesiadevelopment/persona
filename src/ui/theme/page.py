import os

import flet as ft

from ...core.assets import asset_path
from .colors import COLORS


def _engine_option(key: str, label: str, icon_file: str) -> ft.dropdown.Option:
    path = asset_path(icon_file)
    row_controls: list[ft.Control] = []
    if os.path.exists(path):
        row_controls.append(ft.Image(src=path, width=18, height=18))
    row_controls.append(
        ft.Text(label, color=COLORS["text_main"], font_family="monospace")
    )
    return ft.dropdown.Option(
        key=key,
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=row_controls,
        ),
    )


def build_page_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=COLORS["accent"],
        color_scheme=ft.ColorScheme(
            primary=COLORS["accent"],
            on_primary="#000000",
            surface=COLORS["card_bg"],
            on_surface=COLORS["text_main"],
            on_surface_variant=COLORS["text_sub"],
            surface_container=COLORS["sidebar"],
            outline=COLORS["border"],
            error="#EF4444",
        ),
    )


def build_os_dropdown(value: str = "windows") -> ft.Dropdown:
    return ft.Dropdown(
        label="Operating System",
        value=value,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family="monospace"),
        text_style=ft.TextStyle(font_family="monospace"),
        border_radius=3,
        options=[
            ft.dropdown.Option("windows"),
            ft.dropdown.Option("macos"),
            ft.dropdown.Option("linux"),
            ft.dropdown.Option("android"),
            ft.dropdown.Option("ios"),
        ],
    )


def build_engine_dropdown(value: str = "chromium") -> ft.Dropdown:
    return ft.Dropdown(
        label="Engine",
        value=value,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family="monospace"),
        text_style=ft.TextStyle(font_family="monospace"),
        border_radius=3,
        options=[
            _engine_option(
                "chromium", "fingerprint-chromium (Chrome)", "engine_chrome.png"
            ),
            _engine_option("firefox", "Firefox (invisible)", "engine_firefox.png"),
        ],
    )


def configure_page(page: ft.Page) -> None:
    page.title = "persona"
    page.window.width, page.window.height = 1280, 820
    page.window.min_width, page.window.min_height = 1024, 680

    from ...core.assets import asset_path

    icon_path = asset_path("icon.png")
    if os.path.exists(icon_path):
        page.window.icon = icon_path

    page.padding = page.spacing = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = COLORS["bg"]
    page.theme = build_page_theme()
