import os
import sys

import flet as ft

from .colors import COLORS


def _primary_work_rect() -> "tuple[int, int, int, int]":
    """The PRIMARY display's work-area rectangle (x, y, width, height) in the
    LOGICAL (virtual/device-independent) pixel space Flet positions windows in,
    or (0, 0, 0, 0) if it can't be read.

    Flet's page.window.left/top/width/height are LOGICAL pixels — window_manager
    multiplies by the display's devicePixelRatio internally before talking to the
    OS. So the centre must be computed in logical space too. On Windows the Flet
    process is per-monitor DPI-aware, so a raw SPI_GETWORKAREA returns PHYSICAL
    pixels (3840x2160 for a 4K panel at 150%); feeding those to left/top
    double-scales and throws the window off-centre (Mars's live 4K@150% report).
    Divide the physical rect by the monitor's scale to get the logical rect Flet
    expects. Using the work-area rect (not bare size) keeps its origin + excludes
    the taskbar, so the window centres on the PRIMARY display even with a second
    monitor at a non-zero origin."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                            ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

            r = RECT()
            SPI_GETWORKAREA = 0x0030
            if not ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(r), 0
            ):
                return (0, 0, 0, 0)
            scale = _windows_scale()
            return (int(r.left / scale), int(r.top / scale),
                    int((r.right - r.left) / scale),
                    int((r.bottom - r.top) / scale))
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
            # macOS CGDisplayPixelsWide/High already report POINTS (logical),
            # and the main display is the origin of the global space.
            return (0, 0,
                    int(cg.CGDisplayPixelsWide(did)),
                    int(cg.CGDisplayPixelsHigh(did)))
    except Exception:
        pass
    return (0, 0, 0, 0)


def _windows_scale() -> float:
    """The primary monitor's display scale (1.0 at 100%, 1.5 at 150%, 2.0 at
    200%). 1.0 on any failure so the caller falls back to unscaled coordinates."""
    try:
        import ctypes

        # GetDpiForSystem needs a DPI-aware process to report the real value; the
        # Flet host already is. 96 DPI == 100%.
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def _engine_option(key: str, label: str) -> ft.dropdown.Option:
    from ...core.assets import asset_path

    fname = "engine_firefox.svg" if key in ("firefox", "camoufox") else "engine_chrome.svg"
    path = asset_path(fname)
    icon: ft.Control = (
        ft.Image(src=path, width=16, height=16)
        if os.path.exists(path)
        else ft.Icon(ft.Icons.PUBLIC, size=16, color=COLORS["accent"])
    )
    return ft.dropdown.Option(
        key=key,
        text=label,
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                icon,
                ft.Text(label, font_family="monospace", color=COLORS["text_main"]),
            ],
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
        value=value,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
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
        value=value,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
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
    # Open centred on the PRIMARY monitor. page.window.center() is unreliable (it
    # centres the pre-resize default window — measured on macOS), and centring by
    # bare screen SIZE drifts the window onto other monitors on a multi-display
    # desktop (live: a 2nd monitor at x=3840 made persona open far off-centre).
    # Centre inside the primary display's work-area rect, which carries its origin.
    # macOS: setting page.window.left/top here crashes the BUILT flet-desktop
    # app on launch (the native window isn't realised yet; source/flet-run and
    # Win/Linux are fine — this path was never tested on a macOS BUILD, #216
    # centring "verified Win+Linux, NOT Mac"). Skip positioning on darwin so the
    # window opens; Win/Linux keep the work-area centring.
    if sys.platform != "darwin":
        wx, wy, ww, wh = _primary_work_rect()
        if ww and wh:
            page.window.left = wx + max(0, (ww - win_w) // 2)
            page.window.top = wy + max(0, (wh - win_h) // 2)
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
