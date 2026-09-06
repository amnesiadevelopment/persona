import pytest

from src.models.proxy import Proxy
from src.services.proxy.freshness import proxy_indicator_state
from src.services.proxy.store import ProxyStore


def _store(tmp_path):
    return ProxyStore(path=str(tmp_path / "proxies.json"))


def test_add_and_list(tmp_path):
    s = _store(tmp_path)
    assert s.add("home", "socks5://user:pass@1.2.3.4:1080") is True
    assert s.names() == ["home"]
    assert s.list_proxies() == [Proxy("home", "socks5://user:pass@1.2.3.4:1080")]


def test_add_duplicate_name_rejected(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.add("home", "http://5.6.7.8:8080") is False
    assert s.url_for("home") == "socks5://1.2.3.4:1080"


def test_add_empty_name_rejected(tmp_path):
    s = _store(tmp_path)
    assert s.add("", "socks5://1.2.3.4:1080") is False


def test_url_for(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.url_for("home") == "socks5://1.2.3.4:1080"
    assert s.url_for("missing") is None
    assert s.url_for(None) is None


def test_update_value(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.update("home", "home", "http://9.9.9.9:3128") is True
    assert s.url_for("home") == "http://9.9.9.9:3128"


def test_update_rename(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.update("home", "work", "socks5://1.2.3.4:1080") is True
    assert s.names() == ["work"]
    assert s.get("home") is None


def test_update_rename_collision_rejected(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    s.add("work", "http://5.6.7.8:8080")
    assert s.update("home", "work", "socks5://1.2.3.4:1080") is False
    assert s.url_for("home") == "socks5://1.2.3.4:1080"


def test_delete(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.delete("home") is True
    assert s.names() == []
    assert s.delete("home") is False


def test_resolve_by_name(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.resolve("home") == "socks5://1.2.3.4:1080"


def test_resolve_none(tmp_path):
    s = _store(tmp_path)
    assert s.resolve(None) is None
    assert s.resolve("") is None


def test_resolve_unknown_name_that_is_raw_url(tmp_path):
    s = _store(tmp_path)
    assert s.resolve("socks5://9.9.9.9:1080") == "socks5://9.9.9.9:1080"
    assert s.resolve("1.2.3.4:8080") == "1.2.3.4:8080"


def test_resolve_unknown_garbage(tmp_path):
    s = _store(tmp_path)
    assert s.resolve("not-a-proxy") is None


def test_resolve_named_proxy_with_unparseable_url_is_none(tmp_path):
    # audit7 #1 (regression of the fail-closed proxy guard): a NAMED proxy whose
    # stored url can't be parsed (missing port, bare host) must resolve to None,
    # NOT return the truthy-but-useless url. The launch guard only checks
    # emptiness, so a truthy url passed the guard while chromium's parser then
    # returned None → no --proxy-server AND the whole anti-leak block skipped →
    # DIRECT clearnet on a profile WITH a proxy. resolve must gate on
    # parseability, matching the raw-url fallback path.
    s = _store(tmp_path)
    s.add("broken-noport", "socks5://1.2.3.4")   # no port
    s.add("broken-barehost", "just-a-host")
    assert s.resolve("broken-noport") is None
    assert s.resolve("broken-barehost") is None
    # a well-formed named proxy still resolves
    s.add("good", "socks5://1.2.3.4:1080")
    assert s.resolve("good") == "socks5://1.2.3.4:1080"


def test_persistence_across_instances(tmp_path):
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path)
    s1.add("home", "socks5://1.2.3.4:1080")
    s2 = ProxyStore(path=path)
    assert s2.url_for("home") == "socks5://1.2.3.4:1080"


def _fixed_store(tmp_path, t):
    return ProxyStore(path=str(tmp_path / "proxies.json"), now=lambda: t)


def test_mark_checked_records_geo_and_time(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    assert (
        s.mark_checked("home", "CA", "Canada", "142.119.62.143", "America/Toronto")
        is True
    )
    p = s.get("home")
    assert p.country_code == "CA"
    assert p.country_name == "Canada"
    assert p.last_ip == "142.119.62.143"
    assert p.timezone == "America/Toronto"
    assert p.checked_at == 1000.0


def test_mark_checked_unknown(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    assert s.mark_checked("missing", "CA", "Canada") is False


def test_geo_persists(tmp_path):
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path, now=lambda: 500.0)
    s1.add("home", "socks5://1.2.3.4:1080")
    s1.mark_checked("home", "NL", "Netherlands")
    s2 = ProxyStore(path=path)
    p = s2.get("home")
    assert p.country_code == "NL"
    assert p.checked_at == 500.0


def test_update_same_url_keeps_geo(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    s.mark_checked("home", "CA", "Canada")
    s.update("home", "home-renamed", "socks5://1.2.3.4:1080")
    p = s.get("home-renamed")
    assert p.country_code == "CA"
    assert p.checked_at == 1000.0


def test_update_changed_url_resets_geo(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    s.mark_checked("home", "CA", "Canada")
    s.update("home", "home", "socks5://9.9.9.9:1080")
    p = s.get("home")
    assert p.country_code == ""
    assert p.checked_at == 0.0


def test_mark_checked_sets_last_check_ok_true(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    s.mark_checked("home", "CA", "Canada")
    assert s.get("home").last_check_ok is True


def test_mark_check_failed_records_failure_and_time(tmp_path):
    s = _fixed_store(tmp_path, 2000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.mark_check_failed("home") is True
    p = s.get("home")
    assert p.last_check_ok is False
    assert p.checked_at == 2000.0


def test_mark_check_failed_unknown(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    assert s.mark_check_failed("missing") is False


def test_failure_after_success_flips_flag(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    s.mark_checked("home", "CA", "Canada")
    s.mark_check_failed("home")
    assert s.get("home").last_check_ok is False


def test_last_check_ok_defaults_none(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.get("home").last_check_ok is None


def test_last_check_ok_persists(tmp_path):
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path, now=lambda: 500.0)
    s1.add("home", "socks5://1.2.3.4:1080")
    s1.mark_check_failed("home")
    s2 = ProxyStore(path=path)
    assert s2.get("home").last_check_ok is False


def test_update_changed_url_resets_check_status(tmp_path):
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://1.2.3.4:1080")
    s.mark_check_failed("home")
    s.update("home", "home", "socks5://9.9.9.9:1080")
    assert s.get("home").last_check_ok is None


def test_rotate_url_defaults_empty(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    assert s.get("home").rotate_url == ""


def test_rotate_url_round_trips(tmp_path):
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path)
    s1.add("mob", "socks5://1.2.3.4:1080", "https://api.asocks.com/v2/proxy/refresh/1")
    s2 = ProxyStore(path=path)
    assert s2.get("mob").rotate_url == "https://api.asocks.com/v2/proxy/refresh/1"


def test_old_format_without_rotate_url_loads(tmp_path):
    import json
    import pathlib

    path = tmp_path / "proxies.json"
    path.write_text(
        json.dumps(
            {
                "home": {
                    "name": "home",
                    "url": "socks5://1.2.3.4:1080",
                    "country_code": "CA",
                    "country_name": "Canada",
                    "last_ip": "5.5.5.5",
                    "timezone": "",
                    "lat": None,
                    "lon": None,
                    "checked_at": 100.0,
                    "last_check_ok": True,
                }
            }
        ),
        encoding="utf-8",
    )
    s = ProxyStore(path=str(path))
    p = s.get("home")
    assert p is not None
    assert p.rotate_url == ""
    assert p.last_ip == "5.5.5.5"
    assert pathlib.Path(path).exists()


def test_update_sets_rotate_url(tmp_path):
    s = _store(tmp_path)
    s.add("home", "socks5://1.2.3.4:1080")
    s.update("home", "home", "socks5://1.2.3.4:1080", "https://rotate.example/x")
    assert s.get("home").rotate_url == "https://rotate.example/x"


def test_set_url_changes_url_and_drops_the_old_exits_geography(tmp_path):
    """A moved URL is a moved EXIT: nothing recorded about the old one survives.

    INVERTED from `test_set_url_changes_url_and_keeps_rest`, which asserted
    `country_code == "CA"` and `last_ip == "5.5.5.5"` survived a URL change —
    it encoded the defect (the record went on asserting the PREVIOUS exit's
    geography under a "verified" verdict). The rotate settings are genuinely
    kept; the geography is not.
    """
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://sess-a:p@1.2.3.4:1080", "https://rotate.example/x")
    s.mark_checked("home", "CA", "Canada", "5.5.5.5", "America/Toronto", 43.7, -79.4)
    assert s.set_url("home", "socks5://sess-b:p@1.2.3.4:1080") is True
    p = s.get("home")
    assert p.url == "socks5://sess-b:p@1.2.3.4:1080"
    # Rotate settings survive — they describe the ROTATOR, not the exit.
    assert p.rotate_url == "https://rotate.example/x"
    # All six geo fields plus the check bookkeeping are gone.
    assert p.country_code == ""
    assert p.country_name == ""
    assert p.last_ip == ""
    assert p.timezone == ""
    assert p.lat is None
    assert p.lon is None
    assert p.checked_at == 0.0
    assert p.last_check_ok is None
    # The verdict the launch path actually reads, not just the fields.
    assert proxy_indicator_state(p, 1000.0) == "unverified"


def test_set_url_without_a_url_change_keeps_everything(tmp_path):
    """The no-change path is byte-identical to before this slice.

    `app.py`'s `_rotate_proxy` already guards with `if url != proxy.url`, so
    this is reachable only by calling directly — but it is the contract the
    invalidation is conditioned on, so it is covered rather than assumed.
    """
    s = _fixed_store(tmp_path, 1000.0)
    s.add("home", "socks5://sess-a:p@1.2.3.4:1080", "https://rotate.example/x")
    s.mark_checked("home", "CA", "Canada", "5.5.5.5", "America/Toronto", 43.7, -79.4)
    assert s.set_url("home", "socks5://sess-a:p@1.2.3.4:1080") is True
    p = s.get("home")
    assert p.url == "socks5://sess-a:p@1.2.3.4:1080"
    assert p.country_code == "CA"
    assert p.country_name == "Canada"
    assert p.last_ip == "5.5.5.5"
    assert p.timezone == "America/Toronto"
    assert p.lat == 43.7
    assert p.lon == -79.4
    assert p.checked_at == 1000.0
    assert p.last_check_ok is True
    assert proxy_indicator_state(p, 1000.0) == "verified"


def test_set_url_persists(tmp_path):
    """The URL moves across a reload — and so does the invalidation.

    The `mark_checked` here is load-bearing, not scene-setting. Without it the
    fixture never has any geography for `set_url` to drop: `Proxy`'s dataclass
    defaults are already `country_code=""` / `timezone=""`, so the two geo
    assertions below held identically with the fix, without it, and against any
    future regression — they read as covering the invariant while being
    structurally incapable of detecting its violation.
    """
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path, now=lambda: 1000.0)
    s1.add("home", "socks5://1.2.3.4:1080")
    s1.mark_checked("home", "CA", "Canada", "5.5.5.5", "America/Toronto", 43.7, -79.4)
    s1.set_url("home", "socks5://9.9.9.9:1080")
    s2 = ProxyStore(path=path)
    assert s2.get("home").url == "socks5://9.9.9.9:1080"
    # The URL moved, so no geography may ride across the reload with it.
    assert s2.get("home").country_code == ""
    assert s2.get("home").timezone == ""


def test_set_url_invalidation_survives_a_restart(tmp_path):
    """The durable half — a `set_url` with NO follow-up check, then a reload.

    `set_url` `_save()`s before the rotate caller's check runs, so a crash,
    kill or quit between the two used to leave the stale affirmative on DISK:
    a fresh store read back the NEW url beside the OLD exit's geography and a
    `last_check_ok` of True, indicator "verified", and nothing later
    re-examined it. This is not a ~10s race — it survived a restart.
    """
    path = str(tmp_path / "proxies.json")
    s1 = ProxyStore(path=path, now=lambda: 1000.0)
    s1.add("home", "socks5://sess-a:pw@1.2.3.4:1080", "https://rotate.example/x")
    s1.mark_checked("home", "DE", "Germany", "5.5.5.5", "Europe/Berlin", 52.5, 13.4)
    s1.set_url("home", "socks5://sess-vy4bplk2:pw@1.2.3.4:1080")

    p = ProxyStore(path=path).get("home")  # a fresh store, as a restart would
    assert p.url == "socks5://sess-vy4bplk2:pw@1.2.3.4:1080"
    assert p.country_code == ""
    assert p.country_name == ""
    assert p.last_ip == ""
    assert p.timezone == ""
    assert p.lat is None
    assert p.lon is None
    assert p.checked_at == 0.0
    assert p.last_check_ok is None
    assert proxy_indicator_state(p, 1000.0) == "unverified"


def test_set_url_leaves_no_zone_for_a_launch_to_declare(tmp_path):
    """Reaches the OBSERVER, not just the record.

    The point of the invalidation is what a proxied profile DECLARES. Clearing
    only the verdict (leaving the geography) would move the record to
    "unverified" and change this outcome not at all — `_proxy_timezone`'s first
    branch returns `proxy.timezone` whenever it is non-empty, and the
    unverified-with-geography row is deliberately left launching. So assert the
    refusal, which is the thing that would silently not happen.
    """
    from src.services.browser.launch_policy import _proxy_timezone
    from src.services.proxy.errors import GeographyUnknownError

    path = str(tmp_path / "proxies.json")
    s = ProxyStore(path=path, now=lambda: 1000.0)
    s.add("home", "socks5://sess-a:pw@1.2.3.4:1080", "https://rotate.example/x")
    s.mark_checked("home", "DE", "Germany", "5.5.5.5", "Europe/Berlin", 52.5, 13.4)
    assert _proxy_timezone(s.get("home")) == "Europe/Berlin"  # before the move

    s.set_url("home", "socks5://sess-vy4bplk2:pw@1.2.3.4:1080")
    with pytest.raises(GeographyUnknownError):
        _proxy_timezone(ProxyStore(path=path).get("home"))


def test_set_url_unknown(tmp_path):
    s = _store(tmp_path)
    assert s.set_url("missing", "socks5://9.9.9.9:1080") is False


def test_one_malformed_record_is_skipped_not_fatal(tmp_path):
    import json

    path = tmp_path / "proxies.json"
    path.write_text(
        json.dumps(
            {
                "good": {"name": "good", "url": "socks5://1.2.3.4:1080"},
                "bad": {"name": "bad"},  # missing required url
            }
        ),
        encoding="utf-8",
    )
    s = ProxyStore(path=str(path))
    # the good record still loaded; only the malformed one was dropped
    assert s.url_for("good") == "socks5://1.2.3.4:1080"
    assert s.get("bad") is None


def test_corrupt_file_quarantined_not_overwritten(tmp_path):
    import pathlib

    path = tmp_path / "proxies.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    s = ProxyStore(path=str(path))
    # An unreadable file holds every saved proxy+creds; it must be moved aside,
    # not silently overwritten by the next save with the empty in-memory dict.
    s.add("home", "socks5://user:pass@1.2.3.4:1080")
    backups = list(pathlib.Path(tmp_path).glob("proxies.json.corrupt-*"))
    assert backups, "corrupt proxies.json must be quarantined"
    assert backups[0].read_text(encoding="utf-8") == "{ this is not valid json"


def test_save_blocked_when_quarantine_fails(tmp_path, monkeypatch):
    import pathlib

    path = tmp_path / "proxies.json"
    path.write_text("{ broken", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("cannot rename")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    s = ProxyStore(path=str(path))
    # rename failed -> saving disabled so we never overwrite the creds file
    s.add("home", "socks5://user:pass@1.2.3.4:1080")
    assert path.read_text(encoding="utf-8") == "{ broken"


# --- PS-358: geo_for_launch — the launch path's geography question -----------


def _store(tmp_path):
    from src.services.proxy.store import ProxyStore

    return ProxyStore(path=str(tmp_path / "proxies.json"))


def test_geo_for_launch_returns_a_stored_proxy_untouched_and_never_probes(tmp_path):
    """The NAMED path must be unchanged, and must pay NO launch-time round trip.

    A named proxy is geo-checked ahead of time. If this method probed for it too,
    the fix would put a network call in front of every proxied launch — the
    latency cost the ticket asks to be weighed, imposed on the one population
    that had already paid it.
    """
    s = _store(tmp_path)
    s.add("pl-exit", "socks5://u:p@5.6.7.8:1080")
    s.mark_checked("pl-exit", "PL", "Poland", ip="5.6.7.8", timezone="Europe/Warsaw")

    probed = []

    def _never(url, timeout):
        probed.append(url)
        raise AssertionError("a STORED proxy must not be probed at launch")

    got = s.geo_for_launch("pl-exit", check=_never)
    assert got is s.get("pl-exit"), "the stored record itself must come back"
    assert got.country_code == "PL"
    assert probed == []


def test_geo_for_launch_answers_none_for_a_genuinely_direct_profile(tmp_path):
    """No ref at all is DIRECT, and must keep taking the direct branch."""
    s = _store(tmp_path)
    assert s.geo_for_launch(None) is None
    assert s.geo_for_launch("") is None


def test_geo_for_launch_resolves_an_inline_refs_exit_geography(tmp_path):
    """THE FIX. An inline ref must come back carrying its EXIT's geography.

    Before PS-358 this ref answered None, which the launch read as "direct".
    """
    s = _store(tmp_path)
    ref = "socks5://u:p@1.2.3.4:1080"
    assert s.get(ref) is None, "premise: an inline ref names no stored proxy"

    def _pl(url, timeout):
        assert url == ref, "the probe must be handed the resolved URL"
        return (True, "ok", "PL", "Poland", "46.205.198.123", "Europe/Warsaw", 52.2, 21.0)

    got = s.geo_for_launch(ref, check=_pl)
    assert got is not None, "an inline proxy must not answer None (that is DIRECT)"
    assert (got.country_code, got.timezone) == ("PL", "Europe/Warsaw")
    assert got.last_check_ok is True
    # It must NOT have been written into the store: launching a profile is not
    # a gesture that creates a saved proxy.
    assert s.get(ref) is None
    assert "pl" not in s.proxies and ref not in s.proxies


def test_geo_for_launch_carries_no_geography_when_the_exit_is_unknown(tmp_path):
    """A failed probe must yield an EMPTY record, so the launch gate refuses.

    ⛔ It must never invent a value — not a host-derived one, not a coarser one.
    The record comes back geoless and `_profile_timezone` refuses on it.
    """
    s = _store(tmp_path)
    ref = "socks5://u:p@203.0.113.9:1080"

    def _fail(url, timeout):
        return (False, "timed out", "", "", "", "", None, None)

    got = s.geo_for_launch(ref, check=_fail)
    assert got is not None
    assert got.country_code == "" and got.timezone == ""
    assert got.last_check_ok is None


def test_geo_for_launch_does_not_let_a_raising_probe_take_the_launch_down(tmp_path):
    """A checker that RAISES must refuse the launch, not crash it — and must not
    be mistaken for a successful check."""
    s = _store(tmp_path)

    def _boom(url, timeout):
        raise OSError("network unreachable")

    got = s.geo_for_launch("socks5://u:p@1.2.3.4:1080", check=_boom)
    assert got is not None
    assert got.country_code == "" and got.timezone == ""


def test_geo_for_launch_pays_the_round_trip_at_most_once_per_exit(tmp_path):
    """The latency bound. Relaunching the same inline proxy must not re-probe."""
    s = _store(tmp_path)
    ref = "socks5://u:p@1.2.3.4:1080"
    calls = []

    def _pl(url, timeout):
        calls.append(url)
        return (True, "ok", "PL", "Poland", "46.205.198.123", "Europe/Warsaw", None, None)

    first = s.geo_for_launch(ref, check=_pl)
    second = s.geo_for_launch(ref, check=_pl)
    assert len(calls) == 1, f"probed {len(calls)} times for one exit"
    assert (second.country_code, second.timezone) == (first.country_code, first.timezone)


def test_a_failed_probe_is_not_cached_so_a_transient_outage_is_retried(tmp_path):
    """Caching a FAILURE would pin a blip into a permanent refusal for the life
    of the process. Only a successful check is remembered."""
    s = _store(tmp_path)
    ref = "socks5://u:p@1.2.3.4:1080"
    calls = []

    def _flaky(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return (False, "timed out", "", "", "", "", None, None)
        return (True, "ok", "PL", "Poland", "1.2.3.4", "Europe/Warsaw", None, None)

    assert s.geo_for_launch(ref, check=_flaky).country_code == ""
    assert s.geo_for_launch(ref, check=_flaky).country_code == "PL"
    assert len(calls) == 2, "a failed probe must be retried, not cached"


def test_geo_for_launch_answers_none_for_an_unparseable_ref(tmp_path):
    """`resolve` already fails closed on an unusable URL; the launch guard
    refuses on the empty URL before geography is consulted, so there is nothing
    to probe."""
    s = _store(tmp_path)
    probed = []

    got = s.geo_for_launch(
        "socks5://1.2.3.4",  # no port: parse_proxy_server returns None
        check=lambda u, t: probed.append(u) or (True, "", "PL", "", "", "", None, None),
    )
    assert got is None
    assert probed == [], "an unusable ref must not be probed"
