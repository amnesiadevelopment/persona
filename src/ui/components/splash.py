"""Fingerprint-scan loading screen: the persona mark glowing green inside
scanner corner-brackets, with a red neon line sweeping top-to-bottom over it.
Shown as the window's first frame while the real UI builds — including right
after a self-update restart, when the window otherwise sits on a bare grey
background."""

import asyncio

import flet as ft

from ...core.assets import asset_path
from ..theme.colors import COLORS

# keep the splash up for at least ~one full sweep, so a fast start reads as a
# deliberate loading screen instead of a single-frame flash
MIN_SECONDS = 1.2

_BOX = 200
_LOGO = 120
_BRACKET = 30
# the beam is much narrower than the box so its horizontal glow fades out well
# INSIDE the scan area instead of clipping against the frame edges (a visible
# hard border). The bloom radius is kept small too, for the same reason.
_LINE_W = _BOX - 96
_LINE_H = 4
_SWEEP_MS = 1100
# a tall, soft beam so the red halo fades gradually above and below the bright
# core — a light beam sweeping the print, not a flat bar
_BEAM_H = 72
_LOGO_TOP = (_BOX - _LOGO) / 2
# the beam sweeps ONLY across the fingerprint mark (not the whole box), so the
# red always lies OVER the green print — the two colours blend instead of
# meeting in a hard line below the logo. offsets are in units of the beam's own
# height; travel spans the logo's height.
_TRAVEL = (_LOGO - _LINE_H) / _BEAM_H

_GREEN = COLORS["accent"]
_GREEN_GLOW = "#66" + _GREEN.lstrip("#")
_RED = COLORS["error"]
_RED_GLOW = "#CC" + _RED.lstrip("#")
# graded red haze stops (opaque→transparent) for the soft beam falloff
_RED_HAZE = "#59" + _RED.lstrip("#")
_RED_HAZE_MID = "#33" + _RED.lstrip("#")
_RED_HAZE_SOFT = "#14" + _RED.lstrip("#")


def _bracket(*, top: bool, left: bool) -> ft.Container:
    side = ft.BorderSide(2, _GREEN)
    return ft.Container(
        width=_BRACKET,
        height=_BRACKET,
        top=0 if top else None,
        bottom=None if top else 0,
        left=0 if left else None,
        right=None if left else 0,
        border=ft.Border(
            top=side if top else None,
            bottom=None if top else side,
            left=side if left else None,
            right=None if left else side,
        ),
        shadow=ft.BoxShadow(blur_radius=10, color=_GREEN_GLOW),
    )


class Splash:
    def __init__(self) -> None:
        self._running = False
        self._at_top = True
        # hot white core with a graded red bloom in several layers, so the glow
        # falls off smoothly. Its ends fade horizontally (transparent→white→
        # transparent) so the beam tapers to points instead of ending in a hard
        # bright rectangle whose glow would clip against the frame.
        core = ft.Container(
            height=_LINE_H,
            width=_LINE_W,
            top=(_BEAM_H - _LINE_H) / 2,
            left=0,
            border_radius=4,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=["#00FFFFFF", "#FFFFFF", "#FFFFFF", "#00FFFFFF"],
                stops=[0.0, 0.2, 0.8, 1.0],
            ),
            # small bloom radii so the shadow's own spread never reaches the
            # frame edges; the wider, soft red halo is painted by the haze band
            # below (a gradient we fully control, kept inside the box)
            shadow=[
                ft.BoxShadow(blur_radius=5, spread_radius=0, color="#FFFFFF"),
                ft.BoxShadow(blur_radius=12, spread_radius=1, color=_RED),
                ft.BoxShadow(blur_radius=22, spread_radius=2, color=_RED_GLOW),
            ],
        )
        # a tall soft haze band around the core, brightest at the line and
        # fading smoothly to transparent — turns a flat bar into a light beam
        haze = ft.Container(
            width=_LINE_W,
            height=_BEAM_H,
            left=0,
            top=0,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER,
                end=ft.Alignment.BOTTOM_CENTER,
                colors=[
                    "#00000000",
                    _RED_HAZE_SOFT,
                    _RED_HAZE_MID,
                    _RED_HAZE,
                    _RED_HAZE_MID,
                    _RED_HAZE_SOFT,
                    "#00000000",
                ],
                stops=[0.0, 0.28, 0.42, 0.5, 0.58, 0.72, 1.0],
            ),
        )
        # the whole beam (haze + core) is what sweeps
        self._line = ft.Container(
            width=_LINE_W,
            height=_BEAM_H,
            left=(_BOX - _LINE_W) / 2,
            # start with the core aligned to the TOP of the logo, so the sweep
            # rides over the fingerprint from its top edge down to its bottom
            top=_LOGO_TOP - (_BEAM_H - _LINE_H) / 2,
            content=ft.Stack(controls=[haze, core]),
            offset=ft.Offset(0, 0),
            animate_offset=ft.Animation(_SWEEP_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        logo = ft.Container(
            width=_LOGO,
            height=_LOGO,
            left=(_BOX - _LOGO) / 2,
            top=(_BOX - _LOGO) / 2,
            border_radius=24,
            shadow=ft.BoxShadow(blur_radius=36, spread_radius=4, color=_GREEN_GLOW),
            # the mark is already neon green on black — tinting it (SRC_IN)
            # would flood every opaque pixel and erase the fingerprint ridges
            content=ft.Image(
                src=asset_path("icon.png"),
                width=_LOGO,
                height=_LOGO,
                border_radius=24,
            ),
        )
        scanner = ft.Container(
            width=_BOX,
            height=_BOX,
            content=ft.Stack(
                controls=[
                    logo,
                    _bracket(top=True, left=True),
                    _bracket(top=True, left=False),
                    _bracket(top=False, left=True),
                    _bracket(top=False, left=False),
                    self._line,
                ],
            ),
        )
        self.control = ft.Container(
            expand=True,
            bgcolor=COLORS["bg"],
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=24,
                controls=[
                    scanner,
                    ft.Text(
                        "persona",
                        size=13,
                        color=COLORS["text_dim"],
                        font_family="monospace",
                    ),
                ],
            ),
        )

    def _toggle(self) -> ft.Offset:
        self._at_top = not self._at_top
        self._line.offset = ft.Offset(0, 0 if self._at_top else _TRAVEL)
        return self._line.offset

    def start(self, page: ft.Page) -> None:
        if self._running:
            return
        self._running = True
        page.run_task(self._sweep)

    async def _sweep(self) -> None:
        while self._running:
            self._toggle()
            try:
                self._line.update()
            except Exception:
                return  # detached mid-patch; the splash is gone
            await asyncio.sleep(_SWEEP_MS / 1000)

    def stop(self) -> None:
        self._running = False
