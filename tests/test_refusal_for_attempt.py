"""PS-82: the staleness rule itself — "is the refusal on record THIS call's?"

The two lane tests (``test_automation_api.py``, ``test_mcp_launch_refusal.py``)
drive this rule through a route and a tool, which is where it has to be right.
They cannot reach its BOUNDARY, though: both stamp ``attempt_at`` inside the
request, so a test driving them can never hold the two instants equal on
purpose. This file tests ``refusal_for_attempt`` directly, for the one decision
it exists to make.

WHY THE EQUAL CASE IS A TEST AND NOT A DETAIL. ``refusal_report.py`` argues the
comparison at length: ``>=`` rather than ``>``, because on a coarse system clock
the stamp taken before ``start_thread`` and the stamp taken inside the launcher's
handler can land on the SAME tick — and ``>`` would then drop a genuine refusal,
which is precisely the defect this slice exists to close. That argument was
written down and then bound by nothing: flipping the comparison left the whole
suite green, so a future reader "tidying" it got no signal. A reasoned choice
that no test can see is a comment, not a rule. ``test_a_refusal_recorded_in_the
_same_tick_...`` below is that rule.

These assertions bind to the RETURNED VERDICT, never to "the function can call
the accessor" (PS-11).
"""

from __future__ import annotations

from src.api.refusal_report import refusal_for_attempt
from src.services.browser.refusal import classify_refusal
from src.services.proxy.errors import ProxyUnresolvedError

ATTEMPT_AT = 1_000_000.0

# The real sentence process.py composes, not a stand-in: `detail` is passed
# through untouched and a test that invents its own wording stops noticing if
# that ever changes.
_REFUSED = ProxyUnresolvedError(
    "Profile 'acct-7' has proxy 'home' assigned but it could not be resolved "
    "(deleted/renamed?). Refusing to launch DIRECT."
)


class StubLauncher:
    """Only the accessor this rule reads. A recorded verdict, at an instant the
    test chooses — built through the SHIPPED ``classify_refusal`` so a test
    cannot pin a ``kind`` the real classifier would never produce.
    """

    def __init__(self, recorded_at: float | None):
        self._refusal = (
            None if recorded_at is None else classify_refusal(_REFUSED, recorded_at)
        )

    def last_refusal(self, name):
        return self._refusal


def test_no_recorded_refusal_is_not_a_refusal():
    # The overwhelmingly common case: nothing on record, nothing to report.
    assert refusal_for_attempt(StubLauncher(None), "acct-7", ATTEMPT_AT) is None


def test_a_refusal_older_than_this_attempt_is_not_reported():
    # THE TRAP. _last_refusal is keyed by profile NAME and outlives the attempt
    # that produced it (deliberately — it is what lets the card still show the
    # marker an hour later). A caller that just read the dict would hand this
    # hour-old verdict to a later call as its own.
    stale = StubLauncher(ATTEMPT_AT - 3600.0)

    assert refusal_for_attempt(stale, "acct-7", ATTEMPT_AT) is None, (
        "a verdict recorded an hour before this attempt was reported as its own"
    )


def test_a_refusal_recorded_after_this_attempt_started_is_reported():
    # The ordinary refusal: the guard fired inside THIS call, so the handler
    # stamped it after the caller stamped attempt_at.
    fresh = StubLauncher(ATTEMPT_AT + 0.25)

    verdict = refusal_for_attempt(fresh, "acct-7", ATTEMPT_AT)

    assert verdict is not None, "this attempt's own refusal was dropped"
    assert verdict.kind == "proxy_unresolved"
    assert "Refusing to launch DIRECT" in verdict.detail


def test_a_refusal_recorded_in_the_same_tick_as_the_attempt_is_reported():
    # THE BOUNDARY, and the reason the comparison is `>=` rather than `>`.
    # A coarse system clock (Windows' ~15.6ms timer is the shipped case) lands
    # the caller's stamp and the handler's stamp on the SAME value. Under `>`
    # this genuine, just-recorded refusal is discarded as stale and the caller
    # is told the launch succeeded — the exact defect this slice closes,
    # reintroduced by a one-character "tidy" of the comparison.
    same_tick = StubLauncher(ATTEMPT_AT)

    verdict = refusal_for_attempt(same_tick, "acct-7", ATTEMPT_AT)

    assert verdict is not None, (
        "a refusal recorded in the same clock tick as the attempt was dropped "
        "as stale — on a coarse clock this is a REAL refusal reported as success"
    )
    assert verdict.kind == "proxy_unresolved"


def test_the_verdict_is_passed_through_untouched():
    # `kind` and `detail` are the launcher's, not this module's. refusal.py
    # explains why restating the wording anywhere forks it at the first edit —
    # so this rule may drop a verdict, never rewrite one.
    recorded = classify_refusal(_REFUSED, ATTEMPT_AT + 1.0)
    launcher = StubLauncher(None)
    launcher._refusal = recorded

    assert refusal_for_attempt(launcher, "acct-7", ATTEMPT_AT) is recorded, (
        "the rule returned a copy or a rebuilt value instead of the launcher's "
        "own recorded fact"
    )
