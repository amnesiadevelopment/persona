from collections.abc import Callable

import flet as ft

from ...models.bookmark import Bookmark, Pool
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, DLG_FIELD_KWARGS, MONO, OUTLINE_STYLE


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
        label="Pool name",
        value=pool.name if pool is not None else "",
        hint_text="e.g. verify-tools",
        expand=True,
        **DLG_FIELD_KWARGS,
    )
    error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    checks: dict[str, ft.Checkbox] = {}
    rows: list[ft.Control] = []
    for b in all_bookmarks:
        cb = ft.Checkbox(
            value=b.name in initial,
            fill_color=COLORS["accent"],
            check_color=COLORS["bg"],
        )
        checks[b.name] = cb
        rows.append(
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    cb,
                    ft.Text(b.name, size=13, color=COLORS["text_main"], font_family=MONO),
                ],
            )
        )
    if not rows:
        rows.append(
            ft.Text(
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
        selected = [n for n, cb in checks.items() if cb.value]
        err = on_save(name, selected)
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
        title=ft.Text(
            "Edit Pool" if is_edit else "Create Pool",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=560,
            content=ft.Column(
                tight=True,
                spacing=14,
                controls=[
                    ft.Container(height=2),
                    ft.Row(controls=[name_field]),
                    error,
                    ft.Text(
                        "bookmarks in this pool:",
                        size=12, color=COLORS["text_sub"], font_family=MONO,
                    ),
                    ft.Container(
                        height=240,
                        border_radius=3,
                        border=ft.Border.all(1, COLORS["card_border"]),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                        content=ft.Column(
                            spacing=4, scroll=ft.ScrollMode.ALWAYS, controls=rows
                        ),
                    ),
                    ft.Container(height=6),
                ],
            ),
        ),
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
