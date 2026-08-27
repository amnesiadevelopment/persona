from urllib.parse import quote, unquote, urlparse, urlsplit


class ProxyUrlUnparseable(ValueError):
    """A proxy URL that this code cannot fully understand (PS-217).

    Raised INSTEAD of degrading the URL to an unauthenticated connection. It is
    its own class so a caller can tell "this credential is malformed" from any
    other launch failure and refuse the launch rather than continue.

    ⚠️ THE MESSAGE NEVER CONTAINS THE URL — it carries the credential. Same rule
    :func:`services.verify.socks_fetch.parse_socks5h` states for its own
    refusal; these messages reach logs and a pipe the UI reads.

    THE RULE IS ENFORCED BY :func:`_safe_to_quote`, NOT BY EACH CALL SITE BEING
    CAREFUL. The invariant was stated here and violated anyway, so the reason it
    was violated is recorded rather than just the rule:

      urlsplit does NOT put userinfo in ``.username`` when the userinfo contains
      a ``:`` BEFORE the ``://``. It reports ``'bob:p://w@1.2.3.4:1080'`` as
      ``scheme='bob'``, ``username=None`` — the USERNAME lands in ``.scheme``.
      So a guard of the shape ``if not parts.username`` reads that input as
      credential-free and echoes the username, and a test using a URL with a
      REAL scheme (``ftp://user:pw@…``) cannot fail, because userinfo only
      lands in ``.username`` in the case that was never at risk.

    Nothing DERIVED from the URL — not the scheme, not a urllib exception
    string (which quotes the offending port substring, and a password fragment
    can be read as one) — may be interpolated unless the URL is known to carry
    no userinfo at all.
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

#: The scheme assumed when a stored proxy URL carries none ("1.2.3.4:8080").
#:
#: A DELIBERATE CHOICE, and the tree does not agree with itself, so the evidence
#: is recorded here rather than left to the next reader to re-derive:
#:
#:   validate_proxy_format   ACCEPTS a scheme-less URL (pinned, test_validation
#:                           .py:42) — so this shape reaches us by design, and
#:                           refusing it here makes save accept what launch
#:                           rejects, which is the mirror of the contradiction
#:                           validation.py:27-30 exists to prevent.
#:   parse_proxy             http
#:   parse_proxy_server      http
#:   process._proxy_arg      http   (unauthenticated -> parse_proxy_server)
#:   split_proxy_url         socks5 (the EDIT dialog's field default)
#:   the old _proxy_dict     bare "host:port" passed through, which Playwright
#:                           reads as an HTTP proxy.
#:
#: `http` wins on the only criterion that matters: it is what this input already
#: did on BOTH engines before PS-217 touched anything, so no stored profile
#: changes meaning. Choosing socks5 would silently re-point every scheme-less
#: profile at a different protocol on the same address — a behaviour change
#: disguised as a bug fix, and one that fails at connect time rather than
#: visibly.
#:
#: ⚠️ KNOWN, DELIBERATELY UNFIXED ASYMMETRY. `process._proxy_arg` defaults to
#: http here but prepends `socks5://` on its AUTHENTICATED branch, because that
#: branch hands the URL to `ProxyBridge`, which performs a real SOCKS5 handshake
#: upstream unconditionally (`services/proxy/bridge.py`). So Chromium's socks5
#: is load-bearing, not arbitrary, and "make both engines default the same" is
#: not a one-line change there — it is a Chromium-path change, which PS-217
#: explicitly puts out of scope. Recorded so the next person finds the reason
#: instead of the discrepancy.
_DEFAULT_ENGINE_PROXY_SCHEME = "http"


def _safe_to_quote(proxy_url: str) -> bool:
    """May a detail DERIVED from this URL be put in a refusal message?

    Only when the URL carries no userinfo at all. The test is a bare ``"@" in
    url`` and is deliberately CRUDE — it is a security guard, so it is wrong in
    the safe direction on purpose:

      * it cannot be fooled by the urlsplit surprise the exception's docstring
        records, because it never asks urlsplit anything. ``parts.username`` is
        ``None`` on exactly the authenticated inputs that leak
        (``'bob:p://w@…'`` → ``scheme='bob'``), so any guard built on it reads
        those as credential-free.
      * a passwordless ``socks5://bob@host:1080`` still has an ``@``, so it is
        also protected.
      * the false positive is a URL with an ``@`` and no credential, which
        costs only the scheme name in one error message.

    The credential is what an ``@`` in a proxy URL means; a URL containing one
    does not get to have any part of itself quoted back.
    """
    return "@" not in proxy_url


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

    A MISSING scheme is DEFAULTED, not refused — see
    :data:`_DEFAULT_ENGINE_PROXY_SCHEME` for which value and why. Refusing it is
    the one thing this function must not do: ``validate_proxy_format`` accepts a
    scheme-less URL and is pinned that way, so refusing here would make the SAVE
    path accept what the LAUNCH path rejects, and a profile the user entered
    exactly as the validator permitted could not open at all. Defaulting also
    *extracts* the credentials from ``bob:pw@host:1080``, which the old regex
    dropped silently — so it closes another instance of the defect above rather
    than trading it away.

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
    # A MISSING SCHEME IS DEFAULTED, NOT REFUSED, and the test is `"://" not in`
    # rather than a falsy `parts.scheme` — urlsplit does not report an absent
    # scheme the way it looks like it would, and the difference is not cosmetic:
    #
    #   '1.2.3.4:8080'        -> scheme=''          (the only obvious case)
    #   'localhost:8080'      -> scheme='localhost' (a NAMED host)
    #   'bob:pw@1.2.3.4:1080' -> scheme='bob'       (an AUTHENTICATED proxy)
    #
    # so a falsy check catches only the first, and the other two fall into the
    # unknown-scheme refusal below instead. All three are URLs the product
    # accepts at save time, and the last one is the case that carries a
    # credential — exactly what this function exists to protect.
    if "://" not in proxy_url:
        proxy_url = f"{_DEFAULT_ENGINE_PROXY_SCHEME}://{proxy_url}"
    # EVERY message below is gated on _safe_to_quote. The detail being quoted is
    # DERIVED from the URL (urlsplit's idea of the scheme, urllib's exception
    # text) and each one has been measured to carry credential material on an
    # input the SAVE path accepts — see the exception's docstring.
    safe = _safe_to_quote(proxy_url)
    _schemes = ", ".join(sorted(_ENGINE_PROXY_SCHEMES))
    try:
        parts = urlsplit(proxy_url)
    except ValueError as e:
        # A malformed IPv6 literal, a non-numeric port, etc. Never echo the URL.
        # urllib quotes the offending substring, and on an authenticated URL
        # that substring can BE the credential.
        raise ProxyUrlUnparseable(
            f"proxy URL is not a valid URL ({e})"
            if safe
            else "proxy URL is not a valid URL"
        ) from e

    scheme = _ENGINE_PROXY_SCHEMES.get(parts.scheme)
    if scheme is None:
        # THE LEAK THIS GUARD EXISTS FOR: on 'bob:p://w@1.2.3.4:1080' — which
        # validate_proxy_format ACCEPTS — urlsplit reports scheme='bob', so the
        # unqualified form of this message publishes the USERNAME.
        raise ProxyUrlUnparseable(
            f"proxy scheme {parts.scheme!r} is not one this engine can take "
            f"(expected one of {_schemes})"
            if safe
            else f"proxy URL is not in a form this engine can read "
            f"(expected a URL whose scheme is one of {_schemes})"
        )

    try:
        host, port = parts.hostname, parts.port
    except ValueError as e:
        # urllib's text quotes the unreadable port value, and a password
        # fragment can be read as one: 'socks5://bob:p://w@1.2.3.4:1080' gives
        # "Port could not be cast to integer value as 'p:'".
        raise ProxyUrlUnparseable(
            f"proxy URL has an unreadable port ({e})"
            if safe
            else "proxy URL has an unreadable port"
        ) from e
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
