import flet as ft

from ..log_format import log_line_control
from ..theme.colors import COLORS


def open_log_dialog(page: ft.Page, log_lines: list[str]) -> None:
    rows = (
        [log_line_control(ln) for ln in log_lines]
        if log_lines
        else [ft.Text("No activity yet.", size=12, color=COLORS["text_dim"])]
    )

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(
            radius=3,
            side=ft.BorderSide(1, COLORS["accent_dim"]),
        ),
        title=ft.Text("Activity Log", size=22, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            width=800,
            height=400,
            border_radius=10,
            bgcolor=COLORS["log_bg"],
            padding=14,
            content=ft.Column(
                expand=True,
                controls=[
                    ft.Text(
                        f"{len(log_lines)} entries",
                        size=12,
                        color=COLORS["text_dim"],
                    ),
                    ft.Container(height=8),
                    # A ListView (not a Column) is what actually takes the wheel
                    # and drag scroll here; auto_scroll on the outer Column was
                    # pinning the view to the bottom so manual scroll-up did
                    # nothing. The user browses history, so don't auto-pin.
                    ft.ListView(
                        controls=rows,
                        expand=True,
                        spacing=2,
                    ),
                ],
            ),
        ),
        actions=[
            ft.TextButton("Close", on_click=lambda _: page.pop_dialog()),
        ],
    )
    page.show_dialog(dlg)
