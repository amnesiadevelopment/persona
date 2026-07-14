import asyncio

import flet as ft

from src.ui.components import splash as splash_mod
from src.ui.components.splash import (
    _BEAM_H,
    _BOX,
    _LINE_H,
    _LOGO,
    _SWEEP_MS,
    _TRAVEL,
    FLASH_SECONDS,
    MIN_SECONDS,
    ScanFlash,
    Splash,
)
from src.ui.theme.colors import COLORS


async def _instant_sleep(_t):
    return None


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


def test_min_display_covers_a_full_sweep_cycle():
    # at least one full down-and-back-up pass, so even the fastest start shows
    # a real scan instead of a beam that dies mid-travel
    assert MIN_SECONDS >= 2 * _SWEEP_MS / 1000
    assert MIN_SECONDS <= 3.0


def test_sweep_lets_the_first_frame_paint_before_moving(monkeypatch):
    # The first offset change must reach the client only after it painted the
    # beam at its start position: two patches applied within one client frame
    # collapse into a single build, animate_offset has nothing to animate FROM,
    # and the beam lands at the bottom with no sweep — the frozen splash seen
    # on warm second launches.
    s = Splash()
    s._running = True
    events = []
    real_toggle = s._toggle
    s._toggle = lambda: events.append("toggle") or real_toggle()

    class Stop(Exception):
        pass

    async def first_sleep(t):
        events.append(("sleep", t))
        raise Stop

    monkeypatch.setattr(splash_mod.asyncio, "sleep", first_sleep)
    try:
        asyncio.run(s._sweep())
    except Stop:
        pass
    assert events, "the sweep did nothing at all"
    assert events[0][0] == "sleep" and events[0][1] > 0
    assert "toggle" not in events


def test_sweep_keeps_sweeping_until_stopped(monkeypatch):
    # the beam must bounce for as long as the splash is up — one lonely toggle
    # reads as a frozen/broken loading screen
    s = Splash()
    s._running = True
    toggles = []
    real_toggle = s._toggle
    s._toggle = lambda: toggles.append(1) or real_toggle()
    monkeypatch.setattr(ft.Container, "update", lambda self: None)
    ticks = 0

    async def fake_sleep(t):
        nonlocal ticks
        ticks += 1
        if ticks >= 5:
            s.stop()

    monkeypatch.setattr(splash_mod.asyncio, "sleep", fake_sleep)
    asyncio.run(s._sweep())
    assert len(toggles) >= 3


def test_scan_flash_builds_headless():
    f = ScanFlash()
    assert isinstance(f.control, ft.Container)
    # a red sweep pinned exactly over the 28px sidebar logo (not a box in the
    # corner, not a full-window overlay), clipped so its glow stays on the mark
    assert f.control.expand is not True
    assert f.control.bgcolor is None
    assert f.control.left == splash_mod._LOGO_LEFT
    assert f.control.top == splash_mod._LOGO_TOP_INSET
    assert f.control.width == splash_mod._LOGO_PX
    assert f.control.height == splash_mod._LOGO_PX
    assert f.control.clip_behavior == ft.ClipBehavior.HARD_EDGE


def test_scan_flash_reuses_the_scan_beam():
    f = ScanFlash()
    line = f._line
    assert line.animate_offset is not None
    assert line.offset.y == 0
    red = COLORS["error"].lstrip("#")
    core = line.content.controls[-1]
    assert any(red in str(sh.color) for sh in core.shadow)


def test_scan_flash_play_sweeps_down_then_up(monkeypatch):
    # one click = a there-and-back pass: down to _FLASH_TRAVEL, then back to 0.
    # With an ATTACHED control both moves apply; here we just prove play() runs
    # the down move without raising and reset() re-homes to the top.
    monkeypatch.setattr(splash_mod.asyncio, "sleep", _instant_sleep)
    f = ScanFlash()
    f._line.update = lambda: None  # pretend attached so both moves execute
    asyncio.run(f.play())
    assert f._line.offset.y == 0  # ends back at the top after the round trip


def test_scan_flash_reset_homes_to_top():
    f = ScanFlash()
    f._line.offset = ft.Offset(0, splash_mod._FLASH_TRAVEL)
    f.reset()
    assert f._line.offset.y == 0


def test_scan_flash_play_bows_out_when_cancelled(monkeypatch):
    # a newer click supersedes an in-flight sweep: cancelled() true → play stops
    # early at the top position (never sweeps down), so beams never stack.
    monkeypatch.setattr(splash_mod.asyncio, "sleep", _instant_sleep)
    f = ScanFlash()
    f._line.update = lambda: None
    asyncio.run(f.play(cancelled=lambda: True))
    assert f._line.offset.y == 0  # bowed out before the down sweep


def test_scan_flash_is_a_quick_flash_not_the_startup_splash():
    assert 0.5 <= FLASH_SECONDS <= 1.2
    assert FLASH_SECONDS <= MIN_SECONDS


def test_startup_splash_structure_unchanged_by_flash():
    s = Splash()
    f = ScanFlash()
    assert s._line is not f._line
    # the splash keeps its slow bounce sweep; the flash sweep is quicker
    assert f._line.animate_offset.duration < s._line.animate_offset.duration


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
        window=SimpleNamespace(visible=None),
    )
    app._main(page)
    assert added, "nothing was added to the page"
    assert added[0] is app._splash.control
    # the real UI builds in the first serviced task, behind the splash
    names = [getattr(h, "__name__", "") for h in tasks]
    assert "_finish_startup" in names
    # the scan sweep is scheduled too
    assert "_sweep" in names


def test_main_never_touches_window_visibility(monkeypatch):
    # the window is visible from the client's first frame (pyproject bans
    # hide_window_on_start); a Python-side `visible = False` would hide a
    # live window that only a healthy Python session could bring back — any
    # startup failure after it would leave an invisible zombie process. The
    # first patch must leave window visibility completely untouched, on
    # every OS.
    from types import SimpleNamespace

    from src.ui import app as ui_app

    monkeypatch.setattr(ui_app, "configure_page", lambda p: None)
    for is_macos in (False, True):
        monkeypatch.setattr(ui_app._platform, "IS_MACOS", is_macos)
        app = ui_app.App.__new__(ui_app.App)
        page = SimpleNamespace(
            add=lambda c: None,
            run_task=lambda h, *a, **k: None,
            services=[],
            window=SimpleNamespace(visible=None),
        )
        app._main(page)
        assert page.window.visible is None
