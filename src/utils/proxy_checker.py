import asyncio

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from .proxy_parser import parse_proxy


def proxy_ok_message(code: str, country: str) -> str:
    """The activity-log message for a working proxy. Shows the exit COUNTRY (and
    flag) but never the exact exit IP: this tool's own logs are disk-backed and
    UI-visible, and a timestamped IP history would de-anonymize the operator and
    link separate personas. The IP is returned separately for the store and the
    file-only debug log, never for the activity log."""
    flag = flag_from_country_code(code)
    where = f"{flag} [{code}] {country}".strip() if country else ""
    return f"Proxy working. {where}".strip() if where else "Proxy working."


def flag_from_country_code(code: str) -> str:
    """Turn a two-letter ISO country code into a flag emoji.

    Empty/invalid codes yield an empty string.
    """
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


async def check_proxy(
    proxy_str: str, timeout: int = 10
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    """Probe a proxy. Returns
    (ok, message, country_code, country_name, ip, timezone, lat, lon)."""
    if not AIOHTTP_AVAILABLE:
        return True, "Proxy check skipped (aiohttp not installed)", "", "", "", "", None, None

    proxy_config = parse_proxy(proxy_str)
    if not proxy_config:
        return False, "Invalid proxy format", "", "", "", "", None, None

    proxy_url = proxy_config["server"]
    if "username" in proxy_config:
        scheme, rest = proxy_url.split("://", 1)
        password = proxy_config.get("password", "")
        proxy_url = f"{scheme}://{proxy_config['username']}:{password}@{rest}"

    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(
                "http://ip-api.com/json/?fields=status,country,countryCode,query,timezone,lat,lon",
                proxy=proxy_url,
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    ip = data.get("query", "unknown")
                    country = data.get("country", "")
                    code = (data.get("countryCode") or "").upper()
                    tz = data.get("timezone", "")
                    lat = data.get("lat")
                    lon = data.get("lon")
                    return (
                        True,
                        proxy_ok_message(code, country),
                        code, country, ip, tz, lat, lon,
                    )
                return False, f"Proxy returned status {response.status}", "", "", "", "", None, None
    except asyncio.TimeoutError:
        return False, "Proxy connection timed out", "", "", "", "", None, None
    except aiohttp.ClientProxyConnectionError:
        return False, "Failed to connect to proxy", "", "", "", "", None, None
    except aiohttp.ClientError as e:
        return False, f"Proxy error: {e!s}", "", "", "", "", None, None
    except Exception as e:
        return False, f"Unexpected error: {e!s}", "", "", "", "", None, None


def _run(
    proxy_str: str, timeout: int
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(check_proxy(proxy_str, timeout))
        finally:
            loop.close()
    except Exception as e:
        return False, f"Error checking proxy: {e!s}", "", "", "", "", None, None


def check_proxy_sync(proxy_str: str, timeout: int = 10) -> tuple[bool, str]:
    ok, message = _run(proxy_str, timeout)[:2]
    return ok, message


def check_proxy_detailed_sync(
    proxy_str: str,
    timeout: int = 10,
) -> tuple[bool, str, str, str, str, str, float | None, float | None]:
    return _run(proxy_str, timeout)
