"""Install-location and relaunch-environment facts shared by the update paths.

These are not the full installer's private business, though they lived there:
both the full installer and the code-only fast path need to know WHERE the
installed persona.exe is and WHAT environment a process that outlives this one
may inherit. The fast path used to reach across for them with an in-function

    from .updater import _installed_windows_exe, _relaunch_env

written inside a function body to dodge an import cycle — a module boundary that
had failed. They live here instead, so both callers import a public name from a
module that depends on neither of them.
"""

import os
import sys


# Per-process values the flet runtime plants in os.environ: PYTHONPATH /
# PYTHONHOME point into THIS install's private extraction dir, and the FLET_*
# server vars carry this process's socket/port. A relaunched build re-applies
# any INHERITED values over its own correct ones (the build template sets every
# one of these with putIfAbsent — an inherited value silently beats the fresh
# one), so leaking them makes the new persona import from a dead path or bind a
# stale port the client never connects to — it starts and dies with no window
# (#135 on Linux; the same inheritance runs through the Windows relaunch chain
# persona -> cmd -> start persona.exe).
RUNTIME_ENV_VARS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONUNBUFFERED",
    "FLET_SERVER_UDS_PATH",
    "FLET_SERVER_PORT",
    "FLET_PYTHON_CALLBACK_SOCKET_ADDR",
    "FLET_APP_CONSOLE",
    "FLET_APP_STORAGE_DATA",
    "FLET_APP_STORAGE_TEMP",
    "FLET_PLATFORM",
    "FLET_ASSETS_DIR",
    # the client hides its window when this is merely PRESENT (any value) and
    # nothing client-side ever shows it again — inherited across a relaunch it
    # turns the new persona into an invisible zombie
    "FLET_HIDE_WINDOW_ON_START",
)


def relaunch_env() -> dict:
    """A copy of the environment with every flet/python runtime var dropped,
    for processes that outlive this persona and start the next one."""
    env = dict(os.environ)
    for var in RUNTIME_ENV_VARS:
        env.pop(var, None)
    return env


def installed_windows_exe() -> str:
    """Best-effort path to the installed persona.exe, so the updater can relaunch
    it after a silent install (rather than relying on the installer's own
    relaunch, which came up to a black window under a lowered token). Falls back
    to sys.executable's directory, then the default install path."""
    candidates = []
    try:
        # in a flet build, sys.executable IS persona.exe
        exe = sys.executable
        if exe and exe.lower().endswith("persona.exe"):
            candidates.append(exe)
        if exe:
            candidates.append(os.path.join(os.path.dirname(exe), "persona.exe"))
    except Exception:
        pass
    # per-user install location (current), then the old per-machine one
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "persona", "persona.exe"))
    candidates.append(
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "persona", "persona.exe")
    )
    for c in candidates:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            continue
    return ""
