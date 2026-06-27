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


def test_pick_binary_asset_filters_by_name():
    assets = [
        {"name": "persona-windows.exe", "browser_download_url": "u1"},
        {"name": "persona-x86_64.AppImage", "browser_download_url": "u2"},
        {"name": "persona-macos", "browser_download_url": "u3"},
    ]
    assert au.pick_asset(assets) == ("u2", 0)


def test_pick_binary_asset_none_when_no_appimage():
    assets = [{"name": "persona-windows.exe", "browser_download_url": "u1"}]
    assert au.pick_asset(assets) == ("", 0)
