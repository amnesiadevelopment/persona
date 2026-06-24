"""First-run onboarding: a multi-step dialog that introduces persona and
downloads the browser engine, shown once. Step content comes from
onboarding_flow; this module only renders it.
"""

import time
from collections.abc import Callable

import flet as ft

from .. import onboarding_flow as flow
from .. import progress_fmt as pf
from ..theme.colors import COLORS
from ..theme.styles import MONO


class Onboarding:
    """Owns the onboarding dialog and its step state."""

    def __init__(
        self,
        page: ft.Page,
        on_finish: Callable[[], None],
        start_engine: Callable[
            [Callable[[int, int], None], Callable[[bool], None]], None
        ],
        engine_already_installed: bool = False,
    ) -> None:
        self.page = page
        self.on_finish = on_finish
        self.start_engine = start_engine
        self.engine_done = engine_already_installed
        self.index = 0
        self.dlg: ft.AlertDialog | None = None
        self._bar = ft.ProgressBar(
            value=1.0 if engine_already_installed else None,
            color=COLORS["accent"],
            bgcolor=COLORS["input_bg"],
            width=420,
        )
        self._pct = ft.Text(
            "", size=13, color=COLORS["text_main"], font_family=MONO
        )
        self._detail = ft.Text(
            "", size=11, color=COLORS["text_sub"], font_family=MONO
        )
        self._start_t = 0.0

    def open(self) -> None:
        self.dlg = ft.AlertDialog(
            modal=True,
            bgcolor="#00000000",
            content=self._frame(),
        )
        self.page.show_dialog(self.dlg)

    def _frame(self) -> ft.Control:
        return ft.Container(
            width=600,
            padding=ft.Padding.symmetric(horizontal=36, vertical=32),
            bgcolor=COLORS["card_hover"],
            border=ft.Border.all(1, COLORS["accent_dim"]),
            border_radius=14,
            content=self._body(),
        )

    def _finish(self) -> None:
        if self.dlg is not None:
            self.page.pop_dialog()
        self.on_finish()

    def _rebuild(self) -> None:
        if self.dlg is not None:
            self.dlg.content = self._frame()
            self.page.update()

    def _next(self, _: ft.ControlEvent) -> None:
        self.index = flow.next_index(self.index)
        if flow.steps()[self.index]["id"] == "engine" and not self.engine_done:
            self._begin_engine()
        self._rebuild()

    def _back(self, _: ft.ControlEvent) -> None:
        self.index = flow.prev_index(self.index)
        self._rebuild()

    def _begin_engine(self) -> None:
        self._start_t = time.monotonic()

        def progress(done: int, total: int) -> None:
            elapsed = max(time.monotonic() - self._start_t, 0.001)
            self._bar.value = pf.fraction(done, total)
            self._pct.value = (
                f"{pf.percent(done, total)}%" if total > 0 else pf.fmt_mb(done)
            )
            self._detail.value = pf.fmt_line(done, total, elapsed)
            self.page.update()

        def done(ok: bool) -> None:
            self.engine_done = True
            self._bar.value = 1.0
            self._pct.value = "done" if ok else "failed"
            self._detail.value = "" if ok else "could not download engine"
            self.page.update()

        self.start_engine(progress, done)

    def _body(self) -> ft.Control:
        step = flow.steps()[self.index]
        if step["id"] == "engine":
            return self._engine_body(step)
        return self._welcome_body(step)

    def _welcome_body(self, step: dict) -> ft.Control:
        return ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.FINGERPRINT, size=40, color=COLORS["accent"]),
                ft.Container(height=10),
                ft.Text(
                    step["title"],
                    size=22,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
                ft.Container(height=4),
                ft.Text(
                    step["subtitle"],
                    size=13,
                    color=COLORS["text_sub"],
                    text_align=ft.TextAlign.CENTER,
                    font_family=MONO,
                ),
                ft.Container(height=18),
                self._feature_grid(step["features"]),
                ft.Container(height=22),
                self._nav_row(),
            ],
        )

    def _feature_grid(self, features: list[dict]) -> ft.Control:
        rows = []
        for i in range(0, len(features), 2):
            pair = features[i : i + 2]
            cells = [self._feature(f) for f in pair]
            if len(cells) == 1:
                cells.append(ft.Container(expand=True))
            rows.append(
                ft.Row(
                    spacing=20,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=cells,
                )
            )
        return ft.Column(spacing=14, controls=rows)

    def _feature(self, f: dict) -> ft.Control:
        return ft.Container(
            expand=True,
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Icon(
                        getattr(ft.Icons, f["icon"].upper(), ft.Icons.CHECK),
                        size=18,
                        color=COLORS["accent"],
                    ),
                    ft.Column(
                        spacing=2,
                        tight=True,
                        expand=True,
                        controls=[
                            ft.Text(
                                f["label"],
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=COLORS["text_main"],
                                font_family=MONO,
                            ),
                            ft.Text(
                                f["desc"],
                                size=10,
                                color=COLORS["text_sub"],
                                font_family=MONO,
                                no_wrap=False,
                            ),
                        ],
                    ),
                ],
            ),
        )

    def _engine_body(self, step: dict) -> ft.Control:
        return ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    step["title"],
                    size=22,
                    weight=ft.FontWeight.BOLD,
                    color=COLORS["text_main"],
                    font_family=MONO,
                ),
                ft.Container(height=4),
                ft.Text(
                    step["subtitle"],
                    size=13,
                    color=COLORS["text_sub"],
                    text_align=ft.TextAlign.CENTER,
                    font_family=MONO,
                ),
                ft.Container(height=22),
                self._bar,
                ft.Container(height=8),
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    width=420,
                    controls=[self._detail, self._pct],
                ),
                ft.Container(height=22),
                self._nav_row(),
            ],
        )

    def _nav_row(self) -> ft.Control:
        last = flow.is_last(self.index)
        left = (
            ft.TextButton("Back", on_click=self._back)
            if self.index > 0
            else ft.TextButton("Skip", on_click=lambda _: self._finish())
        )
        label = "Done" if last else "Next"
        on_click = (lambda _: self._finish()) if last else self._next
        right = ft.FilledButton(
            label,
            on_click=on_click,
            style=ft.ButtonStyle(bgcolor=COLORS["accent"], color="#000000"),
        )
        return ft.Row(
            width=520,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[left, right],
        )
