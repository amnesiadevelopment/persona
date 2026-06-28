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



from src.api.app import create_app
from src.api.server import APIServer
from src.core.config import API_HOST, API_PORT
from src.core.container import Container
from src.core.logging import get_logger
from src.ui.app import App

logger = get_logger("main")


def main() -> None:
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
