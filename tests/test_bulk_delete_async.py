"""audit5 #6: bulk delete of running profiles must not freeze the UI thread.
do_bulk_delete (the confirm-dialog on_confirm) used to run synchronously on the
flet UI thread, each delete blocking on terminate (proc.wait ~2-3s). It now runs
on a daemon thread; the dialog closes immediately and refresh is marshaled back
to the UI thread as each delete completes."""
import threading
import time

from src.ui.actions.bulk import bulk_delete_profiles


class FakePage:
    def __init__(self):
        self.dialog = None

    def show_dialog(self, dlg):
        self.dialog = dlg

    def pop_dialog(self):
        self.dialog = None


def _confirm(page):
    """Invoke the dialog's [delete] button handler like the user would."""
    # open_confirm_dialog builds an AlertDialog whose second action is [delete].
    delete_btn = page.dialog.actions[1]
    delete_btn.on_click(None)


def test_bulk_delete_runs_off_the_ui_thread(monkeypatch):
    deleted = []
    gate = threading.Event()

    class SlowPM:
        def delete_profile(self, name):
            gate.wait(2.0)  # emulate a blocking terminate
            deleted.append(name)

    posted = []

    def ui(fn):
        # record that refresh/log were marshaled, run them inline for the test
        posted.append(fn)
        fn()

    page = FakePage()
    bulk_delete_profiles(
        page, ["a", "b"], SlowPM(),
        log=lambda *_: None,
        refresh=lambda: None,
        on_done=lambda: None,
        ui=ui,
    )

    # the confirm handler must return immediately even though delete blocks
    t0 = time.monotonic()
    _confirm(page)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"confirm blocked the UI thread for {elapsed:.2f}s"
    # dialog closed right away
    assert page.dialog is None

    # nothing deleted yet — the worker is blocked on the gate
    assert deleted == []
    gate.set()
    # let the daemon thread finish
    for _ in range(50):
        if len(deleted) == 2:
            break
        time.sleep(0.05)
    assert set(deleted) == {"a", "b"}
    # refresh/log/on_done were marshaled through ui, not called on the worker raw
    assert posted, "refresh/on_done must be marshaled onto the UI thread"


def test_bulk_delete_empty_names_is_noop():
    page = FakePage()
    called = []
    bulk_delete_profiles(
        page, [], object(),
        log=lambda *_: called.append("log"),
        refresh=lambda: called.append("refresh"),
        on_done=lambda: called.append("done"),
    )
    assert page.dialog is None  # no dialog opened for an empty selection
    assert called == []
