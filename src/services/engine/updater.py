"""fingerprint-chromium engine version check + download, per OS.

The engine lives in ENGINE_DIR. We track the installed version in
ENGINE_DIR/version.txt and compare it against the latest GitHub release. The
release ships a different asset per OS — a Linux AppImage, a Windows zip
(containing chrome.exe), and a macOS dmg — so download/install branches on the
running platform while the launcher always finds the binary at the path
platform.fingerprint_chromium_filename() resolves to.
"""

import json
import os
import shutil
import threading
import urllib.request

from ...core.config import ENGINE_DIR
from ...core import platform as _platform
from ...utils import httpdl
from ...utils.httpdl import atomic_replace, range_opener, resumable_download
from . import policy

ENGINE_BINARY = os.path.join(ENGINE_DIR, _platform.fingerprint_chromium_filename())
VERSION_FILE = os.path.join(ENGINE_DIR, "version.txt")
# Written LAST, after a whole engine is in place, so is_installed() can tell a
# complete install from a half-extracted one (a Windows zip drops chrome.exe
# and its DLLs as separate files — the binary can appear before its libraries).
MARKER_FILE = os.path.join(ENGINE_DIR, ".engine-complete")
# Written when an install STARTS and removed once it succeeds, so an install
# that dies midway leaves a mark that outlives the process. This is what makes
# the completeness gate work on an UPGRADE: clearing MARKER_FILE alone is inert
# there, because version.txt (which nothing ever removes — deliberately, it's
# the provenance record) keeps answering the gate on its own.
INSTALLING_NAME = ".engine-installing"
# Where the PREVIOUS build is renamed to while a new one is promoted into place,
# so a failed upgrade can be rolled back instead of leaving a half-new tree with
# nothing to return to. Lives inside ENGINE_DIR on purpose: the rename must stay
# on one filesystem to be atomic and free. The name deliberately collides with
# neither the ".staging" prefix _install_windows/_install_macos use (and whose
# absence is asserted after a clean install) nor the marker/sentinel names above.
BACKUP_NAME = ".engine-backup"
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


def _installing_file() -> str:
    """Path of the in-progress sentinel, resolved against ENGINE_DIR at CALL
    time rather than frozen at import. Everything else here is a module
    constant, so this asymmetry is deliberate: a test (or any caller) that
    repoints ENGINE_DIR must not be able to strand a sentinel in the operator's
    REAL engine dir, where it would make a perfectly good engine read as
    not-installed forever."""
    return os.path.join(ENGINE_DIR, INSTALLING_NAME)


def _install_complete() -> bool:
    """True when a whole engine finished installing. The completion marker is
    written last by download_engine; an engine installed before the marker
    existed has version.txt (also written last, by ensure_engine), so accept
    that as the legacy completion signal rather than force a re-download.

    The in-progress sentinel VETOES both signals. Clearing MARKER_FILE alone
    cannot express "an install is running" on an upgrade, because version.txt is
    already there and nothing ever removes it — so the gate answered True over a
    half-promoted tree (part old build, part new) on exactly the upgrade case it
    was written for. The sentinel outlives a crashed install, so the failure
    mode is a needless re-download, never a launch of a mixed engine."""
    if os.path.exists(_installing_file()):
        return False
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

    This is the project-wide missing-checksum policy, and it now lives in one
    place (utils.httpdl) that the app updater shares — the app path used to fail
    OPEN on the identical situation.
    """
    return httpdl.verify_bytes(data, digest, allow_missing=allow_missing)


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

    This is the RAW fetch: it reports what upstream published and applies no
    policy. Anything that INSTALLS should call fetch_latest_checked() instead,
    which runs the same fetch through the known-bad list and the tested-major
    ceiling (see engine/policy.py) and blanks the URL when a build is refused.
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


def fetch_latest_checked(timeout: int = 20) -> tuple[str, str, str, str, str]:
    """The governed fetch: (tag, url, digest, verdict, message).

    Same network call as fetch_latest_full(), then persona's own policy is
    applied to the tag that came back (see engine/policy.py):

    * ``verdict == policy.OK`` — installable; url/digest are the fetch's.
    * ``policy.KNOWN_BAD`` — this exact build is on persona's known-bad list.
    * ``policy.ABOVE_CEILING`` — above a ceiling the OPERATOR set in their own
      engine policy file. persona ships no Chromium ceiling since PS-42 (the
      advertised version is derived from the installed engine, so there is no
      constant for an engine to get ahead of), so this verdict is unreachable
      unless someone asked for it. It needs an edit to THEIR file — not a
      persona update, which would not lift a limit persona did not impose.

    On a refusal the URL and digest are BLANKED while the tag is preserved. That
    shape is deliberate: a caller that ignores the verdict still cannot install
    the build (download_engine("") returns False), and a caller that reads it can
    still name the version in the message it shows. Fail-closed by construction
    rather than by everyone remembering to check.

    ``message`` is operator-facing and empty when OK. It exists so the UI can
    distinguish "persona refused this" from "the download failed" — the Firefox
    path already draws that line via its `compatible` flag, and the two engines
    must not report the same situation differently.
    """
    tag, url, digest = fetch_latest_full(timeout)
    verdict, message = policy.check(tag)
    if verdict != policy.OK:
        return tag, "", "", verdict, message
    return tag, url, digest, verdict, message


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
    engine.

    The transfer itself is utils.httpdl's resumable_download, shared with the app
    updater. The opener is built here, through this module's range_opener(), so
    the Range header survives GitHub's 302 to a signed CDN URL — without that a
    resume gets the whole file (200) instead of the tail (206) and over Tor never
    finishes.
    """
    return resumable_download(
        path,
        url,
        timeout,
        digest,
        progress=progress,
        allow_missing=allow_missing,
        opener_factory=lambda: range_opener(),
    )


def _install_linux(asset_path: str) -> bool:
    """The downloaded AppImage IS the engine; make it executable in place.

    Via the shared atomic replace, so a failed swap restores the engine that was
    working instead of leaving none at all — the rollback the app updater always
    had for its own AppImage and this path silently lacked."""
    return atomic_replace(asset_path, ENGINE_BINARY, mode=0o755)


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
    first) keeps the window where the engine is incomplete as small as possible.

    The previous build is REVERSIBLE, not destroyed: each entry about to be
    replaced is RENAMED into BACKUP_NAME first, and if any part of the promotion
    raises, every entry already moved aside is put back before the error
    propagates — and entries the NEW build introduced, which had no previous
    counterpart to restore over, are removed. So a failed upgrade leaves the
    build that was working, rather than a tree that is part old and part new
    with no way back to either.

    A backup is dropped only once it is provably redundant: after a clean
    promotion, or after every entry has been confirmed restored. If a restore
    could NOT be completed, the backup is deliberately LEFT in ENGINE_DIR — it
    is then the last surviving copy of the working build, and deleting it would
    turn a failed upgrade into no engine at all.

    That is deliberately belt AND braces with the completion marker/sentinel:
    the marker (written afterwards) still gates LAUNCH, so a failure is
    detectable; this makes it recoverable. Detectability alone left an operator
    with no engine at all until a fresh download of a *newer* build succeeded —
    which needs the network, and cannot return you to the build you had.

    The backups live inside ENGINE_DIR so the rename is same-filesystem (atomic,
    O(1), no second copy of a ~300-600MB tree, and signatures/permissions
    survive because nothing is rewritten).
    """
    backup_root = os.path.join(ENGINE_DIR, BACKUP_NAME)
    httpdl.discard_aside(backup_root)  # a stale backup from an earlier crash
    # dst -> its backup path, for every entry we actually moved aside
    moved: list[tuple[str, str]] = []
    # Entries the NEW build introduced that the previous one never had. They
    # have no backup to restore over, so rolling back means REMOVING them —
    # otherwise a rolled-back tree is still part old and part new, and a
    # Chromium upgrade routinely adds files.
    added: list[str] = []
    try:
        os.makedirs(backup_root, exist_ok=True)
        for name in os.listdir(staging):
            src = os.path.join(staging, name)
            dst = os.path.join(ENGINE_DIR, name)
            backup = os.path.join(backup_root, name)
            if httpdl.move_aside(dst, backup):
                moved.append((dst, backup))
            else:
                added.append(dst)
            shutil.move(src, dst)
    except Exception:
        # Put the working build back, best-effort, then let the caller see the
        # failure. Restore never raises, so a failed rollback cannot turn a
        # reported install failure into a crash.
        fully_restored = True
        for dst, backup in reversed(moved):
            if not httpdl.restore_aside(backup, dst):
                fully_restored = False
        for dst in reversed(added):
            httpdl.discard_aside(dst)
        if fully_restored:
            httpdl.discard_aside(backup_root)  # nothing left in it to recover
        # else: LEAVE the backup. It holds the only copy of at least one file
        # of the working build, and an operator can put it back by hand.
        raise
    # The new build is fully in place; the old one is no longer needed.
    httpdl.discard_aside(backup_root)


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
        # Move the previous bundle aside rather than deleting it, so a failed
        # swap restores the Chromium.app that was working. A RENAME, not a copy:
        # it is atomic, costs no extra disk, and preserves the signature and
        # resource forks exactly — a copytree'd backup would restore a bundle
        # Gatekeeper refuses to launch, which is not a rollback.
        backup = os.path.join(ENGINE_DIR, BACKUP_NAME + "-Chromium.app")
        httpdl.discard_aside(backup)
        had_previous = httpdl.move_aside(dest, backup)
        # A backup is dropped only once it is provably redundant: the new
        # bundle is in place, or the previous one has been CONFIRMED restored.
        # A restore that could not be completed leaves the backup where it is —
        # it is then the only surviving copy of the working Chromium.app, and
        # deleting it would turn a failed upgrade into no engine at all.
        try:
            os.replace(staging, dest)
            ok = os.path.isfile(ENGINE_BINARY)
        except OSError:
            if had_previous and httpdl.restore_aside(backup, dest):
                httpdl.discard_aside(backup)
            return False
        if not ok and had_previous:
            # The new bundle landed but has no runnable binary inside it — a
            # broken engine is no better than a failed swap, so go back.
            if httpdl.restore_aside(backup, dest):
                httpdl.discard_aside(backup)
            return ok
        httpdl.discard_aside(backup)
        return ok
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
        # ...and state the in-progress fact POSITIVELY. The clear above is inert
        # on an upgrade (version.txt is already there and nothing removes it), so
        # the sentinel is what actually makes is_installed() False from here
        # until the marker is written. Same lock as the marker, on purpose: the
        # two must not split across it.
        try:
            with open(_installing_file(), "w", encoding="utf-8") as f:
                f.write("installing")
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
            # Clear the in-progress sentinel only now the install succeeded.
            # As forgiving as the marker write above: a swallowed OSError here
            # costs a needless re-download next start, whereas raising would
            # turn a SUCCESSFUL install into a reported failure.
            try:
                os.remove(_installing_file())
            except OSError:
                pass
        # A FAILED install deliberately leaves the sentinel behind — that is the
        # whole point: is_installed() stays False across the crash, so
        # ensure_engine re-installs on next start instead of launching a tree
        # that is part previous build, part new one.
        return ok


def ensure_engine(
    progress=None, timeout: int = 600, attempts: int = 3, log=None
) -> tuple[bool, str]:
    """Make sure the engine is installed. If already present, no-op. Otherwise
    fetch the latest release and download it. Returns (ok, message).

    The GitHub releases API call and the download each occasionally fail on a
    transient network hiccup (a dropped connection, a flaky first request on a
    cold start). Retry the whole fetch+download a few times so one blip doesn't
    leave the engine uninstalled — the same treatment the Firefox engine gets.

    ``log`` (optional) receives every operator-facing note this path produces:
    the reason for a refusal (persona declining a build) and the reason for any
    failure (the network). That wording lives HERE, in one place, because the
    refuse/failed distinction is only correct if one owner draws it — the two
    callers each got it wrong in a different way when they owned it (onboarding
    discarded the reason entirely; the sidebar prefixed a governance refusal
    with "Engine download failed:", blaming the network for a decision persona
    made). Callers log the returned message verbatim or not at all.
    """
    if is_installed():
        return True, "engine present"
    last = "could not reach GitHub releases"
    for _ in range(max(1, attempts)):
        # The raw fetch, with policy applied HERE rather than via
        # fetch_latest_checked(), because the first install answers the
        # ABOVE_CEILING verdict differently from an update and so needs the URL
        # the checked fetch deliberately blanks.
        tag, url, digest = fetch_latest_full()
        verdict, message = policy.check(tag)
        # THE FIRST INSTALL IS NOT AN UPDATE, and the two verdicts are not the
        # same kind of claim — so this path treats them differently on purpose.
        #
        # KNOWN_BAD means "this exact build is broken". Installing it would
        # produce a broken engine, so refuse: no engine and a clear reason beats
        # an engine that does not work and no reason. Retrying cannot change the
        # answer, so return rather than burn the remaining attempts.
        if verdict == policy.KNOWN_BAD:
            # Log HERE, not at the call sites. The reason has to reach the
            # operator, and returning it in the tuple is not enough: the
            # onboarding caller discards the message entirely (it only uses
            # `ok`), and the sidebar caller renders it as "Engine download
            # failed: ..." — the exact words this ticket exists to stop using
            # for a decision persona made. Logging inside the branch fixes both
            # call sites at once and keeps the refuse/failed distinction in one
            # place, next to the code that draws it.
            if log:
                log(message)
            return False, message
        # ABOVE_CEILING is now an OPERATOR instruction, and that inverted this
        # branch's answer (PS-42). It used to mean "persona has not been SHOWN
        # to work against this" — persona's own soft self-assessment, recorded
        # in a shipped constant. Overriding a soft claim to avoid leaving the
        # app with no browser at all was a defensible trade, because the
        # operator could not lift that limit without a persona release: refusing
        # would have stranded them with no engine and no local remedy.
        #
        # persona ships no ceiling now. The only way to reach this verdict is an
        # operator who set max_tested_major in their own policy file, so the two
        # halves of the old trade both flipped:
        #
        #   - Installing anyway does not override a self-assessment any more, it
        #     overrides an EXPLICIT INSTRUCTION. They said "do not install above
        #     major N"; persona is not entitled to do it anyway because it would
        #     prefer to have a browser.
        #   - The refusal is no longer a dead end. policy.check()'s message names
        #     their file and the one-line edit that lifts it, so an operator who
        #     did not mean to pin themselves out of an engine is one edit away
        #     from an install — recoverable locally, without a persona release.
        #
        # So this now answers exactly as KNOWN_BAD does, and for the same
        # reason: persona declining a build, said in those words, beats persona
        # substituting its own judgement for the operator's. Retrying cannot
        # change a verdict that comes from a local file, so return rather than
        # burn the remaining attempts.
        if verdict == policy.ABOVE_CEILING:
            # Logged HERE for the same reason KNOWN_BAD is: the onboarding
            # caller discards the returned message and the sidebar would prefix
            # it with "Engine download failed:", blaming the network for a
            # decision the OPERATOR made. The message already names their file
            # and the edit; do not dress it up as an engine problem.
            if log:
                log(message)
            return False, message
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
    # The other operator-facing exit. Prefixed with "Engine download failed"
    # BECAUSE THAT IS WHAT THIS ONE IS — a network/transfer failure, worth
    # retrying. The KNOWN_BAD return above deliberately carries no such prefix:
    # that is persona declining a build, and retrying cannot change it. Both
    # lines are emitted from this function so the two can never again be worded
    # by a caller that cannot tell them apart.
    if log:
        log(f"Engine download failed: {last}")
    return False, last
