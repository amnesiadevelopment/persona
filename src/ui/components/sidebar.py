import os
from collections.abc import Callable

import flet as ft

from ...core.strings import app_subtitle, get_string
from ..theme.colors import COLORS
from ..theme.styles import MONO

from ...core.assets import asset_path

_ICON = asset_path("icon.png")

_NAV_ITEMS = [
    ("profiles", ft.Icons.PERSON_OUTLINE, "profiles"),
    ("network", ft.Icons.LAN_OUTLINED, "network"),
    ("bookmarks", ft.Icons.BOOKMARK_BORDER, "bookmarks"),
    ("tags", ft.Icons.LABEL_OUTLINE, "tags"),
    ("certificates", ft.Icons.DESCRIPTION_OUTLINED, "certificates"),
    ("connect", ft.Icons.SMART_TOY_OUTLINED, "connect"),
    ("trash", ft.Icons.DELETE_OUTLINE, "trash"),
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
    log_panel: ft.Control | None = None,
    engine_panel: ft.Control | None = None,
    version_panel: ft.Control | None = None,
    on_logo_click: Callable[[], None] | None = None,
) -> ft.Container:
    # THE NAV IS THE PART THAT GIVES, and it gives by SCROLLING rather than by
    # crushing. The rail is a hard 200px and nothing in it used to scroll, so a
    # short window pushed the bottom cluster down until `trash` sat flush
    # against the engines dropdown — and at the app's own minimum size (1024x680)
    # it went further than that: the engines panel was clipped to a sliver and
    # the version panel was pushed out of the rail entirely.
    #
    # `expand=True` is what makes the scroll real. A scrollable Column with no
    # expand still claims its full NATURAL height inside a parent Column, so it
    # never scrolls and simply overflows — the scroll mode alone fixed nothing.
    # Expanded, the nav takes exactly the room the fixed bottom cluster leaves
    # it and scrolls inside that, so a short window costs a scroll gesture
    # instead of costing the operator two controls.
    nav = ft.Column(
        spacing=6,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        controls=[
            _nav_button(key, icon, label, active_page == key, on_navigate)
            for key, icon, label in _NAV_ITEMS
        ],
    )
    header: ft.Control = ft.Row(
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
    )
    if on_logo_click is not None:
        # A transparent click layer laid OVER the whole header (via a Stack)
        # catches clicks anywhere on it, including the logo image — the image
        # otherwise swallows pointer events, so only the wordmark's gaps (and
        # the icon's frame edge) triggered the scan.
        # Wrap the whole header row in a GestureDetector: it receives taps across
        # its entire content subtree, including the logo image, so a click on the
        # icon triggers the scan too — a Container.on_click only fires on the
        # container's own painted pixels, which the image child covered, leaving
        # just the wordmark's gaps and the icon's frame edge clickable.
        header = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=lambda _: on_logo_click(),
            content=header,
        )
    return ft.Container(
        width=200,
        bgcolor=COLORS["sidebar"],
        # 22px top and bottom cost 44px of a rail that needs every pixel at the
        # app's minimum window size — measured there, `trash` was still clipped
        # by ~9px with the dock already at its 120px floor, so the rail is where
        # the remaining space had to come from. Horizontal padding is untouched.
        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
        content=ft.Column(
            spacing=0,
            expand=True,
            controls=[
                header,
                ft.Text(
                    app_subtitle(),
                    size=10,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
                ft.Divider(height=24, color=COLORS["border"]),
                nav,
                # A gap that CANNOT collapse — and deliberately NOT `expand`.
                # `expand` yields all of its height when the window is short, so
                # a spacer alone guaranteed nothing: measured at the app's own
                # minimum size (1024x680), `trash` ended up 3px from the engines
                # dropdown, two unrelated controls reading as one cluster. It
                # must not be expandable for a second reason too: the nav above
                # is the expanding child now, and a second `expand` sibling
                # splits the free space with it 50/50 — which clipped the nav
                # mid-list (at `tags`) while leaving blank rail below it.
                ft.Container(height=14),
                *(
                    [ft.Divider(height=1, color=COLORS["border"]), engine_panel]
                    if engine_panel is not None
                    else []
                ),
                *([version_panel] if version_panel is not None else []),
                *([log_panel] if log_panel is not None else []),
            ],
        ),
    )
