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


def _rotate_through_socks5_seq(tmp_path, monkeypatch, responses: list[bytes]):
    """Stand up a real SOCKS5 listener that terminates TLS as the rotate
    endpoint, drive `_fetch_rotate_url` through it, and report what the wire saw
    ON EVERY CONNECTION.

    Serves one scripted response per connection, in order; once the script is
    exhausted the LAST response repeats, which is what makes a self-referential
    redirect (the hostile case for a hop bound) expressible as a single entry.

    Every redirect hop opens a FRESH tunnel — that is the property being
    asserted, not an implementation detail: it is what keeps each hop's target
    resolved at the exit rather than on the operator's resolver.

    Returns (result, seen) where seen is one dict PER CONNECTION, each carrying
    the SOCKS greeting, the handshake credentials, the CONNECT target, the atyp
    byte and the raw HTTP request that came out the far end of that tunnel.
    """
    certfile, keyfile = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))

    seen: list[dict[str, object]] = []
    srv, port = _listener()
    srv.listen(8)
    # Poll rather than block: the client stops connecting when it stops
    # following, and the server cannot know in advance which hop was the last.
    srv.settimeout(0.25)
    stop = threading.Event()

    def serve() -> None:
        index = 0
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            record: dict[str, object] = {}
            seen.append(record)
            response = responses[min(index, len(responses) - 1)]
            index += 1
            conn.settimeout(10)
            try:
                greeting = _recv_exactly(conn, 2)
                record["greeting"] = greeting
                _recv_exactly(conn, greeting[1])          # the offered methods
                conn.sendall(b"\x05\x02")                 # demand user/pass auth
                _recv_exactly(conn, 1)                    # auth sub-negotiation ver
                user = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
                password = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
                record["auth"] = (user, password)
                conn.sendall(b"\x01\x00")                 # auth ok
                _ver, _cmd, _rsv, atyp = _recv_exactly(conn, 4)
                record["atyp"] = atyp
                host = _recv_exactly(conn, _recv_exactly(conn, 1)[0])
                tport = struct.unpack(">H", _recv_exactly(conn, 2))[0]
                record["target"] = (host.decode(), tport)
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")

                tls = server_ctx.wrap_socket(conn, server_side=True)
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                record["request"] = request
                tls.sendall(response)
                tls.close()
            except Exception as exc:                       # surfaced via asserts
                record["error"] = repr(exc)
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
        stop.set()
        thread.join(20)
        srv.close()
    return result, seen


def _rotate_through_socks5(tmp_path, monkeypatch, response: bytes):
    """Single-response form: the first (and only) connection's record."""
    result, seen = _rotate_through_socks5_seq(tmp_path, monkeypatch, [response])
    return result, (seen[0] if seen else {})


_PLAIN_TEXT_200 = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: 20\r\n"
    b"Connection: close\r\n\r\n"
    b"rotation successful."
)

# The aiohttp branch's rotate target is http:// so the proxy is spoken to in
# absolute-form (no CONNECT, no TLS) — the shape a real HTTP proxy sees.
ROTATE_URL_HTTP = "http://rotate.example/refresh/1?apiKey=k"


def _rotate_through_http_proxy_seq(monkeypatch, responses: list[bytes]):
    """The aiohttp-branch twin of `_rotate_through_socks5_seq`.

    Stands up a real forward HTTP proxy — aiohttp sends absolute-form
    (`GET http://rotate.example/... HTTP/1.1`) to it, so the proxy answers
    directly and no CONNECT or TLS is involved. Same scripted-response contract
    as the SOCKS harness: one response per connection, in order, with the last
    repeating so a redirect loop is a single-element list.

    This exists because the aiohttp redirect branch was previously pinned only
    by a source-level grep. Nothing drove it end-to-end, which is exactly how
    its bound came to differ from the SOCKS branch's by one hop without any
    test noticing.
    """
    seen: list[dict[str, object]] = []
    srv, port = _listener()
    srv.listen(16)
    srv.settimeout(0.25)
    stop = threading.Event()

    def serve() -> None:
        index = 0
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            record: dict[str, object] = {}
            seen.append(record)
            response = responses[min(index, len(responses) - 1)]
            index += 1
            conn.settimeout(10)
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                record["request"] = request
                conn.sendall(response)
            except Exception as exc:                       # surfaced via asserts
                record["error"] = repr(exc)
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = service_mod._fetch_rotate_url(
            ROTATE_URL_HTTP, f"http://user:pass@127.0.0.1:{port}", 10
        )
    finally:
        stop.set()
        thread.join(20)
        srv.close()
    return result, seen


def _chain(hops: int) -> list[bytes]:
    """`hops` redirects in a row, then a 200 — a chain of exactly that length."""
    return [_redirect(f"/hop{i + 1}") for i in range(hops)] + [_PLAIN_TEXT_200]


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
    """The (status < 300, "HTTP <status>") contract the caller reports on."""
    (ok, detail), seen = _rotate_through_socks5(
        tmp_path,
        monkeypatch,
        b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
        b"Connection: close\r\n\r\n",
    )
    assert (ok, detail) == (False, "HTTP 503"), f"server: {seen.get('error')}"


# --------------------------------------------------------------------------
# A 3xx must be FOLLOWED, or else reported as a failure. Never counted as a
# success on the strength of `status < 400`.
#
# The direct urlopen this path replaced followed redirects for free. Dropping
# that while keeping the `< 400` comparison meant an unfollowed 302 wrote
# "rotate endpoint OK (HTTP 302)" to the operator's activity log for a rotation
# that never happened: the redirect target — the request that would ACTUALLY
# have rotated the proxy — was never fetched. A silent false success is worse
# than the honest failure the rest of this path is built around.
# --------------------------------------------------------------------------


def _redirect(location: str, status: bytes = b"302 Found") -> bytes:
    return (
        b"HTTP/1.1 " + status + b"\r\n"
        b"Location: " + location.encode() + b"\r\n"
        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )


def test_redirected_rotate_is_followed_through_the_tunnel(tmp_path, monkeypatch):
    """THE regression assertion for this rework.

    A rotate endpoint behind a 302 must end up reporting the status of the
    request that actually rotated the proxy — and the followed hop must travel
    through the SAME tunnel, not leak onto the real IP once redirected.
    """
    (ok, detail), seen = _rotate_through_socks5_seq(
        tmp_path,
        monkeypatch,
        [_redirect("https://rotate.example/final"), _PLAIN_TEXT_200],
    )

    assert (ok, detail) == (True, "HTTP 200"), f"server: {seen}"
    # Two connections: each hop opens a FRESH tunnel through the proxy.
    assert len(seen) == 2, f"expected the redirect to be followed: {seen}"
    # The second request went to the redirect TARGET...
    assert b"GET /final HTTP/1.1" in seen[1]["request"]
    # ...and it too was carried as a domain name through the SOCKS proxy, so the
    # redirect target was never resolved on the operator's real resolver either.
    assert seen[1]["atyp"] == 0x03
    assert seen[1]["target"] == ("rotate.example", 443)
    assert seen[1]["auth"] == (b"user", b"pass")


def test_relative_location_resolves_against_the_url_it_came_from(
    tmp_path, monkeypatch
):
    """`Location: /final` is the common provider shape and is not a URL."""
    (ok, detail), seen = _rotate_through_socks5_seq(
        tmp_path, monkeypatch, [_redirect("/final"), _PLAIN_TEXT_200]
    )

    assert (ok, detail) == (True, "HTTP 200"), f"server: {seen}"
    assert len(seen) == 2, f"expected the redirect to be followed: {seen}"
    assert b"GET /final HTTP/1.1" in seen[1]["request"]
    assert seen[1]["target"] == ("rotate.example", 443)


def test_a_3xx_that_cannot_be_followed_is_not_a_success(tmp_path, monkeypatch):
    """A 302 with no Location has nowhere to go, so nothing rotated.

    This is why the verdict is `< 300` and not `< 400`: an unfollowable 3xx
    still reaches the comparison, and under the old bound it would have been
    reported to the operator as a successful rotation.
    """
    (ok, detail), seen = _rotate_through_socks5(
        tmp_path,
        monkeypatch,
        b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
    )

    assert (ok, detail) == (False, "HTTP 302"), f"server: {seen.get('error')}"


def test_redirect_to_a_non_http_scheme_is_refused(tmp_path, monkeypatch):
    """The caller's http(s) guard only ever sees the ORIGINAL url.

    A `Location: file:///etc/passwd` (or a scheme downgrade) must not ride
    through on the second hop, and the refusal must not read as success.
    """
    (ok, detail), seen = _rotate_through_socks5_seq(
        tmp_path, monkeypatch, [_redirect("file:///etc/passwd")]
    )

    assert (ok, detail) == (False, "HTTP 302"), f"server: {seen}"
    # It was never followed: the one connection is the original request.
    assert len(seen) == 1, f"the refused target was fetched anyway: {seen}"


def test_a_redirect_loop_is_bounded_and_fails(tmp_path, monkeypatch):
    """A self-referential Location is the hostile case: unbounded, it hangs.

    The chain is capped and the verdict is a failure — the operator loses a
    rotate attempt and sees why.

    The reason names the HOP BUDGET, not the last status code. Exhausting the
    budget is one condition, so it gets one message on both transports: the
    SOCKS loop raises _TooManyRotateRedirects where aiohttp raises
    TooManyRedirects, and they share an except arm. Reporting `HTTP 302` here
    (as this path used to) told the operator the endpoint had answered with a
    redirect, when the real fact is that persona gave up following one.
    """
    (ok, detail), seen = _rotate_through_socks5_seq(
        tmp_path, monkeypatch, [_redirect("https://rotate.example/loop")]
    )

    assert ok is False, f"a redirect loop reported success: {detail}"
    assert detail == "rotate request failed: too many redirects"
    # Bounded: the original request plus at most _MAX_ROTATE_REDIRECTS hops.
    assert len(seen) == proxy_checker._MAX_ROTATE_REDIRECTS + 1, (
        f"hop bound not enforced: {len(seen)} connections"
    )


@pytest.mark.parametrize("hops", [0, 1, 4, 5, 6, 7])
def test_both_transports_agree_at_every_chain_length(tmp_path, monkeypatch, hops):
    """One function must not have two redirect behaviours chosen by transport.

    socks5 is persona's DEFAULT scheme, so a divergent bound would leave most
    operators on the weaker path — the drift this module exists to prevent.

    This assertion is BEHAVIOURAL on purpose. Its predecessor grepped
    `inspect.getsource` for `max_redirects=_MAX_ROTATE_REDIRECTS`, which any
    SOCKS-side bound whatsoever satisfies — so it passed while the two branches
    differed by a hop, and kept passing when the SOCKS loop was deliberately
    sabotaged to `+ 99`. A test named for a property it does not test is worse
    than no test: it is what the next maintainer will trust. This one drives a
    chain of exactly `hops` redirects through EACH transport against real
    listeners and requires the same verdict, so it survives a refactor that
    stops using the literal — and it fails if either bound moves.

    The parametrization straddles the boundary deliberately: `_MAX_ROTATE_REDIRECTS`
    follows must succeed and one more must fail, on both. That is where the
    branches used to part company, because `max_redirects=N` fails ON the N-th
    redirect while the SOCKS loop permits N follows.
    """
    socks_result, socks_seen = _rotate_through_socks5_seq(
        tmp_path, monkeypatch, _chain(hops)
    )
    http_result, http_seen = _rotate_through_http_proxy_seq(monkeypatch, _chain(hops))

    assert socks_result == http_result, (
        f"{hops}-redirect chain: socks5 said {socks_result}, http said "
        f"{http_result} — one function, two behaviours chosen by transport"
    )

    # And pin WHICH verdict they agree on, so the two cannot drift together
    # into being uniformly wrong.
    if hops <= proxy_checker._MAX_ROTATE_REDIRECTS:
        assert socks_result == (True, "HTTP 200"), f"{socks_seen} / {http_seen}"
    else:
        ok, _detail = socks_result
        assert ok is False, (
            f"a chain of {hops} exceeded the {proxy_checker._MAX_ROTATE_REDIRECTS}"
            f"-hop bound and was reported as success anyway"
        )


def test_the_aiohttp_redirect_bound_is_offset_deliberately():
    """The `+ 1` at the aiohttp call site is load-bearing, not a typo.

    `max_redirects=N` fails ON the N-th redirect; the SOCKS loop permits N
    follows. Passing the bare constant is what made the two disagree. This
    documents the asymmetry at the point someone would "simplify" it away —
    but it is the BEHAVIOURAL test above that actually binds it.
    """
    source = inspect.getsource(proxy_checker.fetch_status_via_proxy)
    assert "max_redirects=_MAX_ROTATE_REDIRECTS + 1" in source
    assert "allow_redirects=True" in source


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
