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


def _force_utf8() -> None:
    """Push this process and its descendants toward UTF-8 text.

    On Windows the frozen app inherits the legacy ANSI code page (cp1251 on a
    Russian install) as its locale encoding, so text written without an
    explicit encoding — including the flet launcher's console.log, to which it
    redirects stdout/stderr — comes out as mojibake ("в—Џ", "installingвЂ¦").

    reconfigure() fixes this process's stdio. The env vars CANNOT change this
    already-running interpreter (UTF-8 mode is read only at startup) — they are
    for descendants: any spawned Python child, and the relaunched exe after a
    self-update, whose embedded interpreter reads PYTHONUTF8 from the inherited
    environment before Py_Initialize. A fresh launch from the shell gets UTF-8
    from the exe's activeCodePage manifest, stamped in CI (set_exe_utf8.py).
    No-op where already UTF-8 (Linux/macOS)."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


_REEXEC_UTF8_FLAG = "PERSONA_UTF8_REEXEC"


def _ensure_utf8_fs() -> None:
    """Re-exec the interpreter in UTF-8 mode when the filesystem encoding is
    ASCII, so a profile with a non-ASCII (e.g. Cyrillic) name can be created.

    The filesystem encoding is fixed at interpreter startup from the locale. On
    a Linux desktop launched with a POSIX/C locale (no UTF-8 LANG/LC_ALL — the
    AppImage can inherit a bare environment) it is 'ascii', so os.mkdir of a
    Cyrillic profile dir raises UnicodeEncodeError and profile creation crashes
    (#222). PYTHONUTF8 is read only at startup, so setting it in-process can't
    fix THIS interpreter — re-exec once with it set. Guarded by an env flag so
    the re-exec can't loop, and only where the fs encoding is actually not UTF-8
    (Windows/macOS default to UTF-8; a UTF-8 Linux locale is already fine)."""
    if os.environ.get(_REEXEC_UTF8_FLAG):
        return
    enc = (sys.getfilesystemencoding() or "").lower()
    if enc.startswith("utf"):
        return
    os.environ[_REEXEC_UTF8_FLAG] = "1"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Also give the C library a UTF-8 locale. PYTHONUTF8 alone fixes the fs
    # encoding, but GTK/Flet read the C locale directly; a bare 'C'/POSIX locale
    # (what an AppImage relaunched after a self-update inherits — the update's
    # execv doesn't carry the desktop LANG) leaves them on the fallback 'C'
    # locale. C.UTF-8 is always present on glibc and needs no locale-gen.
    os.environ["LC_ALL"] = "C.UTF-8"
    os.environ.setdefault("LANG", "C.UTF-8")
    # Re-exec THROUGH the AppImage entrypoint when we are one ($APPIMAGE is set by
    # the type-2 runtime). A bare os.execv(sys.executable) re-runs the interpreter
    # WITHOUT the AppRun that sets PYTHONHOME to this build's extracted stdlib, so
    # the second interpreter resolves encodings/ from a stale or system Python and
    # dies with "bad magic number in 'encodings'" — the app relaunches after an
    # update and never opens a window (#240). Going through $APPIMAGE re-enters
    # AppRun, which rebuilds the correct Python environment for this build.
    appimage = os.environ.get("APPIMAGE")
    try:
        if appimage and os.path.isfile(appimage):
            os.execv(appimage, [appimage] + sys.argv[1:])
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except OSError:
        # Can't re-exec (frozen build with no discrete interpreter, or execv
        # denied) — carry on; a UTF-8 profile name may still fail, but the app
        # must not refuse to start.
        os.environ.pop(_REEXEC_UTF8_FLAG, None)


_force_utf8()
_ensure_utf8_fs()

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


from src.core.container import Container
from src.core.logging import get_logger
from src.ui.app import App

logger = get_logger("main")


def _build_api_server(container):
    """Create the (off-by-default) Claude control server. Importing FastAPI +
    uvicorn + httpx costs ~0.7s; deferring it out of module import and into a
    background call AFTER the window is up keeps that cost off the startup path
    — the API is only needed when the user turns Claude control on."""
    from src.api.app import create_app
    from src.api.server import APIServer
    from src.core.config import API_HOST, API_PORT

    server = APIServer(create_app(container))
    logger.info(
        "Claude control server available at http://%s:%s (off until enabled)",
        API_HOST, API_PORT,
    )
    return server


def main() -> None:
    # Self-update verification hook: the updater launches the new AppImage with
    # PERSONA_SELFTEST=1 to confirm it boots before swapping it in. Reaching
    # here means the AppImage mounted and every import above succeeded, so the
    # build is sound — print the token and exit cleanly, WITHOUT starting the
    # GUI or binding the API port (which would need a display / a free port and
    # made the old probe a false-negative that looped the update forever).
    if os.environ.get("PERSONA_SELFTEST") == "1":
        print("SELFTEST_OK", flush=True)
        # In the flet-bundled build, returning from main() leaves the Flutter
        # host process alive, so the probe's subprocess.run would hang for its
        # full timeout. Hard-exit so the probe finishes in ~1-2s.
        os._exit(0)

    # Single-instance guard: two persona windows share one ~/.persona (profiles,
    # settings, engine caches, the MCP port) and race each other into corrupt
    # state, so only one GUI may run. Runs AFTER the selftest gate — the update
    # probe is a short-lived separate process that must never be blocked. A brief
    # retry covers the self-update handoff, where the exiting instance may still
    # hold the lock for a moment as the new one starts.
    import time as _time

    from src.core import single_instance as _si

    _lock = None
    for _try in range(6):
        _lock = _si.acquire()
        if _lock is not None:
            break
        _time.sleep(0.5)
    if _lock is None:
        # Another persona is already running. Don't open a second window.
        print("persona is already running.", flush=True)
        os._exit(0)

    # After the selftest gate: the updater's probe runs the STAGED AppImage,
    # whose $APPIMAGE must not become the launcher's Exec.
    from src.core.desktop_entry import install_desktop_entry

    install_desktop_entry()

    container = Container()

    # Enforce the trash retention window before the UI opens. An entry that has
    # been in the trash longer than the window is gone after the app next starts,
    # without the operator doing anything — the trash is a floor beneath a
    # mis-click, not an archive. Best-effort: a failure here must never stop the
    # app from starting, and the entries simply expire on a later run.
    try:
        purged = container.trash_service.purge_expired()
        if purged:
            logger.info("Purged %d expired trash entry/entries on start", purged)
    except Exception:
        logger.exception("Could not purge expired trash entries on start")

    # Build the API server lazily, in the background, once the UI is up — its
    # FastAPI/uvicorn import is the biggest chunk of pre-window startup and the
    # server is off until the user enables Claude control.
    gui = App(container, api_server_factory=lambda: _build_api_server(container))
    try:
        gui.run()
    finally:
        gui.stop_api_server()


if __name__ == "__main__":
    main()
