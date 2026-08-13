"""audit-8 #1/#2: the SSH/SFTP/tmux path must fail CLOSED when a host is bound
to a profile whose proxy is assigned-but-unresolvable — never connect DIRECT
from the operator's real IP (which would authenticate with the SSH password +
key passphrase in the clear of the intended tunnel)."""

from __future__ import annotations

import pytest

from src.services.proxy.errors import ProxyUnresolvedError
from src.services.proxy.store import ProxyStore
from src.services.ssh import resolver as R
from src.services.ssh.store import SSHHost


class _PM:
    """Minimal profile manager stand-in: .profiles is a name->profile dict."""

    def __init__(self, profiles):
        self.profiles = profiles


class _Profile:
    def __init__(self, name, proxy):
        self.name = name
        self.proxy = proxy


def _store(tmp_path, proxies):
    s = ProxyStore(path=str(tmp_path / "proxies.json"))
    s.proxies = dict(proxies)
    return s


def test_no_profile_field_connects_direct(tmp_path):
    # A host with no profile bound → direct is intended, no raise.
    s = _store(tmp_path, {})
    assert R.resolve_proxy_url("", _PM({}), s) == ""


def test_assigned_but_deleted_proxy_raises(tmp_path):
    # Profile references a proxy name that no longer exists in the store.
    from src.models.proxy import Proxy  # noqa: F401 (kept for parity)

    pm = _PM({"acme": _Profile("acme", "GonePL")})
    s = _store(tmp_path, {})  # "GonePL" is not present
    with pytest.raises(ProxyUnresolvedError):
        R.resolve_proxy_url("acme", pm, s)


def test_assigned_but_unparseable_url_raises(tmp_path):
    # Stored proxy exists but its url has no port → resolve() returns None.
    from src.models.proxy import Proxy

    pm = _PM({"acme": _Profile("acme", "BadPL")})
    s = _store(tmp_path, {"BadPL": Proxy(name="BadPL", url="socks5://1.2.3.4")})
    with pytest.raises(ProxyUnresolvedError):
        R.resolve_proxy_url("acme", pm, s)


def test_assigned_and_resolvable_returns_url(tmp_path):
    from src.models.proxy import Proxy

    pm = _PM({"acme": _Profile("acme", "GoodPL")})
    s = _store(
        tmp_path,
        {"GoodPL": Proxy(name="GoodPL", url="socks5://user:pass@1.2.3.4:1080")},
    )
    assert R.resolve_proxy_url("acme", pm, s) == "socks5://user:pass@1.2.3.4:1080"


def test_profile_with_no_proxy_connects_direct(tmp_path):
    # Profile exists but has no proxy assigned → direct is intended.
    pm = _PM({"acme": _Profile("acme", "")})
    s = _store(tmp_path, {})
    assert R.resolve_proxy_url("acme", pm, s) == ""


def test_target_for_raises_for_deleted_proxy(tmp_path):
    # End-to-end through target_for (the UI/MCP entry point).
    pm = _PM({"acme": _Profile("acme", "GonePL")})
    s = _store(tmp_path, {})
    host = SSHHost(name="h", host="1.2.3.4", profile="acme")
    with pytest.raises(ProxyUnresolvedError):
        R.target_for(host, pm, s)
