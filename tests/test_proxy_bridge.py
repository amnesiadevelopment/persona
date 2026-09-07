import base64
import os
import socket
import struct
import threading
import time

import src.services.proxy.bridge as bridge_mod
from src.services.proxy.bridge import ProxyBridge


def _claim(bridge: ProxyBridge) -> ProxyBridge:
    """Authorize THIS process to use the bridge.

    The bridge now serves only the browser process tree it was started for, so a
    test that connects from the pytest process must say so. This is the same
    call the launcher makes after spawning the browser, and the same shape the
    Firefox non-fork path uses (where the engine really does run in-process).
    """
    bridge.bind_to_process(os.getpid())
    return bridge



def test_bridge_starts_and_listens():
    bridge = _claim(ProxyBridge("socks5://user:pass@1.2.3.4:1080"))
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
        self.streamed_request: bytes | None = None
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
        if self.behavior == "silent_forever":
            # Simulate a silently-reaped upstream: after CONNECT, send NOTHING and
            # never close (no bytes, no FIN) — a dead half-open leg. The bridge
            # must reap the tunnel on its idle timeout, not hang forever.
            try:
                while True:
                    if not conn.recv(4096):
                        return
            except OSError:
                return
        if self.behavior == "stream_after_client_eof":
            # A browserchannel long-poll: the client sends its request then
            # half-closes its write side (client->up EOF); the server keeps the
            # response open and streams data back AFTER that. Read the request,
            # wait for the client's half-close, then stream the reply.
            req = conn.recv(4096)
            self.streamed_request = req
            # Drain until the client's write half is closed (recv returns b"").
            while conn.recv(4096):
                pass
            for chunk in (b"chunk-1;", b"chunk-2;", b"chunk-3;"):
                conn.sendall(chunk)
                time.sleep(0.05)
            return
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
    bridge = _claim(ProxyBridge(upstream.url))
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


def test_upstream_connect_retries_a_transient_failure(monkeypatch):
    # #184 real root on Tor: a single CONNECT to a Google host would hit the
    # 15s timeout on a slow circuit, get REJECTED to the browser (rep=1 →
    # ERR_SOCKS_CONNECTION_FAILED), and Sheets stuck on "Working". A transient
    # bad circuit must be RETRIED on a fresh one before failing the browser.
    # Here the first two upstream opens fail, the third succeeds — the browser
    # must receive a SUCCESS reply, not a failure.
    import asyncio

    monkeypatch.setattr(bridge_mod, "_UPSTREAM_TIMEOUT", 2.0)
    monkeypatch.setattr(bridge_mod, "_UPSTREAM_ATTEMPTS", 3)

    bridge = ProxyBridge("socks5://user:pass@1.2.3.4:1080")
    calls = {"n": 0}

    async def flaky_open(host, port):
        calls["n"] += 1
        if calls["n"] < 3:
            # a transient Tor-circuit failure (dropped connection), NOT a
            # definitive proxy reject — this is the case that must be retried
            raise ConnectionResetError("circuit dropped")
        # succeed on the 3rd: an already-EOF reader plus a stub writer so the
        # splice has something to pump (it EOFs at once, which is fine here).
        r = asyncio.StreamReader()
        r.feed_eof()

        class _StubWriter:
            def write(self, *_): pass
            async def drain(self): pass
            def close(self): pass
            def get_extra_info(self, *_): return None
            def can_write_eof(self): return False
            def write_eof(self): pass

        return r, _StubWriter()

    monkeypatch.setattr(bridge, "_open_upstream", flaky_open)
    _claim(bridge)
    port = bridge.start()
    try:
        client, reply = _socks5_request(port)
        # 3 attempts were made and the browser got SUCCESS (rep=0), not rep=1
        assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
        assert reply[1] == 0x00, f"browser should see success, got rep={reply[1]}"
        client.close()
    finally:
        bridge.stop()


def test_tunnel_sockets_have_tcp_keepalive():
    # A silent proxy circuit (Tor wedges, socket stays open with no bytes and no
    # EOF) leaves both _pipe directions blocked on read() forever. TCP keepalive
    # lets the OS detect the dead half-open tunnel and drop it, so the pipe
    # unblocks and the browser reconnects. Both the client-side and upstream-side
    # tunnel sockets must have SO_KEEPALIVE on.
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


def test_tunnel_sockets_have_tcp_nodelay():
    # #184 (v2.5.1 live: keepalive alone didn't fix Sheets "Working" on a
    # chromium+proxy profile): a live tunnel was stalling the collab websocket,
    # not dying. Nagle's algorithm batches the tiny interactive WS frames Sheets
    # sends, and combined with delayed-ACK downstream that compounds into
    # multi-hundred-ms stalls that read as a permanent "Working". Disabling Nagle
    # (TCP_NODELAY) on both tunnel ends sends each frame immediately.
    bridge_mod._debug_tunnel_sockets().clear()
    upstream, reply, client, bridge = _run_bridge_case("ok")
    try:
        assert reply[1] == 0x00
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
        socks = [s for s in bridge_mod._debug_tunnel_sockets() if s.fileno() != -1]
        assert len(socks) >= 2, "expected both tunnel sockets captured"
        for sock in socks:
            assert (
                sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
            ), "tunnel socket missing TCP_NODELAY (Nagle still batching frames)"
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


def test_client_half_close_keeps_upstream_stream_alive():
    # #184 THE root: Google Sheets' realtime collab channel is a browserchannel
    # long-poll — the client sends its request then HALF-CLOSES its write side
    # and waits for the server to STREAM the response back. Tearing the whole
    # tunnel down the instant the client->upstream pipe EOFs (FIRST_COMPLETED)
    # killed that streamed response mid-flight, so the calendar/overlays never
    # painted and "Working" stuck forever. A client half-close must NOT tear
    # down the still-active upstream->client direction.
    upstream, reply, client, bridge = _run_bridge_case("stream_after_client_eof")
    try:
        assert reply[1] == 0x00
        client.sendall(b"GET /bind?long-poll HTTP/1.1\r\n\r\n")
        # Half-close: the browser is done sending, now waits for the stream.
        client.shutdown(socket.SHUT_WR)
        client.settimeout(5)
        got = b""
        while len(got) < len(b"chunk-1;chunk-2;chunk-3;"):
            part = client.recv(4096)
            if not part:
                break
            got += part
        assert got == b"chunk-1;chunk-2;chunk-3;"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_reader_buffers_are_large_enough_for_multiplexed_streams():
    # #184 real root: one tunnel carries a multiplexed HTTP/2 connection — a large
    # download (Sheets' 375 KB calc-worker) AND small client->server control
    # frames (WINDOW_UPDATE / the /bind long-poll) share it. With asyncio's 64 KiB
    # default StreamReader limit, a busy download fills the buffer, asyncio pauses
    # reading the transport, the reverse control frames stall, and over a
    # high-latency proxy the whole h2 connection wedges — Sheets sticks on
    # "Working". The bridge must give both directions a buffer big enough that one
    # busy stream can't starve the others.
    upstream = FakeUpstream("ok")
    upstream.start()
    bridge = _claim(ProxyBridge(upstream.url))
    bridge.start()
    try:
        _socks5_request(bridge.port)
        assert bridge_mod._STREAM_LIMIT >= 4 * 1024 * 1024
    finally:
        bridge.stop()
        upstream.join(timeout=5)


def test_silent_but_open_tunnel_is_NOT_reaped():
    # #184 TRUE root: the bridge used to reap ANY tunnel with no bytes for N
    # seconds. But Google Sheets' realtime /bind long-poll legitimately holds a
    # connection OPEN and SILENT for tens of seconds waiting for an event. Reaping
    # it killed the collab channel → chromium reopened → permanent "Working"
    # (chromium-only, because Firefox uses native SOCKS and never goes through
    # this bridge). A silent-but-OPEN upstream must be LEFT ALIVE; only a truly
    # dead half-open circuit (EOF/RST, caught by _pipe's read or TCP keepalive)
    # gets torn down. Here the upstream stays open and silent; the tunnel must
    # SURVIVE (no blind idle-reap kills a live long-poll anymore).
    upstream, reply, client, bridge = _run_bridge_case("silent_forever")
    try:
        assert reply[1] == 0x00
        client.settimeout(3)
        # No bytes flow either way, but the upstream socket is still OPEN. The
        # bridge must NOT reap it: recv should TIME OUT (tunnel alive, waiting),
        # not return EOF (reaped).
        raised = False
        try:
            data = client.recv(4096)
        except socket.timeout:
            raised = True
            data = None
        assert raised, f"tunnel was reaped (got {data!r}) — a silent long-poll must survive"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_start_raises_when_no_port_bound(monkeypatch):
    # #9 (audit4) fail-closed: if the listener never binds, _port stays 0.
    # Returning 0 lets the caller build socks5://127.0.0.1:0, which Chromium
    # treats as no-proxy → a silent DIRECT clearnet launch (deanon). start()
    # must RAISE instead of returning a bogus port.
    bridge = ProxyBridge("socks5://user:pass@1.2.3.4:1080")

    # Never let _serve set the port: simulate a bind that never completes.
    async def _never_binds():
        return

    monkeypatch.setattr(bridge, "_serve", _never_binds)
    try:
        import pytest

        with pytest.raises(RuntimeError, match="failed to bind"):
            bridge.start()
    finally:
        bridge.stop()


def test_non_ascii_credentials_are_byte_length_prefixed():
    # #9 (audit4): SOCKS5 sends a 1-byte length then the raw credential bytes.
    # A cred with non-ASCII chars (e.g. Cyrillic) encodes to MORE UTF-8 bytes
    # than str characters — len(str) would desync the frame (FakeUpstream reads
    # ulen then exactly ulen bytes). The bridge must encode first, then measure.
    upstream = FakeUpstream("ok")
    upstream.start()
    # Cyrillic user + password: 'юзер' is 4 chars / 8 UTF-8 bytes.
    bridge = _claim(ProxyBridge(f"socks5://юзер:парол@127.0.0.1:{upstream.port}"))
    bridge.start()
    try:
        client, reply = _socks5_request(bridge.port)
        assert reply[1] == 0x00
        # The upstream decoded exactly what we sent — frame stayed in sync.
        assert upstream.auth == ("юзер", "парол")
        client.close()
    finally:
        upstream.join(timeout=5)
        bridge.stop()


def test_over_long_credentials_are_rejected():
    # A credential longer than 255 BYTES cannot fit SOCKS5's 1-byte length
    # field; sending a truncated length would desync the frame. Constructing the
    # bridge must fail CLOSED — raise up front rather than start a listener that
    # can't authenticate and would fall through to a DIRECT clearnet connection.
    import pytest

    with pytest.raises(ValueError, match="255 bytes"):
        ProxyBridge(f"socks5://u:{'x' * 300}@127.0.0.1:1080")


def test_dead_upstream_eof_tears_the_tunnel_down():
    # The other half: a truly dead upstream (it closes after one exchange = EOF)
    # MUST tear the tunnel down so the browser rebuilds — the real half-open case,
    # now handled by EOF propagation (_pipe's read returns b'') + TCP keepalive,
    # NOT a blind idle timer that also killed live long-polls.
    upstream, reply, client, bridge = _run_bridge_case("drop_after_first")
    try:
        assert reply[1] == 0x00
        client.sendall(b"ping")          # upstream echoes then closes (EOF)
        client.settimeout(6)
        assert client.recv(4096) == b"ping"
        t0 = time.time()
        data = client.recv(4096)         # upstream EOF must propagate to us
        dt = time.time() - t0
        assert data == b"", "dead upstream (EOF) should close our side"
        assert dt < 5, f"dead tunnel not torn down promptly ({dt:.1f}s)"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_a_dead_tunnels_trace_says_WHAT_went_wrong_not_only_its_CLASS(
    tmp_path, monkeypatch
):
    """PS-160: the pipe recorded `reason=<ClassName>` and dropped the message.

    Every proxied browser session runs through `_pipe`, so when a tunnel dies
    this trace is the whole account of it. `OSError` alone says a kind of
    thing went wrong; `[Errno 104] Connection reset by peer` says what did.

    Driven through a REAL reset rather than a raised stub: the client aborts
    with SO_LINGER 0, which sends an RST instead of a FIN, so the bridge's
    read genuinely raises instead of returning EOF. A stub would let this
    assert wording against an exception the socket layer never produces.
    """
    trace = tmp_path / "bridge.log"
    monkeypatch.setattr(bridge_mod, "_TRACE_PATH", str(trace))

    upstream, reply, client, bridge = _run_bridge_case("ok")
    try:
        assert reply[1] == 0x00
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
        # RST, not FIN: an orderly close is EOF (`reason=eof`) and never
        # reaches the `except` arm this test is about.
        client.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.close()

        deadline = time.time() + 5
        line = None
        while time.time() < deadline and line is None:
            if trace.exists():
                for entry in trace.read_text(encoding="utf-8").splitlines():
                    if "DONE" in entry and "reason=" in entry:
                        reason = entry.split("reason=", 1)[1]
                        if reason != "eof":
                            line = entry
                            break
            if line is None:
                time.sleep(0.05)

        assert line is not None, (
            "no non-eof DONE line in the trace: "
            + (trace.read_text(encoding="utf-8") if trace.exists() else "<no trace written>")
        )
        reason = line.split("reason=", 1)[1]
        # The class is still there...
        assert "Error" in reason or "OSError" in reason
        # ...and the MESSAGE is too, which is the half that identifies it.
        # On the old code `reason` was a bare identifier with no colon.
        assert ": " in reason, f"message discarded, got bare class: {reason!r}"
        # ...specifically the OS's OWN account of the reset — the half a class
        # name cannot produce.
        #
        # ⚠️ ASSERTED ON THE ERRNO TAG, NOT ON THE PROSE, because the prose is
        # platform-specific and the first version of this line pinned POSIX's:
        # `[Errno 104] Connection reset by peer` on Linux/macOS versus
        # `[WinError 64] The specified network name is no longer available` on
        # Windows. Same event, same fix, different words — so wording is the
        # wrong thing to hold the fix to. What must be true everywhere is that
        # an errno-tagged detail reached the trace at all.
        lowered = reason.lower()
        assert "[errno" in lowered or "[winerror" in lowered, (
            f"no OS-level detail in the reason, so the message half of the "
            f"fix is not proven here: {reason!r}"
        )
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


# --- PS-160: the trace is credential-adjacent, and now carries exception text -


def test_a_credential_shaped_string_cannot_reach_the_TRACE_FILE(
    tmp_path, monkeypatch
):
    """THE ONE THAT MUST NOT REGRESS on this side.

    PS-160 made four trace sites carry the exception MESSAGE rather than only
    its class. That is un-authored text, and unlike `exit_guard`'s in-process
    refusal it lands in a FILE ON DISK that outlives the process — while this
    module holds `_up_user`/`_up_pass` unquoted and is built from a
    credential-shaped `upstream_url`. So the talkative change and the secret
    live in the same module, which is exactly the case `exit_guard`'s doctrine
    exists for: "the risky part is the one nobody thought of".

    ⚠️ STAGED AT `_open_upstream`, DELIBERATELY, AND THE FIRST VERSION OF THIS
    TEST WAS WRONG. It patched `StreamReader.read` to raise, then aborted the
    client — but the pipe is ALREADY BLOCKED IN `read()` by then, so the patch
    never took effect and the trace recorded the ordinary reset instead. It
    passed against UN-REDACTED source: the secret was never in the trace to be
    redacted, so it asserted nothing. This seam is entered AFTER the patch, so
    the injected message genuinely travels the real handler into the real
    `_trace` and out to the real file.

    That the exception is injected is the honest bound here: no bridge
    exception is KNOWN to carry the credential today. This asserts the
    guarantee that holds whether or not one ever does.
    """
    trace = tmp_path / "bridge.log"
    monkeypatch.setattr(bridge_mod, "_TRACE_PATH", str(trace))
    monkeypatch.setattr(bridge_mod, "_UPSTREAM_ATTEMPTS", 1)

    secret_url = "socks5://alice:s3cr3t@gate.example.com:10000"

    async def _leaky(self, host, port):
        raise OSError(f"connection to {secret_url} failed")

    monkeypatch.setattr(bridge_mod.ProxyBridge, "_open_upstream", _leaky)

    upstream = FakeUpstream("ok")
    upstream.start()
    bridge = _claim(ProxyBridge(upstream.url))
    bridge.start()
    client = None
    try:
        client, reply = _socks5_request(bridge.port)
        # The connect failed, so the browser is told so — behaviour unchanged.
        assert reply[1] != 0x00

        deadline = time.time() + 5
        text = ""
        while time.time() < deadline:
            if trace.exists():
                text = trace.read_text(encoding="utf-8")
                if "UPSTREAM-FAIL" in text:
                    break
            time.sleep(0.05)

        assert "UPSTREAM-FAIL" in text, (
            f"the failing trace line was never written, so this test proved "
            f"nothing. Trace was: {text!r}"
        )
        # The injected message DID reach the trace (without this, the
        # assertions below would pass on an empty-handed line)...
        assert "connection to" in text and "failed" in text
        # ...the failure still names itself...
        assert "OSError" in text
        # ...and the credential did not survive into the file.
        assert "s3cr3t" not in text, f"credential reached the trace file: {text!r}"
        assert "alice" not in text, f"credential reached the trace file: {text!r}"
        assert "***:***@" in text
    finally:
        if client is not None:
            client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_redaction_is_applied_by_the_SINK_so_every_trace_site_inherits_it(
    tmp_path, monkeypatch
):
    """The placement claim, asserted directly.

    `_trace` is the seam the guarantee sits on. If someone later moves
    redaction out to the individual call sites, this fails — which is the
    point: a future fifth call site must be covered without its author having
    to know that redaction exists.
    """
    trace = tmp_path / "bridge.log"
    monkeypatch.setattr(bridge_mod, "_TRACE_PATH", str(trace))

    bridge_mod._trace("conn=1 host=x SOMETHING socks5://bob:0th3r@relay.example:1080")

    written = trace.read_text(encoding="utf-8")
    assert "SOMETHING" in written, "the line itself must still be recorded"
    assert "0th3r" not in written
    assert "bob" not in written
    assert "***:***@" in written


# --- PS-329: the bridge must speak its UPSTREAM's protocol -------------------
#
# THE DEFECT, measured at f626386 before the fix. `ProxyBridge` discarded the
# upstream scheme (`grep -c scheme bridge.py` -> 0) and wrote a SOCKS5 greeting
# unconditionally, so an authenticated `http://` proxy -- a configuration
# `PROXY_SCHEMES` offers and the proxy dialog's Type dropdown derives from --
# received binary SOCKS and answered HTTP:
#
#     SUBJECT  http:// + creds    REFUSED rep=0x50
#     CONTROL  socks5:// + creds  GRANTED (rep=0x00)
#     bytes the HTTP proxy received: [b'\x05\x02\x00\x02']   <- a SOCKS5 greeting
#
# Only the scheme differed, which is what makes it a gap and not a category.
#
# WHAT THESE ASSERT, and why it is shaped this way (AC1):
#   * the TRANSPORT RESULT the driver receives, or the bytes the upstream server
#     actually observed -- never that a helper was called, never a source
#     substring.
#   * REFUSED vs GRANTED, never a literal reply byte. The pre-fix `0x50` is just
#     the 'P' of the upstream's ASCII "HTTP/1.1" surfacing in the reply slot; a
#     test pinned to it would be a test about the fixture.
#   * nothing about TIMING. The ticket's source proposal claimed a ~91s hang;
#     that did not reproduce (refusal is immediate against a prompt upstream),
#     and the hang belongs to a SILENT upstream rather than to this defect.
#
# The peer gate is NOT weakened: these use the same `_claim` every other test in
# this file uses, which is the call the launcher makes after spawning the
# browser (PS-25 ground).


class FakeHttpUpstream(threading.Thread):
    """Scripted single-connection HTTP proxy that expects CONNECT.

    Deliberately a REAL socket speaking real HTTP rather than a stub on the
    bridge's own method: the whole defect was that the bytes on the wire were
    the wrong protocol, so the assertion has to be able to see those bytes.
    """

    def __init__(self, behavior: str = "ok") -> None:
        super().__init__(daemon=True)
        self.behavior = behavior
        self.request_head: bytes | None = None
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(5)
        self.port = self._srv.getsockname()[1]
        self.url = f"http://alice:secret@127.0.0.1:{self.port}"

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

    def _serve(self, conn: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                self.request_head = head
                return
            head += chunk
            # A SOCKS5 greeting is 4 binary bytes and no CRLFCRLF ever arrives.
            # Record what we got and stop, so the pre-fix behaviour is
            # OBSERVABLE here rather than hanging the test.
            if not head.startswith(b"CONNECT") and len(head) >= 4:
                self.request_head = head
                conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                return
        self.request_head = head
        # --- malformed answers: an upstream whose reply cannot be believed ---
        if self.behavior == "close":
            # Closes without replying at all. -> IncompleteReadError
            return
        if self.behavior == "garbage":
            # Answers something that is not an HTTP response -- the shape a
            # SOCKS proxy misconfigured as `http://` produces.
            conn.sendall(b"NOT-HTTP AT ALL\r\n\r\n")
            return
        if self.behavior == "unterminated":
            # A plausible status line whose head is never terminated, then EOF.
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n")
            return
        if self.behavior == "badstatus":
            # HTTP-shaped, but the status is not a number.
            conn.sendall(b"HTTP/1.1 zzz Weird\r\n\r\n")
            return
        if self.behavior == "oversized":
            # A terminated head far past _HTTP_REPLY_LIMIT (64 KiB) and well
            # under the stream's 8 MiB -- the band the explicit check exists for.
            conn.sendall(
                b"HTTP/1.1 200 Connection established\r\nX-Pad: "
                + b"a" * (128 * 1024)
                + b"\r\n\r\n"
            )
            return
        if self.behavior == "authfail":
            conn.sendall(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b"Proxy-Authenticate: Basic realm=\"p\"\r\n\r\n"
            )
            return
        if self.behavior == "reject":
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        while True:
            data = conn.recv(4096)
            if not data:
                return
            conn.sendall(data)


def _run_http_bridge_case(
    behavior: str,
) -> tuple[FakeHttpUpstream, bytes, socket.socket, ProxyBridge]:
    upstream = FakeHttpUpstream(behavior)
    upstream.start()
    bridge = _claim(ProxyBridge(upstream.url))
    bridge.start()
    try:
        client, reply = _socks5_request(bridge.port)
    except Exception:
        bridge.stop()
        raise
    return upstream, reply, client, bridge


def test_an_authenticated_http_proxy_carries_a_request_end_to_end():
    """THE SUBJECT. Driven through the real listener, with a claimed peer.

    Pre-fix this reply byte was NON-ZERO (a refusal) because the upstream got a
    SOCKS5 greeting. The payload echo is what makes this a claim about a working
    TUNNEL rather than about a handshake that merely returned.
    """
    upstream, reply, client, bridge = _run_http_bridge_case("ok")
    try:
        assert reply[1] == 0x00, (
            f"an authenticated http:// proxy did not carry the request: "
            f"reply={reply!r}"
        )
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping", "the tunnel did not carry payload"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_the_http_upstream_receives_CONNECT_and_not_a_socks_greeting():
    """The bytes ON THE WIRE, which is where the defect actually lived.

    Pre-fix `request_head` was exactly b"\x05\x02\x00\x02".
    """
    upstream, reply, client, bridge = _run_http_bridge_case("ok")
    try:
        head = upstream.request_head or b""
        assert not head.startswith(b"\x05"), (
            f"the bridge spoke SOCKS to an HTTP proxy: {head[:16]!r}"
        )
        assert head.startswith(b"CONNECT example.com:443 HTTP/1.1\r\n"), (
            f"unexpected request line: {head[:64]!r}"
        )
        # RFC 7231 requires Host on HTTP/1.1; several proxies refuse without it.
        assert b"\r\nHost: example.com:443\r\n" in head
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_the_credential_is_SENT_to_the_http_proxy_not_dropped():
    """⛔ The prohibited resolution was to drop the credentials and send
    unauthenticated traffic -- the fail-open shape this module exists to
    prevent. This asserts the credential really is on the wire, decoded."""
    upstream, reply, client, bridge = _run_http_bridge_case("ok")
    try:
        head = (upstream.request_head or b"").decode("latin-1")
        line = [
            ln for ln in head.split("\r\n")
            if ln.lower().startswith("proxy-authorization:")
        ]
        assert line, f"no Proxy-Authorization header was sent: {head!r}"
        token = line[0].split()[-1]
        assert base64.b64decode(token).decode() == "alice:secret"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_the_destination_hostname_is_not_resolved_locally():
    """Remote DNS. The SOCKS5 leg gets this from atyp=0x03; the HTTP leg must
    not regress it by resolving before CONNECT -- that would leak a DNS query
    from the operator's real resolver for every page."""
    upstream, reply, client, bridge = _run_http_bridge_case("ok")
    try:
        head = (upstream.request_head or b"").decode("latin-1")
        assert "CONNECT example.com:443" in head, (
            f"the destination was not sent as a hostname: {head[:64]!r}"
        )
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_a_407_from_the_http_proxy_is_a_refusal_not_a_tunnel():
    """A wrong credential must FAIL. Handing the browser a socket carrying an
    error page would be indistinguishable from a working proxy until every
    request failed."""
    upstream, reply, client, bridge = _run_http_bridge_case("authfail")
    try:
        assert reply[1] != 0x00, f"a 407 was treated as a tunnel: reply={reply!r}"
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_a_403_from_the_http_proxy_is_a_refusal_not_a_tunnel():
    """A refused destination must fail too -- any non-2xx head, not just 407."""
    upstream, reply, client, bridge = _run_http_bridge_case("reject")
    try:
        assert reply[1] != 0x00, f"a 403 was treated as a tunnel: reply={reply!r}"
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_THE_CONTROL_an_authenticated_socks5_upstream_still_works():
    """AC2, non-waivable and IN THE SAME RUN as the subject above.

    A subject-only reading proves nothing: if the SOCKS5 leg had been broken by
    the dispatch, the subject passing would be worthless.
    """
    upstream, reply, client, bridge = _run_bridge_case("ok")
    try:
        assert reply[1] == 0x00
        assert upstream.target == ("example.com", 443)
        assert upstream.auth == ("alice", "secret")
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
    finally:
        client.close()
        upstream.join(timeout=5)
        bridge.stop()


def test_THE_CONTROL_an_unauthenticated_http_proxy_still_takes_no_bridge():
    """AC2's second control: the un-credentialed path must be byte-identical.

    `_proxy_arg` starts a bridge only for a credentialed URL. An unauthenticated
    http:// proxy goes to --proxy-server verbatim and must not acquire a bridge
    as a side effect of this change.
    """
    from src.services.browser.process import _proxy_arg

    for url in ("http://1.2.3.4:8080", "https://1.2.3.4:8443", "socks5://1.2.3.4:1080"):
        arg, br = _proxy_arg(url)
        try:
            assert br is None, f"{url} unexpectedly started a bridge"
            assert arg == url, f"{url} was rewritten to {arg!r}"
        finally:
            if br is not None:
                br.stop()


def test_an_https_upstream_is_dialled_over_TLS_to_the_proxy_itself():
    """`https://` means the hop to the PROXY is TLS.

    Asserted at the connection attempt rather than with a TLS fixture: the
    observable is that an ssl context is handed to the connection for `https`
    and NOT for `http`, which is what distinguishes the two schemes here.
    """
    import asyncio

    seen = {}

    async def _fake_open_connection(host, port, **kw):
        seen["ssl"] = kw.get("ssl")
        raise ConnectionRefusedError("stop here: the kwargs are all this needs")

    async def _drive(url):
        b = ProxyBridge(url)
        try:
            await b._open_upstream("example.com", 443)
        except ConnectionRefusedError:
            pass

    loop = asyncio.new_event_loop()
    try:
        orig = bridge_mod.asyncio.open_connection
        bridge_mod.asyncio.open_connection = _fake_open_connection
        try:
            loop.run_until_complete(_drive("https://u:p@127.0.0.1:8443"))
            assert seen.get("ssl") is not None, "https upstream was dialled in the clear"
            loop.run_until_complete(_drive("http://u:p@127.0.0.1:8080"))
            assert seen.get("ssl") is None, "http upstream was wrapped in TLS"
        finally:
            bridge_mod.asyncio.open_connection = orig
    finally:
        loop.close()


def test_an_http_upstream_defaults_to_8080_not_the_socks_port():
    """A portless http:// upstream must not be dialled on 1080."""
    assert ProxyBridge("http://u:p@proxy.example")._up_port == 8080
    assert ProxyBridge("https://u:p@proxy.example")._up_port == 8080
    assert ProxyBridge("socks5://u:p@proxy.example")._up_port == 1080
    # The scheme-less fallback still implies socks5, and so 1080.
    assert ProxyBridge("u:p@proxy.example")._up_port == 1080


def test_a_long_credential_is_refused_for_socks5_but_allowed_for_http():
    """The 255-byte cap is SOCKS5's length-prefix, not a universal rule.

    HTTP Proxy-Authorization base64-encodes user:pass with no length field, so
    refusing a long credential there would remove a working configuration in the
    name of a constraint that does not apply to it. The SOCKS5 refusal is
    unchanged.
    """
    import pytest

    long_user = "u" * 300
    with pytest.raises(ValueError):
        ProxyBridge(f"socks5://{long_user}:p@1.2.3.4:1080")
    b = ProxyBridge(f"http://{long_user}:p@1.2.3.4:8080")
    assert b._up_user == long_user


# --- The malformed-upstream family -------------------------------------------
#
# ⚠️ WHY THESE EXIST, so nobody deletes them as redundant with the 407/403 pair.
#
# The 407 and 403 tests take the STATUS branch, which maps to SOCKS5 0x02. These
# five take the OTHER branches -- the ones where the upstream's answer cannot be
# parsed at all -- and those were each written as `_ConnectRejected(0)`. `0x00`
# is SOCKS5's `succeeded`: the handler writes `rep` back verbatim, so every one
# of them told the browser THE TUNNEL WAS OPEN and then handed it an empty
# socket. Measured through the real listener before the fix:
#
#     mode=close         reply=b'\x05\x00\x00\x01...'  rep=0x00  <- "succeeded"
#     mode=garbage       reply=b'\x05\x00\x00\x01...'  rep=0x00  <- "succeeded"
#     mode=unterminated  reply=b'\x05\x00\x00\x01...'  rep=0x00  <- "succeeded"
#     mode=badstatus     reply=b'\x05\x00\x00\x01...'  rep=0x00  <- "succeeded"
#     mode=oversized     reply=b'\x05\x00\x00\x01...'  rep=0x00  <- "succeeded"
#     mode=403 (control) reply=b'\x05\x02\x00\x01...'  rep=0x02  <- refused
#
# The assertion is `reply[1] != 0x00` -- the transport result the driver
# receives (AC1), REFUSED vs GRANTED, never a literal byte (Correction 1). It is
# deliberately not `== 0x01`: which non-zero code is chosen is an
# implementation's business, that it is non-zero is the contract.


def _assert_refused(behavior: str) -> None:
    """Drive one malformed upstream end-to-end and require a refusal."""
    upstream, reply, client, bridge = _run_http_bridge_case(behavior)
    try:
        assert len(reply) >= 2, f"the browser got no SOCKS reply at all: {reply!r}"
        assert reply[1] != 0x00, (
            f"upstream mode {behavior!r}: a failed CONNECT was reported to the "
            f"browser as an OPEN TUNNEL (0x00 is SOCKS5 'succeeded'): "
            f"reply={reply!r}"
        )
        # And nothing is carried -- the refusal is the end of it.
        assert client.recv(4096) == b""
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_an_upstream_that_closes_without_replying_is_a_refusal_not_a_tunnel():
    _assert_refused("close")


def test_an_upstream_answering_non_http_is_a_refusal_not_a_tunnel():
    """The `http://`-mislabelled SOCKS proxy: refuse rather than guess."""
    _assert_refused("garbage")


def test_an_unterminated_reply_head_is_a_refusal_not_a_tunnel():
    """A 200 whose head never ends is not a tunnel, however promising it looks."""
    _assert_refused("unterminated")


def test_an_unparseable_status_is_a_refusal_not_a_tunnel():
    _assert_refused("badstatus")


def test_an_oversized_reply_head_is_a_refusal_not_a_tunnel():
    """The 64 KiB bound. Under the stream's own 8 MiB limit, so this is the
    branch `_HTTP_REPLY_LIMIT` exists for -- and it must refuse, not succeed."""
    _assert_refused("oversized")


def test_a_rejection_can_never_carry_the_socks5_success_code():
    """The guard, asserted directly.

    Every test above drives one path that could regress. This one closes the
    class: a future leg that invents a sixth failure branch cannot express
    "rejected with 0x00" at all, so it cannot repeat this bug quietly.
    """
    import pytest

    with pytest.raises(ValueError):
        bridge_mod._ConnectRejected(0x00)
    # Non-zero codes are unaffected -- the SOCKS5 leg passes the upstream's own
    # rep through here, and the HTTP leg its mapped equivalents.
    for rep in (0x01, 0x02, 0x05, 0xFF):
        assert bridge_mod._ConnectRejected(rep).rep == rep


# ---------------------------------------------------------------------------
# PS-329 round 3. The request DIRECTION of the new HTTP leg.
#
# Rounds 1 and 2 hardened what the upstream SAYS BACK. These cover where the
# UNTRUSTED input actually enters: the destination host, which arrives from the
# local SOCKS handshake as a length-prefixed byte string that is `.decode()`d
# with no validation (`_read_local_handshake`) and was then interpolated
# straight into an HTTP request line.
#
# That is safe on the SOCKS5 leg and unsafe on the HTTP one for a structural
# reason, not an incidental one: SOCKS5 is BINARY and length-prefixed, so a CRLF
# in a hostname is two bytes in a counted field; HTTP is LINE-DELIMITED TEXT, so
# the same bytes end a header and start another. Measured on this branch before
# the fix, through the real listener with the existing `_claim`:
#
#   host field b"evil.com:443 HTTP/1.1\r\nX-Injected: yes\r\nHost: evil.com"
#     -> the upstream received an X-Injected header; browser told rep=0x00
#   host field with a BLANK LINE
#     -> the upstream received THREE "CONNECT " occurrences, including a whole
#        second `CONNECT secret-internal.corp:22`; browser told rep=0x00
#   atyp=0x04 (2001:db8::1)
#     -> `CONNECT 2001:db8::1:443 HTTP/1.1`, which RFC 7230 5.3.3 forbids
#   IDN host
#     -> UnicodeEncodeError, whole leg failed (rep=0x01) while the SAME host
#        over the SOCKS5 leg returned rep=0x00 and carried payload
#
# Every assertion below is on the BYTES THE UPSTREAM SERVER OBSERVED and on the
# SOCKS reply the driver receives (AC1) -- never that a helper was called, and
# never a literal reply byte (Correction 1: refused vs granted).


def _socks5_connect(
    port: int,
    addr: bytes,
    atyp: int = 0x03,
    dst_port: int = 443,
) -> tuple[socket.socket, bytes]:
    """Handshake with the bridge and CONNECT to an ARBITRARY address.

    The sibling of `_socks5_request`, which pins `example.com`/`atyp=0x03`. The
    address is written as the browser would write it, so a hostile host field is
    delivered exactly the way a renderer's resolved name would be -- the bridge
    is not asked to do anything unusual to reach these paths.
    """
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"\x05\x01\x00")
    assert _recvn(s, 2) == b"\x05\x00"
    body = bytes([len(addr)]) + addr if atyp == 0x03 else addr
    s.sendall(b"\x05\x01\x00" + bytes([atyp]) + body + struct.pack(">H", dst_port))
    return s, _recvn(s, 10)


def _run_http_bridge_to(
    addr: bytes,
    atyp: int = 0x03,
    dst_port: int = 443,
    behavior: str = "ok",
) -> tuple[FakeHttpUpstream, bytes, socket.socket, ProxyBridge]:
    upstream = FakeHttpUpstream(behavior)
    upstream.start()
    bridge = _claim(ProxyBridge(upstream.url))
    bridge.start()
    try:
        client, reply = _socks5_connect(bridge.port, addr, atyp, dst_port)
    except Exception:
        bridge.stop()
        raise
    return upstream, reply, client, bridge


def test_a_CRLF_in_the_destination_host_cannot_inject_a_header():
    """The injection primitive, asserted on what the PROXY received.

    The operator's proxy is authenticated with their credential, so a header
    this bridge did not author reaching it is the operator's request being
    rewritten by page content.
    """
    hostile = b"evil.com:443 HTTP/1.1\r\nX-Injected: yes\r\nHost: evil.com"
    upstream, reply, client, bridge = _run_http_bridge_to(hostile)
    try:
        head = upstream.request_head or b""
        assert b"X-Injected" not in head, (
            f"an attacker-chosen header reached the operator's authenticated "
            f"proxy: {head!r}"
        )
        assert head.count(b"CONNECT ") <= 1, (
            f"more than one request head reached the upstream: {head!r}"
        )
        assert len(reply) >= 2, f"the browser got no SOCKS reply at all: {reply!r}"
        assert reply[1] != 0x00, (
            f"a target the bridge could not render was reported to the browser "
            f"as an OPEN TUNNEL: reply={reply!r}"
        )
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_a_blank_line_in_the_destination_host_cannot_smuggle_a_second_CONNECT():
    """Full request smuggling: two CONNECTs from one bridge connection.

    Sharper than the header case -- the second request names a target and a PORT
    the bridge never sanctioned, spent against the operator's credential and
    appearing at their exit IP.
    """
    hostile = (
        b"good.com:443 HTTP/1.1\r\nHost: good.com\r\n\r\n"
        b"CONNECT secret-internal.corp:22 HTTP/1.1\r\nHost: secret-internal.corp"
    )
    upstream, reply, client, bridge = _run_http_bridge_to(hostile)
    try:
        head = upstream.request_head or b""
        assert head.count(b"CONNECT ") <= 1, (
            f"a second CONNECT was smuggled to the upstream proxy: {head!r}"
        )
        assert b"secret-internal.corp" not in head, (
            f"an unsanctioned target reached the operator's proxy: {head!r}"
        )
        assert len(reply) >= 2 and reply[1] != 0x00, (
            f"an unrenderable target was reported as an open tunnel: {reply!r}"
        )
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_an_ipv6_destination_is_bracketed_in_the_request_line():
    """RFC 7230 5.3.3. Unbracketed is genuinely ambiguous, not merely untidy.

    `urlsplit("//2001:db8::1:443")` raises ValueError on the unbracketed form,
    so a proxy that parses its request line the way Python does cannot honour
    it. The SOCKS5 leg carries the family structurally and never had this
    problem -- this is the HTTP leg being brought level.
    """
    v6 = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
    upstream, reply, client, bridge = _run_http_bridge_to(v6, atyp=0x04)
    try:
        head = upstream.request_head or b""
        assert head.startswith(b"CONNECT [2001:db8::1]:443 HTTP/1.1\r\n"), (
            f"the upstream received a malformed request line: {head!r}"
        )
        assert b"Host: [2001:db8::1]:443\r\n" in head, (
            f"the Host header was not bracketed either: {head!r}"
        )
        assert len(reply) >= 2 and reply[1] == 0x00
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_an_IDN_destination_reaches_the_http_proxy_as_punycode():
    """A capability the HTTP leg lost relative to the SOCKS5 one.

    The request is encoded latin-1 and simply cannot carry a non-ASCII host: the
    encode raised, and because a UnicodeEncodeError is a plain Exception it took
    the RETRY arm -- three attempts and two sleeps for something deterministic.

    The payload echo makes this a claim about a working TUNNEL rather than about
    a handshake that returned. And the A-label is a TRANSFORMATION of the name,
    not a RESOLUTION of it: the upstream still receives a name to resolve
    itself, so remote DNS is intact.
    """
    upstream, reply, client, bridge = _run_http_bridge_to("пример.рф".encode())
    try:
        head = upstream.request_head or b""
        assert head.startswith(b"CONNECT xn--e1afmkfd.xn--p1ai:443 HTTP/1.1\r\n"), (
            f"the upstream did not receive the punycode A-label: {head!r}"
        )
        assert len(reply) >= 2 and reply[1] == 0x00, (
            f"an IDN destination was refused by the http leg: reply={reply!r}"
        )
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping", "the IDN tunnel carried nothing"
    finally:
        client.close()
        bridge.stop()
        upstream.join(timeout=5)


def test_THE_CONTROL_the_same_IDN_host_already_worked_over_socks5():
    """The control for the test above, IN THE SAME RUN.

    Without it, "the http leg now carries an IDN host" is not obviously a gap
    being closed -- it could be a capability neither leg ever had. The SOCKS5 leg
    carries the name UTF-8 in a length-prefixed field and always did.
    """
    upstream = FakeUpstream("ok")
    upstream.start()
    bridge = _claim(ProxyBridge(upstream.url))
    bridge.start()
    client = None
    try:
        client, reply = _socks5_connect(bridge.port, "пример.рф".encode())
        assert len(reply) >= 2 and reply[1] == 0x00, (
            f"the socks5 leg refused an IDN host: reply={reply!r}"
        )
        assert upstream.target == ("пример.рф", 443), (
            f"the socks5 upstream saw {upstream.target!r}"
        )
        client.sendall(b"ping")
        assert _recvn(client, 4) == b"ping"
    finally:
        if client is not None:
            client.close()
        bridge.stop()
        upstream.join(timeout=5)
