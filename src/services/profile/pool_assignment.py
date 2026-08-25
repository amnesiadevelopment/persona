"""How ``ProfileManager.update_profile`` decides what an edit means about a
profile's bookmark pool.

The sibling of ``proxy_assignment.py``, for the field that was the LAST place
the shape that module exists to end still survived. ``update_profile`` did
``profile.bookmark_pool = new_bookmark_pool or None`` with the argument
defaulting to ``None``, so **absence and emptiness both cleared it** and an edit
made for an unrelated reason — a note, a search engine, a rename — silently
discarded the assignment.

What that cost is not a leak, and must not be dressed as one: a bookmark pool
protects nothing. It is recoverability. ``delete_pool`` RECORDS the profiles
referencing a pool before clearing them (``services/bookmark/store.py``), and
``restore_pool`` replays that list — the store owns both halves precisely
because doing them in the wrong order once meant "the store recorded no
referencing profiles at all and a restore silently returned a pool nothing
pointed at". That recorded list is computed from the very field this module
guards. An unrelated edit that wiped the reference beforehand reproduced that
exact end-state through a different route, and the store could not defend
itself against it: by the time ``delete_pool`` ran, the reference was already
gone.

A SIBLING rather than a generalisation of ``proxy_assignment.py``: that module
is shipped and correct, its docstring carries proxy-specific reasoning about
real-IP exposure that does not describe a bookmark pool, and rewriting it to
serve two fields would put a working protection at risk to save a few lines.
The idiom is what is being reused here, deliberately, rather than the code.

Neither ``""`` nor ``None`` could carry the new meaning, because both were
already spoken on this path — ``""`` is what the profile dialog sends for its
"(none)" option, and ``None`` is what gets stored. Hence two explicit
directives:

``POOL_UNCHANGED``
    Leave the stored pool exactly as it is. The DEFAULT, so a caller that says
    nothing changes nothing.

``POOL_NONE``
    Clear the assignment. The operator deliberately chose no pool.

An empty value (``""`` or ``None``) is deliberately read as UNCHANGED rather
than as a clear. That is the whole point: clearing a pool is now something a
caller has to SAY, never something it can do by omitting a value or by passing
a falsy one. A caller that means "no pool" passes ``POOL_NONE`` — the profile
dialog does, and the REST lane translates an explicitly-supplied empty
``bookmark_pool`` field into it, because a route is the one layer that can tell
an omitted key from a supplied empty one.

The failure mode this trades for is recoverable, not silent: a caller that
meant to clear and forgot to say so leaves the profile pointing at a pool,
which a later edit can undo. The mode it removes destroys the reference that a
restore needs, and nothing left behind says it was ever there.
"""

from __future__ import annotations


class PoolDirective:
    """An instruction about the bookmark_pool field that is not a pool name.

    A distinct class rather than a sentinel string: a directive can then never
    compare equal to a pool name, never be stored as one, and never survive a
    round-trip through JSON pretending to be one. Compared with ``is``.
    """

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self._label}>"


#: Leave the stored bookmark pool alone. The default for every update.
POOL_UNCHANGED = PoolDirective("POOL_UNCHANGED")

#: Clear the assignment — the operator deliberately chose no pool.
POOL_NONE = PoolDirective("POOL_NONE")


def resolve_pool_assignment(
    new_pool: str | PoolDirective | None,
    stored: str | None,
) -> str | None:
    """The bookmark pool an update should RESULT IN, given what the caller
    supplied.

    ``stored`` is the profile's current pool, returned untouched whenever the
    caller did not clearly ask for something else.
    """
    if new_pool is POOL_NONE:
        return None
    if new_pool is POOL_UNCHANGED:
        return stored
    # A directive that is neither of the two above is not a name and must never
    # be stored as one; fall back to the safe reading.
    if isinstance(new_pool, PoolDirective):
        return stored
    # Falsy means the caller supplied no value. Preserving here is what makes
    # "clear it" something a caller has to say explicitly.
    if not new_pool:
        return stored
    return new_pool


def pool_for_new_profile(pool: str | PoolDirective | None) -> str | None:
    """The bookmark pool a NEWLY CREATED profile should carry.

    Creation has no stored value to preserve, so both directives mean the same
    thing here — no pool. Present so a caller (the profile dialog, which sends
    ``POOL_NONE`` for a deliberate "(none)" on both paths) cannot accidentally
    store a directive object as if it were a pool name.
    """
    if isinstance(pool, PoolDirective) or not pool:
        return None
    return pool
