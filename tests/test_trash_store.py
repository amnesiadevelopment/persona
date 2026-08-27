"""The trash store itself: where a deleted record is parked, and how well.

The charter condition this store is built against is that the recovery layer
must not become a second, LESS-guarded copy of the operator's identity — so the
tests here are as much about protection (location, permissions, atomicity,
quarantine) as they are about round-tripping.
"""
import json
import os
import pathlib
import stat
import sys

import pytest

from src.services.trash.store import RETENTION_DAYS, TrashEntry, TrashStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    return TrashStore()


def test_add_then_list_returns_the_entry(store):
    entry = store.add("proxy", "exit-us", {"proxy": {"name": "exit-us"}})
    assert [e.id for e in store.list()] == [entry.id]
    assert store.get(entry.id).name == "exit-us"


def test_add_persists_so_a_fresh_store_still_has_it(store, tmp_path):
    store.add("bookmark", "leaks", {"bookmark": {"url": "https://x"}})
    fresh = TrashStore()
    assert [e.name for e in fresh.list()] == ["leaks"]
    assert fresh.list()[0].payload["bookmark"]["url"] == "https://x"


def test_unknown_kind_is_refused(store):
    with pytest.raises(ValueError):
        store.add("wallet", "x", {})


def test_list_is_newest_first(store):
    clock = {"t": 1000.0}
    s = TrashStore(now=lambda: clock["t"])
    s.add("proxy", "first", {})
    clock["t"] = 2000.0
    s.add("proxy", "second", {})
    assert [e.name for e in s.list()] == ["second", "first"]


def test_list_filters_by_kind(store):
    store.add("proxy", "p", {})
    store.add("bookmark", "b", {})
    assert [e.name for e in store.list("bookmark")] == ["b"]


def test_trash_file_lives_under_persona_home_by_default(tmp_path, monkeypatch):
    # The trashed record must not be written OUTSIDE PERSONA_HOME — that home is
    # 0700 and is what protects every live store.
    monkeypatch.delenv("PERSONA_TRASH_FILE", raising=False)
    import src.core.config as cfg
    import src.services.trash.store as mod

    monkeypatch.setattr(cfg, "PERSONA_HOME", str(tmp_path), raising=False)
    assert mod.trash_file() == str(tmp_path / "trash.json")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_trash_file_is_written_owner_only(store, tmp_path):
    # Payloads carry proxy creds / SSH passwords / .p12 passwords verbatim, so
    # the trashed copy must be written at the SAME 0600 the live stores use —
    # never looser than the record it came from.
    #
    # POSIX-ONLY, AND DELIBERATELY SO. On Windows os.chmod only toggles the
    # read-only bit and cannot restrict WHO may read a file, so the mode reads
    # back 0o666 no matter what the writer asked for; the protection there is
    # the per-user ACL inherited from the .persona home, not the mode bits.
    # src/utils/atomic.py's module docstring states exactly this. Asserting the
    # POSIX bits unconditionally tested the PLATFORM, not the product — and it
    # was escalated twice as a suspected Invariant #0 breach before being
    # cleared, which is the concrete cost of leaving it unstated.
    #
    # THE GUARANTEE ITSELF IS NOT DROPPED ON WINDOWS: what this test protects is
    # that the trashed copy is never written LOOSER than the live record it came
    # from, and `test_trash_file_is_not_written_looser_than_the_live_store`
    # below asserts that in terms every platform can express — same writer, same
    # `private=True`, so the trash file and the live store agree. Same marker as
    # tests/test_atomic_write.py::test_private_mode_sets_0600, which declines
    # this identical assertion for this identical reason.
    store.add("ssh_host", "box", {"host": {"password": "hunter2"}})
    path = pathlib.Path(os.environ["PERSONA_TRASH_FILE"])
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_trash_file_is_not_written_looser_than_the_live_store(store, tmp_path):
    """The WINDOWS-RUNNABLE half of the guarantee above, and the reason that one
    may decline to run without dropping it.

    "0600" is one platform's SPELLING of the real rule: the parked copy of a
    secret must be protected no less than the live record it was parked from. On
    Windows the mode bits cannot express that, but the rule is still checkable —
    compare the trash file against a file the LIVE store path wrote in the same
    place, and require they agree. On POSIX that is 0600 == 0600; on Windows it
    is 0666 == 0666, which is not a claim that either is restrictive, only that
    trashing did not make anything WORSE. That is precisely the invariant, and
    it is the half a bare mode assertion could never state portably.
    """
    from src.utils.atomic import atomic_write_json

    store.add("ssh_host", "box", {"host": {"password": "hunter2"}})
    trashed = pathlib.Path(os.environ["PERSONA_TRASH_FILE"])

    # what the LIVE stores' own writer produces for a credential file, here
    live = tmp_path / "live_reference.json"
    atomic_write_json(str(live), {"host": {"password": "hunter2"}}, private=True)

    assert stat.S_IMODE(trashed.stat().st_mode) == stat.S_IMODE(live.stat().st_mode), (
        "the trashed copy is written LOOSER than the live record it came from"
    )


def test_save_is_atomic_leaving_no_temp_file(store, tmp_path):
    store.add("proxy", "p", {})
    names = sorted(f.name for f in tmp_path.iterdir())
    assert names == ["trash.json"], names


def test_pop_removes_and_returns(store):
    entry = store.add("proxy", "p", {})
    assert store.pop(entry.id).id == entry.id
    assert store.list() == []
    assert store.pop(entry.id) is None


def test_pop_persists_the_removal(store):
    entry = store.add("proxy", "p", {})
    store.pop(entry.id)
    assert TrashStore().list() == []


def test_put_back_refiles_an_entry(store):
    entry = store.add("proxy", "p", {})
    popped = store.pop(entry.id)
    store.put_back(popped)
    assert [e.id for e in TrashStore().list()] == [entry.id]


def test_clear_empties_and_returns_what_was_there(store):
    store.add("proxy", "a", {})
    store.add("bookmark", "b", {})
    cleared = store.clear()
    assert sorted(e.name for e in cleared) == ["a", "b"]
    assert store.list() == []
    assert TrashStore().list() == []


def test_expired_reports_only_entries_past_the_window(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    clock = {"t": 0.0}
    s = TrashStore(now=lambda: clock["t"])
    s.add("proxy", "old", {})
    clock["t"] = 20 * 86400
    s.add("proxy", "recent", {})
    # 31 days after the first, 11 after the second
    clock["t"] = 31 * 86400
    assert [e.name for e in s.expired()] == ["old"]


def test_expired_is_empty_before_the_window_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    clock = {"t": 0.0}
    s = TrashStore(now=lambda: clock["t"])
    s.add("proxy", "p", {})
    clock["t"] = (RETENTION_DAYS - 1) * 86400
    assert s.expired() == []


def test_entry_knows_when_it_expires():
    e = TrashEntry(id="x", kind="proxy", name="p", deleted_at=1000.0)
    assert e.expires_at() == 1000.0 + RETENTION_DAYS * 86400


@pytest.mark.parametrize(
    "kind,secret",
    [
        ("proxy", True),
        ("ssh_host", True),
        ("certificate", True),
        ("profile", False),
        ("bookmark", False),
        ("pool", False),
    ],
)
def test_entry_reports_whether_it_still_holds_secret_material(kind, secret):
    # The honest consequence the interface must state rather than hide: trashing
    # a proxy / SSH host / certificate does NOT remove its secret from disk.
    e = TrashEntry(id="x", kind=kind, name="n", deleted_at=0.0)
    assert e.holds_secret_material is secret


def test_a_malformed_record_does_not_abort_the_whole_load(tmp_path, monkeypatch):
    # One bad row must not take the rest with it — the next save would then
    # overwrite trash.json with only what parsed, destroying the very records
    # this store exists to preserve.
    path = tmp_path / "trash.json"
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(path))
    path.write_text(
        json.dumps(
            {
                "bad": {"name": "no-kind-key"},
                "good": {
                    "id": "good",
                    "kind": "proxy",
                    "name": "keeper",
                    "deleted_at": 5.0,
                    "payload": {},
                },
            }
        ), encoding="utf-8"
    )
    assert [e.name for e in TrashStore().list()] == ["keeper"]


def test_an_unreadable_trash_file_is_quarantined_not_overwritten(
    tmp_path, monkeypatch
):
    # Inherited from StoreGuardMixin: an unreadable trash.json still holds every
    # recoverable record, so it is moved aside rather than overwritten.
    path = tmp_path / "trash.json"
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(path))
    path.write_text("{not json", encoding="utf-8")
    s = TrashStore()
    assert s.list() == []
    backups = [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(backups) == 1, list(tmp_path.iterdir())
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_new_id_is_an_opaque_hex_token(store):
    token = store.new_id()
    assert token and all(c in "0123456789abcdef" for c in token)
