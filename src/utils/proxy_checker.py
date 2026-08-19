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
from .validation import PROXY_SCHEMES

# Exception classes for the except arms in check_proxy. They must NOT name
# `aiohttp` directly: the SOCKS path is stdlib-only and runs with aiohttp
# absent, and Python evaluates an except expression while another exception is
# in flight — an unbound `aiohttp` there would raise NameError and lose the
# real error. An empty tuple simply never matches.
if AIOHTTP_AVAILABLE:
    _PROXY_CONNECT_ERRORS: tuple[type[BaseException], ...] = (
        aiohttp.ClientProxyConnectionError,
    )
    _CLIENT_ERRORS: tuple[type[BaseException], ...] = (aiohttp.ClientError,)
else:
    _PROXY_CONNECT_ERRORS = ()
    _CLIENT_ERRORS = ()

#: The socks schemes the validator accepts, derived from ITS tuple rather than
#: retyped here — see PROXY_SCHEMES. Used for documentation and by the tests;
#: the runtime decision is _is_socks_scheme(), which is deliberately broader.
SOCKS_SCHEMES = frozenset(s for s in PROXY_SCHEMES if s.startswith("socks"))

# Hard cap on the geo response we will buffer. The body is attacker-influenced
# (hostile endpoint or a MITM on the exit), so an unbounded read is a memory DoS.
_MAX_GEO_BODY = 256 * 1024


def _is_socks_scheme(scheme: str) -> bool:
    """True for every scheme that must take a real SOCKS handshake.

    aiohttp's `proxy=` parameter implements HTTP(S) proxies ONLY and does not
    validate the scheme: handed a socks5:// URL it sends an HTTP `CONNECT`,
    which a SOCKS server (waiting for a \\x05 greeting) never answers. socks5 is
    persona's default scheme, so the geo check failed permanently for the app's
    primary proxy type — and a proxy left with no country/timezone makes the
    launcher fall back to the operator's REAL host timezone inside a proxied
    profile (services/browser/process.py:306-314). Routing a socks URL to
    aiohttp is therefore a location leak, not just a broken check.

    Matched as a PREFIX, not against a fixed set. A hand-written set drifted
    from validation.py in both directions on the first attempt at this fix
    (guarding an impossible `socks4a`, missing the reachable `socks4h`), and
    `check_proxy` gates on `parse_proxy`, which accepts ANY scheme and never
    consults the validator — so a stored or API-supplied URL can carry a socks
    spelling no allowlist anticipated. Every one of them fails closed here;
    only sending one to aiohttp reproduces the leak.
    """
    return scheme.startswith("socks")


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


def _is_loopback_only_host(server: str) -> bool:
    """True if the proxy endpoint resolves to loopback and NOTHING else.

    This is the narrow exemption the operator lane is allowed to make on top of
    _is_blocked_proxy_host — it never replaces that rule, it only subtracts the
    loopback case from it. The two are composed at the call site
    (`blocked and not (allow_loopback and loopback_only)`), so the SSRF rule
    stays single-sourced in _is_blocked_proxy_host rather than being copied.

    "and nothing else" is load-bearing: a name that resolves to BOTH 127.0.0.1
    and 192.168.1.10 is not a local tunnel, it is the private-range target the
    guard exists to refuse, so any non-loopback answer withdraws the exemption.
    An unresolvable host gets no exemption either — _is_blocked_proxy_host lets
    those through to fail at connect, and this must not widen that.
    """
    host = urllib.parse.urlparse(server).hostname or ""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    seen = False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False  # something we can't reason about -> no exemption
        if not ip.is_loopback:
            return False
        seen = True
    return seen


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
    """SOCKS4a CONNECT — the 0.0.0.1 destination-IP form that carries a domain
    name — for both socks4 and socks4h.

    The SOCKS4a form is sent even for plain `socks4://`, and a STRICT SOCKS4
    server that does not implement the 4a extension will read 0.0.0.1 as a
    literal destination and refuse. That is deliberate: the alternative is to
    resolve the geo endpoint locally, which emits a DNS query from the
    operator's real resolver for a host the proxy is supposed to reach — a
    location leak, and the very class of tell this module exists to close. A
    refusal fails closed (ok=False, geo untouched); a DNS leak does not.
    """
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
    # Neither Content-Length nor chunked — legal under the `Connection: close`
    # this client asks for, so the body runs to EOF. It must be READ to EOF:
    # StreamReader.read(n) returns as soon as ANY data is buffered, so a body
    # split across TLS records came back truncated, the JSON failed to parse,
    # and the check fell into the empty-geo failure arm — the same real-timezone
    # leak this module exists to close, reintroduced intermittently.
    out = bytearray()
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        out += chunk
        if len(out) > _MAX_GEO_BODY:  # enforced every pass, not just at the end
            raise ValueError("geo response too large")
    return bytes(out)


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
        # Await the close: on a TLS transport an un-awaited close defers the
        # shutdown to GC, which surfaces as "Task was destroyed but it is
        # pending" / unraised SSL errors in the app's disk-backed log. Errors
        # here are about tearing down a connection whose answer we already have,
        # so they must not turn a good check into a failure.
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


async def check_proxy(
    proxy_str: str, timeout: int = 10, *, allow_loopback: bool = False
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    """Probe a proxy. Returns
    (ok, message, country_code, country_name, ip, timezone, lat, lon).

    `allow_loopback` is the LANE selector, and it defaults to the strict
    (remote-lane) posture so a future caller that forgets it inherits the safe
    one. It exempts loopback — 127.0.0.0/8, ::1, localhost — and ONLY loopback;
    private, link-local, reserved, multicast and 169.254.169.254 stay refused
    on both lanes.

    Why the operator lane may make that exemption: its input is a proxy the
    operator typed into their own dialog and stored, and an operator can already
    point their own browser at any local port, so reaching 127.0.0.1:9050 grants
    no capability they did not have. The port-scan-oracle risk the guard was
    written for is entirely about the REMOTE caller (the REST route hands us a
    raw off-machine string), and that lane keeps the full guard.

    Refusing loopback on BOTH lanes is what made Tor / `ssh -D` profiles
    permanently unverifiable: the check could never pass, geo stayed empty, and
    _proxy_timezone then declared the operator's REAL host timezone inside a
    proxied profile.
    """
    proxy_config = parse_proxy(proxy_str)
    if not proxy_config:
        return False, "Invalid proxy format", "", "", "", "", None, None

    # Runs for EVERY scheme, ahead of the branch: check_proxy connects to
    # whatever host:port the user pasted, so this is the SSRF gate. The
    # loopback exemption SUBTRACTS from this rule rather than replacing it —
    # _is_blocked_proxy_host stays the single source of what is forbidden.
    if _is_blocked_proxy_host(proxy_config["server"]) and not (
        allow_loopback and _is_loopback_only_host(proxy_config["server"])
    ):
        return (
            False,
            "Proxy host is not allowed (private/loopback address)",
            "", "", "", "", None, None,
        )

    scheme = urllib.parse.urlparse(proxy_config["server"]).scheme.lower()
    is_socks = _is_socks_scheme(scheme)

    if not is_socks and not AIOHTTP_AVAILABLE:
        # NOT ok: a skipped check must never be recorded as a success (which
        # would set last_check_ok=True and, with empty geo, erase the proxy's
        # known-good country/timezone). ok=False leaves the geo fields intact.
        # Scoped to the aiohttp branch — the SOCKS path is stdlib-only, and
        # since socks5 is persona's DEFAULT scheme, skipping it here would
        # leave exactly the empty geo (and the real-host-timezone fallback)
        # this module exists to prevent.
        return False, "Proxy check skipped (aiohttp not installed)", "", "", "", "", None, None

    proxy_url = proxy_config["server"]
    if "username" in proxy_config:
        url_scheme, rest = proxy_url.split("://", 1)
        password = proxy_config.get("password", "")
        proxy_url = f"{url_scheme}://{proxy_config['username']}:{password}@{rest}"

    try:
        if is_socks:
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
            if data is None:
                # A 200 whose body is valid JSON but not an object. Fails closed
                # either way; this reports it accurately instead of tripping an
                # AttributeError into the generic "Proxy check failed" arm.
                return False, "Proxy geo lookup failed", "", "", "", "", None, None
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
    except _PROXY_CONNECT_ERRORS:
        return False, "Failed to connect to proxy", "", "", "", "", None, None
    except _CLIENT_ERRORS:
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
    proxy_str: str, timeout: int, *, allow_loopback: bool = False
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                check_proxy(proxy_str, timeout, allow_loopback=allow_loopback)
            )
        finally:
            loop.close()
    except Exception as e:
        return False, f"Error checking proxy: {e!s}", "", "", "", "", None, None


def check_proxy_sync(proxy_str: str, timeout: int = 10) -> tuple[bool, str]:
    """REMOTE lane. Sole caller is the REST route (src/api/routes/proxy.py),
    which hands us a raw string from an off-machine API caller — the genuine
    port-scan oracle. The full SSRF guard applies: no loopback exemption, and
    the refusal happens before any socket is opened."""
    ok, message = _run(proxy_str, timeout)[:2]
    return ok, message


def check_proxy_detailed_sync(
    proxy_str: str,
    timeout: int = 10,
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    """OPERATOR lane. UI-only (src/ui/app.py, src/ui/dialogs/proxy.py): the
    input is the operator's own stored / just-typed proxy, so loopback — and
    loopback alone — is checkable here. That is what lets a Tor or `ssh -D`
    endpoint establish real geography instead of leaving it empty and letting
    the profile declare the host timezone."""
    return _run(proxy_str, timeout, allow_loopback=True)
