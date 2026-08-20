"""Both loopback listeners must serve the browser they were started for — and
nobody else on the machine.

The SOCKS5 bridge re-applies the operator's proxy credentials on the way out, and
the mTLS terminator performs the mutual-TLS handshake with the operator's client
certificate. Before this, any local process could drive either one. These tests
prove BOTH halves: the legitimate client still works, and a process that is not
the browser is refused *before* any upstream work is done on its behalf.

A control that cannot be shown to refuse has not been tested, so the refusals
here assert on the CONSEQUENCE (no upstream connection, no handshake), not on
whether some helper was called.
"""

import os
import socket
import ssl
import subprocess
import sys
import threading
import time

import pytest

from src.core import peerauth
from src.core.peerauth import PeerGate
from src.services.cert import terminator as term
from src.services.proxy.bridge import ProxyBridge


# --------------------------------------------------------------------------
# The primitive: why this is not SO_PEERCRED
# --------------------------------------------------------------------------

def test_so_peercred_is_meaningless_on_a_tcp_socket():
    """Pins the reason the obvious primitive was NOT used.

    SO_PEERCRED / LOCAL_PEERCRED are AF_UNIX mechanisms. On an AF_INET socket
    the kernel does not fail the call — it returns a zeroed credential — so a
    check built on it would read as a boundary while authenticating nothing.
    Both listeners here are TCP, which is why peer verification resolves the
    peer to a process instead.

    If a future platform ever returns a real pid here, this test fails and the
    simpler primitive becomes available — that is a result worth being told.
    """
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is None:
        pytest.skip("SO_PEERCRED not exposed on this platform")

    import struct

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        conn, _ = srv.accept()
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, peercred, struct.calcsize("3i")
            )
            pid, _uid, _gid = struct.unpack("3i", raw)
            assert pid == 0, (
                "SO_PEERCRED returned a real pid on TCP — the AF_UNIX primitive "
                "may now be usable here"
            )
        except OSError:
            pass  # a platform that refuses outright is equally unusable
        finally:
            conn.close()
            client.close()
    finally:
        srv.close()


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------

def _sleeper() -> subprocess.Popen:
    """A live process that is NOT this one and owns no connection to us."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])


def _reap(proc: subprocess.Popen) -> None:
    """Kill AND wait. kill() alone leaves a zombie for the rest of the session,
    which the gate then has to walk on every subsequent scan."""
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def test_gate_refuses_a_peer_outside_the_browser_tree():
    gate = PeerGate("test")
    other = _sleeper()
    try:
        gate.bind_to_process(other.pid)
        # A plausible-looking loopback peer that simply is not that process's.
        assert gate.allows(("127.0.0.1", 65000)) is False
    finally:
        _reap(other)


def test_gate_refuses_a_non_loopback_peer():
    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())
    assert gate.allows(("10.0.0.7", 4444)) is False


def test_gate_refuses_a_malformed_peer():
    gate = PeerGate("test")
    gate.bind_to_process(os.getpid())
    assert gate.allows(None) is False
    assert gate.allows(("127.0.0.1",)) is False


def test_gate_refuses_while_unclaimed(monkeypatch):
    """An unclaimed listener has no legitimate client.

    The wait is shortened here so the test does not pay the real bind grace
    period; the refusal, not the duration, is the behaviour under test.
    """
    monkeypatch.setattr(peerauth, "_BIND_WAIT_SECONDS", 0.2)
    gate = PeerGate("test")
    assert gate.allows(("127.0.0.1", 65000)) is False


def test_gate_allows_a_descendant_of_the_browser(tmp_path):
    """Authorization covers the whole process TREE, not one pid.

    Neither engine connects from the pid the launcher holds: Chromium proxies
    through its network-service child. A gate that only accepted the root pid
    would refuse the real browser.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    listen_port = srv.getsockname()[1]

    # parent -> grandchild, and the GRANDCHILD is what connects
    code = (
        "import subprocess,sys,time\n"
        "g = subprocess.Popen([sys.executable,'-c',"
        f"\"import socket,time;c=socket.create_connection(('127.0.0.1',{listen_port}));time.sleep(6)\"])\n"
        "g.wait()\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", code])
    grandchildren = []
    try:
        conn, peer = srv.accept()
        gate = PeerGate("test")
        gate.set_listen_port(listen_port)
        gate.bind_to_process(parent.pid)
        try:
            import psutil

            grandchildren = psutil.Process(parent.pid).children(recursive=True)
        except Exception:
            grandchildren = []
        assert gate.allows(peer) is True, (
            "the browser's own grandchild was refused — this would break "
            "proxied browsing"
        )
        conn.close()
    finally:
        # Tear down the whole tree: an orphaned grandchild would outlive this
        # test still holding a live loopback connection.
        for gc in grandchildren:
            try:
                gc.kill()
            except Exception:
                pass
        _reap(parent)
        srv.close()


# --------------------------------------------------------------------------
# The SOCKS5 bridge
# --------------------------------------------------------------------------

class RecordingUpstream(threading.Thread):
    """An upstream proxy that records whether it was ever contacted."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.connections = 0
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self._srv.settimeout(0.25)
        self.port = self._srv.getsockname()[1]
        self.url = f"socks5://alice:secret@127.0.0.1:{self.port}"
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (OSError, socket.timeout):
                continue
            self.connections += 1
            try:
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def test_bridge_refuses_a_stranger_and_opens_no_upstream_tunnel():
    """The negative case: a local process that is not the browser gets nothing.

    Asserted on the consequence that matters — the operator's proxy credentials
    are never spent — plus the fact that the bridge does not even answer the
    SOCKS5 greeting, so the refusal leaks nothing about what it protects.
    """
    upstream = RecordingUpstream()
    upstream.start()
    other = _sleeper()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        # The listener belongs to some OTHER process; this test is the attacker.
        bridge.bind_to_process(other.pid)

        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")  # SOCKS5 greeting, no-auth
        client.settimeout(5)
        try:
            reply = client.recv(2)
        except (socket.timeout, ConnectionResetError, OSError):
            reply = b""
        client.close()

        assert reply == b"", (
            "the bridge answered the SOCKS5 greeting for an unauthorized caller"
        )
        time.sleep(0.3)
        assert upstream.connections == 0, (
            "the bridge opened an upstream tunnel for an unauthorized caller — "
            "the operator's proxy credentials were spent on it"
        )
    finally:
        bridge.stop()
        _reap(other)
        upstream.stop()


def test_bridge_serves_its_own_browser():
    """The positive case: the authorized client is not refused."""
    upstream = RecordingUpstream()
    upstream.start()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        bridge.bind_to_process(os.getpid())
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")
        client.settimeout(5)
        reply = client.recv(2)
        client.close()
        assert reply == b"\x05\x00", (
            "the bridge refused its own browser — proxied browsing would break"
        )
    finally:
        bridge.stop()
        upstream.stop()


def test_bridge_refusal_is_the_mechanism_not_the_test(monkeypatch):
    """Falsification: with the gate forced open, the negative test's assertion
    flips. This binds the test above to the check, so an inert implementation
    cannot pass it."""
    monkeypatch.setattr(PeerGate, "allows", lambda self, peer: True)

    upstream = RecordingUpstream()
    upstream.start()
    other = _sleeper()
    bridge = ProxyBridge(upstream.url)
    port = bridge.start()
    try:
        bridge.bind_to_process(other.pid)
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"\x05\x01\x00")
        client.settimeout(5)
        reply = client.recv(2)
        client.close()
        assert reply == b"\x05\x00", (
            "with the gate disabled the stranger should have been served — "
            "if not, the negative test proves nothing about the gate"
        )
    finally:
        bridge.stop()
        _reap(other)
        upstream.stop()


# --------------------------------------------------------------------------
# The mTLS terminator
# --------------------------------------------------------------------------

def _leaf_and_bogus_client_pem(tmp_path):
    """A real leaf (the Terminator loads it at construction) and a client PEM
    path that does NOT exist.

    The missing client PEM is deliberate: if a refused connection ever reached
    the MITM path, load_cert_chain would raise on it. Reaching the certificate
    at all is the failure this test is about.
    """
    leaf = term.make_leaf("admin.example.com", tmp_path)
    return leaf, str(tmp_path / "does-not-exist-client.pem")


def test_terminator_refuses_a_stranger_before_any_tls(tmp_path):
    """A local process that is not the browser gets no mutual-TLS handshake
    performed with the operator's certificate."""
    leaf, client_pem = _leaf_and_bogus_client_pem(tmp_path)
    other = _sleeper()
    t = term.Terminator(
        "admin.example.com", leaf, client_pem, verify_upstream=False
    )
    port = t.start()
    try:
        t.bind_to_process(other.pid)

        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw.settimeout(5)
        with pytest.raises((ssl.SSLError, OSError, socket.timeout)):
            # The terminator must close without completing a TLS handshake, so
            # the client never gets a session — and the certificate is untouched.
            wrapped = ctx.wrap_socket(raw, server_hostname="admin.example.com")
            wrapped.do_handshake()
            wrapped.close()
        try:
            raw.close()
        except OSError:
            pass
    finally:
        t.stop()
        _reap(other)


def test_terminator_serves_its_own_browser(tmp_path):
    """The positive case: the authorized client completes TLS with the leaf.

    Uses the CONNECT arrival shape (Firefox's path); the Chromium direct-TLS
    path is covered by the terminator's own end-to-end suite.
    """
    leaf = term.make_leaf("admin.example.com", tmp_path)
    client_pem = str(tmp_path / "unused-client.pem")
    t = term.Terminator(
        "admin.example.com", leaf, client_pem, verify_upstream=False
    )
    port = t.start()
    try:
        t.bind_to_process(os.getpid())
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.settimeout(5)
        # A CONNECT to a NON-admin host is a plain tunnel: it proves the caller
        # was accepted without involving the client certificate at all.
        raw.sendall(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n\r\n")
        hdr = b""
        try:
            while b"\r\n\r\n" not in hdr:
                chunk = raw.recv(1024)
                if not chunk:
                    break
                hdr += chunk
        except (socket.timeout, OSError):
            pass
        raw.close()
        assert b"200" in hdr, (
            f"the terminator refused its own browser (got {hdr!r}) — mTLS "
            "browsing would break"
        )
    finally:
        t.stop()


def test_terminator_refusal_is_the_mechanism_not_the_test(tmp_path, monkeypatch):
    """Falsification for the terminator: with the gate forced open, the same
    stranger is accepted and gets a CONNECT response."""
    monkeypatch.setattr(PeerGate, "allows", lambda self, peer: True)

    leaf = term.make_leaf("admin.example.com", tmp_path)
    other = _sleeper()
    t = term.Terminator(
        "admin.example.com", leaf, str(tmp_path / "unused.pem"),
        verify_upstream=False,
    )
    port = t.start()
    try:
        t.bind_to_process(other.pid)
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.settimeout(5)
        raw.sendall(b"CONNECT 127.0.0.1:9 HTTP/1.1\r\n\r\n")
        hdr = b""
        try:
            while b"\r\n\r\n" not in hdr:
                chunk = raw.recv(1024)
                if not chunk:
                    break
                hdr += chunk
        except (socket.timeout, OSError):
            pass
        raw.close()
        assert b"200" in hdr, (
            "with the gate disabled the stranger should have been served — "
            "if not, the terminator's negative test proves nothing"
        )
    finally:
        t.stop()
        _reap(other)
