import pathlib
import re

from src.services.browser.window_entry import app_id_for, write_window_entry


def test_app_id_is_dbus_valid_and_unique():
    # The app_id doubles as the Firefox DBus remoting name, so it must be a
    # valid DBus path segment: only [A-Za-z0-9_]. A name with a dash/space
    # would be rejected by Firefox and collapse to the shared default, which
    # makes a second profile fail to launch.
    app_id = app_id_for("My Profile-8")
    assert re.fullmatch(r"persona_[A-Za-z0-9_]+", app_id)
    assert "-" not in app_id and " " not in app_id


def test_app_id_is_deterministic():
    # Same name → same id across launches, so the .desktop StartupWMClass keeps
    # matching the window and the icon stays stable.
    assert app_id_for("acc") == app_id_for("acc")


def test_app_id_distinguishes_names_that_sanitize_alike():
    # "a-b" and "a b" both sanitise to "a_b"; the crc suffix keeps them distinct
    # so two such profiles get different remoting names.
    assert app_id_for("a-b") != app_id_for("a b")


def test_write_uses_app_id_as_wmclass(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("test8")
    assert pathlib.Path(path).exists()
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert f"StartupWMClass={app_id_for('test8')}" in text
    assert "Name=test8" in text
    assert "Icon=chromium" in text


def test_write_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p1 = write_window_entry("acc")
    p2 = write_window_entry("acc")
    assert p1 == p2


def test_filename_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("my acc/01")
    # no path separators leak into the filename
    assert "/" not in pathlib.Path(path).name
    text = pathlib.Path(path).read_text(encoding="utf-8")
    # the human-facing Name keeps the original profile name
    assert "Name=my acc/01" in text
