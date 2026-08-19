"""Profiles through the trash: the sharpest case, because a profile IS an identity.

A profile's whole presented machine derives from crc32(name), and its data dir
holds the cookies that make it a logged-in identity. So these tests hold the
line on four things: the data dir is MOVED (not copied, not destroyed), the
restore returns the SAME name (or refuses), nothing in the trash is reachable
as a live profile, and the trash never becomes a new arbitrary-path primitive.
"""
import os
import pathlib

import pytest

import src.services.profile.manager as manager_mod
from src.services.profile.manager import ProfileManager, TRASH_DIR_NAME
from src.services.trash.store import TrashStore


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    m = ProfileManager()
    m.set_trash(TrashStore())
    return m


def _seed(mgr, name="alpha", cookie="jar"):
    mgr.add_profile(name, "", "windows")
    data_dir = mgr._data_path(name)
    os.makedirs(data_dir, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text(cookie)
    return data_dir


# --- deleting parks rather than destroys ---


def test_delete_moves_the_profile_to_the_trash(mgr):
    _seed(mgr)
    assert mgr.delete_profile("alpha") is True
    assert [e.name for e in mgr._trash().list()] == ["alpha"]


def test_delete_removes_it_from_the_live_profile_list(mgr):
    _seed(mgr)
    mgr.delete_profile("alpha")
    assert mgr.list_profiles() == []
    assert "alpha" not in mgr.profiles


def test_a_trashed_profile_is_not_addressable_by_name(mgr):
    # "Not reachable as a live record": a trashed profile must not be findable,
    # launchable or editable through the ordinary name-keyed paths.
    _seed(mgr)
    mgr.delete_profile("alpha")
    assert mgr.profiles.get("alpha") is None
    assert mgr.update_profile("alpha", "alpha", "", "windows") is False
    assert mgr.delete_profile("alpha") is False


def test_a_fresh_manager_does_not_resurrect_a_trashed_profile(mgr):
    # Profiles are enumerated from profiles.json, and the parked data dir must
    # not reappear as a profile through any load path.
    _seed(mgr)
    mgr.delete_profile("alpha")
    assert ProfileManager().list_profiles() == []


def test_delete_moves_the_data_dir_out_of_the_launchable_location(mgr):
    data_dir = _seed(mgr)
    mgr.delete_profile("alpha")
    assert not os.path.exists(data_dir), "a launch must not find the data dir"


def test_the_data_dir_is_moved_not_copied_and_not_destroyed(mgr):
    # Move, never copy: a browser profile is large and two copies of one
    # identity would diverge. The cookies must exist in EXACTLY one place.
    data_dir = _seed(mgr, cookie="secret-session")
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    assert os.path.exists(entry.material_path)
    assert (
        pathlib.Path(entry.material_path, "Cookies").read_text()
        == "secret-session"
    )
    assert not os.path.exists(data_dir)


def test_the_parked_dir_is_named_by_token_not_by_profile_name(mgr):
    # The desktop entry is removed on delete because it carries the name in
    # cleartext; the trash area must not reintroduce that trace.
    _seed(mgr, name="client-acme")
    mgr.delete_profile("client-acme")
    entry = mgr._trash().list()[0]
    assert "client-acme" not in entry.material_path
    assert os.path.basename(entry.material_path) == entry.id


def test_the_parked_dir_stays_inside_the_data_dir(mgr, tmp_path):
    _seed(mgr)
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    data_root = os.path.realpath(str(tmp_path / "data"))
    assert os.path.realpath(entry.material_path).startswith(data_root + os.sep)
    assert TRASH_DIR_NAME in entry.material_path


def test_delete_removes_the_desktop_entry_immediately(mgr, monkeypatch):
    # The entry carries the profile name in cleartext, so it goes the moment the
    # profile is trashed — not only when it is destroyed.
    removed = []
    monkeypatch.setattr(
        ProfileManager, "_remove_window_entry", staticmethod(removed.append)
    )
    _seed(mgr)
    mgr.delete_profile("alpha")
    assert removed == ["alpha"]


def test_delete_of_an_unknown_profile_is_a_noop(mgr):
    assert mgr.delete_profile("nope") is False
    assert mgr._trash().list() == []


def test_delete_stops_a_running_browser_before_moving_the_data_dir(mgr):
    # A data dir cannot be moved out from under a live engine any more than it
    # could be removed — today's stop-first discipline is preserved.
    order = []
    _seed(mgr)
    mgr.set_stop_hook(lambda name: order.append(("stopped", name)))
    mgr.delete_profile("alpha")
    assert order == [("stopped", "alpha")]


def test_a_profile_with_no_data_dir_still_trashes(mgr):
    mgr.add_profile("bare", "", "windows")
    import shutil

    shutil.rmtree(mgr._data_path("bare"), ignore_errors=True)
    assert mgr.delete_profile("bare") is True
    assert mgr._trash().list()[0].material_path == ""


# --- restoring returns the same identity ---


def test_restore_brings_the_profile_back_under_the_same_name(mgr):
    _seed(mgr)
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    ok, msg = mgr.restore_profile(entry)
    assert (ok, msg) == (True, "")
    assert [p.name for p in mgr.list_profiles()] == ["alpha"]


def test_restore_returns_the_cookies_to_the_launch_location(mgr):
    data_dir = _seed(mgr, cookie="logged-in")
    mgr.delete_profile("alpha")
    mgr.restore_profile(mgr._trash().list()[0])
    assert pathlib.Path(data_dir, "Cookies").read_text() == "logged-in"


def test_a_restored_profile_keeps_the_same_fingerprint_seed(mgr):
    # The identity a site would recognise: same name -> same crc32 seed. This is
    # the whole reason restore refuses to rename.
    _seed(mgr)
    before = mgr.profiles["alpha"].fingerprint_seed
    mgr.delete_profile("alpha")
    mgr.restore_profile(mgr._trash().list()[0])
    assert mgr.profiles["alpha"].fingerprint_seed == before


def test_a_restored_profile_keeps_its_settings_and_assignments(mgr):
    mgr.add_profile(
        "beta", "exit-us", "macos",
        search_engine="google", tags=["work"], notes="prod login",
        engine="firefox", resolution="1920x1080", certificate="admin",
    )
    mgr.delete_profile("beta")
    mgr.restore_profile(mgr._trash().list()[0])
    p = mgr.profiles["beta"]
    assert (p.proxy, p.os_type, p.engine) == ("exit-us", "macos", "firefox")
    assert (p.resolution, p.search_engine) == ("1920x1080", "google")
    assert (p.tags, p.notes, p.certificate) == (["work"], "prod login", "admin")


def test_a_restored_profile_survives_a_reload_from_disk(mgr):
    _seed(mgr)
    mgr.delete_profile("alpha")
    mgr.restore_profile(mgr._trash().list()[0])
    assert [p.name for p in ProfileManager().list_profiles()] == ["alpha"]


def test_restore_is_refused_when_the_name_is_taken(mgr):
    _seed(mgr)
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    mgr.add_profile("alpha", "", "windows")  # the name is reused meanwhile
    ok, msg = mgr.restore_profile(entry)
    assert ok is False
    assert "already exists" in msg
    assert "fingerprint" in msg, "the refusal must explain WHY, not just refuse"


def test_a_refused_restore_leaves_the_live_profile_untouched(mgr):
    _seed(mgr, cookie="original")
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    mgr.add_profile("alpha", "", "windows")
    pathlib.Path(mgr._data_path("alpha"), "Cookies").write_text("replacement")
    mgr.restore_profile(entry)
    assert pathlib.Path(mgr._data_path("alpha"), "Cookies").read_text() == (
        "replacement"
    )


def test_freeing_the_name_then_restoring_works(mgr):
    # The documented way out of a refusal: free the name and restore again.
    _seed(mgr, cookie="original")
    mgr.delete_profile("alpha")
    entry = mgr._trash().list()[0]
    mgr.add_profile("alpha", "", "windows")
    assert mgr.restore_profile(entry)[0] is False
    mgr.delete_profile("alpha")  # frees the name
    ok, _ = mgr.restore_profile(entry)
    assert ok is True
    assert pathlib.Path(mgr._data_path("alpha"), "Cookies").read_text() == (
        "original"
    )


# --- permanent destruction ---


def test_destroy_trashed_material_removes_the_parked_dir(mgr):
    _seed(mgr)
    mgr.delete_profile("alpha")
    parked = mgr._trash().list()[0].material_path
    mgr.destroy_trashed_material(parked)
    assert not os.path.exists(parked)


def test_destroy_refuses_a_path_outside_the_data_dir(mgr, tmp_path):
    # The trash must not become a path by which a delete escapes the data dir,
    # whatever a hand-edited trash.json claims.
    outside = tmp_path / "not-ours"
    outside.mkdir()
    (outside / "keep").write_text("x")
    mgr.destroy_trashed_material(str(outside))
    assert (outside / "keep").exists()


def test_destroy_refuses_a_traversal_path(mgr, tmp_path):
    outside = tmp_path / "escaped"
    outside.mkdir()
    (outside / "keep").write_text("x")
    traversal = os.path.join(
        str(tmp_path / "data"), TRASH_DIR_NAME, "..", "..", "escaped"
    )
    mgr.destroy_trashed_material(traversal)
    assert (outside / "keep").exists()


def test_destroy_of_an_empty_path_is_a_noop(mgr):
    mgr.destroy_trashed_material("")  # must not raise


# --- the trash is not a new arbitrary-path primitive ---


@pytest.mark.parametrize(
    "token",
    ["../escape", "a/b", "..", "with space", "semi;colon", "", "zz-not-hex"],
)
def test_a_non_token_trash_path_is_refused(mgr, token):
    # Resolve the exception class through the MODULE, not a from-import bound at
    # collection time: other specs importlib.reload this module, which rebinds
    # InvalidTrashToken to a NEW class object. A captured reference then fails to
    # match the exception actually raised, and pytest.raises reports "did not
    # raise" for a guard that fired correctly (it only shows up in a full-suite
    # run, never when this file runs alone).
    with pytest.raises(manager_mod.InvalidTrashToken):
        mgr._trash_data_path(token)


def test_a_valid_token_resolves_inside_the_data_dir(mgr, tmp_path):
    path = mgr._trash_data_path("deadbeef")
    data_root = os.path.realpath(str(tmp_path / "data"))
    assert os.path.realpath(path).startswith(data_root + os.sep)
