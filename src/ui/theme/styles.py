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

# Shared kwargs for an input that carries NO floating label of its own — the
# label is rendered above the field by `labeled()` so every field type (text,
# dropdown, custom) reads the same. A fixed content height keeps text fields and
# dropdowns aligned to one row height across a dialog.
DLG_INPUT_KWARGS: dict[str, Any] = dict(
    border_radius=3,
    bgcolor=COLORS["input_bg"],
    color=COLORS["text_main"],
    border_color=COLORS["card_border"],
    focused_border_color=COLORS["accent"],
    text_style=ft.TextStyle(font_family=MONO, size=14),
    text_size=14,
    content_padding=ft.Padding.symmetric(horizontal=12, vertical=12),
)


def _as_icon(icon, size: int, color: str) -> ft.Control:
    """Accept either a Material icon (a str name or an ft.Icons enum member) or a
    ready control (e.g. an ft.Image of persona's own logo) and render it at the
    given size. Only actual controls go through untouched; everything else is a
    Material icon reference (ft.Icons.* members are enums, NOT str)."""
    if isinstance(icon, ft.Control):
        return ft.Container(width=size, height=size, content=icon)
    return ft.Icon(icon, size=size, color=color)


def field_label(text: str, icon=None) -> ft.Control:
    """The one label style used above every dialog field. An optional leading
    icon (muted, small) names the field's kind at a glance."""
    text_ctl = ft.Text(
        text,
        size=11,
        color=COLORS["text_sub"],
        font_family=MONO,
    )
    if icon is None:
        return text_ctl
    return ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            _as_icon(icon, 13, COLORS["text_sub"]),
            text_ctl,
        ],
    )


def labeled(
    label: str,
    control: ft.Control,
    hint: ft.Control | None = None,
    icon=None,
) -> ft.Column:
    """Stack a static label over an input so text fields and dropdowns share one
    look (flet's floating TextField label drifts into the field as a placeholder
    when empty, which is what made dialogs read as a mix of two styles). An
    optional hint line sits under the field in the same muted style, and an
    optional leading icon names the field's kind."""
    controls: list[ft.Control] = [field_label(label, icon), control]
    if hint is not None:
        controls.append(hint)
    # STRETCH so the input fills the column's (and thus the parent's) width —
    # without it a TextField/Dropdown takes its natural width and `expand` here
    # would (wrongly) stretch it vertically instead.
    return ft.Column(
        spacing=8,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=controls,
    )


def row_button(
    label: str,
    on_click,
    kind: str = "default",
    disabled: bool = False,
    tooltip: str | None = None,
) -> ft.Button:
    """The one style for a row's inline action (edit / delete / check / rotate /
    copy). Bracket text, a solid (non-muddy) border, and a hover colour by kind:
    edit=amber, delete=red, everything else=accent."""
    # Each kind has its own colour, shown on the TEXT by default (not only on
    # hover) — edit=amber, delete=red, rotate=blue, check/copy=accent. Hover
    # just adds the matching border + faint fill.
    tint = {
        "edit": COLORS["edit"],
        "delete": COLORS["error"],
        "rotate": COLORS["rotate"],
    }.get(kind, COLORS["accent"])
    return ft.Button(
        label,
        height=34,
        disabled=disabled,
        tooltip=tooltip,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=3),
            color=tint,
            side={
                ft.ControlState.HOVERED: ft.BorderSide(1, tint),
                ft.ControlState.DEFAULT: ft.BorderSide(1, COLORS["btn_edge"]),
            },
            bgcolor={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(0.10, tint),
                ft.ControlState.DEFAULT: COLORS["input_bg"],
            },
            padding=ft.Padding.symmetric(horizontal=11, vertical=0),
            text_style=ft.TextStyle(font_family=MONO, size=12),
        ),
        on_click=on_click,
    )


def section_header(title: str, icon=None) -> ft.Row:
    """A dialog section divider: an optional accent icon, an accent all-caps
    label, then a hairline rule — used to group a long form into
    IDENTITY / NETWORK / ENGINE blocks."""
    controls: list[ft.Control] = []
    if icon is not None:
        controls.append(_as_icon(icon, 14, COLORS["accent"]))
    controls.append(
        ft.Text(
            title,
            size=11,
            color=COLORS["accent"],
            font_family=MONO,
            weight=ft.FontWeight.BOLD,
        )
    )
    controls.append(ft.Container(expand=True, height=1, bgcolor=COLORS["border"]))
    return ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=8,
        controls=controls,
    )
