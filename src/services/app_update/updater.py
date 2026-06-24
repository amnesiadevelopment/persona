"""persona self-update.

Checks the project's GitHub releases for a newer build of the app itself,
downloads the Linux binary, swaps it in, and restarts. Only meaningful for a
frozen (PyInstaller) build; running from source updates via git instead.

APP_REPO is empty until the project is published; until then the check is a
no-op and never reaches the network.
"""

import os
import sys
import urllib.request

from ..engine.updater import is_newer

APP_VERSION = "0.1.0"
APP_REPO = ""  # e.g. "user/persona" once published


def releases_api() -> str:
    if not APP_REPO:
        return ""
    return f"https://api.github.com/repos/{APP_REPO}/releases/latest"


def update_available(latest: str, current: str = APP_VERSION) -> bool:
    return is_newer(latest, current)


def pick_asset(assets: list[dict]) -> str:
    """Pick the Linux binary download URL from a release's assets."""
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith("-linux") or name == "persona":
            return asset.get("browser_download_url", "")
    return ""


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def check_for_update(timeout: int = 15) -> tuple[str, str]:
    """Return (tag, download_url) when a newer release exists, else ('','').

    No-op (returns empty) when APP_REPO is unset, so it is safe to call before
    the project is published.
    """
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
    """Download a new binary next to the current one. Returns the staged path
    or '' on failure.
    """
    if not url or not is_frozen():
        return ""
    current = sys.executable
    staged = current + ".new"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(
            staged, "wb"
        ) as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        os.chmod(staged, 0o755)
        return staged
    except Exception:
        if os.path.exists(staged):
            try:
                os.remove(staged)
            except OSError:
                pass
        return ""


def apply_and_restart(staged: str) -> None:
    """Replace the running binary with the staged one and re-exec. On Linux the
    running executable can be replaced while in use (the open inode survives).
    """
    if not staged or not os.path.exists(staged) or not is_frozen():
        return
    current = sys.executable
    os.replace(staged, current)
    os.execv(current, [current, *sys.argv[1:]])
