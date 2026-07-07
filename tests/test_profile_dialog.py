"""The create/edit profile dialog must build without raising.

A regression where resolution_dropdown was constructed with an unsupported
`on_change=` keyword made ft.Dropdown.__init__ raise at build time, so the
dialog never opened and the "Create Profile" button appeared dead. Building the
dialog headlessly guards every control's constructor against that class of bug.
"""
import dataclasses

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


def _find_row_containing(page, child):
    for c in _walk(page.shown):
        if child in (getattr(c, "controls", None) or []):
            return c
    return None


def test_dropdown_dispatches_on_select():
    # ft.Dropdown dispatches selection via on_select; assigning .on_change is
    # silently accepted as a plain attribute and never fires. Fail loudly if
    # Flet renames the field again.
    assert "on_select" in {f.name for f in dataclasses.fields(ft.Dropdown)}


def _find_search_locked(page, search_dd):
    # search_locked is the Container sibling of the search dropdown's Row
    # inside the search section Column.
    row = _find_row_containing(page, search_dd)
    section = _find_row_containing(page, row)
    return next(c for c in section.controls if isinstance(c, ft.Container))


def test_firefox_hides_dropdown_shows_locked_field():
    # For a Firefox profile the live dropdown must be HIDDEN (it can still be
    # opened even when disabled on this Flet) and a static "DuckDuckGo (fixed)"
    # field shown instead — nothing to open, no misleading live picker.
    prof = Profile(name="FF", engine="firefox", resolution="auto")
    page = _open(prof)
    dd = _find_dropdown(page, "Default search engine")
    assert dd is not None
    assert dd.visible is False                     # the openable dropdown is gone
    assert _find_by_text(page, "fixed for Firefox") is not None
    assert _find_search_locked(page, dd).visible is True


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
    # and the locked field shown. Simulate the on_select to prove it's wired.
    page = _open(None)  # fresh create dialog, defaults to chromium
    engine_dd = _find_dropdown(page, "Engine")
    search_dd = _find_dropdown(page, "Default search engine")
    assert engine_dd is not None and search_dd is not None
    locked = _find_search_locked(page, search_dd)
    assert search_dd.visible is not False          # chromium: dropdown shown
    assert locked.visible is False
    engine_dd.value = "firefox"
    assert engine_dd.on_select is not None
    engine_dd.on_select(None)                       # the event Flet fires on pick
    assert search_dd.visible is False              # dropdown hidden for firefox
    assert locked.visible is True                  # static locked field shown
    # switching back to chromium shows it again
    engine_dd.value = "chromium"
    engine_dd.on_select(None)
    assert search_dd.visible is True
    assert locked.visible is False


def test_resolution_picker_omits_4k_keeps_common_sizes():
    # #131: 4K (3840x2160) is not offered — on the Firefox engine it can't
    # render readable AND stay a plausible physical size on a HiDPI host (the
    # devicePixelRatio follows the render scale, so 3840 * 1.5 = 5760, an
    # impossible panel), and the engine's launch hangs at that size there. The
    # remaining presets — 2K and the common desktop sizes — stay.
    page = _open(None)
    res_dd = _find_dropdown(page, "Screen resolution")
    keys = {o.key for o in res_dd.options}
    assert "3840x2160" not in keys
    assert "2560x1440" in keys
    assert "1920x1080" in keys
    assert "auto" in keys and "custom" in keys


def test_selecting_custom_resolution_reveals_width_height():
    page = _open(None)
    res_dd = _find_dropdown(page, "Screen resolution")
    width_field = _find_dropdown(page, "width")
    custom_row = _find_row_containing(page, width_field)
    assert res_dd is not None and custom_row is not None
    assert custom_row.visible is False
    res_dd.value = "custom"
    assert res_dd.on_select is not None
    res_dd.on_select(None)
    assert custom_row.visible is True
    res_dd.value = "auto"
    res_dd.on_select(None)
    assert custom_row.visible is False


def test_selecting_mobile_os_forces_chromium_and_hides_resolution():
    page = _open(None)
    os_dd = _find_dropdown(page, "Operating System")
    engine_dd = _find_dropdown(page, "Engine")
    width_field = _find_dropdown(page, "width")
    custom_row = _find_row_containing(page, width_field)
    resolution_section = _find_row_containing(page, custom_row)
    assert os_dd is not None and engine_dd is not None
    assert resolution_section is not None
    engine_dd.value = "firefox"
    os_dd.value = "android"
    assert os_dd.on_select is not None
    os_dd.on_select(None)
    assert engine_dd.value == "chromium"
    assert "firefox" not in {o.key for o in engine_dd.options}
    assert resolution_section.visible is False
    # back to a desktop OS: full engine choice and resolution picker return
    os_dd.value = "windows"
    os_dd.on_select(None)
    assert "firefox" in {o.key for o in engine_dd.options}
    assert resolution_section.visible is True
