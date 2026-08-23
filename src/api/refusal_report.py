"""Reporting a REFUSED launch to a machine caller — the staleness rule, once.

The launcher already computes the product's most important verdict. When a
fail-closed guard stops a launch, ``BrowserLauncher.start_thread`` catches the
exception, classifies it, records a ``Refusal`` under the profile name, and
returns ``None`` — the same ``None`` a successful launch returns. Both API lanes
then composed success over it: REST answered ``{"success": true}`` and MCP
answered ``{"launched": true}`` for a profile that never opened. The guard's
whole purpose is to convert a silent de-anonymization into a loud stop; on those
two lanes it became a silent no-op, and an API caller is the worst audience for
that misreading — most likely to retry in a loop, least able to see the
server-side log line the sentence went to.

This module owns exactly one decision: **is the refusal on record this call's?**
It lives here, apart from both lanes, for the same reason ``refusal.py`` lives
apart from the launcher and the card — two callers must not each grow their own
opinion about it, because the two wrong answers are asymmetric and both bad.

WHY THE QUESTION IS NOT "IS THERE A REFUSAL?". ``_last_refusal`` is keyed by
profile NAME and is dropped at the START of an attempt (``launcher.py``), which
means a recorded refusal outlives the attempt that produced it until the next
attempt supersedes it. That is deliberate — it is what lets the UI still show
the marker an hour later — but it makes an unconditional read WRONG here in one
specific case: a duplicate-launch call returns BEFORE that drop, on purpose
("a click that gets refused as a duplicate is not an attempt and must not erase
the verdict from the attempt that did run"). A lane that just read the dict
would hand an older refusal to a caller as this call's own verdict.

THE DISCRIMINATOR IS THE ATTEMPT, NOT THE DICT. The caller stamps the instant it
called ``start_thread`` and a refusal counts as this call's only if it is not
OLDER than that instant. ``Refusal.at`` exists for exactly this ("a marker that
cannot say how old it is gets read as NOW"). This works because ``spawn_browser``
runs in ``start_thread``'s own body rather than on a thread: by the time the call
returns, this attempt has already been resolved, so a fresh verdict is either
recorded or was never going to be.

The comparison is ``>=``, not ``>``, and the direction is chosen rather than
inherited. On a coarse system clock the stamp taken before the call and the
stamp taken inside the handler can land on the same tick, and ``>`` would then
drop a genuine refusal — which is precisely the defect this module exists to
close. ``>=`` instead risks re-reporting a refusal recorded in the same tick as
a later duplicate call, a far narrower window with a far cheaper failure: a
caller told "refused" for a profile that is in fact already running, rather than
"launched" for a profile that never opened.

WHAT IS NOT RESTATED HERE. ``kind`` and ``detail`` are passed through untouched.
``detail`` is the settled operator sentence ``process.py`` composes, and
``refusal.py`` explains why copying that wording anywhere forks it at the first
edit. ``label`` is deliberately NOT carried: it is the short at-a-glance string
for a human scanning a list of cards, and a machine caller wants the stable
``kind`` instead.

EXPOSURE, CHECKED RATHER THAN ASSUMED. ``detail`` names the profile and the
PROXY NAME, both of which this audience already reads on ``ProfileResponse``. It
carries no exit IP and no proxy host/port — the two things ``mcp_server.py`` and
the SSH path deliberately withhold from an off-machine caller. Report the
refusal; never widen it into the endpoint data those comments exclude.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..interfaces import IBrowserLauncher
    from ..services.browser.refusal import Refusal


def refusal_for_attempt(
    bl: IBrowserLauncher,
    profile_name: str,
    attempt_at: float,
) -> Refusal | None:
    """The refusal produced by THIS launch attempt, or None.

    ``attempt_at`` is the wall-clock instant the caller invoked
    ``start_thread``. A recorded refusal older than that belongs to an earlier
    attempt and is not this call's to report — see the module docstring.

    None means "this attempt was not refused": it launched, it failed in an
    ordinary way that ``classify_refusal`` does not treat as a refusal, or it
    returned early as a duplicate. All three keep the response byte-identical to
    what the lane answered before this read existed.
    """
    refusal = bl.last_refusal(profile_name)
    if refusal is None:
        return None
    if refusal.at < attempt_at:
        # Stale: recorded by an attempt that ran before this call.
        return None
    return refusal
