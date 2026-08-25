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
                for entry in trace.read_text().splitlines():
                    if "DONE" in entry and "reason=" in entry:
                        reason = entry.split("reason=", 1)[1]
                        if reason != "eof":
                            line = entry
                            break
            if line is None:
                time.sleep(0.05)

        assert line is not None, (
            "no non-eof DONE line in the trace: "
            + (trace.read_text() if trace.exists() else "<no trace written>")
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
                text = trace.read_text()
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

    written = trace.read_text()
    assert "SOMETHING" in written, "the line itself must still be recorded"
    assert "0th3r" not in written
    assert "bob" not in written
    assert "***:***@" in written
