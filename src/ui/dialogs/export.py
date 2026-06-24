from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO, OUTLINE_STYLE

_LABEL = ft.TextStyle(font_family=MONO, color=COLORS["text_main"])


def _checkbox(label: str, value: bool) -> ft.Checkbox:
    return ft.Checkbox(
        label=label,
        value=value,
        label_style=_LABEL,
        fill_color=COLORS["accent"],
        check_color="#000000",
        border_side=ft.BorderSide(1.5, COLORS["card_border"]),
    )


def open_export_dialog(
    page: ft.Page,
    file_picker: ft.FilePicker,
    profiles: list[Profile],
    on_complete: Callable[[list[str], str, bool], None],
) -> None:
    if not profiles:
        return

    names = [p.name for p in profiles]
    checkboxes = [_checkbox(n, False) for n in names]
    include_data_cb = _checkbox("Include browser data", True)

    async def on_export(_: ft.ControlEvent) -> None:
        selected = [cb.label for cb in checkboxes if cb.value]
        if not selected:
            return
        include_data = include_data_cb.value
        page.pop_dialog()
        dir_path = await file_picker.get_directory_path(
            dialog_title="Select export directory",
        )
        if dir_path:
            on_complete(selected, dir_path, include_data)

    def sync_select_all() -> None:
        select_all_cb.value = all(cb.value for cb in checkboxes)
        select_all_cb.tristate = any(cb.value for cb in checkboxes) and not all(
            cb.value for cb in checkboxes
        )

    def on_profile_change(_: ft.ControlEvent) -> None:
        sync_select_all()
        page.update()

    for cb in checkboxes:
        cb.on_change = on_profile_change

    def toggle_all(e: ft.ControlEvent) -> None:
        target = e.control.value
        if target is None:
            target = True
        for cb in checkboxes:
            cb.value = target
        select_all_cb.value = target
        select_all_cb.tristate = False
        page.update()

    select_all_cb = _checkbox("Select all", False)
    select_all_cb.tristate = False
    select_all_cb.on_change = toggle_all

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text(
            "Export Profiles",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=360,
            content=ft.Column(
                tight=True,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    ft.Text(
                        "choose profiles to export",
                        size=12,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                    ft.Container(height=4),
                    select_all_cb,
                    ft.Divider(height=1, color=COLORS["border"]),
                    *checkboxes,
                    ft.Container(height=8),
                    include_data_cb,
                ],
            ),
        ),
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                height=38,
                style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ export ]",
                style=ACCENT_STYLE,
                on_click=on_export,
            ),
        ],
    )
    page.show_dialog(dlg)
