"""The four JSON stores through the trash.

Each of these deletes used to be "del entry + save" — total and immediate, with
three of them dropping credentials the operator cannot regenerate. These tests
hold the line on the round trip AND on the two things that are easy to lose in
it: the membership relationships a delete edits away (a bookmark's pools, a
pool's/proxy's referencing profiles), and the fact that trashing a secret-bearing
record does NOT shred its secret.
"""
import os
import pathlib
import stat

import pytest

from src.models.profile import Profile
from src.services.bookmark.store import BookmarkStore
from src.services.cert.store import Certificate, CertStore
from src.services.proxy.store import ProxyStore
from src.services.ssh.store import SSHHost, SSHHostStore
from src.services.trash.store import TrashStore


@pytest.fixture
def trash(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    return TrashStore()


class _FakeProfileManager:
    """Just enough profile manager for the reference-restoring paths."""

    def __init__(self, *profiles: Profile) -> None:
        self.profiles = {p.name: p for p in profiles}
        self.saved = 0

    def list_profiles(self):
        return list(self.profiles.values())

    def save_profiles(self):
        self.saved += 1


# --- bookmarks ---


@pytest.fixture
def bstore(tmp_path, trash):
    s = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    s.set_trash(trash)
    return s


def test_deleting_a_bookmark_trashes_it(bstore, trash):
    bstore.add("leaks", "https://browserleaks.com")
    assert bstore.delete("leaks") is True
    assert bstore.get("leaks") is None
    assert [e.name for e in trash.list("bookmark")] == ["leaks"]


def test_restoring_a_bookmark_returns_its_url(bstore, trash):
    bstore.add("leaks", "https://browserleaks.com")
    bstore.delete("leaks")
    ok, msg = bstore.restore_bookmark(trash.list("bookmark")[0])
    assert (ok, msg) == (True, "")
    assert bstore.get("leaks").url == "https://browserleaks.com"


def test_restoring_a_bookmark_returns_it_to_the_pools_it_was_in(bstore, trash):
    # delete() strips the name from every pool, so the membership is only
    # recoverable because the trash entry recorded it.
    bstore.add("leaks", "https://browserleaks.com")
    bstore.add_pool("checks", ["leaks"])
    bstore.delete("leaks")
    assert bstore.get_pool("checks").bookmark_names == []
    bstore.restore_bookmark(trash.list("bookmark")[0])
    assert bstore.get_pool("checks").bookmark_names == ["leaks"]


def test_restoring_a_bookmark_is_refused_when_the_name_is_taken(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.delete("leaks")
    bstore.add("leaks", "https://b")
    ok, msg = bstore.restore_bookmark(trash.list("bookmark")[0])
    assert ok is False and "already exists" in msg
    assert bstore.get("leaks").url == "https://b"


def test_a_restored_bookmark_survives_a_reload(bstore, trash, tmp_path):
    bstore.add("leaks", "https://browserleaks.com")
    bstore.delete("leaks")
    bstore.restore_bookmark(trash.list("bookmark")[0])
    fresh = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    assert fresh.get("leaks").url == "https://browserleaks.com"


# --- pools ---


def test_deleting_a_pool_trashes_it_and_keeps_the_bookmarks(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.add_pool("checks", ["leaks"])
    assert bstore.delete_pool("checks") is True
    assert bstore.get_pool("checks") is None
    assert bstore.get("leaks") is not None, "the bookmarks themselves are kept"
    assert [e.name for e in trash.list("pool")] == ["checks"]


def test_restoring_a_pool_returns_its_membership(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.add("scan", "https://b")
    bstore.add_pool("checks", ["leaks", "scan"])
    bstore.delete_pool("checks")
    bstore.restore_pool(trash.list("pool")[0])
    assert bstore.get_pool("checks").bookmark_names == ["leaks", "scan"]


def test_a_restored_pool_skips_members_that_no_longer_exist(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.add("scan", "https://b")
    bstore.add_pool("checks", ["leaks", "scan"])
    bstore.delete_pool("checks")
    bstore.delete("scan")  # gone from the live store meanwhile
    bstore.restore_pool(trash.list("pool")[0])
    assert bstore.get_pool("checks").bookmark_names == ["leaks"]


def test_restoring_a_pool_repoints_the_profiles_that_used_it(bstore, trash):
    # _delete_pool clears the dangling reference (a lingering pool name made the
    # profile launch with an empty toolbar), so restore must put it back.
    profile = Profile(name="alpha", bookmark_pool="checks")
    pm = _FakeProfileManager(profile)
    bstore.set_profile_manager(pm)
    bstore.add("leaks", "https://a")
    bstore.add_pool("checks", ["leaks"])
    bstore.delete_pool("checks")
    profile.bookmark_pool = None  # what the caller does after the delete
    bstore.restore_pool(trash.list("pool")[0])
    assert profile.bookmark_pool == "checks"


def test_restoring_a_pool_does_not_override_a_newer_choice(bstore, trash):
    # The operator may have picked another pool meanwhile; a restore must not
    # silently change a live profile's configuration.
    profile = Profile(name="alpha", bookmark_pool="checks")
    pm = _FakeProfileManager(profile)
    bstore.set_profile_manager(pm)
    bstore.add_pool("checks", [])
    bstore.delete_pool("checks")
    profile.bookmark_pool = "other"
    bstore.restore_pool(trash.list("pool")[0])
    assert profile.bookmark_pool == "other"


def test_restoring_a_pool_is_refused_when_the_name_is_taken(bstore, trash):
    bstore.add_pool("checks", [])
    bstore.delete_pool("checks")
    bstore.add_pool("checks", [])
    ok, msg = bstore.restore_pool(trash.list("pool")[0])
    assert ok is False and "already exists" in msg


# --- proxies ---


@pytest.fixture
def pstore(tmp_path, trash):
    s = ProxyStore(path=str(tmp_path / "proxies.json"))
    s.set_trash(trash)
    return s


def test_deleting_a_proxy_trashes_it(pstore, trash):
    pstore.add("exit-us", "socks5://user:pass@1.2.3.4:1080")
    assert pstore.delete("exit-us") is True
    assert pstore.get("exit-us") is None
    assert [e.name for e in trash.list("proxy")] == ["exit-us"]


def test_a_trashed_proxy_keeps_its_credentials_for_restore(pstore, trash):
    pstore.add("exit-us", "socks5://user:pass@1.2.3.4:1080")
    pstore.delete("exit-us")
    pstore.restore_proxy(trash.list("proxy")[0])
    assert pstore.get("exit-us").url == "socks5://user:pass@1.2.3.4:1080"


def test_a_restored_proxy_keeps_its_geo_and_rotation(pstore, trash):
    pstore.add("exit-us", "socks5://1.2.3.4:1080", rotate_url="https://rotate")
    pstore.mark_checked("exit-us", "US", "United States", ip="9.9.9.9",
                        timezone="America/New_York", lat=40.0, lon=-74.0)
    pstore.delete("exit-us")
    pstore.restore_proxy(trash.list("proxy")[0])
    p = pstore.get("exit-us")
    assert (p.country_code, p.last_ip, p.timezone) == (
        "US", "9.9.9.9", "America/New_York"
    )
    assert (p.lat, p.lon, p.rotate_url) == (40.0, -74.0, "https://rotate")


def test_trashing_a_proxy_does_not_remove_its_secret_from_disk(
    pstore, trash, tmp_path
):
    # The honest consequence stated in the ticket: the credentials are parked,
    # not shredded — and they are parked at the SAME 0600 the live store used.
    pstore.add("exit-us", "socks5://user:hunter2@1.2.3.4:1080")
    pstore.delete("exit-us")
    raw = pathlib.Path(os.environ["PERSONA_TRASH_FILE"]).read_text()
    assert "hunter2" in raw
    mode = stat.S_IMODE(pathlib.Path(os.environ["PERSONA_TRASH_FILE"]).stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_restoring_a_proxy_repoints_the_profiles_that_used_it(pstore, trash):
    profile = Profile(name="alpha", proxy="exit-us")
    pm = _FakeProfileManager(profile)
    pstore.set_profile_manager(pm)
    pstore.add("exit-us", "socks5://1.2.3.4:1080")
    pstore.delete("exit-us")
    profile.proxy = None  # what clear_proxy does after the delete
    pstore.restore_proxy(trash.list("proxy")[0])
    assert profile.proxy == "exit-us"


def test_restoring_a_proxy_does_not_change_a_live_exit_ip(pstore, trash):
    # A profile reassigned meanwhile must keep its new proxy: a restore must
    # never silently change which exit IP a profile presents.
    profile = Profile(name="alpha", proxy="exit-us")
    pm = _FakeProfileManager(profile)
    pstore.set_profile_manager(pm)
    pstore.add("exit-us", "socks5://1.2.3.4:1080")
    pstore.delete("exit-us")
    profile.proxy = "exit-de"
    pstore.restore_proxy(trash.list("proxy")[0])
    assert profile.proxy == "exit-de"


def test_restoring_a_proxy_is_refused_when_the_name_is_taken(pstore, trash):
    pstore.add("exit-us", "socks5://1.1.1.1:1080")
    pstore.delete("exit-us")
    pstore.add("exit-us", "socks5://2.2.2.2:1080")
    ok, msg = pstore.restore_proxy(trash.list("proxy")[0])
    assert ok is False and "already exists" in msg
    assert pstore.get("exit-us").url == "socks5://2.2.2.2:1080"


# --- ssh hosts ---


@pytest.fixture
def sshstore(tmp_path, trash, monkeypatch):
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    s = SSHHostStore()
    s.set_trash(trash)
    return s


def test_removing_an_ssh_host_trashes_it(sshstore, trash):
    sshstore.add(SSHHost(name="box", host="1.2.3.4", username="root"))
    assert sshstore.remove("box") is True
    assert sshstore.get("box") is None
    assert [e.name for e in trash.list("ssh_host")] == ["box"]


def test_a_restored_ssh_host_keeps_its_credentials_and_routing(sshstore, trash):
    sshstore.add(
        SSHHost(
            name="box", host="1.2.3.4", port=2222, username="root",
            key_path="/keys/id", key_passphrase="phrase", password="hunter2",
            profile="alpha",
        )
    )
    sshstore.remove("box")
    ok, msg = sshstore.restore_host(trash.list("ssh_host")[0])
    assert (ok, msg) == (True, "")
    h = sshstore.get("box")
    assert (h.host, h.port, h.username) == ("1.2.3.4", 2222, "root")
    assert (h.key_path, h.key_passphrase, h.password) == (
        "/keys/id", "phrase", "hunter2"
    )
    assert h.profile == "alpha"


def test_trashing_an_ssh_host_does_not_remove_its_password_from_disk(
    sshstore, trash
):
    sshstore.add(SSHHost(name="box", host="h", password="hunter2"))
    sshstore.remove("box")
    raw = pathlib.Path(os.environ["PERSONA_TRASH_FILE"]).read_text()
    assert "hunter2" in raw


def test_restoring_an_ssh_host_is_refused_when_the_name_is_taken(sshstore, trash):
    sshstore.add(SSHHost(name="box", host="old"))
    sshstore.remove("box")
    sshstore.add(SSHHost(name="box", host="new"))
    ok, msg = sshstore.restore_host(trash.list("ssh_host")[0])
    assert ok is False and "already exists" in msg
    assert sshstore.get("box").host == "new"


# --- certificates (the sharpest case: the store owns the .p12) ---


@pytest.fixture
def cstore(tmp_path, trash, monkeypatch):
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))
    s = CertStore()
    s.set_trash(trash)
    return s


def _owned_cert(cstore, tmp_path, name="admin", body="KEYMATERIAL"):
    source = tmp_path / "source.p12"
    source.write_text(body)
    stored = cstore.import_p12(name, str(source))
    cstore.add(
        Certificate(name=name, p12_path=stored, password="p12pass",
                    url="https://admin.example.com")
    )
    return stored


def test_removing_a_certificate_trashes_it(cstore, tmp_path, trash):
    _owned_cert(cstore, tmp_path)
    assert cstore.remove("admin") is True
    assert cstore.get("admin") is None
    assert [e.name for e in trash.list("certificate")] == ["admin"]


def test_trashing_a_certificate_moves_the_p12_instead_of_deleting_it(
    cstore, tmp_path, trash
):
    # Today's remove() deletes the .p12 immediately; under the trash that
    # removal happens on PERMANENT deletion, so the key bundle must still exist.
    stored = _owned_cert(cstore, tmp_path, body="KEYMATERIAL")
    cstore.remove("admin")
    entry = trash.list("certificate")[0]
    assert not os.path.exists(stored), "it left the live location"
    assert os.path.exists(entry.material_path)
    assert pathlib.Path(entry.material_path).read_text() == "KEYMATERIAL"


def test_the_parked_p12_stays_inside_personas_own_certificate_store(
    cstore, tmp_path, trash
):
    # It must keep exactly the protection it had, and stay covered by the
    # containment check that guards deletion.
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    parked = trash.list("certificate")[0].material_path
    certs_dir = os.path.abspath(str(tmp_path / "certificates"))
    assert os.path.abspath(parked).startswith(certs_dir + os.sep)


def test_a_restored_certificate_points_at_a_working_bundle(
    cstore, tmp_path, trash
):
    _owned_cert(cstore, tmp_path, body="KEYMATERIAL")
    cstore.remove("admin")
    ok, msg = cstore.restore_certificate(trash.list("certificate")[0])
    assert (ok, msg) == (True, "")
    cert = cstore.get("admin")
    assert os.path.exists(cert.p12_path)
    assert pathlib.Path(cert.p12_path).read_text() == "KEYMATERIAL"


def test_a_restored_certificate_keeps_its_password_and_admin_url(
    cstore, tmp_path, trash
):
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    cstore.restore_certificate(trash.list("certificate")[0])
    cert = cstore.get("admin")
    assert cert.password == "p12pass"
    assert cert.url == "https://admin.example.com"


def test_a_legacy_certificate_outside_the_store_is_never_moved(
    cstore, tmp_path, trash
):
    # A legacy record may point at the operator's ORIGINAL file, which is never
    # persona's to move any more than it is ours to delete. That restraint must
    # survive the trash.
    original = tmp_path / "operators-own.p12"
    original.write_text("USER OWNED")
    cstore.add(Certificate(name="legacy", p12_path=str(original)))
    cstore.remove("legacy")
    entry = trash.list("certificate")[0]
    assert entry.material_path == "", "nothing of ours to park"
    assert original.exists() and original.read_text() == "USER OWNED"


def test_restoring_a_legacy_certificate_points_back_at_the_original_file(
    cstore, tmp_path, trash
):
    original = tmp_path / "operators-own.p12"
    original.write_text("USER OWNED")
    cstore.add(Certificate(name="legacy", p12_path=str(original)))
    cstore.remove("legacy")
    cstore.restore_certificate(trash.list("certificate")[0])
    assert cstore.get("legacy").p12_path == str(original)
    assert original.read_text() == "USER OWNED"


def test_permanently_deleting_a_certificate_destroys_the_key_bundle(
    cstore, tmp_path, trash
):
    from src.services.trash.service import destroy_entry

    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    entry = trash.pop(trash.list("certificate")[0].id)
    destroy_entry(entry, cert_store=cstore)
    assert not os.path.exists(entry.material_path)


def test_destroying_never_deletes_a_file_outside_personas_store(
    cstore, tmp_path
):
    from src.services.trash.store import TrashEntry

    outside = tmp_path / "operators-own.p12"
    outside.write_text("USER OWNED")
    entry = TrashEntry(
        id="x", kind="certificate", name="legacy", deleted_at=0.0,
        material_path=str(outside),
    )
    from src.services.trash.service import destroy_entry

    destroy_entry(entry, cert_store=cstore)
    assert outside.exists(), "the operator's own file is never ours to delete"


def test_restoring_a_certificate_is_refused_when_the_name_is_taken(
    cstore, tmp_path, trash
):
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    _owned_cert(cstore, tmp_path, body="REPLACEMENT")
    ok, msg = cstore.restore_certificate(trash.list("certificate")[0])
    assert ok is False and "already exists" in msg
    assert pathlib.Path(cstore.get("admin").p12_path).read_text() == "REPLACEMENT"
