"""The create/edit profile dialog must build without raising.

A regression where resolution_dropdown was constructed with an unsupported
`on_change=` keyword made ft.Dropdown.__init__ raise at build time, so the
dialog never opened and the "Create Profile" button appeared dead. Building the
dialog headlessly guards every control's constructor against that class of bug.
"""
import flet as ft

from src.models.bookmark import Bookmark
from src.models.profile import Profile
from src.ui.dialogs.profile import open_profile_dialog


class _FakePage:
    def __init__(self):
        self.shown = None

    def show_dialog(self, dlg):
        self.shown = dlg

    def pop_dialog(self):
        pass

    def update(self):
        pass


def _open(profile):
    # proxy_service is only used to type the parameter; the dialog builds
    # entirely from the passed lists, so a bare object stands in for it.
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=lambda *a: None,
        profile=profile,
        proxy_names=["p1"],
        pool_names=["pool1"],
        all_bookmarks=[Bookmark("browserleaks", "https://browserleaks.com/")],
    )
    return page


def test_create_dialog_builds():
    page = _open(None)
    assert page.shown is not None
    assert isinstance(page.shown, ft.AlertDialog)


def test_edit_dialog_builds():
    prof = Profile(name="P1", engine="firefox", resolution="1920x1080")
    page = _open(prof)
    assert page.shown is not None


def test_edit_dialog_with_custom_resolution_builds():
    prof = Profile(name="P2", engine="chromium", resolution="1234x777")
    page = _open(prof)
    assert page.shown is not None


def test_edit_dialog_with_auto_resolution_builds():
    prof = Profile(name="P3", engine="chromium", resolution="auto")
    page = _open(prof)
    assert page.shown is not None
