from collections.abc import Callable

import flet as ft

from ...services.ssh.store import SSHHost
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def build_ssh_page(
    hosts: list[SSHHost],
    on_add: Callable,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_run: Callable[[str, str], tuple[int, str, str]],
) -> ft.Container:
    """SSH page: saved hosts + a command runner whose output is shown inline.

    on_run(host_name, command) -> (exit, stdout, stderr), already routed through
    the host's profile proxy by the caller.
    """
    output = ft.Text(
        "",
        size=12,
        color=COLORS["text_sub"],
        font_family=MONO,
        selectable=True,
    )
    cmd_field = ft.TextField(
        label="command",
        text_style=ft.TextStyle(font_family=MONO),
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_radius=3,
        expand=True,
    )
    target_host = ft.Dropdown(
        label="host",
        value=hosts[0].name if hosts else None,
        options=[ft.dropdown.Option(h.name) for h in hosts],
        bgcolor=COLORS["input_bg"],
        color=COLORS["text_main"],
        border_color=COLORS["card_border"],
        focused_border_color=COLORS["accent"],
        label_style=ft.TextStyle(color=COLORS["text_sub"], font_family=MONO),
        text_style=ft.TextStyle(font_family=MONO),
        border_radius=3,
        width=200,
    )

    def run(_: ft.ControlEvent) -> None:
        host = target_host.value
        cmd = (cmd_field.value or "").strip()
        if not host or not cmd:
            return
        output.value = f"$ {cmd}\n…"
        output.update()
        try:
            code, out, err = on_run(host, cmd)
            body = out + (("\n[stderr]\n" + err) if err else "")
            output.value = f"$ {cmd}\n{body}\n(exit {code})"
        except Exception as e:  # connection/auth errors surface here
            output.value = f"$ {cmd}\n[error] {e}"
        output.update()

    runner = ft.Column(
        spacing=10,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.END,
                controls=[
                    target_host,
                    cmd_field,
                    ft.Button(
                        "[ run ]", height=48, width=90,
                        style=ACCENT_STYLE,
                        on_click=run,
                    ),
                ],
            ),
            ft.Container(
                content=output,
                bgcolor=COLORS["log_bg"],
                border_radius=6,
                padding=12,
                height=260,
            ),
        ],
    )

    host_rows: list[ft.Control] = (
        [_host_row(h, on_edit, on_delete) for h in hosts] if hosts else [_empty()]
    )

    top = ft.Row(
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Row(
                spacing=10,
                controls=[
                    ft.Text("ssh", size=16, weight=ft.FontWeight.BOLD,
                            color=COLORS["text_main"], font_family=MONO),
                    ft.Text(str(len(hosts)), size=14,
                            color=COLORS["text_sub"], font_family=MONO),
                ],
            ),
            ft.Button("[ + add host ]", width=150, height=40,
                      style=ACCENT_STYLE, on_click=lambda _: on_add()),
        ],
    )

    return ft.Container(
        padding=20,
        content=ft.Column(
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                top,
                runner,
                ft.Divider(height=12, color=COLORS["border"]),
                ft.Column(spacing=8, controls=host_rows),
            ],
        ),
    )


def _host_row(
    h: SSHHost, on_edit: Callable[[str], None], on_delete: Callable[[str], None]
) -> ft.Control:
    via = f" · via {h.profile}" if h.profile else " · direct"
    return ft.Container(
        bgcolor=COLORS["card_bg"],
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text(h.name, size=14, weight=ft.FontWeight.BOLD,
                                color=COLORS["text_main"], font_family=MONO),
                        ft.Text(
                            f"{h.username}@{h.host}:{h.port}{via}",
                            size=11, color=COLORS["text_sub"], font_family=MONO),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.TextButton("[ edit ]",
                                      on_click=lambda _, n=h.name: on_edit(n)),
                        ft.TextButton("[ x ]",
                                      on_click=lambda _, n=h.name: on_delete(n)),
                    ],
                ),
            ],
        ),
    )


def _empty() -> ft.Control:
    return ft.Container(
        padding=30,
        content=ft.Text(
            "no saved SSH hosts — add one to connect through a profile's proxy",
            size=13, color=COLORS["text_dim"], font_family=MONO),
    )
