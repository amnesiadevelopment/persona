"""Deleting a certificate must ask for confirmation first — a cert is not
trivially replaceable (a profile assigned it silently loses its client auth),
so `[ x ]` opens a confirm dialog and only the confirm actually removes it."""
import types

from src.ui.app import App


class _FakePage:
    def __init__(self):
        self.shown = None

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        pass

    def update(self):
        pass


class _FakeStore:
    def __init__(self):
        self.removed = []

    def remove(self, name):
        self.removed.append(name)


def _confirm_button(dlg):
    for a in getattr(dlg, "actions", []):
        c = getattr(a, "content", None)
        if isinstance(c, str) and "delete" in c.lower():
            return a
    raise AssertionError("no delete/confirm button in the dialog")


def _make():
    page = _FakePage()
    store = _FakeStore()
    app = types.SimpleNamespace(
        page=page,
        cert_store=store,
        _render_active_page=lambda: None,
        _safe_update=lambda: None,
    )
    return app, page, store


def test_delete_certificate_asks_before_removing():
    app, page, store = _make()
    App._delete_certificate(app, "testcert")
    # the click opened a confirm dialog and removed nothing yet
    assert page.shown is not None
    assert store.removed == []


def test_delete_certificate_removes_only_after_confirm():
    app, page, store = _make()
    App._delete_certificate(app, "testcert")
    _confirm_button(page.shown).on_click(None)
    assert store.removed == ["testcert"]
