"""Launch the invisible_playwright (patched Firefox 150) engine for a profile.

A Popen-compatible handle that runs the browser in a forked child (on Linux,
to dodge the flet-AppImage's embedded Python) or a plain subprocess elsewhere,
keeps the window open until the user closes it, and reports readiness on a pipe
— the same shape as the chromium/camoufox launchers so spawn_browser can treat
them alike.
"""

import json
import multiprocessing as mp
import os
import re
import subprocess
import sys

from ...core import platform as _platform


def _invisible_binary_path():
    """Path to the patched Firefox executable, or None if it isn't present yet.
    Reuses invisible_playwright's own version/layout so we agree on where the
    binary lives without re-downloading when it's already there."""
    try:
        import platform as _pyplatform

        from invisible_playwright.constants import (
            BINARY_ENTRY_REL,
            BINARY_VERSION,
        )
        from invisible_playwright.download import cache_dir_for_version

        entry_rel = BINARY_ENTRY_REL.get(sys.platform)
        if entry_rel is None:
            return None
        return cache_dir_for_version(BINARY_VERSION) / entry_rel
    except Exception:
        return None


def is_invisible_installed() -> bool:
    p = _invisible_binary_path()
    return bool(p and p.exists())


def ensure_invisible_installed(progress=None, log=None) -> bool:
    """True if the patched Firefox binary is present; fetch it (resumably, over
    Tor) if not. `progress(done, total)` reports bytes; `log(msg)` reports each
    stage. Returns False only if the fetch failed — the caller can retry later.

    invisible_playwright's own ensure_binary() does a single non-resumable
    request with a 60s timeout, which Tor reliably tears down mid-stream on an
    ~80MB Firefox archive (the same failure mode fingerprint-chromium and
    Camoufox already solved). This fetches with HTTP Range resume + retries so a
    dropped circuit picks up where it left off, then verifies the sha256 and
    extracts via invisible's own helpers."""
    if is_invisible_installed():
        return True
    try:
        return _download_invisible(progress=progress, log=log)
    except Exception as e:
        if log:
            try:
                log(f"Firefox engine: install failed — {type(e).__name__}: {e}")
            except Exception:
                pass
        return False


def _download_invisible(progress=None, log=None) -> bool:
    import platform as _pyplatform
    import tempfile

    from invisible_playwright.constants import ARCHIVE_NAME, BINARY_VERSION
    from invisible_playwright.download import (
        _extract,
        _parse_checksums,
        _resolve_asset_url,
        _sha256_file,
        cache_dir_for_version,
    )

    def say(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass

    asset = ARCHIVE_NAME(sys.platform, _pyplatform.machine())
    version_dir = cache_dir_for_version(BINARY_VERSION)
    version_dir.mkdir(parents=True, exist_ok=True)

    say("Firefox engine: resolving release over Tor…")
    url_archive = _resolve_asset_url(BINARY_VERSION, asset)
    url_sums = _resolve_asset_url(BINARY_VERSION, "checksums.txt")

    # Keep the partial next to the cache dir so a dropped Tor circuit resumes
    # across restarts (same approach as fp-chromium / Camoufox).
    archive_path = version_dir.parent / (asset + ".download")
    say("Firefox engine: downloading…")
    if not _resumable_download(str(url_archive), str(archive_path), progress=progress):
        say("Firefox engine: download didn't complete — will resume next start.")
        return False

    # checksums.txt is tiny; a plain fetch is fine.
    sums_path = version_dir.parent / "checksums.txt"
    if not _resumable_download(str(url_sums), str(sums_path), progress=None):
        say("Firefox engine: couldn't fetch checksums — retrying later.")
        return False
    sums = _parse_checksums(open(sums_path, encoding="utf-8").read())
    expected = sums.get(asset)
    if expected and _sha256_file(archive_path).lower() != expected.lower():
        say("Firefox engine: checksum mismatch — discarding and retrying.")
        try:
            os.remove(archive_path)
        except OSError:
            pass
        return False

    say("Firefox engine: extracting…")
    _extract(archive_path, version_dir)
    try:
        os.remove(archive_path)
    except OSError:
        pass
    return is_invisible_installed()


def _resumable_download(url: str, path: str, progress=None, timeout: int = 120) -> bool:
    """Download `url` to `path`, resuming with an HTTP Range header across
    dropped connections. Returns True only on a complete file."""
    import urllib.request

    attempts = 0
    while attempts < 40:
        attempts += 1
        have = os.path.getsize(path) if os.path.exists(path) else 0
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cr = resp.headers.get("Content-Range")
                if cr and "/" in cr:
                    try:
                        total = int(cr.rsplit("/", 1)[-1])
                    except ValueError:
                        total = 0
                else:
                    cl = int(resp.headers.get("Content-Length") or 0)
                    total = (have + cl) if cl else 0
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                done = have
                with open(path, mode) as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
            if total and os.path.getsize(path) < total:
                continue  # dropped early, resume
            return True
        except Exception:
            continue  # keep the partial for the next resume attempt
    return False


def installed_version() -> str:
    try:
        from invisible_playwright import BINARY_VERSION

        return BINARY_VERSION
    except Exception:
        return ""


def _proxy_dict(proxy_url: str):
    """Turn a 'socks5://user:pass@host:port' url into invisible_playwright's
    proxy dict. invisible_playwright does SOCKS5-with-auth natively, so no
    local bridge is needed (unlike Camoufox)."""
    if not proxy_url:
        return None
    m = re.match(r"socks5://(?:([^:]+):([^@]+)@)?(.+)", proxy_url)
    if not m:
        return {"server": proxy_url}
    user, pw, hostport = m.group(1), m.group(2), m.group(3)
    d = {"server": f"socks5://{hostport}"}
    if user:
        d["username"] = user
        d["password"] = pw
    return d


_SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q=",
    "google": "https://www.google.com/search?q=",
    "brave": "https://search.brave.com/search?q=",
}
_SEARCH_NAMES = {
    "duckduckgo": "DuckDuckGo",
    "google": "Google",
    "brave": "Brave Search",
}


def _profile_prefs(cfg: dict) -> dict:
    """Firefox prefs overlaid (LAST) on invisible_playwright's generated profile.

    invisible forces session restore off; these bring back the behaviour
    persona's users expect — the chosen search engine and restoring the last
    session's tabs — so a profile keeps what the user set across launches.

    The UI theme the user picks by hand persists in the profile's own prefs.js;
    we deliberately don't force a theme pref here, so re-injected user.js can't
    overwrite the user's choice on the next launch.
    """
    engine = cfg.get("search_engine", "duckduckgo")
    return {
        # Address-bar search → chosen engine (keyword.URL covers non-keyword
        # queries without rewriting search.json.mozlz4).
        "keyword.enabled": True,
        "keyword.URL": _SEARCH_URLS.get(engine, _SEARCH_URLS["duckduckgo"]),
        "browser.search.defaultenginename": _SEARCH_NAMES.get(engine, "DuckDuckGo"),
        "browser.urlbar.suggest.searches": True,
        # Restore the previous session's tabs/windows (invisible defaults this
        # to 0 = blank). The persistent profile_dir holds sessionstore.jsonlz4,
        # so page 3 brings the user's tabs back across launches.
        "browser.startup.page": 3,
        "browser.sessionstore.resume_from_crash": True,
        # Playwright drives Firefox's lifecycle, so the periodic sessionstore
        # write (default 15s) is the only thing that captures the open tabs
        # before the process is told to exit. Write more often so a tab opened
        # seconds before close is still in the restored session.
        "browser.sessionstore.interval": 1500,
    }


def _child(cfg: dict, write_fd: int) -> None:
    """Runs in the child: open a single visible Firefox window via
    invisible_playwright and keep it alive until the user closes every window."""
    import time

    out = os.fdopen(write_fd, "w", buffering=1)

    def emit(msg: str) -> None:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass

    def close_and_exit() -> None:
        """Report closure and hard-exit. Tearing the context down can block on
        an already-dead Firefox, which would leave this child alive and the
        profile stuck 'running'. Exiting the process directly skips that
        teardown so the launcher sees the profile stop."""
        emit("BROWSER_CLOSED")
        try:
            out.flush()
        except Exception:
            pass
        os._exit(0)

    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as e:
        emit(f"LAUNCH_FAILED: invisible_playwright import error: {e}")
        return

    proxy = _proxy_dict(cfg.get("proxy_url", ""))
    # Seed the fingerprint deterministically from the profile name, so the same
    # profile keeps a stable identity across launches.
    seed = abs(hash(cfg.get("profile_name", ""))) % (2**31)
    kwargs = {"seed": seed, "headless": False, "extra_prefs": _profile_prefs(cfg)}
    if proxy:
        kwargs["proxy"] = proxy
    # Locale + timezone follow the proxy's geo so they match the exit IP. Empty
    # values leave invisible_playwright's own auto-detection in place.
    locale = cfg.get("locale", "")
    timezone = cfg.get("timezone", "")
    if locale:
        kwargs["locale"] = locale
    if timezone:
        kwargs["timezone"] = timezone
    # Taskbar identity (Linux desktop only): the window's app_id must equal the
    # StartupWMClass persona wrote to the .desktop entry so labwc shows the fox
    # icon + name. Under Wayland, Firefox derives app_id from
    # MOZ_APP_REMOTINGNAME, not --name (which only sets the X11 instance); set
    # the env so the match works on Wayland. --name is kept for the X11 path.
    name = cfg.get("profile_name", "")
    if name and _platform.IS_LINUX:
        kwargs["extra_args"] = [f"--name=persona-{name}"]
        os.environ["MOZ_APP_REMOTINGNAME"] = f"persona-{name}"
    # A persistent profile dir keeps cookies/bookmarks/logins/tabs across launches.
    profile_dir = cfg.get("profile_dir", "")
    if profile_dir:
        kwargs["profile_dir"] = profile_dir

    try:
        with InvisiblePlaywright(**kwargs) as ctx:
            # Prefix every tab/window title with the profile name so the taskbar
            # button identifies which persona owns the window (the WM_CLASS from
            # --name labels the icon group, but labwc shows the window title).
            # An init script runs in every page the context opens, including tabs
            # the user opens by hand — a per-page goto-time inject would miss those.
            if name:
                _prefix = f"[{name}] "
                ctx.add_init_script(
                    "(()=>{const P=" + json.dumps(_prefix) + ";"
                    "const f=()=>{if(document.title&&!document.title.startsWith(P))"
                    "document.title=P+document.title;};f();"
                    "document.addEventListener('DOMContentLoaded',f);"
                    "const h=document.head||document.documentElement;"
                    "if(h)new MutationObserver(f).observe(h,"
                    "{subtree:true,childList:true,characterData:true});})();"
                )
            # With profile_dir, __enter__ yields a persistent BrowserContext that
            # already has one stray about:home page. We can't navigate that page
            # (its browsingContext never binds — "loadURI undefined"), so open a
            # fresh work page and close the stray, leaving exactly ONE window.
            stray = list(ctx.pages)
            page = ctx.new_page()
            try:
                page.goto(cfg.get("start_url", "https://www.google.com"), timeout=60000)
            except Exception:
                pass
            for p in stray:
                try:
                    p.close()
                except Exception:
                    pass
            # BROWSER_STARTED is the readiness marker the launcher's monitor
            # waits for to flip the UI from loading to running (LAUNCH_OK is not
            # one it knows — the profile would hang in the loading state).
            emit("BROWSER_STARTED")
            # Exit when the user closes every window. is_connected() stays True
            # while the Firefox process lives even with zero tabs, so it can't
            # signal "all windows closed"; the page count going to 0 does (proven
            # empirically). A brief grace period covers the moment right after
            # launch before the work page is fully registered.
            grace = time.time() + 5
            while True:
                time.sleep(0.5)
                try:
                    n = len(ctx.pages)
                except Exception:
                    break
                if n == 0 and time.time() > grace:
                    break
            close_and_exit()
    except Exception as e:
        emit(f"LAUNCH_FAILED: {type(e).__name__}: {e}")
    close_and_exit()


def _child_main() -> None:
    """Entry for the non-fork (Win/Mac) subprocess path: read cfg from env and
    run _child writing to stdout (fd 1)."""
    cfg = json.loads(os.environ.get("PERSONA_INVISIBLE_CFG", "{}"))
    _child(cfg, 1)


class InvisibleProcess:
    """Popen-compatible handle around the invisible_playwright child."""

    def __init__(self, cfg: dict) -> None:
        self._fork = _platform.needs_fork_launch()
        if self._fork:
            ctx = mp.get_context("fork")
            r, w = os.pipe()
            self._proc = ctx.Process(target=_child, args=(cfg, w), daemon=False)
            self._proc.start()
            os.close(w)
            self.stdout = os.fdopen(r)
            self.pid = self._proc.pid
        else:
            env = dict(os.environ)
            env["PERSONA_INVISIBLE_CFG"] = json.dumps(cfg)
            self._proc = subprocess.Popen(
                [sys.executable, "-c",
                 "from src.services.browser.invisible_launch import _child_main;"
                 "_child_main()"],
                stdout=subprocess.PIPE, env=env, text=True,
            )
            self.stdout = self._proc.stdout
            self.pid = self._proc.pid
        self.returncode = None

    def poll(self):
        if self._fork:
            if self._proc.is_alive():
                return None
            self.returncode = self._proc.exitcode
            return self.returncode
        return self._proc.poll()

    def wait(self, timeout=None):
        if self._fork:
            self._proc.join(timeout)
            self.returncode = self._proc.exitcode
            return self.returncode
        return self._proc.wait(timeout)

    def terminate(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.terminate()
        else:
            if self._proc.poll() is None:
                self._proc.terminate()

    def kill(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.kill()
        else:
            if self._proc.poll() is None:
                self._proc.kill()


def spawn(cfg: dict) -> InvisibleProcess:
    return InvisibleProcess(cfg)
