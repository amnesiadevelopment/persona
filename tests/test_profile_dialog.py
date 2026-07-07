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


def _find_by_text(page, needle):
    for c in _walk(page.shown):
        if getattr(c, "value", None) and needle in str(c.value):
            return c
    return None


def test_firefox_hides_dropdown_shows_locked_field():
    # For a Firefox profile the live dropdown must be HIDDEN (it can still be
    # opened even when disabled on this Flet) and a static "DuckDuckGo (fixed)"
    # field shown instead — nothing to open, no misleading live picker.
    prof = Profile(name="FF", engine="firefox", resolution="auto")
    page = _open(prof)
    dd = _find_dropdown(page, "Default search engine")
    assert dd is not None
    assert dd.visible is False                     # the openable dropdown is gone
    locked = _find_by_text(page, "fixed for Firefox")
    assert locked is not None                      # the static locked field shows


def test_chromium_shows_live_dropdown():
    # Chromium DOES have a per-profile search engine, so its dropdown is visible
    # and keeps the profile's chosen value.
    prof = Profile(name="CH", engine="chromium", search_engine="google",
                   resolution="auto")
    page = _open(prof)
    dd = _find_dropdown(page, "Default search engine")
    assert dd is not None
    assert dd.visible is not False                 # visible (None or True)
    assert dd.value == "google"


def test_switching_engine_to_firefox_hides_dropdown():
    # The real bug: a NEW profile opens on chromium (dropdown live). When the
    # user switches the engine to Firefox, the dropdown must be hidden right then
    # and the locked field shown. Simulate the on_change to prove it's wired.
    page = _open(None)  # fresh create dialog, defaults to chromium
    engine_dd = _find_dropdown(page, "Engine")
    search_dd = _find_dropdown(page, "Default search engine")
    assert engine_dd is not None and search_dd is not None
    assert search_dd.visible is not False          # chromium: dropdown shown
    engine_dd.value = "firefox"
    assert engine_dd.on_change is not None
    engine_dd.on_change(None)                       # the event Flet fires on pick
    assert search_dd.visible is False              # dropdown hidden for firefox
    # switching back to chromium shows it again
    engine_dd.value = "chromium"
    engine_dd.on_change(None)
    assert search_dd.visible is True
