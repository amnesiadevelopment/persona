from collections.abc import Callable

import flet as ft

from ...models.bookmark import Bookmark, Pool
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def build_bookmarks_page(
    bookmarks: list[Bookmark],
    pools: list[Pool],
    on_add_bookmark: Callable,
    on_edit_bookmark: Callable[[str], None],
    on_delete_bookmark: Callable[[str], None],
    on_make_pool: Callable[[list[str]], None],
    on_edit_pool: Callable[[str], None],
    on_delete_pool: Callable[[str], None],
) -> ft.Container:
    checked: set[str] = set()
    make_pool_btn = ft.Button(
        "[ make pool from selected ]",
        height=38,
        style=ACCENT_STYLE,
        disabled=True,
    )

    def on_toggle(name: str, value: bool) -> None:
        if value:
            checked.add(name)
        else:
            checked.discard(name)
        make_pool_btn.disabled = not checked
        make_pool_btn.update()

    make_pool_btn.on_click = lambda _: on_make_pool(sorted(checked))

    bm_rows: list[ft.Control] = (
        [_bookmark_row(b, on_toggle, on_edit_bookmark, on_delete_bookmark) for b in bookmarks]
        if bookmarks
        else [_empty("no bookmarks yet — add one to attach it to a profile")]
    )
    pool_rows: list[ft.Control] = (
        [_pool_row(p, on_edit_pool, on_delete_pool) for p in pools]
        if pools
        else [_empty("no pools yet — check bookmarks above and make one")]
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
                _section_header("bookmarks", len(bookmarks), "[ + add bookmark ]", on_add_bookmark),
                ft.Container(height=14),
                ft.Column(spacing=10, controls=bm_rows),
                ft.Container(height=12),
                ft.Row(controls=[make_pool_btn]),
                ft.Divider(height=40, color=COLORS["border"]),
                ft.Text(
                    "pools",
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
                ft.Container(height=14),
                ft.Column(spacing=10, controls=pool_rows),
                ft.Container(height=20),
            ],
        ),
    )


def _section_header(
    title: str, count: int, btn_label: str, on_add: Callable
) -> ft.Row:
    return ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
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
                    ft.Text(
                        str(count),
                        size=14,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
            ft.Button(btn_label, width=190, height=40, style=ACCENT_STYLE, on_click=on_add),
        ],
    )


def _bookmark_row(
    bookmark: Bookmark,
    on_toggle: Callable[[str, bool], None],
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> ft.Container:
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=12),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                    controls=[
                        ft.Checkbox(
                            fill_color=COLORS["accent"],
                            check_color=COLORS["bg"],
                            on_change=lambda e, n=bookmark.name: on_toggle(n, e.control.value),
                        ),
                        ft.Column(
                            spacing=2,
                            expand=True,
                            controls=[
                                ft.Text(
                                    bookmark.name,
                                    size=15,
                                    weight=ft.FontWeight.BOLD,
                                    color=COLORS["text_main"],
                                    font_family=MONO,
                                ),
                                ft.Text(
                                    bookmark.url,
                                    size=11,
                                    color=COLORS["text_sub"],
                                    font_family=MONO,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        _btn("[ edit ]", COLORS["text_sub"], lambda _, n=bookmark.name: on_edit(n)),
                        _btn("[ x ]", COLORS["error"], lambda _, n=bookmark.name: on_delete(n)),
                    ],
                ),
            ],
        ),
    )


def _pool_row(
    pool: Pool,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> ft.Container:
    summary = ", ".join(pool.bookmark_names) if pool.bookmark_names else "empty"
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=12),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                    controls=[
                        ft.Icon(ft.Icons.FOLDER_OUTLINED, size=18, color=COLORS["accent"]),
                        ft.Column(
                            spacing=2,
                            expand=True,
                            controls=[
                                ft.Text(
                                    pool.name,
                                    size=15,
                                    weight=ft.FontWeight.BOLD,
                                    color=COLORS["text_main"],
                                    font_family=MONO,
                                ),
                                ft.Text(
                                    f"{len(pool.bookmark_names)} · {summary}",
                                    size=11,
                                    color=COLORS["text_sub"],
                                    font_family=MONO,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        _btn("[ edit ]", COLORS["text_sub"], lambda _, n=pool.name: on_edit(n)),
                        _btn("[ x ]", COLORS["error"], lambda _, n=pool.name: on_delete(n)),
                    ],
                ),
            ],
        ),
    )


def _btn(label: str, color: str, handler: Callable) -> ft.Button:
    return ft.Button(
        label,
        height=38,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=3),
            color=color,
            side=ft.BorderSide(1, COLORS["card_border"]),
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            text_style=ft.TextStyle(font_family=MONO, size=13),
        ),
        on_click=handler,
    )


def _empty(text: str) -> ft.Container:
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=18, vertical=24),
        content=ft.Text(text, size=13, color=COLORS["text_sub"], font_family=MONO),
    )
