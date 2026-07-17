"""Every dialog must BUILD headlessly without raising, and open with its action
buttons present.

A dialog whose constructor raises (an unsupported ft.* kwarg, a bad ref) makes
the triggering button look dead — the class of regression that killed Create
Profile in 2.2.0. Unit tests of the underlying logic don't catch it; only
actually building the control tree does. This covers the dialogs that had no
build test: pool, bulk, export, ssh_host, confirm. (profile has its own suite in
test_profile_dialog.py.)
"""
import flet as ft

from src.models.bookmark import Bookmark, Pool
from src.models.profile import Profile
from src.services.ssh.store import SSHHost
from src.ui.dialogs.bulk import open_bulk_dialog
from src.ui.dialogs.confirm import open_confirm_dialog
from src.ui.dialogs.export import open_export_dialog
from src.ui.dialogs.pool import open_pool_dialog
from src.ui.dialogs.ssh_host import open_ssh_host_dialog


class _FakePage:
    def __init__(self):
        self.shown = None

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        self.shown = None

    def update(self):
        pass


def _walk(control):
    yield control
    for attr in ("content", "controls", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for c in items:
            if c is not None and hasattr(c, "__dict__"):
                yield from _walk(c)


_BUTTON_TYPES = tuple(
    t for t in (
        getattr(ft, "Button", None),
        getattr(ft, "TextButton", None),
        getattr(ft, "OutlinedButton", None),
        getattr(ft, "ElevatedButton", None),
        getattr(ft, "FilledButton", None),
        getattr(ft, "IconButton", None),
    )
    if t is not None
)


def _has_button(page):
    return any(isinstance(c, _BUTTON_TYPES) for c in _walk(page.shown))


_BOOKMARKS = [
    Bookmark("browserleaks", "https://browserleaks.com/"),
    Bookmark("iphey", "https://iphey.com/"),
]


def test_pool_dialog_create_builds_with_buttons():
    page = _FakePage()
    open_pool_dialog(page, _BOOKMARKS, on_save=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _has_button(page)


def test_pool_dialog_edit_builds_with_saved_selection():
    page = _FakePage()
    pool = Pool(name="mypool", bookmark_names=["iphey"])
    open_pool_dialog(page, _BOOKMARKS, on_save=lambda *a: None, pool=pool)
    assert isinstance(page.shown, ft.AlertDialog)
    # the pool's name landed in a field
    assert any(
        getattr(c, "value", None) == "mypool" for c in _walk(page.shown)
    )


def test_pool_dialog_with_preselected_builds():
    page = _FakePage()
    open_pool_dialog(
        page, _BOOKMARKS, on_save=lambda *a: None, preselected=["browserleaks"]
    )
    assert isinstance(page.shown, ft.AlertDialog)


def test_bulk_dialog_builds_with_buttons():
    page = _FakePage()
    open_bulk_dialog(page, on_create=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _has_button(page)


def test_export_dialog_builds_with_profiles():
    page = _FakePage()
    fp = ft.FilePicker()
    profiles = [Profile(name="A"), Profile(name="B", engine="firefox")]
    open_export_dialog(page, fp, profiles, on_complete=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _has_button(page)


def test_export_dialog_no_profiles_is_a_noop():
    # By design: nothing to export → the dialog doesn't open (no crash, no empty
    # dialog). Guards that the empty-list early return stays a clean no-op.
    page = _FakePage()
    fp = ft.FilePicker()
    open_export_dialog(page, fp, [], on_complete=lambda *a: None)
    assert page.shown is None


def test_ssh_host_dialog_create_builds():
    page = _FakePage()
    open_ssh_host_dialog(page, None, ["p1", "p2"], on_save=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _has_button(page)


def test_ssh_host_dialog_edit_builds_with_values():
    page = _FakePage()
    host = SSHHost(name="box", host="1.2.3.4", port=2222, username="root")
    open_ssh_host_dialog(page, host, ["p1"], on_save=lambda *a: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert any(
        getattr(c, "value", None) == "1.2.3.4" for c in _walk(page.shown)
    )


def test_confirm_dialog_builds():
    page = _FakePage()
    open_confirm_dialog(page, "myprofile", on_confirm=lambda: None)
    assert isinstance(page.shown, ft.AlertDialog)
    assert _has_button(page)


def test_confirm_dialog_custom_title_body_builds():
    page = _FakePage()
    open_confirm_dialog(
        page, "x", on_confirm=lambda: None, title="Delete?", body="Are you sure"
    )
    assert isinstance(page.shown, ft.AlertDialog)
    assert any("sure" in str(getattr(c, "value", "")) for c in _walk(page.shown))
