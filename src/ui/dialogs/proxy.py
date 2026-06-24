import threading
from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IProxyService
from ...models.proxy import Proxy
from ...utils.proxy_parser import build_proxy_url, split_proxy_url
from ...utils.validation import validate_proxy_format
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, DLG_FIELD_KWARGS, MONO, OUTLINE_STYLE

_SCHEMES = ["socks5", "http", "https"]


def _flag_control(country_code: str) -> ft.Control:
    path = flag_path(country_code)
    if path:
        return ft.Image(src=path, width=28, height=20)
    return ft.Container(width=28, height=20)


def _fail_control() -> ft.Control:
    return ft.Container(
        width=28,
        height=20,
        alignment=ft.Alignment(0, 0),
        content=ft.Text("✕", size=18, color=COLORS["error"], font_family=MONO),
    )


def _initial_status_control(proxy: Proxy | None) -> ft.Control:
    if proxy is not None and proxy.last_check_ok is False:
        return _fail_control()
    return _flag_control(proxy.country_code if proxy is not None else "")


def open_proxy_dialog(
    page: ft.Page,
    proxy_service: IProxyService,
    on_save: Callable[[str, str], str | None],
    proxy: Proxy | None = None,
    on_checked: Callable[..., None] | None = None,
    on_check_failed: Callable[[str], None] | None = None,
) -> None:
    is_edit = proxy is not None
    fields = split_proxy_url(proxy.url) if proxy is not None else split_proxy_url("")

    paste_field = ft.TextField(
        label="Paste full proxy URL",
        hint_text="scheme://user:pass@host:port",
        **DLG_FIELD_KWARGS,
    )
    name_field = ft.TextField(
        label="Name",
        value=proxy.name if proxy is not None else "",
        hint_text="e.g. home-socks",
        **DLG_FIELD_KWARGS,
    )
    type_dd = ft.Dropdown(
        label="Type",
        value=fields["scheme"] if fields["scheme"] in _SCHEMES else "socks5",
        options=[ft.dropdown.Option(s) for s in _SCHEMES],
        width=130,
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        text_style=ft.TextStyle(font_family=MONO),
    )
    host_field = ft.TextField(
        label="Host", value=fields["host"], hint_text="proxy.example.com",
        expand=True, **DLG_FIELD_KWARGS,
    )
    port_field = ft.TextField(
        label="Port", value=fields["port"], hint_text="1080",
        width=110, **DLG_FIELD_KWARGS,
    )
    user_field = ft.TextField(
        label="Username (optional)", value=fields["username"], hint_text="optional",
        expand=True, **DLG_FIELD_KWARGS,
    )
    pass_field = ft.TextField(
        label="Password (optional)", value=fields["password"], hint_text="optional",
        password=True, can_reveal_password=True, expand=True, **DLG_FIELD_KWARGS,
    )
    name_error = ft.Text("", size=12, color=COLORS["error"], visible=False)
    addr_error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    flag_holder = ft.Container(content=_initial_status_control(proxy))

    def on_paste(_: ft.ControlEvent) -> None:
        raw = (paste_field.value or "").strip()
        if ":" not in raw:
            return
        parts = split_proxy_url(raw)
        if not parts["host"] or not parts["port"]:
            return
        host_field.value = parts["host"]
        port_field.value = parts["port"]
        user_field.value = parts["username"]
        pass_field.value = parts["password"]
        if parts["scheme"] in _SCHEMES:
            type_dd.value = parts["scheme"]
        paste_field.value = ""
        page.update()

    paste_field.on_change = on_paste

    def current_url() -> str:
        return build_proxy_url(
            type_dd.value or "socks5",
            (host_field.value or "").strip(),
            (port_field.value or "").strip(),
            (user_field.value or "").strip(),
            (pass_field.value or "").strip(),
        )

    check_btn = ft.OutlinedButton("[ check ]", height=38, style=OUTLINE_STYLE)

    def on_check_result(
        success: bool, code: str, country: str, ip: str, tz: str
    ) -> None:
        if success:
            flag_holder.content = _flag_control(code)
            if on_checked is not None and proxy is not None:
                on_checked(proxy.name, code, country, ip, tz, lat, lon)
        else:
            flag_holder.content = _fail_control()
            if on_check_failed is not None and proxy is not None:
                on_check_failed(proxy.name)

    def on_check_click(_: ft.ControlEvent) -> None:
        flag_holder.content = ft.ProgressRing(
            width=18, height=18, stroke_width=2, color=COLORS["accent"]
        )
        page.update()
        _do_check(
            page, current_url, addr_error, check_btn, proxy_service, on_check_result
        )

    check_btn.on_click = on_check_click

    def on_submit(_: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        name_error.visible = addr_error.visible = False

        if not name:
            name_error.value = "Name cannot be empty"
            name_error.visible = True
            page.update()
            return
        if not (host_field.value or "").strip() or not (port_field.value or "").strip():
            addr_error.value = "Host and port are required"
            addr_error.visible = True
            page.update()
            return

        url = current_url()
        valid, err = validate_proxy_format(url)
        if not valid:
            addr_error.value = err
            addr_error.visible = True
            page.update()
            return

        error = on_save(name, url)
        if error:
            name_error.value = error
            name_error.visible = True
            page.update()
        else:
            page.pop_dialog()

    dlg = ft.AlertDialog(
        modal=True,
        bgcolor=COLORS["card_bg"],
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                flag_holder,
                ft.Text(
                    "Edit Proxy" if is_edit else "Add Proxy",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
            ],
        ),
        content=ft.Container(
            width=480,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
            content=ft.Column(
                tight=True,
                spacing=16,
                controls=[
                    ft.Container(height=2),
                    paste_field,
                    ft.Divider(height=10, color=COLORS["border"]),
                    name_field,
                    name_error,
                    type_dd,
                    ft.Row(spacing=12, controls=[host_field, port_field]),
                    ft.Row(spacing=12, controls=[user_field, pass_field]),
                    addr_error,
                    check_btn,
                    ft.Container(height=10),
                ],
            ),
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                style=ft.ButtonStyle(color=COLORS["text_sub"]),
                on_click=lambda _: page.pop_dialog(),
            ),
            ft.Button(
                "[ save ]" if is_edit else "[ add ]",
                style=ACCENT_STYLE,
                on_click=on_submit,
            ),
        ],
    )
    page.show_dialog(dlg)


def _do_check(
    page: ft.Page,
    current_url: Callable[[], str],
    addr_error: ft.Text,
    check_btn: ft.OutlinedButton,
    proxy_service: IProxyService,
    on_result: Callable[[bool, str, str, str, str], None],
) -> None:
    url = current_url()
    if not url or "://:" in url or url.endswith("://"):
        addr_error.value = "Enter host and port to check"
        addr_error.color = COLORS["warning"]
        addr_error.visible = True
        on_result(False, "", "", "", "")
        page.update()
        return

    check_btn.content = ft.Text(get_string("proxy_checking"), font_family=MONO)
    check_btn.disabled = True
    addr_error.visible = False
    page.update()

    def do_check() -> None:
        success, message, code, name, ip, tz, lat, lon = proxy_service.check_proxy_detailed_sync(
            url
        )
        check_btn.content = ft.Text("[ check ]", font_family=MONO)
        check_btn.disabled = False
        addr_error.value = message
        addr_error.color = COLORS["success"] if success else COLORS["error"]
        addr_error.visible = True
        on_result(success, code, name, ip, tz)
        page.update()

    threading.Thread(target=do_check, daemon=True).start()
