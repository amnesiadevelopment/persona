from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def build_tags_page(
    profiles: list[Profile],
    on_assign: Callable[[list[str], str], None],
    on_remove_tag: Callable[[str], None],
) -> ft.Container:
    # tag -> count
    counts: dict[str, int] = {}
    for p in profiles:
        for tag in p.tags:
            counts[tag] = counts.get(tag, 0) + 1

    checked: set[str] = set()
    tag_field = ft.TextField(
        label="tag name",
        width=240,
        height=44,
        border_radius=3,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        text_style=ft.TextStyle(font_family=MONO, size=13),
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
    )
    assign_btn = ft.Button(
        "[ assign to selected ]", height=44, style=ACCENT_STYLE, disabled=True
    )

    def on_toggle(name: str, value: bool) -> None:
        if value:
            checked.add(name)
        else:
            checked.discard(name)
        assign_btn.disabled = not checked
        assign_btn.update()

    assign_btn.on_click = lambda _: on_assign(sorted(checked), tag_field.value or "")

    existing = (
        [_tag_row(t, counts[t], on_remove_tag) for t in sorted(counts)]
        if counts
        else [_empty("no tags yet — assign one below")]
    )
    profile_rows = [_profile_check_row(p, on_toggle) for p in profiles]

    return ft.Container(
        expand=True,
        bgcolor=COLORS["bg"],
        padding=ft.Padding.symmetric(horizontal=32, vertical=24),
        content=ft.Column(
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                _header("tags", len(counts)),
                ft.Container(height=14),
                ft.Column(spacing=10, controls=existing),
                ft.Divider(height=40, color=COLORS["border"]),
                ft.Text(
                    "assign a tag to profiles",
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
                ft.Container(height=12),
                ft.Row(spacing=10, controls=[tag_field, assign_btn]),
                ft.Container(height=14),
                ft.Column(spacing=8, controls=profile_rows),
                ft.Container(height=20),
            ],
        ),
    )


def _header(title: str, count: int) -> ft.Row:
    return ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Text(
                title,
                size=16,
                weight=ft.FontWeight.BOLD,
                color=COLORS["text_main"],
                font_family=MONO,
            ),
            ft.Text(str(count), size=14, color=COLORS["text_sub"], font_family=MONO),
        ],
    )


def _tag_row(tag: str, count: int, on_remove: Callable[[str], None]) -> ft.Container:
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=10),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row(
                    spacing=10,
                    controls=[
                        ft.Text(tag, size=14, color=COLORS["accent"], font_family=MONO),
                        ft.Text(
                            f"{count} profile{'s' if count != 1 else ''}",
                            size=11,
                            color=COLORS["text_sub"],
                            font_family=MONO,
                        ),
                    ],
                ),
                ft.Button(
                    "[ x ]",
                    height=34,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=3),
                        color=COLORS["error"],
                        side=ft.BorderSide(1, COLORS["card_border"]),
                        text_style=ft.TextStyle(font_family=MONO, size=13),
                    ),
                    on_click=lambda _, t=tag: on_remove(t),
                ),
            ],
        ),
    )


def _profile_check_row(
    profile: Profile, on_toggle: Callable[[str, bool], None]
) -> ft.Row:
    tag_str = f"  [{', '.join(profile.tags)}]" if profile.tags else ""
    return ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Checkbox(
                fill_color=COLORS["accent"],
                check_color=COLORS["bg"],
                on_change=lambda e, n=profile.name: on_toggle(n, e.control.value),
            ),
            ft.Text(
                profile.name + tag_str,
                size=13,
                color=COLORS["text_main"],
                font_family=MONO,
            ),
        ],
    )


def _empty(text: str) -> ft.Container:
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=18, vertical=24),
        content=ft.Text(text, size=13, color=COLORS["text_sub"], font_family=MONO),
    )
