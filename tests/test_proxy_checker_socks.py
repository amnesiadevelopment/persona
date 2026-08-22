"""The proxy geo check must speak a real SOCKS handshake for socks* schemes.

aiohttp's `proxy=` implements HTTP CONNECT only and silently ignores the URL
scheme, so `check_proxy` used to send `CONNECT ipwho.is:443` at a SOCKS5 server
that was waiting for a `\\x05` greeting. It never answered, the check failed
permanently for persona's DEFAULT scheme, the proxy kept an empty
country/timezone, and the launcher then fell back to the operator's REAL host
timezone inside a proxied profile (services/browser/process.py:306-314).

The assertion that would have caught it is the one below on the FIRST BYTES the
proxy receives: \\x05..., not `CONNECT `.
"""
import asyncio
import datetime
import json
import socket
import ssl
import struct
import threading
import time

import pytest

from src.utils import proxy_checker
from src.utils.validation import PROXY_SCHEMES, validate_proxy_format

#: Derived from the validator's own tuple, never retyped. The first attempt at
#: this fix hard-coded ["socks5", "socks5h", "socks4", "socks4a"] — covering a
#: socks4a the validator REJECTS while omitting the socks4h it ACCEPTS — so the
#: suite was green while socks4h proxies still got an HTTP CONNECT.
_VALIDATOR_SOCKS_SCHEMES = [s for s in PROXY_SCHEMES if s.startswith("socks")]

_GEO_PAYLOAD = {
    "success": True,
    "ip": "203.0.113.7",
    "country": "Poland",
    "country_code": "PL",
    "timezone": {"id": "Europe/Warsaw"},
    "latitude": 52.23,
    "longitude": 21.01,
}


def _listener() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client went away mid-handshake")
        buf += chunk
    return buf


# --------------------------------------------------------------------------
# The regression assertion: what actually goes on the wire.
# --------------------------------------------------------------------------


def test_socks5_check_opens_with_a_socks_greeting_not_http_connect(monkeypatch):
    seen: dict[str, bytes] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            seen["first"] = conn.recv(512)
        except OSError:
            seen["first"] = b""
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            proxy_checker.check_proxy(
                f"socks5://user:pass@127.0.0.1:{port}", timeout=5, allow_loopback=True
            )
        )
    finally:
        thread.join(15)
        srv.close()

    first = seen.get("first", b"")
    assert first, "the proxy port received nothing at all"
    # THE regression assertion: SOCKS5 version byte, and emphatically not the
    # HTTP CONNECT aiohttp was emitting.
    assert first[:1] == b"\x05", f"expected a SOCKS5 greeting, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT ")
    # Same root cause, secondary effect: aiohttp's pre-TLS CONNECT shipped the
    # credentials as a cleartext Proxy-Authorization header. A SOCKS handshake
    # never does.
    assert b"Proxy-Authorization" not in first
    assert b"user:pass" not in first

    # The listener hangs up after the greeting, so the check must FAIL — and
    # fail without ever naming the endpoint (test_proxy_log_privacy's rule).
    assert result[0] is False
    assert "127.0.0.1" not in result[1] and str(port) not in result[1]


def test_http_scheme_still_uses_the_aiohttp_connect_path(monkeypatch):
    """http:// / https:// proxies must be untouched by the SOCKS branch."""
    seen: dict[str, bytes] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            seen["first"] = conn.recv(512)
        except OSError:
            seen["first"] = b""
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            proxy_checker.check_proxy(
                f"http://user:pass@127.0.0.1:{port}", timeout=5, allow_loopback=True
            )
        )
    finally:
        thread.join(15)
        srv.close()

    assert seen.get("first", b"").startswith(b"CONNECT ")
    assert result[0] is False


# --------------------------------------------------------------------------
# End to end: a reachable socks5:// proxy yields populated geo.
# --------------------------------------------------------------------------


def _self_signed(tmp_path, hostname: str = "ipwho.is"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "geo-cert.pem"
    keyfile = tmp_path / "geo-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


def test_reachable_socks5_proxy_returns_geo(tmp_path, monkeypatch):
    """A real SOCKS5 listener (auth + CONNECT) that then terminates TLS as the
    geo endpoint. Proves the whole path: handshake, TLS INSIDE the tunnel, HTTP
    parse, _validate_geo — country_code and timezone come back populated, which
    is exactly what stops _proxy_timezone falling back to the host zone."""
    certfile, keyfile = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))

    seen: dict[str, object] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            seen["greeting"] = greeting
            _recv_exactly(conn, greeting[1])          # the offered methods
            conn.sendall(b"\x05\x02")                 # demand user/pass auth
            _recv_exactly(conn, 1)                    # auth sub-negotiation ver
            user = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            password = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            seen["auth"] = (user, password)
            conn.sendall(b"\x01\x00")                 # auth ok
            _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
            assert atyp == 0x03                       # domain, not a local resolve
            host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            tport = struct.unpack(">H", _recv_exactly(conn, 2))[0]
            seen["target"] = (host.decode(), tport)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")

            tls = server_ctx.wrap_socket(conn, server_side=True)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                request += chunk
            seen["request"] = request
            body = json.dumps(_GEO_PAYLOAD).encode()
            tls.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            tls.close()
        except Exception as exc:                       # surfaced via the asserts
            seen["error"] = repr(exc)
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: client_ctx)
    try:
        ok, message, code, country, ip, tz, lat, lon = (
            proxy_checker.check_proxy_detailed_sync(
                f"socks5://user:pass@127.0.0.1:{port}", timeout=10
            )
        )
    finally:
        thread.join(20)
        srv.close()

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert country == "Poland"
    assert (lat, lon) == (52.23, 21.01)
    assert ip == "203.0.113.7"
    # The activity-log message still carries the country, never the exit IP.
    assert "203.0.113.7" not in message

    assert seen["greeting"][:1] == b"\x05"
    assert seen["auth"] == (b"user", b"pass")
    assert seen["target"] == ("ipwho.is", 443)
    assert b"GET / HTTP/1.1" in seen["request"]
    assert b"Host: ipwho.is" in seen["request"]


def test_socks5_geo_is_dropped_when_the_endpoint_lies(tmp_path, monkeypatch):
    """_validate_geo still sanitizes what comes back through the tunnel."""
    certfile, keyfile = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])
            conn.sendall(b"\x05\x00")                 # no auth needed
            _recv_exactly(conn, 4)
            _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            _recv_exactly(conn, 2)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            tls = server_ctx.wrap_socket(conn, server_side=True)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                request += chunk
            payload = dict(_GEO_PAYLOAD, country_code="XYZ", timezone="not-a-zone")
            body = json.dumps(payload).encode()
            tls.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            tls.close()
        except Exception:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: client_ctx)
    try:
        ok, _message, code, _country, _ip, tz, _lat, _lon = (
            proxy_checker.check_proxy_detailed_sync(f"socks5://127.0.0.1:{port}", timeout=10)
        )
    finally:
        thread.join(20)
        srv.close()

    assert ok is True
    assert code == ""   # 3 letters -> dropped
    assert tz == ""     # no "/" -> dropped


@pytest.mark.parametrize("scheme", sorted(_VALIDATOR_SOCKS_SCHEMES))
def test_every_socks_scheme_avoids_http_connect(scheme, monkeypatch):
    seen: dict[str, bytes] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            seen["first"] = conn.recv(512)
        except OSError:
            seen["first"] = b""
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        asyncio.run(
            proxy_checker.check_proxy(
                f"{scheme}://127.0.0.1:{port}", timeout=5, allow_loopback=True
            )
        )
    finally:
        thread.join(15)
        srv.close()

    first = seen.get("first", b"")
    assert first[:1] in (b"\x04", b"\x05"), f"{scheme}: got {first[:16]!r}"
    assert not first.startswith(b"CONNECT ")


def test_scheme_set_cannot_drift_from_the_validator():
    """Every socks scheme the validator ACCEPTS must take the SOCKS path.

    The guard that failed the first time was a hand-written set. This asserts
    the property directly against validate_proxy_format, so adding a scheme to
    the validator without teaching the checker about it fails here.
    """
    for scheme in _VALIDATOR_SOCKS_SCHEMES:
        assert validate_proxy_format(f"{scheme}://gate.example.com:1080") == (True, "")
        assert proxy_checker._is_socks_scheme(scheme), (
            f"{scheme} is accepted by the validator but would be sent to aiohttp, "
            "which speaks HTTP CONNECT only"
        )
    # ...and the HTTP schemes emphatically must not.
    assert not proxy_checker._is_socks_scheme("http")
    assert not proxy_checker._is_socks_scheme("https")


def test_socks4h_specifically_does_not_emit_http_connect(monkeypatch):
    """socks4h is the scheme the first fix missed: accepted by validation.py,
    absent from the hand-written set, so it still sent `CONNECT ipwho.is:443`
    at a SOCKS server — leaving geo empty and the launcher on the operator's
    real host timezone. Reachable via the API (routes/profiles.py gates on
    validate_proxy_format), not just the dialog's dropdown."""
    seen: dict[str, bytes] = {}
    srv, port = _listener()

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            seen["first"] = conn.recv(512)
        except OSError:
            seen["first"] = b""
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            proxy_checker.check_proxy(
                f"socks4h://127.0.0.1:{port}", timeout=5, allow_loopback=True
            )
        )
    finally:
        thread.join(15)
        srv.close()

    first = seen.get("first", b"")
    assert first, "the proxy port received nothing at all"
    assert first[:1] == b"\x04", f"expected a SOCKS4 request, got {first[:16]!r}"
    assert not first.startswith(b"CONNECT ")
    assert result[0] is False
    assert "127.0.0.1" not in result[1] and str(port) not in result[1]


def test_geo_body_without_content_length_is_read_to_eof(tmp_path, monkeypatch):
    """A 200 with neither Content-Length nor chunked framing — legal under the
    `Connection: close` this client requests, and what a CDN in front of
    ipwho.is may well send.

    The body is delivered in two segments. `StreamReader.read(n)` returns as
    soon as ANY data is buffered, so the first implementation kept only segment
    one, the JSON failed to parse, and the check fell into the empty-geo arm —
    reopening the real-timezone leak intermittently, which is strictly harder to
    diagnose than the permanent failure this ticket started from.
    """
    certfile, keyfile = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))
    srv, port = _listener()
    seen: dict[str, object] = {}

    body = json.dumps(_GEO_PAYLOAD).encode()
    split = len(body) // 2
    assert split > 0

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])
            conn.sendall(b"\x05\x00")                 # no auth
            _recv_exactly(conn, 4)
            _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            _recv_exactly(conn, 2)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            tls = server_ctx.wrap_socket(conn, server_side=True)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                request += chunk
            # NO Content-Length, NO Transfer-Encoding: the body is framed only
            # by the connection close.
            tls.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n\r\n"
            )
            tls.sendall(body[:split])
            time.sleep(0.2)          # force a second, separate TLS record
            tls.sendall(body[split:])
            tls.close()              # close_notify -> EOF for the reader
        except Exception as exc:
            seen["error"] = repr(exc)
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: client_ctx)
    try:
        ok, message, code, _country, _ip, tz, _lat, _lon = (
            proxy_checker.check_proxy_detailed_sync(
                f"socks5://127.0.0.1:{port}", timeout=10
            )
        )
    finally:
        thread.join(20)
        srv.close()

    assert ok is True, f"{message} / server: {seen.get('error')}"
    # The whole point: geo survives the split body, so the launcher never falls
    # back to the operator's real host timezone.
    assert code == "PL"
    assert tz == "Europe/Warsaw"


async def _drain_body(headers: dict, *segments: bytes) -> bytes:
    reader = asyncio.StreamReader()

    async def feed() -> None:
        for segment in segments:
            reader.feed_data(segment)
            await asyncio.sleep(0.01)
        reader.feed_eof()

    task = asyncio.ensure_future(feed())
    try:
        return await proxy_checker._read_http_body(reader, headers)
    finally:
        await task


def test_read_http_body_joins_segments_and_caps_size():
    """Unit-level companion to the end-to-end test above."""
    body = asyncio.run(_drain_body({}, b'{"a": 1,', b' "b": 2}'))
    assert json.loads(body) == {"a": 1, "b": 2}

    # The cap is still enforced, and enforced DURING the loop rather than only
    # after it — an unbounded read of an attacker-influenced body is a memory DoS.
    oversized = b"x" * (proxy_checker._MAX_GEO_BODY // 2 + 1)
    with pytest.raises(ValueError):
        asyncio.run(_drain_body({}, oversized, oversized))


def test_socks_check_never_reports_success_without_a_proxy(monkeypatch):
    """The AIOHTTP_AVAILABLE rule generalised: no path may record ok=True
    without real geo, because that writes last_check_ok=True with empty fields
    and ERASES a proxy's known-good country/timezone."""
    srv, port = _listener()
    srv.close()  # nothing is listening -> connection refused
    result = asyncio.run(
        proxy_checker.check_proxy(f"socks5://127.0.0.1:{port}", timeout=5, allow_loopback=True)
    )
    assert result[0] is False
    assert result[2:6] == ("", "", "", "")
    assert "127.0.0.1" not in result[1] and str(port) not in result[1]


def test_socks_geo_body_that_is_a_json_array_reports_the_specific_failure(monkeypatch):
    """A 200 whose SOCKS geo body is a valid JSON ARRAY must reach check_proxy's
    documented "Proxy geo lookup failed" arm, not the generic catch-all.

    _http_get_json is SHARED between this geo probe and the release-metadata
    fetch, and the latter legitimately receives a top-level array — so the
    helper returns `dict | list | None`. The geo caller must narrow that back
    to `dict | None`, because check_proxy's 200-handler reaches its specific
    message only via None: a list sails past the `data is None` check and then
    trips `.get()` into the blanket `except`, which reports the generic
    "Proxy check failed" for a case the code deliberately wrote a message for.

    No TLS here on purpose: _ssl_context is patched to None so the tunnel stays
    cleartext. The contract under test is the RETURN TYPE, not the transport,
    and this keeps the test green in containers without `cryptography`.
    """
    srv, port = _listener()
    seen: dict[str, object] = {}
    body = b'[{"success": true, "country_code": "PL"}]'   # an ARRAY, not an object

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            _recv_exactly(conn, greeting[1])
            conn.sendall(b"\x05\x00")                     # no auth
            _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
            seen["atyp"] = atyp
            _recv_exactly(conn, _recv_exactly(conn, 1)[0])
            _recv_exactly(conn, 2)
            conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            conn.close()
        except Exception as exc:
            seen["error"] = repr(exc)
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: None)
    try:
        result = asyncio.run(
            proxy_checker.check_proxy(
                f"socks5://127.0.0.1:{port}", timeout=10, allow_loopback=True
            )
        )
    finally:
        thread.join(20)
        srv.close()

    ok, message = result[0], result[1]
    assert ok is False, f"an array body must never read as a good geo check"
    # THE assertion: the specific, documented message — not the generic arm the
    # AttributeError falls into.
    assert message == "Proxy geo lookup failed", (
        f"got {message!r}; server: {seen.get('error')}"
    )
    assert message != "Proxy check failed"
    # Failing closed must still leave the geo fields empty rather than half-set.
    assert result[2:6] == ("", "", "", "")
    # And the endpoint is still never named in an operator-visible string.
    assert "127.0.0.1" not in message and str(port) not in message
