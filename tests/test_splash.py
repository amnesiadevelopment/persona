import flet as ft

from src.ui.components.splash import (
    _BEAM_H,
    _BOX,
    _LINE_H,
    _LOGO,
    _TRAVEL,
    MIN_SECONDS,
    Splash,
)
from src.ui.theme.colors import COLORS


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        children = v if isinstance(v, list) else [v]
        for child in children:
            if isinstance(child, ft.BaseControl):
                yield from _walk(child)


def test_splash_builds_headless():
    s = Splash()
    assert isinstance(s.control, ft.Container)
    assert s.control.bgcolor == COLORS["bg"]
    assert s.control.expand is True


def test_splash_shows_the_persona_mark_untinted():
    s = Splash()
    images = [c for c in _walk(s.control) if isinstance(c, ft.Image)]
    assert len(images) == 1
    img = images[0]
    assert str(img.src).endswith("icon.png")
    # the mark is already neon green on black; a tint (SRC_IN) floods every
    # opaque pixel and erases the fingerprint ridges — never tint it
    assert img.color is None
    # the green bloom comes from a glow shadow on the wrapping container
    holder = next(
        c for c in _walk(s.control)
        if isinstance(c, ft.Container) and c.content is img
    )
    assert holder.shadow is not None
    assert COLORS["accent"].lstrip("#") in str(holder.shadow.color)


def test_splash_scan_line_is_red_neon_and_animated():
    s = Splash()
    line = s._line
    # the sweeping beam is a Stack of a soft red haze band + a bright core line
    assert line.animate_offset is not None
    assert line.offset.y == 0
    red = COLORS["error"].lstrip("#")
    stack = line.content
    core = stack.controls[-1]
    # the core carries a multi-layer red bloom (the neon halo)
    assert isinstance(core.shadow, list) and len(core.shadow) >= 2
    assert any(red in str(sh.color) for sh in core.shadow)
    # the haze band is a red vertical gradient so it reads as a scanning beam
    haze = stack.controls[0]
    assert haze.gradient is not None
    assert any(red in str(c) for c in haze.gradient.colors)


def test_splash_brackets_are_green():
    s = Splash()
    brackets = [
        c
        for c in _walk(s.control)
        if isinstance(c, ft.Container) and getattr(c, "border", None) is not None
    ]
    assert len(brackets) == 4
    for b in brackets:
        sides = [b.border.top, b.border.bottom, b.border.left, b.border.right]
        colored = [side for side in sides if side is not None]
        # each bracket draws exactly its two corner edges, in brand green
        assert len(colored) == 2
        assert all(side.color == COLORS["accent"] for side in colored)


def test_sweep_toggle_bounces_between_top_and_bottom():
    s = Splash()
    ys = [s._toggle().y for _ in range(4)]
    assert ys == [_TRAVEL, 0, _TRAVEL, 0]
    # the beam sweeps across the fingerprint LOGO (not the whole box) so the red
    # rides over the green print instead of meeting it in a hard line below
    assert _TRAVEL == (_LOGO - _LINE_H) / _BEAM_H


def test_stop_ends_the_sweep_loop():
    import asyncio

    s = Splash()
    s._running = True
    s.stop()
    assert s._running is False
    # with _running cleared the loop returns immediately (no page needed)
    asyncio.run(s._sweep())


def test_min_display_is_about_one_sweep():
    assert 0.5 <= MIN_SECONDS <= 3.0


def test_main_shows_splash_before_anything_else(monkeypatch):
    from types import SimpleNamespace

    from src.ui import app as ui_app

    monkeypatch.setattr(ui_app, "configure_page", lambda p: None)
    app = ui_app.App.__new__(ui_app.App)
    added, tasks = [], []
    page = SimpleNamespace(
        add=lambda c: added.append(c),
        run_task=lambda h, *a, **k: tasks.append(h),
        services=[],
    )
    app._main(page)
    assert added, "nothing was added to the page"
    assert added[0] is app._splash.control
    # the real UI builds in the first serviced task, behind the splash
    names = [getattr(h, "__name__", "") for h in tasks]
    assert "_finish_startup" in names
    # the scan sweep is scheduled too
    assert "_sweep" in names
