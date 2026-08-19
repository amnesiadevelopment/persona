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

import json
import re
import urllib.request

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


def fetch_latest(timeout: int = 20) -> tuple[str, bool]:
    """Return (tag, compatible) for the newest usable firefox-NN release, or
    ('', False) on failure.

    Enumerates the repo's releases (the latest release isn't necessarily a
    firefox-NN tag — the repo also carries e.g. 'usage-counter'), skips
    drafts, prereleases and BROKEN_VERSIONS, and picks the highest build
    number. `compatible` is True when that release ships this OS's expected
    asset; False means the build needs a newer engine package, i.e. a persona
    update."""
    try:
        from invisible_playwright.constants import (
            BINARY_VERSION,
            BROKEN_VERSIONS,
        )

        asset = _expected_asset()
        pkg_num = build_number(BINARY_VERSION)
    except Exception:
        return "", False
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            releases = json.load(resp)
    except Exception:
        return "", False
    best_tag = ""
    best_assets: list[str] = []
    for rel in releases if isinstance(releases, list) else []:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name", "")
        if build_number(tag) < 0 or tag in BROKEN_VERSIONS:
            continue
        if build_number(tag) > build_number(best_tag):
            best_tag = tag
            best_assets = [a.get("name", "") for a in rel.get("assets", [])]
    if not best_tag:
        return "", False
    # Compatible only when the release ships this OS's expected asset AND its
    # build number does not exceed what the bundled driver can drive. A newer
    # firefox-NN (even one carrying the same upstream asset) speaks a juggler
    # contract the shipped invisible_playwright can't drive, so it needs a persona
    # update that ships the matching driver — report it incompatible rather than
    # let the updater install an unlaunchable engine (#405).
    compatible = (asset in best_assets) and build_number(best_tag) <= pkg_num
    return best_tag, compatible


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
