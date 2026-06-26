"""Turn a saved SSHHost into a connectable SSHTarget, resolving the profile's
proxy URL so the SSH session routes through the same exit IP as the profile."""

from __future__ import annotations

from ..proxy.store import ProxyStore
from .client import SSHTarget
from .store import SSHHost


def resolve_proxy_url(profile_name: str, pm, proxy_store: ProxyStore) -> str:
    """The proxy URL for a profile: profile -> its proxy ref -> proxy url."""
    if not profile_name:
        return ""
    profile = pm.profiles.get(profile_name) if pm else None
    if profile is None:
        # fall back to treating the field as a direct proxy ref/url
        return proxy_store.resolve(profile_name) or ""
    return proxy_store.resolve(profile.proxy) or ""


def target_for(host: SSHHost, pm=None, proxy_store: ProxyStore | None = None) -> SSHTarget:
    store = proxy_store or ProxyStore()
    proxy_url = resolve_proxy_url(host.profile, pm, store) if host.profile else ""
    return SSHTarget(
        host=host.host,
        port=host.port,
        username=host.username,
        password=host.password,
        key_path=host.key_path,
        key_passphrase=host.key_passphrase,
        proxy_url=proxy_url,
    )
