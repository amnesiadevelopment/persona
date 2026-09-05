import time
from collections.abc import Callable

import flet as ft

from ...models.proxy import Proxy
from ...services.browser.launch_policy import (
    UNLAUNCHABLE_DECLARABLE,
    proxy_unlaunchable_remedy,
)
from ...utils.proxy_parser import split_proxy_url
from ...utils.timefmt import humanize_since
from ..flags import flag_path
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO, row_button


def build_network_page(
    proxies: list[Proxy],
    on_add: Callable,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_check: Callable[[str], None],
    on_rotate: Callable[[str], None],
    checking: set[str] | None = None,
) -> ft.Container:
    checking = checking or set()
    now = time.time()
    rows: list[ft.Control] = (
        [
            _proxy_row(p, now, on_edit, on_delete, on_check, on_rotate, p.name in checking)
            for p in proxies
        ]
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


# `unlaunchable` is REQUIRED, with no default, deliberately: a default of
# False is exactly the silent-healthy render this ticket exists to remove,
# and a future caller that forgets the argument should fail loudly at the
# call site rather than quietly draw a clean flag on a stuck proxy.
def _flag_widget(proxy: Proxy, is_checking: bool, unlaunchable: bool) -> ft.Control:
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
        flag: ft.Control = ft.Image(src=path, width=26, height=18)
        if unlaunchable:
            # THE FLAG STOPS BEING THE WHOLE STORY. It still says where the
            # exit is — that part was never wrong and the operator needs it —
            # but a badge sits on it, so a passing check that cannot launch is
            # no longer PIXEL-IDENTICAL to a working proxy in a list of twenty.
            # The meta line below carries the sentence; this is what makes the
            # row worth reading at a glance.
            return ft.Stack(
                width=26,
                height=18,
                controls=[
                    flag,
                    ft.Container(
                        alignment=ft.Alignment(1, -1),
                        content=ft.Text(
                            "!",
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=COLORS["warning"],
                            font_family=MONO,
                        ),
                    ),
                ],
            )
        return flag
    if unlaunchable:
        return ft.Container(
            width=26,
            height=18,
            alignment=ft.Alignment(0, 0),
            content=ft.Text("!", size=16, color=COLORS["warning"], font_family=MONO),
        )
    return ft.Container(
        width=26,
        height=18,
        border_radius=2,
        border=ft.Border.all(1, COLORS["card_border"]),
        alignment=ft.Alignment(0, 0),
        content=ft.Text("·", size=14, color=COLORS["text_dim"], font_family=MONO),
    )


#: What the row says when a PASSING check still cannot launch a profile AND the
#: operator can fix it here. It names the missing thing and the remedy they can
#: actually reach, and it deliberately does NOT read as an invitation to
#: re-check: the check already passed and will keep passing (see refusal.py's
#: ``_UNDERIVABLE``, which makes the same choice for the profile card's label).
UNLAUNCHABLE_NOTE = "cannot launch: set the exit timezone in [ edit ]"

#: What the row says when the launch is refused and NO declaration can fix it —
#: the exit's country is outside the product's geography tables entirely, so
#: the locale gate refuses whatever zone is typed.
#:
#: ⚠️ IT NAMES NO GESTURE, and that is the whole reason it exists as a second
#: sentence rather than a re-wording of the first. Since PS-240 made
#: ``_COUNTRY_TZ`` and ``_COUNTRY_LOCALE`` set-equal, this is the population the
#: unlaunchable indication actually fires on, and showing them
#: ``set the exit timezone in [ edit ]`` sends them to type a zone that is
#: accepted, stored, and still does not launch — the "remedy that LOOPS" this
#: whole ticket exists to end, one gate further along. Their real remedy is a
#: ``_COUNTRY_TZ`` + ``_COUNTRY_LOCALE`` pair, which is a code change by a
#: different person (PS-240's lane), so the honest thing the row can do is name
#: the state and stop. Like its sibling it is not a re-check prompt.
UNSUPPORTED_COUNTRY_NOTE = "cannot launch: this exit country is not supported yet"


def _meta_line(proxy: Proxy, now: float, remedy: str | None) -> str:
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
    # THE SENTENCE BRANCHES, THE BADGE DOES NOT. Both reasons mean the profile
    # will not launch, so both get the badge; only one of them has a gesture
    # the operator can perform, so only that one names it.
    if remedy == UNLAUNCHABLE_DECLARABLE:
        parts.append(UNLAUNCHABLE_NOTE)
    elif remedy is not None:
        parts.append(UNSUPPORTED_COUNTRY_NOTE)
    return "  ·  ".join(parts)


def _proxy_row(
    proxy: Proxy,
    now: float,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_check: Callable[[str], None],
    on_rotate: Callable[[str], None],
    is_checking: bool,
) -> ft.Container:
    check_label = "[ ... ]" if is_checking else "[ check ]"
    # ONE call to the launch path's own owner, once per row, feeding both the
    # badge and the sentence — never two independent opinions about the same
    # state. It returns WHY rather than merely WHETHER, because the two reasons
    # need different sentences and the same badge.
    remedy = None if is_checking else proxy_unlaunchable_remedy(proxy)
    unlaunchable = remedy is not None
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
                        _flag_widget(proxy, is_checking, unlaunchable),
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
                                    _meta_line(proxy, now, remedy),
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
                        row_button(check_label, lambda _, n=proxy.name: on_check(n), disabled=is_checking),
                        row_button("[ rotate ]", lambda _, n=proxy.name: on_rotate(n), kind="rotate", disabled=is_checking),
                        row_button("[ edit ]", lambda _, n=proxy.name: on_edit(n), kind="edit"),
                        row_button("[ x ]", lambda _, n=proxy.name: on_delete(n), kind="delete"),
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
