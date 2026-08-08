from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def _col_header(icon: str, title: str) -> ft.Row:
    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(icon, size=15, color=COLORS["accent"]),
            ft.Text(
                title,
                size=11,
                color=COLORS["accent"],
                font_family=MONO,
                weight=ft.FontWeight.BOLD,
            ),
        ],
    )


def _checkbox(on_change) -> ft.Checkbox:
    return ft.Checkbox(
        fill_color={
            ft.ControlState.SELECTED: COLORS["accent"],
            ft.ControlState.DEFAULT: "transparent",
        },
        check_color=COLORS["bg"],
        border_side=ft.BorderSide(1.5, COLORS["text_dim"]),
        on_change=on_change,
    )


def build_tags_page(
    profiles: list[Profile],
    on_assign: Callable[[list[str], str], None],
    on_remove_tag: Callable[[str], None],
) -> ft.Container:
    # Count tags case-insensitively so "Work"/"work" are one chip — the filter
    # and remove both treat tags case-insensitively (audit5 LOW). Keep the first
    # spelling seen as the canonical display form.
    counts: dict[str, int] = {}
    canon: dict[str, str] = {}
    for p in profiles:
        for tag in p.tags:
            key = tag.lower()
            canon.setdefault(key, tag)
            counts[key] = counts.get(key, 0) + 1

    checked: set[str] = set()

    tag_field = ft.TextField(
        hint_text="type a tag name…",
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        expand=True,
        height=44,
        border_radius=3,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        text_style=ft.TextStyle(font_family=MONO, size=13),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
    )

    _grey_style = ft.ButtonStyle(
        shape=ft.RoundedRectangleBorder(radius=3),
        bgcolor=COLORS["card_hover"],
        color=COLORS["text_dim"],
        side=ft.BorderSide(1, COLORS["card_border"]),
        padding=ft.Padding.symmetric(horizontal=14, vertical=0),
        text_style=ft.TextStyle(font_family=MONO, weight=ft.FontWeight.BOLD, size=13),
    )
    assign_btn = ft.Button(
        "[ assign to selected ]", height=44, style=_grey_style, disabled=True
    )

    def _refresh_assign_state() -> None:
        n = len(checked)
        # Enable ONLY when there's a non-empty tag AND at least one profile
        # picked. Enabled with an empty tag was a silent no-op that also cleared
        # the selection (audit5 LOW).
        ready = bool((tag_field.value or "").strip()) and n > 0
        assign_btn.text = (
            f"[ assign to {n} selected ]" if n else "[ assign to selected ]"
        )
        assign_btn.style = ACCENT_STYLE if ready else _grey_style
        assign_btn.disabled = not ready
        assign_btn.update()

    def on_toggle(name: str, value: bool) -> None:
        if value:
            checked.add(name)
        else:
            checked.discard(name)
        _refresh_assign_state()

    tag_field.on_change = lambda _: _refresh_assign_state()

    def _do_assign(_: object) -> None:
        tag = (tag_field.value or "").strip()
        if not tag or not checked:
            return
        on_assign(sorted(checked), tag)

    assign_btn.on_click = _do_assign

    # Existing tags as a chip cloud; ✕ on a chip removes the tag everywhere.
    if counts:
        tag_cloud: ft.Control = ft.Row(
            spacing=8,
            wrap=True,
            controls=[
                _tag_chip(canon[k], counts[k], on_remove_tag) for k in sorted(counts)
            ],
        )
    else:
        tag_cloud = _empty("no tags yet — assign one below")

    # Profiles as a 2-column grid of checkable cards.
    profile_cards = [_profile_card(p, on_toggle) for p in profiles]
    grid_rows: list[ft.Control] = []
    for i in range(0, len(profile_cards), 2):
        pair = profile_cards[i:i + 2]
        while len(pair) < 2:
            pair.append(ft.Container(expand=1))
        grid_rows.append(
            ft.Row(
                spacing=10,
                controls=[ft.Container(expand=1, content=c) for c in pair],
            )
        )

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
                ft.Container(height=18),
                _col_header(ft.Icons.LABEL_OUTLINE, "EXISTING TAGS · ✕ removes a tag everywhere"),
                ft.Container(height=10),
                tag_cloud,
                ft.Container(height=24),
                _col_header(ft.Icons.SELL_OUTLINED, "ASSIGN A TAG"),
                ft.Container(height=10),
                ft.Row(spacing=10, controls=[tag_field, assign_btn]),
                ft.Container(height=20),
                _col_header(ft.Icons.PERSON_OUTLINE, "SELECT PROFILES"),
                ft.Container(height=10),
                ft.Column(spacing=10, controls=grid_rows),
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


def _tag_chip(tag: str, count: int, on_remove: Callable[[str], None]) -> ft.Container:
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.only(left=12, right=8, top=6, bottom=6),
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(tag, size=13, color=COLORS["accent"], font_family=MONO),
                ft.Container(
                    bgcolor=COLORS["card_hover"],
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=7, vertical=1),
                    content=ft.Text(
                        str(count), size=11, color=COLORS["text_sub"], font_family=MONO
                    ),
                ),
                ft.Container(
                    on_click=lambda _, t=tag: on_remove(t),
                    ink=True,
                    tooltip=f"Remove '{tag}' from every profile",
                    padding=ft.Padding.symmetric(horizontal=4, vertical=2),
                    content=ft.Text("✕", size=12, color=COLORS["error"], font_family=MONO),
                ),
            ],
        ),
    )


def _profile_card(
    profile: Profile, on_toggle: Callable[[str, bool], None]
) -> ft.Container:
    tag_str = ", ".join(profile.tags) if profile.tags else "no tags"
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.only(left=10, right=14, top=8, bottom=8),
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                _checkbox(lambda e, n=profile.name: on_toggle(n, e.control.value)),
                ft.Column(
                    spacing=1,
                    expand=True,
                    controls=[
                        ft.Text(
                            profile.name,
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color=COLORS["text_main"],
                            font_family=MONO,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            tag_str,
                            size=11,
                            color=COLORS["text_sub"],
                            font_family=MONO,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                ),
                ft.Text(
                    profile.os_type,
                    size=11,
                    color=COLORS["text_dim"],
                    font_family=MONO,
                ),
            ],
        ),
    )


def _empty(text: str) -> ft.Container:
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=18, vertical=24),
        content=ft.Text(text, size=13, color=COLORS["text_sub"], font_family=MONO),
    )
