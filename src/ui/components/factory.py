from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IProfileManager
from ..refs import UIRefs
from ..theme.colors import COLORS


def build_ui_refs(
    pm: IProfileManager,
    on_change_page: Callable[[int], None],
    file_picker: ft.FilePicker,
) -> UIRefs:
    return UIRefs(
        stats_text=ft.Text(
            get_string("total_profiles", count=len(pm.profiles)),
            size=12,
            color=COLORS["text_sub"],
        ),
        running_text=ft.Text("", size=12, color=COLORS["text_dim"]),
        content_subtitle=ft.Text("", size=13, color=COLORS["text_sub"]),
        profile_list_area=ft.Column(
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        prev_btn=ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            icon_color=COLORS["text_sub"],
            disabled=True,
            on_click=lambda _: on_change_page(-1),
        ),
        next_btn=ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            icon_color=COLORS["text_sub"],
            disabled=True,
            on_click=lambda _: on_change_page(1),
        ),
        page_label=ft.Text(
            get_string("page_of", current=1, total=1),
            size=13,
            color=COLORS["text_sub"],
        ),
        bulk_bar=ft.Row(visible=False, spacing=0, controls=[]),
        file_picker=file_picker,
    )
