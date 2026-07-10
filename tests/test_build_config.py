"""The flet build configuration in pyproject.toml.

flet 0.85.3 has no native desktop splash (flutter_native_splash covers only
iOS/Android/web), so the packaged app relies on the build template's
hide_window_on_start: the Flutter window stays hidden while Python boots —
otherwise it opens seconds early as a bare dark rectangle — and app.py reveals
it once the splash is the page's first frame.
"""

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_built_app_starts_with_hidden_window():
    data = _pyproject()
    assert data["tool"]["flet"]["app"]["hide_window_on_start"] is True


def test_hidden_start_has_the_python_side_reveal():
    # hide_window_on_start without the matching page.window.visible = True in
    # the app would ship a build whose window NEVER appears.
    src = (ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")
    assert "page.window.visible = False" in src
    assert "page.window.visible = True" in src
