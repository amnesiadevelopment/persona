import json
import pathlib
import time
from collections.abc import Callable

from ...core.config import PROXIES_FILE
from ...core.logging import get_logger
from ...models.proxy import Proxy
from ...utils.proxy_parser import parse_proxy_server

logger = get_logger("proxy.store")


class ProxyStore:
    def __init__(
        self,
        path: str = PROXIES_FILE,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._now = now
        self.proxies: dict[str, Proxy] = {}
        self._load()

    def _load(self) -> None:
        if not pathlib.Path(self._path).exists():
            return
        try:
            with pathlib.Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
            for name, p in data.items():
                self.proxies[name] = Proxy(
                    name=p["name"],
                    url=p["url"],
                    country_code=p.get("country_code", ""),
                    country_name=p.get("country_name", ""),
                    last_ip=p.get("last_ip", ""),
                    timezone=p.get("timezone", ""),
                    lat=p.get("lat"),
                    lon=p.get("lon"),
                    checked_at=p.get("checked_at", 0.0),
                    last_check_ok=p.get("last_check_ok"),
                )
            logger.info("Loaded %d proxies", len(self.proxies))
        except Exception as e:
            logger.exception("Error loading proxies: %s", e)

    def _save(self) -> None:
        try:
            with pathlib.Path(self._path).open("w", encoding="utf-8") as f:
                json.dump(
                    {name: p.to_dict() for name, p in self.proxies.items()},
                    f,
                    indent=4,
                )
        except Exception as e:
            logger.exception("Error saving proxies: %s", e)

    def list_proxies(self) -> list[Proxy]:
        return list(self.proxies.values())

    def names(self) -> list[str]:
        return list(self.proxies.keys())

    def get(self, name: str) -> Proxy | None:
        return self.proxies.get(name)

    def url_for(self, name: str | None) -> str | None:
        if not name:
            return None
        proxy = self.proxies.get(name)
        return proxy.url if proxy else None

    def resolve(self, ref: str | None) -> str | None:
        """Resolve a profile's proxy reference to a usable proxy URL.

        ``ref`` is a stored proxy name. Falls back to treating ``ref`` as a
        raw proxy URL when no stored proxy matches, so profiles created before
        named proxies existed still launch.
        """
        if not ref:
            return None
        proxy = self.proxies.get(ref)
        if proxy:
            return proxy.url
        return ref if parse_proxy_server(ref) else None

    def add(self, name: str, url: str) -> bool:
        if not name or name in self.proxies:
            return False
        self.proxies[name] = Proxy(name=name, url=url)
        self._save()
        logger.info("Added proxy: %s", name)
        return True

    def update(self, original_name: str, new_name: str, new_url: str) -> bool:
        if original_name not in self.proxies:
            return False
        if new_name != original_name and new_name in self.proxies:
            return False
        old = self.proxies.pop(original_name)
        keep_geo = new_url == old.url
        self.proxies[new_name] = Proxy(
            name=new_name,
            url=new_url,
            country_code=old.country_code if keep_geo else "",
            country_name=old.country_name if keep_geo else "",
            last_ip=old.last_ip if keep_geo else "",
            timezone=old.timezone if keep_geo else "",
            lat=old.lat if keep_geo else None,
            lon=old.lon if keep_geo else None,
            checked_at=old.checked_at if keep_geo else 0.0,
            last_check_ok=old.last_check_ok if keep_geo else None,
        )
        self._save()
        logger.info("Updated proxy: %s -> %s", original_name, new_name)
        return True

    def mark_checked(
        self,
        name: str,
        country_code: str,
        country_name: str,
        ip: str = "",
        timezone: str = "",
        lat: float | None = None,
        lon: float | None = None,
    ) -> bool:
        proxy = self.proxies.get(name)
        if proxy is None:
            return False
        proxy.country_code = country_code
        proxy.country_name = country_name
        proxy.last_ip = ip
        proxy.timezone = timezone
        proxy.lat = lat
        proxy.lon = lon
        proxy.checked_at = self._now()
        proxy.last_check_ok = True
        self._save()
        return True

    def mark_check_failed(self, name: str) -> bool:
        proxy = self.proxies.get(name)
        if proxy is None:
            return False
        proxy.checked_at = self._now()
        proxy.last_check_ok = False
        self._save()
        return True

    def delete(self, name: str) -> bool:
        if name not in self.proxies:
            return False
        del self.proxies[name]
        self._save()
        logger.info("Deleted proxy: %s", name)
        return True
