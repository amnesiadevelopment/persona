import re
import time

from src.services.proxy import service as service_mod
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

    def fake_fetch(url, timeout):
        calls.append((url, timeout))
        return True, "HTTP 200"

    monkeypatch.setattr(service_mod, "_fetch_rotate_url", fake_fetch)
    s = ProxyService(default_timeout=7)
    url, note = s.rotate_proxy(
        "socks5://u:p@h:1", "https://api.asocks.com/v2/proxy/refresh/1?apiKey=k"
    )
    assert calls == [("https://api.asocks.com/v2/proxy/refresh/1?apiKey=k", 7)]
    assert url == "socks5://u:p@h:1"
    assert "rotate endpoint OK" in note


def test_rotate_reports_rotate_url_failure(monkeypatch):
    monkeypatch.setattr(
        service_mod, "_fetch_rotate_url", lambda u, t: (False, "HTTP 500")
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
