"""Personium engine version check + download, per OS.

The engine lives in ENGINE_DIR. We track the installed version in
ENGINE_DIR/version.txt and compare it against the newest published Personium
release. Releases are published in persona's OWN repository alongside the
application (see RELEASING.md), tagged `personium-<chromium version>` and marked
as prereleases; each ships a different asset per OS — a Linux AppImage, a
Windows zip (containing chrome.exe), and a macOS dmg — so download/install
branches on the running platform while the launcher always finds the binary at
the path platform.fingerprint_chromium_filename() resolves to.

⚠️ THE APPLICATION'S OWN RELEASES LIVE IN THAT SAME REPOSITORY, and this module
must never select one. See is_engine_tag and _asset_matches — two independent
guards, either of which excludes every application release on its own.
"""

import json
import os
import shutil
import threading
# Retained deliberately though this module no longer calls urlopen itself: the
# direct send now happens in services/egress.py, and `updater.urllib.request` is
# the SAME module object egress resolves — which is what lets the existing
# tests patch one attribute and cover both. See fetch_latest_full.
import urllib.request

from ...core.config import ENGINE_DIR
from ...core import platform as _platform
from ...services import egress
from ...utils.atomic import atomic_write_json
from ...utils import httpdl
from ...utils.httpdl import atomic_replace, resumable_download
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
# WHICH builds this machine has run, as identities rather than as trees: a JSON
# record holding {"current": {tag, digest}, "previous": {tag, digest}}.
#
# This is the whole of what makes a SUCCESSFUL swap reversible, and it is a few
# hundred bytes rather than a second ~300-600MB engine. The tree of the previous
# build is still destroyed on the success path exactly as it always was (see
# _promote_staging) — what survives is its NAME and the digest it was verified
# against, which is enough to fetch it again.
#
# THE NAME WAS THE MISSING PIECE, NOT THE BYTES. version.txt has exactly one
# slot: the moment a swap succeeds, write_version overwrites the tag of the
# build that was working and it exists nowhere on the machine. So an operator
# facing a bad unattended upgrade could not roll back — not because the old tree
# was gone, but because nothing recorded WHICH build to go back to, and
# "the previous one" had no referent.
#
# THE PAIR IS ONE UNIT. A tag without the digest it was verified against would
# force the rollback to trust whatever a fresh API response advertises for that
# tag, which is exactly the check PS-49 exists to prevent — so a record that
# carries no digest is treated as no rollback target at all, not as a rollback
# to be attempted unverified.
#
# DEPTH 1, deliberately. A second successful swap replaces "previous"; this is
# not a version history and there is no way back to an arbitrary older build.
# Matches the Firefox side's policy (engine_install.rollback_target).
BUILDS_FILE = os.path.join(ENGINE_DIR, "builds.json")
# WHERE PERSONIUM RELEASES COME FROM — OUR OWN REPOSITORY, ALONGSIDE THE APP.
#
# This used to be adryfish/fingerprint-chromium, the upstream that has stopped
# (last commit 2026-06-21; its newest tag ships no source tree). The owner's
# decision, 2026-09-03: the application and the engine only work as a pair and
# are versioned together, so Personium releases are published in persona's own
# repository. See RELEASING.md for the whole scheme.
#
# THE CONSEQUENCE THAT SHAPES THIS WHOLE MODULE: both updaters now read the SAME
# repository's releases, and neither may ever select the other's artifact. Three
# independent guards keep them apart (RELEASING.md § Why this matters); two of
# them live here — the tag filter (`is_engine_tag`) and the asset rule
# (`_asset_matches`) — and each excludes every application release ON ITS OWN.
ENGINE_REPO = "amnesiadevelopment/persona"

# What marks a release as an ENGINE release rather than an application one. The
# app tags `vX.Y.Z`; Personium tags `personium-<chromium version>`, which keeps
# engine releases sortable among themselves and unmistakable against the app's.
ENGINE_TAG_PREFIX = "personium-"

# THE RELEASES LIST, NOT `/releases/latest`, AND THAT IS LOAD-BEARING.
#
# Engine releases are published as PRERELEASES, so that the app's one
# `releases/latest` pointer stays on the application (verified 2026-09-04
# against neovim/neovim, whose 11-day-newer `nightly` prerelease does not take
# that pointer). `/releases/latest` would therefore never return an engine
# release at all — it has to be found by enumerating and filtering by tag, the
# same shape the Firefox updater already uses for the same reason (its upstream
# also publishes releases this one must not pick).
#
# NO RATE-LIMIT REGRESSION AND NO TOKEN (PS-216). This is the same
# api.github.com rate class the `/releases/latest` API call it replaces was in —
# 60/hour unauthenticated per IP — so nothing got cheaper or more expensive.
# The APP updater deliberately avoids the API entirely by reading the
# rate-limit-free redirect, and that trick is unavailable here precisely because
# the pointer it reads is the one engine releases must not take. Do NOT "solve"
# a rate limit by adding a token: installs stay unauthenticated and proxied.
RELEASES_API = (
    f"https://api.github.com/repos/{ENGINE_REPO}/releases?per_page=30"
)
# The by-tag sibling of RELEASES_API. Same document shape, so fetch_release_full
# below is a variant of fetch_latest_full rather than a second mechanism.
RELEASE_BY_TAG_API = (
    f"https://api.github.com/repos/{ENGINE_REPO}/releases/tags/{{tag}}"
)

# Serialises concurrent installs (the UI update thread and ensure_engine can
# both reach download_engine) so two extracts don't race into ENGINE_DIR.
_install_lock = threading.Lock()

# The oracle an UNATTENDED install consults before replacing the tree — a
# zero-arg callable returning True while any profile is running.
#
# Chromium keeps ONE un-versioned tree and every install path replaces entries
# of it IN PLACE, so an install that lands while a profile is executing from
# that tree swaps the binary and its resources under a live session. POSIX does
# not refuse that os.replace — only Windows does, by accident of its sharing
# rules — so on Linux/macOS it is silent corruption, not a loud failure. The
# only defence is to ASK.
#
# Injected rather than imported, exactly as the Firefox side does it
# (browser/engine_install.set_in_use_provider): updater sits BELOW the launcher
# and the UI in the layering and cannot import running_profile_names itself.
# Keeping it injected also keeps the POLICY out of the updater — this module
# learns whether the tree is busy, never what a profile is.
_in_use_provider = None  # Callable[[], bool] | None


def set_in_use_provider(fn) -> None:
    """Wire the oracle an unattended install consults before replacing the tree.

    `fn` is a zero-arg callable returning True while any profile is running.
    Called once at startup by the UI, which owns the launcher; passing None
    clears it. Only consulted by download_engine(defer_if_in_use=True) — the
    operator's explicit click and the first-install path are deliberately
    unaffected (see download_engine)."""
    global _in_use_provider
    _in_use_provider = fn


def _engine_in_use(log=None) -> bool:
    """True when the wired provider reports a running profile.

    Fails CLOSED — no provider, or a provider that raises, both read as "in
    use". That is deliberately the OPPOSITE of engine_install._engine_in_use's
    fail-open default, because the two guards protect different things at
    different costs. There, a broken oracle must not permanently wedge DISK
    RECLAMATION, and the cost of proceeding is bounded. Here the cost of a
    false "idle" is replacing a running browser's binary underneath it, while
    the cost of a false "in use" is that an unattended update waits for the
    next hourly check and installs from bytes already on disk. An unwired
    provider is the same argument: this is only ever reached from the
    unattended path, which exists only inside the app that wires it, so
    "unwired" means something is wrong rather than "no UI is present"."""
    fn = _in_use_provider
    if fn is None:
        if log:
            log(
                "Chromium engine: no in-use oracle wired — deferring the "
                "unattended install rather than risking a live session"
            )
        return True
    try:
        return bool(fn())
    except Exception as e:
        if log:
            log(
                f"Chromium engine: in-use check failed ({e!r}) — deferring the "
                "unattended install"
            )
        return True


def current_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is here because it is NOT an `OSError` — it
        # inherits from `ValueError`, so an undecodable version.txt (a torn
        # write, disk corruption, an external edit) walked straight through an
        # `OSError`-only arm. This value is the SOLE source of the Chromium
        # version an Android profile advertises: `engine_version.parse` turns
        # it into the named `EngineVersionUnreadableError` refusal that
        # `browser/process.py` catches BY TYPE to refuse the launch. A
        # `UnicodeDecodeError` is not that type, so it sailed past that gate
        # and reached the operator as a raw traceback instead of the
        # actionable "run an engine check" sentence. Returning "" restores
        # this function's own contract — the same answer a missing file gets,
        # and the input `parse` already refuses by name.
        #
        # Named and narrow, mirroring `_from_file` in `verify/exit_guard.py`
        # and `_read_builds` fifteen lines below: NOT bare `Exception`, and
        # NOT `errors="replace"` — a version persona cannot decode must not be
        # made to PARSE as something, which is the opposite of the fail-closed
        # contract this whole module is built around.
        return ""


def _read_builds() -> dict:
    """The build record, or {} when there is none / it is unreadable.

    Degrades to {} rather than raising, on the same reasoning as
    engine_install.pinned_build(): this is consulted on paths that must keep
    working (the update check, the UI row), and an unreadable record must mean
    "no rollback offered", never "every launch breaks"."""
    try:
        with open(BUILDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _entry(rec: dict, key: str) -> tuple[str, str]:
    """(tag, digest) out of one slot of the record, or ("", "") when the slot is
    absent or malformed.

    BOTH OR NEITHER: a slot carrying a tag but no digest returns ("", ""), so a
    caller can never end up fetching a tag it has nothing to verify against. See
    BUILDS_FILE — that is the PS-49 check, enforced at the read rather than left
    to each caller to remember."""
    slot = rec.get(key)
    if not isinstance(slot, dict):
        return "", ""
    tag = slot.get("tag") or ""
    digest = slot.get("digest") or ""
    if not isinstance(tag, str) or not isinstance(digest, str):
        return "", ""
    if not tag or httpdl.digest_missing(digest):
        return "", ""
    return tag, digest


def record_installed_build(tag: str, digest: str) -> None:
    """Record `tag` as the build now installed, demoting the one it replaced to
    "previous" — the few bytes that make a SUCCESSFUL swap reversible.

    CALL THIS BEFORE write_version, always. version.txt has one slot, so once it
    is overwritten the identity of the build being replaced is gone from the
    machine and there is nothing left to demote. The ordering is the whole
    mechanism, not a detail of it.

    What gets demoted is the record's OWN "current" slot, not current_version():
    the pair (tag, digest) has to travel together, and version.txt holds no
    digest. A machine that upgraded before this record existed therefore gets no
    rollback target from its first swap — it has a version.txt but no digest for
    it, and inventing one by asking upstream is exactly the fresh-API-response
    trust PS-49 refuses. The second swap records normally.

    DEPTH 1: the outgoing "previous" is dropped, not kept. A re-install of the
    SAME tag is not a swap and must not demote a build over itself — that would
    make the rollback target the build you are already on and quietly destroy
    the real one.

    Best-effort like write_version beside it: a failed record costs the ability
    to roll back, whereas raising would turn a SUCCESSFUL install into a
    reported failure."""
    if not tag:
        return
    rec = _read_builds()
    cur_tag, cur_digest = _entry(rec, "current")
    out = {"current": {"tag": tag, "digest": httpdl.normalize_digest(digest)}}
    if cur_tag and cur_tag != tag:
        out["previous"] = {"tag": cur_tag, "digest": cur_digest}
    else:
        # Same tag re-installed (or nothing recorded yet): carry the existing
        # previous through untouched rather than demoting a build over itself.
        prev_tag, prev_digest = _entry(rec, "previous")
        if prev_tag and prev_tag != tag:
            out["previous"] = {"tag": prev_tag, "digest": prev_digest}
    try:
        atomic_write_json(BUILDS_FILE, out)
    except OSError:
        pass


def rollback_target() -> tuple[str, str]:
    """The (tag, digest) an operator can go BACK to, or ("", "") when there is
    nothing to go back to.

    This is the whole "can I undo this update?" question as one call, exactly as
    engine_install.rollback_target is for Firefox: ("", "") means the gesture
    must not be offered, because a revert with no recorded previous build is a
    button that cannot work."""
    return _entry(_read_builds(), "previous")


def current_build_recorded() -> bool:
    """True when the record names the build that is installed RIGHT NOW.

    The mirror of rollback_target() next door: that one reads "previous" and
    answers "can I go back?", this one reads "current" and answers "will the
    NEXT swap have something to demote?". Both are one-call questions about the
    record, kept here rather than reconstructed by callers poking at
    BUILDS_FILE.

    WHY A CALLER NEEDS THIS (PS-172). Two different machines both have an engine
    installed and an empty rollback_target(), and the honest thing to tell their
    operators is NOT the same sentence:
      * recorded — a clean install. ensure_engine wrote "current", so the very
        next swap demotes it and the operator can go back after ONE update.
      * NOT recorded — a machine that upgraded into v3.0.0 with an engine
        already present. ensure_engine short-circuited on is_installed() and
        wrote nothing, so its next swap has no "current" to demote and records
        only the incoming build; the swap AFTER that is the first reversible
        one. TWO updates, not one.
    Telling the second machine "after the next update" would be a promise it
    watches fail — the same defect as the silence it replaces, only louder.

    Note this is deliberately about the RECORD, not about version.txt: a machine
    can have a perfectly good current_version() and nothing recorded, and that
    gap is precisely the state being detected."""
    return bool(_entry(_read_builds(), "current")[0])


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
    opts in with allow_missing. A present-but-wrong digest is always rejected.

    NOTHING IN persona PASSES allow_missing=True ANY MORE (PS-49). The one
    caller that did was the engine download's Linux predictable-URL fallback,
    and it turned out not to need it: upstream publishes a sha256 for every
    asset persona matches, so the engine path now verifies on every OS with no
    platform carved out. The parameter survives because it is httpdl's
    project-wide vocabulary — `digest_missing` vs "a digest arrived and is
    unusable" is a distinction worth keeping expressible and tested — not
    because any path is entitled to it. If you find yourself reaching for it,
    the question to answer first is why a digest cannot be had, because last
    time the answer was "it can".

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


def is_engine_tag(tag: str) -> bool:
    """True when a release tag names a PERSONIUM ENGINE release.

    THE FIRST OF THE TWO GUARDS THIS MODULE OWNS, and the one that runs first:
    persona's own repository carries application releases (`vX.Y.Z`) beside
    engine ones (`personium-<version>`), and an engine install that considered
    an application release at all would be one loosened asset rule away from
    installing the app as the browser. So application releases are not merely
    outranked here, they are never candidates.

    Deliberately NOT "it is a prerelease". The prerelease marking is what keeps
    engine releases off the app's `releases/latest` pointer, but it is a box a
    person ticks by hand at release time, and a guard that rests on it would
    fail exactly when that hand slipped. This reads the TAG, which is the same
    string the version is derived from and cannot be got wrong independently."""
    return (tag or "").startswith(ENGINE_TAG_PREFIX)


def version_from_tag(tag: str) -> str:
    """'personium-152.0.7977.75' -> '152.0.7977.75'; anything else unchanged.

    THE PREFIX MUST NOT TRAVEL PAST THIS MODULE'S API BOUNDARY. version.txt is
    the SOLE source of the Chromium version an Android profile advertises
    (`current_version` -> `browser/engine_version.parse`), so a prefixed string
    recorded there would leak straight into what a page can read. Everything
    this module hands a caller — `fetch_latest_full`, `fetch_release_full`,
    `ensure_engine` — therefore speaks BARE VERSIONS, exactly as it did when
    upstream's tags were bare, and `engine_tag()` puts the prefix back at the
    one place it is needed (the by-tag URL).

    That also means `policy.check`, `is_newer`, `parse_version`, builds.json and
    every UI reader keep working unchanged, and no on-disk record needs
    migrating."""
    t = tag or ""
    return t[len(ENGINE_TAG_PREFIX):] if t.startswith(ENGINE_TAG_PREFIX) else t


def engine_tag(version: str) -> str:
    """'152.0.7977.75' -> 'personium-152.0.7977.75'; an already-prefixed value
    is returned unchanged. The inverse of version_from_tag, used only where a
    real published tag is required — i.e. the by-tag release URL."""
    v = version or ""
    if not v or v.startswith(ENGINE_TAG_PREFIX):
        return v
    return ENGINE_TAG_PREFIX + v


def _asset_matches(name: str) -> bool:
    """True when a release asset filename is the ENGINE asset for this OS.

    THE SECOND OF THIS MODULE'S TWO GUARDS, and it is anchored at BOTH ENDS on
    purpose. The rule used to be a bare suffix test — on Linux,
    `name.endswith("x86_64.AppImage")` — and the moment engine and application
    assets shared a repository that rule selected `persona-x86_64.AppImage`, the
    APPLICATION's own Linux asset. Reproduced, not predicted (PS-305).

    So an engine asset must carry the `personium-` prefix AND this OS's marker
    (`personium-<version>-linux-x86_64.AppImage`, `-windows-x86_64.zip`,
    `-macos-x86_64.dmg`; see RELEASING.md). EITHER anchor alone already excludes
    every application asset — `persona-` is not `personium-`, and no application
    asset carries an OS-marked engine suffix — which is the point: neither is
    load-bearing by itself, so loosening one does not silently reopen the hole.

    Note the prefix is checked with an explicit separator-bearing constant
    rather than by leaning on `persona` vs `personium` differing by three
    characters: the per-OS suffix is the fuller anchor and both are required."""
    if not name.startswith(ENGINE_TAG_PREFIX):
        return False
    if _platform.IS_WINDOWS:
        return name.endswith("-windows-x86_64.zip")
    if _platform.IS_MACOS:
        return name.endswith("-macos-x86_64.dmg")
    return name.endswith("-linux-x86_64.AppImage")


# THE LINUX PREDICTABLE-URL FALLBACK IS GONE, DELIBERATELY (PS-305).
#
# `appimage_url_for(tag)` built a download URL by string-formatting a tag, for
# releases whose JSON listed no assets. It hardcoded an adryfish download URL
# and could not survive the move to our own repository as written, so it had to
# be re-pointed or removed. REMOVED, for three reasons:
#
#   * It never bought the availability it cost. It fired only when the asset
#     matcher found nothing, and PS-49 measured that on every upstream release
#     where that happened the URL it formatted 404'd. It rescued no real
#     release; it only widened what persona would install without looking.
#   * We cut our own releases now. A release listing no asset for this OS is a
#     BROKEN RELEASE, and the right answer to one is a refusal a person can see
#     and fix — not a guessed URL that installs whatever answers it.
#   * It could not install anything anyway. A guessed URL carries no digest, and
#     since PS-49 a digest-less asset is refused at the transfer on every OS.
#     The fallback's only remaining effect would be to turn a clean "no asset
#     for this OS" into an EngineUnverifiable further down the path.
#
# If a predictable URL is ever wanted again, derive it from RELEASING.md's asset
# table as a named per-OS rule — not as a Linux-only special case.


def _release_asset(data) -> tuple[str, str, str]:
    """Pull (version, asset_url, sha256_digest) for THIS OS out of one GitHub
    release document, or ('','','') when it is not a usable ENGINE release.

    Shared by the latest-release fetch and the by-tag fetch below, which is the
    point: the two endpoints return the SAME document shape, so the selection
    rule — is this an engine release at all, which asset is ours, where its
    digest lives — must be one piece of code. Two copies of it is how a rollback
    quietly starts picking a different asset than an install.

    RETURNS THE BARE VERSION, not the published tag: see version_from_tag for
    why the `personium-` prefix must not travel past this boundary.

    REFUSES RATHER THAN GUESSES. An application release answers ('','','')
    because its tag is not an engine tag; an engine release that lists no asset
    for this OS answers ('','','') too, because the predictable-URL fallback
    that used to paper over that case is gone (see the note above it)."""
    if not isinstance(data, dict):
        return "", "", ""
    tag = data.get("tag_name", "")
    if not is_engine_tag(tag):
        # An APPLICATION release (or anything else published in this repo). Not
        # a candidate at all — see is_engine_tag.
        return "", "", ""
    version = version_from_tag(tag)
    url = ""
    digest = ""
    assets = data.get("assets") or []
    if not isinstance(assets, list):
        return "", "", ""
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "") or ""
        if _asset_matches(name):
            url = asset.get("browser_download_url", "")
            digest = asset.get("digest", "") or ""
            break
    return version, url, digest


def fetch_latest_full(timeout: int = 20) -> tuple[str, str, str]:
    """Return (version, asset_url, sha256_digest) of the newest ENGINE release
    for THIS OS, or ('','','') on failure. Picks the per-OS engine asset.

    ENUMERATES AND FILTERS rather than asking `/releases/latest`, because our
    repository publishes both kinds of release and engine releases are
    prereleases (which `/releases/latest` excludes by design — that is what
    keeps that pointer on the application). Same shape as the Firefox updater's
    fetch, for the same reason.

    THE MAXIMISATION IS OVER ENGINE RELEASES ONLY. An application release is not
    a candidate (is_engine_tag), an engine release with no asset for this OS is
    not a candidate either (_release_asset refuses rather than guesses), and the
    winner is the highest by `parse_version` — NOT simply the first the API
    listed. GitHub returns releases newest-created-first, but an engine release
    published out of order, or a re-published older build, must not be able to
    read as "latest" and downgrade an operator's engine.

    Returns the BARE version, never the `personium-` tag: see version_from_tag.

    This is the RAW fetch: it reports what is published and applies no policy.
    Anything that INSTALLS should call fetch_latest_checked() instead, which
    runs the same fetch through the known-bad list and the tested-major ceiling
    (see engine/policy.py) and blanks the URL when a build is refused.
    """
    try:
        # Through persona's OWN egress policy, never a bare urlopen: this runs
        # unattended at every startup, so it must leave the way the operator
        # said the application's traffic should leave. With no policy set that
        # is a direct send — byte-identical to what this line used to do.
        releases = egress.fetch_json(RELEASES_API, timeout=timeout)
    except Exception:
        return "", "", ""
    best = ("", "", "")
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version, url, digest = _release_asset(release)
        if not version or not url:
            continue
        if not best[0] or parse_version(version) > parse_version(best[0]):
            best = (version, url, digest)
    return best


def fetch_release_full(tag: str, timeout: int = 20) -> tuple[str, str, str]:
    """Return (version, asset_url, sha256_digest) for ONE NAMED engine release,
    or ('','','') when it is not served or is not an engine release.

    The by-tag sibling of fetch_latest_full: same egress authority, same
    document shape, same per-OS asset selection (_release_asset). It exists
    because a rollback needs the URL of a SPECIFIC older build, and the only
    fetch this module had reported whatever is currently newest — which is
    precisely the build being rolled back FROM.

    ACCEPTS A BARE VERSION and puts the `personium-` prefix back for the URL
    (engine_tag), because everything on disk — builds.json, version.txt — holds
    bare versions. An already-prefixed value is accepted too and is not double-
    prefixed, so a hand-edited record naming the real published tag still works.

    ('','','') here is the honest answer to a YANKED OR DELETED RELEASE, and it
    is the trade this whole mechanism was chosen for: persona no longer keeps a
    copy of the previous engine, so if that release stops being hosted the
    rollback target is genuinely unreachable and the operator is where they were
    before this existed. The caller must REPORT that plainly — a rollback that
    silently installs something else is worse than one that refuses. It is also
    the answer for an APPLICATION tag handed to this function by mistake: an
    engine rollback must never resolve to an application release."""
    if not tag:
        return "", "", ""
    try:
        data = egress.fetch_json(
            RELEASE_BY_TAG_API.format(tag=engine_tag(tag)), timeout=timeout
        )
    except Exception:
        return "", "", ""
    return _release_asset(data)


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
    updater. The opener is built here, through persona's OWN egress authority, so
    the Range header survives GitHub's 302 to a signed CDN URL — without that a
    resume gets the whole file (200) instead of the tail (206) and over Tor never
    finishes — AND the ~80-230MB asset leaves the host the way the operator said
    the application's traffic should leave.

    Routing the asset was the half this path was missing. `fetch_latest_full`
    above already asks the authority "may I speak to GitHub, and how?"; until
    PS-75 the binary it located was then fetched by a transport that never
    asked, so one operator gesture governed the metadata poll and not the
    download it exists to locate.

    The verdict is resolved HERE and the opener handed down, rather than the
    shared downloader resolving it: httpdl is a mechanism several pipelines
    call, and a policy implemented twice is one that disagrees with itself
    (services/egress.py). A REFUSE means NOTHING IS SENT — it lands on this
    function's existing False, which is the same answer its caller already
    handles for a failed download, and it is resolved BEFORE the transfer so a
    refusal costs no connection. With no policy set this is byte-identical to
    the direct send it has always been.
    """
    try:
        opener = egress.download_opener()
    except egress.EgressRefused:
        return False
    return resumable_download(
        path,
        url,
        timeout,
        digest,
        progress=progress,
        allow_missing=allow_missing,
        opener_factory=lambda: opener,
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


class InstallDeferred(Exception):
    """Raised by download_engine(defer_if_in_use=True) when the asset is on disk
    and verified but a profile is running, so the tree must not be replaced yet.

    A distinct type because this is NOT a failure and must not be reported as
    one: the network worked, the bytes are good, and the install will happen on
    a later check. Callers that conflate it with False would tell the operator
    "Engine update failed" and blame the network for a decision persona made —
    the exact confusion the refuse/failed vocabulary already exists to prevent."""


class EngineUnverifiable(Exception):
    """Raised by download_engine when upstream published NO sha256 for the asset,
    so its contents cannot be checked and persona refuses to install it.

    A distinct type for the same reason InstallDeferred is one, and the reason
    is the mirror image: InstallDeferred must not be reported as a failure
    because nothing failed, and this must not be reported as one because the
    NETWORK did not fail — the transfer would succeed perfectly. It is a
    refusal, and a refusal an operator can do nothing about by retrying.

    THIS LIVES AT THE TRANSFER, NOT AT A CALLER, ON PURPOSE (PS-49 round 2).
    The refusal was first written in ensure_engine, which is only the FIRST-
    INSTALL path. The sidebar's update path (_update_engine_async) does not call
    ensure_engine at all — it calls download_engine directly — so on a digest-
    less release an existing operator, the one who actually hits this, still got
    "Engine update failed": the network blamed for a decision persona made,
    about a condition retrying cannot change. That is the precise defect the
    ticket named. Raised from inside the shared transfer instead, both entry
    points inherit ONE refusal with ONE wording, and a third caller cannot be
    added that misses it."""


def _unverifiable_message(url: str, tag: str = "") -> str:
    """The FOURTH operator-facing message, in one place.

    There are already three distinct situations with three distinct wordings —
    a known-bad build, a build above the operator's ceiling, and a failed
    transfer. An unverifiable asset is a fourth and must not be folded into any
    of them: an operator told "download failed" retries forever against
    something retrying cannot change."""
    asset = url.rsplit("/", 1)[-1] or "the engine asset"
    what = f"Engine {tag} not installed" if tag else "Engine not installed"
    return (
        f"{what}: no sha256 digest was published for {asset}, so its contents "
        "cannot be verified. persona does not install an unverified browser "
        "engine. This is not a download failure and retrying will not change it."
    )


def download_engine(
    url: str,
    timeout: int = 600,
    digest: str | None = None,
    progress=None,
    defer_if_in_use: bool = False,
    log=None,
    tag: str = "",
) -> bool:
    """Download the per-OS engine asset and install it so the launcher finds the
    runnable binary at ENGINE_BINARY. `progress(done, total)` is called as bytes
    arrive. A missing digest ALWAYS fails closed, on every OS.

    There is deliberately no `allow_unverified` opt-in any more (PS-49). One
    existed, one caller set it (`not digest and IS_LINUX`), and it forwarded to
    a gate that returns it verbatim WITHOUT EVER HASHING — so Linux installed
    engine binaries whose bytes nothing had checked while Windows and macOS
    refused the identical asset. Removed rather than merely left unset: an
    unused escape hatch is one edit away from being used again, and the caller
    that wanted it turned out not to need it (upstream publishes a digest for
    every asset persona matches — see ensure_engine).

    `defer_if_in_use` is for the UNATTENDED caller, and it is the whole reason
    the in-use oracle lives in this module rather than at the decision site.
    Deciding "no profile is running" before a multi-minute download and then
    installing on the strength of that stale answer is a TOCTOU: a profile can
    launch inside the window, and the promotion below would replace the tree it
    is executing from. So the question is asked AGAIN here, under _install_lock,
    immediately before the replacement — check and act adjacent, with no window
    between them. On a yes, InstallDeferred is raised and the verified asset is
    LEFT on disk, so a later check installs it without re-downloading.

    Off by default, so the two paths that must NOT defer keep today's behaviour:
    the operator's explicit click (they asked for it, and a silent no-op would
    look like the stall this all exists to remove) and the first install (an app
    with no browser at all is worse than one whose tree gets replaced — and with
    nothing installed there is no live session to protect).

    `log` (optional) receives the deferral notes the in-use oracle produces —
    the wired-provider fault and the unwired case. Separate from `progress`,
    which reports bytes, not prose."""
    if not url:
        return False
    # THE DIGEST GATE, AT THE ALTITUDE BOTH ENTRY POINTS SHARE (PS-49).
    #
    # Asked BEFORE any bytes move, and before ENGINE_DIR is even created: there
    # is nothing to fetch when we already know we would refuse the result, and a
    # refusal must not leave a half-populated directory behind.
    #
    # Raised rather than returned False, because False is the TRANSFER-FAILED
    # answer and both callers already render it as "Engine update failed" /
    # "download failed". This is not that: the transfer would succeed. It is
    # persona declining to install bytes nothing can check, and an operator told
    # the network failed retries forever against a condition retrying cannot
    # change — the exact confusion the refuse/failed vocabulary exists to
    # prevent. Same reasoning, and the same distinct-exception shape, as
    # InstallDeferred immediately above.
    #
    # `digest_missing`, NOT `not normalize_digest(...)`: only "nothing was ever
    # published" is this refusal. A digest that ARRIVED and is unusable
    # ("sha256:", "   ") is a mismatch and is rejected by the verify gate below
    # in the ordinary way — collapsing the two would let a malformed digest take
    # this exit and be described to the operator as an upstream omission.
    if httpdl.digest_missing(digest):
        raise EngineUnverifiable(_unverifiable_message(url, tag))
    os.makedirs(ENGINE_DIR, exist_ok=True)
    # download the raw asset next to the engine dir, named after the URL so a
    # resumed .part survives restarts
    asset_name = url.rsplit("/", 1)[-1] or "engine.download"
    asset_path = os.path.join(ENGINE_DIR, asset_name)
    # A previous run may have downloaded and VERIFIED this exact asset and then
    # deferred the install. resumable_download cannot see that: it resumes from
    # `asset_path + ".part"`, and publishing the complete file renamed that away
    # — so without this check a deferral would re-download the whole asset on
    # every retry, which is what makes deferring expensive enough to be wrong.
    # Re-verified rather than trusted on presence: the file has been sitting on
    # disk, and this is the same fail-closed digest gate the download itself
    # applies, so a truncated or tampered leftover is discarded, not installed.
    #
    # NOTE the deliberate absence of any `allow_missing=` here, which is what
    # makes that promise true. Before PS-49 this line forwarded the caller's
    # `allow_unverified`, and the asymmetry was load-bearing: `allow_missing`
    # was an opt-in to accept bytes THIS RUN JUST FETCHED from the source when
    # no digest was published — a weak provenance, but a provenance. A leftover
    # has none: verify_file short-circuits on a missing digest and returns
    # allow_missing WITHOUT EVER HASHING, so passing it through would make
    # have_asset True for any file that happens to hold this name, and promote
    # it into the engine tree unread.
    #
    # PS-49 removed the opt-in entirely, so both halves now ask the same
    # stricter question — is there a digest, and does the file match it — and
    # the download re-runs whenever it cannot verify. This paragraph survives
    # the parameter it was written about because the reasoning is what stops
    # someone re-introducing a hatch on the reuse side: the two look identical
    # at the call site and have opposite security properties.
    have_asset = (
        not httpdl.digest_missing(digest)
        and os.path.isfile(asset_path)
        and httpdl.verify_file(asset_path, digest)
    )
    if not have_asset and not _download_to(
        asset_path, url, timeout, digest, progress
    ):
        return False
    # Serialise the extract/move + marker: two concurrent installs into the
    # shared ENGINE_DIR would interleave and could publish a mixed tree.
    with _install_lock:
        # THE LAST POSSIBLE MOMENT to ask, and the only one that is safe to ask
        # at. The decision to fetch was made minutes ago, before a download that
        # a profile can easily outlive; re-asking here — holding the lock, with
        # nothing between this answer and the replacement below — is what turns
        # the guard from an opinion about the past into a fact about now.
        #
        # BEFORE the marker/sentinel writes, deliberately: those two are what
        # make is_installed() read False, and a deferral must leave the engine
        # exactly as it found it. Writing them first and bailing would report a
        # perfectly good installed engine as missing and block the very launches
        # this is protecting.
        if defer_if_in_use and _engine_in_use(log=log):
            raise InstallDeferred(
                "a profile is running — install deferred to a later check"
            )
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
        # THE FOURTH REFUSAL, AND NO PLATFORM IS CARVED OUT OF IT (PS-49).
        #
        # The rule itself is NOT written here. It lives in download_engine,
        # which raises EngineUnverifiable — see that exception's docstring for
        # why the altitude matters. In one line: this function is only the
        # FIRST-INSTALL path, and the sidebar's update path does not call it at
        # all, so a refusal written here would have covered exactly one of the
        # two callers that reach the digest gate. This branch only translates
        # the shared refusal into the (ok, message) shape this function returns.
        #
        # What the check REPLACED is worth keeping on the record. It used to
        # read `allow_unverified = not digest and IS_LINUX`, handed to
        # download_engine, which forwarded it as `allow_missing` to a gate that
        # returns it VERBATIM without ever hashing. Windows and macOS refused a
        # digest-less asset; Linux installed one. So on that path persona
        # installed — and then launched — a browser binary whose bytes nothing
        # had checked, fetched from a URL built by string-formatting a tag.
        # install.sh calls itself "the initial-install trust root that every
        # later in-app update check builds on" and refuses what it cannot
        # verify; this is one of those later updates and it did the opposite.
        #
        # The exemption existed for the Linux predictable-URL fallback, whose
        # asset was believed to carry no digest. MEASURED AGAINST UPSTREAM
        # (2026-08-21, adryfish/fingerprint-chromium) BOTH HALVES OF THAT
        # BELIEF ARE FALSE:
        #
        #   * The digest is not unobtainable — it is already in hand. Every
        #     asset of every release that publishes an AppImage (148, 144, 142,
        #     139) carries `digest: "sha256:…"` in the very asset list this
        #     function already reads, the AppImage included, and _asset_matches
        #     matches it. The release body publishes no checksums file, so the
        #     API field is THE source, and we were already holding it.
        #   * The fallback never bought the availability it cost integrity for.
        #     It fires on `tag and not url and IS_LINUX` — i.e. only when the
        #     matcher found nothing — and on every such release (138 and older,
        #     which ship `_linux.tar.xz` and no AppImage at all) the URL it
        #     formats 404s. It rescued no real release; it only ever widened
        #     what persona would install without looking.
        #
        # So this removes a carve-out rather than converting one: on today's
        # releases the digest is present and the refusal is unreachable. It is
        # still written, and still worded, because a digest-less release is
        # historically real — 135 and older carry no digest on ANY asset — and
        # that is exactly the shape a substitution would take.
        #
        # AND THIS BINDS ON THE FIRST INSTALL TOO, deliberately. The adjacent
        # ABOVE_CEILING branch above once installed anyway on a first install
        # because an app with no browser is worse than one with an untested
        # browser (PS-42 has since inverted even that). That reasoning does not
        # carry over here: untested is a bounded risk about a build's behaviour,
        # unverified is an unbounded one about whether these are that build's
        # bytes at all — and a first install is precisely when the operator has
        # no previous engine and no way to notice a substitution.
        try:
            installed = download_engine(
                url,
                timeout=timeout,
                digest=digest,
                progress=progress,
                tag=tag,
            )
        except EngineUnverifiable as e:
            message = str(e)
            # Logged HERE, for the reason the KNOWN_BAD and ABOVE_CEILING
            # branches above are: the onboarding caller reads only `ok` and
            # discards this message, and the sidebar caller would render it
            # behind "Engine download failed:" — blaming the network for a
            # refusal persona made.
            if log:
                log(message)
            # RETURNED, not `continue`d, for the same reason KNOWN_BAD returns:
            # what upstream published will not differ across the remaining
            # attempts, so burning them only delays the same answer.
            return False, message
        if installed:
            # BEFORE write_version, always — version.txt has one slot, and once
            # it is overwritten the identity of the build being replaced is gone
            # from the machine. On a first install there is nothing to demote,
            # so this simply records the starting point; on a re-install after a
            # wipe it is the same. Either way the NEXT swap has a target.
            record_installed_build(tag, digest)
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


def revert_to_previous_build(
    progress=None, timeout: int = 600, log=None
) -> tuple[bool, str]:
    """Go BACK to the build that was working before the last successful swap, by
    RE-DOWNLOADING it. Returns (ok, message).

    This is the operator's undo for a bad engine update, and unlike the Firefox
    side's revert it MOVES BYTES: Chromium keeps one un-versioned tree, so the
    previous build's files are genuinely gone (the success path destroys them,
    deliberately and unchanged — see _promote_staging). What survives a swap is
    the previous build's IDENTITY, and that is enough to fetch it again.

    The owner's call, 2026-08-23: keeping a second ~300-600MB engine tree on
    every operator's disk forever, against an event that has never been
    observed, is not worth it — a rollback re-downloads instead.

    Verified against the RECORDED digest, never against whatever upstream
    currently advertises for that tag. That is the point of storing the pair:
    re-asking the API what this tag should hash to would make the rollback trust
    a fresh response, which is the check PS-49 exists to prevent. The API is
    asked only for the URL; the digest comes off the disk record.

    Refused, with a plain message, in exactly three cases — and never silently:

      * nothing recorded to go back to (rollback_target is empty). Includes the
        machine that upgraded before this record existed, and any record whose
        digest is missing, which _entry answers as no target at all.
      * a profile is RUNNING. The install replaces the tree in place, so this
        is the same guard the unattended update obeys, for the same reason.
      * UPSTREAM NO LONGER SERVES THAT RELEASE. This is the honest limit of the
        whole design and the trade the owner accepted: if the tag is yanked or
        deleted the target is unreachable and the operator is where they were
        before this existed. It is REPORTED, in those words — a rollback that
        quietly installs something else instead would be worse than one that
        refuses, and "the newest build" is exactly the thing being rolled back
        from.

    On success the pin is set, which is what makes the reversal SURVIVE: the
    hourly unattended check would otherwise see the build just rejected as newer
    than what is installed and re-install it within the hour.
    """
    tag, digest = rollback_target()
    if not tag:
        message = (
            "Chromium engine: nothing to go back to — no previous build is "
            "recorded on this machine"
        )
        if log:
            log(message)
        return False, message
    # Fails CLOSED when no oracle is wired, exactly as the unattended install
    # does — and that is the right default even for an explicit click, because
    # the cost of a false "idle" is replacing a running browser's binary
    # underneath it. `log` is passed so the unwired/faulty-oracle reason reaches
    # the operator rather than being swallowed behind the message below.
    if _engine_in_use(log=log):
        message = (
            "Chromium engine: close your running profiles before going back to "
            f"{tag}"
        )
        if log:
            log(message)
        return False, message
    # The URL only. The digest is the RECORDED one, above.
    _t, url, _fresh_digest = fetch_release_full(tag, timeout=20)
    if not url:
        # The stated limit, reported plainly rather than papered over.
        message = (
            f"Chromium engine: {tag} is no longer available from upstream, so "
            "persona cannot go back to it. Nothing has been changed — the "
            "engine you have is still installed."
        )
        if log:
            log(message)
        return False, message
    try:
        ok = download_engine(
            url,
            timeout=timeout,
            digest=digest,
            progress=progress,
            # ARMED, exactly as the unattended update arms it, and for the same
            # TOCTOU reason: the check above is a cheap early exit made before a
            # download that takes minutes, and a profile can launch inside that
            # window. This is the binding guard — asked again under the install
            # lock, immediately before the tree is replaced.
            defer_if_in_use=True,
            log=log,
            tag=tag,
        )
    except EngineUnverifiable as e:
        # Unreachable via the recorded digest (rollback_target refuses a
        # digest-less record), so this can only mean the record was hand-edited.
        # Reported as the refusal it is, not as a download failure.
        message = str(e)
        if log:
            log(message)
        return False, message
    except InstallDeferred:
        message = (
            "Chromium engine: a profile started while the download was running "
            f"— {tag} is on disk and going back will finish on the next try"
        )
        if log:
            log(message)
        return False, message
    if not ok:
        message = f"Chromium engine: going back to {tag} failed — download failed"
        if log:
            log(message)
        return False, message
    # ORDER MATTERS, exactly as it does on the way forward: record first (which
    # demotes the build we just left), then overwrite the one-slot version.txt.
    # Recording makes the swap we just performed reversible in its own right —
    # an operator who reverts and then changes their mind can go forward again
    # by the same gesture, because the build they reverted FROM is now the
    # recorded previous one.
    #
    # NOTE THE ASYMMETRY WITH THE FORWARD PATH, which is deliberate and not a
    # missed normalisation: there `digest` is a RAW value straight off the API
    # response, and record_installed_build canonicalises it. Here it is the
    # digest we just VERIFIED against, read back out of the record, so it is
    # already canonical — _entry returns exactly what normalize_digest stored.
    # Re-normalising a canonical digest is idempotent, so passing it back
    # through is correct; do not "fix" either site to match the other.
    record_installed_build(tag, digest)
    write_version(tag)
    _set_pin(tag, log=log)
    message = (
        f"Chromium engine: went back to {tag} — automatic updates are paused "
        "until you resume them"
    )
    if log:
        log(message)
    return True, message


def _set_pin(tag: str, log=None) -> None:
    """Write the standing "not that build" instruction, best-effort.

    Read through a try for the same reason engine_install.pinned_build() is: a
    settings file that cannot be written must not turn a COMPLETED revert into a
    reported failure. The engine on disk is already the reverted one; a missing
    pin costs the reversal its durability, not its correctness."""
    try:
        from ...core import settings

        settings.set_chromium_build_pin(tag)
    except Exception as e:
        if log:
            log(
                f"Chromium engine: couldn't record the revert ({e}) — the "
                "automatic update may put you back on the newer build"
            )


def pinned_build() -> str:
    """The Chromium tag an operator deliberately reverted to, or "" when they
    never did. Degrades to "" (normal updating) when settings are unreadable —
    the same fail-soft shape as engine_install.pinned_build()."""
    try:
        from ...core import settings

        return settings.chromium_build_pin()
    except Exception:
        return ""


def resume_engine_updates(log=None) -> None:
    """Clear the pin: the operator saying "go forward again". The next check
    offers the newest acceptable build once more.

    Unlike the Firefox side's resume this is NOT instant — the build they
    reverted from is not on disk any more, so going forward is an ordinary
    download on the next check rather than a change of which tree launches.
    That asymmetry is the whole cost of not keeping a second engine tree."""
    try:
        from ...core import settings

        settings.set_chromium_build_pin("")
    except Exception as e:
        if log:
            log(f"Chromium engine: couldn't clear the pin ({e})")
        return
    if log:
        log("Chromium engine: automatic updates resumed")
