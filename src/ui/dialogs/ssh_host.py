from collections.abc import Callable

import flet as ft

from ...services.ssh.store import SSHHost
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)

_DIRECT = "(direct)"


def _field(value: str = "", hint: str = "", password: bool = False) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=hint,
        hint_style=ft.TextStyle(color=COLORS["text_dim"], font_family=MONO),
        password=password,
        can_reveal_password=password,
        **DLG_INPUT_KWARGS,
    )


def open_ssh_host_dialog(
    page: ft.Page,
    host: SSHHost | None,
    profile_names: list[str],
    on_save: Callable[[SSHHost], str | None],
) -> None:
    name_f = _field(host.name if host else "", hint="e.g. prod-box")
    host_f = _field(host.host if host else "", hint="host.example.com")
    port_f = _field(str(host.port) if host else "22", hint="22")
    user_f = _field(host.username if host else "", hint="root")
    key_f = _field(host.key_path if host else "", hint="~/.ssh/id_ed25519")
    keypass_f = _field(host.key_passphrase if host else "", hint="optional", password=True)
    pass_f = _field(host.password if host else "", hint="optional", password=True)
    profile_dd = ft.Dropdown(
        value=(host.profile if host and host.profile else _DIRECT),
        options=[ft.dropdown.Option(_DIRECT)]
        + [ft.dropdown.Option(n) for n in profile_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        text_style=ft.TextStyle(font_family=MONO),
        border_radius=3,
    )
    err = ft.Text("", size=12, color=COLORS["error"], visible=False)

    def save(_: ft.ControlEvent) -> None:
        try:
            port = int(port_f.value or "22")
        except ValueError:
            err.value = "port must be a number"
            err.visible = True
            page.update()
            return
        prof = profile_dd.value or _DIRECT
        h = SSHHost(
            name=(name_f.value or "").strip(),
            host=(host_f.value or "").strip(),
            port=port,
            username=(user_f.value or "").strip(),
            key_path=(key_f.value or "").strip(),
            key_passphrase=keypass_f.value or "",
            password=pass_f.value or "",
            profile="" if prof == _DIRECT else prof,
        )
        if not h.name or not h.host:
            err.value = "name and host are required"
            err.visible = True
            page.update()
            return
        error = on_save(h)
        if error:
            err.value = error
            err.visible = True
            page.update()
        else:
            page.pop_dialog()

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
                ft.Icon(ft.Icons.TERMINAL, size=22, color=COLORS["accent"]),
                ft.Text(
                    "Edit SSH host" if host else "Add SSH host",
                    size=20, weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"], font_family=MONO,
                ),
            ],
        ),
        content=ft.Container(
            width=460,
            padding=ft.Padding.only(left=4, top=4, bottom=4, right=14),
            content=ft.Column(
                tight=True, spacing=16, scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    section_header("CONNECTION", icon=ft.Icons.LAN_OUTLINED),
                    labeled("Name", name_f, icon=ft.Icons.LABEL_OUTLINE),
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=labeled("Host", host_f, icon=ft.Icons.DNS),
                            ),
                            ft.Container(
                                width=100,
                                content=labeled("Port", port_f, icon=ft.Icons.TAG),
                            ),
                        ],
                    ),
                    labeled("Username", user_f, icon=ft.Icons.PERSON_OUTLINE),
                    labeled(
                        "Route via profile (proxy)",
                        profile_dd,
                        icon=ft.Icons.PUBLIC,
                    ),
                    section_header("AUTH · key and/or password", icon=ft.Icons.VPN_KEY),
                    labeled("Private key path", key_f, icon=ft.Icons.KEY_OUTLINED),
                    labeled("Key passphrase", keypass_f, icon=ft.Icons.LOCK_OUTLINE),
                    labeled("Password", pass_f, icon=ft.Icons.LOCK_OUTLINE),
                    err,
                ],
            ),
        ),
        actions_alignment=ft.MainAxisAlignment.END,
        actions=[
            ft.OutlinedButton(
                "[ cancel ]", height=40, style=OUTLINE_STYLE,
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ save ]", height=40, style=ACCENT_STYLE, on_click=save,
            ),
        ],
    )
    page.show_dialog(dlg)
