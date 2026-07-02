import pytest

from src.services.app_update import updater as au


def test_app_version_is_set():
    assert au.APP_VERSION
    assert isinstance(au.APP_VERSION, str)


def test_update_available_true_when_remote_newer():
    assert au.update_available("0.2.0", "0.1.0") is True


def test_update_available_false_when_same_or_older():
    assert au.update_available("0.1.0", "0.1.0") is False
    assert au.update_available("0.1.0", "0.2.0") is False


def test_update_available_false_when_no_remote():
    assert au.update_available("", "0.1.0") is False


def test_release_url_built_from_configured_repo(monkeypatch):
    monkeypatch.setattr(au, "APP_REPO", "someone/persona")
    assert au.releases_api() == (
        "https://api.github.com/repos/someone/persona/releases/latest"
    )


def test_release_url_empty_when_repo_unconfigured(monkeypatch):
    monkeypatch.setattr(au, "APP_REPO", "")
    assert au.releases_api() == ""


def test_check_returns_none_when_repo_unconfigured(monkeypatch):
    # no GitHub repo set yet -> check is a no-op, never crashes
    monkeypatch.setattr(au, "APP_REPO", "")
    assert au.check_for_update() == ("", "", 0)


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(au._platform, "IS_WINDOWS", win)
    monkeypatch.setattr(au._platform, "IS_MACOS", mac)
    monkeypatch.setattr(au._platform, "IS_LINUX", linux)


_ASSETS = [
    {"name": "persona-windows-setup.exe", "browser_download_url": "uwin"},
    {"name": "persona-x86_64.AppImage", "browser_download_url": "ulin"},
    {"name": "persona-macos.dmg", "browser_download_url": "umac"},
]


def test_pick_asset_linux(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert au.pick_asset(_ASSETS) == ("ulin", 0)


def test_pick_asset_windows(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert au.pick_asset(_ASSETS) == ("uwin", 0)


def test_pick_asset_macos(monkeypatch):
    _force_os(monkeypatch, mac=True)
    assert au.pick_asset(_ASSETS) == ("umac", 0)


def test_pick_asset_none_when_os_asset_absent(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert au.pick_asset(
        [{"name": "persona-windows-x64.zip", "browser_download_url": "u1"}]
    ) == ("", 0)


def test_asset_name_per_os(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert au.asset_name() == "persona-windows-setup.exe"
    _force_os(monkeypatch, mac=True)
    assert au.asset_name() == "persona-macos.dmg"
    _force_os(monkeypatch, linux=True)
    assert au.asset_name() == "persona-x86_64.AppImage"


def test_staged_path_windows_uses_temp(monkeypatch):
    # On Windows the staged installer goes to a temp file (there's no $APPIMAGE
    # to sit next to), so downloading has somewhere to land.
    _force_os(monkeypatch, win=True)
    p = au.staged_path()
    assert p
    assert p.endswith(".exe")


def test_apply_and_restart_windows_runs_installer_silently(monkeypatch, tmp_path):
    # On Windows apply runs the downloaded setup.exe silently (Inno /SILENT), so
    # it upgrades in place and restarts persona — no manual "download it yourself".
    _force_os(monkeypatch, win=True)
    staged = tmp_path / "persona-windows-setup.exe"
    staged.write_bytes(b"MZ")
    called = {}

    def fake_popen(args, **kw):
        called["args"] = args
        called["kw"] = kw

        class P:
            pass

        return P()

    monkeypatch.setattr(au.subprocess, "Popen", fake_popen)
    # no_window_kwargs touches Windows-only subprocess constants; stub it so the
    # test runs on the Linux CI too.
    monkeypatch.setattr(au._platform, "no_window_kwargs", lambda: {})

    # don't actually exit the test process — raise instead so we can assert it
    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setattr(au.os, "_exit", fake_exit)
    msgs = []
    with pytest.raises(SystemExit):
        au.apply_and_restart(str(staged), log=msgs.append)
    # the installer was launched, with a silent flag
    assert str(staged) in called["args"]
    assert any("/SILENT" in a or "/silent" in a.lower() for a in called["args"])


def test_apply_and_restart_macos_is_notify_only(monkeypatch, tmp_path):
    # macOS has no self-updater yet; apply must notify and never touch anything.
    _force_os(monkeypatch, mac=True)
    staged = tmp_path / "staged.dmg"
    staged.write_bytes(b"x")
    msgs = []
    assert au.apply_and_restart(str(staged), log=msgs.append) is False
    assert any("update available" in m.lower() for m in msgs)
