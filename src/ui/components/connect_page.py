from collections.abc import Callable

import flet as ft

from ...models.profile import Profile
from ..theme.colors import COLORS
from ..theme.styles import MONO


def build_connect_page(
    profiles: list[Profile],
    token: str,
    add_command: str,
    config_json: str,
    on_toggle_ai: Callable[[str, bool], None],
    server_running: bool,
    on_toggle_server: Callable[[bool], None],
    endpoint: str,
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
            ft.Container(height=14),
            _detail_card("token", _token_field(token)),
            ft.Container(height=12),
            _detail_card("example request", _code(add_command)),
            ft.Container(height=12),
            _detail_card("or add to your MCP client config", _code(config_json)),
            ft.Divider(height=40, color=COLORS["border"]),
            _ai_section(profiles, on_toggle_ai),
        ]

    controls.append(ft.Container(height=20))

    return ft.Container(
        expand=True,
        bgcolor=COLORS["bg"],
        padding=ft.Padding.symmetric(horizontal=32, vertical=24),
        content=ft.Column(
            spacing=0, expand=True, scroll=ft.ScrollMode.AUTO, controls=controls
        ),
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
                        ft.Switch(
                            value=running,
                            active_color=COLORS["accent"],
                            on_change=lambda e: on_toggle(e.control.value),
                        ),
                    ],
                ),
                status,
            ],
        ),
    )


def _detail_card(label: str, body: ft.Control) -> ft.Container:
    return ft.Container(
        border_radius=4,
        border=ft.Border.all(1, COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        content=ft.Column(
            spacing=8,
            controls=[
                ft.Text(
                    label.upper(),
                    size=10,
                    color=COLORS["text_sub"],
                    font_family=MONO,
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


def _code(value: str) -> ft.Control:
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
                    ft.Switch(
                        value=getattr(p, "ai_control", False),
                        active_color=COLORS["accent"],
                        on_change=lambda e, n=p.name: on_toggle(
                            n, e.control.value
                        ),
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
