import asyncio
import ipaddress
import json
import socket
import ssl
import struct
import urllib.parse

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from .proxy_parser import parse_proxy

# Schemes that speak SOCKS, not HTTP CONNECT. aiohttp's `proxy=` parameter
# implements HTTP(S) proxies ONLY and does not validate the scheme: handed a
# socks5:// URL it sends an HTTP `CONNECT`, which a SOCKS server (waiting for a
# \x05 greeting) never answers. socks5 is persona's default scheme, so the geo
# check failed permanently for the app's primary proxy type — and a proxy with
# no country/timezone makes the launcher fall back to the operator's REAL host
# timezone inside a proxied profile. These schemes take the SOCKS path below.
_SOCKS_SCHEMES = frozenset({"socks4", "socks4a", "socks5", "socks5h"})

# Hard cap on the geo response we will buffer. The body is attacker-influenced
# (hostile endpoint or a MITM on the exit), so an unbounded read is a memory DoS.
_MAX_GEO_BODY = 256 * 1024


def _ssl_context() -> ssl.SSLContext:
    """TLS context for the geo probe: full certificate + hostname verification.

    Split out as a named seam so a test can point the probe at a local listener
    with its own CA without weakening verification in the real path.
    """
    return ssl.create_default_context()


def _is_blocked_proxy_host(server: str) -> bool:
    """True if the proxy endpoint resolves to loopback / private / link-local /
    cloud-metadata space. check_proxy() connects to whatever host:port the user
    pasted, so an unconstrained check is a caller-controlled port-scan oracle
    against the local network and 169.254.169.254. A real upstream proxy is a
    public host, so blocking private ranges costs nothing legitimate."""
    host = urllib.parse.urlparse(server).hostname or ""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # can't resolve -> let the connect fail normally
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or addr == "169.254.169.254"
        ):
            return True
    return False


def _validate_geo(
    code: str, tz: str, lat, lon
) -> tuple[str, str, float | None, float | None]:
    """Sanitize the geo fields the check endpoint returned before they are
    persisted into the profile's fingerprint. The response is attacker-influenced
    (a MITM or a hostile endpoint could inject a bogus timezone/country to skew
    the spoof), so drop anything malformed rather than store it: a 2-letter
    country code, a plausible tz string, and lat/lon inside valid ranges."""
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        code = ""
    tz = tz if isinstance(tz, str) and "/" in tz else ""
    try:
        latf = float(lat)
        if not (-90.0 <= latf <= 90.0):
            latf = None
    except (TypeError, ValueError):
        latf = None
    try:
        lonf = float(lon)
        if not (-180.0 <= lonf <= 180.0):
            lonf = None
    except (TypeError, ValueError):
        lonf = None
    return code, tz, latf, lonf


def proxy_ok_message(code: str, country: str) -> str:
    """The activity-log message for a working proxy. Shows the exit COUNTRY (and
    flag) but never the exact exit IP: this tool's own logs are disk-backed and
    UI-visible, and a timestamped IP history would de-anonymize the operator and
    link separate personas. The IP is returned separately for the store and the
    file-only debug log, never for the activity log."""
    flag = flag_from_country_code(code)
    where = f"{flag} [{code}] {country}".strip() if country else ""
    return f"Proxy working. {where}".strip() if where else "Proxy working."


def flag_from_country_code(code: str) -> str:
    """Turn a two-letter ISO country code into a flag emoji.

    Empty/invalid codes yield an empty string.
    """
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


async def _recv_exactly(loop, sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = await loop.sock_recv(sock, n - len(buf))
        if not chunk:
            raise ConnectionError("proxy closed the connection during the handshake")
        buf += chunk
    return buf


async def _connect_socket(loop, host: str, port: int) -> socket.socket:
    """Open a non-blocking TCP socket to the proxy endpoint, trying every
    address family the host resolves to (so an IPv6-only proxy still works)."""
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    last: Exception | None = None
    for family, stype, proto, _canon, addr in infos:
        sock = socket.socket(family, stype, proto)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, addr)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise last or ConnectionError("could not connect to the proxy")


async def _socks5_connect(
    loop,
    sock: socket.socket,
    host: str,
    port: int,
    username: str,
    password: str,
) -> None:
    """RFC1928 greeting + optional RFC1929 user/pass auth + CONNECT.

    The target is sent as a DOMAIN NAME (atyp 0x03), never a pre-resolved IP:
    resolving the geo endpoint locally would emit a DNS query from the
    operator's real resolver for a host the proxy is supposed to reach.
    """
    u = (username or "").encode("utf-8")
    p = (password or "").encode("utf-8")
    # SOCKS5 length-prefixes each credential with a single byte (same limit the
    # bridge enforces at src/services/proxy/bridge.py:169) — a longer one cannot
    # be framed at all, so fail rather than send a desynced frame.
    if len(u) > 255 or len(p) > 255:
        raise ValueError("SOCKS5 username/password exceeds 255 bytes")

    await loop.sock_sendall(sock, b"\x05\x02\x00\x02" if u else b"\x05\x01\x00")
    ver, method = await _recv_exactly(loop, sock, 2)
    if ver != 0x05:
        raise ConnectionError("proxy did not answer with SOCKS5")
    if method == 0x02:
        if not u:
            raise ConnectionError("proxy requires credentials")
        await loop.sock_sendall(
            sock, b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p
        )
        _, status = await _recv_exactly(loop, sock, 2)
        if status != 0x00:
            raise ConnectionError("proxy rejected the credentials")
    elif method != 0x00:
        raise ConnectionError("proxy offered no supported auth method")

    host_b = host.encode("idna") if not host.isascii() else host.encode("ascii")
    if not host_b or len(host_b) > 255:
        raise ValueError("target host name is not addressable over SOCKS5")
    await loop.sock_sendall(
        sock,
        b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack(">H", port),
    )
    _ver, rep, _rsv, atyp = await _recv_exactly(loop, sock, 4)
    if rep != 0x00:
        raise ConnectionError(f"proxy refused CONNECT (reply {rep})")
    if atyp == 0x01:
        await _recv_exactly(loop, sock, 4)
    elif atyp == 0x03:
        ln = (await _recv_exactly(loop, sock, 1))[0]
        await _recv_exactly(loop, sock, ln)
    elif atyp == 0x04:
        await _recv_exactly(loop, sock, 16)
    else:
        raise ConnectionError("proxy sent an unknown bound-address type")
    await _recv_exactly(loop, sock, 2)


async def _socks4_connect(
    loop,
    sock: socket.socket,
    host: str,
    port: int,
    username: str,
) -> None:
    """SOCKS4a CONNECT (the 0.0.0.x destination-IP form that carries a domain
    name), so socks4/socks4a URLs the validator accepts also get a real
    handshake instead of an HTTP CONNECT no SOCKS server will answer."""
    uid = (username or "").encode("utf-8")
    host_b = host.encode("idna") if not host.isascii() else host.encode("ascii")
    if b"\x00" in uid or not host_b:
        raise ValueError("SOCKS4 user id or target host is not addressable")
    await loop.sock_sendall(
        sock,
        b"\x04\x01"
        + struct.pack(">H", port)
        + b"\x00\x00\x00\x01"  # 0.0.0.1 -> "the host name follows" (SOCKS4a)
        + uid
        + b"\x00"
        + host_b
        + b"\x00",
    )
    reply = await _recv_exactly(loop, sock, 8)
    if reply[1] != 0x5A:
        raise ConnectionError(f"proxy refused CONNECT (reply {reply[1]})")


async def _read_http_body(reader: asyncio.StreamReader, headers: dict) -> bytes:
    if headers.get("transfer-encoding", "").lower().startswith("chunked"):
        out = bytearray()
        while True:
            size = int((await reader.readuntil(b"\r\n")).split(b";")[0].strip(), 16)
            if size == 0:
                break
            if len(out) + size > _MAX_GEO_BODY:
                raise ValueError("geo response too large")
            out += await reader.readexactly(size)
            await reader.readexactly(2)  # trailing CRLF
        return bytes(out)
    length = headers.get("content-length", "")
    if length.isdigit():
        n = int(length)
        if n > _MAX_GEO_BODY:
            raise ValueError("geo response too large")
        return await reader.readexactly(n)
    body = await reader.read(_MAX_GEO_BODY + 1)
    if len(body) > _MAX_GEO_BODY:
        raise ValueError("geo response too large")
    return body


async def _http_get_json(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    path: str,
) -> tuple[int, dict | None]:
    """Minimal HTTP/1.1 GET over an already-established (TLS) stream."""
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: persona-proxy-check/1.0\r\n"
            "Accept: application/json\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()

    head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
    parts = head[0].split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    for line in head[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    if status != 200:
        return status, None
    data = json.loads(await _read_http_body(reader, headers))
    return status, (data if isinstance(data, dict) else None)


async def _geo_via_socks(
    proxy_config: dict, scheme: str, url: str
) -> tuple[int, dict | None]:
    """Fetch the geo endpoint through a real SOCKS handshake.

    Uses plain asyncio + the stdlib only — the same approach the in-tree bridge
    (src/services/proxy/bridge.py) already takes — so this adds no dependency
    and cannot raise at import time in an environment where PySocks or
    aiohttp_socks happen not to be installed.
    """
    target = urllib.parse.urlparse(url)
    target_host = target.hostname or ""
    target_port = target.port or (443 if target.scheme == "https" else 80)
    path = target.path or "/"
    upstream = urllib.parse.urlparse(proxy_config["server"])

    loop = asyncio.get_running_loop()
    sock = await _connect_socket(loop, upstream.hostname or "", upstream.port or 1080)
    try:
        if scheme.startswith("socks4"):
            await _socks4_connect(
                loop, sock, target_host, target_port, proxy_config.get("username", "")
            )
        else:
            await _socks5_connect(
                loop,
                sock,
                target_host,
                target_port,
                proxy_config.get("username", ""),
                proxy_config.get("password", ""),
            )
        # TLS is negotiated INSIDE the SOCKS tunnel, against the geo endpoint's
        # own certificate: the proxy carries opaque bytes and can no more read
        # or forge the response than it could before. (It also means the
        # credentials went out in the SOCKS handshake, not as the cleartext
        # pre-TLS `Proxy-Authorization: Basic` header aiohttp was emitting.)
        context = _ssl_context() if target.scheme == "https" else None
        reader, writer = await asyncio.open_connection(
            sock=sock,
            ssl=context,
            server_hostname=target_host if context else None,
        )
    except BaseException:
        sock.close()
        raise
    try:
        return await _http_get_json(reader, writer, target_host, path)
    finally:
        writer.close()


async def check_proxy(
    proxy_str: str, timeout: int = 10
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    """Probe a proxy. Returns
    (ok, message, country_code, country_name, ip, timezone, lat, lon)."""
    if not AIOHTTP_AVAILABLE:
        # NOT ok: a skipped check must never be recorded as a success (which
        # would set last_check_ok=True and, with empty geo, erase the proxy's
        # known-good country/timezone). ok=False leaves the geo fields intact.
        return False, "Proxy check skipped (aiohttp not installed)", "", "", "", "", None, None

    proxy_config = parse_proxy(proxy_str)
    if not proxy_config:
        return False, "Invalid proxy format", "", "", "", "", None, None

    if _is_blocked_proxy_host(proxy_config["server"]):
        return (
            False,
            "Proxy host is not allowed (private/loopback address)",
            "", "", "", "", None, None,
        )

    proxy_url = proxy_config["server"]
    if "username" in proxy_config:
        scheme, rest = proxy_url.split("://", 1)
        password = proxy_config.get("password", "")
        proxy_url = f"{scheme}://{proxy_config['username']}:{password}@{rest}"

    scheme = urllib.parse.urlparse(proxy_config["server"]).scheme.lower()

    try:
        if scheme in _SOCKS_SCHEMES:
            # aiohttp speaks HTTP CONNECT only and ignores the URL scheme, so
            # this branch must NOT go through it: a SOCKS server never answers
            # a CONNECT, the check failed permanently for persona's default
            # scheme, and a proxy left with no country/timezone makes the
            # launcher fall back to the operator's real host zone.
            status, data = await asyncio.wait_for(
                _geo_via_socks(proxy_config, scheme, "https://ipwho.is/"),
                timeout,
            )
        else:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                # HTTPS geo endpoint: over cleartext HTTP a MITM on the exit could
                # inject a bogus country/timezone that then feeds the persisted
                # fingerprint. ipwho.is serves the same fields over TLS for free.
                async with session.get(
                    "https://ipwho.is/",
                    proxy=proxy_url,
                ) as response:
                    status = response.status
                    data = await response.json() if status == 200 else None
        if status == 200:
            if not data.get("success", True):
                return False, "Proxy geo lookup failed", "", "", "", "", None, None
            ip = data.get("ip", "unknown")
            country = data.get("country", "")
            code = (data.get("country_code") or "").upper()
            tzobj = data.get("timezone")
            tz = (tzobj.get("id", "") if isinstance(tzobj, dict) else tzobj) or ""
            lat = data.get("latitude")
            lon = data.get("longitude")
            code, tz, lat, lon = _validate_geo(code, tz, lat, lon)
            return (
                True,
                proxy_ok_message(code, country),
                code, country, ip, tz, lat, lon,
            )
        return False, f"Proxy returned status {status}", "", "", "", "", None, None
    except asyncio.TimeoutError:
        return False, "Proxy connection timed out", "", "", "", "", None, None
    except aiohttp.ClientProxyConnectionError:
        return False, "Failed to connect to proxy", "", "", "", "", None, None
    except aiohttp.ClientError:
        # Don't echo the exception: for a DNS/connection failure it embeds the
        # proxy host:port, and this message reaches the disk-backed daily log +
        # the Activity Log, which the app otherwise keeps free of the endpoint.
        return False, "Proxy connection failed", "", "", "", "", None, None
    except OSError:
        # The SOCKS path's failures (TCP refused, handshake rejected, TLS) all
        # land here. Same rule as above: a fixed string, never the exception —
        # its text carries the proxy host:port.
        return False, "Failed to connect to proxy", "", "", "", "", None, None
    except Exception:
        return False, "Proxy check failed", "", "", "", "", None, None


def _run(
    proxy_str: str, timeout: int
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(check_proxy(proxy_str, timeout))
        finally:
            loop.close()
    except Exception as e:
        return False, f"Error checking proxy: {e!s}", "", "", "", "", None, None


def check_proxy_sync(proxy_str: str, timeout: int = 10) -> tuple[bool, str]:
    ok, message = _run(proxy_str, timeout)[:2]
    return ok, message


def check_proxy_detailed_sync(
    proxy_str: str,
    timeout: int = 10,
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    return _run(proxy_str, timeout)
