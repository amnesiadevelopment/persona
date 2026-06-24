"""persona self-update.

Checks the project's GitHub releases for a newer AppImage of the app itself,
downloads it (resumable, with progress), atomically replaces the running
AppImage, and re-execs into the new version.

Only meaningful when running as a packaged AppImage (the AppImage runtime sets
$APPIMAGE). When running from source the check still reports availability but
apply_and_restart is a no-op guarded by the $APPIMAGE check.
"""

import os
import shutil
import stat
import sys
import tempfile
import urllib.request

from ..engine.updater import is_newer

APP_VERSION = "1.1.0"
APP_REPO = "amnesiadevelopment/persona"
ASSET_NAME = "persona-x86_64.AppImage"


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
    target = installed_appimage_path()
    if target is None:
        return ""
    target_dir = os.path.dirname(target)
    staged = os.path.join(target_dir, ".persona-update.AppImage.part")

    attempts = 0
    max_attempts = 40
    total = 0
    while attempts < max_attempts:
        attempts += 1
        have = os.path.getsize(staged) if os.path.exists(staged) else 0
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
                with open(staged, mode) as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
            if total and os.path.getsize(staged) < total:
                continue  # dropped early, resume
            os.chmod(staged, 0o755)
            return staged
        except Exception:
            continue  # keep partial for next resume
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
