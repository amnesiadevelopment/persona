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


def _checkbox_toggle(on: bool, label: str, on_change: Callable[[bool], None]) -> ft.Container:
    """A [x]/[ ] checkbox in persona's bracket style — reads unambiguously as a
    STATE (checked = enabled) rather than a button whose label could be misread
    as the action. Used for the per-profile AI toggle so 'on'/'off' can't be
    mistaken for 'press to turn on/off'."""
    return ft.Container(
        on_click=lambda _: on_change(not on),
        ink=True,
        border_radius=3,
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        content=ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    "[x]" if on else "[ ]",
                    size=13,
                    color=COLORS["accent"] if on else COLORS["text_dim"],
                    font_family=MONO,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    label,
                    size=12,
                    color=COLORS["accent"] if on else COLORS["text_sub"],
                    font_family=MONO,
                    no_wrap=True,
                ),
            ],
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
        # One reveal gate for every place the token appears — the TOKEN field AND
        # the cli/json snippets that embed it. Hiding the token but printing it in
        # plain sight two lines below defeats the point; the single eye toggle
        # masks/unmasks all of them together. Copy always carries the real value.
        reveal = _TokenReveal(token)
        controls += [
            ft.Container(height=18),
            _section_header("CONNECT YOUR CLIENT", ft.Icons.LINK),
            ft.Container(height=12),
            _copy_field(
                "TOKEN", reveal.field(), token, ft.Icons.KEY_OUTLINED
            ),
            ft.Container(height=12),
            _copy_field(
                "ONE-LINE ADD (claude cli)",
                reveal.code(add_command, wrap=False),
                add_command,
                ft.Icons.TERMINAL,
            ),
            ft.Container(height=12),
            _copy_field(
                "CLIENT CONFIG (json)",
                reveal.code(config_json, wrap=True),
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


class _TokenReveal:
    """A single hide/show gate for every place the MCP token appears: the TOKEN
    field, the cli command, and the JSON config. One eye toggles them all so the
    token is never left in plain sight in one snippet while masked in another.
    Masked text swaps the token for a run of dots; the [ copy ] buttons keep the
    real value regardless."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._dots = "•" * len(token)
        self._on = False
        # (control, full_text, masked_text) for every text that embeds the token
        self._items: list[tuple[ft.Text, str, str]] = []

    def _register(self, ctl: ft.Text, full: str) -> None:
        self._items.append((ctl, full, full.replace(self._token, self._dots)))

    def _toggle(self, _: ft.ControlEvent) -> None:
        self._on = not self._on
        for ctl, full, masked in self._items:
            ctl.value = full if self._on else masked
            ctl.update()
        self._eye.icon = (
            ft.Icons.VISIBILITY_OFF if self._on else ft.Icons.VISIBILITY
        )
        self._eye.update()

    def field(self) -> ft.Control:
        shown = ft.Text(
            self._dots, size=12, color=COLORS["accent"],
            font_family=MONO, selectable=True, expand=True,
        )
        self._register(shown, self._token)
        self._eye = ft.IconButton(
            icon=ft.Icons.VISIBILITY,
            icon_size=16,
            icon_color=COLORS["text_sub"],
            tooltip="Show / hide token",
            on_click=self._toggle,
        )
        return ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[shown, self._eye],
        )

    def code(self, value: str, wrap: bool = True) -> ft.Control:
        text = ft.Text(
            value.replace(self._token, self._dots),
            size=11,
            color=COLORS["text_main"],
            font_family=MONO,
            selectable=True,
            no_wrap=not wrap,
            overflow=ft.TextOverflow.ELLIPSIS if not wrap else None,
        )
        self._register(text, value)
        return ft.Container(
            border_radius=3,
            bgcolor=COLORS["input_bg"],
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=text,
        )


def _title(text: str) -> ft.Text:
    return ft.Text(
        text,
        size=16,
        weight=ft.FontWeight.BOLD,
        color=COLORS["text_main"],
        font_family=MONO,
    )


def _ai_profile_row(
    p: Profile, on_toggle: Callable[[str, bool], None]
) -> ft.Container:
    """One profile as a compact card: name on the left, the AI checkbox on the
    right, framed and highlighted when AI is on so the enabled ones stand out."""
    on = getattr(p, "ai_control", False)
    return ft.Container(
        border_radius=3,
        border=ft.Border.all(1, COLORS["accent_dim"] if on else COLORS["card_border"]),
        bgcolor=COLORS["card_bg"],
        padding=ft.Padding.only(left=14, top=6, bottom=6, right=6),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    p.name, size=13, color=COLORS["text_main"], font_family=MONO,
                ),
                _checkbox_toggle(
                    on, "AI", lambda want, n=p.name: on_toggle(n, want),
                ),
            ],
        ),
    )


def _ai_section(
    profiles: list[Profile], on_toggle: Callable[[str, bool], None]
) -> ft.Column:
    warning = ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=15, color=COLORS["error"]),
            ft.Text(
                "AI/CDP control leaves automation traces — avoid on profiles "
                "that must appear human.",
                size=11,
                color=COLORS["text_sub"],
                font_family=MONO,
                expand=True,
            ),
        ],
    )
    rows: list[ft.Control] = [_ai_profile_row(p, on_toggle) for p in profiles]
    return ft.Column(
        spacing=10,
        controls=[
            _section_header("AI CONTROL PER PROFILE", ft.Icons.SMART_TOY_OUTLINED),
            warning,
            ft.Container(height=2),
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
