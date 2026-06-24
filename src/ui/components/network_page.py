import time
from collections.abc import Callable

import flet as ft

from ...models.proxy import Proxy
from ...utils.proxy_parser import split_proxy_url
from ...utils.timefmt import humanize_since
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def build_network_page(
    proxies: list[Proxy],
    on_add: Callable,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_check: Callable[[str], None],
    checking: set[str] | None = None,
) -> ft.Container:
    checking = checking or set()
    now = time.time()
    rows: list[ft.Control] = (
        [_proxy_row(p, now, on_edit, on_delete, on_check, p.name in checking) for p in proxies]
        if proxies
        else [_empty()]
    )
    top = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        "proxies",
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    ft.Text(
                        str(len(proxies)),
                        size=14,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
            ft.Button(
                "[ + add proxy ]",
                width=160,
                height=40,
                style=ACCENT_STYLE,
                on_click=on_add,
            ),
        ],
    )
    return ft.Container(
        expand=True,
        bgcolor=COLORS["bg"],
        padding=ft.Padding.symmetric(horizontal=32, vertical=24),
        content=ft.Column(
            spacing=0,
            expand=True,
            controls=[
                top,
                ft.Container(height=20),
                ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=rows),
            ],
        ),
    )


def _flag_widget(proxy: Proxy, is_checking: bool) -> ft.Control:
    if is_checking:
        return ft.Container(
            width=26,
            height=18,
            alignment=ft.Alignment(0, 0),
            content=ft.ProgressRing(width=14, height=14, stroke_width=2, color=COLORS["accent"]),
        )
    if proxy.last_check_ok is False:
        return ft.Container(
            width=26,
            height=18,
            alignment=ft.Alignment(0, 0),
            content=ft.Text("✕", size=16, color=COLORS["error"], font_family=MONO),
        )
    path = flag_path(proxy.country_code)
    if path:
        return ft.Image(src=path, width=26, height=18)
    return ft.Container(
        width=26,
        height=18,
        border_radius=2,
        border=ft.Border.all(1, COLORS["card_border"]),
        alignment=ft.Alignment(0, 0),
        content=ft.Text("·", size=14, color=COLORS["text_dim"], font_family=MONO),
    )


def _meta_line(proxy: Proxy, now: float) -> str:
    parts = [split_proxy_url(proxy.url)["scheme"]]
    if proxy.country_name:
        code = f"[{proxy.country_code}] " if proxy.country_code else ""
        parts.append(f"{code}{proxy.country_name}")
    if proxy.last_ip:
        parts.append(proxy.last_ip)
    if proxy.last_check_ok is False and proxy.checked_at:
        parts.append(f"check failed {humanize_since(proxy.checked_at, now)}")
    elif proxy.checked_at:
        parts.append(f"checked {humanize_since(proxy.checked_at, now)}")
    else:
        parts.append("not checked yet")
    return "  ·  ".join(parts)


def _proxy_row(
    proxy: Proxy,
    now: float,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_check: Callable[[str], None],
    is_checking: bool,
) -> ft.Container:
    check_label = "[ ... ]" if is_checking else "[ check ]"
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=18, vertical=14),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        _flag_widget(proxy, is_checking),
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text(
                                    proxy.name,
                                    size=15,
                                    weight=ft.FontWeight.BOLD,
                                    color=COLORS["text_main"],
                                    font_family=MONO,
                                ),
                                ft.Text(
                                    _meta_line(proxy, now),
                                    size=11,
                                    color=COLORS["text_sub"],
                                    font_family=MONO,
                                ),
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        _btn(check_label, COLORS["accent"], lambda _, n=proxy.name: on_check(n), is_checking),
                        _btn("[ edit ]", COLORS["text_sub"], lambda _, n=proxy.name: on_edit(n)),
                        _btn("[ x ]", COLORS["error"], lambda _, n=proxy.name: on_delete(n)),
                    ],
                ),
            ],
        ),
    )


def _btn(label: str, color: str, handler: Callable, disabled: bool = False) -> ft.Button:
    return ft.Button(
        label,
        height=38,
        disabled=disabled,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=3),
            color=color,
            side=ft.BorderSide(1, COLORS["card_border"]),
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            text_style=ft.TextStyle(font_family=MONO, size=13),
        ),
        on_click=handler,
    )


def _empty() -> ft.Container:
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=18, vertical=30),
        content=ft.Text(
            "no proxies yet — add one to attach it to a profile",
            size=13,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
    )
