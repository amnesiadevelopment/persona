import asyncio

from src.api.mcp_server import build_mcp
from src.core.container import Container
from src.services.proxy.store import ProxyStore


def _call(mcp, name, args=None):
    return asyncio.run(mcp.call_tool(name, args or {}))


def _isolated_container(tmp_path):
    # Bind an isolated ProxyStore so the test never reads (or logs) the user's
    # real ~/.persona proxies — those hold live exit IPs and SOCKS creds.
    c = Container()
    c._instances["pstore"] = ProxyStore(path=str(tmp_path / "proxies.json"))
    return c


def test_list_proxies_does_not_ship_exit_ip(tmp_path):
    # #8: the proxy's last_ip is the exit IP of an anti-detect identity. Sending
    # it to the connected LLM client takes that IP off-machine. list_proxies must
    # expose only the coarse country_code, never last_ip.
    c = _isolated_container(tmp_path)
    c.proxy_store.add("home", "socks5://user:pass@1.2.3.4:1080")
    c.proxy_store.mark_checked("home", "PL", "Poland", "156.243.150.219", "Europe/Warsaw")
    mcp = build_mcp(c)
    result = _call(mcp, "list_proxies")
    blob = repr(result)
    assert "156.243.150.219" not in blob, "exit IP must not leave the machine"
    assert "PL" in blob, "country_code is fine to expose"
    assert "last_ip" not in blob


def test_proxy_bridge_log_omits_upstream_hostname(caplog, monkeypatch):
    # #9: the authenticated-proxy bridge log must not carry the upstream proxy
    # hostname — it identifies the provider and often embeds session/geo labels,
    # and it would persist one line per launch in the log + Activity Log.
    import logging

    from src.services.browser import process

    class _FakeBridge:
        def __init__(self, url):
            self._url = url

        def start(self):
            return 51999

    monkeypatch.setattr(process, "ProxyBridge", _FakeBridge)
    with caplog.at_level(logging.INFO):
        server, bridge = process._proxy_arg(
            "socks5://user:pass@gate.decodo-secret-provider.com:7000"
        )
    assert server == "socks5://127.0.0.1:51999"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "decodo-secret-provider.com" not in joined
    assert "51999" in joined  # the local bridge port is still logged
