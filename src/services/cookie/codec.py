import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Chromium on Linux with --password-store=basic encrypts cookie values with
# AES-128-CBC, a key derived from the fixed password "peanuts", IV of 16
# spaces, and a "v10" prefix. The plaintext is SHA-256(host_key) followed by
# the real value (an anti-tamper measure). We always launch with
# --password-store=basic, so this key is deterministic and lets us read/write
# the Cookies database directly.
_PREFIX = b"v10"
_IV = b" " * 16
_KEY = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
_DOMAIN_HASH_LEN = 32


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def encrypt_value(value: str, host_key: str) -> bytes:
    """Encrypt a cookie value the way Chromium's basic store expects, so the
    browser accepts it: v10 + AES-CBC(SHA256(host) + value)."""
    plaintext = hashlib.sha256(host_key.encode()).digest() + value.encode()
    enc = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    body = enc.update(_pkcs7_pad(plaintext)) + enc.finalize()
    return _PREFIX + body


def decrypt_value(encrypted: bytes) -> str:
    """Reverse of encrypt_value: strip v10, AES-CBC decrypt, drop the 32-byte
    domain hash, unpad. Returns '' for plaintext/legacy or undecryptable rows."""
    if not encrypted or encrypted[:3] != _PREFIX:
        try:
            return encrypted.decode()
        except Exception:
            return ""
    body = encrypted[3:]
    if len(body) % 16 != 0 or not body:
        return ""
    dec = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
    plaintext = dec.update(body) + dec.finalize()
    plaintext = _pkcs7_unpad(plaintext)
    if len(plaintext) >= _DOMAIN_HASH_LEN:
        plaintext = plaintext[_DOMAIN_HASH_LEN:]
    try:
        return plaintext.decode()
    except Exception:
        return ""
