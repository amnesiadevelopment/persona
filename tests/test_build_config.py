"""The flet build configuration in pyproject.toml.

The Flutter window must be visible on every launch, in every context. The
build template renders its boot/startup/ERROR screens into the app window,
and a window hidden by hide_window_on_start can only be shown again by a
healthy Python session — so any startup failure (a torn app.zip
re-extraction after a self-update, a poisoned relaunch environment, a dead
interpreter) becomes an invisible zombie process the user can neither see
nor close. These tests pin the visible-start design: no hidden start
anywhere, honest boot/startup screens instead, no Python-side visibility
writes, and a relaunch environment that can't re-hide the window.
"""

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_built_app_never_starts_hidden():
    tool_flet = _pyproject()["tool"]["flet"]
    # the template honors the key from tool.flet.app AND any per-platform
    # tool.flet.<platform>.app section — none of them may hide the window
    app_sections = [tool_flet.get("app", {})]
    for value in tool_flet.values():
        if isinstance(value, dict) and isinstance(value.get("app"), dict):
            app_sections.append(value["app"])
    for section in app_sections:
        assert not section.get("hide_window_on_start", False)


def test_boot_and_startup_screens_cover_the_pre_python_window():
    # the window is up before Python is; without these the user stares at a
    # bare blank rectangle during app.zip extraction and interpreter boot
    app = _pyproject()["tool"]["flet"]["app"]
    assert app["boot_screen"]["show"] is True
    assert app["startup_screen"]["show"] is True


def test_app_code_never_writes_window_visibility():
    # a `window.visible = False` anywhere in the UI would hide a live window
    # that only a healthy Python session could bring back — recreating the
    # invisible-zombie failure the visible-start design removes
    for py in (ROOT / "src").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "window.visible = " not in src, f"visibility write in {py}"


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
