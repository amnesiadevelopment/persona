"""PS-372: the Firefox major watcher must FIRE on a Mozilla major and STAY QUIET
across the provider's rebuild churn — and both halves are the deliverable.

WHY THIS FILE EXISTS
────────────────────
⭐ A watcher that cannot fail is worthless, and a watcher that cannot stay quiet
is worse than worthless: it trains its reader to ignore it. So BOTH ARMS are
driven here, and the workflow runs this file BEFORE any network work rather than
trusting a judgement it has never seen behave.

The specific way to get this wrong is mechanical, not stylistic. Two numbering
schemes are in play and they move independently:

    firefox-NN   the PROVIDER'S BUILD TAG, bumped on every rebuild
    151.0        the FIREFOX VERSION that build contains

Measured against the provider on 2026-09-09, `firefox-21` through `firefox-29` —
NINE build tags past the `firefox-20` we ship — are EVERY ONE of them still
Firefox `151.0`. A watcher comparing build tags fires nine times, and all nine
firings are exactly the upstream churn the owner ruled out. That population is
the fixture in `test_provider_rebuild_churn_is_silent`, so the quiet arm rests on
the real shape rather than on one invented pair of numbers.

THE THIRD ARM: "WE COULD NOT LOOK" IS NOT "NO NEWS"
───────────────────────────────────────────────────
A watcher that dies quietly on a network error looks IDENTICAL from outside to
one reporting good news. So every unreachable path is asserted to produce a
REPORTED status with its own exit code, its own issue title and a body that says
nothing was compared — never a traceback, and never `up_to_date`.

NO NETWORK. Everything here drives the pure functions or injects a fake URL
opener, so this runs offline — which is what lets the workflow put it in front of
the live reads rather than after them.
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCH = REPO_ROOT / "scripts" / "ps372_firefox_major_watch.py"
BASELINE_FILE = REPO_ROOT / "engine-baseline.txt"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "firefox-major-watch.yml"
CHROMIUM_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "chromium-upstream-watch.yml"
AUTOUPDATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-autoupdate.yml"
AUTOBUMP = REPO_ROOT / "scripts" / "engine_autobump.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def watch():
    return load(WATCH, "ps372_firefox_major_watch")


# ── fixtures shaped like what the provider and Mozilla actually return ────────


def _assets(version, *, macos=False):
    """The asset list a provider release of `version` carries.

    Shaped from the real firefox-20 release: linux arm64 + x86_64, win x86_64,
    and (on builds that have it) two macOS archives, plus the non-versioned
    sidecars.
    """
    names = [
        "checksums.txt",
        "firefox-%s-stealth-linux-arm64.tar.gz" % version,
        "firefox-%s-stealth-linux-x86_64.tar.gz" % version,
        "firefox-%s-stealth-win-x86_64.zip" % version,
        "seal.json",
        "source-commit.txt",
    ]
    if macos:
        names[4:4] = [
            "firefox-%s-stealth-macos-arm64.tar.gz" % version,
            "firefox-%s-stealth-macos-x86_64.tar.gz" % version,
        ]
    return [{"name": n} for n in names]


def _release(tag, version, *, macos=False, draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": _assets(version, macos=macos),
    }


# The provider's release list AS MEASURED on 2026-09-09 — the churn population
# this whole ticket is about. Note firefox-21..29 all still carry 151.0 and,
# separately, dropped macOS.
PROVIDER_RELEASES = (
    [_release("firefox-%d" % n, "151.0", macos=False) for n in (29, 27, 26, 25, 24, 23, 22, 21)]
    + [_release("firefox-%d" % n, "151.0", macos=True) for n in (20, 19, 18)]
    + [_release("firefox-%d" % n, "150.0.1", macos=True) for n in (17, 16, 15, 14)]
    + [{"tag_name": "usage-counter", "draft": False, "prerelease": False, "assets": []}]
)


def fake_opener(*, mozilla=None, releases=None, release_by_tag=None, fail=()):
    """A urlopen stand-in that answers the three URLs this script reads.

    `fail` names substrings of URLs that should raise instead of answering, which
    is how the "we could not look" arms are driven without a network.
    """

    def opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for needle in fail:
            if needle in url:
                raise OSError("injected failure for %s" % needle)
        if "product-details.mozilla.org" in url:
            payload = mozilla
        elif "/releases/tags/" in url:
            tag = url.rsplit("/", 1)[-1]
            payload = (release_by_tag or {}).get(tag)
            if payload is None:
                raise OSError("no such release: %s" % tag)
        else:
            payload = releases

        class _Resp:
            def __enter__(self_inner):
                return io.BytesIO(json.dumps(payload).encode("utf-8"))

            def __exit__(self_inner, *a):
                return False

        return _Resp()

    return opener


def moz(version):
    """Mozilla's feed, with the other keys present so the RIGHT one is read."""
    return {
        "FIREFOX_DEVEDITION": "156.0b4",
        "FIREFOX_ESR": "140.15.0esr",
        "FIREFOX_NIGHTLY": "157.0a1",
        "LATEST_FIREFOX_DEVEL_VERSION": "156.0b4",
        "LATEST_FIREFOX_VERSION": version,
    }


def by_tag():
    return {r["tag_name"]: r for r in PROVIDER_RELEASES}


# ═════════════════════════════════════════════════════════════════════════════
# ARM 1 — A MOZILLA MAJOR PAST OURS FIRES
# ═════════════════════════════════════════════════════════════════════════════


def test_a_higher_mozilla_major_fires(watch):
    """The arm the ticket names first: 151 -> 152 must be reported."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("152.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.NEWER_MAJOR
    assert result["our_major"] == 151
    assert result["latest_major"] == 152
    assert not watch.is_green(result["status"])
    assert watch.exit_code_for(result["status"]) == 1


def test_the_live_reading_today_fires(watch):
    """Mozilla is on 155.0.1 and we ship 151.0 — four majors, and it must fire.

    This is not a hypothetical fixture: it is the reading taken on 2026-09-09,
    and it is the news this project has never been told. It is pinned so the
    first scheduled run's behaviour is a tested expectation rather than a
    surprise.
    """
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("155.0.1"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.NEWER_MAJOR
    assert result["our_major"] == 151
    assert result["latest_major"] == 155
    assert "4 majors behind" in watch.render_report(result)


def test_the_firing_report_says_what_to_decide(watch):
    """⭐ A notification that returns a REASON beats one that returns a fact."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("152.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    body = watch.render_report(result)
    # the two options, named
    assert "Take the provider's build" in body
    assert "Self-build" in body
    assert "PARKED" in body
    # and the fact the choice turns on
    assert "macOS" in body


# ═════════════════════════════════════════════════════════════════════════════
# ARM 2 — OUR OWN MAJOR, AND THE PROVIDER'S CHURN, STAY QUIET
# ═════════════════════════════════════════════════════════════════════════════


def test_our_own_major_stays_quiet(watch):
    """The other arm: feed it our own major and it must report no news."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("151.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.UP_TO_DATE
    assert watch.is_green(result["status"])
    assert watch.exit_code_for(result["status"]) == 0


def test_a_mozilla_point_release_stays_quiet(watch):
    """151.0 -> 151.0.1 is not a major. Only the major is the trigger."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("151.0.1"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.UP_TO_DATE


@pytest.mark.parametrize("tag", ["firefox-21", "firefox-22", "firefox-23",
                                 "firefox-24", "firefox-25", "firefox-26",
                                 "firefox-27", "firefox-29"])
def test_provider_rebuild_churn_is_silent(watch, tag):
    """⛔ THE FAILURE MODE THIS TICKET EXISTS TO PREVENT.

    Every one of these build tags is PAST the firefox-20 we ship, and every one
    of them still contains Firefox 151.0. A watcher comparing build tags fires on
    all eight; this one must stay silent on all eight, because the browser did
    not move.
    """
    result = watch.watch(
        tag,
        opener=fake_opener(mozilla=moz("151.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.UP_TO_DATE, (
        "a provider rebuild at an unchanged Firefox version must not be news"
    )
    assert result["our_major"] == 151


def test_a_build_tag_bump_does_not_change_the_verdict(watch):
    """Bumping the BUILD TAG nine steps changes nothing, because the browser did not.

    This is the churn property stated as an equality rather than as eight
    separate quiet runs: the tag moved from 20 to 29 and the reported Firefox
    version, major and status are byte-identical.
    """
    opener = fake_opener(mozilla=moz("151.0"), releases=PROVIDER_RELEASES,
                         release_by_tag=by_tag())
    before = watch.watch("firefox-20", opener=opener, now="fixed")
    after = watch.watch("firefox-29", opener=opener, now="fixed")
    assert before["status"] == after["status"] == watch.UP_TO_DATE
    assert before["our_firefox_version"] == after["our_firefox_version"] == "151.0"
    assert before["our_major"] == after["our_major"]


def test_up_to_date_files_no_issue(watch, tmp_path, monkeypatch):
    """The churn guarantee at the WORKFLOW boundary, not just in the verdict.

    The issue step is gated on `report`, so the quiet arm has to write
    `report=false` — otherwise every rebuild would still notify, which is the
    thing being ruled out, merely one layer further out.
    """
    out = tmp_path / "gh_out"
    monkeypatch.setattr(
        watch, "watch",
        lambda *a, **k: {
            "baseline_tag": "firefox-20", "our_firefox_version": "151.0",
            "our_major": 151, "latest_firefox": "151.0", "latest_major": 151,
            "status": watch.UP_TO_DATE, "macos": watch.MACOS_WITH,
            "macos_tags": [], "error": None, "measured_at": "now", "forced": False,
        })
    watch.main(["--github-output", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "report=false" in text
    assert "green=true" in text


# ═════════════════════════════════════════════════════════════════════════════
# ⭐ THE TWO NUMBERING SCHEMES MUST NOT BE CONFUSED
# ═════════════════════════════════════════════════════════════════════════════


def test_the_build_tag_is_never_compared_against_a_firefox_version(watch):
    """`firefox-20` is a lookup key. 20 is not a Firefox major and never is.

    The bug this guards against is arithmetic that happens to typecheck: both
    schemes are integers, so comparing the wrong pair produces a confident wrong
    answer with no error anywhere.
    """
    assert watch.build_number("firefox-20") == 20
    # A build tag is not a Firefox version…
    assert watch.parse_firefox_version("firefox-20") is None
    assert watch.firefox_major("firefox-20") is None
    # …and a Firefox version is not a build tag.
    assert watch.build_number("151.0") is None
    assert watch.canonical_build_tag("151.0") is None


def test_a_build_tag_far_above_a_firefox_major_is_still_not_news(watch):
    """The confusion made concrete: build tag 29 vs Firefox major 151.

    If anything ever compared these two numbers, 29 < 151 would read as "we are
    behind" — or, with the operands the other way around, a build-tag bump would
    read as a browser bump. Neither happens.
    """
    result = watch.watch(
        "firefox-29",
        opener=fake_opener(mozilla=moz("151.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.UP_TO_DATE
    assert result["baseline_tag"] == "firefox-29"
    assert result["our_major"] == 151


def test_a_suffixed_cache_dir_tag_is_canonicalised(watch):
    """An INSTALLED engine's dir is `firefox-20_151.0_2026…`; the release is not."""
    assert watch.canonical_build_tag("firefox-20_151.0_20260817150018") == "firefox-20"
    assert watch.canonical_build_tag("firefox-20") == "firefox-20"
    assert watch.canonical_build_tag("nonsense") is None


# ═════════════════════════════════════════════════════════════════════════════
# ⭐ OUR SIDE IS READ FROM A REAL SOURCE, NOT A CONSTANT
# ═════════════════════════════════════════════════════════════════════════════


def test_no_firefox_version_constant_is_hardcoded_in_the_watcher(watch):
    """⛔ A hardcoded `151` would be a second place to update, and would go stale
    SILENTLY the day engine-autoupdate moves us onto a newer browser.

    Scanned over CODE only — the docstrings and comments legitimately cite the
    measured `151.0` as provenance, and forbidding that would forbid explaining
    the trap. What must not exist is a version LITERAL any comparison can reach.
    """
    import ast

    tree = ast.parse(WATCH.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            if 100 <= node.value <= 999:
                offenders.append(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A bare version string like "151.0" used as a value.
            if watch.parse_firefox_version(node.value) is not None:
                offenders.append(node.value)
    assert not offenders, (
        "the watcher must not carry a Firefox version literal — found %r" % offenders
    )


def test_our_version_comes_from_the_provider_release_not_the_tag(watch):
    """The tag names WHICH release to ask about; the release says the version."""
    version, err = watch.firefox_version_of_release(_release("firefox-20", "151.0",
                                                             macos=True))
    assert err is None
    assert version == "151.0"


def test_the_seal_is_read_when_present(watch):
    """`seal.json`'s `upstream_version` is the provider's explicit statement."""
    release = _release("firefox-20", "151.0", macos=True)
    release["_seal"] = {"upstream_version": "151.0", "tag": "firefox-20"}
    version, err = watch.firefox_version_of_release(release)
    assert err is None and version == "151.0"


def test_a_release_that_describes_itself_two_ways_is_refused(watch):
    """⚠️ A disagreement is an ERROR, not a tie to break.

    Picking a winner between two contradicting sources is how a watcher starts
    comparing a number nobody meant.
    """
    release = _release("firefox-20", "151.0", macos=True)
    release["_seal"] = {"upstream_version": "152.0"}
    version, err = watch.firefox_version_of_release(release)
    assert version is None
    assert "two ways" in err


def test_the_baseline_file_holds_a_build_tag_this_watcher_can_read(watch):
    """The live file, not a fixture — the whole source chain rests on it.

    If `engine-baseline.txt` ever stops holding a `firefox-NN` tag, the watcher
    reports `baseline_unreadable` on every scheduled run, so pinning this here
    means CI on any PR disagrees before the schedule does.
    """
    tag = watch.read_baseline_tag(str(BASELINE_FILE))
    assert watch.build_number(tag) is not None


def test_the_baseline_file_is_the_one_engine_autobump_maintains():
    """The claim that makes the derived read safe: something keeps this current.

    A source of truth nobody is obliged to update rots into a constant with extra
    steps. `scripts/engine_autobump.py` rewrites this file on every engine bump,
    which is why reading it is not the duplication the ticket forbids.
    """
    text = AUTOBUMP.read_text(encoding="utf-8")
    assert "engine-baseline.txt" in text
    assert "eb.write_text" in text


# ═════════════════════════════════════════════════════════════════════════════
# THE MACOS READING — FOUR VALUES, BECAUSE THEY ARE FOUR DECISIONS
# ═════════════════════════════════════════════════════════════════════════════


def test_macos_present_for_the_major_we_ship(watch):
    """firefox-18/19/20 carry macOS at 151, so 151 reads `with_macos`."""
    state, tags = watch.macos_state(PROVIDER_RELEASES, 151)
    assert state == watch.MACOS_WITH
    assert "firefox-20" in tags


def test_macos_absent_reads_without_macos_not_not_shipped(watch):
    """⚠️ THE PARKED SELF-BUILD'S TRIGGER. They shipped it; they left macOS out.

    Distinct from `not_shipped`, because the next step differs: here there IS a
    build and it is not enough, so the parked option's condition has fired.
    """
    releases = [_release("firefox-40", "152.0", macos=False)]
    state, tags = watch.macos_state(releases, 152)
    assert state == watch.MACOS_WITHOUT
    assert tags == ["firefox-40"]


def test_a_major_the_provider_has_not_shipped_reads_not_shipped(watch):
    state, tags = watch.macos_state(PROVIDER_RELEASES, 152)
    assert state == watch.MACOS_NOT_SHIPPED
    assert tags == []


def test_an_unreadable_release_list_reads_unknown_not_not_shipped(watch):
    """⛔ "We could not tell" must never wear "they shipped nothing"'s colour."""
    state, tags = watch.macos_state(None, 152)
    assert state == watch.MACOS_UNKNOWN
    assert tags == []


def test_one_build_with_macos_is_enough(watch):
    """The provider dropped macOS after firefox-20 while staying on 151.0.

    That does NOT make 151 a `without_macos` major: firefox-20 exists and carries
    it, so "take their build" remains available. The `without_macos` reading is
    reserved for a major where NOTHING carries macOS.
    """
    state, _tags = watch.macos_state(PROVIDER_RELEASES, 151)
    assert state == watch.MACOS_WITH


def test_each_macos_state_gets_its_own_sentence(watch):
    """Four readings, four different pieces of advice — no shared wording."""
    sentences = {watch.MACOS_SENTENCE[s] for s in
                 (watch.MACOS_WITH, watch.MACOS_WITHOUT,
                  watch.MACOS_NOT_SHIPPED, watch.MACOS_UNKNOWN)}
    assert len(sentences) == 4


def test_the_unknown_macos_reading_does_not_suppress_the_news(watch):
    """A secondary lookup failing must not lose the primary finding.

    We already know a major arrived; dropping that because the release list did
    not answer would be worse than reporting it with `unknown` beside it.
    """
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("152.0"), release_by_tag=by_tag(),
                           fail=("releases?per_page",)),
    )
    assert result["status"] == watch.NEWER_MAJOR
    assert result["macos"] == watch.MACOS_UNKNOWN
    assert "could not establish" in watch.render_report(result).lower()


# ═════════════════════════════════════════════════════════════════════════════
# ARM 3 — "WE COULD NOT LOOK" IS REPORTED, NEVER SILENT AND NEVER GREEN
# ═════════════════════════════════════════════════════════════════════════════


def test_an_unreachable_mozilla_is_reported_not_silently_up_to_date(watch):
    """⚠️ A watcher that dies quietly on a network error looks IDENTICAL to one
    reporting good news."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(release_by_tag=by_tag(), releases=PROVIDER_RELEASES,
                           fail=("product-details",)),
    )
    assert result["status"] == watch.UPSTREAM_UNREADABLE
    assert result["status"] != watch.UP_TO_DATE
    assert not watch.is_green(result["status"])
    assert watch.exit_code_for(result["status"]) == 2
    assert result["error"]


def test_an_unreachable_provider_is_its_own_status(watch):
    """The missing half is OURS, not Mozilla's — a different cause and remedy."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("155.0.1"), fail=("/releases/tags/",)),
    )
    assert result["status"] == watch.PROVIDER_UNREADABLE
    assert not watch.is_green(result["status"])


def test_a_mozilla_feed_without_the_key_is_a_failure_not_a_pass(watch):
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla={"FIREFOX_NIGHTLY": "157.0a1"},
                           release_by_tag=by_tag()),
    )
    assert result["status"] == watch.UPSTREAM_UNREADABLE
    assert "LATEST_FIREFOX_VERSION" in result["error"]


def test_a_beta_string_is_refused_rather_than_read_as_a_major(watch):
    """`156.0b4` must not be parsed to major 156 — that would fire on a beta."""
    assert watch.parse_firefox_version("156.0b4") is None
    assert watch.parse_firefox_version("157.0a1") is None
    assert watch.parse_firefox_version("140.15.0esr") is None
    assert watch.firefox_major("156.0b4") is None


def test_the_release_channel_key_is_the_one_read(watch):
    """The feed carries devel/nightly keys a major or two ahead. Read the right one."""
    result = watch.watch(
        "firefox-20",
        opener=fake_opener(mozilla=moz("151.0"), releases=PROVIDER_RELEASES,
                           release_by_tag=by_tag()),
    )
    # the feed's DEVEL key is 156.0b4 — reading it would have fired
    assert result["latest_firefox"] == "151.0"
    assert result["status"] == watch.UP_TO_DATE


def test_an_unreadable_baseline_is_reported_not_a_traceback(watch, tmp_path,
                                                            monkeypatch):
    """A traceback writes no step outputs, files no issue and uploads no
    artifact — red and SILENT, the exact failure mode this watcher exists to end.
    And this path is on the SCHEDULED route: every run reads this file.
    """
    monkeypatch.setattr(watch, "BASELINE_FILE", str(tmp_path / "absent.txt"))
    out = tmp_path / "gh_out"
    md = tmp_path / "report.md"
    code = watch.main(["--report-md", str(md), "--github-output", str(out)])
    assert code == 2
    text = out.read_text(encoding="utf-8")
    assert "status=baseline_unreadable" in text
    assert "green=false" in text
    assert "report=true" in text          # it FILES; it does not vanish
    assert "NOTHING WAS LOOKED UP" in md.read_text(encoding="utf-8")


def test_a_corrupt_baseline_is_reported_too(watch, tmp_path, monkeypatch):
    bad = tmp_path / "engine-baseline.txt"
    bad.write_text("151.0\n", encoding="utf-8")   # a VERSION, not a build tag
    monkeypatch.setattr(watch, "BASELINE_FILE", str(bad))
    out = tmp_path / "gh_out"
    assert watch.main(["--github-output", str(out)]) == 2
    assert "status=baseline_unreadable" in out.read_text(encoding="utf-8")


def test_every_unreadable_status_exits_two_and_is_not_green(watch):
    for status in (watch.UPSTREAM_UNREADABLE, watch.PROVIDER_UNREADABLE,
                   watch.BASELINE_UNREADABLE):
        assert not watch.is_green(status)
        assert watch.exit_code_for(status) == 2


def test_only_up_to_date_is_green(watch):
    """The single place the question is answered — asserted directly.

    Note `newer_major` is NOT green: it is not a defect, but it is the one
    outcome needing a person, and an interesting answer wearing green is one
    nobody sees.
    """
    assert watch.GREEN_STATUSES == frozenset({watch.UP_TO_DATE})
    assert not watch.is_green(watch.NEWER_MAJOR)


def test_every_status_has_an_exit_code_and_a_headline(watch):
    for status in watch.EXIT_FOR_STATUS:
        assert status in watch.HEADLINE
    assert set(watch.EXIT_FOR_STATUS) == set(watch.HEADLINE)


# ═════════════════════════════════════════════════════════════════════════════
# THE ISSUE TITLE IS THE DEDUP KEY
# ═════════════════════════════════════════════════════════════════════════════


def _result(watch, **kw):
    base = {
        "baseline_tag": "firefox-20", "our_firefox_version": "151.0",
        "our_major": 151, "latest_firefox": "152.0", "latest_major": 152,
        "status": watch.NEWER_MAJOR, "macos": watch.MACOS_WITH,
        "macos_tags": [], "error": None, "measured_at": "now", "forced": False,
    }
    base.update(kw)
    return base


def test_each_unreadable_cause_files_under_its_own_title(watch):
    """⚠️ Sharing a title means the second cause is SUPPRESSED into a comment on
    the first — so a day where Mozilla is down AND our baseline is corrupt would
    lose one of them."""
    titles = {
        watch.issue_title(_result(watch, status=s))
        for s in (watch.UPSTREAM_UNREADABLE, watch.PROVIDER_UNREADABLE,
                  watch.BASELINE_UNREADABLE, watch.NEWER_MAJOR)
    }
    assert len(titles) == 4


def test_the_same_news_rechecked_files_one_title(watch):
    """Daily re-checks of the same finding must dedup, or it notifies every morning."""
    a = watch.issue_title(_result(watch, measured_at="day one"))
    b = watch.issue_title(_result(watch, measured_at="day two"))
    assert a == b


def test_a_different_major_is_different_news(watch):
    assert watch.issue_title(_result(watch, latest_major=152)) != \
        watch.issue_title(_result(watch, latest_major=153))


def test_the_macos_reading_is_not_in_the_title(watch):
    """⛔ It can move from `not_shipped` to `with_macos` while the news is the
    same — a title tracking it would file the identical decision twice."""
    assert watch.issue_title(_result(watch, macos=watch.MACOS_WITH)) == \
        watch.issue_title(_result(watch, macos=watch.MACOS_NOT_SHIPPED))


def test_the_error_text_never_reaches_the_step_output_title(watch, tmp_path,
                                                            monkeypatch):
    """Outputs are bare `key=value` lines: a newline in `title=` forges outputs.

    The offending value belongs in the BODY, which is written to a file and
    passed with `--body-file`.
    """
    bad = tmp_path / "engine-baseline.txt"
    bad.write_text("oops\ngreen=true\n", encoding="utf-8")
    monkeypatch.setattr(watch, "BASELINE_FILE", str(bad))
    out = tmp_path / "gh_out"
    watch.main(["--github-output", str(out)])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "green=false" in lines
    assert "green=true" not in lines


def test_a_forced_latest_that_is_not_a_version_is_refused_not_measured(watch):
    result = watch.watch("firefox-20", forced_latest="not-a-version",
                         forced_our_version="151.0")
    assert result["status"] == watch.UPSTREAM_UNREADABLE
    assert "Nothing was compared" in result["error"]


# ═════════════════════════════════════════════════════════════════════════════
# THE WORKFLOW
# ═════════════════════════════════════════════════════════════════════════════

# ⚠️ NOT `pytest.importorskip` AT MODULE LEVEL. That raises `Skipped` during
# COLLECTION, which skips the ENTIRE FILE — so on a host without PyYAML every
# test above would vanish while the run stayed green, and the workflow's "Prove
# both arms still hold" step would prove nothing while passing. That is the
# invisibly-broken gate `tests/test_ci_shard_partition.py` exists to forbid,
# reproduced one file down. Measured: this file's first draft used it, and the
# whole suite reported `1 skipped` on a host with no PyYAML. The dependency is
# confined to the fixture, so only the tests that actually parse YAML can ever
# be skipped.

try:  # noqa: SIM105 — see the note above
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML explicitly
    yaml = None


@pytest.fixture(scope="module")
def workflow():
    if yaml is None:
        pytest.skip("PyYAML is needed to parse the workflow")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_is_not_triggered_by_pull_requests_or_pushes(workflow):
    """A new Firefox major is NEWS, not a broken build. A red here must never
    make someone's unrelated PR look broken."""
    # PyYAML parses the bare key `on:` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}


def test_workflow_does_not_bump_build_or_publish():
    """⛔ Do not auto-bump anything. This watcher reports; a human decides.

    Scanned over the EXECUTABLE body, not the whole file. The comments and the
    operator-facing remedy text legitimately NAME `scripts/engine_autobump.py` —
    the `baseline_unreadable` branch tells a human which script writes the file
    they need to restore, which is exactly the actionable advice a report should
    carry. Forbidding the mention would forbid explaining the remedy; what must
    not exist is a step that RUNS any of these.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("git commit", "git push", "git tag", "download_engine"):
        assert forbidden not in body, "the watcher must not %r" % forbidden
    assert "python3 scripts/engine_autobump.py" not in body
    assert "engine_autobump import" not in body


def test_workflow_only_writes_issues(workflow):
    perms = workflow["jobs"]["watch"]["permissions"] if "permissions" in \
        workflow["jobs"]["watch"] else workflow["permissions"]
    assert perms["contents"] == "read"
    assert perms["issues"] == "write"


def test_workflow_files_the_report_somewhere_a_human_receives_it(workflow):
    """⛔ "Do not notify into a channel nobody reads." A red Actions run is not a
    notification — nobody is subscribed to it. PS-342 settled on an ISSUE and this
    matches it, which is what the ticket asked for."""
    steps = workflow["jobs"]["watch"]["steps"]
    filing = [s for s in steps if "gh issue create" in (s.get("run") or "")]
    assert filing, "the report must be filed as an issue"
    assert "gh issue comment" in filing[0]["run"], (
        "a daily job must comment on the existing issue rather than file a "
        "duplicate every morning"
    )


def test_the_issue_step_is_gated_on_report_so_churn_files_nothing(workflow):
    steps = workflow["jobs"]["watch"]["steps"]
    filing = [s for s in steps if "gh issue create" in (s.get("run") or "")][0]
    assert "outputs.report == 'true'" in filing["if"]


def test_workflow_runs_the_selftest_before_the_network(workflow):
    """A judgement only ever observed one way is not coverage. Both arms are
    proven offline before any live read."""
    steps = workflow["jobs"]["watch"]["steps"]
    names = [s.get("name", "") for s in steps]
    selftest = next(i for i, s in enumerate(steps)
                    if "pytest" in (s.get("run") or ""))
    live = next(i for i, s in enumerate(steps)
                if "ps372_firefox_major_watch.py" in (s.get("run") or "")
                and "pytest" not in (s.get("run") or ""))
    assert selftest < live, "self-test must precede the live read; got %r" % names


def test_the_selftest_step_is_preceded_by_an_install(workflow):
    """`setup-python` gives a clean interpreter — no pytest, no PyYAML. Without
    the install the self-test is what fails, and the watcher delivers nothing
    every scheduled day while looking like it reported something."""
    steps = workflow["jobs"]["watch"]["steps"]
    selftest = next(i for i, s in enumerate(steps)
                    if "pytest" in (s.get("run") or ""))
    installs = [i for i, s in enumerate(steps)
                if "pip install" in (s.get("run") or "")]
    assert installs and min(installs) < selftest
    install_text = steps[min(installs)]["run"]
    assert "PyYAML" in install_text, (
        "this test file parses the workflow with yaml; PyYAML is declared in no "
        "requirements file, so it must be named explicitly"
    )


def test_the_selftest_runs_this_very_file(workflow):
    steps = workflow["jobs"]["watch"]["steps"]
    selftest = next(s for s in steps if "pytest" in (s.get("run") or ""))
    assert Path(__file__).name in selftest["run"]


def test_workflow_does_no_version_arithmetic_of_its_own():
    """The comparison lives in ONE tested place. A second copy in YAML is a copy
    nothing exercises."""
    text = WORKFLOW.read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("-gt ", "-lt ", "cut -d.", "awk -F."):
        assert forbidden not in body, (
            "the workflow must not compare versions itself: found %r" % forbidden
        )


def test_workflow_verdict_step_names_every_non_green_status(workflow):
    """Each cause gets its own operator-facing sentence, or the reader cannot
    tell an outage from a decision."""
    steps = workflow["jobs"]["watch"]["steps"]
    verdict = next(s for s in steps if s.get("name") == "Report the verdict")
    for status in ("newer_major", "upstream_unreadable", "provider_unreadable",
                   "baseline_unreadable"):
        assert status + ")" in verdict["run"], "no verdict branch for %s" % status


def test_workflow_verdict_step_treats_a_missing_status_as_not_news(workflow):
    steps = workflow["jobs"]["watch"]["steps"]
    verdict = next(s for s in steps if s.get("name") == "Report the verdict")
    assert "never as 'no news'" in verdict["run"]


def test_workflow_does_not_interpolate_the_dispatch_input_into_a_shell_command(workflow):
    """A `${{ inputs.* }}` expanded into a `run:` block is substituted before bash
    sees it, so a dispatch input becomes shell source text."""
    steps = workflow["jobs"]["watch"]["steps"]
    for step in steps:
        assert "${{ inputs." not in (step.get("run") or ""), (
            "dispatch input must travel through env:, never the command line"
        )
    live = next(s for s in steps if s.get("id") == "watch")
    assert "inputs.latest_firefox" in json.dumps(live.get("env") or {})


def test_workflow_does_not_touch_the_firefox_autoupdate_lane():
    """`engine-autoupdate.yml` holds `contents: write` and cuts releases. This
    read-only watcher must not be folded into it, and must not modify it."""
    text = WORKFLOW.read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "engine-autoupdate" not in body
    assert AUTOUPDATE_WORKFLOW.exists()


def test_the_two_engine_watchers_do_not_share_a_schedule_slot(workflow):
    """Same mechanism, different runner slot — so the two do not contend."""
    chromium = yaml.safe_load(CHROMIUM_WORKFLOW.read_text(encoding="utf-8"))
    ours = (workflow.get("on", workflow.get(True)))["schedule"]
    theirs = (chromium.get("on", chromium.get(True)))["schedule"]
    assert {c["cron"] for c in ours}.isdisjoint({c["cron"] for c in theirs})


def test_the_two_engine_watchers_do_not_share_an_issue_title_prefix(watch):
    """Both file issues; a shared prefix would make one engine's news dedup
    against the other's."""
    chromium_script = (REPO_ROOT / "scripts" / "ps342_chromium_watch.py").read_text(
        encoding="utf-8")
    ours = watch.issue_title(_result(watch))
    assert ours.startswith("[firefox-watch]")
    assert "[firefox-watch]" not in chromium_script


def test_watcher_script_compiles():
    import py_compile

    py_compile.compile(str(WATCH), doraise=True)
