"""Windows code-only fast update (#205).

Most persona releases change only the app's own Python code — shipped inside the
flet bundle as data/flutter_assets/app/app.zip (~1MB), which flet re-extracts to
%APPDATA%/persona/persona/flet/app on launch whenever app.zip's sha256
(app.zip.hash) differs from the extracted .hash marker. The 218MB runtime the
Inno installer reinstalls (Python, Flutter, site-packages) is UNCHANGED between
those releases. So a code-only update just swaps app.zip + app.zip.hash in the
install dir and relaunches — seconds instead of the ~30s Inno reinstall — while
the full installer stays the fallback for releases that DID change the runtime or
dependencies (CI marks those requires_full_install in the manifest).

Windows only: macOS (.dmg) and Linux (.AppImage) updates are already fast and are
left untouched.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

from ...core import platform as _platform

MANIFEST_ASSET = "update-manifest.json"
APP_ZIP_ASSET = "app.zip"
APP_ZIP_HASH_ASSET = "app.zip.hash"


def parse_manifest(body: str) -> "dict | None":
    """Parse the release's update-manifest.json, or None on any malformed input."""
    if not body:
        return None
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def should_fast_update(manifest: "dict | None", current: str) -> bool:
    """True when a code-only fast update is safe for this release: the manifest
    exists, targets a NEWER version, does NOT require a full install (runtime /
    dependencies unchanged), and carries the app.zip checksum to verify."""
    if not manifest:
        return False
    from .updater import is_newer

    version = str(manifest.get("version", ""))
    if not version or not is_newer(version, current):
        return False
    if manifest.get("requires_full_install", True):
        return False
    if not manifest.get("app_zip_sha256"):
        return False
    return True


def _install_root() -> str:
    """The installed persona directory (%LOCALAPPDATA%/persona) — the parent of
    the flet build tree. In a flet build sys.executable IS persona.exe at that
    root; fall back to the per-user install location."""
    exe = sys.executable or ""
    if exe.lower().endswith("persona.exe"):
        return os.path.dirname(exe)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return os.path.join(local, "persona")
    return ""


def install_app_zip_paths() -> "tuple[str | None, str | None]":
    """(app.zip, app.zip.hash) inside the install tree, or (None, None) when the
    flet asset layout isn't found (e.g. a source run)."""
    root = _install_root()
    if not root:
        return None, None
    app_dir = os.path.join(root, "data", "flutter_assets", "app")
    zip_path = os.path.join(app_dir, APP_ZIP_ASSET)
    hash_path = os.path.join(app_dir, APP_ZIP_HASH_ASSET)
    if os.path.isfile(zip_path) and os.path.isfile(hash_path):
        return zip_path, hash_path
    return None, None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _asset_url(assets: list, name: str) -> str:
    for a in assets:
        if a.get("name", "") == name:
            return a.get("browser_download_url", "")
    return ""


def manifest_and_appzip_urls(assets: list) -> "tuple[str, str]":
    """(manifest_url, app_zip_url) from a release's asset list; '' for a missing
    one. The app.zip.hash is derived from the downloaded zip, not fetched."""
    return _asset_url(assets, MANIFEST_ASSET), _asset_url(assets, APP_ZIP_ASSET)


def can_fast_update() -> bool:
    """True only on an installed Windows build whose flet app.zip we can swap."""
    if not _platform.IS_WINDOWS:
        return False
    zip_path, hash_path = install_app_zip_paths()
    return bool(zip_path and hash_path)


def _write_appzip_swap_bat(exe: str, new_zip: str, new_hash: str,
                           dst_zip: str, dst_hash: str, old_pid: int) -> str:
    """A temp .bat that waits for THIS persona to exit, swaps the new app.zip +
    app.zip.hash into the install dir, and relaunches persona. flet re-extracts
    the new code on the next boot (its hash bookkeeping sees the changed
    app.zip.hash). Mirrors the full-installer relauncher's discipline: swap only
    after the holder is gone (errno-32 avoidance, #195), bounded polls, self-
    delete."""
    image = os.path.basename(exe)
    # flet re-extracts app.zip into this dir on the next boot when app.zip.hash
    # changes. If a straggler handle still holds the OLD extraction, that
    # delete-and-reextract hits errno-32 and the new persona comes up white (#195).
    # Purge it in the swap .bat AFTER every holder is gone — mirroring the
    # full-installer relauncher (updater.py) which the fast path skipped.
    flet_app = r"%APPDATA%\persona\persona\flet\app"
    checks = ""
    if old_pid:
        checks += (
            f'tasklist /FI "PID eq {old_pid}" 2>nul | find "{old_pid}" >nul\r\n'
            "if not errorlevel 1 goto hold\r\n"
        )
    checks += (
        f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH 2>nul'
        f' | find /I "{image}" >nul\r\n'
        "if not errorlevel 1 goto hold\r\n"
    )
    content = (
        "@echo off\r\n"
        'cd /d "%~dp0" >nul 2>&1\r\n'
        "set tries=0\r\n"
        "set boots=0\r\n"
        "set purges=0\r\n"
        ":wait\r\n"
        + checks +
        "goto swap\r\n"
        ":hold\r\n"
        "set /a tries+=1\r\n"
        "if %tries% geq 900 goto launch\r\n"
        "ping -n 1 127.0.0.1 >nul\r\n"
        "goto wait\r\n"
        ":swap\r\n"
        # copy the new code + hash over the install-dir originals. /Y overwrites.
        f'copy /Y "{new_zip}" "{dst_zip}" >nul 2>&1\r\n'
        f'copy /Y "{new_hash}" "{dst_hash}" >nul 2>&1\r\n'
        # Purge the stale flet extraction so the new persona re-extracts cleanly
        # instead of racing a straggler handle on delete-and-reextract (#195).
        ":purge\r\n"
        f'if not exist "{flet_app}" goto launch\r\n'
        f'rd /s /q "{flet_app}" >nul 2>&1\r\n'
        f'if not exist "{flet_app}" goto launch\r\n'
        "set /a purges+=1\r\n"
        "if %purges% geq 60 goto launch\r\n"
        "ping -n 1 127.0.0.1 >nul\r\n"
        "goto purge\r\n"
        ":launch\r\n"
        f'start "" /D "{os.path.dirname(exe)}" "{exe}"\r\n'
        # After start, the new persona re-extracts app.zip behind its boot
        # screen before persona.exe registers in tasklist — several seconds. A
        # near-instant recheck sees "not running yet" and re-launches, spawning a
        # SECOND instance that races the extraction; one wins, the other dies →
        # "reopened then closed again quickly" (#229). Wait a real ~3s beat
        # BEFORE the confirm, use a SEPARATE bounded counter (not the wait loop's
        # `tries`, which is already near its cap), and only re-launch a handful
        # of times — a genuine failure shouldn't spam processes.
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH 2>nul'
        f' | find /I "{image}" >nul\r\n'
        "if not errorlevel 1 goto done\r\n"
        "set /a boots+=1\r\n"
        "if %boots% lss 5 goto launch\r\n"
        ":done\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )
    fd, path = tempfile.mkstemp(prefix="persona-fastswap-", suffix=".bat")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
            f.write(content)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _download_small(url: str, dst: str, attempts: int = 5) -> bool:
    """Fetch a small asset (app.zip is ~1MB) to dst via curl, resumable across a
    dropped connection. True once the file is on disk with non-zero size."""
    if not url:
        return False
    try:
        os.remove(dst)
    except OSError:
        pass
    for _ in range(attempts):
        cmd = [
            "curl", "-fsSL",
            "--connect-timeout", "15", "--max-time", "180",
            "-C", "-", "-o", dst, url,
        ]
        try:
            rc = subprocess.run(
                cmd, capture_output=True, **_platform.no_window_kwargs()
            ).returncode
        except FileNotFoundError:
            return False
        have = os.path.getsize(dst) if os.path.exists(dst) else 0
        if (rc == 0 or rc in (33, 36)) and have > 0:
            return True
    return os.path.exists(dst) and os.path.getsize(dst) > 0


def apply_code_only_and_restart(app_zip_url: str, expected_sha256: str, log=None):
    """Download the new app.zip, verify its sha256 against the manifest, stage it
    + a freshly-computed app.zip.hash, then spawn the swap-and-relaunch .bat and
    exit. Returns False (current version intact) on any failure BEFORE the swap;
    does not return on success (the process exits so the .bat can swap the files).

    The swap itself runs from the .bat AFTER this persona exits (the file can't be
    replaced while flet holds it — errno-32, #195). flet re-extracts the new code
    on the next boot behind the boot screen."""

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    if not _platform.IS_WINDOWS:
        return False
    dst_zip, dst_hash = install_app_zip_paths()
    if not dst_zip or not dst_hash:
        say("Fast update: install layout not found; using the full installer.")
        return False
    from .updater import _installed_windows_exe, _relaunch_env

    exe = _installed_windows_exe()
    if not exe:
        say("Fast update: couldn't locate persona.exe; using the full installer.")
        return False

    say("Update: downloading the code update…")
    staged_zip = os.path.join(tempfile.gettempdir(), "persona-fast-app.zip")
    if not _download_small(app_zip_url, staged_zip):
        say("Fast update: download failed; using the full installer.")
        return False

    actual = ""
    try:
        actual = sha256_file(staged_zip)
    except OSError:
        pass
    if not actual or actual != expected_sha256.lower():
        say("Fast update: checksum mismatch; using the full installer.")
        try:
            os.remove(staged_zip)
        except OSError:
            pass
        return False

    # the hash file flet reads is exactly the zip's sha256
    staged_hash = os.path.join(tempfile.gettempdir(), "persona-fast-app.zip.hash")
    try:
        with open(staged_hash, "w", encoding="ascii", newline="") as f:
            f.write(actual)
    except OSError:
        return False

    try:
        bat = _write_appzip_swap_bat(
            exe, staged_zip, staged_hash, dst_zip, dst_hash, os.getpid()
        )
    except Exception as e:
        say(f"Fast update: couldn't stage the swap ({e}); using the full installer.")
        return False

    try:
        subprocess.Popen(
            ["cmd", "/c", bat],
            close_fds=True,
            env=_relaunch_env(),
            cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        say(f"Fast update: couldn't schedule the swap ({e}); keeping current.")
        return False
    say("Update: restarting…")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
