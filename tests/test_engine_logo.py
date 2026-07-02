"""Building an engine-row logo must not raise.

A regression used ft.ImageFit.CONTAIN, which doesn't exist in this Flet, so
_engine_logo threw when the engines panel was rendered — making the panel look
like it wouldn't open. Pure-logic tests missed it because they never build the
Flet controls; this constructs the real control.
"""
from src.ui.app import App


def _app():
    return App.__new__(App)


def test_engine_logo_builds_for_both_engines():
    app = _app()
    for key in ("chromium", "firefox"):
        logo = app._engine_logo(key)
        assert logo is not None


def test_engine_logo_builds_at_custom_size():
    app = _app()
    logo = app._engine_logo("chromium", size=24)
    assert logo is not None
