import asyncio
import threading
from types import SimpleNamespace

from src.ui.app import App


class FakePage:
    """Stands in for ft.Page: records run_task marshaling and update calls,
    and drives the scheduled coroutine to completion like the session loop
    would."""

    def __init__(self):
        self.run_task_handlers = []
        self.update_calls = 0

    def run_task(self, handler, *args, **kwargs):
        self.run_task_handlers.append(handler)
        return asyncio.run(handler(*args, **kwargs))

    def update(self):
        self.update_calls += 1


class BrokenPage(FakePage):
    def run_task(self, handler, *args, **kwargs):
        raise RuntimeError("session is gone")


class FailingUpdatePage(FakePage):
    def update(self):
        raise RuntimeError("update blew up")


def make_app(page):
    app = App.__new__(App)
    app.page = page
    app.state = SimpleNamespace(_ui_update_lock=threading.Lock())
    return app


# --- _ui ---

def test_ui_without_page_calls_fn_directly():
    app = make_app(None)
    called = []
    app._ui(lambda: called.append(1))
    assert called == [1]


def test_ui_without_page_swallows_fn_error():
    app = make_app(None)

    def boom():
        raise RuntimeError("boom")

    app._ui(boom)  # must not raise


def test_ui_marshals_through_run_task_off_loop():
    page = FakePage()
    app = make_app(page)
    called = []
    app._ui(lambda: called.append(1))
    assert called == [1]
    assert len(page.run_task_handlers) == 1


def test_ui_on_session_loop_calls_fn_inline():
    page = FakePage()
    app = make_app(page)
    called = []

    async def main():
        app._ui(lambda: called.append(1))

    asyncio.run(main())
    assert called == [1]
    assert page.run_task_handlers == []


def test_ui_marshaled_fn_error_is_swallowed():
    page = FakePage()
    app = make_app(page)

    def boom():
        raise RuntimeError("boom")

    app._ui(boom)  # must not raise
    assert len(page.run_task_handlers) == 1


def test_ui_run_task_failure_is_swallowed():
    page = BrokenPage()
    app = make_app(page)
    called = []
    app._ui(lambda: called.append(1))  # must not raise
    assert called == []  # dropped, not run off-thread


def test_ui_from_worker_thread_marshals():
    page = FakePage()
    app = make_app(page)
    called = []
    t = threading.Thread(target=lambda: app._ui(lambda: called.append(1)))
    t.start()
    t.join()
    assert called == [1]
    assert len(page.run_task_handlers) == 1


# --- _safe_update ---

def test_safe_update_marshals_page_update():
    page = FakePage()
    app = make_app(page)
    app._safe_update()
    assert page.update_calls == 1
    assert len(page.run_task_handlers) == 1


def test_safe_update_without_page_is_noop():
    app = make_app(None)
    app._safe_update()  # must not raise


def test_safe_update_swallows_update_error():
    page = FailingUpdatePage()
    app = make_app(page)
    app._safe_update()  # must not raise
    assert len(page.run_task_handlers) == 1


def test_safe_update_holds_ui_update_lock():
    class LockCheckingPage(FakePage):
        def __init__(self, app_ref):
            super().__init__()
            self.app_ref = app_ref
            self.lock_held = None

        def update(self):
            super().update()
            self.lock_held = not self.app_ref.state._ui_update_lock.acquire(
                blocking=False
            )

    app = make_app(None)
    page = LockCheckingPage(app)
    app.page = page
    app._safe_update()
    assert page.lock_held is True


# --- _apply_update (self-update refusal recovery) ---

def test_apply_update_clears_staged_when_installer_deleted(monkeypatch, tmp_path):
    # When apply_and_restart refuses a corrupt installer it DELETES the staged
    # file and returns. _apply_update must then clear _update_staged so the
    # periodic checker (gated on `not self._update_staged`) can re-download.
    from src.ui import app as app_mod

    app = make_app(None)
    app._update_staged = str(tmp_path / "gone.exe")   # never created → "deleted"
    app._app_update_status = "ready"
    app._refresh_sidebar = lambda: None
    app._log = lambda *_: None
    monkeypatch.setattr(app_mod.app_update, "apply_and_restart", lambda *a, **k: False)

    app._apply_update(app._update_staged)
    assert app._update_staged == ""          # cleared → re-download possible
    assert app._app_update_status == ""


def test_apply_update_keeps_staged_when_file_still_present(monkeypatch, tmp_path):
    # If the installer is still on disk (relaunch failed, not a verify refusal),
    # keep the "ready" state so the restart button still works.
    from src.ui import app as app_mod

    staged = tmp_path / "setup.exe"
    staged.write_bytes(b"MZ")
    app = make_app(None)
    app._update_staged = str(staged)
    app._app_update_status = "ready"
    app._refresh_sidebar = lambda: None
    app._log = lambda *_: None
    monkeypatch.setattr(app_mod.app_update, "apply_and_restart", lambda *a, **k: False)

    app._apply_update(str(staged))
    assert app._app_update_status == "ready"   # still offer restart
