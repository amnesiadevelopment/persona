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


def _open(profile, on_save=lambda *a: None, cert_names=None):
    # proxy_service is only used to type the parameter; the dialog builds
    # entirely from the passed lists, so a bare object stands in for it.
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=on_save,
        profile=profile,
        proxy_names=["p1"],
        pool_names=["pool1"],
        all_bookmarks=[Bookmark("browserleaks", "https://browserleaks.com/")],
        cert_names=cert_names if cert_names is not None else ["admin", "staging"],
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


def _label_text_of(control):
    """The label string a `labeled()` column shows above its field — either a
    bare Text or the Text inside a Row(Icon, Text)."""
    val = getattr(control, "value", None)
    if isinstance(val, str):
        return val
    kids = getattr(control, "controls", None) or []
    for k in kids:
        v = getattr(k, "value", None)
        if isinstance(v, str):
            return v
    return None


def _find_dropdown(page, label):
    # The label now sits ABOVE the field (via labeled()), so find the column
    # whose first child's text matches `label` and return the Dropdown in it.
    for col in _walk(page.shown):
        controls = getattr(col, "controls", None)
        if not controls or len(controls) < 2:
            continue
        if _label_text_of(controls[0]) == label:
            for c in controls:
                if isinstance(c, ft.Dropdown):
                    return c
    # Fallback: legacy floating label (kept for any dialog not yet migrated).
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


_NONE_CERT = "(none)"


def test_certificate_dropdown_lists_available_certs():
    page = _open(None)
    dd = _find_dropdown(page, "Certificate (mTLS)")
    assert dd is not None
    opts = [o.key for o in dd.options]
    assert opts == [_NONE_CERT, "admin", "staging"]
    assert dd.value == _NONE_CERT  # a fresh profile has none


def test_edit_dialog_prefills_assigned_certificate():
    prof = Profile(name="P", engine="chromium", certificate="admin")
    page = _open(prof)
    dd = _find_dropdown(page, "Certificate (mTLS)")
    assert dd.value == "admin"


def _set_name(page, value):
    # Profile name is the first TextField in the dialog (IDENTITY section).
    for c in _walk(page.shown):
        if isinstance(c, ft.TextField):
            c.value = value
            return


def _click_create(page):
    btn = next(
        c for c in _walk(page.shown)
        if isinstance(c, ft.Button)
        and getattr(c, "content", None) in ("[ create ]", "[ save ]")
    )
    btn.on_click(None)


def test_save_passes_selected_certificate():
    captured = {}
    page = _open(None, on_save=lambda *a: captured.setdefault("args", a) or None)
    _set_name(page, "newp")
    _find_dropdown(page, "Certificate (mTLS)").value = "staging"
    _click_create(page)
    # certificate is the 11th positional arg
    assert captured["args"][10] == "staging"


def test_save_none_certificate_passes_empty():
    captured = {}
    page = _open(None, on_save=lambda *a: captured.setdefault("args", a) or None)
    _set_name(page, "newp")
    _click_create(page)
    assert captured["args"][10] == ""


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
    # #131: 4K (3840x2160) is not offered — the Firefox engine's launch hangs at
    # that spoofed size. The remaining presets — 2K and the common desktop sizes —
    # stay.
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


_BOOKMARKS = [
    Bookmark("browserleaks", "https://browserleaks.com/"),
    Bookmark("iphey", "https://iphey.com/"),
    Bookmark("mysite", "https://example.com/"),
]


def _open_with_bookmarks(profile, on_save=lambda *a: None):
    page = _FakePage()
    open_profile_dialog(
        page,
        object(),
        on_save=on_save,
        profile=profile,
        proxy_names=["p1"],
        pool_names=["pool1"],
        all_bookmarks=_BOOKMARKS,
    )
    return page


def _bookmark_chips(page):
    """Map bookmark name -> its toggle chip container. Bookmarks are now chips
    (a clickable Container whose text matches a bookmark name); a selected chip
    is drawn in the accent colour."""
    names = {b.name for b in _BOOKMARKS}
    chips = {}
    for c in _walk(page.shown):
        if not getattr(c, "on_click", None):
            continue
        for inner in _walk(c):
            v = getattr(inner, "value", None)
            if isinstance(v, str) and v in names:
                chips[v] = c
                break
    return chips


def _chip_selected(chip):
    """A chip is selected when its inner label Text is drawn in the accent
    colour (set by _chip_content)."""
    from src.ui.theme.colors import COLORS

    for inner in _walk(chip):
        v = getattr(inner, "value", None)
        if isinstance(v, str):
            return getattr(inner, "color", None) == COLORS["accent"]
    return False


class _BookmarkChecks:
    """Adapts the chip UI to the old `checks[name].value` interface the tests
    were written against: reading `.value` reports selection, setting it toggles
    the chip via its click handler."""

    def __init__(self, page):
        self._chips = _bookmark_chips(page)

    def __contains__(self, name):
        return name in self._chips

    def __iter__(self):
        return iter(self._chips)

    def keys(self):
        return self._chips.keys()

    def __getitem__(self, name):
        chip = self._chips[name]
        return _ChipView(chip)

    def values(self):
        return [_ChipView(c) for c in self._chips.values()]


class _ChipView:
    def __init__(self, chip):
        self._chip = chip

    @property
    def value(self):
        return _chip_selected(self._chip)

    @value.setter
    def value(self, want):
        if _chip_selected(self._chip) != want:
            self._chip.on_click(None)


def _bookmark_checks(page):
    return _BookmarkChecks(page)


def test_create_dialog_no_bookmarks_prechecked():
    # #155: on CREATE nothing is pre-checked — the user picks what they want.
    page = _open_with_bookmarks(None)
    checks = _bookmark_checks(page)
    assert set(checks) == {"browserleaks", "iphey", "mysite"}
    assert all(cb.value is False for cb in checks.values())


def test_edit_dialog_shows_saved_selection():
    prof = Profile(name="P1", bookmarks=["iphey"])
    checks = _bookmark_checks(_open_with_bookmarks(prof))
    assert checks["iphey"].value is True
    assert checks["browserleaks"].value is False
    assert checks["mysite"].value is False


def test_edit_dialog_shows_explicit_empty_selection():
    # #147: [] is an intentional empty selection, shown as-is.
    prof = Profile(name="P1", bookmarks=[])
    checks = _bookmark_checks(_open_with_bookmarks(prof))
    assert all(cb.value is False for cb in checks.values())


def test_edit_unconfigured_profile_prechecks_defaults():
    # bookmarks=None = never configured: the profile opens with the stock
    # defaults, so the editor pre-checks them to reflect that.
    prof = Profile(name="P1", bookmarks=None)
    checks = _bookmark_checks(_open_with_bookmarks(prof))
    assert checks["browserleaks"].value is True     # in DEFAULT_BOOKMARKS
    assert checks["iphey"].value is True            # in DEFAULT_BOOKMARKS
    assert checks["mysite"].value is False          # not a default


def test_create_with_nothing_checked_saves_empty_selection():
    # A fresh create with no boxes ticked must save an EXPLICIT [] (empty
    # toolbar), not None — otherwise the defaults would resurrect on launch.
    saved = {}

    def on_save(name, proxy, os_type, search, pool, bookmarks, tags, notes,
                engine, resolution, certificate=""):
        saved["bookmarks"] = bookmarks
        return None

    page = _open_with_bookmarks(None, on_save=on_save)
    _set_name(page, "fresh")
    create_btn = next(
        c for c in _walk(page.shown)
        if isinstance(c, ft.Button) and getattr(c, "content", None) == "[ create ]"
    )
    create_btn.on_click(None)
    assert saved["bookmarks"] == []


def test_firefox_engine_restricts_os_to_windows():
    # #211: stealth-Firefox reports Windows platform regardless of os_type, so a
    # macOS/Linux Firefox profile is an inconsistent lie. Picking the Firefox
    # engine forces the OS to windows and drops every non-windows option, so no
    # inconsistent profile can be created.
    page = _open(None)  # fresh create, defaults to chromium/windows
    os_dd = _find_dropdown(page, "Operating system")
    engine_dd = _find_dropdown(page, "Engine")
    assert os_dd is not None and engine_dd is not None
    # Pick a non-windows desktop OS first, then switch engine to firefox.
    os_dd.value = "macos"
    os_dd.on_select(None)
    engine_dd.value = "firefox"
    engine_dd.on_select(None)
    assert os_dd.value == "windows"
    assert {o.key for o in os_dd.options} == {"windows"}
    # Switching back to chromium restores the full OS choice.
    engine_dd.value = "chromium"
    engine_dd.on_select(None)
    keys = {o.key for o in os_dd.options}
    assert {"windows", "macos", "linux", "android", "ios"} <= keys


def test_selecting_nonwindows_os_forces_chromium_off_firefox():
    # #211 (the other direction): if a Firefox profile is somehow on a
    # non-windows OS and the user picks macOS/Linux, the engine flips to
    # chromium — the only engine that honors a non-windows platform.
    page = _open(None)
    os_dd = _find_dropdown(page, "Operating system")
    engine_dd = _find_dropdown(page, "Engine")
    engine_dd.value = "firefox"
    engine_dd.on_select(None)
    assert os_dd.value == "windows"  # firefox pinned it to windows
    # Now force a linux pick; the restriction re-opens the OS list, but a
    # non-windows desktop choice must knock the engine back to chromium.
    os_dd.options = [ft.dropdown.Option("windows"), ft.dropdown.Option("linux")]
    os_dd.value = "linux"
    os_dd.on_select(None)
    assert engine_dd.value == "chromium"


def test_edit_firefox_profile_keeps_windows_os():
    # An existing Firefox profile (engine locked on edit) shows windows and only
    # windows in the OS list — consistent with what the engine actually spoofs.
    prof = Profile(name="FFwin", engine="firefox", os_type="windows",
                   resolution="auto")
    page = _open(prof)
    os_dd = _find_dropdown(page, "Operating system")
    assert os_dd.value == "windows"
    assert {o.key for o in os_dd.options} == {"windows"}


def test_selecting_mobile_os_forces_chromium_and_hides_resolution():
    page = _open(None)
    os_dd = _find_dropdown(page, "Operating system")
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
