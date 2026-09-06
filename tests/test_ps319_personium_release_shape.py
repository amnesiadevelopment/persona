"""PS-319 — the FIRST published Personium engine release, pinned as a fixture.

WHY THIS FILE EXISTS, AND WHY IT IS NOT A NETWORK TEST.

PS-319's real work was DRIVING the runtime update path against the live release
``personium-152.0.7977.75`` — a genuine 148 install crossing onto our own
self-built engine, the profile launching on it, the digest verified, the
rollback exercised. None of that can be re-run in CI: it moves ~200 MB per leg
and depends on GitHub being reachable, and a test that silently skips when the
network is absent is exactly the "verification layer that can quietly stop
verifying" this suite's conftest was written to refuse.

So what is pinned here is the part that CAN be checked offline and is the part a
future edit could actually break: the SELECTION RULE, evaluated against the
REAL release document as it was published. The asset names, the tag, the
prerelease flag and the per-asset digests below are transcribed from
``/repos/amnesiadevelopment/persona/releases/tags/personium-152.0.7977.75`` as
served on 2026-09-06, not invented — which is what makes these assertions about
a release that exists rather than about a fixture someone made agree with the
code.

The neighbouring ``test_engine_updater.py`` already covers this rule with
synthetic 148 names. This file is deliberately NOT that: it uses the real 152
release, and it pairs it with the real APPLICATION release published in the same
repository — including ``persona-x86_64.AppImage``, the exact asset PS-305
recorded the old bare-suffix rule selecting. That pairing is the point. A guard
tested only against names chosen to fail it is a guard tested against an
agreeable adversary.
"""

import inspect

import pytest

import src.core.platform as _platform
from src.services.engine import updater


# ---------------------------------------------------------------------------
# The REAL published documents, transcribed from the live API on 2026-09-06.
# ---------------------------------------------------------------------------

ENGINE_TAG = "personium-152.0.7977.75"
ENGINE_VERSION = "152.0.7977.75"

LINUX_ASSET = "personium-152.0.7977.75-linux-x86_64.AppImage"
WIN_ASSET = "personium-152.0.7977.75-windows-x86_64.zip"
MAC_ASSET = "personium-152.0.7977.75-macos-arm64.dmg"

LINUX_DIGEST = (
    "sha256:6ddb7bbea0a2063b7a3618e6b5d4ebc96301cd80f8b0d6eae486af46a30bb4c3"
)
WIN_DIGEST = (
    "sha256:997c72754c3cedc94bca4ebb7dc5337e3f5c65d02bbb109f8ab25e00beaa5259"
)
MAC_DIGEST = (
    "sha256:591572b344dd9cb033774f901a4effd282ae344a268963c19d4c1f0a3ef370bb"
)

_DL = (
    "https://github.com/amnesiadevelopment/persona/releases/download/"
    + ENGINE_TAG
    + "/"
)

# The engine release as GitHub serves it. `prerelease: True` is what keeps the
# app's one `releases/latest` pointer on the application — measured on the live
# repository, where this release is NINE DAYS NEWER than v3.0.2 and did not take
# that pointer.
ENGINE_RELEASE = {
    "tag_name": ENGINE_TAG,
    "draft": False,
    "prerelease": True,
    "published_at": "2026-09-06T11:30:58Z",
    "assets": [
        {
            "name": LINUX_ASSET,
            "browser_download_url": _DL + LINUX_ASSET,
            "digest": LINUX_DIGEST,
        },
        {
            "name": MAC_ASSET,
            "browser_download_url": _DL + MAC_ASSET,
            "digest": MAC_DIGEST,
        },
        {
            "name": WIN_ASSET,
            "browser_download_url": _DL + WIN_ASSET,
            "digest": WIN_DIGEST,
        },
    ],
}

# The APPLICATION release published in the SAME repository. Its asset list is
# the real one, verbatim — note `persona-x86_64.AppImage`.
APP_RELEASE = {
    "tag_name": "v3.0.2",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-08-29T00:34:39Z",
    "assets": [
        {"name": n, "browser_download_url": "https://example.invalid/" + n,
         "digest": "sha256:" + "a" * 64}
        for n in (
            "app.zip",
            "app.zip.hash",
            "checksums.txt",
            "persona-macos.dmg",
            "persona-macos.dmg.sha256",
            "persona-windows-setup.exe",
            "persona-x86_64.AppImage",
            "persona-x86_64.AppImage.sha256",
            "update-manifest.json",
        )
    ],
}

_OS_CASES = (
    # (label, win, mac, expected asset, expected digest)
    ("linux", False, False, LINUX_ASSET, LINUX_DIGEST),
    ("windows", True, False, WIN_ASSET, WIN_DIGEST),
    ("macos", False, True, MAC_ASSET, MAC_DIGEST),
)


def _force_os(monkeypatch, *, win=False, mac=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", not (win or mac))


# ---------------------------------------------------------------------------
# AC1 — the published release satisfies the publication shape.
# ---------------------------------------------------------------------------


def test_published_release_is_an_engine_release_by_tag():
    """Guard 1 accepts the real published tag, and the version round-trips.

    The bare-version half is load-bearing: version.txt is the sole source of the
    Chromium version an Android profile advertises, so a `personium-` prefix
    recorded there would leak into what a page can read."""
    assert updater.is_engine_tag(ENGINE_TAG) is True
    assert updater.version_from_tag(ENGINE_TAG) == ENGINE_VERSION
    assert updater.engine_tag(ENGINE_VERSION) == ENGINE_TAG
    # and it does not double-prefix a value that already carries it
    assert updater.engine_tag(ENGINE_TAG) == ENGINE_TAG


def test_published_release_is_a_prerelease_not_a_draft():
    """The publication shape RELEASING.md specifies, pinned on the real document.

    `prerelease` is what keeps the app's `releases/latest` pointer on the
    application; `draft: False` is what makes the release readable at all, since
    the unauthenticated by-tag endpoint answers 404 for a draft."""
    assert ENGINE_RELEASE["prerelease"] is True
    assert ENGINE_RELEASE["draft"] is False


@pytest.mark.parametrize("label,win,mac,asset,_digest", _OS_CASES)
def test_asset_rule_selects_exactly_one_asset_per_os(
    monkeypatch, label, win, mac, asset, _digest
):
    """Guard 2 picks ONE asset per OS out of the real three-asset release.

    Asserting the COUNT, not merely that the right one matches: a rule that
    matched two assets would still "find" the right one first and pass a
    membership check while being ambiguous."""
    _force_os(monkeypatch, win=win, mac=mac)
    hits = [a["name"] for a in ENGINE_RELEASE["assets"]
            if updater._asset_matches(a["name"])]
    assert hits == [asset], f"{label}: expected exactly [{asset}], got {hits}"


@pytest.mark.parametrize("label,win,mac,asset,digest", _OS_CASES)
def test_release_asset_yields_version_url_and_digest(
    monkeypatch, label, win, mac, asset, digest
):
    """The whole selection rule, end to end, on the real document.

    Every asset carries a digest, so none of them can reach the PS-49 refusal —
    which is the property that makes this release installable at all."""
    _force_os(monkeypatch, win=win, mac=mac)
    version, url, got_digest = updater._release_asset(ENGINE_RELEASE)
    assert version == ENGINE_VERSION
    assert url.endswith(asset)
    assert got_digest == digest
    assert not updater.httpdl.digest_missing(got_digest)


def test_macos_asset_reports_the_tag_version_not_its_compile_version():
    """⚠️ The macOS binary was COMPILED at 152.0.7977.64 and is published under a
    `.75` name (the owner's naming decision, closed by publishing).

    Pinned because it is the one place that skew could have leaked: the updater
    derives its version from the TAG and the ASSET NAME, never from what the
    binary was built from — so the skew is invisible to the update path. This
    documents that as intended behaviour rather than leaving a future reader to
    rediscover the discrepancy and treat it as a defect."""
    old = (_platform.IS_WINDOWS, _platform.IS_MACOS)
    _platform.IS_WINDOWS, _platform.IS_MACOS = False, True
    try:
        version, url, _d = updater._release_asset(ENGINE_RELEASE)
    finally:
        _platform.IS_WINDOWS, _platform.IS_MACOS = old
    assert version == "152.0.7977.75"
    assert url.endswith("-macos-arm64.dmg")


# ---------------------------------------------------------------------------
# AC2 — the two guards keep the APPLICATION release out, on its real assets.
# ---------------------------------------------------------------------------


def test_application_release_is_refused_by_the_tag_guard():
    """Guard 1 alone already excludes the app release: `v3.0.2` is not an engine
    tag, so it is never a candidate."""
    assert updater.is_engine_tag(APP_RELEASE["tag_name"]) is False


@pytest.mark.parametrize("label,win,mac,_asset,_digest", _OS_CASES)
def test_application_assets_are_refused_by_the_asset_guard(
    monkeypatch, label, win, mac, _asset, _digest
):
    """Guard 2 alone ALSO excludes every real application asset, on every OS.

    ⭐ `persona-x86_64.AppImage` is in this list because it is in the real
    release — it is the exact asset PS-305 reproduced the old bare-suffix rule
    selecting as the "engine". Either anchor excludes it on its own, which is
    the design: loosening one does not silently reopen the hole."""
    _force_os(monkeypatch, win=win, mac=mac)
    hits = [a["name"] for a in APP_RELEASE["assets"]
            if updater._asset_matches(a["name"])]
    assert hits == [], f"{label}: application assets matched the engine rule: {hits}"


@pytest.mark.parametrize("label,win,mac,_asset,_digest", _OS_CASES)
def test_release_asset_refuses_the_application_release_outright(
    monkeypatch, label, win, mac, _asset, _digest
):
    """Both guards together: the app release resolves to nothing installable."""
    _force_os(monkeypatch, win=win, mac=mac)
    assert updater._release_asset(APP_RELEASE) == ("", "", "")


@pytest.mark.parametrize("label,win,mac,asset,_digest", _OS_CASES)
def test_asset_rule_refuses_the_OTHER_platforms_assets(
    monkeypatch, label, win, mac, asset, _digest
):
    """Each OS refuses the other two platforms' assets from the same release.

    A rule that matched two sibling assets would install the wrong OS's engine,
    and the per-OS marker is the only anchor distinguishing them — the
    `personium-` prefix is identical on all three."""
    _force_os(monkeypatch, win=win, mac=mac)
    others = [a["name"] for a in ENGINE_RELEASE["assets"] if a["name"] != asset]
    assert others, "fixture must carry the sibling platforms' assets"
    for other in others:
        assert updater._asset_matches(other) is False, (
            f"{label}: matched another platform's asset {other!r} — the OS "
            "marker is not anchoring"
        )


# The `-<os>-<arch>` markers RELEASING.md specifies, paired with plausible
# assets that share the extension but NOT the marker. Every one of these is a
# name a future release could legitimately carry.
_LOOSENED_MARKER_CASES = (
    ("linux", False, False, "personium-153.0.1.2-linux-arm64.AppImage"),
    ("linux", False, False, "personium-153.0.1.2-android-x86_64.AppImage"),
    ("windows", True, False, "personium-153.0.1.2-windows-arm64.zip"),
    ("windows", True, False, "personium-153.0.1.2-linux-x86_64.zip"),
    ("macos", False, True, "personium-153.0.1.2-macos-x86_64.dmg"),
)


@pytest.mark.parametrize("label,win,mac,foreign", _LOOSENED_MARKER_CASES)
def test_os_marker_is_the_full_marker_not_a_bare_extension(
    monkeypatch, label, win, mac, foreign
):
    """⚠️ THE ARCH HALF OF THE MARKER IS LOAD-BEARING, AND NOTHING ELSE PINS IT.

    Written after a sabotage this file's other tests could not see. Loosening
    the Linux arm from `-linux-x86_64.AppImage` to a bare `.AppImage` leaves
    EVERY other assertion here still green: no application asset carries the
    `personium-` prefix, and only one of the three sibling assets is an
    `.AppImage` at all — so neither the app-release tests nor the
    wrong-platform test above can bite.

    What such a loosening would actually do is match a name from a release we
    have not cut yet. `personium-<v>-linux-arm64.AppImage` is the obvious one:
    an arm64 Linux asset is a plausible future addition, and a bare-extension
    rule would hand it to an x86_64 machine as its engine — an unrunnable
    binary installed with a valid digest, so every check upstream of the launch
    passes.

    These names are deliberately from a HYPOTHETICAL future release rather than
    the published one, because that is where the risk lives: today's release
    has exactly one asset per extension, which is precisely why the rest of this
    file cannot detect the defect."""
    _force_os(monkeypatch, win=win, mac=mac)
    assert updater._asset_matches(foreign) is False, (
        f"{label}: matched {foreign!r} — the OS marker has been loosened to a "
        "bare extension and would select another architecture's asset"
    )


def test_engine_release_did_not_take_the_apps_latest_pointer():
    """The engine release is NEWER than the application release and is still not
    what `releases/latest` points at — because it is a prerelease, which that
    endpoint excludes by design.

    Pinned as an ORDERING fact: a pointer that simply never had a chance to move
    would prove much less than one that could have moved and did not."""
    assert ENGINE_RELEASE["published_at"] > APP_RELEASE["published_at"]
    assert ENGINE_RELEASE["prerelease"] is True
    assert APP_RELEASE["prerelease"] is False


# ---------------------------------------------------------------------------
# AC5 — the unverifiable-asset refusal survives the move to our own repository.
# ---------------------------------------------------------------------------


def test_download_engine_still_has_no_unverified_escape_hatch():
    """PS-49 removed `allow_unverified` entirely rather than leaving it unset.

    Pinned here as well as in its own file because PS-319 moved the release
    SOURCE: an engine fetched from our own repository must be held to the same
    fail-closed rule as one fetched from a third party. Owning the upstream is
    not a reason to trust bytes nothing has checked."""
    params = inspect.signature(updater.download_engine).parameters
    assert "allow_unverified" not in params


def test_missing_digest_is_refused_before_any_bytes_move(tmp_path, monkeypatch):
    """A digest-less asset raises rather than downloading — and, critically,
    without creating ENGINE_DIR.

    The refusal is raised, not returned False, because False is the
    TRANSFER-FAILED answer and the two are different events: retrying cannot fix
    an absent digest, and an operator told "the network failed" would retry
    forever against a condition retrying cannot change."""
    monkeypatch.setattr(updater, "ENGINE_DIR", str(tmp_path / "engine"))
    url = _DL + LINUX_ASSET
    for absent in (None, ""):
        with pytest.raises(updater.EngineUnverifiable):
            updater.download_engine(url, digest=absent, tag=ENGINE_VERSION)
    assert not (tmp_path / "engine").exists(), (
        "a refusal must not leave a half-populated engine directory behind"
    )


def test_blank_digest_is_a_mismatch_not_an_omission():
    """A digest that ARRIVED and is unusable is NOT the omission refusal.

    `download_engine` takes the EngineUnverifiable exit only on
    `digest_missing`, so a malformed value ("sha256:", "   ") falls through to
    the ordinary verify gate and is rejected as a mismatch. Collapsing the two
    would let a malformed digest be described to the operator as an upstream
    omission — a different, and wrong, story about what happened."""
    assert updater.httpdl.digest_missing(None) is True
    assert updater.httpdl.digest_missing("") is True
    assert updater.httpdl.digest_missing("   ") is False
    assert updater.httpdl.digest_missing(LINUX_DIGEST) is False


# ---------------------------------------------------------------------------
# AC7 — assert the predictable-URL fallback is STILL ABSENT. Do not delete it a
# second time (PS-305 already removed it).
# ---------------------------------------------------------------------------


def test_predictable_url_fallback_is_still_absent():
    """AC7 is an ABSENCE assertion, deliberately.

    `test_appimage_url_fallback_is_gone` in test_engine_updater.py already pins
    this; PS-319 re-states it beside the release that made the question live,
    because the fallback's whole purpose was to paper over "this release lists
    no asset for my OS" — and PS-319 is the first ticket where WE are the one
    cutting the release. A missing per-OS asset is now our own broken release,
    and the right answer to one is a refusal a person can see and fix."""
    assert not hasattr(updater, "appimage_url_for")


def test_engine_source_constants_are_ours():
    """The four constants PS-319 was explicitly forbidden from editing, pinned.

    Not decoration: each carries a recorded reason in updater.py, and this is
    the cheap tripwire that says so if one is changed without reading them."""
    assert updater.ENGINE_REPO == "amnesiadevelopment/persona"
    assert updater.ENGINE_TAG_PREFIX == "personium-"
    assert updater.ENGINE_TAG_REFS_API.endswith(
        "/git/matching-refs/tags/personium-"
    )
    assert "/releases/tags/{tag}" in updater.RELEASE_BY_TAG_API
