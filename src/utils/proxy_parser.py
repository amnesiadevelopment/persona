from urllib.parse import quote, unquote, urlparse, urlsplit


class ProxyUrlUnparseable(ValueError):
    """A proxy URL that this code cannot fully understand (PS-217).

    Raised INSTEAD of degrading the URL to an unauthenticated connection. It is
    its own class so a caller can tell "this credential is malformed" from any
    other launch failure and refuse the launch rather than continue.

    ⚠️ THE MESSAGE NEVER CONTAINS THE URL — it carries the credential. Same rule
    :func:`services.verify.socks_fetch.parse_socks5h` states for its own
    refusal; these messages reach logs and a pipe the UI reads.
    """


# The schemes an engine proxy value may legitimately carry.
#
# NOT a taste list — each entry is something a caller can actually reach:
# `src/ui/dialogs/proxy.py` offers socks5/http/https, and invisible_playwright's
# launcher documents that `socks5://`/`socks4://` go through the patched
# nsProtocolProxyService while `http(s)://` go through Playwright's own proxy=
# kwarg. Refusing http/https here would fail-close a configuration the product
# ships a dropdown for, which is a regression, not a fix.
#
# The values are what the ENGINE is handed. `socks5h` normalises to `socks5`
# because socks5h in a `server=` value is a curl-ism the browser rejects
# outright — remote resolution is carried by the network.proxy.socks_remote_dns
# pref instead, which both launch paths set for a proxied profile. That is the
# same normalisation `services/verify/browser_tier._proxy_dict` documents.
_ENGINE_PROXY_SCHEMES = {
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "socks4a": "socks4",
    "http": "http",
    "https": "https",
}


def engine_proxy_dict(proxy_url: str | None) -> dict | None:
    """Turn a proxy URL into the engine's proxy dict — or REFUSE it (PS-217).

    Returns ``None`` only for a genuinely absent proxy (empty/None), which means
    "this profile is direct". Anything non-empty either yields a complete dict
    or raises :class:`ProxyUrlUnparseable`. There is deliberately no third
    outcome: the defect this replaces was a fall-through that returned
    ``{"server": <the raw url>}`` with the credentials never extracted, so an
    authenticated proxy was handed a connection with no auth, refused it, and
    rendered to the user as a page that would not load — indistinguishable from
    a dropped network.

    WHY urlsplit AND NOT A REGEX. The regex this replaces
    (``socks5://(?:([^:]+):([^@]+)@)?(.+)``) was wrong on three independent
    axes, and the third is the sharp one:

      * SCHEME — anchored on the literal ``socks5://``, so ``socks5h://`` (the
        form this project's own credential path uses, see
        ``verify/socks_fetch.parse_socks5h``) and any uppercase spelling fell
        through and lost their credentials. urlsplit lowercases the scheme, so
        the case axis closes for free.
      * ``:`` IN THE USERNAME — ``[^:]+`` cannot match it, so the whole match
        fails and the credentials are dropped.
      * ``@`` IN THE PASSWORD — ``[^@]+`` stops at the FIRST ``@``, so
        ``socks5://bob:se@cret@host:1080`` yielded password ``se`` and server
        ``socks5://cret@host:1080``. That is not a dropped credential, it is a
        MISDIRECTED CONNECTION: the browser is pointed at a different proxy
        address entirely. urlsplit splits the userinfo at the LAST ``@``, which
        is what RFC 3986 specifies.

    Credentials are unquoted, the inverse of :func:`build_proxy_url`'s quote —
    that pair is how a credential containing reserved characters survives the
    round trip (audit6 #8), and it is also the ESCAPE HATCH for a username
    containing ``:``, which is only expressible percent-encoded.

    A host AND a port are both required, matching the sibling parsers
    (:func:`parse_proxy`, ``parse_socks5h``). Fail-closed is the correct
    direction here: this is the value that decides whether a profile whose whole
    purpose is not to touch the network directly reaches it through the tunnel.
    """
    if not proxy_url or proxy_url == "None":
        return None
    try:
        parts = urlsplit(proxy_url)
    except ValueError as e:
        # A malformed IPv6 literal, a non-numeric port, etc. Never echo the URL.
        raise ProxyUrlUnparseable(f"proxy URL is not a valid URL ({e})") from e

    if not parts.scheme:
        raise ProxyUrlUnparseable(
            "proxy URL has no scheme (expected one of "
            f"{', '.join(sorted(_ENGINE_PROXY_SCHEMES))})"
        )
    scheme = _ENGINE_PROXY_SCHEMES.get(parts.scheme)
    if scheme is None:
        raise ProxyUrlUnparseable(
            f"proxy scheme {parts.scheme!r} is not one this engine can take "
            f"(expected one of {', '.join(sorted(_ENGINE_PROXY_SCHEMES))})"
        )

    try:
        host, port = parts.hostname, parts.port
    except ValueError as e:
        raise ProxyUrlUnparseable(f"proxy URL has an unreadable port ({e})") from e
    if not host:
        raise ProxyUrlUnparseable("proxy URL is missing a host")
    if not port:
        raise ProxyUrlUnparseable("proxy URL is missing a port")

    out = {"server": f"{scheme}://{host}:{port}"}
    if parts.username:
        out["username"] = unquote(parts.username)
        # "" rather than None: a username with no password is legal, and the
        # engine's dict wants a string. The old code passed None through here.
        out["password"] = unquote(parts.password) if parts.password else ""
    return out


def build_proxy_url(
    scheme: str,
    host: str,
    port: str,
    username: str = "",
    password: str = "",
) -> str:
    """Assemble a proxy URL from separate fields.

    Credentials are included only when a username is present and are percent-
    encoded so a password containing URL-reserved characters (/ # ? @) survives.
    Assembling raw creds let the dialog SAVE a url that the launcher's urlparse
    then rejected (a `/#?` in the password made the host parse empty → the
    fail-closed ProxyUnresolvedError), while an `@` in the password was rejected
    at save though the launcher would have handled it (audit6 #8). Encoding here
    + urlparse's automatic decoding on split makes the two paths agree.
    """
    auth = ""
    if username:
        u = quote(username, safe="")
        if password:
            auth = f"{u}:{quote(password, safe='')}@"
        else:
            auth = f"{u}@"
    return f"{scheme}://{auth}{host}:{port}"


def split_proxy_url(url: str) -> dict:
    """Break a proxy URL into separate fields for editing.

    Returns keys scheme/host/port/username/password (empty strings when
    absent). Defaults scheme to socks5 and leaves fields blank on parse error.
    """
    blank = {
        "scheme": "socks5",
        "host": "",
        "port": "",
        "username": "",
        "password": "",
    }
    if not url:
        return blank
    try:
        text = url if "://" in url else "socks5://" + url
        p = urlparse(text)
        # urlparse does NOT decode username/password — unquote so the dialog shows
        # the real credential (a percent-encoded `p%2Fss` → `p/ss`), the inverse
        # of build_proxy_url's quote (audit6 #8).
        return {
            "scheme": p.scheme or "socks5",
            "host": p.hostname or "",
            "port": str(p.port) if p.port else "",
            "username": unquote(p.username) if p.username else "",
            "password": unquote(p.password) if p.password else "",
        }
    except Exception:
        return blank


def parse_proxy(proxy_str: str) -> dict | None:
    if not proxy_str or proxy_str == "None":
        return None
    try:
        if "://" not in proxy_str:
            proxy_str = "http://" + proxy_str
        p = urlparse(proxy_str)
        if not p.hostname or not p.port:
            return None
        cfg = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        # Decode percent-encoded creds so the auth handler gets the real
        # username/password (build_proxy_url encodes them) (audit6 #8).
        if p.username:
            cfg["username"] = unquote(p.username)
        if p.password:
            cfg["password"] = unquote(p.password)
        return cfg
    except Exception:
        return None


def parse_proxy_server(proxy_str: str | None) -> str | None:
    """Return a Chromium --proxy-server value (scheme://host:port) or None.

    Chromium's --proxy-server does not accept inline credentials; auth is
    handled separately. We pass only scheme://host:port here.
    """
    if not proxy_str or proxy_str == "None":
        return None
    try:
        if "://" not in proxy_str:
            proxy_str = "http://" + proxy_str
        p = urlparse(proxy_str)
        if not p.hostname or not p.port:
            return None
        return f"{p.scheme}://{p.hostname}:{p.port}"
    except Exception:
        return None
