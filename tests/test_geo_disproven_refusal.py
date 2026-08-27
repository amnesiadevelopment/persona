"""A proxied profile whose proxy's LAST CHECK FAILED must refuse to launch.

The defect this pins: `mark_check_failed` (store.py:216-224) records the verdict
and leaves the geography untouched, and `_proxy_timezone` branched on
`proxy.timezone` / `proxy.country_code` and NOTHING ELSE — so a proxy whose most
recent check failed kept shipping the very geography that failure disproved.

This is NOT the PS-31 case and must not be confused with it. PS-31 closed the
"no geography at all" hole (a host-timezone leak). Here the geography IS present
and the zone shipped is the exit's last-RECORDED zone, not the host's. The defect
is COHERENCE: the profile declares a location the product's own most recent
evidence says is untrue.

The governing rule, already shipped in the indicator's docstring and now applied
inward: "A failure does not age into something softer; it stays a failure at any
age."

These tests bind to the MECHANISM (the spawn spy records nothing / the raise
happens), not to prose, so removing the guard turns them red — see
test_removing_the_guard_is_what_these_tests_detect for the falsification rung.
"""

import os
import time

import pytest

import src.services.browser.invisible_launch as il
import src.services.browser.launch_policy as launch_policy
import src.services.browser.process as process
from src.models.profile import Profile
from src.models.proxy import Proxy
from src.services.proxy.errors import GeographyDisprovenError, GeographyUnknownError
from src.services.proxy.freshness import PROXY_STALE_AFTER_S, proxy_indicator_state


class _Spawned:
    """Stand-in for the spawned handle (accepts attribute assignment, as Popen
    does and a bare object() does not)."""


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _disproven_proxy(**over):
    """A REAL Proxy record in the exact state the defect ships from: geography
    on file from an earlier successful check, and a most-recent check that
    FAILED (what ProxyStore.mark_check_failed leaves behind).

    A real `Proxy` rather than a duck-typed stub on purpose: the whole point is
    that the launch path must read `last_check_ok`, so the fixture has to carry
    the field the way the store actually writes it.
    """
    fields = dict(
        name="p1",
        url="socks5://1.2.3.4:1080",
        country_code="DE",
        country_name="Germany",
        timezone="Europe/Berlin",
        checked_at=time.time() - 30,
        last_check_ok=False,
    )
    fields.update(over)
    return Proxy(**fields)


class _StoreWithDisprovenProxy:
    def resolve(self, name):
        return "socks5://1.2.3.4:1080"

    def get(self, name):
        return _disproven_proxy()


def _host_zone_is_distinctive(monkeypatch):
    """Patch the host zone to a value the assertions would catch if anything
    here fell back to it. Patch on launch_policy, not process: `_proxy_timezone`
    resolves `_host_timezone` in its OWN namespace, so a patch on the process
    re-export alias is silently bypassed.
    """
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "Europe/Kyiv")


# ---------------------------------------------------------------------------
# AC1 (red-first): the policy function refuses disproven geography.
# ---------------------------------------------------------------------------


def test_proxy_timezone_refuses_when_the_last_check_failed(monkeypatch):
    """The slice, in one assertion: geography present, last check FAILED, and
    the answer is a refusal rather than the stale zone.

    On origin/main this FAILS — `_proxy_timezone`'s branch 1 returns
    "Europe/Berlin" because nothing on the launch path reads `last_check_ok`.
    """
    _host_zone_is_distinctive(monkeypatch)
    with pytest.raises(GeographyDisprovenError):
        launch_policy._proxy_timezone(_disproven_proxy())


def test_the_disproven_zone_is_never_returned_as_a_string(monkeypatch):
    """The refusal must not be expressible as a timezone STRING.

    Specifically: the stored zone must not come back. A returned value can be
    shipped to an engine by a caller that forgot to check it, which is precisely
    how the disproven zone reached both launchers.
    """
    _host_zone_is_distinctive(monkeypatch)
    try:
        result = launch_policy._proxy_timezone(_disproven_proxy())
    except GeographyUnknownError:
        return  # correct: unrepresentable
    pytest.fail(
        f"_proxy_timezone returned {result!r} instead of refusing; the geography "
        "on file was disproven by the most recent check"
    )


def test_a_country_only_proxy_whose_check_failed_is_also_refused(monkeypatch):
    """Branch 2 (country -> zone) must be guarded too, not just branch 1.

    This is the proposal's fifth row: no explicit `timezone`, only a
    `country_code`. Guarding branch 1 alone would leave it launching.
    """
    _host_zone_is_distinctive(monkeypatch)
    with pytest.raises(GeographyDisprovenError):
        launch_policy._proxy_timezone(_disproven_proxy(timezone=""))


def test_the_failure_does_not_age_into_something_softer(monkeypatch):
    """"A failure does not age into something softer; it stays a failure at any
    age." Asserted across the staleness boundary so no age-based carve-out can
    creep in: a failed check refuses whether it is 30s or 400 days old."""
    _host_zone_is_distinctive(monkeypatch)
    now = time.time()
    for age in (30, PROXY_STALE_AFTER_S + 1, 400 * 86400):
        with pytest.raises(GeographyDisprovenError):
            launch_policy._proxy_timezone(_disproven_proxy(checked_at=now - age))


# ---------------------------------------------------------------------------
# AC3: BOTH engines refuse. A fix covering one engine is what this forbids.
# ---------------------------------------------------------------------------


def test_firefox_refuses_to_launch_on_disproven_geography(monkeypatch, tmp_path):
    """Firefox path (_spawn_invisible, process.py:173). Spy records NOTHING."""
    spawned = []
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(il, "spawn", lambda cfg: spawned.append(cfg) or _Spawned())
    monkeypatch.setattr(process, "ProxyStore", _StoreWithDisprovenProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    _host_zone_is_distinctive(monkeypatch)

    profile = Profile(name="tz-firefox", engine="firefox", proxy="p1")
    with pytest.raises(GeographyDisprovenError):
        process._spawn_invisible(profile, str(tmp_path))

    assert spawned == [], (
        "Firefox must NOT spawn a profile whose recorded geography was "
        f"disproven by the most recent check: {spawned!r}"
    )


def test_chromium_refuses_to_launch_on_disproven_geography(monkeypatch, tmp_path):
    """Chromium path (arg builder, process.py:582). Popen must never be reached."""
    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithDisprovenProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    _host_zone_is_distinctive(monkeypatch)

    profile = Profile(name="tz-chromium", proxy="p1")
    with pytest.raises(GeographyDisprovenError):
        process.spawn_browser(profile)

    assert spawned == [], (
        "Chromium must NOT spawn a profile whose recorded geography was "
        f"disproven by the most recent check: {spawned!r}"
    )


def test_neither_engine_emits_the_disproven_zone(monkeypatch, tmp_path):
    """The defect itself, asserted directly: whatever happens, the disproven
    zone must not reach an engine.

    Belt-and-braces over the two tests above — those assert "nothing spawned",
    this asserts "and specifically not THAT value". Europe/Berlin is the exact
    string origin/main ships here.
    """
    emitted = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            emitted.extend(args)
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithDisprovenProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(il, "spawn", lambda cfg: emitted.append(cfg) or _Spawned())
    _host_zone_is_distinctive(monkeypatch)

    with pytest.raises(GeographyDisprovenError):
        process.spawn_browser(Profile(name="c", proxy="p1"))
    with pytest.raises(GeographyDisprovenError):
        process._spawn_invisible(
            Profile(name="f", engine="firefox", proxy="p1"), str(tmp_path)
        )

    assert not any("Europe/Berlin" in str(item) for item in emitted), (
        f"the disproven zone reached an engine: {emitted!r}"
    )


# ---------------------------------------------------------------------------
# AC4: the refusal REACHES THE OPERATOR, and says WHICH cause.
# ---------------------------------------------------------------------------


def test_the_refusal_is_reported_to_the_operator(monkeypatch, tmp_path):
    """launcher.start_thread catches the raise and reports it via log_callback
    (launcher.py:322-328), so the operator sees WHY no browser appeared.

    Driven through the REAL spawn_browser, not a stand-in that raises a
    hand-written sentence. An earlier cut of this test monkeypatched in its own
    message and then asserted that message came back, so it would have passed
    no matter what process.py actually composed — it tested the launcher's
    plumbing and silently claimed to test AC4's CONTENT. Here the only thing
    stubbed is the environment (the store, the bookmark pool, Popen); every
    word the operator sees is composed by process._profile_timezone.
    """
    import src.services.browser.launcher as launcher_mod
    from src.services.browser.launcher import BrowserLauncher

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _StoreWithDisprovenProxy)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    _host_zone_is_distinctive(monkeypatch)
    # The real thing: whatever process.py raises is what the operator is told.
    monkeypatch.setattr(launcher_mod, "spawn_browser", process.spawn_browser)

    messages = []
    stopped = []
    BrowserLauncher().start_thread(
        Profile(name="disproven", proxy="p1"),
        messages.append,
        on_stop=lambda: stopped.append(True),
    )

    joined = " | ".join(messages)
    assert "last check failed" in joined.lower(), (
        f"the operator was never told the check FAILED: {messages!r}"
    )
    assert "never been checked" not in joined.lower(), (
        "the operator must not be told the proxy was never checked when it was "
        f"checked and the check failed: {messages!r}"
    )
    assert "proxy" in joined.lower(), (
        f"the message must name what to act on: {messages!r}"
    )
    assert "re-check" in joined.lower(), (
        f"the message must name the remedy: {messages!r}"
    )
    assert not any("Europe/Berlin" in m for m in messages), (
        f"the disproven zone must not be echoed to the operator: {messages!r}"
    )
    assert spawned == [], f"no engine may be spawned: {spawned!r}"
    assert stopped, "the UI must be released from its loading state"


def _message_for(proxy):
    """The operator-facing sentence composed for `proxy`, lowercased.

    Driven through `_profile_timezone`, which is where process.py:139-146
    actually composes the wording — not a hand-written string.
    """
    with pytest.raises(GeographyUnknownError) as raised:
        process._profile_timezone(Profile(name="p", proxy="p1"), proxy)
    return str(raised.value).lower()


class _Geoless:
    """No geography on file, and no check ever recorded — PS-31's row."""

    timezone = ""
    country_code = ""
    checked_at = 0.0
    last_check_ok = None


class _GeolessAndFailed:
    """No geography on file, and the most recent check FAILED.

    The row a BRAND-NEW proxy lands in when its FIRST check fails: app.py's
    on_check_failed -> ProxyStore.mark_check_failed writes last_check_ok=False
    while tz/country stay empty, because no successful check ever wrote them.
    Verified against the real store, not assumed — see
    test_a_failed_first_check_is_reported_as_never_checked_not_disproven.

    This is the overlap of the two causes, and it is the row the first cut of
    this file was blind to: it reads "failed" from the indicator predicate, but
    it has NO geography for a failure to have disproven.
    """

    timezone = ""
    country_code = ""
    checked_at = 1_000.0
    last_check_ok = False


def test_the_message_distinguishes_disproven_from_never_checked(monkeypatch):
    """AC4's real content: each refusal must name its OWN cause.

    The operator must not be told "never checked" when the truth is "checked,
    and it failed" — and, just as importantly, not told "the geography on file
    is disproven" when there is no geography on file. The inverse error is the
    same error.

    A row per state rather than one representative each, because the bug that
    escaped review lived precisely in the OVERLAP (no geo AND last_check_ok is
    False), which a two-representative test cannot see.
    """
    _host_zone_is_distinctive(monkeypatch)

    disproven_msg = _message_for(_disproven_proxy())
    country_only_msg = _message_for(_disproven_proxy(timezone=""))
    never_msg = _message_for(_Geoless())
    failed_no_geo_msg = _message_for(_GeolessAndFailed())

    # --- geography IS on file and a check disproved it -> "the check failed"
    for label, msg in (("explicit zone", disproven_msg), ("country only", country_only_msg)):
        assert "last check failed" in msg, (
            f"the disproven case ({label}) must say the check FAILED: {msg!r}"
        )
        assert "never been checked" not in msg, (
            f"the disproven case ({label}) must NOT claim the proxy was never "
            f"checked — it was checked, and it failed: {msg!r}"
        )

    # --- no geography ever established -> PS-31's wording, unchanged
    assert "never been checked" in never_msg, (
        f"PS-31's never-checked wording must be unchanged: {never_msg!r}"
    )

    # --- THE OVERLAP: a failed check with NO geography is a never-checked
    # proxy, not a disproven one. Nothing was disproven because nothing was
    # ever established, and claiming otherwise asserts a record that does not
    # exist.
    assert "never been checked" in failed_no_geo_msg, (
        "a proxy whose FIRST check failed has no geography on file, so it must "
        "get PS-31's true 'never been checked' wording, not a claim that "
        f"something on file was disproven: {failed_no_geo_msg!r}"
    )
    assert "disproven" not in failed_no_geo_msg, (
        "the message must not assert that geography on file was disproven when "
        f"there is no geography on file: {failed_no_geo_msg!r}"
    )

    assert disproven_msg != never_msg, "the two causes must read differently"


def test_a_failed_first_check_is_reported_as_never_checked_not_disproven(tmp_path):
    """The overlap row, reached through the REAL ProxyStore rather than a stub.

    A brand-new proxy whose first check fails is the most ordinary way to reach
    `last_check_ok=False`, and it is far more common than a proxy that worked
    and later stopped. The store is what proves the state is real: add() then
    mark_check_failed() leaves tz='' country='' last_check_ok=False.

    Both states refuse — the launch outcome is identical and fails closed
    either way — so what this pins is the SENTENCE, which is what AC4 is about.
    """
    from src.services.proxy.store import ProxyStore

    store = ProxyStore(str(tmp_path / "proxies.json"))
    store.add("bad", "socks5://1.2.3.4:1080")
    store.mark_check_failed("bad")
    proxy = store.get("bad")

    # The state is what the guard's extra conjunct is about: "failed" verdict,
    # no geography on file.
    assert proxy.last_check_ok is False
    assert not proxy.timezone and not proxy.country_code
    assert proxy_indicator_state(proxy, time.time()) == "failed", (
        "precondition: the indicator reports 'failed' regardless of whether "
        "any geography is on file — which is why the guard must ALSO require "
        "geography before claiming something was disproven"
    )

    # It still refuses (fails closed, no zone ships) ...
    with pytest.raises(GeographyUnknownError) as raised:
        launch_policy._proxy_timezone(proxy)

    # ... but it is NOT the disproven error, and does not claim a record exists.
    assert not isinstance(raised.value, GeographyDisprovenError), (
        "nothing was disproven: this proxy never had geography for a failed "
        "check to contradict"
    )
    assert "never successfully checked" in str(raised.value).lower(), (
        f"expected PS-31's true wording: {str(raised.value)!r}"
    )


# ---------------------------------------------------------------------------
# AC2 (falsification, rung 3): these tests are bound to the MECHANISM.
# ---------------------------------------------------------------------------


def test_removing_the_guard_is_what_these_tests_detect(monkeypatch):
    """The falsification rung, executable rather than asserted in prose.

    Simulate the guard's REMOVAL by neutering the one thing it consults — make
    the freshness predicate answer "verified" for everything, which is exactly
    what the launch path effectively believed before this slice — and show the
    refusal disappears and the disproven zone comes back.

    If this test ever passes with the guard genuinely deleted, the suite above
    is testing prose rather than the mechanism.
    """
    _host_zone_is_distinctive(monkeypatch)

    # Guard in place: refuses.
    with pytest.raises(GeographyDisprovenError):
        launch_policy._proxy_timezone(_disproven_proxy())

    # Guard's input neutered == guard removed: the stale zone is served again.
    monkeypatch.setattr(
        launch_policy, "proxy_indicator_state", lambda proxy, now: "verified"
    )
    assert launch_policy._proxy_timezone(_disproven_proxy()) == "Europe/Berlin", (
        "with the freshness verdict neutered the old behaviour must return — "
        "otherwise these tests are not bound to the guard at all"
    )


# ---------------------------------------------------------------------------
# The states that must KEEP LAUNCHING. The blast radius is bounded on purpose.
# ---------------------------------------------------------------------------


def test_a_passing_check_still_launches(monkeypatch):
    """"verified" is untouched: branches 1 and 2 answer exactly as before."""
    _host_zone_is_distinctive(monkeypatch)
    now = time.time()
    ok = _disproven_proxy(last_check_ok=True, checked_at=now - 30)
    assert launch_policy._proxy_timezone(ok) == "Europe/Berlin"
    assert (
        launch_policy._proxy_timezone(
            _disproven_proxy(last_check_ok=True, checked_at=now - 30, timezone="")
        )
        == "Europe/Berlin"
    )


def test_a_stale_but_passing_check_still_launches(monkeypatch):
    """"stale" is deliberately NOT merged with "failed".

    PROXY_STALE_AFTER_S was calibrated for a RENDER ("should this flag look
    confident?"), which does not transfer to a REFUSAL ("may this profile launch
    at all?"). Rotating/backconnect proxies are the product's stated target
    configuration, so staleness is their steady state — a launch-time age limit
    would lock operators out of their own profiles between checks.

    This test exists to make that carve-out DELIBERATE: anyone who later adds a
    launch-time staleness threshold has to delete an assertion that says why.
    """
    _host_zone_is_distinctive(monkeypatch)
    stale = _disproven_proxy(
        last_check_ok=True, checked_at=time.time() - (PROXY_STALE_AFTER_S + 1)
    )
    assert proxy_indicator_state(stale, time.time()) == "stale"
    assert launch_policy._proxy_timezone(stale) == "Europe/Berlin", (
        "a stale-but-verified proxy must still launch — refusing it would make "
        "rotating proxies unlaunchable between checks"
    )


def test_a_direct_profile_is_untouched(monkeypatch):
    """No proxy => no geography question => _timezone_for("US"), unchanged."""
    _host_zone_is_distinctive(monkeypatch)
    assert (
        process._profile_timezone(Profile(name="direct"), None) == "America/New_York"
    )


def test_the_remedy_works(monkeypatch):
    """The refusal is a stop, not a dead end: a passing re-check (what
    ProxyStore.mark_checked writes) restores the launch."""
    _host_zone_is_distinctive(monkeypatch)
    proxy = _disproven_proxy()
    with pytest.raises(GeographyDisprovenError):
        launch_policy._proxy_timezone(proxy)

    # mark_checked: fresh geo + last_check_ok=True.
    proxy.country_code = "PL"
    proxy.timezone = "Europe/Warsaw"
    proxy.checked_at = time.time()
    proxy.last_check_ok = True

    assert launch_policy._proxy_timezone(proxy) == "Europe/Warsaw"


# ---------------------------------------------------------------------------
# AC8: ONE authority, consulted by both the renderer and the launcher.
# ---------------------------------------------------------------------------


def test_the_freshness_predicate_has_a_single_definition():
    """The renderer and the launch path must consult the SAME predicate, not
    two copies that can drift. profile_card re-exports it; it is DEFINED once,
    in the services layer, where both can reach it."""
    import inspect

    import src.services.proxy.freshness as freshness

    assert launch_policy.proxy_indicator_state is freshness.proxy_indicator_state, (
        "the launch path must consult the same authority the renderer does"
    )
    assert (
        inspect.getsourcefile(freshness.proxy_indicator_state)
        == freshness.__file__
    )

    # The predicate is DEFINED once. Asserted over source rather than by import,
    # so this half holds even where the render layer cannot be imported.
    #
    # Resolved from THIS file's location, never from the CWD: other tests in the
    # suite chdir into tmp dirs, so a relative path here passes in isolation and
    # fails in a full run (it did).
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    card_src = (repo_root / "src/ui/components/profile_card.py").read_text(encoding="utf-8")
    assert "def proxy_indicator_state" not in card_src, (
        "profile_card must not hold a second copy of the predicate"
    )
    assert "PROXY_STALE_AFTER_S = " not in card_src, (
        "profile_card must not hold a second copy of the threshold"
    )


def test_profile_card_consumes_the_shared_predicate():
    """The renderer half of AC8, asserted by identity.

    Skipped where flet is absent (this container): importing profile_card pulls
    the whole render layer. The source-level half above still runs there, so the
    "defined once" claim is never left unverified.
    """
    pytest.importorskip("flet")

    import src.services.proxy.freshness as freshness
    import src.ui.components.profile_card as profile_card

    assert profile_card.proxy_indicator_state is freshness.proxy_indicator_state, (
        "profile_card must consume the shared predicate, not hold a second copy"
    )
    assert profile_card.PROXY_STALE_AFTER_S is freshness.PROXY_STALE_AFTER_S


def test_the_predicate_still_answers_the_four_rendered_states():
    """The relocation must not change what the renderer is told (AC7). The four
    outcomes, exercised through the moved predicate."""
    now = time.time()
    base = dict(name="P", url="socks5://1.2.3.4:1")
    assert (
        proxy_indicator_state(
            Proxy(**base, last_check_ok=False, checked_at=now - 30), now
        )
        == "failed"
    )
    assert (
        proxy_indicator_state(Proxy(**base, last_check_ok=None), now) == "unverified"
    )
    assert (
        proxy_indicator_state(
            Proxy(**base, last_check_ok=True, checked_at=now - 30), now
        )
        == "verified"
    )
    assert (
        proxy_indicator_state(
            Proxy(**base, last_check_ok=True, checked_at=now - (PROXY_STALE_AFTER_S + 1)),
            now,
        )
        == "stale"
    )


# ---------------------------------------------------------------------------
# AC9: the TRI-STATE row, addressed explicitly rather than by silence.
# ---------------------------------------------------------------------------


def test_geo_present_with_no_recorded_check_is_reachable_and_still_launches():
    """`Proxy.last_check_ok` is `bool | None`, and the None-WITH-geography row is
    REACHABLE: a legacy/hand-edited proxies.json with geo but no `last_check_ok`
    key loads through store.py:62 as None.

    This slice leaves that row LAUNCHING, deliberately. Refusing it is defensible
    on the shipped rule that a country code without a timestamp is not evidence,
    but it is a strictly wider behaviour change than the disproven case this
    ticket is scoped to, and it cannot be made without editing assertions the
    ticket requires to pass untouched (the duck-typed stand-ins in test_tz.py /
    test_geo_unknown_refusal.py / test_process.py carry geography but no check
    bookkeeping, so they all read "unverified").

    Pinned as a test so the decision is VISIBLE and a later slice that changes
    it has to change this assertion and say so, rather than silently flipping
    behaviour. See the PR discussion for the full reasoning.
    """
    legacy = Proxy(
        name="legacy",
        url="socks5://1.2.3.4:1080",
        country_code="DE",
        timezone="Europe/Berlin",
        checked_at=1.0,
        last_check_ok=None,
    )
    assert proxy_indicator_state(legacy, time.time()) == "unverified"
    assert launch_policy._proxy_timezone(legacy) == "Europe/Berlin", (
        "documented carve-out: geo-with-no-recorded-check still launches in "
        "this slice; see the PR for why it was not widened here"
    )
