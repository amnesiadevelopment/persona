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

# Reported by ``compare_profiles`` when two DIFFERENT profiles produce the SAME
# reading on a vector that is seed-derived and therefore must vary. This is the
# inverted-polarity status: on the cross-profile axis agreement is the defect —
# two identities a site can link on that vector — so this is a FINDING, not a
# pass. Deliberately not named CHANGED/AGREED: it answers a different question
# than the two continuity comparators, and sharing their vocabulary would
# invite reading an agreement as a pass.
COLLIDING = "colliding"

# Reported when NEITHER side of the comparison carries an obtained reading —
# both errored, or both absent. The comparison could not be made because the
# evidence to make it was never gathered.
#
# Note which side of the line the ASYMMETRIC case falls on: a reading obtained
# on one side and a failure on the other is CHANGED, not inconclusive. One side
# WAS read, and a vector that read fine before and throws now is the loudest
# continuity signal this subsystem can produce — demoting it to "look again"
# is the bug, not the fix.
#
# Distinct from CHANGED on purpose: "the identity moved" and "we failed to
# look" are different facts, and only one of them is about the identity.
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
    """How many of these entries rest on readings nobody obtained.

    An entry counts when NEITHER side carries an obtained reading, **or** when
    the comparator that produced it already labelled it ``INCONCLUSIVE``. The
    second clause is what lets the cross-profile comparator share this function
    and ``cli._exit_code`` verbatim: it draws the "no evidence" line in a
    different place (see :func:`compare_profiles`), and it says so in the
    status rather than being second-guessed here.

    It changes nothing for :func:`diff_snapshots` / :func:`diff_realms`, where
    ``INCONCLUSIVE`` is only ever set when both sides are unread — so the
    clause is already true of every entry it labels, and the count is
    unchanged. An ``added``/``removed`` entry resting on no obtained reading is
    still counted, exactly as before, via the first clause.
    """
    return sum(1 for e in entries if _inconclusive(e) or _status(e) == INCONCLUSIVE)


def _status(entry: Any) -> Any:
    return entry.get("status") if isinstance(entry, dict) else None


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


def compare_profiles(a: dict, b: dict) -> list[dict]:
    """Compare two snapshots taken from two DIFFERENT profiles.

    The third comparison mode, and the only one whose polarity is inverted.
    ``diff_snapshots`` and ``diff_realms`` both ask "did these agree?" and treat
    agreement as the pass. That is right for continuity (one profile, two times)
    and for realm parity (one snapshot, two realms), and exactly WRONG for
    mutual unlinkability: two DIFFERENT profiles agreeing on a seed-derived
    vector is two identities a site can link, which is the defect. So here
    **AGREEMENT is the finding** and difference is the silent pass.

    Only probes classified :data:`probes.INDEPENDENT` are compared — the
    vectors that are seed-derived and high-entropy, so that two distinct
    profiles are REQUIRED to differ on them. Everything else is skipped, and
    the skipping is the point rather than an omission:

    * operator-chosen configuration (``os_type``, ``resolution``, ``engine``,
      locale) is SUPPOSED to match across profiles — two Windows profiles both
      reporting "Win32" is the operator's choice, not a leak;
    * a :data:`probes.POOLED` vector is drawn from a small fixed set, so a
      collision is ordinary pigeonhole. ``webgl.unmasked`` is the sharp case:
      for an iOS profile the GPU pair is a single compile-time constant that
      EVERY iOS device reports, and a seed-varied one would itself be the tell
      (``gpu_ext.py:581-589``). Two iOS profiles must agree there;
    * ``masking.*`` and ``realm.*`` observe the masking MECHANISM, not the
      identity it produces, and should agree across profiles.

    Returns entries ordered by ``(realm, probe_id)``, shaped like the other two
    comparators so ``format_diff``/``inconclusive_count`` work unchanged::

        {"probe_id": ..., "realm": ...,
         "status": "colliding"|"inconclusive",
         "expected": <a's entry>, "observed": <b's entry>,
         "value": <the shared reading>}   # colliding entries only

    ``value`` carries the reading the two profiles share, so an operator reads
    WHICH value links them without decoding two entries.

    An empty list is the pass: every compared vector was READ on both sides and
    the two profiles differ on all of them.

    **Where this draws the "no evidence" line, and why it is not where
    ``diff_snapshots`` draws it.** That comparator treats an ASYMMETRIC reading
    — obtained on one side, failed on the other — as CHANGED, because a vector
    that read fine before and throws now is the loudest continuity signal there
    is. Inverting the question inverts that too: holding profile A's digest and
    NOT holding profile B's is not evidence the two differ, it is one reading
    and one hole. Claiming distinctness from it would manufacture an
    unlinkability pass out of a reading nobody obtained — the PS-29 defect
    reintroduced on this axis. So a vector unread on EITHER side is
    :data:`INCONCLUSIVE` here (``_unread(a) or _unread(b)``), where continuity
    needs BOTH sides unread. Same rule — an unobtained reading is never a pass
    — applied to a different question.

    Two probes that both errored are likewise INCONCLUSIVE and never
    "differing": that is the same failure twice, not two distinct identities.
    """
    from .probes import must_differ_ids

    targets = must_differ_ids()
    out: list[dict] = []

    realms = sorted(set(_probes(a)) | set(_probes(b)))
    for realm in realms:
        a_entries = _realm(a, realm)
        b_entries = _realm(b, realm)
        for probe_id in sorted(set(a_entries) & set(b_entries)):
            if probe_id not in targets:
                continue
            a_side, b_side = a_entries[probe_id], b_entries[probe_id]
            # Checked BEFORE equality, exactly as the other two comparators do:
            # a reading nobody obtained can neither agree nor differ. Note the
            # OR — see the docstring: one side read and one side missing is not
            # evidence of distinctness on THIS axis.
            if _unread(a_side) or _unread(b_side):
                status = INCONCLUSIVE
            elif a_side == b_side:
                status = COLLIDING
            else:
                # The pass: both READ, and they differ. Silent, like two
                # agreeing snapshots are silent in diff_snapshots.
                continue
            entry = {
                "probe_id": probe_id,
                "realm": realm,
                "status": status,
                "expected": a_side,
                "observed": b_side,
            }
            if status == COLLIDING:
                entry["value"] = a_side.get("value") if isinstance(a_side, dict) else a_side
            out.append(entry)
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


def format_comparison(entries: "list[dict]") -> str:
    """Render a cross-profile comparison for an operator.

    Deliberately NOT ``format_diff``. That renderer's vocabulary is the
    continuity axis' — an empty result is "no differences" and entries are
    counted as "differing" — and every one of those words means the opposite
    here. An empty cross-profile result is the PASS ("the two profiles differ
    on every vector compared"), and a reported entry is a COLLISION. Rendering
    a leak through a renderer that calls it "1 differing" would tell an
    operator the good news in the exact words that describe the bad news.

    Empty input renders as the explicit pass statement, never as blank output.
    """
    if not entries:
        return "no collisions: the profiles differ on every vector compared"
    lines: list[str] = []
    for e in entries:
        lines.append(f"{e['realm']}/{e['probe_id']}: {e['status']}")
        if e["status"] == COLLIDING:
            lines.append(f"  both profiles: {_render(e.get('value'))}")
        else:
            lines.append(f"  profile a: {_render(e['expected'])}")
            lines.append(f"  profile b: {_render(e['observed'])}")

    unread = inconclusive_count(entries)
    colliding = len(entries) - unread
    summary = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}: "
    summary += f"{colliding} colliding, {unread} inconclusive"
    if colliding:
        summary += " — the profiles are LINKABLE on the colliding vector(s)"
    if unread:
        summary += " (readings that were never obtained — not distinctness)"
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
    "COLLIDING",
    "INCONCLUSIVE",
    "META_REALM",
    "REMOVED",
    "compare_profiles",
    "diff_realms",
    "diff_snapshots",
    "format_comparison",
    "format_diff",
    "inconclusive_count",
]
