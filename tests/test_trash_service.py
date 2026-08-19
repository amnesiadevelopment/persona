"""TrashService: the one place that knows how to restore and how to destroy.

Both lanes go through this object, so the tests here are what make "the REST
delete and the UI delete agree" a checkable claim rather than a hope: the
service is the shared seam, and the API/UI tests elsewhere only wire into it.
"""
import os
import pathlib

import pytest

from src.models.profile import Profile
from src.services.bookmark.store import BookmarkStore
from src.services.cert.store import Certificate, CertStore
from src.services.profile.manager import ProfileManager
from src.services.proxy.store import ProxyStore
from src.services.ssh.store import SSHHost, SSHHostStore
from src.services.trash.service import TrashService
from src.services.trash.store import TrashStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A full set of stores sharing ONE trash, exactly as the container wires it."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))

    clock = {"t": 1000.0}
    trash = TrashStore(now=lambda: clock["t"])
    pm = ProfileManager()
    bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    pstore = ProxyStore(path=str(tmp_path / "proxies.json"))
    sshstore = SSHHostStore()
    cstore = CertStore()
    for store in (pm, bstore, pstore, sshstore, cstore):
        store.set_trash(trash)
    for store in (bstore, pstore):
        store.set_profile_manager(pm)
    svc = TrashService(
        trash,
        profile_manager=pm,
        bookmark_store=bstore,
        proxy_store=pstore,
        ssh_host_store=sshstore,
        cert_store=cstore,
    )
    return type(
        "Env",
        (),
        {
            "clock": clock, "trash": trash, "pm": pm, "bstore": bstore,
            "pstore": pstore, "sshstore": sshstore, "cstore": cstore,
            "svc": svc, "tmp_path": tmp_path,
        },
    )


# --- listing ---


def test_list_reports_every_kind_in_one_place(env):
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.sshstore.add(SSHHost(name="box", host="h"))
    env.pm.delete_profile("alpha")
    env.bstore.delete("leaks")
    env.pstore.delete("exit-us")
    env.sshstore.remove("box")
    assert sorted(e.kind for e in env.svc.list()) == [
        "bookmark", "profile", "proxy", "ssh_host"
    ]


def test_list_can_be_narrowed_to_one_kind(env):
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.pm.delete_profile("alpha")
    env.bstore.delete("leaks")
    assert [e.name for e in env.svc.list("profile")] == ["alpha"]


# --- restore dispatch, per kind ---


def test_restore_routes_a_profile_back_to_the_manager(env):
    env.pm.add_profile("alpha", "", "windows")
    env.pm.delete_profile("alpha")
    entry = env.svc.list()[0]
    ok, msg = env.svc.restore(entry.id)
    assert (ok, msg) == (True, "")
    assert [p.name for p in env.pm.list_profiles()] == ["alpha"]


def test_restore_routes_a_bookmark_back_to_the_bookmark_store(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    assert env.svc.restore(env.svc.list()[0].id)[0] is True
    assert env.bstore.get("leaks") is not None


def test_restore_routes_a_pool_back_to_the_bookmark_store(env):
    env.bstore.add_pool("checks", [])
    env.bstore.delete_pool("checks")
    assert env.svc.restore(env.svc.list()[0].id)[0] is True
    assert env.bstore.get_pool("checks") is not None


def test_restore_routes_a_proxy_back_to_the_proxy_store(env):
    env.pstore.add("exit-us", "socks5://1.2.3.4:1080")
    env.pstore.delete("exit-us")
    assert env.svc.restore(env.svc.list()[0].id)[0] is True
    assert env.pstore.get("exit-us") is not None


def test_restore_routes_an_ssh_host_back_to_the_ssh_store(env):
    env.sshstore.add(SSHHost(name="box", host="h"))
    env.sshstore.remove("box")
    assert env.svc.restore(env.svc.list()[0].id)[0] is True
    assert env.sshstore.get("box") is not None


def test_restore_routes_a_certificate_back_to_the_cert_store(env):
    source = env.tmp_path / "src.p12"
    source.write_text("KEY")
    stored = env.cstore.import_p12("admin", str(source))
    env.cstore.add(Certificate(name="admin", p12_path=stored))
    env.cstore.remove("admin")
    assert env.svc.restore(env.svc.list()[0].id)[0] is True
    assert env.cstore.get("admin") is not None


# --- restore removes the entry from the trash, refusals do not ---


def test_a_restored_entry_leaves_the_trash(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.svc.restore(env.svc.list()[0].id)
    assert env.svc.list() == []


def test_a_refused_restore_leaves_the_entry_recoverable(env):
    # A refusal must cost nothing: the record stays in the trash so the operator
    # can free the name and try again.
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.bstore.add("leaks", "https://b")
    entry_id = env.svc.list()[0].id
    ok, msg = env.svc.restore(entry_id)
    assert ok is False and "already exists" in msg
    assert [e.id for e in env.svc.list()] == [entry_id]


def test_a_refused_restore_survives_a_reload(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.bstore.add("leaks", "https://b")
    env.svc.restore(env.svc.list()[0].id)
    assert [e.name for e in TrashStore().list()] == ["leaks"]


def test_restoring_an_unknown_id_is_refused_with_a_reason(env):
    ok, msg = env.svc.restore("nope")
    assert ok is False and "no longer in the trash" in msg


def test_a_restore_that_raises_puts_the_entry_back(env, monkeypatch):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    entry_id = env.svc.list()[0].id

    def boom(entry):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(env.bstore, "restore_bookmark", boom)
    ok, msg = env.svc.restore(entry_id)
    assert ok is False and "disk on fire" in msg
    assert [e.id for e in env.svc.list()] == [entry_id]


# --- permanent deletion ---


def test_delete_permanently_removes_the_entry(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    entry_id = env.svc.list()[0].id
    ok, msg = env.svc.delete_permanently(entry_id)
    assert (ok, msg) == (True, "")
    assert env.svc.list() == []


def test_delete_permanently_destroys_a_profiles_data_dir(env):
    env.pm.add_profile("alpha", "", "windows")
    data_dir = env.pm._data_path("alpha")
    os.makedirs(data_dir, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text("jar")
    env.pm.delete_profile("alpha")
    entry = env.svc.list()[0]
    parked = entry.material_path
    assert os.path.exists(parked)
    env.svc.delete_permanently(entry.id)
    assert not os.path.exists(parked), "the cookies are gone for good"


def test_delete_permanently_destroys_a_certificates_key_bundle(env):
    source = env.tmp_path / "src.p12"
    source.write_text("KEYMATERIAL")
    stored = env.cstore.import_p12("admin", str(source))
    env.cstore.add(Certificate(name="admin", p12_path=stored))
    env.cstore.remove("admin")
    entry = env.svc.list()[0]
    env.svc.delete_permanently(entry.id)
    assert not os.path.exists(entry.material_path)


def test_a_permanently_deleted_profile_cannot_be_restored(env):
    env.pm.add_profile("alpha", "", "windows")
    env.pm.delete_profile("alpha")
    entry_id = env.svc.list()[0].id
    env.svc.delete_permanently(entry_id)
    ok, msg = env.svc.restore(entry_id)
    assert ok is False and "no longer in the trash" in msg
    assert env.pm.list_profiles() == []


def test_delete_permanently_of_an_unknown_id_is_refused(env):
    ok, msg = env.svc.delete_permanently("nope")
    assert ok is False and "no longer in the trash" in msg


# --- empty ---


def test_empty_destroys_everything_and_reports_the_count(env):
    env.pm.add_profile("alpha", "", "windows")
    env.bstore.add("leaks", "https://a")
    env.pm.delete_profile("alpha")
    env.bstore.delete("leaks")
    parked = env.svc.list("profile")[0].material_path
    assert env.svc.empty() == 2
    assert env.svc.list() == []
    assert not os.path.exists(parked)


def test_empty_on_an_already_empty_trash_returns_zero(env):
    assert env.svc.empty() == 0


# --- retention ---


def test_purge_expired_removes_only_entries_past_the_window(env):
    env.bstore.add("old", "https://a")
    env.bstore.delete("old")
    env.clock["t"] = 1000.0 + 20 * 86400
    env.bstore.add("recent", "https://b")
    env.bstore.delete("recent")
    env.clock["t"] = 1000.0 + 31 * 86400
    assert env.svc.purge_expired() == 1
    assert [e.name for e in env.svc.list()] == ["recent"]


def test_purge_expired_destroys_the_expired_profiles_data(env):
    env.pm.add_profile("alpha", "", "windows")
    os.makedirs(env.pm._data_path("alpha"), exist_ok=True)
    env.pm.delete_profile("alpha")
    parked = env.svc.list()[0].material_path
    env.clock["t"] = 1000.0 + 31 * 86400
    env.svc.purge_expired()
    assert not os.path.exists(parked)


def test_purge_expired_keeps_everything_inside_the_window(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.clock["t"] = 1000.0 + 29 * 86400
    assert env.svc.purge_expired() == 0
    assert [e.name for e in env.svc.list()] == ["leaks"]


def test_purge_expired_honours_an_explicit_window(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.clock["t"] = 1000.0 + 2 * 86400
    assert env.svc.purge_expired(retention_days=1) == 1


def test_a_purge_survives_a_reload(env):
    env.bstore.add("leaks", "https://a")
    env.bstore.delete("leaks")
    env.clock["t"] = 1000.0 + 31 * 86400
    env.svc.purge_expired()
    assert TrashStore().list() == []
