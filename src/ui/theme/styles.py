from typing import Any

import flet as ft

from .colors import COLORS

# Monospace terminal font for the tray-style UI aesthetic.
MONO = "monospace"

# Accent button: dark fill, neon-green text + border, square corners.
_BTN_PADDING = ft.Padding.symmetric(horizontal=4, vertical=0)

ACCENT_STYLE = ft.ButtonStyle(
    shape=ft.RoundedRectangleBorder(radius=3),
    bgcolor=COLORS["card_hover"],
    color=COLORS["accent"],
    side=ft.BorderSide(1, COLORS["accent"]),
    padding=_BTN_PADDING,
    text_style=ft.TextStyle(font_family=MONO, weight=ft.FontWeight.BOLD, size=13),
)

ERROR_STYLE = ft.ButtonStyle(
    shape=ft.RoundedRectangleBorder(radius=3),
    bgcolor=COLORS["card_hover"],
    color=COLORS["error"],
    side=ft.BorderSide(1, COLORS["error"]),
    padding=_BTN_PADDING,
    text_style=ft.TextStyle(font_family=MONO, weight=ft.FontWeight.BOLD, size=13),
)

OUTLINE_STYLE = ft.ButtonStyle(
    shape=ft.RoundedRectangleBorder(radius=3),
    side=ft.BorderSide(1, COLORS["card_border"]),
    color=COLORS["text_sub"],
    padding=_BTN_PADDING,
    text_style=ft.TextStyle(font_family=MONO, size=13),
)


DLG_FIELD_KWARGS: dict[str, Any] = dict(
    border_radius=3,
    bgcolor=COLORS["input_bg"],
    color=COLORS["text_main"],
    border_color=COLORS["card_border"],
    focused_border_color=COLORS["accent"],
    label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
    text_style=ft.TextStyle(font_family=MONO),
    cursor_color=COLORS["accent"],
)
