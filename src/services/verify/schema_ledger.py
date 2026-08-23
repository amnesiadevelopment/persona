"""What each schema version's header ACTUALLY CONTAINS, recorded rather than remembered.

Why this module exists
----------------------
A record carries a ``schema_version``. That integer is worth exactly as much as
the discipline that maintains it, and on this project that discipline failed
**twice, silently, in two different modules**:

* **PS-69** (``6741df7``) added ``declared_machine`` and
  ``declared_machine_honoured`` to the checker-matrix header and left
  ``matrix.SCHEMA_VERSION`` at ``1``.
* **PS-19** (``b3fe337``) added ``engine_build`` to the snapshot header and left
  ``snapshot.SCHEMA_VERSION`` at ``1``.

Neither was careless — both were reviewed and both shipped. That is the point:
the convention lives in whoever happens to be reading, and a reader who does
not know it exists cannot follow it.

The first one's consequence is the reason this module is not a nicety. PS-67
built the comparator days later and found that, unguarded, two records taken on
different declared machines would report **every fingerprint row as COUPLING** —
the loudest signal the apparatus can raise, fired on the one channel that must
never cry wolf. It was caught by a worker thorough enough to notice the header
had moved under them. Nothing in the tree would have told them.

What is recorded here
---------------------
For each artifact, a literal map from version -> **the exact set of header keys
that version emits**. Two properties make it a mechanism rather than a note:

* It is a **literal**, hand-maintained. It is deliberately NOT derived from the
  writer — a set computed from the writer's own output would agree with the
  writer by construction and could never disagree with it, which is the whole
  job. Adding a header field means editing this map, and editing this map is
  the moment the version question gets asked.
* It is checked by **calling the writer and reading the document it returns**,
  never by inspecting the writer's source text. A test that greps for a key
  name proves the text is present, not that the emitted document carries it;
  see knowledge article PS-11 on exactly that failure.

The check is a TEST, not a runtime raise, and that is deliberate. This failure
mode is a developer editing a writer — it cannot arise from a bad reading, a
dead proxy or a missing engine. Raising inside document assembly would destroy
a run that has already collected its readings, which is the hazard
``snapshot.engine_build`` is explicitly written to avoid.

What this is NOT
----------------
Not a migration framework. There is no upcasting, no downcasting and no
rewriting: two artifacts, one integer each, and a record of what each integer
meant. A record already on disk is READ AS IT IS. :func:`generation_of` reports
what a document's keys match so a consumer can SEE a mislabel; nothing here
silently re-interprets an old reading as a new one.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


class SchemaLedgerViolation(AssertionError):
    """The emitted header does not match the generation it claims to be.

    An ``AssertionError`` because the only way to reach it is a source edit:
    someone changed what a writer emits without saying which generation the
    result is. It is a defect in the tree, not a condition of a run.
    """


def header_keys(
    document: Mapping[str, Any],
    *,
    body_key: str,
    annotations: "Iterable[str] | None" = None,
) -> frozenset:
    """The header key set of ``document`` — every top-level key but the body.

    ``body_key`` is the readings/probes collection, which is data rather than
    header and whose contents are governed by their own row shape. Everything
    else is header, including nested objects like ``exit`` and ``counts``: this
    tracks the KEY SET, so a change INSIDE ``exit`` is not caught here. That
    boundary is stated rather than implied — see the module tests, which pin it.

    ``annotations`` names keys added to a document AFTER the writer returned,
    by a layer that is not the writer — ``baseline.record`` attaches
    ``provenance`` and ``engine_gate`` attaches ``engine_stack``. They are
    excluded because the ledger describes what the WRITER emits: counting them
    would make a snapshot's generation depend on which pipeline happened to
    handle it, so an annotated document would be permanently unrecognisable.
    Their own drift is a separate question this module does not claim to answer.
    """
    skip = {body_key} | set(annotations or ())
    return frozenset(k for k in document if k not in skip)


def generation_of(
    document: Mapping[str, Any],
    *,
    body_key: str,
    generations: Mapping[int, Iterable[str]],
    annotations: "Iterable[str] | None" = None,
) -> "int | None":
    """Which recorded generation this document's HEADER KEYS match, if any.

    Read from the keys the document actually carries, NOT from the
    ``schema_version`` it claims. That is the entire value: a record written
    during a drift window claims one generation and carries another, and this
    is what lets a consumer see the discrepancy instead of trusting the label.

    Returns ``None`` when the keys match no recorded generation — an honest
    "I do not recognise this", never a guess at the nearest one.
    """
    keys = header_keys(document, body_key=body_key, annotations=annotations)
    for version, expected in generations.items():
        if keys == frozenset(expected):
            return version
    return None


def mislabelled(
    document: Mapping[str, Any],
    *,
    body_key: str,
    generations: Mapping[int, Iterable[str]],
    annotations: "Iterable[str] | None" = None,
    version_key: str = "schema_version",
) -> bool:
    """True when the document's keys match a generation OTHER than the one it claims.

    A document whose keys match NO recorded generation is not "mislabelled" —
    it is unrecognised, which :func:`generation_of` reports as ``None``. The two
    are different findings and collapsing them would turn "I have never seen
    this shape" into a confident claim about which shape it really is.
    """
    actual = generation_of(
        document,
        body_key=body_key,
        generations=generations,
        annotations=annotations,
    )
    return actual is not None and actual != document.get(version_key)


def check_emitted_header(
    document: Mapping[str, Any],
    *,
    body_key: str,
    generations: Mapping[int, Iterable[str]],
    current_version: int,
    artifact: str,
    version_symbol: str,
    annotations: "Iterable[str] | None" = None,
) -> None:
    """Refuse a writer whose emitted header is not the generation it claims.

    ``document`` must be the REAL output of the REAL writer — call it, do not
    hand-build the dict. A hand-built dict tests this function against itself.

    Three distinct failures, reported apart because they have different fixes:

    * the claimed version is not in the ledger at all;
    * the ledger's newest entry is not the writer's ``SCHEMA_VERSION``;
    * the emitted keys differ from what the claimed generation records, and the
      message names exactly which keys appeared and which vanished.
    """
    claimed = document.get("schema_version")
    if claimed != current_version:
        raise SchemaLedgerViolation(
            f"the {artifact} writer emitted schema_version {claimed!r} but "
            f"{version_symbol} is {current_version!r}. The header must state "
            "the version the writer actually is."
        )
    if current_version not in generations:
        raise SchemaLedgerViolation(
            f"{version_symbol} is {current_version!r} but the ledger records no "
            f"such generation for the {artifact} (it knows "
            f"{sorted(generations)}). Add the generation, naming the exact "
            "header keys it emits."
        )
    if max(generations) != current_version:
        raise SchemaLedgerViolation(
            f"the ledger's newest {artifact} generation is {max(generations)!r} "
            f"but {version_symbol} is {current_version!r}. The two must agree, "
            "or the ledger stops describing what the writer produces."
        )

    emitted = header_keys(document, body_key=body_key, annotations=annotations)
    recorded = frozenset(generations[current_version])
    if emitted == recorded:
        return

    appeared = sorted(emitted - recorded)
    vanished = sorted(recorded - emitted)
    detail = []
    if appeared:
        detail.append(f"NEW keys not recorded in any generation: {appeared}")
    if vanished:
        detail.append(f"keys the ledger expects but the writer dropped: {vanished}")
    raise SchemaLedgerViolation(
        f"the {artifact} header changed without the schema version being "
        f"addressed. {'; '.join(detail)}. "
        f"This is exactly the drift that shipped twice before (PS-69 on the "
        f"checker record, PS-19 on the snapshot) and cost PS-67 a comparator "
        f"that would have reported every fingerprint row as a COUPLING. "
        f"Decide which it is: a NEW GENERATION (bump {version_symbol} and add "
        f"its key set to the ledger), or a CORRECTION to generation "
        f"{current_version} that no committed record has yet been written "
        f"under (amend that generation's key set in place)."
    )


__all__ = [
    "SchemaLedgerViolation",
    "check_emitted_header",
    "generation_of",
    "header_keys",
    "mislabelled",
]
