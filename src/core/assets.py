import os
import sys


def asset_path(*parts: str) -> str:
    """Absolute path to a bundled asset, correct in dev and when frozen by
    PyInstaller (where assets land under sys._MEIPASS/src/assets).

    `parts` are relative to src/assets, e.g. asset_path("v_engine.png") or
    asset_path("flags", "us.svg").
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = os.path.join(meipass, "src", "assets")
    else:
        # this file is src/core/assets.py -> src/assets
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    return os.path.join(base, *parts)
