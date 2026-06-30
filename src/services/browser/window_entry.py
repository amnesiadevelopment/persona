import os
import pathlib
import re
import zlib

from ...core.logging import get_logger

logger = get_logger("browser.window_entry")


def app_id_for(profile_name: str) -> str:
    """The window app_id / WM_CLASS for a profile, shared by the .desktop
    StartupWMClass, the browser's --class, and (on Firefox) MOZ_APP_REMOTINGNAME.

    Must be a valid DBus path segment ([A-Za-z0-9_]): Firefox uses the app_id as
    its DBus remoting name, and a dash or space makes it reject the name and fall
    back to a shared default, so two profiles collide and the second won't open.
    A crc of the original name keeps profiles whose names collapse to the same
    sanitised form (e.g. "a-b" and "a b") distinct."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", profile_name)
    digest = format(zlib.crc32(profile_name.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return f"persona_{safe}_{digest}"


def _entry_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.local/share/applications"))


def _safe_filename(profile_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    return f"persona-{safe}.desktop"


def write_window_entry(profile_name: str, icon: str = "chromium") -> str:
    """Write a desktop entry so the Wayland taskbar shows the browser window
    with the engine icon and the profile name instead of a generic fallback.

    labwc/lxqt-panel matches a toplevel's app_id against StartupWMClass to pick
    the icon and label; the browser is launched with --class=<app_id>.
    `icon` is the engine's icon-theme name (chromium / firefox).
    """
    entry_dir = _entry_dir()
    entry_dir.mkdir(parents=True, exist_ok=True)
    path = entry_dir / _safe_filename(profile_name)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={profile_name}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"StartupWMClass={app_id_for(profile_name)}\n"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)
