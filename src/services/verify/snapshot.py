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

# The body of the document — data, not header. See `schema_ledger.header_keys`.
SNAPSHOT_BODY_KEY = "probes"

# Keys attached to a snapshot AFTER `build_snapshot` returns, by a layer that
# is not the writer: `baseline.record` attaches `provenance`, `engine_gate`
# attaches `engine_stack` (:data:`engine_gate.STACK_FIELD`). Excluded from the
# generation match because the ledger describes what the WRITER emits —
# counting them would make a document's generation depend on which pipeline
# handled it, so an annotated snapshot would be permanently unrecognisable.
POST_WRITER_ANNOTATIONS = frozenset({"provenance", "engine_stack"})

# What each generation's HEADER ACTUALLY CONTAINS. Same mechanism as
# ``matrix.HEADER_GENERATIONS``, and same rule — **generation N is defined by
# what committed records labelled N actually carry** — but that rule lands
# somewhere different here, and the difference is forced by the artifacts
# rather than chosen:
#
# PS-19 (`b3fe337`) added ``engine_build`` to this header and left
# SCHEMA_VERSION at 1, exactly as PS-69 later did to the checker record. So the
# drift is the same defect, twice, in two modules — which is the strongest
# argument that a remembered convention was never going to hold.
#
# The REMEDY differs because the evidence differs. The one committed snapshot
# (``tests/fixtures/engine-fingerprint-baseline.firefox.json``) says
# ``schema_version: 1`` and ALREADY CARRIES ``engine_build``: there is no record
# on disk with the pre-PS-19 shape. So generation 1 here is the post-PS-19 key
# set, recorded in place, and NOTHING IS RE-TAGGED — every committed snapshot
# already matches it. Bumping would be the harmful move: it would label a shape
# as new when no reader has ever seen the old one, and strand the committed
# baseline at a version no writer produces.
#
# The checker record needed the opposite call for the opposite reason — its
# committed reading predates PS-69's fields — so it gained a generation 2. Two
# artifacts, one rule, two outcomes.
HEADER_GENERATIONS: "dict[int, frozenset[str]]" = {
    1: frozenset({
        "schema_version",
        "engine",
        "profile",
        "app_version",
        "engine_build",
        "realms",
    }),
}

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


def quote_path(path: object) -> str:
    """Render a filesystem path for an error message, readably and LITERALLY.

    Use this instead of ``{path!r}`` anywhere a refusal names a file, because
    ``repr()`` ESCAPES BACKSLASHES and a Windows path is mostly backslashes::

        >>> p = r"C:\\Users\\me\\snap.json"
        >>> f"{p!r}"                      # what repr gives you
        "'C:\\\\Users\\\\me\\\\snap.json'"
        >>> quote_path(p)                 # what the operator actually typed
        "'C:\\Users\\me\\snap.json'"

    That difference is not cosmetic. These messages exist so an operator who
    typo'd a path can SEE which file was bad, and the suite asserts exactly
    that (``assert str(path) in combined``, "it must name the file the operator
    typed"). Under ``repr()`` the doubled form does not contain the typed path,
    so on Windows the message named a file that does not exist while claiming
    to name theirs — the one job it had. On POSIX the bug is invisible: ``/`` is
    not an escape character, so ``repr()`` happens to round-trip.

    The quotes are kept because they are load-bearing: they delimit a path with
    leading or trailing spaces, which is otherwise unreadable in prose. For a
    path with no quote characters this returns BYTE-IDENTICAL output to
    ``repr()``, which is why adopting it changes no POSIX message.
    """
    text = str(path)
    # Mirror repr()'s quote choice so a path containing an apostrophe stays
    # unambiguous, without ever escaping the separators themselves.
    if "'" in text and '"' not in text:
        return f'"{text}"'
    return f"'{text}'"


__all__ = [
    "FLOAT_PRECISION",
    "HEADER_GENERATIONS",
    "POST_WRITER_ANNOTATIONS",
    "SCHEMA_VERSION",
    "SNAPSHOT_BODY_KEY",
    "app_version",
    "build_snapshot",
    "canonicalise",
    "dumps",
    "engine_build",
    "load",
    "quote_path",
    "write",
]
