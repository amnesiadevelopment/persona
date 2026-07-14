import re
import secrets
import string

# Rotating providers pin a sticky exit IP to the session segment of the proxy
# credentials (IPRoyal `_session-xxxxxxxx`, Oxylabs `-sessid-x`, Smartproxy
# `user-session-x`); a fresh token maps to a fresh IP on the next connection.
_SESSION_TOKEN = re.compile(r"(?i)(?:session|sessid)[-_]([A-Za-z0-9]+)")
_ALPHABET = string.ascii_lowercase + string.digits


def _random_token(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def regenerate_session_token(url: str) -> str | None:
    """Swap the session token in a proxy URL's credentials for a fresh one.

    Keeps the token length (some providers require an exact length) and every
    other part of the URL. Returns None when the URL carries no credentials or
    no recognizable session token.
    """
    if "@" not in url:
        return None
    userinfo, rest = url.rsplit("@", 1)
    m = _SESSION_TOKEN.search(userinfo)
    if m is None:
        return None
    old = m.group(1)
    new = _random_token(len(old))
    while new == old:
        new = _random_token(len(old))
    return userinfo[: m.start(1)] + new + userinfo[m.end(1):] + "@" + rest
