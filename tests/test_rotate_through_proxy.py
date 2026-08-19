"""The provider rotate request must travel THROUGH the proxy it is rotating.

`_fetch_rotate_url` used to be a bare `urllib.request.urlopen` on the operator's
REAL IP, carrying `User-Agent: persona`. One rotation disclosed three things at
once:

  1. that this real address controls that proxy account, timestamped, to the
     provider and to anyone observing the operator's traffic;
  2. a DNS query from the operator's REAL resolver for the provider's hostname
     (urlopen resolves locally) — the exact leak class the SOCKS client already
     fails closed for on purpose (proxy_checker._socks4_connect);
  3. a self-identifying `persona` User-Agent.

The assertions that would have caught it are the ones below on what actually
reaches the wire: the request arrives at a SOCKS listener, the rotate host is
carried as a DOMAIN NAME (atyp 0x03) rather than pre-resolved, no `persona`
token is present, and with no usable transport NO SOCKET IS OPENED AT ALL.
"""
import asyncio
import datetime
import inspect
import socket
import ssl
import struct
import threading

import pytest

from src.services.proxy import service as service_mod
from src.utils import proxy_checker

ROTATE_URL = "https://rotate.example/refresh/1?apiKey=k"


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


def _self_signed(tmp_path, hostname: str = "rotate.example"):
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
    certfile = tmp_path / "rotate-cert.pem"
    keyfile = tmp_path / "rotate-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


def _rotate_through_socks5(tmp_path, monkeypatch, response: bytes):
    """Stand up a real SOCKS5 listener that terminates TLS as the rotate
    endpoint, drive `_fetch_rotate_url` through it, and report what the wire saw.

    Returns (result, seen) where seen carries the SOCKS greeting, the credentials
    from the handshake, the CONNECT target, the atyp byte and the raw HTTP
    request that came out the far end of the tunnel.
    """
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
            seen["atyp"] = atyp
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
            tls.sendall(response)
            tls.close()
        except Exception as exc:                       # surfaced via the asserts
            seen["error"] = repr(exc)
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: client_ctx)
    try:
        result = service_mod._fetch_rotate_url(
            ROTATE_URL, f"socks5://user:pass@127.0.0.1:{port}", 10
        )
    finally:
        thread.join(20)
        srv.close()
    return result, seen


_PLAIN_TEXT_200 = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 20\r\n"
    b"Connection: close\r\n\r\n"
    b"rotation successful."
)


# --------------------------------------------------------------------------
# THE regression assertion: the request goes through the tunnel, and the
# rotate host is resolved at the EXIT, never on the operator's resolver.
# --------------------------------------------------------------------------


def test_rotate_request_travels_through_the_socks_proxy(tmp_path, monkeypatch):
    (ok, detail), seen = _rotate_through_socks5(
        tmp_path, monkeypatch, _PLAIN_TEXT_200
    )

    assert ok is True, f"{detail} / server: {seen.get('error')}"
    # It arrived at the PROXY, having spoken a real SOCKS5 handshake...
    assert seen["greeting"][:1] == b"\x05"
    assert seen["auth"] == (b"user", b"pass")
    # ...and the rotate host was carried as a DOMAIN NAME. atyp 0x03 is the
    # whole point: a pre-resolved IP here would mean the operator's real
    # resolver had already been asked for the provider's hostname.
    assert seen["atyp"] == 0x03
    assert seen["target"] == ("rotate.example", 443)
    # The rotate request itself came out the far end of the tunnel, intact,
    # with its query string (the API key) preserved.
    assert b"GET /refresh/1?apiKey=k HTTP/1.1" in seen["request"]
    assert b"Host: rotate.example" in seen["request"]


def test_plain_text_rotate_response_is_a_success(tmp_path, monkeypatch):
    """A rotate endpoint commonly answers 200 with plain text, not JSON.

    Reusing the geo probe's JSON-only reader would turn a perfectly successful
    rotation into a JSONDecodeError; the status-only path must not parse the
    body at all.
    """
    (ok, detail), seen = _rotate_through_socks5(
        tmp_path, monkeypatch, _PLAIN_TEXT_200
    )
    assert (ok, detail) == (True, "HTTP 200"), f"server: {seen.get('error')}"


def test_rotate_request_carries_no_persona_token(tmp_path, monkeypatch):
    """`User-Agent: persona` self-identified the tool to the provider."""
    (_ok, _detail), seen = _rotate_through_socks5(
        tmp_path, monkeypatch, _PLAIN_TEXT_200
    )
    captured_request = seen["request"]
    assert captured_request, f"nothing reached the endpoint: {seen.get('error')}"
    assert b"persona" not in captured_request


def test_error_status_through_the_tunnel_fails(tmp_path, monkeypatch):
    """The (status < 400, "HTTP <status>") contract the caller reports on."""
    (ok, detail), seen = _rotate_through_socks5(
        tmp_path,
        monkeypatch,
        b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
        b"Connection: close\r\n\r\n",
    )
    assert (ok, detail) == (False, "HTTP 503"), f"server: {seen.get('error')}"


# --------------------------------------------------------------------------
# No transport -> no request. Not "a direct request instead".
# --------------------------------------------------------------------------


class _SocketSpy:
    """Records every INET/INET6 socket constructed. asyncio's own self-pipe is
    an AF_UNIX socketpair, so it is correctly not counted as egress."""

    def __init__(self, monkeypatch):
        self.opened: list[tuple] = []
        real = socket.socket

        class Spy(real):  # type: ignore[misc,valid-type]
            def __init__(inner, family=socket.AF_INET, type=socket.SOCK_STREAM,
                         *args, **kwargs):
                if family in (socket.AF_INET, socket.AF_INET6):
                    self.opened.append((family, type))
                super().__init__(family, type, *args, **kwargs)

        monkeypatch.setattr(socket, "socket", Spy)


@pytest.mark.parametrize(
    "proxy_url",
    ["", "not-a-proxy", "socks5://host-without-a-port"],
    ids=["empty", "unparseable", "no-port"],
)
def test_no_transport_sends_nothing_and_opens_no_socket(monkeypatch, proxy_url):
    spy = _SocketSpy(monkeypatch)

    ok, detail = service_mod._fetch_rotate_url(ROTATE_URL, proxy_url, 5)

    assert ok is False
    assert "no proxy transport" in detail
    # The point of the whole ticket: with nowhere safe to send it, the request
    # is NOT SENT. Not sent directly, not sent at all.
    assert spy.opened == [], f"a socket was opened anyway: {spy.opened}"


def test_fetch_rotate_url_cannot_be_called_without_a_transport():
    """`proxy_url` has NO DEFAULT — a default would let a future caller silently
    reintroduce the direct, real-IP path by simply omitting the argument."""
    params = inspect.signature(service_mod._fetch_rotate_url).parameters
    assert params["proxy_url"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        service_mod._fetch_rotate_url(ROTATE_URL)  # type: ignore[call-arg]


def test_missing_aiohttp_with_an_http_proxy_fails_closed(monkeypatch):
    """aiohttp absent + a non-SOCKS proxy must FAIL, never fall back to a direct
    send — the same fail-closed rule check_proxy already applies."""
    monkeypatch.setattr(proxy_checker, "AIOHTTP_AVAILABLE", False)
    spy = _SocketSpy(monkeypatch)

    ok, detail = service_mod._fetch_rotate_url(ROTATE_URL, "http://u:p@1.2.3.4:8080", 5)

    assert ok is False
    assert "not sent" in detail
    assert spy.opened == [], f"a socket was opened anyway: {spy.opened}"


def test_non_http_rotate_scheme_is_still_refused():
    """The http(s)-only guard stays: it blocks file:// / data://."""
    for bad in ("file:///etc/passwd", "data:text/plain,x", "ftp://host/x"):
        ok, detail = service_mod._fetch_rotate_url(bad, "socks5://u:p@h:1080", 5)
        assert ok is False
        assert "http or https" in detail


def test_the_direct_urlopen_path_is_gone():
    """AC1, asserted rather than grepped: the module must not even import the
    machinery that made a direct request possible."""
    source = inspect.getsource(service_mod)
    assert "urllib.request" not in source
    assert "urlopen" not in source
