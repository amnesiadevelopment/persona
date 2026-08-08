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
    content = kh.read_text()
    assert "srv.example.com" in content
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(kh).st_mode)
        assert mode == 0o600
