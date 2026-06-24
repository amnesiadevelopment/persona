from src.utils.proxy_parser import (
    build_proxy_url,
    parse_proxy_server,
    split_proxy_url,
)


def test_build_no_auth():
    assert build_proxy_url("socks5", "1.2.3.4", "1080") == "socks5://1.2.3.4:1080"


def test_build_user_pass():
    assert (
        build_proxy_url("http", "h.com", "8080", "u", "p")
        == "http://u:p@h.com:8080"
    )


def test_build_user_only():
    assert build_proxy_url("socks5", "h", "1080", "u") == "socks5://u@h:1080"


def test_split_full():
    got = split_proxy_url("socks5://u:p@1.2.3.4:1080")
    assert got == {
        "scheme": "socks5",
        "host": "1.2.3.4",
        "port": "1080",
        "username": "u",
        "password": "p",
    }


def test_split_no_auth():
    got = split_proxy_url("http://h.com:8080")
    assert got["scheme"] == "http"
    assert got["host"] == "h.com"
    assert got["port"] == "8080"
    assert got["username"] == ""
    assert got["password"] == ""


def test_split_empty():
    assert split_proxy_url("")["scheme"] == "socks5"


def test_roundtrip():
    url = "socks5://user:pass@10.0.0.1:1080"
    f = split_proxy_url(url)
    rebuilt = build_proxy_url(
        f["scheme"], f["host"], f["port"], f["username"], f["password"]
    )
    assert rebuilt == url


def test_built_url_is_valid_proxy_server():
    url = build_proxy_url("socks5", "1.2.3.4", "1080", "u", "p")
    assert parse_proxy_server(url) == "socks5://1.2.3.4:1080"
