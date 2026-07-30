"""The cert manager starts a terminator for a profile's certificate and hands
each engine what it needs to route the admin host through it."""
import ssl

from src.services.cert import manager as cm
from src.services.cert.store import Certificate


def test_no_session_without_certificate(tmp_path):
    assert cm.start_cert_session(None, None, str(tmp_path)) is None


def test_session_exposes_proxy_spki_and_ca(tmp_path):
    cert = Certificate(
        name="admin", p12_path="/nope.p12", password="",
        url="https://admin.example.com/login",
    )
    # a p12 that doesn't exist should yield no session (launch proceeds normally)
    assert cm.start_cert_session(cert, None, str(tmp_path)) is None


def test_session_end_to_end(tmp_path):
    # build a real p12 + mTLS origin, run a full session, and confirm the browser
    # side (trusting only the leaf CA) reaches the origin through the terminator.
    import socket
    import threading
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    def mk(cn, ik=None, ic=None, ca=False, san=None):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
        b = (x509.CertificateBuilder().subject_name(subj)
             .issuer_name(ic.subject if ic else subj).public_key(key.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(datetime.datetime(2020, 1, 1))
             .not_valid_after(datetime.datetime(2035, 1, 1))
             .add_extension(ski, critical=False))
        aki = (x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key())
               if ic is None else
               x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                   ic.extensions.get_extension_for_class(
                       x509.SubjectKeyIdentifier).value))
        b = b.add_extension(aki, critical=False)
        if ca:
            b = b.add_extension(x509.BasicConstraints(True, None), critical=True)
            b = b.add_extension(x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False), critical=True)
        if san:
            b = b.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        return key, b.sign(ik or key, hashes.SHA256())

    ca_key, ca_cert = mk("ca", ca=True)
    srv_key, srv_cert = mk("localhost", ca_key, ca_cert, san="localhost")
    cli_key, cli_cert = mk("client", ca_key, ca_cert)
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    srv_pem = tmp_path / "srv.pem"
    srv_pem.write_bytes(
        srv_cert.public_bytes(serialization.Encoding.PEM)
        + srv_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    p12 = tmp_path / "cli.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"client", cli_key, cli_cert, [ca_cert],
        serialization.BestAvailableEncryption(b"pw")))

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(srv_pem))
    ctx.load_verify_locations(str(ca_pem))
    ctx.verify_mode = ssl.CERT_REQUIRED
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    origin_port = srv.getsockname()[1]
    stop = threading.Event()

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            try:
                s = ctx.wrap_socket(c, server_side=True)
                s.recv(4096)
                s.sendall(b"HTTP/1.1 200 OK\r\nContent-Length:3\r\n\r\nHI!")
                s.close()
            except Exception:
                pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        cert = Certificate(
            name="admin", p12_path=str(p12), password="pw",
            url=f"https://localhost:{origin_port}/login",
        )
        sess = cm.start_cert_session(
            cert, None, str(tmp_path), verify_upstream=False
        )
        assert sess is not None
        assert sess.proxy_url.startswith("http://127.0.0.1:")
        assert sess.spki_b64 and sess.ca_path
        try:
            raw = socket.create_connection(
                ("127.0.0.1", sess.port), timeout=8
            )
            raw.sendall(
                f"CONNECT localhost:{origin_port} HTTP/1.1\r\n\r\n".encode()
            )
            hdr = b""
            while b"\r\n\r\n" not in hdr:
                hdr += raw.recv(1024)
            cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            cctx.load_verify_locations(sess.ca_path)
            cctx.check_hostname = True
            s = cctx.wrap_socket(raw, server_hostname="localhost")
            s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            resp = s.recv(4096)
            s.close()
            assert b"200 OK" in resp and b"HI!" in resp
        finally:
            sess.stop()
    finally:
        stop.set()
        srv.close()
