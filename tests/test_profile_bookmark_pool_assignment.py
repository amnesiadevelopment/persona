"""PS-157: an edit that OMITS ``bookmark_pool`` must leave the stored assignment
alone — the last surviving instance of the shape ``proxy_assignment.py`` exists
to end, twenty-four lines below the comment that ends it.

``update_profile`` did ``profile.bookmark_pool = new_bookmark_pool or None`` with
the argument defaulting to ``None``, so absence and emptiness both CLEARED the
assignment and an edit made for an unrelated reason silently discarded it.

What that defeats is recoverability, not a protection — a bookmark pool guards
nothing and losing it exposes nothing. ``delete_pool`` RECORDS the profiles
referencing a pool before clearing them, precisely because doing those two halves
in the wrong order once meant "the store recorded no referencing profiles at all
and a restore silently returned a pool nothing pointed at". It computes that list
from the very field this edit path wiped, so an unrelated edit reproduced that
exact end-state through a different route — and the store could not defend
itself, because by the time it ran the reference was already gone.

Every assertion here binds to the PERSISTED value or to what the trash actually
recorded, never to "a sentinel constant exists" and never to "a helper was
called": restore the old line with the rest of the diff in place and
``test_unrelated_edit_preserves_pool`` and the cascade test go red.
"""
import pathlib
from types import SimpleNamespace

import pytest

from src.services.bookmark.store import BookmarkStore
from src.services.profile.manager import ProfileManager
from src.services.profile.pool_assignment import POOL_NONE, POOL_UNCHANGED
from src.services.trash.store import TrashStore


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Real stores wired exactly as the Container wires them, so the cascade
    below runs the production path rather than a simulation of it."""
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
    bstore = BookmarkStore(path=str(tmp_path / "bookmarks.json"))
    bstore.set_trash(trash)
    bstore.set_profile_manager(pm)
    return SimpleNamespace(pm=pm, bstore=bstore, trash=trash, tmp=tmp_path)


# --- AC1 / AC2: the defect itself -------------------------------------------


def test_unrelated_edit_preserves_pool(mgr):
    """AC1. Asserts on the STORED value after a real update_profile.

    AC2 (premise inversion): this FAILS on origin/main today — executed against
    0fe5cfe, `corp-pool` came back None.
    """
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.profiles["acct"].bookmark_pool == "corp-pool"

    assert mgr.update_profile("acct", "acct", new_notes="unrelated note") is True

    assert mgr.profiles["acct"].bookmark_pool == "corp-pool"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"new_notes": "a note"},
        {"new_search_engine": "google"},
        {"new_tags": ["x"]},
    ],
    ids=["notes", "search_engine", "tags"],
)
def test_every_unrelated_field_edit_preserves_pool(mgr, kwargs):
    """The three shapes the verify lane actually calls with (AC7's evidence)."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.update_profile("acct", "acct", **kwargs) is True
    assert mgr.profiles["acct"].bookmark_pool == "corp-pool"


def test_rename_preserves_pool(mgr):
    """A rename is an edit like any other — the pool travels with the profile."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.update_profile("acct", "acct-renamed", new_notes="n") is True
    assert mgr.profiles["acct-renamed"].bookmark_pool == "corp-pool"


def test_empty_string_reads_as_unchanged_not_as_clear(mgr):
    """An empty value is UNCHANGED, never a clear. That is the whole fix: a
    caller that passes a falsy value has not SAID anything about the pool."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.update_profile("acct", "acct", new_bookmark_pool="") is True
    assert mgr.profiles["acct"].bookmark_pool == "corp-pool"


def test_explicit_unchanged_directive_preserves(mgr):
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert (
        mgr.update_profile("acct", "acct", new_bookmark_pool=POOL_UNCHANGED) is True
    )
    assert mgr.profiles["acct"].bookmark_pool == "corp-pool"


# --- AC3: clearing stays expressible ----------------------------------------


def test_caller_that_says_clear_clears(mgr):
    """AC3. If the design made clearing unexpressible, the design is wrong."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.update_profile("acct", "acct", new_bookmark_pool=POOL_NONE) is True
    assert mgr.profiles["acct"].bookmark_pool is None


def test_clear_survives_a_round_trip(mgr, tmp_path):
    """A cleared pool must STAY cleared through a reload — the clear is
    persisted, not merely applied in memory."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    mgr.update_profile("acct", "acct", new_bookmark_pool=POOL_NONE)
    assert ProfileManager().profiles["acct"].bookmark_pool is None


def test_pool_can_still_be_changed_to_another_pool(mgr):
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    assert mgr.update_profile("acct", "acct", new_bookmark_pool="other") is True
    assert mgr.profiles["acct"].bookmark_pool == "other"


def test_directive_is_never_stored_as_a_pool_name(mgr):
    """A directive must never survive into the model as if it were a name —
    the reason it is a class and not a sentinel string."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    mgr.update_profile("acct", "acct", new_bookmark_pool=POOL_UNCHANGED)
    stored = mgr.profiles["acct"].bookmark_pool
    assert isinstance(stored, str)
    # And it must survive serialisation as a real name, not a repr.
    assert mgr.profiles["acct"].to_dict()["bookmark_pool"] == "corp-pool"


def test_creation_never_stores_a_directive(mgr):
    """The dialog shares one value across create and edit, so a directive
    legitimately reaches add_profile. It must land as "no pool"."""
    mgr.add_profile("fresh", None, "windows", bookmark_pool=POOL_NONE)
    assert mgr.profiles["fresh"].bookmark_pool is None
    assert ProfileManager().profiles["fresh"].bookmark_pool is None


# --- AC4: round trip ---------------------------------------------------------


def test_preserved_pool_survives_a_fresh_manager(mgr):
    """AC4. Write, construct a fresh ProfileManager against the same file, and
    assert the assignment survived — the persisted value is the observer."""
    mgr.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    mgr.update_profile("acct", "acct", new_notes="unrelated note")

    reloaded = ProfileManager()
    assert reloaded.profiles["acct"].bookmark_pool == "corp-pool"


# --- AC5: the cascade, driven end to end ------------------------------------


def test_unrelated_edit_does_not_defeat_pool_restore(env):
    """AC5 — the criterion the slice exists for.

    A profile referencing a pool -> an unrelated edit -> delete_pool -> the trash
    entry's recorded `profiles` list must STILL name that profile, and
    restore_pool must put the reference back.

    On origin/main the edit wiped the reference first, so delete_pool recorded
    [] and the restore returned a pool nothing pointed at — the exact end-state
    delete_pool owns both halves to make impossible.
    """
    env.pm.add_profile("acct", None, "windows", bookmark_pool="corp-pool")
    env.bstore.add_pool("corp-pool", [])

    # The unrelated edit that used to discard the reference.
    env.pm.update_profile("acct", "acct", new_notes="unrelated note")

    assert env.bstore.delete_pool("corp-pool") is True

    entry = env.trash.list("pool")[0]
    assert entry.name == "corp-pool"
    # What the trash actually RECORDED — the list restore replays.
    assert entry.payload["profiles"] == ["acct"]

    # delete_pool still drops the live reference, as it must.
    assert env.pm.profiles["acct"].bookmark_pool is None

    ok, msg = env.bstore.restore_pool(entry)
    assert ok is True, msg
    # The reference is BACK on the profile, and persisted.
    assert env.pm.profiles["acct"].bookmark_pool == "corp-pool"
    assert ProfileManager().profiles["acct"].bookmark_pool == "corp-pool"
