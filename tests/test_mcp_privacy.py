import asyncio

from src.api.mcp_server import build_mcp
from src.core.container import Container
from src.services.proxy.store import ProxyStore
from src.services.ssh.store import SSHHost, SSHHostStore


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


def test_list_ssh_hosts_omits_host_user_port(tmp_path, monkeypatch):
    # #11: host+port+user together fingerprint and locate the operator's infra +
    # login. The off-machine LLM client should get only name + profile;
    # ssh_exec resolves the real host server-side by name.
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    c = Container()
    store = c.ssh_host_store
    store.add(SSHHost(
        name="box", host="10.20.30.40", port=2222,
        username="rootadmin", profile="P1",
    ))
    mcp = build_mcp(c)
    result = _call(mcp, "list_ssh_hosts")
    blob = repr(result)
    assert "10.20.30.40" not in blob
    assert "rootadmin" not in blob
    assert "2222" not in blob
    assert "box" in blob and "P1" in blob


def test_mtls_terminator_log_omits_admin_host():
    # #10: the mTLS terminator log must not carry the internal admin hostname —
    # same class as the proxy-hostname fix. Only the loopback port is logged.
    import inspect

    from src.services.cert import manager as mgr

    src = inspect.getsource(mgr)
    assert "host, port_, port)" not in src
    assert "terminator for %r on 127.0.0.1:%s" in src


def test_sftp_path_confined_to_sftp_dir(tmp_path, monkeypatch):
    # #2 (audit4 HIGH): sftp local paths from the off-machine LLM must be confined
    # to ~/.persona/sftp — absolute paths and '..' traversal are rejected so a
    # download/upload can't touch the token/creds/binary anywhere else on disk.
    import os

    import pytest

    import src.api.mcp_server as ms

    monkeypatch.setattr(ms, "_SFTP_DIR", str(tmp_path / "sftp"))

    # a plain relative name is allowed and lands inside the sftp dir
    safe = ms._confine_sftp_path("report.txt")
    assert safe.startswith(os.path.realpath(str(tmp_path / "sftp")))

    # a nested relative path is fine too
    safe2 = ms._confine_sftp_path("sub/dir/x.bin")
    assert "sub" in safe2

    # absolute paths, drive letters and '..' traversal are rejected
    for bad in ("/etc/passwd", r"C:\Windows\evil.exe",
                "../../.persona/mcp_token", "../../../profiles.json"):
        with pytest.raises(ValueError):
            ms._confine_sftp_path(bad)
