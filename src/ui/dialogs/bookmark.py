from collections.abc import Callable

import flet as ft

from ...models.bookmark import Bookmark
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)


def open_bookmark_dialog(
    page: ft.Page,
    on_save: Callable[[str, str], str | None],
    bookmark: Bookmark | None = None,
) -> None:
    is_edit = bookmark is not None
    _hint = ft.TextStyle(color=COLORS["text_dim"], font_family=MONO)
    name_field = ft.TextField(
        value=bookmark.name if bookmark is not None else "",
        hint_text="e.g. browserleaks",
        hint_style=_hint,
        **DLG_INPUT_KWARGS,
    )
    url_field = ft.TextField(
        value=bookmark.url if bookmark is not None else "",
        hint_text="https://example.com",
        hint_style=_hint,
        **DLG_INPUT_KWARGS,
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
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.BOOKMARK_BORDER, size=22, color=COLORS["accent"]),
                ft.Text(
                    "Edit Bookmark" if is_edit else "Add Bookmark",
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
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    section_header("BOOKMARK", icon=ft.Icons.BOOKMARK_BORDER),
                    labeled("Name", name_field, icon=ft.Icons.LABEL_OUTLINE),
                    labeled("URL", url_field, icon=ft.Icons.LINK),
                    error,
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
                "[ save ]" if is_edit else "[ add ]",
                height=38, style=ACCENT_STYLE, on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)
