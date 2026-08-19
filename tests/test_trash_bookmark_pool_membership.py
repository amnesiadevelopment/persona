"""Regression for the QA defect: restoring a bookmark before its pool silently
dropped the membership forever.

The trash promises a trashed record "restores to the same working state,
including its membership relationships where it had them." It did — but only on
one of the two restore orders, and the operator was never told which:

* ``restore_bookmark`` re-added the bookmark to its recorded pools, but a pool
  still IN THE TRASH is not in ``self.pools``, so the re-add was skipped.
* ``restore_pool`` rebuilt the pool from its own snapshot filtered to
  ``n in self.bookmarks`` — and that snapshot was captured AFTER the bookmark
  delete had already stripped the name, so the member was not in it either.

Each write was individually defensible; together they lost the edge. Worse, the
SAFE order inverted with the DELETION order, so there was no rule the trash page
could have taught. By the time it was visible the trash was empty and there was
nothing left to undo from — a silent, unrecoverable loss inside the recovery
mechanism itself.

These tests drive the real store methods the UI and the REST lane both call, and
assert the membership actually came back — not merely that restore returned
True. Both restore orders are covered for both deletion orders, because passing
on only one of them is exactly how this shipped.
"""
import pathlib
from types import SimpleNamespace

import pytest

from src.services.bookmark.store import BookmarkStore
from src.services.trash.service import TrashService
from src.services.trash.store import TrashStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A real BookmarkStore + TrashStore wired as the Container wires them."""
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
    trash = TrashStore()
    bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    bstore.set_trash(trash)
    svc = TrashService(trash, bookmark_store=bstore)
    return SimpleNamespace(bstore=bstore, trash=trash, svc=svc, tmp=tmp_path)


def _find(trash, kind, name):
    """Locate a trashed entry using only the store API that predates this fix.

    Deliberately NOT TrashStore.find(): that helper ships WITH the fix, so a test
    that reached for it would fail against the old code with an AttributeError
    from its own scaffolding and prove nothing about the defect. Going through
    list() keeps the revert-check honest — these tests fail on the MEMBERSHIP
    assertion, which is the behaviour under test.
    """
    matches = [e for e in trash.list(kind) if e.name == name]
    assert matches, f"expected a trashed {kind} named {name!r}"
    return matches[0]


def _entry_id(env, kind, name):
    return _find(env.trash, kind, name).id


def _setup_pool(env, members=("mail", "work-login")):
    for n in members:
        env.bstore.add(n, f"https://{n}.example")
    env.bstore.add_pool("work-pool", list(members))


# --- the four order combinations: every one must preserve the membership ---


@pytest.mark.parametrize("delete_order", [("bookmark", "pool"), ("pool", "bookmark")])
@pytest.mark.parametrize("restore_order", [("bookmark", "pool"), ("pool", "bookmark")])
def test_membership_survives_every_delete_and_restore_order(
    env, delete_order, restore_order
):
    # The defect was order-dependent in a way the operator could not learn: the
    # safe restore order INVERTED with the deletion order. So the guarantee has
    # to hold for all four combinations, not for the one a test happened to pick.
    _setup_pool(env)

    for what in delete_order:
        if what == "bookmark":
            env.bstore.delete("work-login")
        else:
            env.bstore.delete_pool("work-pool")

    ids = {w: _entry_id(env, w, "work-login" if w == "bookmark" else "work-pool")
           for w in ("bookmark", "pool")}
    for what in restore_order:
        ok, msg = env.svc.restore(ids[what])
        assert ok, f"restoring the {what} failed: {msg}"

    assert env.bstore.get_pool("work-pool").bookmark_names == ["mail", "work-login"], (
        "the bookmark must come back as a member of its pool whichever order the "
        "two were deleted and restored in"
    )


@pytest.mark.parametrize("restore_order", [("bookmark", "pool"), ("pool", "bookmark")])
def test_the_profile_toolbar_actually_resolves_the_restored_member(env, restore_order):
    # The membership matters because it IS the launch toolbar. Assert the thing
    # the operator sees, not just the list the store holds.
    _setup_pool(env)
    env.bstore.delete("work-login")
    env.bstore.delete_pool("work-pool")

    ids = {w: _entry_id(env, w, "work-login" if w == "bookmark" else "work-pool")
           for w in ("bookmark", "pool")}
    for what in restore_order:
        env.svc.restore(ids[what])

    toolbar = [b.name for b in env.bstore.resolve_selection("work-pool", None)]
    assert toolbar == ["mail", "work-login"], (
        "the restored bookmark must appear on the toolbar the pool resolves to"
    )


# --- position: a pool's order IS the toolbar order ---


def test_a_restored_member_returns_to_its_original_position(env):
    # Appending the member to the END hands back a visibly DIFFERENT toolbar,
    # which is not "the same working state". It must land where it was.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("b")
    env.bstore.delete_pool("work-pool")

    env.svc.restore(_entry_id(env, "bookmark", "b"))
    env.svc.restore(_entry_id(env, "pool", "work-pool"))

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"], (
        "a middle member must come back in the middle, not appended to the end"
    )


def test_the_position_is_preserved_on_the_other_restore_order_too(env):
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("b")
    env.bstore.delete_pool("work-pool")

    env.svc.restore(_entry_id(env, "pool", "work-pool"))
    env.svc.restore(_entry_id(env, "bookmark", "b"))

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"]


def test_a_bookmark_restored_into_a_live_pool_keeps_its_position(env):
    # The simplest lane — the pool was never deleted at all.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("b")

    env.svc.restore(_entry_id(env, "bookmark", "b"))

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"]


# --- a bookmark that belonged to SEVERAL pools ---


def test_every_pool_gets_its_member_back(env):
    for n in ("a", "b"):
        env.bstore.add(n, f"https://{n}.example")
    env.bstore.add_pool("p1", ["a", "b"])
    env.bstore.add_pool("p2", ["b"])

    env.bstore.delete("b")
    env.bstore.delete_pool("p1")
    env.bstore.delete_pool("p2")

    env.svc.restore(_entry_id(env, "bookmark", "b"))
    env.svc.restore(_entry_id(env, "pool", "p1"))
    env.svc.restore(_entry_id(env, "pool", "p2"))

    assert env.bstore.get_pool("p1").bookmark_names == ["a", "b"]
    assert env.bstore.get_pool("p2").bookmark_names == ["b"]


# --- the edge must NOT resurrect something genuinely destroyed ---


def test_a_permanently_deleted_bookmark_is_not_resurrected_into_its_pool(env):
    # "Delete permanently" really cannot be undone — parking the edge must not
    # smuggle the member back as a name that resolves to nothing.
    _setup_pool(env)
    env.bstore.delete("work-login")
    env.bstore.delete_pool("work-pool")

    env.svc.delete_permanently(_entry_id(env, "bookmark", "work-login"))
    env.svc.restore(_entry_id(env, "pool", "work-pool"))

    assert env.bstore.get_pool("work-pool").bookmark_names == ["mail"]
    assert "work-login" not in env.bstore.bookmarks


def test_a_bookmark_restores_after_its_pool_was_permanently_deleted(env):
    # The counterpart is gone for good; the bookmark must still come back, and
    # must not recreate a phantom pool.
    _setup_pool(env)
    env.bstore.delete("work-login")
    env.bstore.delete_pool("work-pool")

    env.svc.delete_permanently(_entry_id(env, "pool", "work-pool"))
    ok, msg = env.svc.restore(_entry_id(env, "bookmark", "work-login"))

    assert ok, msg
    assert "work-login" in env.bstore.bookmarks
    assert env.bstore.get_pool("work-pool") is None, (
        "restoring a bookmark must not recreate a pool the operator destroyed"
    )


# --- durability: a parked edge is no less real than the entry it rides on ---


def test_a_parked_edge_survives_a_reload_from_disk(env, tmp_path):
    # The edge is parked on a still-trashed entry, so it has to be PERSISTED —
    # an in-memory-only amendment would evaporate on the next app start, which
    # is precisely when a 30-day trash gets used.
    _setup_pool(env)
    env.bstore.delete("work-login")
    env.bstore.delete_pool("work-pool")
    env.svc.restore(_entry_id(env, "bookmark", "work-login"))

    # Fresh objects reading the same files, as a restart would.
    reloaded_trash = TrashStore()
    reloaded_bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    reloaded_bstore.set_trash(reloaded_trash)
    reloaded_svc = TrashService(reloaded_trash, bookmark_store=reloaded_bstore)

    entry = reloaded_trash.find("pool", "work-pool")
    assert entry is not None
    ok, msg = reloaded_svc.restore(entry.id)

    assert ok, msg
    assert reloaded_bstore.get_pool("work-pool").bookmark_names == [
        "mail",
        "work-login",
    ], "the parked membership must survive an app restart"


def test_restoring_is_still_refused_when_the_name_is_taken(env):
    # The refusal is a load-bearing property of the trash (a profile's identity
    # derives from its name), and the membership fix must not weaken it.
    _setup_pool(env)
    env.bstore.delete("work-login")
    env.bstore.add("work-login", "https://impostor.example")

    entry_id = _entry_id(env, "bookmark", "work-login")
    ok, msg = env.svc.restore(entry_id)

    assert not ok
    assert "already exists" in msg
    assert env.trash.get(entry_id) is not None, (
        "a refused restore must leave the entry recoverable"
    )


# --- MULTI-MEMBER position: the axis the first position fix was silent on ---
#
# Every test above deletes exactly ONE bookmark, which is the one case an
# absolute integer index gets right. With a second member involved it broke, and
# the safe restore order inverted with the DELETION order again — the same
# unlearnable shape as the original QA defect, one layer down:
#
#   pool ['a','b','c']; delete 'a' then 'b'  ->  'b' recorded index 0, not 1,
#   because 'a' had already been stripped from the list it was measured against.
#   restore 'a' then 'b'  ->  ['b','a','c'].
#
#   pool deleted first, then 'a' and 'b'; restore pool first  ->  each absolute
#   index lands in a partially-repopulated list  ->  ['a','c','b'].
#
# An absolute index is not a stable descriptor of position when an arbitrary
# subset of peers is absent, so the descriptor is now the pool's ORDERING and a
# member is placed after the last peer that preceded it and is currently present.
# These tests pin that rule on both reproductions, in every restore order.


@pytest.mark.parametrize(
    "restore_order",
    [("a", "b"), ("b", "a")],
    ids=["restore-in-deletion-order", "restore-in-reverse-order"],
)
def test_two_members_deleted_from_a_live_pool_both_return_to_their_places(
    env, restore_order
):
    # Reproduction A. Deleting two bookmarks one after the other is ordinary
    # operator behaviour — two plain per-bookmark confirm dialogs — and the
    # second delete must not record a position measured against a pool the first
    # delete had already changed.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("a")
    env.bstore.delete("b")

    for name in restore_order:
        ok, msg = env.svc.restore(_entry_id(env, "bookmark", name))
        assert ok, f"restoring {name} failed: {msg}"

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"], (
        "both members must come back where they were, whichever order they are "
        "restored in — a second deleted member must not shift the first"
    )


@pytest.mark.parametrize(
    "restore_order",
    [("a", "b"), ("b", "a")],
    ids=["restore-in-deletion-order", "restore-in-reverse-order"],
)
def test_two_members_return_to_their_places_when_the_pool_was_deleted_first(
    env, restore_order
):
    # Reproduction B. The pool comes back holding only the members that were
    # never deleted, so each restored member lands in a list still missing its
    # peers — the case an absolute index cannot survive and the clamp silently
    # turned into "appended to the end".
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete_pool("work-pool")
    env.bstore.delete("a")
    env.bstore.delete("b")

    ok, msg = env.svc.restore(_entry_id(env, "pool", "work-pool"))
    assert ok, msg
    assert env.bstore.get_pool("work-pool").bookmark_names == ["c"], (
        "only the undeleted member is live yet; the other two are still trashed"
    )

    for name in restore_order:
        ok, msg = env.svc.restore(_entry_id(env, "bookmark", name))
        assert ok, f"restoring {name} failed: {msg}"

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"]


@pytest.mark.parametrize(
    "restore_order",
    [("a", "b"), ("b", "a")],
    ids=["restore-in-deletion-order", "restore-in-reverse-order"],
)
def test_two_members_return_to_their_places_when_the_pool_is_restored_last(
    env, restore_order
):
    # The mirror of the case above: both members come back while the pool is
    # still trashed, so each edge is parked on the POOL's snapshot and the
    # snapshot itself has to keep them in the right order relative to each other.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("a")
    env.bstore.delete("b")
    env.bstore.delete_pool("work-pool")

    for name in restore_order:
        ok, msg = env.svc.restore(_entry_id(env, "bookmark", name))
        assert ok, f"restoring {name} failed: {msg}"
    ok, msg = env.svc.restore(_entry_id(env, "pool", "work-pool"))
    assert ok, msg

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c"]


def test_the_toolbar_of_a_multi_member_restore_resolves_in_the_original_order(env):
    # A pool's order IS the toolbar order, so assert what the operator actually
    # sees rather than only the list the store holds.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("a")
    env.bstore.delete("b")
    env.svc.restore(_entry_id(env, "bookmark", "a"))
    env.svc.restore(_entry_id(env, "bookmark", "b"))

    toolbar = [b.name for b in env.bstore.resolve_selection("work-pool", None)]
    assert toolbar == ["a", "b", "c"], (
        "the restored toolbar must be the toolbar the operator had before"
    )


def test_a_permanently_deleted_peer_does_not_come_back_as_a_member(env):
    # The ordering now folds in peers that are still trashed, so it must still
    # refuse to carry one the operator DESTROYED. Deleting 'a' permanently and
    # then trashing the pool must not smuggle 'a' back into its snapshot.
    _setup_pool(env, members=("a", "b", "c"))
    env.bstore.delete("a")
    env.svc.delete_permanently(_entry_id(env, "bookmark", "a"))
    env.bstore.delete_pool("work-pool")

    ok, msg = env.svc.restore(_entry_id(env, "pool", "work-pool"))
    assert ok, msg
    assert env.bstore.get_pool("work-pool").bookmark_names == ["b", "c"]
    assert "a" not in env.bstore.bookmarks, (
        "reconstructing the ordering must not resurrect a permanently deleted "
        "record — 'delete permanently' really cannot be undone"
    )


def test_three_members_deleted_and_restored_in_a_rotated_order(env):
    # Three absent peers at once, restored in an order that matches neither the
    # deletion order nor its reverse — the general case the anchor rule exists
    # for, rather than the two orderings a pair happens to have.
    _setup_pool(env, members=("a", "b", "c", "d"))
    for name in ("c", "a", "b"):
        env.bstore.delete(name)

    for name in ("b", "c", "a"):
        ok, msg = env.svc.restore(_entry_id(env, "bookmark", name))
        assert ok, f"restoring {name} failed: {msg}"

    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "b", "c", "d"]


def test_a_legacy_entry_without_a_recorded_ordering_still_restores(env, tmp_path):
    # Entries already sitting in a real operator's trash.json were written by the
    # previous code: they carry the old absolute-index key and no ordering at
    # all. Restoring one must still return the bookmark and its membership —
    # degrading to "at the end", which is the honest best available answer when
    # nothing recorded where it sat — rather than raising on the missing key.
    _setup_pool(env, members=("a", "c"))
    entry = env.trash.add(
        "bookmark",
        "b",
        {
            "bookmark": {"name": "b", "url": "https://b.example"},
            "pools": ["work-pool"],
            "pool_positions": {"work-pool": 1},
        },
    )

    ok, msg = env.svc.restore(entry.id)

    assert ok, msg
    assert "b" in env.bstore.bookmarks
    assert env.bstore.get_pool("work-pool").bookmark_names == ["a", "c", "b"]
