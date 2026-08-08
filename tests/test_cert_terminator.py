"""The mTLS terminator presents a profile's client certificate to ONE admin host
via a local MITM-CONNECT proxy, so the certificate never enters the browser and
can't leak to any other site.
"""
import base64
import datetime
import hashlib
import socket
import ssl
import threading

import pytest

from src.services.cert import terminator as term


# ---------- helpers: build a real mTLS origin ----------

def _mk(cn, issuer_key=None, issuer_cert=None, ca=False, san=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    b = (
        x509.CertificateBuilder()
        .subject_name(subj)
        .issuer_name(issuer_cert.subject if issuer_cert else subj)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2035, 1, 1))
        .add_extension(ski, critical=False)
    )
    if issuer_cert is None:
        aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key())
    else:
        aki = x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
            issuer_cert.extensions.get_extension_for_class(
                x509.SubjectKeyIdentifier
            ).value
        )
    b = b.add_extension(aki, critical=False)
    if ca:
        b = b.add_extension(x509.BasicConstraints(True, None), critical=True)
        b = b.add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    if san:
        b = b.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False
        )
    return key, b.sign(issuer_key or key, hashes.SHA256())


def _mtls_origin(tmp_path, require_client=True):
    """A local TLS origin that (optionally) requires a client cert. Returns
    (port, ca_pem, client_p12, p12_pass, stop_fn) and logs admits into a list."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    ca_key, ca_cert = _mk("t-ca", ca=True)
    srv_key, srv_cert = _mk("localhost", ca_key, ca_cert, san="localhost")
    cli_key, cli_cert = _mk("client", ca_key, ca_cert)

    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    srv_pem = tmp_path / "srv.pem"
    srv_pem.write_bytes(
        srv_cert.public_bytes(serialization.Encoding.PEM)
        + srv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    p12 = tmp_path / "cli.p12"
    p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"client", cli_key, cli_cert, [ca_cert],
            serialization.BestAvailableEncryption(b"pw"),
        )
    )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(srv_pem))
    if require_client:
        ctx.load_verify_locations(str(ca_pem))
        ctx.verify_mode = ssl.CERT_REQUIRED

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()
    admits = []

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            try:
                s = ctx.wrap_socket(c, server_side=True)
                admits.append(True)
                s.recv(4096)
                s.sendall(b"HTTP/1.1 200 OK\r\nContent-Length:3\r\n\r\nHI!")
                s.close()
            except Exception:
                try:
                    c.close()
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()

    def stopper():
        stop.set()
        srv.close()

    return port, str(ca_pem), str(p12), "pw", admits, stopper


def _connect_via_proxy(proxy_port, host, dest_port, leaf_ca, timeout=8):
    """Speak HTTP CONNECT to the terminator, upgrade to TLS trusting the leaf CA,
    send a request, return the response bytes."""
    raw = socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout)
    raw.sendall(
        f"CONNECT {host}:{dest_port} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
    )
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += raw.recv(1024)
    assert b"200" in resp.split(b"\r\n")[0]
    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cctx.load_verify_locations(leaf_ca)
    cctx.check_hostname = True
    s = cctx.wrap_socket(raw, server_hostname=host)
    s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    out = s.recv(4096)
    s.close()
    return out


# ---------- pure helpers ----------

def test_leaf_has_ca_and_valid_spki(tmp_path):
    leaf = term.make_leaf("admin.example.com", tmp_path)
    raw = base64.b64decode(leaf.spki_b64)
    assert len(raw) == 32
    from cryptography import x509
    with open(leaf.cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    from cryptography.hazmat.primitives import serialization
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert base64.b64encode(hashlib.sha256(der).digest()).decode() == leaf.spki_b64
    # a CA cert exists and signs the leaf
    with open(leaf.ca_path, "rb") as f:
        ca = x509.load_pem_x509_certificate(f.read())
    assert ca.subject == cert.issuer


def test_host_port_from_url():
    assert term.host_port("https://a.com/login") == ("a.com", 443)
    assert term.host_port("https://a.com:8443/x") == ("a.com", 8443)
    assert term.host_port("a.com") == ("a.com", 443)


def test_client_pem_from_p12(tmp_path):
    _, _, p12, pw, _, stop = _mtls_origin(tmp_path)
    try:
        pem = term.client_pem_from_p12(p12, pw, tmp_path)
        text = open(pem).read()
        assert "BEGIN CERTIFICATE" in text
        assert "PRIVATE KEY" in text
    finally:
        stop()


# ---------- the terminator end to end ----------

def test_terminator_presents_cert_to_admin_host(tmp_path):
    port, ca_pem, p12, pw, admits, stop = _mtls_origin(tmp_path)
    try:
        leaf = term.make_leaf("localhost", tmp_path)
        pem = term.client_pem_from_p12(p12, pw, tmp_path)
        t = term.Terminator(
            "localhost", leaf, pem, verify_upstream=False
        )
        pport = t.start()
        try:
            resp = _connect_via_proxy(pport, "localhost", port, leaf.ca_path)
            assert b"200 OK" in resp
            assert b"HI!" in resp
        finally:
            t.stop()
    finally:
        stop()


def test_terminator_does_not_present_cert_to_other_host(tmp_path):
    # an origin that also requires a client cert, but is NOT the admin host, must
    # be reached as a plain tunnel with no certificate → its handshake fails.
    port, ca_pem, p12, pw, admits, stop = _mtls_origin(tmp_path)
    try:
        leaf = term.make_leaf("admin.example.com", tmp_path)  # admin != localhost
        pem = term.client_pem_from_p12(p12, pw, tmp_path)
        t = term.Terminator("admin.example.com", leaf, pem, verify_upstream=False)
        pport = t.start()
        try:
            raw = socket.create_connection(("127.0.0.1", pport), timeout=8)
            raw.sendall(
                f"CONNECT localhost:{port} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
            )
            hdr = b""
            while b"\r\n\r\n" not in hdr:
                hdr += raw.recv(1024)
            # plain tunnel to the mTLS origin, presenting NO client cert
            cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            cctx.check_hostname = False
            cctx.verify_mode = ssl.CERT_NONE
            s = cctx.wrap_socket(raw, server_hostname="localhost")
            with pytest.raises(ssl.SSLError):
                s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                s.recv(100)
        finally:
            t.stop()
    finally:
        stop()


def _local_socks5(seen):
    """A no-auth SOCKS5 server that records each CONNECT target and forwards it.
    Returns (port, stop_fn)."""
    import struct

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                b.sendall(d)
        except Exception:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def handle(c):
        try:
            c.recv(262)
            c.sendall(b"\x05\x00")
            req = c.recv(4)
            atyp = req[3]
            if atyp == 1:
                host = socket.inet_ntoa(c.recv(4))
            elif atyp == 3:
                ln = c.recv(1)[0]
                host = c.recv(ln).decode()
            else:
                host = socket.inet_ntoa(c.recv(16))
            dport = struct.unpack(">H", c.recv(2))[0]
            seen.append(f"{host}:{dport}")
            up = socket.create_connection((host, dport))
            c.sendall(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00" * 2)
            threading.Thread(target=pipe, args=(c, up), daemon=True).start()
            pipe(up, c)
        except Exception:
            pass

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            threading.Thread(target=handle, args=(c,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return port, (lambda: (stop.set(), srv.close()))


def test_terminator_routes_upstream_through_socks(tmp_path):
    # When a profile has a proxy, the terminator's mTLS connection to the admin
    # host must go THROUGH that SOCKS proxy so the exit IP matches the rest of the
    # profile — never a direct connection that would leak the real IP.
    port, ca_pem, p12, pw, admits, stop = _mtls_origin(tmp_path)
    seen: list = []
    socks_port, socks_stop = _local_socks5(seen)
    try:
        leaf = term.make_leaf("localhost", tmp_path)
        pem = term.client_pem_from_p12(p12, pw, tmp_path)
        t = term.Terminator(
            "localhost", leaf, pem,
            upstream_socks=f"socks5://127.0.0.1:{socks_port}",
            verify_upstream=False,
        )
        pport = t.start()
        try:
            resp = _connect_via_proxy(pport, "localhost", port, leaf.ca_path)
            assert b"200 OK" in resp and b"HI!" in resp
            # the admin origin was reached VIA the SOCKS proxy
            assert any(str(port) in s for s in seen), seen
        finally:
            t.stop()
    finally:
        socks_stop()
        stop()


def test_stop_wipes_client_and_leaf_keys(tmp_path):
    # #7 (audit4): the decrypted UNENCRYPTED client key + leaf key sit in
    # .persona-mtls; stop() must securely delete them so the operator's real mTLS
    # key isn't left as plaintext PEM at rest.
    import os

    leaf = term.make_leaf("admin.example.com", str(tmp_path))
    assert os.path.exists(leaf.key_path)
    assert os.path.exists(leaf.cert_path)

    # a stand-in client PEM (the decrypted key would live here)
    client_pem = os.path.join(str(tmp_path), "client.pem")
    with open(client_pem, "w") as f:
        f.write("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n")

    t = term.Terminator("admin.example.com", leaf, client_pem)
    t.stop()

    assert not os.path.exists(client_pem), "client key PEM must be wiped on stop"
    assert not os.path.exists(leaf.key_path), "leaf key must be wiped on stop"
    assert not os.path.exists(leaf.cert_path), "leaf cert must be wiped on stop"
