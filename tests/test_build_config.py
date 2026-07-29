"""The flet build configuration in pyproject.toml.

The window starts HIDDEN (hide_window_on_start) so the user's first frame is
persona's own centred fingerprint splash — never the client's off-centre corner
spinner nor the jump to centre. The old ban (visible-start) feared a hidden
window that a crashed Python session never shows becoming an invisible zombie;
that's now prevented by revealing the window from Python's first frame AND
force-revealing it on any startup error (see App._main / _finish_startup), plus
scrubbing FLET_HIDE_WINDOW_ON_START out of any relaunch env so it can't leak.
These tests pin that design: hidden start, native screens off (they'd never be
seen behind the hidden window), and NO `window.visible = False` anywhere (only
the reveal to True is allowed).
"""

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_built_app_starts_hidden():
    # The window starts hidden so the pre-Python corner spinner + centre-jump are
    # never seen; Python reveals it once the centred splash is up.
    app = _pyproject()["tool"]["flet"]["app"]
    assert app["hide_window_on_start"] is True


def test_native_boot_and_startup_screens_are_off():
    # With the window hidden until Python's first frame, the client's boot/startup
    # spinners would never be visible anyway — keep them off so nothing but
    # persona's own centred splash ever paints.
    app = _pyproject()["tool"]["flet"]["app"]
    assert app["boot_screen"]["show"] is False
    assert app["startup_screen"]["show"] is False


def test_app_code_never_hides_the_window():
    # Revealing the window (`window.visible = True`) is required; HIDING it
    # (`= False`) anywhere would recreate the invisible-zombie failure — a live
    # window nothing is guaranteed to bring back. Only the True reveal is allowed.
    for py in (ROOT / "src").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "window.visible = False" not in src, f"window hidden in {py}"


def test_relaunch_env_scrubs_every_client_env_gate(monkeypatch):
    # every var the build template applies with putIfAbsent (an inherited
    # value silently beats the fresh one) plus the client's hide gate, which
    # hides the window when merely PRESENT — none may survive into anything
    # that outlives this persona and starts the next one
    from src.services.app_update import updater as au

    gates = (
        "FLET_PLATFORM",
        "FLET_SERVER_PORT",
        "FLET_SERVER_UDS_PATH",
        "FLET_PYTHON_CALLBACK_SOCKET_ADDR",
        "FLET_APP_CONSOLE",
        "FLET_APP_STORAGE_DATA",
        "FLET_APP_STORAGE_TEMP",
        "FLET_ASSETS_DIR",
        "FLET_HIDE_WINDOW_ON_START",
        "PYTHONPATH",
        "PYTHONHOME",
    )
    for var in gates:
        assert var in au._RUNTIME_ENV_VARS, var
        monkeypatch.setenv(var, "stale")
    env = au._relaunch_env()
    for var in gates:
        assert var not in env, f"{var} leaked into the relaunch environment"
