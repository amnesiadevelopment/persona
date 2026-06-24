"""fingerprint-chromium engine version check + update.

The engine is an AppImage in ~/fpchrome/. We track the installed version in
~/fpchrome/version.txt and compare it against the latest GitHub release.
"""

import hashlib
import hashlib
import json
import os
import urllib.request

from ...core.config import ENGINE_DIR
APPIMAGE = os.path.join(ENGINE_DIR, "fpchrome.AppImage")
VERSION_FILE = os.path.join(ENGINE_DIR, "version.txt")
RELEASES_API = (
    "https://api.github.com/repos/adryfish/fingerprint-chromium/releases/latest"
)


def current_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def is_installed() -> bool:
    """True when a non-empty engine AppImage is present."""
    try:
        return os.path.getsize(APPIMAGE) > 0
    except OSError:
        return False


def sha256_ok(data: bytes, digest: str | None) -> bool:
    """Verify data against a sha256 digest. Empty/absent digest passes
    (we don't block an install when GitHub didn't give us a checksum).
    """
    if not digest:
        return True
    want = digest.split(":", 1)[-1].strip().lower()
    return hashlib.sha256(data).hexdigest() == want


def parse_version(text: str) -> tuple[int, ...]:
    """Turn '144.0.7559.132' into a comparable tuple, ignoring junk."""
    parts = []
    for chunk in (text or "").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True when `latest` is a strictly higher version than `current`."""
    if not latest:
        return False
    if not current:
        return True
    return parse_version(latest) > parse_version(current)


def appimage_url_for(tag: str) -> str:
    return (
        f"https://github.com/adryfish/fingerprint-chromium/releases/download/"
        f"{tag}/ungoogled-chromium-{tag}-1-x86_64.AppImage"
    )


def fetch_latest_full(timeout: int = 20) -> tuple[str, str, str]:
    """Return (tag, appimage_url, sha256_digest) of the latest release, or
    ('','','') on failure. Picks the x86_64 AppImage asset.
    """
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag = data.get("tag_name", "")
        url = ""
        digest = ""
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.endswith("x86_64.AppImage"):
                url = asset.get("browser_download_url", "")
                digest = asset.get("digest", "") or ""
                break
        if tag and not url:
            url = appimage_url_for(tag)
        return tag, url, digest
    except Exception:
        return "", "", ""


def fetch_latest(timeout: int = 20) -> tuple[str, str]:
    """Return (tag, appimage_url) of the latest release, or ('','') on failure."""
    tag, url, _ = fetch_latest_full(timeout)
    return tag, url


def write_version(tag: str) -> None:
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(tag)
    except OSError:
        pass


def download_engine(
    url: str,
    timeout: int = 600,
    digest: str | None = None,
    progress=None,
) -> bool:
    """Download the AppImage, verify its sha256 (when a digest is given), and
    swap it in atomically. `progress(done, total)` is called as bytes arrive.
    """
    if not url:
        return False
    os.makedirs(ENGINE_DIR, exist_ok=True)
    tmp = APPIMAGE + ".new"
    try:
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(
            tmp, "wb"
        ) as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if digest and h.hexdigest() != digest.split(":", 1)[-1].strip().lower():
            os.remove(tmp)
            return False
        os.chmod(tmp, 0o755)
        os.replace(tmp, APPIMAGE)
        return True
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def ensure_engine(progress=None, timeout: int = 600) -> tuple[bool, str]:
    """Make sure the engine is installed. If already present, no-op. Otherwise
    fetch the latest release and download it. Returns (ok, message).
    """
    if is_installed():
        return True, "engine present"
    tag, url, digest = fetch_latest_full()
    if not url:
        return False, "could not reach GitHub releases"
    if download_engine(url, timeout=timeout, digest=digest, progress=progress):
        write_version(tag)
        return True, tag
    return False, "download failed"

