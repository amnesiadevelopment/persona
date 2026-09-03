"""The app's own launcher entry + window icon on Linux.

The AppImage carries a .desktop file and icon inside its squashfs, but nothing
installs them into the user's session (Whonix ships no appimaged), so the
compositor cannot map the app's window to an icon and the taskbar shows a
generic one. Maintain the entry and icon under ~/.local/share on every launch
so they stay valid across updates, moves, and stale hand-made entries.
"""

import os
import pathlib
import shutil
import sys

from .assets import asset_path
from .logging import get_logger
from .platform import supports_linux_desktop_integration

logger = get_logger("core.desktop_entry")

# The Wayland app_id of the flet-built window (the Flutter runner is named
# after the flet `product`). lxqt-panel/labwc resolve the taskbar icon by
# matching the app_id against a desktop-entry id, so the entry file MUST be
# "<app_id>.desktop".
APP_ID = "persona"


def _data_home() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.local/share"))


def icon_path() -> pathlib.Path:
    # The standard user icon-theme slot, so the icon also resolves for any
    # pre-existing entry that references it by name ("Icon=persona").
    return _data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"


def entry_path() -> pathlib.Path:
    return _data_home() / "applications" / f"{APP_ID}.desktop"


def launch_command() -> str:
    # Inside a mounted AppImage sys.argv[0] points at the transient /tmp
    # mount; $APPIMAGE is the on-disk file the user actually runs.
    return os.environ.get("APPIMAGE") or os.path.abspath(sys.argv[0])


def entry_content(exec_path: str, icon: str) -> str:
    exec_field = f'"{exec_path}"' if " " in exec_path else exec_path
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_ID}\n"
        "Comment=Anti-detect browser manager\n"
        f"Exec={exec_field}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Network;WebBrowser;\n"
        f"StartupWMClass={APP_ID}\n"
    )


def install_desktop_entry() -> str | None:
    """Write/refresh the launcher entry and icon; never let this break startup."""
    if not supports_linux_desktop_integration():
        return None
    try:
        icon_dst = icon_path()
        icon_src = asset_path("icon.png")
        if os.path.exists(icon_src):
            icon_dst.parent.mkdir(parents=True, exist_ok=True)
            if not icon_dst.exists() or icon_dst.stat().st_size != os.path.getsize(icon_src):
                shutil.copyfile(icon_src, icon_dst)
        entry = entry_path()
        entry.parent.mkdir(parents=True, exist_ok=True)
        content = entry_content(launch_command(), str(icon_dst))
        if not entry.exists() or entry.read_text(encoding="utf-8") != content:
            entry.write_text(content, encoding="utf-8")
        return str(entry)
    except (OSError, UnicodeDecodeError) as exc:
        # `UnicodeDecodeError` is here because it is NOT an `OSError` — it
        # inherits from `ValueError`, so the `read_text` above walked straight
        # through an `OSError`-only arm and the docstring's "never let this
        # break startup" became false: `main()` calls this bare, so a foreign
        # non-UTF-8 entry in the shared ~/.local/share/applications (localized
        # `Name[xx]`/`Comment[xx]` keys are historically latin-1) aborted the
        # app before the window opened. Named and narrow, mirroring
        # `_from_file` in `verify/exit_guard.py`: NOT bare `Exception`, and NOT
        # `errors="replace"` — a file persona cannot decode is not one persona
        # wrote, so declining it (leaving its bytes intact) is the right
        # semantic, not clobbering it.
        logger.warning("desktop entry install failed: %s", exc)
        return None
