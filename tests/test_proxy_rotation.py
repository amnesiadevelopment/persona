import re
import time

import pytest

from src.services.browser.launch_policy import _proxy_timezone
from src.services.proxy import service as service_mod
from src.services.proxy.errors import GeographyUnknownError
from src.services.proxy.freshness import proxy_indicator_state
from src.services.proxy.service import ProxyService
from src.services.proxy.store import ProxyStore
from src.ui.app import App
from src.utils.proxy_rotation import regenerate_session_token

# --- regenerate_session_token ---

IPROYAL_URL = (
    "socks5://user123:pass321_country-us_session-abcd1234_lifetime-30m@geo.iproyal.com:12321"
)


def test_session_token_is_replaced():
    out = regenerate_session_token(IPROYAL_URL)
    assert out is not None
    assert out != IPROYAL_URL
    assert "_session-abcd1234_" not in out


def test_session_token_keeps_length_and_charset():
    out = regenerate_session_token(IPROYAL_URL)
    m = re.search(r"_session-([a-z0-9]+)_", out)
    assert m is not None
    assert len(m.group(1)) == len("abcd1234")


def test_session_token_keeps_base_credentials_and_host():
    out = regenerate_session_token(IPROYAL_URL)
    assert out.startswith("socks5://user123:pass321_country-us_session-")
    assert out.endswith("_lifetime-30m@geo.iproyal.com:12321")


def test_sessid_variant_is_recognized():
    url = "http://customer-me-sessid-xyz789:pw@pr.oxylabs.io:7777"
    out = regenerate_session_token(url)
    assert out is not None
    assert "sessid-xyz789:" not in out
    assert out.endswith("@pr.oxylabs.io:7777")


def test_session_underscore_separator_is_recognized():
    url = "http://user-session_abc12345:pw@host:8080"
    out = regenerate_session_token(url)
    assert out is not None
    assert "session_abc12345" not in out


def test_no_session_token_returns_none():
    assert regenerate_session_token("socks5://user:pass@1.2.3.4:1080") is None


def test_no_credentials_returns_none():
    assert regenerate_session_token("socks5://1.2.3.4:1080") is None


def test_regenerated_token_differs_every_time():
    outs = {regenerate_session_token(IPROYAL_URL) for _ in range(5)}
    assert IPROYAL_URL not in outs


# --- ProxyService.rotate_proxy ---


def test_rotate_uses_rotate_url_when_set(monkeypatch):
    calls = []

    def fake_fetch(url, proxy_url, timeout):
        calls.append((url, proxy_url, timeout))
        return True, "HTTP 200"

    monkeypatch.setattr(service_mod, "_fetch_rotate_url", fake_fetch)
    s = ProxyService(default_timeout=7)
    url, note = s.rotate_proxy(
        "socks5://u:p@h:1", "https://api.asocks.com/v2/proxy/refresh/1?apiKey=k"
    )
    # The proxy being rotated must be handed down as the TRANSPORT for the
    # rotate request — it is not enough that rotate_proxy merely holds it.
    assert calls == [
        (
            "https://api.asocks.com/v2/proxy/refresh/1?apiKey=k",
            "socks5://u:p@h:1",
            7,
        )
    ]
    assert url == "socks5://u:p@h:1"
    assert "rotate endpoint OK" in note


def test_rotate_reports_rotate_url_failure(monkeypatch):
    monkeypatch.setattr(
        service_mod, "_fetch_rotate_url", lambda u, p, t: (False, "HTTP 500")
    )
    s = ProxyService()
    url, note = s.rotate_proxy("socks5://u:p@h:1", "https://rotate.example/x")
    assert url == "socks5://u:p@h:1"
    assert "failed" in note
    assert "HTTP 500" in note


def test_rotate_regenerates_session_token_without_rotate_url():
    s = ProxyService()
    url, note = s.rotate_proxy(IPROYAL_URL)
    assert url != IPROYAL_URL
    assert url.endswith("@geo.iproyal.com:12321")
    assert note == "regenerated session token"


def test_rotate_falls_back_to_fresh_connection():
    s = ProxyService()
    url, note = s.rotate_proxy("socks5://u:p@h:1")
    assert url == "socks5://u:p@h:1"
    assert "fresh connection" in note


# --- App._rotate_proxy ---


class FakeService:
    def __init__(self, new_url=None, note="n", ip="9.9.9.9", ok=True):
        self.new_url = new_url
        self.note = note
        self.ip = ip
        self.ok = ok
        self.rotate_calls = []
        self.checked_urls = []

    def rotate_proxy(self, proxy_url, rotate_url="", timeout=None):
        self.rotate_calls.append((proxy_url, rotate_url))
        return self.new_url or proxy_url, self.note

    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        self.checked_urls.append(proxy_str)
        if self.ok:
            return (
                True, "Proxy working", "US", "United States",
                self.ip, "America/New_York", 25.77, -80.19,
            )
        return False, "Proxy connection timed out", "", "", "", "", None, None


def make_app(tmp_path, service):
    app = App.__new__(App)
    app.page = None
    app.pstore = ProxyStore(path=str(tmp_path / "proxies.json"))
    app.ps = service
    app._checking_proxies = set()
    app._active_page = "network"
    app._page_host = None
    app.logs = []
    app._log = app.logs.append
    return app


def _wait_done(app, name, timeout=5.0):
    deadline = time.time() + timeout
    while name in app._checking_proxies:
        assert time.time() < deadline, "rotate never finished"
        time.sleep(0.01)


def test_rotate_proxy_updates_last_ip_and_logs_change(tmp_path):
    svc = FakeService(ip="9.9.9.9")
    app = make_app(tmp_path, svc)
    app.pstore.add("mob", "socks5://u:p@h:1")
    app.pstore.mark_checked("mob", "US", "United States", "1.1.1.1")
    app._rotate_proxy("mob")
    _wait_done(app, "mob")
    # the store still tracks the exit IP internally (for geo/country display)
    assert app.pstore.get("mob").last_ip == "9.9.9.9"
    # ...but the activity log reports the change WITHOUT leaking the IP
    assert any("rotated to a new exit" in ln for ln in app.logs)
    assert not any("9.9.9.9" in ln or "1.1.1.1" in ln for ln in app.logs)


def test_rotate_proxy_persists_regenerated_url(tmp_path):
    svc = FakeService(new_url="socks5://u_session-newtoken:p@h:1")
    app = make_app(tmp_path, svc)
    app.pstore.add("sticky", "socks5://u_session-oldtoken:p@h:1")
    app._rotate_proxy("sticky")
    _wait_done(app, "sticky")
    assert app.pstore.get("sticky").url == "socks5://u_session-newtoken:p@h:1"
    assert svc.checked_urls == ["socks5://u_session-newtoken:p@h:1"]


def test_rotate_proxy_passes_rotate_url_to_service(tmp_path):
    svc = FakeService()
    app = make_app(tmp_path, svc)
    app.pstore.add("asocks", "socks5://u:p@h:1", "https://api.asocks.com/v2/proxy/refresh/1")
    app._rotate_proxy("asocks")
    _wait_done(app, "asocks")
    assert svc.rotate_calls == [
        ("socks5://u:p@h:1", "https://api.asocks.com/v2/proxy/refresh/1")
    ]


def test_rotate_proxy_reports_unchanged_ip(tmp_path):
    svc = FakeService(ip="1.1.1.1")
    app = make_app(tmp_path, svc)
    app.pstore.add("static", "socks5://u:p@h:1")
    app.pstore.mark_checked("static", "US", "United States", "1.1.1.1")
    app._rotate_proxy("static")
    _wait_done(app, "static")
    assert any("exit unchanged" in ln for ln in app.logs)
    assert not any("1.1.1.1" in ln for ln in app.logs)


def test_rotate_proxy_failed_check_marks_failure(tmp_path):
    svc = FakeService(ok=False)
    app = make_app(tmp_path, svc)
    app.pstore.add("dead", "socks5://u:p@h:1")
    app._rotate_proxy("dead")
    _wait_done(app, "dead")
    assert app.pstore.get("dead").last_check_ok is False
    assert any("timed out" in ln for ln in app.logs)


def test_rotate_proxy_unknown_name_is_noop(tmp_path):
    svc = FakeService()
    app = make_app(tmp_path, svc)
    app._rotate_proxy("missing")
    assert svc.rotate_calls == []


def test_rotate_proxy_skips_while_check_in_flight(tmp_path):
    svc = FakeService()
    app = make_app(tmp_path, svc)
    app.pstore.add("busy", "socks5://u:p@h:1")
    app._checking_proxies.add("busy")
    app._rotate_proxy("busy")
    assert svc.rotate_calls == []


# --- PS-250: the rotate path invalidates the OLD exit's geography ---
#
# The gate `app.py` used to rely on is `if url != proxy.url` -> `set_url`, and
# `set_url` invalidates only when the URL actually MOVES. Two of
# `ProxyService.rotate_proxy`'s three arms return the URL UNCHANGED, because a
# rotating/backconnect proxy's URL is constant BY DESIGN, so for them the gate
# never fires and the record went on asserting the previous exit's geography
# under a verdict reading "verified".
#
# These tests drive the REAL `ProxyService` (not a stand-in that merely
# approximates its return values), so which arm is taken is decided by the
# shipped code, and they assert on the STORED RECORD and on the value
# `_proxy_timezone` hands a launching profile — never that a helper was called.


class ObservingService(ProxyService):
    """The real rotate logic, with the record observed mid-rotation.

    `check_proxy_detailed_sync` is the first thing that happens after the
    rotation is issued, and it is a real network round trip in production (~10s
    of `PROXY_CHECK_TIMEOUT`). Snapshotting here is therefore the honest
    observation point for BOTH windows this ticket is about:

    - IN FLIGHT — what a launch racing the rotation would read.
    - DURABLE — what stays on disk if the process ends before the re-check
      lands. The snapshot is taken through a FRESH `ProxyStore` reading
      `proxies.json` back, so it is the persisted state, not an in-memory one.
    """

    def __init__(self, store_path, geo=("US", "United States", "9.9.9.9",
                                        "America/New_York", 25.77, -80.19)):
        super().__init__()
        self._store_path = store_path
        self._geo = geo
        self.observed = None
        self.observed_tz = None
        self.observed_state = None
        self.checked_urls = []

    def check_proxy_detailed_sync(self, proxy_str, timeout=None):
        self.checked_urls.append(proxy_str)
        reloaded = ProxyStore(path=self._store_path).get("p")
        self.observed = reloaded
        self.observed_state = proxy_indicator_state(reloaded, time.time())
        try:
            self.observed_tz = _proxy_timezone(reloaded)
        except GeographyUnknownError as exc:
            self.observed_tz = exc
        cc, cn, ip, tz, lat, lon = self._geo
        return True, "Proxy working", cc, cn, ip, tz, lat, lon


def _seeded_app(tmp_path, service, url, rotate_url=""):
    """A proxy carrying a fully verified PL exit — the geography to invalidate."""
    app = make_app(tmp_path, service)
    app.pstore.add("p", url, rotate_url)
    app.pstore.mark_checked(
        "p", "PL", "Poland", "95.49.113.111", "Europe/Warsaw", 52.23, 21.01
    )
    assert proxy_indicator_state(app.pstore.get("p"), time.time()) == "verified"
    assert _proxy_timezone(app.pstore.get("p")) == "Europe/Warsaw"
    return app


def _assert_old_exit_not_asserted(svc):
    """The record mid-rotation carries nothing about the exit being abandoned."""
    p = svc.observed
    assert p is not None, "the follow-up check never ran"
    # All six geo fields.
    assert p.country_code == ""
    assert p.country_name == ""
    assert p.last_ip == ""
    assert p.timezone == ""
    assert p.lat is None
    assert p.lon is None
    # Plus the two bookkeeping fields.
    assert p.checked_at == 0.0
    assert p.last_check_ok is None
    # The verdict the launch path actually reads...
    assert svc.observed_state == "unverified"
    # ...and the value _proxy_timezone would hand a launching profile: a
    # REFUSAL, not the abandoned exit's zone.
    assert isinstance(svc.observed_tz, GeographyUnknownError), (
        f"_proxy_timezone declared {svc.observed_tz!r} instead of refusing"
    )


def test_rotate_endpoint_arm_stops_asserting_the_old_exits_geography(tmp_path, monkeypatch):
    """ARM 1 — provider rotate endpoint reports success, URL UNCHANGED.

    This is the arm the url-keyed gate skips entirely: `rotate_proxy` returns
    `proxy_url` verbatim, so `set_url` is never called and, before this slice,
    the record still read country='PL' tz='Europe/Warsaw' ok=True.
    """
    monkeypatch.setattr(service_mod, "_fetch_rotate_url", lambda u, p, t: (True, "HTTP 200"))
    path = str(tmp_path / "proxies.json")
    svc = ObservingService(path)
    app = _seeded_app(tmp_path, svc, "socks5://u:p@backconnect:1080",
                      "https://api.asocks.com/v2/proxy/refresh/1")
    app._rotate_proxy("p")
    _wait_done(app, "p")
    # The URL genuinely did not move — the premise of the defect, asserted so a
    # future change that starts moving it does not quietly void this test.
    assert svc.checked_urls == ["socks5://u:p@backconnect:1080"]
    assert app.pstore.get("p").url == "socks5://u:p@backconnect:1080"
    _assert_old_exit_not_asserted(svc)


def test_fresh_connection_arm_stops_asserting_the_old_exits_geography(tmp_path):
    """ARM 3 — no rotate URL and no session token, URL UNCHANGED.

    The second arm the gate skips: a backconnect endpoint hands out a new exit
    per connection at the same URL, which is precisely why the URL string is
    the wrong signal to condition the invalidation on.
    """
    path = str(tmp_path / "proxies.json")
    svc = ObservingService(path)
    app = _seeded_app(tmp_path, svc, "socks5://u:p@backconnect:1080")
    app._rotate_proxy("p")
    _wait_done(app, "p")
    assert any("fresh connection" in ln for ln in app.logs)
    assert svc.checked_urls == ["socks5://u:p@backconnect:1080"]
    _assert_old_exit_not_asserted(svc)


def test_session_token_arm_behaves_exactly_as_before(tmp_path):
    """ARM 2 (CONTROL) — regenerated session token, URL DOES change.

    The arm PS-101/PS-207 already fixed via `set_url`. It must keep behaving
    exactly as today: the URL is persisted, the follow-up check runs against the
    NEW url, and the geography is gone. Covered separately because it is the
    one arm that reaches the url-keyed gate, so it is the only place a
    regression in `set_url`'s half would show.
    """
    path = str(tmp_path / "proxies.json")
    svc = ObservingService(path)
    app = _seeded_app(tmp_path, svc, IPROYAL_URL)
    app._rotate_proxy("p")
    _wait_done(app, "p")
    assert any("regenerated session token" in ln for ln in app.logs)
    new_url = app.pstore.get("p").url
    assert new_url != IPROYAL_URL
    assert new_url.endswith("@geo.iproyal.com:12321")
    assert svc.checked_urls == [new_url]
    _assert_old_exit_not_asserted(svc)


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_interrupted_rotate_leaves_no_verified_stale_geography_on_disk(tmp_path):
    """AC4 — the DURABLE case: the follow-up check is never reached.

    Modelled by a check that raises, so `mark_checked`/`mark_check_failed` are
    both skipped and what survives is exactly what the rotation itself wrote —
    the same state a crash, kill or quit between the two would leave. Read back
    through a FRESH `ProxyStore`, i.e. what a restart sees in `proxies.json`.

    The raise escapes `do_rotate`'s daemon thread by design (that IS the
    interruption being modelled), so the resulting thread-exception warning
    is suppressed for THIS test only — never suite-wide, where an unhandled
    thread exception is a signal worth keeping.
    """

    class DyingService(ProxyService):
        def check_proxy_detailed_sync(self, proxy_str, timeout=None):
            raise RuntimeError("process died mid-check")

    app = _seeded_app(tmp_path, DyingService(), "socks5://u:p@backconnect:1080")
    try:
        app._rotate_proxy("p")
        _wait_done(app, "p")
    except RuntimeError:
        pass
    reloaded = ProxyStore(path=str(tmp_path / "proxies.json")).get("p")
    assert reloaded is not None
    assert proxy_indicator_state(reloaded, time.time()) != "verified"
    assert reloaded.timezone == ""
    assert reloaded.country_code == ""
    assert reloaded.last_ip == ""
    assert reloaded.lat is None and reloaded.lon is None
    assert reloaded.last_check_ok is None
    # The credentials and rotate settings are NOT collateral damage.
    assert reloaded.url == "socks5://u:p@backconnect:1080"


def test_successful_rotate_still_ends_verified_with_the_new_geography(tmp_path):
    """AC5 — widening the invalidation must not leave a working rotate unverified.

    The normal path re-checks and calls `mark_checked`; the END state is the
    NEW exit's geography under a "verified" verdict, and `_proxy_timezone`
    declares the new zone rather than refusing.
    """
    path = str(tmp_path / "proxies.json")
    svc = ObservingService(path)
    app = _seeded_app(tmp_path, svc, "socks5://u:p@backconnect:1080")
    app._rotate_proxy("p")
    _wait_done(app, "p")
    p = app.pstore.get("p")
    assert p.country_code == "US"
    assert p.country_name == "United States"
    assert p.last_ip == "9.9.9.9"
    assert p.timezone == "America/New_York"
    assert p.lat == 25.77 and p.lon == -80.19
    assert p.last_check_ok is True
    assert p.checked_at > 0.0
    assert proxy_indicator_state(p, time.time()) == "verified"
    assert _proxy_timezone(p) == "America/New_York"
    # And it survives a restart in that state.
    assert proxy_indicator_state(
        ProxyStore(path=path).get("p"), time.time()
    ) == "verified"


def test_rotate_still_reports_unchanged_exit_after_the_invalidation(tmp_path):
    """AC6 guard — `old_ip` must be read BEFORE the invalidation.

    `ProxyStore.get()` returns the LIVE `Proxy`, so an invalidation ordered
    before `old_ip = proxy.last_ip` empties the caller's own alias and the
    "exit unchanged" branch silently stops firing. This is the same claim
    `test_rotate_proxy_reports_unchanged_ip` makes, restated against a proxy
    whose full geography was invalidated, so the ordering is pinned by a test
    that fails for the ORDERING reason specifically.
    """
    svc = FakeService(ip="95.49.113.111")  # the check returns the SAME exit
    app = _seeded_app(tmp_path, svc, "socks5://u:p@backconnect:1080")
    app._rotate_proxy("p")
    _wait_done(app, "p")
    assert any("exit unchanged" in ln for ln in app.logs)
    assert not any("rotated to a new exit" in ln for ln in app.logs)
    assert not any("95.49.113.111" in ln for ln in app.logs)
