"""Panic wipe: wipe_all_profiles() deletes every profile AND its data dir in one
pass, irreversibly. Gated in the UI behind a typed DELETE confirmation.
"""
import os

import pytest

from src.services.profile.manager import ProfileManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


def test_wipe_removes_all_profiles_and_returns_count(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")
    # materialise a data dir for one profile
    os.makedirs(mgr._data_path("a"), exist_ok=True)
    with open(os.path.join(mgr._data_path("a"), "cookies.sqlite"), "w") as f:
        f.write("x")

    n = mgr.wipe_all_profiles()

    assert n == 3
    assert mgr.list_profiles() == []
    assert not os.path.exists(mgr._data_path("a")), "data dir must be deleted"


def test_wipe_persists_empty_profiles_file(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.wipe_all_profiles()
    # a fresh manager (reloading from disk) sees no profiles
    fresh = ProfileManager()
    assert fresh.list_profiles() == []


def test_wipe_on_empty_is_a_noop_returning_zero(mgr):
    assert mgr.wipe_all_profiles() == 0


def _capture_wipe_dialog():
    """Open the wipe dialog against fake page/controls, returning the field, the
    confirm button, and a list recording every page-level update() call."""
    from types import SimpleNamespace

    from src.ui.dialogs import wipe_confirm

    page_updates = []
    shown = {}

    page = SimpleNamespace(
        update=lambda: page_updates.append(1),
        show_dialog=lambda d: shown.setdefault("dlg", d),
        pop_dialog=lambda: shown.__setitem__("popped", True),
    )
    wipe_confirm.open_wipe_confirm_dialog(page, 3, on_confirm=lambda: None)
    dlg = shown["dlg"]
    field = dlg.content.controls[-1]
    confirm_btn = dlg.actions[-1]
    return field, confirm_btn, page_updates


def test_wipe_confirm_typing_delete_enables_button():
    field, confirm_btn, _ = _capture_wipe_dialog()
    assert confirm_btn.disabled is True
    field.value = "DELETE"
    field.on_change(None)
    assert confirm_btn.disabled is False


def test_wipe_confirm_partial_input_keeps_button_disabled():
    field, confirm_btn, _ = _capture_wipe_dialog()
    field.value = "DEL"
    field.on_change(None)
    assert confirm_btn.disabled is True


def test_wipe_confirm_on_change_does_not_update_whole_page():
    # #(2.7.1 Mac): a page.update() per keystroke reset the IME mid-word, flipping
    # a Cyrillic keyboard back after each Latin letter so DELETE was untypable.
    # on_change must touch only the button, never the page.
    field, confirm_btn, page_updates = _capture_wipe_dialog()
    field.value = "D"
    field.on_change(None)
    field.value = "DE"
    field.on_change(None)
    assert page_updates == []
