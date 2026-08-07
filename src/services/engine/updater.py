"""fingerprint-chromium engine version check + download, per OS.

The engine lives in ENGINE_DIR. We track the installed version in
ENGINE_DIR/version.txt and compare it against the latest GitHub release. The
release ships a different asset per OS — a Linux AppImage, a Windows zip
(containing chrome.exe), and a macOS dmg — so download/install branches on the
running platform while the launcher always finds the binary at the path
platform.fingerprint_chromium_filename() resolves to.
"""

import hashlib
import json
import os
import shutil
import threading
import urllib.request

from ...core.config import ENGINE_DIR
from ...core import platform as _platform

ENGINE_BINARY = os.path.join(ENGINE_DIR, _platform.fingerprint_chromium_filename())
VERSION_FILE = os.path.join(ENGINE_DIR, "version.txt")
# Written LAST, after a whole engine is in place, so is_installed() can tell a
# complete install from a half-extracted one (a Windows zip drops chrome.exe
# and its DLLs as separate files — the binary can appear before its libraries).
MARKER_FILE = os.path.join(ENGINE_DIR, ".engine-complete")
RELEASES_API = (
    "https://api.github.com/repos/adryfish/fingerprint-chromium/releases/latest"
)

# Serialises concurrent installs (the UI update thread and ensure_engine can
# both reach download_engine) so two extracts don't race into ENGINE_DIR.
_install_lock = threading.Lock()


def current_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _binary_root() -> str:
    """The path whose presence means "the engine is installed". For Linux/Windows
    that's the executable itself; for macOS it's the .app bundle directory the
    binary lives inside (the inner Mach-O binary is what we launch, but the
    bundle is what gets extracted)."""
    if _platform.IS_MACOS:
        # ENGINE_BINARY = ENGINE_DIR/Chromium.app/Contents/MacOS/Chromium
        # the bundle root is ENGINE_DIR/Chromium.app
        return os.path.join(ENGINE_DIR, "Chromium.app")
    return ENGINE_BINARY


def _install_complete() -> bool:
    """True when a whole engine finished installing. The completion marker is
    written last by download_engine; an engine installed before the marker
    existed has version.txt (also written last, by ensure_engine), so accept
    that as the legacy completion signal rather than force a re-download."""
    return os.path.exists(MARKER_FILE) or os.path.exists(VERSION_FILE)


def is_installed() -> bool:
    """True when the engine binary (or macOS .app bundle) is present, non-empty,
    AND the install completed (marker/version.txt present) — so a half-extracted
    engine, where the binary is on disk but its libraries aren't, doesn't read as
    ready and get launched."""
    root = _binary_root()
    try:
        if _platform.IS_MACOS:
            present = os.path.isdir(root) and os.path.isfile(ENGINE_BINARY)
        else:
            present = os.path.getsize(root) > 0
    except OSError:
        return False
    return present and _install_complete()


def sha256_ok(data: bytes, digest: str | None, allow_missing: bool = False) -> bool:
    """Verify data against a sha256 digest. An absent digest fails closed: an
    unverifiable asset could be a MITM swap, so we refuse it — UNLESS the caller
    opts in with allow_missing (the Linux predictable-URL fallback, where the
    asset lives outside the API that carries the digest, has no other source).
    A present-but-wrong digest is always rejected.
    """
    if not digest:
        return allow_missing
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


def _asset_matches(name: str) -> bool:
    """True when a release asset filename is the one for this OS. The release
    carries several artifacts; we pick the AppImage on Linux, the Windows zip
    (chrome.exe inside), and the macOS dmg."""
    if _platform.IS_WINDOWS:
        return name.endswith("_windows_x64.zip")
    if _platform.IS_MACOS:
        return name.endswith("_macos.dmg")
    return name.endswith("x86_64.AppImage")


def appimage_url_for(tag: str) -> str:
    """Direct Linux-AppImage URL for a tag, used as a fallback when the release
    JSON doesn't list assets. Linux only — the other OSes have no stable
    predictable name (the Windows/macOS assets carry a build suffix like
    '-1.1'), so off-Linux we rely on the asset list instead."""
    return (
        f"https://github.com/adryfish/fingerprint-chromium/releases/download/"
        f"{tag}/ungoogled-chromium-{tag}-1-x86_64.AppImage"
    )


def fetch_latest_full(timeout: int = 20) -> tuple[str, str, str]:
    """Return (tag, asset_url, sha256_digest) of the latest release for THIS OS,
    or ('','','') on failure. Picks the per-OS asset.
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
            if _asset_matches(name):
                url = asset.get("browser_download_url", "")
                digest = asset.get("digest", "") or ""
                break
        if tag and not url and _platform.IS_LINUX:
            url = appimage_url_for(tag)
        return tag, url, digest
    except Exception:
        return "", "", ""


def fetch_latest(timeout: int = 20) -> tuple[str, str]:
    """Return (tag, asset_url) of the latest release, or ('','') on failure."""
    tag, url, _ = fetch_latest_full(timeout)
    return tag, url


def write_version(tag: str) -> None:
    try:
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(tag)
    except OSError:
        pass


def _download_to(
    path: str,
    url: str,
    timeout: int,
    digest: str | None,
    progress,
    allow_missing: bool = False,
) -> bool:
    """Download `url` to `path`, resuming across dropped connections (Tor), and
    verify its sha256. A missing digest fails closed (the .part is discarded and
    we return False) unless allow_missing is set — an unverifiable asset could be
    a MITM swap. Returns True only on a complete, verified file. Shared by all
    OSes — the per-OS install step then turns this raw asset into the runnable
    engine."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    attempts = 0
    max_attempts = 40
    total = 0
    while attempts < max_attempts:
        attempts += 1
        have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
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
                with open(tmp, mode) as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)
            if total and os.path.getsize(tmp) < total:
                continue  # dropped early, resume
            if digest:
                h = hashlib.sha256()
                with open(tmp, "rb") as f:
                    for blk in iter(lambda: f.read(1 << 20), b""):
                        h.update(blk)
                if h.hexdigest() != digest.split(":", 1)[-1].strip().lower():
                    os.remove(tmp)
                    return False
            elif not allow_missing:
                # No digest to verify against and the caller didn't opt in — an
                # unverifiable asset could be a swap, so refuse it.
                os.remove(tmp)
                return False
            os.replace(tmp, path)
            return True
        except Exception:
            continue  # keep the partial for the next resume attempt
    return False


def _install_linux(asset_path: str) -> bool:
    """The downloaded AppImage IS the engine; make it executable in place."""
    try:
        os.replace(asset_path, ENGINE_BINARY)
        os.chmod(ENGINE_BINARY, 0o755)
        return True
    except OSError:
        return False


def _install_windows(asset_path: str) -> bool:
    """Extract the Windows zip into ENGINE_DIR. The archive holds chrome.exe plus
    its DLLs/resources, which the launcher expects at ENGINE_DIR/chrome.exe.

    Extraction goes into a staging dir first, then the whole tree is moved into
    ENGINE_DIR — so chrome.exe never appears without the DLLs beside it, which
    would let a launch pick up a half-extracted engine (#319)."""
    import zipfile

    staging = os.path.join(ENGINE_DIR, ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        os.makedirs(staging, exist_ok=True)
        with zipfile.ZipFile(asset_path) as zf:
            members = zf.namelist()
            # The zip may nest everything under a top-level folder; find where
            # chrome.exe sits and flatten that folder into staging so the
            # launcher's ENGINE_DIR/chrome.exe path resolves after the move.
            exe_member = next(
                (m for m in members if m.replace("\\", "/").endswith("/chrome.exe")
                 or m == "chrome.exe"),
                None,
            )
            prefix = ""
            if exe_member and "/" in exe_member.replace("\\", "/"):
                prefix = exe_member.replace("\\", "/").rsplit("/", 1)[0] + "/"
            for m in members:
                norm = m.replace("\\", "/")
                if prefix and not norm.startswith(prefix):
                    continue
                rel = norm[len(prefix):] if prefix else norm
                if not rel:
                    continue
                dest = os.path.join(staging, *rel.split("/"))
                if m.endswith("/"):
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(m) as src, open(dest, "wb") as out:
                    out.write(src.read())
        if not os.path.isfile(os.path.join(staging, "chrome.exe")):
            return False
        _promote_staging(staging)
        os.remove(asset_path)
        return os.path.isfile(ENGINE_BINARY)
    except (OSError, zipfile.BadZipFile):
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _promote_staging(staging: str) -> None:
    """Move every entry from `staging` into ENGINE_DIR, replacing what's there.
    Overwriting an upgrade's old files in place (rather than emptying ENGINE_DIR
    first) keeps the window where the engine is incomplete as small as possible;
    the completion marker, written afterwards, is what actually gates launch."""
    for name in os.listdir(staging):
        src = os.path.join(staging, name)
        dst = os.path.join(ENGINE_DIR, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        elif os.path.exists(dst):
            os.remove(dst)
        shutil.move(src, dst)


def _install_macos(asset_path: str) -> bool:
    """Mount the dmg, copy Chromium.app into ENGINE_DIR, detach. Requires the
    macOS `hdiutil` tool, so this path only runs on macOS.

    ditto lands the .app in a staging path first, then it's swapped into place —
    so a launch never sees a partially-copied bundle (the previous engine stays
    whole until the new one is ready)."""
    import subprocess
    import tempfile

    mount = tempfile.mkdtemp(prefix="fpchrome-dmg-")
    staging = os.path.join(ENGINE_DIR, ".staging-Chromium.app")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        rc = subprocess.run(
            ["hdiutil", "attach", asset_path, "-nobrowse", "-mountpoint", mount],
            capture_output=True,
        ).returncode
        if rc != 0:
            return False
        app_src = None
        for entry in os.listdir(mount):
            if entry.endswith(".app"):
                app_src = os.path.join(mount, entry)
                break
        if not app_src:
            return False
        dest = os.path.join(ENGINE_DIR, "Chromium.app")
        # ditto preserves the code signature/resource forks/permissions that a
        # plain copytree drops — Gatekeeper kills an unsigned-looking .app on
        # Apple Silicon (same reason the app-updater uses ditto).
        subprocess.run(["ditto", app_src, staging], check=True)
        if os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)
        return os.path.isfile(ENGINE_BINARY)
    except OSError:
        return False
    finally:
        subprocess.run(["hdiutil", "detach", mount], capture_output=True)
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.remove(asset_path)
        except OSError:
            pass


def download_engine(
    url: str,
    timeout: int = 600,
    digest: str | None = None,
    progress=None,
    allow_unverified: bool = False,
) -> bool:
    """Download the per-OS engine asset and install it so the launcher finds the
    runnable binary at ENGINE_BINARY. `progress(done, total)` is called as bytes
    arrive. A missing digest fails closed unless allow_unverified is set (the
    Linux predictable-URL fallback, whose asset carries no digest)."""
    if not url:
        return False
    os.makedirs(ENGINE_DIR, exist_ok=True)
    # download the raw asset next to the engine dir, named after the URL so a
    # resumed .part survives restarts
    asset_name = url.rsplit("/", 1)[-1] or "engine.download"
    asset_path = os.path.join(ENGINE_DIR, asset_name)
    if not _download_to(
        asset_path, url, timeout, digest, progress, allow_missing=allow_unverified
    ):
        return False
    # Serialise the extract/move + marker: two concurrent installs into the
    # shared ENGINE_DIR would interleave and could publish a mixed tree.
    with _install_lock:
        # Clear any prior completion marker so a failed install can't leave the
        # engine reading as "complete" — is_installed() must reflect the actual
        # on-disk state until we mark success below.
        try:
            os.remove(MARKER_FILE)
        except OSError:
            pass
        if _platform.IS_WINDOWS:
            ok = _install_windows(asset_path)
        elif _platform.IS_MACOS:
            ok = _install_macos(asset_path)
        else:
            ok = _install_linux(asset_path)
        if ok:
            # Marker LAST: only a fully-installed engine reads as ready.
            try:
                with open(MARKER_FILE, "w", encoding="utf-8") as f:
                    f.write("ok")
            except OSError:
                pass
        return ok


def ensure_engine(
    progress=None, timeout: int = 600, attempts: int = 3
) -> tuple[bool, str]:
    """Make sure the engine is installed. If already present, no-op. Otherwise
    fetch the latest release and download it. Returns (ok, message).

    The GitHub releases API call and the download each occasionally fail on a
    transient network hiccup (a dropped connection, a flaky first request on a
    cold start). Retry the whole fetch+download a few times so one blip doesn't
    leave the engine uninstalled — the same treatment the Firefox engine gets.
    """
    if is_installed():
        return True, "engine present"
    last = "could not reach GitHub releases"
    for _ in range(max(1, attempts)):
        tag, url, digest = fetch_latest_full()
        if not url:
            last = "could not reach GitHub releases"
            continue
        # A missing digest is only expected on the Linux predictable-URL
        # fallback (the asset lives outside the API that carries the digest).
        # Everywhere else a missing digest means an unverifiable asset — don't
        # allow it.
        allow_unverified = not digest and _platform.IS_LINUX
        if download_engine(
            url,
            timeout=timeout,
            digest=digest,
            progress=progress,
            allow_unverified=allow_unverified,
        ):
            write_version(tag)
            return True, tag
        last = "download failed"
    return False, last
