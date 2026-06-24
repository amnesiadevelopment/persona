import os
import sys

from src.core.assets import asset_path


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
