from collections.abc import Callable

import flet as ft

from ...services.ssh.store import SSHHost
from ..theme.colors import COLORS
from ..theme.styles import MONO

_DIRECT = "(direct)"


def _field(label: str, value: str = "", password: bool = False) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        password=password,
        can_reveal_password=password,
        text_style=ft.TextStyle(font_family=MONO),
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_radius=3,
    )


def open_ssh_host_dialog(
    page: ft.Page,
    host: SSHHost | None,
    profile_names: list[str],
    on_save: Callable[[SSHHost], str | None],
) -> None:
    name_f = _field("name", host.name if host else "")
    host_f = _field("host", host.host if host else "")
    port_f = _field("port", str(host.port) if host else "22")
    user_f = _field("username", host.username if host else "")
    key_f = _field("private key path", host.key_path if host else "")
    keypass_f = _field("key passphrase", host.key_passphrase if host else "", password=True)
    pass_f = _field("password", host.password if host else "", password=True)
    profile_dd = ft.Dropdown(
        label="route via profile (proxy)",
        value=(host.profile if host and host.profile else _DIRECT),
        options=[ft.dropdown.Option(_DIRECT)]
        + [ft.dropdown.Option(n) for n in profile_names],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
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
        title=ft.Text(
            "Edit SSH host" if host else "Add SSH host",
            size=20, weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"], font_family=MONO,
        ),
        content=ft.Container(
            width=480,
            content=ft.Column(
                tight=True, spacing=12, scroll=ft.ScrollMode.AUTO,
                controls=[
                    name_f, host_f,
                    ft.Row(controls=[port_f, user_f]),
                    profile_dd,
                    ft.Divider(height=8, color=COLORS["border"]),
                    ft.Text("auth — key and/or password",
                            size=12, color=COLORS["text_sub"], font_family=MONO),
                    key_f, keypass_f, pass_f,
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
