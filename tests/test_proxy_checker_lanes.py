"""The SSRF guard is LANE-AWARE: loopback is checkable on the operator lane only.

`_is_blocked_proxy_host` was applied unconditionally, ahead of the scheme
branch, so it also covered the lane where the "user" is the operator inspecting
their OWN stored proxy. A local SOCKS endpoint — Tor's `127.0.0.1:9050`, an
`ssh -D` tunnel — could therefore never pass the geo check. The save path
accepts those (validate_proxy_format has no address restriction), so the
product let the operator store a proxy it then permanently refused to check:
`mark_check_failed` writes no geo, and `_proxy_timezone` falls back to
`_host_timezone()`. The tunnel works, traffic really exits through Tor, and the
profile declares the OPERATOR'S REAL TIMEZONE next to an `en-US` locale — the
`language ⊥ timezone` mismatch a detector flags.

These tests pin the fix as NARROWING, not removing:

  * the remote lane (check_proxy_sync — the REST route, a raw string from an
    off-machine caller, the genuine port-scan oracle) still refuses loopback
    and opens no socket doing it;
  * the operator lane (check_proxy_detailed_sync — UI-only) reaches a loopback
    SOCKS5 listener and comes back with real geo;
  * private / link-local / reserved / cloud-metadata stay refused on BOTH.

tests/test_proxy_checker_ssrf.py keeps the predicate itself honest and is
deliberately untouched by this change.
"""
import json
import socket
import ssl
import struct
import threading

import pytest

from src.models.proxy import Proxy
from src.services.browser import launch_policy
from src.services.browser.launch_policy import _proxy_timezone
from src.services.proxy.store import ProxyStore
from src.utils import proxy_checker

# The loopback SOCKS5 + TLS harness is the one the socks tests already use —
# imported, never retyped, so a change to the handshake fixture can't leave
# this file asserting against a stale copy of it.
from tests.test_proxy_checker_socks import (
    _GEO_PAYLOAD,
    _listener,
    _recv_exactly,
    _self_signed,
)

#: The exact refusal the guard returns. Pinned as a constant so a test can't
#: pass against a reworded message that no longer means "refused".
_REFUSED = "Proxy host is not allowed (private/loopback address)"

#: Ranges that must stay blocked on BOTH lanes. An operator's own proxy has no
#: legitimate reason to be a LAN host or the cloud-metadata address, and these
#: are the ranges the port-scan-oracle argument actually protects.
_STILL_BLOCKED = [
    "http://192.168.1.1:8080",
    "http://10.0.0.5:3128",
    "http://172.16.0.1:8080",
    "http://169.254.169.254:80",
]


# --------------------------------------------------------------------------
# The remote lane keeps the full guard — and refuses BEFORE touching the network.
# --------------------------------------------------------------------------


class _SocketSpy:
    """Delegating stand-in for socket.socket that records outbound TCP sockets.

    It delegates rather than stubs, so the code under test behaves normally and
    a failure here means "a socket was opened", not "the spy broke the test".
    Only AF_INET/AF_INET6 + SOCK_STREAM creations are counted: asyncio's event
    loop builds an AF_UNIX socketpair for its self-pipe on every _run(), which
    is loop plumbing rather than an outbound connection to the pasted host.
    """

    def __init__(self):
        self.tcp_sockets = 0
        self._real = socket.socket

    def __call__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, *a, **kw):
        if family in (socket.AF_INET, socket.AF_INET6) and type == socket.SOCK_STREAM:
            self.tcp_sockets += 1
        return self._real(family, type, *a, **kw)


def test_remote_lane_refuses_loopback_without_opening_a_socket(monkeypatch):
    """check_proxy_sync is the REST route's entry point: its input is a raw
    string from an off-machine API caller. That is the port-scan oracle the
    guard was written for, so loopback stays refused there — and the refusal
    must be a POLICY decision made before any connect, otherwise the oracle
    still leaks through connect-timing even while returning an error."""
    srv, port = _listener()
    accepted: list[bool] = []

    def serve() -> None:
        srv.settimeout(3)
        try:
            conn, _ = srv.accept()
            accepted.append(True)
            conn.close()
        except OSError:
            pass  # the expected path: nothing ever connects

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    spy = _SocketSpy()
    monkeypatch.setattr(socket, "socket", spy)
    try:
        ok, message = proxy_checker.check_proxy_sync(
            f"socks5://127.0.0.1:{port}", timeout=5
        )
    finally:
        thread.join(10)
        srv.close()

    assert ok is False
    assert message == _REFUSED
    # The two independent halves of "opened no socket": the checker created no
    # outbound TCP socket at all, and the listener saw nobody.
    assert spy.tcp_sockets == 0, "the remote lane opened a TCP socket to a blocked host"
    assert not accepted, "the blocked port received a connection"


@pytest.mark.parametrize("server", _STILL_BLOCKED)
def test_remote_lane_still_refuses_private_and_metadata(server):
    ok, message = proxy_checker.check_proxy_sync(server, timeout=5)
    assert ok is False
    assert message == _REFUSED


@pytest.mark.parametrize("server", _STILL_BLOCKED)
def test_operator_lane_still_refuses_private_and_metadata(server):
    """THE criterion that pins this fix as narrowing rather than removing: the
    operator lane gained loopback and NOTHING else."""
    ok, message = proxy_checker.check_proxy_detailed_sync(server, timeout=5)[:2]
    assert ok is False
    assert message == _REFUSED


def test_loopback_exemption_is_withdrawn_when_a_name_also_resolves_off_host(
    monkeypatch,
):
    """The exemption is loopback-ONLY, not loopback-ALSO. A name answering with
    both 127.0.0.1 and a LAN address is not a local tunnel — it is exactly the
    private-range target the guard exists to refuse — so any non-loopback
    answer withdraws the exemption on the operator lane too."""
    monkeypatch.setattr(
        proxy_checker.socket,
        "getaddrinfo",
        lambda host, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 0)),
        ],
    )
    ok, message = proxy_checker.check_proxy_detailed_sync(
        "socks5://split-horizon.test:1080", timeout=5
    )[:2]
    assert ok is False
    assert message == _REFUSED


# --------------------------------------------------------------------------
# The operator lane reaches a real loopback SOCKS5 endpoint and gets geo back.
# --------------------------------------------------------------------------


def _serve_socks5_geo(srv, server_ctx, seen: dict) -> threading.Thread:
    """A loopback SOCKS5 listener that completes the handshake and then answers
    the geo probe over TLS — i.e. what a Tor SOCKS port at 127.0.0.1:9050 is
    from the checker's point of view."""

    def serve() -> None:
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            greeting = _recv_exactly(conn, 2)
            seen["greeting"] = greeting
            _recv_exactly(conn, greeting[1])          # the offered methods
            conn.sendall(b"\x05\x00")                 # no auth, like a Tor port
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
    return thread


def _check_loopback_proxy(tmp_path, monkeypatch, seen: dict):
    certfile, keyfile = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(certfile), str(keyfile))
    srv, port = _listener()
    thread = _serve_socks5_geo(srv, server_ctx, seen)

    client_ctx = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(proxy_checker, "_ssl_context", lambda: client_ctx)
    try:
        return proxy_checker.check_proxy_detailed_sync(
            f"socks5://127.0.0.1:{port}", timeout=10
        )
    finally:
        thread.join(20)
        srv.close()


def test_operator_lane_reaches_a_loopback_socks5_proxy_and_returns_geo(
    tmp_path, monkeypatch
):
    """The defect, inverted: a Tor-shaped endpoint at 127.0.0.1 now completes
    the check and yields a country and timezone instead of a refusal."""
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_loopback_proxy(
        tmp_path, monkeypatch, seen
    )

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert code == "PL"
    assert tz == "Europe/Warsaw"
    assert country == "Poland"
    assert (lat, lon) == (52.23, 21.01)
    assert ip == "203.0.113.7"
    assert seen["greeting"][:1] == b"\x05"


def test_loopback_path_sends_a_domain_name_not_a_local_resolution(
    tmp_path, monkeypatch
):
    """No local DNS resolution is introduced on the newly-opened path. Resolving
    the geo endpoint here would emit a query from the operator's REAL resolver
    for a host the proxy is supposed to reach — the location leak the SOCKS
    path was written to avoid, and it would be silent."""
    seen: dict = {}
    ok, message = _check_loopback_proxy(tmp_path, monkeypatch, seen)[:2]

    assert ok is True, f"{message} / server: {seen.get('error')}"
    assert seen["atyp"] == 0x03, "the target was pre-resolved instead of sent by name"
    assert seen["target"] == ("ipwho.is", 443)


# --------------------------------------------------------------------------
# The consequence: the profile stops declaring the operator's real timezone.
# --------------------------------------------------------------------------


def test_checked_loopback_proxy_declares_the_exit_zone_not_the_host_zone(
    tmp_path, monkeypatch
):
    """The whole point of the slice, end to end.

    Before: the check was refused, mark_check_failed wrote no geo, and
    _proxy_timezone fell through to _host_timezone() — a Tor profile announcing
    the operator's real zone. After: the operator-lane check populates geo via
    mark_checked, so _proxy_timezone returns the EXIT's zone. The host zone is
    patched to a distinctive value that the assertion would catch if the
    fallback were still being reached.
    """
    seen: dict = {}
    ok, message, code, country, ip, tz, lat, lon = _check_loopback_proxy(
        tmp_path, monkeypatch, seen
    )
    assert ok is True, f"{message} / server: {seen.get('error')}"

    store = ProxyStore(path=str(tmp_path / "proxies.json"))
    store.proxies["tor"] = Proxy(name="tor", url="socks5://127.0.0.1:9050")
    assert store.mark_checked("tor", code, country, ip, tz, lat, lon) is True

    proxy = store.proxies["tor"]
    assert proxy.timezone == "Europe/Warsaw"
    assert proxy.country_code == "PL"
    assert proxy.last_check_ok is True

    # Patch on launch_policy, where _proxy_timezone resolves _host_timezone in
    # its OWN namespace (a patch on the process re-export is silently bypassed).
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    assert _proxy_timezone(proxy) == "Europe/Warsaw"


def test_unchecked_loopback_proxy_still_shows_the_leak_this_slice_closes(monkeypatch):
    """The baseline the test above is measured against: with geo still empty —
    which is every loopback proxy's permanent state before this fix — the host
    zone IS what gets declared. Kept explicit so the pair reads as a real
    before/after rather than a single assertion that could pass vacuously."""
    monkeypatch.setattr(launch_policy, "_host_timezone", lambda: "America/Chicago")
    assert _proxy_timezone(Proxy(name="tor", url="socks5://127.0.0.1:9050")) == (
        "America/Chicago"
    )
