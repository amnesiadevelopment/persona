"""Per-profile screen resolution.

A profile's ``resolution`` is either ``"auto"`` (a stable per-profile pick from
the common desktop sizes below) or an explicit ``"WIDTHxHEIGHT"`` string. The
resolved size drives both the spoofed ``screen`` geometry and the real window
extent, so what the fingerprint reports and what the user sees agree.

The auto pick is generation-filtered — see ``hardware_generation.py``. Adding a
resolution used to change the divisor of ``seed % len(...)`` and re-index a
large share of existing profiles onto a different screen; a profile only ever
sees the entries of its own generation, so appending one leaves every existing
profile at exactly the size it already had.
"""

from dataclasses import dataclass

from ...models.hardware_generation import visible_entries


@dataclass(frozen=True)
class ResolutionEntry:
    """One desktop resolution, tagged with the generation it was added in.

    ``since`` is a promise to the profiles already pinned to this entry, not
    bookkeeping: never renumber it on a shipped entry. New entries get the
    bumped ``CURRENT_HARDWARE_GENERATION``.
    """

    width: int
    height: int
    since: int = 0

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)


# Common real desktop resolutions (StatCounter top set). Auto picks one of
# these per profile; the picker is seeded so a profile keeps the same size.
#
# ADDING ONE: give it `since=<CURRENT_HARDWARE_GENERATION after you bump it>`
# and leave every entry below untouched. Order is free to stay readable — the
# generation filter is by tag, not by position.
DESKTOP_RESOLUTIONS: list[ResolutionEntry] = [
    ResolutionEntry(1366, 768),
    ResolutionEntry(1440, 900),
    ResolutionEntry(1536, 864),
    ResolutionEntry(1600, 900),
    ResolutionEntry(1920, 1080),
    ResolutionEntry(1680, 1050),
    ResolutionEntry(1920, 1200),
    ResolutionEntry(2560, 1080),
    ResolutionEntry(2560, 1440),
]

# Below this, a "resolution" is not a plausible desktop screen — reject it so a
# typo can't produce a tiny unusable window / an obvious fingerprint tell.
_MIN_W, _MIN_H = 800, 600


def resolutions_for_generation(generation: int) -> list[tuple[int, int]]:
    """The (w, h) pool a profile of ``generation`` picks from."""
    return [e.size for e in visible_entries(DESKTOP_RESOLUTIONS, generation)]


def parse_resolution(value: str) -> tuple[int, int] | None:
    """Parse ``"WIDTHxHEIGHT"`` into a (w, h) tuple, or None if it isn't a
    valid, sane desktop resolution."""
    if not value:
        return None
    parts = value.lower().replace(" ", "").split("x")
    if len(parts) != 2:
        return None
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if w < _MIN_W or h < _MIN_H:
        return None
    return (w, h)


def resolve_resolution(
    value: str, seed: int, generation: int
) -> tuple[int, int]:
    """Resolve a profile's stored resolution to a concrete (w, h).

    An explicit ``"WIDTHxHEIGHT"`` is used as-is; anything else ("auto", blank,
    unparseable) picks a preset deterministically from the seed, out of the
    entries visible to ``generation``.

    ``generation`` is REQUIRED and deliberately has no default: every default
    would be a silent guess about which pool a profile belongs to, and guessing
    high is exactly the re-roll this argument exists to prevent. Callers pass
    ``profile.hardware_generation``.
    """
    explicit = parse_resolution(value)
    if explicit is not None:
        return explicit
    pool = resolutions_for_generation(generation)
    return pool[abs(int(seed)) % len(pool)]
