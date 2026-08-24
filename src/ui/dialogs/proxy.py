import threading
from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IProxyService
from ...models.proxy import Proxy
from ...utils.proxy_parse import parse_proxy_line
from ...utils.proxy_parser import build_proxy_url, split_proxy_url
from ...utils.validation import PROXY_SCHEMES, validate_proxy_format
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import (
    ACCENT_STYLE,
    DLG_INPUT_KWARGS,
    MONO,
    OUTLINE_STYLE,
    labeled,
    section_header,
)

#: The Type dropdown IS validation.PROXY_SCHEMES — derived, never retyped.
#: That tuple is the single source of truth for what persona accepts, and this
#: was a second list that had drifted below it: it offered three of the six, so
#: socks4/socks4h/socks5h could be stored (by the API, by a hand-edited
#: proxies.json, by an import) and validated and launched, but never SELECTED
#: here. Two silent rewrites followed from that. Opening such a proxy to edit an
#: unrelated field fell the dropdown back to "socks5" and saved the downgrade —
#: for socks4 a proxy that can no longer connect, for socks5h a change of who
#: resolves the hostname on every path that reads the credential back
#: (verify.exit_guard wants socks5h; the Chromium seam already normalises it to
#: socks5 by itself, so the dropdown had nothing to protect). And a pasted
#: provider line whose scheme was not in the three left the dropdown on its
#: default, so `socks4://...` was accepted, understood by the parser, and saved
#: as socks5. Deriving here means adding a scheme to PROXY_SCHEMES still needs
#: no second edit, which is exactly what its own comment promises.
_SCHEMES = list(PROXY_SCHEMES)


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
    on_save: Callable[[str, str, str], str | None],
    proxy: Proxy | None = None,
    on_checked: Callable[..., None] | None = None,
    on_check_failed: Callable[[str], None] | None = None,
    ui: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    is_edit = proxy is not None
    fields = split_proxy_url(proxy.url) if proxy is not None else split_proxy_url("")

    _hint = ft.TextStyle(color=COLORS["text_dim"], font_family=MONO)
    paste_field = ft.TextField(
        hint_text="scheme://user:pass@host:port  — paste any provider line",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    name_field = ft.TextField(
        value=proxy.name if proxy is not None else "",
        hint_text="e.g. home-socks", hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    type_dd = ft.Dropdown(
        value=fields["scheme"] if fields["scheme"] in _SCHEMES else "socks5",
        options=[ft.dropdown.Option(s) for s in _SCHEMES],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        border_radius=3,
        text_style=ft.TextStyle(font_family=MONO),
    )
    host_field = ft.TextField(
        value=fields["host"], hint_text="proxy.example.com",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    port_field = ft.TextField(
        value=fields["port"], hint_text="1080",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    user_field = ft.TextField(
        value=fields["username"], hint_text="optional",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    pass_field = ft.TextField(
        value=fields["password"], hint_text="optional",
        hint_style=_hint, password=True, can_reveal_password=True,
        expand=True, **DLG_INPUT_KWARGS,
    )
    rotate_field = ft.TextField(
        value=proxy.rotate_url if proxy is not None else "",
        hint_text="provider endpoint that forces a new exit IP",
        hint_style=_hint, **DLG_INPUT_KWARGS,
    )
    name_error = ft.Text("", size=12, color=COLORS["error"], visible=False)
    addr_error = ft.Text("", size=12, color=COLORS["error"], visible=False)

    flag_holder = ft.Container(content=_initial_status_control(proxy))

    def on_paste(_: ft.ControlEvent) -> None:
        raw = (paste_field.value or "").strip()
        # A multi-line paste (several provider lines at once) used to be parsed as
        # ONE line and silently discarded — this dialog adds a single proxy, so
        # take the FIRST non-empty line and fill the fields from it rather than
        # dropping everything (audit6 LOW d). A true bulk import is out of scope.
        if "\n" in raw:
            first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
            raw = first
        if ":" not in raw:
            return
        parsed = parse_proxy_line(raw)
        if parsed is None or not parsed["ip"] or not parsed["port"]:
            return
        host_field.value = parsed["ip"]
        port_field.value = parsed["port"]
        user_field.value = parsed["login"]
        pass_field.value = parsed["password"]
        if parsed["scheme"] in _SCHEMES:
            type_dd.value = parsed["scheme"]
        if parsed["name"]:
            name_field.value = parsed["name"]
        if parsed["rotate_url"]:
            rotate_field.value = parsed["rotate_url"]
        paste_field.value = ""
        page.update()

    # on_change catches keystroke entry; on_blur catches a PASTE that some
    # platforms (macOS flet) don't emit a change event for — a bare "ip:port"
    # pasted then clicked away wasn't splitting into fields (#220). Both call the
    # same splitter, which no-ops when the field doesn't hold a proxy string.
    paste_field.on_change = on_paste
    paste_field.on_blur = on_paste

    def current_url() -> str:
        return build_proxy_url(
            type_dd.value or "socks5",
            (host_field.value or "").strip(),
            (port_field.value or "").strip(),
            (user_field.value or "").strip(),
            (pass_field.value or "").strip(),
        )

    check_btn = ft.OutlinedButton("[ check ]", height=38, style=OUTLINE_STYLE)

    async def on_copy(_: ft.ControlEvent) -> None:
        # Copy the whole proxy line to the clipboard so it can be pasted into
        # another tool or profile. Uses the assembled URL, matching what persona
        # stores.
        url = current_url()
        if not url or url.endswith("://") or "://:" in url:
            addr_error.value = "Enter host and port before copying"
            addr_error.color = COLORS["warning"]
            addr_error.visible = True
            page.update()
            return
        await ft.Clipboard().set(url)
        copy_btn.content = ft.Text("[ copied ]", font_family=MONO, color=COLORS["success"])
        page.update()
        # Revert the label after a beat so it doesn't stay "[ copied ]" and go
        # stale once the fields are edited (audit6 LOW e).
        import asyncio

        await asyncio.sleep(1.5)
        copy_btn.content = "[ copy proxy ]"
        page.update()

    copy_btn = ft.OutlinedButton(
        "[ copy proxy ]",
        height=38,
        style=OUTLINE_STYLE,
        tooltip="Copy the full proxy string to the clipboard",
        on_click=on_copy,
    )

    def on_check_result(
        success: bool,
        code: str,
        country: str,
        ip: str,
        tz: str,
        lat: float | None,
        lon: float | None,
        checked_url: str = "",
    ) -> None:
        # Only PERSIST a check result onto the stored proxy when the URL that was
        # actually checked equals the stored proxy.url. In edit mode the dialog
        # fields may hold an UNSAVED URL; persisting its geo (or a failure flag)
        # onto the stored record — which cancel won't undo — gives a proxy whose
        # exit is in one country but whose persisted geo/tz says another, a silent
        # fingerprint mismatch across every profile using it (audit6 #6). The flag
        # icon still reflects the live check either way; only the DB write is
        # gated.
        persist = proxy is not None and checked_url == proxy.url
        if success:
            flag_holder.content = _flag_control(code)
            if persist and on_checked is not None:
                on_checked(proxy.name, code, country, ip, tz, lat, lon)
        else:
            flag_holder.content = _fail_control()
            if persist and on_check_failed is not None:
                on_check_failed(proxy.name)

    def on_check_click(_: ft.ControlEvent) -> None:
        flag_holder.content = ft.ProgressRing(
            width=18, height=18, stroke_width=2, color=COLORS["accent"]
        )
        page.update()
        _do_check(
            page, current_url, addr_error, check_btn, proxy_service,
            on_check_result, ui=ui,
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

        error = on_save(name, url, (rotate_field.value or "").strip())
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
            width=460,
            padding=ft.Padding.only(left=4, top=4, bottom=4, right=14),
            content=ft.Column(
                tight=True,
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    # ---- QUICK PASTE ----
                    section_header("QUICK PASTE", icon=ft.Icons.CONTENT_PASTE),
                    labeled(
                        "Paste proxy string",
                        paste_field,
                        icon=ft.Icons.BOLT,
                    ),
                    # ---- DETAILS ----
                    section_header("DETAILS", icon=ft.Icons.LAN_OUTLINED),
                    labeled("Name", name_field, icon=ft.Icons.LABEL_OUTLINE),
                    name_error,
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                width=140,
                                content=labeled("Type", type_dd, icon=ft.Icons.TUNE),
                            ),
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Host", host_field, icon=ft.Icons.DNS
                                ),
                            ),
                            ft.Container(
                                width=110,
                                content=labeled(
                                    "Port", port_field, icon=ft.Icons.TAG
                                ),
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Username (optional)",
                                    user_field,
                                    icon=ft.Icons.PERSON_OUTLINE,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=labeled(
                                    "Password (optional)",
                                    pass_field,
                                    icon=ft.Icons.LOCK_OUTLINE,
                                ),
                            ),
                        ],
                    ),
                    labeled(
                        "Rotate URL (optional)",
                        rotate_field,
                        icon=ft.Icons.AUTORENEW,
                    ),
                    addr_error,
                    ft.Row(spacing=10, controls=[check_btn, copy_btn]),
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
            ft.Button(
                "[ save ]" if is_edit else "[ add ]",
                style=ACCENT_STYLE,
                height=40,
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
    on_result: Callable[..., None],
    ui: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    def _post(fn: Callable[[], None]) -> None:
        # Marshal UI mutations onto the flet session thread. The check runs on a
        # worker; touching controls / page.update() from it is the #124 freeze
        # class (audit6 #9). When no marshaler is supplied (older callers/tests),
        # run inline.
        if ui is not None:
            ui(fn)
        else:
            fn()

    url = current_url()
    if not url or "://:" in url or url.endswith("://"):
        # Pre-flight input validation: this is a "you didn't type a host/port"
        # message, NOT a proxy-check failure. Do NOT call on_result — flagging
        # the stored proxy as failed here permanently marked a working proxy bad
        # just because a field was momentarily blank mid-edit (audit6 #6).
        addr_error.value = "Enter host and port to check"
        addr_error.color = COLORS["warning"]
        addr_error.visible = True
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

        def apply() -> None:
            check_btn.content = ft.Text("[ check ]", font_family=MONO)
            check_btn.disabled = False
            addr_error.value = message
            addr_error.color = COLORS["success"] if success else COLORS["error"]
            addr_error.visible = True
            on_result(success, code, name, ip, tz, lat, lon, url)
            page.update()

        _post(apply)

    threading.Thread(target=do_check, daemon=True).start()
