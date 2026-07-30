import os
from collections.abc import Callable

import flet as ft

from ...services.cert.store import Certificate
from ..theme.colors import COLORS
from ..theme.styles import ACCENT_STYLE, MONO


def build_certificates_page(
    certs: list[Certificate],
    on_add: Callable,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> ft.Container:
    """mTLS client certificates. A certificate added here is presented by any
    profile it's assigned to when logging into an admin site — it lives inside
    persona and is installed into the browser at launch, never into the OS
    certificate store."""
    rows: list[ft.Control] = (
        [_cert_row(c, on_edit, on_delete) for c in certs] if certs else [_empty()]
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
                        "certificates",
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    ft.Text(
                        str(len(certs)),
                        size=14,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
            ft.Button(
                "[ + add certificate ]",
                width=200,
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


def _cert_row(
    c: Certificate,
    on_edit: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> ft.Control:
    fname = os.path.basename(c.p12_path) if c.p12_path else "(no file)"
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
                        ft.Text(
                            c.name,
                            size=14,
                            weight=ft.FontWeight.BOLD,
                            color=COLORS["text_main"],
                            font_family=MONO,
                        ),
                        ft.Text(
                            fname,
                            size=11,
                            color=COLORS["text_sub"],
                            font_family=MONO,
                        ),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.TextButton(
                            "[ edit ]",
                            on_click=lambda _, n=c.name: on_edit(n),
                        ),
                        ft.TextButton(
                            "[ x ]",
                            on_click=lambda _, n=c.name: on_delete(n),
                        ),
                    ],
                ),
            ],
        ),
    )


def _empty() -> ft.Control:
    return ft.Container(
        padding=30,
        content=ft.Text(
            "no certificates — add a .p12/.pfx to log into an admin site",
            size=13,
            color=COLORS["text_dim"],
            font_family=MONO,
        ),
    )
