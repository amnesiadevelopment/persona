from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IBrowserLauncher, IProfileManager, IProxyService
from ...models.bookmark import Bookmark
from ..dialogs import open_bulk_dialog, open_confirm_dialog, open_profile_dialog


def delete_profile(
    page: ft.Page,
    name: str,
    pm: IProfileManager,
    log: Callable[[str], None],
    refresh: Callable[[], None],
) -> None:
    def do_delete() -> None:
        pm.delete_profile(name)
        log(get_string("deleted_profile", name=name))
        refresh()

    open_confirm_dialog(page, name, do_delete)


def edit_profile(
    page: ft.Page,
    name: str,
    pm: IProfileManager,
    bl: IBrowserLauncher,
    ps: IProxyService,
    log: Callable[[str], None],
    refresh: Callable[[], None],
    proxy_names: list[str] | None = None,
    pool_names: list[str] | None = None,
    all_bookmarks: list[Bookmark] | None = None,
    import_cookies_file=None,
    export_cookies_file=None,
) -> None:
    profile = pm.profiles.get(name)
    if not profile:
        return
    original = profile.name

    def on_save(
        new_name: str,
        new_proxy: str,
        new_os: str,
        new_search: str,
        new_pool: str,
        new_bookmarks: list[str],
        new_tags: list[str],
        new_notes: str = "",
        new_engine: str = "chromium",
    ) -> str | None:
        if new_name != original and bl.is_running(original):
            return "Stop the browser before renaming"
        if not pm.update_profile(
            original, new_name, new_proxy, new_os, new_search, new_pool,
            new_bookmarks, new_tags,
            new_notes=new_notes, new_engine=new_engine,
        ):
            return get_string("update_failed")
        log(get_string("updated_profile", old=original, new=new_name))
        refresh()
        return None

    open_profile_dialog(
        page,
        ps,
        on_save,
        profile,
        proxy_names=proxy_names,
        pool_names=pool_names,
        all_bookmarks=all_bookmarks,
        on_import_cookies_file=(
            (lambda: import_cookies_file(original))
            if import_cookies_file is not None
            else None
        ),
        on_export_cookies_file=(
            (lambda: export_cookies_file(original))
            if export_cookies_file is not None
            else None
        ),
    )


def add_profile(
    page: ft.Page,
    pm: IProfileManager,
    ps: IProxyService,
    log: Callable[[str], None],
    refresh: Callable[[], None],
    proxy_names: list[str] | None = None,
    pool_names: list[str] | None = None,
    all_bookmarks: list[Bookmark] | None = None,
    on_bulk: Callable[[], None] | None = None,
) -> None:
    def on_save(
        name: str,
        proxy: str,
        os_type: str,
        search: str,
        pool: str,
        bookmarks: list[str],
        tags: list[str],
        notes: str = "",
        engine: str = "chromium",
    ) -> str | None:
        if not pm.add_profile(
            name, proxy, os_type, search, pool, bookmarks, tags,
            notes=notes, engine=engine,
        ):
            return get_string("profile_exists")
        log(get_string("created_profile", name=name))
        refresh()
        return None

    open_profile_dialog(
        page,
        ps,
        on_save,
        proxy_names=proxy_names,
        pool_names=pool_names,
        all_bookmarks=all_bookmarks,
        on_bulk=on_bulk,
    )


def bulk_create_profiles(
    page: ft.Page,
    pm: IProfileManager,
    log: Callable[[str], None],
    refresh: Callable[[], None],
) -> None:
    from ...services.profile.bulk import bulk_create, parse_names

    def on_create(
        names_text: str,
        os_type: str,
        tags_text: str,
        _: list[str],
    ) -> str | None:
        names = parse_names(names_text)
        tags = [t.strip() for t in tags_text.split(",") if t.strip()]
        result = bulk_create(
            pm, names, os_type=os_type, tags=tags or None
        )
        created = len(result["created"])
        skipped = len(result["skipped"])
        log(f"bulk create: created {created}, skipped {skipped}")
        refresh()
        return None

    open_bulk_dialog(page, on_create)
