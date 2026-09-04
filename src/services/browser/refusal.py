"""What a REFUSED launch is, as a value the UI can render on the profile.

A refusal is not an ordinary error. It is the moment this product's fail-closed
protection actually fires — the system working — and it arrives at the operator
as ``log_callback(f"Error starting process: {e}")``, one line in a shared panel
that scrolls. Nothing lands on the profile that refused, so a card that refused
looks identical to one that was never clicked, and an hour later there is no way
to tell which profiles refused or why. Launch twenty at once and the reasons
interleave with routine chatter.

That misreading is expensive in a specific direction: an operator who does not
register a refusal reads it as "the profile didn't open", and the natural next
move is to remove the protection. So the answer has to reach the profile, and it
has to say WHICH refusal applied.

This module owns exactly one decision: mapping a launch exception onto that
answer. It is deliberately separate from both the launcher (which catches) and
the card (which draws), because the two of them must not each grow their own
opinion about what counts as a refusal.

SCOPE — refusals only. ``classify_refusal`` returns None for every other
exception, and a None is never recorded on a profile. That is not an oversight:
the ticket's standing constraint is that routine noise stays quiet enough that a
refusal reads as loud, and a card that marks every transient spawn failure
trains the operator to skim past the one marker that means a guard fired. An
ordinary crash keeps going to the log exactly as it does today.

THE WORDING IS NOT RESTATED HERE. ``detail`` carries the exception's own
message, which process.py composes with the profile and proxy named and the
one-click remedy spelled out. Those sentences are settled and deliberately
distinguish "never checked successfully" from "the last check FAILED" — sending
an operator to re-check a proxy they already checked wastes their time. Copying
them into this module would fork them at the first edit, so this module only
picks the SHORT label and lets the settled sentence through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..proxy.errors import (
    ExitCountryUnknownError,
    GeographyDisprovenError,
    GeographyUnknownError,
    LocaleUnderivableError,
    ProxyUnresolvedError,
    TimezoneUnderivableError,
)

# The short label the card shows while the operator is SCANNING a list. Each one
# has to survive being read at a glance, next to nineteen others, without the
# full sentence — so they name the state of the evidence, never the remedy.
#
# None of them may read as an invitation to launch without a proxy. "cannot
# establish where this profile would exit" is a NOT-YET-KNOWN, and the honest
# response to it is one proxy check; a label like "no geography — launch direct?"
# would convert the product's own guard into a nudge to switch it off. The
# labels below describe the PROXY's evidence and stop there. See
# test_refusal_wording_is_not_a_nudge.
_UNRESOLVED = "proxy unresolved"
_DISPROVEN = "last check failed"
_UNKNOWN = "proxy never checked"
# NOT a re-check prompt, unlike the three above, and that is the whole point of
# giving it its own label. The proxy here may have been checked successfully
# seconds ago — the geo response just carried no usable zone — so "never
# checked" would send the operator to re-run a check that already passed. This
# names the missing thing instead. It stays inside the rule the block above
# states: it describes the EVIDENCE ("no zone for this country") and stops
# there, never hinting that launching without the proxy would be a way out.
_UNDERIVABLE = "no timezone for exit country"
# The locale half of the same shape, and it needs its OWN label for the same
# reason the zone half did: the proxy may have checked successfully seconds ago,
# so any re-check prompt is wrong. It is a SEPARATE label rather than a shared
# "no data for exit country" because the two name DIFFERENT missing rows in
# DIFFERENT tables, and an operator reading a scanning list has to know which
# one to add. Same rule as the block above: it describes the evidence and stops
# there, never hinting that launching without the proxy would be a way out.
_LOCALE_UNDERIVABLE = "no locale for exit country"
# The one label in this block that IS a re-check prompt, and it has to be: the
# other three "underivable"-shaped states are missing a table ROW, while this
# one is missing the COUNTRY itself. A check that answers with a country fixes
# it, and no code change can. Named apart from _UNKNOWN for the reason that
# label's own comment gives — the proxy here has very likely been checked
# SUCCESSFULLY (a partial answer with a zone but no country), so "never checked"
# would send the operator hunting a failure that did not happen. Still inside
# the rule the block above states: it describes the evidence, never hints that
# launching without the proxy would be a way out.
_EXIT_COUNTRY_UNKNOWN = "exit country unknown"


@dataclass(frozen=True)
class Refusal:
    """One refused launch, as the profile's own fact.

    ``kind``    stable identifier for tests and callers ("proxy_unresolved",
                "geography_disproven", "geography_unknown",
                "timezone_underivable", "locale_underivable",
                "exit_country_unknown"). Never rendered.

                NOT internal: this goes out over the wire as the 409 body
                (routes/browser.py) and out of the MCP tool (mcp_server.py),
                both of which document it as the stable value to branch on. So
                a caller's switch is written against THIS enumeration — adding a
                refusal without extending it leaves them branching on a list
                that is short by the case they are most likely to hit. Every
                value here is produced by exactly one arm of
                ``classify_refusal`` below; keep the two in step.
    ``label``   the short scanning label (see above).
    ``detail``  the settled, full operator-facing sentence — the exception's own
                message, passed through rather than restated.
    ``at``      wall-clock time of the refusal.

    ``at`` is what makes this survivable. The marker is meant to be found an
    hour later, and a marker that cannot say how old it is gets read as NOW —
    so a refusal from an hour ago would look like one that just happened. It is
    carried as a timestamp rather than a rendered string because the card
    re-renders continuously and the age has to move with the clock.

    Frozen: a recorded refusal is a historical fact. Nothing should edit one
    after the fact — a new launch attempt REPLACES it (see
    ``BrowserLauncher.start_thread``), which is a different operation from
    mutating it, and the difference is what keeps the timestamp honest.
    """

    kind: str
    label: str
    detail: str
    at: float


def classify_refusal(exc: BaseException, now: float) -> Refusal | None:
    """The Refusal for a fail-closed launch guard, or None for anything else.

    ORDER IS LOAD-BEARING, and it is the one thing in this file that will break
    silently if rearranged. ``GeographyDisprovenError`` is a SUBCLASS of
    ``GeographyUnknownError`` (errors.py says so, and says why: every existing
    ``except GeographyUnknownError`` in the fail-closed plumbing must keep
    catching it untouched). So an ``isinstance`` chain that tests the parent
    first swallows the child, and the operator is told the proxy was "never
    checked" when it WAS checked and the check FAILED — the precise wrong
    direction, because it sends them to re-run a check they already ran instead
    of investigating why it failed. The distinction the wording works so hard to
    preserve would die here, in a branch that looks correct. There is a test
    pinning this ordering specifically.

    ``now`` is injected rather than read from the clock, so the caller stamps
    the refusal with the same instant it handles it and tests are deterministic.
    """
    # Subclass FIRST — see above.
    if isinstance(exc, GeographyDisprovenError):
        return Refusal("geography_disproven", _DISPROVEN, str(exc), now)
    # ALSO before the parent, and for the identical reason: TimezoneUnderivableError
    # subclasses GeographyUnknownError too, so the parent's branch would swallow
    # it and label it "proxy never checked" — sending the operator to re-run a
    # check that may already have PASSED and will keep passing, because the
    # missing thing is a _COUNTRY_TZ row rather than a check result. It is a
    # SIBLING of the branch above, not a narrowing of it: neither is a subclass
    # of the other, so their relative order does not matter, but BOTH must
    # precede the parent. There is a test pinning this ordering specifically.
    if isinstance(exc, TimezoneUnderivableError):
        return Refusal("timezone_underivable", _UNDERIVABLE, str(exc), now)
    # A THIRD sibling, before the parent for the identical reason: it subclasses
    # GeographyUnknownError too, so the parent's branch would swallow it and
    # label it "proxy never checked" — sending the operator to re-run a check
    # that may already have PASSED and will keep passing, because the missing
    # thing is a _COUNTRY_LOCALE row rather than a check result. Like the two
    # above it is a SIBLING, not a narrowing: none of the three is a subclass of
    # another, so their relative order among themselves does not matter, but ALL
    # THREE must precede the parent. There is a test pinning this ordering.
    if isinstance(exc, LocaleUnderivableError):
        return Refusal("locale_underivable", _LOCALE_UNDERIVABLE, str(exc), now)
    # A FOURTH sibling, before the parent for the identical reason: it
    # subclasses GeographyUnknownError too. Distinct from the three above in
    # CAUSE and — uniquely — in REMEDY DIRECTION: the other two "underivable"
    # arms must NOT prompt a re-check (a table row is missing), while this one
    # must (the COUNTRY is missing, and only a check can supply it). Labelling
    # it "proxy never checked" via the parent would be doubly wrong: the check
    # most likely PASSED, it just answered without a country. Like the rest it
    # is a SIBLING, not a narrowing — none of the four is a subclass of another,
    # so their order among themselves does not matter, but ALL FOUR must precede
    # the parent. There is a test pinning this ordering.
    if isinstance(exc, ExitCountryUnknownError):
        return Refusal("exit_country_unknown", _EXIT_COUNTRY_UNKNOWN, str(exc), now)
    if isinstance(exc, GeographyUnknownError):
        return Refusal("geography_unknown", _UNKNOWN, str(exc), now)
    if isinstance(exc, ProxyUnresolvedError):
        return Refusal("proxy_unresolved", _UNRESOLVED, str(exc), now)
    # Not a refusal: an ordinary failure. Stays in the log, off the card.
    return None
