"""Regressions for the three defects the PS-10 code review found.

Each test here drives the path PRODUCTION actually uses. That is the whole point
of the file: the original suite covered all three behaviours and passed anyway,
because it simulated the call sequence instead of executing it. Two of these
defects were invisible purely because a test did the steps in an order the app
does not.

1. Restoring a proxy / bookmark pool must put back the profile references it had.
   The stores recorded which profiles referenced a record and the UI cleared
   those references FIRST, so nothing was ever recorded and a restore silently
   returned a record nothing pointed at.
2. The park area for trashed profile data must not be addressable as a profile.
   It lived at DATA_DIR/.trash, and ".trash" is a name validate_profile_name
   accepts — so a profile called ".trash" WAS the park area.
3. delete_profile returns False when it cannot park the data dir, leaving the
   profile intact. All three lanes reported success regardless.
"""
import os
import pathlib
from types import SimpleNamespace

import pytest

from src.models.profile import Profile
from src.services.bookmark.store import BookmarkStore
from src.services.profile.manager import ProfileManager, trash_data_root
from src.services.proxy.store import ProxyStore
from src.services.trash.service import TrashService
from src.services.trash.store import TrashStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Real stores, wired together exactly as the Container wires them."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))

    trash = TrashStore()
    pm = ProfileManager()
    pm.set_trash(trash)
    pstore = ProxyStore(path=str(tmp_path / "proxies.json"))
    bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    for s in (pstore, bstore):
        s.set_trash(trash)
        s.set_profile_manager(pm)
    svc = TrashService(
        trash,
        profile_manager=pm,
        bookmark_store=bstore,
        proxy_store=pstore,
    )
    return SimpleNamespace(
        pm=pm, pstore=pstore, bstore=bstore, trash=trash, svc=svc, tmp=tmp_path
    )


# --- 1. restore puts the profile references back, on the REAL call path ---


def _ui_delete_proxy(env, name):
    """Exactly what App._delete_proxy's confirm handler runs."""
    env.pstore.delete(name)


def _ui_delete_pool(env, name):
    """Exactly what App._delete_pool's confirm handler runs."""
    env.bstore.delete_pool(name)


def test_the_ui_delete_path_records_the_profiles_that_used_a_proxy(env):
    # The defect: the UI cleared profile.proxy before calling delete, so the
    # store asked "who references this?" after the answer had been erased.
    env.pm.add_profile("alpha", "", "windows")
    env.pstore.add("exit-us", "socks5://u:p@1.2.3.4:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")

    entry = env.trash.list("proxy")[0]
    assert entry.payload["profiles"] == ["alpha"], (
        "the referencing profile must be recorded, or restore cannot re-point it"
    )


def test_deleting_a_proxy_through_the_ui_path_still_clears_the_reference(env):
    # The reference MUST go — a lingering proxy name stranded the profile page.
    # Moving the clear into the store must not lose that.
    env.pm.add_profile("alpha", "", "windows")
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")

    assert env.pm.profiles["alpha"].proxy is None


def test_restoring_a_proxy_deleted_through_the_ui_path_repoints_the_profile(env):
    # The end-to-end claim of the ticket: "restores to the same working state,
    # including its membership relationships where it had them."
    env.pm.add_profile("alpha", "", "windows")
    env.pstore.add("exit-us", "socks5://u:p@1.2.3.4:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")
    entry = env.trash.list("proxy")[0]
    ok, msg = env.svc.restore(entry.id)

    assert (ok, msg) == (True, "")
    assert env.pm.profiles["alpha"].proxy == "exit-us", (
        "a restored proxy that no profile points at is not the same working state"
    )


def test_a_restored_proxy_reference_survives_a_reload_from_disk(env):
    # Re-pointing the in-memory Profile is not enough: it has to be saved.
    env.pm.add_profile("alpha", "", "windows")
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")
    env.svc.restore(env.trash.list("proxy")[0].id)

    reloaded = ProfileManager()
    assert reloaded.profiles["alpha"].proxy == "exit-us"


def test_the_ui_delete_path_records_the_profiles_that_used_a_pool(env):
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.bstore.add_pool("checks", ["leaks"])
    env.pm.profiles["alpha"].bookmark_pool = "checks"
    env.pm.save_profiles()

    _ui_delete_pool(env, "checks")

    entry = env.trash.list("pool")[0]
    assert entry.payload["profiles"] == ["alpha"]


def test_deleting_a_pool_through_the_ui_path_still_clears_the_reference(env):
    # A lingering pool name made the profile launch with an EMPTY toolbar
    # (audit5 #4) — the reason the clear exists at all.
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.bstore.add_pool("checks", ["leaks"])
    env.pm.profiles["alpha"].bookmark_pool = "checks"
    env.pm.save_profiles()

    _ui_delete_pool(env, "checks")

    assert env.pm.profiles["alpha"].bookmark_pool is None


def test_restoring_a_pool_deleted_through_the_ui_path_repoints_the_profile(env):
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.bstore.add_pool("checks", ["leaks"])
    env.pm.profiles["alpha"].bookmark_pool = "checks"
    env.pm.save_profiles()

    _ui_delete_pool(env, "checks")
    ok, _ = env.svc.restore(env.trash.list("pool")[0].id)

    assert ok
    assert env.pm.profiles["alpha"].bookmark_pool == "checks"
    assert env.bstore.get_pool("checks").bookmark_names == ["leaks"]


def test_only_the_referencing_profiles_are_recorded_and_repointed(env):
    # A profile using a DIFFERENT proxy must be left alone in both directions.
    for n in ("alpha", "beta"):
        env.pm.add_profile(n, "", "windows")
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.pstore.add("exit-de", "socks5://5.6.7.8:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.profiles["beta"].proxy = "exit-de"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")

    assert env.trash.list("proxy")[0].payload["profiles"] == ["alpha"]
    assert env.pm.profiles["beta"].proxy == "exit-de"


def test_a_restore_still_does_not_override_a_newer_choice(env):
    # The store now clears the reference itself; the guard that a restore must
    # not silently change a live profile's exit IP must survive that move.
    env.pm.add_profile("alpha", "", "windows")
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.pm.profiles["alpha"].proxy = "exit-us"
    env.pm.save_profiles()

    _ui_delete_proxy(env, "exit-us")
    env.pm.profiles["alpha"].proxy = "exit-de"  # operator reassigned meanwhile
    env.svc.restore(env.trash.list("proxy")[0].id)

    assert env.pm.profiles["alpha"].proxy == "exit-de"


# --- 2. the park area is not addressable as a profile ---


def test_a_profile_named_like_the_park_area_is_not_the_park_area(env):
    # ".trash" passes validate_profile_name, so when the park area lived at
    # DATA_DIR/.trash a profile with that name resolved to the park area itself.
    env.pm.add_profile(".trash", "", "windows")
    assert os.path.realpath(env.pm._data_path(".trash")) != os.path.realpath(
        trash_data_root()
    )


def test_trashed_data_is_never_parked_inside_a_live_profiles_data_dir(env):
    # The sharp consequence of the collision: another profile's cookies and
    # logins were parked INSIDE a live, launchable profile's data directory.
    env.pm.add_profile(".trash", "", "windows")
    os.makedirs(env.pm._data_path(".trash"), exist_ok=True)
    env.pm.add_profile("alpha", "", "windows")
    alpha_dir = env.pm._data_path("alpha")
    os.makedirs(alpha_dir, exist_ok=True)
    pathlib.Path(alpha_dir, "Cookies").write_text("jar", encoding="utf-8")

    assert env.pm.delete_profile("alpha") is True

    parked = env.trash.list("profile")[0].material_path
    hostile = os.path.realpath(env.pm._data_path(".trash")) + os.sep
    assert not os.path.realpath(parked).startswith(hostile)


def test_the_park_area_is_outside_the_profile_data_dir(env):
    # True by construction: no profile name can address a directory that is not
    # under DATA_DIR at all.
    import src.services.profile.manager as mod

    assert not os.path.realpath(trash_data_root()).startswith(
        os.path.realpath(mod.DATA_DIR) + os.sep
    )


def test_a_profile_named_like_the_park_area_can_itself_be_deleted(env):
    # This used to fail with EINVAL — renaming a directory into itself — and
    # delete_profile returned False, so the profile could not be deleted at all.
    env.pm.add_profile(".trash", "", "windows")
    os.makedirs(env.pm._data_path(".trash"), exist_ok=True)
    pathlib.Path(env.pm._data_path(".trash"), "Cookies").write_text("jar", encoding="utf-8")

    assert env.pm.delete_profile(".trash") is True
    assert ".trash" not in env.pm.profiles


def test_deleting_a_profile_named_like_the_park_area_keeps_other_trash_intact(env):
    # With the collision, deleting the ".trash" profile rmtree'd the park area
    # and took every other profile's recoverable data with it.
    env.pm.add_profile("alpha", "", "windows")
    alpha_dir = env.pm._data_path("alpha")
    os.makedirs(alpha_dir, exist_ok=True)
    pathlib.Path(alpha_dir, "Cookies").write_text("jar", encoding="utf-8")
    env.pm.delete_profile("alpha")
    parked = env.trash.list("profile")[0].material_path

    env.pm.add_profile(".trash", "", "windows")
    os.makedirs(env.pm._data_path(".trash"), exist_ok=True)
    env.pm.delete_profile(".trash")

    assert pathlib.Path(parked, "Cookies").read_text(encoding="utf-8") == "jar"


def test_a_profile_named_like_the_park_area_restores_intact(env):
    env.pm.add_profile(".trash", "", "windows")
    d = env.pm._data_path(".trash")
    os.makedirs(d, exist_ok=True)
    pathlib.Path(d, "Cookies").write_text("jar", encoding="utf-8")
    env.pm.delete_profile(".trash")

    entry = env.trash.list("profile")[0]
    ok, msg = env.pm.restore_profile(entry)

    assert (ok, msg) == (True, "")
    assert pathlib.Path(env.pm._data_path(".trash"), "Cookies").read_text(encoding="utf-8") == "jar"


# --- 3. a failed park is reported as a failure, in every lane ---


def _break_the_park(monkeypatch):
    """Make the data-dir move fail the way a full disk or bad perms would."""
    def boom(self, dest):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pathlib.Path, "rename", boom)


def test_delete_profile_returns_false_when_the_data_dir_cannot_be_parked(
    env, monkeypatch
):
    env.pm.add_profile("alpha", "", "windows")
    os.makedirs(env.pm._data_path("alpha"), exist_ok=True)
    _break_the_park(monkeypatch)

    assert env.pm.delete_profile("alpha") is False


def test_a_profile_that_could_not_be_parked_is_left_completely_intact(env, monkeypatch):
    env.pm.add_profile("alpha", "", "windows")
    d = env.pm._data_path("alpha")
    os.makedirs(d, exist_ok=True)
    pathlib.Path(d, "Cookies").write_text("jar", encoding="utf-8")
    _break_the_park(monkeypatch)

    env.pm.delete_profile("alpha")

    assert "alpha" in env.pm.profiles
    assert pathlib.Path(d, "Cookies").read_text(encoding="utf-8") == "jar"
    assert env.trash.list("profile") == []


def test_the_ui_reports_a_failed_delete_instead_of_success(env, monkeypatch):
    # The defect: the UI logged "Deleted: alpha" unconditionally, telling the
    # operator an identity was gone while it was still on disk.
    from src.ui.actions.profile import delete_profile as ui_delete

    env.pm.add_profile("alpha", "", "windows")
    os.makedirs(env.pm._data_path("alpha"), exist_ok=True)
    _break_the_park(monkeypatch)

    logged: list[str] = []
    captured = {}

    def fake_dialog(page, name, on_confirm, **kw):
        captured["run"] = on_confirm

    monkeypatch.setattr(
        "src.ui.actions.profile.open_confirm_dialog", fake_dialog
    )
    ui_delete(None, "alpha", env.pm, logged.append, lambda: None)
    captured["run"]()

    assert logged, "the delete must report something"
    assert "Deleted: alpha" not in logged[0]
    assert "alpha" in logged[0] and "Could not delete" in logged[0]


def test_the_ui_still_reports_success_when_the_delete_works(env, monkeypatch):
    from src.ui.actions.profile import delete_profile as ui_delete

    env.pm.add_profile("alpha", "", "windows")
    os.makedirs(env.pm._data_path("alpha"), exist_ok=True)

    logged: list[str] = []
    captured = {}
    monkeypatch.setattr(
        "src.ui.actions.profile.open_confirm_dialog",
        lambda page, name, on_confirm, **kw: captured.__setitem__(
            "run", on_confirm
        ),
    )
    ui_delete(None, "alpha", env.pm, logged.append, lambda: None)
    captured["run"]()

    assert logged == ["Deleted: alpha"]


def test_the_bulk_delete_reports_each_failure_rather_than_success(env, monkeypatch):
    from src.ui.actions import bulk as bulk_mod

    for n in ("alpha", "beta"):
        env.pm.add_profile(n, "", "windows")
        os.makedirs(env.pm._data_path(n), exist_ok=True)
    _break_the_park(monkeypatch)

    logged: list[str] = []
    captured = {}
    monkeypatch.setattr(
        bulk_mod,
        "open_confirm_dialog",
        lambda page, name, on_confirm, **kw: captured.__setitem__(
            "run", on_confirm
        ),
    )
    # Run the work inline instead of on a daemon thread, so the assertions
    # cannot race it.
    monkeypatch.setattr(
        bulk_mod.threading,
        "Thread",
        lambda target, daemon=False: SimpleNamespace(start=target),
    )
    bulk_mod.bulk_delete_profiles(
        None, ["alpha", "beta"], env.pm, logged.append, lambda: None, lambda: None
    )
    captured["run"]()

    assert len(logged) == 2
    assert all("Could not delete" in line for line in logged), logged
