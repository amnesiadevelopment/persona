"""persona self-update.

Checks the project's GitHub releases for a newer AppImage of the app itself,
downloads it (resumable, with progress), atomically replaces the running
AppImage, and re-execs into the new version.

Only meaningful when running as a packaged AppImage (the AppImage runtime sets
$APPIMAGE). When running from source the check still reports availability but
apply_and_restart is a no-op guarded by the $APPIMAGE check.
"""

import os
import subprocess
import sys
import threading
import time

from ..engine.updater import is_newer

APP_VERSION = "2.1.3"
APP_REPO = "amnesiadevelopment/persona"
ASSET_NAME = "persona-x86_64.AppImage"

# curl keeps a download alive over a flaky Tor circuit far better than urllib,
# which can block for the whole timeout on a dead exit. These match install.sh.
_CONNECT_TIMEOUT = 30  # give up a stalled CONNECT fast, then retry a fresh one
_SPEED_LIMIT = 1024  # bytes/s; below this for _SPEED_TIME, abort + resume
_SPEED_TIME = 30
_MAX_ATTEMPTS = 40


def staged_path() -> str:
    """Deterministic path for the in-progress download, next to the installed
    AppImage (same filesystem, so the later os.replace is atomic). '' when not
    running as a packaged AppImage."""
    target = installed_appimage_path()
    if target is None:
        return ""
    return os.path.join(os.path.dirname(target), ".persona-update.AppImage.part")


def _curl_get(url: str, headers: dict | None = None, max_time: int = 30) -> str:
    """GET a URL via curl with a short connect-timeout and a hard max-time, so a
    dead/slow Tor circuit fails fast instead of hanging the whole updater (the
    version check used urllib, whose `timeout` is per-read and would block for
    its full duration on a stalled connection — making the updater 'work through
    a router-down minute and then silently miss the new version'). Returns the
    body, or '' on any failure/timeout."""
    cmd = ["curl", "-fsSL", "--connect-timeout", "15", "--max-time", str(max_time)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=max_time + 5)
        if out.returncode != 0:
            return ""
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def remote_size(url: str, timeout: int = 30) -> int:
    """HEAD the asset to learn its size, so a resumed/staged file can be checked
    for completeness. 0 when unknown. Uses curl (-I) with a short connect
    timeout so a slow Tor circuit can't hang it."""
    if not url:
        return 0
    try:
        out = subprocess.run(
            ["curl", "-fsSLI", "--connect-timeout", "15", "--max-time", str(timeout), url],
            capture_output=True, timeout=timeout + 5,
        )
        if out.returncode != 0:
            return 0
        # GitHub releases 302-redirect to a CDN; the redirect response carries
        # "Content-Length: 0" and the REAL size is in the final response's
        # header. Take the LAST non-zero Content-Length, not the first.
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


def find_ready_staged(url: str, timeout: int = 30, size: int = 0) -> str:
    """If a fully-downloaded staged file from a previous run is already on disk
    (size matches the remote asset), return it so we can offer to restart into
    it without re-downloading. Else ''. Prefers the API-provided `size` over a
    HEAD request."""
    staged = staged_path()
    if not staged or not os.path.exists(staged):
        return ""
    total = size or remote_size(url, timeout)
    if total and os.path.getsize(staged) == total:
        return staged
    return ""


def releases_api() -> str:
    if not APP_REPO:
        return ""
    return f"https://api.github.com/repos/{APP_REPO}/releases/latest"


def update_available(latest: str, current: str = APP_VERSION) -> bool:
    return is_newer(latest, current)


def pick_asset(assets: list[dict]) -> tuple[str, int]:
    """Pick the AppImage (download_url, size) from a release's assets. The size
    comes straight from the GitHub API, so the download has an exact total
    without a separate (Tor-flaky) HEAD request."""
    for asset in assets:
        if asset.get("name", "") == ASSET_NAME:
            return asset.get("browser_download_url", ""), int(asset.get("size", 0) or 0)
    # fallback: any .AppImage
    for asset in assets:
        if asset.get("name", "").endswith(".AppImage"):
            return asset.get("browser_download_url", ""), int(asset.get("size", 0) or 0)
    return "", 0


def installed_appimage_path() -> str | None:
    """Symlink-resolved absolute path to the running AppImage, or None when not
    running as a packaged AppImage. The AppImage type-2 runtime sets $APPIMAGE
    in both FUSE and extract-and-run modes; it is absent when run from source."""
    p = os.environ.get("APPIMAGE")
    if not p or not os.path.isfile(p):
        return None
    return os.path.realpath(p)


def is_packaged_appimage() -> bool:
    return installed_appimage_path() is not None


def check_for_update(timeout: int = 30) -> tuple[str, str, int]:
    """Return (tag, download_url, size) when a newer release exists, else
    ('', '', 0). Uses curl with a short connect timeout so the check fails fast
    on a dead Tor circuit instead of hanging (and then silently missing the
    update)."""
    api = releases_api()
    if not api:
        return "", "", 0
    body = _curl_get(
        api, headers={"Accept": "application/vnd.github+json"}, max_time=timeout
    )
    if not body:
        return "", "", 0
    try:
        import json

        data = json.loads(body)
        tag = data.get("tag_name", "")
        if not update_available(tag):
            return "", "", 0
        url, size = pick_asset(data.get("assets", []))
        return tag, url, size
    except Exception:
        return "", "", 0


def download_update(url: str, timeout: int = 600, progress=None, size: int = 0) -> str:
    """Download the new AppImage to a temp file next to the installed one
    (same filesystem, so the later os.replace is atomic). Resumable across
    dropped connections (Tor). Returns the staged path or '' on failure.
    `progress(done, total)` is called as bytes arrive. `size` is the exact asset
    size from the GitHub API; we trust it over a HEAD request (which is flaky to
    impossible over Tor — that's why the bar had no total and looked stuck).
    """
    if not url:
        return ""
    staged = staged_path()
    if not staged:
        return ""

    total = size or remote_size(url)

    # Report progress by watching the staged file grow, so the UI shows the real
    # MB/speed (and "connecting…" via progress(0, total)) instead of freezing on
    # 0.0 when a Tor circuit is slow to deliver the first byte.
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            try:
                done = os.path.getsize(staged) if os.path.exists(staged) else 0
            except OSError:
                done = 0
            if progress is not None:
                progress(done, total)
            stop.wait(0.5)

    watcher = threading.Thread(target=watch, daemon=True)
    if progress is not None:
        progress(0, total)
        watcher.start()

    deadline = time.monotonic() + timeout
    try:
        for _ in range(_MAX_ATTEMPTS):
            if time.monotonic() > deadline:
                break
            cmd = [
                "curl", "-fsSL",
                "--connect-timeout", str(_CONNECT_TIMEOUT),
                "--speed-limit", str(_SPEED_LIMIT),
                "--speed-time", str(_SPEED_TIME),
                "-C", "-",            # resume from where the .part left off
                "-o", staged,
                url,
            ]
            try:
                rc = subprocess.run(cmd, capture_output=True).returncode
            except FileNotFoundError:
                break  # no curl; nothing else to try
            have = os.path.getsize(staged) if os.path.exists(staged) else 0
            # Done when the file is fully here. Several ways the last attempt can
            # leave a COMPLETE file with a non-zero rc, all of which we accept:
            #  - have >= total (the real size, now correctly read past redirects)
            #  - curl exited 33/36 (HTTP 416 Range Not Satisfiable): -C - asked
            #    to resume an already-complete file, which means it's done
            #  - total unknown but curl succeeded
            complete = bool(total) and have >= total
            range_done = rc in (33, 36) and bool(total) and have >= total
            if complete or range_done or (rc == 0 and not total and have > 0):
                if progress is not None and total:
                    progress(have, total)  # flush 100% to the UI
                os.chmod(staged, 0o755)
                return staged
            # otherwise: rc != 0 (timeout/slow/drop) or short file -> loop+resume
    finally:
        stop.set()
    return ""


def apply_and_restart(staged: str, extra_args=None, log=None) -> bool:
    """Atomically replace the running AppImage with the staged download and
    re-exec into the new version. Returns False (and leaves the staged file in
    place so it can be retried) when not applicable or a step fails; `log` (if
    given) explains why. Does not return on success (process is replaced)."""

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    target = installed_appimage_path()
    if target is None:
        say("Update: not running as an AppImage, can't self-replace.")
        return False
    if not staged or not os.path.isfile(staged):
        say("Update: staged file missing.")
        return False
    target_dir = os.path.dirname(target)
    if not os.access(target_dir, os.W_OK | os.X_OK):
        say(f"Update: {target_dir} not writable, can't replace.")
        return False
    try:
        f = os.open(staged, os.O_RDONLY)
        try:
            os.fsync(f)
        finally:
            os.close(f)
        os.replace(staged, target)  # same fs; old inode stays live while open
        os.chmod(target, 0o755)
    except Exception as e:
        say(f"Update: replacing the AppImage failed: {e}")
        return False  # keep `staged` for a retry; do NOT delete it
    # Re-exec. Preserve extract-and-run mode if we were launched that way, so a
    # missing-FUSE host (Whonix) still relaunches instead of silently failing.
    args = [target]
    cur = list(sys.argv[1:])
    if "--appimage-extract-and-run" not in cur and not os.environ.get(
        "APPIMAGE_EXTRACT_AND_RUN"
    ):
        os.environ["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    args += list(extra_args or cur)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    say("Update: restarting…")
    try:
        os.execv(target, args)
    except Exception as e:
        say(f"Update: relaunch failed: {e}")
        return False
    return False  # unreachable on success
