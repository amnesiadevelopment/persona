"""Did this run gather enough to be a reading at all? — the evidence floor.

A record can be complete, well-formed, correctly counted, written to disk, and
describe nothing. That is not a hypothetical: PS-110 was filed off a real run
that crashed its Chromium renderer on the first heavy page, lost every checker
sequenced after it, recorded **two** fingerprint-bearing rows out of 27, printed
``browser tier: 37 readings``, wrote the file and exited ``0``. Nothing in the
artifact distinguished it from a clean run. The planner who took it reported
"zero adverse rows fired" before looking closely — true, and meaningless,
because nothing was left to fire.

This module answers the one question that separates those two runs, and it is
deliberately the ONLY place that answers it.

WHY THE QUESTION IS PUT TO FINGERPRINT ROWS AND NOT TO READ ROWS
----------------------------------------------------------------
Counting everything that was read scores that dead run at **seven** and clears
any plausible floor. Five of those seven are the engine-exit rows, and they
survive a dead browser BY CONSTRUCTION: the exit is proven before the browser
tier runs, so those rows are already in hand when the renderer dies. The JSON
tier survives too — it is fetched by Python, not by the engine — and it is
tagged ``HARNESS`` precisely because it describes the instrument rather than
persona (see ``checkers.HARNESS``).

So a floor over all read rows measures how much of the run does not depend on
the browser, which is the opposite of what it must measure. The floor is over
the ``FINGERPRINT`` sort — the rows that carry evidence about the identity, the
rows the matrix exists to read, and the rows that die with the browser.

THE FLOOR HAS TWO TERMS AND A RUN MUST CLEAR BOTH
--------------------------------------------------
Neither term alone survives contact with the catalogue, and the reason is worth
stating because each covers exactly the hole the other opens.

``fraction``
    Obtained fingerprint rows as a share of the fingerprint rows THE RECORD
    ITSELF CARRIES. A share rather than a count, so the floor does not silently
    tighten every time a checker is added to the catalogue — and against the
    record's own rows rather than today's catalogue, so an older record is
    judged against the matrix it was actually taken from.

``checkers``
    How many DISTINCT checkers those rows came from. This is not a second way
    of saying the same thing. Rows within one checker are perfectly correlated:
    one page load yields all of them or none of them, so counting nine CreepJS
    rows as nine pieces of evidence double-counts a single page load — the same
    error class as counting unobtainable rows as readings, one level in. CreepJS
    alone is 9 of 27 rows (33%), so a fraction-only floor set anywhere below
    that is cleared by ONE checker answering, which is not a matrix reading.

WHERE THE NUMBERS COME FROM — two real runs, not a preference
--------------------------------------------------------------
Both ends of the range are measured artifacts in this repository, which is what
makes the threshold defensible rather than chosen:

* **The healthy control.** ``tests/fixtures/checker-matrix-reading.sandbox.json``
  — the first real reading ever taken on this project (PS-59), through the real
  exit. It carries 18 fingerprint rows, **7 obtained (38.9%)**, from **2**
  distinct checkers. This record is the floor's hard upper constraint: it is a
  GOOD run, its 24-of-53 unobtainable rows are this matrix's designed steady
  state (see ``matrix_diff.coverage_lost``), and a floor that fires on it is
  simply wrong.
* **The dead run.** The PS-110 record: **2 of 27 (7.4%)**, from **1** checker.

:data:`DEFAULT_FLOOR` sits at the RATIO-MIDPOINT of those two observations —
``sqrt(0.074 x 0.389) ~= 0.17``, rounded to **0.20** — so it clears the real
healthy run by ~1.9x and refuses the real dead one by ~2.7x. Stated as a ratio
rather than an arithmetic midpoint because the two runs differ by a factor,
not by a margin.

**The honest limit of that margin, disclosed rather than smoothed over:** ~1.9x
is not comfortable. A legitimate run that loses one of its two contributing
checkers to an ordinary timeout drops to ~22% and lands just above the floor,
and one that loses the larger of the two goes under it and is called
inconclusive. That is the intended direction of the error — this floor is built
to say "look at the run" and the cost of doing so is reading a record — but it
means the fraction is NOT a comfortable ceiling and must not be raised toward
the control without new measurements to raise it against.

WHAT THIS IS NOT
----------------
Not a gate, and not a verdict about the product. ``inconclusive`` says the RUN
did not gather enough to be read — never that persona failed anything. That
distinction is the same one ``baseline.count_errors`` draws one artifact over
(*"an unobtainable reading is inconclusive, and inconclusive is never a pass"*)
and the same one the checker CLI's exit codes draw between "a finding" and
"I could not look". Folding the two together would train a reader to skim a
red report, which is the failure this whole subsystem exists to prevent.

ONE DEFINITION, TWO LANES
-------------------------
``read`` (PS-110) and ``compare`` (PS-92) need the same floor over the same
rows, and writing it twice is how the two lanes come to disagree about what
counts as evidence. :func:`obtained` is the primitive both rest on, and
``matrix_diff._obtained`` delegates to it rather than keeping a second copy —
so the aggregate floor ``compare`` applies and the one ``read`` applies can
never drift apart on the question of what a reading IS.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .checkers import FINGERPRINT
from .matrix import ABSENT, READ

# --- the floor --------------------------------------------------------------

# See the module header for the derivation. Both terms, both measured.
DEFAULT_FLOOR: "dict[str, Any]" = {
    "fraction": 0.20,
    "checkers": 2,
}

SUFFICIENT = "sufficient"
INCONCLUSIVE = "inconclusive"

# WHY a run is short of evidence. Empty on a SUFFICIENT record — a cause with
# no verdict behind it would invite a reader to act on the absence of a problem.
#
# ``session_died``
#     The browser died mid-run and rows after it were never asked. The one
#     that is a symptom: something broke, and the record can name it.
# ``tier_skipped``
#     The operator asked for less. Deliberate, and still not a reading.
# ``not_gathered``
#     Everything was asked, nothing was skipped, and the answers did not come
#     back — checkers that timed out, refused, or rendered nothing.
SESSION_DIED = "session_died"
TIER_SKIPPED = "tier_skipped"
NOT_GATHERED = "not_gathered"


def obtained(row: Any) -> bool:
    """True when this row carries a reading that was actually OBTAINED.

    Two states count, and the second is the one that has to be argued for.
    ``read`` is obviously evidence. ``absent`` is evidence TOO: the checker
    answered and did not say this, which for an adverse item
    (``proxy_detected``) is precisely the GOOD news. Folding ``absent`` in with
    ``unobtainable`` would make every clean page look unread — and would make
    a record of a perfectly clean run fail this floor, which is the one outcome
    that would get the floor switched off.

    ``unobtainable``, a missing row, and anything malformed are not evidence.
    The safe default in this subsystem is that what we cannot recognise is
    evidence we do not have.
    """
    if not isinstance(row, Mapping):
        return False
    return row.get("state") in (READ, ABSENT)


def _fingerprint_rows(rows: "Iterable[Any]") -> "list[Mapping]":
    return [
        r for r in rows if isinstance(r, Mapping) and r.get("sort") == FINGERPRINT
    ]


def never_asked_rows(rows: "Iterable[Any]") -> "list[Mapping]":
    """Rows for checkers the run never got to ask.

    Distinct from a checker that was asked and could not answer, and the
    distinction is the load-bearing half of PS-110: 45 rows lost to ONE dead
    browser context are not 45 independent failures, and a reader who cannot
    tell those apart attributes a single crash to the whole catalogue.
    """
    return [
        r
        for r in rows
        if isinstance(r, Mapping) and bool(r.get("never_asked", False))
    ]


def assess(
    rows: "Iterable[Any]",
    *,
    floor: "Mapping[str, Any] | None" = None,
    skipped_tiers: "Iterable[str] | None" = None,
) -> dict:
    """The run's own verdict on whether it gathered enough to be a reading.

    Returns the block the record carries. It is computed from the ROWS rather
    than passed in by whoever ran the tiers, so it describes what was actually
    recorded and cannot be contradicted by the run's own optimism — the same
    argument that puts ``counts`` in the record rather than a caller's tally.

    Every input to the verdict is reported beside it (the numerator, the
    denominator, the contributing checkers, the floor that was applied), so a
    reader can re-derive the verdict from the record instead of trusting the
    word. A record that only carried ``inconclusive`` would be one more
    unfalsifiable claim, which is the shape this ticket exists to remove.

    A DELIBERATELY SKIPPED TIER IS STILL INCONCLUSIVE, and that is the answer
    rather than an oversight. ``--skip-browser`` gathers no fingerprint
    evidence at all, so the record cannot support a reading of the identity —
    and softening the verdict because the OPERATOR chose the skip would be
    exactly the "a skipped test reporting green" hazard this subsystem already
    names (``browser_tier``'s own docstring). What the skip changes is the
    CAUSE, not the verdict: ``cause`` separates "this run did not ask" from
    "this run asked and could not get an answer", because only the second is a
    symptom of anything. A consumer that wants to ignore deliberate skips has
    the field to do it with; it is not decided for them here.
    """
    applied = dict(DEFAULT_FLOOR if floor is None else floor)
    rows = list(rows)
    skipped = [str(t) for t in (skipped_tiers or [])]

    fingerprint = _fingerprint_rows(rows)
    got = [r for r in fingerprint if obtained(r)]
    checkers = sorted({str(r.get("checker", "")) for r in got if r.get("checker")})
    total = len(fingerprint)
    fraction = (len(got) / total) if total else 0.0

    unasked = never_asked_rows(rows)

    reasons: "list[str]" = []
    if total == 0:
        reasons.append(
            "this record carries no fingerprint-sorted rows at all, so there "
            "is nothing for the floor to rest on"
        )
    else:
        if fraction < float(applied["fraction"]):
            reasons.append(
                f"{len(got)} of {total} fingerprint-bearing rows were obtained "
                f"({fraction:.1%}), below the floor of "
                f"{float(applied['fraction']):.0%}"
            )
        if len(checkers) < int(applied["checkers"]):
            reasons.append(
                f"the fingerprint evidence came from "
                f"{len(checkers)} checker(s) "
                f"({', '.join(checkers) or 'none'}), below the floor of "
                f"{int(applied['checkers'])} — rows from one checker are one "
                "page load, not independent evidence"
            )

    if unasked:
        # Reported whether or not it changed the verdict. A run can lose a
        # whole tail to one dead context and still clear the floor on what it
        # got in first; that is a run worth looking at even when it passes.
        reasons.append(
            f"{len(unasked)} row(s) were NEVER ASKED — the session ended "
            "mid-run and everything sequenced after it was abandoned, not "
            "attempted and failed"
        )

    verdict = INCONCLUSIVE if (total == 0 or fraction < float(applied["fraction"])
                               or len(checkers) < int(applied["checkers"])) else SUFFICIENT

    # WHY the run is short of evidence — never WHETHER, which is `verdict`.
    # The three are ranked by what a reader should do about them, most
    # actionable first, and only one of them is a symptom of anything.
    if verdict == SUFFICIENT:
        cause = ""
    elif unasked:
        # Outranks the skip: a session that died mid-run is the finding, and it
        # would be buried if a run that ALSO skipped a tier reported the skip
        # as its cause.
        cause = SESSION_DIED
    elif skipped:
        cause = TIER_SKIPPED
    else:
        cause = NOT_GATHERED
    if cause == TIER_SKIPPED:
        reasons.append(
            f"the {', '.join(skipped)} tier(s) were skipped at the operator's "
            "request, so this run never asked for the evidence it lacks — "
            "deliberate, but still not a reading of the identity"
        )

    return {
        "verdict": verdict,
        "cause": cause,
        "fingerprint_obtained": len(got),
        "fingerprint_total": total,
        "fingerprint_fraction": round(fraction, 4),
        "checkers_contributing": checkers,
        "never_asked": len(unasked),
        "floor": applied,
        "reasons": reasons,
    }


def is_inconclusive(evidence: Any) -> bool:
    """Read the verdict off a record's evidence block.

    Anything unrecognisable reads as INCONCLUSIVE rather than as sufficient.
    A record whose evidence block is missing or malformed is one this floor
    cannot vouch for, and the direction of that error is the whole point: the
    failure being guarded against is a run that could not say it measured
    nothing, so silence must never resolve to "fine".
    """
    if not isinstance(evidence, Mapping):
        return True
    return evidence.get("verdict") != SUFFICIENT


__all__ = [
    "DEFAULT_FLOOR",
    "INCONCLUSIVE",
    "NOT_GATHERED",
    "SESSION_DIED",
    "SUFFICIENT",
    "TIER_SKIPPED",
    "assess",
    "is_inconclusive",
    "never_asked_rows",
    "obtained",
]
