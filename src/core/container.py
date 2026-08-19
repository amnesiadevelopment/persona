import logging
import threading

from ..interfaces.protocols import IBrowserLauncher, IProfileManager, IProxyService
from .config import LOG_DIR, LOG_LEVEL
from .events import EventBus
from .logging import setup_logging


class Container:
    def __init__(self) -> None:
        setup_logging(LOG_DIR, getattr(logging, LOG_LEVEL, logging.INFO))
        self._instances: dict = {}
        # _build_api_server runs on a background thread while the UI thread also
        # touches the container; without this lock two threads could each pass
        # the "key not in _instances" check and build a service twice (two
        # ProfileManagers/stores → divergent in-memory state, double file handles).
        # RLock, not Lock: a factory may resolve another service from the same
        # container (every trashable store asks for the shared trash_store, and
        # two of them for the profile_manager), which re-enters _get on THIS
        # thread. A plain Lock deadlocked the first such build outright. Two
        # distinct threads are still serialized exactly as before.
        self._lock = threading.RLock()

    def _get(self, key: str, factory):
        # double-checked: fast path without the lock once built, lock only to build.
        if key not in self._instances:
            with self._lock:
                if key not in self._instances:
                    self._instances[key] = factory()
        return self._instances[key]

    @property
    def event_bus(self) -> EventBus:
        return self._get("eb", EventBus)

    @property
    def profile_manager(self) -> IProfileManager:
        def build():
            from ..services.profile.manager import ProfileManager
            pm = ProfileManager()
            pm.set_trash(self.trash_store)
            return pm
        return self._get("pm", build)

    @property
    def browser_launcher(self) -> IBrowserLauncher:
        def build():
            from ..services.browser.launcher import BrowserLauncher
            return BrowserLauncher()
        return self._get("bl", build)

    @property
    def proxy_service(self) -> IProxyService:
        def build():
            from ..services.proxy.service import ProxyService
            return ProxyService()
        return self._get("ps", build)

    @property
    def proxy_store(self):
        def build():
            from ..services.proxy.store import ProxyStore
            store = ProxyStore()
            store.set_trash(self.trash_store)
            store.set_profile_manager(self.profile_manager)
            return store
        return self._get("pstore", build)

    @property
    def ssh_host_store(self):
        def build():
            from ..services.ssh.store import SSHHostStore
            store = SSHHostStore()
            store.set_trash(self.trash_store)
            return store
        return self._get("sshstore", build)

    @property
    def bookmark_store(self):
        def build():
            from ..services.bookmark.store import BookmarkStore
            store = BookmarkStore()
            store.set_trash(self.trash_store)
            store.set_profile_manager(self.profile_manager)
            return store
        return self._get("bstore", build)

    @property
    def cert_store(self):
        def build():
            from ..services.cert.store import CertStore
            store = CertStore()
            store.set_trash(self.trash_store)
            return store
        return self._get("cstore", build)

    @property
    def trash_store(self):
        """The ONE trash every store and every lane files into. Built without
        touching any other service so the stores above can depend on it without
        a cycle."""
        def build():
            from ..services.trash.store import TrashStore
            return TrashStore()
        return self._get("trash", build)

    @property
    def trash_service(self):
        """Restore / permanent-delete across every record kind. Both lanes — the
        REST endpoints and the UI trash page — go through this one object, so a
        restore through the API and a restore through the window cannot drift
        apart."""
        def build():
            from ..services.trash.service import TrashService
            return TrashService(
                self.trash_store,
                profile_manager=self.profile_manager,
                bookmark_store=self.bookmark_store,
                proxy_store=self.proxy_store,
                ssh_host_store=self.ssh_host_store,
                cert_store=self.cert_store,
            )
        return self._get("trashsvc", build)
