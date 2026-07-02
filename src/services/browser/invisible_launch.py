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
    """The host display's scale factor (1.0 at 100%, 1.5 at 150%, 2.0 at 200%).

    On Windows a HiDPI monitor runs at 125–200% scale; matching it keeps the
    spoofed browser's content readable instead of microscopic. Non-Windows
    desktops the shrink bug didn't affect fall back to 1.0. Clamped to a sane
    desktop range so a weird reading can't produce an unusable window."""
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


def _init_places_db(profile_dir: str) -> bool:
    """Launch the engine once headless so it creates a valid places.sqlite.

    Firefox rejects a hand-built places database ("files in use"), so the only
    way to get a database we can seed is to let the engine create one. Returns
    True once places.sqlite exists.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception:
        return False
    try:
        with InvisiblePlaywright(
            seed=abs(hash(profile_dir)) % (2**31),
            headless=True,
            profile_dir=profile_dir,
        ):
            time.sleep(6)  # let Places create + flush the database
    except Exception:
        pass
    # Clear the lock the headless run leaves so the real launch isn't blocked.
    for fname in ("lock", ".parentlock"):
        try:
            os.remove(os.path.join(profile_dir, fname))
        except OSError:
            pass
    return os.path.exists(os.path.join(profile_dir, "places.sqlite"))


def _seed_firefox_bookmarks(profile_dir: str, bookmarks: list) -> None:
    """Put the profile's bookmarks on the Firefox toolbar via places.sqlite.

    The engine must have created places.sqlite first (Firefox rejects a
    hand-built one); if it hasn't, do a one-time headless init to create it.
    Seed only once per bookmark set (a marker file) so a set the user has
    since edited by hand isn't re-clobbered on every launch."""
    if not profile_dir or not bookmarks:
        return
    try:
        from ...models.bookmark import Bookmark
        from .firefox_bookmarks import seed_places_bookmarks

        os.makedirs(profile_dir, exist_ok=True)
        sig = _bookmarks_sig(bookmarks)
        marker = os.path.join(profile_dir, ".persona-bookmarks-sig")
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as f:
                if f.read().strip() == sig:
                    return  # this exact set was already seeded

        places = os.path.join(profile_dir, "places.sqlite")
        if not os.path.exists(places) and not _init_places_db(profile_dir):
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
        into the parent's code; a thread must just return."""
        try:
            out.flush()
        except Exception:
            pass
        if not in_thread:
            os._exit(0)

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
    # Seed the fingerprint deterministically from the profile name so the same
    # profile keeps a stable identity across launches.
    seed = abs(hash(cfg.get("profile_name", ""))) % (2**31)
    kwargs = {"seed": seed, "headless": False, "extra_prefs": _profile_prefs(cfg)}
    # Pin the screen to the profile's resolution. The engine derives the window
    # viewport, the spoofed `screen` and `device_scale_factor` from these, so
    # the window opens at exactly the chosen size and the fingerprint agrees.
    # A desktop DPR of 1.0 keeps `layout.css.devPixelsPerPx` at 1 — without it
    # the engine samples a HiDPI DPR and the page renders tiny on a 150%-scaled
    # Windows host after the window is already up.
    res = cfg.get("resolution")
    if res:
        w, h = int(res[0]), int(res[1])
        dpr = _system_dpr()
        # DEBUG: log the real display metrics so the HiDPI window sizing can be
        # tuned to an actual 4K/scaled host (dev VMs can't reproduce HiDPI).
        try:
            emit(f"HIDPI_DEBUG dpr={dpr} chosen={w}x{h} metrics={_screen_metrics()}")
        except Exception:
            pass
        # Open the window physically at the chosen resolution: Firefox's
        # --width/--height are CSS pixels, so at dpr>1 pass resolution/dpr and the
        # physical window comes out to exactly the chosen resolution instead of
        # resolution*dpr (which overflowed the monitor). The spoofed screen still
        # reports the full chosen resolution for the fingerprint.
        if dpr > 1.0:
            cw, ch = int(round(w / dpr)), int(round(h / dpr))
            _extra_win = [f"--width={cw}", f"--height={ch}"]
        else:
            _extra_win = [f"--width={w}", f"--height={h}"]
        kwargs["pin"] = {
            "screen.width": w,
            "screen.height": h,
            "screen.avail_width": w,
            "screen.avail_height": h - 40,
            "screen.dpr": _system_dpr(),
        }
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
    if res:
        extra_args.extend(_extra_win)
    if extra_args:
        kwargs["extra_args"] = extra_args
    if profile_dir:
        kwargs["profile_dir"] = profile_dir

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

    # Closure watch: the user closing the last window drops ctx.pages to 0
    # (Playwright keeps the Firefox PROCESS alive in a persistent context, so we
    # watch WINDOWS not the process). ctx.pages must be read from the thread that
    # created ctx — on both paths that's THIS thread now (see _enter_with_timeout,
    # which enters inline on the thread path).
    # On the fork path (Linux) ctx.pages drops to 0 when the user closes the last
    # window, and reading it from this process is reliable. On the Windows thread
    # path a persistent context keeps a background page after the visible window
    # is gone (ctx.pages stays 1) AND the Firefox process stays alive — neither
    # signals the close. What actually tracks "the user closed it" is the count
    # of VISIBLE top-level Firefox windows for this profile, read from the OS. Grace
    # period: the window takes a moment to appear, so don't treat the initial
    # zero as closed.
    saw_window = False
    while not closed.wait(0.5):
        if stop_event is not None and stop_event.is_set():
            stop_gracefully()
            break
        if in_thread:
            # Count only THIS profile's Firefox windows, not every firefox.exe —
            # otherwise other open profiles keep the count above zero and this
            # profile's close is never seen (the "stuck running" bug).
            pids = _profile_firefox_pids(profile_dir)
            if not pids:
                # No process for this profile at all: it never started, or the
                # whole thing already exited. Once we've seen a window, that's a
                # close; before that, keep waiting through the launch grace.
                if saw_window:
                    break
                continue
            n = _count_windows_for_pids(pids)
            if n > 0:
                saw_window = True
            elif saw_window:
                break
            continue
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
    emit("BROWSER_CLOSED")
    _finish()
    return


def _profile_firefox_pids(profile_dir: str) -> set:
    """PIDs of firefox.exe processes belonging to THIS profile (Windows), matched
    by profile_dir in the command line via WMI.

    Counting ALL firefox.exe windows is the bug behind "profile stuck running":
    with several profiles (or a stray/zombie Firefox) open, closing one still
    leaves other firefox.exe windows, so an all-windows count never reaches zero
    and the close is never detected. Scoping to this profile's own processes
    makes the close-watch reliable regardless of what else is running."""
    if not profile_dir or not _platform.IS_WINDOWS:
        return set()
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='firefox.exe'\" | "
            "Where-Object { $_.CommandLine -like '*' + "
            f"{json.dumps(profile_dir)}"
            " + '*' } | Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True, **_platform.no_window_kwargs(),
        )
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return set()


def _firefox_pids_snapshot() -> set:
    """Set of all running firefox.exe pids (Windows), via a Toolhelp32 process
    snapshot. Pure ctypes — no subprocess. Empty set on failure."""
    if not _platform.IS_WINDOWS:
        return set()
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    pids = set()
    try:
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return set()
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == "firefox.exe":
                pids.add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        kernel32.CloseHandle(snap)
    except Exception:
        return set()
    return pids


def _count_windows_for_pids(pids: set) -> int:
    """Count VISIBLE, titled top-level windows owned by any pid in `pids`
    (Windows). Pure ctypes EnumWindows. A persistent-context Firefox keeps its
    process alive after the user closes the last visible window, so this window
    count is the reliable close signal. Returns 0 when pids is empty."""
    if not pids or not _platform.IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    count = {"n": 0}

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.GetWindowTextLengthW(hwnd) > 0:
                count["n"] += 1
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        return 0
    return count["n"]


def _firefox_pid(profile_dir: str):
    """The pid of a Firefox process owning this profile, or None.

    invisible launches `firefox -no-remote ... -profile <profile_dir> ...`; match
    that command line so we watch the right process even with several profiles
    open. profile_dir is unique per profile, so the match is unambiguous. Uses
    pgrep on Linux/macOS and WMIC/tasklist on Windows (pgrep doesn't exist
    there)."""
    if not profile_dir:
        return None
    if _platform.IS_WINDOWS:
        try:
            # Query the command line of every firefox.exe and match the profile
            # dir. CIM/WMI exposes CommandLine; PowerShell keeps this dependency
            # free of extra packages.
            ps = (
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='firefox.exe'\" | "
                "Where-Object { $_.CommandLine -like '*' + "
                f"{json.dumps(profile_dir)}"
                " + '*' } | Select-Object -First 1 -ExpandProperty ProcessId"
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
    if pid is None:
        return False
    if _platform.IS_WINDOWS:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                text=True, **_platform.no_window_kwargs(),
            )
            return str(pid) in out
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
        # Seed the profile's bookmarks into places.sqlite before launching. The
        # first time a profile with bookmarks is opened this does a one-time
        # headless engine init to create the database, so the very first real
        # window already shows the bookmarks.
        _seed_firefox_bookmarks(cfg.get("profile_dir", ""), cfg.get("bookmarks", []))
        # Pin DuckDuckGo as the default search engine for every Firefox profile.
        _ensure_firefox_policies()
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
