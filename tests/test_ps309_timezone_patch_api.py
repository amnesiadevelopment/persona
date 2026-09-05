"""PS-309: pin the two Chromium-152 API fixes in `018-timezone.patch`.

WHY THIS FILE EXISTS — THE PROBE CANNOT SEE EITHER OF THESE
───────────────────────────────────────────────────────────
All 16 fingerprint patches applied at ungoogled `152.0.7977.75-1` with 81/81
hunks and fuzz 0, and the build STILL failed — at ninja step 29955/56861, on
`obj/third_party/blink/renderer/core/core/timezone_controller.o` (run
33860055910, the first run whose unmodified control tree built end to end, so
the failure was attributable to our patches rather than to the environment).

`scripts/ps299_rebase_probe.py` measures TEXT: it cannot catch an API change
under a hunk that applies perfectly. `scripts/ps299_verify_semantics.py` does
not catch these either — its identifier regex matches ``switches::k\\w+`` and
``kFingerprint\\w*``, both of which are *namespace-insensitive* and *spelling-
insensitive* in exactly the way these two breakages are not. So both existing
gates score the broken patch green, and a compile is a ~40-minute round trip on
a dedicated runner. These assertions cost milliseconds and fail on the same
regression.

WHAT IS PINNED, AND WHY EACH IS A REAL FALSIFIER
────────────────────────────────────────────────
1. ``::switches::kFingerprintTimezone`` — the LEADING ``::`` is the whole fix.
   Our switches are declared in the GLOBAL ``switches`` namespace by
   `000-add-fingerprint-switches.patch`; upstream has a same-named
   ``blink::switches``, and at 152 `timezone_controller.cc` began including
   `blink/public/common/switches.h` (it did not at 144), so from inside
   ``namespace blink`` the unqualified name started resolving to upstream's and
   stopped finding ours. Dropping the ``::`` restores a tree that compiled
   cleanly by every text measure and failed the build.

2. ``String::FromUtf8`` — upstream renamed ``FromUTF8``. A one-character casing
   difference that no context line carries, so no hunk rejects on it.

The companion assertion is the one most at risk of being "helpfully" undone:
NO ``base::as_byte_span`` wrapper. The compiler also reported `no viable
conversion from 'std::string' to 'base::span<const uint8_t>'` at each site,
which reads as a third breakage demanding an explicit conversion. It is fallout
from clang's typo-correction: having suggested ``FromUtf8``, clang re-checked
the argument against the SINGLE declaration it named (the span overload) and
reported that one not matching. The 152 header carries two overloads, and
``std::string`` binds to the ``std::string_view`` one, which converts
internally. Adding the wrapper would be dead noise carried on every future
rebase. See `engine/patches/fingerprint/REBASING.md`.
"""

import pathlib
import re

import pytest

PATCH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "engine"
    / "patches"
    / "fingerprint"
    / "018-timezone.patch"
)

# The three call sites the patch inserts: GetCurrentTimezoneId(), the
# TimeZoneController constructor, and SetIcuTimeZoneAndNotifyV8().
EXPECTED_SWITCH_REFS = 6  # HasSwitch + GetSwitchValueASCII at each of 3 sites
EXPECTED_FROM_UTF8_CALLS = 3  # one per site


@pytest.fixture(scope="module")
def added_lines():
    """Lines the patch INSERTS — not its context.

    Context lines are upstream's code and are none of our business; asserting
    over them would make this test fail on an innocent re-anchor.
    """
    assert PATCH.is_file(), f"missing patch: {PATCH}"
    out = []
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
    assert out, "patch inserts no lines at all — it cannot be doing anything"
    return out


def test_switch_is_reached_through_the_global_namespace(added_lines):
    """Every kFingerprintTimezone reference names ``::switches``, not ``switches``.

    Breakage (1). Inside `namespace blink`, bare ``switches::`` resolves to
    ``blink::switches`` and cannot see our declaration.
    """
    refs = [l for l in added_lines if "kFingerprintTimezone" in l]
    assert len(refs) == EXPECTED_SWITCH_REFS, (
        f"expected {EXPECTED_SWITCH_REFS} kFingerprintTimezone references across "
        f"the three call sites, found {len(refs)}:\n" + "\n".join(refs)
    )
    unqualified = [l for l in refs if not re.search(r"::switches::kFingerprintTimezone", l)]
    assert not unqualified, (
        "kFingerprintTimezone reached WITHOUT the leading '::'. Inside namespace "
        "blink this resolves to blink::switches (upstream's, via "
        "blink/public/common/switches.h) and not to our global ::switches, which "
        "is exactly how PS-309's build failed:\n" + "\n".join(unqualified)
    )


def test_uses_the_renamed_FromUtf8(added_lines):
    """``String::FromUtf8``, never ``FromUTF8``. Breakage (2) — an upstream rename."""
    old = [l for l in added_lines if "FromUTF8" in l]
    assert not old, (
        "String::FromUTF8 no longer exists at Chromium 152; it was renamed to "
        "FromUtf8 in wtf_string.h:\n" + "\n".join(old)
    )
    new = [l for l in added_lines if "String::FromUtf8(" in l]
    assert len(new) == EXPECTED_FROM_UTF8_CALLS, (
        f"expected {EXPECTED_FROM_UTF8_CALLS} String::FromUtf8 calls (one per call "
        f"site), found {len(new)}:\n" + "\n".join(new)
    )


def test_no_explicit_byte_span_conversion_was_added(added_lines):
    """The ``std::string_view`` overload converts internally — do not wrap.

    The 'no viable conversion to base::span<const uint8_t>' error was an
    artifact of clang's typo-correction narrowing overload resolution to one
    candidate, not a third breakage. An added wrapper is noise re-applied on
    every future rebase.
    """
    wrapped = [l for l in added_lines if "as_byte_span" in l]
    assert not wrapped, (
        "explicit base::as_byte_span wrapper added. String::FromUtf8 has a "
        "std::string_view overload at 152 that does this itself, so a std::string "
        "binds without conversion. See REBASING.md:\n" + "\n".join(wrapped)
    )


def test_the_override_still_applies_under_the_same_conditions(added_lines):
    """Behaviour is unchanged: this was a rebase onto a moved API, not a redesign.

    The guard shape at every site is still 'switch present AND value non-empty'.
    A patch that compiles but changes WHEN the override applies has failed
    PS-309, and neither the text probe nor the semantics gate would notice.
    """
    joined = "\n".join(added_lines)
    assert joined.count("command_line->HasSwitch(::switches::kFingerprintTimezone)") == 3, (
        "the three sites no longer each guard on HasSwitch(kFingerprintTimezone)"
    )
    assert joined.count("if (!timezone_str.empty())") == 3, (
        "the non-empty guard is missing from a call site — an empty --timezone "
        "value must fall through to the unmodified behaviour, not override it"
    )
    # The DCHECK is only reachable on the no-switch path; losing it would change
    # behaviour for every unpatched caller.
    assert "DCHECK(!timezone_id.empty());" in joined, (
        "SetIcuTimeZoneAndNotifyV8 lost the DCHECK on its no-switch path"
    )
