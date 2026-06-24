import json
import pathlib
import sqlite3

from ...core.logging import get_logger
from .codec import decrypt_value, encrypt_value

logger = get_logger("cookie.store")

# Chromium epoch is 1601-01-01; unix epoch offset in microseconds.
_EPOCH_OFFSET = 11644473600

_CREATE = """
CREATE TABLE IF NOT EXISTS cookies(
    creation_utc INTEGER NOT NULL,
    host_key TEXT NOT NULL,
    top_frame_site_key TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    encrypted_value BLOB NOT NULL,
    path TEXT NOT NULL,
    expires_utc INTEGER NOT NULL,
    is_secure INTEGER NOT NULL,
    is_httponly INTEGER NOT NULL,
    last_access_utc INTEGER NOT NULL,
    has_expires INTEGER NOT NULL,
    is_persistent INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    samesite INTEGER NOT NULL,
    source_scheme INTEGER NOT NULL,
    source_port INTEGER NOT NULL,
    last_update_utc INTEGER NOT NULL,
    source_type INTEGER NOT NULL DEFAULT 0,
    has_cross_site_ancestor INTEGER NOT NULL DEFAULT 0,
    UNIQUE (host_key, top_frame_site_key, name, path, source_scheme, source_port)
)
"""

_SAMESITE = {"no_restriction": 0, "lax": 1, "strict": 2, "unspecified": -1, "none": 0}


def _to_chromium_time(unix_seconds: float) -> int:
    return int((unix_seconds + _EPOCH_OFFSET) * 1_000_000)


def _from_chromium_time(chromium_us: int) -> float:
    if not chromium_us:
        return 0.0
    return chromium_us / 1_000_000 - _EPOCH_OFFSET


def _norm_samesite(v) -> int:
    if isinstance(v, int):
        return v
    return _SAMESITE.get(str(v).lower(), -1)


def import_cookies(profile_dir: str, cookies: list[dict]) -> int:
    """Write a list of cookies (Cookie-Editor JSON shape) into the profile's
    Cookies DB. Returns how many were written. Creates the DB if absent.
    """
    default_dir = pathlib.Path(profile_dir) / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    db = default_dir / "Cookies"
    con = sqlite3.connect(str(db))
    try:
        con.execute(_CREATE)
        now = _to_chromium_time(_now())
        written = 0
        for c in cookies:
            host = c.get("domain") or c.get("host_key") or ""
            name = c.get("name", "")
            value = c.get("value", "")
            path = c.get("path", "/")
            if not host or not name:
                continue
            exp = c.get("expirationDate") or c.get("expires") or 0
            has_expires = 1 if exp else 0
            expires_utc = _to_chromium_time(float(exp)) if exp else 0
            secure = 1 if c.get("secure") else 0
            httponly = 1 if c.get("httpOnly") or c.get("httponly") else 0
            samesite = _norm_samesite(c.get("sameSite", c.get("samesite", -1)))
            # Chromium rejects SameSite=None cookies that aren't Secure, so a
            # None cookie that isn't marked secure would silently never be sent.
            # Force Secure in that case (the only valid combination).
            if samesite == 0:
                secure = 1
            # Chromium stores cookies with the scheme/port of the context they
            # came from; modern sites are https, and a cookie tagged http:80
            # won't be sent to an https:443 request. Default to the secure
            # context (scheme 2 / port 443) like the browser itself does.
            source_scheme = 2
            source_port = int(c.get("port") or 443)
            enc = encrypt_value(value, host)
            con.execute(
                """INSERT OR REPLACE INTO cookies(
                    creation_utc, host_key, top_frame_site_key, name, value,
                    encrypted_value, path, expires_utc, is_secure, is_httponly,
                    last_access_utc, has_expires, is_persistent, priority,
                    samesite, source_scheme, source_port, last_update_utc,
                    source_type, has_cross_site_ancestor)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now, host, "", name, "", enc, path, expires_utc,
                    secure, httponly, now, has_expires, has_expires, 1,
                    samesite, source_scheme, source_port, now, 0, 0,
                ),
            )
            written += 1
        con.commit()
        logger.info("Imported %d cookies into %s", written, profile_dir)
        return written
    finally:
        con.close()


def export_cookies(profile_dir: str) -> list[dict]:
    """Read the profile's Cookies DB and return a Cookie-Editor-style JSON
    list with decrypted values. Empty list if no DB."""
    db = pathlib.Path(profile_dir) / "Default" / "Cookies"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            """SELECT host_key, name, encrypted_value, value, path, expires_utc,
                      is_secure, is_httponly, has_expires, samesite
               FROM cookies"""
        )
        out = []
        for row in cur.fetchall():
            host, name, enc, plain, path, exp, sec, httponly, has_exp, ss = row
            value = decrypt_value(enc) if enc else (plain or "")
            cookie = {
                "domain": host,
                "name": name,
                "value": value,
                "path": path,
                "secure": bool(sec),
                "httpOnly": bool(httponly),
                "sameSite": ss,
            }
            if has_exp and exp:
                cookie["expirationDate"] = _from_chromium_time(exp)
            out.append(cookie)
        return out
    finally:
        con.close()


def parse_cookies_json(text: str) -> list[dict]:
    """Parse pasted cookie JSON; accepts either a bare list or {"cookies":[...]}.
    Raises ValueError on bad input."""
    data = json.loads(text)
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of cookies")
    return data


def _now() -> float:
    import time

    return time.time()
