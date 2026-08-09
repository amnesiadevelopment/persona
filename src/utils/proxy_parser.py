from urllib.parse import quote, unquote, urlparse


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
