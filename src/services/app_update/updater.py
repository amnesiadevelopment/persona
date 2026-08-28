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
import tempfile
import threading
import time

from .. import egress
from ..engine.updater import is_newer
from ...core import platform as _platform
from ...utils.httpdl import atomic_replace, curl_download, digest_ok, sha256_file
from . import install_env, relaunch_bat

APP_VERSION = "3.0.2"
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
    try:
        # Persona's own egress authority decides how this leaves the host; a
        # REFUSE raises here, INSIDE the try, so it lands on this function's
        # existing '' failure sentinel rather than becoming a new exception the
        # callers (:499, :511, :933) were never written to handle. Nothing is
        # sent on that path — the raise happens before the subprocess exists.
        proxy_args = egress.curl_proxy_args()
        cmd = [
            "curl", "-fsSL",
            *proxy_args,
            "--connect-timeout", "15", "--max-time", str(max_time),
        ]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
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
        # Egress policy, resolved before anything is sent; a REFUSE raises here
        # inside the try and lands on this function's existing 0 sentinel. The
        # -fsSLI shape is NOT touched: -L is load-bearing (GitHub 302s to a CDN
        # and the real size is in the FINAL response), so --proxy is spliced in
        # rather than the flags being reordered or normalised.
        proxy_args = egress.curl_proxy_args()
        out = subprocess.run(
            ["curl", "-fsSLI", *proxy_args,
             "--connect-timeout", "15", "--max-time", str(timeout), url],
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
        # treat some redirects as errors. --proxy is spliced into that exact
        # shape; reordering or "cleaning up" these flags silently breaks version
        # detection, which this path has already shipped once (a 403 read as
        # "up to date"). The egress verdict is resolved before anything is sent;
        # a REFUSE raises inside the try and lands on the existing '' sentinel.
        proxy_args = egress.curl_proxy_args()
        out = subprocess.run(
            ["curl", "-sI", *proxy_args,
             "--connect-timeout", "15", "--max-time", str(timeout), url],
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
    # Resolve the egress policy FIRST — before staging, clearing or starting the
    # progress watcher — so a refusal costs nothing and, above all, nothing is
    # sent. The argv is caller-owned and handed down to the shared downloader
    # (which must not hold a second copy of this decision); a REFUSE lands on
    # this function's existing '' failure sentinel.
    try:
        proxy_args = egress.curl_proxy_args()
    except egress.EgressRefused:
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

    # The transfer is utils.httpdl's shared curl downloader (resume, retries and
    # the completion rule live there, one copy for every update path); the
    # timeout policy stays here because it's this path's own — a short connect
    # timeout plus a speed floor, matching install.sh, so a dead Tor circuit
    # fails fast and retries on a fresh one instead of hanging.
    try:
        ok = curl_download(
            url,
            staged,
            timeout_args=[
                "--connect-timeout", str(_CONNECT_TIMEOUT),
                "--speed-limit", str(_SPEED_LIMIT),
                "--speed-time", str(_SPEED_TIME),
            ],
            proxy_args=proxy_args,
            attempts=_MAX_ATTEMPTS,
            total=total,
            deadline=time.monotonic() + timeout,
        )
    finally:
        stop.set()
    if not ok:
        return ""
    if progress is not None and total:
        progress(total, total)  # flush 100% to the UI
    os.chmod(staged, 0o755)
    return staged


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
    for its release. FAIL-CLOSED: an installer we cannot verify never runs.

    A fetched-and-mismatching checksum refuses (a truncated/corrupted download
    must never run as an installer), and so does a checksum that cannot be
    fetched at all. This used to fall back to the download-time size check and
    install anyway — the same fail-open the engine updater already refused for
    the identical situation, and the defect class 5e00d66 and bfc7cbf were each
    reaching for. A size check is a completeness check, never an integrity one:
    it cannot tell a truncated download from a substituted installer.

    The refusal is deliberately not conditional on WHY the digest is missing.
    fetch_expected_sha256 already retries, and on exhaustion a transient network
    failure is indistinguishable from an absent checksum — so both refuse.
    Availability of an update is never weighed against the integrity of what
    gets executed (Invariant #0): a user who does not update has lost nothing, a
    user who installs an unverified binary has lost the machine. CI has
    published a checksum for every asset on every OS since 7b119f6 (v2.9.10), so
    no reachable release is blocked by this.

    There is no allow_missing opt-in here, on purpose: every app-update asset
    has a digest source. Nor is there one anywhere else in persona any more —
    the engine's Linux predictable-URL fallback used to opt in at its own call,
    and PS-49 removed that carve-out after measuring that the asset it covered
    carries a digest upstream all along. The primitive still accepts the flag;
    nothing passes it.
    """

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    expected = fetch_expected_sha256(tag or _tag_from_staged(staged))
    if not expected:
        say("Update: no published checksum could be fetched for this release — "
            "refusing to run an unverified installer. Keeping the current "
            "version; the update will be retried.")
        return False
    try:
        actual = sha256_file(staged)
    except OSError as e:
        say(f"Update: couldn't read the installer to verify it: {e}")
        return False
    if digest_ok(actual, expected):
        return True
    say("Update: installer checksum mismatch — refusing to run it. "
        "Keeping the current version; the download will be retried.")
    return False


# Both of these are now owned by install_env, which the code-only fast path also
# imports — it used to reach in here for the private names from inside a
# function body to dodge an import cycle. The underscore aliases stay because
# they are this module's established internal spelling.
_installed_windows_exe = install_env.installed_windows_exe


# Per-process values the flet runtime plants in os.environ: PYTHONPATH /
# PYTHONHOME point into THIS install's private extraction dir, and the FLET_*
# server vars carry this process's socket/port. A relaunched build re-applies
# any INHERITED values over its own correct ones (the build template sets every
# one of these with putIfAbsent — an inherited value silently beats the fresh
# one), so leaking them makes the new persona import from a dead path or bind a
# stale port the client never connects to — it starts and dies with no window
# (#135 on Linux; the same inheritance runs through the Windows relaunch chain
# persona -> cmd -> start persona.exe).
_RUNTIME_ENV_VARS = install_env.RUNTIME_ENV_VARS
_relaunch_env = install_env.relaunch_env


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
    while the installer still runs).

    The script itself — the bounded wait loop, the flet-extraction purge, the
    launch-and-confirm retry, the self-delete — comes from the shared generator
    (relaunch_bat), which the fast path's app.zip swapper also uses. Only the
    WAIT SET below is this path's own. The two used to be hand-maintained
    near-clones with identical magic constants, which is how #195 got fixed here
    and missed there.
    """
    installer_image = os.path.basename(installer)
    installer_stage2 = os.path.splitext(installer_image)[0] + ".tmp"
    checks = ""
    for pid in (installer_pid, old_pid):
        checks += relaunch_bat.pid_check(pid)
    # The whole Inno process family plus the exe itself, matched against ONE
    # tasklist snapshot: the downloaded setup loader, Inno's elevated <name>.tmp
    # second stage, and any lingering persona.
    checks += relaunch_bat.image_snapshot_check(
        (installer_image, installer_stage2, os.path.basename(exe))
    )
    # The stage is empty for this path — the installer has already replaced the
    # files by the time everything has exited, so there is nothing to do between
    # the wait and the purge. The label survives as a jump target (and as the
    # documented "no dead time here" boundary, #205).
    content = relaunch_bat.build_bat(
        exe, wait_checks=checks, stage_label="settle"
    )
    return relaunch_bat.write_bat(content, prefix="persona-relaunch-")


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
        # The revert's parked bundle gets the SAME depth-not-duration bound as
        # the stale .bak above. A revert that is refused after moving the
        # current build aside can leave `app + ".reverting"` behind, and until
        # now the only thing that removed it was the NEXT revert's own
        # pre-clean (revert_to_previous_build) — so an operator who reverted
        # once and never again kept a full bundle forever. Bounding it here,
        # beside its sibling, is the same self-bounding shape the Firefox
        # engine states in place at browser/engine_install.py:628-631.
        shutil.rmtree(app + ".reverting", ignore_errors=True)
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
        # RETENTION — the previous bundle STAYS on disk. This is where it used
        # to be destroyed, the instant ditto returned 0. A macOS release can be
        # authentically what upstream published, pass its sha256, install
        # perfectly and then simply not launch; that left the operator with
        # nothing to go back to, while the FAILED-ditto arm just above restored
        # cleanly. The arm where everything succeeded was the one keeping
        # nothing. See rollback_target / revert_to_previous_build for the way
        # back this retention makes expressible.
        #
        # Bounded to exactly ONE by construction — no second cleanup path, no
        # timer: the pre-clean above rmtree's a stale `.bak` BEFORE the next
        # update renames its own bundle aside, so each update's retained bundle
        # REPLACES the last one rather than accumulating. That is the
        # depth-not-duration policy the Firefox engine states in place at
        # browser/engine_install.py:628-631, mirrored here for the .app.
        if moved_aside:
            say("Update: keeping the previous version — you can go back to it "
                "if the new one doesn't work.")
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


def rollback_target() -> str:
    """The retained previous build an operator can go BACK to, or "" when there
    is nothing to go back to.

    This is the whole "can I undo this app update?" question as one call —
    exactly as engine_install.rollback_target is for the Firefox engine, whose
    `-> str` shape this deliberately mirrors (the Chromium one in
    engine/updater.py returns a tuple; the two are not interchangeable, and the
    UI reaches the Firefox-shaped one). "" means the gesture must not be
    offered, because a revert with no retained bundle is a button that cannot
    work.

    macOS (.app bundle) and Linux (AppImage). The Linux arm is what makes the
    row render there at all: _app_rollback_row is NOT platform-gated — it asks
    this function and renders nothing when the answer is "" — so while this
    early-returned on every non-macOS host, the control silently did not exist
    for any Linux operator, on the platform that installs updates unattended
    by default. Windows is still "" here on purpose: its fast path keeps a
    `.prev` under its own scheme (fast_update.py) and its full-installer path
    upgrades in place under Inno's AppId semantics, neither of which this
    resolves.

    Note the two retained artifacts are different KINDS — a .app is a
    directory, an AppImage is a single executable file — so each arm tests for
    what it actually keeps rather than sharing one existence check.

    Deliberately quiet: every failure to resolve an install location answers ""
    rather than raising, because this is called to decide whether to RENDER a
    control."""
    if _platform.IS_LINUX:
        try:
            target = installed_appimage_path()
            if not target:
                return ""
            backup = target + ".bak"
            return backup if os.path.isfile(backup) else ""
        except Exception:
            return ""
    if not _platform.IS_MACOS:
        return ""
    try:
        app = installed_macos_app()
        if not app:
            return ""
        # the same install-target resolution the update itself used, so a
        # translocated run offers the bundle that was actually replaced rather
        # than the read-only mirror it runs from
        app = _macos_install_target(app, lambda _m: None)
        if not app:
            return ""
        backup = app + ".bak"
        return backup if os.path.isdir(backup) else ""
    except Exception:
        return ""


def _remove_any(path: str) -> None:
    """Delete `path` whatever KIND it is, quietly.

    The revert machinery below is shared by two platforms that retain two
    different kinds of artifact: a macOS .app is a DIRECTORY (rmtree), a Linux
    AppImage is a single executable FILE (remove). Calling the wrong one raises
    — rmtree on a file, remove on a directory — so every cleanup in this module
    routes through here rather than assuming a kind. A symlink is unlinked
    rather than followed, so a symlinked install location never causes a
    recursive delete of whatever it points at."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    except OSError:
        pass


def _restore_install_location(app: str, *candidates: str) -> str:
    """Put SOMETHING launchable back at `app`, trying each candidate bundle in
    order. Returns the candidate that landed there, or "" when none could.

    This exists because the revert's restore attempts all share ONE
    destination, and that is exactly where the correlation lives: a condition
    attached to `app` defeats every source identically, so "try the other
    bundle" is not on its own a recovery. Two failures here are the expected
    shape, not a surprise.

    RENAME ONLY — never a copy, for the reason revert_to_previous_build states
    at length: a copytree-restored bundle has a broken code signature and
    Gatekeeper refuses to launch it, so a "recovery" that copies would leave
    something that is not a launchable app at all.

    The one correlated cause that IS recoverable is an OCCUPIED destination
    (ENOTEMPTY defeats every source with the same errno). Only then, and only
    after every plain attempt has failed, is the obstruction cleared and the
    candidates retried — safe because we renamed our own bundle out of `app`
    moments ago, so whatever sits there now is not the bundle we are holding.
    A destination that is unwritable rather than occupied (the volume going
    read-only mid-flight) is NOT recoverable by any rename, and this returns
    "" rather than pretending otherwise."""
    # os.path.EXISTS, not isdir: this helper is shared with the Linux AppImage
    # revert, whose retained artifact is a single executable FILE rather than a
    # .app directory. An isdir filter would silently discard every candidate
    # there and return "" without attempting a single rename — the "nothing
    # could be put back" arm, reached without trying.
    live = [c for c in candidates if c and os.path.exists(c)]

    def _try(cand: str) -> bool:
        try:
            os.rename(cand, app)
            return True
        except OSError:
            return False

    for cand in live:
        if _try(cand):
            return cand
    if live and os.path.exists(app):
        _remove_any(app)
        for cand in live:
            if _try(cand):
                return cand
    return ""


def revert_to_previous_build(log=None) -> str:
    """Go BACK to the retained previous build. Returns the path now installed,
    or "" when the revert was refused or could not be completed.

    Serves both platforms that retain something — the macOS .app bundle and the
    Linux AppImage — off whatever rollback_target() resolves, so the mechanics
    below are written in terms of the artifact rather than the bundle. The two
    are different KINDS (a .app is a directory, an AppImage is a single
    executable file), which is why every cleanup here routes through
    _remove_any rather than calling rmtree directly: rmtree raises on a file,
    and that raise would abort the very revert it was meant to tidy up for.

    The RELAUNCH is the operator's, not ours, and deliberately so on both
    platforms: this returns after the swap and says "restart persona to run
    it". Reverting is a rename, so the process still running is the build being
    reverted FROM — exec'ing into the restored one from here would kill the
    running app mid-gesture, and on Linux _apply_linux's own relaunch comment
    records what that costs when the cwd disappears underneath it.

    RENAME, NEVER COPY — and that is a correctness constraint, not a
    performance one. `ditto` is used for the forward swap precisely because it
    preserves the code signature, resource forks and permissions that a plain
    python copy does not (see the note beside the ditto call above);
    Gatekeeper refuses a bundle whose signature a copy has broken. A
    copytree-based revert would restore a bundle macOS will not launch, which
    is not a rollback at all. os.rename is atomic, O(1), and preserves every
    one of those attributes because nothing is rewritten — the directory is
    relocated, not reconstructed. The retained bundle is kept beside the
    original (app + ".bak") so the rename stays same-filesystem.

    The new bundle is moved aside first, so a rename that fails midway leaves
    the operator with something rather than an empty install location, and the
    build being reverted FROM lands in the retained slot — making the revert
    itself undoable by the same gesture.

    That "something rather than an empty install location" is a guarantee this
    function now actually keeps, and it took real work: the restore attempts
    all share ONE destination (`app`), which is precisely where a correlated
    failure lives — a condition attached to that destination defeats the
    retained bundle and the parked bundle identically, so the compensating
    rename is attempted under the very condition that just defeated its twin.
    Both are therefore tried, and an OCCUPIED destination is cleared and
    retried, by _restore_install_location. The one case that stays empty is a
    destination no rename can write at all (the volume going read-only
    mid-flight); the refusal message says so explicitly rather than reporting
    it as the ordinary "your app is fine" refusal, because the operator's
    situation differs radically between the two."""
    def _say(m):
        if log:
            log(m)

    target = rollback_target()
    if not target:
        _say("Update: nothing to go back to — no previous version is retained.")
        return ""
    app = target[: -len(".bak")]
    # park the build being reverted FROM rather than destroying it: it becomes
    # the retained slot's new occupant, so the revert is itself reversible
    parked = app + ".reverting"
    try:
        _remove_any(parked)
        current_exists = os.path.exists(app)
        if current_exists:
            os.rename(app, parked)
        try:
            os.rename(target, app)
        except OSError as e:
            if current_exists:
                # Put SOMETHING launchable back. Preference order matters: the
                # build we just moved aside is what the operator was running a
                # moment ago, so it is restored first and the retained bundle
                # is the fallback — either outcome beats an empty install
                # location, and both are real signed bundles because this only
                # ever renames.
                landed = _restore_install_location(app, parked, target)
                if landed == parked:
                    # Case A, the ordinary refusal: their app is exactly as it
                    # was and nothing else needs saying.
                    _say("Update: couldn't go back to the previous version "
                         f"({e}).")
                elif landed == target:
                    # The revert's restore failed but its own goal was reached
                    # anyway — the previous version IS what is installed now.
                    # The retained slot was just vacated by that landing, so
                    # move the reverted-FROM build into it exactly as the
                    # success path does; otherwise this arm would strand the
                    # new build under `.reverting` and leave the revert not
                    # undoable, which is the property the docstring promises.
                    try:
                        os.rename(parked, target)
                    except OSError:
                        pass
                    # The revert SUCCEEDED here in the only sense that
                    # matters — the previous version is what is installed —
                    # so it earns the hold exactly as the clean path does.
                    # Omitting it on this arm would leave the messier outcome
                    # the only one the 60s poll can undo.
                    _set_hold(log=log)
                    _say("Update: couldn't complete going back cleanly "
                         f"({e}), but the previous version is now installed — "
                         "restart persona to run it, and the version you went "
                         "back from is held until you resume updates.")
                    return app
                else:
                    # Case B: nothing could be put back. Say so plainly — this
                    # operator has no app to launch, which is a different
                    # situation from the refusal above, not a louder one.
                    _say("Update: couldn't go back to the previous version "
                         f"({e}), and the app could not be put back at "
                         f"{app}. Both bundles are safe beside it — "
                         "try going back again, or reinstall persona.")
            else:
                _say(f"Update: couldn't go back to the previous version ({e}).")
            return ""
        # the reverted-from build now occupies the single retained slot
        if current_exists:
            try:
                os.rename(parked, target)
            except OSError:
                _remove_any(parked)
    except OSError as e:
        _say(f"Update: couldn't go back to the previous version ({e}).")
        return ""
    # The reversal is COMPLETE on disk; make it DURABLE. Without this the
    # restart the next line demands is itself the undo: a fresh process resets
    # the in-memory dedup, the 60s poll sees the release just rejected as newer
    # than the restored one, and on Linux with auto-update on (the default) it
    # is re-installed with nobody present. Best-effort by design — see _set_hold.
    _set_hold(log=log)
    _say("Update: went back to the previous version — restart persona to run "
         "it. The version you went back from is held until you resume updates.")
    return app


def _set_hold(log=None) -> None:
    """Write the standing "not that release" instruction, best-effort.

    Held value is APP_VERSION — the release the operator is REJECTING, which is
    exactly the one this process is running: reverting is a rename, so the
    build still executing is the one being reverted FROM (see
    revert_to_previous_build's docstring on why the relaunch is the operator's).

    Read through a try for the same reason engine/updater.py's _set_pin is: a
    settings file that cannot be written must not turn a COMPLETED revert into a
    reported failure. The bundle on disk is already the reverted one; a missing
    hold costs the reversal its DURABILITY, not its correctness — so this logs
    the loss and returns rather than raising into a revert that has succeeded.
    """
    try:
        from ...core import settings

        settings.set_app_update_hold(APP_VERSION)
    except Exception as e:
        if log:
            log(
                f"Update: couldn't record the revert ({e}) — the automatic "
                "update may put you back on the version you just rejected"
            )


def update_held(latest: str) -> bool:
    """True when `latest` is a release the operator deliberately went back FROM
    and has not resumed — so it must not be downloaded, staged-offered, or (on
    Linux, unattended) installed.

    Holds back the rejected release AND anything older, because "not newer than
    the thing I rejected" is not an upgrade in any case. A STRICTLY newer
    release is NOT held: that is the one likely to carry the fix, and letting it
    through is what stops the hold becoming silently permanent (PS-208).

    Degrades to False (normal updating) when settings are unreadable — the same
    fail-soft direction as engine/updater.py's pinned_build(). An unreadable
    settings file must not brick the update path, security updates included."""
    if not latest:
        return False
    try:
        from ...core import settings

        held = settings.app_update_hold()
    except Exception:
        return False
    if not held:
        return False
    return not is_newer(latest, held)


def held_version() -> str:
    """The release an operator went back from and is still being held back from,
    or "" when they never went back — or when the hold has since been SPENT.

    A hold has no clearing write on the forward path, deliberately: nothing on
    the install path knows about it, and adding a write there would still leave
    every already-written stale hold in place. So "is it still held" is DERIVED
    here rather than stored, which self-heals a hold the operator moved past
    instead of requiring them to notice and clear it.

    A hold the running build has already moved PAST is spent: update_held() is
    False for every release that could still be offered (they are all strictly
    newer than the held one), so the hold cannot suppress anything. Reporting
    it anyway is not merely cosmetic — _app_rollback_row reads this FIRST and
    returns early, so a spent hold strands the operator on a resume gesture
    with nothing to resume AND hides the revert row behind it. That matters
    because clearing the hold to reach "go back" re-arms the rejected release,
    which reopens the very loop this ticket closes (PS-208).

    Deriving it keeps a single source of truth: the row's "is it held" question
    now answers exactly the way update_held() does.

    Fail-soft to "" so an unreadable settings file cannot make the UI claim a
    hold that is not there."""
    try:
        from ...core import settings

        held = settings.app_update_hold()
    except Exception:
        return ""
    if held and is_newer(APP_VERSION, held):
        return ""
    return held


def resume_app_updates(log=None) -> None:
    """Clear the hold: the operator saying "go forward again". The next check
    offers the release they had rejected once more."""
    try:
        from ...core import settings

        settings.set_app_update_hold("")
    except Exception as e:
        if log:
            log(f"Update: couldn't clear the hold ({e})")
        return
    if log:
        log("Update: automatic updates resumed")


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


def _apply_windows(staged: str, say) -> bool | None:
    """Hand the downloaded installer control, then relaunch persona ourselves.

    The installer has a fixed AppId, so it upgrades the existing install in
    place (old files removed, one entry in Programs and Features). A running
    .exe can't replace itself, but a SEPARATE installer process can replace it
    while persona exits — what Chrome/Discord-style updaters do. Returns False
    with the current version intact on any failure before the handover; does not
    return on success (this process exits so the installer can overwrite it).
    """
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
    if not _verify_or_discard(staged, say):
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
    if exe:
        _schedule_windows_relaunch(exe, staged, installer, say)
    # Exit now so the installer can overwrite our files.
    _exit_for_relaunch(say)


def _schedule_windows_relaunch(exe: str, staged: str, installer, say) -> None:
    """Start the invisible cmd that relaunches persona once the installer is
    done. Never raises — a failed relaunch must not stop the update itself.

    Relaunch persona OURSELVES after the installer finishes, in this same
    (normal) user context — NOT from the installer's [Run] entry. The
    installer's runasoriginaluser relaunch ran persona in a lowered-token shell
    where the flet/Flutter client came up to a black window that never painted.
    An invisible cmd waits for the whole installer process FAMILY to exit — by
    image name, because the setup.exe elevates via UAC and the pid Popen gave us
    is only the un-elevated broker that dies as soon as the user clicks Yes
    (#174) — then starts the new persona.exe. The cmd chain is spawned by THIS
    un-elevated persona before it exits, so everything it starts (including the
    new persona) stays at normal user integrity; the elevated installer never
    touches the relaunch.
    """
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


def _verify_or_discard(staged: str, say) -> bool:
    """Verify the staged artifact and DELETE it when it doesn't verify, so a
    full-size corrupt/substituted file isn't matched again by find_ready_staged
    and re-run on the next attempt. The verify itself is fail-closed."""
    if verify_staged_installer(staged, log=say):
        return True
    try:
        os.remove(staged)
    except OSError:
        pass
    return False


def _exit_for_relaunch(say) -> None:
    """Flush and exit so whatever we just scheduled can replace our files."""
    say("Update: restarting…")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


def _apply_linux(staged: str, extra_args, say) -> bool:
    """Replace the running AppImage with the staged download and re-exec into it
    — but ONLY after proving the new binary actually launches, and with the old
    binary RETAINED: restored if the swap fails, and kept beside the new one if
    it succeeds. This can never leave a non-launchable AppImage in place (the
    v2.1.3 brick), and it always leaves a way back.

    That second half is what this docstring used to promise and not deliver.
    "kept as a backup that is restored if the swap fails" was true only of the
    FAILURE path: the success path handed the swap to atomic_replace, which
    dropped its backup the instant the replace returned. So the arm where
    everything went wrong recovered cleanly and the arm where everything went
    RIGHT kept nothing — and Linux is the one platform that installs unattended
    by default (see the auto-update branch in ui/app.py), so it was also the
    platform most likely to need a way back and the only one without one.
    verify_appimage_runs catches a corrupt runtime BEFORE the swap, but its own
    docstring bounds it precisely — it never runs the payload — so it cannot
    catch a build that is authentically what CI published, passes its sha256,
    extracts cleanly and then does not work. Pre-verification and reversibility
    are complementary; this is the second one.

    See rollback_target / revert_to_previous_build for the way back the
    retention makes expressible."""
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
    # do — the completeness check at download time isn't an integrity gate. A
    # mismatch refuses and deletes the bad file so it isn't matched again.
    if not _verify_or_discard(staged, say):
        return False

    # 1) Prove the new AppImage launches on THIS host before touching the live
    #    one. If it can't, we abort and the user stays on the working version.
    say("Update: verifying the new build…")
    if not verify_appimage_runs(staged):
        say("Update: the new build didn't launch here — keeping the current "
            "version. The download is saved; it will be retried.")
        return False

    # 2) Back up the working binary, then swap in the verified new one. If the
    #    swap fails, the backup is restored so we never lose a launchable app;
    #    if it SUCCEEDS the backup now stays, which is the whole point of this
    #    slice. retain_backup is opt-in precisely so the OTHER caller of this
    #    shared helper — the engine binary install at
    #    services/engine/updater.py — keeps dropping its backup exactly as
    #    before rather than silently changing behaviour with us.
    #
    #    Pre-clean the revert's parked binary first, for the same reason
    #    _apply_macos rmtree's `app + ".reverting"` beside its stale `.bak`: a
    #    revert that is refused after moving the current binary aside can leave
    #    one behind, and without this the only thing that would ever remove it
    #    is the NEXT revert's own pre-clean — so an operator who reverted once
    #    and never again would keep a whole extra AppImage forever. Bounding it
    #    HERE, beside the retention it belongs to, is what keeps the policy a
    #    DEPTH and not a duration: exactly one retained binary, replaced by each
    #    update, with no second cleanup path and no timer.
    _remove_any(target + ".reverting")
    if not atomic_replace(
        staged, target, mode=0o755, log=say, retain_backup=True
    ):
        return False
    say("Update: keeping the previous version — you can go back to it if the "
        "new one doesn't work.")

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


def apply_and_restart(staged: str, extra_args=None, log=None) -> bool | None:
    """Install the staged update for THIS platform and restart into it.

    Dispatch only — each platform's strategy lives in its own function
    (_apply_windows / _apply_macos / _apply_linux), which is what makes the
    per-platform discipline readable: hand off to the installer on Windows, swap
    the .app bundle on macOS, verify-then-swap-then-execv the AppImage on Linux.

    THE RETURN VALUE IS NOT A RESULT. On success this call DOES NOT RETURN —
    every platform either execv's or os._exit()s — so a returned False means
    "the update did not happen, and `log` explains why", and there is no value
    that means success. The old `-> bool` promised an answer that two of the
    three platforms could never deliver (the source carried `# unreachable on
    success` twice); callers correctly ignore it and simply carry on with the
    current version when this returns.
    """

    def say(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    if _platform.IS_WINDOWS:
        return _apply_windows(staged, say)
    if _platform.IS_MACOS:
        # verify the dmg, mount it, swap the installed .app for the one inside
        # (old bundle kept as a backup until the copy succeeds), then relaunch
        # once this process exits.
        return _apply_macos(staged, say)
    if not _platform.IS_LINUX:
        say("Update available — download the new version from the releases page.")
        return False
    return _apply_linux(staged, extra_args, say)
