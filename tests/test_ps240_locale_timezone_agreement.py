"""PS-240 — the two halves of one geo derivation must AGREE about whether the
exit country is known. Either both answer, or both refuse.

The defect, stated once: ``_locale_for`` was ``_COUNTRY_LOCALE.get(code,
"en-US")`` while its sibling ``_timezone_for`` RAISED for the identical input.
Two functions in one module taking opposite positions on the same country.

⭐ THE LOAD-BEARING HALF, and the reason a reader's first objection is wrong.
The obvious response is "the zone half's refusal already stops those profiles".
It does not — it is never reached. ``_proxy_timezone``'s FIRST branch returns
the zone the CHECK RECORDED and returns before ``_timezone_for`` is consulted at
all. So on the ORDINARY passing check against a Bulgarian exit, both answers
shipped together::

    lang = "en-US"          # invented by the locale half
    tz   = "Europe/Sofia"   # correct, from branch 1

An American-English browser whose clock is in Sofia — ``Accept-Language: en-US``
beside ``Intl.DateTimeFormat().resolvedOptions().timeZone === "Europe/Sofia"``,
a pair checkers compare directly. The zone half being strict did not prevent
this; it only guaranteed that when the two halves disagreed the disagreement
was SHIPPED rather than caught.

WHAT THESE TESTS ASSERT, AND WHAT THEY DELIBERATELY DO NOT. Every launch test
below reads the ``lang``/``tz`` pair **a real launch computes** — the Firefox
cfg dict's ``locale``/``timezone`` keys, and Chromium's ``--lang`` /
``--accept-lang`` / ``--timezone`` argv — through the public ``spawn_browser``
entry point. None of them asserts that a helper was CALLED: a call-spy would go
green on a build that called the right function and shipped the wrong string,
which is the exact defect. Both engines are asserted SEPARATELY, because a
one-engine fix is what the charter forbids and Chromium consumes the value on
four surfaces Firefox does not (``--lang``, ``--accept-lang``, the locale
extension and the voice extension).

The falsification block at the bottom re-creates the pre-fix derivation and
asserts this suite goes RED against it, so a green run means the agreement is
present rather than that the assertions are vacuous.
"""

import os

import pytest

import src.services.browser.invisible_launch as il
import src.services.browser.launch_policy as launch_policy
import src.services.browser.process as process
from src.models.profile import Profile
from src.models.proxy import Proxy
from src.services.bookmark.store import Bookmark
from src.services.browser.launch_policy import (
    _COUNTRY_LOCALE,
    _COUNTRY_TZ,
    _locale_for,
    _timezone_for,
)
from src.services.browser.refusal import classify_refusal
from src.services.proxy.errors import (
    ExitCountryUnknownError,
    GeographyUnknownError,
    LocaleUnderivableError,
    TimezoneUnderivableError,
)

# The five exits from the ticket's own transcript. Each pairs a country that was
# ABSENT from _COUNTRY_LOCALE at the defect commit with the real IANA zone a geo
# endpoint returns for it — which `_validate_geo` keeps (it accepts any string
# containing "/"), so branch 1 answers and the refusal never fires.
_CONTRADICTION_CASES = [
    ("BG", "Europe/Sofia"),
    ("RO", "Europe/Bucharest"),
    ("TH", "Asia/Bangkok"),
    ("VN", "Asia/Ho_Chi_Minh"),
    ("AR", "America/Argentina/Buenos_Aires"),
]

# Countries that were in the original 30 rows. Controls: they were coherent
# before this change and must be byte-identical after it.
_CONTROL_CASES = [("DE", "Europe/Berlin", "de-DE"), ("PL", "Europe/Warsaw", "pl-PL")]


class _Spawned:
    """Accepts attribute assignment, as Popen does and object() does not."""

    pid = 4242


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return [Bookmark(name="News", url="https://example.com/")]


def _store_for(proxy):
    class _Store:
        def resolve(self, name):
            return "socks5://1.2.3.4:1080"

        def get(self, name):
            return proxy

    return _Store


def _proxy(**geo) -> Proxy:
    """A real ``Proxy`` record with the given geography. Not a duck-typed
    stand-in: the field defaults and coercions are part of what is under test."""
    return Proxy(name="p1", url="socks5://1.2.3.4:1080", **geo)


def _refusal_for(raising: bool = False, **geo):
    """The exception ``_profile_locale`` raises for a proxy with this geography,
    RETURNED rather than raised so a caller can interrogate it.

    Goes through the real ``_profile_locale`` — the composed, operator-facing
    sentence is what the card and the 409 body carry, so asserting on
    ``_locale_for``'s bare message would be asserting on text nobody reads.
    ``raising=True`` re-raises instead, for a ``pytest.raises`` caller.
    """
    profile = Profile(name="probe", proxy="p1")
    if raising:
        return process._profile_locale(profile, _proxy(**geo))
    try:
        process._profile_locale(profile, _proxy(**geo))
    except Exception as e:  # noqa: BLE001 — the object under test
        return e
    raise AssertionError(f"no refusal was raised for geo={geo!r}")


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """A real launch with only the outside world stubbed, exposing the
    lang/tz pair each engine was actually handed.

    Nothing in the derivation is stubbed — not ``_locale_for``, not
    ``_profile_locale``, not either table. The value read back is the one the
    engine would have received.
    """
    home = tmp_path / "home"
    (home / ".local/share/applications").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(process, "DATA_DIR", str(data))
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda *a, **kw: None)
    monkeypatch.setattr(
        process._platform, "supports_linux_desktop_integration", lambda: False
    )
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process._platform, "IS_MACOS", False)
    monkeypatch.setattr(process._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(process, "_host_display_scale", lambda: 1.0)

    # A distinctive host zone the assertions would catch if any removed fallback
    # were somehow still reached. Patched on launch_policy, not process: the
    # policy functions resolve it in their OWN namespace.
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "Europe/Kyiv")

    argv: list[list[str]] = []
    ff_cfgs: list[dict] = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            argv.append(list(args))
            self.pid = 4242

    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        process, "popen_in_new_session", lambda args, **kw: _FakePopen(args, **kw)
    )
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    monkeypatch.setattr(
        il, "spawn", lambda cfg, **kw: ff_cfgs.append(cfg) or _Spawned()
    )

    class Env:
        @staticmethod
        def use(**geo):
            monkeypatch.setattr(process, "ProxyStore", _store_for(_proxy(**geo)))

        @staticmethod
        def no_proxy():
            monkeypatch.setattr(process, "ProxyStore", _store_for(None))

        @staticmethod
        def firefox(name="ff") -> tuple[str, str]:
            """The (lang, tz) pair the FIREFOX engine was handed, read off the
            cfg dict ``invisible_launch.spawn`` receives."""
            ff_cfgs.clear()
            process.spawn_browser(
                Profile(name=name, engine="firefox", proxy="p1")
            )
            assert len(ff_cfgs) == 1, "the firefox engine was not spawned once"
            return ff_cfgs[0]["locale"], ff_cfgs[0]["timezone"]

        @staticmethod
        def chromium(name="cr") -> tuple[str, str, str, list[str]]:
            """The (lang, accept_lang, tz, argv) the CHROMIUM engine was handed,
            read off the real command line."""
            argv.clear()
            process.spawn_browser(Profile(name=name, proxy="p1"))
            assert len(argv) == 1, "the chromium engine was not spawned once"
            args = argv[0]

            def one(flag: str) -> str:
                hits = [a for a in args if a.startswith(f"--{flag}=")]
                assert len(hits) == 1, f"expected exactly one --{flag}=, got {hits}"
                return hits[0].split("=", 1)[1]

            return one("lang"), one("accept-lang"), one("timezone"), args

        @staticmethod
        def firefox_direct(name="ff-direct") -> tuple[str, str]:
            ff_cfgs.clear()
            process.spawn_browser(Profile(name=name, engine="firefox"))
            assert len(ff_cfgs) == 1
            return ff_cfgs[0]["locale"], ff_cfgs[0]["timezone"]

        @staticmethod
        def chromium_direct(name="cr-direct") -> tuple[str, str]:
            argv.clear()
            process.spawn_browser(Profile(name=name))
            assert len(argv) == 1
            args = argv[0]
            lang = [a for a in args if a.startswith("--lang=")][0].split("=", 1)[1]
            tz = [a for a in args if a.startswith("--timezone=")][0].split("=", 1)[1]
            return lang, tz

    return Env


# ---------------------------------------------------------------------------
# AC1 + AC3 — the computed pair, on BOTH engines, for the five exits that shipped
# the contradiction. Asserted on the pair a real launch produces.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("country,zone", _CONTRADICTION_CASES)
def test_firefox_never_ships_en_us_beside_a_non_us_exit_zone(launch, country, zone):
    """Firefox arm (``_spawn_invisible`` -> cfg ``locale`` / ``timezone``).

    The assertion is on the PAIR, not on the locale alone: what makes this a
    defect is the two values contradicting each other, and a test that only
    checked the locale would pass on a build that silently stopped shipping the
    exit's zone.
    """
    launch.use(country_code=country, timezone=zone, checked_at=9e9, last_check_ok=True)
    lang, tz = launch.firefox()

    assert tz == zone, "precondition: branch 1 must still ship the exit's real zone"
    assert lang != "en-US", (
        f"a {country} exit shipped lang={lang!r} beside tz={tz!r} — an "
        "American-English browser whose clock is elsewhere, which is the "
        "'spoofed location' pair this ticket exists to remove"
    )
    assert lang == _COUNTRY_LOCALE[country]
    assert lang.endswith(f"-{country}"), (
        f"lang={lang!r} does not declare the exit country {country}"
    )


@pytest.mark.parametrize("country,zone", _CONTRADICTION_CASES)
def test_chromium_never_ships_en_us_beside_a_non_us_exit_zone(launch, country, zone):
    """Chromium arm, asserted on the REAL command line.

    Chromium consumes the locale on more surfaces than Firefox — ``--lang`` and
    ``--accept-lang`` here, plus the locale and voice extensions — so both flags
    are read rather than one: ``--accept-lang`` is what a server sees as the
    ``Accept-Language`` header, and it derives from ``lang`` independently of
    ``--lang``.
    """
    launch.use(country_code=country, timezone=zone, checked_at=9e9, last_check_ok=True)
    lang, accept_lang, tz, _args = launch.chromium()

    assert tz == zone, "precondition: branch 1 must still ship the exit's real zone"
    assert lang != "en-US", (
        f"a {country} exit shipped --lang={lang} beside --timezone={tz}"
    )
    assert lang == _COUNTRY_LOCALE[country]
    assert accept_lang == f"{lang},{lang.split('-')[0]}", (
        "the Accept-Language header must be derived from the SAME locale — it "
        "is the surface a server actually reads"
    )
    assert not accept_lang.startswith("en-US"), (
        f"a {country} exit still advertises Accept-Language: {accept_lang}"
    )


@pytest.mark.parametrize("country,zone", _CONTRADICTION_CASES)
def test_both_engines_compute_the_same_pair_for_the_same_exit(launch, country, zone):
    """AC3 as an EQUALITY between the arms rather than two independent claims.

    One derivation, two consumers: a fix that landed on one engine only would
    leave these disagreeing, and this is the assertion that catches it without
    depending on what the right answer happens to be.
    """
    launch.use(country_code=country, timezone=zone, checked_at=9e9, last_check_ok=True)
    ff_lang, ff_tz = launch.firefox()
    cr_lang, _accept, cr_tz, _args = launch.chromium()

    assert (ff_lang, ff_tz) == (cr_lang, cr_tz), (
        f"the two engines disagree about a {country} exit: firefox "
        f"{(ff_lang, ff_tz)} vs chromium {(cr_lang, cr_tz)}"
    )


@pytest.mark.parametrize("country,zone,expected_lang", _CONTROL_CASES)
def test_a_country_that_was_already_listed_is_unchanged(
    launch, country, zone, expected_lang
):
    """The control the transcript carried, kept as a test.

    These two were coherent before this change. If they moved, the change did
    something wider than it claims — and a suite with only the broken cases in
    it cannot tell "fixed the defect" from "changed the derivation".
    """
    launch.use(country_code=country, timezone=zone, checked_at=9e9, last_check_ok=True)
    assert launch.firefox() == (expected_lang, zone)
    cr_lang, _accept, cr_tz, _args = launch.chromium()
    assert (cr_lang, cr_tz) == (expected_lang, zone)


# ---------------------------------------------------------------------------
# The invariant itself, pinned as a PROPERTY over the whole table rather than
# over the five cases above
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["BG", "RO", "TH", "VN", "AR", "AQ", "NG", "ZW", "DE", "US", "JP", "XK"],
)
def test_the_two_halves_agree_about_whether_a_country_is_known(code):
    """THE PROPERTY. Either both answer or both refuse — never one of each.

    Parametrised across countries in the table, countries outside it, and the
    residue, so the claim is about the RULE and not about any row. This is the
    assertion that would have failed at the defect commit for every one of the
    five contradiction cases.
    """

    def answers(fn) -> bool:
        try:
            fn(code)
            return True
        except GeographyUnknownError:
            return False

    locale_answers = answers(_locale_for)
    zone_answers = answers(_timezone_for)
    assert locale_answers == zone_answers, (
        f"the two halves disagree about {code!r}: _locale_for "
        f"{'answers' if locale_answers else 'refuses'} while _timezone_for "
        f"{'answers' if zone_answers else 'refuses'}. One derivation must not "
        "take two positions on one country — that is how a profile ships "
        "en-US beside a Sofia clock."
    )


def test_the_property_holds_for_every_code_in_either_table():
    """The exhaustive form, over the union of both tables' keys.

    The parametrised test above is readable; this one is complete. A row added
    to one table alone fails here as well as in the correspondence suite.
    """
    for code in set(_COUNTRY_LOCALE) | set(_COUNTRY_TZ):
        assert _locale_for(code) and _timezone_for(code), (
            f"{code} is in a table but one half of the derivation refuses it"
        )


# ---------------------------------------------------------------------------
# AC4 — the NO-COUNTRY case is a DIFFERENT input and must be untouched
# ---------------------------------------------------------------------------


def test_the_no_country_case_still_answers_en_us_and_does_not_refuse():
    """``""`` and ``"ZZ"`` mean "no country was determined", which is NOT the
    case this refusal is for.

    The direct (no-proxy) path forces ``en-US`` deliberately so persona never
    leaks the host locale (#218), and pins a US zone so the pair agrees. Making
    THAT refuse would break a coherent, intentional identity in the name of
    fixing an incoherent, accidental one.

    ``"ZZ"`` is reserved by ISO 3166-1 as user-assigned — it names no country
    however far the tables widen — so it stays in this bucket permanently rather
    than by luck.
    """
    assert _locale_for("") == "en-US"
    assert _locale_for("ZZ") == "en-US"
    assert _locale_for("") == _locale_for("ZZ"), (
        "the two no-country inputs must be byte-identical"
    )


def test_the_no_country_case_is_distinguished_from_an_unanswerable_country():
    """The discriminator that makes the test above correct rather than a
    loophole.

    If "no country" and "a country we cannot answer for" produced the same
    answer, the refusal would be unreachable and this whole change vacuous.
    They must differ.
    """
    assert _locale_for("ZZ") == "en-US"
    with pytest.raises(LocaleUnderivableError):
        _locale_for("AQ")


# ---------------------------------------------------------------------------
# AC5 — the direct (no-proxy) path, end to end on both engines
# ---------------------------------------------------------------------------


def test_the_direct_path_is_unchanged_and_coherent_on_both_engines(launch):
    """No proxy: ``en-US`` beside a US zone, coherent by construction.

    Asserted through a real launch rather than on ``_locale_for("")``, because
    what AC5 protects is the SHIPPED pair — and the host zone is patched to a
    distinctive value the assertion would catch if it leaked.
    """
    launch.no_proxy()
    ff_lang, ff_tz = launch.firefox_direct()
    cr_lang, cr_tz = launch.chromium_direct()

    for lang, tz, engine in ((ff_lang, ff_tz, "firefox"), (cr_lang, cr_tz, "chromium")):
        assert lang == "en-US", f"{engine} direct profile shipped lang={lang!r}"
        assert tz == _timezone_for("US"), (
            f"{engine} direct profile shipped tz={tz!r}, which must be the US "
            "zone that AGREES with the forced en-US language"
        )
        assert tz != "Europe/Kyiv", (
            f"{engine} leaked the host zone into a direct profile"
        )


# ---------------------------------------------------------------------------
# AC10 — the refusal is a REFUSAL, not a crash: it names the country and lands
# on the same fail-closed path as its siblings
# ---------------------------------------------------------------------------


def test_the_locale_refusal_names_the_country_so_an_operator_can_act():
    """A refusal nobody can diagnose is a worse product than a wrong locale.
    The whole remedy is "which country is missing a row"."""
    assert "AQ" not in _COUNTRY_LOCALE, "precondition: pick a country the table lacks"
    with pytest.raises(LocaleUnderivableError) as raised:
        _locale_for("AQ")
    assert "AQ" in str(raised.value)
    assert "_COUNTRY_LOCALE" in str(raised.value), (
        "the message must name the table to edit, not just the failure type"
    )


def test_the_locale_refusal_is_caught_by_existing_fail_closed_handlers():
    """Subclassing is load-bearing, not cosmetic: every fail-closed handler
    already written says ``except GeographyUnknownError``, and all of them must
    keep catching this without being touched."""
    assert issubclass(LocaleUnderivableError, GeographyUnknownError)
    with pytest.raises(GeographyUnknownError):
        _locale_for("AQ")


def test_the_locale_refusal_gets_its_own_label_and_kind_not_the_parents():
    """It must not be classified as "proxy never checked".

    ``LocaleUnderivableError`` subclasses ``GeographyUnknownError``, so an
    ``isinstance`` chain testing the parent first would swallow it and send the
    operator to re-run a check that may already have PASSED and will keep
    passing — the missing thing is a table row, not a check result. This is the
    same ordering trap ``classify_refusal``'s two existing siblings document.
    """
    r = classify_refusal(LocaleUnderivableError("no locale for AQ"), 100.0)
    assert r is not None
    assert r.kind == "locale_underivable", (
        f"got kind {r.kind!r} — a parent branch swallowed the subclass"
    )
    assert "re-check" not in r.label.lower() and "checked" not in r.label.lower(), (
        f"the label {r.label!r} reads as a re-check prompt, which is wrong here"
    )
    assert "no locale for AQ" in r.detail, "the settled sentence must pass through"


def test_every_refusal_kind_stays_distinct():
    """``kind`` is a published contract (the API's 409 body, the MCP tool), so
    two refusals collapsing onto one value silently narrows a caller's switch."""
    excs = [
        LocaleUnderivableError("a"),
        ExitCountryUnknownError("b"),
        TimezoneUnderivableError("c"),
        GeographyUnknownError("d"),
    ]
    kinds = {classify_refusal(e, 1.0).kind for e in excs}
    assert len(kinds) == len(excs), f"refusals collapsed into {kinds}"


# ---------------------------------------------------------------------------
# THE OTHER INPUT THAT REACHES THIS PATH: a proxy IS present and its
# country_code is EMPTY. Same shipped contradiction, reached from the side
# `_locale_for` structurally cannot see.
# ---------------------------------------------------------------------------
#
# `_locale_for("")` answers `en-US`, and that is CORRECT — it is a pure function
# and cannot tell "no country was supplied" (the direct path, where en-US is
# deliberate policy, #218) from "a proxy is present and we do not know its
# country". Only the CALLER knows a proxy exists, so the gate is in
# `_profile_locale`, not in `_locale_for`, and every assertion below therefore
# drives a REAL LAUNCH rather than the helper.
#
# The shape is not hypothetical. `proxy_checker` produces it two ways ON
# PURPOSE, and one of them is pinned by a shipped test as correct behaviour:
#
#   * `_resolve_geo` REMEMBERS a partial — a 200 carrying a usable timezone but
#     no country is kept rather than discarded, on the stated reasoning that
#     condemning a healthy exit is worse than a partial answer.
#     `ProxyStore.mark_checked` then stores country_code="" beside a real zone
#     and last_check_ok=True.
#   * `_validate_geo` DROPS a code that is not two alphabetic characters while
#     keeping any tz containing "/", so a lying endpoint yields the same record
#     — `test_proxy_checker_socks::test_socks5_geo_is_dropped_when_the_endpoint_lies`
#     pins code == "" as the right answer there.
#
# And the zone half ANSWERS for this record, from `_proxy_timezone`'s FIRST
# branch, exactly as it does for the country-known case. So the two halves
# genuinely disagree and the disagreement SHIPS — the ticket's headline
# transcript line, reached by a different route.

# The zone-carrying, country-less record both paths above produce.
_COUNTRYLESS_RECORD = dict(
    country_code="", timezone="Europe/Sofia", checked_at=9e9, last_check_ok=True
)


@pytest.mark.parametrize("engine", ["firefox", "chromium"])
def test_a_proxy_with_no_country_but_a_real_zone_refuses_on_both_engines(
    launch, engine
):
    """The gap this file's first version missed, asserted through a real launch
    on EACH arm.

    Before the gate in ``_profile_locale`` this shipped, byte for byte::

        FIREFOX   lang='en-US'                       tz='Europe/Sofia'
        CHROMIUM  lang='en-US' accept-lang='en-US,en' tz='Europe/Sofia'

    Both engines are asserted separately for AC3's reason and not as ceremony:
    the two arms compute the pair at different call sites, so a gate added to
    one is a real one-engine fix.
    """
    launch.use(**_COUNTRYLESS_RECORD)
    with pytest.raises(GeographyUnknownError) as raised:
        if engine == "firefox":
            launch.firefox()
        else:
            launch.chromium()

    msg = str(raised.value)
    assert "EXIT COUNTRY is not known" in msg, (
        f"{engine}: the refusal does not say what is missing: {msg}"
    )
    assert "p1" in msg and "Refusing to launch" in msg, (
        f"{engine}: the refusal must name the proxy and say it refused: {msg}"
    )


def test_the_no_country_proxy_refusal_prompts_a_RE_CHECK_not_a_table_row():
    """The remedy runs the OPPOSITE way to its two neighbours, and saying the
    wrong one sends the operator to do work that cannot help.

    ``LocaleUnderivableError``/``TimezoneUnderivableError`` mean "we know the
    country and have no row" — a code change, and re-checking is futile. This
    means "we do not know the country" — a check that answers with one fixes
    it, and there is no row to add for a country nobody can name.
    """
    exc = _refusal_for(**_COUNTRYLESS_RECORD)
    assert isinstance(exc, ExitCountryUnknownError)
    msg = str(exc)
    assert "Re-check the proxy to resolve it" in msg, (
        f"the message must point at the remedy that actually works: {msg}"
    )
    assert "Re-checking will NOT help" not in msg, (
        "this is the row-missing sentence, and it is FALSE here — following it "
        f"leaves the operator with an unlaunchable proxy and nothing to do: {msg}"
    )
    assert "_COUNTRY_LOCALE" not in msg, (
        "asking for a table row for a country nobody can name is an "
        f"instruction the operator cannot follow: {msg}"
    )


def test_the_no_country_proxy_refusal_is_not_labelled_never_checked():
    """It gets its OWN kind and label rather than the parent's.

    The parent's ``"proxy never checked"`` is doubly wrong here: the check most
    likely PASSED (it simply answered without a country), so that label sends
    the operator hunting a failure that did not happen. This is the same
    distinction ``GeographyDisprovenError`` was split out for.
    """
    r = classify_refusal(_refusal_for(**_COUNTRYLESS_RECORD), 1.0)
    assert r is not None, "a fail-closed guard fired but no Refusal was recorded"
    assert r.kind == "exit_country_unknown", (
        f"got kind={r.kind!r}: the parent's branch swallowed the subclass, and "
        "the operator is told the proxy was never checked when it was checked "
        "and passed"
    )
    assert r.label != "proxy never checked", (
        "the short scanning label repeats the same falsehood as the kind"
    )
    assert "country" in r.label, f"the label must name what is missing: {r.label!r}"


def test_the_no_country_proxy_refusal_is_caught_by_existing_fail_closed_handlers():
    """A subclass of ``GeographyUnknownError``, so every ``except
    GeographyUnknownError`` already written catches it untouched — the same
    property PS-31's plumbing and the launcher's report path depend on."""
    assert issubclass(ExitCountryUnknownError, GeographyUnknownError)
    with pytest.raises(GeographyUnknownError):
        _refusal_for(**_COUNTRYLESS_RECORD, raising=True)


def test_a_reserved_non_country_code_on_a_proxy_takes_the_same_path():
    """``ZZ`` is ISO 3166-1 user-assigned — it names no country — so a PROXY
    carrying it is in the "we cannot name the exit" state, not the
    "country known, row missing" one.

    ⚠️ And ``_locale_for("ZZ")`` is UNCHANGED and still answers ``en-US``: this
    is the caller distinguishing two inputs the pure function cannot, exactly
    as it does for ``""``. AC4 is about the helper; this is about the launch.
    """
    assert _locale_for("ZZ") == "en-US", "AC4: the helper must not move"
    exc = _refusal_for(
        country_code="ZZ", timezone="Europe/Sofia", checked_at=9e9, last_check_ok=True
    )
    assert isinstance(exc, ExitCountryUnknownError), (
        f"a proxy carrying a reserved code got {type(exc).__name__}"
    )


def test_the_two_halves_disagree_on_this_record_without_the_gate(launch):
    """The property restated for THIS input, and the reason a helper-level
    parametrisation could never have caught it.

    ``test_the_two_halves_agree_about_whether_a_country_is_known`` cannot list
    ``""`` — the helper-level property is deliberately FALSE there, because
    ``_locale_for("")`` must keep answering for the direct path. The agreement
    that matters for a PROXIED record is therefore only observable at the launch
    surface, which is where this asserts it.
    """
    proxy = _proxy(**_COUNTRYLESS_RECORD)
    # The zone half ANSWERS, from branch 1, without consulting any country.
    assert launch_policy._proxy_timezone(proxy) == "Europe/Sofia"
    # So the locale half must not invent one. Before the gate it returned
    # "en-US" here and the pair shipped.
    with pytest.raises(GeographyUnknownError):
        process._profile_locale(Profile(name="x", proxy="p1"), proxy)


def test_falsification_removing_only_the_no_country_gate_reships_the_pair(launch):
    """Revert JUST the new gate, keep everything else — the contradiction comes
    straight back on both engines.

    Without this, the tests above prove only that *something* refuses; they do
    not prove the gate is what stops the contradictory pair.
    """

    def _pre_gate_locale(profile, proxy):
        # process.py's `_profile_locale` as it stood before the gate: the empty
        # code falls through to `_locale_for`, which answers en-US.
        if proxy is None:
            return "en-US"
        return _locale_for(getattr(proxy, "country_code", "") or "")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(process, "_profile_locale", _pre_gate_locale)
        launch.use(**_COUNTRYLESS_RECORD)
        assert launch.firefox() == ("en-US", "Europe/Sofia"), (
            "the pre-gate derivation did NOT reproduce the contradiction, so "
            "the assertions above are not measuring what they claim to"
        )
        cr_lang, cr_accept, cr_tz, _ = launch.chromium()
        assert (cr_lang, cr_accept, cr_tz) == ("en-US", "en-US,en", "Europe/Sofia")


def test_every_refusal_this_change_adds_fits_the_expanded_log_budget():
    """The refusals are RENDERED, and the reveal that shows them is BOUNDED.

    ``_MESSAGE_EXPANDED_MAX_LINES`` is 5, sized against the app's MINIMUM window
    (1024px -> ~105 chars/line). A refusal longer than 5 lines is CUT — and the
    payload sits at the END of every one of these sentences (the country, the
    table to edit, the remedy), so a cut destroys exactly the part an operator
    needs. That is not hypothetical here: this ticket's first draft of the
    no-country refusal composed 570 characters and would have wrapped to SIX.

    Asserted on the string the product actually composes, through
    ``_profile_locale``, not on a pasted copy — a reworded refusal must move
    this test rather than silently outgrow the box that renders it.
    """
    import math

    from src.ui.dialogs.log import _MESSAGE_EXPANDED_MAX_LINES

    # The log's own arithmetic, at the app's minimum window (window.min_width).
    budget = (1024 - 297) / 6.9
    prefix = "Error starting process: "  # launcher.py adds this

    for label, geo in [
        ("locale row missing", dict(country_code="AQ", timezone="Antarctica/Casey")),
        ("exit country unknown", dict(country_code="", timezone="Europe/Sofia")),
    ]:
        msg = prefix + str(_refusal_for(**geo, checked_at=9e9, last_check_ok=True))
        lines = math.ceil(len(msg) / budget)
        assert lines <= _MESSAGE_EXPANDED_MAX_LINES, (
            f"the {label} refusal composes {len(msg)} chars -> {lines} wrapped "
            f"lines at the app's minimum width, over the "
            f"{_MESSAGE_EXPANDED_MAX_LINES}-line reveal bound. It will be CUT, and "
            "the remedy is at the end of the sentence — the operator loses the one "
            "part they can act on. Shorten the message or raise the bound "
            "deliberately."
        )


@pytest.mark.parametrize("engine", ["firefox", "chromium"])
def test_a_country_outside_the_shared_set_refuses_on_both_engines(launch, engine):
    """The residue's behaviour, asserted through a REAL launch on each arm.

    ``AQ`` has no permanent population and no proxy exits there, so it is a safe
    stand-in for the residue — the point is the SHAPE of the answer, which must
    be a named refusal rather than a silent en-US or a crash.
    """
    launch.use(
        country_code="AQ", timezone="Antarctica/Casey", checked_at=9e9,
        last_check_ok=True,
    )
    with pytest.raises(GeographyUnknownError) as raised:
        if engine == "firefox":
            launch.firefox()
        else:
            launch.chromium()

    msg = str(raised.value)
    assert "AQ" in msg, f"{engine}: the refusal does not name the country: {msg}"
    assert "Re-checking will NOT help" in msg, (
        f"{engine}: the remedy is a code change, and saying otherwise sends the "
        "operator to re-run a check that already passed"
    )


# ---------------------------------------------------------------------------
# AC9 — coverage is bounded and STATED. A number and a list, not "expanded".
# ---------------------------------------------------------------------------


def test_the_five_transcript_countries_launch_coherently_rather_than_refusing(launch):
    """AC9's concrete half: widening is not merely "the invariant now holds",
    it is "and these five exits WORK"."""
    for country, zone in _CONTRADICTION_CASES:
        assert country in _COUNTRY_LOCALE and country in _COUNTRY_TZ
        launch.use(
            country_code=country, timezone=zone, checked_at=9e9, last_check_ok=True
        )
        lang, tz = launch.firefox(name=f"ff-{country}")
        assert (lang, tz) == (_COUNTRY_LOCALE[country], zone)


def test_the_shared_set_is_wide_enough_that_the_refusal_is_a_residue():
    """The bound the PR states, pinned so it cannot silently regress.

    Not an exact equality on the table size — rows will be added, and a test
    that fails on a NEW country row is a test that punishes the remedy. A FLOOR
    is the honest form: what must not happen is the set shrinking back to the
    point where refusing is the default case rather than the exception.
    """
    assert len(_COUNTRY_LOCALE) == len(_COUNTRY_TZ), (
        "the two tables must hold the same number of rows — the correspondence "
        "suite proves they hold the same KEYS"
    )
    assert len(_COUNTRY_LOCALE) >= 240, (
        f"the shared set holds {len(_COUNTRY_LOCALE)} countries. Below ~240 the "
        "refusal stops ordinary exits rather than a residue, which is the "
        "outage this ticket's ordered two-step sequence exists to avoid"
    )


def test_every_locale_row_declares_its_own_country():
    """A row whose region subtag does not match its key is a silent
    contradiction of exactly the kind this ticket removes — it would ship a
    coherent-LOOKING locale that names a different country than the exit."""
    for code, locale in _COUNTRY_LOCALE.items():
        lang, _, region = locale.partition("-")
        assert region == code, (
            f"_COUNTRY_LOCALE[{code!r}] == {locale!r} declares region {region!r}, "
            f"not {code!r} — the locale would name a country the exit is not in"
        )
        assert lang and lang.isalpha() and lang.islower(), (
            f"{locale!r} has a malformed language subtag {lang!r}"
        )


def test_no_os_provided_locale_or_timezone_database_is_read(monkeypatch):
    """AC11. The mapping ships in the tree.

    The rejected route derived the locale from the recorded ZONE via an oracle
    like ``/usr/share/zoneinfo/zone.tab`` — correct on Linux, ABSENT on Windows,
    where it would fall back to the very ``en-US`` this ticket removes. That is
    an OS axis, and a green Linux suite would not catch it. So the check is made
    mechanically: the derivation must not open any such file.

    Asserted by trapping ``open`` rather than by grepping the source, because a
    grep is satisfied by a docstring and would go green on a build that read the
    file through a helper.
    """
    opened: list[str] = []
    real_open = open

    def _spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr("builtins.open", _spy)
    for code in ("BG", "TH", "AR", "DE", ""):
        _locale_for(code)
    for code in ("BG", "TH", "AR", "DE"):
        _timezone_for(code)

    forbidden = ("zoneinfo", "zone.tab", "zone1970", "iso3166", "i18n/SUPPORTED")
    hits = [p for p in opened if any(f in p for f in forbidden)]
    assert hits == [], (
        f"the derivation read an OS-provided database: {hits}. The mapping must "
        "ship in the tree — an OS lookup is correct on Linux and silently wrong "
        "on Windows."
    )


# ---------------------------------------------------------------------------
# AC7 — FALSIFICATION. Revert only the new agreement and this suite must go RED.
# A green run above means the fix is present, not that the assertions are empty.
# ---------------------------------------------------------------------------


#: The 30 rows ``_COUNTRY_LOCALE`` held at the defect commit. Copied literally
#: so the falsification below re-creates the defect's ACTUAL state — table AND
#: function — rather than the new table read through the old function, which is
#: a build that never existed.
_PRE_FIX_COUNTRY_LOCALE = {
    "US": "en-US", "CA": "en-CA", "GB": "en-GB", "AU": "en-AU", "IE": "en-IE",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH", "FR": "fr-FR", "BE": "fr-BE",
    "ES": "es-ES", "MX": "es-MX", "IT": "it-IT", "NL": "nl-NL", "PT": "pt-PT",
    "BR": "pt-BR", "PL": "pl-PL", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK",
    "FI": "fi-FI", "UA": "uk-UA", "RU": "ru-RU", "TR": "tr-TR", "JP": "ja-JP",
    "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW", "IN": "en-IN", "SG": "en-SG",
}


def _pre_fix_locale_for(country_code: str) -> str:
    """``_locale_for`` EXACTLY as it shipped at the defect commit: one line, a
    silent ``en-US`` default, no refusal, over the 30-row table of that commit.

    Reads ``_PRE_FIX_COUNTRY_LOCALE`` rather than the live module global,
    because the live one is now widened — the old FUNCTION over the NEW table
    answers ``bg-BG`` for Bulgaria and reproduces nothing.
    """
    return _PRE_FIX_COUNTRY_LOCALE.get((country_code or "").upper(), "en-US")


def test_falsification_the_pre_fix_derivation_reproduces_the_contradiction(
    launch, monkeypatch
):
    """Restore ONLY the silent default — keep the widened tables, keep the
    engines, keep every assertion — and a Bulgarian exit must go back to
    shipping ``en-US`` beside its Sofia clock.

    This is what proves AC1's tests are load-bearing. The table reverted with it
    is the 30-row one of that commit — the old function over the NEW table
    answers ``bg-BG`` and reproduces nothing, which is a build that never
    existed and would make this control vacuous.

    Note ``_COUNTRY_TZ`` is NOT reverted: branch 1 answers from the recorded
    zone and never consults it, so the Sofia clock is unaffected. That is the
    whole load-bearing point — the contradiction needs only the locale half to
    invent.
    """
    monkeypatch.setattr(launch_policy, "_locale_for", _pre_fix_locale_for)
    monkeypatch.setattr(process, "_locale_for", _pre_fix_locale_for)

    launch.use(
        country_code="BG", timezone="Europe/Sofia", checked_at=9e9, last_check_ok=True
    )
    lang, tz = launch.firefox()

    assert (lang, tz) == ("en-US", "Europe/Sofia"), (
        "the pre-fix derivation did NOT reproduce the contradiction, so the "
        "tests above are not measuring what they claim to measure"
    )


def test_falsification_the_property_test_fails_against_the_pre_fix_derivation():
    """The invariant test itself, run against the old function. It must FAIL —
    a property test that passes on the defect pins nothing."""
    with pytest.raises(TimezoneUnderivableError):
        _timezone_for("AQ")
    # The old locale half ANSWERS the same input the zone half REFUSES. That
    # disagreement is the defect, and it is what the property test rejects.
    assert _pre_fix_locale_for("AQ") == "en-US"
    with pytest.raises(LocaleUnderivableError):
        _locale_for("AQ")


def test_falsification_a_call_spy_would_not_have_caught_this(launch):
    """Why every launch test above reads the VALUE rather than the call.

    The pre-fix code called ``_locale_for`` too — it called the right function
    and shipped the wrong string. A test asserting "the helper was called" is
    green on both builds, which is why this suite reads ``cfg["locale"]`` and
    the real ``--lang=`` argv instead.
    """
    launch.use(
        country_code="BG", timezone="Europe/Sofia", checked_at=9e9, last_check_ok=True
    )
    lang, _tz = launch.firefox()
    assert lang == "bg-BG"
    # Same function NAME, same call, opposite answers — the discriminator a call
    # spy has no access to. Both builds call `_locale_for(proxy.country_code)`
    # once; only the returned STRING tells them apart.
    assert _pre_fix_locale_for("BG") == "en-US"
    assert _locale_for("BG") == "bg-BG"


# ---------------------------------------------------------------------------
# The residue, named. AC9 asks for a number and a list, not "expanded".
# ---------------------------------------------------------------------------

#: Countries assigned by ISO 3166-1 that remain outside the shared set, with the
#: reason each is out. Stated as data so the PR's claim is checkable rather than
#: prose that rots.
_RESIDUE = {
    # No permanent civilian population and no commercial proxy exits. A zone
    # here would be a research-station convention, not a country's civil time.
    "AQ": "Antarctica — no permanent population",
    "BV": "Bouvet Island — uninhabited",
    "GS": "South Georgia & the South Sandwich Islands — no permanent population",
    "HM": "Heard & McDonald Islands — uninhabited",
    "TF": "French Southern Territories — no permanent population",
    "UM": "US Minor Outlying Islands — no permanent population",
    # Excluded ONLY because a shipped test fixture pins them as absent, not
    # because they lack an answer. See the PR and the ticket comment: both are
    # ordinary, populous exits and both deserve rows. Retargeting those two
    # fixture literals is a one-line follow-up above a worker's authority.
    "NG": "excluded: tests/test_tz.py pins 'NG' not in _COUNTRY_TZ as a precondition",
    "ZW": "excluded: tests/test_ps283_...py uses ZW as its underivable fixture",
}


def test_the_residue_is_exactly_what_the_pr_says_it_is():
    """AC9's stated bound, asserted rather than described.

    A residue documented only in a PR body is a claim nobody re-checks. This
    keeps the list and the tables in step: adding a row for a residue country
    without removing it here fails, and so does the reverse.
    """
    for code, reason in _RESIDUE.items():
        assert code not in _COUNTRY_LOCALE, (
            f"{code} is listed as residue ({reason}) but now has a locale row — "
            "remove it from _RESIDUE"
        )
        assert code not in _COUNTRY_TZ, f"{code} is listed as residue but has a zone row"
        with pytest.raises(LocaleUnderivableError):
            _locale_for(code)
        with pytest.raises(TimezoneUnderivableError):
            _timezone_for(code)


def test_the_two_fixture_excluded_countries_are_the_only_populated_residue():
    """The honest bound, pinned so it cannot quietly grow.

    Six of the eight residue entries are uninhabited territories — refusing
    them costs nothing. The other two are ordinary countries excluded for a
    TEST-FIXTURE reason, and that is a debt this test keeps visible rather than
    letting it dissolve into a longer list.
    """
    fixture_excluded = {c for c, r in _RESIDUE.items() if r.startswith("excluded:")}
    assert fixture_excluded == {"NG", "ZW"}, (
        f"the fixture-excluded residue changed to {sorted(fixture_excluded)}. "
        "Excluding a populated country to keep a test literal green is a debt, "
        "not a design — it must not grow silently."
    )
