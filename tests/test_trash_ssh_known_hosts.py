"""Permanent deletion of an SSH host takes its known_hosts pin with it.

Connecting to a saved SSH host writes a SECOND file — persona's own
``known_hosts`` (0600, in PERSONA_HOME, outside every profile perimeter) — and
until now nothing removed it. ``destroy_entry``'s docstring called SSH hosts
"pure JSON"; the store scrubbed the hostname AND the password out of
``ssh_hosts.json`` while the pinned key file kept the same hostname in
cleartext. The operator performed the product's irreversible delete gesture and
the name of the machine they were reaching was still on disk.

Every assertion here is on BYTES IN THE FILE, never on a helper having been
called: the point is what survives on disk, and a test that watches a call
would still pass if the call wrote nothing.

The three refusals are as load-bearing as the removal, and each has a test:

* a RECOVERABLE delete must leave the pin standing — dropping it would convert
  a restored host's changed-key REJECTION into a fresh trust-on-first-use,
  turning MITM detection into MITM acceptance;
* a pin another record still reaches must stand, live or restorable-from-trash;
* with no live store to ask, the pin stands (the panic wipe's call site has
  none to give, and it deliberately leaves the live credential stores alone
  too).
"""
import os
import pathlib
import stat

import pytest

from src.services.ssh import client as C
from src.services.ssh.store import SSHHost, SSHHostStore
from src.services.trash.service import TrashService, destroy_entry
from src.services.trash.store import TrashStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A PERSONA_HOME with the SSH host store, the trash and known_hosts all
    inside it, exactly as they sit in a real install."""
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh_hosts.json"))
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    monkeypatch.setattr(C, "PERSONA_HOME", str(tmp_path))
    monkeypatch.setattr(C, "_KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    return tmp_path


@pytest.fixture
def trash(home):
    return TrashStore()


@pytest.fixture
def store(trash):
    st = SSHHostStore()
    st.set_trash(trash)
    return st


def _pin(entry_name, key=None):
    """Pin a key through paramiko's own writer, so the file is in exactly the
    shape ``HostKeys.save()`` produces (and the normalization is paramiko's,
    not ours)."""
    import paramiko

    key = key or paramiko.RSAKey.generate(2048)
    hostkeys = paramiko.HostKeys()
    if os.path.exists(C._KNOWN_HOSTS):
        hostkeys.load(C._KNOWN_HOSTS)
    hostkeys.add(entry_name, key.get_name(), key)
    hostkeys.save(C._KNOWN_HOSTS)
    return key


def _known_hosts_text():
    p = pathlib.Path(C._KNOWN_HOSTS)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _service(trash, store):
    return TrashService(trash, ssh_host_store=store)


# --- the entry name paramiko keys a pin under -------------------------------


def test_pin_name_matches_what_paramikos_own_connect_computes():
    # The bare-vs-[host]:port rule is paramiko's, and getting it wrong means
    # looking for a pin that is filed under a different name — a removal that
    # silently does nothing. Read off paramiko's constant, not a literal 22.
    from paramiko.config import SSH_PORT

    assert C.known_hosts_entry_name("srv.example.com", SSH_PORT) == "srv.example.com"
    assert C.known_hosts_entry_name("127.0.0.1", 2222) == "[127.0.0.1]:2222"


def test_removal_finds_a_hashed_entry_it_did_not_write():
    # persona writes plain names, but a known_hosts it inherits can be hashed.
    # Going through HostKeys means the hashed form matches for free; a
    # hand-rolled string search would miss it and leave the pin behind.
    import paramiko

    import tempfile

    d = tempfile.mkdtemp()
    path = os.path.join(d, "known_hosts")
    key = paramiko.RSAKey.generate(2048)
    hostkeys = paramiko.HostKeys()
    hostkeys.add(paramiko.HostKeys.hash_host("hashed.example.com"), key.get_name(), key)
    hostkeys.save(path)
    assert "hashed.example.com" not in pathlib.Path(path).read_text(encoding="utf-8")

    original = C._KNOWN_HOSTS
    try:
        C._KNOWN_HOSTS = path
        assert C.remove_pinned_host_key("hashed.example.com", 22) is True
    finally:
        C._KNOWN_HOSTS = original
    assert pathlib.Path(path).read_text(encoding="utf-8").strip() == ""


# --- AC1 / AC6: the removal, and that the branch actually runs --------------


def test_permanently_deleting_an_ssh_host_removes_its_known_hosts_pin(
    home, trash, store
):
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22, password="s3cret"))
    store.remove("box")

    entry = trash.list("ssh_host")[0]
    # AC6: SSH hosts are trashed with NO material_path, so a removal branch
    # placed below destroy_entry's `if not entry.material_path: return` guard
    # could never execute. This assertion is what makes the test fail loudly
    # rather than mysteriously if the branch is ever moved below it.
    assert entry.material_path == ""

    ok, _ = _service(trash, store).delete_permanently(entry.id)
    assert ok
    assert "box.example.com" not in _known_hosts_text()


def test_removing_one_pin_leaves_every_other_hosts_pin_alone(home, trash, store):
    _pin("box.example.com")
    _pin("[other.example.com]:2222")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.add(SSHHost(name="other", host="other.example.com", port=2222))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    text = _known_hosts_text()
    assert "box.example.com" not in text
    assert "[other.example.com]:2222" in text


def test_a_non_default_port_pin_is_removed_under_its_bracketed_name(
    home, trash, store
):
    # The bracketed form is where a naive `host in line` check goes wrong in
    # the other direction: it would match, and delete the wrong entry.
    _pin("[box.example.com]:2222")
    store.add(SSHHost(name="box", host="box.example.com", port=2222))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)
    assert "box.example.com" not in _known_hosts_text()


def test_every_pinned_key_type_for_the_host_goes(home, trash, store):
    import paramiko

    _pin("box.example.com", paramiko.RSAKey.generate(2048))
    _pin("box.example.com", paramiko.ECDSAKey.generate())
    assert _known_hosts_text().count("box.example.com") == 2

    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")
    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert "box.example.com" not in _known_hosts_text()


def test_emptying_the_trash_removes_the_pin_too(home, trash, store):
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    assert _service(trash, store).empty() == 1
    assert "box.example.com" not in _known_hosts_text()


def test_purging_an_expired_entry_removes_the_pin_too(home, trash, store):
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")
    # Age the entry past the retention window.
    entry = trash.list("ssh_host")[0]
    entry.deleted_at = 0.0

    assert _service(trash, store).purge_expired() == 1
    assert "box.example.com" not in _known_hosts_text()


# --- AC3: the recoverable delete must NOT touch the pin ---------------------


def test_a_recoverable_delete_leaves_the_pin_standing(home, trash, store):
    # The single most dangerous way to implement this ticket. A trashed host
    # can be restored; dropping the pin here would turn the restored host's
    # changed-key rejection into a fresh trust-on-first-use.
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))

    store.remove("box")

    assert "box.example.com" in _known_hosts_text()


def test_a_restored_host_still_has_its_pin_and_so_still_rejects_a_changed_key(
    home, trash, store
):
    import paramiko

    key_a = _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")
    ok, _ = _service(trash, store).restore(trash.list("ssh_host")[0].id)
    assert ok

    # The pin is still the ORIGINAL key — byte-identical — so paramiko has
    # something to compare a spoofed key against. Asserting the base64 rather
    # than the hostname is what makes this about MITM detection rather than
    # about a string surviving.
    hostkeys = paramiko.HostKeys(C._KNOWN_HOSTS)
    pinned = hostkeys.lookup("box.example.com")
    assert pinned is not None
    assert pinned[key_a.get_name()].get_base64() == key_a.get_base64()
    # And a DIFFERENT key under that name is not what is pinned — which is
    # exactly the mismatch connect() hard-rejects on.
    assert (
        pinned[key_a.get_name()].get_base64()
        != paramiko.RSAKey.generate(2048).get_base64()
    )


# --- AC4: a pin another record still reaches is not ours to drop ------------


def test_a_pin_shared_by_another_live_record_is_not_removed(home, trash, store):
    # Two saved records, same machine. Deleting one must not re-arm TOFU for
    # the survivor: the next connection would accept whatever key an untrusted
    # exit offered.
    _pin("box.example.com")
    store.add(SSHHost(name="box-admin", host="box.example.com", port=22))
    store.add(SSHHost(name="box-deploy", host="box.example.com", port=22))
    store.remove("box-admin")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert "box.example.com" in _known_hosts_text()


def test_a_record_on_a_different_port_does_not_block_the_removal(
    home, trash, store
):
    # The share is by host:PORT — known_hosts keys that way and so must we.
    # Without the port half this pin would be kept forever by an unrelated
    # record that cannot reach it.
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.add(SSHHost(name="box-alt", host="box.example.com", port=2222))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert "box.example.com" not in _known_hosts_text()


def test_a_pin_a_still_trashed_record_could_be_restored_onto_is_not_removed(
    home, trash, store
):
    # DECISION (recorded in _destroy_ssh_host_pin): a record sitting in the
    # trash counts as blocking. It is restorable, and a restore re-arms TOFU
    # exactly as a live record would — so the pin is not ours to drop while one
    # exists. When that record is itself permanently deleted, the pin goes.
    _pin("box.example.com")
    store.add(SSHHost(name="box-admin", host="box.example.com", port=22))
    store.add(SSHHost(name="box-deploy", host="box.example.com", port=22))
    store.remove("box-admin")
    store.remove("box-deploy")

    svc = _service(trash, store)
    first, second = trash.list("ssh_host")
    svc.delete_permanently(first.id)
    assert "box.example.com" in _known_hosts_text(), (
        "the other trashed record could still be restored onto this pin"
    )

    svc.delete_permanently(second.id)
    assert "box.example.com" not in _known_hosts_text()


def test_a_multi_host_known_hosts_line_is_left_alone(home, trash, store):
    # OpenSSH allows `a,b <keytype> <key>` on one line. persona's writer never
    # produces one, but a file we inherited can — and deleting that entry would
    # silently un-pin a host nobody asked us to forget.
    import paramiko

    key = paramiko.RSAKey.generate(2048)
    pathlib.Path(C._KNOWN_HOSTS).write_text(
        f"box.example.com,shared.example.com {key.get_name()} {key.get_base64()}\n",
        encoding="utf-8",
    )
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert "shared.example.com" in _known_hosts_text()


# --- AC5: no store to ask means the pin stands ------------------------------


def test_destroy_entry_without_an_ssh_store_leaves_the_pin_standing(
    home, trash, store
):
    # The panic wipe's call site (ProfileManager._purge_trash_for_wipe) has no
    # SSH store to give. With none, "does anything still reach this host?" is
    # unanswerable, and the safe answer to an unanswerable safety question is
    # to leave the pin — which is also the wipe's existing behaviour, since it
    # deliberately leaves ssh_hosts.json and its passwords alone too.
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    destroy_entry(trash.pop(trash.list("ssh_host")[0].id))

    assert "box.example.com" in _known_hosts_text()


def test_destroy_entry_without_a_trash_leaves_the_pin_standing(home, trash, store):
    # Same rule one step further in: without the trash we cannot see whether a
    # restorable record still reaches the host, so we do not remove.
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    destroy_entry(trash.pop(trash.list("ssh_host")[0].id), ssh_store=store)

    assert "box.example.com" in _known_hosts_text()


def test_the_panic_wipe_leaves_known_hosts_alone(home, trash, store, monkeypatch):
    # The wipe's own call site, driven rather than described.
    from src.services.profile.manager import ProfileManager

    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    pm = ProfileManager.__new__(ProfileManager)
    monkeypatch.setattr(ProfileManager, "_trash", lambda self: trash, raising=False)
    pm._purge_trash_for_wipe()

    assert trash.list("ssh_host") == []
    assert "box.example.com" in _known_hosts_text()


# --- the surrounding behaviour this must not disturb ------------------------


def test_no_known_hosts_file_at_all_is_not_an_error(home, trash, store):
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    ok, _ = _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert ok
    assert not os.path.exists(C._KNOWN_HOSTS)


def test_a_host_that_was_never_connected_to_deletes_cleanly(home, trash, store):
    _pin("other.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert "other.example.com" in _known_hosts_text()


def test_the_rewritten_known_hosts_is_still_0600(home, trash, store):
    _pin("box.example.com")
    _pin("[keep.example.com]:2222")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(C._KNOWN_HOSTS).st_mode)
        assert mode == 0o600, "the rewrite must not widen the pin file"


def test_a_removal_that_raises_does_not_fail_the_permanent_delete(
    home, trash, store, monkeypatch
):
    # Best-effort and non-fatal, mirroring destroy_entry's other per-kind
    # branches: a delete that raised because the file was locked (Windows)
    # would be worse than the residue it failed to clear.
    _pin("box.example.com")
    store.add(SSHHost(name="box", host="box.example.com", port=22))
    store.remove("box")

    def boom(host, port=22):
        raise OSError("known_hosts is locked")

    monkeypatch.setattr(C, "remove_pinned_host_key", boom)

    ok, msg = _service(trash, store).delete_permanently(trash.list("ssh_host")[0].id)

    assert ok and msg == ""
    assert trash.list("ssh_host") == []


def test_other_kinds_still_take_the_material_path_early_return(home, trash):
    # The SSH branch sits ABOVE the material_path guard. Nothing else may have
    # changed shape: a pure-JSON kind with no material still destroys nothing
    # and raises nothing.
    from src.services.trash.store import TrashEntry

    destroy_entry(
        TrashEntry(id="x", kind="proxy", name="p", deleted_at=0.0, material_path="")
    )
