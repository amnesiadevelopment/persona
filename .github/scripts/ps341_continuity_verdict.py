#!/usr/bin/env python3
"""PS-375 — the VERDICT on engine continuity, as a judgement that can be wrong.

WHAT THIS DECIDES
─────────────────
One question, across two of OUR OWN published engine releases: **does a profile
read the same engine-authored WebGL identity pair after an engine change as it
did before?**

PS-341 measured that it does NOT. Same seed, same profile directory, everything
else bit-identical — screen, dpr, platform, hardwareConcurrency, deviceMemory,
maxTouchPoints, timezone, languages, colorDepth and the full canvas
``toDataURL()`` all held — and the WebGL pair moved for **8 seeds of 8**, with
the two builds' card pools not intersecting at all (152 answers NVIDIA RTX
parts, 148 answers Intel integrated parts). The whole GPU table was replaced
between builds.

⭐ WHY THIS FILE EXISTS, AND WHAT IT IS *NOT*
─────────────────────────────────────────────
PS-341 landed 1,411 lines of instrument across six ``scripts/ps341_*.py`` files
and **nothing called any of it**: ``git grep -l "ps341" -- .github/`` returned
zero while four positive controls in the identical probe shape fired. So the
fact was recorded and the RECURRENCE was unwatched — when the next
``personium-`` tag ships, the only comparison available is against a committed
transcript of a previous binary.

⛔ AND "WIRE IT UP" DID NOT MEAN "CALL THE SCRIPT FROM A WORKFLOW", because
there was nothing to call that could answer anything. Measured:

    grep -nE "def classify|exit_code_for|EXIT_" scripts/ps341_*.py  ->  nothing

``scripts/ps341_gpu_seeds.py`` prints a tally and its ``main()`` ends
``return 0`` — **unconditionally**. It is a MEASUREMENT script, not a judge. A
workflow that merely ran it would be green on every possible reading, including
the reading where the engine's whole identity table was swapped, including the
reading where no browser started at all. This file is the missing half: it turns
that tally into a decision with a direction, and the direction is demonstrated
rather than asserted (``selftest`` below, run BEFORE any download).

⭐⭐ THE POSTURE: THIS REPORTS, IT DOES NOT REFUSE — AND THAT IS A DECISION
──────────────────────────────────────────────────────────────────────────
The tempting shape is a gate that FAILS the release when the pair moves. It
would be wrong here, and the reason is mechanical rather than tasteful.

The moved value is **engine-authored**: ``gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS``
is ``frozenset({"windows"})``, so on that arm persona's GPU layer deliberately
stands down and the pair a page reads is the ENGINE's, derived by a table
compiled into the binary. The observed ``0x00009A49`` is not in persona's
``WIN_GPUS`` pool at all. PS-341's own disposition is therefore **"recorded, not
fixed"**: no profile migration can change it, because rewriting a profile
directory cannot rewrite a table inside someone else's binary.

A gate that failed on it would be red on every engine bump forever, and this
project has written down what that costs, twice, in the files this one sits
beside:

    src/services/verify/engine_gate.py:27
    .github/workflows/engine-gpu-variance.yml
        "A gate that is always red is a gate people learn to ignore, which is
         worse than no gate."

PS-4's first claim does not ask for a refusal either. It asks that where an
engine change moves a surface, that is *"a decision someone made, **recorded
against a version**, not a side effect nobody noticed."* **Recording, not
refusing.** So a measured move is NEWS: it exits 0, it is filed as an issue
against the tag pair, and a human decides. ``chromium-upstream-watch.yml`` is
the in-tree precedent for exactly this posture, and files a report for its own
``clean`` status for the same reason — suppressing news because it is not a
defect would put us back where we started.

⚠️ A REPORTING POSTURE DOES NOT MEAN THE JOB CAN NEVER FAIL, and the line is
drawn in one place: **"the pair moved" (report, stay green) versus "the
measurement did not happen" (fail)**. The same discipline
``engine-gpu-variance.yml`` states as *"we failed to look" must never wear the
colour of "we looked and it was fine"*.

─────────────────────────────────────────────────────────────────────────────
STATUSES AND THIS MODULE'S EXIT CONTRACT
─────────────────────────────────────────────────────────────────────────────
    status                   exit  green  meaning
    ───────────────────────  ────  ─────  ─────────────────────────────────────
    held                       0    yes   Every scored seed read the SAME pair
                                          on both builds, over a COMPLETE
                                          sample. Continuity held. No news.
    moved                      0    yes   A MEASURED move. News, recorded
                                          against the tag pair, and NOT a
                                          defect this gate can fix — see the
                                          posture block above.
    no_predecessor             0    yes   Fewer than two engine versions are
                                          published, so no predecessor EXISTS.
                                          There is nothing to compare, and that
                                          is a definitive answer rather than a
                                          failure to look — the tag list was
                                          read successfully and it says so.
    unmeasured                 2    no    Seeds went unreadable, or nothing was
                                          scored at all. NOT a pass.
    record_inconsistent        2    no    The reading's own summary disagrees
                                          with its rows, so the record cannot be
                                          trusted. NOT a pass.
    predecessor_unreachable    3    no    A predecessor version EXISTS in the
                                          tag list and its release could not be
                                          resolved — yanked, deleted, or no
                                          asset for this OS. We were supposed to
                                          be able to compare, and could not.
    discovery_failed           2    no    We could not even ask which engine
                                          versions are published.
    refused_by_policy          4    no    persona itself refuses to install this
                                          build. Measuring a build persona will
                                          not install is the wrong question; the
                                          refusal is correct and this job must
                                          not re-litigate it.

⚠️ WHY ``no_predecessor`` IS GREEN AND ``predecessor_unreachable`` IS NOT — the
two are one letter apart in prose and opposite in meaning, and collapsing them
is the mistake this vocabulary exists to prevent:

  * ``no_predecessor`` rests on a SUCCESSFUL reading. We asked
    ``updater.engine_versions_newest_first()`` which engine versions exist, got
    a definitive answer, and it holds fewer than two. There is no comparison to
    make because there is no second thing in the world to compare against. This
    is structurally ``chromium-upstream-watch.yml``'s ``up_to_date``: nothing to
    decide today. **It is also the live state of this repository as this was
    written** — exactly one ``personium-`` tag is published — so making it red
    would ship a gate that is red from its first run until a second engine
    release lands, which is precisely the always-red gate the posture block
    above refuses to build.

  * ``predecessor_unreachable`` rests on a FAILURE. The version is in the tag
    list, so the comparison is supposed to be possible, and
    ``updater.fetch_release_full`` returned ``('','','')``. That function's own
    docstring names this case and says what a caller owes it: *"The caller must
    REPORT that plainly — a rollback that silently installs something else is
    worse than one that refuses."* So it is named, red, and never quietly
    substituted with some other build.

⚠️ EXIT 1 IS RESERVED AND DELIBERATELY UNALLOCATED. Across this repository's
gates — ``engine_gpu_variance.exit_code_for``, ``ps342_chromium_watch``,
``ps344_verdict``, ``ps299_rebase_probe`` — exit 1 means A FINDING: we looked,
and what we saw is a defect to fix. Under the reporting posture above, no
outcome of THIS comparison is such a defect: the move is recorded as
un-migratable, so calling it a finding would assert a repair that does not
exist. Leaving 1 unspent is the statement of that posture, not an oversight —
and it keeps the vocabulary shared with the four siblings, where a 1 from any of
them means the same thing. (``.github/scripts/ps344_gate_plan.py`` reserves it
identically, for the same reason.)

─────────────────────────────────────────────────────────────────────────────
⛔ THE TRAP THIS MODULE IS BUILT AROUND: AN UNREADABLE LEG IS NOT A DIFFERENCE
─────────────────────────────────────────────────────────────────────────────
This is not hypothetical and it is not someone else's mistake. It is PS-341's
OWN instrument history, recorded in ``scripts/ps341_gpu_seeds.py``'s header:

    An earlier draft read both builds with ``--headless=new --dump-dom`` and
    reported **8 seeds of 8 moved** — *"a clean, confident, and COMPLETELY
    FALSE result"*. The old build's headless arm returned no reading at all for
    every seed, and "no reading" compared against a real string is unequal, so
    every row scored MOVED. **The instrument, not the engine, produced the 8/8.**

So an unreadable leg lands in ``seeds_unreadable`` and is EXCLUDED from the
moved/same tally, never counted as a difference. ``read_pair`` already returns
``None`` rather than a sentinel for exactly this reason and that is preserved,
but this module does NOT rest on it: scorability is **DERIVED HERE** from the
row's own pair values (:func:`scorable`), because a judge that trusts the
instrument's self-report cannot catch an instrument that is lying.

⭐ AND THE SUMMARY IS RE-DERIVED, NEVER READ. ``classify`` recomputes
``seeds_scored`` / ``seeds_unreadable`` / ``moved`` / ``same`` from ``rows`` and
then CHECKS the record's own asserted summary against what it derived,
reporting ``record_inconsistent`` on a disagreement. The lesson is
``engine_gpu_variance.completeness``'s, paid for once already: PS-177's
``coverage_section()`` hardcoded *"all four GPU arms returned 24/24 readable
seeds"* and read no records at all — a reviewer nulled 12 of 24 readings and it
still printed 24/24. A claim that is structurally unable to become false is not
a check.

USAGE
─────
    # prove the judgement still refuses to launder a broken run (NO network,
    # NO engine, NO display — which is why the workflow runs it FIRST)
    python3 .github/scripts/ps341_continuity_verdict.py selftest

    # verdict a reading written by scripts/ps341_gpu_seeds.py
    python3 .github/scripts/ps341_continuity_verdict.py verdict /tmp/gpu_seeds.json

    # verdict PS-341's own committed reading of two genuinely different builds
    # (the falsification arm: this MUST report `moved`)
    python3 .github/scripts/ps341_continuity_verdict.py verdict \\
        readings/ps341-2026-09-07/gpu_seeds.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# ── the statuses ─────────────────────────────────────────────────────────────
HELD = "held"
MOVED = "moved"
NO_PREDECESSOR = "no_predecessor"
UNMEASURED = "unmeasured"
RECORD_INCONSISTENT = "record_inconsistent"
PREDECESSOR_UNREACHABLE = "predecessor_unreachable"
DISCOVERY_FAILED = "discovery_failed"
REFUSED_BY_POLICY = "refused_by_policy"

# Which statuses mean "we established something and nothing is owed today".
# This set is the SINGLE place that question is answered, and the tests assert
# on it directly. Note what is NOT in it: UNMEASURED and RECORD_INCONSISTENT.
GREEN_STATUSES = frozenset({HELD, MOVED, NO_PREDECESSOR})

EXIT_HELD = 0
EXIT_FINDING = 1          # RESERVED, deliberately unallocated — see the header.
EXIT_UNMEASURED = 2
EXIT_PREDECESSOR_UNREACHABLE = 3
EXIT_REFUSED_BY_POLICY = 4

EXIT_FOR_STATUS = {
    HELD: EXIT_HELD,
    MOVED: EXIT_HELD,
    NO_PREDECESSOR: EXIT_HELD,
    UNMEASURED: EXIT_UNMEASURED,
    RECORD_INCONSISTENT: EXIT_UNMEASURED,
    PREDECESSOR_UNREACHABLE: EXIT_PREDECESSOR_UNREACHABLE,
    DISCOVERY_FAILED: EXIT_UNMEASURED,
    REFUSED_BY_POLICY: EXIT_REFUSED_BY_POLICY,
}

# Which statuses are NEWS a human should receive rather than a green line in a
# log nobody is subscribed to. `chromium-upstream-watch.yml`'s convention, and
# reused rather than reinvented: the report is filed as an issue.
#
# HELD and NO_PREDECESSOR are deliberately absent. "The identity held" and
# "there is still only one published release" are not news, and filing them
# weekly forever would train the reader to ignore the ones that are.
REPORT_STATUSES = frozenset({
    MOVED, UNMEASURED, RECORD_INCONSISTENT,
    PREDECESSOR_UNREACHABLE, DISCOVERY_FAILED, REFUSED_BY_POLICY,
})

HEADLINE = {
    HELD: "The engine-authored WebGL identity HELD across the build change",
    MOVED: "The engine-authored WebGL identity MOVED across the build change",
    NO_PREDECESSOR: "Only one engine release is published — nothing to compare",
    UNMEASURED: "NOTHING WAS MEASURED — this is not a pass",
    RECORD_INCONSISTENT: "THE READING CONTRADICTS ITSELF — it cannot be trusted",
    PREDECESSOR_UNREACHABLE: "THE PREVIOUS BUILD COULD NOT BE OBTAINED — nothing was compared",
    DISCOVERY_FAILED: "COULD NOT ASK which engine releases are published",
    REFUSED_BY_POLICY: "persona itself REFUSES this build — it was not measured",
}


def is_green(status: str) -> bool:
    """Only a status that rests on an actual determination is green."""
    return status in GREEN_STATUSES


def exit_code_for(status: str) -> int:
    """The exit code for a status.

    ⚠️ An unknown status is ``EXIT_UNMEASURED``, never 0. An automation must
    have an answer for a status it does not recognise, and that answer is "we
    did not establish anything" — the same catch-all discipline
    ``ps342_chromium_watch.classify`` applies to an unrecognised probe exit.
    """
    return EXIT_FOR_STATUS.get(status, EXIT_UNMEASURED)


def scorable(row) -> bool:
    """Can this row's two legs be COMPARED at all? DERIVED, never read.

    ⛔ Deliberately does NOT consult the row's own ``readable`` flag. A judge
    that trusts the instrument's self-report cannot catch an instrument that is
    lying, and this whole module exists because PS-341's first probe produced a
    confident, completely false 8/8 (see the header). ``readable`` is checked
    AGAINST this in :func:`classify`, as a cross-check rather than as input.

    A leg counts only as a two-element pair of NON-EMPTY strings — vendor and
    renderer. ``None`` fails, and so does a pair of empty strings, which is the
    shape that would otherwise compare EQUAL to another empty pair and score a
    false ``same``: the mirror image of the false ``moved`` that started all
    this, and just as wrong.
    """
    if not isinstance(row, dict):
        return False
    for leg in ("new", "old"):
        v = row.get(leg)
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            return False
        if not all(isinstance(x, str) and x.strip() for x in v):
            return False
    return True


def row_moved(row) -> bool:
    """Did this row's pair change between the two builds? Only ask a SCORABLE
    row: an unscorable one has no answer, and inventing one for it is exactly
    the failure this module is built around."""
    return list(row["new"]) != list(row["old"])


def tally(rows) -> dict:
    """Re-derive the whole summary from the rows. PURE.

    ⭐ Nothing here reads an asserted total. ``engine_gpu_variance.completeness``
    records why in its own docstring, and it was paid for: a coverage claim that
    cannot become false is not a check.
    """
    rows = list(rows or [])
    scored = [r for r in rows if scorable(r)]
    moved = [r for r in scored if row_moved(r)]
    return {
        "seeds_attempted": len(rows),
        "seeds_scored": len(scored),
        "seeds_unreadable": len(rows) - len(scored),
        "moved": len(moved),
        "same": len(scored) - len(moved),
        "moved_seeds": [r.get("seed") for r in moved],
        "unreadable_seeds": [
            r.get("seed") if isinstance(r, dict) else None
            for r in rows if not scorable(r)
        ],
    }


def _asserted_summary(record) -> dict:
    return {
        k: record.get(k)
        for k in ("seeds_attempted", "seeds_scored", "seeds_unreadable", "moved", "same")
        if record.get(k) is not None
    }


def classify(record) -> dict:
    """Turn one ``gpu_seeds.json`` reading into a verdict. PURE — no network,
    no filesystem, no clock beyond what the caller passes in.

    THE ORDER OF THE THREE DECISIONS IS LOAD-BEARING and each is stated:

      1. **Nothing scored -> UNMEASURED.** Zero comparable rows is the
         signature of a broken venue (the ``--dump-dom`` substitution that
         produced PS-341's false 8/8 returns no reading at all on the old leg).
         It must never read as ``held``.

      2. **Any measured move -> MOVED, even from a truncated sample.** Gated on
         the move rather than ahead of it, deliberately, and for the reason
         ``engine_gpu_variance.exit_code_for`` gives for the same ordering:
         *truncation can hide a defect; it cannot invent one*. A seed that was
         read and moved, moved.

      3. **Any unreadable seed with NO move -> UNMEASURED.** This is the one
         that matters. "Every seed held" computed over a partial sample is
         exactly the claim truncation could falsify — the seeds that fail are
         the ones that ran LAST, so the surviving sample is position-biased
         rather than random. An all-same verdict is only earned over a COMPLETE
         sample.
    """
    if not isinstance(record, dict):
        return {
            "status": RECORD_INCONSISTENT,
            "error": "the reading is not a JSON object",
            **tally([]),
        }

    counted = tally(record.get("rows"))
    result = {
        "status": None,
        "new_version": record.get("new_version"),
        "old_version": record.get("old_version"),
        "new_binary": record.get("new_binary"),
        "old_binary": record.get("old_binary"),
        "measured_at": record.get("measured_at"),
        "error": None,
        **counted,
    }

    # THE CROSS-CHECK. The record's own summary is compared against what was
    # derived from its rows; a disagreement means the file cannot be trusted as
    # evidence of anything, whichever side is wrong. Reported rather than
    # silently preferring one — picking a side would be inventing a reading.
    asserted = _asserted_summary(record)
    derived = {k: counted[k] for k in asserted}
    if asserted and asserted != derived:
        result["status"] = RECORD_INCONSISTENT
        result["error"] = (
            "the reading's own summary %r disagrees with what its rows say %r. "
            "One of them is wrong and this file cannot say which, so nothing "
            "is concluded from it." % (asserted, derived)
        )
        return result

    # An asserted `readable` flag that disagrees with what the row's own values
    # support is the same class of fault, one level down: an instrument that
    # reports a leg as readable while carrying no readable leg.
    for row in record.get("rows") or []:
        if not isinstance(row, dict) or "readable" not in row:
            continue
        if bool(row.get("readable")) != scorable(row):
            result["status"] = RECORD_INCONSISTENT
            result["error"] = (
                "seed %r is marked readable=%r but its recorded pair says "
                "otherwise. The instrument is contradicting itself; nothing is "
                "concluded from this run." % (row.get("seed"), row.get("readable"))
            )
            return result

    if counted["seeds_scored"] == 0:
        result["status"] = UNMEASURED
        result["error"] = (
            "no seed produced a comparable pair on BOTH builds (%d attempted). "
            "Nothing was compared. This is the signature of a venue that did "
            "not come up, and it is NOT a pass."
            % counted["seeds_attempted"]
        )
        return result

    if counted["moved"] > 0:
        result["status"] = MOVED
        return result

    if counted["seeds_unreadable"] > 0:
        result["status"] = UNMEASURED
        result["error"] = (
            "%d of %d seeds were unreadable and were EXCLUDED from the tally "
            "(correctly). Every seed that WAS read held — but 'the identity "
            "held' computed over a partial sample is exactly the claim the "
            "missing seeds could falsify, so it is not earned here. A run to "
            "REPEAT, not a reading to act on."
            % (counted["seeds_unreadable"], counted["seeds_attempted"])
        )
        return result

    result["status"] = HELD
    return result


# ── the non-measurement outcomes, built here so they travel the same path ────
#
# Same argument `ps342_chromium_watch.invalid_tag_result` makes: the REPORT is
# the deliverable, so a cause that stops us measuring must still produce a
# result dict, a rendered report and a step output — never a traceback that
# writes no output, files no issue and uploads no artifact, leaving the run red
# and silent in an Actions tab this project has already recorded that nobody
# receives.


def _empty_tally() -> dict:
    return tally([])


def no_predecessor_result(new_version, published, now=None) -> dict:
    return {
        "status": NO_PREDECESSOR,
        "new_version": new_version,
        "old_version": None,
        "published_versions": list(published or []),
        "error": None,
        "measured_at": now or utcnow(),
        **_empty_tally(),
    }


def predecessor_unreachable_result(new_version, old_version, now=None) -> dict:
    return {
        "status": PREDECESSOR_UNREACHABLE,
        "new_version": new_version,
        "old_version": old_version,
        "error": (
            "engine version %r is published but its release could not be "
            "resolved to an asset for this OS (fetch_release_full returned "
            "('','','') — a yanked or deleted release, or one with no asset "
            "here). Nothing was compared, and nothing was substituted for it."
            % old_version
        ),
        "measured_at": now or utcnow(),
        **_empty_tally(),
    }


def discovery_failed_result(error, now=None) -> dict:
    return {
        "status": DISCOVERY_FAILED,
        "new_version": None,
        "old_version": None,
        "error": error,
        "measured_at": now or utcnow(),
        **_empty_tally(),
    }


def refused_by_policy_result(new_version, verdict, message, now=None) -> dict:
    return {
        "status": REFUSED_BY_POLICY,
        "new_version": new_version,
        "old_version": None,
        "policy_verdict": verdict,
        "error": (
            "persona's own policy refuses engine build %r (%s): %s. A build "
            "persona will not install is the wrong thing to measure, and this "
            "job does not re-litigate the refusal."
            % (new_version, verdict, message or "no message")
        ),
        "measured_at": now or utcnow(),
        **_empty_tally(),
    }


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── reporting ────────────────────────────────────────────────────────────────


def issue_title(result) -> str:
    """A stable title, so re-running does not file the same news twice.

    ⚠️ THIS TITLE IS THE DEDUP KEY — the workflow matches an OPEN issue by
    EXACT title and comments on it rather than filing a second one. So two
    unrelated causes sharing a title means the second is SUPPRESSED into a
    comment on the first, and `ps342_chromium_watch.issue_title` records what
    that costs. Both versions AND the status are in it deliberately: the same
    build pair going from `unmeasured` to `moved` is genuinely different news
    and deserves its own record rather than quietly editing away the fact that
    we once could not measure it.
    """
    status = result.get("status")
    if status == DISCOVERY_FAILED:
        return "[engine-continuity] could not read the published engine release list"
    if status == REFUSED_BY_POLICY:
        return "[engine-continuity] persona refuses engine %s — not measured" % (
            result.get("new_version") or "?"
        )
    return "[engine-continuity] %s vs %s — %s" % (
        result.get("new_version") or "?",
        result.get("old_version") or "?",
        status,
    )


def _rows_table(result, record) -> list:
    lines = [
        "",
        "| seed | build N (`%s`) | build N−1 (`%s`) | |" % (
            result.get("new_version") or "?", result.get("old_version") or "?"),
        "|---|---|---|---|",
    ]
    for row in (record or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        ok = scorable(row)
        mark = ("MOVED" if row_moved(row) else "same") if ok else "**UNREADABLE**"

        def cell(v):
            if not isinstance(v, (list, tuple)) or len(v) != 2:
                return "—"
            return "`%s`" % (" | ".join(str(x) for x in v)).replace("|", "\\|")

        lines.append("| `%s` | %s | %s | %s |" % (
            row.get("seed"), cell(row.get("new")), cell(row.get("old")), mark))
    return lines


def render_report(result, record=None) -> str:
    """Markdown for the issue body / job summary."""
    status = result.get("status")
    lines = ["## %s" % HEADLINE.get(status, "Unrecognised status %r" % status), ""]
    lines += [
        "| | |",
        "|---|---|",
        "| build N (newest published) | `%s` |" % (result.get("new_version") or "—"),
        "| build N−1 (its predecessor) | `%s` |" % (result.get("old_version") or "—"),
        "| verdict | **%s** |" % status,
        "| seeds attempted | %s |" % result.get("seeds_attempted"),
        "| seeds scored | %s |" % result.get("seeds_scored"),
        "| seeds unreadable | %s |" % result.get("seeds_unreadable"),
        "| moved / same | %s / %s |" % (result.get("moved"), result.get("same")),
        "| measured at | %s |" % (result.get("measured_at") or "—"),
        "",
    ]

    if status == HELD:
        lines += [
            "Every seed read the SAME engine-authored WebGL identity pair on "
            "both builds, over a complete sample. A profile's observable "
            "fingerprint on this vector survived the engine change.",
            "",
            "Nothing to decide today.",
        ]
    elif status == MOVED:
        lines += [
            "**%d of %d scored seeds read a DIFFERENT WebGL vendor/renderer "
            "pair after the engine change.**" % (
                result.get("moved"), result.get("seeds_scored")),
            "",
            "⚠️ **This is news, not a defect, and it is not an instruction to "
            "roll anything back.** The value is ENGINE-AUTHORED on the "
            "`windows` arm — `gpu_ext.ENGINE_AUTHORED_IDENTITY_ARMS` is "
            "`frozenset({\"windows\"})`, so persona's GPU layer deliberately "
            "stands down there and the engine's own compiled-in table is the "
            "single author. **No profile migration can change it**, which is "
            "why PS-341 recorded this as *\"recorded, not fixed\"* and why this "
            "job reports rather than refuses.",
            "",
            "**What this run is for.** PS-4's first claim is that where an "
            "engine change moves a surface, that must be *\"a decision someone "
            "made, recorded against a version, not a side effect nobody "
            "noticed\"*. This issue IS that record. Reading it and closing it "
            "is the decision.",
            "",
            "**What it does NOT establish.** Whether a site can LINK a profile "
            "across an engine update via this pair is unmeasured and is not "
            "claimed here. Two profiles on the SAME build still differ — that "
            "is `engine-gpu-variance.yml`'s question and it is unaffected. "
            "What moved is one profile against its own past.",
            "",
            "⛔ Coverage is the `windows` arm on Linux x86_64 only. This says "
            "nothing about macOS or Linux-declared profiles.",
        ]
        lines += _rows_table(result, record)
    elif status == NO_PREDECESSOR:
        pub = result.get("published_versions") or []
        lines += [
            "The published engine tag list holds %d version(s): %s." % (
                len(pub), ", ".join("`%s`" % v for v in pub) or "none"),
            "",
            "There is no predecessor build in existence, so there is nothing "
            "to compare. **This is a definitive answer, not a failure to "
            "look** — the tag list was read successfully and it says so. This "
            "job will begin comparing the moment a second `personium-` release "
            "is published.",
        ]
    elif status == UNMEASURED:
        lines += [
            "⛔ **NOTHING WAS ESTABLISHED. This is not a pass.**",
            "",
            "%s" % (result.get("error") or ""),
            "",
            "An unreadable leg is EXCLUDED from the moved/same tally and is "
            "never counted as a difference — that exclusion is deliberate and "
            "is why PS-341's first probe's *\"clean, confident, and COMPLETELY "
            "FALSE\"* 8/8 cannot recur here. But the exclusion is what makes "
            "the remaining sample partial, and a verdict over a partial sample "
            "is a run to repeat.",
            "",
            "Check the step log for whether the display or an engine failed to "
            "come up before reading anything into this.",
        ]
        lines += _rows_table(result, record)
    elif status == RECORD_INCONSISTENT:
        lines += [
            "⛔ **THE READING CONTRADICTS ITSELF, so nothing is concluded from "
            "it.**",
            "",
            "%s" % (result.get("error") or ""),
            "",
            "The verdict re-derives every count from the per-seed rows and "
            "then checks the reading's own asserted summary against it. A "
            "disagreement means one side is wrong and this file cannot say "
            "which, so it is reported rather than resolved by preferring one — "
            "preferring one would be inventing a reading.",
        ]
    elif status == PREDECESSOR_UNREACHABLE:
        lines += [
            "⛔ **THE COMPARISON COULD NOT BE SET UP. This is not a finding "
            "about the engine, and it is not a pass.**",
            "",
            "%s" % (result.get("error") or ""),
            "",
            "`updater.fetch_release_full`'s own docstring names this case and "
            "what a caller owes it: *\"The caller must REPORT that plainly — a "
            "rollback that silently installs something else is worse than one "
            "that refuses.\"* Nothing was substituted for the missing build.",
        ]
    elif status == DISCOVERY_FAILED:
        lines += [
            "⛔ **NOTHING WAS MEASURED.** %s" % (result.get("error") or ""),
            "",
            "We could not read the published engine tag list, so we do not "
            "know which builds exist, let alone whether the identity moved "
            "between two of them. Usually transient; re-dispatch before "
            "reading anything into it.",
        ]
    elif status == REFUSED_BY_POLICY:
        lines += [
            "%s" % (result.get("error") or ""),
            "",
            "This is the CORRECT outcome after someone blocklists a bad tag in "
            "`src/services/engine/policy.py`. It clears when a newer engine "
            "release supersedes the refused one.",
        ]

    lines += [
        "",
        "---",
        "",
        "Produced by `.github/workflows/engine-continuity.yml` from "
        "`scripts/ps341_gpu_seeds.py`'s probe (PS-341's instrument, imported "
        "rather than forked). Exit contract and posture: "
        "`.github/scripts/ps341_continuity_verdict.py`.",
    ]
    return "\n".join(lines) + "\n"


def write_github_output(result, path) -> None:
    """Append the step outputs the workflow branches on.

    ⚠️ Each output is a bare ``key=value`` line with no delimiter, so an
    embedded newline FORGES additional outputs — `ps342_chromium_watch` records
    a measured instance of exactly that (a `--tag` value carrying `\\ngreen=true`
    handing the dispatcher a green on a run that measured nothing). Every value
    written here is therefore either a fixed vocabulary word, a boolean, an
    integer, or a version string that has been through
    :func:`_safe`. The prose — which can contain anything — goes only into the
    report BODY, which is written to a FILE and passed with ``--body-file``.
    """
    def _safe(v):
        return "".join(c for c in str(v if v is not None else "")
                       if c not in "\r\n")[:200]

    status = result.get("status")
    with open(path, "a", encoding="utf-8") as f:
        f.write("status=%s\n" % _safe(status))
        f.write("green=%s\n" % ("true" if is_green(status) else "false"))
        f.write("report=%s\n" % ("true" if status in REPORT_STATUSES else "false"))
        f.write("title=%s\n" % _safe(issue_title(result)))
        f.write("new_version=%s\n" % _safe(result.get("new_version")))
        f.write("old_version=%s\n" % _safe(result.get("old_version")))
        f.write("moved=%s\n" % _safe(result.get("moved")))
        f.write("seeds_scored=%s\n" % _safe(result.get("seeds_scored")))
        f.write("seeds_unreadable=%s\n" % _safe(result.get("seeds_unreadable")))


# ── the selftest ─────────────────────────────────────────────────────────────


def _row(seed, new, old, readable=None):
    row = {"seed": seed, "new": new, "old": old}
    if readable is not None:
        row["readable"] = readable
    return row


NVIDIA = ["Google Inc. (NVIDIA)", "ANGLE (NVIDIA, RTX 3060 (0x00002487) D3D11)"]
INTEL = ["Google Inc. (Intel)", "ANGLE (Intel, Iris(R) Xe (0x00009A49) D3D11)"]


def _selftest_cases():
    """Synthesised readings driven through the SAME classify -> exit_code_for
    path the live run gates on. Each names the real shape it stands for.
    """
    seeds = [3805799318, 12345, 1, 999983, 42424242, 777, 20260907, 88888888]

    all_moved = {"rows": [_row(s, NVIDIA, INTEL) for s in seeds]}
    none_moved = {"rows": [_row(s, NVIDIA, NVIDIA) for s in seeds]}
    all_unreadable = {"rows": [_row(s, None, None) for s in seeds]}
    # THE FALSIFICATION CASE. The old leg produced no reading for any seed —
    # precisely the `--dump-dom` shape that made PS-341's first probe report a
    # confident, completely false 8/8. It must NOT be `moved`.
    old_leg_dark = {"rows": [_row(s, NVIDIA, None) for s in seeds]}
    # Truncation with no move: an all-same claim over a partial sample.
    partial_same = {"rows": (
        [_row(s, NVIDIA, NVIDIA) for s in seeds[:5]]
        + [_row(s, NVIDIA, None) for s in seeds[5:]]
    )}
    # Truncation WITH a move: truncation can hide a defect, not invent one.
    partial_moved = {"rows": (
        [_row(s, NVIDIA, INTEL) for s in seeds[:5]]
        + [_row(s, None, None) for s in seeds[5:]]
    )}
    # An empty pair on BOTH legs compares equal — the mirror image of the false
    # `moved`, and just as wrong. It must not score `held`.
    empty_pairs = {"rows": [_row(s, ["", ""], ["", ""]) for s in seeds]}
    # The instrument contradicting itself.
    lying_flag = {"rows": [_row(s, NVIDIA, None, readable=True) for s in seeds]}
    bad_summary = {
        "seeds_scored": 8, "moved": 8, "same": 0, "seeds_unreadable": 0,
        "rows": [_row(s, NVIDIA, NVIDIA) for s in seeds],
    }
    no_rows = {"rows": []}

    return [
        ("all-moved", all_moved, MOVED, EXIT_HELD),
        ("none-moved", none_moved, HELD, EXIT_HELD),
        ("all-unreadable", all_unreadable, UNMEASURED, EXIT_UNMEASURED),
        ("old-leg-dark", old_leg_dark, UNMEASURED, EXIT_UNMEASURED),
        ("mixed-truncated-same", partial_same, UNMEASURED, EXIT_UNMEASURED),
        ("mixed-truncated-moved", partial_moved, MOVED, EXIT_HELD),
        ("empty-pairs-both-legs", empty_pairs, UNMEASURED, EXIT_UNMEASURED),
        ("instrument-lying", lying_flag, RECORD_INCONSISTENT, EXIT_UNMEASURED),
        ("summary-disagrees", bad_summary, RECORD_INCONSISTENT, EXIT_UNMEASURED),
        ("no-rows-at-all", no_rows, UNMEASURED, EXIT_UNMEASURED),
        ("no-predecessor", no_predecessor_result("152.0.7977.75", ["152.0.7977.75"]),
         NO_PREDECESSOR, EXIT_HELD),
        ("predecessor-unreachable",
         predecessor_unreachable_result("152.0.7977.75", "148.0.7778.215"),
         PREDECESSOR_UNREACHABLE, EXIT_PREDECESSOR_UNREACHABLE),
        ("discovery-failed", discovery_failed_result("upstream did not answer"),
         DISCOVERY_FAILED, EXIT_UNMEASURED),
        ("refused-by-policy",
         refused_by_policy_result("152.0.7977.75", "known_bad", "blocklisted"),
         REFUSED_BY_POLICY, EXIT_REFUSED_BY_POLICY),
    ]


def _cmd_selftest(args) -> int:
    """Prove this judgement still refuses to launder a broken run, BEFORE
    trusting a verdict from it.

    A gate is only worth wiring if its failing outcomes are reachable, and the
    live run cannot demonstrate that: it can only ever show what today's two
    builds happen to produce. That is precisely the "check that could not have
    failed" this project does not count as coverage.

    So the job runs this FIRST, and it needs no engine, no display and no
    network. If the judgement is ever broken such that an unreadable run reads
    as `held`, or a dark leg reads as a move, THIS goes red — on the gate's own
    path, on every run — instead of the job quietly reporting a verdict it is no
    longer able to withhold. Placed before provisioning on purpose: two ~200 MB
    downloads to produce a meaningless green are wasted, and the red the job
    would report would be the wrong red.
    """
    failures = []
    for name, payload, want_status, want_exit in _selftest_cases():
        result = classify(payload) if "rows" in payload else payload
        status = result["status"]
        code = exit_code_for(status)
        ok = (status == want_status) and (code == want_exit)
        print("[selftest] %-24s expected %-24s exit %d  ->  got %-24s exit %d  %s"
              % (name, want_status, want_exit, status, code,
                 "ok" if ok else "WRONG"))
        if not ok:
            failures.append((name, want_status, want_exit, status, code))

    # THE INVARIANT THE WHOLE MODULE RESTS ON, asserted rather than assumed: an
    # unreadable leg lands in `seeds_unreadable` and is EXCLUDED from the
    # moved/same tally. AC 3 of PS-375, and PS-341's own recorded near-miss.
    dark = classify({"rows": [_row(s, NVIDIA, None) for s in (1, 2, 3)]})
    if dark["moved"] != 0 or dark["seeds_unreadable"] != 3 or dark["seeds_scored"] != 0:
        failures.append(("dark-leg-tally", "moved=0 unreadable=3 scored=0", 0,
                         "moved=%s unreadable=%s scored=%s" % (
                             dark["moved"], dark["seeds_unreadable"],
                             dark["seeds_scored"]), 0))
    else:
        print("[selftest] an unreadable leg is EXCLUDED from the tally "
              "(moved=0, unreadable=3) rather than scoring as a difference.")

    if failures:
        print("\nSELF-TEST FAILED: this verdict can no longer be trusted.",
              file=sys.stderr)
        for name, ws, we, gs, gc in failures:
            print("  %s: expected %s/exit %s, got %s/exit %s"
                  % (name, ws, we, gs, gc), file=sys.stderr)
        print("A verdict from the live run below would be meaningless while "
              "this is broken, so the job stops here rather than producing "
              "one.", file=sys.stderr)
        return EXIT_UNMEASURED

    print("\n[selftest] the verdict still refuses to call an unmeasured run a "
          "pass, still reports a measured move as news rather than a failure, "
          "and still names 'cannot obtain N−1' as its own outcome.")
    return EXIT_HELD


def _cmd_verdict(args) -> int:
    try:
        with open(args.record, encoding="utf-8") as f:
            record = json.load(f)
    except Exception as exc:               # noqa: BLE001 — the report IS the deliverable
        record = None
        result = {
            "status": RECORD_INCONSISTENT,
            "new_version": None, "old_version": None,
            "error": "could not read %r: %s" % (args.record, exc),
            "measured_at": utcnow(),
            **_empty_tally(),
        }
    else:
        result = classify(record)
        result.setdefault("measured_at", None)
        if not result.get("measured_at"):
            result["measured_at"] = utcnow()
        if args.new_version:
            result["new_version"] = args.new_version
        if args.old_version:
            result["old_version"] = args.old_version

    return emit(result, record, args)


def emit(result, record, args) -> int:
    """Write every artifact, then return the status's exit code.

    The order matters: the report, the JSON and the step outputs are all
    written BEFORE the code is returned, so a red outcome still files its issue
    and uploads its evidence instead of vanishing.
    """
    report = render_report(result, record)
    if getattr(args, "report_json", None):
        os.makedirs(os.path.dirname(os.path.abspath(args.report_json)) or ".",
                    exist_ok=True)
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump({"verdict": result, "reading": record}, f, indent=2)
    if getattr(args, "report_md", None):
        os.makedirs(os.path.dirname(os.path.abspath(args.report_md)) or ".",
                    exist_ok=True)
        with open(args.report_md, "w", encoding="utf-8") as f:
            f.write(report)
    if getattr(args, "github_output", None):
        write_github_output(result, args.github_output)
    print(report)
    return exit_code_for(result["status"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ps341_continuity_verdict",
        description="the verdict on engine continuity (PS-375, wiring PS-341)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser(
        "selftest",
        help="prove the verdict can still withhold a pass, without the engine")
    s.set_defaults(func=_cmd_selftest)

    v = sub.add_parser(
        "verdict", help="verdict a gpu_seeds.json reading")
    v.add_argument("record", help="a reading written by scripts/ps341_gpu_seeds.py")
    v.add_argument("--new-version", default="", help="label for build N")
    v.add_argument("--old-version", default="", help="label for build N−1")
    v.add_argument("--report-json", default="")
    v.add_argument("--report-md", default="")
    v.add_argument("--github-output", default="", help="append outputs here")
    v.set_defaults(func=_cmd_verdict)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
