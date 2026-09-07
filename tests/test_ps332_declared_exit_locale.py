"""PS-332 — an operator can DECLARE a proxy exit's LANGUAGE, and the residue
PS-274's door was built for can finally launch.

THE STATE THIS CLOSES
---------------------
``ProxyStore.set_manual_timezone`` (PS-274) lets an operator declare the exit's
TIMEZONE when the product's tables cannot derive one. There was no sibling for
the LOCALE, and on the exact population that declaration door was built for the
locale gate refused anyway. Measured at ``db6d528`` before this change, through
the real ``process`` helpers::

    PL control     tz=Europe/Warsaw            lang=pl-PL
    NG bare        tz=TimezoneUnderivableError lang=LocaleUnderivableError
    NG declared    tz=Africa/Lagos             lang=LocaleUnderivableError
    ZW declared    tz=Africa/Harare            lang=LocaleUnderivableError

The operator typed a valid zone, the product accepted it, and the profile still
would not launch. The network row said ``cannot launch: this exit country is
not supported yet`` — a sentence naming no gesture, honest at the time and
expired now.

The coupling is STRUCTURAL rather than incidental: ``_COUNTRY_LOCALE`` and
``_COUNTRY_TZ`` are 241 rows each with an empty symmetric difference
(``test_country_table_correspondence.py`` enforces it in both directions), so a
country needing the timezone declaration ALWAYS also lacks a locale row. The
door PS-274 shipped could never finish the job on its own population.

⭐ THE DESIGN DECISION — A LANGUAGE, NOT A LOCALE
-------------------------------------------------
All 241 ``_COUNTRY_LOCALE`` values are exactly ``lang-REGION``, and in all 241
rows the REGION subtag equals the table key (measured: 0 exceptions, 68
distinct language subtags). Combined with the country gate — a declaration is
made FOR a country and retires when the exit moves — the region half of a
declared locale is not the operator's to choose: it is the exit country already
on file.

So the operator declares a LANGUAGE SUBTAG and the product composes
``<lang>-<COUNTRY_CODE>``. An ``en-GB`` typed against a Nigerian exit is
UNREPRESENTABLE rather than merely refused, which is a shape that cannot express
the mistake in place of a ``region == country_code`` rule that can be forgotten.
The accepted set is VENDORED (``language_names.py``), never read from an OS
locale database — the OS-parity property PS-274 established for zones, which
matters MORE here because Python's stdlib offers no locale-name list at all.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
Every launch assertion goes through the REAL ``process._profile_locale`` /
``_profile_timezone`` (and, for AC2, the real chromium argv and the real
firefox cfg), never through "a helper was called". Persistence is asserted
through a FRESH ``ProxyStore`` reading the file back, never through the
in-memory object. A PASSING NON-RESIDUE CONTROL (PL) rides the end-to-end
tests, so a refusal cannot pass for the wrong reason.
"""

from __future__ import annotations

import json
import os
import re
import time

import pytest

import src.services.browser.launch_policy as launch_policy
from src.models.proxy import Proxy
from src.services.browser.launch_policy import (
    _COUNTRY_LOCALE,
    _COUNTRY_TZ,
    UNLAUNCHABLE_DECLARABLE,
    UNLAUNCHABLE_UNSUPPORTED_COUNTRY,
    declared_locale,
    proxy_is_checked_but_unlaunchable,
    proxy_unlaunchable_remedy,
)
from src.services.browser.process import _profile_locale, _profile_timezone
from src.services.proxy.errors import LocaleUnderivableError
from src.services.proxy.language_names import (
    DECLARABLE_LANGUAGE_SUBTAGS,
    ENGINE_RENAMED_SUBTAGS,
    is_declarable_language,
)
from src.services.proxy.store import ProxyStore

#: THE RESIDUE. Two ordinary countries with commercial proxy exits and no row
#: in EITHER table — the population PS-274's declaration door was built for and
#: could not serve. The residue against ISO 3166-1 alpha-2 is
#: ``AQ BV GS HM NG TF UM ZW``; the other six are uninhabited territories.
RESIDUE_CC = "NG"
RESIDUE_COUNTRY = "Nigeria"
RESIDUE_ZONE = "Africa/Lagos"
RESIDUE_LANG = "ha"          # Hausa — a real language of the exit country.

#: THE COUNTRY A MOVED EXIT LANDS IN. It must ALSO be underivable, or the
#: "refuses again after the exit moves" assertion would pass for the wrong
#: reason (a derivable country's table simply answers).
MOVED_CC = "ZW"
MOVED_COUNTRY = "Zimbabwe"
MOVED_ZONE = "Africa/Harare"

#: A DERIVABLE CONTROL that rides the end-to-end tests. It answers on BOTH
#: gates through the same code, so a residue refusal is a real reading rather
#: than an artefact of a bad stand-in object.
CONTROL_CC = "PL"
CONTROL_COUNTRY = "Poland"
CONTROL_ZONE = "Europe/Warsaw"
CONTROL_LOCALE = "pl-PL"

LOCALE_LABEL = "Exit language (optional)"
TZ_LABEL = "Exit timezone (optional)"

try:
    import cryptography  # noqa: F401

    _HAS_CRYPTO = True
except ImportError:                                    # pragma: no cover
    _HAS_CRYPTO = False

#: The two AC2 tests spawn through the product's own ``spawn_browser``, and
#: ``src.services.browser.process`` imports ``src.services.cert.terminator``,
#: which imports ``cryptography`` at module scope. Skipped LOUDLY and NARROWLY,
#: exactly as the PS-274 suite does: AC1 (the helpers' own return values) has no
#: such import and runs everywhere, so no acceptance criterion is skipped WHOLE.
_needs_crypto = pytest.mark.skipif(
    not _HAS_CRYPTO,
    reason="spawn_browser imports services.cert.terminator, which imports "
           "cryptography at module scope",
)


class _Profile:
    """The duck-typed profile the launch helpers take — the same stand-in shape
    the shipped launch-path tests use."""

    def __init__(self, proxy_name="p"):
        self.name = "prof"
        self.proxy = proxy_name


def _store(tmp_path, name="proxies.json") -> ProxyStore:
    return ProxyStore(path=str(tmp_path / name))


def _residue_proxy(tmp_path, cc=RESIDUE_CC, country=RESIDUE_COUNTRY):
    """A proxy in exactly the shipped deadlock: checked, PASSING, a country on
    file, and no zone — in a country neither table knows."""
    s = _store(tmp_path)
    s.add("ng-exit", "socks5://u:pw@1.2.3.4:1080")
    s.mark_checked("ng-exit", cc, country, "5.6.7.8", "", None, None)
    return s, "ng-exit"


# ---------------------------------------------------------------------------
# THE PREMISE — asserted once, in one place, so a later table widening says WHY
# this suite is stale in one line instead of failing a dozen tests obscurely.
# ---------------------------------------------------------------------------


def test_the_chosen_countries_are_genuinely_underivable_on_both_gates():
    """Every deadlock fixture here depends on the residue countries having NO
    row in EITHER table. PS-240 widened ``_COUNTRY_TZ`` from 31 rows to 241
    between two rounds of PS-274 and silently invalidated fifteen of its tests;
    this says so in one line if it happens again."""
    for cc in (RESIDUE_CC, MOVED_CC):
        assert cc not in _COUNTRY_TZ, f"{cc} gained a _COUNTRY_TZ row"
        assert cc not in _COUNTRY_LOCALE, f"{cc} gained a _COUNTRY_LOCALE row"
    # And the control must answer on both, or it is not a control.
    assert CONTROL_CC in _COUNTRY_TZ and CONTROL_CC in _COUNTRY_LOCALE


def test_the_structural_coupling_this_ticket_rests_on(tmp_path):
    """WHY the timezone door alone could never finish the job.

    The two tables are SET-EQUAL, so a country needing the zone declaration
    ALWAYS also lacks a locale row. That is what makes the gap structural
    rather than a two-country coincidence — the same shape recurs for every
    country a future exit lands in that neither table knows.
    """
    assert set(_COUNTRY_TZ) == set(_COUNTRY_LOCALE)
    # And the region-equals-key property the design decision rests on.
    for code, locale in _COUNTRY_LOCALE.items():
        language, _, region = locale.partition("-")
        assert region == code, f"{code} -> {locale} breaks region == key"
        assert is_declarable_language(language), (
            f"{locale}'s language subtag is outside the vendored set, so the "
            "operator could not declare the value the table itself uses"
        )


def test_the_deadlock_exists_a_declared_zone_that_still_cannot_launch(tmp_path):
    """THE DEFECT, reproduced through the real gates before it is fixed.

    A declared zone makes the TIMEZONE gate answer and leaves the LOCALE gate
    refusing — so the product accepts the operator's declaration and the
    profile still will not launch. Asserted with the locale declaration
    deliberately ABSENT, which is the pre-PS-332 state.
    """
    s, name = _residue_proxy(tmp_path)
    ok, err = s.set_manual_timezone(name, RESIDUE_ZONE)
    assert (ok, err) == (True, ""), "the zone door accepts it"
    proxy = _store(tmp_path).get(name)
    assert _profile_timezone(_Profile(name), proxy) == RESIDUE_ZONE
    with pytest.raises(LocaleUnderivableError):
        _profile_locale(_Profile(name), proxy)


# ---------------------------------------------------------------------------
# AC1 — the residue LAUNCHES once both halves are declared, driven through the
# real helpers with a passing non-residue control in the same test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cc,country,zone",
    [(RESIDUE_CC, RESIDUE_COUNTRY, RESIDUE_ZONE),
     (MOVED_CC, MOVED_COUNTRY, MOVED_ZONE)],
)
def test_both_gates_answer_once_both_halves_are_declared(tmp_path, cc, country, zone):
    """AC1. Both residue countries, both gates, through the REAL helpers, read
    back from a FRESH store — and the control asserted in the same test through
    the same code, so a refusal cannot pass for the wrong reason."""
    s, name = _residue_proxy(tmp_path, cc, country)
    assert s.set_manual_timezone(name, zone) == (True, "")
    assert s.set_manual_locale(name, RESIDUE_LANG) == (True, "")

    proxy = _store(tmp_path).get(name)
    assert _profile_timezone(_Profile(name), proxy) == zone
    assert _profile_locale(_Profile(name), proxy) == f"{RESIDUE_LANG}-{cc}"

    # THE CONTROL, through the same code and with no declaration at all.
    control = Proxy(
        name="pl-exit", url="socks5://9.9.9.9:1080", country_code=CONTROL_CC,
        country_name=CONTROL_COUNTRY, timezone=CONTROL_ZONE,
        checked_at=time.time(), last_check_ok=True,
    )
    assert _profile_timezone(_Profile("pl-exit"), control) == CONTROL_ZONE
    assert _profile_locale(_Profile("pl-exit"), control) == CONTROL_LOCALE


def test_the_declared_locale_survives_a_restart_read_back_from_disk(tmp_path):
    """AC8. A field that lives only in RAM is not a fix for a desktop app the
    operator restarts, so the read is through a SECOND ``ProxyStore`` opening
    the same file — never a re-read of the in-memory object."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)

    second_reader = _store(tmp_path)
    assert second_reader is not s
    proxy = second_reader.get(name)
    assert proxy.manual_locale_language == RESIDUE_LANG
    assert proxy.manual_locale_country == RESIDUE_CC
    assert declared_locale(proxy) == f"{RESIDUE_LANG}-{RESIDUE_CC}"


def test_the_declaration_is_actually_written_to_the_json_file(tmp_path):
    """One level below the round trip: the KEYS are on disk, so an old build
    reading a new file (and the reverse) is a key-by-key question."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    with (tmp_path / "proxies.json").open() as f:
        record = json.load(f)[name]
    assert record["manual_locale_language"] == RESIDUE_LANG
    assert record["manual_locale_country"] == RESIDUE_CC


def test_an_old_proxies_json_without_the_keys_loads_unmigrated(tmp_path):
    """A pre-PS-332 file must load with no migration step: the `.get` defaults
    are what make the upgrade silent, and a file that failed to load would take
    every proxy's SOCKS5 credentials with it."""
    (tmp_path / "proxies.json").write_text(json.dumps({
        "old": {
            "name": "old", "url": "socks5://1.2.3.4:1080", "rotate_url": "",
            "country_code": RESIDUE_CC, "country_name": RESIDUE_COUNTRY,
            "last_ip": "5.6.7.8", "timezone": "", "lat": None, "lon": None,
            "checked_at": 1.0, "last_check_ok": True,
            "manual_timezone": RESIDUE_ZONE,
            "manual_timezone_country": RESIDUE_CC,
        }
    }), encoding="utf-8")
    proxy = _store(tmp_path).get("old")
    assert proxy.manual_locale_language == ""
    assert proxy.manual_locale_country == ""
    # The pre-PS-332 declaration still works, and the record is still refused
    # on the locale half — i.e. loading an old file changes no behaviour.
    assert _profile_timezone(_Profile("old"), proxy) == RESIDUE_ZONE
    with pytest.raises(LocaleUnderivableError):
        _profile_locale(_Profile("old"), proxy)


# ---------------------------------------------------------------------------
# AC2 — the declared locale reaches BOTH ENGINES, asserted on the argv / cfg
# the way test_ps240_locale_timezone_agreement.py does, never on a helper's
# return value alone.
# ---------------------------------------------------------------------------


class _Bookmarks:
    def __init__(self, *a, **k):
        pass

    def resolve_selection(self, *a, **k):
        return []


class _Spawned:
    pid = 4242

    def poll(self):
        return None


@pytest.fixture
def launch(tmp_path, monkeypatch):
    """A REAL launch with only the outside world stubbed, exposing the
    lang/tz pair each engine was actually handed.

    Deliberately the same harness shape ``test_ps240_locale_timezone_agreement``
    uses, and for its reason: nothing in the DERIVATION is stubbed — not
    ``_locale_for``, not ``_profile_locale``, not either table, not the
    declaration — so the value read back is the one the engine would have
    received. The store is the only thing replaced, and it hands back a record
    a real ``ProxyStore`` wrote and read back from disk.
    """
    import src.services.browser.invisible_launch as il
    import src.services.browser.process as process

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
        process, "installed_chromium_version",
        lambda: process.ChromiumVersion(full="152.0.7977.75"),
    )
    monkeypatch.setattr(
        process._platform, "supports_linux_desktop_integration", lambda: False
    )
    monkeypatch.setattr(process._platform, "IS_LINUX", False)
    monkeypatch.setattr(process._platform, "IS_MACOS", False)
    monkeypatch.setattr(process._platform, "IS_WINDOWS", False)
    monkeypatch.setattr(process, "_host_display_scale", lambda: 1.0)
    # A distinctive host zone the assertions catch if any removed fallback were
    # somehow reached. Patched on launch_policy: the policy functions resolve it
    # in their OWN namespace.
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
        def use(proxy):
            class _Store:
                def __init__(self, *a, **k):
                    pass

                def resolve(self, ref):
                    return proxy.url

                def get(self, ref):
                    return proxy

            monkeypatch.setattr(process, "ProxyStore", _Store)

        @staticmethod
        def firefox(name="ff") -> tuple[str, str]:
            from src.models.profile import Profile

            ff_cfgs.clear()
            process.spawn_browser(
                Profile(name=name, engine="firefox", proxy="p1")
            )
            assert len(ff_cfgs) == 1, "the firefox engine was not spawned once"
            return ff_cfgs[0]["locale"], ff_cfgs[0]["timezone"]

        @staticmethod
        def chromium(name="cr") -> tuple[str, str, str, list[str]]:
            from src.models.profile import Profile

            argv.clear()
            process.spawn_browser(Profile(name=name, proxy="p1"))
            assert len(argv) == 1, "the chromium engine was not spawned once"
            args = argv[0]

            def one(flag: str) -> str:
                hits = [a for a in args if a.startswith(f"--{flag}=")]
                assert len(hits) == 1, f"expected one --{flag}=, got {hits}"
                return hits[0].split("=", 1)[1]

            return one("lang"), one("accept-lang"), one("timezone"), args

    return Env


@_needs_crypto
def test_the_declared_locale_reaches_the_real_chromium_argv(tmp_path, launch):
    """AC2, chromium arm: ``--lang=`` and ``--accept-lang=`` on the REAL command
    line, read off a real ``spawn_browser`` with Popen faked at the boundary —
    so nothing between the store and the process can drop it.

    ⭐ THIS IS THE TEST PS-274 COULD NOT WRITE. Its own argv test had to use a
    derivable country (RU) and records the residue's launch as NOT COVERED,
    because ``_profile_locale`` refused before the timezone was ever read. This
    one runs on NG, which is the whole point of the ticket.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_timezone(name, RESIDUE_ZONE)
    s.set_manual_locale(name, RESIDUE_LANG)
    launch.use(_store(tmp_path).get(name))

    lang, accept_lang, tz, args = launch.chromium()
    expected = f"{RESIDUE_LANG}-{RESIDUE_CC}"
    assert lang == expected
    # ``--accept-lang`` carries the fallback chain the engine builds from the
    # SAME value; asserted by prefix so a change to the chain's tail is not a
    # false failure, and by content so a silent switch to another locale is.
    assert accept_lang.split(",")[0] == expected, accept_lang
    # THE PAIR, not the locale alone: what makes the shipped defect a defect is
    # the two values contradicting each other.
    assert tz == RESIDUE_ZONE
    assert not any("en-US" in a for a in args), (
        "en-US beside a non-US exit clock is the 'spoofed location' tell"
    )
    assert not any("Europe/Kyiv" in a for a in args), (
        "the host zone must never reach the engine"
    )


@_needs_crypto
def test_the_declared_locale_reaches_the_real_firefox_cfg(tmp_path, launch):
    """AC2, firefox arm: ``cfg["locale"]`` / ``cfg["timezone"]``, asserted the
    way ``test_ps240_locale_timezone_agreement.py`` asserts the pair — because
    a fix applied to one engine only moves the contradiction to the other."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_timezone(name, RESIDUE_ZONE)
    s.set_manual_locale(name, RESIDUE_LANG)
    launch.use(_store(tmp_path).get(name))

    lang, tz = launch.firefox()
    assert (lang, tz) == (f"{RESIDUE_LANG}-{RESIDUE_CC}", RESIDUE_ZONE)


@_needs_crypto
def test_both_engines_compute_the_same_pair_for_the_declared_exit(tmp_path, launch):
    """The property that makes AC2 worth having: one derivation, two engines.
    A profile the declaration unblocks must launch identically whichever engine
    it runs on, or the fix simply moves the contradiction."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_timezone(name, RESIDUE_ZONE)
    s.set_manual_locale(name, RESIDUE_LANG)
    launch.use(_store(tmp_path).get(name))

    ff_lang, ff_tz = launch.firefox()
    cr_lang, _accept, cr_tz, _args = launch.chromium()
    assert (ff_lang, ff_tz) == (cr_lang, cr_tz)
    assert ff_lang == f"{RESIDUE_LANG}-{RESIDUE_CC}"


# ---------------------------------------------------------------------------
# AC3 — the declaration agrees with the zone declaration ABOUT THE COUNTRY, and
# retires itself when the exit moves.
# ---------------------------------------------------------------------------


def test_the_country_gate_retires_the_declaration_when_the_exit_moves(tmp_path):
    """AC3. A language declared for NG does not apply after the exit moves, and
    the launch REFUSES AGAIN — the same disarming the zone half performs.

    Driven on ONE record through a real state change, because the interesting
    failure is the TRANSITION: a per-state test would pass on an implementation
    that never disarms.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_timezone(name, RESIDUE_ZONE)
    s.set_manual_locale(name, RESIDUE_LANG)
    proxy = _store(tmp_path).get(name)
    assert _profile_locale(_Profile(name), proxy) == f"{RESIDUE_LANG}-{RESIDUE_CC}"

    # The backconnect exit moves to the OTHER residue country. The declaration
    # is still ON DISK — it is the gate, not a delete, that disarms it.
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)
    moved = _store(tmp_path).get(name)
    assert moved.manual_locale_language == RESIDUE_LANG
    assert declared_locale(moved) == ""
    with pytest.raises(LocaleUnderivableError):
        _profile_locale(_Profile(name), moved)


def test_a_moved_exit_never_composes_the_new_country_onto_the_old_language(
    tmp_path,
):
    """The sharper half of AC3, and the reason the region is COMPOSED at the
    gate rather than stored: a retired declaration must not be able to produce
    ``ha-CZ``. The country that would compose it is the country the gate has
    already refused to match, so the shape makes it unrepresentable."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, "CZ", "Czechia", "5.6.7.8", "", None, None)
    proxy = _store(tmp_path).get(name)
    assert declared_locale(proxy) == ""
    # CZ is in the tables, so the launch answers — from the TABLE, not from the
    # retired declaration.
    assert _profile_locale(_Profile(name), proxy) == _COUNTRY_LOCALE["CZ"]


def test_a_declaration_cannot_manufacture_geography_that_was_never_measured(
    tmp_path,
):
    """An unchecked proxy has no country on file, so nothing composes and the
    declaration answers "" — the same property the zone half holds."""
    s = _store(tmp_path)
    s.add("fresh", "socks5://1.2.3.4:1080")
    ok, err = s.set_manual_locale("fresh", RESIDUE_LANG)
    assert ok is False and "check" in err.lower()
    proxy = _store(tmp_path).get("fresh")
    assert declared_locale(proxy) == ""
    assert proxy.manual_locale_language == "", "a refused value must not half-land"


# ---------------------------------------------------------------------------
# AC5 — a region that contradicts the exit country is UNREPRESENTABLE.
# ---------------------------------------------------------------------------


def test_a_contradicting_region_cannot_be_expressed_at_all(tmp_path):
    """AC5, and the design decision's whole payoff.

    The operator declares a LANGUAGE; the region is the exit country already on
    file. So ``en-GB`` against a Nigerian exit is not "refused by a rule" — it
    is not a value this field can hold. A full-locale field would have needed a
    ``region == country_code`` assertion, which is a rule that can be forgotten;
    this is a shape that cannot express the mistake.
    """
    s, name = _residue_proxy(tmp_path)
    for contradiction in ("en-GB", "en_US", "pl-PL", "ha-CZ"):
        ok, err = s.set_manual_locale(name, contradiction)
        assert ok is False, contradiction
        assert "two-letter" in err, err
    assert _store(tmp_path).get(name).manual_locale_language == ""

    # And what IS accepted composes the exit's own country, never another.
    assert s.set_manual_locale(name, "en") == (True, "")
    assert declared_locale(_store(tmp_path).get(name)) == f"en-{RESIDUE_CC}"


def test_the_composed_locale_always_carries_the_exit_country(tmp_path):
    """The property stated directly rather than by example: whatever language
    is declared, the region half is the country ON FILE."""
    for language in ("ha", "yo", "en", "ig"):
        s, name = _residue_proxy(tmp_path / language)
        assert s.set_manual_locale(name, language) == (True, "")
        proxy = _store(tmp_path / language).get(name)
        assert declared_locale(proxy) == f"{language}-{RESIDUE_CC}"


# ---------------------------------------------------------------------------
# AC4 — the accepted set is VENDORED, and no OS locale database is consulted.
# ---------------------------------------------------------------------------


def test_the_language_subtag_set_reads_no_os_locale_database():
    """AC4, asserted at its source and mirroring
    ``test_ps274_declared_exit_timezone.py::test_the_zone_name_set_reads_no_os_timezone_database``.

    Python's stdlib exposes NO list of language subtags, so the candidate oracles are all
    OS- or dependency-shaped: ``locale -a`` (absent on Windows),
    ``/usr/share/i18n/locales`` (glibc only — absent on this container),
    ``babel`` / ``pycountry`` (new runtime dependencies with their own CLDR
    snapshots). Each accepts a DIFFERENT set per platform, and the operator's
    value feeds an engine as fact, so "accepted on my machine, refused on
    yours" is not an acceptable property. The module must therefore import none
    of them.
    """
    import inspect

    import src.services.proxy.language_names as language_names

    source = inspect.getsource(language_names)
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    # Strip the module docstring, so the assertion is about CODE rather than
    # about the prose explaining why these are absent.
    body = code.split('"""', 2)[-1]
    for forbidden in ("import locale", "import babel", "import pycountry",
                      "getdefaultlocale", "setlocale", "locale_alias",
                      "/usr/share", "subprocess"):
        assert forbidden not in body, forbidden


def test_the_accepted_set_is_a_fixed_size_shipped_with_the_product():
    """Byte-identical on Windows, macOS and Linux BECAUSE it is source. The
    number is pinned so a regeneration that silently truncated the list (a
    partial download, a wrong resource) is caught rather than shipped."""
    assert len(DECLARABLE_LANGUAGE_SUBTAGS) == 181


def test_no_declarable_language_is_one_an_ENGINE_RENAMES():
    """⭐ THE PROPERTY, not the two names that first exposed it.

    THE DEFECT THIS EXISTS FOR. The generator's first filter was
    ``"Deprecated" in record`` while the module's prose claimed the rule was
    "an engine must not report the declared value back under another name".
    Those are different rules, and the shipped module SAID ``sh`` was excluded
    while accepting it: ``set_manual_locale("ng", "sh")`` returned ``(True,
    "")`` and shipped ``--lang=sh-NG`` / ``cfg["locale"]="sh-NG"``, while the
    engine canonicalizes that to ``sr-Latn`` — a declared-vs-observed
    disagreement inside one realm, which is precisely the class of tell PS-2
    exists to close, and the exact failure the exclusion was written to
    prevent. ``tl`` -> ``fil`` was a second instance and ``tw`` -> ``ak`` a
    third; a per-name test for ``sh`` would have been green for both.

    WHY THIS IS PHRASED AS A SET RELATION. The old suite could not have caught
    this and it is worth naming why, because the same blindness is easy to
    rebuild: ``test_the_validator_rejects_everything_that_is_not_a_subtag``
    probes SHAPES, and ``test_every_language_the_product_itself_uses_is_
    declarable`` asserts ``table ⊆ set`` — the OTHER direction, which is silent
    about anything the table does not happen to use. Neither asks the question
    this one does.

    NO ICU AND NO OS DATABASE AT RUNTIME (AC4). The rename facts are VENDORED
    beside the list, in ``ENGINE_RENAMED_SUBTAGS``, for the same reason the
    list itself is: the accepted set must be byte-identical on Windows, macOS
    and Linux, and an ICU that ships with the host is not that. The expectation
    was MEASURED once (node 24 / ICU 78.2, over the full 190 x 261 = 49,590
    language/region cross-product) and committed.
    """
    assert not (DECLARABLE_LANGUAGE_SUBTAGS & set(ENGINE_RENAMED_SUBTAGS)), (
        DECLARABLE_LANGUAGE_SUBTAGS & set(ENGINE_RENAMED_SUBTAGS)
    )
    # And the three the field test admitted are named here as REGRESSION
    # anchors — the property above is the guard, these are the witnesses.
    for renamed in ("sh", "tl", "tw"):
        assert renamed in ENGINE_RENAMED_SUBTAGS, renamed
        assert not is_declarable_language(renamed), renamed


def test_a_language_an_engine_renames_cannot_be_DECLARED_at_all(tmp_path):
    """The property above, driven through the shipped writer rather than
    asserted about a set — because the set is only interesting if the door
    actually consults it. This is the reproduction of the audited defect:
    before the fix this call returned ``(True, "")``.
    """
    s, name = _residue_proxy(tmp_path)
    for renamed, replacement in sorted(ENGINE_RENAMED_SUBTAGS.items()):
        ok, err = s.set_manual_locale(name, renamed)
        assert ok is False, f"{renamed!r} was accepted; engine renames it"
        assert renamed in err
        # THE SENTENCE NAMES WHAT THE ENGINE ANSWERS. These are REAL languages
        # refused for a reason a non-language is not, so "'sh' is not a
        # language code" would be both false and a dead end.
        assert replacement in err, (renamed, err)
    # Nothing was written by any of them.
    assert _store(tmp_path).get(name).manual_locale_language == ""


def test_the_refusal_never_names_a_remedy_that_would_be_refused_too(tmp_path):
    """⛔ THE 'REMEDY THAT LOOPS', rebuilt one gate further along — the exact
    shape this whole ticket exists to end, and the one a helpful error message
    invites.

    Six of the nine renames point at a two-letter subtag this door accepts
    (``sh`` -> ``sr``, ``tw`` -> ``ak``, and the four deprecated ones), so the
    refusal can name it. THREE DO NOT: ``bh`` -> ``bho`` and ``tl`` -> ``fil``
    are THREE-letter subtags and the declarable set is two-letter only. A
    message telling the operator to "declare 'fil' instead" would send them
    back through the same door to be refused again — which is precisely the
    ``UNSUPPORTED_COUNTRY_NOTE`` defect this ticket was filed to fix.

    So the property is: WHATEVER a refusal tells the operator to type, typing
    it must be accepted. Asserted over every rename rather than over the three
    that happen to be affected today.
    """
    s, name = _residue_proxy(tmp_path)
    for renamed in sorted(ENGINE_RENAMED_SUBTAGS):
        ok, err = s.set_manual_locale(name, renamed)
        assert ok is False, renamed
        # WHATEVER the sentence tells the operator to type, typing it must be
        # accepted — asserted by actually typing it into a fresh store.
        for quoted in re.findall(r"Declare '([a-z-]+)'", err):
            assert is_declarable_language(quoted), (renamed, quoted, err)
            s2, n2 = _residue_proxy(tmp_path / renamed)
            ok2, err2 = s2.set_manual_locale(n2, quoted)
            assert ok2 is True, (renamed, quoted, err2)
    # The three with no two-letter replacement say so and name no gesture,
    # rather than naming one that loops.
    for dead_end in ("bh", "tl"):
        _, err = s.set_manual_locale(name, dead_end)
        assert "Declare '" not in err, (dead_end, err)
        assert ENGINE_RENAMED_SUBTAGS[dead_end] in err


def test_the_two_CLDR_aliases_the_product_itself_needs_are_NOT_excluded():
    """THE OTHER DIRECTION, and the reason the exclusion is measured data
    rather than "every CLDR ``<languageAlias>`` entry".

    CLDR's alias table also carries ``nb`` -> ``no`` and ``sr`` -> ``sh``.
    ICU does not apply those directions — ``nb-NO`` and ``sr-RS`` canonicalize
    to themselves, measured — and BOTH are live values in the shipped
    ``_COUNTRY_LOCALE`` table. Excluding them by reading the alias table
    naively would make a locale the product itself ships undeclarable, which is
    the same class of defect one step in the other direction: an over-wide
    exclusion is as wrong as an under-wide one, and only a measurement
    distinguishes them.
    """
    for keep in ("nb", "sr"):
        assert keep not in ENGINE_RENAMED_SUBTAGS, keep
        assert is_declarable_language(keep), keep
    # Stated as the set relation too, so a future regeneration that widened the
    # exclusion into the shipped table fails here rather than in the field.
    used = {locale.split("-")[0] for locale in _COUNTRY_LOCALE.values()}
    assert not (used & set(ENGINE_RENAMED_SUBTAGS)), used & set(
        ENGINE_RENAMED_SUBTAGS
    )


def test_the_rename_is_measured_on_the_COMPOSED_form_not_the_bare_subtag():
    """WHY ``tw`` IS IN THE LIST, and the reading that a bare-subtag check gets
    wrong.

    This product never ships a bare language subtag: ``declared_locale``
    composes ``<lang>-<COUNTRY>`` and hands THAT to the engine. ``tw`` alone
    canonicalizes to ``tw`` — so a check written against the bare form passes
    it — while ``tw-NG`` canonicalizes to ``ak-NG``. The exclusion set must
    therefore be measured on the composed value, which is the unit the operator
    is actually declaring.

    Asserted here as the shape of the recorded fact rather than by re-running
    ICU: every excluded subtag records what an engine answers INSTEAD, and that
    replacement is a different language subtag — never the subtag itself, which
    would be a no-op entry papering over a bad measurement.
    """
    for tag, replacement in ENGINE_RENAMED_SUBTAGS.items():
        assert replacement and replacement != tag, (tag, replacement)
        assert replacement.split("-")[0] != tag, (tag, replacement)
    assert ENGINE_RENAMED_SUBTAGS["tw"] == "ak"


def test_the_vendored_list_matches_its_recorded_checksum():
    """The list is what the generator produced. The checksum is recorded beside
    it so a hand edit — the one way this file can drift unnoticed — fails
    here."""
    import hashlib

    from src.services.proxy.language_names import (
        LANGUAGE_SUBTAGS_SHA256,
        _SUBTAGS_TEXT,
    )

    assert hashlib.sha256(_SUBTAGS_TEXT.encode()).hexdigest() == (
        LANGUAGE_SUBTAGS_SHA256
    )


def test_the_validator_is_a_set_test_not_a_shape_test(tmp_path):
    """THE DISCRIMINATOR that makes AC4 worth having.

    ``[a-z]{2}`` accepts ``xx``, ``qq`` and ``zz``, none of which is a language
    — and each would then be COMPOSED into a locale and handed to a browser
    engine as fact. A set test needs no rule for any of them.
    """
    s, name = _residue_proxy(tmp_path)
    for plausible in ("xx", "qq", "zz", "aq"):
        ok, err = s.set_manual_locale(name, plausible)
        assert ok is False, plausible
        assert plausible in err
    assert _store(tmp_path).get(name).manual_locale_language == ""
    # And real subtags of every shape the table uses are accepted.
    for real in ("en", "ha", "zh", "nb", "sw", "am"):
        assert is_declarable_language(real), real


def test_the_validator_rejects_everything_that_is_not_a_subtag():
    """The probes AC4 asks for, plus the traversal shapes the zone validator's
    own test covers — none of which needs a rule of its own, because they are
    simply absent from the set."""
    for bad in ("", "   ", "eng", "e", "EN-GB", "en-US", "../../etc/passwd",
                "en\x00", "121", "en ha"):
        assert not is_declarable_language(bad), bad
    # Case and surrounding whitespace ARE normalised: BCP 47 subtags are
    # case-insensitive, and nothing else about the input is forgiven.
    assert is_declarable_language("EN") and is_declarable_language("  ha  ")


def test_every_language_the_product_itself_uses_is_declarable():
    """The set must cover the product's OWN table, or an operator could not
    declare a value ``_COUNTRY_LOCALE`` already ships — 68 distinct language
    subtags across 241 rows."""
    used = {locale.split("-")[0] for locale in _COUNTRY_LOCALE.values()}
    assert used <= DECLARABLE_LANGUAGE_SUBTAGS, used - DECLARABLE_LANGUAGE_SUBTAGS


# ---------------------------------------------------------------------------
# AC6 — all four store refusals, inherited from set_manual_timezone, each with
# its own test.
# ---------------------------------------------------------------------------


def test_refusal_1_an_empty_language_clears_BOTH_fields(tmp_path):
    """How an operator takes a declaration back. The country goes with it, so
    no half-record survives — a language with no country, or a country with no
    language, is a state nothing downstream knows how to read."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    assert s.set_manual_locale(name, "") == (True, "")
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_language == ""
    assert proxy.manual_locale_country == ""
    with pytest.raises(LocaleUnderivableError):
        _profile_locale(_Profile(name), proxy)


def test_refusal_2_a_re_declare_does_not_RE_STAMP_the_country(tmp_path):
    """THE LOAD-BEARING ONE. The country gate is a READ-side guard that retires
    a declaration when the exit moves; re-stamping the country on every call
    would let any caller re-arm one the gate had already retired.

    It is reachable without anyone typing anything — the dialog prefills its
    field, so a bare ``[ save ]`` re-submits it.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)

    ok, err = s.set_manual_locale(name, RESIDUE_LANG)
    assert ok is False
    assert RESIDUE_CC in err and MOVED_CC in err, err
    assert "clear the field" in err, "the operator is told how to re-declare"
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_country == RESIDUE_CC, "NOT re-stamped"
    assert declared_locale(proxy) == "", "still retired"

    # A re-declaration of a LIVE declaration is an accepted no-op, not an
    # error: nothing is written and nothing is re-stamped.
    s2, name2 = _residue_proxy(tmp_path / "live")
    s2.set_manual_locale(name2, RESIDUE_LANG)
    assert s2.set_manual_locale(name2, RESIDUE_LANG) == (True, "")


def test_refusal_3_a_declaration_needs_a_COUNTRY_ON_FILE(tmp_path):
    """Storing it with an empty country was the fail-closed direction and it was
    also SILENT AND PERMANENT: a success, a closed dialog, and a declaration
    that never activates — not even after a later check finds the country,
    because ``mark_checked`` writes only the six measured fields and nothing
    re-binds it. It gets a sentence instead of a shrug.

    Sharper here than on the zone half: the country IS the region of the
    composed locale, so without one there is nothing to compose.
    """
    s = _store(tmp_path)
    s.add("unchecked", "socks5://1.2.3.4:1080")
    ok, err = s.set_manual_locale("unchecked", RESIDUE_LANG)
    assert ok is False
    assert "[ check ]" in err
    assert _store(tmp_path).get("unchecked").manual_locale_language == ""
    # And a later check does NOT retroactively bind it — the point of refusing.
    s.mark_checked("unchecked", RESIDUE_CC, RESIDUE_COUNTRY, "1.1.1.1", "", None, None)
    assert declared_locale(_store(tmp_path).get("unchecked")) == ""


def test_refusal_4_a_DISPROVEN_exit_is_not_a_country_on_file_either(tmp_path):
    """A failed check leaves ``country_code`` populated from the last successful
    one while setting ``last_check_ok = False``, so the country term passes and
    the declaration would be stored against a country nobody can currently
    confirm the proxy exits from. Inert rather than dangerous — which is
    exactly the problem: a success for a value that changes nothing."""
    s, name = _residue_proxy(tmp_path)
    s.mark_check_failed(name)
    ok, err = s.set_manual_locale(name, RESIDUE_LANG)
    assert ok is False
    assert "FAILED" in err and RESIDUE_CC in err
    assert _store(tmp_path).get(name).manual_locale_language == ""


# ---------------------------------------------------------------------------
# The store's OTHER seams — the ones update()/set_url() carry for the zone
# half and must carry identically here, or a declaration outlives its exit.
# ---------------------------------------------------------------------------


def test_update_preserves_the_declaration_on_a_rename(tmp_path):
    """A rename leaves the exit exactly where it was, so the declaration still
    describes it. ``update``'s constructor is hand-enumerated, so a field
    omitted there is silently dropped on EVERY rename."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    assert s.update(name, "renamed", "socks5://u:pw@1.2.3.4:1080", "")
    proxy = _store(tmp_path).get("renamed")
    assert proxy.manual_locale_language == RESIDUE_LANG
    assert proxy.manual_locale_country == RESIDUE_CC


def test_update_invalidates_the_declaration_when_the_url_changes(tmp_path):
    """A URL change moves the exit, so nothing recorded about the old one
    describes the new one. It rides ``keep_geo`` with the six geo fields."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    assert s.update(name, name, "socks5://u:pw@9.9.9.9:1080", "")
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_language == ""
    assert proxy.manual_locale_country == ""


def test_set_url_invalidates_the_declaration_exactly_like_the_geo_fields(tmp_path):
    """Session-token rotation. The country gate would usually retire it once a
    check ran, but a rotation followed by a crash leaves nothing to run the
    gate — so it is cleared at the WRITE, like everything else there."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    assert s.set_url(name, "socks5://u:pw@9.9.9.9:1080")
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_language == ""
    assert proxy.manual_locale_country == ""


def test_set_url_with_an_unchanged_url_keeps_the_declaration(tmp_path):
    """The other half of the gate: a URL write that changes nothing must change
    nothing, which is what makes ``set_url`` safe to call unconditionally."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    assert s.set_url(name, "socks5://u:pw@1.2.3.4:1080")
    assert _store(tmp_path).get(name).manual_locale_language == RESIDUE_LANG


def test_mark_checked_does_not_touch_the_declaration_fields(tmp_path):
    """The whole reason the declaration is a SEPARATE field pair: a check that
    reports a country and no usable zone must not destroy what the operator
    declared. Asserted on the SOURCE too, so a future edit that starts writing
    these fields from a measurement fails here rather than silently."""
    import inspect

    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, RESIDUE_CC, RESIDUE_COUNTRY, "7.7.7.7", "", None, None)
    assert _store(tmp_path).get(name).manual_locale_language == RESIDUE_LANG
    assert "manual_locale" not in inspect.getsource(ProxyStore.mark_checked)


def test_restore_proxy_brings_the_declaration_back_from_the_trash(tmp_path):
    """``restore_proxy`` builds by reflection over ``Proxy``'s own fields, so a
    field added to the dataclass comes back for free. Asserted rather than
    assumed, because the enumerated constructors in this same file do NOT."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.delete(name)
    entry = s._trash().find("proxy", name)
    assert entry is not None
    ok, err = s.restore_proxy(entry)
    assert (ok, err) == (True, "")
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_language == RESIDUE_LANG
    assert proxy.manual_locale_country == RESIDUE_CC


# ---------------------------------------------------------------------------
# AC7 — the render. The row must not report a still-refused launch as
# launchable, and its sentence must name a gesture that now works.
# ---------------------------------------------------------------------------


def test_the_remedy_predicate_consults_the_declaration_and_not_only_the_table(
    tmp_path,
):
    """The drift PS-274's round 6 closed on the zone gate, closed here on the
    locale gate — asserted on the SOURCE as well as on outcomes, because a
    re-implementation that hard-codes today's table contents would pass every
    behavioural assertion and drift the day a table moves."""
    import inspect

    owner = inspect.getsource(proxy_unlaunchable_remedy)
    assert "declared_locale(" in owner, (
        "the launcher consults the declaration in the LocaleUnderivableError "
        "arm; a render that stops at the table reports a fixable proxy as "
        "unfixable, and an unfixable one as fixed"
    )
    assert "proxy_unlaunchable_remedy(" in inspect.getsource(
        proxy_is_checked_but_unlaunchable
    ), "the boolean must project the owner, not re-decide beside it"


def test_the_row_stops_marking_the_proxy_only_when_the_launch_ACTUALLY_works(
    tmp_path,
):
    """The state machine the render must model, on ONE record, in order —
    because the interesting failures are TRANSITIONS.

    Each step is paired with the real launch outcome, so the render can never
    be right about the badge and wrong about the product.
    """
    s, name = _residue_proxy(tmp_path)

    def _state():
        proxy = _store(tmp_path).get(name)
        try:
            _profile_locale(_Profile(name), proxy)
            _profile_timezone(_Profile(name), proxy)
            launches = True
        except Exception:
            launches = False
        return proxy_unlaunchable_remedy(proxy), launches

    # 1. Nothing declared: both gates refuse, and no declaration is on file.
    assert _state() == (UNLAUNCHABLE_UNSUPPORTED_COUNTRY, False)
    # 2. ZONE only — PS-274's shipped residue state. The launch is STILL
    #    refused, so the row must still say so.
    s.set_manual_timezone(name, RESIDUE_ZONE)
    assert _state() == (UNLAUNCHABLE_UNSUPPORTED_COUNTRY, False)
    # 3. LANGUAGE only: the locale gate answers, the ZONE gate still refuses —
    #    and that state has a gesture that works, so it gets the [ edit ] note.
    s.set_manual_timezone(name, "")
    s.set_manual_locale(name, RESIDUE_LANG)
    assert _state() == (UNLAUNCHABLE_DECLARABLE, False)
    # 4. BOTH: the launch works, so the row clears. A marker that never clears
    #    trains the operator to ignore it.
    s.set_manual_timezone(name, RESIDUE_ZONE)
    assert _state() == (None, True)
    # 5. The exit MOVES: both declarations retire, the launch refuses again,
    #    and the row comes back.
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)
    assert _state() == (UNLAUNCHABLE_UNSUPPORTED_COUNTRY, False)


def test_the_network_row_carries_the_sentence_and_clears_it_end_to_end(tmp_path):
    """AC7 on the RENDER, through the real ``build_network_page``, paired with a
    healthy proxy so it cannot pass by marking everything."""
    from src.ui.components.network_page import (
        UNLAUNCHABLE_NOTE,
        UNSUPPORTED_COUNTRY_NOTE,
        build_network_page,
    )

    s, name = _residue_proxy(tmp_path)
    fine = Proxy(name="pl-exit", url="socks5://9.9.9.9:1080",
                 country_code=CONTROL_CC, country_name=CONTROL_COUNTRY,
                 timezone=CONTROL_ZONE, checked_at=time.time(), last_check_ok=True)

    def _texts():
        return _all_text(build_network_page(
            [_store(tmp_path).get(name), fine],
            on_add=lambda _: None, on_edit=lambda n: None,
            on_delete=lambda n: None, on_check=lambda n: None,
            on_rotate=lambda n: None,
        ))

    marked = [t for t in _texts() if UNSUPPORTED_COUNTRY_NOTE in t]
    assert len(marked) == 1 and RESIDUE_COUNTRY in marked[0], _texts()
    assert not any(CONTROL_COUNTRY in t and UNSUPPORTED_COUNTRY_NOTE in t
                   for t in _texts()), "the healthy control must read healthy"

    # Declaring BOTH halves clears it — and this is the first time in the
    # product's life that a proxy in this country can reach that state.
    s.set_manual_timezone(name, RESIDUE_ZONE)
    s.set_manual_locale(name, RESIDUE_LANG)
    texts = _texts()
    assert not any(UNSUPPORTED_COUNTRY_NOTE in t or UNLAUNCHABLE_NOTE in t
                   for t in texts), texts


def test_the_unsupported_sentence_now_names_the_gesture_that_works(tmp_path):
    """The sentence's own contract. It used to name NO gesture — correctly,
    because none existed: declaring a zone was accepted, stored, and the
    profile still refused. PS-332 gave that population a real remedy, so a
    sentence still saying "not supported yet" would tell an operator to wait
    for a build for a state they can clear in two fields.

    ⚠️ IT NAMES THE EDITOR IN WORDS RATHER THAN AS ``[ edit ]``, and that is
    the one thing here a reviewer should rule on. ``test_ps274_...`` pins
    ``"[ edit ]" not in UNSUPPORTED_COUNTRY_NOTE`` with the reason *"naming a
    door that cannot fix this state is the loop again"* — a reason that has
    expired, on an assertion that is literal, in a suite this ticket's AC9
    requires green UNMODIFIED. Both sentences point at exactly one place.
    """
    from src.ui.components.network_page import (
        UNLAUNCHABLE_NOTE,
        UNSUPPORTED_COUNTRY_NOTE,
    )

    assert "declare" in UNSUPPORTED_COUNTRY_NOTE
    assert "timezone" in UNSUPPORTED_COUNTRY_NOTE
    assert "language" in UNSUPPORTED_COUNTRY_NOTE
    assert "proxy editor" in UNSUPPORTED_COUNTRY_NOTE, (
        "it must name WHERE, or it states a remedy the operator cannot find"
    )
    # Neither sentence is a re-check prompt: the check PASSED and will keep
    # passing, because what is missing is a declaration, not a measurement.
    for sentence in (UNLAUNCHABLE_NOTE, UNSUPPORTED_COUNTRY_NOTE):
        assert "re-check" not in sentence.lower()
    assert "not supported yet" not in UNSUPPORTED_COUNTRY_NOTE, (
        "the state IS supported now — telling the operator to wait for a build "
        "is the dead end this ticket removes"
    )


def test_the_predicate_is_pure_and_opens_no_socket(monkeypatch):
    """It is called once per row on every render, so it must never probe."""
    import socket

    def _boom(*a, **k):
        raise AssertionError("the render opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    proxy = Proxy(name="p", url="socks5://1.2.3.4:1080", country_code=RESIDUE_CC,
                  checked_at=time.time(), last_check_ok=True,
                  manual_locale_language=RESIDUE_LANG,
                  manual_locale_country=RESIDUE_CC)
    assert proxy_unlaunchable_remedy(proxy) == UNLAUNCHABLE_DECLARABLE
    assert declared_locale(proxy) == f"{RESIDUE_LANG}-{RESIDUE_CC}"


# ---------------------------------------------------------------------------
# THE DIALOG — the door itself, and the write-side seams PS-274 paid for once.
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self):
        self.shown = None
        self.popped = False

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        self.popped = True

    def update(self):
        pass


class _FakeCheckService:
    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        return (True, "Proxy working", RESIDUE_CC, RESIDUE_COUNTRY,
                "5.6.7.8", "", None, None)


def _all_text(control) -> list[str]:
    """Every rendered string in a flet control tree."""
    import flet as ft

    out: list[str] = []
    stack, visited = [control], set()
    while stack:
        node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, ft.Text) and isinstance(node.value, str):
            out.append(node.value)
        for attr in ("content", "controls", "actions", "title"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in (child if isinstance(child, list) else [child]):
                if c is not None and hasattr(c, "__dict__"):
                    stack.append(c)
    return out


def _dialog_field(dlg, label):
    """The field under a visible LABEL — the way the shipped dialog tests
    address controls, and the way a user finds it."""
    import flet as ft

    stack, seen = [dlg], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        controls = getattr(node, "controls", None)
        if controls and len(controls) >= 2:
            head = controls[0]
            value = getattr(head, "value", None)
            if not isinstance(value, str):
                for k in getattr(head, "controls", None) or []:
                    v = getattr(k, "value", None)
                    if isinstance(v, str):
                        value = v
                        break
            if value == label:
                for c in controls:
                    if isinstance(c, ft.TextField):
                        return c
        for attr in ("content", "controls", "actions", "title"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in (child if isinstance(child, list) else [child]):
                if c is not None and hasattr(c, "__dict__"):
                    stack.append(c)
    raise AssertionError(f"no field labeled {label!r}")


def _open(store, proxy_name=None, *, language=None, zone=None, name=None,
          submit=True, service=None):
    """Open the shipped proxy dialog on a stored proxy, optionally edit fields,
    and press [ save ] — the wiring ``app.py`` performs, with the real store.

    Returns ``(page, dlg)`` so a caller can assert whether the dialog CLOSED
    (accepted) or stayed open (refused).
    """
    from src.ui.dialogs.proxy import open_proxy_dialog

    proxy = store.get(proxy_name) if proxy_name else None
    page = _FakePage()

    def on_save(new_name, new_url, new_rotate):
        if proxy is None:
            return None if store.add(new_name, new_url, new_rotate) else "exists"
        return None if store.update(
            proxy.name, new_name, new_url, new_rotate
        ) else "exists"

    def on_declare_timezone(target, z):
        ok, err = store.set_manual_timezone(target, z)
        return None if ok else err

    def on_declare_locale(target, lang):
        ok, err = store.set_manual_locale(target, lang)
        return None if ok else err

    open_proxy_dialog(
        page, service or _FakeCheckService(), on_save=on_save, proxy=proxy,
        on_declare_timezone=on_declare_timezone,
        on_declare_locale=on_declare_locale,
    )
    dlg = page.shown
    if name is not None:
        _dialog_field(dlg, "Name").value = name
    if zone is not None:
        _dialog_field(dlg, TZ_LABEL).value = zone
    if language is not None:
        _dialog_field(dlg, LOCALE_LABEL).value = language
    if submit:
        dlg.actions[1].on_click(None)
    return page, dlg


def test_the_dialog_has_a_language_field_beside_the_timezone_one():
    """The door itself. Before this, the capability existed nowhere at all —
    no field, no store method, no gate."""
    page, dlg = _open(ProxyStore(path="/dev/null"), submit=False)
    assert _dialog_field(dlg, LOCALE_LABEL).value == ""
    assert _dialog_field(dlg, TZ_LABEL).value == ""


def test_saving_the_dialog_declares_the_language_through_the_store(tmp_path):
    """End to end through the dialog's own submit handler onto a real store,
    then read back from a FRESH store — and the LAUNCH driven from what came
    back, not from the dialog's own belief about it."""
    s, name = _residue_proxy(tmp_path)
    page, _ = _open(s, name, zone=RESIDUE_ZONE, language=RESIDUE_LANG)
    assert page.popped is True

    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_language == RESIDUE_LANG
    assert _profile_locale(_Profile(name), proxy) == f"{RESIDUE_LANG}-{RESIDUE_CC}"
    assert _profile_timezone(_Profile(name), proxy) == RESIDUE_ZONE


def test_the_dialog_refuses_a_bad_language_and_does_not_close(tmp_path):
    """A rejected value must not silently close the dialog: the operator would
    return to a network page still showing the proxy as stuck with no idea
    their input was thrown away."""
    s, name = _residue_proxy(tmp_path)
    page, dlg = _open(s, name, language="xx")
    assert page.popped is False
    assert [t for t in _all_text(dlg) if "xx" in t], (
        "the operator must be told which value was refused"
    )
    assert _store(tmp_path).get(name).manual_locale_language == ""


def test_the_dialog_refuses_every_renamed_language_the_STORE_refuses(tmp_path):
    """⭐ THE TWO GATES MUST AGREE, asserted as a SET relation over the whole
    exclusion table rather than on one example.

    The dialog validates BEFORE the store does, so a helper that answers where
    its caller raises — or, here, a caller that raises where the helper is
    silent — produces a sentence the store would never have written. Both arms
    read the SAME vendored ``ENGINE_RENAMED_SUBTAGS``, and this pins that they
    cannot drift apart: every renamed subtag is refused at the dialog, nothing
    reaches disk, and the dialog stays open so the operator's input is not
    thrown away.

    ⛔ AND THE SENTENCE MUST NOT BE FALSE. ``sh`` IS Serbo-Croatian, so
    "'sh' is not a language code" is a lie about the operator's own input. The
    dialog says what an engine does to it instead.
    """
    for renamed, answer in sorted(ENGINE_RENAMED_SUBTAGS.items()):
        s, name = _residue_proxy(tmp_path / f"dlg-{renamed}")
        page, dlg = _open(s, name, language=renamed)
        assert page.popped is False, renamed
        shown = _all_text(dlg)
        assert [t for t in shown if renamed in t], renamed
        assert [t for t in shown if answer in t], (renamed, answer)
        assert not [t for t in shown if "is not a language code" in t], (
            f"{renamed!r} IS a language; the dialog must not say otherwise"
        )
        assert _store(tmp_path / f"dlg-{renamed}").get(
            name
        ).manual_locale_language == "", renamed


def test_the_dialog_prefills_a_LIVE_declaration_so_it_can_be_read_back(tmp_path):
    """The operator must be able to read back what they declared. It prefills
    the LANGUAGE — not the composed locale — because that is the field's own
    vocabulary, and typing a region back in is the mistake the design removes."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    _, dlg = _open(s, name, submit=False)
    assert _dialog_field(dlg, LOCALE_LABEL).value == RESIDUE_LANG


def test_the_dialog_box_is_EMPTY_for_a_declaration_the_country_gate_RETIRED(
    tmp_path,
):
    """PS-274's hardest-won dialog rule, inherited.

    Prefilling from the RAW stored string put a RETIRED declaration in the box
    as though it were live — a lie in the one place the operator goes to fix
    the problem, and a loop with a success-shaped exit. The box reads from the
    country-gated value, so it is empty exactly when the declaration is not in
    force.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)
    assert _store(tmp_path).get(name).manual_locale_language == RESIDUE_LANG
    _, dlg = _open(s, name, submit=False)
    assert _dialog_field(dlg, LOCALE_LABEL).value == "", (
        "a retired declaration must not be shown as live"
    )


def test_a_bare_save_after_the_exit_moved_does_not_RE_ARM_the_declaration(
    tmp_path,
):
    """THE WRITE-SIDE SEAM. The dialog prefills and the operator presses
    [ save ] having touched nothing — which used to re-submit the value and
    re-stamp the country, re-arming a declaration the gate had deliberately
    retired. Two guards stop it (the dialog only writes a CHANGED field, and
    the store refuses a re-stamp to any caller); this drives the shipped one.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)

    page, _ = _open(s, name)          # a BARE save: nothing touched.
    assert page.popped is True, "an untouched field must not be able to fail a save"
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_country == RESIDUE_CC, "NOT re-stamped"
    assert declared_locale(proxy) == "", "still retired"
    with pytest.raises(LocaleUnderivableError):
        _profile_locale(_Profile(name), proxy)


def test_the_operator_can_re_declare_for_the_MOVED_exit_through_the_dialog(
    tmp_path,
):
    """The escape from the state above, driven as a real gesture: the box is
    empty (the gate retired the old value), the operator types the language for
    the CURRENT exit, and it takes effect.

    ⚠️ RE-TYPING THE SAME SUBTAG IS LEGITIMATE and must work — one language
    serves many countries (``en`` for a dozen exits), so a rule that refused it
    would lock the operator out of the ordinary case.
    """
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    s.mark_checked(name, MOVED_CC, MOVED_COUNTRY, "5.6.7.8", "", None, None)

    page, _ = _open(s, name, language=RESIDUE_LANG, zone=MOVED_ZONE)
    assert page.popped is True
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_locale_country == MOVED_CC, "re-declared for the new exit"
    assert _profile_locale(_Profile(name), proxy) == f"{RESIDUE_LANG}-{MOVED_CC}"
    assert _profile_timezone(_Profile(name), proxy) == MOVED_ZONE


def test_a_url_edit_through_the_dialog_leaves_no_half_record(tmp_path):
    """A URL change moves the exit, so ``update()`` invalidates the declaration
    — and the dialog must not re-write it afterwards, which would restore the
    half-record the store's docstring says cannot exist."""
    s, name = _residue_proxy(tmp_path)
    s.set_manual_locale(name, RESIDUE_LANG)
    from src.ui.dialogs.proxy import open_proxy_dialog

    proxy = s.get(name)
    page = _FakePage()
    open_proxy_dialog(
        page, _FakeCheckService(),
        on_save=lambda n, u, r: (
            None if s.update(proxy.name, n, u, r) else "exists"
        ),
        proxy=proxy,
        on_declare_timezone=lambda t, z: None,
        on_declare_locale=lambda t, lang: (
            None if s.set_manual_locale(t, lang)[0] else "refused"
        ),
    )
    _dialog_field(page.shown, "Host").value = "9.9.9.9"
    page.shown.actions[1].on_click(None)

    stored = _store(tmp_path).get(name)
    assert (stored.manual_locale_language, stored.manual_locale_country) == ("", "")


def test_declaring_a_language_on_an_unchecked_proxy_is_refused_at_the_dialog(
    tmp_path,
):
    """Caught BEFORE ``on_save``, so an add does not create the proxy and then
    report an error about a different field — and the operator is told what
    the rest of the gesture cost them."""
    s = _store(tmp_path)
    s.add("fresh", "socks5://1.2.3.4:1080")
    page, dlg = _open(s, "fresh", language=RESIDUE_LANG, name="renamed")
    assert page.popped is False
    texts = _all_text(dlg)
    assert any("[ check ]" in t for t in texts), texts
    assert any("NOT been saved" in t for t in texts), (
        "the operator must be told the rename was dropped with the declaration"
    )
    assert _store(tmp_path).get("fresh") is not None, "the rename did not land"


def test_the_store_and_dialog_gates_agree_that_no_country_cannot_be_declared(
    tmp_path,
):
    """Two gates, one rule. The dialog's exists so an add does not half-save;
    the store's exists because it owns the field and a second caller (the REST
    API, an import) must not be able to skip it."""
    s = _store(tmp_path)
    s.add("fresh", "socks5://1.2.3.4:1080")
    ok, err = s.set_manual_locale("fresh", RESIDUE_LANG)
    assert ok is False and "[ check ]" in err
    page, _ = _open(s, "fresh", language=RESIDUE_LANG)
    assert page.popped is False


def test_clearing_the_language_box_lets_the_rest_of_the_gesture_through(tmp_path):
    """The escape the refusal sentence names must actually work, or the
    operator is locked out of renaming an unchecked proxy."""
    s = _store(tmp_path)
    s.add("fresh", "socks5://1.2.3.4:1080")
    page, _ = _open(s, "fresh", language="", name="renamed")
    assert page.popped is True
    assert _store(tmp_path).get("renamed") is not None


# ---------------------------------------------------------------------------
# INVARIANT #0 POSTURE — this WIDENS what launches, so the bounds are asserted
# rather than argued.
# ---------------------------------------------------------------------------


def test_no_currently_launching_profile_changes_at_all(tmp_path):
    """The declaration is consulted ONLY inside the ``LocaleUnderivableError``
    arm, so a country the table answers for is untouched — even when a
    contradicting declaration is somehow on file. Measured over the WHOLE
    table rather than a sample, because "measured before declared" is a
    precedence claim about every row.
    """
    for code, expected in _COUNTRY_LOCALE.items():
        proxy = Proxy(name="p", url="socks5://1.2.3.4:1080", country_code=code,
                      checked_at=time.time(), last_check_ok=True,
                      manual_locale_language="ha", manual_locale_country=code)
        assert _profile_locale(_Profile(), proxy) == expected, code


def test_the_direct_path_still_forces_en_us_and_reads_no_host_locale():
    """#218. The no-proxy path forces ``en-US`` precisely so the host locale
    never leaks, and NOTHING this ticket adds may introduce a host-locale
    fallback. Asserted on the SOURCE too: the prohibition is absolute, so it is
    pinned as a property of the code rather than of one input.
    """
    import inspect

    assert _profile_locale(_Profile(), None) == "en-US"
    for func in (_profile_locale, declared_locale):
        source = inspect.getsource(func)
        # Strip the docstring: both functions EXPLAIN the prohibition at
        # length, and asserting over prose would match the words rather than
        # the code. What is asserted is that no host-locale read exists in the
        # body — which is where one would actually do harm.
        body = source.split('"""', 2)[-1]
        for forbidden in ("_host_", "getdefaultlocale", "getlocale",
                          "os.environ", "LANG"):
            assert forbidden not in body, (
                f"{func.__name__} must never read a host locale: {forbidden}"
            )


def test_a_declaration_never_bypasses_the_no_country_and_disproven_refusals(
    tmp_path,
):
    """The two gates that sit AHEAD of the table lookup are untouched: a
    declaration must not make a proxy with no known country, or a disproven
    one, launch. Both refuse before any declaration is consulted."""
    from src.services.proxy.errors import (
        ExitCountryUnknownError,
        GeographyDisprovenError,
    )

    no_country = Proxy(name="p", url="socks5://1.2.3.4:1080", country_code="",
                       checked_at=time.time(), last_check_ok=True,
                       manual_locale_language=RESIDUE_LANG,
                       manual_locale_country=RESIDUE_CC)
    with pytest.raises(ExitCountryUnknownError):
        _profile_locale(_Profile(), no_country)

    zz = Proxy(name="p", url="socks5://1.2.3.4:1080", country_code="ZZ",
               checked_at=time.time(), last_check_ok=True,
               manual_locale_language=RESIDUE_LANG, manual_locale_country="ZZ")
    with pytest.raises(ExitCountryUnknownError):
        _profile_locale(_Profile(), zz)

    disproven = Proxy(name="p", url="socks5://1.2.3.4:1080",
                      country_code=RESIDUE_CC, timezone=RESIDUE_ZONE,
                      checked_at=time.time(), last_check_ok=False,
                      manual_locale_language=RESIDUE_LANG,
                      manual_locale_country=RESIDUE_CC)
    with pytest.raises(GeographyDisprovenError):
        _profile_timezone(_Profile(), disproven)


def test_the_refusal_message_now_names_the_declaration_as_the_remedy(tmp_path):
    """A refusal an operator cannot diagnose is a worse product than a wrong
    locale. The message named only a code change — a remedy a desktop-app user
    cannot perform — and now names the declaration first."""
    s, name = _residue_proxy(tmp_path)
    proxy = _store(tmp_path).get(name)
    with pytest.raises(LocaleUnderivableError) as excinfo:
        _profile_locale(_Profile(name), proxy)
    message = str(excinfo.value)
    assert RESIDUE_CC in message
    assert "declare" in message.lower()
    assert "Re-checking will NOT help" in message, (
        "the check passed and will keep passing; sending them to re-check loops"
    )
