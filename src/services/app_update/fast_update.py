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

import json
import os
import subprocess
import sys
import tempfile

from ...core import platform as _platform
from ...utils.httpdl import curl_download, digest_ok, sha256_file
from . import install_env, relaunch_bat

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


# The retained previous release, kept beside the live pair INSIDE the install
# dir. Same volume, so moving it aside is an atomic rename rather than a 1MB
# copy — and unlike %TEMP% it survives a reboot mid-update, which is exactly the
# window this exists for. One previous version, overwritten by each update.
RETAINED_SUFFIX = ".prev"


def retained_paths(dst_zip: str, dst_hash: str) -> "tuple[str, str]":
    """(app.zip.prev, app.zip.hash.prev) beside the live pair."""
    return dst_zip + RETAINED_SUFFIX, dst_hash + RETAINED_SUFFIX


# A file step is (kind, src, dst): "move" and "del" are conditional on src
# existing, "copy" is not (its source is the freshly staged download, which the
# caller has already verified). Steps exist as DATA rather than as batch text so
# the same sequence that renders into the .bat can be executed directly against
# a real install dir in a test — the swap runs on Windows, but what it does to
# the files is asserted here rather than taken on faith from a grep of the
# script text.
def stage_steps(new_zip: str, new_hash: str,
                dst_zip: str, dst_hash: str) -> "list[tuple[str, str, str]]":
    """Retain the live pair, then put the new pair in place.

    The retain is what makes a bad release survivable. Before this swap the
    working code exists twice — as this app.zip and as the flet extraction — and
    the script destroys both in consecutive steps (the purge is load-bearing for
    its own reason and stays). Since the updater itself ships INSIDE app.zip, a
    release that verifies but does not boot would otherwise leave no persona and
    no way to fetch a fix.
    """
    prev_zip, prev_hash = retained_paths(dst_zip, dst_hash)
    return [
        ("move", dst_zip, prev_zip),
        ("move", dst_hash, prev_hash),
        ("copy", new_zip, dst_zip),
        ("copy", new_hash, dst_hash),
    ]


def restore_steps(dst_zip: str, dst_hash: str) -> "list[tuple[str, str, str]]":
    """Put the retained pair back over the failed release.

    `move`, not `copy`: the retained pair is consumed by the restore, so a
    recovered install is left holding exactly one good pair and no leftovers.
    The hash goes back with the zip, which is what makes flet re-extract — its
    extracted marker now records the FAILED release, so a differing
    app.zip.hash is precisely the signal it acts on.
    """
    prev_zip, prev_hash = retained_paths(dst_zip, dst_hash)
    return [
        ("move", prev_zip, dst_zip),
        ("move", prev_hash, dst_hash),
    ]


def drop_retained_steps(dst_zip: str, dst_hash: str) -> "list[tuple[str, str, str]]":
    """Discard the retained pair — the new release booted, so it has earned the
    slot. Nothing accumulates across updates."""
    prev_zip, prev_hash = retained_paths(dst_zip, dst_hash)
    return [("del", prev_zip, ""), ("del", prev_hash, "")]


def render_steps_bat(steps: "list[tuple[str, str, str]]") -> str:
    """File steps as cmd lines. `move`/`del` are guarded by `if exist` so a
    missing file is a no-op rather than an error written to a console nobody is
    watching; every line is silenced and none of them can fail the script."""
    out = ""
    for kind, src, dst in steps:
        if kind == "move":
            out += f'if exist "{src}" move /Y "{src}" "{dst}" >nul 2>&1\r\n'
        elif kind == "copy":
            out += f'copy /Y "{src}" "{dst}" >nul 2>&1\r\n'
        elif kind == "del":
            out += f'if exist "{src}" del /F /Q "{src}" >nul 2>&1\r\n'
    return out


def _write_appzip_swap_bat(exe: str, new_zip: str, new_hash: str,
                           dst_zip: str, dst_hash: str, old_pid: int) -> str:
    """A temp .bat that waits for THIS persona to exit, swaps the new app.zip +
    app.zip.hash into the install dir, and relaunches persona. flet re-extracts
    the new code on the next boot (its hash bookkeeping sees the changed
    app.zip.hash).

    Only the WAIT set and the swap itself are this path's own; the wait loop,
    the flet-extraction purge, the launch-and-confirm and the self-delete come
    from the shared generator (relaunch_bat) that the full installer's
    relauncher also uses. That sharing is the point: this emitter used to be a
    hand-maintained near-clone, and #195 was fixed in the installer's copy while
    "the fast path skipped" it.
    """
    checks = relaunch_bat.pid_check(old_pid) + relaunch_bat.image_check(
        os.path.basename(exe)
    )
    content = relaunch_bat.build_bat(
        exe,
        wait_checks=checks,
        stage_label="swap",
        # Retain the live pair, then copy the new code + hash over the
        # install-dir originals. /Y overwrites. Only now that every holder is
        # gone: replacing app.zip under a live flet is the errno-32 white
        # screen (#195).
        stage_body=render_steps_bat(
            stage_steps(new_zip, new_hash, dst_zip, dst_hash)
        ),
        # The confirm has failed its whole re-launch budget, so this release
        # does not come up. Put the retained pair back and launch it — without
        # this the script falls through to a dead install whose own updater
        # shipped inside the file it just overwrote.
        recover_body=render_steps_bat(restore_steps(dst_zip, dst_hash)),
        # It booted: the retained pair has been superseded, so drop it.
        confirm_body=render_steps_bat(drop_retained_steps(dst_zip, dst_hash)),
    )
    return relaunch_bat.write_bat(content, prefix="persona-fastswap-")


def _download_small(url: str, dst: str, attempts: int = 5) -> bool:
    """Fetch a small asset (app.zip is ~1MB) to dst via the shared curl
    downloader, resumable across a dropped connection. True once the file is on
    disk with non-zero size.

    Starts from a clean file: this asset is small enough that a fresh fetch
    costs nothing, and a leftover partial from an earlier attempt must never be
    resumed onto. The caller verifies the sha256 before anything is swapped."""
    if not url:
        return False
    try:
        os.remove(dst)
    except OSError:
        pass
    return curl_download(
        url,
        dst,
        timeout_args=["--connect-timeout", "15", "--max-time", "180"],
        attempts=attempts,
    )


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
    exe = install_env.installed_windows_exe()
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
    if not digest_ok(actual, expected_sha256):
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
            env=install_env.relaunch_env(),
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
