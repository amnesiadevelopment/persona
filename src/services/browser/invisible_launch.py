"""Launch the invisible_playwright (patched Firefox 150) engine for a profile.

A Popen-compatible handle that runs the browser in a forked child (on Linux,
to dodge the flet-AppImage's embedded Python) or a plain subprocess elsewhere,
keeps the window open until the user closes it, and reports readiness on a pipe
— the same shape as the chromium launcher so spawn_browser can treat them alike.
"""

import json
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time

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


def _ensure_firefox_policies() -> None:
    """Pin DuckDuckGo as the default search engine for the Firefox engine via an
    Enterprise Policy file next to the binary.

    FF150 ignores browser.search.defaultenginename; it resolves the default from
    search-config-v2, so the only durable way to set it is policies.json with
    SearchEngines.Default. This lives in the install-relative `distribution/`
    dir (shared by all profiles — Firefox has no per-profile default engine), so
    every Firefox profile opens on DuckDuckGo instead of the region default
    (often Google), and the user can't have it silently reset. DuckDuckGo is a
    builtin engine, so this resolves from the local config dump with no network
    fetch at startup."""
    p = _invisible_binary_path()
    if not p:
        return
    try:
        dist = p.parent / "distribution"
        dist.mkdir(parents=True, exist_ok=True)
        policies = dist / "policies.json"
        content = json.dumps(
            {"policies": {"SearchEngines": {"Default": "DuckDuckGo"}}}, indent=2
        )
        # Only rewrite when different so we don't touch the file every launch.
        if not policies.exists() or policies.read_text(encoding="utf-8") != content:
            policies.write_text(content, encoding="utf-8")
    except Exception:
        pass


def ensure_invisible_installed(progress=None, log=None) -> bool:
    """True if the patched Firefox binary is present; fetch it (resumably, over
    Tor) if not. `progress(done, total)` reports bytes; `log(msg)` reports each
    stage. Returns False only if the fetch failed — the caller can retry later.

    invisible_playwright's own ensure_binary() does a single non-resumable
    request with a 60s timeout, which Tor reliably tears down mid-stream on an
    ~80MB Firefox archive (the same failure mode fingerprint-chromium already
    solves). This fetches with HTTP Range resume + retries so a dropped circuit
    picks up where it left off, then verifies the sha256 and extracts via
    invisible's own helpers."""
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


def _extract_as(archive_path, dst, asset_name: str) -> None:
    """Extract `archive_path` into `dst`, choosing the archive format from
    `asset_name`'s extension rather than the file's own name.

    The downloaded file is named "<asset>.download", whose suffix hides the real
    type; passing the asset name (".zip" on Windows, ".tar.gz" on Linux) lets us
    extract the partial in place with no rename — which is what avoids the
    Windows "file in use" lock on os.replace."""
    import os as _os
    import tarfile
    import zipfile

    name = asset_name.lower()
    _os.makedirs(dst, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dst)
    elif name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dst)
    else:
        raise RuntimeError(f"unknown archive format for asset: {asset_name}")


def _download_invisible(progress=None, log=None) -> bool:
    import platform as _pyplatform
    import tempfile

    from invisible_playwright.constants import ARCHIVE_NAME, BINARY_VERSION
    from invisible_playwright.download import (
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
    # across restarts (same approach as fp-chromium).
    archive_path = version_dir.parent / (asset + ".download")

    # checksums.txt is tiny; a plain fetch is fine.
    sums_path = version_dir.parent / "checksums.txt"
    if not _resumable_download(str(url_sums), str(sums_path), progress=None):
        say("Firefox engine: couldn't fetch checksums — retrying later.")
        return False
    sums = _parse_checksums(open(sums_path, encoding="utf-8").read())
    expected = sums.get(asset)

    # Download, then verify the sha256. Over Tor the long transfer can flip a
    # byte (a circuit swaps mid-stream) and corrupt the archive; a single bad
    # byte fails the checksum. Don't give up on the whole 118MB for that — retry
    # the download a few times, starting each retry from a CLEAN file so a bad
    # resume can't keep a corrupt tail around.
    for verify_attempt in range(3):
        say("Firefox engine: downloading…")
        if not _resumable_download(
            str(url_archive), str(archive_path), progress=progress
        ):
            say("Firefox engine: download didn't complete — will resume next start.")
            return False
        if not expected:
            break  # no checksum to verify against
        if _sha256_file(archive_path).lower() == expected.lower():
            break  # verified
        say("Firefox engine: checksum mismatch — re-downloading from scratch.")
        try:
            os.remove(archive_path)  # next attempt restarts clean, no bad resume
        except OSError:
            pass
    else:
        say("Firefox engine: couldn't get a clean download — will retry next start.")
        return False

    # Extract straight from the downloaded partial, choosing the archive type
    # from the ASSET name (not the file's ".download" suffix). Renaming the
    # partial onto the real ".zip"/".tar.gz" name first is what caused the
    # Windows failures: os.replace raised WinError 32 ("file in use") because
    # Defender scans a freshly written file and briefly locks it, so the whole
    # install aborted and retried. Extracting by known type needs no rename, so
    # there's no window for that lock to bite.
    say("Firefox engine: extracting…")
    _extract_as(archive_path, version_dir, asset)
    try:
        os.remove(archive_path)
    except OSError:
        pass
    return is_invisible_installed()


class _KeepRangeRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    """Re-attach the Range header after a redirect.

    GitHub release downloads 302 to a signed CDN URL, and urllib's default
    redirect handler builds the follow-up request WITHOUT the original headers —
    so the Range header is lost and the CDN returns the whole file (200) instead
    of the requested tail (206). That silently restarts the download from zero on
    every resume, which over Tor never finishes. Carry Range across the redirect
    so resume actually works."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            rng = req.headers.get("Range")
            if rng:
                new.add_header("Range", rng)
        return new


def _resumable_download(
    url: str,
    path: str,
    progress=None,
    timeout: int = 30,
    stall_timeout: int = 25,
) -> bool:
    """Download `url` to `path`, resuming with an HTTP Range header across
    dropped connections. Returns True only on a complete file.

    Over Tor a circuit can connect and then go silent — the socket stays open
    but no bytes arrive, so a plain socket timeout never fires and the download
    hangs on "connecting" forever. A stall watchdog closes the response if no
    byte arrives within `stall_timeout`, which raises in read() and drops us to
    the next attempt with a fresh circuit; the partial on disk lets us resume."""
    import threading
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(_KeepRangeRedirect)

    attempts = 0
    while attempts < 80:
        attempts += 1
        have = os.path.getsize(path) if os.path.exists(path) else 0
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        resp = None
        try:
            try:
                resp = opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as he:
                # 416 = the Range is past the end → the file is already complete
                # on disk (a finished partial from a prior run). Treat as done.
                if he.code == 416 and have:
                    return True
                raise
            cr = resp.headers.get("Content-Range")  # "bytes START-END/TOTAL"
            range_start = None
            total = 0
            if cr and "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[-1])
                    range_start = int(cr.split()[1].split("-")[0])
                except (ValueError, IndexError):
                    range_start = None
            else:
                cl = int(resp.headers.get("Content-Length") or 0)
                total = (have + cl) if (have and resp.status == 206 and cl) else cl

            # Append ONLY when the server confirms a 206 starting exactly where
            # our file ends. Otherwise (200, or a range starting somewhere else)
            # we'd duplicate bytes and bloat the file past its real size — so
            # restart from scratch by truncating. This is the bug that grew the
            # archive to ~200MB instead of 118MB.
            if have and resp.status == 206 and range_start == have:
                seek_to = have
            else:
                seek_to = 0
            done = seek_to

            # Stall watchdog: if no chunk arrives within stall_timeout, close
            # the response so the blocked read() raises and we retry with a new
            # circuit. Reset the timer on every received chunk.
            last_progress = [time.monotonic()]
            stop_watch = threading.Event()

            def _watch():
                while not stop_watch.wait(1.0):
                    if time.monotonic() - last_progress[0] > stall_timeout:
                        try:
                            resp.close()
                        except Exception:
                            pass
                        return

            watcher = threading.Thread(target=_watch, daemon=True)
            watcher.start()
            try:
                # r+b so we can seek to the resume point without truncating a
                # valid prefix; create the file if it's missing.
                if not os.path.exists(path):
                    open(path, "wb").close()
                with open(path, "r+b") as out:
                    out.seek(seek_to)
                    if seek_to == 0:
                        out.truncate(0)
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        # Never write past the known total — a stray duplicated
                        # tail would otherwise grow the file.
                        if total and done + len(chunk) > total:
                            chunk = chunk[: total - done]
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        last_progress[0] = time.monotonic()
                        if progress is not None:
                            progress(done, total)
                    out.flush()
            finally:
                stop_watch.set()

            size = os.path.getsize(path)
            if total and size < total:
                continue  # dropped early, resume with a fresh circuit
            if total and size > total:
                # Safety net: trim any overshoot back to the real size.
                with open(path, "r+b") as out:
                    out.truncate(total)
            return True
        except Exception:
            continue  # keep the partial for the next resume attempt
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
    return False


def installed_version() -> str:
    """The underlying Firefox version (e.g. "150.0.1") for display. The engine's
    own BINARY_VERSION ("firefox-13") is an internal build tag, not a Firefox
    version, so show the real upstream version the user recognises."""
    try:
        from invisible_playwright.constants import FIREFOX_UPSTREAM_VERSION

        return FIREFOX_UPSTREAM_VERSION
    except Exception:
        pass
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


def _system_dpr() -> float:
    """The host display's scale factor (1.0 at 100%, 1.5 at 150%, 2.0 at 200%),
    used to convert the physical work-area size to Firefox CSS px for the
    initial-window-size seed. Non-Windows desktops fall back to 1.0. Clamped to
    a sane desktop range so a weird reading can't produce an unusable window."""
    if not _platform.IS_WINDOWS:
        return 1.0
    try:
        import ctypes

        # Per-monitor DPI awareness so GetDpiForSystem returns the real scale.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForSystem()
        scale = dpi / 96.0 if dpi else 1.0
        return max(1.0, min(3.0, round(scale, 2)))
    except Exception:
        return 1.0


def _context_overrides_for(w: int, h: int) -> dict:
    """Playwright context kwargs that decouple the spoofed screen (the
    fingerprint) from the physical window — the chromium model.

    The `screen` reports the CHOSEN resolution the user picked (the anti-detect
    value — this already works: pin -> zoom.stealth.screen.* -> JS screen.*).
    `no_viewport` stops Playwright from fixing the content size, so the page
    follows the NATIVE OS window: freely draggable/resizable, maximizes with no
    skew, exactly like a normal browser. Any fixed viewport couples the window
    to a chosen size — viewport = work_area opened the window across the whole
    4K monitor and rendered the page misscaled."""
    return {
        "screen": {"width": w, "height": h},
        "no_viewport": True,
    }


def _with_context_overrides(InvisiblePlaywright, overrides: dict):
    """A subclass whose context kwargs overlay `overrides` on the engine's own.

    The overlay rides on the launch's OWN class, never on InvisiblePlaywright
    itself: on the Windows/macOS thread path several launches share one
    process, so patching the engine class races — the last patch wins for any
    launch still inside __enter__, handing a profile the WRONG viewport/screen,
    and the patch would stay on for launches that chose no resolution at all.
    A per-launch subclass gives each launch its own overlay with nothing shared
    to race on or restore."""
    ov = dict(overrides)

    class _WithOverrides(InvisiblePlaywright):
        def _default_context_kwargs(self):
            kw = super()._default_context_kwargs()
            kw.update(ov)
            # The engine's own kwargs carry a fixed viewport and a
            # device_scale_factor; a viewport defeats no_viewport, and
            # Playwright rejects device_scale_factor with a null viewport.
            kw.pop("viewport", None)
            kw.pop("device_scale_factor", None)
            return kw

    return _WithOverrides


def _outer_size_override_script() -> str:
    """JS that pins window.outerWidth/outerHeight to the real window (inner +
    chrome), not the spoofed screen.

    With the screen spoofed to a big resolution but the window physically small,
    Firefox reports outerWidth == the spoofed screen width — larger than
    innerWidth on a small window, an inner<outer==screen mismatch no real
    un-maximized window shows. Deriving outer from the live inner size keeps
    outer ≈ inner + chrome (both below screen), which is what a normal window
    looks like. Chrome offsets match the engine's own (_CHROME_W/_CHROME_H)."""
    return (
        "(() => {"
        "const def=(o,k,v)=>{try{Object.defineProperty(o,k,"
        "{get:()=>v,configurable:true})}catch(e){}};"
        "def(window,'outerWidth', window.innerWidth + 14);"
        "def(window,'outerHeight', window.innerHeight + 91);"
        "})();"
    )


def _work_area() -> tuple[int, int]:
    """The usable desktop size in PHYSICAL pixels (excludes the taskbar).
    SPI_GETWORKAREA returns physical pixels on a DPI-aware process; divide by
    _system_dpr() for CSS px. Non-Windows / failure returns (0, 0) so callers
    skip work-area-based sizing."""
    if not _platform.IS_WINDOWS:
        return (0, 0)
    try:
        import ctypes
        from ctypes import wintypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        SPI_GETWORKAREA = 0x0030
        r = RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(r), 0
        ):
            return (0, 0)
        return (r.right - r.left, r.bottom - r.top)
    except Exception:
        return (0, 0)


def _seed_window_size(profile_dir: str) -> None:
    """Seed the profile's INITIAL Firefox window size to half the work area
    (CSS px) so a fresh profile opens a normal mid-size window — the same feel
    as chromium's default. Only when xulstore.json is absent: Firefox persists
    the user's own window size there, and a manual resize must survive
    relaunches."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "xulstore.json")
    if os.path.exists(path):
        return
    aw, ah = _work_area()
    if not (aw and ah):
        return
    dpr = _system_dpr()
    w, h = int(aw / dpr) // 2, int(ah / dpr) // 2
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "chrome://browser/content/browser.xhtml": {
                        "main-window": {
                            "width": str(w),
                            "height": str(h),
                            "sizemode": "normal",
                        }
                    }
                },
                f,
            )
    except OSError:
        pass


def _screen_metrics() -> str:
    """A compact string of the host display metrics for debugging HiDPI sizing:
    physical pixel size, virtual (scaled) size, and the work area. Windows only;
    empty elsewhere."""
    if not _platform.IS_WINDOWS:
        return "n/a"
    try:
        import ctypes

        u = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        vx = u.GetSystemMetrics(SM_CXSCREEN)
        vy = u.GetSystemMetrics(SM_CYSCREEN)
        # physical resolution via EnumDisplaySettings (current mode)
        return f"virt={vx}x{vy}"
    except Exception:
        return "err"


from .window_entry import app_id_for as _remoting_name


_SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q=",
    "google": "https://www.google.com/search?q=",
    "brave": "https://search.brave.com/search?q=",
}

# Remote Settings / Normandy / Pocket / telemetry all do a network fetch during
# Firefox startup. Over Tor those fetches are slow, and two profiles starting at
# once make one of them hang the full launch timeout on the changeset poll. The
# data: URL makes Remote Settings' shouldSkipRemoteActivity short-circuit BEFORE
# any request (a valid URL, so no invalid-URL hang — an empty string breaks URL
# parsing and hangs instead). The rest kill the remaining startup requests so a
# launch never blocks on the network. NEVER blank a *.server pref to disable a
# feature — use its enabled flag; a blank server URL is an invalid URL and hangs.
_NO_STARTUP_FETCH = {
    "services.settings.server": "data:,#remote-settings-dummy/v1",
    "services.settings.poll_interval": 0,
    "services.settings.load_dumps": True,
    "extensions.pocket.enabled": False,
    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
    "browser.newtabpage.activity-stream.feeds.system.topstories": False,
    "browser.newtabpage.activity-stream.showSponsored": False,
    "browser.newtabpage.activity-stream.showSponsoredTopSites": False,
    "app.normandy.enabled": False,
    "app.normandy.first_run": False,
    "app.shield.optoutstudies.enabled": False,
    "messaging-system.rsexperimentloader.enabled": False,
    "browser.discovery.enabled": False,
    "extensions.blocklist.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "toolkit.telemetry.unified": False,
    "toolkit.telemetry.archive.enabled": False,
    "browser.search.update": False,
    "browser.region.update.enabled": False,
    "network.captive-portal-service.enabled": False,
    "network.connectivity-service.enabled": False,
    "extensions.getAddons.cache.enabled": False,
    "browser.safebrowsing.downloads.remote.enabled": False,
}


def _bookmarks_sig(bookmarks: list) -> str:
    import hashlib

    return hashlib.md5(
        json.dumps(bookmarks, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _init_places_db(
    profile_dir: str, seed: int, timeout: float = 90.0, close_grace: float = 15.0
) -> bool:
    """Launch the engine once headless so it creates a valid places.sqlite.

    Firefox rejects a hand-built places database ("files in use"), so the only
    way to get a database we can seed is to let the engine create one. `seed`
    is the profile's stable fingerprint seed, the same one the real launch
    uses. Waits for Places to write the bookmark roots (the toolbar row the
    seeder needs) — the file alone lands on disk long before the roots, and
    seeding a rootless database silently inserts nothing. Playwright's sync
    API is thread-affine, so the whole init (enter → wait for roots → polite
    exit) runs on one worker thread, bounded: `timeout` covers reaching the
    roots, and the polite close gets only `close_grace` on top (Firefox's
    shutdown blockers hang it for ~90s at times; the settle below kills the
    leftovers anyway). Returns True once the roots exist.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception:
        return False
    import threading

    from .firefox_bookmarks import places_ready

    places = os.path.join(profile_dir, "places.sqlite")
    deadline = time.monotonic() + timeout
    ready = threading.Event()

    def init() -> None:
        try:
            with InvisiblePlaywright(
                seed=seed,
                headless=True,
                profile_dir=profile_dir,
                # Skip Firefox's startup remote-settings sync — over Tor that
                # fetch stalls this throwaway run for minutes.
                extra_prefs=dict(_NO_STARTUP_FETCH),
            ):
                while time.monotonic() < deadline and not places_ready(places):
                    time.sleep(0.5)
                ready.set()
        except Exception:
            pass
        finally:
            ready.set()

    t = threading.Thread(target=init, daemon=True)
    t.start()
    ready.wait(timeout)
    t.join(close_grace)
    # The engine's __exit__ is a polite Playwright teardown the multi-process
    # Firefox routinely survives; a REAL launch over a still-dying instance
    # can't come up cleanly. Don't proceed until this profile's Firefox is
    # confirmed gone.
    _wait_profile_released(profile_dir)
    # Clear the lock the headless run leaves so the real launch isn't blocked.
    for fname in ("lock", ".parentlock"):
        try:
            os.remove(os.path.join(profile_dir, fname))
        except OSError:
            pass
    # Drop this run's saved session. The real launch restores the previous
    # session (browser.startup.page=3) — restoring the throwaway headless
    # window replaces the initial window juggler attaches to (half-destroyed
    # webProgress, endless SimpleChannel churn) and the launch wedges before
    # BROWSER_STARTED. Live-proven: process settling alone did NOT unwedge it,
    # wiping the session did.
    import shutil

    for name in (
        "sessionstore.jsonlz4",
        "sessionCheckpoints.json",
        "sessionstore-backups",
    ):
        path = os.path.join(profile_dir, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    return places_ready(places)


def _profile_released(profile_dir: str) -> bool:
    """Whether no Firefox of THIS profile is running. Prefers the WMI pid scan
    (a confident empty set); on no-verdict falls back to the pgrep/single-pid
    probe — treating its None as released, since blocking every launch on a
    broken scan is worse than a best-effort relaunch."""
    pids = _profile_firefox_pids(profile_dir)
    if pids is not None:
        return not pids
    return _firefox_pid(profile_dir) is None


def _wait_profile_released(
    profile_dir: str, grace: float = 5.0, timeout: float = 15.0
) -> bool:
    """Block until this profile's Firefox has fully released the profile.

    The polite teardown gets `grace` seconds to exit on its own; survivors are
    then force-killed and polled until confirmed gone (or `timeout` passes).
    Returns True when the profile is confirmed released."""
    deadline = time.monotonic() + grace
    while True:
        if _profile_released(profile_dir):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    _kill_profile_firefox(profile_dir)
    deadline = time.monotonic() + timeout
    while True:
        if _profile_released(profile_dir):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _seed_firefox_bookmarks(profile_dir: str, bookmarks: list, seed: int) -> None:
    """Put the profile's bookmarks on the Firefox toolbar via places.sqlite.

    The engine must have created places.sqlite first (Firefox rejects a
    hand-built one); if it hasn't, do a one-time headless init to create it.
    Seed only once per bookmark set (a marker file) so a set the user has
    since edited by hand isn't re-clobbered on every launch."""
    if not profile_dir or not bookmarks:
        return
    try:
        from ...models.bookmark import Bookmark
        from .firefox_bookmarks import places_ready, seed_places_bookmarks

        os.makedirs(profile_dir, exist_ok=True)
        sig = _bookmarks_sig(bookmarks)
        marker = os.path.join(profile_dir, ".persona-bookmarks-sig")
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as f:
                if f.read().strip() == sig:
                    return  # this exact set was already seeded

        places = os.path.join(profile_dir, "places.sqlite")
        if not places_ready(places) and not _init_places_db(profile_dir, seed):
            return

        marks = [Bookmark(b.get("name", ""), b.get("url", "")) for b in bookmarks]
        if seed_places_bookmarks(places, marks):
            with open(marker, "w", encoding="utf-8") as f:
                f.write(sig)
    except Exception:
        pass


def _profile_prefs(cfg: dict) -> dict:
    """Firefox prefs overlaid (LAST) on invisible_playwright's generated profile.

    Brings back the behaviour persona's users expect on top of invisible's
    stealth profile: a dark UI, the chosen start page, restored tabs, a visible
    bookmarks toolbar — and disables every startup network fetch so a launch
    never blocks on Tor (the multi-profile launch hang).
    """
    prefs = dict(_NO_STARTUP_FETCH)
    prefs.update(
        {
            # Force the dark UI regardless of the seed. invisible derives the
            # theme from the fingerprint seed, so without this a profile's theme
            # is random; persona's users expect dark.
            "ui.systemUsesDarkTheme": 1,
            # Restore the previous session's tabs/windows across launches. The
            # persistent profile_dir holds sessionstore, so page 3 brings the
            # user's tabs back. Write the store often so a tab opened seconds
            # before close is still in the restored session.
            "browser.startup.page": 3,
            "browser.sessionstore.resume_from_crash": True,
            "browser.sessionstore.interval": 1500,
            # Always show the bookmarks toolbar so the shipped test bookmarks
            # are visible (default only shows it on the new-tab page).
            "browser.toolbars.bookmarks.visibility": "always",
            # Close the window immediately when the user hits the X — no
            # "close N tabs?" confirmation. The confirmation would leave the
            # window (and the profile's "running" state) up until dismissed.
            "browser.tabs.warnOnClose": False,
            "browser.warnOnQuit": False,
            "browser.sessionstore.warnOnQuit": False,
        }
    )
    # The chosen search engine drives the start page so the window opens on the
    # engine the user picked instead of about:home. (Firefox 150 has no
    # per-profile way to set the URL-bar default engine without a network search
    # config, so the address bar keeps its built-in default; the start page is
    # what the user sees open.)
    engine = cfg.get("search_engine", "duckduckgo")
    start = _SEARCH_URLS.get(engine, _SEARCH_URLS["duckduckgo"]).split("?", 1)[0]
    prefs["browser.startup.homepage"] = start
    return prefs


def _enter_with_timeout(InvisiblePlaywright, kwargs, profile_dir, attempts, per_try,
                        inline=False):
    """Enter an InvisiblePlaywright context, bounding each attempt to `per_try`
    seconds and retrying. Returns (inv, ctx) on success, or (None, None) if every
    attempt timed out.

    __enter__ is a blocking call, so it runs in a thread; when the attempt
    overruns we kill the launching Firefox so the blocked call raises and the
    thread unwinds, then try again with a clean profile lock.

    `inline=True` enters on the CALLING thread instead (no watchdog). Playwright's
    sync API is thread-affine — the object must be used on the thread that made
    it — so the Windows/macOS thread path enters inline, keeping ctx usable for
    the close-watch. The launch is against a local engine there (not Tor), so the
    startup-fetch stall the watchdog guards against doesn't apply."""
    import threading

    if inline:
        try:
            inv = InvisiblePlaywright(**kwargs)
            ctx = inv.__enter__()
            return inv, ctx
        except BaseException:  # noqa: BLE001
            return None, None

    for _ in range(attempts):
        holder = {}

        def attempt():
            try:
                inv = InvisiblePlaywright(**kwargs)
                holder["inv"] = inv
                holder["ctx"] = inv.__enter__()
            except BaseException as e:  # noqa: BLE001 — record, retry decides
                holder["err"] = e

        t = threading.Thread(target=attempt, daemon=True)
        t.start()
        t.join(per_try)
        if "ctx" in holder:
            return holder["inv"], holder["ctx"]
        # Timed out or failed — kill the launching Firefox so the thread unwinds,
        # clear the stale lock, and retry.
        pid = _firefox_pid(profile_dir)
        if pid:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        t.join(5)
        for fname in ("lock", ".parentlock"):
            try:
                os.remove(os.path.join(profile_dir, fname))
            except OSError:
                pass
        try:
            inv = holder.get("inv")
            if inv is not None:
                inv.__exit__(None, None, None)
        except Exception:
            pass
    return None, None


def _resolve_seed(cfg: dict) -> int:
    """The profile's fingerprint seed. persona passes the profile's stable
    crc32 seed in cfg — hash(str) is salted per-process, so deriving the seed
    here gave a DIFFERENT fingerprint every app restart. The hash fallback only
    covers a cfg that predates the seed field."""
    seed = cfg.get("seed")
    if seed is not None:
        return int(seed)
    return abs(hash(cfg.get("profile_name", ""))) % (2**31)


def _child(cfg: dict, write_fd: int, stop_event=None) -> None:
    """Open a single visible Firefox window via invisible_playwright and keep it
    alive until the user closes the window or the parent asks to stop.

    Runs as a forked PROCESS on Linux and as a THREAD on Windows/macOS (where
    re-exec via sys.executable can't work: sys.executable is the flet launcher,
    not a python interpreter). When `stop_event` is given we're in a thread:
    don't install a SIGTERM handler (only valid on the main thread) and never
    os._exit (that would kill the whole app) — return instead and honour the
    event for STOP.

    Readiness and closure are reported on the pipe (BROWSER_STARTED /
    BROWSER_CLOSED) so the launcher can treat this like the chromium Popen.
    """
    import threading
    import time

    in_thread = stop_event is not None

    out = os.fdopen(write_fd, "w", buffering=1)

    def emit(msg: str) -> None:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass

    def _finish() -> None:
        """End the child: a forked process must os._exit so it doesn't return
        into the parent's code; a thread must close the pipe's write end so the
        parent's reader sees EOF (readline would otherwise block until the fd
        happens to be garbage-collected), then just return."""
        try:
            out.flush()
        except Exception:
            pass
        if not in_thread:
            os._exit(0)
        try:
            out.close()
        except Exception:
            pass

    profile_dir = cfg.get("profile_dir", "")

    # A killed Firefox leaves lock/.parentlock in the profile; a stale lock makes
    # the next launch think the profile is already running. persona only spawns
    # this child when it knows the profile isn't running, so any lock here is
    # stale — clear it before launching.
    for fname in ("lock", ".parentlock"):
        try:
            os.remove(os.path.join(profile_dir, fname))
        except OSError:
            pass

    # Deterministic per-profile seed so the same profile keeps a stable
    # fingerprint across launches AND app restarts.
    seed = _resolve_seed(cfg)

    # Seed the profile's bookmarks into places.sqlite before the engine opens
    # the visible window, so the first real window already shows them. The
    # first time a profile with bookmarks is opened this does a one-time
    # HEADLESS engine init (a real Firefox start, tens of seconds) — it must
    # run here in the child, never on the thread constructing the handle,
    # which on a UI launch is the Flet session thread and would freeze the app.
    _seed_firefox_bookmarks(profile_dir, cfg.get("bookmarks", []), seed)
    # Pin DuckDuckGo as the default search engine for every Firefox profile.
    _ensure_firefox_policies()

    # A DBus-valid, per-profile-unique remoting name so multiple profiles open
    # at once (see _remoting_name). It doubles as the Wayland app_id for the
    # taskbar icon. Set in this child's own environment — forks have separate
    # memory, so this doesn't race with other profiles' children.
    name = cfg.get("profile_name", "")
    if name and _platform.IS_LINUX:
        os.environ["MOZ_APP_REMOTINGNAME"] = _remoting_name(name)

    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as e:
        emit(f"LAUNCH_FAILED: invisible_playwright import error: {e}")
        emit("BROWSER_CLOSED")
        _finish()  # close the pipe so the monitor's reader unblocks (no fd leak)
        return

    # When persona already knows the timezone (it always passes a concrete one),
    # short-circuit invisible's egress-IP lookup. invisible runs that lookup over
    # the proxy to drive a WebRTC srflx override, but on Tor WebRTC carries no
    # real candidates anyway, and the lookup adds ~15s of round-trips through
    # Tor+proxy to every launch.
    if cfg.get("timezone"):
        try:
            from invisible_playwright import _geo as _ipgeo
            from invisible_playwright import launcher as _iplauncher

            def _geo_no_egress(timezone, proxy, _orig=_ipgeo.prepare_session_geo):
                tz = (timezone or "").strip()
                if tz and tz.lower() != "auto":
                    return _ipgeo.SessionGeo(tz, None)
                return _orig(timezone, proxy)

            _iplauncher.prepare_session_geo = _geo_no_egress
        except Exception:
            pass

    proxy = _proxy_dict(cfg.get("proxy_url", ""))
    kwargs = {"seed": seed, "headless": False, "extra_prefs": _profile_prefs(cfg)}
    # Decouple the spoofed screen (the chosen resolution the fingerprint
    # reports) from the physical window (native, user-sized — the chromium
    # model). Overlaid on the context kwargs below, AFTER the engine's own, so
    # a 4K pick reports 4K in JS while the window behaves like a normal
    # browser. None when the profile uses Auto (engine's own screen/sizing).
    _res_overrides = None
    res = cfg.get("resolution")
    if res:
        w, h = int(res[0]), int(res[1])
        _res_overrides = _context_overrides_for(w, h)
        # With no_viewport Firefox owns the window size; seed a fresh profile's
        # first window so it doesn't open at Firefox's own default.
        _seed_window_size(profile_dir)
        emit(f"HIDPI_DEBUG chosen={w}x{h} window=native")
        # Pin the fingerprint screen to the CHOSEN resolution with DPR 1 so
        # screen.width * devicePixelRatio == screen.width (a spoofed screen at
        # the host's real 1.5 DPR would report an impossible size — the same
        # tell fixed in the chromium device ext).
        #
        # Firefox has NO --width/--height CLI flags (those are chromium's); a
        # patched-Firefox launch that received them treated the leftover as a URL
        # and opened a bogus "0.0.9.51" page. Size comes ONLY from the profile's
        # xulstore.json (seeded above), never from extra_args.
        kwargs["pin"] = {
            "screen.width": w,
            "screen.height": h,
            "screen.avail_width": w,
            "screen.avail_height": h - 40,
            "screen.dpr": 1,
        }
        # The pinned screen.dpr feeds BOTH the JS spoof (zoom.stealth.screen.dpr)
        # and the render scale (layout.css.devPixelsPerPx) — invisible_core.prefs
        # derives the two from one profile.screen.dpr — so at 1 the window drew
        # 1:1 physical px, ignoring the OS scale (content ~1/3 too small on a
        # 150% 4K display). extra_prefs overlays LAST in the engine, so re-point
        # the RENDER pref alone at the real OS scale: the window draws like any
        # native browser while JS keeps reporting the chosen resolution at dpr 1
        # (the chromium model). Set explicitly rather than deleted — a "1" from
        # an earlier run persists in the profile's prefs.js and would win if
        # user.js merely dropped the key.
        kwargs["extra_prefs"]["layout.css.devPixelsPerPx"] = str(_system_dpr())
    if proxy:
        kwargs["proxy"] = proxy
    locale = cfg.get("locale", "")
    timezone = cfg.get("timezone", "")
    if locale:
        kwargs["locale"] = locale
    if timezone:
        kwargs["timezone"] = timezone
    extra_args: list = []
    if name and _platform.IS_LINUX:
        # --name sets the X11 instance (the WM_CLASS labwc matches for the icon);
        # MOZ_APP_REMOTINGNAME (set above) is the Wayland app_id. Keep both so
        # the taskbar icon matches the .desktop StartupWMClass on either backend.
        extra_args.append(f"--name={_remoting_name(name)}")
    if extra_args:
        kwargs["extra_args"] = extra_args
    if profile_dir:
        kwargs["profile_dir"] = profile_dir

    # Decouple window size from the spoofed screen. The engine builds both the
    # context `viewport` (window) and `screen` (fingerprint) from one p.screen
    # value; overlay our own so the window stays native while the screen
    # reports the chosen resolution. The overlay is a per-launch subclass (see
    # _with_context_overrides — patching the engine class races on the thread
    # path), and only when a resolution was chosen (Auto keeps the engine's own
    # sizing).
    if _res_overrides is not None:
        InvisiblePlaywright = _with_context_overrides(
            InvisiblePlaywright, _res_overrides
        )

    # Launch with a bounded timeout and one retry. Over Tor, a launch
    # occasionally stalls on Firefox's startup remote-settings fetch and would
    # otherwise hang the full 180s Playwright timeout — unacceptably long for the
    # user. Cap each attempt; if it overruns, tear it down and try once more (a
    # fresh attempt almost always comes up fast). InvisiblePlaywright's
    # __enter__ is blocking, so run it in a thread and watchdog it: killing the
    # Firefox process makes the blocked __enter__ raise so the thread unwinds.
    inv, ctx = _enter_with_timeout(
        InvisiblePlaywright, kwargs, profile_dir,
        attempts=3, per_try=25, inline=in_thread,
    )
    if ctx is None:
        emit("LAUNCH_FAILED: launch timed out")
        emit("BROWSER_CLOSED")
        _finish()
        return

    # Prefix every tab/window title with the profile name so the taskbar button
    # identifies which persona owns the window. An init script runs in every page
    # the context opens, including tabs the user opens by hand. The prefix also
    # lets the close-watch below count THIS profile's windows by title.
    _prefix = f"[{name}] " if name else None
    if name:
        try:
            ctx.add_init_script(
                "(()=>{const P=" + json.dumps(_prefix) + ";"
                "const f=()=>{if(document.title&&!document.title.startsWith(P))"
                "document.title=P+document.title;};f();"
                "document.addEventListener('DOMContentLoaded',f);"
                "const h=document.head||document.documentElement;"
                "if(h)new MutationObserver(f).observe(h,"
                "{subtree:true,childList:true,characterData:true});})();"
            )
        except Exception:
            pass

    # Keep outerWidth/outerHeight tied to the real window (inner + chrome) so a
    # small window on a big spoofed screen doesn't leak the screen size through
    # outerWidth (an inner<outer==screen mismatch a scanner can flag). Only when
    # a resolution was chosen — Auto opens the window at the engine's own screen,
    # where outer already agrees.
    if _res_overrides is not None:
        try:
            ctx.add_init_script(_outer_size_override_script())
        except Exception:
            pass

    # The persistent context already opened ONE window (about:home, which the
    # startup-homepage pref navigates to the chosen engine). Don't open a second
    # page — new_page() opens a whole new WINDOW in this Firefox, which is the
    # "two windows, one flashes and dies" bug. The single window is enough; the
    # user drives it from there.

    # The window is on screen the moment __enter__ returns, so report ready now.
    emit("BROWSER_STARTED")

    # Detect closure by watching the WINDOW count, not the process. Playwright
    # keeps the Firefox process ALIVE after the user closes the last window (a
    # persistent context stays connected for more commands), so the process
    # never exits on its own — watching the pid would never see the close and
    # the profile would stay "running". But ctx.pages drops to 0 (and page
    # "close" events fire) the instant the last window is closed. Poll the page
    # count and treat zero as "user closed the browser".
    closed = threading.Event()

    def stop_gracefully() -> None:
        """On STOP (parent terminate) close the context so Firefox removes its
        own lock before exit; a hard kill would leave a stale lock and block the
        next launch."""
        try:
            if inv is not None:
                inv.__exit__(None, None, None)
        except Exception:
            pass
        closed.set()

    # A forked process is told to STOP with SIGTERM (only settable on the main
    # thread). In the Windows/macOS thread path there's no signal; the parent
    # sets stop_event, which we poll in the wait loop below.
    if not in_thread:
        import signal

        signal.signal(signal.SIGTERM, lambda *a: stop_gracefully())

    # Closure watch. On the Linux fork path, ctx.pages drops to 0 when the user
    # closes the last window (read from the thread that created ctx — that's THIS
    # thread on both paths; see _enter_with_timeout inline entry). The
    # Windows/macOS thread path watches the profile's visible window and
    # processes instead (see _thread_close_watch — a persistent context there
    # keeps ctx.pages at 1 and never fires a close event, and the multi-process
    # Firefox doesn't exit on an X-close).
    tracked_pids = None
    if in_thread:
        tracked_pids = _thread_close_watch(
            profile_dir, closed, stop_event, stop_gracefully
        )
    else:
        while not closed.wait(0.5):
            try:
                if len(ctx.pages) == 0:
                    break  # user closed the last window
            except Exception:
                break  # context torn down (browser gone) → treat as closed

    # Tear down so Firefox actually exits and releases its lock, then report.
    try:
        if inv is not None:
            inv.__exit__(None, None, None)
    except Exception:
        pass
    # __exit__ is a polite Playwright teardown; a persistent-context
    # multi-process Firefox routinely survives it (parent + GPU/content/socket
    # children stayed alive after an X-close), holding the profile lock and
    # piling up across launches. Kill whatever still belongs to this profile.
    _kill_profile_firefox(profile_dir, tracked_pids)
    emit("BROWSER_CLOSED")
    _finish()
    return


def _ps_single_quote(s: str) -> str:
    """Quote a string as a PowerShell single-quoted literal.

    A Windows path is full of backslashes; json.dumps (used before) escaped them
    to '\\\\', which no longer matches the real single-backslash CommandLine, so
    the WMI -like filter returned nothing and the close-watch never saw the
    profile's processes (the "profile stuck running after X" bug). A PowerShell
    single-quoted string treats backslashes literally — only a single quote needs
    doubling."""
    return "'" + s.replace("'", "''") + "'"


def _thread_close_watch(profile_dir, closed, stop_event, stop_gracefully,
                        no_process_timeout=60.0, interval=1.0):
    """Wait until the user closed THIS profile's Firefox or STOP is requested —
    the Windows/macOS thread-path close signal. A persistent context keeps
    ctx.pages at 1 and never fires a close event, and the multi-process
    Firefox does NOT exit when the window is X-closed — GPU/content/socket
    firefox.exe children (and the connected parent) keep running, so waiting
    for a pid to die never fires. The close is decided by the profile's
    VISIBLE top-level window disappearing after it was seen (EnumWindows over
    the tracked pids); every tracked pid dying stays a close signal too (a
    crash, or macOS where there's no window enumeration).

    The profile's pids are resolved once and then polled with cheap ctypes
    checks — re-running the WMI CommandLine scan every tick for every open
    profile is real CPU/battery burn. A failed query (None) carries no
    verdict: it must NOT count as "process/window gone", or a transient
    failure would tear down a live browser — only a confident dead poll, or a
    confident no-window after the window was actually seen, decides the
    close. If no process is ever confidently seen within `no_process_timeout`,
    give up so a half-failed launch can't wedge the profile "running"
    forever.

    Returns the tracked pid set (None if never seen) — captured while the
    browser was still alive, so the caller can force-kill the survivors after
    the polite teardown (which the multi-process Firefox routinely outlives)."""
    pids = None
    window_seen = False
    deadline = time.monotonic() + no_process_timeout
    while not closed.wait(interval):
        if stop_event is not None and stop_event.is_set():
            stop_gracefully()
            return pids
        if pids is not None:
            if not any(_pid_alive(p) for p in pids):
                return pids  # every tracked Firefox process exited
            visible = _pids_have_visible_window(pids)
            if visible:
                window_seen = True
            elif visible is False and window_seen:
                return pids  # the window the user saw is gone → they closed it
            continue
        found = _profile_firefox_pids(profile_dir)
        if found:
            pids = found
            continue
        if found is None:
            # No verdict from the WMI scan; the single-pid helper is a second
            # chance (and the only resolver on macOS, which has no WMI).
            p = _firefox_pid(profile_dir)
            if p is not None:
                pids = {p}
                continue
        if time.monotonic() > deadline:
            # Never confidently saw the process → treat the launch as dead.
            return pids
    return pids


def _kill_profile_firefox(profile_dir, known_pids=None) -> None:
    """Force-kill every firefox.exe still running for THIS profile.

    The kill set is the union of `known_pids` (resolved by the close-watch
    while the browser was alive) and a fresh resolve — children spawn/exit
    over the window's lifetime, and a no-verdict re-resolve must not lose the
    tracked pids. Only pids matched to this profile_dir are ever killed;
    other profiles' Firefox is untouchable."""
    pids = set(known_pids or ())
    fresh = _profile_firefox_pids(profile_dir)
    if fresh:
        pids |= fresh
    for pid in pids:
        if _pid_alive(pid):
            _force_kill_pid(pid)


def _force_kill_pid(pid: int) -> None:
    """Kill `pid` and, on Windows, its whole process tree. Firefox content/GPU
    children don't carry the profile dir on their command line, so the WMI
    match only sees the parent — a single TerminateProcess would orphan the
    children (the 30 leftover firefox.exe after an X-close)."""
    if _platform.IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **_platform.no_window_kwargs(),
            )
            return
        except Exception:
            pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _profile_firefox_pids(profile_dir: str):
    """PIDs of firefox.exe processes belonging to THIS profile (Windows), matched
    by profile_dir in the command line via WMI. Returns None when the query
    can't run or failed — a transient WMI error is "no verdict", while an empty
    set means the query SUCCEEDED and the profile truly has no process; the
    close-watch must tell the two apart or it either misses a close forever or
    tears down a live browser.

    Counting ALL firefox.exe windows is the bug behind "profile stuck running":
    with several profiles (or a stray/zombie Firefox) open, closing one still
    leaves other firefox.exe windows, so an all-windows count never reaches zero
    and the close is never detected. Scoping to this profile's own processes
    makes the close-watch reliable regardless of what else is running."""
    if not profile_dir or not _platform.IS_WINDOWS:
        return None
    try:
        pat = _ps_single_quote("*" + profile_dir + "*")
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='firefox.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like {pat} }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True, **_platform.no_window_kwargs(),
        )
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return None


def _visible_window_pids():
    """PIDs that own a visible top-level window (Windows), or None when the
    enumeration can't run or failed — no-verdict must stay distinct from a
    confident empty, same as _profile_firefox_pids. Pure ctypes so the
    close-watch can poll every tick without spawning a subprocess."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        pids = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_window(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                pids.add(int(owner.value))
            return True

        if not user32.EnumWindows(on_window, 0):
            return None
        return pids
    except Exception:
        return None


def _pids_have_visible_window(pids):
    """Whether any of this profile's Firefox pids still owns a visible
    top-level window. True/False are confident verdicts; None means the
    enumeration couldn't tell (non-Windows or a transient failure) and the
    close-watch must not act on it. Firefox's hidden helper windows don't
    pass IsWindowVisible, so "no visible window" is exactly the state after
    the user X-closed the browser while the processes live on."""
    visible = _visible_window_pids()
    if visible is None:
        return None
    return bool(visible & set(pids))


def _firefox_pid(profile_dir: str):
    """The pid of a Firefox process owning this profile, or None.

    invisible launches `firefox -no-remote ... -profile <profile_dir> ...`; match
    that command line so we watch the right process even with several profiles
    open. profile_dir is unique per profile, so the match is unambiguous. Uses
    pgrep on Linux/macOS and a WMI CommandLine query on Windows (pgrep doesn't
    exist there)."""
    if not profile_dir:
        return None
    if _platform.IS_WINDOWS:
        try:
            # Query the command line of every firefox.exe and match the profile
            # dir. CIM/WMI exposes CommandLine; PowerShell keeps this dependency
            # free of extra packages.
            pat = _ps_single_quote("*" + profile_dir + "*")
            ps = (
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='firefox.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like {pat} }} | "
                "Select-Object -First 1 -ExpandProperty ProcessId"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, **_platform.no_window_kwargs(),
            )
            out = out.strip()
            return int(out) if out else None
        except Exception:
            return None
    try:
        # `--` stops pgrep parsing the pattern (which starts with "-profile") as
        # options. Match on the profile dir alone — it's unique per profile.
        out = subprocess.check_output(
            ["pgrep", "-f", "--", re.escape(profile_dir)], text=True
        )
    except Exception:
        return None
    for line in out.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def _pid_alive(pid: int) -> bool:
    """Whether `pid` is still running. The Windows check is pure ctypes
    (OpenProcess + WaitForSingleObject) so the close-watch can poll every tick
    without spawning a subprocess per poll."""
    if pid is None:
        return False
    if _platform.IS_WINDOWS:
        try:
            import ctypes

            SYNCHRONIZE = 0x00100000
            WAIT_TIMEOUT = 0x00000102
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if not handle:
                return False  # an exited pid can't be opened
            try:
                return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _child_main() -> None:
    """Entry for the non-fork (Win/Mac) subprocess path: read cfg from env and
    run _child writing to stdout (fd 1)."""
    cfg = json.loads(os.environ.get("PERSONA_INVISIBLE_CFG", "{}"))
    _child(cfg, 1)


class InvisibleProcess:
    """Popen-compatible handle around the invisible_playwright child."""

    def __init__(self, cfg: dict) -> None:
        # No blocking work here: this runs on the caller's thread (the Flet
        # session thread on a UI launch). Bookmark seeding — which can do a
        # one-time headless engine init taking tens of seconds — happens in
        # _child, off this thread, before the visible window opens.
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
            # Windows/macOS: sys.executable is the flet launcher, not a python
            # interpreter, so re-exec (`sys.executable -c ...`) just opens a
            # second GUI. Run _child in a THREAD in this process instead; it
            # talks to us over an os.pipe exactly like the forked child does,
            # and a stop_event stands in for the SIGTERM the fork path uses.
            import threading

            r, w = os.pipe()
            self._stop_event = threading.Event()
            wf = w

            def _run(_cfg=cfg, _wf=wf, _ev=self._stop_event):
                try:
                    _child(_cfg, _wf, stop_event=_ev)
                except Exception:
                    try:
                        os.write(_wf, b"BROWSER_CLOSED\n")
                    except Exception:
                        pass
                    try:
                        os.close(_wf)
                    except Exception:
                        pass

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
            self.stdout = os.fdopen(r)
            self.pid = 0
        self.returncode = None

    def poll(self):
        if self._fork:
            if self._proc.is_alive():
                return None
            self.returncode = self._proc.exitcode
            return self.returncode
        if self._thread.is_alive():
            return None
        self.returncode = 0
        return 0

    def wait(self, timeout=None):
        if self._fork:
            self._proc.join(timeout)
            self.returncode = self._proc.exitcode
            return self.returncode
        self._thread.join(timeout)
        self.returncode = 0 if not self._thread.is_alive() else None
        return self.returncode

    def terminate(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.terminate()
        else:
            self._stop_event.set()

    def kill(self):
        if self._fork:
            if self._proc.is_alive():
                self._proc.kill()
        else:
            self._stop_event.set()


def spawn(cfg: dict) -> InvisibleProcess:
    return InvisibleProcess(cfg)
