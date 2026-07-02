"""Per-profile screen resolution.

A profile's ``resolution`` is either ``"auto"`` (a stable per-profile pick from
the common desktop sizes below) or an explicit ``"WIDTHxHEIGHT"`` string. The
resolved size drives both the spoofed ``screen`` geometry and the real window
extent, so what the fingerprint reports and what the user sees agree.
"""

# Common real desktop resolutions (StatCounter top set). Auto picks one of
# these per profile; the picker is seeded so a profile keeps the same size.
DESKTOP_RESOLUTIONS: list[tuple[int, int]] = [
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1920, 1080),
    (1680, 1050),
    (1920, 1200),
    (2560, 1080),
    (2560, 1440),
]

# Below this, a "resolution" is not a plausible desktop screen — reject it so a
# typo can't produce a tiny unusable window / an obvious fingerprint tell.
_MIN_W, _MIN_H = 800, 600


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


def resolve_resolution(value: str, seed: int) -> tuple[int, int]:
    """Resolve a profile's stored resolution to a concrete (w, h).

    An explicit ``"WIDTHxHEIGHT"`` is used as-is; anything else ("auto", blank,
    unparseable) picks a preset deterministically from the seed.
    """
    explicit = parse_resolution(value)
    if explicit is not None:
        return explicit
    return DESKTOP_RESOLUTIONS[abs(int(seed)) % len(DESKTOP_RESOLUTIONS)]
