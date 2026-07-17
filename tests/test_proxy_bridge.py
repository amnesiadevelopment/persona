import socket
import struct
import threading

import src.services.proxy.bridge as bridge_mod
from src.services.proxy.bridge import ProxyBridge


def test_bridge_starts_and_listens():
    bridge = ProxyBridge("socks5://user:pass@1.2.3.4:1080")
    port = bridge.start()
    try:
        assert port > 0
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
    finally:
        bridge.stop()


def test_bridge_parses_upstream():
    bridge = ProxyBridge("socks5://alice:secret@9.9.9.9:1080")
    assert bridge._up_host == "9.9.9.9"
    assert bridge._up_port == 1080
    assert bridge._up_user == "alice"
    assert bridge._up_pass == "secret"


def test_bridge_parses_without_scheme():
    bridge = ProxyBridge("u:p@host.example:1080")
    assert bridge._up_host == "host.example"
    assert bridge._up_port == 1080


def _recvn(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


class FakeUpstream(threading.Thread):
    """Scripted single-connection SOCKS5 upstream."""

    def __init__(self, behavior: str = "ok") -> None:
        super().__init__(daemon=True)
        self.behavior = behavior
        self.auth: tuple[str, str] | None = None
        self.target: tuple[str, int] | None = None
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5)
        self.port = self._srv.getsockname()[1]
        self.url = f"socks5://alice:secret@127.0.0.1:{self.port}"

    def run(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        self._srv.close()
        conn.settimeout(5)
        try:
            self._serve(conn)
        except OSError:
            pass
        finally:
            conn.close()

    # Named _serve, not _handle: threading.Thread on Python 3.13+ stores its
    # _thread._ThreadHandle in self._handle, silently shadowing a method of
    # that name.
    def _serve(self, conn: socket.socket) -> None:
        _ver, nmethods = _recvn(conn, 2)
        _recvn(conn, nmethods)
        if self.behavior == "hang":
            while conn.recv(4096):
                pass
            return
        conn.sendall(b"\x05\x02")  # require user/pass auth
        _ver, ulen = _recvn(conn, 2)
        user = _recvn(conn, ulen)
        plen = _recvn(conn, 1)[0]
        pwd = _recvn(conn, plen)
        self.auth = (user.decode(), pwd.decode())
        if self.behavior == "authfail":
            conn.sendall(b"\x01\x01")
            return
        conn.sendall(b"\x01\x00")
        _ver, _cmd, _rsv, atyp = _recvn(conn, 4)
        assert atyp == 0x03
        hlen = _recvn(conn, 1)[0]
        host = _recvn(conn, hlen).decode()
        port = struct.unpack(">H", _recvn(conn, 2))[0]
        self.target = (host, port)
        if self.behavior == "reject":
            conn.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            return
        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        if self.behavior == "drop_after_first":
            data = conn.recv(4096)
            if data:
                conn.sendall(data)
            return  # close the upstream side after one echo (upstream EOF)
        while True:
            data = conn.recv(4096)
            if not data:
                return
            conn.sendall(data)


def _socks5_request(port: int) -> tuple[socket.socket, bytes]:
    """Handshake with the bridge as a browser would; return the CONNECT reply."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"\x05\x01\x00")
    assert _recvn(s, 2) == b"\x05\x00"
    s.sendall(b"\x05\x01\x00\x03\x0bexample.com" + struct.pack(">H", 443))
    return s, _recvn(s, 10)


def _run_bridge_case(
    behavior: str,
) -> tuple[FakeUpstream, bytes, socket.socket, ProxyBridge]:
    upstream = FakeUpstream(behavior)
    upstream.start()
    bridge = ProxyBridge(upstream.url)
    bridge.start()
    try:
        client, reply = _socks5_request(bridge.port)
    except Exception:
        bridge.stop()
        raise
    return upstream, reply, client, bridge


def test_success_reply_only_after_upstream_connect():
    upstream, reply, client, bridge = _run_bridge_case("ok")
    try:
        assert reply[1] == 0x00
        # upstream CONNECT completed before the browser saw success
        assert upstream.target == ("example.com", 443)
        assert upstream.auth == ("alice", "secret")
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_upstream_reject_sends_failure_reply():
    upstream, reply, client, bridge = _run_bridge_case("reject")
    try:
        assert len(reply) == 10
        assert reply[1] == 0x05  # upstream reply code passed through, not success
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_upstream_auth_failure_sends_failure_reply():
    upstream, reply, client, bridge = _run_bridge_case("authfail")
    try:
        assert len(reply) == 10
        assert reply[1] == 0x01
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_upstream_hang_times_out_with_failure_reply(monkeypatch):
    monkeypatch.setattr(bridge_mod, "_UPSTREAM_TIMEOUT", 0.5)
    upstream, reply, client, bridge = _run_bridge_case("hang")
    try:
        assert len(reply) == 10
        assert reply[1] == 0x01
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_tunnel_sockets_have_tcp_keepalive():
    # #184: a silent proxy circuit (Tor wedges, socket stays open with no bytes
    # and no EOF) left both _pipe directions blocked on read() forever — Sheets
    # stuck on "Working". TCP keepalive lets the OS detect the dead half-open
    # tunnel and drop it, so the pipe unblocks and the browser reconnects. Both
    # the client-side and upstream-side tunnel sockets must have SO_KEEPALIVE on.
    bridge_mod._debug_tunnel_sockets().clear()
    upstream, reply, client, bridge = _run_bridge_case("ok")
    try:
        assert reply[1] == 0x00
        # round-trip proves the tunnel is fully established before we inspect it
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
        # both tunnel ends were captured (client-accept + upstream) and each has
        # keepalive enabled. Inspect only still-open sockets — a fd=-1 means it
        # already closed, which the OS reports as an error, not a missing option.
        socks = [s for s in bridge_mod._debug_tunnel_sockets() if s.fileno() != -1]
        assert len(socks) >= 2, "expected both tunnel sockets captured"
        for sock in socks:
            assert (
                sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
            ), "tunnel socket missing SO_KEEPALIVE"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_one_dead_pipe_direction_tears_the_whole_tunnel():
    # #184: when one direction of the tunnel ends (upstream EOF), the browser's
    # side must close too instead of hanging on its own read() — otherwise a
    # one-way stall leaks a half-open tunnel. Upstream closes after echoing;
    # the client read must return EOF promptly, not block.
    upstream, reply, client, bridge = _run_bridge_case("drop_after_first")
    try:
        assert reply[1] == 0x00
        client.sendall(b"hi")
        assert _recvn(client, 2) == b"hi"
        # upstream closed its side after the echo; the bridge must propagate the
        # teardown so our recv returns EOF quickly, not hang.
        client.settimeout(5)
        assert client.recv(4096) == b""
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()
