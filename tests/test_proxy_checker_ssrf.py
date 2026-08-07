"""check_proxy() connects to whatever host the pasted proxy names — it must
refuse loopback / private / link-local / cloud-metadata targets so it can't be
used as a local-network port-scan oracle."""
import pytest

from src.utils.proxy_checker import _is_blocked_proxy_host


@pytest.mark.parametrize("server,blocked", [
    ("socks5://127.0.0.1:1080", True),
    ("socks5://localhost:1080", True),
    ("http://10.0.0.5:3128", True),
    ("http://192.168.1.1:8080", True),
    ("http://172.16.0.1:8080", True),
    ("http://169.254.169.254:80", True),      # cloud metadata
    ("http://[::1]:8080", True),               # ipv6 loopback
    ("socks5://gate.decodo.com:10000", False), # a real public proxy
    ("http://1.1.1.1:8080", False),            # public IP
])
def test_is_blocked_proxy_host(server, blocked):
    assert _is_blocked_proxy_host(server) is blocked
