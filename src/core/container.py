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
        self._lock = threading.Lock()

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
            return ProfileManager()
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
            return ProxyStore()
        return self._get("pstore", build)

    @property
    def ssh_host_store(self):
        def build():
            from ..services.ssh.store import SSHHostStore
            return SSHHostStore()
        return self._get("sshstore", build)

    @property
    def bookmark_store(self):
        def build():
            from ..services.bookmark.store import BookmarkStore
            return BookmarkStore()
        return self._get("bstore", build)

    @property
    def cert_store(self):
        def build():
            from ..services.cert.store import CertStore
            return CertStore()
        return self._get("cstore", build)
