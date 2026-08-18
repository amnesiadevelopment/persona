"""#10/#15: the proxy geo check must use HTTPS and validate the returned geo
before it feeds the persisted fingerprint, and a skipped check (no aiohttp) must
not be recorded as a success that erases known-good geo."""
import asyncio
import socket

from src.utils import proxy_checker
from src.utils.proxy_checker import _validate_geo, check_proxy


def test_uses_https_geo_endpoint():
    import inspect

    src = inspect.getsource(check_proxy)
    assert "https://" in src
    assert "http://ip-api.com" not in src


def test_validate_geo_accepts_good_values():
    code, tz, lat, lon = _validate_geo("pl", "Europe/Warsaw", 52.23, 21.01)
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert lat == 52.23 and lon == 21.01


def test_validate_geo_drops_bogus_country_and_tz():
    code, tz, lat, lon = _validate_geo("XYZ", "not-a-zone", 52.23, 21.01)
    assert code == ""      # 3 letters -> dropped
    assert tz == ""        # no "/" -> dropped


def test_validate_geo_drops_out_of_range_coords():
    code, tz, lat, lon = _validate_geo("PL", "Europe/Warsaw", 999.0, "nan")
    assert lat is None
    assert lon is None


def test_skipped_check_is_not_a_success(monkeypatch):
    # #15: with aiohttp missing the HTTP-proxy check can't run; it must return
    # ok=False so a caller records a failure (which keeps geo) instead of a
    # success with empty geo (which erases the proxy's known-good
    # country/timezone).
    monkeypatch.setattr(proxy_checker, "AIOHTTP_AVAILABLE", False)
    result = asyncio.run(check_proxy("http://user:pass@1.2.3.4:8080"))
    ok = result[0]
    assert ok is False
    assert "skipped" in result[1].lower()


def test_socks_check_does_not_need_aiohttp(monkeypatch):
    """The #15 rule is scoped to the aiohttp branch, deliberately.

    The SOCKS path is stdlib-only, and socks5 is persona's DEFAULT scheme — so
    skipping it whenever aiohttp is absent would hand back the empty geo that
    makes the launcher fall back to the operator's real host timezone, i.e. the
    exact leak this module exists to close. It must still never report success
    without real geo: nothing is listening here, so it fails closed.
    """
    monkeypatch.setattr(proxy_checker, "AIOHTTP_AVAILABLE", False)
    monkeypatch.setattr(proxy_checker, "_is_blocked_proxy_host", lambda server: False)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing is listening -> connection refused, fast

    result = asyncio.run(check_proxy(f"socks5://127.0.0.1:{port}", timeout=5))
    assert result[0] is False
    assert "skipped" not in result[1].lower()   # it really tried
    assert result[2:6] == ("", "", "", "")      # and recorded no geo
    assert "127.0.0.1" not in result[1] and str(port) not in result[1]
