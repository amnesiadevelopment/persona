from collections.abc import Callable

import flet as ft

from ...models.bookmark import Bookmark
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, DLG_FIELD_KWARGS, MONO, OUTLINE_STYLE


def open_bookmark_dialog(
    page: ft.Page,
    on_save: Callable[[str, str], str | None],
    bookmark: Bookmark | None = None,
) -> None:
    is_edit = bookmark is not None
    name_field = ft.TextField(
        label="Name",
        value=bookmark.name if bookmark is not None else "",
        hint_text="e.g. browserleaks",
        expand=True,
        **DLG_FIELD_KWARGS,
    )
    url_field = ft.TextField(
        label="URL",
        value=bookmark.url if bookmark is not None else "",
        hint_text="https://example.com",
        expand=True,
        **DLG_FIELD_KWARGS,
    )
    error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        url = (url_field.value or "").strip()
        error.visible = False
        if not name:
            error.value = "Name cannot be empty"
            error.visible = True
            page.update()
            return
        if not url or "." not in url:
            error.value = "Enter a valid URL"
            error.visible = True
            page.update()
            return
        if "://" not in url:
            url = "https://" + url
        err = on_save(name, url)
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
            "Edit Bookmark" if is_edit else "Add Bookmark",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=560,
            content=ft.Column(
                tight=True,
                spacing=16,
                controls=[
                    ft.Container(height=2),
                    ft.Row(controls=[name_field]),
                    ft.Row(controls=[url_field]),
                    error,
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
                "[ save ]" if is_edit else "[ add ]",
                height=38, style=ACCENT_STYLE, on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
