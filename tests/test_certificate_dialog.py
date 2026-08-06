"""The add/edit certificate dialog builds headlessly and wires save/validation."""
import flet as ft

from src.services.cert.store import Certificate
from src.ui.dialogs.certificate import open_certificate_dialog


class _FakePage:
    def __init__(self):
        self.shown = None
        self.popped = False

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        self.popped = True

    def update(self):
        pass


class _FakePicker:
    """Returns a preset picked file path from pick_files."""

    def __init__(self, path=None):
        self._path = path

    async def pick_files(self, **kwargs):
        if self._path is None:
            return None

        class F:
            path = self._path

        return [F()]


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        v = getattr(control, attr, None)
        if v is None:
            continue
        for child in (v if isinstance(v, list) else [v]):
            if isinstance(child, ft.BaseControl):
                yield from _walk(child)


def _save_button(dlg):
    for a in dlg.actions:
        if getattr(a, "content", None) == "[ save ]":
            return a
    raise AssertionError("no Save button")


def _fields(dlg):
    return [c for c in _walk(dlg) if isinstance(c, ft.TextField)]


def test_add_dialog_builds():
    page = _FakePage()
    open_certificate_dialog(page, None, _FakePicker(), on_save=lambda c: None)
    assert isinstance(page.shown, ft.AlertDialog)


def test_edit_dialog_prefills_name():
    page = _FakePage()
    cert = Certificate(name="admin", p12_path="/v/admin.p12", password="pw")
    open_certificate_dialog(page, cert, _FakePicker(), on_save=lambda c: None)
    names = [f.value for f in _fields(page.shown) if f.value == "admin"]
    assert names == ["admin"]


def test_edit_dialog_prefills_url():
    page = _FakePage()
    cert = Certificate(
        name="admin", p12_path="/v/admin.p12", url="https://admin.example.com/"
    )
    open_certificate_dialog(page, cert, _FakePicker(), on_save=lambda c: None)
    urls = [f.value for f in _fields(page.shown) if f.value == "https://admin.example.com/"]
    assert urls == ["https://admin.example.com/"]


def test_save_captures_url():
    page = _FakePage()
    saved = []
    cert = Certificate(name="admin", p12_path="/v/admin.p12")
    open_certificate_dialog(
        page, cert, _FakePicker(None), on_save=lambda c: saved.append(c) or None
    )
    # The admin URL field is the one whose placeholder mentions an https URL
    # (labels now sit above the field, not on it).
    url_f = [
        f for f in _fields(page.shown)
        if "https://" in (f.hint_text or "")
    ][0]
    url_f.value = "https://admin.example.com/login"
    _save_button(page.shown).on_click(None)
    assert saved[0].url == "https://admin.example.com/login"


def test_fields_have_icons():
    # Each field carries a leading icon above it (labels moved above the field).
    # Guards that the icon wiring survives refactors: a document icon for the
    # certificate, a lock for the password, a link for the admin URL.
    import flet as ft

    page = _FakePage()
    open_certificate_dialog(page, None, _FakePicker(), on_save=lambda c: None)
    icons = {c.icon for c in _walk(page.shown) if isinstance(c, ft.Icon)}
    assert ft.Icons.DESCRIPTION_OUTLINED in icons  # the certificate document
    assert ft.Icons.LOCK_OUTLINE in icons          # password
    assert ft.Icons.LINK in icons                  # admin URL


def test_save_requires_name():
    page = _FakePage()
    saved = []
    open_certificate_dialog(
        page, None, _FakePicker("/x/a.p12"), on_save=lambda c: saved.append(c)
    )
    _save_button(page.shown).on_click(None)  # name empty
    assert saved == []
    assert page.popped is False


def test_save_requires_file():
    page = _FakePage()
    saved = []
    open_certificate_dialog(
        page, None, _FakePicker(None), on_save=lambda c: saved.append(c)
    )
    # set a name but no file chosen
    name_f = _fields(page.shown)[0]
    name_f.value = "admin"
    _save_button(page.shown).on_click(None)
    assert saved == []


def test_edit_without_new_file_keeps_existing_path():
    page = _FakePage()
    saved = []
    cert = Certificate(name="admin", p12_path="/v/admin.p12", password="old")
    open_certificate_dialog(
        page, cert, _FakePicker(None), on_save=lambda c: saved.append(c) or None
    )
    # change password field, keep file
    fields = _fields(page.shown)
    pw = [f for f in fields if f.password][0]
    pw.value = "new"
    _save_button(page.shown).on_click(None)
    assert len(saved) == 1
    assert saved[0].name == "admin"
    assert saved[0].p12_path == "/v/admin.p12"
    assert saved[0].password == "new"
    assert page.popped is True
