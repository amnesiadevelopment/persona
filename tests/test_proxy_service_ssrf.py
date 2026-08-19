"""rotate_url is fetched from an untrusted pasted proxy string — it must only
reach http(s), never file:// / ftp:// / data: (local-file read / SSRF)."""
import pytest

from src.services.proxy.service import _fetch_rotate_url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "ftp://internal/secret",
    "data:text/plain;base64,QQ==",
    "gopher://x",
])
def test_rotate_url_rejects_non_http(url):
    # The scheme guard runs BEFORE the transport is used, so the proxy argument
    # is immaterial here — but it is required (PS-9 removed its default so the
    # direct, real-IP path cannot be reintroduced by omitting it).
    ok, msg = _fetch_rotate_url(url, "socks5://u:p@127.0.0.1:1", timeout=1)
    assert ok is False
    assert "http" in msg.lower()


def test_rotate_url_allows_http_scheme_shape():
    # a well-formed http URL passes the scheme guard (the fetch itself will fail
    # fast on an unroutable host, which is fine — we only assert it's not the
    # scheme rejection).
    ok, msg = _fetch_rotate_url(
        "http://127.0.0.1:1/nope", "socks5://u:p@127.0.0.1:1", timeout=1
    )
    assert "must be http" not in msg
