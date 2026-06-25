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
import urllib.request

from ..engine.updater import is_newer

APP_VERSION = "1.1.2"
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


def remote_size(url: str, timeout: int = 30) -> int:
    """HEAD the asset to learn its size, so a resumed/staged file can be checked
    for completeness. 0 when unknown."""
    if not url:
        return 0
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def find_ready_staged(url: str, timeout: int = 30) -> str:
    """If a fully-downloaded staged file from a previous run is already on disk
    (size matches the remote asset), return it so we can offer to restart into
    it without re-downloading. Else ''."""
    staged = staged_path()
    if not staged or not os.path.exists(staged):
        return ""
    total = remote_size(url, timeout)
    if total and os.path.getsize(staged) == total:
        return staged
    return ""


def releases_api() -> str:
    if not APP_REPO:
        return ""
    return f"https://api.github.com/repos/{APP_REPO}/releases/latest"


def update_available(latest: str, current: str = APP_VERSION) -> bool:
    return is_newer(latest, current)


def pick_asset(assets: list[dict]) -> str:
    """Pick the AppImage download URL from a release's assets."""
    for asset in assets:
        if asset.get("name", "") == ASSET_NAME:
            return asset.get("browser_download_url", "")
    # fallback: any .AppImage
    for asset in assets:
        if asset.get("name", "").endswith(".AppImage"):
            return asset.get("browser_download_url", "")
    return ""


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


def check_for_update(timeout: int = 30) -> tuple[str, str]:
    """Return (tag, download_url) when a newer release exists, else ('','')."""
    api = releases_api()
    if not api:
        return "", ""
    try:
        import json

        req = urllib.request.Request(
            api, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag = data.get("tag_name", "")
        if not update_available(tag):
            return "", ""
        return tag, pick_asset(data.get("assets", []))
    except Exception:
        return "", ""


def download_update(url: str, timeout: int = 600, progress=None) -> str:
    """Download the new AppImage to a temp file next to the installed one
    (same filesystem, so the later os.replace is atomic). Resumable across
    dropped connections (Tor). Returns the staged path or '' on failure.
    `progress(done, total)` is called as bytes arrive.
    """
    if not url:
        return ""
    staged = staged_path()
    if not staged:
        return ""

    total = remote_size(url)

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
            if rc == 0 and (not total or have >= total):
                os.chmod(staged, 0o755)
                return staged
            # rc != 0 (timeout/slow/drop) or short file: loop and resume
    finally:
        stop.set()
    return ""


def apply_and_restart(staged: str, extra_args=None) -> bool:
    """Atomically replace the running AppImage with the staged download and
    re-exec into the new version. Returns False (and leaves things intact) when
    not applicable or the install dir isn't writable. Does not return on
    success (process is replaced)."""
    target = installed_appimage_path()
    if target is None or not staged or not os.path.isfile(staged):
        return False
    target_dir = os.path.dirname(target)
    if not os.access(target_dir, os.W_OK | os.X_OK):
        return False
    try:
        # staged is already on the same fs (downloaded next to target)
        f = os.open(staged, os.O_RDONLY)
        try:
            os.fsync(f)
        finally:
            os.close(f)
        os.replace(staged, target)
    except Exception:
        try:
            os.remove(staged)
        except OSError:
            pass
        return False
    args = [target] + list(extra_args or sys.argv[1:])
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os.execv(target, args)
    return False  # unreachable on success
