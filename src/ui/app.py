import asyncio
import os
import threading

import flet as ft

from ..core.container import Container
from ..core.logging import get_logger
from ..core.strings import get_string
from ..interfaces.protocols import IBrowserLauncher, IProfileManager, IProxyService
from .components import (
    build_bookmarks_page,
    build_tags_page,
    build_connect_page,
    build_content_area,
    build_empty_state,
    build_network_page,
    build_profile_card,
    build_sidebar,
    build_top_bar,
    build_ui_refs,
    rebuild_bulk_bar,
)
from ..services.engine import updater as engine
from ..services.app_update import updater as app_update
from ..core import settings as app_settings
from .components.onboarding import Onboarding
from . import progress_fmt as pf
from .dialogs.proxy import open_proxy_dialog
from .dialogs.bookmark import open_bookmark_dialog
from .dialogs.pool import open_pool_dialog
from .dialogs.confirm import open_confirm_dialog
from .handlers import AppHandlers
from .log_format import log_line_control
from .refs import UIRefs
from .state import ITEMS_PER_PAGE, AppState
from .theme import ACCENT_STYLE, COLORS, configure_page

logger = get_logger("app")


from ..services.profile.filter import all_tags, filter_by_tag, filter_profiles




class App:
    def __init__(
        self,
        container: Container | None = None,
        api_server=None,
    ) -> None:
        c = container or Container()
        self.api_server = api_server
        self.pm: IProfileManager = c.profile_manager
        self.bl: IBrowserLauncher = c.browser_launcher
        self.ps: IProxyService = c.proxy_service
        self.pstore = c.proxy_store
        self.ssh_store = c.ssh_host_store
        self.bstore = c.bookmark_store
        self.state = AppState()
        self.page: ft.Page | None = None
        self._reconcile_started = False
        self.refs: UIRefs | None = None
        self._active_page = "profiles"
        self._search_query = ""
        self._active_tag = ""
        self._page_host: ft.Container | None = None
        self._sidebar_host: ft.Container | None = None
        self._app_latest = ""
        self._app_update_url = ""
        self._app_update_size = 0
        self._app_update_status = ""  # '', downloading, ready, failed
        self._app_update_done = 0
        self._app_update_total = 0
        self._update_in_progress = False
        self._update_staged = ""
        self._update_start_t = 0.0
        self._checking_proxies: set[str] = set()
        self._engine_latest: str = ""
        self._engine_busy = False
        self._engines_open = False
        self._engine2_latest: str = ""
        self._engine2_busy = False
        self._engine2_status: str = ""
        self._engine2_checking = False
        self._engine2_start_t = 0.0
        self._engine_throttle = pf.ProgressThrottle()
        self._engine2_throttle = pf.ProgressThrottle()
        self._engine_pstate = pf.ProgressState()
        self._engine2_pstate = pf.ProgressState()
        self._engine2_bar = ft.ProgressBar(
            value=None, color=COLORS["accent"], bgcolor=COLORS["input_bg"], height=4,
        )
        self._engine2_detail = ft.Text(
            "", size=10, color=COLORS["text_sub"], font_family="monospace",
        )
        self.engine_text = ft.Text(
            "…",
            size=12,
            color=COLORS["text_main"],
            font_family="monospace",
        )
        self._engine_start_t = 0.0
        self._engine_bar = ft.ProgressBar(
            value=None,
            color=COLORS["accent"],
            bgcolor=COLORS["input_bg"],
            height=4,
        )
        self._engine_detail = ft.Text(
            "",
            size=10,
            color=COLORS["text_sub"],
            font_family="monospace",
        )
        self.count_text = ft.Text(
            "0",
            size=14,
            color=COLORS["text_sub"],
            font_family="monospace",
        )
        c.event_bus.subscribe(self.state.schedule_refresh)
        self.h = AppHandlers(
            pm=self.pm,
            bl=self.bl,
            ps=self.ps,
            state=self.state,
            get_page=lambda: self.page,
            get_refs=lambda: self.refs,
            log_fn=self._log,
            refresh_fn=self._refresh_profiles,
            get_page_profiles=self._get_page_profiles,
            get_proxy_names=lambda: self.pstore.names(),
            get_pool_names=lambda: self.bstore.pool_names(),
            get_bookmarks=lambda: self.bstore.list_bookmarks(),
            import_cookies_file=self._import_cookies_file,
            export_cookies_file=self._export_cookies_file,
            open_add_proxy=self._goto_add_proxy,
        )

    def run(self) -> None:
        ft.run(self._main)

    def _main(self, page: ft.Page) -> None:
        self.page = page
        configure_page(page)
        fp = ft.FilePicker()
        page.services.append(fp)
        self.refs = build_ui_refs(
            pm=self.pm,
            on_change_page=self._change_page,
            file_picker=fp,
        )
        page.add(self._build_root_layout(self.refs))
        self._render_active_page()
        self._refresh_profiles()
        self._refresh_engine_text()
        if app_settings.is_onboarding_done():
            self._check_engine_async()
            self._ensure_engine2_async()
        else:
            self._show_onboarding()
        self._check_app_update_async()
        self._check_engines_periodic()
        self.state._last_running_snapshot = self.bl.running_profile_names()
        self._start_server_if_enabled()
        if not self._reconcile_started:
            self._reconcile_started = True
            page.run_task(self._ui_reconcile_loop)

    def _build_sidebar(self) -> ft.Container:
        r = self.refs
        assert r is not None
        log_panel = ft.Column(
            spacing=4,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        r.log_toggle_btn,
                        ft.IconButton(
                            icon=ft.Icons.OPEN_IN_FULL,
                            icon_size=14,
                            icon_color=COLORS["text_sub"],
                            on_click=lambda _: self.h.open_log_fullscreen(),
                        ),
                    ],
                ),
                r.log_column,
            ],
        )
        engine_panel = self._build_engines_panel()
        return build_sidebar(
            active_page=self._active_page,
            on_navigate=self._navigate,
            log_panel=log_panel,
            engine_panel=engine_panel,
            version_panel=self._build_version_panel(),
        )

    def _update_button(self, label: str) -> ft.Control:
        # full-width, single-line: the sidebar is only ~200px so a default
        # Button wraps "[ update to vX.Y.Z ]" onto two lines and tears the box.
        return ft.Container(
            on_click=lambda _: self._apply_update_now(),
            ink=True,
            height=30,
            border_radius=3,
            border=ft.Border.all(1, COLORS["accent"]),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=6, vertical=0),
            content=ft.Text(
                label,
                size=11,
                color=COLORS["accent"],
                font_family="monospace",
                no_wrap=True,
                max_lines=1,
                text_align=ft.TextAlign.CENTER,
            ),
        )

    def _build_version_panel(self) -> ft.Control:
        from . import progress_fmt as pf

        ver = app_update.APP_VERSION
        has_update = bool(self._app_latest) and self._app_latest != ver
        auto_on = app_settings.is_auto_update_enabled()

        rows: list[ft.Control] = []

        # version line (+ badge when an update is available)
        version_row = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    f"persona v{ver}",
                    size=11,
                    color=COLORS["text_sub"],
                    font_family="monospace",
                ),
                *(
                    [
                        ft.Container(
                            width=7, height=7, border_radius=4,
                            bgcolor=COLORS["accent"],
                        )
                    ]
                    if has_update
                    else []
                ),
            ],
        )
        rows.append(version_row)

        # auto-update toggle on its own line so the label fits
        toggle = ft.Container(
            on_click=lambda _: self._set_auto_update(not auto_on),
            ink=True,
            border_radius=3,
            padding=ft.Padding.symmetric(horizontal=4, vertical=2),
            tooltip=(
                "Auto-update is ON: new versions download and install\n"
                "automatically when no profiles are running. Click to turn off."
                if auto_on else
                "Auto-update is OFF: you update manually from here.\n"
                "Click to turn on automatic updates."
            ),
            content=ft.Text(
                f"[ auto-update: {'on' if auto_on else 'off'} ]",
                size=10,
                color=COLORS["accent"] if auto_on else COLORS["text_dim"],
                font_family="monospace",
            ),
        )
        rows.append(toggle)

        # status line / action
        if self._app_update_status == "downloading":
            done, total = self._app_update_done, self._app_update_total
            import time

            elapsed = max(time.monotonic() - self._update_start_t, 0.001)
            target = self._app_latest or "new version"
            if done <= 0:
                # no bytes yet: a Tor circuit can take a while to deliver the
                # first byte \u2014 say so instead of showing a frozen "0.0 MB".
                label = "connecting\u2026"
            elif total > 0:
                label = f"{pf.percent(done, total)}%"
            else:
                label = pf.fmt_mb(done)
            rows.append(
                ft.Text(
                    f"updating to {target} \u00b7 {label}",
                    size=10, color=COLORS["accent"], font_family="monospace",
                )
            )
            rows.append(
                ft.ProgressBar(
                    value=pf.fraction(done, total) if done > 0 else None,
                    color=COLORS["accent"], bgcolor=COLORS["input_bg"], height=4,
                )
            )
            rows.append(
                ft.Text(
                    pf.fmt_line(done, total, elapsed),
                    size=9, color=COLORS["text_sub"], font_family="monospace",
                )
            )
        elif self._update_staged or self._app_update_status == "ready":
            rows.append(self._update_button("[ restart to update ]"))
        elif has_update:
            rows.append(
                self._update_button(f"[ update to {self._app_latest} ]")
            )

        return ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["card_border"]),
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            margin=ft.Margin.only(bottom=10),
            content=ft.Column(spacing=4, controls=rows),
        )

    def _build_root_layout(self, r: UIRefs) -> ft.Row:
        r.log_toggle_btn.on_click = lambda _: self.h.toggle_log()
        self._sidebar_host = ft.Container(content=self._build_sidebar())
        self._page_host = ft.Container(expand=True)
        return ft.Row(
            expand=True,
            spacing=0,
            controls=[
                self._sidebar_host,
                ft.VerticalDivider(width=1, color=COLORS["border"]),
                self._page_host,
            ],
        )

    def _navigate(self, page_name: str) -> None:
        if page_name == self._active_page:
            return
        self._active_page = page_name
        if self._sidebar_host is not None:
            self._sidebar_host.content = self._build_sidebar()
        self._render_active_page()
        self._safe_update()

    def _render_active_page(self) -> None:
        if self._page_host is None:
            return
        if self._active_page == "network":
            self._page_host.content = build_network_page(
                self.pstore.list_proxies(),
                on_add=lambda _: self._open_proxy_dialog(),
                on_edit=self._edit_proxy,
                on_delete=self._delete_proxy,
                on_check=self._check_proxy,
                checking=self._checking_proxies,
            )
        elif self._active_page == "bookmarks":
            self._page_host.content = build_bookmarks_page(
                self.bstore.list_bookmarks(),
                self.bstore.list_pools(),
                on_add_bookmark=lambda _: self._open_bookmark_dialog(),
                on_edit_bookmark=self._edit_bookmark,
                on_delete_bookmark=self._delete_bookmark,
                on_make_pool=self._make_pool_from,
                on_edit_pool=self._edit_pool,
                on_delete_pool=self._delete_pool,
            )
        elif self._active_page == "connect":
            from ..api.mcp_config import claude_add_command, client_config_json, mcp_url
            from ..api.mcp_token import get_or_create_token
            tok = get_or_create_token()
            self._page_host.content = build_connect_page(
                self.pm.list_profiles(),
                token=tok,
                add_command=claude_add_command(tok),
                config_json=client_config_json(tok),
                on_toggle_ai=self._toggle_ai,
                server_running=self._server_running(),
                on_toggle_server=self._set_server,
                endpoint=mcp_url(),
                ssh_hosts=self.ssh_store.list(),
                on_ssh_add=lambda: self._open_ssh_host_dialog(),
                on_ssh_edit=self._edit_ssh_host,
                on_ssh_delete=self._delete_ssh_host,
                on_ssh_run=self._ssh_run,
            )
        elif self._active_page == "tags":
            self._page_host.content = build_tags_page(
                self.pm.list_profiles(),
                on_assign=self._assign_tag,
                on_remove_tag=self._remove_tag,
            )
        else:
            self._page_host.content = self._build_profiles_page()

    def _ssh_run(self, host_name: str, command: str) -> tuple[int, str, str]:
        from ..services.ssh import client as ssh
        from ..services.ssh.resolver import target_for

        host = self.ssh_store.get(host_name)
        if host is None:
            return 1, "", f"host {host_name!r} not found"
        target = target_for(host, self.pm, self.pstore)
        return ssh.run_command(target, command)

    def _open_ssh_host_dialog(self, name: str | None = None) -> None:
        from .dialogs.ssh_host import open_ssh_host_dialog

        host = self.ssh_store.get(name) if name else None
        profile_names = [p.name for p in self.pm.list_profiles()]

        def on_save(h) -> str | None:
            if name:
                if not self.ssh_store.update(name, h):
                    return "Update failed (name conflict?)"
            else:
                if not self.ssh_store.add(h):
                    return "Host name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_ssh_host_dialog(self.page, host, profile_names, on_save)

    def _edit_ssh_host(self, name: str) -> None:
        self._open_ssh_host_dialog(name)

    def _delete_ssh_host(self, name: str) -> None:
        self.ssh_store.remove(name)
        self._render_active_page()
        self._safe_update()

    def _build_profiles_page(self) -> ft.Container:
        r = self.refs
        assert r is not None
        search_field = ft.TextField(
            value=self._search_query,
            on_change=self._on_search,
            hint_text="search profiles...",
            width=220,
            height=40,
            border_radius=3,
            bgcolor=COLORS["input_bg"],
            color=COLORS["text_main"],
            border_color=COLORS["card_border"],
            focused_border_color=COLORS["accent"],
            text_style=ft.TextStyle(font_family="monospace", size=13),
            hint_style=ft.TextStyle(font_family="monospace", size=13),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        top_bar = build_top_bar(
            self.count_text,
            search_field,
            on_new=lambda _: self.h.open_add_dialog(),
            on_import=self.h.on_import,
            on_export=lambda _: self.h.on_export_open(),
        )
        content = build_content_area(
            r.content_subtitle,
            r.profile_list_area,
            r.prev_btn,
            r.next_btn,
            r.page_label,
            r.bulk_bar,
        )
        return ft.Container(
            expand=True,
            bgcolor=COLORS["bg"],
            padding=ft.Padding.symmetric(horizontal=32, vertical=24),
            content=ft.Column(
                spacing=0,
                expand=True,
                controls=[top_bar, self._build_tag_chips(), content],
            ),
        )

    def _build_tag_chips(self) -> ft.Control:
        tags = all_tags(self.pm.list_profiles())
        if not tags:
            return ft.Container(height=0)
        chips: list[ft.Control] = []
        for tag in tags:
            active = self._active_tag.lower() == tag.lower()
            chips.append(
                ft.Container(
                    on_click=lambda _, tg=tag: self._toggle_tag_filter(tg),
                    ink=True,
                    border_radius=3,
                    border=ft.Border.all(1, COLORS["accent"] if active else COLORS["card_border"]),
                    bgcolor=COLORS["accent"] if active else COLORS["card_bg"],
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    content=ft.Text(
                        tag,
                        size=12,
                        color=COLORS["bg"] if active else COLORS["text_sub"],
                        font_family="monospace",
                    ),
                )
            )
        return ft.Container(
            padding=ft.Padding.only(bottom=12),
            content=ft.Row(spacing=8, wrap=True, controls=chips),
        )

    def _toggle_tag_filter(self, tag: str) -> None:
        if self._active_tag.lower() == tag.lower():
            self._active_tag = ""
        else:
            self._active_tag = tag
        self.state.current_page = 1
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _toggle_ai(self, name: str, enabled: bool) -> None:
        self.pm.set_ai_control(name, enabled)
        state = "enabled" if enabled else "disabled"
        self._log(f"AI control {state} for '{name}'")
        self._safe_update()

    def _save_notes_inline(self, name: str, notes: str) -> None:
        """Save a profile's notes edited inline on the card (no dialog)."""
        p = self.pm.profiles.get(name)
        if p is None or getattr(p, "notes", "") == notes:
            return
        p.notes = notes
        self.pm.save_profiles()

    def _engine2_version_text(self) -> str:
        from ..services.browser.invisible_launch import (
            installed_version,
            is_invisible_installed,
        )

        if not is_invisible_installed():
            return "not installed"
        return installed_version() or "installed"

    def _engine2_update_available(self) -> bool:
        # The Firefox engine's version is pinned to the invisible_playwright
        # package, which ships with persona — it updates with the app, not on
        # its own. So there's never a standalone engine update to offer.
        return False

    def _engine2_status_text(self) -> str:
        if self._engine2_checking:
            return "checking…"
        if self._engine2_status:
            return self._engine2_status
        return self._engine2_version_text()

    def _assign_tag(self, names: list[str], tag: str) -> None:
        n = self.pm.assign_tag(names, tag)
        if n:
            self._log(f"Tagged {n} profile(s) with '{tag.strip()}'")
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _remove_tag(self, tag: str) -> None:
        n = self.pm.remove_tag(tag)
        if n:
            self._log(f"Removed tag '{tag}' from {n} profile(s)")
        if self._active_tag.lower() == tag.lower():
            self._active_tag = ""
        self._render_active_page()
        self._refresh_profiles()
        self._safe_update()

    def _goto_add_proxy(self) -> None:
        """Jump from the profile dialog to the network page with the add-proxy
        dialog already open."""
        self._navigate("network")
        self._open_proxy_dialog()

    def _open_proxy_dialog(self, name: str | None = None) -> None:
        page = self.page
        assert page is not None
        existing = self.pstore.get(name) if name else None

        def on_save(new_name: str, new_url: str) -> str | None:
            if existing is None:
                if not self.pstore.add(new_name, new_url):
                    return "Proxy name already exists"
            else:
                if not self.pstore.update(existing.name, new_name, new_url):
                    return "Proxy name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        def on_checked(
            proxy_name: str,
            code: str,
            country: str,
            ip: str,
            tz: str,
            lat: float | None = None,
            lon: float | None = None,
        ) -> None:
            self.pstore.mark_checked(
                proxy_name, code, country, ip, tz, lat, lon
            )

        def on_check_failed(proxy_name: str) -> None:
            self.pstore.mark_check_failed(proxy_name)

        open_proxy_dialog(
            page,
            self.ps,
            on_save,
            existing,
            on_checked=on_checked,
            on_check_failed=on_check_failed,
        )

    def _edit_proxy(self, name: str) -> None:
        self._open_proxy_dialog(name)

    def _delete_proxy(self, name: str) -> None:
        page = self.page
        assert page is not None
        in_use = [
            p.name for p in self.pm.list_profiles() if p.proxy == name
        ]

        def do_delete() -> None:
            self.pstore.delete(name)
            self._render_active_page()
            self._safe_update()

        if in_use:
            shown = ", ".join(in_use[:5])
            more = f" and {len(in_use) - 5} more" if len(in_use) > 5 else ""
            body = (
                f"{len(in_use)} profile(s) use it ({shown}{more}); "
                "they will fall back to a direct connection."
            )
        else:
            body = "No profiles use this proxy."

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete proxy '{name}'?",
            body=body,
        )

    def _refresh_proxy_views(self) -> None:
        # the flag/spinner lives on both the network page and the
        # profile cards; refresh whichever is active.
        if self._active_page == "profiles":
            self._refresh_profiles()
        else:
            self._render_active_page()
        self._safe_update()

    def _check_proxy(self, name: str) -> None:
        proxy = self.pstore.get(name)
        if proxy is None or name in self._checking_proxies:
            return
        self._checking_proxies.add(name)
        self._refresh_proxy_views()

        def do_check() -> None:
            try:
                ok, message, code, country, ip, tz, lat, lon = (
                    self.ps.check_proxy_detailed_sync(proxy.url)
                )
                self._log(f"[{name}] {message}")
                if ok:
                    self.pstore.mark_checked(
                        name, code, country, ip, tz, lat, lon
                    )
                else:
                    self.pstore.mark_check_failed(name)
            finally:
                self._checking_proxies.discard(name)
                self._refresh_proxy_views()

        threading.Thread(target=do_check, daemon=True).start()

    # --- Bookmarks ---

    def _open_bookmark_dialog(self, name: str | None = None) -> None:
        page = self.page
        assert page is not None
        existing = self.bstore.get(name) if name else None

        def on_save(new_name: str, new_url: str) -> str | None:
            if existing is None:
                if not self.bstore.add(new_name, new_url):
                    return "Bookmark name already exists"
            else:
                if not self.bstore.update(existing.name, new_name, new_url):
                    return "Bookmark name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_bookmark_dialog(page, on_save, existing)

    def _edit_bookmark(self, name: str) -> None:
        self._open_bookmark_dialog(name)

    def _delete_bookmark(self, name: str) -> None:
        page = self.page
        assert page is not None

        def do_delete() -> None:
            self.bstore.delete(name)
            self._render_active_page()
            self._safe_update()

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete bookmark '{name}'?",
            body="It will also be removed from any pools.",
        )

    def _open_pool_dialog(
        self, name: str | None = None, preselected: list[str] | None = None
    ) -> None:
        page = self.page
        assert page is not None
        existing = self.bstore.get_pool(name) if name else None

        def on_save(new_name: str, members: list[str]) -> str | None:
            if existing is None:
                if not self.bstore.add_pool(new_name, members):
                    return "Pool name already exists"
            else:
                if not self.bstore.update_pool(existing.name, new_name, members):
                    return "Pool name already exists"
            self._render_active_page()
            self._safe_update()
            return None

        open_pool_dialog(
            page,
            self.bstore.list_bookmarks(),
            on_save,
            existing,
            preselected=preselected,
        )

    def _make_pool_from(self, bookmark_names: list[str]) -> None:
        self._open_pool_dialog(preselected=bookmark_names)

    def _edit_pool(self, name: str) -> None:
        self._open_pool_dialog(name)

    def _delete_pool(self, name: str) -> None:
        page = self.page
        assert page is not None

        def do_delete() -> None:
            self.bstore.delete_pool(name)
            self._render_active_page()
            self._safe_update()

        open_confirm_dialog(
            page,
            name,
            do_delete,
            title=f"Delete pool '{name}'?",
            body="The bookmarks themselves are kept.",
        )

    # --- Cookies ---

    def _profile_dir(self, name: str) -> str:
        import os

        from ..core.config import DATA_DIR

        return os.path.join(os.getcwd(), DATA_DIR, name)

    async def _import_cookies_file(self, profile_name: str) -> str | None:
        from ..services.cookie.store import import_cookies, parse_cookies_json

        assert self.refs is not None
        files = await self.refs.file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["json"],
            dialog_title=f"Import cookies into {profile_name}",
        )
        if not files or not files[0].path:
            return None
        import os

        fname = os.path.basename(files[0].path)
        try:
            with open(files[0].path, encoding="utf-8") as f:
                cookies = parse_cookies_json(f.read())
            n = import_cookies(self._profile_dir(profile_name), cookies)
        except Exception as e:
            self._log(f"[{profile_name}] cookie import failed: {e}")
            return f"import failed: {e}"
        status = f"{fname} · {n} cookies"
        self.pm.set_cookie_status(profile_name, status)
        self._log(f"[{profile_name}] imported {n} cookies from {fname}")
        return f"imported {status}"

    async def _export_cookies_file(self, profile_name: str) -> str | None:
        import json

        from ..services.cookie.store import export_cookies

        assert self.refs is not None
        path = await self.refs.file_picker.save_file(
            dialog_title=f"Export cookies from {profile_name}",
            file_name=f"{profile_name}-cookies.json",
            allowed_extensions=["json"],
        )
        if not path:
            return None
        try:
            cookies = export_cookies(self._profile_dir(profile_name))
            if not path.endswith(".json"):
                path += ".json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
        except Exception as e:
            self._log(f"[{profile_name}] cookie export failed: {e}")
            return f"export failed: {e}"
        self._log(f"[{profile_name}] exported {len(cookies)} cookies")
        return f"exported {len(cookies)} cookies"

    # --- Engine update ---

    def _engine_icon(self) -> ft.Control:
        from ..core.assets import asset_path

        path = asset_path("v_engine.png")
        if os.path.exists(path):
            return ft.Image(src=path, width=18, height=18)
        return ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, size=15, color=COLORS["text_sub"])

    def _engine_update_available(self) -> bool:
        return engine.is_newer(self._engine_latest, engine.current_version())

    def _engine_logo(self, engine_key: str, size: int = 18) -> ft.Control:
        from ..core.assets import asset_path

        fname = (
            "engine_firefox.png"
            if engine_key in ("firefox", "camoufox")
            else "engine_chrome.png"
        )
        path = asset_path(fname)
        # Box the logo in a fixed-size container with CONTAIN fit so a non-square
        # source can't overflow its slot and clip/overlap the neighbouring row.
        if os.path.exists(path):
            inner: ft.Control = ft.Image(
                src=path, width=size, height=size, fit=ft.ImageFit.CONTAIN
            )
        else:
            inner = ft.Icon(ft.Icons.PUBLIC, size=size, color=COLORS["text_sub"])
        return ft.Container(width=size, height=size, content=inner)

    def _engine_row(
        self, badge: ft.Control, name: str, version: str, checking: bool,
        dot: bool = False,
    ) -> ft.Control:
        trailing: list[ft.Control] = []
        if checking:
            trailing.append(
                ft.ProgressRing(
                    width=11, height=11, stroke_width=2, color=COLORS["accent"]
                )
            )
        elif dot:
            trailing.append(
                ft.Container(width=7, height=7, border_radius=4, bgcolor=COLORS["accent"])
            )
        return ft.Column(
            spacing=1,
            controls=[
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        badge,
                        ft.Text(
                            name, size=11, color=COLORS["text_sub"],
                            font_family="monospace",
                        ),
                        *trailing,
                    ],
                ),
                ft.Container(
                    padding=ft.Padding.only(left=26),
                    content=ft.Text(
                        version, size=12, color=COLORS["text_main"],
                        font_family="monospace",
                    ),
                ),
            ],
        )

    def _build_engines_panel(self) -> ft.Control:
        header = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            on_click=lambda _: self._toggle_engines(),
            ink=True,
            tooltip="Browser engines — open to check both",
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self._engine_icon(),
                            ft.Text(
                                "engines", size=11, color=COLORS["text_sub"],
                                font_family="monospace",
                            ),
                            *(
                                [
                                    ft.Container(
                                        width=7, height=7, border_radius=4,
                                        bgcolor=COLORS["accent"],
                                    )
                                ]
                                if (
                                    self._engine_update_available()
                                    or self._engine2_update_available()
                                )
                                else []
                            ),
                        ],
                    ),
                    ft.Icon(
                        ft.Icons.KEYBOARD_ARROW_UP
                        if self._engines_open
                        else ft.Icons.KEYBOARD_ARROW_DOWN,
                        size=16,
                        color=COLORS["text_sub"],
                    ),
                ],
            ),
        )

        def _bar_block(bar, detail) -> ft.Control:
            return ft.Container(
                padding=ft.Padding.only(left=36, right=10, top=2, bottom=2),
                content=ft.Column(spacing=2, controls=[bar, detail]),
            )

        body: list[ft.Control] = []
        if self._engines_open:
            body = [ft.Divider(height=10, color=COLORS["border"])]
            # fp-chromium row, with its own progress bar directly beneath it
            body.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10),
                    on_click=lambda _: self._on_engine_click(),
                    ink=True,
                    tooltip="Check / update fp-chromium",
                    content=self._engine_row(
                        self._engine_logo("chromium"),
                        "fp-chromium",
                        self.engine_text.value or "…",
                        checking=self._engine_busy,
                        dot=self._engine_update_available(),
                    ),
                )
            )
            if self._engine_busy:
                body.append(_bar_block(self._engine_bar, self._engine_detail))
            body.append(ft.Container(height=8))
            # firefox engine row, with its own progress bar directly beneath it
            body.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10),
                    on_click=lambda _: self._on_engine2_click(),
                    ink=True,
                    tooltip="Download the Firefox engine",
                    content=self._engine_row(
                        self._engine_logo("firefox"),
                        "firefox",
                        self._engine2_status_text(),
                        checking=self._engine2_busy or self._engine2_checking,
                        dot=self._engine2_update_available(),
                    ),
                )
            )
            if self._engine2_busy:
                body.append(_bar_block(self._engine2_bar, self._engine2_detail))
            body.append(ft.Container(height=6))

        return ft.Container(
            border_radius=3,
            border=ft.Border.all(1, COLORS["card_border"]),
            margin=ft.Margin.only(bottom=10),
            content=ft.Column(spacing=0, controls=[header, *body]),
        )

    def _toggle_engines(self) -> None:
        self._engines_open = not self._engines_open
        if self._engines_open:
            self._check_both_engines()
        self._refresh_sidebar()

    def _check_both_engines(self) -> None:
        """Opening the panel checks both engines for an upstream update over
        the network — each runs on its own thread with its own spinner."""
        if not self._engine_busy and not self._engine_update_available():
            self._engine_busy = True
            self._refresh_engine_text("checking…")

            def work() -> None:
                tag, _url = engine.fetch_latest()
                self._engine_latest = tag
                self._engine_busy = False
                if self._engine_update_available():
                    self._log(f"Engine update available: {tag}")
                self._refresh_engine_text()

            threading.Thread(target=work, daemon=True).start()

        # The Firefox engine's version is pinned to the bundled package, so
        # there's no upstream update to fetch — but show a brief "checking…" on
        # its row too so the user sees BOTH engines get verified, then settle
        # back to the installed version. If it isn't installed yet, download it.
        from ..services.browser import invisible_launch as inv

        if inv.is_invisible_installed() and not self._engine2_busy:
            import time

            def check2() -> None:
                self._engine2_checking = True
                self._refresh_sidebar()
                time.sleep(1.0)  # a visible beat so "checking…" is seen
                self._engine2_checking = False
                self._refresh_sidebar()

            threading.Thread(target=check2, daemon=True).start()
        else:
            self._ensure_engine2_async()

    def _on_engine2_click(self) -> None:
        """Clicking the Firefox-engine row downloads it if it isn't installed.
        There's no separate update to check — the engine version is pinned to
        the bundled invisible_playwright package."""
        if self._engine2_busy:
            return
        self._ensure_engine2_async()

    def _refresh_engine_text(self, status: str = "") -> None:
        cur = engine.current_version() or "unknown"
        if status:
            self.engine_text.value = status
        elif self._engine_update_available():
            self.engine_text.value = f"update → {self._engine_latest}"
        else:
            self.engine_text.value = cur
        if self._sidebar_host is not None:
            self._sidebar_host.content = self._build_sidebar()
        self._safe_update()

    def _check_app_update_async(self) -> None:
        """Check for a newer release on startup and then periodically. The
        actual download/restart decision is made by _on_update_found per the
        auto-update setting and whether profiles are running."""
        import threading

        def loop() -> None:
            import time

            while True:
                if not self._update_in_progress and not self._update_staged:
                    tag, url, size = app_update.check_for_update()
                    if tag and url and tag != self._app_latest:
                        self._app_latest = tag
                        self._app_update_url = url
                        self._app_update_size = size
                        self._on_update_found(tag, url)
                time.sleep(60)

        threading.Thread(target=loop, daemon=True).start()

    def _check_engines_periodic(self) -> None:
        """Quietly poll the chromium engine for an upstream update once an hour
        so the sidebar dot lights up on its own. This only refreshes the
        'latest' version (no spinner, no auto-download) — installing stays a
        click. The Firefox engine has no standalone update (its version is
        pinned to the bundled package), so it isn't polled."""
        import threading
        import time

        def loop() -> None:
            while True:
                time.sleep(3600)
                try:
                    if not self._engine_busy:
                        tag, _url = engine.fetch_latest()
                        if tag:
                            self._engine_latest = tag
                            if self._engine_update_available():
                                self._log(f"Engine update available: {tag}")
                except Exception:
                    pass
                self._refresh_sidebar()

        threading.Thread(target=loop, daemon=True).start()

    def _on_update_found(self, tag: str, url: str) -> None:
        """Decide what to do when a newer version is available."""
        # Always refresh the sidebar so the "new version" badge shows.
        self._refresh_sidebar()
        if not app_update.is_packaged_appimage():
            # running from source: just surface it, can't self-update
            self._log(f"New version {tag} available (update from source).")
            return
        # A previous run may have already finished downloading this update; if a
        # complete staged file is on disk, offer to restart into it instead of
        # downloading again (e.g. the user reopened the app before it restarted).
        if not self._update_staged:
            ready = app_update.find_ready_staged(url, size=self._app_update_size)
            if ready:
                self._update_staged = ready
                self._app_update_status = "ready"
                self._log(f"Update {tag} ready — restart to apply.")
                self._refresh_sidebar()
                if (
                    app_settings.is_auto_update_enabled()
                    and len(self.bl.running_profile_names()) == 0
                ):
                    self._log("Restarting into the new version…")
                    app_update.apply_and_restart(ready, log=self._log)
                    # only reached if the relaunch failed: surface the button
                    self._app_update_status = "ready"
                    self._refresh_sidebar()
                return
        if not app_settings.is_auto_update_enabled():
            self._log(f"New version {tag} available — update from the sidebar.")
            return
        running = len(self.bl.running_profile_names()) > 0
        if running:
            # don't interrupt active work; wait for a manual click
            self._log(f"New version {tag} ready to install when you're idle.")
            return
        # no profiles running -> download automatically
        self._log(f"New version {tag} found — downloading…")
        self._start_app_update(url)

    def _start_app_update(self, url: str) -> None:
        import threading

        if self._update_in_progress:
            return
        self._update_in_progress = True

        def work() -> None:
            import time

            self._update_start_t = time.monotonic()
            self._app_update_status = "downloading"
            self._refresh_sidebar()
            staged = app_update.download_update(
                url, progress=self._update_progress_cb, size=self._app_update_size
            )
            self._update_in_progress = False
            if staged:
                self._update_staged = staged
                self._app_update_status = "ready"
                self._log("Update downloaded.")
                self._refresh_sidebar()
                # if still idle (no profiles running), restart now
                if len(self.bl.running_profile_names()) == 0 and app_settings.is_auto_update_enabled():
                    self._log("Restarting into the new version…")
                    app_update.apply_and_restart(staged, log=self._log)
                    # reached only if relaunch failed; keep the restart button
                    self._app_update_status = "ready"
                    self._refresh_sidebar()
            else:
                self._app_update_status = "failed"
                self._log("Update download failed — will retry.")
                self._refresh_sidebar()

        threading.Thread(target=work, daemon=True).start()

    def _update_progress_cb(self, done: int, total: int) -> None:
        import time

        elapsed = max(time.monotonic() - self._update_start_t, 0.001)
        self._app_update_done = done
        self._app_update_total = total
        self._refresh_sidebar()

    def _apply_update_now(self) -> None:
        """Manual 'update now' click: download if needed, then restart."""
        if self._update_staged:
            self._log("Restarting into the new version…")
            app_update.apply_and_restart(self._update_staged, log=self._log)
            # reached only if the relaunch failed
            self._app_update_status = "ready"
            self._refresh_sidebar()
        elif self._app_update_url and not self._update_in_progress:
            # download then restart regardless of running profiles (user asked)
            import threading

            def work() -> None:
                import time

                self._update_in_progress = True
                self._update_start_t = time.monotonic()
                self._app_update_status = "downloading"
                self._refresh_sidebar()
                staged = app_update.download_update(
                    self._app_update_url,
                    progress=self._update_progress_cb,
                    size=self._app_update_size,
                )
                self._update_in_progress = False
                if staged:
                    self._update_staged = staged
                    self._log("Update downloaded — restarting…")
                    app_update.apply_and_restart(staged, log=self._log)
                    self._app_update_status = "ready"
                    self._refresh_sidebar()
                else:
                    self._app_update_status = "failed"
                    self._log("Update download failed — try again.")
                    self._refresh_sidebar()

            threading.Thread(target=work, daemon=True).start()

    def _set_auto_update(self, enabled: bool) -> None:
        app_settings.set_auto_update_enabled(enabled)
        self._refresh_sidebar()
        # if turning on and an update is already known + idle, kick it off
        if (
            enabled
            and self._app_update_url
            and not self._update_in_progress
            and not self._update_staged
            and len(self.bl.running_profile_names()) == 0
        ):
            self._start_app_update(self._app_update_url)

    def _server_running(self) -> bool:
        return bool(self.api_server is not None and self.api_server.is_running)

    def _set_server(self, enabled: bool) -> None:
        if self.api_server is None:
            return
        if enabled and not self.api_server.is_running:
            self.api_server.start()
            self._log("Claude control server started")
        elif not enabled and self.api_server.is_running:
            self.api_server.stop()
            self._log("Claude control server stopped")
        app_settings.set_server_enabled(enabled)
        self._render_active_page()
        self._safe_update()

    def _start_server_if_enabled(self) -> None:
        if app_settings.is_server_enabled():
            self._set_server(True)

    def _show_onboarding(self) -> None:
        page = self.page
        assert page is not None

        def start_engine(progress, done) -> None:
            def work() -> None:
                if engine.is_installed():
                    done(True)
                    return
                self._engine_busy = True
                self._engine_progress_start()

                # mirror progress to BOTH the onboarding dialog and the
                # sidebar panel, so if the user closes onboarding mid-
                # download the sidebar keeps showing live progress instead
                # of a bare 'unknown'.
                def both(done_bytes, total):
                    try:
                        progress(done_bytes, total)
                    except Exception:
                        pass
                    self._engine_progress_cb(done_bytes, total)

                ok, _msg = engine.ensure_engine(progress=both)
                self._engine_busy = False
                if ok:
                    self._engine_latest = engine.current_version()
                self._refresh_engine_text()
                self._refresh_sidebar()
                done(ok)

            threading.Thread(target=work, daemon=True).start()

        def on_finish() -> None:
            app_settings.mark_onboarding_done()
            # if the operator skipped the download, fetch in the background
            if not engine.is_installed() and not self._engine_busy:
                self._check_engine_async()
            # both engines are required: pull the Firefox engine too
            self._ensure_engine2_async()
            self._refresh_engine_text()
            self._safe_update()

        ob = Onboarding(
            page,
            on_finish=on_finish,
            start_engine=start_engine,
            engine_already_installed=engine.is_installed(),
        )
        ob.open()

    def _refresh_sidebar(self) -> None:
        if self._sidebar_host is not None:
            self._sidebar_host.content = self._build_sidebar()
            self._safe_update()

    def _engine_progress_start(self) -> None:
        import time

        self._engine_start_t = time.monotonic()
        self._engine_throttle = pf.ProgressThrottle()
        self._engine_pstate = pf.ProgressState()
        self._engine_bar.value = None
        self._engine_detail.value = ""
        # _engine_busy is already True here; rebuild so the progress
        # bar/detail are inserted into the sidebar tree.
        self._refresh_sidebar()

    def _engine_progress_cb(self, done: int, total: int) -> None:
        import time

        now = time.monotonic()
        # Feed every chunk into the smoothed, monotonic state (cheap), but only
        # repaint when the throttle allows — a chunk-rate repaint flickers the
        # sidebar. The state keeps percent from jumping backwards on a retry and
        # EMA-smooths the speed so the numbers move steadily.
        self._engine_pstate.update(done, total, now)
        if not self._engine_throttle.should_emit(done, total, now):
            return
        st = self._engine_pstate
        self._engine_bar.value = st.fraction
        # With a known size show a percentage; when the server omits
        # Content-Length (common over Tor) show the live downloaded amount
        # so it's obvious bytes are flowing rather than a bar spinning idle.
        label = f"{st.percent}%" if st.total > 0 else pf.fmt_mb(st.done)
        self.engine_text.value = f"downloading {label}"
        self._engine_detail.value = st.line()
        # The bar/detail/text controls are already in the sidebar tree, so
        # updating their .value and pushing the page reflects the change
        # without rebuilding the whole sidebar on every chunk (which made
        # unrelated controls flicker).
        self._safe_update()

    def _check_engine_async(self) -> None:
        def work() -> None:
            if not engine.is_installed():
                self._download_engine_fresh()
                return
            tag, _url = engine.fetch_latest()
            self._engine_latest = tag
            if self._engine_update_available():
                self._log(f"Engine update available: {tag}")
            self._refresh_engine_text()

        threading.Thread(target=work, daemon=True).start()

    def _ensure_engine2_async(self) -> None:
        """Both engines are required, not optional. If the Firefox engine binary
        isn't present (fresh install, or an update that added it to an install
        that only had chromium), fetch it in the background with a visible
        status — the same first-run treatment fp-chromium gets."""
        from ..services.browser import invisible_launch as inv

        def work() -> None:
            import time

            if inv.is_invisible_installed():
                return
            self._engine2_busy = True
            self._engine2_status = "downloading…"
            self._engine2_start_t = time.monotonic()
            self._engine2_throttle = pf.ProgressThrottle()
            self._engine2_pstate = pf.ProgressState()
            self._engine2_bar.value = None
            self._engine2_detail.value = "connecting…"
            self._log("Firefox engine not found — downloading…")
            self._refresh_sidebar()
            ok = False
            # the binary is ~80MB over Tor; retry a few times so a dropped
            # circuit doesn't leave the (required) engine uninstalled
            for attempt in range(3):
                try:
                    ok = inv.ensure_invisible_installed(
                        progress=self._engine2_progress_cb, log=self._log
                    )
                except Exception as e:
                    self._log(f"Firefox engine download error: {e}")
                    ok = False
                if ok:
                    break
                if attempt < 2:
                    self._log("Firefox engine download interrupted — retrying…")
            self._engine2_busy = False
            self._engine2_detail.value = ""
            if ok:
                # Show the installed version straight away — clearing the status
                # first would flash "not installed" until the version resolved.
                self._engine2_status = inv.installed_version()
                self._log(f"Firefox engine installed: {inv.installed_version()}")
            else:
                self._engine2_status = ""
                self._log("Firefox engine download failed — will retry on next start")
            self._refresh_sidebar()

        threading.Thread(target=work, daemon=True).start()

    def _engine2_progress_cb(self, done: int, total: int) -> None:
        import time

        now = time.monotonic()
        if done > 0:
            self._engine2_pstate.update(done, total, now)
        # Always let the pre-transfer "connecting" ticks and completion through;
        # throttle only the steady stream of transfer chunks so the download
        # renders as smoothly as chromium's without flickering the sidebar.
        if done > 0 and not self._engine2_throttle.should_emit(done, total, now):
            return
        elapsed = max(now - self._engine2_start_t, 0.001)
        if done <= 0:
            # The fetch can sit ~30-60s before the first byte arrives over Tor.
            # Show a ticking "connecting" so it reads as alive, not frozen.
            self._engine2_bar.value = None
            self._engine2_status = "downloading…"
            self._engine2_detail.value = (
                f"connecting over Tor… {int(elapsed)}s (first bytes can take a "
                f"minute)"
            )
        else:
            st = self._engine2_pstate
            self._engine2_bar.value = st.fraction
            # At 100% the bytes are down but extraction still runs; show
            # "installing…" so the row reads as progressing instead of snapping
            # from a percent to a momentary blank / "not installed".
            if st.total > 0 and st.done >= st.total:
                self._engine2_status = "installing…"
                self._engine2_detail.value = "installing…"
            else:
                self._engine2_status = (
                    f"{st.percent}%" if st.total > 0 else pf.fmt_mb(st.done)
                )
                self._engine2_detail.value = st.line()
        # Bar and detail are live controls already in the tree; update in place
        # instead of rebuilding the sidebar on every chunk (the flicker source).
        self._safe_update()

    def _download_engine_fresh(self) -> None:
        """First-run: no engine installed yet, fetch it before anything can
        launch. Runs on the engine-check thread; shows progress in the panel.
        """
        self._engine_busy = True
        self._log("No browser engine found — downloading…")
        self._engine_progress_start()
        self.engine_text.value = "connecting…"
        self._refresh_sidebar()

        ok, msg = engine.ensure_engine(progress=self._engine_progress_cb)
        self._engine_busy = False
        self._engine_detail.value = ""
        if ok:
            self._engine_latest = engine.current_version()
            self._log(f"Engine installed: {engine.current_version()}")
        else:
            self._log(f"Engine download failed: {msg}")
        self._refresh_engine_text()
        self._refresh_sidebar()

    def _on_engine_click(self) -> None:
        if self._engine_busy:
            return
        if self._engine_update_available():
            self._update_engine_async()
        else:
            self._engine_busy = True
            self._refresh_engine_text("checking…")

            def work() -> None:
                tag, _url = engine.fetch_latest()
                self._engine_latest = tag
                self._engine_busy = False
                if self._engine_update_available():
                    self._log(f"Engine update available: {tag}")
                else:
                    self._log("Engine is up to date")
                self._refresh_engine_text()

            threading.Thread(target=work, daemon=True).start()

    def _update_engine_async(self) -> None:
        self._engine_busy = True
        self._engine_progress_start()
        self._refresh_engine_text("downloading…")
        self._log(f"Downloading engine {self._engine_latest}…")

        def work() -> None:
            _tag, url = engine.fetch_latest()
            ok = engine.download_engine(url, progress=self._engine_progress_cb)
            if ok:
                engine.write_version(self._engine_latest)
                self._log(f"Engine updated to {self._engine_latest}")
            else:
                self._log("Engine update failed")
            self._engine_busy = False
            self._engine_detail.value = ""
            self._refresh_engine_text()

        threading.Thread(target=work, daemon=True).start()

    def _on_search(self, e: ft.ControlEvent) -> None:
        self._search_query = e.control.value or ""
        self.state.current_page = 1
        self._refresh_profiles()

    def _get_page_profiles(self) -> tuple[list, list, int]:
        all_profiles = filter_by_tag(
            filter_profiles(self.pm.list_profiles(), self._search_query),
            self._active_tag,
        )
        total = max(1, (len(all_profiles) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        self.state.current_page = min(self.state.current_page, total)
        start = (self.state.current_page - 1) * ITEMS_PER_PAGE
        return all_profiles, all_profiles[start : start + ITEMS_PER_PAGE], total

    def _refresh_profiles(self) -> None:
        r = self.refs
        assert r is not None
        self._update_stats()
        self._flush_log()
        all_profiles, page_profiles, total_pages = self._get_page_profiles()

        all_names = {p.name for p in all_profiles}
        for stale in self.state.selected_names() - all_names:
            self.state.toggle_selection(stale)

        r.profile_list_area.controls = (
            [
                build_profile_card(
                    p,
                    self.state.is_loading(p.name),
                    self.bl.is_running(p.name),
                    self.h.on_launch,
                    self.h.on_edit,
                    self.h.on_delete,
                    is_selected=self.state.is_selected(p.name),
                    on_select=self.h.on_toggle_select,
                    proxy=self.pstore.get(p.proxy) if p.proxy else None,
                    on_check_proxy=self._check_proxy,
                    on_notes_change=self._save_notes_inline,
                    proxy_checking=(
                        p.proxy in self._checking_proxies if p.proxy else False
                    ),
                )
                for p in page_profiles
            ]
            if page_profiles
            else [build_empty_state(lambda _: self.h.open_add_dialog())]
        )
        rebuild_bulk_bar(
            r.bulk_bar,
            self.state,
            page_profiles,
            {
                "launch": self.h.on_bulk_launch,
                "stop": self.h.on_bulk_stop,
                "delete": self.h.on_bulk_delete,
                "select_page": self.h.on_select_all_page,
                "deselect_page": self.h.on_deselect_page,
                "clear": self.h.on_clear_selection,
            },
        )
        r.content_subtitle.value = self._profiles_subtitle()
        total_count = len(self.pm.profiles)
        self.count_text.value = (
            f"{len(all_profiles)}/{total_count}"
            if self._search_query.strip()
            else str(total_count)
        )
        r.page_label.value = get_string(
            "page_of",
            current=self.state.current_page,
            total=total_pages,
        )
        r.prev_btn.disabled = self.state.current_page <= 1
        r.next_btn.disabled = self.state.current_page >= total_pages
        self._safe_update()

    def _change_page(self, delta: int) -> None:
        self.state.current_page += delta
        self._refresh_profiles()

    def _profiles_subtitle(self) -> str:
        r = self.bl.running_count()
        return f"● {r} running" if r else ""

    def _update_stats(self) -> None:
        r = self.refs
        if r:
            cnt = self.bl.running_count()
            r.stats_text.value = get_string(
                "total_profiles",
                count=len(self.pm.profiles),
            )
            r.running_text.value = (
                f"●  {cnt} browser{'s' if cnt != 1 else ''} running"
                if cnt
                else "○  No active sessions"
            )

    def _log(self, message: str) -> None:
        logger.info(message)
        if self.state.add_log(message):
            self.state.schedule_refresh()

    def _flush_log(self) -> None:
        text = self.state.flush_log()
        if text is not None and self.refs:
            lines = [ln for ln in text.split("\n") if ln]
            sidebar_lines = lines[-6:]
            self.refs.log_list.controls = [
                log_line_control(ln, wrap=False) for ln in sidebar_lines
            ]
            self.refs.log_column.height = max(72, len(sidebar_lines) * 18 + 20)
            self.refs.log_column.visible = (
                bool(sidebar_lines) and not self.state.log_collapsed
            )

    def _safe_update(self) -> None:
        if not self.page:
            return
        try:
            with self.state._ui_update_lock:
                self.page.update()
        except Exception as e:
            logger.error("Error updating UI: %s", e)

    async def _ui_reconcile_loop(self) -> None:
        while self.page:
            try:
                running_now = self.bl.running_profile_names()
                changed = running_now != self.state._last_running_snapshot
                if changed:
                    self.state._last_running_snapshot = running_now
                if changed or self.state.consume_refresh():
                    self._refresh_profiles()
            except Exception as e:
                logger.error("Error in UI reconcile loop: %s", e)
            await asyncio.sleep(0.12)
