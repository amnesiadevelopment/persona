"""PS-319 — the FIRST published Personium engine release, pinned as a fixture.

WHY THIS FILE EXISTS, AND WHY IT IS NOT A NETWORK TEST.

PS-319's real work was DRIVING the runtime update path against the live release
``personium-152.0.7977.75`` — a genuine 148 install crossing onto our own
self-built engine, the profile launching on it, the digest verified, the
rollback exercised. None of that can be re-run in CI: it moves ~200 MB per leg
and depends on GitHub being reachable, and a test that silently skips when the
network is absent is exactly the "verification layer that can quietly stop
verifying" this suite's conftest was written to refuse.

So what is pinned here is the part that CAN be checked offline AND is driven
through product code — the SELECTION RULE, evaluated against the REAL release
document as it was published. The asset names, the tag and the per-asset digests
below are transcribed from
``/repos/amnesiadevelopment/persona/releases/tags/personium-152.0.7977.75`` as
served on 2026-09-06, not invented — which is what makes these assertions about
a release that exists rather than about a fixture someone made agree with the
code.

Every test in this file goes through ``updater``. Facts about the RELEASE that
no code here reads — that it was published as a prerelease, and that it did not
take the app's ``releases/latest`` pointer — are transcribed into the fixture
and recorded in the PR body, but deliberately NOT asserted: a test comparing two
hardcoded values cannot fail for any reason except someone editing the fixture,
and the first draft of this file proved that the hard way (see the notes at the
fixture and in the AC2 section).

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

# The engine release as GitHub serves it.
#
# ⚠️ `prerelease` and `published_at` are transcribed here because they are part
# of the real document, NOT because anything below asserts them. `prerelease` is
# a GITHUB-SIDE fact — no code in this repository reads it, so there is no
# product path an offline test could drive it through, and a test comparing this
# dict to the value written beside it would only ever fail if someone edited the
# fixture. That measurement is an ONLINE one and its honest home is the PR body
# and the ticket, where it was recorded: at publication this release was newer
# than the then-latest v3.0.2 and did not take the pointer. It is deliberately
# NOT restated as an assertion here — the earlier attempt to do so pinned an
# ORDERING that stopped being true within hours (v3.1.0 was cut at 13:30Z, two
# hours after this release, and correctly took the pointer) and went on passing
# green through the change, because two hardcoded strings cannot notice the
# world moving.
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


# NOTE — there is no test here for the `prerelease`/`draft` publication flags,
# and both halves of that absence are deliberate.
#
# `prerelease` is a GITHUB-SIDE fact that no code in this repository reads (see
# the note on ENGINE_RELEASE), so nothing offline can drive it through the
# product; the version that used to sit here compared the fixture's own literal
# to the value written 80 lines above it.
#
# `draft` IS read by the product — `_release_asset` refuses on it — so the
# obvious repair was to re-point the test through it:
# `assert updater._release_asset({**ENGINE_RELEASE, "draft": True}) == ("","","")`.
# That was written and then MEASURED, and it is a third copy: deleting the
# `data.get("draft")` arm fails it and
# `test_release_channel_separation.py::test_engine_updater_skips_draft_releases`
# TOGETHER, and never one without the other. Same standard applied to the digest
# truth table below — duplicating an existing guard on the real document instead
# of a synthetic one adds maintenance surface, not defence, because the
# refusal returns before any part of the document is looked at.
#
# What the real document's flags actually were is transcribed in ENGINE_RELEASE
# and recorded in the PR body, which is the honest home for a measurement of the
# world.


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


def test_macos_asset_reports_the_tag_version_not_its_compile_version(monkeypatch):
    """⚠️ The macOS binary was COMPILED at 152.0.7977.64 and is published under a
    `.75` name (the owner's naming decision, closed by publishing).

    Pinned because it is the one place that skew could have leaked: the updater
    derives its version from the TAG and the ASSET NAME, never from what the
    binary was built from — so the skew is invisible to the update path. This
    documents that as intended behaviour rather than leaving a future reader to
    rediscover the discrepancy and treat it as a defect."""
    _force_os(monkeypatch, mac=True)
    version, url, _d = updater._release_asset(ENGINE_RELEASE)
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


# The per-OS markers `_asset_matches` ACTUALLY ENFORCES (updater.py:660-666:
# `-linux-x86_64.AppImage`, `-windows-x86_64.zip`, `-macos-arm64.dmg`), paired
# with plausible assets that share the extension but NOT the marker. Every one
# of these is a name a future release could legitimately carry.
#
# ⚠️ Derived from the CODE, not from RELEASING.md, because the two disagree on
# one row: RELEASING.md:24 still lists the macOS engine asset as
# `personium-<version>-macos-x86_64.dmg`, while the matcher was corrected to
# `-macos-arm64.dmg` in 622b0e6 and the real published asset is arm64. That is
# why `...-macos-x86_64.dmg` appears BELOW as a name that must be REFUSED
# despite the doc naming it — the doc's row is stale. Left uncorrected here on
# purpose: the divergence predates PS-319 and fixing the doc is a different
# diff.
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


# NOTE — there is no test here asserting that this engine release did not take
# the app's `releases/latest` pointer, and that absence is deliberate. That is a
# claim about GITHUB'S BEHAVIOUR, driven by a field no code in this repository
# reads, so nothing offline can drive it through the product; the version that
# used to sit here compared two hardcoded timestamps and went on passing green
# when the fact it claimed to pin stopped being true (v3.1.0 was published at
# 13:30Z on the same day, two hours AFTER this engine release, and correctly
# took the pointer). The measurement was made live and belongs in the PR body
# and the ticket, not in a test that cannot notice it going stale.
#
# What CAN be pinned offline is the separation itself, and it is — by the tag
# and asset guards above, both driven through `updater`.


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


# NOTE — `digest_missing`'s truth table is NOT restated here. The version that
# used to sit at this point asserted None/""/"   "/a-real-digest, which is a
# strict SUBSET of
# tests/test_update_verify.py::test_allow_missing_does_not_cover_a_present_but_unusable_digest
# (:286): that test covers six
# malformed inputs including "sha256::" and "\t\n", and carries the downstream
# `digest_ok`/`verify_bytes`/`verify_file`/`sha256_ok` legs this file's copy
# dropped. Duplicating the weaker half of an existing guard adds maintenance
# surface, not defence. The distinction it documented — a digest that ARRIVED
# and is unusable is a MISMATCH, not the omission refusal above — is pinned
# there, and the refusal itself is driven through `download_engine` above.


# ---------------------------------------------------------------------------
# AC7 — the predictable-URL fallback must still be ABSENT. Do not delete it a
# second time (PS-305 already removed it).
#
# NOT re-asserted here. `assert not hasattr(updater, "appimage_url_for")`
# already exists TWICE — test_engine_updater.py:91
# (`test_appimage_url_fallback_is_gone`, :84) and
# test_release_channel_separation.py:277
# — and a third identical line is maintenance surface rather than defence in
# depth: all three fail together or none does. AC7 asks that the absence be
# ASSERTED rather than the fallback deleted again; it already is, by those two,
# and this file's contribution to that AC is that it deletes nothing.
#
# What PS-319 changed about the question is worth recording even though it
# needs no new test: the fallback's whole purpose was to paper over "this
# release lists no asset for my OS", and PS-319 is the first ticket where WE
# cut the release. A missing per-OS asset is now our own broken release, and the
# right answer to one is a refusal a person can see and fix — which is what
# `_release_asset` does, driven above.
# ---------------------------------------------------------------------------


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
