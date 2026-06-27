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


_SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q={searchTerms}",
    "google": "https://www.google.com/search?q={searchTerms}",
    "brave": "https://search.brave.com/search?q={searchTerms}",
    "startpage": "https://www.startpage.com/sp/search?query={searchTerms}",
}
_SEARCH_NAMES = {
    "duckduckgo": "DuckDuckGo",
    "google": "Google",
    "brave": "Brave Search",
    "startpage": "Startpage",
}


def _search_prefs(engine: str) -> dict:
    """Firefox prefs to steer the address-bar search at the chosen engine.
    Firefox keeps its real default in search.json.mozlz4, but the keyword.URL
    pref still drives non-keyword address-bar queries, which covers the common
    case without rewriting the search store."""
    url = _SEARCH_URLS.get(engine, _SEARCH_URLS["duckduckgo"])
    keyword_url = url.replace("{searchTerms}", "")
    return {
        "keyword.enabled": True,
        "keyword.URL": keyword_url,
        "browser.search.defaultenginename": _SEARCH_NAMES.get(engine, "DuckDuckGo"),
        "browser.urlbar.suggest.searches": True,
    }


def build_title_addon(profile_name: str, base_dir: str) -> str:
    """Generate an unpacked Firefox extension that prefixes every tab/window
    title with the profile name. Loaded natively by Camoufox, so it reaches
    tabs the user opens by hand (a Playwright init script would not). The
    taskbar button shows the window title, so the name has to live in the title.
    """
    import json
    import pathlib

    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"[{profile_name}] "
    content_js = (
        "const PREFIX = " + json.dumps(prefix) + ";\n"
        "function apply(){if(!document.title.startsWith(PREFIX))"
        "document.title=PREFIX+document.title;}\n"
        "apply();\n"
        "const head=document.head||document.documentElement;\n"
        "if(head)new MutationObserver(apply).observe(head,"
        "{subtree:true,childList:true,characterData:true});\n"
    )
    manifest = {
        "manifest_version": 2,
        "name": "persona-title",
        "version": "1.0",
        "applications": {"gecko": {"id": "persona-title@persona"}},
        "content_scripts": [
            {
                "matches": ["<all_urls>"],
                "js": ["title.js"],
                "run_at": "document_start",
                "all_frames": False,
            }
        ],
    }
    (ext_dir / "title.js").write_text(content_js, encoding="utf-8")
    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return str(ext_dir)


def patch_playwright_driver() -> None:
    """Firefox can emit a page error with no source location. Playwright's
    bundled driver reads location.url unconditionally and its event validator
    then demands a string, so an unhandled error on the page (e.g. pixelscan's
    scan) kills the Node driver and strands the browser. Default the location
    fields so the validator is satisfied. Idempotent; safe to call every launch.
    """
    try:
        import playwright

        base = os.path.dirname(playwright.__file__)
        cb = os.path.join(
            base, "driver", "package", "lib", "coreBundle.js"
        )
        if not os.path.exists(cb):
            return
        with open(cb, encoding="utf-8") as f:
            src = f.read()
        patched = src
        patched = patched.replace(
            "url: pageError.location.url,", 'url: pageError.location?.url ?? "",'
        )
        patched = patched.replace(
            "url: pageError.location?.url,", 'url: pageError.location?.url ?? "",'
        )
        patched = patched.replace(
            "line: pageError.location.lineNumber,",
            "line: pageError.location?.lineNumber ?? 0,",
        )
        patched = patched.replace(
            "line: pageError.location?.lineNumber,",
            "line: pageError.location?.lineNumber ?? 0,",
        )
        patched = patched.replace(
            "column: pageError.location.columnNumber",
            "column: pageError.location?.columnNumber ?? 0",
        )
        patched = patched.replace(
            "column: pageError.location?.columnNumber",
            "column: pageError.location?.columnNumber ?? 0",
        )
        # collapse any double-defaulting from re-running over a patched file
        patched = patched.replace('?? "" ?? ""', '?? ""').replace("?? 0 ?? 0", "?? 0")
        if patched != src:
            with open(cb, "w", encoding="utf-8") as f:
                f.write(patched)
    except Exception:
        pass


def ensure_camoufox_installed(progress=None, log=None) -> bool:
    """True if the Camoufox Firefox binary is present; fetch it if not. When
    `progress(done, total)` is given the fetch reports byte progress so the UI
    can show a real download bar; `log(msg)` reports each stage. Returns False
    only if the fetch failed."""
    try:
        from camoufox.pkgman import installed_verstr

        if installed_verstr():
            return True
    except Exception:
        pass
    # not installed -> fetch the FF binary into the user cache (with progress)
    return download_camoufox(progress=progress, log=log)


def installed_version() -> str:
    try:
        from camoufox.pkgman import installed_verstr

        return installed_verstr() or ""
    except Exception:
        return ""


def fetch_latest_version() -> str:
    """The newest Camoufox version available upstream (queries GitHub
    releases). Empty string if the check fails (e.g. Tor unreachable)."""
    try:
        from camoufox.pkgman import CamoufoxFetcher

        f = CamoufoxFetcher()
        f.fetch_latest()
        return f.verstr or ""
    except Exception:
        return ""


def update_available(latest: str = "") -> bool:
    """True if a newer Camoufox is published upstream than the one installed.
    Pass a pre-fetched `latest` to avoid a second network round-trip."""
    have = installed_version()
    if not have:
        return False
    latest = latest or fetch_latest_version()
    return bool(latest) and latest != have


def download_camoufox(progress=None, log=None) -> bool:
    """Download + install the latest Camoufox to the user cache. When `progress`
    is given (progress(done_bytes, total_bytes)), the binary is fetched with a
    visible byte/percent/ETA readout — the same first-run treatment fp-chromium
    gets — instead of Camoufox's silent terminal-only download. `log(msg)`
    reports each stage so a stall has a visible reason. Returns False on
    failure. Resumable across dropped Tor circuits AND across restarts."""

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    if progress is None:
        try:
            from camoufox.pkgman import CamoufoxFetcher, installed_verstr

            CamoufoxFetcher().install()
            return bool(installed_verstr())
        except Exception:
            return False

    import subprocess
    import threading

    # tell the UI we've started before any (slow, over-Tor) network call, so the
    # bar appears immediately instead of after fetch_latest/HEAD round-trips
    progress(0, 0)

    # fetch_latest() + the curl connect can take 30-60s over Tor before a single
    # byte lands. Tick progress(0,0) once a second through that dead time so the
    # UI's "connecting… Ns" keeps moving and never looks frozen. `done_connect`
    # is set once the real watcher takes over, which ends the ticker.
    done_connect = threading.Event()

    def tick() -> None:
        # wait() returns True the instant done_connect is set, else blocks ~1s,
        # so this loops once a second without busy-spinning
        while not done_connect.wait(1.0):
            progress(0, 0)

    threading.Thread(target=tick, daemon=True).start()

    try:
        from camoufox.pkgman import (
            INSTALL_DIR,
            CamoufoxFetcher,
            installed_verstr,
            unzip,
        )

        say("Camoufox: contacting release server over Tor…")
        f = CamoufoxFetcher()
        f.fetch_latest()
        url = f.url
        say("Camoufox: downloading engine…")

        # Download to a DETERMINISTIC path next to the install dir, not a random
        # temp file. curl -C - then resumes a partial download across restarts —
        # so if a slow Tor circuit stalls and the user restarts persona, the
        # next run continues from where it left off instead of starting over
        # (which is why it looked like it "never downloads after a restart").
        class _Tmp:
            name = str(INSTALL_DIR) + ".download.zip"

        tmp = _Tmp()
        os.makedirs(os.path.dirname(tmp.name), exist_ok=True)

        # learn the total in the background so a slow HEAD never blocks the bar
        total_box = {"v": 0}

        def get_total() -> None:
            total_box["v"] = _remote_size(url)

        threading.Thread(target=get_total, daemon=True).start()

        stop = threading.Event()

        def watch() -> None:
            while not stop.is_set():
                try:
                    done = os.path.getsize(tmp.name)
                except OSError:
                    done = 0
                progress(done, total_box["v"])
                stop.wait(0.5)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        done_connect.set()  # watcher now drives progress; stop the connect ticker

        ok = False
        try:
            for _ in range(40):
                rc = subprocess.run(
                    [
                        "curl", "-fsSL", "--connect-timeout", "30",
                        "--speed-limit", "1024", "--speed-time", "30",
                        "-C", "-", "-o", tmp.name, url,
                    ],
                    capture_output=True,
                ).returncode
                have = os.path.getsize(tmp.name) if os.path.exists(tmp.name) else 0
                total = total_box["v"]
                if (total and have >= total) or rc in (33, 36) or (rc == 0 and have > 0):
                    ok = True
                    break
        finally:
            stop.set()

        if not ok:
            say("Camoufox: download didn't complete — will resume on next start.")
            return False

        final_total = total_box["v"] or os.path.getsize(tmp.name)
        progress(final_total, final_total)
        say("Camoufox: extracting…")
        # hand the downloaded zip to Camoufox's own extractor
        import shutil

        if INSTALL_DIR.exists():
            shutil.rmtree(INSTALL_DIR)
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp.name, "rb") as zf:
            unzip(zf, str(INSTALL_DIR), bar=False)
        f.set_version()
        try:
            os.remove(tmp.name)  # resumable zip no longer needed once installed
        except OSError:
            pass
        return bool(installed_verstr())
    except Exception:
        return False
    finally:
        done_connect.set()  # never leave the connect ticker running


def _remote_size(url: str) -> int:
    """Content-Length of the asset (last non-zero past GitHub's 302 redirect)."""
    import subprocess

    try:
        out = subprocess.run(
            ["curl", "-fsSLI", "--connect-timeout", "15", "--max-time", "30", url],
            capture_output=True, timeout=35,
        )
        size = 0
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("content-length:"):
                try:
                    v = int(line.split(":", 1)[1].strip())
                except ValueError:
                    continue
                if v > 0:
                    size = v
        return size
    except Exception:
        return 0


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

    patch_playwright_driver()

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

    profile_name = cfg.get("profile_name", "")
    search_engine = cfg.get("search_engine", "duckduckgo")
    locale = cfg.get("locale", "")
    timezone = cfg.get("timezone", "")

    prefs = _search_prefs(search_engine)
    # let our unpacked title addon load (it's unsigned and out of the normal
    # install scopes); without these Firefox silently ignores it
    prefs["xpinstall.signatures.required"] = False
    prefs["extensions.autoDisableScopes"] = 0
    if timezone:
        # keep JS Date()/Intl timezone consistent with the proxy exit
        prefs["browser.search.region"] = ""  # don't let Mozilla geo-override

    opts: dict = {
        "headless": False,
        "os": cam_os,
        "block_webrtc": True,
        "i_know_what_im_doing": True,
        # the red dot is Camoufox's debug cursor overlay, on by default; it's
        # only useful for watching scripted mouse moves, not manual browsing
        "config": {"showcursor": False},
        "firefox_user_prefs": prefs,
        # Wayland app_id / X11 WM_CLASS so the taskbar shows this persona's name
        # and the fox icon (matched by the .desktop StartupWMClass we wrote).
        "args": [f"--name=persona-{profile_name}"] if profile_name else [],
    }
    if locale:
        opts["locale"] = locale
    if profile_name:
        import tempfile

        addon_dir = os.path.join(
            tempfile.gettempdir(), f"persona-title-{os.getpid()}"
        )
        try:
            opts["addons"] = [build_title_addon(profile_name, addon_dir)]
        except Exception:
            pass
    # Firefox can't do SOCKS5 with username/password, so an authenticated
    # upstream is fronted by a local no-auth bridge (same approach as chromium)
    # and Camoufox is pointed at the bridge. The bridge lives for the life of
    # this forked process and dies with it.
    bridge = None
    proxy_url = cfg.get("proxy_url", "")
    if proxy_url:
        p = urlparse(proxy_url if "://" in proxy_url else "socks5://" + proxy_url)
        if p.username:
            from ..proxy.bridge import ProxyBridge

            bridge = ProxyBridge(proxy_url)
            local_port = bridge.start()
            opts["proxy"] = {"server": f"socks5://127.0.0.1:{local_port}"}
            # geoip can't auto-detect through the local bridge (it sees
            # 127.0.0.1); pass the upstream host if it's a literal IP, else
            # let Camoufox resolve geoip from the exit IP it observes.
            host = p.hostname or ""
            opts["geoip"] = host if host.replace(".", "").isdigit() else True
        else:
            opts["proxy"] = _proxy_dict(proxy_url)
            opts["geoip"] = True

    def close_and_exit() -> None:
        """Report closure and hard-exit. The Camoufox context manager can block
        for a long time trying to shut down an already-dead Firefox, which would
        leave this forked process alive and the profile stuck 'running'. Exiting
        the process directly skips that teardown so the launcher sees it stop."""
        emit("BROWSER_CLOSED")
        try:
            out.flush()
        except Exception:
            pass
        os._exit(0)

    try:
        with Camoufox(**opts) as browser:
            page = browser.new_page()
            try:
                page.goto(cfg.get("start_url", "about:blank"), timeout=60000)
            except Exception:
                pass
            emit("BROWSER_STARTED")
            # Watch the browser connection itself: is_connected() flips to
            # False the instant Firefox dies (crash, last tab closed, killed),
            # where probing contexts/pages would instead raise and could hang.
            while True:
                try:
                    if not browser.is_connected():
                        break
                    if not browser.contexts or not browser.contexts[0].pages:
                        break
                    time.sleep(1)
                except Exception:
                    break
            # exit from inside the context so a blocking __exit__ can't strand us
            close_and_exit()
    except Exception as e:
        emit(f"LAUNCH_FAILED: {e}")
    close_and_exit()


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
