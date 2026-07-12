import os
import sys

import pytest

from src.core.assets import asset_path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_dev_path_points_at_src_assets():
    p = asset_path("v_engine.png")
    assert p.replace("\\", "/").endswith("src/assets/v_engine.png")


def test_nested_parts():
    p = asset_path("flags", "us.svg")
    assert p.replace("\\", "/").endswith("src/assets/flags/us.svg")


def test_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    p = asset_path("icon.png")
    assert p == os.path.join(str(tmp_path), "src", "assets", "icon.png")


def test_real_assets_exist():
    # the engine icon and app icon must actually ship
    assert os.path.exists(asset_path("v_engine.png"))
    assert os.path.exists(asset_path("icon.png"))


def test_macos_build_icon_follows_apple_icon_grid():
    # flet build macos picks assets/icon_macos.png over assets/icon.png
    # (flet_cli find_platform_image), so macOS gets an icon that follows
    # Apple's grid: 1024x1024 canvas, artwork inside an 824x824 rounded
    # square (100px transparent gutter per side), no self-drawn keyline
    # border — macOS applies its own squircle mask and shadow, and Tahoe
    # puts non-conforming icons into a shrunken "glass jail" backdrop.
    Image = pytest.importorskip("PIL.Image")
    path = os.path.join(REPO_ROOT, "assets", "icon_macos.png")
    assert os.path.exists(path)

    im = Image.open(path).convert("RGBA")
    assert im.size == (1024, 1024)
    px = im.load()

    # 100px gutter: everything within 92px of each edge must be transparent
    # (8px slack for anti-aliasing of the artwork boundary)
    for x in range(1024):
        for y in (0, 45, 91, 1024 - 92, 1024 - 46, 1023):
            assert px[x, y][3] == 0, f"opaque pixel in top/bottom gutter at {(x, y)}"
            assert px[y, x][3] == 0, f"opaque pixel in left/right gutter at {(y, x)}"

    # artwork present: dark opaque fill at the canvas center
    r, g, b, a = px[512, 512]
    assert a == 255 and r < 40 and g < 40 and b < 40

    # brand mark present: lime-green fingerprint pixels somewhere in the art
    greens = sum(
        1
        for x in range(300, 724, 4)
        for y in range(300, 724, 4)
        if (p := px[x, y])[3] > 200 and p[1] > 180 and p[1] > p[0] and p[1] > p[2]
    )
    assert greens > 50

    # no keyline border: the artwork edge must be the dark fill, not green
    for probe in ((512, 110), (512, 913), (110, 512), (913, 512)):
        r, g, b, a = px[probe]
        assert a == 255 and g < 80, f"green border pixel at {probe}"

    # corners of the ART box stay transparent (rounded, not square art)
    for probe in ((104, 104), (919, 104), (104, 919), (919, 919)):
        assert px[probe][3] == 0, f"square-cornered artwork at {probe}"


def test_windows_linux_icon_unchanged():
    # Win/Linux keep the bordered rounded-square icon.png (green keyline at
    # the edge) — the macOS-specific asset must not have replaced it.
    Image = pytest.importorskip("PIL.Image")
    im = Image.open(os.path.join(REPO_ROOT, "assets", "icon.png")).convert("RGBA")
    assert im.size == (256, 256)
    px = im.load()
    r, g, b, a = px[6, 128]
    assert a == 255 and g > 200 and g > r and g > b
