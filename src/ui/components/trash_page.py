"""The trash page: what was deleted, and the two ways out of it.

Deleting anything in persona now moves it here instead of destroying it. This
page is where the operator sees that, restores from it, or — deliberately, and
separately — destroys something for good.

Two honesty rules this page exists to keep, both from the Honest-interface
direction's "no claim outlives the code it describes":

* A trashed record with secret material (a proxy's SOCKS5 creds, an SSH host's
  password, a certificate's .p12) is labelled as still holding it. Trashing did
  not shred it; only permanent deletion does, and the row says so rather than
  letting the operator assume otherwise.
* Every row shows when it was deleted and when it expires, because retention is
  a floor beneath a mis-click, not an archive — the operator can see the clock.
"""

import time
from collections.abc import Callable

import flet as ft

from ...services.trash.store import RETENTION_DAYS
from ...utils.timefmt import humanize_since
from ..theme.colors import COLORS
from ..theme.styles import ERROR_STYLE, MONO, row_button

#: Icon per record kind, so the mixed list is scannable at a glance.
_KIND_ICONS = {
    "profile": ft.Icons.PERSON_OUTLINE,
    "bookmark": ft.Icons.BOOKMARK_BORDER,
    "pool": ft.Icons.FOLDER_OUTLINED,
    "proxy": ft.Icons.LAN_OUTLINED,
    "ssh_host": ft.Icons.TERMINAL,
    "certificate": ft.Icons.DESCRIPTION_OUTLINED,
}


def build_trash_page(
    entries: list,
    on_restore: Callable[[str], None],
    on_delete_permanently: Callable[[str], None],
    on_empty: Callable[[], None],
    now: float | None = None,
) -> ft.Container:
    current = time.time() if now is None else now
    rows: list[ft.Control] = (
        [
            _entry_row(e, on_restore, on_delete_permanently, current)
            for e in entries
        ]
        if entries
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
                        "trash",
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=COLORS["text_main"],
                        font_family=MONO,
                    ),
                    ft.Text(
                        str(len(entries)),
                        size=14,
                        color=COLORS["text_sub"],
                        font_family=MONO,
                    ),
                ],
            ),
            ft.Button(
                "[ empty trash ]",
                width=200,
                height=40,
                style=ERROR_STYLE,
                disabled=not entries,
                on_click=lambda _: on_empty(),
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
                ft.Container(height=6),
                ft.Text(
                    f"Deleted items are kept for {RETENTION_DAYS} days, then "
                    "removed automatically. Restoring returns an item exactly "
                    "as it was.",
                    size=12,
                    color=COLORS["text_dim"],
                    font_family=MONO,
                ),
                ft.Container(height=14),
                ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, controls=rows),
            ],
        ),
    )


def _entry_row(
    entry,
    on_restore: Callable[[str], None],
    on_delete_permanently: Callable[[str], None],
    now: float,
) -> ft.Control:
    deleted = humanize_since(entry.deleted_at, now)
    days_left = max(0, int((entry.expires_at() - now) // 86400))
    meta = f"{entry.label} · deleted {deleted} · expires in {days_left}d"
    lines: list[ft.Control] = [
        ft.Text(
            entry.name,
            size=14,
            weight=ft.FontWeight.BOLD,
            color=COLORS["text_main"],
            font_family=MONO,
        ),
        ft.Text(meta, size=12, color=COLORS["text_sub"], font_family=MONO),
    ]
    if entry.holds_secret_material:
        # Say the true thing: trashing parked the secret, it did not destroy it.
        # The operator has to be able to tell those two outcomes apart.
        lines.append(
            ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(
                        ft.Icons.LOCK_OUTLINE, size=12, color=COLORS["warning"]
                    ),
                    ft.Text(
                        "still holds its secret material — delete permanently "
                        "to remove it from disk",
                        size=11,
                        color=COLORS["warning"],
                        font_family=MONO,
                    ),
                ],
            )
        )
    return ft.Container(
        bgcolor=COLORS["card_bg"],
        border_radius=3,
        border=ft.Border.all(1, COLORS["card_border"]),
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=12,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(
                            _KIND_ICONS.get(entry.kind, ft.Icons.DELETE_OUTLINE),
                            size=20,
                            color=COLORS["accent"],
                        ),
                        ft.Column(spacing=3, expand=True, controls=lines),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    controls=[
                        row_button(
                            "[ restore ]",
                            lambda _, i=entry.id: on_restore(i),
                        ),
                        row_button(
                            "[ delete permanently ]",
                            lambda _, i=entry.id: on_delete_permanently(i),
                            kind="delete",
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
            "trash is empty — deleted profiles, bookmarks, proxies, SSH hosts "
            "and certificates appear here",
            size=13,
            color=COLORS["text_dim"],
            font_family=MONO,
        ),
    )
