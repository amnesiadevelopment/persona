import urllib.parse

from ...core.config import PROXY_CHECK_TIMEOUT
from ...utils.proxy_checker import check_proxy_detailed_sync as _check_detailed
from ...utils.proxy_checker import check_proxy_sync as _check_sync
from ...utils.proxy_checker import fetch_status_via_proxy_sync as _fetch_via_proxy
from ...utils.proxy_rotation import regenerate_session_token


def _fetch_rotate_url(rotate_url: str, proxy_url: str, timeout: int) -> tuple[bool, str]:
    """Ask the provider to rotate — THROUGH the proxy being rotated, or not at all.

    `proxy_url` has NO DEFAULT on purpose. This used to be a bare stdlib HTTP
    fetch made directly from the operator's REAL IP with `User-Agent: persona`,
    which disclosed three things at once: that this real address controls that
    proxy account (timestamped, to the provider and to anyone watching the
    operator's traffic), a DNS query from the operator's real resolver for the
    provider's hostname, and a self-identifying User-Agent. A defaulted
    parameter would let a future caller silently reintroduce that direct path;
    an omitted argument is now a TypeError instead.

    With no usable transport the request is simply NOT SENT — no socket is
    opened. That fails closed cheaply: rotate_proxy already surfaces the reason
    to the activity log and the caller re-checks the proxy anyway, so the
    operator loses a rotate attempt and sees why. Nothing is corrupted.
    """
    # The rotate_url still comes from an untrusted pasted proxy string, so keep
    # the http(s)-only guard: it blocks file:// / data:// and friends. Its
    # original local-network reach is gone — the target is now resolved and
    # connected AT THE EXIT, so this path can no longer touch the operator's
    # LAN or a cloud metadata endpoint at all.
    scheme = urllib.parse.urlparse(rotate_url).scheme.lower()
    if scheme not in ("http", "https"):
        return False, "Rotate URL must be http or https"
    if not proxy_url:
        return False, "rotate request not sent: no proxy transport"
    return _fetch_via_proxy(proxy_url, rotate_url, timeout)


class ProxyService:
    def __init__(self, default_timeout: int = PROXY_CHECK_TIMEOUT) -> None:
        self._default_timeout = default_timeout

    def check_proxy_sync(
        self,
        proxy_str: str,
        timeout: int | None = None,
    ) -> tuple[bool, str]:
        return _check_sync(proxy_str, timeout or self._default_timeout)

    def check_proxy_detailed_sync(
        self,
        proxy_str: str,
        timeout: int | None = None,
    ) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
        return _check_detailed(proxy_str, timeout or self._default_timeout)

    def rotate_proxy(
        self,
        proxy_url: str,
        rotate_url: str = "",
        timeout: int | None = None,
    ) -> tuple[str, str]:
        """Ask the proxy for a new exit IP.

        Tries, in order: the provider's rotate endpoint (when configured), a
        fresh session token embedded in the credentials, and finally just a
        fresh connection (rotating/backconnect proxies hand out a new IP per
        connection). Returns (url to use for the follow-up check, note about
        what was done).
        """
        if rotate_url:
            ok, detail = _fetch_rotate_url(
                rotate_url, proxy_url, timeout or self._default_timeout
            )
            if ok:
                return proxy_url, f"rotate endpoint OK ({detail})"
            return proxy_url, f"rotate endpoint failed: {detail}"
        fresh = regenerate_session_token(proxy_url)
        if fresh is not None:
            return fresh, "regenerated session token"
        return proxy_url, "no rotate URL or session token — retrying with a fresh connection"
