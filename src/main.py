import os
import sys


def _ensure_valid_cwd() -> None:
    """Guarantee the process has a working directory that still exists.

    A self-update re-exec (or an autostart entry) can leave us running with a
    current directory that was unmounted with the previous AppImage. The very
    next os.getcwd() then fails with "Getting current working directory failed"
    — and because os.path.abspath() below calls getcwd(), and Flet's runtime
    calls it at startup, the app dies before any window appears. Move to the
    first directory that actually exists, before anything else runs.
    """
    try:
        if os.getcwd():
            return
    except OSError:
        pass
    for candidate in (os.path.expanduser("~"), os.environ.get("HOME", ""), "/tmp", "/"):
        if not candidate:
            continue
        try:
            os.chdir(candidate)
            return
        except OSError:
            continue


_ensure_valid_cwd()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _point_flet_at_bundled_client() -> None:
    """When running as a PyInstaller binary, use the Flet desktop client that
    was bundled into the executable instead of downloading it at first run.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return
    for client in (
        os.path.join(base, "flet_client", "flet", "flet"),
        os.path.join(base, "flet_client", "flet"),
    ):
        if os.path.exists(client):
            os.environ.setdefault("FLET_VIEW_PATH", os.path.dirname(client))
            return


_point_flet_at_bundled_client()


def _ensure_flet_desktop_mode() -> None:
    """Keep Flet in desktop mode on a Wayland session that has no DISPLAY.

    Flet decides "this is a headless Linux server → run as a web server" purely
    from `DISPLAY` being unset (flet.utils.is_linux_server). On a pure-Wayland
    desktop DISPLAY is often empty while WAYLAND_DISPLAY is set, so Flet wrongly
    switches to the web path and aborts trying to install the absent `flet-web`
    package — the app never opens a window. When we can see we're on a real
    graphical session (Wayland or an X session that just didn't export DISPLAY),
    point DISPLAY at the XWayland default so Flet stays on the desktop path.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("DISPLAY"):
        return
    graphical = (
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE") in ("wayland", "x11")
        or os.environ.get("XDG_RUNTIME_DIR")
    )
    if graphical:
        os.environ["DISPLAY"] = ":0"


_ensure_flet_desktop_mode()


from src.api.app import create_app
from src.api.server import APIServer
from src.core.config import API_HOST, API_PORT
from src.core.container import Container
from src.core.logging import get_logger
from src.ui.app import App

logger = get_logger("main")


def main() -> None:
    # Self-update verification hook: the updater launches the new AppImage with
    # PERSONA_SELFTEST=1 to confirm it boots before swapping it in. Reaching
    # here means the AppImage mounted and every import above succeeded, so the
    # build is sound — print the token and exit cleanly, WITHOUT starting the
    # GUI or binding the API port (which would need a display / a free port and
    # made the old probe a false-negative that looped the update forever).
    if os.environ.get("PERSONA_SELFTEST") == "1":
        print("SELFTEST_OK", flush=True)
        return

    container = Container()

    fastapi_app = create_app(container)
    api_server = APIServer(fastapi_app)
    logger.info("Claude control server available at http://%s:%s (off until enabled)", API_HOST, API_PORT)

    try:
        gui = App(container, api_server=api_server)
        gui.run()
    finally:
        api_server.stop()


if __name__ == "__main__":
    main()
