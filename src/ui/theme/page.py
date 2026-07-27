import os

import flet as ft

from .colors import COLORS


def _engine_option(key: str, label: str) -> ft.dropdown.Option:
    return ft.dropdown.Option(key=key, text=label)


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
            _engine_option("chromium", 'Chrome ("fingerprint-chromium")'),
            _engine_option("firefox", 'Firefox ("invisible_playwright")'),
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
    # Stretch page children to the full window WIDTH (#227). Controls are added
    # straight to page.controls (the splash, then the root layout), both
    # expand=True. expand already fills the height, but the page's cross-axis
    # default (START) left children at their natural width — so the splash sat
    # in the corner and a window resize mid-load left a black gap and a white
    # artifact strip. STRETCH makes them span the window.
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = COLORS["bg"]
    page.theme = build_page_theme()
