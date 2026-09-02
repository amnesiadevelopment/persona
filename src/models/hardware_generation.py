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

THE PICK SITES, ENUMERATED. An earlier revision of this docstring asserted the
sentence above as a universal without listing what it ranged over, and a review
falsified it: two live pools were missing. So the set is written out here, and
the claim is that THESE SEVEN are all of them:

  1. ``device_presets.pick_preset``   — ANDROID_PRESETS (3)
  2. ``device_presets.pick_preset``   — IOS_PRESETS (2)
  3. ``device_presets.pick_touch_points`` — ANDROID_TOUCH_POINTS (2), consumed
     by process.py; was a bare ``(5, 10)[seed % 2]`` tuple, 50% moved on append
  4. ``resolution.resolve_resolution`` — DESKTOP_RESOLUTIONS (9)
  5. ``gpu_ext`` ``pick()``            — WIN/MAC/ANDROID/LINUX GPU pools
  6. ``device_ext`` ``ALL_RES``        — WIN_SCREEN_RESOLUTIONS (9) /
     MAC_SCREEN_RESOLUTIONS (5), the pool that actually sets screen.width for
     an "auto" profile. Lifted out of the JS string into tagged Python records
     behind ``device_ext.SCREEN_RES_POOLS`` by PS-264; before that it was a JS
     array literal no census guard could iterate, and an untagged append moved
     18 of 20 generation-0 profiles on BOTH arms while the suite stayed green.
  7. ``device_ext`` ``CORES_MEMORY``   — hardwareConcurrency + deviceMemory,
     rendered into BOTH realms (page + the applyHwPatch worker twin); 82% moved

HOW THAT SET WAS DERIVED, so it can be re-derived rather than trusted: the
defect is a pool index whose divisor is a POOL LENGTH, so the sweep is for
``% len(``, ``% <arr>.length``, any indexing of a literal by a seed expression,
and the callers of any generic ``pick()`` helper (a helper hides its call sites
from a ``%`` grep — that is how sites 3 and 7 were missed). ``_resolve_seed`` in
invisible_launch.py matches a ``%`` grep but is NOT in this class: its divisor is
the constant ``2**31`` and it derives a seed rather than indexing a pool.

THE MECHANISM. Every entry carries the generation it was ADDED IN (``since``),
and every profile carries the generation it was CREATED IN (frozen, exactly like
its fingerprint seed). A profile only ever sees entries whose ``since`` is <= its
own generation:

    visible(pool, generation) == [e for e in pool if e.since <= generation]

An entry that shipped BEFORE generations existed carries no ``since`` and reads
as ``since=0``, and a profile that predates the field reads generation 0. So a
generation-0 profile's visible pool is the list AS IT STOOD AT GENERATION 0 —
those entries, in that order, with that LENGTH — hence that divisor and that
pick, permanently, however much the list grows afterwards. That equality is the
whole fix; the filter is the entire mechanism.

⚠️ THE VISIBLE POOL IS NOT THE WHOLE LIST, and has not been since PS-183. That
sentence above once read "today's list ... with today's LENGTH", which was true
only while every shipped entry was ``since=0``. It is now false for ``MAC_GPUS``:
the pool holds 11 entries, nine of them ``since=1``, so a generation-0 macOS
profile's visible pool is 2 of 11 — and its collision probability is the 50.0%
of that two-entry pool, NOT the 9.1% of the eleven. Any figure derived from a
pool's raw LENGTH is therefore a statement about the newest generation only, and
must not be quoted as the number the installed base sees. Read
``len(visible_entries(pool, generation))``, never ``len(pool)``.

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
# Generation 1 (PS-183): nine entries were added to ``gpu_ext``'s MAC_GPUS,
# widening it from 2 to 11. That pool's two-entry form collided 50.0% of the
# time — a shared cross-profile identifier and a Level 2 (mutual unlinkability)
# breach — and widening was the only lever, deferring to the engine having been
# measured and rejected (76.9%, worse). The nine are tagged ``since=1`` and this
# constant was bumped in the same commit, per the procedure above, so no
# EXISTING profile's visible pool changes length and none is re-indexed onto a
# different card. Generation 0 keeps the 2-entry pool it has always presented
# (and its 50.0% collision); generation 1 draws from all 11 at 9.1%.
#
# Was 0 before this, meaning: nothing had been added since generations existed.
CURRENT_HARDWARE_GENERATION = 1


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
