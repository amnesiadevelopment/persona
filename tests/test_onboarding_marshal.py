from types import SimpleNamespace

from src.ui.components.onboarding import Onboarding


class FakePage:
    def __init__(self):
        self.update_calls = 0

    def update(self):
        self.update_calls += 1

    def show_dialog(self, dlg):
        pass

    def pop_dialog(self):
        pass


def _make(on_ui, page):
    cbs = {}

    def start_engine(progress, done):
        cbs["progress"] = progress
        cbs["done"] = done

    ob = Onboarding(
        page,
        on_finish=lambda: None,
        start_engine=start_engine,
        on_ui=on_ui,
    )
    return ob, cbs


def test_progress_routes_through_marshal_not_page_update():
    page = FakePage()
    marshaled = []
    ob, cbs = _make(marshaled.append, page)
    ob._begin_engine()

    cbs["progress"](50, 100)
    # nothing touched the page or controls directly from the worker
    assert page.update_calls == 0
    assert ob._bar.value is None
    assert len(marshaled) == 1

    marshaled[0]()
    assert page.update_calls == 1
    assert ob._bar.value == 0.5
    assert ob._pct.value == "50%"


def test_done_routes_through_marshal_not_page_update():
    page = FakePage()
    marshaled = []
    ob, cbs = _make(marshaled.append, page)
    ob._begin_engine()

    cbs["done"](True)
    assert page.update_calls == 0
    assert len(marshaled) == 1

    marshaled[0]()
    assert page.update_calls == 1
    assert ob._bar.value == 1.0
    assert ob._pct.value == "done"


def test_without_marshal_runs_inline():
    page = FakePage()
    cbs = {}

    def start_engine(progress, done):
        cbs["done"] = done

    ob = Onboarding(page, on_finish=lambda: None, start_engine=start_engine)
    ob._begin_engine()
    cbs["done"](True)
    assert page.update_calls == 1
    assert ob._pct.value == "done"


def test_show_onboarding_passes_app_ui_marshal(monkeypatch):
    import src.ui.app as app_mod

    captured = {}

    class FakeOnboarding:
        def __init__(self, page, on_finish, start_engine,
                     engine_already_installed=False, on_ui=None):
            captured["on_ui"] = on_ui

        def open(self):
            pass

    monkeypatch.setattr(app_mod, "Onboarding", FakeOnboarding)
    monkeypatch.setattr(app_mod.engine, "is_installed", lambda: True)

    stub = SimpleNamespace(page=object())
    stub._ui = lambda fn: fn()
    app_mod.App._show_onboarding(stub)
    assert captured["on_ui"] is stub._ui
