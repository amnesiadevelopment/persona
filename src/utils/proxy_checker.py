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

class _TooManyRotateRedirects(Exception):
    """The rotate chain outran its hop budget.

    Raised by _follow_rotate_chain, which is the ONLY redirect implementation
    in this module — so this is transport-independent by construction rather
    than by two branches agreeing to raise comparable things.

    Deliberately NOT raised for a 3xx that could not be followed for lack of a
    `Location`. That is a fact about the ENDPOINT (it redirected nowhere) and
    still reports its real status code; this one is a fact about PERSONA (it
    stopped following).
    """


class _RefusedRedirectScheme(Exception):
    """A `Location:` pointed somewhere that is not http(s), so it was refused.

    Its own exception, and its own operator-facing message, because it is a
    fact about PERSONA's refusal rather than about the endpoint's answer — the
    same distinction _TooManyRotateRedirects draws. Reporting the endpoint's
    `HTTP 302` here described the wrong actor: the endpoint answered fine and
    persona declined to follow it to `file:///etc/passwd`.

    This is also the class that used to be aiohttp's to raise, and the reason
    the library no longer follows redirects at all (see _follow_rotate_chain):
    across the declared floor of `aiohttp>=3.9.0` the same condition surfaced
    as THREE different operator-facing messages — `NonHttpUrlRedirectClientError`
    (a ClientError subclass, so it was misreported as `connection failed`) on
    newer versions, a bare `ValueError` on 3.9.0 (which has no such class at
    all, so it fell through to the generic arm), and the SOCKS branch's
    `HTTP 302`. Owning the hop ourselves makes the library's version-dependent
    behaviour UNREACHABLE rather than merely matched, which is the only way to
    settle a 3.9-vs-3.14 split without raising the dependency floor.
    """


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

# The rotate path's two redirect outcomes. NEITHER is conditional on aiohttp
# any more, and that is the point: the rotate fetch passes
# `allow_redirects=False`, so the library never processes a `Location` and
# cannot raise about one. Both conditions are decided in _follow_rotate_chain,
# on one code path, for both transports.
#
# This replaced a tuple that paired `aiohttp.TooManyRedirects` with our own
# exception so the two branches would report the same message. That worked for
# the hop budget and failed for everything else, because matching a library's
# behaviour case-by-case only ever covers the cases you thought of — three
# rounds of review found three more. Not letting the library have the
# behaviour is the version of this that cannot drift.
_REDIRECT_LIMIT_ERRORS: tuple[type[BaseException], ...] = (_TooManyRotateRedirects,)
_REFUSED_SCHEME_ERRORS: tuple[type[BaseException], ...] = (_RefusedRedirectScheme,)

#: The socks schemes the validator accepts, derived from ITS tuple rather than
#: retyped here — see PROXY_SCHEMES. Used for documentation and by the tests;
#: the runtime decision is _is_socks_scheme(), which is deliberately broader.
SOCKS_SCHEMES = frozenset(s for s in PROXY_SCHEMES if s.startswith("socks"))

# Hard cap on the geo response we will buffer. The body is attacker-influenced
# (hostile endpoint or a MITM on the exit), so an unbounded read is a memory DoS.
_MAX_GEO_BODY = 256 * 1024

#: Hard cap for the RELEASE-METADATA response, which is a different shape of
#: document than the geo probe and needs its own headroom: the Firefox releases
#: fetch asks for `?per_page=30` and each release enumerates its assets, so the
#: JSON is orders of magnitude larger than a geo answer (measured 129 KB against
#: the live endpoint at the time of writing — already half of _MAX_GEO_BODY, and
#: it grows with every published release). Reusing the geo cap would have worked
#: until some future release quietly crossed it and turned the unattended update
#: check into an intermittent failure. Still a BOUND, not an exemption: this body
#: comes from a third party over a transport the operator may not control.
#:
#: RE-MEASURED 2026-09-04 (PS-305), because this is the only place the number is
#: recorded and it had grown 4x. The Firefox-shaped `?per_page=30` fetch against
#: `amnesiadevelopment/persona` is now **493 KB** (94 releases over 65 days,
#: ~1.45/day, and every one of them enumerates its assets). The CHROMIUM updater
#: no longer makes that request at all: since PS-305 it discovers by tag ref
#: (`git/matching-refs/tags/personium-`, 5 bytes today, ~411 bytes per engine
#: tag) and then reads ONE release document (~26 KB) — about 26 KB per hourly
#: check against the 493 KB the list shape had reached, which matters on a
#: connection persona is designed to route through Tor. The 493 KB figure stands
#: for the FIREFOX call site, which still uses the list shape against an
#: upstream where every release is a candidate; 4 MB remains comfortable headroom
#: for it, but that is a measured comfort and not a permanent one.
_MAX_RELEASE_BODY = 4 * 1024 * 1024

#: User-Agent for requests that travel to a THIRD-PARTY endpoint on the
#: operator's behalf (the provider rotate endpoint). Deliberately neutral: the
#: rotate path used to send `User-Agent: persona`, which self-identified the
#: tool to the proxy provider on every rotation. `persona-proxy-check/1.0`
#: (below, on the geo probe) is a different case — it only ever reaches the geo
#: endpoint we chose — so it is left alone.
_NEUTRAL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: Redirect hops the rotate fetch will follow, stated ONCE for both transports.
#: A rotate endpoint commonly sits behind a 301/302 (vanity path -> API host,
#: http->https canonicalisation, a provider URL migration), and the direct
#: urlopen this path replaced followed those for free. Returning the 3xx
#: unfollowed would report `rotate endpoint OK (HTTP 302)` to the operator for a
#: rotation that NEVER HAPPENED — a silent false success, strictly worse than
#: the honest failure the rest of this path is built around.
#:
#: Both branches are pinned to this same number on purpose. The aiohttp branch
#: would otherwise inherit its library default (10) while the SOCKS branch
#: hard-coded its own, leaving one function with two behaviours selected by
#: transport — and socks5 is persona's DEFAULT scheme, so the divergent one
#: would be what most operators actually get. That is the drift this module
#: exists to prevent (see _is_socks_scheme, _http_get_head).
_MAX_ROTATE_REDIRECTS = 5

#: The 3xx codes that carry a `Location` worth following. 300 (Multiple Choices)
#: and 304 (Not Modified) are deliberately absent: neither designates a single
#: replacement request.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _is_socks_scheme(scheme: str) -> bool:
    """True for every scheme that must take a real SOCKS handshake.

    aiohttp's `proxy=` parameter implements HTTP(S) proxies ONLY and does not
    validate the scheme: handed a socks5:// URL it sends an HTTP `CONNECT`,
    which a SOCKS server (waiting for a \\x05 greeting) never answers. socks5 is
    persona's default scheme, so the geo check failed permanently for the app's
    primary proxy type — and a proxy left with no country/timezone used to make
    the launcher fall back to the operator's REAL host timezone inside a proxied
    profile (`process._profile_timezone`, which now REFUSES that launch instead;
    the leak this argument names is closed, the broken check is still a defect).
    Routing a socks URL to aiohttp is therefore a location leak, not just a
    broken check.

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


# The SECOND geo provider, asked only when the first does not answer.
#
# A single oracle made a THIRD PARTY's availability the operator's proxy's
# availability. `ipwho.is` answering 429 — and the rate limit attaches to the
# EXIT's address, which on a mobile/shared exit other tenants also burn, so it
# is not the operator's to clear — returned ok=False; app.py then calls
# ProxyStore.mark_check_failed, freshness.proxy_indicator_state reads "failed"
# at any age, and launch_policy raises GeographyDisprovenError. A healthy proxy
# becomes UNLAUNCHABLE, the message blames the proxy, and it does not self-heal.
#
# This project already fixed exactly this, twice, in the adjacent verify lane:
# services/verify/exit_guard.py::EXIT_OBSERVATION_URLS and
# services/verify/checkers.py::ENGINE_EXIT_URLS. The rule is carried over from
# exit_guard.py:108-134 UNCHANGED, and it is REACHABILITY ONLY — the providers
# do NOT vote:
#
#   * a provider that ANSWERS WITH A COUNTRY is authoritative and the loop
#     stops there, INCLUDING when the country is wrong. Asking a second
#     provider after an answer is shopping for a friendlier oracle, which is
#     how a fallback becomes a laundering route.
#   * only "no verdict" advances, and there are TWO shapes of it: nothing came
#     back (the 429/5xx), and a 200 carrying no country. The second is the easy
#     one to miss — the reader coerces a missing country to "", which would
#     then refuse a HEALTHY exit while reporting "(unknown)".
#
# HTTPS, like the first provider: over cleartext a MITM on the exit could
# inject a bogus country/timezone into the persisted fingerprint.
#
# NOT an address-only endpoint (api.ipify.org): exit_guard.py:126-134 records
# that such a provider can never answer the geography question, so it would
# occupy a slot in the order while being incapable of proving anything.
_GEO_FALLBACK_PROVIDER = "https://ipinfo.io/json"


def _coords_from_loc(loc: object) -> tuple[object, object]:
    """ipinfo's `loc: "52.23,21.01"` -> the pair ipwho.is gives as two floats.

    Returned UNPARSED (as the split strings) on purpose: _validate_geo is the
    one place that floats and range-checks these, and routing a second dialect
    around it would be a second, unreviewed sanitiser.

    Not cosmetic — services/browser/process.py:587-593 pins
    navigator.geolocation when both are present and builds the extension in
    DENY mode when they are not. Deny mode is safe (it does NOT fall through to
    the real host coords), so a `loc` this cannot read degrades gracefully.
    """
    if not isinstance(loc, str) or "," not in loc:
        return None, None
    lat, _, lon = loc.partition(",")
    return lat.strip(), lon.strip()


def _geo_fields_from_payload(
    data: dict,
) -> tuple[str, str, str, str, object, object]:
    """One shape out of the providers' several. UNVALIDATED by design — every
    field here still goes through _validate_geo at the call site.

    Returns ``(ip, country_code, country_name, timezone, lat, lon)``.

    The providers do not agree on how to give a two-letter code, and reading
    one with the other's key layout is a WORSE failure than the 429 this exists
    to survive (exit_guard.py:618-623 records it: ipwho.is read with ipinfo's
    layout yields ``country == "POLAND"``, refusing a good exit *while naming
    the wrong country*). Keyed on the SHAPE rather than on the URL, exactly as
    exit_guard.py::_normalise_observation is, so a provider is not silently
    misread if the order is ever changed:

        field         ipwho.is                    ipinfo.io
        code          country_code: "PL"          country: "PL"
        country NAME  country: "Poland"           (none at all)
        timezone      timezone: {"id": ...}       timezone: "Europe/Warsaw"
        coords        latitude / longitude        loc: "52.23,21.01"

    A CODE-SHAPED `country` is what marks the ipinfo dialect, not merely the
    ABSENCE of `country_code`. Where `country_code` is present, `country` is
    the human NAME; where it is absent AND `country` is two characters,
    `country` IS the code and the provider has no name field — so an ipinfo
    answer legitimately yields an empty `country_name` beside a populated code.
    That is accepted rather than mapped (store.py:257 stores it;
    ui/components/network_page.py:111-113 already guards on it being empty),
    because a code->name table is a second source of truth for something no
    shipped behaviour depends on. A body carrying a NAME and no code — the
    degraded shape a rate-limited provider emits — is deliberately read as
    NO COUNTRY rather than as a code, so the caller advances to the next
    provider instead of stopping on a "POLAND" that validation then discards.
    """
    code = str(data.get("country_code") or "").strip()
    name = str(data.get("country") or "").strip()
    if not code:
        # No `country_code` key: the ipinfo dialect, where `country` IS the
        # code and there is no name field at all. The swap is gated on the
        # VALUE being code-SHAPED rather than on the key merely being absent,
        # because those two are not the same question. A degraded ipwho body —
        # `{"country": "Poland"}` with the code missing, which is exactly the
        # shape a rate-limited provider tends to emit — would otherwise take
        # the swap and yield code == "POLAND": truthy, so the loop STOPS on it
        # and never asks the second provider, and _validate_geo then drops it
        # to "". That is the `exit_guard.py:618-623` trap reached from the
        # other direction, and `len == 2` is what tells the two apart. A name
        # with no code therefore leaves the code EMPTY, which is the
        # no-country shape the loop already knows how to advance on.
        if len(name) == 2:
            code, name = name, ""
    code = code.upper()

    tzobj = data.get("timezone")
    tz = (tzobj.get("id", "") if isinstance(tzobj, dict) else tzobj) or ""

    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is None and lon is None:
        lat, lon = _coords_from_loc(data.get("loc"))

    return str(data.get("ip", "unknown")), code, name, str(tz), lat, lon


async def _resolve_geo(
    providers: tuple[str, ...], fetch
) -> tuple[tuple[str, str, str, str, object, object] | None, str]:
    """Ask the providers IN ORDER; the first to ANSWER WITH A COUNTRY wins.

    Returns ``(fields, "")`` on an answer, or ``(None, message)`` with the
    operator-facing failure string. That string is a FIXED one either way — no
    arm here may echo the proxy host:port, which reaches the disk-backed daily
    log and the Activity Log (pinned by tests/test_proxy_log_privacy.py and by
    the host/port assertions in the socks tests).

    `fetch` is the transport seam, and it is why BOTH branches get this fix
    from one implementation rather than from two loops that can drift: the
    SOCKS lane passes _geo_via_socks, the aiohttp lane passes a session GET.

    THE COUNTRY IS TESTED ON THE RAW PAYLOAD, BEFORE _validate_geo, and that
    ordering is load-bearing. A provider that answers with a BOGUS code
    ("XYZ") has still ANSWERED: it must end the check with ok=True and an
    empty, sanitised code — the shipped behaviour
    tests/test_proxy_checker_socks.py::test_socks5_geo_is_dropped_when_the_endpoint_lies
    pins. Testing after validation would turn every lie into a reason to go and
    ask the next provider, i.e. the laundering route the rule above forbids.

    A TRANSPORT EXCEPTION DOES NOT ADVANCE — it propagates to check_proxy's
    except arms exactly as before. Every provider is reached through the SAME
    tunnel, so a connect/handshake failure here is a fact about the PROXY, not
    about the provider: retrying a second provider through a dead proxy cannot
    succeed and would only spend a second full timeout before saying so. The
    two shapes this project's rule actually names — the 429 and the
    no-country 200 — are both answers FROM the provider, and both are handled
    below.

    THE ONE PLACE THIS PORT DELIBERATELY DIVERGES FROM ITS PRECEDENT is what
    happens when the loop RUNS OUT. exit_guard's loop simply refuses, because
    it is a MEASUREMENT gate: refusing costs a measurement run and nothing
    else. This is the OPERATOR lane, where the same refusal walks the ticket's
    own chain — app.py -> mark_check_failed -> freshness reads "failed" at any
    age -> launch_policy raises GeographyDisprovenError — and makes a HEALTHY
    proxy unlaunchable, which is the exact defect this function exists to stop.
    So the best PARTIAL answer seen along the way is remembered and used only
    once nobody has answered with a country. A body carrying a usable TIMEZONE
    is enough for the TIMEZONE half of the launch path, which reads
    `proxy.timezone` FIRST and derives from `country_code` only when there is no
    zone (`launch_policy._proxy_timezone`, branch 1) — so throwing that zone
    away and then condemning the proxy for having no geography discards the very
    field the consumer wanted. It is also what the single-provider version did
    before this change: a partial body used to come back ok=True with its zone.

    ⚠️ A PARTIAL NO LONGER MAKES THE PROFILE LAUNCHABLE, and that is a real
    narrowing of what this paragraph promises (PS-240). The LOCALE half has no
    zone to fall back on: handed a record with a timezone but no country it used
    to answer `en-US`, which shipped an American-English browser beside the
    exit's own non-US clock — the contradiction the geo derivation exists to
    avoid. `process._profile_locale` now refuses that launch with
    `ExitCountryUnknownError`. Remembering the partial is still WORTH DOING and
    this behaviour is deliberately unchanged: the record keeps `last_check_ok`
    true rather than condemning a healthy exit, the refusal names a re-check as
    the remedy (unlike its missing-row siblings, a check that answers with a
    country resolves it), and the alternative — reporting the proxy as failed —
    is strictly worse for the operator. What the partial buys is a proxy that is
    still trusted and one check away from launching, not one that launches now.

    A PARTIAL IS STILL NOT AN ANSWER, and that is what keeps the rule above
    intact: remembering one never ends the loop early, so every remaining
    provider is still asked, and it can never pre-empt a real country. It only
    changes the value of the exhausted case, never the ORDER or the STOPPING.
    """
    status = 0
    partial: tuple[str, str, str, str, object, object] | None = None
    for url in providers:
        status, data = await fetch(url)
        if status != 200:
            # Nothing came back: the 429/5xx this exists to survive. Advance.
            continue
        if data is None:
            # A 200 whose body is valid JSON but not an object. Reported
            # accurately rather than tripping an AttributeError into the
            # generic "Proxy check failed" arm — and NOT advanced on: a
            # malformed answer from the provider we asked first is the
            # documented specific failure, not a reachability problem.
            return None, "Proxy geo lookup failed"
        if not data.get("success", True):
            # The provider itself says it did not answer (ipwho.is reports its
            # own rate limiting this way, with a 200). A non-answer -> advance.
            continue
        fields = _geo_fields_from_payload(data)
        if not fields[1]:
            # Answered, carried no country: a rate-limit body, an error page,
            # or a dialect this reader cannot read. Advancing here is what
            # stops a HEALTHY exit being refused and reported as "(unknown)".
            #
            # REMEMBERED, not discarded. If NOBODY ends up answering with a
            # country, a body that did carry a usable TIMEZONE still beats
            # condemning the proxy — see the divergence note in the docstring.
            # FIRST such body wins, matching the ordering rule the answers
            # themselves follow, and a zoneless partial is not remembered at
            # all because it would be indistinguishable from the refusal.
            if partial is None and fields[3]:
                partial = fields
            continue
        return fields, ""

    # Nobody answered WITH A COUNTRY. Before refusing outright — which on this
    # lane means an unlaunchable proxy — fall back to the best partial.
    if partial is not None:
        return partial, ""

    # Nothing usable at all. The message reports the LAST provider's outcome,
    # which is the final word on the check: a 200 that carried nothing usable
    # is a geo-lookup failure, anything else is the status it returned.
    if status == 200:
        return None, "Proxy geo lookup failed"
    return None, f"Proxy returned status {status}"


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


async def _read_http_body(
    reader: asyncio.StreamReader, headers: dict, max_body: int = _MAX_GEO_BODY
) -> bytes:
    """Read the response body, refusing one larger than `max_body`.

    The cap is a PARAMETER rather than a constant because this reader now
    serves two response shapes with genuinely different sizes. It defaults to
    _MAX_GEO_BODY so the geo probe — the caller the cap was sized for — is
    unchanged; the release-metadata caller passes its own (see
    _MAX_RELEASE_BODY). The bound itself is never optional: the body is still
    attacker-influenced on both paths, so an unbounded read stays impossible.
    """
    if headers.get("transfer-encoding", "").lower().startswith("chunked"):
        out = bytearray()
        while True:
            size = int((await reader.readuntil(b"\r\n")).split(b";")[0].strip(), 16)
            if size == 0:
                break
            if len(out) + size > max_body:
                raise ValueError("response too large")
            out += await reader.readexactly(size)
            await reader.readexactly(2)  # trailing CRLF
        return bytes(out)
    length = headers.get("content-length", "")
    if length.isdigit():
        n = int(length)
        if n > max_body:
            raise ValueError("response too large")
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
        if len(out) > max_body:  # enforced every pass, not just at the end
            raise ValueError("response too large")
    return bytes(out)


async def _http_get_head(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    path: str,
    user_agent: str,
    accept: str,
) -> tuple[int, dict[str, str]]:
    """Write a minimal HTTP/1.1 GET over an already-established (TLS) stream and
    parse the status line + response headers. The body is left unread on the
    reader for the caller to consume (or discard).

    Shared by both in-tunnel callers — the JSON geo probe and the status-only
    rotate fetch — on purpose: this is the module whose entire existence is owed
    to a hand-maintained scheme set drifting from its source of truth (see
    _is_socks_scheme). Two hand-rolled copies of the request write and the head
    parse would drift the same way.
    """
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: {user_agent}\r\n"
            f"Accept: {accept}\r\n"
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
    return status, headers


async def _http_get_json(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    path: str,
    user_agent: str = "persona-proxy-check/1.0",
    accept: str = "application/json",
    max_body: int = _MAX_GEO_BODY,
) -> tuple[int, dict | list | None]:
    """Minimal HTTP/1.1 GET over an already-established (TLS) stream.

    `user_agent` is a PARAMETER with the geo probe's value as its default, not
    a hardcoded constant, because the two callers reach different actors and
    must not identify persona the same way. The geo endpoint is one WE chose
    and the tunnel carries the request to it, so `persona-proxy-check/1.0` is
    deliberately left alone there. The release-metadata caller reaches a THIRD
    PARTY (api.github.com), where that string would self-identify the tool on
    every unattended poll — it passes _NEUTRAL_USER_AGENT instead. Defaulting
    rather than requiring keeps every existing caller byte-identical.

    A JSON array is a valid top-level document and the releases endpoint
    returns one, so a list is returned as-is; only a non-JSON or scalar body
    becomes None.
    """
    status, headers = await _http_get_head(
        reader, writer, host, path, user_agent, accept
    )
    if status != 200:
        return status, None
    data = json.loads(await _read_http_body(reader, headers, max_body))
    return status, (data if isinstance(data, (dict, list)) else None)


async def _http_get_status(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    path: str,
) -> tuple[int, dict[str, str]]:
    """Status-only GET: the response BODY IS NEVER PARSED.

    A provider rotate endpoint commonly answers `200 OK` with plain text (or an
    empty body). Reusing _http_get_json here would turn a perfectly successful
    rotation into a JSONDecodeError, so the status line is all this reads.

    The response HEADERS come back with it, because a 3xx has to be followed and
    `Location` is a HEADER. "Never parse the body" was never a reason to ignore
    the head — the two are different reads, and conflating them is what made an
    unfollowed 302 report as a successful rotation.
    """
    return await _http_get_head(reader, writer, host, path, _NEUTRAL_USER_AGENT, "*/*")


async def _open_socks_stream(
    proxy_config: dict, scheme: str, url: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str, str]:
    """Open a stream to `url`'s host THROUGH the SOCKS proxy, TLS included.

    Returns (reader, writer, target_host, path) with the tunnel established and
    — for an https target — TLS already negotiated INSIDE it. The caller owns
    the writer and must close it.

    Uses plain asyncio + the stdlib only — the same approach the in-tree bridge
    (src/services/proxy/bridge.py) already takes — so this adds no dependency
    and cannot raise at import time in an environment where PySocks or
    aiohttp_socks happen not to be installed.

    Extracted so the SOCKS4-vs-5 dispatch, the in-tunnel TLS and the
    connect-failure teardown exist ONCE rather than once per caller. Both
    in-tunnel callers (the JSON geo probe and the status-only rotate fetch) are
    the same tunnel differing only in what they read off it; two copies meant a
    future fix to the handshake had two sites to find, which is the same drift
    argument _is_socks_scheme and _http_get_head are already built on.

    The target host is always handed to the proxy as a DOMAIN NAME (atyp 0x03 /
    the SOCKS4a form) and never resolved here, so no DNS query for it ever
    leaves the operator's real resolver.
    """
    target = urllib.parse.urlparse(url)
    target_host = target.hostname or ""
    target_port = target.port or (443 if target.scheme == "https" else 80)
    path = target.path or "/"
    if target.query:
        # A rotate endpoint's API key/session id lives in the query string —
        # dropping it would turn every rotate into an unauthenticated request.
        path = f"{path}?{target.query}"
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
        # TLS is negotiated INSIDE the SOCKS tunnel, against the target's own
        # certificate: the proxy carries opaque bytes and can no more read or
        # forge the response than it could before. (It also means the
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
    return reader, writer, target_host, path


async def _close_stream(writer: asyncio.StreamWriter) -> None:
    """Tear a tunnel down without letting the teardown fail the result.

    Await the close: on a TLS transport an un-awaited close defers the shutdown
    to GC, which surfaces as "Task was destroyed but it is pending" / unraised
    SSL errors in the app's disk-backed log. Errors here are about tearing down
    a connection whose answer we already have, so they must not turn a good
    check into a failure.
    """
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, ssl.SSLError):
        pass


async def _geo_via_socks(
    proxy_config: dict, scheme: str, url: str
) -> tuple[int, dict | None]:
    """Fetch the geo endpoint through a real SOCKS handshake.

    NARROWED BACK TO `dict | None` AT THIS CALL SITE, deliberately. The shared
    _http_get_json widened to `dict | list | None` for the release-metadata
    caller, which legitimately receives a top-level JSON array. The geo probe
    does not: check_proxy's 200-handler treats a non-object body as the
    specific "Proxy geo lookup failed", and that arm is reached only via None.
    Letting a list through here would sail past the None check and trip
    `.get()` into the blanket handler's generic "Proxy check failed" instead —
    so the narrowing is what keeps that documented behaviour, and this
    signature, true. Narrow here rather than in the shared helper: the other
    caller needs the list.
    """
    reader, writer, target_host, path = await _open_socks_stream(
        proxy_config, scheme, url
    )
    try:
        status, data = await _http_get_json(reader, writer, target_host, path)
        return status, (data if isinstance(data, dict) else None)
    finally:
        await _close_stream(writer)


async def _json_via_socks(
    proxy_config: dict,
    scheme: str,
    url: str,
    user_agent: str,
    max_body: int,
    accept: str = "application/json",
) -> tuple[int, dict | list | None]:
    """ONE JSON GET through a real SOCKS handshake.

    The same tunnel _geo_via_socks opens, differing only in what it sends as
    its User-Agent, what representation it asks for, and how much body it will
    buffer — which is precisely why _open_socks_stream and _http_get_json are
    shared rather than copied. In particular the target host reaches the proxy
    as a DOMAIN NAME (atyp 0x03) and is never resolved here, so routing
    persona's own release-metadata fetch through a proxy does not emit a DNS
    query for api.github.com from the operator's real resolver.

    `accept` is a PARAMETER for the same reason `user_agent` is, and the two
    must be threaded together: the caller above this one decides what to ask
    for, and a header honoured on the direct branch but silently replaced here
    would mean turning the egress policy ON changes the request on the wire.
    Its default is the geo probe's value, so every pre-existing caller stays
    byte-identical.
    """
    reader, writer, target_host, path = await _open_socks_stream(
        proxy_config, scheme, url
    )
    try:
        return await _http_get_json(
            reader, writer, target_host, path, user_agent, accept, max_body
        )
    finally:
        await _close_stream(writer)


async def _one_hop_via_socks(
    proxy_config: dict, scheme: str, url: str
) -> tuple[int, dict[str, str]]:
    """ONE status-only GET through a real SOCKS handshake. Never follows.

    Stops at the status line — the body is never parsed — so a plain-text rotate
    response is a success rather than a JSONDecodeError. The response headers
    come back with it because `Location` is a header and the caller's redirect
    loop needs it.

    A FRESH tunnel per hop is what keeps the exit-side resolution property true
    for every request in a chain: the target host goes to the proxy as a domain
    name and is never resolved on the operator's real resolver, on hop 1 and on
    hop 5 alike.
    """
    reader, writer, target_host, path = await _open_socks_stream(
        proxy_config, scheme, url
    )
    try:
        return await _http_get_status(reader, writer, target_host, path)
    finally:
        await _close_stream(writer)


async def _one_hop_via_aiohttp(session, proxy_url: str, url: str) -> tuple[int, dict[str, str]]:
    """ONE status-only GET through an HTTP proxy. Never follows.

    `allow_redirects=False` is the load-bearing argument in this function, and
    it is what ended a whole class of review findings — see _follow_rotate_chain
    and _RefusedRedirectScheme. aiohttp is reduced to exactly what the SOCKS
    branch does: perform one request, hand back the status and the headers, make
    no policy decision about a `Location`.

    The body is never read, let alone parsed.
    """
    async with session.get(
        url,
        proxy=proxy_url,
        allow_redirects=False,
        headers={"User-Agent": _NEUTRAL_USER_AGENT, "Accept": "*/*"},
    ) as response:
        return response.status, {k.lower(): v for k, v in response.headers.items()}


async def _follow_rotate_chain(do_hop, url: str) -> int:
    """Follow the rotate endpoint's redirect chain. ONE implementation, both
    transports.

    `do_hop` performs a single request and returns (status, headers); it is the
    ONLY part that differs between SOCKS and aiohttp. Every decision about a
    redirect — whether to follow, where to, how many times, and what each
    failure is called — is made HERE, once.

    That structure is the fix for a defect class, not a style preference. This
    policy used to be implemented twice: by this loop for SOCKS, and by aiohttp
    for HTTP proxies. Three consecutive review rounds each found a fresh way
    the two disagreed — follow vs. don't-follow, a hop-budget off-by-one
    (`max_redirects=N` fails ON the N-th while `range(N + 1)` permits N
    follows: the same bound in different units), two different messages for an
    exhausted budget, and finally a refused non-http(s) `Location` reported
    three ways across the declared `aiohttp>=3.9.0` range. Each was fixed by
    making the library's behaviour match ours in one more case; the class kept
    producing new instances because matching case-by-case only ever covers the
    cases you thought of. With `allow_redirects=False` the library's redirect
    behaviour is not matched, it is UNREACHABLE — including the parts of it
    that vary by version, which no amount of matching could have fixed without
    raising the dependency floor.

    A 3xx IS followed. Returning it unfollowed would hand the caller a status
    the old `< 400` verdict read as success, reporting `rotate endpoint OK
    (HTTP 302)` for a rotation that never happened: the redirect target — the
    request that would ACTUALLY have rotated the proxy — was never fetched. The
    direct urlopen this path replaced followed redirects for free.

    Three outcomes, deliberately distinct because they are facts about
    different actors:

    * a status that is not a followable 3xx -> returned as-is (the endpoint's
      answer, including a 3xx that redirected NOWHERE for lack of a Location);
    * _RefusedRedirectScheme -> persona refused a non-http(s) target;
    * _TooManyRotateRedirects -> persona stopped following.

    The http/https guard is RE-APPLIED per hop: the caller's guard only ever
    sees the ORIGINAL url, so a `Location: file:///etc/passwd` or a scheme
    downgrade must not ride through on a later hop.
    """
    for _hop in range(_MAX_ROTATE_REDIRECTS + 1):
        status, headers = await do_hop(url)

        if status not in _REDIRECT_STATUSES:
            return status

        # A 3xx with no Location redirected nowhere. That is the ENDPOINT's
        # answer, so its real status is returned unchanged and the caller's
        # `status < 300` verdict is what makes it a failure.
        location = headers.get("location", "")
        if not location:
            return status
        # urljoin so a relative Location ("/final") resolves against the URL it
        # came from, which is the common provider shape.
        nxt = urllib.parse.urljoin(url, location)
        if urllib.parse.urlparse(nxt).scheme.lower() not in ("http", "https"):
            raise _RefusedRedirectScheme(
                "rotate chain redirected to a non-http(s) target"
            )
        url = nxt

    # The budget is spent and the chain was STILL going — a redirect loop or an
    # over-long chain, which an unbounded follow would hang on.
    raise _TooManyRotateRedirects(
        f"rotate chain exceeded {_MAX_ROTATE_REDIRECTS} redirects"
    )


async def fetch_status_via_proxy(
    proxy_str: str, url: str, timeout: int
) -> tuple[bool, str]:
    """GET `url` THROUGH `proxy_str`, reporting only whether it succeeded.

    Written for the provider rotate endpoint, which used to be fetched with a
    bare urlopen on the operator's REAL IP — disclosing, in one timestamped
    request, that this real address controls that proxy account, plus a DNS
    query from the operator's real resolver for the provider's hostname.

    Two properties matter as much as the routing itself:

    * The rotate target is resolved AT THE EXIT, never locally. The SOCKS path
      sends the host as a domain name (atyp 0x03 / the SOCKS4a form) and the
      aiohttp path hands the absolute URL to the proxy. That is the same rule
      _socks4_connect already fails closed for, now applied here.
    * It also REMOVES rather than adds SSRF exposure: the request no longer
      originates from the operator's machine, so a crafted rotate_url can no
      longer reach the local network or a cloud metadata endpoint from here.
      (The caller's http/https-only scheme guard still stands — it blocks
      file:// / data:// — but the local-network reach it was written against
      is gone.)

    _is_blocked_proxy_host is deliberately NOT applied to the transport proxy.
    That gate guards check_proxy against a PASTED string being used as a
    port-scan oracle; here the proxy is already stored and operator-configured,
    and loopback SOCKS endpoints (Tor, `ssh -D`) must keep working.

    Redirects are FOLLOWED, to the _MAX_ROTATE_REDIRECTS bound. Both transports
    get that behaviour from the SAME loop (_follow_rotate_chain): each performs
    a single hop and makes no policy decision about a `Location`, so the bound,
    the per-hop scheme guard and every failure message are shared by
    construction rather than by two implementations being kept in agreement.
    aiohttp is called with `allow_redirects=False` precisely so its own
    redirect handling — which varies across the declared `aiohttp>=3.9.0`
    range — is never reached. A rotate endpoint commonly sits behind a 301/302,
    and the direct urlopen this replaced followed them for free.

    Returns (status < 300, "HTTP <status>") — or (False, reason) if the request
    could not be sent through the proxy at all. It NEVER falls back to a direct
    send: no transport means no request.

    The verdict is `< 300`, NOT `< 400`, and the difference is the whole point:
    a 3xx that reaches this line is one that could NOT be followed (no Location,
    a non-http(s) Location, or a chain past the hop bound). Its redirect target
    — the request that would actually have rotated the proxy — was never
    fetched, so counting it as success would write `rotate endpoint OK (HTTP
    302)` to the operator's activity log for a rotation that never happened.
    A followed redirect never reaches here as a 3xx; it arrives as the final
    status of the chain.
    """
    proxy_config = parse_proxy(proxy_str)
    if not proxy_config:
        return False, "rotate request not sent: no proxy transport"

    scheme = urllib.parse.urlparse(proxy_config["server"]).scheme.lower()
    is_socks = _is_socks_scheme(scheme)

    if not is_socks and not AIOHTTP_AVAILABLE:
        # Fail closed, exactly as check_proxy does for the same branch: the only
        # alternative to "not sent" here would be a direct send on the real IP,
        # which is the leak this function exists to close.
        return False, "rotate request not sent: aiohttp not installed"

    proxy_url = proxy_config["server"]
    if "username" in proxy_config:
        url_scheme, rest = proxy_url.split("://", 1)
        password = proxy_config.get("password", "")
        proxy_url = f"{url_scheme}://{proxy_config['username']}:{password}@{rest}"

    try:
        if is_socks:

            async def do_hop(hop_url: str) -> tuple[int, dict[str, str]]:
                return await _one_hop_via_socks(proxy_config, scheme, hop_url)

            # ONE wait_for around the WHOLE chain, not per hop: `timeout` is the
            # operator's budget for rotating the proxy, and a per-hop timeout
            # would silently let a redirect chain take _MAX_ROTATE_REDIRECTS
            # times longer than they asked for.
            status = await asyncio.wait_for(_follow_rotate_chain(do_hop, url), timeout)
        else:
            # ClientTimeout(total=) applied to a session whose redirects we now
            # drive ourselves is PER REQUEST, so it is the same per-hop problem
            # in aiohttp's units. The wait_for below is what actually bounds the
            # chain, and it bounds both transports identically.
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:

                async def do_hop(hop_url: str) -> tuple[int, dict[str, str]]:
                    return await _one_hop_via_aiohttp(session, proxy_url, hop_url)

                status = await asyncio.wait_for(
                    _follow_rotate_chain(do_hop, url), timeout
                )
        return status < 300, f"HTTP {status}"
    except asyncio.TimeoutError:
        return False, "rotate request timed out"
    except _REDIRECT_LIMIT_ERRORS:
        # The proxy and the endpoint both worked; the redirect chain was a loop
        # or too long. Raised by _follow_rotate_chain for BOTH transports, so
        # this message cannot depend on which one ran.
        return False, "rotate request failed: too many redirects"
    except _REFUSED_SCHEME_ERRORS:
        # PERSONA refused the target — the endpoint answered fine and the proxy
        # carried it fine, so neither "HTTP 302" nor "connection failed"
        # describes what happened. `Location: file:///etc/passwd` is the hostile
        # case the per-hop guard exists for, and it is the one where the
        # operator most deserves an accurate message rather than being sent to
        # debug their proxy.
        return False, "rotate request failed: redirect to a non-http(s) target refused"
    except _PROXY_CONNECT_ERRORS:
        return False, "rotate request failed: could not connect to proxy"
    except _CLIENT_ERRORS:
        # Never echo the exception: its text embeds the proxy host:port, and
        # this string reaches the disk-backed daily log and the Activity Log,
        # which the app otherwise keeps free of the endpoint.
        return False, "rotate request failed: connection failed"
    except OSError:
        return False, "rotate request failed: could not connect to proxy"
    except Exception:
        return False, "rotate request failed"


def fetch_status_via_proxy_sync(
    proxy_str: str, url: str, timeout: int
) -> tuple[bool, str]:
    """Blocking wrapper — the rotate path runs on a background thread.

    `timeout` is explicit on both this and the async form, matching the reason
    `_fetch_rotate_url` refuses a default for `proxy_url`: on a path whose whole
    premise is that a caller must not silently get behaviour it did not ask for,
    a defaulted argument is the mechanism by which that happens.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                fetch_status_via_proxy(proxy_str, url, timeout)
            )
        finally:
            loop.close()
    except Exception:
        return False, "rotate request failed"


async def fetch_json_via_proxy(
    proxy_str: str,
    url: str,
    timeout: int,
    user_agent: str = _NEUTRAL_USER_AGENT,
    max_body: int = _MAX_RELEASE_BODY,
    accept: str = "application/json",
) -> dict | list:
    """GET `url` THROUGH `proxy_str` and return the parsed JSON document.

    The transport half of persona's OWN egress policy (see services/egress.py,
    which is the only thing that decides whether this is called at all). Its
    contract is deliberately the same one fetch_status_via_proxy states: the
    request goes through the configured transport or it is NOT SENT. There is
    no direct-send fallback anywhere in this function, because a fallback is
    exactly the silent leak the policy exists to prevent — an operator who
    configured a proxy and got a real-IP request instead would be worse off
    than one who configured nothing, since they would believe they were covered.

    Two properties are inherited from the shared tunnel rather than re-argued:

    * SOCKS schemes take a REAL SOCKS handshake. `urllib`'s env-var proxy
      support sends a plain `CONNECT host:443 HTTP/1.1` at a SOCKS port, which
      a SOCKS server waiting for a \\x05 greeting never answers — the same
      defect class _is_socks_scheme documents for aiohttp. Routing through here
      is what makes socks5 (persona's default scheme) actually work.
    * The target is resolved AT THE EXIT, as a domain name (atyp 0x03), so no
      DNS query for the metadata host leaves the operator's real resolver.

    Raises on every failure — a caller must never mistake "could not fetch"
    for an empty release list, which would read as "no update available" and
    silently freeze the update path. `user_agent` defaults to the NEUTRAL one
    because this reaches a THIRD PARTY (api.github.com): the geo probe's
    `persona-proxy-check/1.0` would self-identify the tool on every poll.

    `accept` is threaded for the same reason and MUST stay beside it: the
    policy's whole claim is that one authority decides how persona's requests
    leave, so the request this branch puts on the wire has to be the one the
    caller asked for — a header honoured on egress.py's direct branch and
    replaced with a hardcoded value here would mean switching the policy on
    silently changes what is sent, which is the disagreement-with-itself the
    single authority exists to prevent. It reaches BOTH sub-branches below
    (SOCKS and aiohttp), because a header threaded through only one of them
    would just relocate the same drift. The default is the geo probe's value,
    so callers that do not pass one are byte-identical.
    """
    proxy_config = parse_proxy(proxy_str)
    if not proxy_config:
        raise ValueError("request not sent: no usable proxy transport")

    scheme = urllib.parse.urlparse(proxy_config["server"]).scheme.lower()

    if _is_socks_scheme(scheme):
        status, data = await asyncio.wait_for(
            _json_via_socks(proxy_config, scheme, url, user_agent, max_body, accept),
            timeout,
        )
    else:
        if not AIOHTTP_AVAILABLE:
            # Fail closed, exactly as the sibling fetches do for this branch:
            # the only alternative to "not sent" is a direct send on the real
            # IP, which is the disclosure the policy was configured to stop.
            raise RuntimeError("request not sent: aiohttp not installed")
        proxy_url = proxy_config["server"]
        if "username" in proxy_config:
            url_scheme, rest = proxy_url.split("://", 1)
            password = proxy_config.get("password", "")
            proxy_url = f"{url_scheme}://{proxy_config['username']}:{password}@{rest}"
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(
                url,
                proxy=proxy_url,
                headers={"User-Agent": user_agent, "Accept": accept},
            ) as response:
                status = response.status
                if status != 200:
                    data = None
                else:
                    # Accumulate to EOF rather than `content.read(max_body+1)`:
                    # StreamReader.read(n) returns as soon as ANY data is
                    # buffered, so a body split across TLS records came back
                    # short and json.loads raised. That is the SAME hazard
                    # _read_http_body documents for the raw-socket branch, and
                    # a ~129 KB releases document is the normal path, not an
                    # edge case — it failed intermittently on segmentation.
                    # The bound is enforced every pass, so it still refuses an
                    # oversized body without ever buffering it whole.
                    buf = bytearray()
                    while True:
                        chunk = await response.content.readany()
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) > max_body:
                            raise ValueError("response too large")
                    data = json.loads(buf)

    if status != 200:
        raise RuntimeError(f"request failed: HTTP {status}")
    if not isinstance(data, (dict, list)):
        raise ValueError("response was not a JSON object or array")
    return data


def fetch_json_via_proxy_sync(
    proxy_str: str,
    url: str,
    timeout: int,
    user_agent: str = _NEUTRAL_USER_AGENT,
    max_body: int = _MAX_RELEASE_BODY,
    accept: str = "application/json",
) -> dict | list:
    """Blocking wrapper — both metadata fetches run on background threads.

    Deliberately does NOT swallow exceptions the way fetch_status_via_proxy_sync
    does: that one returns a (ok, message) verdict where False IS the failure
    report, while this one returns a document, and the only honest way to say
    "there is no document" is to raise. Swallowing here would hand the update
    checker an empty result that reads as "no new release".

    Every parameter is forwarded unchanged — this wrapper's only job is the
    event loop. `accept` in particular must reach the transport: this is the
    function egress.py's PROXIED branch calls, so dropping it here would be
    exactly the direct-vs-proxied header drift the threading exists to close.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            fetch_json_via_proxy(proxy_str, url, timeout, user_agent, max_body, accept)
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


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

    # ORDERED providers, first-to-answer-with-a-country wins. See
    # _GEO_FALLBACK_PROVIDER for why one oracle was a defect and for the rule
    # (reachability only, never a vote) this carries over from the verify lane.
    #
    # ipwho.is is FIRST here, the INVERSE of exit_guard's order, deliberately:
    # this call site's reader was written for ipwho's dialect, so a green first
    # provider is byte-identical to the behaviour before this change. The
    # HTTPS literal is spelled out in this function rather than hoisted whole
    # into a module constant because tests/test_proxy_checker_geo.py:11-16
    # asserts on `inspect.getsource(check_proxy)` — it is the shipped guard
    # against reintroducing the old CLEARTEXT geo endpoint, and a pure hoist
    # would take it RED with no behaviour change. (That guard matches on the
    # source TEXT, so the endpoint it forbids must not be named here either.)
    #
    # HTTPS on BOTH entries: over cleartext a MITM on the exit could inject a
    # bogus country/timezone that then feeds the persisted fingerprint.
    #
    # `timeout` is PER PROVIDER, not a shared budget — so a check whose first
    # provider hangs can take up to 2x it before answering (20s at the default
    # PROXY_CHECK_TIMEOUT of 10). Deliberate, and the same shape the verify
    # lane's loop uses: a shared budget would let a slow first provider starve
    # the second of the time it needs, which is precisely the case this
    # fallback exists to survive. The aiohttp branch already behaved this way
    # (ClientTimeout is per-request), so the two branches stay symmetric.
    #
    # NOTHING UPSTREAM HAS A SHORTER WATCHDOG, checked rather than assumed:
    # both operator call sites — ui/app.py::_check_proxy / _rotate_proxy and
    # ui/dialogs/proxy.py::do_check — run this on a daemon thread with no
    # join deadline and no timer, re-enabling their button from the callback,
    # so a longer worst case shows up as a longer "checking" state and never
    # as a truncated or double-fired check. There is no "check all" loop that
    # would multiply the doubling across a list; checks are per-proxy and
    # operator-initiated, guarded by `_checking_proxies` against re-entry.
    providers = ("https://ipwho.is/", _GEO_FALLBACK_PROVIDER)

    try:
        if is_socks:

            async def fetch(url: str) -> tuple[int, dict | None]:
                return await asyncio.wait_for(
                    _geo_via_socks(proxy_config, scheme, url), timeout
                )

            fields, failure = await _resolve_geo(providers, fetch)
        else:
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:

                async def fetch(url: str) -> tuple[int, dict | None]:
                    async with session.get(url, proxy=proxy_url) as response:
                        status = response.status
                        if status != 200:
                            return status, None
                        data = await response.json()
                        # Narrowed to `dict | None` for the same reason
                        # _geo_via_socks narrows: check_proxy's specific
                        # "Proxy geo lookup failed" arm is reached only via
                        # None, so a top-level array must not sail past it into
                        # the generic catch-all.
                        return status, (data if isinstance(data, dict) else None)

                fields, failure = await _resolve_geo(providers, fetch)

        if fields is None:
            return False, failure, "", "", "", "", None, None
        ip, code, country, tz, lat, lon = fields
        code, tz, lat, lon = _validate_geo(code, tz, lat, lon)
        return (
            True,
            proxy_ok_message(code, country),
            code, country, ip, tz, lat, lon,
        )
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
