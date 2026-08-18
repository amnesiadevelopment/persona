"""Install the invisible_playwright (patched Firefox 150) engine.

Download (resumably, over Tor), sha256-verify, extract, mark complete, prune
superseded builds, and report which build is active — everything about getting
an engine build onto disk. `invisible_launch` re-exports every name here, so
existing `invisible_launch.<name>` imports and call sites keep resolving.
"""

import json
import os
import sys
import time

from ...core.logging import get_logger

logger = get_logger("browser.invisible")


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
        pinned_num = build_number(BINARY_VERSION)
        out = []
        for d in root.iterdir():
            # The cache dir is named after the tag, but the newer engine package
            # appends the upstream version + a timestamp
            # (firefox-18_151.0_20260724001829). Canonicalise to the firefox-NN
            # tag so a build in such a dir is recognised (#234) and matches the
            # short BINARY_VERSION the package reports.
            num = build_number(d.name)
            if num < 0:
                continue
            # Never surface a build NEWER than the bundled package can drive.
            # invisible_playwright talks to the engine over juggler, and that
            # contract changes between firefox-NN builds — even ones sharing an
            # upstream Firefox version (firefox-18 and firefox-19 are both 151.0
            # but different juggler builds). A build past pinned_num therefore
            # can't be driven by the shipped pkg and every launch fails (#405);
            # capping here makes a stranded newer install fall back to the drivable
            # pinned build. A newer engine must arrive with a persona update that
            # ships the matching driver (see engine/firefox.fetch_latest).
            if num > pinned_num:
                continue
            tag = f"firefox-{num}"
            if tag in BROKEN_VERSIONS or d.name in BROKEN_VERSIONS:
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
            if num != pinned_num:
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
    if not expected:
        # No checksum for our asset means we can't verify the archive is genuine
        # — refuse rather than install an unverifiable binary (a MITM could swap
        # it). checksums.txt always lists every published asset, so this only
        # happens on a tampered or truncated file.
        say("Firefox engine: no checksum for this build — refusing unverified install.")
        return False

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

    from ...utils.httpdl import range_opener

    opener = range_opener()

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
