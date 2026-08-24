"""Fetch a URL through a SOCKS5 proxy with the hostname resolved AT THE EXIT.

This is the no-browser leg of the checker matrix: the TLS/JSON checkers that
hand back a verdict without a page. It exists as its own module because the
one property that makes it trustworthy is easy to lose by accident.

``socks5h``, never ``socks5``
-----------------------------
The difference is WHO RESOLVES THE NAME. ``socks5://`` resolves the hostname
locally and sends an IPv4/IPv6 address to the proxy; ``socks5h://`` sends the
NAME and the exit resolves it. Measured with the project's own credential, the
local-resolution form fails outright::

    curl: (97) cannot complete SOCKS5 connection to ipinfo.io. (2)

But "it happens to work" is not why this module forces the ``h`` form. A local
resolution is a DNS query leaving this machine on the operator's own resolver,
naming the checker being read — so a harness that leaked DNS while measuring
whether the product leaks DNS would be worthless, and worse, it would look
like a clean run. PS-46 requires the same property OF THE PRODUCT (hostname on
the wire, ``atyp == 0x03``, resolved at the exit); the instrument that measures
the product must not hold itself to a weaker standard than the product.

So this module never calls :func:`socket.getaddrinfo` on a target host, and
:func:`fetch_json` refuses a proxy URL that is not ``socks5h``. It does not
"prefer" remote resolution — there is no code path here that resolves a target
name locally, which is a stronger statement than a flag.

Why not ``requests``/``aiohttp``
--------------------------------
``requests`` is not a declared dependency (see ``requirements.txt``), and
``aiohttp`` — which is — carries no SOCKS5 support of its own. ``PySocks`` IS a
declared dependency and is already how ``utils/proxy_checker.py`` reaches a
SOCKS proxy, so this uses it with the stdlib's own HTTP client on top. No new
dependency is introduced to read a checker.

Never global
------------
The proxy is applied to ONE socket, per call. This module deliberately offers
no way to install a default proxy: ``socks.set_default_proxy`` and the
``HTTP_PROXY``/``HTTPS_PROXY`` environment variables would route the agent
control plane and the LLM gateway through a metered mobile link.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

# A checker that has not answered in this long has not answered. The JSON tier
# is a single request/response with no page to settle, so this is a hang guard
# rather than a latency budget — the browser tier's settle times (45-60s) do
# not apply here and must not be borrowed.
DEFAULT_TIMEOUT = 30.0

# Read cap for one checker response. The JSON tier answers in kilobytes; a
# checker that answers with a megabyte is not handing back a verdict, and this
# runs inside a recording run whose memory is not worth risking on a surprise.
MAX_BODY_BYTES = 4 * 1024 * 1024


def _reported_failure(exc: BaseException) -> BaseException:
    """The exception that actually DESCRIBES the failure.

    Normally ``exc`` itself. The exception is PySocks, which destroys the
    useful class on its way out and needs unwrapping.

    WHY THIS EXISTS — measured, not inferred
    ----------------------------------------
    PySocks raises a specific class per SOCKS5 stage: ``SOCKS5AuthError`` when
    the relay rejects the credential (``socks.py:487,503,511``) and
    ``SOCKS5Error`` carrying a ``"{:#04x}: {}"`` reply code when the relay
    ACCEPTED the credential and then failed to connect (``socks.py:533``).

    Those classes never reach a caller. ``socksocket.connect`` wraps the
    negotiation in ``except socket.error`` (``socks.py:810-814``) and re-raises
    as ``GeneralProxyError``; because ``ProxyError`` subclasses ``OSError``
    (i.e. ``socket.error``), that arm SHADOWS the ``except ProxyError`` arm at
    ``:817``, which is unreachable for a negotiation failure. Driven through a
    real loopback relay, all eight connect-stage reply codes AND an auth
    rejection arrive identically as ``GeneralProxyError`` — so a caller reading
    the class name cannot tell the two stages apart, or tell either from a
    timeout.

    The original survives as ``ProxyError.socket_err``, which PySocks sets in
    its own ``__init__`` (``socks.py:59-64``) and documents as "Socket_err
    contains original socket.error exception". That attribute is read here
    rather than ``__context__``: ``socket_err`` is a value PySocks assigns
    deliberately, while ``__context__`` is implicit interpreter state that any
    intervening ``except`` block can replace. They happen to be the same object
    today; only one of them is a promise.

    ONLY A ``ProxyError`` INNER IS UNWRAPPED, and that condition is the whole
    safety property rather than a tidiness preference. It is what keeps two
    failures that are NOT a SOCKS stage from acquiring stage-shaped names:

      * a negotiation TIMEOUT wraps a bare ``TimeoutError`` — reported as
        ``GeneralProxyError``, unchanged, so it can never be read as a
        connect-stage reply;
      * an unreachable relay raises ``ProxyConnectionError`` wrapping
        ``ConnectionRefusedError`` — reported unchanged, preserving it as the
        contrast case it already is.

    Both were driven through the same relay harness as the eight reply codes.
    """
    try:
        import socks  # PySocks; a declared dependency (requirements.txt)
    except ImportError:  # pragma: no cover - PySocks is a declared dependency
        return exc

    # Bounded rather than `while True`: this walks attacker-adjacent object
    # state, and a self-referential chain must not hang the fetcher. PySocks
    # nests one deep, so the bound is never reached in practice.
    current = exc
    for _ in range(4):
        inner = getattr(current, "socket_err", None)
        if not isinstance(inner, socks.ProxyError):
            break
        current = inner
    return current


class FetchFailed(RuntimeError):
    """A checker was not read.

    Carries a message naming what failed, because this class is the *result*
    for that checker — an unobtainable reading is recorded with its reason, and
    a reason of "something went wrong" is not one. Never coerced into a value:
    inconclusive is never a pass.
    """


class ProxyRefused(ValueError):
    """The proxy URL cannot be used, so nothing was attempted.

    Distinct from :class:`FetchFailed` on purpose: that one means "we asked and
    did not get an answer", this one means "we refused to ask at all". A run
    that cannot honour remote resolution must stop rather than fall back to a
    weaker form, so this is raised BEFORE a socket exists.
    """


def parse_socks5h(proxy_url: str) -> "tuple[str, int, str | None, str | None]":
    """Split a ``socks5h://[user:pass@]host:port`` URL into its parts.

    Refuses any other scheme — including ``socks5://`` — because the scheme is
    the whole contract of this module. The credential file supplies the plain
    ``socks5://`` form, so the CALLER rewrites the scheme deliberately and this
    function refuses to guess on its behalf.

    The refusal message never contains the URL: it carries the credential.
    """
    parsed = urlparse(proxy_url)
    if parsed.scheme != "socks5h":
        raise ProxyRefused(
            f"proxy scheme must be 'socks5h' (the exit resolves the hostname), "
            f"got {parsed.scheme!r}. 'socks5' resolves locally and leaks a DNS "
            "query naming the checker being read."
        )
    if not parsed.hostname or not parsed.port:
        raise ProxyRefused("proxy URL is missing a host or a port")
    return parsed.hostname, parsed.port, parsed.username, parsed.password


def _socket_via(proxy_url: str, host: str, port: int, timeout: float):
    """One proxied socket to ``host:port``, with ``host`` sent AS A NAME.

    ``rdns=True`` is what puts the name on the wire. It is passed explicitly
    rather than left to PySocks' default so that a future default change cannot
    silently turn this into a local resolution.
    """
    import socks  # PySocks; a declared dependency (requirements.txt)

    p_host, p_port, user, password = parse_socks5h(proxy_url)
    sock = socks.socksocket()
    sock.set_proxy(
        socks.SOCKS5,
        p_host,
        p_port,
        rdns=True,  # <- socks5h: the EXIT resolves `host`
        username=user,
        password=password,
    )
    sock.settimeout(timeout)
    sock.connect((host, port))
    return sock


def fetch(
    url: str, *, proxy_url: str, timeout: float = DEFAULT_TIMEOUT
) -> "tuple[int, str]":
    """GET ``url`` through the proxy. Returns ``(status, body_text)``.

    Raises :class:`FetchFailed` for anything that means "no answer" — a refused
    connection, a timeout, a TLS failure, a truncated read. The status code is
    returned rather than raised on, including for 4xx/5xx: a checker answering
    ``403`` HAS answered, and that answer is a reading about the checker (it is
    refusing automation) which the caller records rather than retries.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        # Every checker in the matrix is https. A plaintext fetch through the
        # exit would expose the request to the carrier, and there is no reason
        # to allow it.
        raise ProxyRefused(f"checker URL must be https, got {parsed.scheme!r}")
    host = parsed.hostname or ""
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    sock = None
    try:
        sock = _socket_via(proxy_url, host, port, timeout)
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        conn.sock = tls
        conn.request(
            "GET",
            path,
            headers={
                "Host": host,
                # A checker reads the UA. The JSON tier's verdict is about TLS
                # and IP, not about this string, but sending python's default
                # would make the reading a reading of "urllib" rather than of
                # anything the operator runs. curl's shape is what the manual
                # reconnaissance in PS-10 used, so a later run can compare.
                "User-Agent": "curl/8.14.1",
                "Accept": "*/*",
                "Connection": "close",
            },
        )
        resp = conn.getresponse()
        raw = resp.read(MAX_BODY_BYTES)
        return resp.status, raw.decode("utf-8", errors="replace")
    except (ProxyRefused, FetchFailed):
        raise
    except Exception as exc:
        # Everything else is "we did not get an answer". Named with its class
        # so a timeout is distinguishable from a refusal in the record.
        #
        # The class and the text are BOTH taken from `_reported_failure`, and
        # they have to come from the same object or the pair contradicts each
        # other. PySocks re-wraps a negotiation failure as `GeneralProxyError`
        # and prefixes its text with "Socket error: " — so reporting the
        # unwrapped class beside the WRAPPER's text would yield
        # `SOCKS5Error: Socket error: 0x01: ...`, where the reply code has been
        # pushed out of the position the reader parses it from. Unwrapping both
        # gives `SOCKS5Error: 0x01: General SOCKS server failure`, which is the
        # shape PySocks formatted at `socks.py:533` before the wrap.
        #
        # `from exc` still chains the OUTER exception, so a traceback keeps the
        # whole path; only the reported summary is narrowed to the frame that
        # actually describes the failure.
        reported = _reported_failure(exc)
        raise FetchFailed(f"{type(reported).__name__}: {reported}") from exc
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def fetch_json(
    url: str, *, proxy_url: str, timeout: float = DEFAULT_TIMEOUT
) -> Any:
    """GET ``url`` and parse it as JSON.

    A non-2xx status and a body that is not JSON are both
    :class:`FetchFailed` — a checker that answered ``503`` or an HTML error
    page did NOT hand back a verdict, and recording its body as though it were
    one is the exact defect this subsystem exists to avoid.
    """
    status, body = fetch(url, proxy_url=proxy_url, timeout=timeout)
    if status < 200 or status >= 300:
        raise FetchFailed(
            f"HTTP {status}: the checker answered, but not with a verdict "
            f"(first 120 chars: {body[:120]!r})"
        )
    try:
        return json.loads(body)
    except ValueError as exc:
        raise FetchFailed(
            f"the checker answered HTTP {status} with a body that is not JSON "
            f"({exc}); first 120 chars: {body[:120]!r}"
        ) from exc


__all__ = [
    "DEFAULT_TIMEOUT",
    "FetchFailed",
    "ProxyRefused",
    "fetch",
    "fetch_json",
    "parse_socks5h",
]
