"""The RESTORE door builds each record by reflection over its trash payload.

Every ``to_dict()`` in this tree is ``asdict(self)``, and ``TrashEntry.payload``
is documented as exactly that output, verbatim. So the SAVE half of the trash
has always enumerated a record's fields by reflection: add a field to one of
these dataclasses and it rides into ``trash.json`` for free.

The RESTORE half used to enumerate them by hand in five of the six stores
(``Profile`` was the exception, and the precedent). The consequence is a partial
restore reported as a complete one: the value sits correctly on disk in the
trash entry, the restore returns ``True``, and the operator is told the record
came back — while the field is silently gone. That is a worse failure than the
matching load-path drop, which at least evaporates consistently on the next
save, and it is against PS-10's stated contract that restoring returns the
record *exactly as it was*.

These specs drive the property end to end, per store: a field is added to the
dataclass at test time, the record is added, deleted, and restored, and the
value is asserted ON THE RESTORED RECORD. Nothing here asserts that
``dataclasses.fields`` is *called* — such a test passes against an
implementation that drops all four carve-outs, so each carve-out gets its own
behavioural spec below instead.

⚠️ ON THE RELOAD HALF. The restore door hands the new field back and ``_save()``
writes it to disk verbatim — both asserted here. A *fresh store instance* built
against that same file still drops it, because the five stores' ``_load`` paths
are hand-enumerated too. That is the sibling asymmetry PS-269's shape has not
been applied to yet, and it is explicitly out of this slice's scope; folding it
in would double the blast radius. The reload assertions below therefore pin what
the restore door actually owns: the restored record survives the reload, and the
new field is present in the JSON the restore wrote.
"""
import dataclasses
import json
import os
import pathlib

import pytest

from src.models.bookmark import Bookmark, Pool
from src.models.proxy import Proxy
from src.services import bookmark as _bookmark_pkg  # noqa: F401
from src.services.bookmark import store as bookmark_store_mod
from src.services.cert import store as cert_store_mod
from src.services.cert.store import Certificate, CertStore
from src.services.proxy import store as proxy_store_mod
from src.services.proxy.store import ProxyStore
from src.services.ssh import store as ssh_store_mod
from src.services.ssh.store import SSHHost, SSHHostStore
from src.services.trash.store import TrashStore

BookmarkStore = bookmark_store_mod.BookmarkStore


def _extend(model, field_name: str, default=""):
    """A subclass of ``model`` carrying one extra dataclass field.

    This is how the property is driven honestly. Mutating the real dataclass in
    place would leak into every other test in the session; subclassing gives a
    genuine new field — visible to ``dataclasses.fields`` and to ``asdict`` —
    that disappears with the test. The subclass is named after the original so
    the store's own logging and error messages read the same.
    """
    return dataclasses.make_dataclass(
        model.__name__,
        [(field_name, type(default), dataclasses.field(default=default))],
        bases=(model,),
    )


@pytest.fixture
def trash(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    return TrashStore()


# --- SSH hosts -------------------------------------------------------------


@pytest.fixture
def sshstore(tmp_path, trash, monkeypatch):
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    s = SSHHostStore()
    s.set_trash(trash)
    return s


def test_a_field_added_to_ssh_host_survives_delete_and_restore(
    sshstore, trash, monkeypatch, tmp_path
):
    Extended = _extend(SSHHost, "jump_host")
    monkeypatch.setattr(ssh_store_mod, "SSHHost", Extended)

    sshstore.add(Extended(name="box", host="1.2.3.4", jump_host="bastion"))
    sshstore.remove("box")
    ok, msg = sshstore.restore_host(trash.list("ssh_host")[0])

    assert (ok, msg) == (True, "")
    assert sshstore.get("box").jump_host == "bastion", (
        "the value was in the payload and must come back out of it"
    )
    # The restore door's other half: what it handed back is what got written.
    # (A FRESH store still drops it — the hand-enumerated _load path, which is
    # this slice's named out-of-scope sibling.)
    on_disk = json.loads(pathlib.Path(tmp_path / "ssh.json").read_text(encoding="utf-8"))
    assert on_disk["box"]["jump_host"] == "bastion"
    assert SSHHostStore().get("box").host == "1.2.3.4", "restored record reloads"


def test_a_restored_ssh_hosts_port_is_an_int_not_the_raw_payload_value(
    sshstore, trash
):
    """CARVE-OUT 1. A pure intersection passes the payload value straight
    through; the int() coercion the enumerated form did must survive."""
    sshstore.add(SSHHost(name="box", host="1.2.3.4", port=2222))
    sshstore.remove("box")
    entry = trash.list("ssh_host")[0]
    entry.payload["host"]["port"] = "2222"  # e.g. a hand-edited trash.json

    assert sshstore.restore_host(entry) == (True, "")
    port = sshstore.get("box").port
    assert isinstance(port, int) and not isinstance(port, bool), (
        f"port came back as {type(port).__name__}, so the coercion was dropped"
    )
    assert port == 2222


def test_an_ssh_payload_with_no_name_restores_under_the_entrys_name(
    sshstore, trash
):
    """CARVE-OUT 4. `name` falls back to the ENTRY's name, not the payload's."""
    sshstore.add(SSHHost(name="box", host="1.2.3.4"))
    sshstore.remove("box")
    entry = trash.list("ssh_host")[0]
    del entry.payload["host"]["name"]

    assert sshstore.restore_host(entry) == (True, "")
    assert sshstore.get("box") is not None
    assert sshstore.get("box").name == "box"


def test_an_unknown_key_in_an_ssh_payload_is_ignored(sshstore, trash):
    """AC4. An entry written by an OLDER (or NEWER) build must still restore."""
    sshstore.add(SSHHost(name="box", host="1.2.3.4", username="root"))
    sshstore.remove("box")
    entry = trash.list("ssh_host")[0]
    entry.payload["host"]["retired_field"] = "junk"

    assert sshstore.restore_host(entry) == (True, "")
    host = sshstore.get("box")
    assert host.username == "root"
    assert not hasattr(host, "retired_field")


# --- proxies ---------------------------------------------------------------


@pytest.fixture
def pstore(tmp_path, trash):
    s = ProxyStore(path=str(tmp_path / "proxies.json"))
    s.set_trash(trash)
    return s


def test_a_field_added_to_proxy_survives_delete_and_restore(
    pstore, trash, monkeypatch, tmp_path
):
    Extended = _extend(Proxy, "exit_asn")
    monkeypatch.setattr(proxy_store_mod, "Proxy", Extended)

    pstore.proxies["exit-us"] = Extended(
        name="exit-us", url="socks5://1.2.3.4:1080", exit_asn="AS13335"
    )
    pstore._save()
    pstore.delete("exit-us")
    ok, msg = pstore.restore_proxy(trash.list("proxy")[0])

    assert (ok, msg) == (True, "")
    assert pstore.get("exit-us").exit_asn == "AS13335"
    on_disk = json.loads(pathlib.Path(tmp_path / "proxies.json").read_text(encoding="utf-8"))
    assert on_disk["exit-us"]["exit_asn"] == "AS13335"
    fresh = ProxyStore(path=str(tmp_path / "proxies.json"))
    assert fresh.get("exit-us").url == "socks5://1.2.3.4:1080"


def test_a_proxy_payload_with_no_name_restores_under_the_entrys_name(
    pstore, trash
):
    pstore.add("exit-us", "socks5://1.2.3.4:1080")
    pstore.delete("exit-us")
    entry = trash.list("proxy")[0]
    del entry.payload["proxy"]["name"]

    assert pstore.restore_proxy(entry) == (True, "")
    assert pstore.get("exit-us").name == "exit-us"


def test_an_unknown_key_in_a_proxy_payload_is_ignored(pstore, trash):
    pstore.add("exit-us", "socks5://1.2.3.4:1080")
    pstore.delete("exit-us")
    entry = trash.list("proxy")[0]
    entry.payload["proxy"]["retired_field"] = "junk"

    assert pstore.restore_proxy(entry) == (True, "")
    proxy = pstore.get("exit-us")
    assert proxy.url == "socks5://1.2.3.4:1080"
    assert not hasattr(proxy, "retired_field")


# --- certificates ----------------------------------------------------------


@pytest.fixture
def cstore(tmp_path, trash, monkeypatch):
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(tmp_path / "certificates"))
    s = CertStore()
    s.set_trash(trash)
    return s


def _owned_cert(cstore, tmp_path, model=Certificate, name="admin", **extra):
    source = tmp_path / "source.p12"
    source.write_text("KEYMATERIAL", encoding="utf-8")
    stored = cstore.import_p12(name, str(source))
    cstore.add(
        model(
            name=name,
            p12_path=stored,
            password="p12pass",
            url="https://admin.example.com",
            **extra,
        )
    )
    return stored


def test_a_field_added_to_certificate_survives_delete_and_restore(
    cstore, trash, monkeypatch, tmp_path
):
    Extended = _extend(Certificate, "issuer_cn")
    monkeypatch.setattr(cert_store_mod, "Certificate", Extended)

    _owned_cert(cstore, tmp_path, model=Extended, issuer_cn="Corp Root CA")
    cstore.remove("admin")
    ok, msg = cstore.restore_certificate(trash.list("certificate")[0])

    assert (ok, msg) == (True, "")
    assert cstore.get("admin").issuer_cn == "Corp Root CA"
    on_disk = json.loads(pathlib.Path(tmp_path / "certs.json").read_text(encoding="utf-8"))
    assert on_disk["admin"]["issuer_cn"] == "Corp Root CA"
    assert CertStore().get("admin").password == "p12pass", "record reloads"


def test_a_restored_certificate_points_at_the_unparked_bundle_not_the_payload(
    cstore, trash, tmp_path, monkeypatch
):
    """CARVE-OUT 2, the sharpest. ``p12_path`` does NOT come from the payload.

    remove() MOVED the bundle into the trash area, and _unpark_owned_p12 decides
    where it comes back to. In the ordinary case that destination happens to
    equal the payload's recorded path, which is exactly why a pure splat looks
    harmless — so this drives the case where the two genuinely DIVERGE: the
    certificate store directory moved between the delete and the restore (the
    operator relocated PERSONA_HOME). The payload still records the OLD path,
    which no longer exists and is no longer persona's to own; the unpark lands
    the bundle in the NEW store dir. Reflection over the payload would restore a
    certificate pointing at a path with no key material behind it, and the
    operator would be told the restore succeeded.
    """
    stored = _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    entry = trash.list("certificate")[0]
    parked = entry.material_path
    assert parked and parked != stored, "the bundle really was parked"
    assert entry.payload["certificate"]["p12_path"] == stored

    moved_dir = tmp_path / "relocated-certificates"
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(moved_dir))

    assert cstore.restore_certificate(entry) == (True, "")
    cert = cstore.get("admin")
    assert cert.p12_path != parked, "it must not point into the trash area"
    assert cert.p12_path != stored, (
        "the payload's stale path is not where the bundle came back to"
    )
    assert os.path.exists(cert.p12_path), "the bundle is where the record says"
    assert (
        pathlib.Path(cert.p12_path).read_text(encoding="utf-8") == "KEYMATERIAL"
    )


def test_a_restored_certificate_points_at_a_readable_bundle_in_the_normal_case(
    cstore, trash, tmp_path
):
    """CARVE-OUT 2, ordinary path: nothing moved, and the restored record still
    points at a bundle that is actually there and actually readable."""
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    entry = trash.list("certificate")[0]
    parked = entry.material_path

    assert cstore.restore_certificate(entry) == (True, "")
    cert = cstore.get("admin")
    assert cert.p12_path != parked
    assert os.path.exists(cert.p12_path)
    assert (
        pathlib.Path(cert.p12_path).read_text(encoding="utf-8") == "KEYMATERIAL"
    )


def test_a_certificate_whose_bundle_cannot_be_unparked_is_left_in_the_trash(
    cstore, trash, tmp_path, monkeypatch
):
    """CARVE-OUT 2, second half: the early return must still fire BEFORE the
    record is constructed, or a failed unpark restores a keyless certificate."""
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    monkeypatch.setattr(CertStore, "_unpark_owned_p12", lambda *a, **k: None)

    ok, msg = cstore.restore_certificate(trash.list("certificate")[0])
    assert ok is False
    assert "left in the trash" in msg
    assert cstore.get("admin") is None, "nothing may be constructed"


def test_a_certificate_payload_with_no_name_restores_under_the_entrys_name(
    cstore, trash, tmp_path
):
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    entry = trash.list("certificate")[0]
    del entry.payload["certificate"]["name"]

    assert cstore.restore_certificate(entry) == (True, "")
    assert cstore.get("admin").name == "admin"


def test_an_unknown_key_in_a_certificate_payload_is_ignored(
    cstore, trash, tmp_path
):
    _owned_cert(cstore, tmp_path)
    cstore.remove("admin")
    entry = trash.list("certificate")[0]
    entry.payload["certificate"]["retired_field"] = "junk"

    assert cstore.restore_certificate(entry) == (True, "")
    cert = cstore.get("admin")
    assert cert.password == "p12pass"
    assert not hasattr(cert, "retired_field")


# --- bookmarks and pools ---------------------------------------------------


@pytest.fixture
def bstore(tmp_path, trash):
    s = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    s.set_trash(trash)
    return s


def test_a_field_added_to_bookmark_survives_delete_and_restore(
    bstore, trash, monkeypatch, tmp_path
):
    Extended = _extend(Bookmark, "icon")
    monkeypatch.setattr(bookmark_store_mod, "Bookmark", Extended)

    bstore.bookmarks["leaks"] = Extended(
        name="leaks", url="https://browserleaks.com", icon="shield.png"
    )
    bstore._save()
    bstore.delete("leaks")
    ok, msg = bstore.restore_bookmark(trash.list("bookmark")[0])

    assert (ok, msg) == (True, "")
    assert bstore.get("leaks").icon == "shield.png"
    on_disk = json.loads(pathlib.Path(tmp_path / "bookmarks.json").read_text(encoding="utf-8"))
    assert on_disk["bookmarks"]["leaks"]["icon"] == "shield.png"
    fresh = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    assert fresh.get("leaks").url == "https://browserleaks.com"


def test_a_field_added_to_pool_survives_delete_and_restore(
    bstore, trash, monkeypatch, tmp_path
):
    Extended = _extend(Pool, "toolbar_label")
    monkeypatch.setattr(bookmark_store_mod, "Pool", Extended)

    bstore.add("leaks", "https://a")
    bstore.pools["checks"] = Extended(
        name="checks", bookmark_names=["leaks"], toolbar_label="Checks"
    )
    bstore._save()
    bstore.delete_pool("checks")
    ok, msg = bstore.restore_pool(trash.list("pool")[0])

    assert (ok, msg) == (True, "")
    pool = bstore.get_pool("checks")
    assert pool.toolbar_label == "Checks"
    assert pool.bookmark_names == ["leaks"], "the members still come back too"
    on_disk = json.loads(pathlib.Path(tmp_path / "bookmarks.json").read_text(encoding="utf-8"))
    assert on_disk["pools"]["checks"]["toolbar_label"] == "Checks"
    fresh = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    assert fresh.get_pool("checks").bookmark_names == ["leaks"]


def test_a_restored_pool_still_drops_members_that_no_longer_resolve(
    bstore, trash
):
    """CARVE-OUT 3. ``bookmark_names`` is FILTERED, not copied.

    A pure splat of the payload restores a pool holding a name nothing
    resolves — add_pool/update_pool filter the same way, so restore must too.
    The member here is PERMANENTLY gone (deleted, then purged from the trash),
    which is the case that genuinely stays dropped: a merely-trashed member is
    parked onto its own entry and rejoins on its own restore.
    """
    bstore.add("leaks", "https://a")
    bstore.add("scan", "https://b")
    bstore.add_pool("checks", ["leaks", "scan"])
    bstore.delete_pool("checks")
    bstore.delete("scan")
    for entry in trash.list("bookmark"):
        trash.pop(entry.id)  # really gone, not merely trashed

    assert bstore.restore_pool(trash.list("pool")[0]) == (True, "")
    assert bstore.get_pool("checks").bookmark_names == ["leaks"]


def test_a_bookmark_payload_with_no_name_restores_under_the_entrys_name(
    bstore, trash
):
    bstore.add("leaks", "https://a")
    bstore.delete("leaks")
    entry = trash.list("bookmark")[0]
    del entry.payload["bookmark"]["name"]

    assert bstore.restore_bookmark(entry) == (True, "")
    assert bstore.get("leaks").name == "leaks"


def test_a_pool_payload_with_no_name_restores_under_the_entrys_name(
    bstore, trash
):
    bstore.add("leaks", "https://a")
    bstore.add_pool("checks", ["leaks"])
    bstore.delete_pool("checks")
    entry = trash.list("pool")[0]
    del entry.payload["pool"]["name"]

    assert bstore.restore_pool(entry) == (True, "")
    assert bstore.get_pool("checks").name == "checks"


def test_an_unknown_key_in_a_bookmark_payload_is_ignored(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.delete("leaks")
    entry = trash.list("bookmark")[0]
    entry.payload["bookmark"]["retired_field"] = "junk"

    assert bstore.restore_bookmark(entry) == (True, "")
    bookmark = bstore.get("leaks")
    assert bookmark.url == "https://a"
    assert not hasattr(bookmark, "retired_field")


def test_an_unknown_key_in_a_pool_payload_is_ignored(bstore, trash):
    bstore.add("leaks", "https://a")
    bstore.add_pool("checks", ["leaks"])
    bstore.delete_pool("checks")
    entry = trash.list("pool")[0]
    entry.payload["pool"]["retired_field"] = "junk"

    assert bstore.restore_pool(entry) == (True, "")
    pool = bstore.get_pool("checks")
    assert pool.bookmark_names == ["leaks"]
    assert not hasattr(pool, "retired_field")
