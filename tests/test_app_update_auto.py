"""The update flow downloads by itself as soon as a newer version is seen,
then ASKS before installing (except the Linux AppImage auto-update path, which
installs unattended when enabled and idle — prod boxes rely on it)."""

import os
import time
from types import SimpleNamespace

import flet as ft

from src.ui import app as ui_app


class FakePage:
    def __init__(self):
        self.dialogs = []
        self.popped = 0

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        self.popped += 1


def make_app(*, running=()):
    app = ui_app.App.__new__(ui_app.App)
    app.page = FakePage()
    app.bl = SimpleNamespace(running_profile_names=lambda: list(running))
    app._update_in_progress = False
    app._update_staged = ""
    app._app_latest = "v9.9.9"
    app._app_update_url = "http://x"
    app._app_update_size = 3
    app._app_update_tag = "v9.9.9"
    app._app_update_status = ""
    app._app_update_done = 0
    app._app_update_total = 0
    app._update_start_t = 0.0
    app._ui = lambda fn: fn()
    app._refresh_sidebar = lambda: None
    app.logs = []
    app._log = app.logs.append
    app.applied = []
    app._apply_update = app.applied.append
    return app


def _wire(monkeypatch, tmp_path, *, packaged=True, verify_ok=True, linux=False,
          auto=True):
    staged = tmp_path / "persona-update-setup-v9.9.9.exe"
    downloads = []

    def fake_download(url, progress=None, size=0, tag=""):
        downloads.append(url)
        staged.write_bytes(b"new")
        return str(staged)

    monkeypatch.setattr(ui_app.app_update, "can_self_update", lambda: packaged)
    monkeypatch.setattr(
        ui_app.app_update, "find_ready_staged",
        lambda url, size=0, tag="": "",
    )
    monkeypatch.setattr(ui_app.app_update, "download_update", fake_download)
    monkeypatch.setattr(
        ui_app.app_update, "verify_staged_installer",
        lambda s, tag="", log=None: verify_ok,
    )
    monkeypatch.setattr(ui_app._platform, "IS_LINUX", linux)
    monkeypatch.setattr(
        ui_app.app_settings, "is_auto_update_enabled", lambda: auto
    )
    return staged, downloads


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_detection_downloads_by_itself_then_asks(monkeypatch, tmp_path):
    # Windows: no manual click to start the download; the install still asks.
    app = make_app()
    staged, downloads = _wire(monkeypatch, tmp_path, auto=True)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.page.dialogs), "install prompt never appeared"
    assert downloads == ["http://x"]
    assert app._app_update_status == "ready"
    assert app._update_staged == str(staged)
    assert app.applied == []  # asked, did NOT install on its own


def test_download_starts_even_with_auto_update_off(monkeypatch, tmp_path):
    app = make_app()
    _, downloads = _wire(monkeypatch, tmp_path, auto=False)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.page.dialogs)
    assert downloads == ["http://x"]
    assert app.applied == []


def test_install_prompt_button_applies_the_update(monkeypatch, tmp_path):
    app = make_app()
    staged, _ = _wire(monkeypatch, tmp_path)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.page.dialogs)
    dlg = app.page.dialogs[0]
    install = next(
        a for a in dlg.actions if "install" in str(a.content).lower()
    )
    install.on_click(None)
    assert _wait(lambda: app.applied)
    assert app.applied == [str(staged)]
    assert app.page.popped == 1


def test_linux_auto_update_still_installs_unattended(monkeypatch, tmp_path):
    app = make_app()
    staged, _ = _wire(monkeypatch, tmp_path, linux=True, auto=True)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.applied)
    assert app.applied == [str(staged)]
    assert app.page.dialogs == []  # unattended path doesn't ask


def test_linux_with_auto_off_asks(monkeypatch, tmp_path):
    app = make_app()
    _wire(monkeypatch, tmp_path, linux=True, auto=False)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.page.dialogs)
    assert app.applied == []


def test_corrupt_download_is_quarantined_and_retried(monkeypatch, tmp_path):
    app = make_app()
    staged, _ = _wire(monkeypatch, tmp_path, verify_ok=False)
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app._app_update_status == "failed")
    assert not os.path.exists(str(staged))  # never offered again
    assert app._update_staged == ""
    assert app._app_latest == ""  # the periodic check re-triggers a fresh try
    assert app.page.dialogs == []


def test_source_run_only_notifies(monkeypatch, tmp_path):
    app = make_app()
    _, downloads = _wire(monkeypatch, tmp_path, packaged=False)
    app._on_update_found("v9.9.9", "http://x")
    assert downloads == []
    assert any("update from source" in m for m in app.logs)


def test_ready_staged_from_previous_run_asks_too(monkeypatch, tmp_path):
    # the app was reopened before restarting into an already-downloaded update
    app = make_app()
    staged, downloads = _wire(monkeypatch, tmp_path)
    staged.write_bytes(b"new")
    monkeypatch.setattr(
        ui_app.app_update, "find_ready_staged",
        lambda url, size=0, tag="": str(staged),
    )
    app._on_update_found("v9.9.9", "http://x")
    assert _wait(lambda: app.page.dialogs)
    assert downloads == []  # no re-download
    assert app._app_update_status == "ready"
    assert app.applied == []


def test_update_ready_dialog_builds_headless():
    from src.ui.dialogs.update_ready import open_update_ready_dialog

    page = FakePage()
    calls = []
    open_update_ready_dialog(page, tag="v9.9.9", on_install=lambda: calls.append(1))
    assert len(page.dialogs) == 1
    dlg = page.dialogs[0]
    assert isinstance(dlg, ft.AlertDialog)
    assert "v9.9.9" in str(dlg.title.value)
    labels = [str(a.content).lower() for a in dlg.actions]
    assert any("install" in l for l in labels)
    assert any("later" in l for l in labels)
    later = next(a for a in dlg.actions if "later" in str(a.content).lower())
    later.on_click(None)
    assert page.popped == 1 and calls == []
