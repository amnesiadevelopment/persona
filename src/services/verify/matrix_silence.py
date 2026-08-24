"""N checker-matrix records in, the checkers that NEVER ANSWERED out.

``matrix_diff`` answers *did anything change between these two records?* This
answers a different question that the pairwise comparator cannot be asked at
all: **has this checker ever answered, in any record we hold?**

Why the pairwise lane cannot answer it
--------------------------------------
:func:`matrix_diff.compare_records` takes exactly two records, and it is the
only comparison entry point in the subsystem. Every question put to it is
therefore a question about a PAIR. "Never" is a quantifier over a SET, and no
amount of pairwise comparison recovers it: a checker that is ``unobtainable``
on both sides of every pair is, to the comparator, a row that did not move —
which is its agreement signal. So the matrix presents as 16 wide while some of
it has never once been read, and nothing in the subsystem is capable of
noticing. That is a gap in what can be ASKED, not a bug in what was answered.

Each of the silent checkers already carries a ``note_unreachable`` written by
the catalogue author, recording that failure honestly and ONCE
(*"recorded as UNOBTAINABLE per run rather than dropped"*). The author was
right to refuse to shrink the matrix. Nobody has ever asked whether those
per-run notes ADD UP. This asks.

The partition is the finding — the raw count is not
---------------------------------------------------
**Reporting the bare number of silent checkers is the failure mode this module
exists to avoid.** The catalogue carries a declared-intent field, ``tier``, and
``tier='unreadable'`` means the author already established that this checker
CANNOT be read — click-gated, paywalled, behind a Cloudflare challenge — and
wrote the reason down. Those checkers being silent is not news; it is the
recorded steady state, and it is what ``unreadable`` MEANS.

So a report that alarms on all of them is mostly false alarm, and on an
alarm-shaped deliverable a finding's value is not monotonic in its size: every
by-design member discounts every genuine one, and a reader who learns the gate
cries wolf stops reading it — at which point the real finding is lost too. The
alarm is a **readable-tier** checker (``json`` or ``browser``) that never
answered: something the apparatus BELIEVES it is reading and is not.

An unrecognised tier reports as an ALARM, deliberately. A checker missing from
the catalogue has no established reason to be unreadable, and the conservative
direction is the same one ``matrix_diff`` takes with an untagged row: what
cannot be classified is reported with the findings, never quietly filed under
the harmless heading.

The evidence floor: one record cannot establish "never"
-------------------------------------------------------
A silence reading over a single record is unfalsifiable — every checker that
happened to fail in that one run reads as "never answered", which is a
statement about one bad afternoon and not about the matrix. So a set of fewer
than two records is REFUSED rather than reported clean, the same floor PS-92
applied to ``compare`` and PS-110 to ``read``, one artifact over.

What this deliberately does NOT do
----------------------------------
It takes no reading, touches no network and ships no protection. It makes an
already-recorded fact legible. Repairing reachability for the checkers it names
is separate work behind a different seam. It also does not touch per-row
classification or the pairwise comparator: it consumes ``state`` through the
same :func:`evidence.obtained` definition every other lane uses, so this lane
cannot quietly come to disagree with them about what a reading IS.

**This is a set, not yet a time series.** The records it reads are measurement
campaigns, not a history across releases. "Never answered" is rigorous over
every record that exists, which is exactly the claim made — but it is not a
temporal history and must not be described as one.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .checkers import CHECKERS, TIER_UNREADABLE
from .evidence import obtained
from .matrix_diff import NotARecord, RecordUnreadable, require_record
from .snapshot import quote_path

# --- classifications --------------------------------------------------------

# A READABLE-tier checker (json/browser) that never answered in any record in
# the set. THE ALARM: the matrix presents this checker as part of its width and
# has never once obtained a reading from it.
NEVER_ANSWERED = "never-answered"

# A checker the catalogue already declares unreadable, silent across the set.
# Expected, recorded, and NOT a finding — reported so the set is accounted for
# in full, never so it is alarmed on.
CARRIED = "carried"

# The minimum set size that can support a claim about "never". See the module
# header: one record makes the reading unfalsifiable.
MINIMUM_RECORDS = 2


class NotEnoughRecords(ValueError):
    """Raised when the record set is too small to establish "never answered".

    A refusal, not a finding — and the distinction is the whole reason this
    class exists rather than an empty result. Zero silent checkers over one
    record and zero silent checkers over twenty are the same VALUE and
    completely different EVIDENCE, so returning "nothing is silent" for a set
    that could not have shown silence would be a false green of exactly the
    kind :class:`matrix_diff.NotARecord` was minted to prevent.

    Surfaces as the CLI's exit 2, the convention PS-61 settled: nothing was
    established, so it can never wear a code that means something was.
    """


def _tiers() -> "dict[str, str]":
    """Checker id -> declared tier, read from the catalogue objects.

    Keyed on ``.id`` because that is the field the dataclass actually carries;
    it has no ``.host`` and no ``.name``. This matters more than it looks: a
    missed attribute on a dataclass without ``__slots__`` does not raise, it
    resolves to ``None`` — which would delete the partitioning field for every
    entry at once, collapse the tier split into a single bucket, and leave
    precisely the undifferentiated count this module exists to prevent. The
    output would still look authoritative. Read the field, never guess it.
    """
    return {c.id: c.tier for c in CHECKERS}


def answered_by_record(records: "Iterable[Any]") -> "dict[str, int]":
    """Checker id -> how many records obtained at least ONE reading from it.

    The unit is the RECORD, not the row. A checker with forty unobtainable rows
    and one ``read`` row in the same record ANSWERED in that record: the
    question here is whether the checker was ever reachable, not how much of it
    was read once it was.
    """
    counts: "dict[str, int]" = {}
    for record in records:
        require_record(record)
        answered = set()
        for row in record.get("readings", []):
            if isinstance(row, dict) and obtained(row):
                answered.add(str(row.get("checker", "")))
        for checker_id in answered:
            counts[checker_id] = counts.get(checker_id, 0) + 1
    return counts


def silence_pass(records: "Iterable[Any]") -> "list[dict]":
    """Report every checker that never answered across the whole set.

    Returns one entry per SILENT checker, each carrying its ``classification``
    (:data:`NEVER_ANSWERED` or :data:`CARRIED`), its ``tier``, and the
    ``records`` the claim ranges over — so a reader is never handed a bare
    "never" without being told over how many records "never" was measured.

    A checker that answered in even one record is not silent, however badly it
    did elsewhere. That is the discriminator the whole report rests on: an
    intermittent checker is a reachability problem, and folding it in here
    would drown the checkers that have genuinely never been read.

    Refuses a set smaller than :data:`MINIMUM_RECORDS`; see
    :class:`NotEnoughRecords`.
    """
    records = list(records)
    if len(records) < MINIMUM_RECORDS:
        raise NotEnoughRecords(
            f"a silence pass needs at least {MINIMUM_RECORDS} records and got "
            f"{len(records)}, so NOTHING WAS ESTABLISHED. This is NOT 'no "
            "checker is silent': over a single record every checker that "
            "happened to fail in that one run reads as 'never answered', "
            "which says something about that run and nothing about the "
            "matrix. 'Never' is a claim about a set — hand it a set."
        )

    counts = answered_by_record(records)
    tiers = _tiers()

    # Every checker the catalogue knows about, plus any that appear in the
    # records without being in the catalogue. The union, not the catalogue
    # alone: a checker present in the records and absent from the catalogue is
    # exactly the case that must not fall silently out of the report.
    seen: "set[str]" = set(tiers)
    for record in records:
        for row in record.get("readings", []):
            if isinstance(row, dict):
                seen.add(str(row.get("checker", "")))
    seen.discard("")

    entries = []
    for checker_id in sorted(seen):
        if counts.get(checker_id, 0):
            continue
        tier = tiers.get(checker_id)
        carried = tier == TIER_UNREADABLE
        entries.append(
            {
                "checker": checker_id,
                "tier": tier,
                "classification": CARRIED if carried else NEVER_ANSWERED,
                "records": len(records),
                "answered_in": 0,
            }
        )
    # Alarms first: the report is read top-down and the findings must not sit
    # below the expected entries.
    entries.sort(key=lambda e: (e["classification"] != NEVER_ANSWERED, e["checker"]))
    return entries


def alarms(entries: "Iterable[dict]") -> "list[dict]":
    """The entries that are FINDINGS — readable-tier checkers that never read.

    The counterpart of :func:`matrix_diff.findings`, and the predicate the exit
    code is taken from. Never the length of the whole entry list: that number
    mixes the two populations and is the wrong number to report.
    """
    return [e for e in entries if e.get("classification") == NEVER_ANSWERED]


def carried(entries: "Iterable[dict]") -> "list[dict]":
    """The entries that are EXPECTED — catalogue-declared unreadable checkers."""
    return [e for e in entries if e.get("classification") == CARRIED]


def discover_record_paths(root: str) -> "list[str]":
    """Every checker-matrix record under ``root``, found by PAYLOAD SHAPE.

    Discovery is by shape (``readings`` is a list) rather than by directory
    name, and that is load-bearing rather than fastidious. The records live in
    per-campaign directories that are created by each new recording campaign;
    anything keyed on a remembered subdirectory — or worse, on an expected
    COUNT of files — silently stops ranging over the artifacts it exists to
    consume the moment the next legitimate campaign lands. A count of on-disk
    artifacts is a latent false RED for a feature whose whole job is to read
    those artifacts.

    Returns sorted paths so a report over the set is stable between runs.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in {".git", "node_modules", "__pycache__", ".venv"}
        ]
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # Not every JSON file in the tree is a record, and a file this
                # pass cannot read is not this pass's finding to report.
                continue
            if isinstance(payload, dict) and isinstance(payload.get("readings"), list):
                found.append(path)
    return sorted(found)


def load_record(path: str) -> dict:
    """Load one record file, refusing anything that is not a record.

    Refuses at the load site so the message can name the FILE, matching
    ``checker_cli``'s existing ``_load_record``.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecordUnreadable(
            f"{quote_path(path)} could not be read as a record: {exc}"
        ) from exc
    return require_record(payload, source=path)


def format_silence(entries: "list[dict]", *, records: int) -> str:
    """Render the pass for a human, with the two populations kept APART.

    The sections are the point. A single list of eight checkers is the report
    this module was written to not produce.
    """
    lines = [
        f"SILENCE PASS over {records} record(s)",
        "",
    ]
    found = alarms(entries)
    if found:
        lines.append(
            f"FINDINGS — {len(found)} readable-tier checker(s) NEVER answered "
            f"in any of the {records} records:"
        )
        for entry in found:
            tier = entry["tier"] if entry["tier"] is not None else "(not in catalogue)"
            lines.append(f"  {entry['checker']}   tier={tier}   answered in 0/{records}")
        lines.append("")
        lines.append(
            "  These are not click-gated or paywalled: the catalogue declares "
            "them readable, so the matrix counts them in its width while "
            "never once obtaining a reading from them. Each carries a "
            "per-run `note_unreachable`; this is those notes ADDED UP."
        )
    else:
        lines.append(
            f"FINDINGS — none. Every readable-tier checker answered in at "
            f"least one of the {records} records."
        )

    expected = carried(entries)
    lines.append("")
    if expected:
        lines.append(
            f"CARRIED (expected, NOT findings) — {len(expected)} checker(s) "
            "the catalogue declares unreadable:"
        )
        for entry in expected:
            lines.append(f"  {entry['checker']}   tier={entry['tier']}")
        lines.append(
            "  Silence here is the recorded steady state — click-gated, "
            "paywalled, or behind a challenge — and each carries a written "
            "`unreadable_reason`. Alarming on these would train a reader to "
            "ignore the section above."
        )
    else:
        lines.append("CARRIED — none.")
    return "\n".join(lines)


__all__ = [
    "CARRIED",
    "MINIMUM_RECORDS",
    "NEVER_ANSWERED",
    "NotARecord",
    "NotEnoughRecords",
    "RecordUnreadable",
    "alarms",
    "answered_by_record",
    "carried",
    "discover_record_paths",
    "format_silence",
    "load_record",
    "silence_pass",
]
