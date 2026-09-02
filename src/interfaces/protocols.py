from collections.abc import Callable
from typing import Protocol

from ..models.profile import Profile
from ..services.browser.refusal import Refusal
from ..services.browser.session_registry import SessionRecord
from ..services.profile.cert_assignment import CertDirective
from ..services.profile.pool_assignment import POOL_UNCHANGED, PoolDirective
from ..services.profile.proxy_assignment import PROXY_UNCHANGED, ProxyDirective


class IProfileManager(Protocol):
    profiles: dict[str, Profile]

    # EVERY parameter the concrete ProfileManager accepts is declared here, in
    # the SAME ORDER, including the optional ones (PS-165). The protocol had
    # fallen ten parameters behind, and real callers holding an
    # IProfileManager-typed reference (api/mcp_server.py, ui/actions/profile.py)
    # already passed them — so those calls were type errors that nothing was
    # positioned to see.
    #
    # A protocol MAY legitimately describe a subset; this one may not, because
    # it is the contract the callers actually call through. Keep it in step with
    # services/profile/manager.py — .github/scripts/check_protocol_conformance.py
    # fails the build if it drifts again.
    def add_profile(
        self,
        name: str,
        proxy: str | ProxyDirective | None,
        os_type: str,
        search_engine: str = "duckduckgo",
        bookmark_pool: str | PoolDirective | None = None,
        bookmarks: list[str] | None = None,
        tags: list[str] | None = None,
        device_type: str = "desktop",
        notes: str = "",
        engine: str = "chromium",
        resolution: str = "auto",
        # A certificate NAME, "" to clear, None to leave alone, or
        # CERT_UNCHANGED. Widened in lockstep with the implementation — see
        # services/profile/cert_assignment.py, and PS-165 for what a protocol
        # that drifts from its implementation costs.
        certificate: str | CertDirective | None = None,
        ai_control: bool = False,
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
        # The eight below were missing until PS-165, for the same reason the
        # comment above describes and with the same consequence: callers pass
        # them by keyword through an IProfileManager-typed reference
        # (ui/actions/profile.py, api/routes/profiles.py, api/mcp_server.py),
        # and every one of those calls was an "unexpected keyword argument"
        # error that nothing ran to notice.
        new_bookmarks: list[str] | None = None,
        new_tags: list[str] | None = None,
        new_ai_control: bool | None = None,
        new_device_type: str | None = None,
        new_notes: str | None = None,
        new_engine: str | None = None,
        new_resolution: str | None = None,
        # A certificate NAME, "" to CLEAR, None to leave alone, or
        # CERT_UNCHANGED (what the dialog sends when it cannot account for the
        # stored assignment). "" still clears here, unlike the two fields
        # above — see services/profile/cert_assignment.py for why the asymmetry
        # is deliberate.
        new_certificate: str | CertDirective | None = None,
    ) -> bool: ...

    # Declared ONCE. This was written twice until PS-165 — harmless at runtime,
    # but on a Protocol a duplicate creates a phantom member, so conformance
    # failures elsewhere surfaced under the baffling name
    # "set_cookie_status-redefinition".
    def set_cookie_status(self, name: str, status: str) -> bool: ...

    def set_cert_trust_status(self, name: str, status: str | None) -> bool: ...

    def set_last_launch_build(
        self, name: str, engine: str, build: str | None
    ) -> bool: ...

    # The block below is the OTHER half of the PS-165 drift. These eight are
    # called through IProfileManager-typed references (ui/app.py in seven
    # places, api/mcp_server.py in one) and were declared only on the concrete
    # class — so each call read as "IProfileManager has no attribute ...".
    # Same root cause as the missing parameters above: the protocol stopped
    # describing the implementation while the callers went on using it.
    def assign_tag(self, names: list[str], tag: str) -> int: ...

    def remove_tag(self, tag: str) -> int: ...

    def rename_bookmark_pool(self, old_name: str, new_name: str) -> int: ...

    def set_ai_control(self, name: str, enabled: bool) -> bool: ...

    def set_notes(self, name: str, notes: str) -> bool: ...

    def set_stop_hook(self, hook: Callable[[str], object] | None) -> None: ...

    def set_forget_identity_hook(
        self, hook: Callable[[str], None] | None
    ) -> None: ...

    def wipe_all_profiles(self) -> int: ...

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
        # `str | None` rather than `str`: the launcher calls this with None at
        # the START of an attempt to invalidate the previous session's verdict,
        # and with the status when the engine announces one. The drop and the
        # record ride ONE callback on purpose — a lane cannot record a verdict
        # without also dropping the stale one it supersedes (launcher.py).
        on_cert_trust: "Callable[[str | None], None] | None" = None,
    ) -> None: ...

    def stop_profile(self, profile_name: str, timeout: int = 2) -> bool: ...

    # Declared because it is CALLED THROUGH THIS PROTOCOL: ui/app.py holds
    # `self.bl: IBrowserLauncher` and calls `self.bl.shutdown_all()` on app
    # exit. Absent here that call is an `attr-defined` error even though it
    # succeeds at runtime — the same defect class as the IProfileManager drift
    # this ticket closes, and the test of whether a method belongs on a
    # protocol is exactly this: is it reached through a protocol-typed
    # reference. (`set_launch_record_hook` is NOT, so it stays off: the
    # container wires it on the concrete BrowserLauncher before handing back
    # the protocol-typed value.)
    def shutdown_all(self) -> None: ...

    def running_profile_names(self) -> set[str]: ...

    def running_count(self) -> int: ...

    def is_running(self, profile_name: str) -> bool: ...

    def started_at(self, profile_name: str) -> float | None: ...

    def cdp_channel_open(self, profile_name: str) -> bool: ...

    def last_refusal(self, profile_name: str) -> "Refusal | None": ...

    def forget_refusal(self, profile_name: str) -> None: ...

    # The SURVIVOR surface (PS-223): browsers a PREVIOUS persona launched and
    # did not get to tear down. Declared here because every one of these is
    # reached through a protocol-typed reference — ui/app.py holds
    # `self.bl: IBrowserLauncher` and calls scan_survivors() at startup, and the
    # launch/close paths call survivor_for/close_survivor/forget_survivor — so
    # by this file's own stated test (is it reached through the protocol?) they
    # belong on it.
    def scan_survivors(
        self,
    ) -> "tuple[list[SessionRecord], list[SessionRecord]]": ...

    def survivors(self) -> "list[SessionRecord]": ...

    def survivor_for(self, profile_name: str) -> "SessionRecord | None": ...

    def forget_survivor(self, profile_name: str) -> None: ...

    def close_survivor(self, profile_name: str) -> bool: ...

    # Reached through the protocol from ui/app.py's exit-confirmation confirm
    # handler, which holds `self.bl: IBrowserLauncher`.
    def close_all_survivors(self) -> "list[str]": ...


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
