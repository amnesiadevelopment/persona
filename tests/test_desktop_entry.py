import os
import pathlib

import pytest

from src.core import desktop_entry
from src.core.desktop_entry import (
    APP_ID,
    entry_content,
    entry_path,
    icon_path,
    install_desktop_entry,
    launch_command,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    # expanduser reads USERPROFILE on Windows and HOME on POSIX; set both so
    # the test isolates the filesystem on either OS.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def on_linux(monkeypatch):
    monkeypatch.setattr(desktop_entry, "supports_linux_desktop_integration", lambda: True)


def test_entry_id_matches_window_app_id(fake_home):
    # lxqt-panel resolves the taskbar icon by matching the Wayland app_id
    # against the desktop-entry id, so the filename stem MUST equal the
    # window's app_id ("persona", set by the flet-built runner).
    assert entry_path().name == f"{APP_ID}.desktop"
    assert APP_ID == "persona"


def test_entry_content_declares_icon_and_wmclass():
    text = entry_content("/opt/persona.AppImage", "/icons/persona.png")
    assert "[Desktop Entry]" in text
    assert "Type=Application\n" in text
    assert f"Name={APP_ID}\n" in text
    assert "Exec=/opt/persona.AppImage\n" in text
    assert "Icon=/icons/persona.png\n" in text
    assert f"StartupWMClass={APP_ID}\n" in text
    assert "Terminal=false\n" in text


def test_entry_content_quotes_paths_with_spaces():
    text = entry_content("/home/my user/persona.AppImage", "/icons/persona.png")
    assert 'Exec="/home/my user/persona.AppImage"\n' in text


def test_launch_command_prefers_appimage_env(monkeypatch):
    # Inside a mounted AppImage sys.argv[0] points at the transient /tmp
    # mount; $APPIMAGE is the on-disk file the user actually runs.
    monkeypatch.setenv("APPIMAGE", "/home/user/persona-x86_64.AppImage")
    assert launch_command() == "/home/user/persona-x86_64.AppImage"


def test_launch_command_falls_back_to_argv0(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert launch_command() == os.path.abspath(os.sys.argv[0])


def test_install_writes_entry_and_icon(fake_home, on_linux, monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/user/persona-x86_64.AppImage")
    path = install_desktop_entry()
    assert path is not None
    entry = pathlib.Path(path)
    assert entry == entry_path()
    text = entry.read_text(encoding="utf-8")
    assert f"StartupWMClass={APP_ID}" in text
    assert "Exec=/home/user/persona-x86_64.AppImage" in text
    # the icon is copied out of the (transient) bundle to a stable location
    # and referenced by absolute path so it survives the AppImage unmounting
    icon = icon_path()
    assert icon.exists()
    assert icon.stat().st_size > 0
    assert f"Icon={icon}" in text


def test_install_is_idempotent_and_refreshes_moved_appimage(fake_home, on_linux, monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/a/persona.AppImage")
    p1 = install_desktop_entry()
    p2 = install_desktop_entry()
    assert p1 == p2
    # the entry follows the AppImage when the user moves/renames it
    monkeypatch.setenv("APPIMAGE", "/b/persona.AppImage")
    text = pathlib.Path(install_desktop_entry()).read_text(encoding="utf-8")
    assert "Exec=/b/persona.AppImage" in text


def test_install_repairs_stale_entry(fake_home, on_linux, monkeypatch):
    # A pre-existing entry from the source-run era (StartupWMClass=flet,
    # dead Exec) is what left prod's window without an icon; it must be
    # replaced, not kept.
    monkeypatch.setenv("APPIMAGE", "/home/user/persona-x86_64.AppImage")
    stale = entry_path()
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(
        "[Desktop Entry]\nType=Application\nName=persona\n"
        "Exec=/home/user/persona/persona.sh\nStartupWMClass=flet\n",
        encoding="utf-8",
    )
    text = pathlib.Path(install_desktop_entry()).read_text(encoding="utf-8")
    assert "StartupWMClass=persona" in text
    assert "persona.sh" not in text


def test_install_noop_off_linux(fake_home, monkeypatch):
    monkeypatch.setattr(desktop_entry, "supports_linux_desktop_integration", lambda: False)
    assert install_desktop_entry() is None
    assert not entry_path().exists()


def test_install_survives_readonly_home(fake_home, on_linux, monkeypatch):
    # Desktop integration must never break app startup.
    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(desktop_entry.pathlib.Path, "mkdir", boom)
    assert install_desktop_entry() is None
