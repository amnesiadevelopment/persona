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
import threading
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
    so a fresh profile opens a normal mid-size window — the same feel as
    chromium's default. xulstore.json's main-window size restores in DEVICE
    pixels (live-proven: a window measured 1920x1068 physical at a 1.5 OS
    scale persisted and restored as "1920"/"1068"), so the seed is physical
    px, NOT divided by the OS scale. Only when xulstore.json is absent:
    Firefox persists the user's own window size there, and a manual resize
    must survive relaunches."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "xulstore.json")
    if os.path.exists(path):
        return
    aw, ah = _work_area()
    if not (aw and ah):
        return
    w, h = aw // 2, ah // 2
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


def _init_places_db(
    profile_dir: str,
    seed: int,
    timeout: float = 90.0,
    close_grace: float = 15.0,
    enter_timeout: float = 25.0,
    stop_event=None,
) -> bool:
    """Launch the engine once headless so it creates a valid places.sqlite.

    Firefox rejects a hand-built places database ("files in use"), so the only
    way to get a database we can seed is to let the engine create one. `seed`
    is the profile's stable fingerprint seed, the same one the real launch
    uses. Waits for Places to write the bookmark roots (the toolbar row the
    seeder needs) — the file alone lands on disk long before the roots, and
    seeding a rootless database silently inserts nothing. Playwright's sync
    API is thread-affine, so each attempt (enter → wait for roots → polite
    exit) runs on one worker thread. The enter itself is bounded to
    `enter_timeout` and retried: a wedged persistent-context launch (the #137
    family — live: the playwright driver crashed mid-init) used to eat the
    whole `timeout` as dead air with the user staring at a card that "does
    nothing". `timeout` still bounds the whole init, the polite close gets
    only `close_grace` on top (Firefox's shutdown blockers hang it for ~90s
    at times; the settle below kills the leftovers anyway), and `stop_event`
    cancels between polls so a STOP doesn't have to wait the init out.
    Returns True once the roots exist.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception:
        return False
    import threading

    from .firefox_bookmarks import places_ready

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    # The engine hides this run by passing cloak_windows via user.js, but the
    # cloak gate acts on the value already in prefs.js when the window is
    # created — on a fresh profile there is none, so the init's Firefox window
    # popped up VISIBLY (at the sampled profile's own scale, resizing as
    # Playwright applied its viewport — the "misscaled window skewing before
    # my eyes" of #131). Put the cloak in prefs.js BEFORE the first start;
    # _scrub_headless_cloak_prefs drops it again before the visible launch.
    if not _platform.IS_LINUX:
        _upsert_prefs_js(profile_dir, {
            "zoom.stealth.cloak_windows": True,
            "widget.windows.window_occlusion_tracking.enabled": False,
        })

    places = os.path.join(profile_dir, "places.sqlite")
    deadline = time.monotonic() + timeout

    for _attempt in range(3):
        if stopped() or time.monotonic() >= deadline:
            break
        entered = threading.Event()
        ready = threading.Event()
        done = threading.Event()

        def init(entered=entered, ready=ready, done=done) -> None:
            try:
                with InvisiblePlaywright(
                    seed=seed,
                    headless=True,
                    profile_dir=profile_dir,
                    # Skip Firefox's startup remote-settings sync — over Tor
                    # that fetch stalls this throwaway run for minutes.
                    extra_prefs=dict(_NO_STARTUP_FETCH),
                ):
                    entered.set()
                    while (
                        time.monotonic() < deadline
                        and not stopped()
                        and not places_ready(places)
                    ):
                        time.sleep(0.5)
                    ready.set()
            except Exception:
                pass
            finally:
                ready.set()
                done.set()

        t = threading.Thread(target=init, daemon=True)
        t.start()
        enter_deadline = min(deadline, time.monotonic() + enter_timeout)
        while not entered.wait(0.2):
            if done.is_set() or stopped() or time.monotonic() > enter_deadline:
                break
        if not entered.is_set():
            # Wedged (or failed) before the engine came up: kill this
            # profile's Firefox so the blocked enter unwinds, settle, retry.
            _kill_profile_firefox(profile_dir)
            _wait_profile_released(profile_dir)
            for fname in ("lock", ".parentlock"):
                try:
                    os.remove(os.path.join(profile_dir, fname))
                except OSError:
                    pass
            continue
        # Entered: the worker leaves its roots-wait on ready/stop/deadline,
        # then its polite close gets only close_grace on top (the settle
        # below kills whatever a hung shutdown leaves behind).
        while not ready.wait(0.5):
            if stopped() or time.monotonic() > deadline:
                break
        done.wait(close_grace)
        break
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
    # session (sessionstore.resume_session_once) — restoring the throwaway headless
    # window replaces the initial window juggler attaches to (half-destroyed
    # webProgress, endless SimpleChannel churn) and the launch wedges before
    # BROWSER_STARTED. Live-proven: process settling alone did NOT unwedge it,
    # wiping the session did.
    import shutil

    for name in (
        "sessionstore.jsonlz4",
        "sessionCheckpoints.json",
        "sessionstore-backups",
        # The init run also persists ITS window size into xulstore.json,
        # which would both defeat _seed_window_size (it only seeds when the
        # file is absent) and hand the visible window the throwaway run's
        # geometry. The init's window state is throwaway by definition.
        "xulstore.json",
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


def _upsert_prefs_js(profile_dir: str, prefs: dict) -> None:
    """Write prefs straight into the profile's prefs.js (replacing any existing
    lines for the same prefs), creating the file if needed.

    Firefox's own startup applies user.js over prefs.js, but the patched
    binary's window gates (the DWM cloak) and the first paint act on the value
    already in prefs.js when the window is created — a user.js override lands
    too late for them (live-proven both ways: the first headless init's window
    stayed VISIBLE despite cloak=true in user.js, and a visible launch right
    after the init stayed cloaked despite false in user.js). Prefs that must
    be in force for the profile's FIRST window go through here."""
    if not profile_dir or not prefs:
        return
    path = os.path.join(profile_dir, "prefs.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        lines = []

    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        return json.dumps(str(v))

    kept = [ln for ln in lines if not any(f'"{k}"' in ln for k in prefs)]
    kept += [f'user_pref("{k}", {fmt(v)});\n' for k, v in prefs.items()]
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError:
        pass


def _scrub_headless_cloak_prefs(profile_dir: str) -> None:
    """Drop the engine's headless window-hiding prefs from the profile's
    prefs.js before a visible launch.

    The headless places init runs the engine once in this same profile; on
    Windows/macOS the engine hides that run by pref (zoom.stealth.cloak_windows
    — the patched binary DWM-cloaks its own windows), and Firefox persists the
    pref into prefs.js. The cloak gate acts on the value already in prefs.js
    when the window is created — the visible launch's own pref override lands
    too late to uncloak it (live-proven) — so the stale entries must be gone
    from prefs.js before Firefox starts. Only the two headless-hiding prefs
    are touched."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "prefs.js")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    stale = ("zoom.stealth.cloak_windows",
             "widget.windows.window_occlusion_tracking.enabled")
    kept = [ln for ln in lines if not any(k in ln for k in stale)]
    if len(kept) == len(lines):
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError:
        pass


_DARK_THEME_ID = "firefox-compact-dark@mozilla.org"


def _activate_dark_theme(profile_dir: str) -> None:
    """Make Firefox's chrome (titlebar + tab strip) dark by activating the
    built-in dark theme in the profile's extensions.json.

    extensions.json is the AddonManager's source of truth for WHICH theme is
    active; on this patched Firefox, extensions.activeThemeID in prefs alone
    does not switch the active theme (live-proven: the pref is set yet
    default-theme stays active and the tab strip is light — #152). The browser
    chrome follows the ACTIVE theme add-on, so flip the built-in dark theme on
    and the others off (live-proven: the tab strip and toolbar go dark).

    The file is created by the headless bookmarks/places init that runs before
    the visible launch; when it isn't present yet (a profile with no bookmarks
    on its first launch) this is a no-op — the theme then applies from the next
    launch, once Firefox has written extensions.json."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "extensions.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    addons = data.get("addons")
    if not isinstance(addons, list):
        return
    changed = False
    for a in addons:
        if not isinstance(a, dict) or a.get("type") != "theme":
            continue
        want_active = a.get("id") == _DARK_THEME_ID
        if a.get("active") != want_active or a.get("userDisabled") != (
            not want_active
        ):
            a["active"] = want_active
            a["userDisabled"] = not want_active
            changed = True
    if not changed:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


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


def _seed_firefox_bookmarks(
    profile_dir: str, bookmarks: list, seed: int, stop_event=None
) -> None:
    """Reconcile the Firefox toolbar to the profile's bookmark set via
    places.sqlite — runs before every launch, so edits in the profile editor
    take effect on the next launch: removed bookmarks disappear, nothing ever
    duplicates, an empty set clears the toolbar (#144).

    The engine must have created places.sqlite first (Firefox rejects a
    hand-built one); if it hasn't, do a one-time headless init to create it.
    An empty set on a profile with no places.sqlite yet has nothing to clear —
    no engine init just to write nothing."""
    if not profile_dir:
        return
    try:
        from ...models.bookmark import Bookmark
        from .firefox_bookmarks import places_ready, sync_places_bookmarks

        places = os.path.join(profile_dir, "places.sqlite")
        if not places_ready(places):
            if not bookmarks or not _init_places_db(
                profile_dir, seed, stop_event=stop_event
            ):
                return

        marks = [Bookmark(b.get("name", ""), b.get("url", "")) for b in bookmarks]
        sync_places_bookmarks(places, marks)
    except Exception:
        pass


def _has_saved_session(profile_dir: str) -> bool:
    """Whether the profile holds a session Firefox will restore at startup —
    the clean-shutdown store or a crash-recovery backup."""
    if not profile_dir:
        return False
    return os.path.exists(
        os.path.join(profile_dir, "sessionstore.jsonlz4")
    ) or os.path.exists(
        os.path.join(profile_dir, "sessionstore-backups", "recovery.jsonlz4")
    )


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
            # The engine implements headless on Windows/macOS by making the
            # patched binary DWM-cloak its own windows
            # (zoom.stealth.cloak_windows). The one-time headless places init
            # runs in this SAME profile and Firefox persists that pref into
            # prefs.js, so a visible launch would inherit the cloak and open
            # every window invisible (#142). Force it off — user.js overlays
            # prefs.js at startup. Occlusion tracking goes back to its default
            # for the same reason (the headless run disables it).
            "zoom.stealth.cloak_windows": False,
            "widget.windows.window_occlusion_tracking.enabled": True,
            # Force the dark UI regardless of the seed. invisible derives the
            # theme from the fingerprint seed, so without this a profile's theme
            # is random; persona's users expect dark. systemUsesDarkTheme alone
            # only darkens page content and the in-content UI — the titlebar and
            # tab strip stayed light (a light strip across the top, #152). The
            # browser CHROME follows the active theme, so switch it to Firefox's
            # built-in dark theme and pin both chrome surfaces to dark (0=dark).
            "ui.systemUsesDarkTheme": 1,
            "extensions.activeThemeID": "firefox-compact-dark@mozilla.org",
            "browser.theme.content-theme": 0,
            "browser.theme.toolbar-theme": 0,
            "layout.css.prefers-color-scheme.content-override": 0,
            # Restore the previous session's tabs/windows across launches. The
            # persistent profile_dir holds sessionstore. Restore is enabled via
            # resume_session_once (re-armed through user.js on every launch, so
            # the "once" never expires) and NOT browser.startup.page=3: the
            # startup-page choice must stay 0 so nsIBrowserHandler.defaultArgs
            # stays "about:blank" — SessionStore only lets the restored session
            # OWN the startup window (overwriteTabs) when the cmdline URL
            # equals defaultArgs, and Playwright hardcodes an "about:blank"
            # cmdline URL on every persistent launch (swallowed into a string
            # arg by the trailing -new-window flag, see _child). With page=3
            # defaultArgs became the homepage instead, so every relaunch KEPT
            # an extra blank tab next to the restored ones (#148). Write the
            # store often so a tab opened seconds before close is still in the
            # restored session.
            "browser.startup.page": 0,
            "browser.sessionstore.resume_session_once": True,
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
            # Quit promptly when the user closes the last window. Under
            # Playwright's persistent context + juggler pipe, closing the
            # window does NOT quit Firefox on its own: it runs the full async
            # shutdown, whose blockers keep the process alive for ~60-90s
            # (live-measured). The Linux close-watch waits for that process to
            # die, so the profile card stayed "running" for a minute after an
            # X-close (#149). fastShutdownStage=3 _exit()s right after the
            # essential shutdown notifications — the process dies ~2s after the
            # window closes (live-measured), so pid-death detection fires
            # promptly. The restored session survives: sessionstore's periodic
            # write (interval below) has the tabs on disk before shutdown
            # (live-verified: has_saved_session True after a fast-shutdown
            # X-close), so #148's restore is intact.
            "toolkit.shutdown.fastShutdownStage": 3,
        }
    )
    # The chosen search engine feeds the Home button and the start page a
    # first launch navigates to (see _child — with startup.page=0 Firefox
    # itself never loads the homepage at startup). (Firefox 150 has no
    # per-profile way to set the URL-bar default engine without a network search
    # config, so the address bar keeps its built-in default; the start page is
    # what the user sees open.)
    engine = cfg.get("search_engine", "duckduckgo")
    start = _SEARCH_URLS.get(engine, _SEARCH_URLS["duckduckgo"]).split("?", 1)[0]
    prefs["browser.startup.homepage"] = start
    return prefs


# One launch pipeline per profile at a time (thread path). A relaunch clicked
# while the previous _child of the SAME profile was still winding down (its
# headless places init, or the kill/settle of an abandoned attempt) ran
# concurrently with it — and the predecessor's _kill_profile_firefox shot down
# the successor's just-launching Firefox: the intermittent "clicked open,
# nothing happened" relaunch roulette. Fork children are separate processes
# and never contend on these in-process locks.
_launch_locks: dict = {}
_launch_locks_guard = threading.Lock()


def _profile_launch_lock(profile_dir: str) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(profile_dir or ""))
    with _launch_locks_guard:
        lock = _launch_locks.get(key)
        if lock is None:
            lock = _launch_locks[key] = threading.Lock()
        return lock


class _WorkerSession:
    """A launched engine whose ctx lives on its own worker thread.

    Playwright's sync API is thread-affine: every call on the context — and
    the teardown — must run on the thread that created it. The Windows/macOS
    path can't use the calling thread for that, because a proxied launch of
    the patched Firefox wedges INSIDE launch_persistent_context and never
    returns (live: ~half of fresh proxied launches hang on a half-destroyed
    initial-window attach), and killing the browser does NOT make the blocked
    sync call raise — the driver keeps waiting. So the enter runs on a
    dedicated worker that the caller can bound and ABANDON on overrun, then
    retry on a fresh worker. `run_on_worker` marshals a callable back onto the
    worker (for add_init_script / teardown); the abandoned worker's leaked
    greenlet dies on its own once its Firefox is gone."""

    def __init__(self, inv, ctx, worker, requests, results):
        self.inv = inv
        self.ctx = ctx
        self._worker = worker
        self._requests = requests
        self._results = results

    def run_on_worker(self, fn, timeout=None):
        """Run `fn()` on the worker thread and return its result. Thread-affine
        ctx calls must go through here. `timeout` bounds the wait for the
        result; on overrun returns None and leaves the request in flight (the
        worker is abandoned by the caller). A None wait is unbounded."""
        self._requests.put(fn)
        try:
            return self._results.get(timeout=timeout)
        except Exception:
            return None

    def teardown(self):
        """Politely tear the engine down on its own worker, then join it.

        The polite __exit__ closes the persistent context and stops Playwright;
        over a proxy against a wedged Firefox that close can itself hang, and an
        UNBOUNDED wait for it would hold the caller (and the per-profile launch
        lock #150 wraps around the whole session) forever — the next launch of
        the profile then waits on the lock and times out (#154 mechanism a). So
        the teardown is bounded: on overrun the worker is abandoned (a daemon
        thread whose greenlet dies once its Firefox is force-killed by the
        caller right after this) rather than blocking the lock release."""
        if not self._worker.is_alive():
            return  # a dead worker never answers run_on_worker — blocking forever
        self.run_on_worker(lambda: self.inv.__exit__(None, None, None), timeout=15)
        self._requests.put(None)  # release the worker loop
        self._worker.join(10)


def _enter_on_worker(InvisiblePlaywright, kwargs, profile_dir, attempts, per_try,
                     stop_event=None):
    """Enter on a dedicated worker thread, bounding each attempt to `per_try`
    seconds; on overrun kill this profile's Firefox and retry on a FRESH
    worker (the wedged one is abandoned — see _WorkerSession). Returns a
    _WorkerSession on success, or None if every attempt overran or STOP
    cancelled the launch."""
    import queue
    import threading

    for attempt in range(attempts):
        if attempt and stop_event is not None and stop_event.is_set():
            break  # the user cancelled the launch — don't retry
        entered = threading.Event()
        holder = {}
        requests: "queue.Queue" = queue.Queue()
        results: "queue.Queue" = queue.Queue()

        def worker(entered=entered, holder=holder,
                   requests=requests, results=results):
            try:
                inv = InvisiblePlaywright(**kwargs)
                ctx = inv.__enter__()
            except BaseException:  # noqa: BLE001 — the bound/retry decides
                entered.set()
                return
            holder["inv"] = inv
            holder["ctx"] = ctx
            entered.set()
            # Service thread-affine ctx calls until told to stop (None).
            while True:
                fn = requests.get()
                if fn is None:
                    return
                try:
                    results.put(fn())
                except Exception:
                    results.put(None)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Bound the enter, also breaking early on STOP.
        deadline = time.monotonic() + per_try
        while not entered.wait(0.2):
            stopped = stop_event is not None and stop_event.is_set()
            if stopped or time.monotonic() > deadline:
                break
        if "ctx" in holder:
            return _WorkerSession(
                holder["inv"], holder["ctx"], t, requests, results
            )
        # Overrun (or a stop, or an enter error). Kill this profile's Firefox
        # so the abandoned worker's blocked call eventually unwinds and its
        # Firefox tree is gone, then settle before a fresh attempt so we never
        # relaunch over a still-dying instance (#132's half-destroyed-window
        # wedge).
        _kill_profile_firefox(profile_dir)
        _wait_profile_released(profile_dir)
        for fname in ("lock", ".parentlock"):
            try:
                os.remove(os.path.join(profile_dir, fname))
            except OSError:
                pass
    return None


def _enter_with_timeout(InvisiblePlaywright, kwargs, profile_dir, attempts, per_try):
    """Enter an InvisiblePlaywright context on the FORK path (Linux), bounding
    each attempt to `per_try` seconds and retrying. Returns (inv, ctx) on
    success, or (None, None) if every attempt timed out.

    __enter__ is a blocking call, so it runs in a thread; when the attempt
    overruns we kill the launching Firefox so the blocked call raises and the
    thread unwinds, then try again with a clean profile lock. The Windows/macOS
    thread path uses _enter_on_worker instead (see _WorkerSession)."""
    import threading

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


def _install_geo_shortcircuit() -> None:
    """Skip the engine's egress-IP lookup when the timezone is already
    concrete. invisible runs that lookup over the proxy before every launch to
    drive a WebRTC srflx override, but persona always passes a concrete zone,
    on Tor WebRTC carries no real candidates anyway, and the lookup adds ~15s
    of round-trips (and one more network step for a proxied launch to wedge
    on). "auto"/empty still falls through to the engine's own resolver."""
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
    _launch_and_watch(cfg, profile_dir, emit, _finish, stop_event, in_thread)


def _launch_and_watch(cfg, profile_dir, emit, _finish, stop_event, in_thread):
    """Run one profile's whole browser session: seed the profile, launch the
    engine, report readiness, watch for the close, tear down.

    Launches of the SAME profile are serialized only through the SETUP+ENTER
    phase (see _profile_launch_lock): that's where #150's race lives — a
    relaunch clicked while the previous launch was still in its headless
    bookmark init or the kill/settle of an abandoned enter attempt ran
    concurrently, and the predecessor's cleanup shot down the successor's
    just-launching Firefox. Once the browser is up (BROWSER_STARTED) the lock
    is RELEASED, so the long close-watch never blocks a legitimate relaunch —
    holding it through the whole session made a relaunch wait on the previous
    session's (slow) close and time out (#154). The acquire is cancellable so a
    STOP while a predecessor winds down can't block this thread forever (#141)."""
    import threading
    import time

    launch_lock = _profile_launch_lock(profile_dir)
    while not launch_lock.acquire(timeout=0.5):
        if stop_event is not None and stop_event.is_set():
            emit("LAUNCH_CANCELLED")
            emit("BROWSER_CLOSED")
            _finish()
            return
    _lock_released = False

    def _release_lock():
        nonlocal _lock_released
        if not _lock_released:
            _lock_released = True
            try:
                launch_lock.release()
            except RuntimeError:
                pass

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
    _seed_firefox_bookmarks(profile_dir, cfg.get("bookmarks", []), seed, stop_event)
    # The headless init above persists the engine's window-hiding pref into
    # prefs.js; left there, THIS launch's window comes up DWM-cloaked —
    # running but invisible (#142). Must run after the seeding, before the
    # launch.
    _scrub_headless_cloak_prefs(profile_dir)
    # Switch the profile's chrome to the built-in dark theme. The headless init
    # above created extensions.json (the AddonManager's active-theme source of
    # truth); flip it to dark so the titlebar/tab strip aren't light (#152).
    # No-op when no init ran (no extensions.json yet).
    _activate_dark_theme(profile_dir)
    # Decided BEFORE Firefox starts (a running session rewrites the store):
    # with a saved session Firefox restores the user's tabs into the window;
    # without one the launch navigates the lone blank tab to the start page.
    restoring = _has_saved_session(profile_dir)
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
        _release_lock()
        emit(f"LAUNCH_FAILED: invisible_playwright import error: {e}")
        emit("BROWSER_CLOSED")
        _finish()  # close the pipe so the monitor's reader unblocks (no fd leak)
        return

    # When persona already knows the timezone (it always passes a concrete
    # one), skip invisible's per-launch egress-IP lookup over the proxy.
    if cfg.get("timezone"):
        _install_geo_shortcircuit()

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
        pinned_dpr = 1
        kwargs["pin"] = {
            "screen.width": w,
            "screen.height": h,
            "screen.avail_width": w,
            "screen.avail_height": h - 40,
            "screen.dpr": pinned_dpr,
        }
        # A chosen resolution presents as a device-pixel-ratio-1 monitor (a real
        # 2560x1440 panel is dpr 1). window.devicePixelRatio, the CSS resolution
        # media queries (matchMedia("(resolution: Ndppx)")), and the render scale
        # must ALL report that one dpr, or a scanner cross-checks them and reads
        # screen.width * dpr as the physical size — a 2560 screen at the host's
        # own scale reports 3840/5120 ("4K") on a 150%/200% display. The render
        # pref layout.css.devPixelsPerPx drives the CSS-resolution vector, so
        # pinning it to the HOST scale (the old #131 render-comfort fix) is
        # exactly what leaked the doubled resolution. Pin it to the chosen dpr so
        # every dpr-derived value is coherent at the chosen resolution — the
        # chromium model (its device ext pins dpr=1 so screen.width*dpr==
        # screen.width). Content renders at the spoofed dpr's scale, which is the
        # honest size a real dpr-1 monitor would show. Set explicitly (not
        # deleted): a stale value in the profile's prefs.js would otherwise win.
        kwargs["extra_prefs"]["layout.css.devPixelsPerPx"] = str(pinned_dpr)
        # The FIRST paint uses the value already in prefs.js — the user.js
        # override above only kicks in a beat later, and the headless places
        # init leaves the SAMPLED profile's dpr there. A mismatch between the
        # prefs.js value at first paint and the user.js value applied just after
        # flips the window scale mid-open (the initial skew). Carry the same
        # value in prefs.js before Firefox starts so there's no flip.
        _upsert_prefs_js(
            profile_dir, {"layout.css.devPixelsPerPx": str(pinned_dpr)}
        )
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
    if profile_dir:
        # MUST stay the LAST argument. Playwright hardcodes an "about:blank"
        # positional URL after our args on every persistent-context launch;
        # a positional URL reaches the first window as an nsIArray, which can
        # never equal nsIBrowserHandler.defaultArgs — so SessionStore keeps
        # the cmdline tab and APPENDS the restored ones: an extra blank tab
        # on every relaunch (#148). A trailing -new-window consumes the
        # "about:blank" as its own parameter instead: same single window, but
        # opened through the string path where the URL equals defaultArgs
        # (startup.page=0), so a restored session fully overwrites it.
        extra_args.append("-new-window")
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

    # Launch with a bounded timeout and retries. A launch can stall inside
    # launch_persistent_context: over Tor on Firefox's startup remote-settings
    # fetch, and — the #137 wedge — on a proxied FRESH profile whose initial
    # window is torn down mid-attach ("half-destroyed webProgress"). Cap each
    # attempt; on overrun kill this profile's Firefox and retry. On the thread
    # path the enter runs on an abandonable worker thread (killing the browser
    # does NOT make the blocked sync call raise there); the fork path kills the
    # launching Firefox so the blocked __enter__ raises and the thread unwinds.
    #
    # The per-attempt bound MUST clear the slowest legitimate launch, or it
    # kills a launch that was about to succeed and every retry repeats the
    # kill — the #154 regression: a proxied launch (a cold proxy circuit, and a
    # relaunch that restores a session referencing proxied tabs) routinely
    # takes far longer than the 25s that a fast local launch needs, so all
    # attempts overran and the launch reported LAUNCH_FAILED. The engine itself
    # bounds launch_persistent_context at 180s (a true wedge raises there), so a
    # proxied launch gets a per-attempt budget just past that — a genuinely
    # wedged launch still fails and retries, but a merely-slow one completes.
    per_try = 190 if proxy else 45
    attempts = 2 if proxy else 3
    session = None
    inv = ctx = None
    if in_thread:
        session = _enter_on_worker(
            InvisiblePlaywright, kwargs, profile_dir,
            attempts=attempts, per_try=per_try, stop_event=stop_event,
        )
        if session is not None:
            inv, ctx = session.inv, session.ctx
    else:
        inv, ctx = _enter_with_timeout(
            InvisiblePlaywright, kwargs, profile_dir,
            attempts=attempts, per_try=per_try,
        )
    if ctx is None:
        _release_lock()
        if stop_event is not None and stop_event.is_set():
            emit("LAUNCH_CANCELLED")
        else:
            emit("LAUNCH_FAILED: launch timed out")
        emit("BROWSER_CLOSED")
        _finish()
        return

    # Thread-affine ctx calls must run on the worker that created ctx (the
    # thread path) — route them through the session; the fork path uses ctx
    # directly on this thread.
    def on_ctx(fn):
        if session is not None:
            return session.run_on_worker(fn)
        try:
            return fn()
        except Exception:
            return None

    # Prefix every tab/window title with the profile name so the taskbar button
    # identifies which persona owns the window. An init script runs in every page
    # the context opens, including tabs the user opens by hand.
    _prefix = f"[{name}] " if name else None
    if name:
        on_ctx(lambda: ctx.add_init_script(
            "(()=>{const P=" + json.dumps(_prefix) + ";"
            "const f=()=>{if(document.title&&!document.title.startsWith(P))"
            "document.title=P+document.title;};f();"
            "document.addEventListener('DOMContentLoaded',f);"
            "const h=document.head||document.documentElement;"
            "if(h)new MutationObserver(f).observe(h,"
            "{subtree:true,childList:true,characterData:true});})();"
        ))

    # Keep outerWidth/outerHeight tied to the real window (inner + chrome) so a
    # small window on a big spoofed screen doesn't leak the screen size through
    # outerWidth (an inner<outer==screen mismatch a scanner can flag). Only when
    # a resolution was chosen — Auto opens the window at the engine's own screen,
    # where outer already agrees.
    if _res_overrides is not None:
        on_ctx(lambda: ctx.add_init_script(_outer_size_override_script()))

    # The persistent context already opened ONE window: with a saved session
    # Firefox restored the user's tabs into it (the trailing -new-window arg
    # plus startup.page=0 let the restore fully own the window — no extra
    # blank tab, #148); on a first launch it holds a single about:blank tab,
    # navigated to the start page below. Don't open a second page — new_page()
    # opens a whole new WINDOW in this Firefox, which is the "two windows, one
    # flashes and dies" bug. The single window is enough; the user drives it
    # from there.

    # The window is on screen the moment __enter__ returns, so report ready now.
    emit("BROWSER_STARTED")

    # The browser is up: the #150 launch race is over. Release the per-profile
    # lock so a later relaunch of this profile isn't blocked by this session's
    # (potentially slow) close-watch and teardown below (#154). The end-of-
    # session kill targets only THIS session's tracked pids (never a fresh
    # profile-dir rescan), so it can't shoot down a relaunch's Firefox.
    _release_lock()

    # Windows opens the engine's window BEHIND persona (the launch runs in
    # persona's own process, which holds the foreground), so the user only
    # sees the stop button and no browser. Chromium raises its own window;
    # match that. Off this thread so the close-watch starts immediately.
    if _platform.IS_WINDOWS:
        threading.Thread(
            target=_raise_profile_window, args=(profile_dir,), daemon=True
        ).start()

    # First-ever launch of the profile (nothing to restore): the window came
    # up on the swallowed cmdline's lone about:blank tab — show the chosen
    # start page instead. Never on a restore launch: pages[0] is then a
    # RESTORED tab and navigating it would clobber the user's session.
    # wait_until="commit" + a cap so a stalled proxy can't hold this thread
    # (the close-watch below) for long; on timeout the tab just stays blank.
    if not restoring:
        _start = kwargs["extra_prefs"].get("browser.startup.homepage")
        if _start:
            def _open_start_page():
                pages = ctx.pages
                if pages:
                    pages[0].goto(_start, wait_until="commit", timeout=15000)
            on_ctx(_open_start_page)

    # Set by stop_gracefully when a STOP tears the browser down; the
    # platform close-watches below poll it between liveness checks.
    closed = threading.Event()

    def stop_gracefully() -> None:
        """On STOP (parent terminate) close the context so Firefox removes its
        own lock before exit; a hard kill would leave a stale lock and block the
        next launch. The teardown is thread-affine, so it runs on the worker
        that created ctx (thread path) or directly (fork path)."""
        try:
            if session is not None:
                session.teardown()
            elif inv is not None:
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

    # Closure watch. The Windows/macOS thread path watches the profile's
    # visible window and processes (see _thread_close_watch); the Linux fork
    # path watches the Firefox pid (see _fork_close_watch). Neither can use
    # ctx.pages: the ctx lives on the launch worker thread, so the sync API
    # never delivers close events to a poll made from this thread.
    tracked_pids = None
    if in_thread:
        tracked_pids = _thread_close_watch(
            profile_dir, closed, stop_event, stop_gracefully
        )
    else:
        tracked_pids = _fork_close_watch(profile_dir, closed)

    # Tear down so Firefox actually exits and releases its lock, then report.
    # (On a STOP the close-watch already called stop_gracefully, which tears
    # the session down; a second teardown is a harmless no-op.)
    try:
        if session is not None:
            session.teardown()
        elif inv is not None:
            inv.__exit__(None, None, None)
    except Exception:
        pass
    # __exit__ is a polite Playwright teardown; a persistent-context
    # multi-process Firefox routinely survives it (parent + GPU/content/socket
    # children stayed alive after an X-close), holding the profile lock and
    # piling up across launches. Kill whatever still belongs to THIS session.
    # Kill ONLY the tracked pids (+ their process trees), never a fresh
    # profile-dir rescan: the launch lock was released at BROWSER_STARTED, so a
    # relaunch of this profile may already be starting, and a fresh rescan would
    # match — and kill — the relaunch's Firefox (the #150 race, re-armed by the
    # early lock release). The tracked parent's tree covers its own children.
    # When the watch never resolved a pid (a dead launch, tracked_pids falsy),
    # fall back to a rescan — there's no live session to protect a relaunch from.
    _kill_profile_firefox(
        profile_dir, tracked_pids, rescan=not tracked_pids
    )
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

    A close needs `no_window_streak` CONSECUTIVE confident no-window polls, not
    one: navigating to a heavy page (a scanner site over a proxy) can make a
    single EnumWindows tick miss the profile's window for a beat while the
    window is busy — reacting to that one poll tore the whole session down
    mid-navigation ("окно закрывается" на pixelscan/iphey, #159). A real user
    close keeps the window gone; a nav transient recovers on the next tick and
    resets the streak.

    Returns the tracked pid set (None if never seen) — captured while the
    browser was still alive, so the caller can force-kill the survivors after
    the polite teardown (which the multi-process Firefox routinely outlives)."""
    pids = None
    window_seen = False
    gone_streak = 0
    no_window_streak = 2
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
                gone_streak = 0
            elif visible is False and window_seen:
                gone_streak += 1
                if gone_streak >= no_window_streak:
                    return pids  # the window the user saw is gone → they closed it
            # None (no verdict) leaves the streak unchanged — neither a sighting
            # nor a confident miss.
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


def _fork_close_watch(profile_dir, closed, no_process_timeout=60.0, interval=1.0):
    """Wait until this profile's Firefox process exits or STOP is requested —
    the Linux fork-path close signal.

    ctx.pages cannot be the signal: the context is created on
    _enter_with_timeout's launch thread, and the sync API's dispatcher
    greenlet only pumps events during calls made on THAT thread — polled from
    the child's own thread, ctx.pages is a frozen snapshot that never drops
    to 0, so an X-close was never detected and the profile stayed "running"
    with no close in the log (#143). Pid death is the close signal. Under the
    Playwright juggler pipe an X-close does NOT quit Firefox on its own — it
    runs the full async shutdown whose blockers hold the process alive ~60-90s
    (live-measured), so the card stayed "running" for a minute after an X-close
    even with the #143 pid-watch (#149). toolkit.shutdown.fastShutdownStage=3
    (set in _profile_prefs) makes the process _exit ~2s after the window closes
    (live-measured), so pid death — and this close — now fire promptly. The pid
    is resolved once (a pgrep per tick burns CPU), then polled with the cheap
    liveness check; a launch whose process is never seen within
    `no_process_timeout` is treated as dead so the profile can't wedge
    "running" forever.

    Returns the tracked pid set (None if never seen) so the caller can
    force-kill survivors after the polite teardown."""
    pid = None
    deadline = time.monotonic() + no_process_timeout
    while not closed.wait(interval):
        if pid is not None:
            if not _pid_alive(pid):
                return {pid}
            continue
        pid = _firefox_pid(profile_dir)
        if pid is None and time.monotonic() > deadline:
            return None
    return {pid} if pid is not None else None


def _kill_profile_firefox(profile_dir, known_pids=None, rescan=True) -> None:
    """Force-kill the firefox.exe processes of THIS profile.

    `known_pids` are the pids the close-watch resolved while the browser was
    alive; each is killed together with its process tree, so a parent's
    GPU/content/socket children go too without needing a fresh scan. When
    `rescan` is True the set is also unioned with a fresh profile-dir resolve
    (children spawn/exit over a window's lifetime). The teardown after a live
    session passes rescan=False so a rescan can't match — and kill — a
    concurrently-relaunching Firefox of the same profile (the launch lock is
    released at BROWSER_STARTED). Only pids matched to this profile_dir are ever
    killed; other profiles' Firefox is untouchable."""
    pids = set(known_pids or ())
    if rescan:
        fresh = _profile_firefox_pids(profile_dir)
        if fresh:
            pids |= fresh
    for pid in pids:
        if _pid_alive(pid):
            _force_kill_pid(pid)


def _kill_process_tree_ctypes(pid: int) -> bool:
    """Terminate `pid` and every descendant via pure ctypes (Toolhelp children
    map + TerminateProcess). Returns True when the root process is confirmed
    gone. No subprocess — the packaged flet app couldn't spawn taskkill, which
    is how X-closed Firefox trees piled up as zombies."""
    if not _platform.IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        entries = _win_process_entries()
        if entries is None:
            return False
        children: dict = {}
        for p, pp, _name in entries:
            children.setdefault(pp, []).append(p)
        order = [int(pid)]
        i = 0
        while i < len(order):
            order.extend(children.get(order[i], ()))
            i += 1

        PROCESS_TERMINATE = 0x0001
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        for p in order:
            h = k32.OpenProcess(PROCESS_TERMINATE, False, p)
            if h:
                try:
                    k32.TerminateProcess(h, 1)
                finally:
                    k32.CloseHandle(h)
        return not _pid_alive(pid)
    except Exception:
        return False


def _force_kill_pid(pid: int) -> None:
    """Kill `pid` and, on Windows, its whole process tree. Firefox content/GPU
    children don't carry the profile dir on their command line, so the pid
    match only sees the parent — a single TerminateProcess would orphan the
    children (the 30 leftover firefox.exe after an X-close). Pure ctypes
    first; taskkill (by absolute path) is the fallback."""
    if _platform.IS_WINDOWS:
        if _kill_process_tree_ctypes(pid):
            return
        try:
            subprocess.run(
                [_system32_tool("taskkill.exe"), "/PID", str(pid), "/T", "/F"],
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


def _system32_tool(*rel: str) -> str:
    """Absolute path of a System32 tool, falling back to the bare name when
    the expected file is missing. The packaged flet app failed to spawn
    PATH-searched tools (powershell/taskkill) while absolute-path spawns kept
    working — the same reason playwright invokes its node.exe by full path."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = os.path.join(root, "System32", *rel)
    return p if os.path.exists(p) else rel[-1].rsplit(".", 1)[0]


def _win_process_entries():
    """(pid, parent_pid, exe_name) of every running process (Windows) via a
    pure-ctypes Toolhelp snapshot, or None when the snapshot failed."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        TH32CS_SNAPPROCESS = 0x2

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return None
        entries = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                entries.append(
                    (
                        int(entry.th32ProcessID),
                        int(entry.th32ParentProcessID),
                        entry.szExeFile,
                    )
                )
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return entries
    except Exception:
        return None


def _win_process_command_line(pid: int):
    """The command line of `pid`, read straight from its PEB
    (NtQueryInformationProcess + ReadProcessMemory), or None when unreadable.
    Same-user processes — all of persona's Firefoxes — are readable. No
    subprocess is spawned, so this works identically in the dev venv and the
    packaged flet app."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid)
        )
        if not h:
            return None
        try:

            class PBI(ctypes.Structure):
                _fields_ = [
                    ("ExitStatus", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("AffinityMask", ctypes.c_void_p),
                    ("BasePriority", ctypes.c_void_p),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                ]

            pbi = PBI()
            ret_len = ctypes.c_ulong(0)
            if ctypes.windll.ntdll.NtQueryInformationProcess(
                h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
            ):
                return None  # non-zero NTSTATUS = failure
            if not pbi.PebBaseAddress:
                return None

            def read(addr, size):
                buf = ctypes.create_string_buffer(size)
                got = ctypes.c_size_t(0)
                if (
                    not k32.ReadProcessMemory(
                        h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)
                    )
                    or got.value != size
                ):
                    return None
                return buf.raw

            ptr = ctypes.sizeof(ctypes.c_void_p)
            # PEB.ProcessParameters: 0x20 on x64, 0x10 on x86.
            raw = read(pbi.PebBaseAddress + (0x20 if ptr == 8 else 0x10), ptr)
            if raw is None:
                return None
            params = int.from_bytes(raw, "little")
            if not params:
                return None
            # RTL_USER_PROCESS_PARAMETERS.CommandLine (a UNICODE_STRING:
            # USHORT Length, USHORT MaximumLength, pad, PWSTR Buffer):
            # 0x70 on x64, 0x40 on x86.
            raw = read(params + (0x70 if ptr == 8 else 0x40), 2 * ptr)
            if raw is None:
                return None
            length = int.from_bytes(raw[0:2], "little")
            buf_ptr = int.from_bytes(raw[ptr : 2 * ptr], "little")
            if not buf_ptr or not length:
                return None
            data = read(buf_ptr, length)
            if data is None:
                return None
            return data.decode("utf-16-le", errors="replace")
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _win_firefox_command_lines():
    """[(pid, command_line_or_None)] for every running firefox.exe (Windows),
    or None when the process snapshot itself failed. A per-process None means
    that one process couldn't be read — no verdict for it."""
    entries = _win_process_entries()
    if entries is None:
        return None
    return [
        (pid, _win_process_command_line(pid))
        for pid, _ppid, name in entries
        if name.lower() == "firefox.exe"
    ]


def _profile_firefox_pids(profile_dir: str):
    """PIDs of firefox.exe processes belonging to THIS profile (Windows),
    matched by profile_dir in the command line. Returns None when no resolver
    could produce a verdict — a transient failure is "no verdict", while an
    empty set means the scan SUCCEEDED and the profile truly has no process;
    the close-watch must tell the two apart or it either misses a close
    forever or tears down a live browser.

    The primary resolver is a pure-ctypes scan (Toolhelp + a PEB read): the
    packaged flet app silently failed to spawn powershell.exe, so the WMI
    query below never returned pids and every X-close was only "detected" by
    the close-watch's 60s give-up (persona's own live logs: every Firefox
    close landed at start+60..66s while STOPs resolved in 1s). PowerShell —
    by absolute path now — stays as the fallback verdict for processes the
    ctypes scan couldn't read.

    Counting ALL firefox.exe windows is the bug behind "profile stuck running":
    with several profiles (or a stray/zombie Firefox) open, closing one still
    leaves other firefox.exe windows, so an all-windows count never reaches zero
    and the close is never detected. Scoping to this profile's own processes
    makes the close-watch reliable regardless of what else is running."""
    if not profile_dir or not _platform.IS_WINDOWS:
        return None
    entries = _win_firefox_command_lines()
    if entries is not None:
        needle = profile_dir.lower()
        matched = {pid for pid, cl in entries if cl and needle in cl.lower()}
        if matched or all(cl is not None for _pid, cl in entries):
            return matched
    try:
        pat = _ps_single_quote("*" + profile_dir + "*")
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='firefox.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like {pat} }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            [_system32_tool("WindowsPowerShell", "v1.0", "powershell.exe"),
             "-NoProfile", "-Command", ps],
            text=True, **_platform.no_window_kwargs(),
        )
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return None


def _visible_windows():
    """(hwnd, pid) of every visible top-level window (Windows), or None when
    the enumeration can't run or failed — no-verdict must stay distinct from a
    confident empty, same as _profile_firefox_pids. Pure ctypes so the
    close-watch can poll every tick without spawning a subprocess."""
    if not _platform.IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_window(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                windows.append((int(hwnd), int(owner.value)))
            return True

        if not user32.EnumWindows(on_window, 0):
            return None
        return windows
    except Exception:
        return None


def _visible_window_pids():
    """PIDs that own a visible top-level window (Windows), or None on a failed
    enumeration (no verdict)."""
    windows = _visible_windows()
    if windows is None:
        return None
    return {pid for _hwnd, pid in windows}


def _window_cloaked(hwnd: int) -> bool:
    """Whether the window is DWM-cloaked (invisible to the user while still
    passing IsWindowVisible) — the engine's own headless mechanism, and also
    the state of windows on another virtual desktop. On any failure report
    not-cloaked so the raise still tries the window."""
    try:
        import ctypes

        cloaked = ctypes.c_int(0)
        DWMWA_CLOAKED = 14
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4
        ):
            return False
        return bool(cloaked.value)
    except Exception:
        return False


def _bring_window_to_foreground(hwnd: int) -> None:
    """Raise + focus a top-level window. SetForegroundWindow is permitted here
    because this runs in persona's own process right after the user clicked
    launch — the foreground process may reassign the foreground. When Windows
    refuses anyway (another app grabbed the foreground meanwhile), attach to
    the foreground window's input thread, which lifts the restriction. Every
    step is best-effort: a failed raise leaves the window in the background,
    never hidden."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        SW_SHOW, SW_RESTORE = 5, 9
        user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
        user32.BringWindowToTop(hwnd)
        if user32.SetForegroundWindow(hwnd):
            return
        k32 = ctypes.windll.kernel32
        fg = user32.GetForegroundWindow()
        if not fg or fg == hwnd:
            return
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        our_thread = k32.GetCurrentThreadId()
        if fg_thread and fg_thread != our_thread:
            if user32.AttachThreadInput(our_thread, fg_thread, True):
                try:
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(our_thread, fg_thread, False)
    except Exception:
        pass


def _raise_profile_window(profile_dir: str, timeout: float = 15.0,
                          interval: float = 0.5) -> bool:
    """Bring THIS profile's Firefox window to the foreground (Windows only).

    The engine's window is created while persona holds the foreground, so
    Windows opens it in the BACKGROUND — the profile looks launched-but-
    invisible. Polls briefly: the window can lag BROWSER_STARTED by a beat,
    and the pid resolve is a WMI query (resolved once, then reused). Returns
    True once a window owned by the profile's Firefox was raised."""
    if not _platform.IS_WINDOWS:
        return False
    deadline = time.monotonic() + timeout
    pids = None
    while time.monotonic() < deadline:
        if not pids:
            pids = _profile_firefox_pids(profile_dir)
        if pids:
            for hwnd, pid in _visible_windows() or ():
                # A DWM-cloaked window is invisible to the user even though
                # IsWindowVisible says otherwise; raising it does nothing —
                # keep polling for a really-visible window.
                if pid in pids and not _window_cloaked(hwnd):
                    _bring_window_to_foreground(hwnd)
                    return True
        time.sleep(interval)
    return False


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
                [_system32_tool("WindowsPowerShell", "v1.0", "powershell.exe"),
                 "-NoProfile", "-Command", ps],
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
