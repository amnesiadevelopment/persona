import os
import sys

import flet as ft

from .colors import COLORS


def _screen_size() -> "tuple[int, int]":
    """The primary display's pixel size, or (0, 0) if it can't be read.

    Used to open the window centred: page.window.center() is unreliable — it runs
    before the native resize to the real window size applies, so it centres the
    Flet-default window and the later resize pushes it off-centre (measured on
    macOS). Explicit left/top from the real screen size avoids that race."""
    try:
        if sys.platform == "win32":
            import ctypes

            u = ctypes.windll.user32
            return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
        if sys.platform == "darwin":
            import ctypes
            import ctypes.util

            cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
            cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
            cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            did = cg.CGMainDisplayID()
            return int(cg.CGDisplayPixelsWide(did)), int(cg.CGDisplayPixelsHigh(did))
    except Exception:
        pass
    return (0, 0)


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
    win_w, win_h = 1280, 820
    page.window.width, page.window.height = win_w, win_h
    page.window.min_width, page.window.min_height = 1024, 680
    # Open centred. page.window.center() is unreliable — it runs before the
    # native resize to win_w x win_h applies, so it centres the Flet-default
    # window and the resize pushes it off-centre (measured on macOS). Set
    # explicit left/top from the real screen size; fall back to center() only
    # when the screen size can't be read.
    screen_w, screen_h = _screen_size()
    if screen_w and screen_h:
        page.window.left = max(0, (screen_w - win_w) // 2)
        page.window.top = max(0, (screen_h - win_h) // 2)
    else:
        page.window.center()

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
