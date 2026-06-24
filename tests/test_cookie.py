import sqlite3

import pytest

from src.services.cookie.codec import decrypt_value, encrypt_value
from src.services.cookie.store import (
    export_cookies,
    import_cookies,
    parse_cookies_json,
)


# --- codec ---


def test_encrypt_decrypt_roundtrip():
    enc = encrypt_value("GA1.1.357781804", "google.com")
    assert enc[:3] == b"v10"
    assert decrypt_value(enc) == "GA1.1.357781804"


def test_encrypt_empty_value():
    enc = encrypt_value("", "example.com")
    assert decrypt_value(enc) == ""


def test_decrypt_unicode_value():
    enc = encrypt_value("значение", "example.com")
    assert decrypt_value(enc) == "значение"


def test_decrypt_garbage_returns_empty():
    assert decrypt_value(b"v10\x00\x01\x02") == ""


def test_decrypt_plaintext_legacy():
    assert decrypt_value(b"plainvalue") == "plainvalue"


# --- store import/export ---


def test_import_then_export(tmp_path):
    cookies = [
        {
            "domain": ".google.com",
            "name": "NID",
            "value": "abc123",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "expirationDate": 2000000000,
            "sameSite": 0,
        }
    ]
    assert import_cookies(str(tmp_path), cookies) == 1
    out = export_cookies(str(tmp_path))
    assert len(out) == 1
    c = out[0]
    assert c["domain"] == ".google.com"
    assert c["name"] == "NID"
    assert c["value"] == "abc123"
    assert c["secure"] is True
    assert c["httpOnly"] is True
    assert "expirationDate" in c


def test_import_skips_invalid_rows(tmp_path):
    cookies = [
        {"name": "noHost", "value": "x"},
        {"domain": "ok.com", "name": "good", "value": "y"},
    ]
    assert import_cookies(str(tmp_path), cookies) == 1


def test_export_empty_when_no_db(tmp_path):
    assert export_cookies(str(tmp_path)) == []


def test_browser_can_read_written_value(tmp_path):
    """The encrypted_value we write must round-trip through the same codec the
    browser uses (sanity that we didn't corrupt the blob)."""
    import_cookies(str(tmp_path), [{"domain": "x.com", "name": "s", "value": "sess99"}])
    db = tmp_path / "Default" / "Cookies"
    con = sqlite3.connect(str(db))
    enc = con.execute("SELECT encrypted_value FROM cookies").fetchone()[0]
    con.close()
    assert decrypt_value(enc) == "sess99"


def test_session_cookie_no_expiry(tmp_path):
    import_cookies(str(tmp_path), [{"domain": "x.com", "name": "s", "value": "v"}])
    out = export_cookies(str(tmp_path))
    assert "expirationDate" not in out[0]


# --- parse ---


def test_parse_bare_list():
    assert parse_cookies_json('[{"name":"a","value":"b","domain":"x.com"}]') == [
        {"name": "a", "value": "b", "domain": "x.com"}
    ]


def test_parse_wrapped():
    assert parse_cookies_json('{"cookies":[{"name":"a"}]}') == [{"name": "a"}]


def test_parse_rejects_non_list():
    with pytest.raises(ValueError):
        parse_cookies_json('{"foo":"bar"}')
