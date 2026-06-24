from urllib.parse import urlparse


def build_proxy_url(
    scheme: str,
    host: str,
    port: str,
    username: str = "",
    password: str = "",
) -> str:
    """Assemble a proxy URL from separate fields.

    Credentials are included only when a username is present.
    """
    auth = ""
    if username:
        auth = f"{username}:{password}@" if password else f"{username}@"
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
        return {
            "scheme": p.scheme or "socks5",
            "host": p.hostname or "",
            "port": str(p.port) if p.port else "",
            "username": p.username or "",
            "password": p.password or "",
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
        if p.username:
            cfg["username"] = p.username
        if p.password:
            cfg["password"] = p.password
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
