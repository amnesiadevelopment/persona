"""Parse provider-issued proxy strings into separate field values.

Handles the three shapes providers hand out (asocks and similar):
URL form ``scheme://login:password@ip:port[:name][[rotate_url]]``,
raw colon form ``ip:port[:login:password][:rotate_url]``, and
curl form ``curl -x <url> <target>``.
"""
import re

_FIELDS = ("scheme", "ip", "port", "login", "password", "name", "rotate_url")
_URL_LINE = re.compile(r"^[A-Za-z][\w+.-]*://")
_BRACKET_URL = re.compile(r"\[([^\[\]]+)\]\s*$")
_TRAILING_URL = re.compile(r"^(.*?):(https?://\S+)$")


def parse_proxy_line(raw: str) -> dict | None:
    """Split one pasted proxy string into scheme/ip/port/login/password/
    name/rotate_url (empty strings for absent fields).

    Returns None when the string doesn't look like a proxy at all.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.startswith("curl "):
            return _parse_curl(text)
        if _URL_LINE.match(text):
            return _parse_url(text)
        return _parse_colon(text)
    except Exception:
        return None


def _result(**fields: str) -> dict:
    out = dict.fromkeys(_FIELDS, "")
    out.update(fields)
    return out


def _parse_url(text: str) -> dict | None:
    rotate = ""
    m = _BRACKET_URL.search(text)
    if m:
        rotate = m.group(1).strip()
        text = text[: m.start()].rstrip()
    scheme, _, rest = text.partition("://")
    login = password = ""
    if "@" in rest:
        creds, rest = rest.rsplit("@", 1)
        login, _, password = creds.partition(":")
    parts = rest.split(":", 2)
    if len(parts) < 2:
        return None
    ip, port = parts[0], parts[1]
    name = parts[2].strip() if len(parts) == 3 else ""
    if not ip or not port.isdigit():
        return None
    return _result(
        scheme=scheme.lower(),
        ip=ip,
        port=port,
        login=login,
        password=password,
        name=name,
        rotate_url=rotate,
    )


def _parse_colon(text: str) -> dict | None:
    rotate = ""
    m = _TRAILING_URL.match(text)
    if m:
        text, rotate = m.group(1), m.group(2)
    parts = text.split(":")
    if len(parts) == 2:
        ip, port = parts
        login = password = ""
    elif len(parts) == 4:
        ip, port, login, password = parts
    else:
        return None
    if not ip or not port.isdigit():
        return None
    return _result(
        ip=ip, port=port, login=login, password=password, rotate_url=rotate
    )


def _parse_curl(text: str) -> dict | None:
    tokens = text.split()
    for flag in ("-x", "--proxy"):
        if flag in tokens:
            idx = tokens.index(flag)
            if idx + 1 < len(tokens):
                url = tokens[idx + 1]
                if _URL_LINE.match(url):
                    return _parse_url(url)
                return _parse_colon(url)
    return None
