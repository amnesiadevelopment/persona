import pathlib

from src.services.browser.font_config import (
    bundled_fonts_dir,
    build_font_config,
)


def test_bundled_dir_exists_and_has_fonts():
    d = pathlib.Path(bundled_fonts_dir())
    assert d.is_dir()
    fonts = list(d.rglob("*.tt*"))
    assert fonts, "no bundled font files found"


def test_build_writes_conf(tmp_path):
    conf = build_font_config(str(tmp_path), "windows")
    p = pathlib.Path(conf)
    assert p.exists()
    assert p.name == "fonts.conf"


def test_conf_excludes_system_fonts(tmp_path):
    conf = build_font_config(str(tmp_path), "linux")
    text = pathlib.Path(conf).read_text()
    assert bundled_fonts_dir() in text
    assert "/usr/share/fonts" not in text


def test_conf_includes_common_and_os_dir(tmp_path):
    conf = build_font_config(str(tmp_path), "macos")
    text = pathlib.Path(conf).read_text()
    assert "common" in text
    assert "macos" in text


def test_per_os_configs_differ(tmp_path):
    win = pathlib.Path(
        build_font_config(str(tmp_path / "w"), "windows")
    ).read_text()
    mac = pathlib.Path(
        build_font_config(str(tmp_path / "m"), "macos")
    ).read_text()
    lin = pathlib.Path(
        build_font_config(str(tmp_path / "l"), "linux")
    ).read_text()
    # each OS exposes a different font set / family preference
    assert win != mac != lin
    assert "Arimo" in win and "Arimo" not in lin
    assert "windows" in win and "macos" in mac and "linux" in lin


def test_unknown_os_falls_back_to_linux(tmp_path):
    conf = build_font_config(str(tmp_path), "plan9")
    text = pathlib.Path(conf).read_text()
    assert "linux" in text


def test_cjk_match_present(tmp_path):
    conf = build_font_config(str(tmp_path), "windows")
    text = pathlib.Path(conf).read_text()
    assert "Noto Sans CJK SC" in text
    assert 'compare="contains"' in text


def test_valid_xml(tmp_path):
    conf = build_font_config(str(tmp_path), "windows")
    text = pathlib.Path(conf).read_text()
    assert "<fontconfig>" in text
    assert "<cachedir>" in text
    assert str(tmp_path) in text
