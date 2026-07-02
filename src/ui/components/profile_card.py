from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ...models.proxy import Proxy
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import MONO
from .launch_button import build_launch_button

_OS_LABELS = {"windows": "windows", "macos": "macos", "linux": "linux"}

# The proxy/direct indicator and a flag share this footprint so the row never
# shifts when a flag replaces the placeholder.
_IND_W = 30
_IND_H = 20


def _tag_chips(tags: list[str]) -> ft.Control:
    if not tags:
        return ft.Container(width=0)
    return ft.Row(
        spacing=6,
        wrap=True,
        controls=[
            ft.Container(
                border_radius=3,
                border=ft.Border.all(1, COLORS["card_border"]),
                bgcolor=COLORS["bg"],
                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                content=ft.Text(
                    tag, size=10, color=COLORS["accent"], font_family=MONO
                ),
            )
            for tag in tags
        ],
    )


def _indicator_box(content: ft.Control, border: bool = True) -> ft.Container:
    return ft.Container(
        width=_IND_W,
        height=_IND_H,
        border_radius=2,
        border=ft.Border.all(1, COLORS["card_border"]) if border else None,
        alignment=ft.Alignment(0, 0),
        content=content,
    )


def _proxy_indicator(
    proxy: Proxy | None,
    on_check_proxy: Callable[[str], None] | None,
    is_checking: bool,
) -> ft.Control:
    """Left-of-name indicator that doubles as the proxy check button.

    - no proxy        -> a 'direct' box (not clickable)
    - checking        -> a spinner
    - checked ok      -> the country flag (click to re-check)
    - check failed    -> an ✕ (click to re-check)
    - not checked yet -> a dot placeholder (click to check)
    """
    if proxy is None:
        return _indicator_box(
            ft.Icon(ft.Icons.HOME_OUTLINED, size=15, color=COLORS["text_dim"]),
            border=False,
        )

    if is_checking:
        inner: ft.Control = _indicator_box(
            ft.ProgressRing(
                width=12, height=12, stroke_width=2, color=COLORS["accent"]
            ),
            border=False,
        )
    elif proxy.last_check_ok is False:
        inner = _indicator_box(
            ft.Text("✕", size=14, color=COLORS["error"], font_family=MONO)
        )
    else:
        path = flag_path(proxy.country_code) if proxy.country_code else None
        if path:
            inner = ft.Image(src=path, width=_IND_W, height=_IND_H, border_radius=2)
        else:
            # has a proxy but no successful check yet
            inner = _indicator_box(
                ft.Text("·", size=14, color=COLORS["text_dim"], font_family=MONO)
            )

    if on_check_proxy is None or is_checking:
        return inner
    return ft.Container(
        content=inner,
        on_click=lambda _, n=proxy.name: on_check_proxy(n),
        ink=True,
        tooltip="Check this profile's proxy",
    )


def _notes_field(profile, on_notes_change):
    """Inline, editable notes, vertically centred in the row and dim so it
    doesn't draw the eye. Saved on blur or Enter — no dialog needed. A fixed
    width keeps the notes aligned in a column across every card."""
    field = ft.TextField(
        value=getattr(profile, "notes", ""),
        hint_text="notes…",
        text_size=12,
        text_align=ft.TextAlign.CENTER,
        text_style=ft.TextStyle(
            font_family=MONO, italic=True, color=COLORS["text_dim"]
        ),
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        color=COLORS["text_dim"],
        border=ft.InputBorder.NONE,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        multiline=False,
        on_blur=(
            (lambda e, n=profile.name: on_notes_change(n, e.control.value or ""))
            if on_notes_change
            else None
        ),
        on_submit=(
            (lambda e, n=profile.name: on_notes_change(n, e.control.value or ""))
            if on_notes_change
            else None
        ),
    )
    # Fixed width, centred by the overlay row, so notes line up down the
    # middle of every card regardless of name/button widths.
    return ft.Container(width=260, content=field)


def build_profile_card(
    profile: Profile,
    is_loading: bool,
    is_running: bool,
    on_launch: Callable[[str], None],
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    is_selected: bool = False,
    on_select: Callable[[str], None] | None = None,
    proxy: Proxy | None = None,
    on_check_proxy: Callable[[str], None] | None = None,
    proxy_checking: bool = False,
    on_notes_change: Callable[[str, str], None] | None = None,
) -> ft.Container:
    """Build a single profile row as a terminal-style line."""
    launch_btn = build_launch_button(
        profile.name,
        is_loading,
        is_running,
        on_launch,
        engine=getattr(profile, "engine", "chromium"),
    )
    action_buttons = _build_action_buttons(
        profile.name, on_edit, on_delete, is_running=is_running
    )
    select_box = _build_select_box(profile.name, is_selected, on_select)
    indicator = _proxy_indicator(proxy, on_check_proxy, proxy_checking)

    if is_running:
        border_color = COLORS["accent"]
    elif is_selected:
        border_color = COLORS["text_sub"]
    else:
        border_color = COLORS["card_border"]

    os_label = _OS_LABELS.get(profile.os_type, profile.os_type)
    proxy_label = profile.proxy if profile.proxy else "direct"
    # A running profile is already shown by the accent border and the stop
    # button; a "· running" suffix here would be redundant.
    meta = f"{os_label} · {proxy_label}"

    left_block = ft.Row(
        spacing=14,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            select_box,
            indicator,
            ft.Column(
                spacing=3,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        profile.name,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    *([_tag_chips(profile.tags)] if profile.tags else []),
                    ft.Text(
                        meta,
                        size=11,
                        color=COLORS["accent"] if is_running else COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
        ],
    )
    right_block = ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[launch_btn, *action_buttons],
    )

    # The left and right blocks pin to the edges; notes are layered on top in
    # their own centred row so they sit dead-centre of the whole card, not
    # centred in the gap between the two blocks.
    row = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[left_block, right_block],
    )
    notes_overlay = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[_notes_field(profile, on_notes_change)],
    )

    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, border_color),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=14),
        content=ft.Stack(controls=[row, notes_overlay]),
    )


def _build_action_buttons(
    name: str,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    is_running: bool = False,
) -> list[ft.Button]:
    return [
        ft.Button(
            "[ edit ]",
            width=92,
            height=38,
            # Editing a profile while its browser is open can corrupt its data
            # dir / fingerprint mid-session, so disable edit until it's stopped.
            disabled=is_running,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=3),
                color=COLORS["text_sub"],
                side=ft.BorderSide(1, COLORS["card_border"]),
                padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                text_style=ft.TextStyle(font_family=MONO, size=13),
            ),
            tooltip="Stop the profile to edit it" if is_running else "Edit profile",
            on_click=lambda _, n=name: on_edit(n),
        ),
        ft.Button(
            "[ x ]",
            width=64,
            height=38,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=3),
                color=COLORS["error"],
                side=ft.BorderSide(1, COLORS["card_border"]),
                overlay_color=ft.Colors.with_opacity(0.1, COLORS["error"]),
                padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                text_style=ft.TextStyle(font_family=MONO, size=13),
            ),
            tooltip="Delete profile",
            on_click=lambda _, n=name: on_delete(n),
        ),
    ]


def _build_select_box(
    name: str,
    is_selected: bool,
    on_select: Callable[[str], None] | None,
) -> ft.Container:
    return ft.Container(
        alignment=ft.Alignment(0, 0),
        on_click=lambda _, n=name: on_select(n) if on_select else None,
        ink=True,
        tooltip="Select profile",
        content=ft.Text(
            "[x]" if is_selected else "[ ]",
            size=13,
            color=COLORS["accent"] if is_selected else COLORS["text_dim"],
            font_family=MONO,
        ),
    )
