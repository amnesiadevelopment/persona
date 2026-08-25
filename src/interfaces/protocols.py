from collections.abc import Callable
from typing import Protocol

from ..models.profile import Profile
from ..services.browser.refusal import Refusal
from ..services.profile.pool_assignment import POOL_UNCHANGED, PoolDirective
from ..services.profile.proxy_assignment import PROXY_UNCHANGED, ProxyDirective


class IProfileManager(Protocol):
    profiles: dict[str, Profile]

    def add_profile(
        self,
        name: str,
        proxy: str | ProxyDirective | None,
        os_type: str,
    ) -> bool: ...

    def update_profile(
        self,
        original_name: str,
        new_name: str,
        # A proxy NAME, or PROXY_UNCHANGED / PROXY_NONE. Defaulted to
        # PROXY_UNCHANGED so a caller that says nothing about the proxy changes
        # nothing — see services/profile/proxy_assignment.py.
        new_proxy: str | ProxyDirective | None = PROXY_UNCHANGED,
        new_os: str | None = None,
        # Declared so this protocol's POSITIONS match the concrete
        # ProfileManager's. Callers do call positionally (ui/actions/profile.py
        # passes new_search 5th and new_pool 6th through an IProfileManager-typed
        # reference), so omitting it here would have made position 5 read as
        # new_bookmark_pool and position 6 as nothing that exists — a claim
        # nothing would catch, since mypy is in neither requirements-dev.txt nor
        # CI.
        new_search_engine: str | None = None,
        # A pool NAME, or POOL_UNCHANGED / POOL_NONE. Defaulted to
        # POOL_UNCHANGED for the same reason — see
        # services/profile/pool_assignment.py.
        new_bookmark_pool: str | PoolDirective | None = POOL_UNCHANGED,
    ) -> bool: ...

    def set_cookie_status(self, name: str, status: str) -> bool: ...

    def set_cookie_status(self, name: str, status: str) -> bool: ...

    def set_cert_trust_status(self, name: str, status: str) -> bool: ...

    def set_last_launch_build(
        self, name: str, engine: str, build: str | None
    ) -> bool: ...

    def delete_profile(self, name: str) -> bool: ...

    def list_profiles(self) -> list[Profile]: ...

    def export_profile(
        self,
        name: str,
        export_path: str,
        include_data: bool = True,
    ) -> tuple[bool, str]: ...

    def import_profile(
        self,
        zip_path: str,
        overwrite: bool = False,
    ) -> tuple[bool, str]: ...


class IBrowserLauncher(Protocol):
    def start_thread(
        self,
        profile: Profile,
        log_callback: Callable[[str], None],
        on_start: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        *,
        on_cert_trust: Callable[[str], None] | None = None,
    ) -> None: ...

    def stop_profile(self, profile_name: str, timeout: int = 2) -> bool: ...

    def running_profile_names(self) -> set[str]: ...

    def running_count(self) -> int: ...

    def is_running(self, profile_name: str) -> bool: ...

    def started_at(self, profile_name: str) -> float | None: ...

    def cdp_channel_open(self, profile_name: str) -> bool: ...

    def last_refusal(self, profile_name: str) -> "Refusal | None": ...

    def forget_refusal(self, profile_name: str) -> None: ...


class IProxyService(Protocol):
    def check_proxy_sync(
        self,
        proxy_str: str,
        timeout: int = 10,
    ) -> tuple[bool, str]: ...

    def check_proxy_detailed_sync(
        self,
        proxy_str: str,
        timeout: int | None = None,
    ) -> tuple[bool, str, str, str, str, str, float | None, float | None]: ...

    def rotate_proxy(
        self,
        proxy_url: str,
        rotate_url: str = "",
        timeout: int | None = None,
    ) -> tuple[str, str]: ...
