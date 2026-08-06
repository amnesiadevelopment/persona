import os
from collections.abc import Callable

import flet as ft

from ...services.cert.store import Certificate
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)


def _field(
    value: str = "",
    hint: str = "",
    password: bool = False,
) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=hint,
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        password=password,
        can_reveal_password=password,
        **DLG_INPUT_KWARGS,
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
    name_f = _field(cert.name if cert else "", hint="e.g. acme-admin")
    pass_f = _field(
        cert.password if cert else "",
        hint="the .p12 export password",
        password=True,
    )
    url_f = _field(
        cert.url if cert else "",
        hint="https://admin.example.com",
    )
    # The picked source path lives here; editing keeps the existing one unless a
    # new file is chosen.
    state = {"p12_path": cert.p12_path if cert else ""}

    def _file_label_text() -> str:
        p = state["p12_path"]
        return os.path.basename(p) if p else "no file chosen"

    file_icon = ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=18, color=COLORS["accent"])
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

    choose_btn = ft.OutlinedButton(
        "[ choose file ]", height=38, style=OUTLINE_STYLE, on_click=choose
    )
    # The file picker, framed like a normal input so it sits in the labelled
    # stack with everything else.
    file_box = ft.Container(
        border=ft.Border.all(1, COLORS["card_border"]),
        border_radius=3,
        bgcolor=COLORS["input_bg"],
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        content=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[file_icon, choose_btn, file_label],
        ),
    )

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3, side=ft.BorderSide(1, COLORS["accent_dim"])
        ),
        bgcolor=COLORS["card_bg"],
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=22, color=COLORS["accent"]),
                ft.Text(
                    "Edit certificate" if cert else "Add certificate",
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
                    section_header("CERTIFICATE", icon=ft.Icons.DESCRIPTION_OUTLINED),
                    labeled("Name", name_f, icon=ft.Icons.BADGE_OUTLINED),
                    labeled(
                        "Certificate file (.p12 / .pfx)",
                        file_box,
                        icon=ft.Icons.UPLOAD_FILE,
                    ),
                    labeled(
                        "Certificate password",
                        pass_f,
                        icon=ft.Icons.LOCK_OUTLINE,
                    ),
                    labeled(
                        "Admin URL",
                        url_f,
                        hint=ft.Text(
                            "the one site this certificate authenticates to",
                            size=11,
                            color=COLORS["text_sub"],
                            font_family=MONO,
                        ),
                        icon=ft.Icons.LINK,
                    ),
                    err,
                ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.END,
        actions=[
            ft.OutlinedButton(
                "[ cancel ]",
                style=OUTLINE_STYLE,
                height=40,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button("[ save ]", style=ACCENT_STYLE, height=40, on_click=save),
        ],
    )
    page.show_dialog(dlg)
