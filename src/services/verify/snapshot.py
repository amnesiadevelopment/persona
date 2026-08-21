"""Canonicalisation: turn a probe run into a byte-stable JSON document.

Two records of the same live profile must produce BYTE-IDENTICAL files, or the
differ cannot say anything. That is a property of this module, not of luck:

* keys are sorted, everywhere, at every depth;
* floats are rounded to a pinned precision, and ``-0.0`` is folded to ``0.0``;
* non-finite floats become their canonical string names rather than tripping
  ``json``'s ``NaN`` extension (which is not valid JSON);
* nothing time-varying is recorded. There is deliberately **no timestamp** in a
  snapshot — a clock reading would make every snapshot differ from every other
  snapshot and quietly destroy the one property this file exists to provide.

Every probe in the inventory appears in the document with a ``value`` **or** an
``error``. A probe is never omitted and an error is never coerced into a value:
an unobtainable reading is inconclusive, at probe granularity, and inconclusive
is never a pass.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from typing import Any

from .probes import ALL_REALMS, probes_for_realm

SCHEMA_VERSION = 1

# Decimal places every float is rounded to before it reaches the document.
FLOAT_PRECISION = 6


def app_version() -> str:
    """The running app version, or ``"unknown"`` when it can't be resolved.

    Imported lazily: this package must stay importable in a bare checkout, and
    the updater module pulls a large dependency graph.
    """
    try:
        from ..app_update.updater import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return "unknown"


def engine_build(engine: str) -> str:
    """The engine BUILD the observation was taken under (e.g. ``"firefox-20"``),
    or ``"unknown"`` when it can't be resolved.

    ``engine`` is the FAMILY the snapshot header already carries (``"firefox"``
    / ``"chromium"``); an unrecognised family resolves to ``"unknown"``.

    Imported lazily and guarded broadly, exactly like :func:`app_version`: the
    firefox accessor reaches the engine package transitively, and this package
    must stay importable — and this resolver must stay callable — in a bare
    checkout. It NEVER raises: it runs inside document assembly, where an
    exception would destroy a run that has already collected its readings.

    An accessor answers ``""`` when its engine is not installed. That is mapped
    to ``"unknown"`` so the document never carries an empty string that reads
    like a value.
    """
    try:
        if engine == "firefox":
            from ..engine.firefox import current_version
        elif engine == "chromium":
            from ..engine.updater import current_version
        else:
            return "unknown"

        resolved = current_version()
        return str(resolved) if resolved else "unknown"
    except Exception:
        return "unknown"


def canonicalise(value: Any, *, precision: int = FLOAT_PRECISION) -> Any:
    """Recursively normalise a probe value into stable, JSON-safe data."""
    # bool before int: bool IS an int in Python and must stay a JSON boolean.
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        rounded = round(value, precision)
        # round() can hand back -0.0, which serialises as "-0.0" and would make
        # two otherwise identical snapshots differ.
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {str(k): canonicalise(v, precision=precision) for k, v in sorted(
            ((str(k), v) for k, v in value.items()), key=lambda kv: kv[0]
        )}
    if isinstance(value, (list, tuple)):
        return [canonicalise(v, precision=precision) for v in value]
    # A probe is contracted to return JSON-serialisable data. Anything else is
    # recorded as a stable description rather than crashing the run.
    return f"<unserialisable {type(value).__name__}>"


def _canonical_entry(entry: Any, *, probe_id: str, realm: str) -> dict:
    if isinstance(entry, dict):
        if "error" in entry:
            return {"error": str(entry["error"])}
        if "value" in entry:
            return {"value": canonicalise(entry["value"])}
    return {
        "error": (
            f"MissingResult: no reading was produced for {probe_id!r} "
            f"in realm {realm!r}"
        )
    }


def build_snapshot(
    results: dict,
    *,
    engine: str,
    profile: str,
    realms: "tuple[str, ...] | list[str] | None" = None,
    version: "str | None" = None,
    build: "str | None" = None,
) -> dict:
    """Assemble a canonical snapshot document from a :func:`run_probes` result.

    ``realms`` defaults to the realms present in ``results``. The inventory —
    not ``results`` — decides the key set for each realm, so a runner that
    dropped a probe still produces a document in which that probe is visibly
    unread rather than invisibly absent.
    """
    if realms is None:
        realms = tuple(r for r in ALL_REALMS if r in results)
    probes_out: dict[str, dict] = {}
    for realm in realms:
        realm_results = results.get(realm) or {}
        entries: dict[str, dict] = {}
        for probe in probes_for_realm(realm):
            entries[probe.id] = _canonical_entry(
                realm_results.get(probe.id), probe_id=probe.id, realm=realm
            )
        # A reading for a probe that is no longer in the inventory is kept
        # rather than dropped: silently discarding it would hide a stale runner.
        for extra_id in sorted(set(realm_results) - set(entries)):
            entries[extra_id] = _canonical_entry(
                realm_results[extra_id], probe_id=extra_id, realm=realm
            )
        probes_out[realm] = entries

    return {
        "schema_version": SCHEMA_VERSION,
        "engine": engine,
        "profile": profile,
        "app_version": version if version is not None else app_version(),
        "engine_build": build if build is not None else engine_build(engine),
        "realms": list(realms),
        "probes": probes_out,
    }


def dumps(snapshot: dict) -> str:
    """Serialise a snapshot to its canonical bytes (UTF-8 text, one trailing
    newline). ``allow_nan=False`` is deliberate: a non-finite float reaching
    here means :func:`canonicalise` was bypassed, and that must fail loudly
    rather than emit invalid JSON."""
    return (
        json.dumps(
            snapshot,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def write(snapshot: dict, path: str) -> None:
    """Write the canonical bytes of ``snapshot`` to ``path``, atomically.

    Written to a temp file in the SAME directory and then promoted with
    ``os.replace``, so ``path`` is never observed in a torn state: it is either
    the previous artifact or the complete new one, never half of either.

    This matters because the default ``-o`` for ``record`` is the COMMITTED
    baseline. A plain ``open(path, "w")`` truncates first, so a recording
    interrupted mid-write would leave a partial file at the blessed path — and
    that path is the reference every future ``check`` compares against. The
    guard in ``baseline.check`` means such a file now degrades honestly (exit 2,
    "NOT drift") rather than silently, but not leaving the reference torn in the
    first place is better than reporting it well afterwards.

    Same directory, deliberately: ``os.replace`` is only atomic within a
    filesystem, and a temp dir elsewhere could be on a different one.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps(snapshot))
        os.replace(tmp, path)
    except BaseException:
        # Never leave the temp file behind on a failed write. The original
        # artifact is untouched either way, since the promote is the only thing
        # that can modify it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


__all__ = [
    "FLOAT_PRECISION",
    "SCHEMA_VERSION",
    "app_version",
    "build_snapshot",
    "canonicalise",
    "dumps",
    "engine_build",
    "load",
    "write",
]
