"""The comparator: two snapshots in, an ordered list of differences out.

Deliberately dumb and total. It reports what changed, it does not judge whether
a change is acceptable — that judgement belongs to the gate slices that will
consume this artifact, not here.

A probe present in one snapshot and absent from the other is REPORTED, never
skipped. "The probe vanished" and "the probe agreed" must not look the same:
the second is evidence, the first is a hole where evidence should be.

The same rule governs a reading that FAILED. ``runner``/``snapshot`` record an
unobtainable reading as ``{"error": ...}`` — never omitted, never coerced to a
value — because an unobtainable reading is inconclusive, and inconclusive is
never a pass. Comparing entries verbatim would throw that away at the last
step: ``{"error": "X"} == {"error": "X"}`` is not two probes agreeing, it is
the same probe failing twice.

So the comparator asks one question of every entry it reports: **was a reading
actually OBTAINED on either side?** An error is not a reading, and neither is
an absence.

* Both sides present and NEITHER obtained — status INCONCLUSIVE, reported even
  when the two sides are byte-identical. A comparison nobody had the evidence
  to make is not agreement.
* Both sides present and at least one obtained — the ordinary comparison. Equal
  readings agree and are skipped; unequal ones are CHANGED. A vector that read
  fine before and throws now IS a difference — one side was read, and the fact
  that it stopped being readable is the loudest continuity signal this
  subsystem can produce, so it must not be demoted to "look again".
* Present on one side only — ADDED/REMOVED, because the inventory change is
  real information worth naming. But if no reading was ever obtained for it,
  it is still COUNTED as inconclusive (see ``inconclusive_count``): the
  inventory moved, no reading did, and calling it an observed difference would
  assert evidence nobody gathered.

``inconclusive_count`` therefore keys off the readings an entry carries, not
off its status label — so the count is the same whichever branch produced the
entry.
"""

from __future__ import annotations

from typing import Any

# Sentinel recorded in place of a reading that does not exist on that side.
ABSENT = {"absent": True}

CHANGED = "changed"
ADDED = "added"
REMOVED = "removed"

# Reported when a reading on either side carries an error: the comparison could
# not be made because the evidence was never obtained. Distinct from CHANGED on
# purpose — "the identity moved" and "we failed to look" are different facts,
# and only one of them is about the identity.
INCONCLUSIVE = "inconclusive"

# Snapshot header fields whose disagreement changes how the probe diff should
# be read (a chromium snapshot vs a firefox one is not a regression, it is a
# different question). Reported as entries with realm "__meta__".
_META_FIELDS = ("schema_version", "engine", "profile", "app_version", "engine_build")

META_REALM = "__meta__"


def _probes(snapshot: dict) -> dict:
    probes = snapshot.get("probes")
    return probes if isinstance(probes, dict) else {}


def _realm(snapshot: dict, realm: str) -> dict:
    entries = _probes(snapshot).get(realm)
    return entries if isinstance(entries, dict) else {}


def _unread(side: Any) -> bool:
    """True when this side of a comparison carries no reading that was OBTAINED.

    Three ways evidence can be missing, and this one predicate covers all of
    them, because downstream nothing cares which one happened:

    * ``{"error": ...}`` — ``runner``/``snapshot`` record every failure this
      way (a throwing probe, a worker harness that never answered, a probe the
      runner dropped), never omitted and never coerced to a value.
    * ``ABSENT`` — the probe is not in this snapshot at all.
    * anything else malformed — a probe reading is obtained only if it actually
      carries ``value``. The safe default in this subsystem is to treat what we
      cannot recognise as evidence we do not have.

    Header (``__meta__``) sides are bare scalars rather than reading dicts;
    a present scalar IS the reading, and ``None`` is its absence.
    """
    if isinstance(side, dict):
        return "value" not in side
    return side is None


def _inconclusive(entry: "dict") -> bool:
    """True when NEITHER side of this entry carries an obtained reading.

    Keyed off the readings, not off the status label, so the answer is the same
    whichever branch of the comparator produced the entry. An ADDED probe whose
    only reading errored is an inventory change with no evidence behind it: it
    is still worth REPORTING as added, but it must not be counted as a
    difference anyone observed.
    """
    if not isinstance(entry, dict):
        return True
    return _unread(entry.get("expected")) and _unread(entry.get("observed"))


def inconclusive_count(entries: "list[dict]") -> int:
    """How many of these entries rest on readings nobody obtained."""
    return sum(1 for e in entries if _inconclusive(e))


def diff_snapshots(
    expected: dict, observed: dict, *, include_meta: bool = False
) -> list[dict]:
    """Compare two snapshots probe by probe.

    Returns entries ordered by ``(realm, probe_id)``, each shaped::

        {"probe_id": ..., "realm": ...,
         "status": "changed"|"added"|"removed"|"inconclusive",
         "expected": <entry or ABSENT>, "observed": <entry or ABSENT>}

    ``expected`` is the baseline (the "before"), ``observed`` the new reading.
    An empty list means the two snapshots agree on every probe in every realm
    either of them recorded — and, since a reading nobody obtained is reported
    rather than skipped, it also means every one of those probes was READ.

    ``inconclusive`` is reported when NEITHER side carries an obtained reading.
    An ``added``/``removed`` entry can also rest on no obtained reading; it
    keeps its inventory status, and ``inconclusive_count`` counts it.

    ``include_meta`` additionally reports header disagreements (engine,
    engine_build, profile, schema_version, app_version) under realm
    ``"__meta__"``. Off by default so the common "did this profile survive a
    restart?" question is answered by probe evidence alone — an engine BUILD
    change is provenance, not a probe difference.
    """
    out: list[dict] = []

    if include_meta:
        for field in _META_FIELDS:
            a, b = expected.get(field), observed.get(field)
            if a != b:
                out.append(
                    {
                        "probe_id": field,
                        "realm": META_REALM,
                        "status": CHANGED,
                        "expected": a,
                        "observed": b,
                    }
                )

    realms = sorted(set(_probes(expected)) | set(_probes(observed)))
    for realm in realms:
        a_entries = _realm(expected, realm)
        b_entries = _realm(observed, realm)
        for probe_id in sorted(set(a_entries) | set(b_entries)):
            in_a = probe_id in a_entries
            in_b = probe_id in b_entries
            if in_a and in_b:
                # Checked BEFORE equality: two identical errors are the same
                # probe failing twice, not two probes agreeing.
                if _unread(a_entries[probe_id]) and _unread(b_entries[probe_id]):
                    status = INCONCLUSIVE
                elif a_entries[probe_id] == b_entries[probe_id]:
                    continue
                else:
                    # One side WAS read. A vector that read fine before and
                    # throws now is a difference, not a request to look again.
                    status = CHANGED
            else:
                status = ADDED if in_b else REMOVED
            out.append(
                {
                    "probe_id": probe_id,
                    "realm": realm,
                    "status": status,
                    "expected": a_entries[probe_id] if in_a else dict(ABSENT),
                    "observed": b_entries[probe_id] if in_b else dict(ABSENT),
                }
            )
    return out


def diff_realms(snapshot: dict, left: str, right: str) -> list[dict]:
    """Compare two realms WITHIN one snapshot.

    A vector persona spoofs should read the same in the window realm and in a
    Web Worker; a disagreement is the historically load-bearing defect class
    (a spoof that never reached the worker). This reports it. It does not fix
    it — how a vector *should* be spoofed is authored elsewhere.

    Only probes that declare both realms are compared: a window-only probe
    being absent from the worker realm is the inventory working as designed,
    not a finding.

    Entries carry the same statuses ``diff_snapshots`` uses: ``changed`` when
    the two realms disagree, ``inconclusive`` when NEITHER realm yielded an
    obtained reading. A vector read in one realm and unreadable in the other is
    ``changed`` — that asymmetry is the defect class this function exists to
    catch, not a request to look again.
    """
    a_entries = _realm(snapshot, left)
    b_entries = _realm(snapshot, right)
    out: list[dict] = []
    for probe_id in sorted(set(a_entries) & set(b_entries)):
        # Same rule as diff_snapshots: a vector nobody could read in either
        # realm is not the two realms agreeing about it. But a vector read in
        # ONE realm and unreadable in the other IS a disagreement between the
        # realms — that is the load-bearing defect class, not a retry request.
        if _unread(a_entries[probe_id]) and _unread(b_entries[probe_id]):
            status = INCONCLUSIVE
        elif a_entries[probe_id] == b_entries[probe_id]:
            continue
        else:
            status = CHANGED
        out.append(
            {
                "probe_id": probe_id,
                "realm": f"{left}!={right}",
                "status": status,
                "expected": a_entries[probe_id],
                "observed": b_entries[probe_id],
            }
        )
    return out


def format_diff(entries: "list[dict]") -> str:
    """Render a diff as plain text for an operator. Empty input renders as the
    explicit statement that the two snapshots agree, never as blank output.

    A trailing summary names how many entries are INCONCLUSIVE, so an operator
    reads "4 inconclusive" rather than being left to notice it in a long list.
    An empty diff still renders exactly "no differences" — with the comparator
    fixed, that string now means every probe was READ and agreed, which is what
    a reader always took it to mean.
    """
    if not entries:
        return "no differences"
    lines: list[str] = []
    for e in entries:
        lines.append(f"{e['realm']}/{e['probe_id']}: {e['status']}")
        lines.append(f"  expected: {_render(e['expected'])}")
        lines.append(f"  observed: {_render(e['observed'])}")

    unread = inconclusive_count(entries)
    differing = len(entries) - unread
    summary = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}: "
    summary += f"{differing} differing, {unread} inconclusive"
    if unread:
        summary += " (readings that were never obtained — not agreement)"
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)


def _render(entry: Any, *, limit: int = 400) -> str:
    import json as _json

    try:
        text = _json.dumps(entry, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(entry)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "ABSENT",
    "ADDED",
    "CHANGED",
    "INCONCLUSIVE",
    "META_REALM",
    "REMOVED",
    "diff_realms",
    "diff_snapshots",
    "format_diff",
    "inconclusive_count",
]
