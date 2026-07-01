"""Platform detection and per-OS specifics, so the rest of the app can stay
OS-agnostic. persona started Linux-only (AppImage); these helpers let it run
natively on Windows and macOS without scattering `sys.platform` checks
everywhere.
"""

import os
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def os_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return "linux"


def fingerprint_chromium_filename() -> str:
    """The fingerprint-chromium executable name for this OS. The Linux build
    ships as an AppImage; Windows is a chrome.exe inside the extracted package;
    macOS is the binary inside the .app bundle."""
    if IS_WINDOWS:
        return "chrome.exe"
    if IS_MACOS:
        return "Chromium.app/Contents/MacOS/Chromium"
    return "fpchrome.AppImage"


def needs_fork_launch() -> bool:
    """The Firefox-engine launcher forks on Linux to dodge the flet-AppImage's
    embedded Python (where `subprocess([sys.executable, '-m', ...])` loads
    incompatible .pyc — 'bad magic number'). On a native Windows/macOS build
    sys.executable is a normal interpreter, so a plain subprocess works and
    fork is unavailable (Windows) or unsafe (macOS default is spawn)."""
    return IS_LINUX


def supports_linux_desktop_integration() -> bool:
    """Wayland app_id (--class), fontconfig, and .desktop entries are Linux/X11
    concepts. On Windows/macOS the OS labels windows differently, so we skip
    them rather than emulate."""
    return IS_LINUX


def default_display() -> str | None:
    """X11 DISPLAY only matters on Linux; None elsewhere."""
    return ":0" if IS_LINUX else None


def no_window_kwargs() -> dict:
    """subprocess kwargs that keep a console tool (curl, engine binaries) from
    flashing a console window on Windows. Empty on Linux/macOS, where a
    subprocess never spawns a visible console."""
    if not IS_WINDOWS:
        return {}
    import subprocess

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }
