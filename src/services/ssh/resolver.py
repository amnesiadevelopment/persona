"""Turn a saved SSHHost into a connectable SSHTarget, resolving the profile's
proxy URL so the SSH session routes through the same exit IP as the profile."""

from __future__ import annotations

from ..proxy.errors import ProxyUnresolvedError
from ..proxy.store import ProxyStore
from .client import SSHTarget
from .store import SSHHost


def resolve_proxy_url(profile_name: str, pm, proxy_store: ProxyStore) -> str:
    """The proxy URL for a profile: profile -> its proxy ref -> proxy url.

    Fails CLOSED like the browser launch: when a proxy is ASSIGNED but cannot be
    resolved to a usable URL (deleted/renamed proxy, or a stored value that lost
    its scheme/port), raise instead of returning "" — an empty string means
    "connect directly", and a direct SSH session would authenticate (username +
    password + key passphrase) from the operator's REAL IP, the same
    de-anonymization the browser path is guarded against. An empty ref (no proxy
    assigned) stays "" — a genuine direct connection.
    """
    if not profile_name:
        return ""
    profile = pm.profiles.get(profile_name) if pm else None
    ref = profile_name if profile is None else profile.proxy
    if not ref:
        # No proxy assigned (unknown profile with an empty field, or a profile
        # whose proxy is unset) — a direct connection is intended.
        return ""
    resolved = proxy_store.resolve(ref)
    if not resolved:
        raise ProxyUnresolvedError(
            f"SSH host bound to proxy {ref!r} which could not be resolved "
            "(deleted/renamed, or missing scheme/port). Refusing to connect "
            "DIRECT from the real IP."
        )
    return resolved


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
