import json
import pathlib
import threading
import time
from collections.abc import Callable

from ...core.config import PROXIES_FILE
from ...core.logging import get_logger
from ...models.proxy import Proxy
from ...utils.atomic import atomic_write_json
from ...utils.proxy_parser import parse_proxy_server
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin

logger = get_logger("proxy.store")


class ProxyStore(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "proxies"
    _guard_noun_singular = "proxy"

    def __init__(
        self,
        path: str = PROXIES_FILE,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._now = now
        self.proxies: dict[str, Proxy] = {}
        self._save_blocked = False
        # The container-shared store is mutated from the UI thread, the uvicorn
        # API thread, and proxy-check daemon threads. Serialize every read/write
        # so a mutation can't race a _save iterating self.proxies (RLock so a
        # mutator can call _save while holding it).
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not pathlib.Path(self._path).exists():
            return
        try:
            with pathlib.Path(self._path).open(encoding="utf-8") as f:
                data = json.load(f)
            skipped = 0
            for name, p in data.items():
                # One malformed record must not abort the whole load — the next
                # save would overwrite proxies.json with only what parsed,
                # discarding every proxy's SOCKS5 creds after it.
                try:
                    self.proxies[name] = Proxy(
                        name=p["name"],
                        url=p["url"],
                        rotate_url=p.get("rotate_url", ""),
                        country_code=p.get("country_code", ""),
                        country_name=p.get("country_name", ""),
                        last_ip=p.get("last_ip", ""),
                        timezone=p.get("timezone", ""),
                        lat=p.get("lat"),
                        lon=p.get("lon"),
                        checked_at=p.get("checked_at", 0.0),
                        last_check_ok=p.get("last_check_ok"),
                    )
                except Exception:
                    skipped += 1
                    logger.exception("Skipping malformed proxy %r", name)
            if skipped:
                logger.warning("Skipped %d malformed proxy record(s)", skipped)
            logger.info("Loaded %d proxies", len(self.proxies))
        except Exception as e:
            logger.exception("Error loading proxies: %s", e)
            self._quarantine_proxies_file()

    def _store_path(self) -> str:
        return self._path

    def _quarantine_proxies_file(self) -> None:
        # An unreadable proxies.json still holds every proxy the user saved with
        # its SOCKS5 creds; move it aside so the next _save() can't overwrite it
        # with the empty in-memory dict.
        self._quarantine_store_file()

    def _save(self) -> None:
        if self._save_is_blocked():
            return
        try:
            # Proxy URLs carry SOCKS5 user:pass, so the file is written 0600 and
            # atomically (a crash mid-save must not corrupt every saved proxy).
            atomic_write_json(
                self._path,
                {name: p.to_dict() for name, p in self.proxies.items()},
                private=True,
            )
        except Exception as e:
            logger.exception("Error saving proxies: %s", e)

    def list_proxies(self) -> list[Proxy]:
        with self._lock:
            return list(self.proxies.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self.proxies.keys())

    def get(self, name: str) -> Proxy | None:
        with self._lock:
            return self.proxies.get(name)

    def url_for(self, name: str | None) -> str | None:
        if not name:
            return None
        with self._lock:
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
        with self._lock:
            proxy = self.proxies.get(ref)
            if proxy:
                # Gate a NAMED proxy on parseability too, not just the raw-url
                # fallback below. A stored url with no port ("socks5://1.2.3.4")
                # or a bare host is truthy but unusable: chromium's parser returns
                # None for it, so --proxy-server is dropped AND the anti-leak block
                # is skipped → DIRECT clearnet on a profile WITH a proxy. Returning
                # None here makes the launch guard fail CLOSED (audit7 #1).
                return proxy.url if parse_proxy_server(proxy.url) else None
        return ref if parse_proxy_server(ref) else None

    def add(self, name: str, url: str, rotate_url: str = "") -> bool:
        with self._lock:
            if not name or name in self.proxies:
                return False
            self.proxies[name] = Proxy(name=name, url=url, rotate_url=rotate_url)
            self._save()
        logger.info("Added proxy: %s", name)
        return True

    def update(
        self,
        original_name: str,
        new_name: str,
        new_url: str,
        new_rotate_url: str = "",
    ) -> bool:
        with self._lock:
            if original_name not in self.proxies:
                return False
            if new_name != original_name and new_name in self.proxies:
                return False
            old = self.proxies.pop(original_name)
            keep_geo = new_url == old.url
            self.proxies[new_name] = Proxy(
                name=new_name,
                url=new_url,
                rotate_url=new_rotate_url,
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

    def set_url(self, name: str, url: str) -> bool:
        """Change a proxy's URL in place, keeping geo and rotate settings.

        Used after session-token rotation, where the follow-up check refreshes
        the geo fields anyway.
        """
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            proxy.url = url
            self._save()
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
        with self._lock:
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
        with self._lock:
            proxy = self.proxies.get(name)
            if proxy is None:
                return False
            proxy.checked_at = self._now()
            proxy.last_check_ok = False
            self._save()
        return True

    def delete(self, name: str) -> bool:
        """Move a proxy to the trash. Its credentials go with it — trash.json is
        written 0600 inside PERSONA_HOME, exactly like proxies.json — so trashing
        a proxy does NOT remove its secret material from disk. Permanent deletion
        does; the interface says so rather than implying otherwise."""
        with self._lock:
            if name not in self.proxies:
                return False
            proxy = self.proxies.pop(name)
            self._save()
        # Which profiles used it, so restore can re-point them. The caller drops
        # the dangling reference from every profile (a lingering proxy name
        # stranded the profile page), so it's only recoverable if recorded here.
        refs = []
        pm = self._profile_manager
        if pm is not None:
            refs = [p.name for p in pm.list_profiles() if p.proxy == name]
        self._trash().add(
            "proxy", name, {"proxy": proxy.to_dict(), "profiles": refs}
        )
        logger.info("Moved proxy to trash: %s", name)
        return True

    def restore_proxy(self, entry) -> tuple[bool, str]:
        """Put a trashed proxy back with its credentials and geo, re-pointing
        the profiles that used it."""
        name = entry.name
        with self._lock:
            if name in self.proxies:
                return False, (
                    f"A proxy named '{name}' already exists. Rename or delete "
                    "it, then restore again."
                )
            d = entry.payload.get("proxy") or {}
            self.proxies[name] = Proxy(
                name=d.get("name", name),
                url=d.get("url", ""),
                rotate_url=d.get("rotate_url", ""),
                country_code=d.get("country_code", ""),
                country_name=d.get("country_name", ""),
                last_ip=d.get("last_ip", ""),
                timezone=d.get("timezone", ""),
                lat=d.get("lat"),
                lon=d.get("lon"),
                checked_at=d.get("checked_at", 0.0),
                last_check_ok=d.get("last_check_ok"),
            )
            self._save()
        pm = self._profile_manager
        if pm is not None:
            for profile_name in entry.payload.get("profiles") or []:
                profile = pm.profiles.get(profile_name)
                # Only re-point a profile that is still unassigned: the operator
                # may have picked another proxy in the meantime, and a restore
                # must not silently change a live profile's exit IP.
                if profile is not None and profile.proxy is None:
                    profile.proxy = name
            pm.save_profiles()
        logger.info("Restored proxy from trash: %s", name)
        return True, ""
