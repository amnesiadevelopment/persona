import socket

from src.services.proxy.bridge import ProxyBridge


def test_bridge_starts_and_listens():
    bridge = ProxyBridge("socks5://user:pass@1.2.3.4:1080")
    port = bridge.start()
    try:
        assert port > 0
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
    finally:
        bridge.stop()


def test_bridge_parses_upstream():
    bridge = ProxyBridge("socks5://alice:secret@9.9.9.9:1080")
    assert bridge._up_host == "9.9.9.9"
    assert bridge._up_port == 1080
    assert bridge._up_user == "alice"
    assert bridge._up_pass == "secret"


def test_bridge_parses_without_scheme():
    bridge = ProxyBridge("u:p@host.example:1080")
    assert bridge._up_host == "host.example"
    assert bridge._up_port == 1080
