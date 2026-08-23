"""Firefox (invisible_playwright) engine version check + download.

The patched-Firefox binaries are published as firefox-NN GitHub releases on
feder-cr/firefox_antidetect_patch — the same releases the engine package's
ensure_binary() downloads from:
https://github.com/feder-cr/firefox_antidetect_patch/releases/download/{tag}/{asset}

A newer firefox-NN that still ships the per-OS asset the installed engine
package expects (ARCHIVE_NAME embeds the upstream Firefox version, e.g.
firefox-150.0.1-stealth-win-x86_64.zip) is a binary-only rebuild the package
can download and drive. A release WITHOUT that asset (upstream Firefox bumped,
or the naming changed) needs a newer engine package — i.e. a persona update —
so it is reported as incompatible rather than offered for download.
"""

import logging
import re
# Retained deliberately though this module no longer calls urlopen itself: the
# direct send now happens in services/egress.py, and `firefox.urllib.request` is
# the SAME module object egress resolves — which is what lets the existing
# tests patch one attribute and cover both. See fetch_latest.
import urllib.request

from ...services import egress

# The same "persona" logger services/egress.py writes to, so the update trail
# reads in one place. The operator-facing channel is the RETURN VALUE, not this
# logger — the console handler is WARNING (src/core/logging.py:45) and the UI
# log is fed by AppUI._log, which this module cannot reach. See fetch_latest_full.
logger = logging.getLogger("persona")

RELEASES_API = (
    "https://api.github.com/repos/feder-cr/firefox_antidetect_patch/releases"
    "?per_page=30"
)

# Match the firefox-NN build number whether the tag is bare (firefox-18) OR
# carries the newer engine package's cache-dir suffix
# (firefox-18_151.0_20260724001829 — upstream version + timestamp, joined by '_'
# or '.'). Suffix only via '_'/'.', NOT '-', so firefox-15-beta stays unmatched
# and firefox-180 stays 180.
_TAG_RE = re.compile(r"^firefox-(\d+)(?:[_.].*)?$")


def build_number(tag: str) -> int:
    """firefox-15 → 15; firefox-18_151.0_20260724 → 18; anything else → -1.

    The engine cache directory is named after the tag, and the newer package
    appends the upstream version + a timestamp to it, so the on-disk dir name is
    firefox-18_151.0_20260724001829. Parse the leading firefox-NN so a downloaded
    build in such a dir is still recognised as installed (#234)."""
    m = _TAG_RE.match(tag or "")
    return int(m.group(1)) if m else -1


def is_newer(latest: str, current: str) -> bool:
    """True when `latest` is a strictly higher firefox-NN build than `current`."""
    n = build_number(latest)
    if n < 0:
        return False
    c = build_number(current)
    if c < 0:
        return True
    return n > c


def current_version() -> str:
    """The installed build launches use (e.g. "firefox-15"), or '' when the
    engine isn't installed."""
    from ..browser import invisible_launch as inv

    if not inv.is_invisible_installed():
        return ""
    return inv.active_build()


def _expected_asset() -> str:
    """The per-OS archive name the INSTALLED engine package downloads — a
    release must carry exactly this asset for the package to drive it."""
    import platform as _pyplatform
    import sys

    from invisible_playwright.constants import ARCHIVE_NAME

    return ARCHIVE_NAME(sys.platform, _pyplatform.machine())


def fetch_latest_full(timeout: int = 20) -> tuple[str, bool, str]:
    """Return (tag, compatible, capped_by) for the newest usable firefox-NN
    release, or ('', False, '') on failure.

    Enumerates the repo's releases (the latest release isn't necessarily a
    firefox-NN tag — the repo also carries e.g. 'usage-counter'), skips
    drafts, prereleases and BROKEN_VERSIONS, and picks the highest build
    number THE SHIPPED DRIVER CAN ACTUALLY DRIVE. `compatible` is True when
    that release ships this OS's expected asset; False means the build needs a
    newer engine package, i.e. a persona update.

    THE MAXIMISATION IS OVER DRIVABLE RELEASES, NOT OVER ALL OF THEM (PS-112).
    Maximising over every release and testing the driver pin afterwards means
    that the moment upstream ships above the pin, a drivable build that exists
    and is newer than what is installed is never offered: with pin firefox-18
    and releases [16, 18, 20] the old code returned ('firefox-20', False) and
    the consumer's `not self._engine2_compatible` gate refused the update,
    even though firefox-18 was present, drivable, and shipped the asset.

    Two candidates are tracked in ONE pass — the highest drivable release and
    the highest release overall — because filtering undrivable releases out
    in-loop is NOT equivalent: when the only release is undrivable it would
    leave no candidate at all and return ('', False). Both consumers guard on
    exactly that (`if tag:`), so the "needs a newer persona" message would be
    silenced and the operator told nothing. The overall winner is retained
    precisely to keep that path reporting (tag, False) as it does today.

    `capped_by` IS THE THIRD RETURN VALUE BECAUSE PREFERRING THE DRIVABLE BUILD
    CHANGES WHAT `tag` MEANS. It used to be "the newest release that exists";
    it is now "the newest release you can drive". Every reader that took the
    old meaning needs the passed-over tag or it will say something false — and
    the reader that matters is `is_newer(tag, current)` in BOTH app.py
    consumers. An operator sitting on the highest drivable build with upstream
    above the pin (pin 18, releases [18, 20], installed firefox-18) gets
    is_newer == False, so without this the row goes blank and the startup path
    affirmatively logs "Firefox engine is up to date" while firefox-20 exists.
    That is the state this fix's OWN success path walks its beneficiaries into:
    offer firefox-18, they install it, and from then on they are capped.

    So `capped_by` carries the higher release that was passed over ('' when
    nothing was), and the consumers use it to keep telling the operator the
    truth. It is a RETURN VALUE and not a log line on purpose: the "persona"
    logger's console handler is WARNING (src/core/logging.py:45) and the
    operator-facing sidebar is fed by AppUI._log, which this module cannot
    reach — a line in a file they have to go find does not offset a false
    "up to date" in the interface (PS-112 §5).

    `fetch_latest` below is the 2-tuple wrapper, kept for callers that only
    want the offer, exactly as updater.fetch_latest wraps fetch_latest_full."""
    try:
        from invisible_playwright.constants import (
            BINARY_VERSION,
            BROKEN_VERSIONS,
        )

        asset = _expected_asset()
        pkg_num = build_number(BINARY_VERSION)
    except Exception:
        return "", False, ""
    try:
        # Same single authority the Chromium updater consults — this is the
        # other unattended startup poll, so it must not be able to leave a
        # different way. With no policy set this is a direct send, exactly as
        # before.
        releases = egress.fetch_json(RELEASES_API, timeout=timeout)
    except Exception:
        return "", False, ""
    # Track two candidates in one pass. `best_*` is the highest release
    # overall (today's winner, kept for the nothing-drivable path); `drivable_*`
    # is the highest release at or below the driver pin.
    best_tag = ""
    best_assets: list[str] = []
    drivable_tag = ""
    drivable_assets: list[str] = []
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        num = build_number(tag)
        if num < 0 or tag in BROKEN_VERSIONS:
            continue
        assets = [a.get("name", "") for a in rel.get("assets", [])]
        if num > build_number(best_tag):
            best_tag = tag
            best_assets = assets
        if num <= pkg_num and num > build_number(drivable_tag):
            drivable_tag = tag
            drivable_assets = assets
    if not best_tag:
        return "", False, ""
    # PREFER THE HIGHEST DRIVABLE RELEASE when it exists and ships this OS's
    # expected asset. That is the whole fix: the update the operator can
    # actually install is the one worth offering, even when upstream has moved
    # above the pin. A drivable release that does NOT ship the asset is not a
    # usable offer either, so it falls through to the report-only path below
    # rather than being announced as installable.
    if drivable_tag and asset in drivable_assets:
        # `capped_by` is the higher release we passed over, or '' when the
        # drivable winner IS the newest release. This is the ONLY channel that
        # survives to the consumers: once compatible is True, their
        # `not compatible` branch is unreachable by construction, and when the
        # offered tag equals what is installed their `is_newer` gate is False
        # too — so without this they have nothing left to say and would report
        # "up to date" while a newer build exists.
        capped_by = best_tag if drivable_tag != best_tag else ""
        if capped_by:
            logger.info(
                "Firefox engine %s needs a newer persona — offering the "
                "newest drivable build %s instead",
                capped_by,
                drivable_tag,
            )
        return drivable_tag, True, capped_by
    # Nothing drivable to fall back to: report the overall winner exactly as
    # before. Compatible only when the release ships this OS's expected asset
    # AND its build number does not exceed what the bundled driver can drive. A
    # newer firefox-NN (even one carrying the same upstream asset) speaks a
    # juggler contract the shipped invisible_playwright can't drive, so it needs
    # a persona update that ships the matching driver — report it incompatible
    # rather than let the updater install an unlaunchable engine (#405).
    #
    # `capped_by` is '' here and that is not an omission: the tag being
    # RETURNED is already the newest release that exists, so there is nothing
    # passed over to name. The consumers' existing `not compatible` branch is
    # what speaks on this path, exactly as it does today.
    compatible = (asset in best_assets) and build_number(best_tag) <= pkg_num
    return best_tag, compatible, ""


def fetch_latest(timeout: int = 20) -> tuple[str, bool]:
    """Return (tag, compatible) for the newest usable firefox-NN release, or
    ('', False) on failure — the narrow view of fetch_latest_full, for callers
    that only want the offer and not the build it was capped by.

    Kept as the 2-tuple it has always been so existing callers and test stubs
    (`lambda: (tag, compatible)`) keep working, exactly as updater.fetch_latest
    wraps updater.fetch_latest_full. The two app.py consumers that must tell the
    operator the truth about a capped build call fetch_latest_full instead."""
    tag, compatible, _capped_by = fetch_latest_full(timeout)
    return tag, compatible


def download_engine(tag: str, progress=None, log=None) -> bool:
    """Download the given firefox-NN build via the launcher's Tor-resumable
    fetch: sha256-verified against the release's checksums.txt, extracted into
    its own versioned cache dir, completion-marked last — so the active build
    stays untouched until the new one is whole.

    Installing then prunes superseded builds, which would delete the build a
    profile running on the PREVIOUS one is executing from; that prune defers
    while any profile runs (see engine_install.set_in_use_provider), so a
    running profile is left alone here too."""
    from ..browser import invisible_launch as inv

    return inv.install_engine_build(tag, progress=progress, log=log)
