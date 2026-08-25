"""ONE record in, the vectors that CONTRADICT THEMSELVES out.

``matrix_diff`` answers *did anything change between these two records?* and
``matrix_silence`` answers *has this checker ever answered, in any record we
hold?* This answers a third question that neither can be asked:
**does this single record agree with ITSELF?**

Why neither existing lane can answer it
---------------------------------------
:func:`matrix_diff.compare_records` is strictly pairwise and keyed PER ROW: it
walks ``set(before_rows) | set(after_rows)`` and asks whether each row moved
between two records. Two rows *within one record* are never brought into
contact, so a record in which ``creepjs`` says AMD and ``pixelscan`` says NVIDIA
presents to the comparator as two rows that each held perfectly still. Its
agreement signal fires on a flat self-contradiction.

Per-row classification cannot see it either, and that is the structural point
rather than an oversight. ``adverse`` is decided by looking at ONE row: the
catalogue asks "is *this* value a tell?" and a plausible desktop GPU string is
not one. Both halves of the contradiction that motivated this module
(PS-155) are individually plausible — an AMD integrated part and a discrete
NVIDIA card are each a perfectly ordinary thing for a machine to have — so
every one of the four rows was correctly scored ``adverse: false`` by a
per-row judgement that was working as designed. **A contradiction is a
property of a PAIR of rows, and nothing in the subsystem held a pair.**

The observation this was built from
-----------------------------------
``readings/ps150-2026-08-24/arm-a-baseline-layer-on.json`` — one live Chromium
run, one profile, one exit — carries four rows tagged ``vector:
"gpu_claimed"``, of which ``creepjs`` reads an AMD Radeon and ``pixelscan.net``
reads an NVIDIA RTX 3070. A real machine has one GPU. All four rows are
``adverse: false``.

WHAT "MATERIALLY DIFFERENT" MEANS, AND WHY IT IS NOT STRING EQUALITY
--------------------------------------------------------------------
**Raw string equality is not a conservative starting point that then needs
tuning down — it is measurably WRONG, and it is wrong in both directions.**
Run over the 21 readable records in ``readings/``, equality fires on **14** of
them while the true count of GPU-identity contradictions is **3**. Every one of
the 11 false alarms comes from one of two causes, and both are structural
rather than incidental:

1. **Rows in the same vector carry different KINDS of value.** ``gpu_claimed``
   holds both a vendor string (``Google Inc. (AMD)``) and a renderer string
   (``ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) …)``). These two never
   match as text and were never supposed to. The item names differ per checker
   as well — ``creepjs`` emits ``gpu_renderer``/``gpu_vendor`` while
   ``pixelscan.net`` emits ``webgl_renderer``/``webgl_vendor`` — so grouping by
   item does not rescue equality either; across checkers it groups nothing.
2. **One vector is not cross-comparable at all.** See ``GPU_RENDERED`` below.

So the comparison cannot be made on the raw text. It is made on the **IHV
identity** — the hardware vendor named inside the value — because that is the
one thing a vendor string and a renderer string both carry, and it is precisely
what the contradiction is ABOUT. ``Google Inc. (AMD)`` and ``ANGLE (AMD,
…)`` agree at that level; ``Google Inc. (AMD)`` and ``Google Inc. (NVIDIA)`` do
not.

A second, FINER term is applied where — and only where — it is meaningful:
when two values are both full ``ANGLE (…)`` adapter strings, their normalised
adapter text is compared too, so a same-brand/different-model disagreement
(an AMD RX 6600 against an AMD R9 200) is caught rather than laundered by the
brand match. It is not applied between a vendor string and a renderer string,
which are different kinds and would always differ.

Both terms were run over the corpus before being chosen. Each fires on exactly
the same 3 records — the finer term adds no false alarm, so it is kept for the
strictness rather than dropped for the quiet. Reported in full in the PR, per
the ticket's instruction that a rule's corpus behaviour is a finding to state
and not a threshold to tune until the noise stops.

WHAT IS DELIBERATELY *NOT* COMPARED, AND WHY THAT IS NOT A GAP
---------------------------------------------------------------
``GPU_RENDERED`` rows are **never** compared across checkers, and this is a
declared property of the vector rather than a shortcut. Those rows carry
HASHES computed by each checker from pixels it drew itself — ``creepjs`` emits
an 8-hex digest (``4c7ac378``) and ``pixelscan.net`` a 32-hex one
(``2bcfee1204804fa8ed34ccae53f2362a``). They are different algorithms over
different inputs and **cannot be equal even when the machine is behaving
perfectly**. Comparing them would manufacture a contradiction in every record
that reads both checkers — 14 of 21, the false-alarm population above.

This mirrors the distinction the catalogue already draws in
``checkers.GPU_CLAIMED`` / ``GPU_RENDERED``: claimed values are strings persona
CHOOSES and can therefore be held to agree; rendered values fall out of
whatever rasteriser actually drew the frame and persona does not choose them.
Only the first kind can meaningfully contradict itself.

Comparability is therefore read from :data:`COMPARABLE_VECTORS` — DECLARED,
one place, next to the reason — never inferred from the values at runtime. A
vector this module has not been taught about is reported as
:data:`NOT_COMPARABLE` with that stated, never silently compared and never
silently passed.

NULL IS NOT AGREEMENT
---------------------
The single largest population in the corpus is records whose ``gpu_claimed``
values are all ``None`` — 7 of 21. **A naive equality check scores every one of
them as consistent**, because a set of identical nulls is a set of size one.
That is the exact collapse PS-144 was written to prevent, arriving through a
different door: *"we never looked"* wearing the costume of *"they agreed"*.

Such a record is classified :data:`COVERAGE_HOLE` — neither a contradiction nor
a pass — and a hole is reported alongside the findings rather than under them,
so a record that established nothing can never read as a record that
established agreement.

A value can also be present and still say nothing: ``pixelscan.net`` renders a
literal ``"-"`` placeholder on 6 records, in a row whose ``state`` is ``read``.
An unreadable value in a read row is a coverage hole too — it is only visible
by looking at the value, which is why identifiability is judged here and not
taken from ``state``.

What this deliberately does NOT do
----------------------------------
It takes no reading, touches no network and ships no protection. It reads a
record already on disk and makes a fact already recorded in it legible. It does
not modify per-row ``adverse`` classification, and it does not attempt to say
WHICH of two contradicting values is the correct one — a record does not carry
the evidence to settle that, and a module that guessed would be inventing the
answer to the more important question.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .checkers import GPU_CLAIMED, GPU_RENDERED
from .matrix_diff import NotARecord, RecordUnreadable, require_record

# --- what may be compared ---------------------------------------------------

# Vectors whose rows carry a value two checkers can be held to AGREE on.
#
# Declared here rather than inferred at runtime, and the entry carries its
# reason so a future vector is added with a decision rather than by default.
# See the module header: comparability is a property OF THE VECTOR, and
# guessing it from the values is how a hash vector gets compared.
COMPARABLE_VECTORS: "dict[str, str]" = {
    GPU_CLAIMED: (
        "the adapter identity the renderer DECLARES — a value persona chooses, "
        "so two checkers reading it must agree"
    ),
}

# Vectors explicitly established as NOT cross-comparable, with the reason.
# Kept as data rather than as an `if` so the report can print WHY a vector was
# not compared instead of silently omitting it.
INCOMPARABLE_VECTORS: "dict[str, str]" = {
    GPU_RENDERED: (
        "per-checker HASHES computed from pixels each checker drew itself "
        "(creepjs 8-hex, pixelscan 32-hex) — different algorithms over "
        "different inputs, so they cannot be equal even on a healthy run"
    ),
}

# --- classifications --------------------------------------------------------

# THE ALARM: two rows in the same vector, both read, both identifiable, naming
# DIFFERENT hardware. The record contradicts itself.
CONTRADICTION = "contradiction"

# THE LOUDER ALARM: a row names a SOFTWARE RASTERISER — SwiftShader, llvmpipe.
# That is not a spoof disagreeing with another spoof; it is the machine
# underneath showing through, because a host with no GPU is what draws in
# software. PS-155 ranks this above a contradiction in so many words: "a leak
# of the true adapter is a materially worse finding than an inconsistent
# spoof".
#
# It is a finding ON ITS OWN and needs no second row to disagree with. One such
# row IS the finding — which is the whole distinction from CONTRADICTION, a
# property that requires a PAIR. A record where every checker leaks in unison
# holds no disagreement at all and is the WORST case, not the cleanest.
HOST_LEAK = "host-leak"

# The vector's rows agree everywhere they could be read.
CONSISTENT = "consistent"

# Rows are missing, null, or present-but-unidentifiable. NOT a pass and NOT a
# contradiction: nothing was established. See the module header — this is the
# class that stops "we failed to look" from reading as "they agreed".
COVERAGE_HOLE = "coverage-hole"

# The vector is not cross-comparable (declared), or this module has not been
# taught about it. Reported with its reason, never compared, never passed.
NOT_COMPARABLE = "not-comparable"

# A vector present in the record that :data:`COMPARABLE_VECTORS` and
# :data:`INCOMPARABLE_VECTORS` both fail to name. Reported as NOT_COMPARABLE
# with this as its reason — the conservative direction the rest of the
# subsystem takes with anything it cannot classify.
UNKNOWN_VECTOR_REASON = (
    "this vector is not declared in COMPARABLE_VECTORS or "
    "INCOMPARABLE_VECTORS, so whether its rows may be held to agree has never "
    "been decided — reported rather than compared, and NOT scored as a pass"
)


# --- identity extraction ----------------------------------------------------

# Values that are rendered placeholders rather than readings. `pixelscan.net`
# emits a literal "-" in a row whose `state` is "read", on 6 corpus records.
_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "null", "unknown", "?"}

# THE PARSER IS ANGLE-SHAPED BY DECISION, NOT BY OVERSIGHT.
#
# Both patterns below read the two shapes the Direct3D11/ANGLE stack emits,
# because every arm in the corpus today declares `windows` and every
# `gpu_claimed` value in it is ANGLE-wrapped. The ordinary macOS and Linux
# spellings are NOT parsed and fall to `None`:
#
#     AMD Radeon Pro 5500M OpenGL Engine     -> None
#     NVIDIA GeForce GTX 1650/PCIe/SSE2      -> None
#     Mesa DRI Intel(R) HD Graphics 620      -> None
#     Apple GPU                              -> None
#
# That fires on nothing today — all four of the product's own pools parse
# cleanly and no corpus value takes these shapes — but it is a STATED
# BOUNDARY rather than a property to be rediscovered. Whoever adds a
# non-Windows arm to the matrix meets it here: those rows will land in
# COVERAGE_HOLE, which is honest (nothing established) but is not detection.
# Widening the parse is the work that arm needs, and it belongs with the arm.
#
# "ANGLE (AMD, AMD Radeon(TM) Graphics (0x00001638) Direct3D11 …)" -> "amd"
# The IHV is the first field inside ANGLE(...), terminated by a comma (the
# desktop form) or by the closing paren (the bare form).
_ANGLE = re.compile(r"angle\s*\(\s*([^,()]+?)\s*[,)]")

# "Google Inc. (AMD)" -> "amd". The ANGLE-wrapper vendor convention: the
# OUTER name is always the wrapper (Google), the parenthesised one is the IHV.
_WRAPPED = re.compile(r"^[a-z0-9.\s]*\(\s*([^)]+?)\s*\)\s*$")

# --- the host showing through ----------------------------------------------

# Names that belong to NO graphics vendor: they are what a browser reports
# when it is drawing in SOFTWARE because there is no GPU to draw with. On a
# headless host — which is exactly what `environment: "linux-x86_64 (agent
# sandbox)"` is — a spoof that stops covering one read path does not reveal a
# different plausible card. It reveals one of these.
#
# MATCHED AGAINST THE WHOLE VALUE, NOT AGAINST THE PARSED IHV, and that is
# load-bearing rather than incidental: SwiftShader's ANGLE string puts
# `Google` in the IHV slot — `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device
# (Subzero) (0x0000C0DE)), SwiftShader driver)` — and llvmpipe puts
# `Mesa/X.org` there. The marker never appears in the field the IHV parse
# reads. Parsing first and testing the IHV afterwards is *precisely* how this
# case came to be missed: `google` was mapped to `None`, the row was dropped,
# and the vector routed to a coverage hole.
#
# Bare "mesa" is deliberately NOT a marker. `Mesa DRI Intel(R) HD Graphics
# 620` is a real Intel adapter on a real Linux machine; Mesa is the driver
# stack, not the rasteriser. Only the software devices Mesa can fall back to
# (llvmpipe, softpipe, lavapipe) are named.
_SOFTWARE_RASTERISERS = (
    "swiftshader",
    "llvmpipe",
    "softpipe",
    "lavapipe",
    "swangle",
    "microsoft basic render",
    "basic render driver",
)

# The identity token :func:`identity` returns for such a value. A REAL token
# rather than ``None``, so the row takes part in the comparison instead of
# being dropped out of it — a leaked host is something we LOOKED AT and SAW,
# not something we failed to read.
#
# Chosen to be un-collidable with any IHV: no vendor is called this, so it can
# never be mistaken for one, and `nvidia` beside it reads as the
# self-contradiction it is.
SOFTWARE_RASTERISER = "software-rasteriser"

# pixelscan renders a hedged renderer string: "ANGLE (AMD, Radeon R9 200
# Series Direct3D11 vs_5_0 ps_5_0), or similar". The hedge is pixelscan's
# prose, not a difference in the adapter, so it is stripped before comparison.
# This is the ticket's "two spellings of the same adapter" case and it is the
# ONLY such variance present in the corpus.
_HEDGE = re.compile(r",?\s*or\s+similar\s*$")

# IHV names that mean the same vendor spelled differently across checkers.
_IHV_SYNONYMS = {
    "nvidia corporation": "nvidia",
    "advanced micro devices": "amd",
    "amd inc": "amd",
    "intel inc": "intel",
    "intel corporation": "intel",
    "apple inc": "apple",
    "arm ltd": "arm",
}


def _normalise(value: Any) -> str:
    """Lower-case, collapse whitespace and strip pixelscan's hedge."""
    text = re.sub(r"\s+", " ", str(value)).strip().lower()
    return _HEDGE.sub("", text).strip()


def identity(value: Any) -> "str | None":
    """The IHV named by a ``gpu_claimed`` value, or ``None`` if it names none.

    The comparison key. Returns a vendor token (``"amd"``, ``"nvidia"``) taken
    from the value's STRUCTURE rather than by scanning for brand words: the
    literal string ``Google`` appears in every ANGLE vendor value as the
    wrapper (``Google Inc. (AMD)``), so a brand-word scan would report
    ``google`` for every row and collapse the comparison into a single bucket
    that can never disagree. The parse reads the position the IHV actually
    occupies in each of the two shapes.

    ``None`` means *this value names no hardware* — a placeholder, an empty
    string, or a shape not recognised. It is deliberately NOT folded into a
    vendor: an unidentifiable value must reach :data:`COVERAGE_HOLE` and not
    quietly join a consensus it never supported.

    :data:`SOFTWARE_RASTERISER` is a THIRD answer and is not either of those.
    It means *this value names no hardware because there was none* — the host
    drew in software. It is returned as a real token rather than as ``None``
    so the row PARTICIPATES in the comparison instead of being dropped from
    it, which is the difference between reporting a host leak and printing
    "FINDINGS — none" over it.
    """
    if value is None:
        return None
    text = _normalise(value)
    if text in _PLACEHOLDERS:
        return None

    # BEFORE the IHV parse, and against the WHOLE value. See
    # `_SOFTWARE_RASTERISERS`: the marker lives outside the field the IHV
    # parse reads, so testing the parsed IHV instead would miss every one of
    # these — which is exactly the defect this ordering repairs.
    if any(marker in text for marker in _SOFTWARE_RASTERISERS):
        return SOFTWARE_RASTERISER

    found = _ANGLE.search(text)
    ihv = found.group(1) if found else None
    if ihv is None:
        wrapped = _WRAPPED.match(text)
        if wrapped:
            ihv = wrapped.group(1)
    if ihv is None:
        return None

    ihv = ihv.strip().strip(".").strip()
    # "Google Inc. (Google)" is the ANGLE software-rasteriser form: the IHV
    # slot holds the wrapper's own name, which names no hardware vendor.
    if ihv in _PLACEHOLDERS or ihv == "google":
        return None
    return _IHV_SYNONYMS.get(ihv, ihv)


def adapter_text(value: Any) -> "str | None":
    """The normalised full ``ANGLE (…)`` adapter string, or ``None``.

    The FINER comparison term, and it is deliberately partial. It returns a
    value only for a full adapter string, so it is compared between two
    renderer readings and never between a renderer and a vendor — those are
    different KINDS of value and would always differ, which is one of the two
    causes of raw equality's 11 false alarms over this corpus.

    Its purpose is to catch a same-brand, different-model disagreement that
    :func:`identity` alone would pass: two AMD cards that are not the same
    card still make the record self-contradictory.
    """
    if value is None:
        return None
    text = _normalise(value)
    if text in _PLACEHOLDERS:
        return None
    if not text.startswith("angle"):
        return None
    return text


def _rows_for_vector(record: "dict", vector: str) -> "list[dict]":
    return [
        row
        for row in record.get("readings", [])
        if isinstance(row, dict) and row.get("vector") == vector
    ]


def vectors_in(record: "dict") -> "list[str]":
    """Every vector tag carried by the record's rows, sorted."""
    seen = {
        str(row.get("vector"))
        for row in record.get("readings", [])
        if isinstance(row, dict) and row.get("vector")
    }
    return sorted(seen)


def check_vector(record: "dict", vector: str) -> dict:
    """Judge ONE vector within ONE record.

    Returns an entry carrying the ``classification``, the ``identities`` found
    (each with the rows that produced it), and the rows that could not be
    identified — so a reader is never handed a bare verdict without the values
    it was taken from.

    The order of the checks is load-bearing. Comparability is settled FIRST,
    from the declaration, because a vector that may not be compared cannot
    produce either a contradiction or a pass and asking about its values would
    be meaningless.

    Then :data:`HOST_LEAK`, ABOVE the contradiction — the ticket ranks a leak
    of the true adapter as materially worse than an inconsistent spoof, and a
    vector that is both must report as the worse one. It is also the only
    finding here that a SINGLE row can constitute: a contradiction needs a
    pair, so a record in which every checker leaks in unison would otherwise
    hold no disagreement and fall through to a pass-shaped verdict. That is
    the worst case wearing the best case's costume, and putting this check
    first is what prevents it.

    Then a contradiction, if there is one — a record that contradicts itself
    is reported as such even if OTHER rows in the same vector are missing,
    since the holes do not make the disagreement any less real. Only then the
    coverage hole, and a pass last: :data:`CONSISTENT` is the one verdict that
    asserts something positive, so it is reached only when every other
    explanation has been excluded.
    """
    reason = INCOMPARABLE_VECTORS.get(vector)
    if reason is not None or vector not in COMPARABLE_VECTORS:
        return {
            "vector": vector,
            "classification": NOT_COMPARABLE,
            "reason": reason or UNKNOWN_VECTOR_REASON,
            "rows": len(_rows_for_vector(record, vector)),
            "identities": {},
            "unidentified": [],
        }

    rows = _rows_for_vector(record, vector)
    identities: "dict[str, list[str]]" = {}
    adapters: "dict[str, list[str]]" = {}
    unidentified: "list[dict]" = []

    for row in rows:
        where = f"{row.get('checker')}/{row.get('item')}"
        found = identity(row.get("value"))
        if found is None:
            unidentified.append(
                {
                    "row": where,
                    "value": row.get("value"),
                    # Carried so a reader can tell a row that was never read
                    # from one that was read and said nothing — a "-" in a
                    # `read` row is a different failure from an absent row.
                    "state": row.get("state"),
                }
            )
            continue
        identities.setdefault(found, []).append(where)
        adapter = adapter_text(row.get("value"))
        if adapter is not None:
            adapters.setdefault(adapter, []).append(where)

    entry = {
        "vector": vector,
        "rows": len(rows),
        "identities": identities,
        "unidentified": unidentified,
        "reason": "",
    }

    leaked = identities.get(SOFTWARE_RASTERISER, [])
    if leaked:
        # Checked BEFORE the contradiction, and satisfied by ONE row. See the
        # docstring: this is the finding the ticket ranks worse, and it is the
        # only one a single row can constitute.
        others = {n: w for n, w in identities.items() if n != SOFTWARE_RASTERISER}
        entry["classification"] = HOST_LEAK
        detail = (
            f"{len(leaked)} row(s) rendered in SOFTWARE, naming no real "
            f"adapter ({', '.join(sorted(leaked))})"
        )
        if others:
            entry["reason"] = (
                detail
                + ", while "
                + "; ".join(
                    f"{name} ({', '.join(sorted(where))})"
                    for name, where in sorted(others.items())
                )
                + " claimed real hardware — BOTH a self-contradiction and a "
                "leak of the machine underneath"
            )
        else:
            entry["reason"] = (
                detail
                + ", and NO row claimed real hardware — every checker saw "
                "the host. The rows agree, which is what makes this the "
                "worst case and not the cleanest"
            )
        return entry

    if len(identities) > 1:
        entry["classification"] = CONTRADICTION
        entry["reason"] = (
            f"{len(identities)} different hardware identities in one record: "
            + "; ".join(
                f"{name} ({', '.join(sorted(where))})"
                for name, where in sorted(identities.items())
            )
        )
        return entry

    if len(adapters) > 1:
        # Same IHV, different adapter. A record naming two different cards
        # from one vendor is no less self-contradictory than one naming two
        # vendors, and the brand-level term alone would pass it.
        entry["classification"] = CONTRADICTION
        entry["reason"] = (
            f"one vendor but {len(adapters)} different adapters in one "
            "record: "
            + "; ".join(
                f"{text} ({', '.join(sorted(where))})"
                for text, where in sorted(adapters.items())
            )
        )
        return entry

    if unidentified or not identities:
        entry["classification"] = COVERAGE_HOLE
        if not identities:
            entry["reason"] = (
                f"no row in this vector named any hardware ({len(rows)} "
                "row(s), none identifiable), so NOTHING WAS ESTABLISHED — "
                "this is not agreement"
            )
        else:
            entry["reason"] = (
                f"{len(unidentified)} of {len(rows)} row(s) named no "
                "hardware, so the agreement among the rest is partial "
                "evidence, not a clean run"
            )
        return entry

    entry["classification"] = CONSISTENT
    entry["reason"] = (
        f"all {len(rows)} row(s) name the same hardware "
        f"({next(iter(identities))})"
    )
    return entry


def consistency_pass(record: "dict") -> "list[dict]":
    """Judge every vector in ONE record. The entry point.

    One record, deliberately: a self-contradiction is a property of a single
    record, and it needs no set and no second reading to establish. That is
    what makes this check usable on the committed corpus as-is.

    Findings first — the report is read top-down and an alarm must never sit
    below the entries that are working as intended.
    """
    require_record(record)
    entries = [check_vector(record, vector) for vector in vectors_in(record)]
    order = {
        HOST_LEAK: 0,
        CONTRADICTION: 1,
        COVERAGE_HOLE: 2,
        NOT_COMPARABLE: 3,
        CONSISTENT: 4,
    }
    entries.sort(key=lambda e: (order.get(e["classification"], 9), e["vector"]))
    return entries


def host_leaks(entries: "Iterable[dict]") -> "list[dict]":
    """The entries where the machine underneath showed through.

    Kept as its own predicate rather than folded into :func:`contradictions`
    because it is a DIFFERENT fact with a different fix: an inconsistent spoof
    means two authors disagree, a leak means no spoof covered the read at all.
    Both are findings; only one of them is the one the ticket ranks worse.
    """
    return [e for e in entries if e.get("classification") == HOST_LEAK]


def findings(entries: "Iterable[dict]") -> "list[dict]":
    """Every entry that is a FINDING ABOUT THE PRODUCT — leaks and contradictions.

    The predicate the exit code is taken from. Never the length of the whole
    entry list: that number mixes populations and is the wrong number to
    report.
    """
    return host_leaks(entries) + contradictions(entries)


def contradictions(entries: "Iterable[dict]") -> "list[dict]":
    """The entries that are FINDINGS — vectors that contradict themselves.

    Strictly the two-identities case. A vector carrying a host leak is
    reported by :func:`host_leaks` and is deliberately NOT also counted here,
    so the two populations sum without double-counting.
    """
    return [e for e in entries if e.get("classification") == CONTRADICTION]


def coverage_holes(entries: "Iterable[dict]") -> "list[dict]":
    """The entries where nothing was established, in either direction."""
    return [e for e in entries if e.get("classification") == COVERAGE_HOLE]


def not_comparable(entries: "Iterable[dict]") -> "list[dict]":
    """The entries whose vector may not be compared, with their reasons."""
    return [e for e in entries if e.get("classification") == NOT_COMPARABLE]


def consistent(entries: "Iterable[dict]") -> "list[dict]":
    """The entries that genuinely agree."""
    return [e for e in entries if e.get("classification") == CONSISTENT]


def format_consistency(entries: "list[dict]", *, source: str = "") -> str:
    """Render the pass for a human, with the populations kept APART.

    The sections are the point, for the same reason they are in
    ``matrix_silence.format_silence``: a single undifferentiated list is the
    report this module exists to not produce. A contradiction, a hole and a
    vector that may not be compared are three different facts with three
    different fixes, and collapsing them trains a reader to skim.
    """
    where = f" of {source}" if source else ""
    lines = [f"SAME-VECTOR CONSISTENCY{where}", ""]

    leaks = host_leaks(entries)
    found = contradictions(entries)
    holes = coverage_holes(entries)

    if leaks:
        lines.append(
            f"FINDINGS — {len(leaks)} vector(s) LEAKED THE HOST MACHINE in "
            "this one record:"
        )
        for entry in leaks:
            lines.append(f"  {entry['vector']}   {entry['reason']}")
        lines.append("")
        lines.append(
            "  A software rasteriser is not a graphics card. These rows are "
            "what the machine UNDERNEATH reports when no spoof covered the "
            "read — the true adapter showing through, which is a materially "
            "worse finding than an inconsistent spoof. Note that rows naming "
            "it AGREE with each other: a leak needs no disagreement to be a "
            "finding, so this can never be established by looking for one."
        )

    if found:
        if leaks:
            lines.append("")
        lines.append(
            f"FINDINGS — {len(found)} vector(s) CONTRADICT THEMSELVES in this "
            "one record:"
        )
        for entry in found:
            lines.append(f"  {entry['vector']}   {entry['reason']}")
        lines.append("")
        lines.append(
            "  One profile emitted more than one hardware identity in a "
            "single run. Each row on its own is plausible — which is why "
            "per-row `adverse` scoring passed every one of them — but a real "
            "machine has one GPU, and any observer reading both sees a "
            "contradiction no plausible string repairs."
        )

    if not leaks and not found:
        # "No finding" and "nothing was established" are DIFFERENT STATEMENTS
        # and this headline must never say the first when the second is true.
        # 13 of the 21 readable records in `readings/` route to a coverage
        # hole, and every one of them used to print the unqualified "FINDINGS
        # — none" — a detector reporting cleanliness over records it could not
        # read. That is the failure this whole module exists to not commit.
        if holes:
            lines.append(
                f"FINDINGS — none FOUND, but {len(holes)} vector(s) COULD NOT "
                "BE READ. This is NOT a clean record: see COVERAGE HOLES "
                "below. Nothing was established either way."
            )
        else:
            lines.append(
                "FINDINGS — none. Every comparable vector was read and names "
                "one identity."
            )

    lines.append("")
    if holes:
        lines.append(
            f"COVERAGE HOLES (nothing established — NOT a pass) — "
            f"{len(holes)} vector(s):"
        )
        for entry in holes:
            lines.append(f"  {entry['vector']}   {entry['reason']}")
            for miss in entry["unidentified"]:
                lines.append(
                    f"      {miss['row']}   state={miss['state']}   "
                    f"value={miss['value']!r}"
                )
        lines.append(
            "  A null is not an agreement. These rows did not disagree "
            "because they did not speak; scoring that as consistent is 'we "
            "failed to look' wearing the costume of 'they agreed'."
        )
    else:
        lines.append("COVERAGE HOLES — none.")

    skipped = not_comparable(entries)
    if skipped:
        lines.append("")
        lines.append(
            f"NOT COMPARED (by declaration) — {len(skipped)} vector(s):"
        )
        for entry in skipped:
            lines.append(
                f"  {entry['vector']}   ({entry['rows']} row(s))   "
                f"{entry['reason']}"
            )

    agreed = consistent(entries)
    if agreed:
        lines.append("")
        lines.append(f"CONSISTENT — {len(agreed)} vector(s):")
        for entry in agreed:
            lines.append(f"  {entry['vector']}   {entry['reason']}")

    return "\n".join(lines)


__all__ = [
    "COMPARABLE_VECTORS",
    "CONSISTENT",
    "CONTRADICTION",
    "COVERAGE_HOLE",
    "HOST_LEAK",
    "INCOMPARABLE_VECTORS",
    "NOT_COMPARABLE",
    "NotARecord",
    "RecordUnreadable",
    "SOFTWARE_RASTERISER",
    "UNKNOWN_VECTOR_REASON",
    "adapter_text",
    "check_vector",
    "consistency_pass",
    "consistent",
    "contradictions",
    "coverage_holes",
    "findings",
    "format_consistency",
    "host_leaks",
    "identity",
    "not_comparable",
    "vectors_in",
]
