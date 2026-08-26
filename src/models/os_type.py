"""The ``os_type`` vocabulary: which spellings exist, and which may be STORED.

THE DEFECT THIS EXISTS TO CLOSE (PS-187, follow-up 3 of PS-161). Our OS
normalisation table folds ``("windows", "win") -> "windows"``, so our own code
recognised ``win`` as a legitimate spelling. The packaged engine does NOT: it
answers ``--fingerprint-platform=win`` with ``Google Inc. (Google)`` /
SwiftShader. The masking layer stood down expecting the engine to author the
identity, the engine did not, neither author wrote the pair, and THE HOST'S
SOFTWARE RASTERISER REACHED THE PAGE.

PS-161 fixed the READ side — authorship now resolves from
``ENGINE_HONOURED_PLATFORMS``, the engine's own vocabulary, so a spelling the
engine rejects keeps our spoof instead of deferring. That fix is correct, it is
not restated here, and nothing in this module belongs on that path.

What was never closed is the WRITE side: ``os_type`` was a free-form ``str``
with no ``Literal`` and no enum, and no write door refused a non-canonical
spelling. A value the product can never serve correctly should not be storable
in the first place; the only thing standing between such a record and a host
leak was a fix one layer away.

WHY THIS MODULE OWNS THE TABLE. The fold used to live in
``services/browser/gpu_ext.py``, but the guarantee has to hold at the MODEL —
that is the one place every door funnels through (see ``Profile.__setattr__``),
and ``models/`` cannot import ``services/`` without inverting the dependency
direction. So the table moved DOWN here and ``gpu_ext`` re-exports it. It is not
restated there: a second copy is exactly the drift that produced the ``win``
leak, and the same re-export-don't-restate rule already governs
``ENGINE_HONOURED_PLATFORMS``.

⚠️ THE CANONICAL SET IS THE FOLD'S ARMS, **NOT** ``ENGINE_HONOURED_PLATFORMS``.
This is the trap, and getting it backwards repeats the PS-161 category error in
the opposite direction. ``ENGINE_HONOURED_PLATFORMS`` is ``{windows, macos,
linux}`` and answers a question about the value the ENGINE RECEIVES. A stored
``os_type`` of ``android`` or ``ios`` is perfectly legitimate — the engine has
no mobile platform, so ``engine_platform_for`` backs those with the nearest
desktop platform it DOES spoof (``linux`` / ``macos``) while the device preset
supplies the mobile signals. Constraining STORAGE to the honoured set would
refuse every mobile profile.

The arms are the right set because of a measured property, not a hoped-for one
(``readings``-style check, re-runnable from ``tests/test_ps187_os_type_write_doors.py``):

    every ARM is honoured on BOTH device_types;
    every non-arm recognised spelling is honoured on NEITHER (desktop).

    os_type   desktop->      ok      mobile->   ok
    darwin    darwin      False      linux    True
    ipad      ipad        False      linux    True
    ipados    ipados      False      linux    True
    iphone    iphone      False      linux    True
    mac       mac         False      linux    True
    win       win         False      linux    True

So it is SIX alias/device pairs that reach the engine unhonoured, not one. The
ticket named ``win`` because that is the one that was measured leaking; the
class is wider, and pinning only ``win`` would have left five open.
"""

# The raw ``os_type`` spellings this codebase recognises, and the arm each folds
# onto. THE SINGLE OWNER of this mapping — ``gpu_ext`` re-exports it rather than
# restating it.
#
# Adding a spelling here makes it recognised AND storable-after-repair
# everywhere at once. Adding an ARM (a new left-hand value) is a much bigger
# claim: it asserts the engine will honour that platform on both device types,
# which is a fact about a third party's build and must be MEASURED before it is
# written here. ``test_ps187_os_type_write_doors.py`` re-runs that measurement.
OS_NORM_TABLE = (
    (("ios", "iphone", "ipad", "ipados"), "ios"),
    (("macos", "mac", "darwin"), "macos"),
    (("android",), "android"),
    (("linux",), "linux"),
    (("windows", "win"), "windows"),
)

#: Every spelling the fold recognises, canonical and alias alike. Derived FROM
#: the table rather than restated beside it, so the two cannot drift.
RECOGNISED_OS_TYPES = frozenset(
    spelling for spellings, _arm in OS_NORM_TABLE for spelling in spellings
)

#: The ONLY values that may come to rest in ``Profile.os_type``. Derived from
#: the table's arms for the same anti-drift reason: teach the table a new arm
#: and this follows automatically.
CANONICAL_OS_TYPES = frozenset(arm for _spellings, arm in OS_NORM_TABLE)

#: What an UNRECOGNISED value repairs to. Matches the fold's own default, which
#: the GPU pool has always used ("a plausible desktop card beats no card"), so
#: repair does not invent a second answer to a question already answered.
DEFAULT_OS_TYPE = "windows"


def canonical_os_type(os_type: object) -> str:
    """Fold any spelling onto the canonical arm that may be stored.

    Total by construction: every input returns a member of
    ``CANONICAL_OS_TYPES``, so a caller cannot be handed something it then has
    to re-check. An unrecognised value folds to ``DEFAULT_OS_TYPE``.

    Case is folded because the fold has always folded it, and because Rule 2 in
    ``coherence.py`` compares ``os_type`` to ``"windows"`` by STRING EQUALITY —
    so ``"WINDOWS"`` used to fail that compare and silently downgrade a Firefox
    profile to chromium. Repairing the spelling fixes that arm too.
    """
    ot = str(os_type).lower().strip()
    for spellings, arm in OS_NORM_TABLE:
        if ot in spellings:
            return arm
    return DEFAULT_OS_TYPE


def is_canonical_os_type(os_type: object) -> bool:
    """Whether this value may be STORED as-is, with no repair.

    Note this is deliberately NARROWER than "recognised": ``win`` is recognised
    (we know what it means) and NOT canonical (we cannot serve it). Conflating
    the two is the defect.
    """
    return isinstance(os_type, str) and os_type in CANONICAL_OS_TYPES
