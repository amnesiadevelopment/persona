"""Local credential-stripping SOCKS5 bridge.

Chromium's --proxy-server cannot carry SOCKS5 username/password. This bridge
listens on 127.0.0.1 and forwards every connection to an upstream SOCKS5 proxy,
performing username/password auth on the way out. The browser is pointed at the
local listener; credentials never touch the browser command line.

The local side speaks SOCKS5 "no auth" because the browser it serves CANNOT send
a credential - that inability is the whole reason this bridge exists. So the
caller is VERIFIED rather than challenged: a connection is served only when the
process on the other end is the browser this bridge was started for (see
``core.peerauth``). Without that check any local process got free authenticated
egress through the operator's paid exit - traffic billed to their account and
appearing at their exit IP.
"""

import asyncio
import os
import socket
import struct
import threading
import time
from urllib.parse import unquote, urlparse

from ...core.peerauth import PeerGate, allows_async

# Time budget for ONE upstream CONNECT attempt (TCP to the proxy + SOCKS5
# greeting/auth/CONNECT). Over Tor the proxy leg double-hops, so a single attempt
# can take much longer than a direct connection: the bridge trace showed Google
# hosts hitting exactly 15.0s and being REJECTED to the browser (rep=1 →
# ERR_SOCKS_CONNECTION_FAILED), which is what left Sheets stuck on "Working" —
# chromium's realtime/widget channels never opened (#184). 45s comfortably clears
# a slow-but-live Tor circuit; a genuinely dead attempt is retried on a fresh one.
_UPSTREAM_TIMEOUT = 45.0

# A rejected/timed-out CONNECT over Tor is usually a transient bad circuit, not a
# dead target — retry a couple of times before failing the browser's request, so
# one unlucky circuit doesn't drop a channel Sheets needs and hang on "Working".
_UPSTREAM_ATTEMPTS = 3

# StreamReader buffer per tunnel direction. One tunnel carries a whole
# multiplexed HTTP/2 connection: a large download and small control frames
# (WINDOW_UPDATE, the browserchannel /bind long-poll) share it. asyncio's 64 KiB
# default is far too small — a busy download fills the buffer, asyncio pauses the
# transport, and the reverse control frames stall. Over a high-latency proxy that
# wedges the whole h2 connection and Sheets sticks on "Working" (#184). A wide
# buffer lets one busy stream fill without starving the others.
_STREAM_LIMIT = 8 * 1024 * 1024

# Opt-in tunnel tracing for #184 debugging: set PERSONA_BRIDGE_LOG=/path to log
# every tunnel's open/close (target host, bytes each way, lifetime, close
# reason). Off by default — no prod overhead, no output unless the env is set.
_TRACE_PATH = os.environ.get("PERSONA_BRIDGE_LOG")
_trace_lock = threading.Lock()
_conn_seq = 0


def _trace(msg: str) -> None:
    if not _TRACE_PATH:
        return
    try:
        with _trace_lock, open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass

# TCP keepalive on both ends of a tunnel so the OS detects a SILENT half-open
# proxy circuit (Tor wedges: the socket stays open with no bytes and no EOF)
# and drops it — otherwise both _pipe directions block on read() forever and a
# long-lived stream leaks a one-way-dead tunnel. Probe after 30s idle, every
# 10s, give up after 4 misses (~70s to a forced RST).
# Keepalive detects a truly dead half-open circuit (Tor wedge: socket open, no
# bytes, no EOF). Timers must be GENEROUS over Tor: an aggressive 30s idle + 4x10s
# probe window (~70s to RST) tore down slow-but-LIVE transfers — a big Sheets JS
# bundle (waffle/date-picker/currency) crawling in over Tor can pause >30s, and
# the keepalive probe's own reply may not survive the far NAT in time, so the OS
# RST'd a live channel and the widget never finished loading (#184: 'Trying to
# connect', dead calendar/custom-currency). 120s idle + 5x15s (~195s to RST) lets
# a slow live channel breathe while still reaping a genuinely dead one.
_KEEPALIVE_IDLE = 120
_KEEPALIVE_INTVL = 15
_KEEPALIVE_CNT = 5

# Test hook: the recently-tuned tunnel sockets (client-accept + upstream), so a
# test can assert the options are really set on live sockets. Bounded so a long
# session doesn't pin every socket ever tuned for the process lifetime — an
# unbounded list was a slow memory leak. A WeakSet is unusable here: on Windows
# the object returned by get_extra_info("socket") has no other strong referent,
# so it would be GC'd out mid-tunnel and drop the reference the transport relies
# on. Keep a small strong-ref ring instead.
_TUNNEL_SOCKET_RING = 64
_tunnel_sockets: "list[socket.socket]" = []
_tunnel_sockets_lock = threading.Lock()


def _debug_tunnel_sockets() -> "list[socket.socket]":
    with _tunnel_sockets_lock:
        return list(_tunnel_sockets)


def _tune_tunnel_socket(sock: "socket.socket | None") -> None:
    """Tune a tunnel-end stream socket for a long-lived interactive proxy hop.

    Two things, both best-effort (silent on any platform/option the socket
    doesn't support — the tunnel works without them, they only make it behave):

    1. TCP_NODELAY: disable Nagle's algorithm so a relayed interactive stream's
       small writes go out immediately instead of being held to coalesce with the
       next segment. A proxy carrying interactive traffic should never batch.

    2. TCP keepalive with aggressive timers: let the OS detect and drop a SILENT
       half-open circuit (Tor wedges: socket open, no bytes, no EOF) instead of
       both _pipe directions blocking on read() forever."""
    if sock is None:
        return
    tuned = False
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        tuned = True
    except OSError:
        pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        tuned = True
    except OSError:
        pass
    if tuned:
        with _tunnel_sockets_lock:
            _tunnel_sockets.append(sock)
            if len(_tunnel_sockets) > _TUNNEL_SOCKET_RING:
                del _tunnel_sockets[:-_TUNNEL_SOCKET_RING]
    # Per-connection keepalive idle/interval/count where the platform exposes
    # them. Linux: TCP_KEEPIDLE/INTVL/CNT. macOS: TCP_KEEPALIVE is the idle time.
    # Windows: no per-socket tunables here (system defaults apply); SO_KEEPALIVE
    # above is enough to get the OS probing.
    for opt, val in (
        ("TCP_KEEPIDLE", _KEEPALIVE_IDLE),
        ("TCP_KEEPALIVE", _KEEPALIVE_IDLE),
        ("TCP_KEEPINTVL", _KEEPALIVE_INTVL),
        ("TCP_KEEPCNT", _KEEPALIVE_CNT),
    ):
        code = getattr(socket, opt, None)
        if code is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, code, val)
        except OSError:
            pass


def _sock_of(writer: "asyncio.StreamWriter") -> "socket.socket | None":
    try:
        return writer.get_extra_info("socket")
    except Exception:
        return None


class _ConnectRejected(ConnectionError):
    def __init__(self, rep: int) -> None:
        super().__init__(f"upstream CONNECT failed: {rep}")
        self.rep = rep


class ProxyBridge:
    def __init__(self, upstream_url: str) -> None:
        p = urlparse(upstream_url if "://" in upstream_url else "socks5://" + upstream_url)
        self._up_host = p.hostname or ""
        self._up_port = p.port or 1080
        # Decode percent-encoded creds (build_proxy_url encodes them) so the SOCKS5
        # auth sends the real username/password, not the %XX form (audit6 #8).
        self._up_user = unquote(p.username) if p.username else ""
        self._up_pass = unquote(p.password) if p.password else ""
        # SOCKS5 user/pass auth length-prefixes each credential with a single
        # byte, so neither may exceed 255 UTF-8 bytes. Reject up front (fail
        # CLOSED): a credential that can't be sent means we can't authenticate to
        # the proxy, and launching anyway would fall through to a DIRECT clearnet
        # connection.
        if len(self._up_user.encode("utf-8")) > 255 or len(self._up_pass.encode("utf-8")) > 255:
            raise ValueError("SOCKS5 username/password exceeds 255 bytes")
        self._port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()
        # Only the browser this bridge was started for may use it. The listener
        # binds before that browser exists (its port goes on the command line),
        # so the gate starts unclaimed and the launcher calls bind_to_process
        # once the process is up.
        self._gate = PeerGate("proxy bridge")

    @property
    def port(self) -> int:
        return self._port

    def bind_to_process(self, pid: int | None) -> None:
        """Declare the browser process allowed to use this bridge.

        Until this is called the bridge serves nobody: an unclaimed listener has
        no legitimate client, so a connection that arrives before the browser is
        spawned waits briefly and is then refused."""
        self._gate.bind_to_process(pid)

    def start(self) -> int:
        """Start the listener in a background thread; return the local port.

        Raises if the listener never bound (port stays 0). Returning port 0 would
        make the caller build socks5://127.0.0.1:0, which Chromium treats as
        no-proxy → a silent DIRECT clearnet launch (fail-open). Fail CLOSED."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if not self._port:
            raise RuntimeError("proxy bridge failed to bind a local port")
        return self._port

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
            self._loop.run_forever()
        finally:
            if self._server is not None:
                self._server.close()
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self._loop.run_until_complete(
                    asyncio.gather(*tasks, return_exceptions=True)
                )
            self._loop.close()

    async def _serve(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", 0, limit=_STREAM_LIMIT
        )
        self._port = self._server.sockets[0].getsockname()[1]
        self._gate.set_listen_port(self._port)
        self._ready.set()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        global _conn_seq
        _conn_seq += 1
        cid = _conn_seq
        host = "?"
        t0 = time.monotonic()
        try:
            # FIRST, before anything else: is the caller the browser this bridge
            # was started for? Refuse here and the connection costs an attacker
            # nothing and tells them nothing — no SOCKS5 greeting is answered, so
            # the listener is not even confirmed to BE a SOCKS5 proxy, and above
            # all no upstream tunnel is opened, so the operator's proxy
            # credentials are never spent on a caller we did not authorize.
            peer = writer.get_extra_info("peername")
            if not await allows_async(self._gate, peer):
                _trace(f"conn={cid} REJECT unauthorized-peer")
                return
            target = await _read_local_handshake(reader, writer)
            if target is None:
                _trace(f"conn={cid} REJECT no-handshake")
                return
            host, port = target
            up_r = up_w = None
            last_exc: Exception | None = None
            for attempt in range(_UPSTREAM_ATTEMPTS):
                try:
                    up_r, up_w = await asyncio.wait_for(
                        self._open_upstream(host, port), _UPSTREAM_TIMEOUT
                    )
                    last_exc = None
                    break
                except _ConnectRejected as exc:
                    # The proxy gave a definitive SOCKS reply (host not allowed,
                    # connection refused, auth): retrying the same target won't
                    # change it — fail fast, don't burn attempts.
                    last_exc = exc
                    break
                except Exception as exc:
                    # A timeout / dropped connection to the proxy is a transient
                    # Tor circuit problem (#184): retry on a fresh circuit before
                    # failing the browser, so one unlucky circuit doesn't leave a
                    # Sheets channel unopened and stuck on "Working".
                    last_exc = exc
                    _trace(f"conn={cid} host={host}:{port} UPSTREAM-TRY{attempt} "
                           f"{type(exc).__name__} t={time.monotonic()-t0:.1f}s")
                    if attempt < _UPSTREAM_ATTEMPTS - 1:
                        await asyncio.sleep(0.5)
            if last_exc is not None:
                rep = last_exc.rep if isinstance(last_exc, _ConnectRejected) else 0x01
                _trace(f"conn={cid} host={host}:{port} UPSTREAM-FAIL "
                       f"{type(last_exc).__name__} rep={rep} "
                       f"t={time.monotonic()-t0:.1f}s")
                writer.write(b"\x05" + bytes([rep]) + b"\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")  # success
            await writer.drain()
            _trace(f"conn={cid} host={host}:{port} OPEN")
            # Tune the browser-facing socket too (NODELAY + keepalive): the WS
            # frames arrive from the browser on this end, and it can go
            # silent-dead the same way the upstream can.
            _tune_tunnel_socket(_sock_of(writer))
            await _splice(reader, up_w, up_r, writer, cid=cid, host=host)
        except Exception as exc:
            _trace(f"conn={cid} host={host} HANDLER-EXC {type(exc).__name__}")
        finally:
            _trace(f"conn={cid} host={host} CLOSE t={time.monotonic()-t0:.1f}s")
            with _suppress():
                writer.close()

    async def _open_upstream(
        self,
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        r, w = await asyncio.open_connection(
            self._up_host, self._up_port, limit=_STREAM_LIMIT
        )
        _tune_tunnel_socket(_sock_of(w))
        try:
            # greeting: offer no-auth + user/pass
            w.write(b"\x05\x02\x00\x02")
            await w.drain()
            ver, method = await r.readexactly(2)
            if method == 0x02:
                # Length-prefix the BYTE length, not the str length: SOCKS5 sends
                # a 1-byte length then the raw bytes. For non-ASCII creds the UTF-8
                # encoding is longer than the character count, so len(str) desyncs
                # the frame (and can exceed 255). Encode first, then measure.
                u = self._up_user.encode("utf-8")
                p = self._up_pass.encode("utf-8")
                if len(u) > 255 or len(p) > 255:
                    raise RuntimeError("SOCKS5 username/password exceeds 255 bytes")
                auth = b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p
                w.write(auth)
                await w.drain()
                _, status = await r.readexactly(2)
                if status != 0x00:
                    # bad credentials — definitive, retrying won't help, so raise
                    # a _ConnectRejected (the handler fails fast, no retry loop).
                    raise _ConnectRejected(0x01)
            # CONNECT request, domain name
            host_b = host.encode()
            req = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack(">H", port)
            w.write(req)
            await w.drain()
            await _read_connect_reply(r)
            return r, w
        except BaseException:
            with _suppress():
                w.close()
            raise


async def _read_local_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[str, int] | None:
    """Accept a no-auth SOCKS5 client and return its CONNECT target.

    The CONNECT reply is NOT sent here; the caller replies success or
    failure once the upstream tunnel is known to be up or down. Replying
    success early makes the browser start TLS into a tunnel that may
    never open, which surfaces as ERR_CONNECTION_CLOSED handshake spam.
    """
    ver, nmethods = await reader.readexactly(2)
    if ver != 0x05:
        return None
    await reader.readexactly(nmethods)
    writer.write(b"\x05\x00")  # no auth
    await writer.drain()

    ver, cmd, _rsv, atyp = await reader.readexactly(4)
    if cmd != 0x01:  # CONNECT only
        writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        return None
    if atyp == 0x01:
        host = socket.inet_ntoa(await reader.readexactly(4))
    elif atyp == 0x03:
        ln = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(ln)).decode()
    elif atyp == 0x04:
        host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
    else:
        return None
    port = struct.unpack(">H", await reader.readexactly(2))[0]
    return host, port


async def _read_connect_reply(reader: asyncio.StreamReader) -> None:
    ver, rep, _rsv, atyp = await reader.readexactly(4)
    if rep != 0x00:
        raise _ConnectRejected(rep)
    if atyp == 0x01:
        await reader.readexactly(4)
    elif atyp == 0x03:
        ln = (await reader.readexactly(1))[0]
        await reader.readexactly(ln)
    elif atyp == 0x04:
        await reader.readexactly(16)
    await reader.readexactly(2)


async def _splice(
    client_r: asyncio.StreamReader,
    up_w: asyncio.StreamWriter,
    up_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    cid: int = 0,
    host: str = "?",
) -> None:
    """Pump both directions of a tunnel until BOTH ends EOF.

    Each direction, on EOF, half-closes ITS peer's write side (a TCP FIN) so the
    peer learns "no more data this way" — the OTHER direction keeps running.
    Google Sheets' collab channel is a browserchannel long-poll: the browser
    sends its request, half-closes its write side (client->upstream EOF), and the
    server STREAMS the response back afterwards. Tearing the tunnel down on the
    FIRST direction's EOF killed that streamed response mid-flight (#184), so we
    await BOTH directions.

    NO blind idle timer here (#184, chromium-only): the /bind long-poll holds the
    connection OPEN and SILENT for tens of seconds waiting for a collab event. A
    bounded idle-reap treated that legitimate silence as dead and tore the tunnel
    down, so chromium kept losing the channel and Sheets stuck on "Working"
    (Firefox uses native SOCKS, never this bridge, so it never saw this). A truly
    dead half-open circuit is caught WITHOUT a byte-idle timer: `_pipe`'s read
    returns EOF on a real FIN, and the aggressive TCP keepalive set by
    `_tune_tunnel_socket` (idle 30s, 4 probes/10s) forces an RST on a silently
    dropped socket, which surfaces as a read error and ends the pump. A live but
    idle long-poll keeps its socket open, so keepalive stays happy and it
    survives — exactly the behaviour Firefox's native proxy path already has."""
    c2u = asyncio.ensure_future(_pipe(client_r, up_w, None, cid, host, "c2u"))
    u2c = asyncio.ensure_future(_pipe(up_r, client_w, None, cid, host, "u2c"))
    try:
        await asyncio.gather(c2u, u2c, return_exceptions=True)
    finally:
        for w in (up_w, client_w):
            with _suppress():
                w.close()


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    bump=None,
    cid: int = 0,
    host: str = "?",
    dir: str = "?",
) -> None:
    """Copy reader→writer until EOF, then half-close the writer (send a FIN)
    without closing the reverse direction, so a long-poll's streamed response
    survives the client's request-side half-close (#184). Every chunk bumps the
    shared idle timer so the watchdog only reaps a truly silent tunnel.

    Only drain when the write buffer is actually backed up (above the transport's
    high-water mark), NOT after every write. Awaiting drain() on every 64 KiB
    chunk serialised read↔write and cut throughput ~3x on a large response (a
    375 KiB Sheets JS took 7.8s through the bridge vs 2.5s straight to the proxy),
    which left Google Sheets stuck on "Working" while its calc-worker JS crawled
    in (#184). Letting writes pipeline and draining only on backpressure restores
    full speed without unbounded buffering."""
    total = 0
    reason = "eof"
    _chunks = 0
    try:
        while True:
            data = await reader.read(262144)
            if not data:
                break
            _chunks += 1
            if _TRACE_PATH:
                _trace(f"conn={cid} host={host} pipe={dir} CHUNK #{_chunks} n={len(data)} total={total+len(data)}")
            if bump is not None:
                bump()
            total += len(data)
            writer.write(data)
            # Drain only under real backpressure — the transport reports it once
            # the write buffer passes its high-water mark. This keeps memory
            # bounded without paying a full round-trip stall per chunk.
            transport = writer.transport
            if transport is not None and transport.get_write_buffer_size() > 262144:
                await writer.drain()
    except Exception as exc:
        # The message too, not only the class. Every proxied browser session
        # runs through here, and a dead tunnel recorded as a bare `OSError`
        # says what KIND of thing went wrong without saying what did:
        # `[Errno 104] Connection reset by peer` is the half that identifies
        # the failure. Trace text only - the pipe's behaviour is unchanged.
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        _trace(f"conn={cid} host={host} pipe={dir} DONE bytes={total} reason={reason}")
        # Half-close: signal EOF to the peer (write_eof) but leave the reverse
        # direction alive. If the transport can't half-close, fall back to a
        # full close so we never leak the tunnel.
        try:
            if writer.can_write_eof():
                writer.write_eof()
            else:
                writer.close()
        except Exception:
            with _suppress():
                writer.close()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True
