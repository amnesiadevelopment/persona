from src.models.proxy import Proxy
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
