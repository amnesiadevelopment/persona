"""Fingerprint-scan loading screen: the persona mark glowing green inside
scanner corner-brackets, with a red neon line sweeping top-to-bottom over it.
Shown as the window's first frame while the real UI builds — including right
after a self-update restart, when the window otherwise sits on a bare grey
background."""

import asyncio

import flet as ft

from ...core.assets import asset_path
from ..theme.colors import COLORS

# keep the splash up for at least one full sweep cycle (down and back up,
# 2 x _SWEEP_MS, plus the first-frame delay), so even the fastest start shows
# a complete scan instead of a beam that dies mid-travel
MIN_SECONDS = 2.6

# the logo-click ScanFlash is feedback, not a loading screen: one quicker
# sweep and gone, noticeably shorter than the startup splash
FLASH_SECONDS = 0.9
_FLASH_SWEEP_MS = 700

_BOX = 200
_LOGO = 120
_BRACKET = 30
# the beam is much narrower than the box so its horizontal glow fades out well
# INSIDE the scan area instead of clipping against the frame edges (a visible
# hard border). The bloom radius is kept small too, for the same reason.
_LINE_W = _BOX - 96
_LINE_H = 4
_SWEEP_MS = 1100
# the first offset change must reach the client only AFTER it has painted the
# beam at its start position: patches applied within one client frame collapse
# into a single build, animate_offset then has no rendered state to animate
# FROM, and the beam lands at the bottom with no sweep and sits there for the
# whole splash (warm second launches, where patches arrive fast, hit this)
_FIRST_SWEEP_DELAY = 0.3
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


def _beam(sweep_ms: int = _SWEEP_MS) -> ft.Container:
    """The sweeping scan beam (soft red haze band + hot white core)."""
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
    return ft.Container(
        width=_LINE_W,
        height=_BEAM_H,
        left=(_BOX - _LINE_W) / 2,
        # start with the core aligned to the TOP of the logo, so the sweep
        # rides over the fingerprint from its top edge down to its bottom
        top=_LOGO_TOP - (_BEAM_H - _LINE_H) / 2,
        content=ft.Stack(controls=[haze, core]),
        offset=ft.Offset(0, 0),
        animate_offset=ft.Animation(sweep_ms, ft.AnimationCurve.EASE_IN_OUT),
    )


def _scanner(line: ft.Container) -> ft.Container:
    """The scan box: the persona mark inside corner brackets, under `line`."""
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
    return ft.Container(
        width=_BOX,
        height=_BOX,
        content=ft.Stack(
            controls=[
                logo,
                _bracket(top=True, left=True),
                _bracket(top=True, left=False),
                _bracket(top=False, left=True),
                _bracket(top=False, left=False),
                line,
            ],
        ),
    )


class Splash:
    def __init__(self) -> None:
        self._running = False
        self._at_top = True
        self._line = _beam()
        scanner = _scanner(self._line)
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
        await asyncio.sleep(_FIRST_SWEEP_DELAY)
        while self._running:
            self._toggle()
            try:
                self._line.update()
            except Exception:
                return  # detached mid-patch; the splash is gone
            await asyncio.sleep(_SWEEP_MS / 1000)

    def stop(self) -> None:
        self._running = False


# the logo-click flash is a SMALL scanner pinned in the top-left corner (near
# the sidebar logo it was clicked from), not a full-window centered overlay —
# a compact "scanned" tick, no translucent backdrop over the whole page
_FLASH_BOX = 96
_FLASH_SCALE = _FLASH_BOX / _BOX


class ScanFlash:
    """One quick fingerprint-scan sweep in a small box at the top-left — click
    feedback that reads as "scanned", shown briefly near the logo."""

    def __init__(self) -> None:
        self._line = _beam(sweep_ms=_FLASH_SWEEP_MS)
        # the scanner is built at the full _BOX size; scale it down to the small
        # corner badge so the beam/logo geometry stays consistent
        scanner = ft.Container(
            width=_BOX,
            height=_BOX,
            scale=_FLASH_SCALE,
            alignment=ft.Alignment.TOP_LEFT,
            content=_scanner(self._line),
        )
        self.control = ft.Container(
            # pinned to the top-left corner, sized to the small badge; NO
            # full-window translucent backdrop (that looked huge and janky over
            # the content) — just the compact scanner near the logo
            left=8,
            top=8,
            width=_FLASH_BOX,
            height=_FLASH_BOX,
            alignment=ft.Alignment.TOP_LEFT,
            content=scanner,
        )

    async def play(self) -> None:
        self._line.offset = ft.Offset(0, _TRAVEL)
        try:
            self._line.update()
        except Exception:
            pass  # detached mid-patch; nothing left to animate
        await asyncio.sleep(FLASH_SECONDS)
