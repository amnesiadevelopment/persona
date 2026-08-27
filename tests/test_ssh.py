import socket
import threading

import pytest

from src.services.ssh import client as C


def test_q_escapes_single_quotes():
    assert C._q("echo hi") == "'echo hi'"
    assert C._q("it's") == "'it'\\''s'"


def test_proxy_socket_none_when_no_proxy():
    t = C.SSHTarget(host="h", proxy_url="")
    assert C._proxy_socket(t, timeout=1) is None


def test_tmux_send_builds_session_safe_command(monkeypatch):
    captured = {}

    def fake_run(target, command, timeout=30.0):
        captured["cmd"] = command
        return 0, "", ""

    monkeypatch.setattr(C, "run_command", fake_run)
    C.tmux_send(C.SSHTarget(host="h"), "my sess", "echo 'x'")
    cmd = captured["cmd"]
    # session name + keys are single-quoted, no unescaped injection
    assert "tmux has-session -t 'my sess'" in cmd
    assert "new-session -d -s 'my sess'" in cmd
    assert "send-keys -t 'my sess' 'echo '\\''x'\\''' Enter" in cmd


def test_tmux_capture_uses_capture_pane(monkeypatch):
    captured = {}

    def fake_run(target, command, timeout=30.0):
        captured["cmd"] = command
        return 0, "pane text", ""

    monkeypatch.setattr(C, "run_command", fake_run)
    out = C.tmux_capture(C.SSHTarget(host="h"), "s", lines=50)
    assert out == "pane text"
    assert "capture-pane -p -t 's' -S -50" in captured["cmd"]


def test_proxy_socket_routes_through_socks():
    # Stand up a minimal SOCKS5 server that accepts the handshake and tunnels to
    # a local echo server, proving _proxy_socket actually drives PySocks.
    import struct

    # local TCP "SSH" server that sends a banner
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    ssh_port = srv.getsockname()[1]

    def ssh_server():
        c, _ = srv.accept()
        c.sendall(b"SSH-2.0-fake\r\n")
        c.close()

    threading.Thread(target=ssh_server, daemon=True).start()

    # minimal SOCKS5 proxy: no-auth, CONNECT -> dial the target, then relay
    psrv = socket.socket()
    psrv.bind(("127.0.0.1", 0))
    psrv.listen(1)
    proxy_port = psrv.getsockname()[1]

    def socks_server():
        c, _ = psrv.accept()
        c.recv(2 + 255)  # greeting
        c.sendall(b"\x05\x00")  # no-auth chosen
        req = c.recv(4)
        # read addr (assume IPv4) + port
        atyp = req[3]
        if atyp == 1:
            c.recv(4)
        elif atyp == 3:
            ln = c.recv(1)[0]
            c.recv(ln)
        c.recv(2)
        c.sendall(b"\x05\x00\x00\x01" + struct.pack("!IH", 0, 0))
        # relay to the real ssh server
        up = socket.create_connection(("127.0.0.1", ssh_port))
        data = up.recv(64)
        c.sendall(data)
        up.close()
        c.close()

    threading.Thread(target=socks_server, daemon=True).start()

    t = C.SSHTarget(
        host="127.0.0.1", port=ssh_port,
        proxy_url=f"socks5://127.0.0.1:{proxy_port}",
    )
    sock = C._proxy_socket(t, timeout=5)
    assert sock is not None
    banner = sock.recv(32)
    sock.close()
    assert banner.startswith(b"SSH-2.0")


def test_connect_never_uses_autoadd_policy():
    # #3 (audit4 HIGH): the SSH session rides an untrusted SOCKS exit (MITM
    # position). AutoAddPolicy would auto-accept a spoofed key and leak the
    # cleartext password/passphrase. The client must use the TOFU policy instead.
    import inspect

    src = inspect.getsource(C.connect)
    assert "AutoAddPolicy()" not in src  # no AutoAdd instantiation
    assert "set_missing_host_key_policy(_TOFUPolicy())" in src


def test_tofu_policy_pins_new_host_key_0600(tmp_path, monkeypatch):
    import os
    import stat

    import paramiko

    monkeypatch.setattr(C, "PERSONA_HOME", str(tmp_path))
    monkeypatch.setattr(C, "_KNOWN_HOSTS", str(tmp_path / "known_hosts"))

    client = paramiko.SSHClient()
    key = paramiko.RSAKey.generate(2048)
    C._TOFUPolicy().missing_host_key(client, "srv.example.com", key)

    kh = tmp_path / "known_hosts"
    assert kh.exists(), "a first-sight host key must be pinned"
    content = kh.read_text(encoding="utf-8")
    assert "srv.example.com" in content
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(kh).st_mode)
        assert mode == 0o600


class _FakeSSHServer:
    """A minimal real paramiko SSH server on loopback that presents a chosen
    host key, so a client's known_hosts pinning + changed-key rejection can be
    exercised end-to-end (no mocks). Serves exactly one connection then stops."""

    def __init__(self, host_key):
        import paramiko

        self._host_key = host_key
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(5)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

        class _Handler(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL

            def check_auth_none(self, username):
                return paramiko.AUTH_SUCCESSFUL

            def get_allowed_auths(self, username):
                return "password,none"

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED

        self._handler_cls = _Handler

    def _serve(self):
        import paramiko

        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            t = paramiko.Transport(conn)
            t.add_server_key(self._host_key)
            t.start_server(server=self._handler_cls())
            # keep the transport alive briefly so the handshake completes
            t.join(timeout=3)
        except Exception:
            pass

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def test_connect_rejects_changed_host_key_mitm(tmp_path, monkeypatch):
    # audit6 #4 (Mars's explicit concern): the MITM-detection half of the TOFU
    # fix — connect() loading known_hosts so paramiko RAISES on a changed key —
    # had no behavioral test. Pin server key A, then serve key B on the same
    # host:port and assert connect() raises BadHostKeyException. Deleting the
    # load_host_keys call in connect() makes this fail (the changed key would be
    # silently re-pinned).
    import paramiko

    monkeypatch.setattr(C, "PERSONA_HOME", str(tmp_path))
    monkeypatch.setattr(C, "_KNOWN_HOSTS", str(tmp_path / "known_hosts"))

    key_a = paramiko.RSAKey.generate(2048)
    key_b = paramiko.ECDSAKey.generate()

    # First connection: server presents key A. TOFU pins it (first sight).
    srv_a = _FakeSSHServer(key_a)
    target_a = C.SSHTarget(
        host="127.0.0.1", port=srv_a.port, username="u", password="p", proxy_url=""
    )
    try:
        client = C.connect(target_a, timeout=5)
        client.close()
    finally:
        srv_a.close()

    assert (tmp_path / "known_hosts").exists(), "key A should have been pinned"

    # Second connection to the SAME host:port but the server now presents key B
    # (a MITM swapping the host key). paramiko must reject it.
    srv_b = _FakeSSHServer(key_b)
    target_b = C.SSHTarget(
        host="127.0.0.1", port=srv_b.port, username="u", password="p", proxy_url=""
    )
    # NOTE: known_hosts pins by host:port; use the same port so the pin applies.
    # Re-pin key A under srv_b's port first so the change is on the SAME key entry.
    import os

    # Pin key A to srv_b.port so the second connect sees a *changed* key there.
    hostkeys = paramiko.HostKeys()
    if os.path.exists(str(tmp_path / "known_hosts")):
        hostkeys.load(str(tmp_path / "known_hosts"))
    entry = f"[127.0.0.1]:{srv_b.port}"
    hostkeys.add(entry, key_a.get_name(), key_a)
    hostkeys.save(str(tmp_path / "known_hosts"))

    try:
        with pytest.raises(paramiko.BadHostKeyException):
            C.connect(target_b, timeout=5)
    finally:
        srv_b.close()
