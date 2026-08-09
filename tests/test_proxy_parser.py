from src.utils.proxy_parser import (
    build_proxy_url,
    parse_proxy,
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


import pytest


@pytest.mark.parametrize("pw", ["p/ss", "p#ss", "p?ss", "p@ss", "p:ss", "p ss", "пароль"])
def test_special_char_password_round_trips_and_parses(pw):
    # audit6 #8: a password with URL-reserved characters must survive build ->
    # split (shows the real password) AND build -> launch-parse (host stays
    # intact, creds decode). Assembling raw creds broke urlparse: `/#?` emptied
    # the host (→ fail-closed no-proxy), `@` was rejected at save.
    url = build_proxy_url("socks5", "1.2.3.4", "1080", "user", pw)

    # split round-trips to the real credential
    f = split_proxy_url(url)
    assert f["host"] == "1.2.3.4"
    assert f["port"] == "1080"
    assert f["username"] == "user"
    assert f["password"] == pw

    # the launch parser sees an intact host:port and the DECODED credentials
    cfg = parse_proxy(url)
    assert cfg is not None, f"launcher rejected the built url for pw={pw!r}"
    assert cfg["server"] == "socks5://1.2.3.4:1080"
    assert cfg["username"] == "user"
    assert cfg["password"] == pw


def test_special_char_password_reaches_socks_bridge_decoded():
    # The chromium credential-stripping bridge must send the DECODED password to
    # the upstream SOCKS5 proxy, not the %XX form (audit6 #8).
    from src.services.proxy.bridge import ProxyBridge

    url = build_proxy_url("socks5", "1.2.3.4", "1080", "user", "p/s#s?x")
    b = ProxyBridge(url)
    assert b._up_user == "user"
    assert b._up_pass == "p/s#s?x"
