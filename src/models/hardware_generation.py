"""Hardware-list generations: how a curated list grows without moving anybody.

THE DEFECT THIS EXISTS TO CLOSE. Every seeded hardware pick in this package was
``pool[seed % len(pool)]``. The divisor is the list's length, so appending one
device / resolution / GPU changed the divisor and re-indexed a large share of
EXISTING profiles onto a different entry. Their seed never moved; the mapping
under it did. A site holding that profile's session cookie sees the machine
change — the same linkage event a rename used to cause, arriving through routine
list maintenance. The live divisors made it sharp rather than marginal:
``IOS_PRESETS`` is 2, so appending one iPhone moves the divisor 2 -> 3 and
re-indexes roughly two-thirds of iOS profiles.

THE MECHANISM. Every entry carries the generation it was ADDED IN (``since``),
and every profile carries the generation it was CREATED IN (frozen, exactly like
its fingerprint seed). A profile only ever sees entries whose ``since`` is <= its
own generation:

    visible(pool, generation) == [e for e in pool if e.since <= generation]

Every entry shipped today is ``since=0``, and a profile that predates the field
reads generation 0. So a generation-0 profile's visible pool is today's list, in
today's order, with today's LENGTH — hence today's divisor and today's pick,
permanently, however much the list grows afterwards. That equality is the whole
fix; the filter is the entire mechanism.

WHY IT SURVIVES INSERTION, NOT MERELY APPEND. The filter is by tag, not by
position, so a new entry dropped into the MIDDLE of a list is still invisible to
older profiles and their pool keeps both its contents and its order. Maintaining
these lists in a readable order therefore stays free.

HOW TO ADD HARDWARE (the whole procedure):

  1. bump ``CURRENT_HARDWARE_GENERATION`` by one;
  2. tag each new entry ``since=<that new number>``;
  3. leave every existing entry's ``since`` alone.

Never renumber a shipped entry: its ``since`` is the promise that the profiles
already pinned to it keep seeing it. Bumping the constant without tagging
anything is harmless (it just mints a new generation nothing distinguishes), but
tagging an entry with a generation NOBODY has been minted into yet makes it
unreachable until the constant catches up — so bump first.

WHAT REMOVAL WOULD DO, MEASURED (PS-54 scopes removal out; this is the note the
follow-up starts from rather than re-deriving). Deleting an entry outright is
still unsafe and this mechanism does not make it safe: deletion shortens the
visible pool of every generation that could see it, which is exactly the
re-indexing above. What the mechanism does buy is that removal becomes
EXPRESSIBLE without that: a symmetric ``retired_in`` tag, filtered as
``since <= generation < retired_in``, would keep a retired entry serving the
profiles already pinned to it (tombstoning) while hiding it from new ones. That
is deliberately NOT implemented here — choosing between tombstoning and
re-rolling is an owner-level trade-off — but the shape is left open for it: the
filter is one function, entries are tagged records rather than bare values, and
nothing anywhere indexes a pool by a hard-coded position.
"""

from typing import Protocol, TypeVar


# The generation NEW profiles are minted into. Bump by one in the same commit
# that adds entries, and tag those entries with the bumped value. See the module
# docstring for the full procedure — this constant is half of it.
#
# 0 means: nothing has been added since generations existed. Every entry in
# every list is `since=0`, so every profile — including every profile that
# predates the field — sees every entry, which is precisely the behaviour that
# shipped before this module and why introducing it moved nobody.
CURRENT_HARDWARE_GENERATION = 0


class _HasSince(Protocol):
    since: int


T = TypeVar("T", bound=_HasSince)


def visible_entries(entries: list[T], generation: int) -> list[T]:
    """The entries a profile of ``generation`` may be picked onto.

    Order is preserved, so a generation's pool is stable in both contents and
    indexing. Callers take ``len()`` of THIS, never of the underlying list —
    taking it of the raw list is the original defect.
    """
    gen = int(generation)
    return [e for e in entries if e.since <= gen]


def normalize_generation(value: object) -> int:
    """Coerce a stored/imported generation to a usable one.

    None (a profile that predates the field) and anything malformed read as 0 —
    the generation whose visible pool is the list as it shipped, i.e. what such a
    profile has always presented. A NEGATIVE value would hide every entry and
    leave an empty pool, so it clamps to 0 as well; falling back to "sees the
    original list" is the answer that cannot move an existing profile.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)
