from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IBrowserLauncher, IProfileManager, IProxyService
from ...models.bookmark import Bookmark
from ...services.profile.cert_assignment import CertDirective
from ...services.profile.coherence import IncoherentProfile
from ...services.profile.pool_assignment import PoolDirective
from ...services.profile.proxy_assignment import ProxyDirective
from ..dialogs import open_bulk_dialog, open_confirm_dialog, open_profile_dialog


def delete_profile(
    page: ft.Page,
    name: str,
    pm: IProfileManager,
    log: Callable[[str], None],
    refresh: Callable[[], None],
) -> None:
    def do_delete() -> None:
        # delete_profile returns False when the data dir could not be parked
        # (disk full, permissions, cross-device): the profile is then left
        # completely intact. Logging "Deleted" regardless told the operator an
        # identity was gone while it was still on disk — the claim-outlives-the-
        # code defect this ticket exists to close, on the safety-critical side.
        ok = pm.delete_profile(name)
        log(
            get_string("deleted_profile", name=name)
            if ok
            else get_string("delete_profile_failed", name=name)
        )
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
    cert_names: list[str] | None = None,
    import_cookies_file=None,
    export_cookies_file=None,
    on_add_proxy: Callable[[], None] | None = None,
) -> None:
    profile = pm.profiles.get(name)
    if not profile:
        return
    original = profile.name

    def on_save(
        new_name: str,
        new_proxy: str | ProxyDirective,
        new_os: str,
        new_search: str,
        new_pool: str | PoolDirective,
        new_bookmarks: list[str],
        new_tags: list[str],
        new_notes: str = "",
        new_engine: str = "chromium",
        new_resolution: str = "auto",
        new_certificate: str | CertDirective = "",
    ) -> str | None:
        if new_name != original and bl.is_running(original):
            return "Stop the browser before renaming"
        # The dialog narrows its dropdowns so an incoherent os_type/engine pair
        # cannot be picked, so this should be unreachable from the UI. It is
        # caught anyway because the rule now lives in the model and RAISES:
        # surfacing the reason in the dialog's own error channel beats an
        # unhandled exception escaping into the Flet event loop if the
        # narrowing is ever bypassed or regressed.
        try:
            saved = pm.update_profile(
                original, new_name, new_proxy, new_os, new_search, new_pool,
                new_bookmarks, new_tags,
                new_notes=new_notes, new_engine=new_engine,
                new_resolution=new_resolution, new_certificate=new_certificate,
            )
        except IncoherentProfile as e:
            return str(e)
        if not saved:
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
        cert_names=cert_names,
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
        on_add_proxy=on_add_proxy,
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
    cert_names: list[str] | None = None,
    on_bulk: Callable[[], None] | None = None,
    on_add_proxy: Callable[[], None] | None = None,
) -> None:
    def on_save(
        name: str,
        proxy: str | ProxyDirective,
        os_type: str,
        search: str,
        pool: str | PoolDirective,
        bookmarks: list[str],
        tags: list[str],
        notes: str = "",
        engine: str = "chromium",
        resolution: str = "auto",
        certificate: str | CertDirective = "",
    ) -> str | None:
        # Unreachable from the dialog (its dropdowns are narrowed), but the model
        # RAISES now — catch it so a bypassed/regressed narrowing surfaces the
        # reason in the dialog instead of escaping into the Flet event loop.
        try:
            created = pm.add_profile(
                name, proxy, os_type, search, pool, bookmarks, tags,
                notes=notes, engine=engine, resolution=resolution,
                certificate=certificate,
            )
        except IncoherentProfile as e:
            return str(e)
        if not created:
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
        cert_names=cert_names,
        on_bulk=on_bulk,
        on_add_proxy=on_add_proxy,
    )


#: How many refusals the dialog itself lists before deferring to the Activity
#: Log. The dialog is 460px wide with a scrolling body; a paste that refuses
#: 200 names would otherwise push the [ create ] button and the paste field off
#: the operator's screen — turning "you can fix it in place" back into "you
#: cannot". Every refusal is logged per name regardless, so nothing is lost.
_INLINE_REFUSAL_LIMIT = 12


def bulk_create_profiles(
    page: ft.Page,
    pm: IProfileManager,
    log: Callable[[str], None],
    refresh: Callable[[], None],
) -> None:
    from ...services.profile.bulk import bulk_create, duplicate_names, parse_names

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
        created = result["created"]
        skipped = result["skipped"]
        reasons = result.get("reasons", {})

        # THE DURABLE RECORD (AC3), on the model of bulk delete
        # (actions/bulk.py:36-40): one line per name, by name, with the
        # explained reason — so it survives the dialog being dismissed. The
        # aggregate line is kept as the batch's header, not as the whole story.
        log(f"bulk create: created {len(created)}, skipped {len(skipped)}")
        for name in created:
            log(get_string("created_profile", name=name))
        for name in skipped:
            log(
                get_string(
                    "bulk_create_not_created",
                    name=name,
                    # A skipped name always has a reason (bulk_create writes
                    # both together), but the lane must not go silent if a
                    # future caller does not.
                    reason=reasons.get(name, get_string("error")),
                )
            )
        refresh()

        if not skipped:
            return None

        # THE INLINE REPORT (AC2). Returning a message instead of None is what
        # keeps the dialog OPEN (dialogs/bulk.py:53-56) so the operator can fix
        # the paste in place. The `str | None` contract already carries this —
        # the single-create lane uses it the same way
        # (actions/profile.py on_save).
        n_skipped = len(skipped)
        if created:
            head = get_string(
                "bulk_create_partial",
                created=len(created),
                plural="" if len(created) == 1 else "s",
                skipped=n_skipped,
                skipped_plural="" if n_skipped == 1 else "s",
            )
        else:
            head = get_string(
                "bulk_create_none",
                skipped=n_skipped,
                skipped_plural="" if n_skipped == 1 else "s",
            )
        lines = [head] + [
            get_string(
                "bulk_create_refusal_line",
                name=name,
                reason=reasons.get(name, get_string("error")),
            )
            for name in skipped[:_INLINE_REFUSAL_LIMIT]
        ]
        # A 200-name paste that refuses every name must not render 200 lines
        # into a dialog. The Activity Log has ALL of them (logged above,
        # unconditionally), so the overflow is pointed at rather than dropped.
        if n_skipped > _INLINE_REFUSAL_LIMIT:
            lines.append(
                get_string(
                    "bulk_create_more",
                    count=n_skipped - _INLINE_REFUSAL_LIMIT,
                    plural="" if n_skipped - _INLINE_REFUSAL_LIMIT == 1 else "s",
                )
            )
        # Account for the rows that reached neither list, so the arithmetic the
        # operator can do on screen adds up.
        repeats = duplicate_names(names_text)
        if repeats:
            lines.append(
                get_string(
                    "bulk_create_repeats",
                    count=len(repeats),
                    plural="" if len(repeats) == 1 else "s",
                    was="was" if len(repeats) == 1 else "were",
                    names=", ".join(repeats),
                )
            )
        return "\n".join(lines)

    open_bulk_dialog(page, on_create)
