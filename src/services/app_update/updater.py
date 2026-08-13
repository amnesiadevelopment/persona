"""persona self-update.

Checks the project's GitHub releases for a newer AppImage of the app itself,
downloads it (resumable, with progress), atomically replaces the running
AppImage, and re-execs into the new version.

Only meaningful when running as a packaged AppImage (the AppImage runtime sets
$APPIMAGE). When running from source the check still reports availability but
apply_and_restart is a no-op guarded by the $APPIMAGE check.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time

from ..engine.updater import is_newer
from ...core import platform as _platform

APP_VERSION = "2.9.16"
APP_REPO = "amnesiadevelopment/persona"


def asset_name() -> str:
    """The release asset filename for this OS — what CI publishes per platform."""
    if _platform.IS_WINDOWS:
        return "persona-windows-setup.exe"
    if _platform.IS_MACOS:
        return "persona-macos.dmg"
    return "persona-x86_64.AppImage"


def _asset_suffix() -> str:
    if _platform.IS_WINDOWS:
        return ".exe"
    if _platform.IS_MACOS:
        return ".dmg"
    return ".AppImage"

# curl keeps a download alive over a flaky Tor circuit far better than urllib,
# which can block for the whole timeout on a dead exit. These match install.sh.
_CONNECT_TIMEOUT = 30  # give up a stalled CONNECT fast, then retry a fresh one
_SPEED_LIMIT = 1024  # bytes/s; below this for _SPEED_TIME, abort + resume
_SPEED_TIME = 30
_MAX_ATTEMPTS = 40


def _sanitize_tag(tag: str) -> str:
    """A filesystem-safe slug of a release tag for use in the staged filename."""
    return "".join(c if (c.isalnum() or c in ".-_") else "_" for c in (tag or ""))


def staged_path(tag: str = "") -> str:
    """Deterministic path for the in-progress download, KEYED BY RELEASE TAG.

    The tag is in the filename so a download for one version never resumes on top
    of a different version's leftover file. A fixed name caused the "installed
    2.3.4 but stayed 2.3.3" bug: the 2.3.3 installer left a staged file, and the
    2.3.4 download either resumed onto it (a Frankenstein file) or find_ready_staged
    matched it by an identical size and ran the OLD installer. A per-tag name makes
    each version its own file, so a stale one is never reused.

    Windows/macOS: a temp file (the installer .exe / .dmg is used from there —
    there's no live binary to sit next to). Linux: next to the installed
    AppImage (same filesystem, so the later os.replace is atomic); '' when not
    a packaged AppImage."""
    slug = _sanitize_tag(tag)
    if _platform.IS_WINDOWS:
        name = f"persona-update-setup-{slug}.exe" if slug else "persona-update-setup.exe"
        return os.path.join(tempfile.gettempdir(), name)
    if _platform.IS_MACOS:
        # a temp file, like Windows: the dmg is mounted from there, there is no
        # live binary to sit next to. Without this branch macOS fell into the
        # AppImage one below, got '' (no $APPIMAGE on a mac), and every update
        # ended in "download failed" before a single byte moved.
        name = f"persona-update-{slug}.dmg" if slug else "persona-update.dmg"
        return os.path.join(tempfile.gettempdir(), name)
    target = installed_appimage_path()
    if target is None:
        return ""
    part = (
        f".persona-update-{slug}.AppImage.part" if slug
        else ".persona-update.AppImage.part"
    )
    return os.path.join(os.path.dirname(target), part)


def _clear_stale_staged(keep: str) -> None:
    """Remove leftover staged installers from OTHER versions so a stale one can't
    be picked up or resumed onto. Keeps only `keep` (the current version's file)."""
    import glob

    if _platform.IS_WINDOWS:
        pattern = os.path.join(tempfile.gettempdir(), "persona-update-setup*.exe")
    elif _platform.IS_MACOS:
        pattern = os.path.join(tempfile.gettempdir(), "persona-update*.dmg")
    else:
        target = installed_appimage_path()
        if target is None:
            return
        pattern = os.path.join(os.path.dirname(target), ".persona-update*.AppImage.part")
    for p in glob.glob(pattern):
        if os.path.abspath(p) != os.path.abspath(keep):
            try:
                os.remove(p)
            except OSError:
                pass


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
        out = subprocess.run(
            cmd, capture_output=True, timeout=max_time + 5,
            **_platform.no_window_kwargs(),
        )
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
            **_platform.no_window_kwargs(),
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


def find_ready_staged(url: str, timeout: int = 30, size: int = 0, tag: str = "") -> str:
    """If a fully-downloaded staged file for THIS tag is already on disk (size
    matches the remote asset), return it so we can offer to restart into it
    without re-downloading. Else ''. The tag keys the filename so a leftover from
    a DIFFERENT version is never matched (that's what let the old 2.3.3 installer
    run when 2.3.4 was expected)."""
    staged = staged_path(tag)
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


def releases_latest_url() -> str:
    """The PUBLIC releases/latest page (not the API). It 302-redirects to
    releases/tag/<tag>, so the tag is readable from the redirect Location with no
    authentication and — crucially — NO rate limit. The API endpoint is limited
    to 60 unauthenticated requests/hour per IP, so behind a shared NAT (mobile
    carrier, office) that budget is quickly exhausted and every check then failed
    the `curl -f` and was silently reported as 'up to date' — the update was
    invisible and unreachable. The redirect has no such cap."""
    if not APP_REPO:
        return ""
    return f"https://github.com/{APP_REPO}/releases/latest"


def _tag_from_location(headers: str) -> str:
    """Parse the release tag out of a releases/latest redirect's headers: the
    Location points at .../releases/tag/<tag>."""
    for line in (headers or "").splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            marker = "/releases/tag/"
            i = loc.find(marker)
            if i != -1:
                return loc[i + len(marker):].strip().strip("/")
    return ""


def latest_tag(timeout: int = 30) -> str:
    """The newest release tag, via the rate-limit-free releases/latest redirect.
    '' on any failure (dead network / unexpected response)."""
    url = releases_latest_url()
    if not url:
        return ""
    try:
        # -I: HEAD; no -L so we READ the redirect instead of following it. -f is
        # deliberately omitted — a 302 is the SUCCESS case here, and -f would
        # treat some redirects as errors.
        out = subprocess.run(
            ["curl", "-sI", "--connect-timeout", "15", "--max-time", str(timeout), url],
            capture_output=True, timeout=timeout + 5,
            **_platform.no_window_kwargs(),
        )
        if out.returncode != 0:
            return ""
        return _tag_from_location(out.stdout.decode("utf-8", "replace"))
    except Exception:
        return ""


def asset_download_url(tag: str) -> str:
    """The deterministic download URL for this OS's asset in a release. The asset
    filename is fixed per platform (asset_name), so we don't need the API's asset
    list to build it."""
    if not tag or not APP_REPO:
        return ""
    return f"https://github.com/{APP_REPO}/releases/download/{tag}/{asset_name()}"


def update_available(latest: str, current: str = APP_VERSION) -> bool:
    return is_newer(latest, current)


def pick_asset(assets: list[dict]) -> tuple[str, int]:
    """Pick this OS's release asset (download_url, size). The size comes straight
    from the GitHub API, so the download has an exact total without a separate
    (Tor-flaky) HEAD request."""
    want = asset_name()
    for asset in assets:
        if asset.get("name", "") == want:
            return asset.get("browser_download_url", ""), int(asset.get("size", 0) or 0)
    # fallback: any asset with this OS's extension
    suffix = _asset_suffix()
    for asset in assets:
        if asset.get("name", "").endswith(suffix):
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


def installed_macos_app() -> str:
    """Path to the .app bundle this process runs from ('' when run from a
    source checkout). In a packaged flet build sys.executable points inside
    <name>.app/Contents/, which is enough to recover the bundle root."""
    if not _platform.IS_MACOS:
        return ""
    for exe in (sys.executable or "", sys.argv[0] if sys.argv else ""):
        i = exe.find(".app/Contents/")
        if i != -1:
            return exe[: i + len(".app")]
    return ""


def can_self_update() -> bool:
    """True when this process runs as an installed build the updater knows how
    to replace: the installed Windows exe, a macOS .app bundle, or a packaged
    AppImage. From a source checkout there is nothing to swap."""
    if _platform.IS_WINDOWS:
        return os.path.basename(sys.executable or "").lower() == "persona.exe"
    if _platform.IS_MACOS:
        return bool(installed_macos_app())
    return is_packaged_appimage()


def check_for_update(timeout: int = 30) -> tuple[str, str, int]:
    """Return (tag, download_url, size) when a newer release exists, else
    ('', '', 0). Resolves the latest tag via the releases/latest REDIRECT, not the
    GitHub API — the API's 60/hour unauthenticated cap made checks 403 behind a
    shared NAT, and the resulting empty body read as 'up to date' so the update
    was silently unreachable (live-reproduced: the host at 0/60 got a 403 while a
    new release was published). The redirect has no rate limit. The asset URL is
    deterministic per platform, so no asset-list API call is needed; size comes
    from a HEAD on that URL (0 when unknown — download_update falls back to
    remote_size). curl uses a short connect timeout so a dead circuit fails fast
    instead of hanging and silently missing the update."""
    tag = latest_tag(timeout=timeout)
    if not tag:
        return "", "", 0
    if not update_available(tag):
        return "", "", 0
    url = asset_download_url(tag)
    if not url:
        return "", "", 0
    size = remote_size(url, timeout=timeout)
    return tag, url, size


def download_update(
    url: str, timeout: int = 600, progress=None, size: int = 0, tag: str = ""
) -> str:
    """Download the new installer/AppImage to a per-TAG temp file. Resumable
    across dropped connections (Tor). Returns the staged path or '' on failure.
    `progress(done, total)` is called as bytes arrive. `size` is the exact asset
    size from the GitHub API; we trust it over a HEAD request (which is flaky to
    impossible over Tor — that's why the bar had no total and looked stuck).

    The tag keys the staged filename so a resume never lands on a different
    version's leftover, and stale installers from other versions are cleared
    first — the fix for "installed 2.3.4 but stayed 2.3.3".
    """
    if not url:
        return ""
    staged = staged_path(tag)
    if not staged:
        return ""
    _clear_stale_staged(keep=staged)

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
                rc = subprocess.run(
                    cmd, capture_output=True, **_platform.no_window_kwargs()
                ).returncode
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


def verify_appimage_runs(path: str, timeout: int = 30) -> bool:
    """True if `path` is an AppImage whose runtime executes on this host and
    whose payload is readable — proven WITHOUT ever booting the app.

    A probe that starts the app cannot be windowless: AppRun execs the
    Flutter host, which paints the boot screen BEFORE the Python side (and
    the PERSONA_SELFTEST gate in main.py) gets to run, so for the whole
    verify a second persona window and taskbar button stand next to the live
    one, ending on the engine's error screen when the gate exits under it
    (#199). So the probe never runs the payload at all: `--appimage-extract
    AppRun` is handled by the AppImage RUNTIME itself, which extracts and
    exits with nothing inside the payload ever starting. That still catches
    the launch-blockers the swap must never let through — a runtime that
    can't execute or read its squashfs (v2.1.3's exit-127 "open dir error",
    truncated/corrupt download) — needs no display, no FUSE and no flet
    cache, and works the same on a headless box. Whether the bytes match the
    CI-published build is the download-time sha256 check
    (verify_staged_installer), not this probe's job.

    The runtime may fork, so the probe is killed as a whole process GROUP and
    waited on before this returns — nothing of it survives into the swap and
    execv.
    """
    import shutil
    import signal
    import subprocess

    if not path or not os.path.isfile(path):
        return False
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    try:
        # the runtime drops squashfs-root into its cwd — never the live app's
        # cwd (serious_python chdirs into the flet extraction dir, #195)
        workdir = tempfile.mkdtemp(prefix="persona-verify-")
    except OSError:
        return False

    def reap(proc) -> None:
        try:
            # start_new_session makes the probe its own group leader (pgid ==
            # pid), and the pgid stays valid while ANY member lives — even
            # after the leader itself was reaped
            os.killpg(proc.pid, getattr(signal, "SIGKILL", 9))
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    try:
        try:
            proc = subprocess.Popen(
                [path, "--appimage-extract", "AppRun"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=workdir,
                env=_relaunch_env(),  # #135: scrubbed runtime vars
                start_new_session=True,
            )
        except Exception:
            return False
        try:
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # a runtime without pattern support extracts the whole payload
            # and can outlive the timeout — AppRun lands early, so its
            # presence below still proves the runtime
            pass
        except Exception:
            reap(proc)
            return False
        reap(proc)
        if os.path.isfile(os.path.join(workdir, "squashfs-root", "AppRun")):
            return True
        return proc.returncode == 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_expected_sha256(tag: str, name: str = "", attempts: int = 3) -> str:
    """The sha256 CI publishes for this OS's asset, or '' when unavailable.

    Checks two sources: the combined checksums.txt (which the windows CI job
    fills with the exe + app.zip) AND the per-asset sidecar {asset}.sha256 that
    the macOS/Linux jobs publish. Without the sidecar lookup, verify on mac/linux
    fell through to a fail-OPEN size check — a channel-controlling party could
    then ship an arbitrary dmg/AppImage the auto-updater would install (RCE)."""
    if not tag:
        return ""
    name = name or asset_name()
    base = f"https://github.com/{APP_REPO}/releases/download/{tag}"

    # 1) combined checksums.txt (windows exe + app.zip live here)
    for _ in range(attempts):
        body = _curl_get(f"{base}/checksums.txt")
        if not body:
            continue
        for line in body.splitlines():
            parts = line.split()
            # sha256sum format: "<hex>  <name>" ('*' prefix in binary mode)
            if len(parts) >= 2 and parts[-1].lstrip("*") == name:
                return parts[0].strip().lower()
        break  # fetched but the asset isn't in this file — try the sidecar

    # 2) per-asset sidecar {asset}.sha256 (macOS dmg + Linux AppImage)
    for _ in range(attempts):
        body = _curl_get(f"{base}/{name}.sha256")
        if not body:
            continue
        parts = body.split()
        if parts:
            return parts[0].strip().lower()
        break
    return ""


def _tag_from_staged(staged: str) -> str:
    """Recover the release tag baked into the staged filename by staged_path().

    The Linux AppImage case was MISSING, so a staged .AppImage.part yielded tag=''
    → fetch_expected_sha256('')='' → verify_staged_installer took the fail-OPEN
    'no published checksum' branch and an unverified AppImage was os.execv'd
    (audit7 #2). The dot-prefixed name and the two-part .AppImage.part suffix are
    matched here so the real tag is recovered and the checksum is actually
    verified. Order the Linux case first because its suffix is the most specific.
    """
    name = os.path.basename(staged or "")
    for prefix, suffix in (
        (".persona-update-", ".AppImage.part"),
        ("persona-update-setup-", ".exe"),
        ("persona-update-", ".dmg"),
    ):
        if name.startswith(prefix) and name.endswith(suffix) and len(name) > len(prefix) + len(suffix):
            return name[len(prefix):-len(suffix)]
    return ""


def verify_staged_installer(staged: str, tag: str = "", log=None) -> bool:
    """True when the staged installer's sha256 matches the checksum published
    for its release. A fetched-and-mismatching checksum ALWAYS refuses (a
    truncated/corrupted download must never run as an installer). When no
    checksum can be fetched — an older release that never published one, or
    the network dropped — fall back to the size check already done at download
    time rather than blocking updates, and say so."""

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    expected = fetch_expected_sha256(tag or _tag_from_staged(staged))
    if not expected:
        say("Update: no published checksum for this release — "
            "relying on the size check.")
        return True
    try:
        actual = _sha256_file(staged)
    except OSError as e:
        say(f"Update: couldn't read the installer to verify it: {e}")
        return False
    if actual == expected:
        return True
    say("Update: installer checksum mismatch — refusing to run it. "
        "Keeping the current version; the download will be retried.")
    return False


def _installed_windows_exe() -> str:
    """Best-effort path to the installed persona.exe, so the updater can relaunch
    it after a silent install (rather than relying on the installer's own
    relaunch, which came up to a black window under a lowered token). Falls back
    to sys.executable's directory, then the default install path."""
    candidates = []
    try:
        # in a flet build, sys.executable IS persona.exe
        exe = sys.executable
        if exe and exe.lower().endswith("persona.exe"):
            candidates.append(exe)
        if exe:
            candidates.append(os.path.join(os.path.dirname(exe), "persona.exe"))
    except Exception:
        pass
    # per-user install location (current), then the old per-machine one
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "persona", "persona.exe"))
    candidates.append(
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "persona", "persona.exe")
    )
    for c in candidates:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            continue
    return ""


# Per-process values the flet runtime plants in os.environ: PYTHONPATH /
# PYTHONHOME point into THIS install's private extraction dir, and the FLET_*
# server vars carry this process's socket/port. A relaunched build re-applies
# any INHERITED values over its own correct ones (the build template sets every
# one of these with putIfAbsent — an inherited value silently beats the fresh
# one), so leaking them makes the new persona import from a dead path or bind a
# stale port the client never connects to — it starts and dies with no window
# (#135 on Linux; the same inheritance runs through the Windows relaunch chain
# persona -> cmd -> start persona.exe).
_RUNTIME_ENV_VARS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONUNBUFFERED",
    "FLET_SERVER_UDS_PATH",
    "FLET_SERVER_PORT",
    "FLET_PYTHON_CALLBACK_SOCKET_ADDR",
    "FLET_APP_CONSOLE",
    "FLET_APP_STORAGE_DATA",
    "FLET_APP_STORAGE_TEMP",
    "FLET_PLATFORM",
    "FLET_ASSETS_DIR",
    # the client hides its window when this is merely PRESENT (any value) and
    # nothing client-side ever shows it again — inherited across a relaunch it
    # turns the new persona into an invisible zombie
    "FLET_HIDE_WINDOW_ON_START",
)


def _relaunch_env() -> dict:
    """A copy of the environment with every flet/python runtime var dropped,
    for processes that outlive this persona and start the next one."""
    env = dict(os.environ)
    for var in _RUNTIME_ENV_VARS:
        env.pop(var, None)
    return env


def _write_relaunch_bat(exe: str, installer: str, installer_pid, old_pid: int) -> str:
    """A temp .bat that polls until the installer (by IMAGE NAME, see below),
    the exiting persona itself, and any lingering process with the exe's image
    name are ALL gone (bounded, so a hung process can't block the relaunch
    forever), settles a moment, then starts the installed exe, confirms a
    process with its image name actually exists (retrying the start, bounded,
    when it doesn't — a `start` that loses a race with the installer mid-swap
    fails silently), and deletes itself.

    The installer wait goes by image name, NOT just the Popen pid (#174): the
    setup.exe needs admin, so the pid we captured is the medium-integrity
    process that merely brokers the UAC prompt — it EXITS right after the user
    clicks Yes, while the REAL elevated installer carries on as a separate
    process we never got a handle to. Waiting on that pid alone relaunched the
    OLD persona mid-install, and the installer's /CLOSEAPPLICATIONS promptly
    closed it again — "the update installed but persona never reopened". The
    image-name poll covers the whole Inno process family regardless of
    integrity level: the downloaded setup loader AND its second stage, which
    Inno re-runs (elevated) as `<name>.tmp` from %TEMP%\\is-*.tmp.

    Waiting for the installer alone raced the old persona's teardown: the new
    persona started while the dying one still held the flet app extraction in
    %APPDATA%, so flet's delete-and-reextract failed with errno 32 and the user
    got an "Error starting app" window. The image-name check is safe because the
    NEW persona isn't running yet, and the settle pause gives the OS a beat to
    release file handles after the last holder exits. Sleeps via ping because
    `timeout` refuses to run without console input (and paints a countdown).

    Pid and image waits alone still weren't enough (#195): the new persona's
    bootstrap deletes the flet extraction to unpack the updated app.zip BEFORE
    our Python runs, so that delete cannot retry — any handle still open under
    the dir at that instant (release lag, an AV sweep, a straggler child of
    the old persona) crashes the new instance on a white screen with
    "Deletion failed ... errno 32". So after the settle the bat deletes the
    extraction itself, with bounded retries, and only then starts the exe:
    the new persona finds nothing to delete and unpacks fresh. The bat cd's
    to its own directory up front — rd can't remove the directory the shell
    itself occupies, and the spawner's cwd is nothing to rely on. The
    wait-timeout path skips the purge: never pull the extraction from under
    an instance that may still be alive.

    Every second the bat spends polling is black screen for the user (#205),
    and tasklist itself costs hundreds of ms per invocation — the dominant
    piece of a poll beat. So a beat pays at most one tasklist per check
    still worth making: each check jumps straight to the sleep on its first
    live hit, and all the image names are matched against a single
    unfiltered tasklist snapshot rather than one filtered run each. The
    beat itself stays ~1s (cmd has no reliable sub-second sleep: `ping -w`
    against a blackhole address returns instantly when a gateway answers
    with unreachable, and a near-zero sleep would burn the iteration bound
    while the installer still runs)."""
    image = os.path.basename(exe)
    installer_image = os.path.basename(installer)
    installer_stage2 = os.path.splitext(installer_image)[0] + ".tmp"
    checks = ""
    for pid in (installer_pid, old_pid):
        if not pid:
            continue
        checks += (
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
            "if not errorlevel 1 goto hold\r\n"
        )
    # /FO CSV: the default table format truncates long image names to fit a
    # 25-char column, and a truncated name never matches. /L: the names
    # carry regex-special dots. A substring hit against another CSV field is
    # harmless — it only keeps a beat busy, and the wait stays bounded.
    names = " ".join(
        f'/C:"{name}"'
        for name in dict.fromkeys((installer_image, installer_stage2, image))
    )
    checks += (
        f"tasklist /FO CSV /NH 2>nul | findstr /I /L {names} >nul\r\n"
        "if not errorlevel 1 goto hold\r\n"
    )
    # path_provider resolves the app-data root as %APPDATA%\<company>\<product>
    # (both "persona" in pyproject.toml), and the flet bootstrap unpacks
    # app.zip to flet\app beneath it — the dir it deletes on a hash change
    flet_app = r"%APPDATA%\persona\persona\flet\app"
    content = (
        "@echo off\r\n"
        'cd /d "%~dp0" >nul 2>&1\r\n'
        "set tries=0\r\n"
        "set boots=0\r\n"
        "set purges=0\r\n"
        ":wait\r\n"
        + checks +
        "goto settle\r\n"
        ":hold\r\n"
        "set /a tries+=1\r\n"
        # The poll beat is a near-instant `ping -n 1` (~10ms), not a full ~1s
        # `ping -n 2`: the ~0.3s tasklist snapshot above is throttle enough,
        # so the loop catches the installer's exit within a fraction of a
        # second instead of up to a second late — every one of those seconds
        # is the user staring at nothing. The bound rises to ~900 beats to
        # keep the same ~5-minute wall-clock headroom for a slow silent
        # install now that each beat is a fraction of what it was.
        "if %tries% geq 900 goto launch\r\n"
        "ping -n 1 127.0.0.1 >nul\r\n"
        "goto wait\r\n"
        ":settle\r\n"
        # No sleep here: the wait loop already proved every holder is gone, so
        # handle release is done, and the purge below retries rd on the rare
        # straggler handle. A settle ping was a full second of dead time
        # between the update closing persona and reopening it.
        ":purge\r\n"
        f'if not exist "{flet_app}" goto launch\r\n'
        f'rd /s /q "{flet_app}" >nul 2>&1\r\n'
        f'if not exist "{flet_app}" goto launch\r\n'
        "set /a purges+=1\r\n"
        # bounded: covers handle-release stragglers; a holder that never dies
        # must not block the relaunch forever. Recheck near-instantly — the
        # exists probe is the throttle, not the ping.
        "if %purges% geq 60 goto launch\r\n"
        "ping -n 1 127.0.0.1 >nul\r\n"
        "goto purge\r\n"
        ":launch\r\n"
        # empty title + quoted path: `start` treats the first quoted token as a
        # window title, so a bare path with spaces would launch nothing
        f'start "" /D "{os.path.dirname(exe)}" "{exe}"\r\n'
        # `start` returns once CreateProcess succeeded, but the new persona then
        # re-extracts app.zip behind its boot screen before persona.exe registers
        # in tasklist — several seconds. A near-instant recheck sees "not running
        # yet" and re-launches, spawning a SECOND instance that races the
        # extraction; one wins, the other dies → "reopened then closed again
        # quickly" (#229). So wait a real ~3s beat BEFORE confirming, and only
        # re-launch a handful of times (a `start` that lost a race with the
        # installer mid-swap fails silently — that's the case worth retrying, not
        # a slow first boot).
        "ping -n 4 127.0.0.1 >nul\r\n"
        f'tasklist /FI "IMAGENAME eq {image}" /FO CSV /NH 2>nul'
        f' | find /I "{image}" >nul\r\n'
        "if not errorlevel 1 goto done\r\n"
        "set /a boots+=1\r\n"
        "if %boots% lss 5 goto launch\r\n"
        ":done\r\n"
        '(goto) 2>nul & del "%~f0"\r\n'
    )
    fd, path = tempfile.mkstemp(prefix="persona-relaunch-", suffix=".bat")
    try:
        # ascii on purpose: cmd reads .bat files in the OEM codepage, which only
        # agrees with Python's encodings in the ASCII range. A non-ASCII exe
        # path raises here and the caller falls back to the inline relaunch,
        # which passes the path through the (Unicode) process command line.
        with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
            f.write(content)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


_TRANSLOCATION_MARKER = "/AppTranslocation/"


def _translocated_original_path(path: str) -> str:
    """The pre-translocation location of a bundle Gatekeeper runs from a
    read-only App Translocation mirror, via the Security framework's
    SecTranslocateCreateOriginalPathForURL (signature: CFURLRef translocated
    path in, optional CFErrorRef* out, CFURLRef of the original back — the
    same private API Sparkle uses). '' when the API is unavailable or the
    resolution fails."""
    try:
        import ctypes

        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        sec = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool,
        ]
        cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
        cf.CFURLGetFileSystemRepresentation.argtypes = [
            ctypes.c_void_p, ctypes.c_bool, ctypes.c_char_p, ctypes.c_long,
        ]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        resolve = sec.SecTranslocateCreateOriginalPathForURL
        resolve.restype = ctypes.c_void_p
        resolve.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        raw = path.encode("utf-8")
        url = cf.CFURLCreateFromFileSystemRepresentation(
            None, raw, len(raw), True
        )
        if not url:
            return ""
        try:
            original = resolve(url, None)
            if not original:
                return ""
            try:
                buf = ctypes.create_string_buffer(4096)
                if not cf.CFURLGetFileSystemRepresentation(
                    original, True, buf, len(buf)
                ):
                    return ""
                return buf.value.decode("utf-8", "replace")
            finally:
                cf.CFRelease(original)
        finally:
            cf.CFRelease(url)
    except Exception:
        return ""


def _macos_install_target(app: str, say) -> str:
    """The on-disk bundle the update must replace. Normally the running bundle
    itself — but when Gatekeeper runs an unsigned, quarantined app from a
    read-only App Translocation mirror under /private/var/folders, replacing
    THAT copy is impossible (Errno 30) and pointless: the real app lives
    wherever the user put it. Resolve the original and update it there; fall
    back to /Applications when the original can't be found or its directory
    isn't writable. '' when no writable install location exists."""
    if _TRANSLOCATION_MARKER not in app:
        return app
    original = _translocated_original_path(app)
    if (
        original
        and _TRANSLOCATION_MARKER not in original
        and os.path.isdir(original)
        and os.access(os.path.dirname(original), os.W_OK | os.X_OK)
    ):
        say(f"Update: running from a translocated copy; updating {original}.")
        return original
    fallback = "/Applications/" + os.path.basename(original or app)
    if os.access("/Applications", os.W_OK | os.X_OK):
        say("Update: running from a translocated copy and the original app "
            f"can't be updated in place; installing to {fallback}.")
        return fallback
    say("Update: running from a read-only translocated path and no writable "
        "install location was found; aborting.")
    return ""


def _apply_macos(staged: str, say) -> bool:
    """Swap the installed .app for the one inside the verified dmg, then
    relaunch. Returns False (with the working install intact) on any failure;
    does not return on success."""
    import shutil

    if not staged or not os.path.isfile(staged):
        say("Update: downloaded disk image missing.")
        return False
    if not verify_staged_installer(staged, log=say):
        try:
            os.remove(staged)  # a full-size corrupt file would otherwise
        except OSError:        # be matched again by find_ready_staged
            pass
        return False
    app = installed_macos_app()
    if not app:
        # source checkout / unbundled run: nothing to swap — hand the user the
        # verified dmg instead of failing silently.
        say("Update downloaded — install it from the opened disk image.")
        try:
            subprocess.Popen(["open", staged])
        except Exception:
            say(f"Update saved at {staged}.")
        return False
    # The bundle we run from and the bundle we must REPLACE can differ: under
    # App Translocation the running copy is a read-only mirror. The download
    # carries no quarantine xattr (curl doesn't set one), so once the real
    # bundle is replaced, later launches run from it directly — the update
    # also cures the translocation.
    app = _macos_install_target(app, say)
    if not app:
        return False

    mount = tempfile.mkdtemp(prefix="persona-update-mnt-")
    say("Update: opening the disk image…")
    try:
        rc = subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-readonly",
             "-mountpoint", mount, staged],
            capture_output=True, timeout=120,
        ).returncode
    except Exception as e:
        say(f"Update: couldn't open the disk image: {e}")
        return False
    if rc != 0:
        say("Update: couldn't open the disk image.")
        return False
    backup = app + ".bak"
    try:
        new_app = ""
        try:
            for entry in sorted(os.listdir(mount)):
                if entry.endswith(".app"):
                    new_app = os.path.join(mount, entry)
                    break
        except OSError:
            pass
        if not new_app:
            say("Update: no app bundle inside the disk image.")
            return False
        say("Update: installing the new version…")
        shutil.rmtree(backup, ignore_errors=True)
        # the target may not exist yet (translocated run falling back to a
        # fresh /Applications install) — then there is nothing to move aside
        moved_aside = os.path.exists(app)
        if moved_aside:
            try:
                os.rename(app, backup)
            except OSError as e:
                say(f"Update: couldn't move the current app aside ({e}); "
                    "aborting.")
                return False
        # ditto preserves the code signature, resource forks and permissions,
        # which a plain python copy does not — Gatekeeper would refuse the app.
        try:
            rc = subprocess.run(
                ["ditto", new_app, app], capture_output=True, timeout=600,
            ).returncode
        except Exception:
            rc = 1
        if rc != 0:
            say("Update: copying the new app failed; keeping the current "
                "version.")
            shutil.rmtree(app, ignore_errors=True)
            if moved_aside:
                try:
                    os.rename(backup, app)
                except OSError:
                    pass
            return False
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        try:
            subprocess.run(
                ["hdiutil", "detach", mount], capture_output=True, timeout=60,
            )
        except Exception:
            pass
    try:
        os.remove(staged)
    except OSError:
        pass
    # Relaunch after THIS process is gone — `open` on a still-running app just
    # focuses the old instance. A detached shell waits for our pid, then opens
    # the freshly installed bundle.
    say("Update: restarting…")
    try:
        subprocess.Popen(
            ["/bin/sh", "-c",
             f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done; '
             f'open "{app}"'],
            close_fds=True,
            start_new_session=True,
            # Scrub the inherited PYTHONPATH/FLET_* before reopening — the same
            # env poisoning that left the reopened app hung with no window on
            # Windows (#173) and Linux (#135). `open` hands off to launchd, but
            # pass a clean env anyway so nothing stale can leak into the child.
            env=_relaunch_env(),
        )
    except Exception as e:
        say(f"Update: couldn't schedule the relaunch: {e}")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


def _try_windows_fast_update(say) -> bool:
    """Attempt a Windows code-only update from the latest release's manifest.
    Returns False (falls back to the full installer) when the manifest is
    missing, requires a full install, or the swap can't be staged. Does not
    return on success — the process exits so the swap .bat can replace app.zip.
    Isolated in fast_update so the well-tested full-installer path is untouched."""
    try:
        from . import fast_update as fu
    except Exception:
        return False
    if not fu.can_fast_update():
        return False
    # Resolve the tag via the rate-limit-free redirect (not the API — same 403
    # trap that silently broke the version check behind a shared NAT). The
    # manifest/app.zip asset names are fixed, so their URLs are deterministic —
    # no asset-list API call needed.
    tag = latest_tag()
    if not tag or not update_available(tag):
        return False
    base = f"https://github.com/{APP_REPO}/releases/download/{tag}"
    manifest_url = f"{base}/{fu.MANIFEST_ASSET}"
    app_zip_url = f"{base}/{fu.APP_ZIP_ASSET}"
    manifest = fu.parse_manifest(_curl_get(manifest_url))
    if not fu.should_fast_update(manifest, APP_VERSION):
        return False
    sha = str(manifest.get("app_zip_sha256", ""))
    return fu.apply_code_only_and_restart(app_zip_url, sha, log=say)


def apply_and_restart(staged: str, extra_args=None, log=None) -> bool:
    """Replace the running AppImage with the staged download and re-exec into
    it — but ONLY after proving the new binary actually launches, and with the
    old binary kept as a backup that is restored if anything goes wrong. This
    can never leave a non-launchable AppImage in place (the v2.1.3 brick). On
    any failure it returns False, keeps the working version, and `log` explains
    why. Does not return on success (process is replaced)."""

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    # Windows: hand the downloaded installer control. It has a fixed AppId, so
    # it upgrades the existing install in place (old files removed, one entry in
    # Programs and Features) and restarts persona — no manual download. A running
    # .exe can't replace itself, but a SEPARATE installer process can replace it
    # while persona exits, which is exactly what Chrome/Discord-style updaters do.
    if _platform.IS_WINDOWS:
        # Fast path (#205): most releases change only the app's Python code
        # (app.zip, ~1MB), not the 218MB runtime the Inno installer reinstalls.
        # When the release's manifest says a code-only update is safe (runtime /
        # deps unchanged), swap app.zip + its hash and relaunch — seconds instead
        # of the ~30s reinstall. Does not return on success. Any decline/failure
        # falls through to the full installer below (unchanged), so a bad manifest
        # or a runtime-changing release always degrades to the working full path.
        if _try_windows_fast_update(say):
            return True  # unreachable on success (process exited); safety net
        if not staged or not os.path.isfile(staged):
            say("Update: installer missing.")
            return False
        if not verify_staged_installer(staged, log=log):
            try:
                os.remove(staged)  # a full-size corrupt file would otherwise
            except OSError:        # be matched again by find_ready_staged
                pass
            return False
        say("Update: launching the installer…")
        exe = _installed_windows_exe()
        try:
            # /VERYSILENT installs with no windows at all (/SILENT still shows a
            # progress dialog); /CLOSEAPPLICATIONS closes this persona so its
            # files can be replaced; /NORESTART keeps it from rebooting Windows.
            # /MERGETASKS deselects the installer's wipe tasks explicitly: Inno
            # remembers task selections from a previous interactive install and
            # re-applies them on silent upgrades, so without this a single
            # box-checked reinstall would wipe profiles/engines on every update.
            installer = subprocess.Popen(
                [
                    staged,
                    "/VERYSILENT",
                    "/CLOSEAPPLICATIONS",
                    "/NORESTART",
                    "/MERGETASKS=!wipedata,!wipeengines",
                ],
                close_fds=True,
                # the installer outlives this persona and everything IT may
                # start (its own relaunch entries, Restart Manager restarts)
                # inherits its environment — hand it the scrubbed copy so this
                # process's runtime vars can't poison a persona started from
                # inside the installer's process tree (see _RUNTIME_ENV_VARS)
                env=_relaunch_env(),
                # this process's cwd IS the flet app extraction dir
                # (serious_python chdirs there); a child that inherits it
                # holds a directory handle the new persona's
                # delete-and-reextract trips over with errno 32 (#195) —
                # every process the update spawns gets a cwd outside it
                cwd=tempfile.gettempdir(),
                **_platform.no_window_kwargs(),
            )
        except Exception as e:
            say(f"Update: couldn't start the installer: {e}")
            return False
        # Relaunch persona OURSELVES after the installer finishes, in this same
        # (normal) user context — NOT from the installer's [Run] entry. The
        # installer's runasoriginaluser relaunch ran persona in a lowered-token
        # shell where the flet/Flutter client came up to a black window that never
        # painted. An invisible cmd waits for the whole installer process FAMILY
        # to exit — by image name, because the setup.exe elevates via UAC and
        # the pid Popen gave us is only the un-elevated broker that dies as soon
        # as the user clicks Yes (#174) — then starts the new persona.exe. The
        # cmd chain is spawned by THIS un-elevated persona before it exits, so
        # everything it starts (including the new persona) stays at normal user
        # integrity; the elevated installer never touches the relaunch.
        if exe:
            try:
                try:
                    cmd = ["cmd", "/c",
                           _write_relaunch_bat(exe, staged,
                                               getattr(installer, "pid", None),
                                               os.getpid())]
                except Exception:
                    cmd = [
                        "cmd", "/c",
                        # waiter .bat unwritable (broken temp dir, non-ascii
                        # path) — wait a fixed ~14s for the silent install to
                        # finish swapping files, then launch
                        "ping", "-n", "15", "127.0.0.1", ">nul", "&",
                        "start", "", "/D", os.path.dirname(exe), exe,
                    ]
                subprocess.Popen(
                    cmd,
                    close_fds=True,
                    # the cmd -> start -> persona.exe chain inherits THIS
                    # dying process's environment; hand it a scrubbed copy or
                    # the new persona re-applies our stale runtime vars and
                    # comes up dead (#135's Windows twin: the update installed
                    # but nothing reopened, while a manual open worked).
                    env=_relaunch_env(),
                    # the cmd is ALIVE — by design — when the new persona
                    # boots; an inherited cwd inside the flet extraction is a
                    # handle its delete-and-reextract dies on (#195)
                    cwd=tempfile.gettempdir(),
                    # ONE merged creationflags — spreading no_window_kwargs()
                    # here as well would pass creationflags twice, a TypeError
                    # at the call site that silently kills the relaunch.
                    # CREATE_NO_WINDOW keeps the cmd invisible but with a real
                    # (hidden) console; DETACHED_PROCESS strips the console
                    # entirely, and a console-less cmd wedges forever running a
                    # batch file (its pipes and goto never execute).
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as e:
                say(f"Update: couldn't schedule the relaunch: {e}")
        # Exit now so the installer can overwrite our files.
        say("Update: restarting…")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    # macOS: verify the dmg, mount it, swap the installed .app for the one
    # inside (with the old bundle kept as a backup until the copy succeeds),
    # then relaunch once this process exits.
    if _platform.IS_MACOS:
        return _apply_macos(staged, say)

    if not _platform.IS_LINUX:
        say("Update available — download the new version from the releases page.")
        return False

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

    # Re-verify the staged binary's sha256 at apply time, the way Windows/macOS
    # do — the size check at download time isn't a corruption/integrity gate. A
    # mismatch refuses and deletes the bad file so it isn't matched again.
    if not verify_staged_installer(staged, log=log):
        try:
            os.remove(staged)
        except OSError:
            pass
        return False

    # 1) Prove the new AppImage launches on THIS host before touching the live
    #    one. If it can't, we abort and the user stays on the working version.
    say("Update: verifying the new build…")
    if not verify_appimage_runs(staged):
        say("Update: the new build didn't launch here — keeping the current "
            "version. The download is saved; it will be retried.")
        return False

    # 2) Back up the working binary, then swap in the verified new one. If the
    #    swap fails, restore the backup so we never lose a launchable app.
    backup = target + ".bak"
    try:
        shutil = __import__("shutil")
        shutil.copy2(target, backup)
    except Exception as e:
        say(f"Update: couldn't back up the current version ({e}); aborting.")
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
        say(f"Update: replacing the AppImage failed: {e}; restoring backup.")
        try:
            os.replace(backup, target)
        except Exception:
            pass
        return False

    # 3) Re-exec exactly as launched. (Never force APPIMAGE_EXTRACT_AND_RUN — on
    #    a FUSE host it makes the runtime extract into a dir it can't and the
    #    AppImage fails with "open dir error"; that bricked v2.1.3.)
    args = [target] + list(extra_args or sys.argv[1:])
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    say("Update: restarting…")
    try:
        os.remove(backup)  # verified to launch; the backup is no longer needed
    except OSError:
        pass
    # execv inherits the current working directory. If we were launched from a
    # dir that no longer exists after the swap (e.g. the old AppImage's mount
    # point), the relaunched process can't getcwd() and dies at startup with
    # "Getting current working directory failed". Move to a directory that is
    # guaranteed to exist first.
    for safe in (os.path.expanduser("~"), os.path.dirname(target), "/"):
        try:
            os.chdir(safe)
            break
        except OSError:
            continue
    # execv passes os.environ along; drop the runtime vars or the relaunched
    # build re-applies our stale values over its own (#135) — see
    # _RUNTIME_ENV_VARS.
    for var in _RUNTIME_ENV_VARS:
        os.environ.pop(var, None)
    try:
        os.execv(target, args)
    except Exception as e:
        say(f"Update: relaunch failed: {e}")
        return False
    return False  # unreachable on success
