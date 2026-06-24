import os

from ..core.assets import asset_path

_FLAGS_DIR = asset_path("flags")


def flag_path(country_code: str) -> str | None:
    """Absolute path to the SVG flag for a country code, or None if absent."""
    code = (country_code or "").strip().lower()
    if len(code) != 2 or not code.isalpha():
        return None
    path = os.path.join(_FLAGS_DIR, f"{code}.svg")
    return path if os.path.exists(path) else None
