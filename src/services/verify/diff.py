"""The comparator: two snapshots in, an ordered list of differences out.

Deliberately dumb and total. It reports what changed, it does not judge whether
a change is acceptable — that judgement belongs to the gate slices that will
consume this artifact, not here.

A probe present in one snapshot and absent from the other is REPORTED, never
skipped. "The probe vanished" and "the probe agreed" must not look the same:
the second is evidence, the first is a hole where evidence should be.
"""

from __future__ import annotations

from typing import Any

# Sentinel recorded in place of a reading that does not exist on that side.
ABSENT = {"absent": True}

CHANGED = "changed"
ADDED = "added"
REMOVED = "removed"

# Snapshot header fields whose disagreement changes how the probe diff should
# be read (a chromium snapshot vs a firefox one is not a regression, it is a
# different question). Reported as entries with realm "__meta__".
_META_FIELDS = ("schema_version", "engine", "profile", "app_version")

META_REALM = "__meta__"


def _probes(snapshot: dict) -> dict:
    probes = snapshot.get("probes")
    return probes if isinstance(probes, dict) else {}


def _realm(snapshot: dict, realm: str) -> dict:
    entries = _probes(snapshot).get(realm)
    return entries if isinstance(entries, dict) else {}


def diff_snapshots(
    expected: dict, observed: dict, *, include_meta: bool = False
) -> list[dict]:
    """Compare two snapshots probe by probe.

    Returns entries ordered by ``(realm, probe_id)``, each shaped::

        {"probe_id": ..., "realm": ..., "status": "changed"|"added"|"removed",
         "expected": <entry or ABSENT>, "observed": <entry or ABSENT>}

    ``expected`` is the baseline (the "before"), ``observed`` the new reading.
    An empty list means the two snapshots agree on every probe in every realm
    either of them recorded.

    ``include_meta`` additionally reports header disagreements (engine,
    profile, schema_version, app_version) under realm ``"__meta__"``. Off by
    default so the common "did this profile survive a restart?" question is
    answered by probe evidence alone.
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
                if a_entries[probe_id] == b_entries[probe_id]:
                    continue
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
    """
    a_entries = _realm(snapshot, left)
    b_entries = _realm(snapshot, right)
    out: list[dict] = []
    for probe_id in sorted(set(a_entries) & set(b_entries)):
        if a_entries[probe_id] == b_entries[probe_id]:
            continue
        out.append(
            {
                "probe_id": probe_id,
                "realm": f"{left}!={right}",
                "status": CHANGED,
                "expected": a_entries[probe_id],
                "observed": b_entries[probe_id],
            }
        )
    return out


def format_diff(entries: "list[dict]") -> str:
    """Render a diff as plain text for an operator. Empty input renders as the
    explicit statement that the two snapshots agree, never as blank output."""
    if not entries:
        return "no differences"
    lines: list[str] = []
    for e in entries:
        lines.append(f"{e['realm']}/{e['probe_id']}: {e['status']}")
        lines.append(f"  expected: {_render(e['expected'])}")
        lines.append(f"  observed: {_render(e['observed'])}")
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
    "META_REALM",
    "REMOVED",
    "diff_realms",
    "diff_snapshots",
    "format_diff",
]
