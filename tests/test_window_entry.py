import pathlib

from src.services.browser.window_entry import app_id_for, write_window_entry


def test_app_id_is_persona_prefixed():
    assert app_id_for("test8") == "persona-test8"


def test_write_creates_desktop_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_window_entry("test8")
    assert pathlib.Path(path).exists()
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert "StartupWMClass=persona-test8" in text
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
