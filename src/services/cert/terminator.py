"""mTLS terminator — presents a profile's client certificate to ONE admin host.

The browser uses the terminator as its proxy (only when the profile has a
certificate). The terminator MITM-terminates TLS for the certificate's admin
host, does the mutual-TLS handshake to the real admin host itself (presenting the
client certificate), and pipes bytes through. Every other host is a plain tunnel
forwarded to the profile's real proxy. The certificate therefore never enters the
browser and is never offered to any site but the admin host, so it can't become a
fingerprint that identifies the operator elsewhere.

The browser trusts the terminator's leaf certificate without touching any OS
store: Chromium via the per-launch --ignore-certificate-errors-spki-list flag
(keyed to the leaf's public-key hash), Firefox by importing the terminator's CA
into the profile's own cert9.db.

Blocking sockets on background threads (like the proxy bridge), not asyncio: a
MITM upgrade with asyncio's start_tls proved unreliable, plain wrap_socket is not.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import os
import socket
import ssl
import tempfile
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ...core.logging import get_logger

logger = get_logger("cert.terminator")


@dataclass
class Leaf:
    ca_path: str          # CA cert (Firefox trusts this in cert9.db)
    cert_path: str        # leaf cert (chain: leaf + CA), for the TLS server
    key_path: str         # leaf private key
    spki_b64: str         # leaf public-key hash (Chromium spki-list flag)


def _spki_b64(cert: x509.Certificate) -> str:
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def _ski(pub) -> x509.SubjectKeyIdentifier:
    return x509.SubjectKeyIdentifier.from_public_key(pub)


def make_leaf(host: str, out_dir) -> Leaf:
    """Generate a CA and a leaf certificate for ``host`` under ``out_dir``.

    A CA is used (not a bare self-signed leaf) so Firefox can trust the whole
    thing with a single cert9.db import; Chromium keys on the leaf's SPKI hash.
    """
    os.makedirs(str(out_dir), exist_ok=True)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "persona mTLS terminator")]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2037, 1, 1))
        .add_extension(_ski(ca_key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(True, None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2037, 1, 1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_cert.extensions.get_extension_for_class(
                    x509.SubjectKeyIdentifier
                ).value
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_path = os.path.join(str(out_dir), "term_ca.crt")
    cert_path = os.path.join(str(out_dir), "term_leaf.crt")
    key_path = os.path.join(str(out_dir), "term_leaf.key")
    with open(ca_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    with open(cert_path, "wb") as f:
        # leaf first, then CA — the server sends the chain
        f.write(leaf_cert.public_bytes(serialization.Encoding.PEM))
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    os.chmod(key_path, 0o600)
    return Leaf(ca_path, cert_path, key_path, _spki_b64(leaf_cert))


def client_pem_from_p12(p12_path: str, password: str, out_dir) -> str:
    """Write the .p12's cert + key (+ any CA) to a 0600 PEM the terminator loads
    to present the client certificate. Returns the PEM path."""
    with open(p12_path, "rb") as f:
        blob = f.read()
    key, cert, extra = pkcs12.load_key_and_certificates(
        blob, password.encode() if password else None
    )
    os.makedirs(str(out_dir), exist_ok=True)
    fd, pem = tempfile.mkstemp(prefix="persona-mtls-", dir=str(out_dir))
    os.chmod(pem, 0o600)
    data = cert.public_bytes(serialization.Encoding.PEM)
    data += key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    for ca in extra or []:
        data += ca.public_bytes(serialization.Encoding.PEM)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return pem


def host_port(url: str) -> tuple[str, int]:
    """(host, port) from a cert's admin URL. A bare host defaults to :443."""
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    return (p.hostname or ""), (p.port or 443)


class Terminator:
    """A per-profile MITM-CONNECT proxy that presents the client certificate to
    one admin host. Runs on a background thread; the browser points its proxy at
    ``127.0.0.1:port``."""

    def __init__(
        self,
        admin_host: str,
        leaf: Leaf,
        client_pem: str,
        upstream_socks: str | None = None,
        verify_upstream: bool = True,
    ) -> None:
        self.admin_host = admin_host
        self.leaf = leaf
        self._client_pem = client_pem
        self._socks = upstream_socks
        self._verify = verify_upstream
        self.port: int | None = None
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._leaf_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._leaf_ctx.load_cert_chain(leaf.cert_path, leaf.key_path)

    def start(self) -> int:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(50)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.port

    def _accept_loop(self) -> None:
        self._srv.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                continue
            threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(60)
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                req += chunk
            line = req.split(b"\r\n", 1)[0]
            if not line.startswith(b"CONNECT"):
                conn.close()
                return
            target = line.split()[1].decode()
            host = target.split(":")[0]
            port = int(target.split(":")[1]) if ":" in target else 443
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

            if host == self.admin_host:
                self._mitm(conn, host, port)
            else:
                self._tunnel(conn, host, port)
        except Exception as e:
            logger.debug("terminator handler error: %s", e)
            try:
                conn.close()
            except OSError:
                pass

    def _open_upstream(self, host: str, port: int) -> socket.socket:
        # Bound the connect so a dead admin host or wedged proxy fails fast (the
        # browser sees a closed tunnel) instead of hanging the request forever.
        if self._socks:
            return _socks5_connect(self._socks, host, port, timeout=30)
        raw = socket.create_connection((host, port), timeout=30)
        raw.settimeout(None)  # long-lived tunnel; don't time out a quiet link
        return raw

    def _mitm(self, conn: socket.socket, host: str, port: int) -> None:
        tls = self._leaf_ctx.wrap_socket(conn, server_side=True)
        uctx = ssl.create_default_context()
        if not self._verify:
            uctx.check_hostname = False
            uctx.verify_mode = ssl.CERT_NONE
        uctx.load_cert_chain(self._client_pem)
        raw = self._open_upstream(host, port)
        up = uctx.wrap_socket(raw, server_hostname=host)
        _pipe_both(tls, up)

    def _tunnel(self, conn: socket.socket, host: str, port: int) -> None:
        up = self._open_upstream(host, port)
        _pipe_both(conn, up)

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass


def _pipe_both(a: socket.socket, b: socket.socket) -> None:
    t = threading.Thread(target=_pipe, args=(a, b), daemon=True)
    t.start()
    _pipe(b, a)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


def _socks5_connect(
    socks_url: str, dest_host: str, dest_port: int, timeout: float | None = None
) -> socket.socket:
    """Blocking SOCKS5 CONNECT; returns the connected socket. Destination is sent
    as a domain name so DNS resolves at the proxy's exit. ``timeout`` bounds the
    connect + handshake; it's cleared before the socket is handed back so the
    long-lived tunnel isn't subject to it."""
    import struct

    p = urlparse(socks_url if "://" in socks_url else "socks5://" + socks_url)
    s = socket.create_connection((p.hostname, p.port or 1080), timeout=timeout)
    user, pw = (p.username or ""), (p.password or "")
    if user or pw:
        s.sendall(b"\x05\x01\x02")
    else:
        s.sendall(b"\x05\x01\x00")
    ver, method = s.recv(2)
    if method == 0x02:
        s.sendall(
            b"\x01" + bytes([len(user)]) + user.encode()
            + bytes([len(pw)]) + pw.encode()
        )
        if s.recv(2)[1] != 0x00:
            s.close()
            raise OSError("SOCKS5 auth failed")
    elif method != 0x00:
        s.close()
        raise OSError(f"SOCKS5 no acceptable auth ({method})")
    dh = dest_host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(dh)]) + dh + struct.pack(">H", dest_port))
    rep = s.recv(4)
    if rep[1] != 0x00:
        s.close()
        raise OSError(f"SOCKS5 CONNECT rejected ({rep[1]})")
    atyp = rep[3]
    if atyp == 0x01:
        s.recv(4 + 2)
    elif atyp == 0x03:
        ln = s.recv(1)[0]
        s.recv(ln + 2)
    elif atyp == 0x04:
        s.recv(16 + 2)
    # the tunnel is long-lived; drop the handshake timeout so a quiet connection
    # isn't torn down by it.
    s.settimeout(None)
    return s
