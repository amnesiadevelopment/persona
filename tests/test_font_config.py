import pathlib
import shutil
import subprocess

import pytest

from src.services.browser.font_config import (
    bundled_fonts_dir,
    build_font_config,
)


def _fc_match(conf_path: str, family: str) -> str:
    out = subprocess.run(
        ["fc-match", family],
        env={"FONTCONFIG_FILE": conf_path},
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


_FONTS_PRESENT = list(pathlib.Path(bundled_fonts_dir()).rglob("*.tt*"))
_needs_fonts = pytest.mark.skipif(
    not _FONTS_PRESENT,
    reason="bundled fonts not fetched (download-fonts.sh runs in CI, not locally)",
)


@_needs_fonts
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


@pytest.mark.skipif(
    shutil.which("fc-match") is None, reason="fontconfig not installed"
)
@pytest.mark.parametrize(
    "requested,expected_face",
    [
        ("Arial", "Arimo"),
        ("Helvetica", "Arimo"),
        ("Segoe UI", "Arimo"),
        ("Verdana", "Arimo"),
        ("Tahoma", "Arimo"),
        ("Times New Roman", "Tinos"),
        ("Georgia", "Tinos"),
        ("Courier New", "Cousine"),
        ("Consolas", "Cousine"),
    ],
)
def test_named_windows_families_resolve_to_metric_clones(
    tmp_path, requested, expected_face
):
    # A Windows profile must map the common named families a real site asks
    # for (Arial, Times New Roman, ...) onto their bundled metric-compatible
    # clones. Without this they fall through to DejaVu Sans, whose advance
    # widths differ and break column layout (Google Sheets shifts).
    conf = build_font_config(str(tmp_path), "windows")
    resolved = _fc_match(conf, requested)
    assert expected_face in resolved, (
        f"{requested!r} resolved to {resolved!r}, expected {expected_face}"
    )
    assert "DejaVu" not in resolved, (
        f"{requested!r} fell through to DejaVu: {resolved!r}"
    )


@_needs_fonts
def test_emoji_font_bundled():
    d = pathlib.Path(bundled_fonts_dir())
    assert (d / "common" / "NotoColorEmoji.ttf").exists(), (
        "Noto Color Emoji not bundled — emoji render as tofu"
    )


@pytest.mark.skipif(
    shutil.which("fc-match") is None, reason="fontconfig not installed"
)
@pytest.mark.parametrize("os_type", ["windows", "macos", "linux"])
def test_emoji_codepoint_resolves_to_color_emoji(tmp_path, os_type):
    # An emoji codepoint (U+2705 ✅) must resolve to Noto Color Emoji, not tofu
    # in DejaVu. fc-match by charset proves the glyph is reachable.
    conf = build_font_config(str(tmp_path / os_type), os_type)
    out = subprocess.run(
        ["fc-match", ":charset=2705"],
        env={"FONTCONFIG_FILE": conf},
        capture_output=True, text=True, check=True,
    ).stdout
    assert "Emoji" in out, f"U+2705 resolved to {out!r}, expected Noto Color Emoji"


@pytest.mark.skipif(
    shutil.which("fc-match") is None, reason="fontconfig not installed"
)
def test_platform_emoji_families_map_to_bundled(tmp_path):
    conf = build_font_config(str(tmp_path), "windows")
    for fam in ("Segoe UI Emoji", "Apple Color Emoji"):
        out = _fc_match(conf, fam)
        assert "Emoji" in out, f"{fam} resolved to {out!r}"
