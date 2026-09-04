"""PS-305 — the two updaters read ONE repository and must never cross.

`amnesiadevelopment/persona` publishes BOTH the persona application and the
Personium browser engine (see RELEASING.md). Two updaters consume that one
repository, they run unattended on operator machines, and before this work they
could not tell each other's releases apart. Both failures below were REPRODUCED
against the tree at `b1f00e9`, not predicted:

1. The engine's Linux asset rule was `name.endswith("x86_64.AppImage")`, and the
   application's own Linux asset is `persona-x86_64.AppImage`. A Linux engine
   install would have selected the application and installed it as the browser.

2. Worse, and in the other direction: the app updater compares tags with
   `is_newer`, imported FROM the engine updater. With the app at 3.0.2 and an
   engine at 152.0.7977.75, `is_newer("152.0.7977.75", "3.0.2")` is True and the
   reverse is False — so an engine release would have read as a newer
   APPLICATION to every install, and once a client recorded that version no real
   application release could ever read as newer again. A one-way trap.

Every test here presents BOTH kinds of release TOGETHER and asserts each updater
selects its own and rejects the other's. They are written to FAIL if either
selection rule is loosened — in particular
`test_engine_matcher_rejects_the_application_appimage_on_linux` fails the moment
the engine matcher goes back to accepting any `x86_64.AppImage`.
"""

import pytest

import src.core.platform as _platform
from src.services.app_update import updater as app_updater
from src.services.engine import updater as engine_updater

# ---------------------------------------------------------------------------
# The two release shapes, per RELEASING.md's table. Deliberately literal rather
# than built from the constants under test: a test that derives its fixtures
# from the code it checks passes when both move together, which is exactly the
# regression this file exists to catch.
# ---------------------------------------------------------------------------

APP_TAG = "v3.0.2"
APP_ASSETS = [
    {"name": "persona-x86_64.AppImage", "browser_download_url": "http://x/app-linux",
     "size": 119395520, "digest": "sha256:" + "a" * 64},
    {"name": "persona-windows-setup.exe", "browser_download_url": "http://x/app-win",
     "size": 68795630, "digest": "sha256:" + "b" * 64},
    {"name": "persona-macos.dmg", "browser_download_url": "http://x/app-mac",
     "size": 164197011, "digest": "sha256:" + "c" * 64},
    {"name": "app.zip", "browser_download_url": "http://x/app-zip",
     "size": 6115248, "digest": "sha256:" + "d" * 64},
]

ENGINE_VERSION = "152.0.7977.75"
ENGINE_TAG = f"personium-{ENGINE_VERSION}"
ENGINE_ASSETS = [
    {"name": f"personium-{ENGINE_VERSION}-linux-x86_64.AppImage",
     "browser_download_url": "http://x/engine-linux", "size": 320000000,
     "digest": "sha256:" + "1" * 64},
    {"name": f"personium-{ENGINE_VERSION}-windows-x86_64.zip",
     "browser_download_url": "http://x/engine-win", "size": 340000000,
     "digest": "sha256:" + "2" * 64},
    {"name": f"personium-{ENGINE_VERSION}-macos-x86_64.dmg",
     "browser_download_url": "http://x/engine-mac", "size": 350000000,
     "digest": "sha256:" + "3" * 64},
]

APP_RELEASE = {"tag_name": APP_TAG, "prerelease": False, "draft": False,
               "assets": APP_ASSETS}
ENGINE_RELEASE = {"tag_name": ENGINE_TAG, "prerelease": True, "draft": False,
                  "assets": ENGINE_ASSETS}

# What GitHub's releases list looks like for this repository: newest first, both
# kinds interleaved. The engine release is FIRST because it is newer by date —
# which is precisely the arrangement that makes a naive "take the first" wrong.
BOTH_RELEASES = [ENGINE_RELEASE, APP_RELEASE]


def _ref(tag):
    """One entry of `git/matching-refs/tags/...`, in GitHub's own shape."""
    return {"ref": f"refs/tags/{tag}", "object": {"sha": "0" * 40, "type": "tag"}}


def _fake_github(releases, *, refs=None):
    """Stand in for the whole GitHub metadata surface, answering BY URL.

    PS-305's discovery is two requests — `git/matching-refs/tags/personium-`
    (which the SERVER filters by prefix) and then `releases/tags/<tag>`. This
    fake models both, and models the server-side filter faithfully: `refs`
    defaults to the tags of the releases given, FILTERED to the prefix the
    endpoint names, so a test that hands it application releases gets an answer
    with no application tags in it — exactly as the live endpoint behaves
    (verified 2026-09-04: `tags/v` on this repo returns the 98 `v*` tags and
    nothing else). Pass `refs` explicitly to model a tag with no release.
    """
    by_tag = {r["tag_name"]: r for r in releases}
    all_tags = list(refs) if refs is not None else [r["tag_name"] for r in releases]

    def fetch_json(url, timeout=20, **k):
        if "matching-refs/tags/" in url:
            prefix = url.split("matching-refs/tags/", 1)[1]
            return [_ref(t) for t in all_tags if t.startswith(prefix)]
        tag = url.rsplit("/", 1)[-1]
        if tag not in by_tag:
            # The by-tag endpoint 404s for an unknown tag; egress raises and
            # the updater's own `except Exception` turns that into a refusal.
            raise OSError("404 Not Found")
        return by_tag[tag]

    return fetch_json


def _serve(monkeypatch, releases, *, refs=None):
    monkeypatch.setattr(
        engine_updater.egress, "fetch_json", _fake_github(releases, refs=refs)
    )


def _force_os(monkeypatch, *, win=False, mac=False, linux=False):
    monkeypatch.setattr(_platform, "IS_WINDOWS", win)
    monkeypatch.setattr(_platform, "IS_MACOS", mac)
    monkeypatch.setattr(_platform, "IS_LINUX", linux)


@pytest.fixture
def both_releases(monkeypatch):
    """The engine updater's metadata fetches, over a repository carrying BOTH
    release kinds."""
    _serve(monkeypatch, BOTH_RELEASES)


# ---------------------------------------------------------------------------
# THE PREMISE. These two are here so the rest of the file cannot be read as
# testing a hazard that does not exist.
# ---------------------------------------------------------------------------


def test_the_version_trap_is_real_and_is_what_the_app_guard_defends_against():
    """`is_newer` is a plain numeric compare and it ranks an engine ABOVE the app.

    This asserts the HAZARD, not the fix — the fix is
    test_app_updater_refuses_an_engine_tag below. If this test ever goes red
    because someone made `is_newer` engine-aware, that is a PROBLEM and not a
    success: the helper is shared with the engine updater, so a comparison
    change made for one side silently changes the other's."""
    assert engine_updater.is_newer(ENGINE_VERSION, "3.0.2") is True
    assert engine_updater.is_newer("3.0.2", ENGINE_VERSION) is False
    # and it is the SAME function object both modules use — the coupling is the
    # reason the guard has to be a separate tag test rather than a compare tweak
    assert app_updater.is_newer is engine_updater.is_newer


def test_engine_matcher_rejects_the_application_appimage_on_linux(monkeypatch):
    """THE ORIGINAL DEFECT, pinned by name.

    The old rule was `name.endswith("x86_64.AppImage")`, which the application's
    own `persona-x86_64.AppImage` satisfies. This test FAILS if the matcher is
    ever loosened back to a bare suffix test — which is the specific loosening
    this ticket exists to prevent."""
    _force_os(monkeypatch, linux=True)
    assert engine_updater._asset_matches("persona-x86_64.AppImage") is False
    assert engine_updater._asset_matches(
        f"personium-{ENGINE_VERSION}-linux-x86_64.AppImage"
    ) is True
    # The bare suffix the old rule tested is satisfied by BOTH names, so a
    # matcher that only tested it could not tell them apart.
    assert "persona-x86_64.AppImage".endswith("x86_64.AppImage")


# ---------------------------------------------------------------------------
# THE ENGINE UPDATER SELECTS ITS OWN, ON EVERY OS.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "os_kwargs,expected_url",
    [
        ({"linux": True}, "http://x/engine-linux"),
        ({"win": True}, "http://x/engine-win"),
        ({"mac": True}, "http://x/engine-mac"),
    ],
)
def test_engine_updater_picks_the_engine_release_from_a_mixed_repository(
    monkeypatch, both_releases, os_kwargs, expected_url
):
    """Given both kinds of release together, the engine finds the ENGINE one for
    the running OS — and reports the BARE version, not the published tag."""
    _force_os(monkeypatch, **os_kwargs)
    version, url, digest = engine_updater.fetch_latest_full()
    assert version == ENGINE_VERSION, "the prefix must not leak past this boundary"
    assert url == expected_url
    assert digest.startswith("sha256:")
    # nothing of the application's may appear anywhere in the answer
    assert "app" not in url
    for app_asset in APP_ASSETS:
        assert digest != app_asset["digest"]


def test_engine_updater_ignores_an_application_release_even_alone(monkeypatch):
    """With ONLY application releases published, the engine finds nothing.

    The sharp case: on Linux the application ships an `x86_64.AppImage`, so a
    matcher-only guard would select it here. Two independent guards mean the tag
    filter refuses the release before the asset rule is even consulted."""
    _force_os(monkeypatch, linux=True)
    _serve(monkeypatch, [APP_RELEASE])
    assert engine_updater.fetch_latest_full() == ("", "", "")


def test_engine_tag_filter_holds_even_when_the_asset_rule_cannot(monkeypatch):
    """THE TAG GUARD, ISOLATED FROM THE ASSET GUARD.

    Every other engine-side test here would still pass on the asset rule alone,
    which is the whole idea of two anchors — but it means none of them proves
    the tag filter is doing anything. This one does, by presenting the ONE
    document where the asset rule is blind: an APPLICATION-tagged release
    carrying engine-shaped assets. That is what a release published under the
    wrong tag by hand looks like, and only `is_engine_tag` catches it.

    Note this reaches `_release_asset` at all only because the fetch is forced:
    discovery's refs filter would already have hidden a `v3.0.3` tag, which is
    the third guard. So the test hands the document to the selection rule
    DIRECTLY as well, to prove the tag filter refuses it on its own merits and
    not because discovery never offered it.

    Fails if the tag filter is dropped from `_release_asset`."""
    _force_os(monkeypatch, linux=True)
    mis_tagged = {"tag_name": "v3.0.3", "prerelease": False, "assets": ENGINE_ASSETS}
    # Discovery cannot even see it — the refs endpoint filters by our prefix.
    _serve(monkeypatch, [mis_tagged])
    assert engine_updater.fetch_latest_full() == ("", "", "")
    # ...and handed straight to the selection rule, bypassing discovery, the tag
    # filter refuses it by itself. This is the assertion that would fail if
    # `is_engine_tag` were dropped from `_release_asset`.
    assert engine_updater._release_asset(mis_tagged) == ("", "", "")
    assert engine_updater.is_engine_tag("v3.0.3") is False
    # ...and the identical assets under the RIGHT tag are selected normally, so
    # this is the TAG being refused and not the assets.
    assert engine_updater._release_asset(ENGINE_RELEASE)[1] == "http://x/engine-linux"
    _serve(monkeypatch, [ENGINE_RELEASE])
    assert engine_updater.fetch_latest_full()[1] == "http://x/engine-linux"


def test_engine_updater_skips_draft_releases(monkeypatch):
    """A draft is not published work and must not be installed, matching the
    Firefox updater's own `rel.get("draft")` skip.

    The draft here is the NEWEST engine tag, so this also exercises the bounded
    descent: discovery finds `personium-153.0.0.1` first, its release document
    is a draft and is refused, and the next-newest engine release is returned
    rather than nothing."""
    _force_os(monkeypatch, linux=True)
    draft = dict(ENGINE_RELEASE, tag_name="personium-153.0.0.1", draft=True,
                 assets=[{"name": "personium-153.0.0.1-linux-x86_64.AppImage",
                          "browser_download_url": "http://x/draft",
                          "digest": "sha256:" + "f" * 64}])
    _serve(monkeypatch, [draft, ENGINE_RELEASE])
    version, url, _ = engine_updater.fetch_latest_full()
    assert (version, url) == (ENGINE_VERSION, "http://x/engine-linux")


def test_engine_updater_refuses_rather_than_guesses_when_no_os_asset_matches(
    monkeypatch,
):
    """A genuine ENGINE release that lists no asset for this OS is REFUSED.

    This is where the removed Linux predictable-URL fallback used to fire, and
    the point of removing it: a release with no matching asset is a broken
    release, and the honest answer is a refusal a person can see, not a URL
    built by string-formatting a tag."""
    _force_os(monkeypatch, linux=True)
    macos_only = {
        "tag_name": ENGINE_TAG,
        "prerelease": True,
        "assets": [ENGINE_ASSETS[2]],  # the dmg alone
    }
    _serve(monkeypatch, [macos_only])
    assert engine_updater.fetch_latest_full() == ("", "", "")
    assert not hasattr(engine_updater, "appimage_url_for")


def test_engine_updater_picks_the_HIGHEST_engine_release_not_the_first_listed(
    monkeypatch,
):
    """The winner is the highest version, not whatever the API listed first —
    so a re-published or out-of-order older build cannot downgrade an engine.

    Sharper since PS-305, because discovery now orders TAG REFS and GitHub
    returns those in LEXICOGRAPHIC order, under which `personium-99.…` sorts
    ABOVE `personium-152.…`. The fixture includes exactly that tag, so a
    discovery that trusted the API's order would return the 99 build here."""
    _force_os(monkeypatch, linux=True)

    def _engine(version, url):
        return {
            "tag_name": f"personium-{version}",
            "prerelease": True,
            "assets": [{
                "name": f"personium-{version}-linux-x86_64.AppImage",
                "browser_download_url": url,
                "digest": "sha256:" + "9" * 64,
            }],
        }

    _serve(monkeypatch, [
        _engine("151.0.1.1", "http://x/engine-old"),
        # Lexicographically the LAST personium tag, numerically the lowest.
        _engine("99.0.1.1", "http://x/engine-ancient"),
        ENGINE_RELEASE,
        APP_RELEASE,
    ])
    version, url, _ = engine_updater.fetch_latest_full()
    assert (version, url) == (ENGINE_VERSION, "http://x/engine-linux")
    # And the ordering itself, stated directly.
    assert engine_updater.engine_versions_newest_first() == [
        ENGINE_VERSION, "151.0.1.1", "99.0.1.1",
    ]


def test_engine_by_tag_fetch_uses_the_same_selection_rule_and_refuses_app_tags(
    monkeypatch,
):
    """ONE selection rule, BOTH paths — the property that stops a rollback
    picking a different asset than the install did — and an application tag
    handed to the engine's by-tag fetch resolves to nothing."""
    _force_os(monkeypatch, win=True)
    seen = {}

    def fake_fetch_json(url, timeout=20, **k):
        seen["url"] = url
        # the by-tag endpoint serves whichever release the tag names
        return ENGINE_RELEASE if ENGINE_TAG in url else APP_RELEASE

    monkeypatch.setattr(engine_updater.egress, "fetch_json", fake_fetch_json)

    # A BARE version off disk (builds.json / version.txt hold bare versions) is
    # prefixed back into the real published tag for the URL.
    version, url, digest = engine_updater.fetch_release_full(ENGINE_VERSION)
    assert f"releases/tags/{ENGINE_TAG}" in seen["url"]
    assert (version, url) == (ENGINE_VERSION, "http://x/engine-win")

    # ...and the by-tag path picks EXACTLY what the latest path picks. Since
    # PS-305 that is structural rather than parallel: fetch_latest_full REACHES
    # its answer through fetch_release_full, so there is no second place an
    # asset could be chosen differently.
    _serve(monkeypatch, [ENGINE_RELEASE])
    assert engine_updater.fetch_latest_full() == (version, url, digest)

    # An APPLICATION tag must not resolve to an engine build.
    monkeypatch.setattr(engine_updater.egress, "fetch_json", fake_fetch_json)
    assert engine_updater.fetch_release_full(APP_TAG) == ("", "", "")


# ---------------------------------------------------------------------------
# DISCOVERY MUST NOT BE CROWDED OUT BY APPLICATION RELEASES.
#
# The first cut of this work enumerated `/releases?per_page=30` and filtered.
# That is safe on the Firefox upstream it was borrowed from, where every release
# is a candidate; it is not safe here. GitHub sorts the releases list
# newest-created-first ACROSS BOTH KINDS and offers no server-side "prereleases
# only" filter, so an engine release competes for those 30 slots against every
# application release. Measured on this repository: 94 releases over 65 days
# (1.45/day), so an engine prerelease published today falls off page 1 after a
# median of 17 days — and is then invisible to every installed persona, which
# reports "could not reach GitHub releases" while GitHub is perfectly reachable.
#
# Discovery therefore asks `git/matching-refs/tags/personium-`, which the SERVER
# filters. The tests below are the ones that would have caught that defect.
# ---------------------------------------------------------------------------


def _app_releases(n):
    """`n` real-shaped application releases, newest first, as this repo cuts."""
    return [
        {
            "tag_name": f"v3.{i // 20}.{i % 20}",
            "prerelease": False,
            "draft": False,
            "assets": APP_ASSETS,
        }
        for i in range(n, 0, -1)
    ]


def test_engine_is_found_behind_a_hundred_application_releases(monkeypatch):
    """THE REGRESSION TEST FOR THE CROWD-OUT DEFECT.

    One engine release, buried behind 100 application releases — more than
    three times a `per_page=30` page, and about 69 days of this repository's
    real cadence. The engine must still be found, because application releases
    are STRUCTURALLY ABSENT from the document discovery reads rather than merely
    outranked within it.

    This fails on any discovery that enumerates a bounded page of the releases
    list and filters client-side, whatever the page size — which is exactly the
    point: raising `per_page` moves the wall, it does not remove it. It is also
    the only test in this file whose fixture is larger than a page."""
    _force_os(monkeypatch, linux=True)
    crowd = _app_releases(100)
    assert len(crowd) > 30, "the fixture must exceed a per_page=30 page to bite"
    _serve(monkeypatch, crowd + [ENGINE_RELEASE])

    version, url, digest = engine_updater.fetch_latest_full()
    assert (version, url) == (ENGINE_VERSION, "http://x/engine-linux")
    assert digest.startswith("sha256:")


def test_discovery_reads_a_server_filtered_document_not_the_releases_list(
    monkeypatch,
):
    """THE MECHANISM, ASSERTED DIRECTLY — the reason the test above passes.

    Two claims, and the first is the load-bearing one:

    1. Discovery's first request is the PREFIX-FILTERED tag-ref endpoint, so
       application releases never enter the document at all. Verified against
       the live API on 2026-09-04: it is a genuine server-side prefix match
       (`tags/v` on this repo returns exactly the 98 `v*` tags), it is
       UNPAGINATED and ignores `per_page` (rails/rails answers 552 refs and
       python/cpython 649 in one document, with no `Link` header), and an
       unmatched prefix answers `200 []`.

    2. It then reads ONE named release, not a list — so the hourly unattended
       body is ~26 KB rather than the 493 KB the list shape had reached on this
       repository, which matters on a connection persona routes through Tor.

    Fails the moment discovery asks `/releases` again."""
    _force_os(monkeypatch, linux=True)
    urls = []
    inner = _fake_github(_app_releases(100) + [ENGINE_RELEASE])

    def spy(url, timeout=20, **k):
        urls.append(url)
        return inner(url, timeout=timeout, **k)

    monkeypatch.setattr(engine_updater.egress, "fetch_json", spy)
    assert engine_updater.fetch_latest_full()[0] == ENGINE_VERSION

    assert urls[0] == engine_updater.ENGINE_TAG_REFS_API
    assert urls[0].endswith("/git/matching-refs/tags/personium-"), (
        "discovery must ask the SERVER to filter by our prefix; a client-side "
        "filter over the releases list is what buried the engine"
    )
    assert urls[1] == engine_updater.RELEASE_BY_TAG_API.format(tag=ENGINE_TAG)
    assert len(urls) == 2, f"one refs fetch + one release fetch, got {urls}"
    assert not any("/releases?" in u for u in urls), (
        "the paged releases list must not be consulted at all"
    )
    # And the refs document really did carry no application tag — the property
    # the whole mechanism rests on.
    refs = engine_updater.egress.fetch_json(engine_updater.ENGINE_TAG_REFS_API)
    assert refs and all(
        r["ref"].startswith(f"refs/tags/{engine_updater.ENGINE_TAG_PREFIX}")
        for r in refs
    )


def test_a_tag_without_a_published_release_does_not_hide_the_one_behind_it(
    monkeypatch,
):
    """THE BOUNDED DESCENT.

    A tag can exist with no readable release — pushed minutes before the release
    is cut, or the release deleted while the tag stayed. Discovery orders by
    version, so such a tag is the FIRST thing it tries, and stopping there would
    reproduce the very invisibility this mechanism was chosen to prevent. It
    descends to the newest tag that actually resolves."""
    _force_os(monkeypatch, linux=True)
    _serve(
        monkeypatch,
        [ENGINE_RELEASE],
        # Two newer tags exist; neither has a release document.
        refs=["personium-154.0.0.1", "personium-153.0.0.1", ENGINE_TAG],
    )
    version, url, _ = engine_updater.fetch_latest_full()
    assert (version, url) == (ENGINE_VERSION, "http://x/engine-linux")


def test_the_descent_is_bounded_and_costs_nothing_when_github_is_unreachable(
    monkeypatch,
):
    """The descent runs HOURLY and unattended, so it must never walk the tag
    list — and a repository whose engine tags all fail to resolve must cost a
    bounded number of small requests, not one per tag.

    The second half is the cheaper guarantee and the easier one to lose: when
    the REFS fetch itself fails there is nothing to descend, so the whole check
    costs exactly one failed request and zero probes."""
    _force_os(monkeypatch, linux=True)
    probes = []
    # Far more engine tags than the bound, none of which resolve.
    many = [f"personium-152.0.{i}.1" for i in range(50, 0, -1)]

    def fetch_json(url, timeout=20, **k):
        if "matching-refs/tags/" in url:
            return [_ref(t) for t in many]
        probes.append(url)
        raise OSError("404 Not Found")

    monkeypatch.setattr(engine_updater.egress, "fetch_json", fetch_json)
    assert engine_updater.fetch_latest_full() == ("", "", "")
    assert len(probes) == engine_updater._MAX_TAG_PROBES, (
        f"the descent must be bounded by _MAX_TAG_PROBES, made {len(probes)} "
        f"probes over {len(many)} tags"
    )
    assert engine_updater._MAX_TAG_PROBES < len(many)

    # GitHub unreachable: one failed refs fetch, no probes at all.
    probes.clear()
    calls = []

    def all_fail(url, timeout=20, **k):
        calls.append(url)
        raise OSError("network down")

    monkeypatch.setattr(engine_updater.egress, "fetch_json", all_fail)
    assert engine_updater.fetch_latest_full() == ("", "", "")
    assert len(calls) == 1, f"an unreachable GitHub must cost one request: {calls}"


def test_discovery_answers_empty_for_a_repository_with_no_engine_release_yet(
    monkeypatch,
):
    """Today's real state of this repository, and it must be cheap and quiet.

    The live endpoint answers `200 []` (5 bytes) for an unmatched prefix, not a
    404 — so this is the ORDINARY answer, not an error, and it must cost exactly
    one request and no probes."""
    _force_os(monkeypatch, linux=True)
    calls = []

    def only_app_tags(url, timeout=20, **k):
        calls.append(url)
        if "matching-refs/tags/" in url:
            prefix = url.split("matching-refs/tags/", 1)[1]
            return [_ref(t) for t in ("v3.0.0", "v3.0.1", "v3.0.2")
                    if t.startswith(prefix)]
        raise AssertionError(f"no release should be fetched, got {url}")

    monkeypatch.setattr(engine_updater.egress, "fetch_json", only_app_tags)
    assert engine_updater.engine_versions_newest_first() == []
    assert engine_updater.fetch_latest_full() == ("", "", "")
    assert len(calls) == 2, "one refs fetch each, and no release probes"


def test_engine_digest_verification_is_unchanged_by_this_ticket(monkeypatch, tmp_path):
    """PS-49's fail-closed digest policy must survive the endpoint move: an
    engine asset with no published digest is still refused at the transfer,
    before any bytes move."""
    _force_os(monkeypatch, linux=True)
    monkeypatch.setattr(engine_updater, "ENGINE_DIR", str(tmp_path))
    moved = []
    monkeypatch.setattr(
        engine_updater, "_download_to", lambda *a, **k: moved.append(1) or True
    )
    with pytest.raises(engine_updater.EngineUnverifiable):
        engine_updater.download_engine(
            "http://x/engine-linux", digest="", tag=ENGINE_VERSION
        )
    assert moved == [], "an unverifiable engine asset must not be downloaded at all"


# ---------------------------------------------------------------------------
# THE APPLICATION UPDATER SELECTS ITS OWN — AND REFUSES THE ENGINE'S.
# ---------------------------------------------------------------------------


def test_app_updater_refuses_an_engine_tag(monkeypatch):
    """THE ONE-WAY TRAP, CLOSED.

    Independent of the prerelease marking on purpose: this is the guard that
    holds when a person publishes an engine release as an ordinary release by
    hand, so it must be provable WITHOUT any release document at all."""
    assert app_updater.is_app_release_tag(APP_TAG) is True
    assert app_updater.is_app_release_tag("3.0.2") is True  # unprefixed, still app
    assert app_updater.is_app_release_tag(ENGINE_TAG) is False
    assert app_updater.is_app_release_tag(ENGINE_VERSION) is True, (
        "a BARE version is the app's own tag shape — the guard is on the TAG, "
        "and an engine release never publishes one bare"
    )
    assert app_updater.is_app_release_tag("") is False

    # the consequence: the engine tag is never offered as an app update, even
    # though is_newer ranks it above every app version there has ever been.
    assert app_updater.update_available(ENGINE_TAG, "3.0.2") is False
    assert app_updater.update_available(APP_TAG, "3.0.1") is True


def test_app_updater_refuses_an_engine_tag_that_took_releases_latest(monkeypatch):
    """THE MIS-MARKED RELEASE, end to end.

    Simulates the failure the prerelease marking is supposed to prevent: an
    engine release published as an ordinary release, holding the
    `releases/latest` pointer this module reads. The app updater must still
    refuse it — that is what makes the guard INDEPENDENT rather than a backup
    that shares a single point of failure with the thing it backs up."""
    monkeypatch.setattr(app_updater, "latest_tag", lambda timeout=30: ENGINE_TAG)
    monkeypatch.setattr(
        app_updater, "remote_size", lambda url, timeout=30: pytest.fail(
            "the app updater tried to SIZE an engine release"
        )
    )
    assert app_updater.check_for_update() == ("", "", 0)

    # and the ordinary case still works, so the guard is not simply "refuse all"
    monkeypatch.setattr(app_updater, "latest_tag", lambda timeout=30: "v9.9.9")
    monkeypatch.setattr(app_updater, "remote_size", lambda url, timeout=30: 1234)
    tag, url, size = app_updater.check_for_update()
    assert (tag, size) == ("v9.9.9", 1234)
    assert "/releases/download/v9.9.9/" in url


@pytest.mark.parametrize(
    "os_kwargs,expected",
    [
        ({"linux": True}, ("http://x/app-linux", 119395520)),
        ({"win": True}, ("http://x/app-win", 68795630)),
        ({"mac": True}, ("http://x/app-mac", 164197011)),
    ],
)
def test_app_pick_asset_never_selects_an_engine_asset(monkeypatch, os_kwargs, expected):
    """Presented with BOTH releases' assets in one list, the app picks its own.

    The fallback arm is the one that matters: it used to accept any asset ending
    in this OS's extension, and `personium-…-linux-x86_64.AppImage` ends in
    `.AppImage` exactly as `persona-x86_64.AppImage` does."""
    _force_os(monkeypatch, **os_kwargs)
    assert app_updater.pick_asset(APP_ASSETS + ENGINE_ASSETS) == expected
    # engine assets ALONE yield nothing — including via the suffix fallback
    assert app_updater.pick_asset(ENGINE_ASSETS) == ("", 0)


def test_app_suffix_fallback_still_works_for_a_renamed_application_asset(monkeypatch):
    """The fallback was ANCHORED, not deleted: an application asset whose exact
    name changed is still found, as long as it is recognisably the app's."""
    _force_os(monkeypatch, linux=True)
    renamed = [{"name": "persona-3.0.3-x86_64.AppImage",
                "browser_download_url": "http://x/renamed", "size": 7}]
    assert app_updater.pick_asset(renamed + ENGINE_ASSETS) == ("http://x/renamed", 7)


# ---------------------------------------------------------------------------
# THE SYMMETRY, STATED IN ONE PLACE.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("os_kwargs", [{"linux": True}, {"win": True}, {"mac": True}])
def test_neither_updater_can_select_the_others_asset_on_any_os(monkeypatch, os_kwargs):
    """The mutual exclusion, as one assertion pair per OS: every engine asset is
    refused by the app's picker and every application asset by the engine's
    matcher. Written over the FULL asset lists so a new asset added to either
    side without a rule is caught here."""
    _force_os(monkeypatch, **os_kwargs)

    for asset in ENGINE_ASSETS:
        assert app_updater.pick_asset([asset]) == ("", 0), (
            f"the app updater selected the engine asset {asset['name']}"
        )
    for asset in APP_ASSETS:
        assert engine_updater._asset_matches(asset["name"]) is False, (
            f"the engine updater selected the application asset {asset['name']}"
        )
