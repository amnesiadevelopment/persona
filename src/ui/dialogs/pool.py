from collections.abc import Callable

import flet as ft

from ...models.bookmark import Bookmark, Pool
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    field_label,
    labeled,
    section_header,
)


def open_pool_dialog(
    page: ft.Page,
    all_bookmarks: list[Bookmark],
    on_save: Callable[[str, list[str]], str | None],
    pool: Pool | None = None,
    preselected: list[str] | None = None,
) -> None:
    is_edit = pool is not None
    initial = set(pool.bookmark_names) if pool is not None else set(preselected or [])

    name_field = ft.TextField(
        value=pool.name if pool is not None else "",
        hint_text="e.g. verify-tools",
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        **DLG_INPUT_KWARGS,
    )
    error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    # Bookmarks as toggle chips (same as the profile dialog), so pool membership
    # matches the rest of the UI.
    selected: set[str] = set(n for n in initial if n in {b.name for b in all_bookmarks})
    chip_holders: dict[str, ft.Container] = {}

    def _chip_content(name: str, on: bool) -> ft.Control:
        return ft.Container(
            border_radius=3,
            border=ft.Border.all(
                1, COLORS["accent"] if on else COLORS["card_border"]
            ),
            bgcolor=(
                ft.Colors.with_opacity(0.12, COLORS["accent"])
                if on
                else COLORS["card_bg"]
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Text(
                name,
                size=13,
                color=COLORS["accent"] if on else COLORS["text_sub"],
                font_family=MONO,
            ),
        )

    def _toggle(name: str) -> None:
        if name in selected:
            selected.discard(name)
        else:
            selected.add(name)
        chip_holders[name].content = _chip_content(name, name in selected)
        page.update()

    chips: list[ft.Control] = []
    for b in all_bookmarks:
        holder = ft.Container(
            content=_chip_content(b.name, b.name in selected),
            on_click=lambda _, n=b.name: _toggle(n),
            ink=True,
        )
        chip_holders[b.name] = holder
        chips.append(holder)

    bookmarks_block: ft.Control = (
        ft.Row(spacing=8, wrap=True, controls=chips)
        if chips
        else ft.Text(
            "no bookmarks to add — create some first",
            size=12, color=COLORS["text_sub"], font_family=MONO,
        )
    )

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        error.visible = False
        if not name:
            error.value = "Pool name cannot be empty"
            error.visible = True
            page.update()
            return
        chosen = [b.name for b in all_bookmarks if b.name in selected]
        err = on_save(name, chosen)
        if err:
            error.value = err
            error.visible = True
            page.update()
        else:
            page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3, side=ft.BorderSide(1, COLORS["accent_dim"])
        ),
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.FOLDER_OUTLINED, size=22, color=COLORS["accent"]),
                ft.Text(
                    "Edit Pool" if is_edit else "Create Pool",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
            ],
        ),
        content=ft.Container(
            width=460,
            padding=ft.Padding.only(left=4, top=4, bottom=4, right=14),
            content=ft.Column(
                tight=True,
                spacing=16,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    section_header("POOL", icon=ft.Icons.FOLDER_OUTLINED),
                    labeled("Pool name", name_field, icon=ft.Icons.LABEL_OUTLINE),
                    error,
                    field_label("bookmarks in this pool"),
                    bookmarks_block,
                ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.END,
        actions=[
            ft.OutlinedButton(
                "[ cancel ]", height=38, style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ save ]" if is_edit else "[ create ]",
                height=38, style=ACCENT_STYLE, on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
