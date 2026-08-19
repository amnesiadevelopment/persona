import threading
from collections.abc import Callable

import flet as ft

from ...core.strings import get_string
from ...interfaces.protocols import IBrowserLauncher, IProfileManager
from ..dialogs import open_confirm_dialog
from ..state import AppState
from .browser import launch_or_stop


def bulk_delete_profiles(
    page: ft.Page,
    names: list[str],
    pm: IProfileManager,
    log: Callable[[str], None],
    refresh: Callable[[], None],
    on_done: Callable[[], None],
    ui: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    if not names:
        return

    def do_bulk_delete() -> None:
        # Delete on a daemon thread, NOT inline on the confirm dialog's UI-thread
        # callback: each delete_profile blocks on terminate (proc.wait up to ~3s
        # per running profile), so a bulk delete of several running profiles used
        # to freeze the whole app for N×2-3s — the user thought it crashed and
        # force-quit mid-delete (audit5 #6). The dialog closes immediately; the
        # deletions run behind it and the list refreshes as each completes.
        def work() -> None:
            for name in names:
                # Surface a failed park rather than reporting every profile as
                # deleted: delete_profile returns False when the data dir could
                # not be moved, leaving the profile fully intact.
                ok = pm.delete_profile(name)
                key = "deleted_profile" if ok else "delete_profile_failed"
                # log/refresh must land on the UI thread when we're off it.
                _post(lambda n=name, k=key: log(get_string(k, name=n)))
                _post(refresh)
            _post(on_done)
            _post(refresh)

        threading.Thread(target=work, daemon=True).start()

    def _post(fn: Callable[[], None]) -> None:
        if ui is not None:
            ui(fn)
        else:
            fn()

    count = len(names)
    open_confirm_dialog(
        page,
        "",
        do_bulk_delete,
        title=f"Delete {count} profile{'s' if count != 1 else ''}?",
        # Truthful now that a delete is recoverable: these move to the trash,
        # they are not destroyed. The panic wipe is the irreversible one.
        body=(
            f"{'They' if count != 1 else 'It'} move"
            f"{'' if count != 1 else 's'} to the trash and can be restored."
        ),
    )


def bulk_launch_profiles(
    names: list[str],
    pm: IProfileManager,
    bl: IBrowserLauncher,
    state: AppState,
    log: Callable[[str], None],
) -> None:
    for name in names:
        if not bl.is_running(name) and not state.is_loading(name):
            launch_or_stop(name, pm, bl, state, log)


def bulk_stop_profiles(
    names: list[str],
    pm: IProfileManager,
    bl: IBrowserLauncher,
    state: AppState,
    log: Callable[[str], None],
) -> None:
    for name in names:
        if bl.is_running(name):
            launch_or_stop(name, pm, bl, state, log)
