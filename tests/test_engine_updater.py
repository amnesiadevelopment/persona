import src.core.platform as _platform
from src.services.engine import updater
from src.services.engine.updater import (
    appimage_url_for,
    is_newer,
    parse_version,
)

WIN_ASSET = "ungoogled-chromium_148.0.7778.215-1.1_windows_x64.zip"
MAC_ASSET = "ungoogled-chromium_148.0.7778.215-1.1_macos.dmg"
LINUX_ASSET = "ungoogled-chromium-148.0.7778.215-1-x86_64.AppImage"
LINUX_TARXZ = "ungoogled-chromium-148.0.7778.215-1-x86_64_linux.tar.xz"
WIN_INSTALLER = "ungoogled-chromium_148.0.7778.215-1.1_installer_x64.exe"


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


def test_parse_version():
    assert parse_version("144.0.7559.132") == (144, 0, 7559, 132)
    assert parse_version("") == ()
    assert parse_version("v143.0.1") == (143, 0, 1)


def test_is_newer():
    assert is_newer("144.0.7559.132", "143.0.7000.10") is True
    assert is_newer("144.0.7559.132", "144.0.7559.132") is False
    assert is_newer("144.0.7559.100", "144.0.7559.132") is False


def test_is_newer_edges():
    assert is_newer("144.0.0.1", "") is True       # nothing installed
    assert is_newer("", "144.0.0.1") is False       # no latest info


def test_appimage_url():
    url = appimage_url_for("144.0.7559.132")
    assert url.endswith("/144.0.7559.132/ungoogled-chromium-144.0.7559.132-1-x86_64.AppImage")
    assert url.startswith("https://github.com/adryfish/fingerprint-chromium/")


def test_asset_matches_linux_picks_appimage(monkeypatch):
    _force_os(monkeypatch, linux=True)
    assert updater._asset_matches(LINUX_ASSET) is True
    assert updater._asset_matches(WIN_ASSET) is False
    assert updater._asset_matches(MAC_ASSET) is False
    assert updater._asset_matches(LINUX_TARXZ) is False  # not the AppImage


def test_asset_matches_windows_picks_zip(monkeypatch):
    _force_os(monkeypatch, win=True)
    assert updater._asset_matches(WIN_ASSET) is True
    assert updater._asset_matches(LINUX_ASSET) is False
    assert updater._asset_matches(MAC_ASSET) is False
    assert updater._asset_matches(WIN_INSTALLER) is False  # zip, not the .exe installer


def test_asset_matches_macos_picks_dmg(monkeypatch):
    _force_os(monkeypatch, mac=True)
    assert updater._asset_matches(MAC_ASSET) is True
    assert updater._asset_matches(WIN_ASSET) is False
    assert updater._asset_matches(LINUX_ASSET) is False


def test_fetch_latest_full_selects_per_os_asset(monkeypatch):
    release = {
        "tag_name": "148.0.7778.215",
        "assets": [
            {"name": LINUX_ASSET, "browser_download_url": "http://x/linux", "digest": "sha256:aa"},
            {"name": WIN_ASSET, "browser_download_url": "http://x/win", "digest": "sha256:bb"},
            {"name": MAC_ASSET, "browser_download_url": "http://x/mac", "digest": "sha256:cc"},
        ],
    }

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return __import__("json").dumps(release).encode()

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: FakeResp())

    _force_os(monkeypatch, win=True)
    tag, url, digest = updater.fetch_latest_full()
    assert (tag, url, digest) == ("148.0.7778.215", "http://x/win", "sha256:bb")

    _force_os(monkeypatch, mac=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/mac"

    _force_os(monkeypatch, linux=True)
    _, url, _ = updater.fetch_latest_full()
    assert url == "http://x/linux"
