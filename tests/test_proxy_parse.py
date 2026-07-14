"""parse_proxy_line must split every asocks proxy string shape into
scheme/ip/port/login/password/name/rotate_url and never raise on garbage."""
from src.utils.proxy_parse import parse_proxy_line

CANONICAL = (
    "socks5://01kx0f7zfhvrexcnfgeh4hm0t4:RXuosXF1wj26ySsn@190.2.142.241:10496"
    ":MobUnited States - Miami"
    "[https://api.asocks.com/proxy/4e712f5b-7aab-11f1-ae21-bc24114c89e8/refresh-ip]"
)


def test_canonical_full_socks5():
    assert parse_proxy_line(CANONICAL) == {
        "scheme": "socks5",
        "ip": "190.2.142.241",
        "port": "10496",
        "login": "01kx0f7zfhvrexcnfgeh4hm0t4",
        "password": "RXuosXF1wj26ySsn",
        "name": "MobUnited States - Miami",
        "rotate_url": (
            "https://api.asocks.com/proxy/"
            "4e712f5b-7aab-11f1-ae21-bc24114c89e8/refresh-ip"
        ),
    }


def test_socks5_without_name_or_rotate():
    assert parse_proxy_line("socks5://user:pw@1.2.3.4:1080") == {
        "scheme": "socks5",
        "ip": "1.2.3.4",
        "port": "1080",
        "login": "user",
        "password": "pw",
        "name": "",
        "rotate_url": "",
    }


def test_full_http_with_name_and_rotate():
    line = "http://log:pass@5.6.7.8:8080:My Proxy - 1[https://api.asocks.com/proxy/abc/refresh-ip]"
    assert parse_proxy_line(line) == {
        "scheme": "http",
        "ip": "5.6.7.8",
        "port": "8080",
        "login": "log",
        "password": "pass",
        "name": "My Proxy - 1",
        "rotate_url": "https://api.asocks.com/proxy/abc/refresh-ip",
    }


def test_http_without_name_or_rotate():
    assert parse_proxy_line("http://log:pass@5.6.7.8:8080") == {
        "scheme": "http",
        "ip": "5.6.7.8",
        "port": "8080",
        "login": "log",
        "password": "pass",
        "name": "",
        "rotate_url": "",
    }


def test_raw_colon_with_refresh_link():
    line = (
        "190.2.142.241:10496:01kx0f7zfhvrexcnfgeh4hm0t4:RXuosXF1wj26ySsn"
        ":https://api.asocks.com/proxy/4e71/refresh-ip"
    )
    assert parse_proxy_line(line) == {
        "scheme": "",
        "ip": "190.2.142.241",
        "port": "10496",
        "login": "01kx0f7zfhvrexcnfgeh4hm0t4",
        "password": "RXuosXF1wj26ySsn",
        "name": "",
        "rotate_url": "https://api.asocks.com/proxy/4e71/refresh-ip",
    }


def test_raw_colon_without_refresh_link():
    assert parse_proxy_line("1.2.3.4:1080:user:pw") == {
        "scheme": "",
        "ip": "1.2.3.4",
        "port": "1080",
        "login": "user",
        "password": "pw",
        "name": "",
        "rotate_url": "",
    }


def test_curl_line():
    line = "curl -x http://log:pass@5.6.7.8:8080 https://i.pn"
    assert parse_proxy_line(line) == {
        "scheme": "http",
        "ip": "5.6.7.8",
        "port": "8080",
        "login": "log",
        "password": "pass",
        "name": "",
        "rotate_url": "",
    }


def test_bare_ip_port():
    assert parse_proxy_line("9.9.9.9:3128") == {
        "scheme": "",
        "ip": "9.9.9.9",
        "port": "3128",
        "login": "",
        "password": "",
        "name": "",
        "rotate_url": "",
    }


def test_garbage_returns_none():
    assert parse_proxy_line("not a proxy at all") is None
    assert parse_proxy_line("host:notaport") is None
    assert parse_proxy_line("curl --help") is None


def test_empty_returns_none():
    assert parse_proxy_line("") is None
    assert parse_proxy_line("   ") is None
