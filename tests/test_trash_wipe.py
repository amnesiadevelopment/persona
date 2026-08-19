"""The panic wipe stays genuinely irreversible.

This is the decision the whole ticket is built around: a wipe that quietly
parked fifty logged-in profiles in a recoverable store would be the interface
claiming a protection the code does not deliver — the exact defect the
Honest-interface direction exists to eliminate, in its most damaging form.

So the wipe does two things, and both are tested here: it BYPASSES the trash
(wiped profiles are destroyed, never parked) and it PURGES the trash (whatever
was already in it — including its on-disk material — is destroyed too). Its
typed-confirmation dialog therefore still tells the truth.
"""
import os
import pathlib

import pytest

from src.services.cert.store import Certificate, CertStore
from src.services.profile.manager import ProfileManager
from src.services.proxy.store import ProxyStore
from src.services.trash.store import TrashStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))

    trash = TrashStore()
    pm = ProfileManager()
    pstore = ProxyStore(path=str(tmp_path / "proxies.json"))
    cstore = CertStore()
    for store in (pm, pstore, cstore):
        store.set_trash(trash)
    return type(
        "Env",
        (),
        {
            "trash": trash, "pm": pm, "pstore": pstore, "cstore": cstore,
            "tmp_path": tmp_path,
        },
    )


def _profile_with_data(pm, name, cookie="jar"):
    pm.add_profile(name, "", "windows")
    data_dir = pm._data_path(name)
    os.makedirs(data_dir, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text(cookie)
    return data_dir


# --- the wipe bypasses the trash ---


def test_wiped_profiles_are_destroyed_not_parked(env):
    _profile_with_data(env.pm, "a")
    _profile_with_data(env.pm, "b")
    assert env.pm.wipe_all_profiles() == 2
    assert env.trash.list("profile") == [], (
        "a panic wipe must never leave a logged-in profile recoverable"
    )


def test_wiped_profile_data_dirs_are_really_gone(env):
    data_dir = _profile_with_data(env.pm, "a", cookie="logged-in")
    env.pm.wipe_all_profiles()
    assert not os.path.exists(data_dir)


def test_the_wipe_leaves_no_parked_data_anywhere_under_the_data_dir(env):
    # Not merely "not listed": nothing recoverable may survive on disk, so the
    # trash area itself must hold no profile data after a wipe.
    _profile_with_data(env.pm, "a", cookie="secret")
    env.pm.wipe_all_profiles()
    leftovers = [
        p for p in pathlib.Path(env.tmp_path / "data").rglob("*") if p.is_file()
    ]
    assert leftovers == [], leftovers


def test_the_wipe_still_empties_the_live_profile_list(env):
    _profile_with_data(env.pm, "a")
    env.pm.wipe_all_profiles()
    assert env.pm.list_profiles() == []
    assert ProfileManager().list_profiles() == []


# --- the wipe purges what was already in the trash ---


def test_the_wipe_purges_profiles_already_in_the_trash(env):
    _profile_with_data(env.pm, "trashed-earlier")
    env.pm.delete_profile("trashed-earlier")
    assert len(env.trash.list()) == 1
    _profile_with_data(env.pm, "live")
    env.pm.wipe_all_profiles()
    assert env.trash.list() == [], "the wipe must empty the trash too"


def test_the_wipe_destroys_the_parked_data_of_a_trashed_profile(env):
    _profile_with_data(env.pm, "trashed-earlier", cookie="still-logged-in")
    env.pm.delete_profile("trashed-earlier")
    parked = env.trash.list()[0].material_path
    assert os.path.exists(parked)
    env.pm.wipe_all_profiles()
    assert not os.path.exists(parked), (
        "a parked identity must not survive the panic wipe"
    )


def test_the_wipe_purges_the_trash_even_with_no_live_profiles(env):
    # The wipe returns 0 profiles here, but the trash still had a logged-in
    # identity in it — "everything is gone" has to include that.
    _profile_with_data(env.pm, "only-one")
    env.pm.delete_profile("only-one")
    parked = env.trash.list()[0].material_path
    assert env.pm.wipe_all_profiles() == 0
    assert env.trash.list() == []
    assert not os.path.exists(parked)


def test_the_wipe_purges_trashed_records_of_every_kind(env):
    env.pstore.add("exit-us", "socks5://user:hunter2@1.2.3.4:1080")
    env.pstore.delete("exit-us")
    _profile_with_data(env.pm, "a")
    env.pm.delete_profile("a")
    env.pm.wipe_all_profiles()
    assert env.trash.list() == []


def test_the_wipe_destroys_a_trashed_certificates_key_bundle(env):
    source = env.tmp_path / "src.p12"
    source.write_text("KEYMATERIAL")
    stored = env.cstore.import_p12("admin", str(source))
    env.cstore.add(Certificate(name="admin", p12_path=stored))
    env.cstore.remove("admin")
    parked = env.trash.list()[0].material_path
    assert os.path.exists(parked)
    env.pm.wipe_all_profiles()
    assert not os.path.exists(parked), (
        "private-key material must not survive the panic wipe"
    )


def test_the_purge_survives_a_reload(env):
    _profile_with_data(env.pm, "a")
    env.pm.delete_profile("a")
    env.pm.wipe_all_profiles()
    assert TrashStore().list() == []


def test_a_wipe_after_a_wipe_is_still_a_clean_noop(env):
    _profile_with_data(env.pm, "a")
    env.pm.wipe_all_profiles()
    assert env.pm.wipe_all_profiles() == 0
    assert env.trash.list() == []


def test_the_wipe_stops_running_browsers_before_destroying_data(env):
    stopped = []
    _profile_with_data(env.pm, "a")
    _profile_with_data(env.pm, "b")
    env.pm.set_stop_hook(stopped.append)
    env.pm.wipe_all_profiles()
    assert sorted(stopped) == ["a", "b"]


# --- the dialog that gates it still tells the truth ---


def test_the_wipe_dialog_still_claims_irreversibility(env):
    # The one dialog that must KEEP saying "cannot be undone", because after the
    # two behaviours above it remains true.
    from types import SimpleNamespace

    from src.ui.dialogs import wipe_confirm

    shown = {}
    page = SimpleNamespace(
        update=lambda: None,
        show_dialog=lambda d: shown.setdefault("dlg", d),
        pop_dialog=lambda: None,
    )
    wipe_confirm.open_wipe_confirm_dialog(page, 3, on_confirm=lambda: None)
    body = shown["dlg"].content.controls[0].value
    assert "cannot be undone" in body
    assert "permanently" in body
