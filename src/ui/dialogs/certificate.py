import os
from collections.abc import Callable

import flet as ft

from ...services.cert.store import Certificate
from ..theme.colors import COLORS
from ..theme.styles import MONO


def _field(
    label: str,
    value: str = "",
    password: bool = False,
    prefix_icon: str | None = None,
) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        password=password,
        can_reveal_password=password,
        prefix_icon=prefix_icon,
        text_style=ft.TextStyle(font_family=MONO),
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_radius=3,
    )


def open_certificate_dialog(
    page: ft.Page,
    cert: Certificate | None,
    file_picker,
    on_save: Callable[[Certificate], str | None],
) -> None:
    """Add or edit an mTLS client certificate. The picked .p12/.pfx path is
    handed to on_save, which copies it into persona's certificate store; the
    file is never imported into the OS. on_save returns None on success or an
    error string to show."""
    name_f = _field("name", cert.name if cert else "")
    pass_f = _field(
        "certificate password",
        cert.password if cert else "",
        password=True,
        prefix_icon=ft.Icons.LOCK_OUTLINE,
    )
    url_f = _field(
        "admin URL",
        cert.url if cert else "",
        prefix_icon=ft.Icons.LOCK_OUTLINE,
    )
    # The picked source path lives here; editing keeps the existing one unless a
    # new file is chosen.
    state = {"p12_path": cert.p12_path if cert else ""}

    def _file_label_text() -> str:
        p = state["p12_path"]
        return os.path.basename(p) if p else "no file chosen"

    file_icon = ft.Icon(ft.Icons.VPN_KEY, size=18, color=COLORS["accent"])
    file_label = ft.Text(
        _file_label_text(), size=12, color=COLORS["text_sub"], font_family=MONO
    )

    async def choose(_: ft.ControlEvent) -> None:
        files = await file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["p12", "pfx"],
            dialog_title="Choose certificate (.p12/.pfx)",
        )
        if files and files[0].path:
            state["p12_path"] = files[0].path
            file_label.value = _file_label_text()
            page.update()

    err = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def save(_: ft.ControlEvent) -> None:
        name = (name_f.value or "").strip()
        if not name:
            err.value = "name is required"
            err.visible = True
            page.update()
            return
        if not state["p12_path"]:
            err.value = "choose a .p12/.pfx file"
            err.visible = True
            page.update()
            return
        c = Certificate(
            name=name,
            p12_path=state["p12_path"],
            password=pass_f.value or "",
            url=(url_f.value or "").strip(),
        )
        error = on_save(c)
        if error:
            err.value = error
            err.visible = True
            page.update()
        else:
            page.pop_dialog()

    choose_btn = ft.TextButton("[ choose file ]", on_click=choose)

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3, side=ft.BorderSide(1, COLORS["accent_dim"])
        ),
        bgcolor=COLORS["card_bg"],
        title=ft.Text(
            "Edit certificate" if cert else "Add certificate",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        content=ft.Container(
            width=480,
            content=ft.Column(
                tight=True,
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    name_f,
                    ft.Row(
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[file_icon, choose_btn, file_label],
                    ),
                    pass_f,
                    url_f,
                    err,
                ],
            ),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
            ft.TextButton("Save", on_click=save),
        ],
    )
    page.show_dialog(dlg)
