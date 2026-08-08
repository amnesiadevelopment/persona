"""#10/#15: the proxy geo check must use HTTPS and validate the returned geo
before it feeds the persisted fingerprint, and a skipped check (no aiohttp) must
not be recorded as a success that erases known-good geo."""
import asyncio

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
    # #15: with aiohttp missing the check can't run; it must return ok=False so a
    # caller records a failure (which keeps geo) instead of a success with empty
    # geo (which erases the proxy's known-good country/timezone).
    monkeypatch.setattr(proxy_checker, "AIOHTTP_AVAILABLE", False)
    result = asyncio.run(check_proxy("socks5://user:pass@1.2.3.4:1080"))
    ok = result[0]
    assert ok is False
    assert "skipped" in result[1].lower()
