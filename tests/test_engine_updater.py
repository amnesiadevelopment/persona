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


def test_download_engine_forwards_digest_verification(monkeypatch, tmp_path):
    # download_engine must pass the digest down; an un-digested asset is refused
    # (allow_missing stays False by default), a digested one verifies as normal.
    calls = []

    def fake_download_to(path, url, timeout, digest, progress, allow_missing=False):
        calls.append((digest, allow_missing))
        return bool(digest) or allow_missing

    monkeypatch.setattr(updater, "_download_to", fake_download_to)
    monkeypatch.setattr(updater, "_install_linux", lambda p: True)
    monkeypatch.setattr(updater, "_install_windows", lambda p: True)
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "ENGINE_DIR", str(tmp_path))

    # digested asset installs
    assert updater.download_engine("http://x/linux", digest="sha256:aa") is True
    # un-digested asset is refused by default (fail-closed)
    assert updater.download_engine("http://x/linux", digest="") is False
    assert calls[-1] == ("", False)
    # ...unless the caller explicitly allows it (Linux predictable-URL fallback)
    assert (
        updater.download_engine("http://x/linux", digest="", allow_unverified=True)
        is True
    )
    assert calls[-1] == ("", True)


def test_ensure_engine_allows_unverified_only_for_linux_fallback(monkeypatch):
    # When fetch_latest_full returns a url but NO digest, that only happens on
    # the Linux predictable-URL fallback — ensure_engine must allow it there.
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/linux", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)
    seen = {}

    def fake_download(url, timeout=600, digest=None, progress=None, allow_unverified=False):
        seen["allow_unverified"] = allow_unverified
        return True

    monkeypatch.setattr(updater, "download_engine", fake_download)
    ok, msg = updater.ensure_engine(attempts=1)
    assert ok is True
    assert seen["allow_unverified"] is True


def _make_windows_zip(path, members):
    """Write a zip at `path` containing {name: bytes}."""
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_install_windows_atomic_via_staging(monkeypatch, tmp_path):
    # The Windows zip must not be extracted straight into ENGINE_DIR (chrome.exe
    # could appear before its DLLs). Extract into a staging dir, then move the
    # whole tree into ENGINE_DIR at once.
    _force_os(monkeypatch, win=True)
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "ENGINE_BINARY", str(engine_dir / "chrome.exe"))

    zip_path = tmp_path / "win.zip"
    _make_windows_zip(
        zip_path,
        {
            "chrome-win/chrome.exe": b"MZ" + b"\x00" * 100,
            "chrome-win/some.dll": b"\x00" * 50,
            "chrome-win/locales/en.pak": b"pak",
        },
    )

    assert updater._install_windows(str(zip_path)) is True
    assert (engine_dir / "chrome.exe").read_bytes().startswith(b"MZ")
    assert (engine_dir / "some.dll").exists()
    assert (engine_dir / "locales" / "en.pak").read_bytes() == b"pak"
    # staging must be cleaned up, not left beside the engine
    assert not any(p.name.startswith(".staging") for p in engine_dir.iterdir())


def test_download_engine_writes_marker_last(monkeypatch, tmp_path):
    # The completion marker must be written only AFTER a successful install, so
    # is_installed() can gate on it. If install fails, no marker.
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(
        updater, "_download_to", lambda *a, **k: True
    )

    monkeypatch.setattr(updater, "_install_linux", lambda p: True)
    assert updater.download_engine("http://x/e", digest="sha256:aa") is True
    assert (engine_dir / ".engine-complete").exists()

    # failed install → no marker
    (engine_dir / ".engine-complete").unlink()
    monkeypatch.setattr(updater, "_install_linux", lambda p: False)
    assert updater.download_engine("http://x/e", digest="sha256:aa") is False
    assert not (engine_dir / ".engine-complete").exists()


def test_download_engine_serialized_by_lock(monkeypatch, tmp_path):
    # Two concurrent installs (UI update thread + ensure_engine) must not run
    # their extract/move at the same time into the shared ENGINE_DIR.
    import threading

    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setattr(updater, "ENGINE_DIR", str(engine_dir))
    monkeypatch.setattr(updater, "MARKER_FILE", str(engine_dir / ".engine-complete"))
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(updater, "_download_to", lambda *a, **k: True)

    overlap = {"max": 0, "cur": 0}
    lock = threading.Lock()

    def slow_install(p):
        with lock:
            overlap["cur"] += 1
            overlap["max"] = max(overlap["max"], overlap["cur"])
        # busy a moment so a second thread would overlap if unserialized
        for _ in range(100000):
            pass
        with lock:
            overlap["cur"] -= 1
        return True

    monkeypatch.setattr(updater, "_install_linux", slow_install)

    threads = [
        threading.Thread(
            target=lambda: updater.download_engine("http://x/e", digest="sha256:aa")
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    # install never ran concurrently
    assert overlap["max"] == 1


def test_ensure_engine_refuses_unverified_off_linux(monkeypatch):
    # On Windows/macOS a missing digest is NOT expected (assets always carry one)
    # -> ensure_engine must NOT allow unverified there.
    _force_os(monkeypatch, win=True)
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full", lambda *a, **k: ("148.0", "http://x/win", "")
    )
    monkeypatch.setattr(updater, "write_version", lambda tag: None)
    seen = {}

    def fake_download(url, timeout=600, digest=None, progress=None, allow_unverified=False):
        seen["allow_unverified"] = allow_unverified
        return allow_unverified  # would only succeed if (wrongly) allowed

    monkeypatch.setattr(updater, "download_engine", fake_download)
    ok, _msg = updater.ensure_engine(attempts=1)
    assert seen["allow_unverified"] is False
    assert ok is False
