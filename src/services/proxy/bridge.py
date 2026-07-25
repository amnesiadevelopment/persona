"""Local credential-stripping SOCKS5 bridge.

Chromium's --proxy-server cannot carry SOCKS5 username/password. This bridge
listens on 127.0.0.1 with no auth, and forwards every connection to an
upstream SOCKS5 proxy, performing username/password auth on the way out.
The browser is pointed at the local listener; credentials never touch the
browser command line.
"""

import asyncio
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse

_UPSTREAM_TIMEOUT = 15.0

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
_KEEPALIVE_IDLE = 30
_KEEPALIVE_INTVL = 10
_KEEPALIVE_CNT = 4

# Max idle (no bytes either way) before the whole tunnel is reaped so the
# browser rebuilds it. A commercial residential/mobile upstream silently reaps a
# low-traffic long-poll leg with NO FIN/RST, leaving reader.read() hung forever —
# TCP keepalive often doesn't survive the far NAT, so the collab channel dies and
# Sheets sticks on "Working" (#184). A bounded idle reap (microsocks/gost/socat
# pattern) ends the dead poll cleanly; Google's browserchannel immediately opens
# a fresh `bind`. Must sit ABOVE a legal long-poll hold (Google holds ~tens of
# seconds with no bytes) and BELOW the upstream's reap window (~60-120s).
_TUNNEL_IDLE_TIMEOUT = 55.0

# Test hook: every tunnel socket (client-accept + upstream) we tuned, so a test
# can assert the options are really set on the live sockets.
_tunnel_sockets: "list[socket.socket]" = []


def _debug_tunnel_sockets() -> "list[socket.socket]":
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
        _tunnel_sockets.append(sock)
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
        self._up_user = p.username or ""
        self._up_pass = p.password or ""
        self._port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> int:
        """Start the listener in a background thread; return the local port."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
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
            self._handle_client, "127.0.0.1", 0
        )
        self._port = self._server.sockets[0].getsockname()[1]
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
            target = await _read_local_handshake(reader, writer)
            if target is None:
                _trace(f"conn={cid} REJECT no-handshake")
                return
            host, port = target
            try:
                up_r, up_w = await asyncio.wait_for(
                    self._open_upstream(host, port), _UPSTREAM_TIMEOUT
                )
            except Exception as exc:
                rep = exc.rep if isinstance(exc, _ConnectRejected) else 0x01
                _trace(f"conn={cid} host={host}:{port} UPSTREAM-FAIL "
                       f"{type(exc).__name__} rep={rep} t={time.monotonic()-t0:.1f}s")
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
        r, w = await asyncio.open_connection(self._up_host, self._up_port)
        _tune_tunnel_socket(_sock_of(w))
        try:
            # greeting: offer no-auth + user/pass
            w.write(b"\x05\x02\x00\x02")
            await w.drain()
            ver, method = await r.readexactly(2)
            if method == 0x02:
                auth = (
                    b"\x01"
                    + bytes([len(self._up_user)])
                    + self._up_user.encode()
                    + bytes([len(self._up_pass)])
                    + self._up_pass.encode()
                )
                w.write(auth)
                await w.drain()
                _, status = await r.readexactly(2)
                if status != 0x00:
                    raise ConnectionError("upstream auth failed")
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
    """Pump both directions of a tunnel until BOTH ends EOF or the tunnel goes
    idle past `_TUNNEL_IDLE_TIMEOUT`.

    Each direction, on EOF, half-closes ITS peer's write side (a TCP FIN) so the
    peer learns "no more data this way" — the OTHER direction keeps running.
    Google Sheets' collab channel is a browserchannel long-poll: the browser
    sends its request, half-closes its write side (client->upstream EOF), and the
    server STREAMS the response back afterwards. Tearing the tunnel down on the
    FIRST direction's EOF killed that streamed response mid-flight (#184), so we
    await BOTH directions.

    But a commercial residential/mobile upstream silently reaps a low-traffic
    long-poll leg with NO FIN/RST — reader.read() then hangs forever, the collab
    channel never re-opens, and Sheets sticks on "Working". TCP keepalive often
    doesn't survive the far NAT. A shared idle watchdog reaps the whole tunnel
    when NO byte flows either way for `_TUNNEL_IDLE_TIMEOUT`; closing both writers
    unblocks the hung reads, and the browser immediately rebuilds the poll. The
    timeout sits above a legal long-poll hold and below the upstream reap window
    (microsocks/gost/socat use the same bounded-idle safety net)."""
    activity = [_loop_time()]  # last time ANY byte crossed the tunnel
    closed = asyncio.Event()

    def bump():
        activity[0] = _loop_time()

    c2u = asyncio.ensure_future(_pipe(client_r, up_w, bump, cid, host, "c2u"))
    u2c = asyncio.ensure_future(_pipe(up_r, client_w, bump, cid, host, "u2c"))
    watch = asyncio.ensure_future(
        _idle_watchdog(activity, closed, up_w, client_w, cid, host)
    )
    try:
        await asyncio.gather(c2u, u2c, return_exceptions=True)
    finally:
        closed.set()
        watch.cancel()
        with _suppress():
            await asyncio.gather(watch, return_exceptions=True)
        for w in (up_w, client_w):
            with _suppress():
                w.close()


def _loop_time() -> float:
    try:
        return asyncio.get_event_loop().time()
    except Exception:
        return time.monotonic()


async def _idle_watchdog(activity, closed, up_w, client_w, cid=0, host="?"):
    """Reap the tunnel when no byte has crossed it for _TUNNEL_IDLE_TIMEOUT."""
    while not closed.is_set():
        idle = _loop_time() - activity[0]
        remaining = _TUNNEL_IDLE_TIMEOUT - idle
        if remaining <= 0:
            _trace(f"conn={cid} host={host} IDLE-REAP after "
                   f"{_TUNNEL_IDLE_TIMEOUT:.0f}s")
            for w in (up_w, client_w):
                with _suppress():
                    w.close()
            return
        try:
            await asyncio.wait_for(closed.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            pass


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
    try:
        while True:
            data = await reader.read(262144)
            if not data:
                break
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
        reason = type(exc).__name__
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
