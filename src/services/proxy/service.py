import urllib.parse
import urllib.request

from ...core.config import PROXY_CHECK_TIMEOUT
from ...utils.proxy_checker import check_proxy_detailed_sync as _check_detailed
from ...utils.proxy_checker import check_proxy_sync as _check_sync
from ...utils.proxy_rotation import regenerate_session_token


def _fetch_rotate_url(rotate_url: str, timeout: int) -> tuple[bool, str]:
    # rotate_url comes from an untrusted pasted proxy string. Only fetch http(s):
    # urllib.urlopen also honours file:// / ftp:// / data:, which would let a
    # crafted rotate_url read a local file or reach an internal service.
    scheme = urllib.parse.urlparse(rotate_url).scheme.lower()
    if scheme not in ("http", "https"):
        return False, "Rotate URL must be http or https"
    try:
        req = urllib.request.Request(rotate_url, headers={"User-Agent": "persona"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status < 400:
                return True, f"HTTP {status}"
            return False, f"HTTP {status}"
    except Exception as e:
        return False, str(e)


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
                rotate_url, timeout or self._default_timeout
            )
            if ok:
                return proxy_url, f"rotate endpoint OK ({detail})"
            return proxy_url, f"rotate endpoint failed: {detail}"
        fresh = regenerate_session_token(proxy_url)
        if fresh is not None:
            return fresh, "regenerated session token"
        return proxy_url, "no rotate URL or session token — retrying with a fresh connection"
