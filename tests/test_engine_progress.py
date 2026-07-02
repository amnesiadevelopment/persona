"""The engine download progress must surface MB / percent / speed in the
sidebar panel, and the progress bar must be inserted into the sidebar tree
while a download is in flight."""
import flet as ft

from src.ui import progress_fmt as pf
from src.ui.app import App


def _texts(control):
    out = []
    def walk(c):
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            out.append(c.value)
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str):
                out.append(v)
            elif v is not None:
                walk(v)
    walk(control)
    return out


def _app():
    # App needs a container; build it the way main does but headless.
    from src.core.container import Container
    app = App.__new__(App)
    return app


def test_progress_cb_formats_mb_percent_speed(monkeypatch):
    app = _app()
    # minimal attrs the cb touches
    app._engine_start_t = 0.0
    app._engine_throttle = pf.ProgressThrottle()
    app._engine_pstate = pf.ProgressState()
    app.engine_text = ft.Text("")
    app._engine_bar = ft.ProgressBar()
    app._engine_detail = ft.Text("")
    monkeypatch.setattr(app, "_safe_update", lambda: None)
    monkeypatch.setattr(app, "_refresh_sidebar", lambda: None)

    import time
    monkeypatch.setattr(app, "_engine_start_t", time.monotonic() - 2.0)
    app._engine_progress_cb(50_000_000, 100_000_000)

    assert "downloading" in app.engine_text.value
    assert "50%" in app.engine_text.value
    # detail line carries MB-of-MB and speed
    assert "MB" in app._engine_detail.value
    assert "of" in app._engine_detail.value
    assert "MB/s" in app._engine_detail.value
    assert app._engine_bar.value == 0.5


def test_version_check_uses_checking_not_busy(monkeypatch):
    # A version check must set _engine_checking (spinner only) and NEVER
    # _engine_busy — the download bar is gated on _engine_busy, so conflating
    # the two is what painted a stale "189 MB of 189 MB" bar during a check.
    app = _app()
    app._engine_busy = False
    app._engine_checking = False
    app._engine2_busy = False
    app._engine2_checking = False
    app._engine_latest = ""
    started = {}
    monkeypatch.setattr(app, "_engine_update_available", lambda: False)
    monkeypatch.setattr(app, "_refresh_engine_text", lambda *a, **k: None)
    monkeypatch.setattr(app, "_log", lambda *a, **k: None)

    import threading

    # Capture the check thread's target without running the network fetch.
    real_thread = threading.Thread

    def fake_thread(target=None, **kw):
        started["target"] = target
        return real_thread(target=lambda: None)

    monkeypatch.setattr(threading, "Thread", fake_thread)

    app._check_both_engines()
    # The moment the check starts, checking is on and busy stays off.
    assert app._engine_checking is True
    assert app._engine_busy is False


def test_progress_cb_unknown_total_shows_mb_and_speed(monkeypatch):
    app = _app()
    app._engine_throttle = pf.ProgressThrottle()
    app._engine_pstate = pf.ProgressState()
    app.engine_text = ft.Text("")
    app._engine_bar = ft.ProgressBar()
    app._engine_detail = ft.Text("")
    monkeypatch.setattr(app, "_safe_update", lambda: None)
    monkeypatch.setattr(app, "_refresh_sidebar", lambda: None)
    import time
    app._engine_start_t = time.monotonic() - 1.0
    app._engine_progress_cb(30_000_000, 0)
    # no percent when total unknown, but MB + speed still shown
    assert "MB" in app.engine_text.value
    assert "MB/s" in app._engine_detail.value
    assert app._engine_bar.value is None
