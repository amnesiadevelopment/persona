"""Launch a Camoufox (Firefox/Juggler) profile as a child PROCESS without
re-invoking a Python interpreter.

On the production flet-build AppImage, `sys.executable` is the embedded Python
and `subprocess.Popen([sys.executable, "-m", ...])` loads .pyc compiled by a
different interpreter -> "bad magic number in encodings". Instead we fork the
current process (same interpreter, same imports, same env) via multiprocessing,
and expose a tiny Popen-compatible wrapper so the existing launcher (which
expects .stdout/.poll/.terminate/.pid) works unchanged.

The Camoufox Firefox binary (~150MB, lives in the user cache, fetched
separately) is auto-fetched on first use if absent — like the Chromium engine.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
from urllib.parse import urlparse


def _proxy_dict(proxy_url: str) -> dict | None:
    if not proxy_url:
        return None
    p = urlparse(proxy_url if "://" in proxy_url else "socks5://" + proxy_url)
    scheme = p.scheme or "socks5"
    d = {"server": f"{scheme}://{p.hostname}:{p.port}"}
    if p.username:
        d["username"] = p.username
    if p.password:
        d["password"] = p.password
    return d


def ensure_camoufox_installed() -> bool:
    """True if the Camoufox Firefox binary is present; fetch it if not. Returns
    False only if the fetch failed."""
    try:
        from camoufox.pkgman import installed_verstr

        if installed_verstr():
            return True
    except Exception:
        pass
    # not installed -> fetch (downloads the FF binary into the user cache)
    try:
        from camoufox.pkgman import CamoufoxFetcher

        CamoufoxFetcher().install()
        from camoufox.pkgman import installed_verstr

        return bool(installed_verstr())
    except Exception:
        return False


def installed_version() -> str:
    try:
        from camoufox.pkgman import installed_verstr

        return installed_verstr() or ""
    except Exception:
        return ""


def _child(cfg: dict, write_fd: int) -> None:
    """Runs in the forked child: launch Camoufox, signal readiness on the pipe,
    keep the browser open until killed."""
    import time

    out = os.fdopen(write_fd, "w", buffering=1)

    def emit(msg: str) -> None:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass

    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        emit(f"LAUNCH_FAILED: camoufox import error: {e}")
        return

    os_map = {"android": "linux", "ios": "macos"}
    os_type = cfg.get("os_type", "windows")
    cam_os = os_map.get(os_type, os_type)
    if cam_os not in ("windows", "macos", "linux"):
        cam_os = "windows"

    opts: dict = {
        "headless": False,
        "os": cam_os,
        "humanize": True,
        "block_webrtc": True,
        "i_know_what_im_doing": True,
    }
    proxy = _proxy_dict(cfg.get("proxy_url", ""))
    if proxy:
        opts["proxy"] = proxy
        opts["geoip"] = True

    try:
        with Camoufox(**opts) as browser:
            page = browser.new_page()
            try:
                page.goto(cfg.get("start_url", "about:blank"), timeout=60000)
            except Exception:
                pass
            emit("BROWSER_STARTED")
            while True:
                try:
                    if not browser.contexts or not browser.contexts[0].pages:
                        break
                    time.sleep(1)
                except Exception:
                    break
    except Exception as e:
        emit(f"LAUNCH_FAILED: {e}")
        return
    emit("BROWSER_CLOSED")


class CamoufoxProcess:
    """multiprocessing-fork-backed handle with the subset of the Popen API the
    browser launcher uses: .stdout (readable text pipe), .poll(), .terminate(),
    .kill(), .wait(), .pid."""

    def __init__(self, cfg: dict) -> None:
        r, w = os.pipe()
        ctx = mp.get_context("fork")
        self._proc = ctx.Process(target=_child, args=(cfg, w), daemon=False)
        self._proc.start()
        os.close(w)  # parent keeps only the read end
        self.stdout = os.fdopen(r, "r")
        self.pid = self._proc.pid
        self._proxy_bridge = None  # launcher attribute parity

    def poll(self):
        return None if self._proc.is_alive() else (self._proc.exitcode or 0)

    def wait(self, timeout=None):
        self._proc.join(timeout)
        return self._proc.exitcode

    def terminate(self):
        if self._proc.is_alive():
            self._proc.terminate()

    def kill(self):
        if self._proc.is_alive():
            try:
                os.kill(self._proc.pid, signal.SIGKILL)
            except Exception:
                self._proc.terminate()


def spawn(cfg: dict) -> CamoufoxProcess:
    return CamoufoxProcess(cfg)
