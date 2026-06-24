import os
import pathlib

from ...core.logging import get_logger

logger = get_logger("browser.window_entry")


def app_id_for(profile_name: str) -> str:
    return f"persona-{profile_name}"


def _entry_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.local/share/applications"))


def _safe_filename(profile_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    return f"persona-{safe}.desktop"


def write_window_entry(profile_name: str) -> str:
    """Write a desktop entry so the Wayland taskbar shows the browser window
    with the chromium icon and the profile name instead of a generic fallback.

    labwc/lxqt-panel matches a toplevel's app_id against StartupWMClass to pick
    the icon and label; the browser is launched with --class=persona-<name>.
    """
    entry_dir = _entry_dir()
    entry_dir.mkdir(parents=True, exist_ok=True)
    path = entry_dir / _safe_filename(profile_name)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={profile_name}\n"
        "Icon=chromium\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"StartupWMClass={app_id_for(profile_name)}\n"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)
