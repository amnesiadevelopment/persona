"""PS-274 — an operator can DECLARE a proxy exit's timezone, and the network page
stops calling a checked-but-unlaunchable proxy healthy.

THE STATE THIS CLOSES
---------------------
A proxy whose exit is in a country with no ``_COUNTRY_TZ`` row (RO/CZ/GR/HU/…:
30 rows against ~195 ISO countries) and whose geo provider returned no usable
zone stores ``country_code='RO' timezone='' last_check_ok=True``. The network
page draws a Romanian flag and "checked just now"; every profile using it
REFUSES to launch with ``TimezoneUnderivableError``. The only remedy the UI
offered was a re-check, which writes the country, reaches the same branch and
refuses again — it LOOPS. The documented remedy was editing a Python dict
inside an installed desktop application.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
Every launch assertion below goes through the REAL ``_proxy_timezone`` (and, in
one case, the real chromium argv), never through "a helper was called". The
declaration is asserted through a FRESH ``ProxyStore`` reading the file back,
never through the in-memory object — a field that lives only in RAM is not a
fix for a desktop app the operator restarts.

The route is (d) from the ticket: a SEPARATE operator field consulted AFTER
measured evidence and gated on the country it was declared for. So
``mark_checked`` is byte-identical and its three shipped assertions stay green
unmodified — that is asserted here too, as a property of the source rather than
as a promise.
"""

from __future__ import annotations

import json
import os
import time

import pytest

import src.services.browser.launch_policy as launch_policy
from src.models.proxy import Proxy
from src.services.browser.launch_policy import (
    _proxy_timezone,
    declared_timezone,
    proxy_is_checked_but_unlaunchable,
)
from src.services.proxy.errors import TimezoneUnderivableError
from src.services.proxy.store import ProxyStore
from src.services.proxy.tz_names import (
    ALL_ZONE_NAMES,
    DECLARABLE_ZONE_NAMES,
    is_declarable_zone,
)

#: The exit country from the ticket's transcript: a real, ordinary country with
#: no `_COUNTRY_TZ` row.
RO = "RO"
RO_ZONE = "Europe/Bucharest"

try:
    import cryptography  # noqa: F401

    _HAS_CRYPTO = True
except ImportError:                                    # pragma: no cover
    _HAS_CRYPTO = False

#: ONE test here spawns through the product's own ``spawn_browser``, and
#: ``src.services.browser.process`` imports ``src.services.cert.terminator``,
#: which imports ``cryptography`` at module scope. A container without that
#: wheel therefore gets a collection-time ``ModuleNotFoundError`` from a test
#: about TIMEZONES — reported as a NEW failure in an AC12 failure-set diff,
#: which is how it was found. Skipped LOUDLY and NARROWLY: the marker names the
#: missing dependency, and it guards only the argv assertion. AC2's other half
#: (``_proxy_timezone``'s own return value) has no such import and runs
#: everywhere, so the acceptance criterion is never skipped WHOLE.
_needs_crypto = pytest.mark.skipif(
    not _HAS_CRYPTO,
    reason="spawn_browser imports services.cert.terminator, which imports "
           "cryptography at module scope",
)


def _store(tmp_path, name="proxies.json") -> ProxyStore:
    return ProxyStore(path=str(tmp_path / name))


def _ro_proxy(tmp_path) -> tuple[ProxyStore, str]:
    """A proxy in exactly the shipped deadlock: checked, passing, RO, no zone."""
    s = _store(tmp_path)
    s.add("ro-exit", "socks5://u:pw@1.2.3.4:1080")
    # The five real provider body shapes the ticket measured all reduce to
    # this: a country survives `_validate_geo`, the zone does not.
    s.mark_checked("ro-exit", RO, "Romania", "5.6.7.8", "", None, None)
    return s, "ro-exit"


# ---------------------------------------------------------------------------
# AC3 — PREMISE INVERSION. Without the fix, this is the whole defect.
# ---------------------------------------------------------------------------


def test_the_deadlock_exists_a_passing_check_that_cannot_launch(tmp_path):
    """The premise, asserted rather than asserted-about.

    This is the state the ticket describes, built through the product's own
    writers: the check PASSED (`last_check_ok is True`, a fresh `checked_at`,
    a country on file) and the launch path still refuses. Nothing here is
    about the fix; if this test ever goes green-by-passing-the-launch, the
    premise has changed and everything below is measuring something else.
    """
    s, name = _ro_proxy(tmp_path)
    proxy = s.get(name)
    assert proxy.last_check_ok is True
    assert proxy.country_code == RO
    assert proxy.timezone == ""
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(proxy)


def test_the_re_check_remedy_loops_which_is_why_a_declaration_is_needed(tmp_path):
    """The reason a new input exists at all: the ONE action the UI offered does
    not converge. Re-checking writes the same country and the same empty zone,
    so the refusal is identical however many times it is pressed."""
    s, name = _ro_proxy(tmp_path)
    for _ in range(3):
        s.mark_checked(name, RO, "Romania", "5.6.7.8", "", None, None)
        with pytest.raises(TimezoneUnderivableError):
            _proxy_timezone(s.get(name))


# ---------------------------------------------------------------------------
# AC1 — the declaration persists across an application RESTART.
# ---------------------------------------------------------------------------


def test_a_declared_zone_survives_a_restart_read_back_from_disk(tmp_path):
    """Asserted through a FRESH store reading the file, not the live object.

    An in-memory-only field would pass every other test in this file and fail
    the operator on the next launch of the app.
    """
    s, name = _ro_proxy(tmp_path)
    ok, err = s.set_manual_timezone(name, RO_ZONE)
    assert (ok, err) == (True, "")

    reopened = _store(tmp_path)
    proxy = reopened.get(name)
    assert proxy.manual_timezone == RO_ZONE
    assert proxy.manual_timezone_country == RO


def test_the_declaration_is_actually_written_to_the_json_file(tmp_path):
    """One level below the round trip: the keys are on disk under stable names,
    so an external reader (or a future migration) sees them."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    on_disk = json.loads((tmp_path / "proxies.json").read_text(encoding="utf-8"))[name]
    assert on_disk["manual_timezone"] == RO_ZONE
    assert on_disk["manual_timezone_country"] == RO


def test_an_old_proxies_json_without_the_keys_loads_unmigrated(tmp_path):
    """Forward-tolerance in the direction that matters for an upgrade: a file
    written by a build that predates this field must load, not be skipped as
    malformed (which would silently drop the proxy AND its SOCKS5 creds)."""
    path = tmp_path / "proxies.json"
    path.write_text(json.dumps({
        "legacy": {
            "name": "legacy", "url": "socks5://u:pw@1.2.3.4:1080",
            "country_code": "RO", "country_name": "Romania",
            "timezone": "", "checked_at": time.time(), "last_check_ok": True,
        }
    }), encoding="utf-8")
    proxy = ProxyStore(path=str(path)).get("legacy")
    assert proxy is not None
    assert proxy.manual_timezone == ""
    assert proxy.manual_timezone_country == ""


def test_clearing_the_declaration_clears_the_country_with_it(tmp_path):
    """An operator takes a declaration back by emptying the field. Leaving a
    dangling country behind would be a half-record that reads as a declaration
    for a zone that is gone."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.set_manual_timezone(name, "") == (True, "")
    proxy = _store(tmp_path).get(name)
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == ("", "")
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(proxy)


# ---------------------------------------------------------------------------
# AC2 — the profile LAUNCHES, declaring the zone. On the computed value.
# ---------------------------------------------------------------------------


def test_a_declared_zone_makes_the_launch_path_answer_it(tmp_path, monkeypatch):
    """AC2 on ``_proxy_timezone``'s own return value.

    The host zone is patched to a distinctive value on ``launch_policy`` —
    where ``_proxy_timezone`` resolves it in its OWN namespace — so an answer
    that came from the host rather than the declaration would be caught rather
    than silently accepted.
    """
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert _proxy_timezone(_store(tmp_path).get(name)) == RO_ZONE


@_needs_crypto
def test_a_declared_zone_reaches_the_real_chromium_argv(tmp_path, monkeypatch):
    """AC2 past the policy function: the value a REAL launch puts on the
    command line.

    ``_proxy_timezone`` returning the right string is necessary and not
    sufficient — the engine has to be told. This spawns through the product's
    own ``spawn_browser`` with Popen faked at the boundary and reads
    ``--timezone=`` off the argv, so nothing between the store and the process
    can drop it.
    """
    import src.services.browser.process as process
    from src.models.profile import Profile

    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    declared = _store(tmp_path).get(name)

    class _Store:
        def __init__(self, *a, **k):
            pass

        def resolve(self, ref):
            return "socks5://1.2.3.4:1080"

        def get(self, ref):
            return declared

    class _Bookmarks:
        def __init__(self, *a, **k):
            pass

        def resolve_selection(self, *a, **k):
            return []

    captured: dict = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path / "browsers"))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(process._platform, "IS_LINUX", True)
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")

    process.spawn_browser(Profile(name="ro-profile", proxy=name))
    tz_args = [a for a in captured["args"] if a.startswith("--timezone=")]
    assert tz_args == [f"--timezone={RO_ZONE}"], captured["args"]
    assert not any("America/Chicago" in a for a in captured["args"]), (
        "the host zone must never reach the engine"
    )


# ---------------------------------------------------------------------------
# AC4 — the re-check lifecycle, as the ticket's five-row table.
# ---------------------------------------------------------------------------


def test_the_full_recheck_lifecycle_table(tmp_path, monkeypatch):
    """All five rows of the route-(d) transcript, in order, on ONE record —
    because the interesting failures are TRANSITIONS, not states. A per-state
    test would let an implementation that never disarms pass four of five.
    """
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    s, name = _ro_proxy(tmp_path)
    assert s.set_manual_timezone(name, RO_ZONE) == (True, "")

    def launched() -> str:
        try:
            return _proxy_timezone(s.get(name))
        except TimezoneUnderivableError:
            return "REFUSED"

    rows = []

    # 1. RO exit, provider gave NO zone -> the declaration answers.
    rows.append(("ro-no-zone", launched()))

    # 2. RE-CHECK, same RO, still no zone -> it SURVIVES. This is the row that
    #    kills route (b): mark_checked writes `timezone` unconditionally, so a
    #    fix stored in that field would be destroyed here.
    s.mark_checked(name, RO, "Romania", "5.6.7.8", "", None, None)
    rows.append(("recheck-same-country", launched()))

    # 3. The provider LATER gives the real zone -> MEASURED wins. The stored
    #    value and the declaration agree here by construction, so the next row
    #    is what actually proves precedence.
    s.mark_checked(name, RO, "Romania", "5.6.7.8", RO_ZONE, None, None)
    rows.append(("measured-arrives", launched()))

    # 4. The exit MOVED to CZ with no zone -> the declaration DISARMS and the
    #    launch refuses again. Route (a) fails here: it would declare
    #    Europe/Bucharest for a CZ exit — a country/clock contradiction.
    s.mark_checked(name, "CZ", "Czechia", "9.9.9.9", "", None, None)
    rows.append(("moved-to-cz-no-zone", launched()))

    # 5. The exit MOVED to DE WITH a zone -> the measured one is used.
    s.mark_checked(name, "DE", "Germany", "9.9.9.9", "Europe/Berlin", None, None)
    rows.append(("moved-to-de-with-zone", launched()))

    assert rows == [
        ("ro-no-zone", RO_ZONE),
        ("recheck-same-country", RO_ZONE),
        ("measured-arrives", RO_ZONE),
        ("moved-to-cz-no-zone", "REFUSED"),
        ("moved-to-de-with-zone", "Europe/Berlin"),
    ]


def test_a_measured_zone_outranks_a_disagreeing_declaration(tmp_path):
    """Row 3 of the table with the two values made to DISAGREE, which is the
    only way to actually observe precedence.

    ⚠️ The order is load-bearing: a hand-typed value that outranked a fresh
    measurement would outlive every later measurement contradicting it.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    # Same country, so the declaration still APPLIES — and still loses.
    s.mark_checked(name, RO, "Romania", "5.6.7.8", "Europe/Chisinau", None, None)
    proxy = s.get(name)
    assert proxy.manual_timezone == RO_ZONE
    assert _proxy_timezone(proxy) == "Europe/Chisinau"


def test_the_country_gate_is_what_disarms_a_moved_exit(tmp_path):
    """The gate in isolation, on the pure predicate: same country -> applies,
    different country -> does not, no country on file -> does not."""
    base = dict(name="p", url="socks5://1.2.3.4:1080",
                manual_timezone=RO_ZONE, manual_timezone_country=RO)
    assert declared_timezone(Proxy(**base, country_code="RO")) == RO_ZONE
    assert declared_timezone(Proxy(**base, country_code="ro")) == RO_ZONE
    assert declared_timezone(Proxy(**base, country_code="CZ")) == ""
    assert declared_timezone(Proxy(**base, country_code="")) == ""


def test_a_declaration_cannot_manufacture_geography_that_was_never_measured(
    tmp_path,
):
    """An UNCHECKED proxy has no country on file, so the declaration is REFUSED
    and the no-geography refusal still fires. A declaration answers a question
    the check could not; it does not replace the check.

    ⚠️ REFUSED WITH A SENTENCE, not accepted-and-ignored. Storing the zone with
    an empty country was fail-closed and also SILENT AND PERMANENT: nothing
    ever re-binds it, because ``mark_checked`` writes the six measured fields
    and is untouched by this feature — so a later check finding RO would leave
    the declaration inert forever while the operator, who typed a valid zone
    and got a closed dialog, reads a network row telling them to go and do the
    thing they already did. The next test asserts exactly that sequence.
    """
    s = _store(tmp_path)
    s.add("fresh", "socks5://u:pw@1.2.3.4:1080")
    ok, err = s.set_manual_timezone("fresh", RO_ZONE)
    assert ok is False
    assert "check" in err.lower(), err
    assert _store(tmp_path).get("fresh").manual_timezone == ""
    from src.services.proxy.errors import GeographyUnknownError

    with pytest.raises(GeographyUnknownError):
        _proxy_timezone(s.get("fresh"))


def test_a_declaration_refused_before_a_check_does_not_become_a_dead_record(
    tmp_path,
):
    """The FULL sequence the silent-accept behaviour produced, asserted end to
    end: declare before checking, then check, then launch.

    Under the old behaviour every step "succeeded" and the launch still refused
    — the declaration was bound to an empty country that no later write ever
    filled in. Here the declaration is refused up front, the operator checks,
    declares again, and the profile launches. The point of the test is that
    there is no state in which a stored zone exists and can never activate.
    """
    s = _store(tmp_path)
    s.add("fresh", "socks5://u:pw@1.2.3.4:1080")
    assert s.set_manual_timezone("fresh", RO_ZONE)[0] is False
    s.mark_checked("fresh", RO, "Romania", "1.2.3.4", "", None, None)
    # Nothing was carried over from the refused attempt.
    p = _store(tmp_path).get("fresh")
    assert (p.manual_timezone, p.manual_timezone_country) == ("", "")
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(p)
    # And now that there IS a country, the same declaration is accepted and
    # the launch answers it.
    assert s.set_manual_timezone("fresh", RO_ZONE) == (True, "")
    assert _proxy_timezone(_store(tmp_path).get("fresh")) == RO_ZONE


def test_a_failed_check_still_refuses_even_with_a_declaration(tmp_path):
    """The disproven guard runs BEFORE every branch, and a declaration must not
    become a way around it: a proxy whose last check FAILED has geography the
    product's own latest evidence contradicts."""
    from src.services.proxy.errors import GeographyDisprovenError

    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    s.mark_check_failed(name)
    with pytest.raises(GeographyDisprovenError):
        _proxy_timezone(s.get(name))


# ---------------------------------------------------------------------------
# AC5 — mark_checked is byte-identical.
# ---------------------------------------------------------------------------


def test_mark_checked_does_not_touch_the_declaration_fields(tmp_path):
    """The property route (d) was chosen FOR, asserted as behaviour: the
    check-result writer writes the six geo fields and the two bookkeeping ones,
    and knows nothing about the declaration.

    This is what lets `tests/test_proxy_checker_geo_fallback.py`,
    `tests/test_proxy_checker_lanes.py` and `tests/test_proxy_store.py` pass
    UNMODIFIED — see the source-level assertion below.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    for cc, cn, tz in (
        (RO, "Romania", ""),
        (RO, "Romania", "Europe/Bucharest"),
        ("", "", ""),
    ):
        s.mark_checked(name, cc, cn, "5.6.7.8", tz, None, None)
        proxy = s.get(name)
        assert proxy.manual_timezone == RO_ZONE
        assert proxy.manual_timezone_country == RO


def test_mark_checked_source_never_mentions_the_declaration(tmp_path):
    """AC5 read straight off the source, not off behaviour.

    A behavioural test passes against an implementation that assigns the field
    a value equal to what it already held. The AC is that the method is
    UNCHANGED, and the cheapest honest check of that is that its body does not
    contain the name.
    """
    import inspect

    body = inspect.getsource(ProxyStore.mark_checked)
    assert "manual_timezone" not in body


# ---------------------------------------------------------------------------
# AC6 — the declaration survives (or is invalidated by) every store writer.
# ---------------------------------------------------------------------------


def test_update_preserves_the_declaration_on_a_rename(tmp_path):
    """`update()` builds a WHOLE NEW Proxy with a hand-enumerated constructor,
    so a field missing from that list is silently dropped on every rename —
    the operator's fix would evaporate the next time they edited the name."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    url = s.get(name).url
    assert s.update(name, "ro-renamed", url, "") is True

    proxy = _store(tmp_path).get("ro-renamed")
    assert proxy.manual_timezone == RO_ZONE
    assert proxy.manual_timezone_country == RO
    assert _proxy_timezone(proxy) == RO_ZONE


def test_update_invalidates_the_declaration_when_the_url_changes(tmp_path):
    """Rides `keep_geo` with the six measured fields: a new URL is a new exit,
    and a zone declared for the old one describes nothing."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.update(name, name, "socks5://u:pw@9.9.9.9:1080", "") is True

    proxy = _store(tmp_path).get(name)
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == ("", "")
    assert proxy.country_code == ""


def test_set_url_invalidates_the_declaration_exactly_like_the_geo_fields(
    tmp_path,
):
    """Rotation. The exit MOVED, so the declaration goes with the six geo
    fields — kept, it would assert the previous exit's clock under a new URL,
    and a crash between the rotation and the follow-up check leaves that stale
    affirmative on DISK where nothing re-examines it."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.set_url(name, "socks5://u:pw@9.9.9.9:1080") is True

    proxy = _store(tmp_path).get(name)
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == ("", "")
    assert proxy.timezone == ""
    assert proxy.country_code == ""


def test_set_url_with_an_unchanged_url_keeps_the_declaration(tmp_path):
    """`set_url` is called unconditionally after a rotation attempt, so a URL
    that did not actually move must keep everything — otherwise a no-op
    rotation silently un-fixes the proxy."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.set_url(name, s.get(name).url) is True
    assert _store(tmp_path).get(name).manual_timezone == RO_ZONE


def test_restore_proxy_brings_the_declaration_back_from_the_trash(tmp_path):
    """The third hand-enumerated constructor, and the one the ticket names: a
    restored proxy that came back checked, flagged and still unlaunchable —
    with the operator's fix gone — is the specific failure this catches."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.delete(name) is True

    entry = s._trash().find("proxy", name)
    assert entry is not None
    ok, err = s.restore_proxy(entry)
    assert (ok, err) == (True, "")

    proxy = _store(tmp_path).get(name)
    assert proxy.manual_timezone == RO_ZONE
    assert proxy.manual_timezone_country == RO
    assert _proxy_timezone(proxy) == RO_ZONE


def test_the_field_by_field_proxy_constructors_carry_the_new_fields(
    tmp_path,
):
    """A structural backstop for the tests above, and for the NEXT field
    somebody adds. `_load` and `update` each spell out every Proxy field by
    hand; this asserts neither has fallen behind the dataclass, so a writer
    added later fails here rather than silently dropping data in production.

    `restore_proxy` is asserted SEPARATELY below, because PS-275 (#211)
    deliberately replaced its hand-enumerated constructor with reflection —
    a field-by-field check there would now demand the regression.
    """
    import dataclasses
    import inspect

    fields = {f.name for f in dataclasses.fields(Proxy)} - {"name"}
    for method in (ProxyStore._load, ProxyStore.update):
        src = inspect.getsource(method)
        missing = sorted(f for f in fields if f"{f}=" not in src)
        assert not missing, f"{method.__qualname__} drops {missing}"


def test_restore_proxy_still_builds_by_reflection_not_by_a_key_list():
    """The same guarantee for the third writer, expressed as the mechanism that
    provides it.

    AC6 exists because a hand-enumerated constructor can silently drop one
    field. `restore_proxy` now avoids that by construction rather than by
    diligence — `restore_kwargs` derives the keys from `dataclasses.fields`, so
    `manual_timezone` / `manual_timezone_country` ride for free and so does the
    next field. Unwinding it back to a written-out key list is the regression
    this catches; the behavioural half is
    `test_restore_proxy_brings_the_declaration_back_from_the_trash`.
    """
    import inspect

    src = inspect.getsource(ProxyStore.restore_proxy)
    assert "restore_kwargs(Proxy" in src, (
        "restore_proxy no longer builds by reflection — a hand-written key "
        "list drops any field somebody forgets to add to it"
    )


# ---------------------------------------------------------------------------
# AC7 — validation, against a platform-invariant source.
# ---------------------------------------------------------------------------


def test_the_zone_name_set_reads_no_os_timezone_database(tmp_path):
    """The property AC7 actually asks for, asserted at its source.

    An unguarded `zoneinfo.available_timezones()` validator was REJECTED and
    the measurement is why, not preference:

        zoneinfo, TZPATH=[]  (a bare Windows: no OS db, no tzdata)  ->   0 zones
        zoneinfo, TZPATH=[]  + the `tzdata` wheel present           -> 598 zones
        a Linux container with the OS tz database                   -> 486 zones

    It rejects EVERY input on one of the three platforms we ship, and accepts a
    DIFFERENT set on each of the others. So the names are vendored in-tree and
    the module must not import zoneinfo or tzdata at all.
    """
    import inspect

    import src.services.proxy.tz_names as tz_names

    src = inspect.getsource(tz_names)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    # Inside the module docstring the words appear as prose; strip it before
    # asserting, so the assertion is about CODE rather than about comments.
    body = code.split('"""', 2)[-1]
    assert "available_timezones" not in body
    assert "import zoneinfo" not in body
    assert "import tzdata" not in body


def test_the_accepted_set_is_a_fixed_size_shipped_with_the_product():
    """Byte-identical on Windows, macOS and Linux BECAUSE it is source. The
    two numbers are pinned so a regeneration that silently truncated the list
    (a partial download, a wrong resource) is caught rather than shipped."""
    assert len(ALL_ZONE_NAMES) == 598
    assert len(DECLARABLE_ZONE_NAMES) == 553
    assert DECLARABLE_ZONE_NAMES <= ALL_ZONE_NAMES


def test_the_validator_accepts_real_zones_and_rejects_everything_else():
    """AC7's stated probes, plus the countries from the ticket's own residue —
    a validator that accepted `Europe/Bucharest` and nothing else for RO's
    neighbours would satisfy the letter of the AC and none of its point."""
    for good in (RO_ZONE, "Europe/Prague", "Europe/Athens", "Europe/Budapest",
                 "Asia/Bangkok", "Africa/Johannesburg", "Asia/Ho_Chi_Minh",
                 "America/New_York"):
        assert is_declarable_zone(good), good
    for bad in ("Not/AZone", "EET", "UTC", "Poland", "", "   ",
                "../../etc/passwd", "Europe/../../../etc/passwd",
                "/etc/localtime", "Europe/Bucharest\x00"):
        assert not is_declarable_zone(bad), bad


def test_the_validator_is_a_set_test_not_a_shape_test(tmp_path):
    """The discriminator that makes AC7 worth having.

    `_validate_geo`'s `/` test is a SHAPE check, and `_proxy_timezone` returns
    'Not/AZone' happily — so a shape check here would let a plausible-looking
    non-zone straight through to a browser engine as fact.
    """
    s, name = _ro_proxy(tmp_path)
    ok, err = s.set_manual_timezone(name, "Not/AZone")
    assert ok is False
    assert "Not/AZone" in err
    # And nothing was written: a rejected value must not half-land.
    proxy = _store(tmp_path).get(name)
    assert proxy.manual_timezone == ""
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(proxy)


def test_an_abbreviation_is_rejected_matching_the_shipped_measured_rule():
    """'EET' IS a real tzdata name, and it is still refused — because
    `proxy_checker._validate_geo` already drops a measured zone that carries no
    '/', precisely so an abbreviation never reaches the launch path. A declared
    zone is consumed by the same code, so it is held to the same rule."""
    assert "EET" in ALL_ZONE_NAMES
    assert "EET" not in DECLARABLE_ZONE_NAMES
    assert not is_declarable_zone("EET")


def test_the_vendored_list_matches_its_recorded_checksum():
    """The list is a verbatim copy of the tzdata wheel's own `zones` resource.
    The checksum is recorded beside it so a hand edit — the one way this file
    can drift from upstream unnoticed — fails here."""
    import hashlib

    from src.services.proxy.tz_names import TZDATA_ZONES_SHA256, _ZONES_TEXT

    assert hashlib.sha256(_ZONES_TEXT.encode()).hexdigest() == TZDATA_ZONES_SHA256


# ---------------------------------------------------------------------------
# AC8 — the network page distinguishes checked-but-unlaunchable from working.
# ---------------------------------------------------------------------------


def test_the_predicate_is_derived_from_the_launch_path_not_reimplemented(
    tmp_path,
):
    """The rule has ONE owner. `proxy_is_checked_but_unlaunchable` calls
    `_proxy_timezone` and reports whether it refused — so this ticket's own new
    answer (the declaration) is reflected in the render with no second edit,
    and so a future refusal branch cannot leave the two surfaces disagreeing.
    """
    import inspect

    assert "_proxy_timezone(" in inspect.getsource(
        proxy_is_checked_but_unlaunchable
    )


def test_the_predicate_answers_true_only_for_the_state_it_names(tmp_path):
    now = time.time()
    base = dict(name="p", url="socks5://1.2.3.4:1080")
    # The state: check passed, country on file, no derivable zone.
    assert proxy_is_checked_but_unlaunchable(
        Proxy(**base, country_code=RO, checked_at=now, last_check_ok=True)
    ) is True
    # A country WITH a table row launches: healthy, and must read healthy.
    assert proxy_is_checked_but_unlaunchable(
        Proxy(**base, country_code="DE", checked_at=now, last_check_ok=True)
    ) is False
    # Once declared, it launches — so the warning must clear.
    assert proxy_is_checked_but_unlaunchable(
        Proxy(**base, country_code=RO, checked_at=now, last_check_ok=True,
              manual_timezone=RO_ZONE, manual_timezone_country=RO)
    ) is False
    # A FAILED check and a NEVER-checked proxy already render honestly (a ✕ and
    # a placeholder), so they stay out of this predicate rather than being
    # relabelled.
    assert proxy_is_checked_but_unlaunchable(
        Proxy(**base, country_code=RO, checked_at=now, last_check_ok=False)
    ) is False
    assert proxy_is_checked_but_unlaunchable(Proxy(**base)) is False


def test_the_predicate_is_pure_and_opens_no_socket(monkeypatch):
    """It runs on every render of every row, so it must not probe. Sockets are
    poisoned for the duration: any attempt raises rather than merely being
    slow, so a probe cannot hide behind a passing assertion."""
    import socket

    def _boom(*a, **k):
        raise AssertionError("the render predicate opened a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    now = time.time()
    assert proxy_is_checked_but_unlaunchable(
        Proxy(name="p", url="socks5://1.2.3.4:1080", country_code=RO,
              checked_at=now, last_check_ok=True)
    ) is True


def test_the_network_row_says_so_instead_of_reading_checked_just_now(tmp_path):
    """AC8 on the RENDER. The two surfaces used to disagree — a Romanian flag
    and "checked just now" beside a profile card reading "refused" — and the
    object the operator can actually act on was the one drawn as healthy.

    Asserted on the row's own text, and paired with a healthy proxy built the
    same way so this cannot pass by marking everything.
    """
    from src.ui.components.network_page import (
        UNLAUNCHABLE_NOTE,
        build_network_page,
    )

    now = time.time()
    stuck = Proxy(name="ro-exit", url="socks5://1.2.3.4:1080", country_code=RO,
                  country_name="Romania", checked_at=now, last_check_ok=True)
    fine = Proxy(name="de-exit", url="socks5://1.2.3.4:1080", country_code="DE",
                 country_name="Germany", checked_at=now, last_check_ok=True)
    page = build_network_page(
        [stuck, fine],
        on_add=lambda _: None, on_edit=lambda n: None, on_delete=lambda n: None,
        on_check=lambda n: None, on_rotate=lambda n: None,
    )
    texts = _all_text(page)
    stuck_lines = [t for t in texts if UNLAUNCHABLE_NOTE in t]
    assert len(stuck_lines) == 1, texts
    assert "Romania" in stuck_lines[0]
    assert not any("Germany" in t and UNLAUNCHABLE_NOTE in t for t in texts)


def test_the_network_row_clears_the_warning_once_a_zone_is_declared(tmp_path):
    """The other half of the pair: the indication is a function of the CURRENT
    state, so declaring a zone removes it. A marker that never clears trains
    the operator to ignore it."""
    from src.ui.components.network_page import (
        UNLAUNCHABLE_NOTE,
        build_network_page,
    )

    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    page = build_network_page(
        [_store(tmp_path).get(name)],
        on_add=lambda _: None, on_edit=lambda n: None, on_delete=lambda n: None,
        on_check=lambda n: None, on_rotate=lambda n: None,
    )
    assert not any(UNLAUNCHABLE_NOTE in t for t in _all_text(page))


def test_the_unlaunchable_note_is_not_a_re_check_prompt():
    """Same rule `refusal.py`'s `_UNDERIVABLE` label follows, and for the same
    reason: the check already PASSED and will keep passing, so sending the
    operator to re-check wastes their time on the one refusal where it loops.
    """
    from src.ui.components.network_page import UNLAUNCHABLE_NOTE

    assert "check" not in UNLAUNCHABLE_NOTE.replace("[ edit ]", "").lower() or (
        "re-check" not in UNLAUNCHABLE_NOTE.lower()
    )
    assert "timezone" in UNLAUNCHABLE_NOTE.lower()


def _all_text(control, seen=None) -> list[str]:
    """Every rendered string in a flet control tree."""
    import flet as ft

    out: list[str] = []
    stack = [control]
    visited = set()
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
            items = child if isinstance(child, list) else [child]
            for c in items:
                if c is not None and hasattr(c, "__dict__"):
                    stack.append(c)
    return out


# ---------------------------------------------------------------------------
# AC1 — the DIALOG half: the door exists, prefills, and writes through.
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
        return (True, "Proxy working", RO, "Romania", "5.6.7.8", "", None, None)


def _dialog_field(dlg, label):
    """The field under a visible LABEL — the same way the shipped dialog tests
    address controls, and the same way a user finds it."""
    import flet as ft

    stack = [dlg]
    seen = set()
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
            items = child if isinstance(child, list) else [child]
            for c in items:
                if c is not None and hasattr(c, "__dict__"):
                    stack.append(c)
    raise AssertionError(f"no field labeled {label!r}")


TZ_LABEL = "Exit timezone (optional)"


def test_the_proxy_dialog_has_a_timezone_field_beside_the_check_control():
    """The door itself. Before this, `grep -rn timezone src/ui/` returned two
    hits, both prose in a changelog — the capability existed in the data model
    and had no way in."""
    from src.ui.dialogs.proxy import open_proxy_dialog

    page = _FakePage()
    open_proxy_dialog(page, _FakeCheckService(), on_save=lambda *a: None)
    assert _dialog_field(page.shown, TZ_LABEL).value == ""


def test_the_dialog_prefills_an_existing_declaration_on_re_open(tmp_path):
    """AC1's "re-open to see it persisted" — the operator must be able to read
    back what they declared, not just write it once into the void."""
    from src.ui.dialogs.proxy import open_proxy_dialog

    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    page = _FakePage()
    open_proxy_dialog(
        page, _FakeCheckService(), on_save=lambda *a: None,
        proxy=_store(tmp_path).get(name),
    )
    assert _dialog_field(page.shown, TZ_LABEL).value == RO_ZONE


def test_saving_the_dialog_declares_the_zone_through_the_store(tmp_path):
    """End to end through the dialog's own submit handler onto a real store,
    then read back from a FRESH store — the wiring `app.py` performs, asserted
    without flet being driven."""
    from src.ui.dialogs.proxy import open_proxy_dialog

    s, name = _ro_proxy(tmp_path)
    page = _FakePage()

    def on_save(new_name, new_url, new_rotate):
        assert s.update(name, new_name, new_url, new_rotate)
        return None

    def on_declare(proxy_name, zone):
        ok, err = s.set_manual_timezone(proxy_name, zone)
        return None if ok else err

    open_proxy_dialog(
        page, _FakeCheckService(), on_save=on_save, proxy=s.get(name),
        on_declare_timezone=on_declare,
    )
    dlg = page.shown
    _dialog_field(dlg, TZ_LABEL).value = RO_ZONE
    dlg.actions[1].on_click(None)
    assert page.popped is True

    proxy = _store(tmp_path).get(name)
    assert proxy.manual_timezone == RO_ZONE
    assert _proxy_timezone(proxy) == RO_ZONE


def test_the_dialog_refuses_a_bad_zone_and_does_not_close(tmp_path):
    """A rejected value must not silently close the dialog: the operator would
    return to a network page still showing the proxy as stuck with no idea
    their input was thrown away."""
    from src.ui.dialogs.proxy import open_proxy_dialog

    s, name = _ro_proxy(tmp_path)
    page = _FakePage()
    open_proxy_dialog(
        page, _FakeCheckService(), on_save=lambda *a: None, proxy=s.get(name),
        on_declare_timezone=lambda n, z: None,
    )
    dlg = page.shown
    _dialog_field(dlg, TZ_LABEL).value = "Not/AZone"
    dlg.actions[1].on_click(None)
    assert page.popped is False
    errors = [t for t in _all_text(dlg) if "Not/AZone" in t]
    assert errors, "the operator must be told which value was refused"


# ---------------------------------------------------------------------------
# THE WRITE-SIDE SEAM — the composition the first round shipped broken.
#
# The country gate is a READ-side guard. Every test above drives ONE layer:
# the gate as a pure predicate with no store and no dialog; the dialog once, on
# a proxy whose country never moved; the lifecycle table through the store with
# no dialog. All three were green while the feature was broken, because the
# defect lived in NONE of them — it lived in the ORDER: the dialog prefills its
# field from the RAW stored zone and re-submitted it unconditionally, so a save
# re-stamped `manual_timezone_country` to the CURRENT country and re-armed a
# declaration the gate had deliberately retired.
#
# These tests therefore all drive the dialog AFTER a state change, which is the
# axis the first round's fixtures never varied.
# ---------------------------------------------------------------------------


def _drive_dialog(
    store, proxy_name, *, zone=None, name=None, host=None, port=None,
    press_check=False, service=None, submit=True,
):
    """Open the shipped proxy dialog on a stored proxy, optionally edit fields,
    optionally press [ check ], and press [ save ] — the wiring `app.py`
    performs, with the real store.

    Returns the `_FakePage`, so a caller can assert whether the dialog CLOSED
    (accepted) or stayed open (refused).

    ``press_check`` runs the dialog's OWN [ check ] control before the save, in
    the gesture ORDER an operator uses: fill the address, check it, then
    declare. That ordering is the axis no test varied before — every dialog
    test in the suite drove [ save ] alone — and it is where the second seam
    lived: the declaration gate's own remedy could not clear the gate.
    ``submit=False`` walks away instead (the cancel path).
    """
    from src.ui.dialogs.proxy import open_proxy_dialog

    proxy = store.get(proxy_name)
    page = _FakePage()

    def on_save(new_name, new_url, new_rotate):
        if proxy is None:
            return None if store.add(new_name, new_url, new_rotate) else "exists"
        return None if store.update(proxy.name, new_name, new_url, new_rotate) else "exists"

    def on_checked(pname, code, country, ip, tz, lat=None, lon=None):
        store.mark_checked(pname, code, country, ip, tz, lat, lon)

    def on_check_failed(pname):
        store.mark_check_failed(pname)

    def on_declare(target, z):
        ok, err = store.set_manual_timezone(target, z)
        return None if ok else err

    open_proxy_dialog(
        page, service or _FakeCheckService(), on_save=on_save, proxy=proxy,
        on_checked=on_checked, on_check_failed=on_check_failed,
        on_declare_timezone=on_declare,
    )
    dlg = page.shown
    if name is not None:
        _dialog_field(dlg, "Name").value = name
    if host is not None:
        _dialog_field(dlg, "Host").value = host
    if port is not None:
        _dialog_field(dlg, "Port").value = port
    if press_check:
        _press_check(dlg)
    if zone is not None:
        _dialog_field(dlg, TZ_LABEL).value = zone
    if submit:
        dlg.actions[1].on_click(None)
    return page


def _press_check(dlg) -> None:
    """Click the dialog's [ check ] button and wait for the worker thread.

    The check runs on a daemon thread (`_do_check`), so the button's own
    `disabled` flag is the completion signal — the same one
    `tests/test_proxy_dialog.py` uses.
    """
    import flet as ft

    stack, seen, btn = [dlg], set(), None
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, ft.OutlinedButton) and node.content == "[ check ]":
            btn = node
            break
        for attr in ("content", "controls", "actions", "title"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            for c in (child if isinstance(child, list) else [child]):
                if c is not None and hasattr(c, "__dict__"):
                    stack.append(c)
    assert btn is not None, "the dialog has no [ check ] control"
    btn.on_click(None)
    deadline = time.time() + 10
    while btn.disabled:
        assert time.time() < deadline, "the in-dialog check never completed"
        time.sleep(0.01)
    time.sleep(0.05)


class _FailingCheckService:
    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        return (False, "Proxy failed", "", "", "", "", None, None)


class _GermanCheckService:
    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        return (True, "Proxy working", "DE", "Germany", "9.9.9.9", "", None, None)


def test_a_bare_save_after_the_exit_moved_does_not_re_arm_the_declaration(
    tmp_path,
):
    """THE DEFECT THIS SECTION EXISTS FOR, as the reviewer's transcript.

    The exit moves RO -> CZ, the gate correctly retires the declaration and the
    launch refuses. The operator then opens [ edit ] to find out WHY — which is
    exactly where the new network-page note sends them — and presses [ save ]
    without touching anything. Before the fix, the CZ exit launched declaring
    `Europe/Bucharest`: the country/clock contradiction route (a) was rejected
    for, reintroduced through the UI.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert _proxy_timezone(_store(tmp_path).get(name)) == RO_ZONE

    s.mark_checked(name, "CZ", "Czechia", "9.9.9.9", "", None, None)
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(_store(tmp_path).get(name))

    page = _drive_dialog(s, name)          # nothing typed, nothing changed
    assert page.popped is True             # an untouched field must not block a save

    proxy = _store(tmp_path).get(name)
    assert proxy.manual_timezone_country == RO, (
        "the country the zone was declared FOR was re-stamped to the new exit"
    )
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(proxy)


def test_a_rename_after_the_exit_moved_does_not_re_arm_it_either(tmp_path):
    """Same seam, reached by a gesture that has nothing to do with timezones.

    A rename is the ordinary reason to open this dialog, and it goes through
    `update()` — a DIFFERENT store writer from the bare save above, which
    rebuilds the record through a hand-enumerated constructor.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    s.mark_checked(name, "CZ", "Czechia", "9.9.9.9", "", None, None)

    page = _drive_dialog(s, name, name="renamed-exit")
    assert page.popped is True

    proxy = _store(tmp_path).get("renamed-exit")
    assert proxy is not None, "the rename itself must still work"
    assert proxy.manual_timezone_country == RO
    with pytest.raises(TimezoneUnderivableError):
        _proxy_timezone(proxy)


def test_a_url_edit_through_the_dialog_leaves_no_half_record(tmp_path):
    """`update()` invalidates the declaration with the six geo fields when the
    URL moves — and the dialog must not put it back one line later.

    The resulting record was inert (fail-closed, so no wrong clock) but it was
    a `manual_timezone` with an EMPTY `manual_timezone_country`, the exact
    half-record `set_manual_timezone`'s docstring says cannot exist. It landed
    the operator in a fresh disagreement of the kind AC8 was written to end:
    the network row says "set the exit timezone in [ edit ]" and [ edit ] shows
    the timezone field already filled in.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)

    page = _drive_dialog(s, name, host="5.5.5.5")
    assert page.popped is True

    proxy = _store(tmp_path).get(name)
    assert proxy.url != "socks5://u:pw@1.2.3.4:1080", "the URL edit must land"
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == ("", ""), (
        "a declaration survived the rotation that invalidated the geography"
    )


def test_a_rename_that_does_not_move_the_exit_KEEPS_the_declaration(tmp_path):
    """The other direction, so the fix is not "the dialog never declares".

    A rename leaves the exit exactly where it was, so the declaration still
    describes it and the profile must go on launching. A guard that also killed
    this would be a regression dressed as a fix.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)

    page = _drive_dialog(s, name, name="renamed-ro")
    assert page.popped is True

    proxy = _store(tmp_path).get("renamed-ro")
    assert proxy.manual_timezone_country == RO
    assert _proxy_timezone(proxy) == RO_ZONE


def test_the_operator_can_still_answer_for_a_moved_exit_through_the_dialog(
    tmp_path,
):
    """The recovery path. Not re-arming a retired declaration must not mean the
    operator cannot declare a NEW one for the country the exit moved to —
    otherwise the fix for BLOCKING 1 would create a second deadlock."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    s.mark_checked(name, "CZ", "Czechia", "9.9.9.9", "", None, None)

    page = _drive_dialog(s, name, zone="Europe/Prague")
    assert page.popped is True

    proxy = _store(tmp_path).get(name)
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == (
        "Europe/Prague", "CZ",
    )
    assert _proxy_timezone(proxy) == "Europe/Prague"


def test_re_declaring_a_retired_zone_verbatim_is_refused_with_a_sentence(
    tmp_path,
):
    """The narrow residue of the no-re-stamp rule, at the STORE.

    Wanting the SAME zone string for a different country is legitimate but
    rare (a `Europe/Bucharest` exit in Moldova, say). It cannot be expressed in
    one gesture, because that gesture is indistinguishable from the prefilled
    re-submit that caused the defect. So it is refused with a sentence naming
    what is on file and how to re-declare it — never a silent success that
    changes nothing.
    """
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    s.mark_checked(name, "CZ", "Czechia", "9.9.9.9", "", None, None)

    ok, err = s.set_manual_timezone(name, RO_ZONE)
    assert ok is False
    assert RO in err and "CZ" in err, err
    assert _store(tmp_path).get(name).manual_timezone_country == RO

    # And the two-gesture path the sentence describes works.
    assert s.set_manual_timezone(name, "") == (True, "")
    assert s.set_manual_timezone(name, RO_ZONE) == (True, "")
    assert _proxy_timezone(_store(tmp_path).get(name)) == RO_ZONE


def test_re_declaring_a_LIVE_zone_verbatim_is_an_accepted_no_op(tmp_path):
    """Same country: the declaration on file already says this, so re-declaring
    it succeeds and writes nothing. This is the case a bare [ save ] hits on a
    proxy that never moved, and it must not surface an error."""
    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    assert s.set_manual_timezone(name, RO_ZONE) == (True, "")
    proxy = _store(tmp_path).get(name)
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == (RO_ZONE, RO)


def test_declaring_a_zone_on_an_unchecked_proxy_is_refused_at_the_dialog(
    tmp_path,
):
    """BLOCKING 3 at the layer the operator meets it.

    Adding a proxy and filling the whole form before pressing [ check ] is an
    ordinary sequence. The dialog must say so and stay OPEN, rather than
    accepting the value, closing, and leaving a declaration that can never
    activate. Asserted before `on_save` too: the proxy must not be created and
    then rejected for a different field.
    """
    s = _store(tmp_path)
    page = _drive_dialog(
        s, "does-not-exist",
        zone=RO_ZONE, name="brand-new", host="1.2.3.4", port="1080",
    )
    assert page.popped is False, "the dialog must not close on a refusal"
    assert s.get("brand-new") is None, "nothing may be half-created"
    told = [t for t in _all_text(page.shown) if "check" in t.lower()]
    assert told, "the operator must be told to check the proxy first"


def test_the_network_row_and_the_dialog_agree_after_a_url_edit(tmp_path):
    """AC8's disagreement, closed on the flow that used to manufacture it.

    The half-record left by a URL edit made the network row say "cannot launch:
    set the exit timezone in [ edit ]" while [ edit ] showed the field already
    filled in. Both surfaces are read here, off the same stored record.
    """
    from src.ui.components.network_page import UNLAUNCHABLE_NOTE, build_network_page
    from src.ui.dialogs.proxy import open_proxy_dialog

    s, name = _ro_proxy(tmp_path)
    s.set_manual_timezone(name, RO_ZONE)
    _drive_dialog(s, name, host="5.5.5.5")
    # A later check finds the new exit is in Romania too — which is what
    # re-populates the country and brings the note back.
    s.mark_checked(name, RO, "Romania", "5.5.5.5", "", None, None)

    proxy = _store(tmp_path).get(name)
    rendered = build_network_page(
        [proxy],
        on_add=lambda _: None, on_edit=lambda n: None, on_delete=lambda n: None,
        on_check=lambda n: None, on_rotate=lambda n: None,
    )
    assert any(UNLAUNCHABLE_NOTE in t for t in _all_text(rendered))

    page = _FakePage()
    open_proxy_dialog(
        page, _FakeCheckService(), on_save=lambda *a: None, proxy=proxy,
    )
    assert _dialog_field(page.shown, TZ_LABEL).value == "", (
        "the row says the zone is missing; the dialog must not show one"
    )


# ---------------------------------------------------------------------------
# THE REMEDY SEAM — a refusal that names a gesture is a claim that the gesture
# WORKS, and this one did not.
#
# The gate above reads the country off the `proxy` SNAPSHOT the dialog was
# opened with, and refuses a declaration without one, saying "press [ check ]
# first". The dialog HAS a [ check ] button. But `on_check_result` persists a
# result only when `proxy is not None and checked_url == proxy.url` (audit6 #6,
# and that gate is correct) — so on an ADD, where no record exists yet, and on
# an EDIT whose URL was changed, pressing [ check ] turned the flag Romanian
# on screen and wrote nowhere the gate could see. The operator was told to do
# the thing they had just done: the same looping remedy this ticket exists to
# remove (`launch_policy.py:340-347`), reintroduced one layer up.
#
# Nothing in the suite could have caught it, because every dialog test drove
# [ save ] ALONE. The axis these tests vary is therefore the operator's GESTURE
# SEQUENCE — check THEN save — not the data.
# ---------------------------------------------------------------------------


def test_checking_inside_the_dialog_then_declaring_works_on_an_ADD(tmp_path):
    """THE DEFECT, on the flow the product's own sentence prescribes.

    Adding a proxy, pressing [ check ] (the flag turns Romanian), typing a zone
    and saving must WORK. Before the fix it was refused with "press [ check ]
    first" — with a Romanian flag on screen, from a check just run in that very
    dialog.
    """
    s = _store(tmp_path)
    page = _drive_dialog(
        s, "does-not-exist", press_check=True,
        zone=RO_ZONE, name="brand-new", host="1.2.3.4", port="1080",
    )
    assert page.popped is True, (
        "the remedy the refusal names did not clear the refusal"
    )
    proxy = _store(tmp_path).get("brand-new")
    assert proxy is not None, "the proxy must actually be created"
    assert proxy.country_code == RO, (
        "the check run in the dialog must reach the record once it exists"
    )
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == (RO_ZONE, RO)
    assert _proxy_timezone(proxy) == RO_ZONE


def test_checking_inside_the_dialog_then_declaring_works_on_a_URL_CHANGE(
    tmp_path,
):
    """The second half of the trigger boundary.

    An EDIT whose URL was changed is the other configuration `on_check_result`
    deliberately does not persist for — and it is the one an operator reaches by
    repointing a proxy at a new endpoint and checking it before saving. The geo
    that lands must be the NEW url's, measured in this dialog.
    """
    s, name = _ro_proxy(tmp_path)
    page = _drive_dialog(
        s, name, host="9.9.9.9", press_check=True, zone="Europe/Berlin",
        service=_GermanCheckService(),
    )
    assert page.popped is True

    proxy = _store(tmp_path).get(name)
    assert "9.9.9.9" in proxy.url, "the URL edit must land"
    assert (proxy.country_code, proxy.last_ip) == ("DE", "9.9.9.9"), (
        "the record must carry the geo of the URL that was saved"
    )
    assert (proxy.manual_timezone, proxy.manual_timezone_country) == (
        "Europe/Berlin", "DE",
    )
    assert _proxy_timezone(proxy) == "Europe/Berlin"


def test_an_unsaved_check_still_does_not_reach_the_record_when_cancelled(
    tmp_path,
):
    """audit6 #6 IS NOT WEAKENED, and this is the test that says so.

    The reason `on_check_result` gates its write is that a checked-but-unsaved
    URL's geography must not land on the stored record, because [ cancel ] would
    not undo it. Holding the result in the dialog and persisting it at the SAVE
    keeps that promise exactly: walk away and the stored record is untouched.
    """
    s, name = _ro_proxy(tmp_path)
    before = _store(tmp_path).get(name)

    _drive_dialog(
        s, name, host="9.9.9.9", press_check=True, submit=False,
        service=_GermanCheckService(),
    )

    after = _store(tmp_path).get(name)
    assert (after.url, after.country_code, after.last_ip) == (
        before.url, before.country_code, before.last_ip,
    ), "a check of an unsaved URL reached the stored record after all"


def test_a_check_that_FAILED_in_the_dialog_does_not_satisfy_the_gate(tmp_path):
    """Fail-closed on the same seam. A check that RAN and failed learned no
    country, so it must not let a declaration through — and the sentence must
    stop saying "press [ check ] first", which is the loop again for someone
    whose check just failed.
    """
    s = _store(tmp_path)
    page = _drive_dialog(
        s, "does-not-exist", press_check=True, service=_FailingCheckService(),
        zone=RO_ZONE, name="bad-one", host="1.2.3.4", port="1080",
    )
    assert page.popped is False, "the dialog must not close on a refusal"
    assert s.get("bad-one") is None, "nothing may be half-created"
    told = _all_text(page.shown)
    assert any("check failed" in t.lower() for t in told), told
    assert not any(
        "press [ check ] first" in t.lower() for t in told
    ), "telling someone whose check just failed to check is the loop again"


def test_declaring_on_a_proxy_whose_last_check_FAILED_is_refused(tmp_path):
    """The store half of the same rule, and the non-blocking finding it closes.

    `mark_check_failed` leaves `country_code` populated from the LAST
    successful check while setting `last_check_ok = False` — so the plain
    "is there a country?" term passes and the declaration was accepted. It is
    inert (the disproven-geo guard refuses the launch before any timezone
    branch), and that is the problem: a success and a closed dialog for a value
    that changes nothing is the silent-no-op shape the whole round is about.
    """
    s, name = _ro_proxy(tmp_path)
    s.mark_check_failed(name)

    ok, err = s.set_manual_timezone(name, RO_ZONE)
    assert ok is False
    assert "failed" in err.lower(), err
    stored = _store(tmp_path).get(name)
    assert stored.manual_timezone == "", "an inert declaration was stored"

    page = _drive_dialog(s, name, zone=RO_ZONE)
    assert page.popped is False
    assert any("check failed" in t.lower() for t in _all_text(page.shown))


def test_a_check_of_the_UNCHANGED_url_is_not_re_recorded_at_the_save(tmp_path):
    """The narrow scope of the new persist.

    `on_check_result` already writes through for a check of the STORED url, so
    the save must not write it a second time — that would stamp a fresh
    `checked_at` for a check that was already recorded, and would fire
    `mark_checked` twice for one gesture.
    """
    s, name = _ro_proxy(tmp_path)
    marks = []
    original = ProxyStore.mark_checked

    def counting(self, *a, **k):
        marks.append(a[:1])
        return original(self, *a, **k)

    ProxyStore.mark_checked = counting
    try:
        page = _drive_dialog(s, name, press_check=True, zone=RO_ZONE)
    finally:
        ProxyStore.mark_checked = original

    assert page.popped is True
    assert len(marks) == 1, f"the check was recorded {len(marks)} times"
    assert _proxy_timezone(_store(tmp_path).get(name)) == RO_ZONE
