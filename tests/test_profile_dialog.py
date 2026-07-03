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


def _walk(control):
    """Yield every control in the dialog tree (depth-first)."""
    yield control
    for attr in ("content", "controls", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        items = child if isinstance(child, list) else [child]
        for c in items:
            if c is not None and hasattr(c, "__dict__"):
                yield from _walk(c)


def _find_dropdown(page, label):
    for c in _walk(page.shown):
        if getattr(c, "label", None) == label:
            return c
    return None


def test_firefox_search_dropdown_is_locked_to_ddg():
    # For a Firefox profile the default-search-engine picker must be VISIBLE but
    # disabled (greyed) and pinned to DuckDuckGo вЂ” Firefox has no per-profile
    # search engine, so the control shows the fixed value instead of misleading
    # the user with a live, changeable picker.
    prof = Profile(name="FF", engine="firefox", resolution="auto")
    page = _open(prof)
    dd = _find_dropdown(page, "Default search engine")
    assert dd is not None
    assert dd.disabled is True
    assert dd.value == "duckduckgo"


def test_chromium_search_dropdown_is_active():
    # Chromium DOES have a per-profile search engine, so its picker stays
    # enabled and keeps the profile's chosen value.
    prof = Profile(name="CH", engine="chromium", search_engine="google",
                   resolution="auto")
    page = _open(prof)
    dd = _find_dropdown(page, "Default search engine")
    assert dd is not None
    assert dd.disabled is False
    assert dd.value == "google"
