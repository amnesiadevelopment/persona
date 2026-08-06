from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ...services.ssh.store import SSHHost
from ..theme.colors import COLORS
from ..theme.styles import MONO
from .ssh_page import build_ssh_section


def _bracket_toggle(
    on: bool,
    on_change: Callable[[bool], None],
    on_label: str = "enabled",
    off_label: str = "disabled",
) -> ft.Container:
    """A clickable on/off control in persona's bracket style — used instead of
    an iOS-looking ft.Switch so it matches the rest of the UI."""
    return ft.Container(
        on_click=lambda _: on_change(not on),
        ink=True,
        border_radius=3,
        border=ft.Border.all(
            1, COLORS["accent"] if on else COLORS["card_border"]
        ),
        bgcolor=(
            ft.Colors.with_opacity(0.12, COLORS["accent"])
            if on
            else COLORS["card_bg"]
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        content=ft.Text(
            f"[ {on_label if on else off_label} ]",
            size=12,
            color=COLORS["accent"] if on else COLORS["text_dim"],
            font_family=MONO,
            weight=ft.FontWeight.BOLD,
            no_wrap=True,
        ),
    )


def build_connect_page(
    profiles: list[Profile],
    token: str,
    add_command: str,
    config_json: str,
    on_toggle_ai: Callable[[str, bool], None],
    server_running: bool,
    on_toggle_server: Callable[[bool], None],
    endpoint: str,
    ssh_hosts: list[SSHHost],
    on_ssh_add: Callable,
    on_ssh_edit: Callable[[str], None],
    on_ssh_delete: Callable[[str], None],
    on_ssh_run: Callable[[str, str], tuple[int, str, str]],
) -> ft.Container:
    controls: list[ft.Control] = [
        _title("connect Claude"),
        ft.Container(height=4),
        ft.Text(
            "Let Claude (over MCP) drive persona. Off by default — nothing "
            "listens until you enable it.",
            size=13,
            color=COLORS["text_sub"],
            font_family=MONO,
        ),
        ft.Container(height=16),
        _server_card(server_running, endpoint, on_toggle_server),
    ]

    if server_running:
        controls += [
            ft.Container(height=18),
            _section_header("CONNECT YOUR CLIENT", ft.Icons.LINK),
            ft.Container(height=12),
            _copy_field("TOKEN", _token_field(token), token, ft.Icons.KEY_OUTLINED),
            ft.Container(height=12),
            _copy_field(
                "ONE-LINE ADD (claude cli)",
                _code(add_command, wrap=False),
                add_command,
                ft.Icons.TERMINAL,
            ),
            ft.Container(height=12),
            _copy_field(
                "CLIENT CONFIG (json)",
                _code(config_json, wrap=True),
                config_json,
                ft.Icons.DATA_OBJECT,
            ),
            ft.Divider(height=40, color=COLORS["border"]),
            _ai_section(profiles, on_toggle_ai),
        ]

    section, footer = build_ssh_section(
        ssh_hosts, on_ssh_add, on_ssh_edit, on_ssh_delete, on_ssh_run
    )
    controls += [ft.Divider(height=40, color=COLORS["border"]), section]
    controls.append(ft.Container(height=20))

    # The scrollable content always lives in an expanding region so long pages
    # (MCP enabled) scroll. When SSH has no hosts, the empty-state hint sits
    # below that region as a real footer pinned to the bottom of the page.
    scroller = ft.Column(
        spacing=0,
        scroll=ft.ScrollMode.AUTO,
        controls=controls,
    )
    page_controls: list[ft.Control] = [ft.Container(expand=True, content=scroller)]
    if footer is not None:
        page_controls.append(footer)

    return ft.Container(
        expand=True,
        bgcolor=COLORS["bg"],
        padding=ft.Padding.symmetric(horizontal=32, vertical=24),
        content=ft.Column(spacing=0, expand=True, controls=page_controls),
    )


def _server_card(
    running: bool,
    endpoint: str,
    on_toggle: Callable[[bool], None],
) -> ft.Container:
    status = (
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=7, height=7, border_radius=4, bgcolor=COLORS["accent"]
                ),
                ft.Text(
                    "running on",
                    size=11,
                    color=COLORS["text_sub"],
                    font_family=MONO,
                ),
                ft.Container(
                    border_radius=3,
                    border=ft.Border.all(1, COLORS["card_border"]),
                    bgcolor=COLORS["input_bg"],
                    padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                    content=ft.Text(
                        endpoint,
                        size=11,
                        color=COLORS["accent"],
                        font_family=MONO,
                        selectable=True,
                    ),
                ),
            ],
        )
        if running
        else ft.Text(
            "stopped", size=11, color=COLORS["text_dim"], font_family=MONO
        )
    )
    return ft.Container(
        border_radius=4,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=16, vertical=14),
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Column(
                            spacing=2,
                            tight=True,
                            controls=[
                                ft.Text(
                                    "Enable Claude control (MCP)",
                                    size=14,
                                    weight=ft.FontWeight.BOLD,
                                    color=COLORS["text_main"],
                                    font_family=MONO,
                                ),
                                ft.Text(
                                    "Allow Claude to list, create, launch and "
                                    "stop profiles over MCP.",
                                    size=11,
                                    color=COLORS["text_sub"],
                                    font_family=MONO,
                                ),
                            ],
                        ),
                        _bracket_toggle(running, on_toggle),
                    ],
                ),
                status,
            ],
        ),
    )


def _section_header(title: str, icon: str) -> ft.Row:
    return ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=8,
        controls=[
            ft.Icon(icon, size=14, color=COLORS["accent"]),
            ft.Text(
                title, size=11, color=COLORS["accent"],
                font_family=MONO, weight=ft.FontWeight.BOLD,
            ),
            ft.Container(expand=True, height=1, bgcolor=COLORS["border"]),
        ],
    )


def _copy_button(value: str) -> ft.Control:
    """A small [ copy ] that puts `value` on the clipboard (flet 0.85 async
    Clipboard service)."""
    btn = ft.OutlinedButton(
        "[ copy ]",
        height=28,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=3),
            side=ft.BorderSide(1, COLORS["card_border"]),
            color=COLORS["text_sub"],
            padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            text_style=ft.TextStyle(font_family=MONO, size=11),
        ),
    )

    async def on_copy(_: ft.ControlEvent) -> None:
        await ft.Clipboard().set(value)
        btn.content = ft.Text("[ copied ]", font_family=MONO, size=11, color=COLORS["success"])
        btn.update()

    btn.on_click = on_copy
    return btn


def _copy_field(label: str, body: ft.Control, copy_value: str, icon: str) -> ft.Container:
    return ft.Container(
        border_radius=4,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        content=ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(icon, size=13, color=COLORS["text_sub"]),
                                ft.Text(
                                    label, size=10, color=COLORS["text_sub"],
                                    font_family=MONO,
                                ),
                            ],
                        ),
                        _copy_button(copy_value),
                    ],
                ),
                body,
            ],
        ),
    )


def _token_field(token: str) -> ft.Control:
    shown = ft.Text(
        "•" * len(token),
        size=12,
        color=COLORS["accent"],
        font_family=MONO,
        selectable=True,
        expand=True,
    )
    revealed = {"on": False}

    def toggle(_: ft.ControlEvent) -> None:
        revealed["on"] = not revealed["on"]
        shown.value = token if revealed["on"] else "•" * len(token)
        eye.icon = (
            ft.Icons.VISIBILITY_OFF if revealed["on"] else ft.Icons.VISIBILITY
        )
        shown.update()
        eye.update()

    eye = ft.IconButton(
        icon=ft.Icons.VISIBILITY,
        icon_size=16,
        icon_color=COLORS["text_sub"],
        tooltip="Show / hide",
        on_click=toggle,
    )
    return ft.Row(
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[shown, eye],
    )


def _code(value: str, wrap: bool = True) -> ft.Control:
    # A one-line command stays on one line (ellipsis) so it doesn't run off the
    # card; the JSON config wraps so the whole block is readable. Either way the
    # [ copy ] button carries the full value.
    return ft.Container(
        border_radius=3,
        bgcolor=COLORS["input_bg"],
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        content=ft.Text(
            value,
            size=11,
            color=COLORS["text_main"],
            font_family=MONO,
            selectable=True,
            no_wrap=not wrap,
            overflow=ft.TextOverflow.ELLIPSIS if not wrap else None,
        ),
    )


def _title(text: str) -> ft.Text:
    return ft.Text(
        text,
        size=16,
        weight=ft.FontWeight.BOLD,
        color=COLORS["text_main"],
        font_family=MONO,
    )


def _ai_section(
    profiles: list[Profile], on_toggle: Callable[[str, bool], None]
) -> ft.Column:
    rows: list[ft.Control] = []
    for p in profiles:
        rows.append(
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        p.name,
                        size=13,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    _bracket_toggle(
                        getattr(p, "ai_control", False),
                        lambda want, n=p.name: on_toggle(n, want),
                        on_label="on",
                        off_label="off",
                    ),
                ],
            )
        )
    return ft.Column(
        spacing=10,
        controls=[
            ft.Text(
                "AI control per profile",
                size=16,
                weight=ft.FontWeight.BOLD,
                color=COLORS["text_main"],
                font_family=MONO,
            ),
            ft.Container(
                border_radius=3,
                border=ft.Border.all(1, COLORS["error"]),
                bgcolor=COLORS["card_bg"],
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                content=ft.Text(
                    "AI/CDP control leaves automation traces that anti-fraud "
                    "systems can detect — avoid on profiles that must appear "
                    "human.",
                    size=11,
                    color=COLORS["error"],
                    font_family=MONO,
                ),
            ),
            ft.Container(height=6),
            *(
                rows
                if rows
                else [
                    ft.Text(
                        "no profiles yet",
                        size=13,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    )
                ]
            ),
        ],
    )
