"""Local credential-stripping SOCKS5 bridge.

Chromium's --proxy-server cannot carry SOCKS5 username/password. This bridge
listens on 127.0.0.1 with no auth, and forwards every connection to an
upstream SOCKS5 proxy, performing username/password auth on the way out.
The browser is pointed at the local listener; credentials never touch the
browser command line.
"""

import asyncio
import socket
import struct
import threading
from urllib.parse import urlparse


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
        try:
            target = await _read_local_handshake(reader, writer)
            if target is None:
                writer.close()
                return
            host, port = target
            up_r, up_w = await self._open_upstream(host, port)
            await asyncio.gather(
                _pipe(reader, up_w),
                _pipe(up_r, writer),
            )
        except Exception:
            pass
        finally:
            with _suppress():
                writer.close()

    async def _open_upstream(
        self,
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        r, w = await asyncio.open_connection(self._up_host, self._up_port)
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


async def _read_local_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[str, int] | None:
    """Accept a no-auth SOCKS5 client and return its CONNECT target."""
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
    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")  # success
    await writer.drain()
    return host, port


async def _read_connect_reply(reader: asyncio.StreamReader) -> None:
    ver, rep, _rsv, atyp = await reader.readexactly(4)
    if rep != 0x00:
        raise ConnectionError(f"upstream CONNECT failed: {rep}")
    if atyp == 0x01:
        await reader.readexactly(4)
    elif atyp == 0x03:
        ln = (await reader.readexactly(1))[0]
        await reader.readexactly(ln)
    elif atyp == 0x04:
        await reader.readexactly(16)
    await reader.readexactly(2)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        with _suppress():
            writer.close()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True
