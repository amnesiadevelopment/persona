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
from .firefox_bookmarks import places_ready


# Written into a build's cache dir after a successful extraction. A build
# other than the package-pinned one only counts as installed once this exists,
# so a crash mid-extract can never leave a half build active.
_INSTALL_MARKER = ".persona-complete"

# A markerless pinned build (BINARY_VERSION, installed by the package's own
# ensure_binary) is trusted only if the engine's core is actually unpacked next
# to the entry — not just the thin launcher left by an aborted first download.
#
# The entry executable (firefox / firefox.exe / Firefox.app/.../firefox) is a
# ~700KB LAUNCHER on every OS; the engine's real weight is the shared core
# (libxul, ~230MB) beside it. Gauging completeness by the entry's own size was
# the #225 regression: it rejected even a fully-installed build (thin entry <
# threshold) and re-downloaded the engine on every start. Completeness is the
# unpacked BULK: a whole build carries hundreds of MB, an interrupted fetch
# carries only the stub. 50 MB clears any real build (libxul alone is ~5x that)
# and rejects a stub.
_WHOLE_BUILD_BYTES = 50_000_000


def _build_is_whole(build_dir) -> bool:
    """True when `build_dir` holds an unpacked engine, not just an aborted
    download's stub. Sums file sizes with an early exit the moment the total
    clears the threshold — a real build trips it within the first core file, so
    this rarely walks the whole tree."""
    total = 0
    try:
        for root, _dirs, names in os.walk(str(build_dir)):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                except OSError:
                    continue
                if total >= _WHOLE_BUILD_BYTES:
                    return True
    except OSError:
        return False
    return False


# The FF engine speaks juggler, not CDP, so the MCP browser tools can't attach
# over a debugging port the way they do for chromium. A running FF session
# publishes an eval callable here (running JS on its live page through the
# thread-affine worker); MCP looks it up by profile name for firefox profiles.
_ff_eval_registry: "dict[str, object]" = {}


def register_ff_eval(name: str, fn) -> None:
    if name:
        _ff_eval_registry[name] = fn


def unregister_ff_eval(name: str) -> None:
    _ff_eval_registry.pop(name, None)


def get_ff_eval(name: str):
    return _ff_eval_registry.get(name)


def installed_builds() -> list[str]:
    """firefox-NN tags with a complete binary in the engine cache, ascending
    by build number.

    The package's own pinned build (BINARY_VERSION) counts as soon as its
    binary is present — the engine's ensure_binary() installs it without a
    marker. Any OTHER build additionally needs the completion marker
    install_engine_build writes after extraction."""
    from ..engine.firefox import build_number

    try:
        from invisible_playwright.constants import (
            BINARY_ENTRY_REL,
            BINARY_VERSION,
            BROKEN_VERSIONS,
        )
        from invisible_playwright.download import cache_root

        entry_rel = BINARY_ENTRY_REL.get(sys.platform)
        if entry_rel is None:
            return []
        root = cache_root()
        if not root.is_dir():
            return []
        out = []
        for d in root.iterdir():
            tag = d.name
            if build_number(tag) < 0 or tag in BROKEN_VERSIONS:
                continue
            entry = d / entry_rel
            if not entry.exists():
                continue
            if (d / _INSTALL_MARKER).exists():
                out.append(tag)
                continue
            # No completion marker. Only the package-pinned BINARY_VERSION may be
            # markerless (ensure_binary installs it that way) — and only if the
            # engine core is actually unpacked, not just the thin entry launcher
            # left by an aborted first download (#225). Any other markerless build
            # is a crashed mid-extract.
            if tag != BINARY_VERSION:
                continue
            if not _build_is_whole(d):
                continue
            out.append(tag)
        out.sort(key=build_number)
        return out
    except Exception:
        return []


def active_build() -> str:
    """The firefox-NN build launches use: the highest complete installed
    build, or the package's pinned BINARY_VERSION when nothing is installed
    yet (that's the build the first download fetches)."""
    builds = installed_builds()
    if builds:
        return builds[-1]
    try:
        from invisible_playwright.constants import BINARY_VERSION

        return BINARY_VERSION
    except Exception:
        return ""


def _invisible_binary_path():
    """Path to the active build's patched Firefox executable, or None. Reuses
    invisible_playwright's own layout so we agree on where the binary lives
    without re-downloading when it's already there."""
    try:
        from invisible_playwright.constants import BINARY_ENTRY_REL
        from invisible_playwright.download import cache_dir_for_version

        entry_rel = BINARY_ENTRY_REL.get(sys.platform)
        if entry_rel is None:
            return None
        build = active_build()
        if not build:
            return None
        return cache_dir_for_version(build) / entry_rel
    except Exception:
        return None


def _binary_path_override():
    """Explicit executable path for the engine, or None to let it resolve its
    own. The engine's ensure_binary() always resolves the package-pinned
    BINARY_VERSION; when a newer downloaded build is active the launch must
    point at it explicitly or the update would silently never take effect."""
    try:
        from invisible_playwright.constants import BINARY_VERSION

        if active_build() == BINARY_VERSION:
            return None
    except Exception:
        return None
    p = _invisible_binary_path()
    if p and p.exists():
        return str(p)
    return None


def is_invisible_installed() -> bool:
    # "Installed" means at least one COMPLETE build (installed_builds already
    # rejects a markerless pinned stub whose engine core never unpacked, #225).
    # NOT "the active-build path exists" — active_build() falls back to naming
    # BINARY_VERSION even when nothing is installed, so an aborted first download
    # left that path present and this read True, blocking the auto-redownload and
    # leaving FF unlaunchable.
    return bool(installed_builds())


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


def _download_invisible(progress=None, log=None, version: str | None = None) -> bool:
    import platform as _pyplatform
    import tempfile

    from invisible_playwright.constants import (
        ARCHIVE_NAME,
        BINARY_ENTRY_REL,
        BINARY_VERSION,
    )
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

    version = version or BINARY_VERSION
    asset = ARCHIVE_NAME(sys.platform, _pyplatform.machine())
    version_dir = cache_dir_for_version(version)
    version_dir.mkdir(parents=True, exist_ok=True)

    say("Firefox engine: resolving release over Tor…")
    url_archive = _resolve_asset_url(version, asset)
    url_sums = _resolve_asset_url(version, "checksums.txt")

    # Keep the partial next to the cache dir so a dropped Tor circuit resumes
    # across restarts (same approach as fp-chromium). Version-prefixed: builds
    # share the asset filename (it carries the upstream Firefox version, not
    # the build number), so an unprefixed leftover from another build would be
    # resumed into a corrupt archive.
    archive_path = version_dir.parent / f"{version}-{asset}.download"

    # checksums.txt is tiny; a plain fetch is fine. Version-prefixed for the
    # same reason: a completed leftover is reused as-is (the resume sees the
    # Range past the end and treats the file as done), and another build's
    # checksums would fail every verify.
    sums_path = version_dir.parent / f"{version}-checksums.txt"
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
    try:
        os.remove(sums_path)
    except OSError:
        pass
    entry_rel = BINARY_ENTRY_REL.get(sys.platform)
    installed = bool(entry_rel) and (version_dir / entry_rel).exists()
    if installed:
        # Marker LAST: only a fully-extracted build may become active.
        try:
            (version_dir / _INSTALL_MARKER).touch()
        except OSError:
            pass
    return installed


def install_engine_build(version: str, progress=None, log=None) -> bool:
    """Download a specific firefox-NN engine build into its own versioned
    cache dir. The currently-active build stays on disk untouched until this
    one is complete (the marker is written last), so running profiles keep
    working and a failed download can't leave a half build active. Once the new
    build is whole, older cached builds are pruned (each is ~320MB)."""
    try:
        ok = _download_invisible(progress=progress, log=log, version=version)
    except Exception as e:
        if log:
            try:
                log(f"Firefox engine: update failed — {type(e).__name__}: {e}")
            except Exception:
                pass
        return False
    if ok:
        _prune_old_engine_builds(keep=version, log=log)
    return ok


def _prune_old_engine_builds(keep: str, log=None) -> None:
    """Delete firefox-NN builds older than `keep`, now that `keep` is installed
    and active — reclaiming ~320-600MB per stale build. Prunes any build LOWER
    than `keep`, including the package's own pinned BINARY_VERSION once a newer
    build has superseded it: launches resolve the highest installed build
    (active_build → _invisible_binary_path), and is_invisible_installed checks
    that same active build — so with `keep` present the pinned dir is dead
    weight and its removal never triggers a re-download. Never `keep` itself.
    A markerless dir at the pinned version is the shipped engine, safe to
    remove; a markerless dir at any OTHER version is a half-finished download,
    left alone. Best-effort: a build in use / undeletable is left alone."""
    from ..engine.firefox import build_number

    try:
        from invisible_playwright.constants import BINARY_VERSION
        from invisible_playwright.download import cache_root
    except Exception:
        return
    keep_n = build_number(keep)
    if keep_n < 0:
        return
    root = cache_root()
    if not root.is_dir():
        return
    import shutil

    for d in root.iterdir():
        tag = d.name
        n = build_number(tag)
        if n < 0 or n >= keep_n:
            continue
        # Prune a build we fully installed (has our marker) OR the shipped
        # pinned build now that a newer one is active. Any other markerless
        # dir is a half-finished download — leave it for a later resume.
        if not (d / _INSTALL_MARKER).exists() and tag != BINARY_VERSION:
            continue
        try:
            shutil.rmtree(d)
            if log:
                log(f"Firefox engine: removed old build {tag}")
        except OSError:
            pass


def prune_superseded_builds(log=None) -> None:
    """Housekeeping prune to run at startup: reclaim disk from any build older
    than the active one, including a pinned build a past engine update already
    superseded (the ~600MB firefox-15 an upgrade to firefox-16 left behind).

    _prune_old_engine_builds only runs right after a fresh download, so a build
    that went stale on an EARLIER run (or before this cleanup existed) would sit
    forever. Only prunes when a strictly-higher build is active, so it never
    touches the sole installed engine."""
    from ..engine.firefox import build_number

    builds = installed_builds()
    if len(builds) < 2:
        return  # nothing older than the active build to reclaim
    active = builds[-1]
    if build_number(active) < 0:
        return
    _prune_old_engine_builds(keep=active, log=log)


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

    # Bound the retries by CONSECUTIVE no-progress attempts, not total attempts:
    # over a slow Tor circuit (Mars saw ~0.1 MB/s) a 118MB archive drops its
    # circuit many times but keeps advancing, and a flat attempt cap would give
    # up mid-download. Any attempt that moves bytes resets this counter, so only
    # a truly stuck transfer (nothing arriving for a run of tries) bails.
    idle_attempts = 0
    while idle_attempts < 12:
        # Short backoff between failed tries so a dead circuit isn't hammered
        # open-and-drop with no pause (that just piled up timeouts); grows to a
        # few seconds, capped. No wait before the first try or after progress.
        if idle_attempts:
            time.sleep(min(0.5 * idle_attempts, 4.0))
        have = os.path.getsize(path) if os.path.exists(path) else 0
        progressed = False
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
                        progressed = True
                        last_progress[0] = time.monotonic()
                        if progress is not None:
                            progress(done, total)
                    out.flush()
            finally:
                stop_watch.set()

            size = os.path.getsize(path)
            if total and size < total:
                # Dropped early; resume with a fresh circuit. Only count it
                # against the give-up budget if this try moved NO bytes.
                idle_attempts = 0 if progressed else idle_attempts + 1
                continue
            if total and size > total:
                # Safety net: trim any overshoot back to the real size.
                with open(path, "r+b") as out:
                    out.truncate(total)
            return True
        except Exception:
            # keep the partial for the next resume attempt; a try that still
            # moved bytes before the drop doesn't burn the give-up budget
            idle_attempts = 0 if progressed else idle_attempts + 1
            continue
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
    return False


def installed_version() -> str:
    """Display string for the running engine: the patched build the launches
    actually use plus the upstream Firefox it's built on, e.g.
    "firefox-15 · FF 150.0.1". The patched build (active_build) is what decides
    behaviour — emoji, spoof patches — so it's the version the user needs to
    see (a stale firefox-13 draws flat emoji, firefox-15 draws Fluent); the
    bare upstream number alone hid which patched build was in play."""
    build = ""
    try:
        build = active_build()
    except Exception:
        build = ""
    upstream = ""
    try:
        from invisible_playwright.constants import FIREFOX_UPSTREAM_VERSION

        upstream = FIREFOX_UPSTREAM_VERSION
    except Exception:
        upstream = ""
    if build and upstream:
        return f"{build} · FF {upstream}"
    return build or upstream or ""


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

    Drives the Firefox render scale (layout.css.devPixelsPerPx) so a chosen
    resolution renders at the host's real scale — readable on a HiDPI display
    instead of physically tiny (#167) — and the initial-window-size seed. Read
    per-OS: GetDpiForSystem on Windows, the max NSScreen backingScaleFactor on
    macOS (2.0 on Retina), and the GDK/Wayland/Xft scale on Linux. Clamped to a
    sane desktop range so a weird reading can't produce an unusable window; a
    failed read falls back to 1.0."""
    if _platform.IS_WINDOWS:
        return _clamp_dpr(_windows_dpr())
    if _platform.IS_MACOS:
        return _clamp_dpr(_macos_dpr())
    if _platform.IS_LINUX:
        return _clamp_dpr(_linux_dpr())
    return 1.0


def _clamp_dpr(scale: float) -> float:
    """Keep a scale reading in a sane desktop range; 0/None → 1.0."""
    if not scale or scale <= 0:
        return 1.0
    return max(1.0, min(3.0, round(scale, 2)))


def _windows_dpr() -> float:
    try:
        import ctypes

        # Per-monitor DPI awareness so GetDpiForSystem returns the real scale.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def _macos_dpr() -> float:
    """The primary display's backing scale (2.0 on Retina). Prefers AppKit's
    NSScreen.backingScaleFactor via PyObjC when present; otherwise reads the
    active display mode's pixel-vs-point ratio through Quartz, which is in the
    stdlib-adjacent Core Graphics framework Python can reach with ctypes. Falls
    back to 2.0 — a Retina Mac tiny-rendering at 1.0 is the exact #167 failure,
    and modern Macs are overwhelmingly Retina, so 2.0 is the safer default than
    1.0 when the read fails."""
    try:
        from AppKit import NSScreen  # type: ignore

        screens = NSScreen.screens()
        if screens:
            return max(float(s.backingScaleFactor()) for s in screens)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util

        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
        cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
        cg.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetPixelWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeGetWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]
        did = cg.CGMainDisplayID()
        mode = cg.CGDisplayCopyDisplayMode(did)
        if mode:
            try:
                px = cg.CGDisplayModeGetPixelWidth(mode)
                pt = cg.CGDisplayModeGetWidth(mode)
            finally:
                cg.CGDisplayModeRelease(mode)
            if pt:
                return px / pt
    except Exception:
        pass
    return 2.0  # Retina default — 1.0 would render tiny (#167)


def _linux_dpr() -> float:
    """The desktop's scale factor. GDK_SCALE / QT_SCALE_FACTOR are the explicit
    per-session overrides; otherwise GDK's monitor scale (Wayland's wl_output
    scale, or X's Xft.dpi/96) via a short introspection call. Only integer
    Wayland scales are exposed by wl_output, but GDK reports fractional Xft/
    logical scales too. Falls back to 1.0 (a 100% Linux desktop, the common
    case)."""
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        val = os.environ.get(var)
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    try:
        import gi  # type: ignore

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # type: ignore

        display = Gdk.Display.get_default()
        if display is not None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            if monitor is not None:
                sf = monitor.get_scale_factor()
                if sf:
                    return float(sf)
    except Exception:
        pass
    xft = os.environ.get("XFT_DPI")
    if xft:
        try:
            return float(xft) / 96.0
        except ValueError:
            pass
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


def _language_override_script(locale: str) -> str:
    """JS that pins navigator.language/languages to `locale`.

    firefox-17 applies the locale to the Accept-Language HEADER (through
    intl.accept_languages, built from the Playwright locale) but NOT to
    navigator.language — that reports the host OS locale instead (uk-UA on a
    Ukrainian Windows even behind a US proxy). Header en-US + JS uk-UA is an
    internal contradiction a scanner flags as masking. Pin the JS getters to the
    SAME locale the header already carries so the two agree. languages is
    [locale, base] (e.g. ["en-US","en"]) to mirror the q-valued header. Empty
    locale is a no-op — nothing to pin."""
    if not locale:
        return ""
    base = locale.split("-", 1)[0]
    langs = [locale] if base == locale else [locale, base]
    langs_js = json.dumps(langs)
    loc_js = json.dumps(locale)
    return (
        "(() => {"
        "const L=" + loc_js + ",LS=" + langs_js + ";"
        "const def=(k,v)=>{try{Object.defineProperty(Navigator.prototype,k,"
        "{get:()=>v,configurable:true})}catch(e){}};"
        "def('language', L);"
        "def('languages', Object.freeze(LS.slice()));"
        # firefox-17 also leaks the host locale through the Intl API and Date's
        # locale-aware formatting (pixelscan's "Internationalization API" reads
        # Intl.DateTimeFormat().resolvedOptions().locale; Date.toString renders the
        # timezone name in the host locale). navigator.language alone isn't enough —
        # force every Intl formatter and Date's default locale to L so a scanner
        # sees one consistent locale.
        "try{"
        "const forceLocale=(ctor)=>{if(!ctor)return;"
        "const Orig=ctor.wrapped||ctor;"
        "const Wrapped=function(locales,options){"
        "const l=(locales===undefined||locales===null||"
        "(Array.isArray(locales)&&locales.length===0))?L:locales;"
        "return new.target?Reflect.construct(Orig,[l,options],new.target)"
        ":Orig(l,options);};"
        "Wrapped.prototype=Orig.prototype;"
        "Wrapped.wrapped=Orig;"
        "if(Orig.supportedLocalesOf)Wrapped.supportedLocalesOf="
        "Orig.supportedLocalesOf.bind(Orig);"
        "const ro=Orig.prototype&&Orig.prototype.resolvedOptions;"
        "if(ro){Orig.prototype.resolvedOptions=function(){"
        "const o=ro.call(this);o.locale=L;return o;};}"
        "return Wrapped;};"
        "['DateTimeFormat','NumberFormat','RelativeTimeFormat','Collator',"
        "'PluralRules','ListFormat','DisplayNames','Segmenter'].forEach(k=>{"
        "if(Intl[k]){const w=forceLocale(Intl[k]);if(w)Intl[k]=w;}});"
        # Date locale-aware methods default to L when no locale is passed.
        "const patchDate=(name)=>{const orig=Date.prototype[name];if(!orig)return;"
        "Date.prototype[name]=function(locales,options){"
        "return orig.call(this,locales===undefined?L:locales,options);};};"
        "['toLocaleString','toLocaleDateString','toLocaleTimeString']"
        ".forEach(patchDate);"
        # Date.prototype.toString/toTimeString/toDateString render the timezone
        # name in the host OS locale ("(за східноєвропейським…)" on a Ukrainian
        # host) — pixelscan's "Time from JS" reads exactly this. They bypass Intl,
        # so rebuild the zone suffix with an en-US long-timezone formatter.
        "const OP=String.fromCharCode(40),CP=String.fromCharCode(41);"
        "const zoneName=(d)=>{try{const p=new Intl.DateTimeFormat(L,"
        "{timeZoneName:'long'}).formatToParts(d);"
        "const z=p.find(x=>x.type==='timeZoneName');return z?z.value:'';}"
        "catch(e){return '';}};"
        "const reZone=(s,d)=>{if(isNaN(d.getTime()))return s;"
        "const i=s.indexOf(' '+OP);const zn=zoneName(d);"
        "return i<0||!zn?s:s.slice(0,i)+' '+OP+zn+CP;};"
        "const oTS=Date.prototype.toString,oTTS=Date.prototype.toTimeString;"
        "Date.prototype.toString=function(){return reZone(oTS.call(this),this);};"
        "Date.prototype.toTimeString=function(){"
        "return reZone(oTTS.call(this),this);};"
        "}catch(e){}"
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


def _seed_window_size(
    profile_dir: str,
    screen: "tuple[int, int] | None" = None,
    dpr: float = 1.0,
) -> None:
    """Seed the profile's INITIAL Firefox window size to half the work area
    so a fresh profile opens a normal mid-size window — the same feel as
    chromium's default. xulstore.json's main-window size restores in DEVICE
    pixels (live-proven: a window measured 1920x1068 physical at a 1.5 OS
    scale persisted and restored as "1920"/"1068"), so the seed is physical
    px, NOT divided by the OS scale. Only when xulstore.json is absent:
    Firefox persists the user's own window size there, and a manual resize
    must survive relaunches.

    When a resolution was chosen the window is CAPPED to that spoofed screen:
    the CSS innerWidth is the physical window width divided by `dpr`, so a
    window whose inner CSS size exceeds the spoofed screen is impossible for a
    real browser (a window can't be wider than its screen) — a scanner flags it
    and it skews layout on non-responsive pages (#216). screen is in CSS px, the
    window is device px, so the physical cap is screen*dpr minus a little chrome
    to leave inner ≤ screen."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "xulstore.json")
    if os.path.exists(path):
        return
    aw, ah = _work_area()
    if not (aw and ah):
        return
    w, h = aw // 2, ah // 2
    if screen:
        sw, sh = screen
        # screen.* is CSS px; the window is device px. Physical inner ≤
        # screen*dpr keeps CSS inner (physical/dpr) ≤ screen. Trim a little for
        # the window frame so the content area stays under the screen.
        scale = dpr if dpr and dpr > 0 else 1.0
        if sw:
            w = min(w, max(320, int(sw * scale) - 16))
        if sh:
            h = min(h, max(240, int(sh * scale) - 96))
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


def _scrub_chrome_zoom_css(profile_dir: str) -> None:
    """Remove a userChrome.css that zooms the browser's own UI.

    Profiles may carry a chrome/userChrome.css with a zoom rule on
    #navigator-toolbox/#mainPopupSet (an attempt to enlarge the tiny dpr=1
    chrome on a HiDPI host). CSS zoom participates in layout, so the zoomed
    toolbox moves the content area's geometry out from under the compositor:
    pages render shifted/overlapping with a paint artifact down the left edge,
    worse at Retina 2.0 (#206). Being a SIBLING of the content <browser> in
    browser.xhtml does NOT decouple them — siblings share the window's layout
    flow. A sheet with no zoom rule (a user's own customization) can't skew
    content and is left alone."""
    if not profile_dir:
        return
    path = os.path.join(profile_dir, "chrome", "userChrome.css")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            css = f.read()
        if "zoom" in css:
            os.remove(path)
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


def _wal_settled(places_db: str) -> bool:
    """Whether the places `-wal` has stopped growing — Places has finished its
    initial writes (the bookmark roots among them) and quiesced.

    This is a plain stat of the -wal file, so it needs NO sqlite connection and
    NO lock. The stealth-Firefox can hold places.sqlite under an exclusive lock
    for its whole run — reliably on macOS, INTERMITTENTLY on Windows/Linux
    (proven live in v2.5.1: fresh FF profiles hung 75s-3min there too) — so a
    separate-connection readiness read (places_ready) gets locked out and can't
    tell when the roots have landed. The -wal size settling is the lock-free
    signal that the engine has done its initial Places work and is safe to
    close, after which the roots become readable to the post-close places_ready
    check.

    Requires the -wal to have grown to a real size AND held it across two polls:
    a rootless first-write -wal is only a few pages, and a page-cache checkpoint
    can momentarily shrink it, so a minimum-size floor plus the stability check
    avoids declaring 'settled' before Places has actually written the roots."""
    wal = places_db + "-wal"
    try:
        size = os.path.getsize(wal)
    except OSError:
        return False
    # A -wal that hasn't grown past a few pages hasn't seen the roots yet. The
    # roots + their origins/places rows push it well past this floor.
    if size < _WAL_ROOTS_FLOOR:
        return False
    prev = _wal_settled._sizes.get(places_db)
    _wal_settled._sizes[places_db] = size
    # Settled once we observe the same non-trivial size on two consecutive polls.
    return prev is not None and prev == size


# ~16 KiB: four 4KiB pages. A fresh Places -wal with only its schema is smaller;
# writing the bookmark roots (moz_bookmarks + moz_places + moz_origins rows and
# their indexes) pushes it past this. Tuned to sit above the empty-DB baseline
# and below the roots-written size on all three OS.
_WAL_ROOTS_FLOOR = 16 * 1024
_wal_settled._sizes = {}


def _roots_ready(places_db: str) -> bool:
    """Readiness gate for the headless places init, robust on every OS.

    True if EITHER the concurrent reader can see the toolbar root (places_ready
    — the precise, fast signal, available whenever the writer isn't holding an
    exclusive lock) OR the -wal has settled (the lock-free fallback for when the
    reader is locked out). Preferring places_ready keeps the common case instant;
    the -wal fallback stops a locked-out read from burning the whole 90s timeout
    (#207) on ANY platform. The post-close places_ready is still the correctness
    check — a false-early -wal settle only costs one re-init, never a bad seed."""
    return places_ready(places_db) or _wal_settled(places_db)


def _init_places_db(
    profile_dir: str,
    seed: int,
    timeout: float = 90.0,
    close_grace: float = 15.0,
    enter_timeout: float = 8.0,
    stop_event=None,
    log=None,
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
    `enter_timeout` and retried: on Windows the FIRST headless __enter__ RACES —
    usually ~2.5s, but ~1 in 5 wedges the cold engine start; a 25s bound made
    that wedge cost the full 25s before the fast retry, the whole 28s first
    launch (#219). At 8s the normal enter still fits and a wedge costs ~8s + a
    ~2.5s retry (~10s worst case) instead of 28s. A wedged persistent-context
    launch (the #137 family — live: the playwright driver crashed mid-init) used
    to eat the whole `timeout` as dead air with the user staring at a card that
    "does nothing". `timeout` still bounds the whole init, the polite close gets
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

    # The stealth-Firefox can hold places.sqlite under an EXCLUSIVE lock for its
    # whole run (reliably on macOS, intermittently on Win/Linux — proven live in
    # v2.5.1), locking out the separate-connection places_ready read so the init
    # burned the full ~90s timeout waiting on a read that can't succeed, then
    # closed too late/hard and the seed failed (#207/#208). _roots_ready gates on
    # places_ready OR the lock-free -wal settle on EVERY platform: instant when
    # the reader is free, still bounded when it's locked out. The post-close
    # places_ready stays the correctness check.
    roots_ready = _roots_ready
    _wal_settled._sizes.pop(places, None)

    _t0 = time.monotonic()

    def phase(msg):
        if log:
            log(f"PLACES_INIT {msg} t={time.monotonic() - _t0:.1f}s")

    for _attempt in range(3):
        if stopped() or time.monotonic() >= deadline:
            break
        phase(f"attempt={_attempt} enter-begin")
        entered = threading.Event()
        ready = threading.Event()
        done = threading.Event()

        def init(entered=entered, ready=ready, done=done) -> None:
            try:
                with InvisiblePlaywright(
                    seed=seed,
                    headless=True,
                    profile_dir=profile_dir,
                    # The SAME binary the visible launch uses — the profile
                    # this init creates must match the build that opens it.
                    binary_path=_binary_path_override(),
                    # The engine's __enter__ resolves geo BEFORE Firefox
                    # starts: with no timezone it discovers the egress IP
                    # (three HTTPS echo endpoints, 10s timeout each) and
                    # refreshes the geoip mmdb — over Tor that's 30-60s per
                    # attempt, blowing enter_timeout and looping the init
                    # into a 3-4 minute first launch (#207) whose seed never
                    # lands (#208). Any concrete IANA zone short-circuits all
                    # of it before the first request; this run is throwaway
                    # (its session state is wiped below), so the zone itself
                    # doesn't matter.
                    timezone="UTC",
                    # Skip Firefox's startup remote-settings sync — over Tor
                    # that fetch stalls this throwaway run for minutes. The
                    # fast-shutdown stage spares the polite close below from
                    # Firefox's async shutdown blockers (~60-90s at times,
                    # burning the whole close_grace plus the settle's kill):
                    # the roots are committed before the close is requested,
                    # and the visible launch runs this same profile with the
                    # same pref.
                    extra_prefs={
                        **_NO_STARTUP_FETCH,
                        "toolkit.shutdown.fastShutdownStage": 3,
                    },
                ):
                    entered.set()
                    while (
                        time.monotonic() < deadline
                        and not stopped()
                        and not roots_ready(places)
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
            phase(f"attempt={_attempt} enter-wedged")
            _kill_profile_firefox(profile_dir)
            _wait_profile_released(profile_dir)
            for fname in ("lock", ".parentlock"):
                try:
                    os.remove(os.path.join(profile_dir, fname))
                except OSError:
                    pass
            continue
        phase(f"attempt={_attempt} entered")
        # Entered: the worker leaves its roots-wait on ready/stop/deadline.
        while not ready.wait(0.5):
            if stopped() or time.monotonic() > deadline:
                break
        phase(f"attempt={_attempt} roots-ready")
        # The roots are committed to places.sqlite the moment ready fires, and
        # this throwaway run's session is wiped below — so there's nothing to
        # gain by waiting out its polite close. On Windows that polite close ran
        # the full close_grace (Firefox's async shutdown blockers survive
        # fastShutdownStage here), and the release-wait added more on top — ~35s
        # of pure teardown dead-air on top of every FIRST launch of a bookmarked
        # profile (#219). Kill the headless engine immediately once the roots
        # exist; _wait_profile_released below confirms it's gone before the
        # visible launch. A brief nudge for the polite exit that's usually enough.
        done.wait(1.0)
        if not done.is_set():
            _kill_profile_firefox(profile_dir)
        phase(f"attempt={_attempt} killed")
        break
    # The engine's __exit__ is a polite Playwright teardown the multi-process
    # Firefox routinely survives; a REAL launch over a still-dying instance
    # can't come up cleanly. Don't proceed until this profile's Firefox is
    # confirmed gone. The headless engine was just killed above, so give the
    # polite exit almost no grace before force-killing survivors — the roots are
    # already on disk (#219: the default 5s grace + slow Windows shutdown was
    # dead-air on every first launch).
    _wait_profile_released(profile_dir, grace=0.5)
    phase("released")
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


_PERSONA_BOOKMARKS_MARKER = ".persona-bookmarks.json"


def _read_persona_bookmark_urls(profile_dir: str) -> "set[str]":
    """The urls persona placed on the toolbar last launch, from the profile's
    marker file. Missing/unreadable → empty (a fresh or pre-#171 profile)."""
    path = os.path.join(profile_dir, _PERSONA_BOOKMARKS_MARKER)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(u) for u in data}
    except (OSError, ValueError):
        pass
    return set()


def _write_persona_bookmark_urls(profile_dir: str, urls) -> None:
    """Record the urls persona just placed so the next launch reconciles only
    persona's own footprint (see sync_places_bookmarks)."""
    path = os.path.join(profile_dir, _PERSONA_BOOKMARKS_MARKER)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(urls), f)
    except OSError:
        pass


def _seed_firefox_bookmarks(
    profile_dir: str, bookmarks: list, seed: int, stop_event=None,
    attempts: int = 2, log=None,
) -> bool:
    """Reconcile persona's OWN Firefox toolbar bookmarks via places.sqlite —
    runs before every launch, so edits in the profile editor take effect on the
    next launch: removed bookmarks disappear, nothing ever duplicates (#144),
    an empty set clears persona's bookmarks (#147). Bookmarks the user added
    inside Firefox are preserved across relaunches — persona reconciles only the
    set it placed, tracked in a per-profile marker (#171).

    The engine must have created places.sqlite first (Firefox rejects a
    hand-built one); if it hasn't, do a one-time headless init to create it.
    An empty set on a profile with no places.sqlite yet has nothing to clear —
    no engine init just to write nothing (and no marker to update: persona has
    placed nothing).

    Returns True once the toolbar matches the configured set (or there was
    nothing to apply). Right after the init, places.sqlite can still be held
    EXCLUSIVE by the dying headless Firefox, failing the reconcile with
    "database is locked" — so the ready→reconcile pipeline gets `attempts`
    tries (a retry re-checks readiness first, so a roots-exist-but-was-locked
    miss heals without another engine run). The engine init itself runs at
    most ONCE per seed: a failed init has already exhausted its own budget of
    bounded enter retries, and re-running it from scratch is what stretched a
    fresh profile's first launch to minutes (#207). A single silent skip here
    is what opened the first visible window of a bookmarked profile on an
    unseeded database (#202); False means the seed is NOT in place, so the
    launch can report it — and the next launch, finding the db still rootless,
    inits again."""
    if not profile_dir:
        return True

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    places = os.path.join(profile_dir, "places.sqlite")
    init_ran = False
    for _attempt in range(attempts):
        if stopped():
            return False
        try:
            from ...models.bookmark import Bookmark
            from .firefox_bookmarks import places_ready, sync_places_bookmarks

            if not places_ready(places):
                if not bookmarks:
                    return True
                if init_ran:
                    continue
                init_ran = True
                if not _init_places_db(
                    profile_dir, seed, stop_event=stop_event, log=log
                ):
                    continue

            marks = [
                Bookmark(b.get("name", ""), b.get("url", "")) for b in bookmarks
            ]
            prev = _read_persona_bookmark_urls(profile_dir)
            if sync_places_bookmarks(places, marks, prev_persona_urls=prev):
                _write_persona_bookmark_urls(
                    profile_dir, {bm.url for bm in marks}
                )
                return True
        except Exception:
            pass
    return False


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
            # Render emoji as color glyphs, not tofu boxes (#170). The Linux
            # engine build defaults this list to "Noto Color Emoji, Twemoji
            # Mozilla" — neither resolves on the bundle-only font list (Noto
            # isn't bundled; Twemoji Mozilla is hidden by StealthSkipFamily),
            # so without this pref emoji fall through to .notdef tofu. The
            # engine bundles the genuine COLRv1 Segoe UI Emoji (fonts/
            # seguiemj1_60.ttf, mapped in bundle-fonts.list), so this value —
            # the stock Windows Firefox default — renders the Fluent color set
            # and matches the engine's always-Windows identity. (Flat Twemoji
            # glyphs mean the running build shipped the older engine that
            # bundles only TwemojiMozilla.ttf; a current engine draws Fluent.)
            "font.name-list.emoji": "Segoe UI Emoji, Twemoji Mozilla",
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
    # A seed that didn't land after its bounded retries is reported, not
    # swallowed (#202) — but never blocks the launch itself.
    if not _seed_firefox_bookmarks(
        profile_dir, cfg.get("bookmarks", []), seed, stop_event, log=emit
    ) and cfg.get("bookmarks"):
        emit("BOOKMARK_SEED_FAILED: opening without the profile's bookmarks")
    # The headless init above persists the engine's window-hiding pref into
    # prefs.js; left there, THIS launch's window comes up DWM-cloaked —
    # running but invisible (#142). Must run after the seeding, before the
    # launch.
    _scrub_headless_cloak_prefs(profile_dir)
    # A zoom rule in the profile's userChrome.css skews the page render (#206,
    # see _scrub_chrome_zoom_css). Removed before the launch so the first
    # window paints clean.
    _scrub_chrome_zoom_css(profile_dir)
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
    # When a downloaded build newer than the package's pinned one is active,
    # point the engine at it — its own ensure_binary() only knows the pinned
    # build. None → the engine resolves as usual.
    _binary = _binary_path_override()
    if _binary:
        kwargs["binary_path"] = _binary
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
        # Render at the HOST's display scale (layout.css.devPixelsPerPx = host
        # dpr) so the browser chrome AND page content are drawn at a readable
        # size on a HiDPI display, scaled together by the engine — no content
        # zoom, so nothing overflows its layout boxes. On this engine that one
        # pref moves the render scale, window.devicePixelRatio AND the CSS
        # resolution media queries TOGETHER (live-proven on the engine: at 1.5,
        # devicePixelRatio=1.5, matchMedia('(resolution:1.5dppx)') and
        # '(resolution:144dpi)' true, '(resolution:1dppx)' false). screen.* stays
        # the chosen resolution in CSS px, so a scanner reads an honest scaled
        # HiDPI monitor — screen 2560 at dpr 1.5, physical 2560*1.5 — a coherent
        # device, NOT the #187 "4K tell" (which was screen*dpr with an unrelated
        # chosen res). Only zoom.stealth.screen.width/height are read by xul.dll
        # for JS screen.*; screen.dpr in the pin is not read for devicePixelRatio,
        # so it's kept equal to the render dpr purely for coherence.
        dpr = _system_dpr()
        # With no_viewport Firefox owns the window size; seed a fresh profile's
        # first window so it doesn't open at Firefox's own default. Cap it to the
        # spoofed screen so the CSS innerWidth/Height (physical / dpr) never
        # exceed screen.* — a window wider than its own screen is impossible for a
        # real browser (#216).
        _seed_window_size(profile_dir, screen=(w, h), dpr=dpr)
        # Firefox has NO --width/--height CLI flags (those are chromium's); a
        # patched-Firefox launch that received them treated the leftover as a URL
        # and opened a bogus "0.0.9.51" page. Size comes ONLY from the profile's
        # xulstore.json (seeded above), never from extra_args.
        emit(f"HIDPI_DEBUG chosen={w}x{h} window=capped dpr={dpr}")
        kwargs["pin"] = {
            "screen.width": w,
            "screen.height": h,
            "screen.avail_width": w,
            "screen.avail_height": h - 40,
            "screen.dpr": dpr,
        }
        kwargs["extra_prefs"]["layout.css.devPixelsPerPx"] = str(dpr)
        # The FIRST paint uses the value already in prefs.js — the user.js
        # override only kicks in a beat later, and the headless places init
        # leaves the SAMPLED profile's dpr there. A mismatch between the prefs.js
        # value at first paint and the user.js value applied just after flips the
        # window scale mid-open (the initial skew). Carry the same value in
        # prefs.js before Firefox starts so there's no flip.
        _upsert_prefs_js(
            profile_dir, {"layout.css.devPixelsPerPx": str(dpr)}
        )
    if proxy:
        kwargs["proxy"] = proxy
        # Firefox owns its own proxy sockets (playwright's native proxy), so the
        # bridge's SO_KEEPALIVE can't cover them. Turn on Firefox's TCP keepalive
        # so a silent half-open proxy circuit (Tor wedges: socket open, no bytes,
        # no EOF) is detected and dropped instead of leaving a long-lived stream
        # — a Sheets collab websocket — hung on "Working" (#184). idle_time is in
        # seconds; the default is off for content sockets over a proxy.
        kwargs["extra_prefs"]["network.tcp.keepalive.enabled"] = True
        kwargs["extra_prefs"]["network.tcp.keepalive.idle_time"] = 30
        kwargs["extra_prefs"]["network.tcp.keepalive.retry_interval"] = 10
        kwargs["extra_prefs"]["network.tcp.keepalive.probe_count"] = 4
    # A profile with no explicit locale must still present a consistent one, or
    # firefox-17 leaves the header/JS/Intl at the host OS locale (uk-UA on a
    # Ukrainian host) — a masking tell. Default an unset locale to en-US so the
    # Accept-Language header, navigator.language, and Intl all agree.
    locale = cfg.get("locale") or "en-US"
    timezone = cfg.get("timezone", "")
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

    # firefox-17 leaks the host OS locale through navigator.language even when the
    # Accept-Language header is the intended locale (the header follows
    # intl.accept_languages, navigator.language does not on this build). Pin the JS
    # getters to the same locale the header carries so a scanner sees one
    # consistent language, not a header/JS mismatch (masking). Live-proven: without
    # this a US-proxy profile on a Ukrainian host reported navigator.language=uk-UA
    # while the header was en-US.
    # Mirror the effective locale (en-US when the profile leaves it unset) so the
    # override always runs — an unset-locale profile otherwise kept the host Intl.
    _lang = cfg.get("locale") or "en-US"
    on_ctx(lambda: ctx.add_init_script(_language_override_script(_lang)))

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

    # Publish an eval hook so the MCP browser tools can drive this FF session
    # (FF has no CDP). Runs JS on the live page through on_ctx, which marshals
    # onto the worker that owns the thread-affine ctx. Bounded so a wedged page
    # can't hang the MCP request forever.
    def _ff_eval(expr: str):
        def _do():
            page = ctx.pages[0] if ctx.pages else None
            if page is None:
                return None
            return page.evaluate(expr)
        return on_ctx(_do)

    def _ff_goto(url: str):
        def _do():
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="commit", timeout=30000)
            return {"url": page.url}
        return on_ctx(_do)

    if name:
        register_ff_eval(name, {"eval": _ff_eval, "goto": _ff_goto})

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
    # Diagnostic lifecycle log (#169): a Firefox profile sometimes died with no
    # trace in the Activity Log. Emit WHY the close-watch decided closed, and
    # every teardown/kill with its reason, so a silent death is traceable. These
    # lines reach the activity log via the launcher's log_callback.
    tracked_pids = None
    if in_thread:
        tracked_pids = _thread_close_watch(
            profile_dir, closed, stop_event, stop_gracefully, log=emit
        )
    else:
        tracked_pids = _fork_close_watch(profile_dir, closed, log=emit)

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
    unregister_ff_eval(cfg.get("profile_name", ""))
    emit(f"LIFECYCLE teardown-kill pids={sorted(tracked_pids or ())} "
         f"rescan={not tracked_pids}")
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
                        no_process_timeout=60.0, interval=1.0, log=None):
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
    close. When no process is confidently seen within `no_process_timeout`,
    the watch falls back to the engine's visible windows (the pid resolve can
    be broken while the session lives, #203): a window in sight keeps the
    watch alive and its sustained disappearance closes; only no-pid AND
    no-window-ever is a dead launch, given up so a half-failed launch can't
    wedge the profile "running" forever.

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
    engine_window_seen = False
    engine_window_gone = 0
    # macOS has no window enumeration (_pids_have_visible_window → None) and the
    # X-closed parent lingers, so neither window-gone nor pid-death fires — the
    # close went undetected (#168 on Mac). The content/tab procs DO die on close;
    # track a debounced drop to 0 as the close signal where the window has no
    # verdict.
    content_seen = False
    content_gone = 0
    deadline = time.monotonic() + no_process_timeout
    def say(msg):
        if log:
            log(msg)

    while not closed.wait(interval):
        if stop_event is not None and stop_event.is_set():
            say(f"LIFECYCLE close=stop-requested pids={sorted(pids or ())}")
            stop_gracefully()
            return pids
        if pids is not None:
            if not any(_pid_alive(p) for p in pids):
                say(f"LIFECYCLE close=all-pids-exit pids={sorted(pids)}")
                return pids  # every tracked Firefox process exited
            visible = _pids_have_visible_window(pids)
            if visible:
                window_seen = True
                gone_streak = 0
            elif visible is False and window_seen:
                gone_streak += 1
                if gone_streak >= no_window_streak:
                    say(f"LIFECYCLE close=window-gone pids={sorted(pids)} "
                        f"streak={gone_streak}")
                    return pids  # the window the user saw is gone → they closed it
            # None (no verdict) leaves the streak unchanged — neither a sighting
            # nor a confident miss.
            if visible is None:
                # No window enumeration (macOS): fall back to the content/tab
                # process count, which drops to 0 when the user closes the window
                # even though the parent lingers on shutdown blockers. Debounced
                # (a transient 0 between navigations doesn't close, like the
                # window-gone #159 streak).
                n = _firefox_content_proc_count(profile_dir, parent=next(iter(pids)))
                if n:
                    content_seen = True
                    content_gone = 0
                elif n == 0 and content_seen:
                    content_gone += 1
                    if content_gone >= no_window_streak:
                        say(f"LIFECYCLE close=content-procs-gone pids={sorted(pids)} "
                            f"streak={content_gone}")
                        return pids
            continue
        found = _profile_firefox_pids(profile_dir)
        if found:
            pids = found
            say(f"LIFECYCLE watch-pids pids={sorted(pids)}")
            continue
        if found is None:
            # No verdict from the WMI scan; the single-pid helper is a second
            # chance (and the only resolver on macOS, which has no WMI).
            p = _firefox_pid(profile_dir)
            if p is not None:
                pids = {p}
                say(f"LIFECYCLE watch-pids pids={sorted(pids)}")
                continue
        if time.monotonic() <= deadline:
            continue
        # Deadline passed with no pid ever resolved. That alone is NOT a dead
        # launch: BROWSER_STARTED already fired, and the pid resolve can be
        # broken while the user works in the window — returning here tore a
        # healthy session down mid-use (#203). Fall back to watching the
        # engine's own visible windows: close on a sustained window-gone
        # (the #159 debounce), give up only when no window was ever in sight.
        visible = _any_firefox_window_visible()
        if visible:
            if not engine_window_seen:
                say("LIFECYCLE watch-window (pid unresolved, watching windows)")
            engine_window_seen = True
            engine_window_gone = 0
        elif visible is False and engine_window_seen:
            engine_window_gone += 1
            if engine_window_gone >= no_window_streak:
                say(f"LIFECYCLE close=window-gone pids=[] "
                    f"streak={engine_window_gone} (pid never resolved)")
                return pids
        elif not engine_window_seen:
            # Never confidently saw a process OR a window → a dead launch;
            # give up so it can't wedge the profile "running" forever.
            say("LIFECYCLE close=no-process-timeout (launch never resolved a pid)")
            return pids
        # visible is None with a window seen before: no verdict — neither a
        # sighting nor a confident miss, same as the profile-scoped poll.
    say(f"LIFECYCLE close=closed-event pids={sorted(pids or ())}")
    return pids


def _firefox_content_proc_count(profile_dir: str, parent: int = None):
    """How many Firefox content/tab processes (`-contentproc -isForBrowser`)
    belong to THIS profile's browser (Linux/macOS), or None when the scan
    can't run.

    A persistent-context Firefox renders each open tab in an `-isForBrowser`
    content process. Those exist iff a browser window is open: closing the
    window kills every one of them within ~1s (live-measured #168: 6 → 0),
    while the launcher parent lingers on shutdown blockers. The content procs
    don't carry the profile dir on their command line, so they're matched to
    this profile by descending from the profile's launcher parent.

    `parent` is the launcher pid the close-watch already resolved once; passing
    it avoids re-running _firefox_pid (a pgrep name+cmdline match) every poll.
    That re-resolve is a #168 intermittency source: mid-shutdown pgrep can miss
    the parent for a beat and return None (no verdict), so the window-gone
    signal is lost on that poll and, depending on timing, the whole close. The
    tracked pid is stable until the parent dies (checked separately), so the
    tree walk stays anchored."""
    if _platform.IS_WINDOWS:
        return None
    if parent is None:
        parent = _firefox_pid(profile_dir)
    if parent is None:
        return None
    tree = _descendant_pids(parent)
    if tree is None:
        return None  # pgrep unavailable — no verdict
    count = 0
    for p in tree:
        cmd = _proc_cmdline(p)
        if cmd is not None and b"-isForBrowser" in cmd:
            count += 1
    return count


def _descendant_pids(root: int):
    """Every descendant pid of `root` via a pgrep -P tree walk (a single
    pgrep -P misses grandchildren, e.g. content procs under a forkserver
    child), or None when pgrep can't run — no verdict."""
    tree = set()
    frontier = [root]
    while frontier:
        nxt = []
        for p in frontier:
            try:
                out = subprocess.check_output(["pgrep", "-P", str(p)], text=True)
            except subprocess.CalledProcessError:
                continue  # no children of p
            except Exception:
                return None
            for x in out.split():
                if x.isdigit() and int(x) not in tree:
                    tree.add(int(x))
                    nxt.append(int(x))
        frontier = nxt
    return tree


def _proc_cmdline(pid: int):
    """The command line of `pid` as bytes, or None when unreadable (the process
    exited). Reads /proc/<pid>/cmdline on Linux; macOS has no /proc, so it reads
    the command through `ps` there (without which every content-proc match failed
    and the macOS X-close went undetected, #168)."""
    if _platform.IS_LINUX:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ")
        except OSError:
            return None
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
    except Exception:
        return None
    out = out.strip()
    return out.encode("utf-8", "replace") if out else None


def _forked_firefox_alive():
    """Whether this fork child still has a live Firefox among its own
    descendants (Linux fork path), or None when the scan can't run.

    The fork child launched exactly one browser, so any firefox in its
    process tree is THIS profile's — a liveness verdict that doesn't depend
    on the profile-dir cmdline match, which can be broken while the session
    lives (#203). The engine binary path always carries "firefox"
    (.../firefox-NN/firefox)."""
    if _platform.IS_WINDOWS:
        return None
    tree = _descendant_pids(os.getpid())
    if tree is None:
        return None
    for p in tree:
        cmd = _proc_cmdline(p)
        if cmd is not None and b"firefox" in cmd:
            return True
    return False


def _fork_close_watch(profile_dir, closed, no_process_timeout=60.0, interval=1.0,
                      log=None):
    """Wait until this profile's Firefox window closed or STOP is requested —
    the Linux fork-path close signal.

    ctx.pages cannot be the signal: the context is created on
    _enter_with_timeout's launch thread, and the sync API's dispatcher
    greenlet only pumps events during calls made on THAT thread — polled from
    the child's own thread, ctx.pages is a frozen snapshot that never drops
    to 0, so an X-close was never detected and the profile stayed "running"
    with no close in the log (#143).

    Parent-pid death alone is NOT a reliable close signal: under the Playwright
    juggler pipe an X-close does NOT quit Firefox — it runs the full async
    shutdown whose blockers hold the PARENT process alive ~60-90s (live-measured
    #168: after the window closed the parent lingered 65s, so the card stayed
    "running" a full minute). toolkit.shutdown.fastShutdownStage=3 _exits the
    parent ~2s after close on SOME hosts but NOT reliably (live-measured: the
    same profile X-closed 8 times died in 0.4s some runs and lingered tens of
    seconds others — the #168 "раз через раз" intermittency). So the parent's
    death time is NOT a dependable signal; the WINDOW closing is. Every one of
    the profile's `-isForBrowser` content/tab processes exits within ~1s of the
    window closing regardless of how long the parent lingers (live-measured
    #168: 6 → 0 in one poll while the parent hung on for another 65s). The close
    is decided by that content-proc count dropping to zero after it was seen —
    the Linux window-gone signal — and the caller then force-kills the lingering
    parent's tree, so detection never waits on the variable shutdown.

    The tracked parent pid is resolved ONCE and reused for the content-proc tree
    walk (see _firefox_content_proc_count(parent=...)): re-resolving it per poll
    let a transient pgrep miss return None and drop the window-gone signal for a
    beat — part of the intermittency. A close needs `gone_streak_needed`
    consecutive zero-content polls so a single pgrep hiccup can't tear a LIVE
    session down (#169: never kill a working profile). Returns the tracked pid
    set (None if never seen) so the caller can force-kill the lingering parent
    (+tree) after the polite teardown."""
    def say(msg):
        if log:
            log(msg)

    pid = None
    content_seen = False
    gone_streak = 0
    gone_streak_needed = 2
    tree_seen = False
    tree_gone = 0
    deadline = time.monotonic() + no_process_timeout
    while not closed.wait(interval):
        if pid is not None:
            if not _pid_alive(pid):
                say(f"LIFECYCLE close=parent-pid-exit pid={pid}")
                return {pid}  # parent exited (crash / fast shutdown fired)
            content = _firefox_content_proc_count(profile_dir, parent=pid)
            if content:
                content_seen = True
                gone_streak = 0
            elif content == 0 and content_seen:
                # The window's content processes are gone after we saw them:
                # the user closed the window. The parent may still be winding
                # down its shutdown blockers — don't wait it out; the caller
                # force-kills the tracked parent's tree right after this returns.
                gone_streak += 1
                if gone_streak >= gone_streak_needed:
                    say(f"LIFECYCLE close=window-gone pid={pid} "
                        f"streak={gone_streak}")
                    return {pid}
            # content is None (pgrep couldn't run) carries no verdict — leave
            # the streak untouched, same as the thread path's no-window
            # no-verdict.
            continue
        pid = _firefox_pid(profile_dir)
        if pid is not None:
            say(f"LIFECYCLE watch-pid pid={pid}")
            continue
        if time.monotonic() <= deadline:
            continue
        # Deadline passed with no pid resolved — NOT a dead launch by itself:
        # the profile-dir cmdline match can be broken while the session lives
        # (#203). The fork child launched exactly one browser, so its own
        # process tree is a profile-scoped liveness verdict: firefox among
        # the descendants keeps the watch alive; its sustained disappearance
        # closes; only no-pid AND no-firefox-ever gives up.
        alive = _forked_firefox_alive()
        if alive:
            if not tree_seen:
                say("LIFECYCLE watch-tree (pid unresolved, watching process tree)")
            tree_seen = True
            tree_gone = 0
        elif alive is False and tree_seen:
            tree_gone += 1
            if tree_gone >= gone_streak_needed:
                say(f"LIFECYCLE close=window-gone pid=None "
                    f"streak={tree_gone} (pid never resolved)")
                return None
        elif not tree_seen:
            say("LIFECYCLE close=no-process-timeout (launch never resolved a pid)")
            return None
    say(f"LIFECYCLE close=stop-requested pid={pid}")
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


def _win_path_needle(path: str) -> str:
    """A Windows path as it appears on a command line, for substring matching:
    lowercased with forward slashes folded to backslashes. profile_dir can
    carry a forward slash (expanduser keeps the '/' of "~/.persona"), while
    the engine normalizes it through pathlib before it reaches firefox.exe's
    command line — a raw substring match therefore never hit, the watch
    resolved NO pid for a live session, and the no-process give-up tore the
    working browser down (#203)."""
    return path.replace("/", "\\").lower()


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
        needle = _win_path_needle(profile_dir)
        matched = {
            pid for pid, cl in entries if cl and needle in _win_path_needle(cl)
        }
        if matched or all(cl is not None for _pid, cl in entries):
            return matched
    try:
        pat = _ps_single_quote("*" + profile_dir.replace("/", "\\") + "*")
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


def _any_firefox_window_visible():
    """Whether ANY engine-launched Firefox owns a visible top-level window
    (Windows). True/False are confident verdicts; None means no verdict
    (non-Windows, or a failed scan/enumeration).

    The no-process-timeout guard: when the profile-scoped pid resolve is
    broken, profile scoping is by definition unavailable — this is the widest
    honest check that a session the user can see is still alive, so the watch
    never declares a dead launch under an open window (#203). Only juggler
    parents count: the user's own Firefox is not an engine session, and
    counting it would keep a genuinely dead launch "running"."""
    entries = _win_firefox_command_lines()
    if entries is None:
        return None
    parents = {pid for pid, cl in entries if cl and "-juggler-pipe" in cl}
    if not parents:
        return False
    visible = _visible_window_pids()
    if visible is None:
        return None
    return bool(visible & parents)


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
            # free of extra packages. The real command line holds the pathlib-
            # normalized (backslash) dir — fold separators like _win_path_needle.
            pat = _ps_single_quote("*" + profile_dir.replace("/", "\\") + "*")
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
