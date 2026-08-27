#!/usr/bin/env python3
"""Enumerate the "renders a stored summary" defect class in ``derive.py``.

WHY THIS EXISTS
---------------
Rounds 2, 3 and 4 of PS-185 each fixed the ONE instance they were handed, and
each time the next review found another member of the same class. The class is:

    any figure rendered from a summary the sweep wrote about ITSELF, rather
    than recounted from the raw readings.

``result.per_arm.seeds_readable`` is such a summary: it records what the run
*believed* it read, so a truncated sweep carries a full-looking count and every
consumer of that field inherits the blindness. So do
``collision_probability``, ``distinct_identities`` and ``verdict``, and so does
the ``verdicts`` block in the readback records one file over.

THE SEARCH IS BY BEHAVIOUR, NOT BY GREP
---------------------------------------
A grep is shaped by the instances you already know about, so it returns those
instances again — which is exactly how this ticket reached round 5. This script
searches by behaviour instead, along **two axes**, because a stored summary
block can fail in two ways and each axis is blind to the other's members.

**Axis 1 — destroy the readings, keep the summary.** Null an arm's seeds, or
empty a readback leg's vectors, then re-render: *any number that does not move
is not computed from those readings.*

**Axis 2 — poison the summary, keep the readings.** The inverse, and it is the
axis round 5 did not run. Destroying readings cannot detect a figure that never
consulted readings in the first place, so axis 1 returns a clean sweep over
live members. Axis 2 walks **every scalar field of every stored summary block**
and poisons each in turn: *any line that moves is a rendered figure depending
on a summary rather than on the evidence.*

Axis 2 is a GENERIC WALK rather than a list of known fields, deliberately. A
list can only re-find what someone already named, which is the failure mode
this script exists to end. Walking the records found ``pool_size`` — a site on
nobody's list, where the estimator FORMULAE were recounted but were being fed a
``k`` read from the stored block, leaving ``E[plug-in | uniform]`` and the
``1/k`` bar half-derived and moving the one sentence the artefact finding rests
on.

THE EXEMPTION, AND WHY IT IS ASSERTED RATHER THAN LISTED
--------------------------------------------------------
Axis 2's rule is **not** a blanket "no rendered line may depend on a stored
field", because one site depends on one **on purpose**: ``gpu_completeness``
cross-checks its recount against the sweep's stored ``seeds_readable`` and
DISCLOSES any disagreement, so that a record whose summary and readings tell
different stories says so out loud instead of silently preferring one. A
blanket rule would delete that disclosure.

So ``seeds_readable`` is exempt — but the exemption is **tested, not waived**.
For those fields the script asserts the disclosure actually FIRES, and that
what moves is only prose: **no ``|`` table row may move**, because a table row
is a published figure and the row that mixes a recounted percentage with a
frozen count is the exact artifact this class produces. An exemption nobody
checks is how a defect hides behind the word "intentional".

USE
---
Run it after ANY change to ``derive.py``::

    python3 readings/ps185-2026-08-26/enumerate_summary_sites.py

Axis 1: every scenario should report changed lines. Axis 2: every field should
report NO change, except the asserted exemption above. Either way round, a
violation is a live defect of this class.

This script only READS the committed records; every mutation is applied to an
in-memory copy. It never writes to the reading set.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load_derive():
    spec = importlib.util.spec_from_file_location(
        "ps185_derive_enumerate", HERE / "derive.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


D = _load_derive()


def load_all() -> dict:
    """A FRESH copy of every committed record, safe for a caller to mutate."""
    return {
        "off": D.load(D.LAYER_OFF),
        "on": D.load(D.LAYER_ON),
        "uoff": D.load(D.UNIF_OFF),
        "uon": D.load(D.UNIF_ON),
        "rb": D.load(D.READBACK),
        "rep": D.load(D.REPLICATE),
        "repc": D.load(D.REPLICATE_CHROME),
    }


def render(r: dict) -> str:
    """The whole derived document, from one record set."""
    return "\n".join([
        D.gpu_section(r["off"], r["on"], r["uoff"], r["uon"]),
        D.readback_section(r["rb"], r["rep"], r["repc"]),
        D.coverage_section(r["off"], r["on"], [
            ("readback-vectors.three-seeds.json", r["rb"]),
            ("readback-vectors.replicate.json", r["rep"]),
            ("readback-vectors.replicate-chromium.json", r["repc"]),
        ]),
    ])


def truncate_arm(rec: dict, arm: str, keep: int = 12) -> None:
    """Null all but ``keep`` of an arm's readings. Summary left UNTOUCHED."""
    for seed in sorted(rec["readings"][arm])[keep:]:
        rec["readings"][arm][seed] = None


def empty_readback_legs(rec: dict, engine: str) -> None:
    """Every leg of ``engine`` produces no vectors. Verdicts left intact."""
    for leg in (rec.get("readings", {}).get(engine, {}) or {}).values():
        if isinstance(leg, dict):
            leg["reading"] = {"vectors": {}}


# ---------------------------------------------------------------------------
# AXIS 1 — a GENERIC WALK of the raw readings
# ---------------------------------------------------------------------------
#
# Round 7's blocker. Axis 1 used to be six hand-written scenarios whose entire
# mutation vocabulary was `readings[arm][seed] = None` and
# `leg["reading"] = {"vectors": {}}` — so of the NINE fields a readback leg
# carries it touched exactly one. `label`, `seed`, `layer`, `error`,
# `sandbox_waived`, `dev_shm_waived`, `dev_shm_bytes` and `engine` were never
# mutated by anything, and axis 2 could not see them either because a reading
# is not a stored summary. Two live defects sat in that blind spot BETWEEN the
# axes, one of them a sentence contradicting the table row directly above it.
#
# A hand-written scenario list can only re-find what someone already named.
# This walks the records instead: every field of every leg, every arm of every
# sweep, destroyed and collapsed in turn.

READBACK_KEYS = ("rb", "rep", "repc")
GPU_MODES = ("on", "off")


def _subpaths(node, prefix=()):
    """Every leaf path inside a leg — the generic part of the generic walk."""
    if isinstance(node, dict) and node:
        for k, v in node.items():
            yield from _subpaths(v, prefix + (k,))
    else:
        yield prefix


def _get(node, path):
    for k in path:
        node = node[k]
    return node


def _set(node, path, value):
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


def _collapsed(value):
    """A same-type replacement that DESTROYS the field's information.

    Type-preserving for the same reason axis 2's `poison` is: a replacement
    that changed the type could move the render by raising a formatting error
    rather than by being consulted, reporting a clean site as dirty.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return "COLLAPSED"
    if isinstance(value, (int, float)):
        return 999999
    if isinstance(value, list):
        return ["COLLAPSED"]
    if isinstance(value, dict):
        return {}
    return None


def _leg_values(records, key, engine, path):
    out = []
    for leg in (records[key]["readings"].get(engine, {}) or {}).values():
        if not isinstance(leg, dict):
            continue
        try:
            out.append(_get(leg, path))
        except (KeyError, TypeError):
            pass
    return out


def _readback_group(key, engine, path):
    def mutate(value_of):
        def apply(r):
            for leg in (r[key]["readings"].get(engine, {}) or {}).values():
                if not isinstance(leg, dict):
                    continue
                try:
                    _set(leg, path, value_of(_get(leg, path)))
                except (KeyError, TypeError):
                    pass
        return apply
    return [
        ("destroy", mutate(lambda _v: None)),
        ("collapse", mutate(_collapsed)),
    ]


def _gpu_group(mode, arm):
    def destroy(r):
        for s in r[mode]["readings"][arm]:
            r[mode]["readings"][arm][s] = None

    def collapse(r):
        for s in r[mode]["readings"][arm]:
            r[mode]["readings"][arm][s] = "COLLAPSED-SINGLE-IDENTITY"

    def truncate(r):
        truncate_arm(r[mode], arm)

    return [("destroy", destroy), ("collapse", collapse),
            ("truncate", truncate)]


def _reading_groups():
    """Every raw-reading field, walked out of the records themselves."""
    base = load_all()
    groups = []
    for key in READBACK_KEYS:
        for engine, legs in (base[key].get("readings") or {}).items():
            paths = []
            for leg in legs.values():
                if not isinstance(leg, dict):
                    continue
                for p in _subpaths(leg):
                    if p not in paths:
                        paths.append(p)
            for path in paths:
                groups.append((
                    f"{key}/{engine}/{'.'.join(path)}",
                    (key, ".".join(path)),
                    _readback_group(key, engine, path),
                    lambda r, key=key, engine=engine, path=path:
                        _leg_values(r, key, engine, path),
                ))
    for mode in GPU_MODES:
        for arm in sorted(base[mode]["readings"]):
            groups.append((
                f"gpu[{mode}]/{arm}/identity",
                (f"gpu[{mode}]", "identity"),
                _gpu_group(mode, arm),
                lambda r, mode=mode, arm=arm:
                    list(r[mode]["readings"][arm].values()),
            ))
    return groups


# A raw-reading field that feeds NO rendered claim is not a defect — most of
# these records is provenance, deliberately recorded and deliberately not
# published. So axis 1's rule is NOT "every field must move"; that rule would
# be false, and a harness that cries wolf on 58 honest fields teaches its
# reader to skip the output.
#
# Instead an inert field must be DECLARED, with the reason it is inert, and
# the declaration is keyed by (record, field-path) — `rb` and `rep` are listed
# separately even where they share a spelling, because they are consulted
# differently: `layer.installed` IS a rendered claim on the three-seeds record
# and is pure provenance on the replicates. Keying on the bare field name
# would have waived the real one for sharing a name with the others.
#
# Adding a field to a record makes this fail until someone triages it, which
# is the point: the list records a decision, it does not suppress a check.
NOT_RENDERED = {
    ("rb", "label"): "leg label — provenance; the render names engine+seed itself",
    ("rb", "seed"): "duplicates the record-level `seeds` list, which is what the render iterates",
    ("rb", "engine"): "duplicates the `readings` key the render iterates",
    ("rb", "sandbox_waived"): "launch provenance, reported in PROVENANCE.md not in the article",
    ("rb", "dev_shm_waived"): "launch provenance (the /dev/shm disclosure), not a measured figure",
    ("rb", "dev_shm_bytes"): "launch provenance, not a measured figure",
    ("rb", "layer.route"): "how the layer was installed; the article cites WHAT was installed",
    ("rb", "layer.complete"): "install-completeness flag; the render counts `installed` instead",
    ("rb", "layer.failed"): "empty on every committed leg; nothing to render",
    ("rb", "error"): "rendered only for a leg that produced NO vectors (the lost-leg disclosure); a leg WITH vectors has no error to report",
    ("rb", "reading.note"): "free-text note, empty on every committed leg",
}
for _k in ("rep", "repc"):
    NOT_RENDERED.update({
        (_k, "label"): "replicate record — feeds the instrument check only, which compares VECTORS",
        (_k, "seed"): "replicate record — the check looks vectors up BY seed from the primary record",
        (_k, "engine"): "replicate record — duplicates the `readings` key",
        (_k, "sandbox_waived"): "replicate launch provenance",
        (_k, "dev_shm_waived"): "replicate launch provenance",
        (_k, "dev_shm_bytes"): "replicate launch provenance",
        (_k, "layer.route"): "replicate record — the layer sentence cites the three-seeds record",
        (_k, "layer.complete"): "replicate record — not cited by any rendered claim",
        (_k, "layer.failed"): "replicate record — not cited by any rendered claim",
        (_k, "layer.failed.layer"): "replicate record — the chromium@9001 lost-leg detail; the loss itself is disclosed from the ABSENT vectors",
        (_k, "layer.installed"): "replicate record — the layer sentence cites the three-seeds record, not this one",
        (_k, "error"): "replicate record — the lost leg is disclosed from its absent vectors, not from this string",
        (_k, "reading.note"): "free-text note, empty on every committed leg",
    })


def _cited_values(values, text):
    """Which of a field's values appear VERBATIM in the rendered document.

    This is the mechanical detector for the round-7 defect class, and it needs
    no list of known sites. A value that is PRINTED in the article is a
    rendered claim about the evidence by definition — so if printing it
    survives the destruction of the very field it was read from, the article
    may not be reading it. That is exactly how the layer sentence failed: it
    printed `['audio', 'locale', 'webgl']` while consulting nothing, so the
    list stayed on the page when the records stopped saying it.

    Deliberately restricted to DISTINCTIVE values — lists, and strings of 8+
    characters. A short scalar (`true`, `0`, a seed number) collides with
    unrelated prose and would report noise as a finding.

    ⚠️ A hit here is a SUSPECT, not a verdict. Two records can hold the SAME
    value, and the article legitimately reads one of them — see
    ``_attributable`` below, which is what tells those apart.
    """
    hits = []
    for v in values:
        if isinstance(v, list) and v:
            if repr(v) in text:
                hits.append(repr(v))
        elif isinstance(v, str) and len(v) >= 8:
            if v in text:
                hits.append(v)
    return hits


def run_axis1(base: "list[str]", quiet: bool) -> "list[str]":
    """Walk every raw-reading field. Return the labels that VIOLATE."""
    base_text = "\n".join(base)
    groups = _reading_groups()
    violations: "list[str]" = []
    print(f"\n{'=' * 76}\nAXIS 1 — destroy the raw readings, summary INTACT")
    print(f"  {len(groups)} raw-reading fields walked (generic, from the "
          f"records)\n")

    # ---- pass 1: mutate every field, keep what each mutation rendered ----
    observed = []
    attributable: "set[str]" = set()

    # STRUCTURAL VALUES ARE NOT MEASUREMENTS, and the distinction is drawn
    # from the records rather than by a hand-written waiver. An engine name
    # and a seed are IDENTIFIERS: they key `readings`, `verdicts` and
    # `engine_builds`, they are listed in `engines` / `seeds`, and the render
    # iterates those lists to build its tables. So "chromium" is on the page
    # because the record says the sweep COVERED chromium — and also because
    # prose legitimately says "chromium-only". A leg's own `engine` field is a
    # duplicate label, and destroying it correctly moves nothing.
    #
    # Flagging that would be the harness inventing a defect out of a repeated
    # identifier, which is the false-positive twin of the defect it hunts.
    # Computed from the record's own structure, so a record that grows a new
    # engine or seed is covered without editing this file.
    structural: "set[str]" = set()
    for key in READBACK_KEYS:
        rec = load_all()[key]
        for block in ("readings", "verdicts", "engine_builds"):
            if isinstance(rec.get(block), dict):
                structural.update(str(k) for k in rec[block])
        structural.update(str(v) for v in (rec.get("engines") or []))
        structural.update(str(v) for v in (rec.get("seeds") or []))

    for label, decl_key, ops, values_of in groups:
        moved: "list[str]" = []
        crashed: "list[str]" = []
        for op_name, apply in ops:
            records = load_all()
            apply(records)
            try:
                after = render(records).split("\n")
            except Exception as exc:  # noqa: BLE001
                # A destroyed reading must be REPORTED as unobtainable, never
                # crashed on: `INCONCLUSIVE` is a result the article has to be
                # able to print. A traceback prints nothing at all.
                crashed.append(f"{op_name}: {type(exc).__name__}: {exc}")
                continue
            if any(b != a for b, a in zip(base, after)):
                moved.append(op_name)
            # ATTRIBUTION. Whatever vanished from the page when this field was
            # destroyed is a value the article demonstrably READS from it.
            after_text = "\n".join(after)
            for cited in _cited_values(values_of(load_all()), base_text):
                if cited not in after_text:
                    attributable.add(cited)
        observed.append((label, decl_key, values_of, moved, crashed))

    # ---- pass 2: verdicts, now that attribution is known ----
    for label, decl_key, values_of, moved, crashed in observed:
        if crashed:
            violations.append(f"{label} — render CRASHED: {crashed[0]}")
            print(f"  ✗ {label}: render CRASHED — {crashed[0][:90]}")
            continue

        if moved:
            if not quiet:
                print(f"  ✓ {label} (moved under: {', '.join(moved)})")
            continue

        # Inert. A cited value is only FROZEN if the article reads it from
        # nowhere. Two legitimate reasons a printed value survives the
        # destruction of THIS field, and neither is a defect:
        #
        #  1. ATTRIBUTABLE — destroying some OTHER field removes it, so the
        #     claim is live and this field is a duplicate copy. The
        #     three-seeds record and its replicates hold identical layer
        #     reports and the article cites the three-seeds one; flagging the
        #     replicates for holding the same value would invent a defect.
        #  2. STRUCTURAL — the value is an IDENTIFIER (an engine name, a
        #     seed) that keys the record and is iterated to build the tables,
        #     and that prose legitimately uses as a word. A leg's `engine`
        #     field is a duplicate label, not a measurement.
        frozen = [c for c in _cited_values(values_of(load_all()), base_text)
                  if c not in attributable and c not in structural]
        if frozen:
            violations.append(
                f"{label} — CITED BUT FROZEN: the article prints "
                f"{frozen[0][:60]!r}, and destroying this field — or any "
                "other — does not remove it")
            print(f"  ✗ {label}: CITED BUT FROZEN — prints {frozen[0][:60]!r} "
                  "but reads it from nothing")
        elif decl_key not in NOT_RENDERED:
            violations.append(
                f"{label} — inert and UNDECLARED: it feeds no rendered claim, "
                "but nothing on the record says that was intended")
            print(f"  ✗ {label}: inert and UNDECLARED")
        elif not quiet:
            print(f"  ~ {label}: inert, declared ({NOT_RENDERED[decl_key]})")
    return violations


# ---------------------------------------------------------------------------
# AXIS 2 — poison the stored summary, leave the readings intact
# ---------------------------------------------------------------------------

# The one field a rendered line may legitimately depend on, SCOPED TO THE
# RECORD THAT CROSS-CHECKS IT. `gpu_completeness` compares its own recount
# against the GPU sweep's stored `seeds_readable` and DISCLOSES a
# disagreement, so a record whose summary and readings tell different stories
# says so out loud. See the module docstring: the exemption is asserted below,
# not waived.
#
# ⚠️ SCOPED BY LABEL PREFIX, NOT BY BARE FIELD NAME, and that distinction is
# load-bearing. The uniformity records carry a `seeds_readable` of their OWN,
# which is a different quantity that nothing cross-checks and which is now
# fully recounted. A name-keyed exemption matched those too, and would have
# waived any future defect in them for no better reason than a shared field
# name — an exemption is only as good as its scope.
DISCLOSED_FIELDS = {("gpu[on]", "seeds_readable"), ("gpu[off]", "seeds_readable")}


def _is_disclosed(label: str, field: str) -> bool:
    return any(
        label.startswith(prefix) and field == name
        for prefix, name in DISCLOSED_FIELDS
    )


# THE SECOND EXEMPTION, AND IT IS A DIFFERENT KIND FROM THE FIRST.
#
# `seeds_readable` above is exempt because a rendered line CROSS-CHECKS it.
# `fallback_pool_size` is exempt because it is not a summary at all: it is an
# ENVIRONMENTAL INPUT the sweep recorded beside its draws — the pool `k` the
# identities were drawn FROM. Axis 2's rule ("a rendered figure must not depend
# on a stored field") is aimed at a summary the sweep wrote ABOUT ITSELF, and
# it does not apply to an input, because for an input a dependency is the
# entire point: change the pool the draw came from and the score against it
# MUST change.
#
# It is exempt here precisely BECAUSE round 7 stopped reading it from the live
# product. Recounting `k` from today's `gpu_ext` looked like the same recount
# every other field got, and it silently rescored a committed measurement
# against a pool that did not exist when it was taken — PS-183 widened
# MAC_GPUS 2 -> 11 and flipped macos from `artefact` to `genuine`. Twenty-four
# observed identities cannot recover the size of the pool behind them, so the
# record is the only witness there is.
#
#     Recount what the readings determine. Pin what the readings merely
#     witnessed.
#
# ⚠️ ASSERTED, NOT WAIVED, and the assertion runs the OPPOSITE way to the
# disclosed-field one: a disclosed field must move only PROSE, while an input
# must actually MOVE A PUBLISHED ROW somewhere. An input that changes nothing
# is not load-bearing, which would mean the epoch pin reaches no rendered
# figure and the article is scoring against something else entirely. See the
# check after the walk.
#
# SCOPED TO `gpu[on]` ONLY, and the omission of `gpu[off]` is a measured fact
# rather than an oversight. The layer-OFF pool size IS consulted — poisoning it
# moves that record's bar and `E[plug-in | uniform]` internally — but the only
# layer-OFF figures the article RENDERS are the constant-arm bullets, which
# print a p-value to three decimals, and those arms are so far from uniform
# that p is `0.000` at any pool size. Nothing published responds, so `gpu[off]`
# needs no exemption: it passes the ordinary rule by not moving the render at
# all. Listing it anyway would claim a dependency the document does not have,
# and the assertion below would correctly call that claim inert.
# THE SECOND EPOCH INPUT, AND IT IS THE SAME KIND AS `fallback_pool_size`
# (PS-226). `verdict` is the sweep's record of WHAT THE GATE SAID when the draw
# was taken. Axis 2's rule targets a summary the sweep wrote about ITSELF and
# could recount from its own readings; this is not that. A verdict is a
# JUDGEMENT PASSED ON those readings by a specific version of
# `engine_gpu_variance`, so the readings WITNESSED it but do not DETERMINE it —
# 24 identities cannot tell you which rule was applied to them, exactly as they
# cannot recover the pool `k` they were drawn from.
#
#     Recount what the readings determine. Pin what the readings merely
#     witnessed.
#
# It is exempt here precisely BECAUSE round 8 stopped re-asking the live gate.
# Asking `classify` afresh looked like the same recount every other field got —
# and it reproduced all eight stored verdicts on the day it was written — but
# PS-191 then replaced the gate's rule (plug-in vs `1/k` bar became a one-sided
# hypothesis test at α=0.05) twelve minutes after this reading set was
# committed, and linux/android flipped `TOO_NARROW` -> `OK`. That re-scored a
# finished measurement against a rule that did not exist when it was taken, and
# left the article contradicting itself: the prose names three TOO_NARROW arms
# while the table below it printed `OK`. The sweep record is the only witness
# to what the gate said that day.
#
# ⚠️ ASSERTED, NOT WAIVED, the same way round: poisoning it MUST move a
# published row. A quoted verdict that no longer responds to the record would
# mean the article is reporting some other gate's opinion, which is the defect
# the pin exists to prevent. (Measured: all four arms move their table row.)
#
# SCOPED TO `gpu[on]` ONLY, for the same measured reason `fallback_pool_size`
# is. The layer-OFF verdicts ARE consulted, but the only layer-OFF figures the
# article renders are the constant-arm bullets, which print a recounted
# identity count and a p-value and never the verdict string. Nothing published
# responds, so `gpu[off]` passes the ordinary rule by not moving the render at
# all, and listing it would claim a dependency the document does not have.
EPOCH_INPUT_FIELDS = {
    ("gpu[on]", "fallback_pool_size"),
    ("gpu[on]", "verdict"),
}


def _is_epoch_input(label: str, field: str) -> bool:
    return any(
        label.startswith(prefix) and field == name
        for prefix, name in EPOCH_INPUT_FIELDS
    )


def poison(value):
    """A clearly-wrong replacement of the SAME TYPE.

    Type-preserving on purpose: a replacement that changed the type could move
    the render by raising a formatting error rather than by being consulted,
    which would report a site that is actually clean.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, float):
        return 0.123456
    if isinstance(value, int):
        return 999
    if isinstance(value, str):
        return "POISONED"
    if isinstance(value, list):
        return ["POISONED"]
    if isinstance(value, dict):
        return {k: poison(v) for k, v in value.items()}
    return value


def _summary_fields():
    """Every scalar field of every stored summary block, as (label, mutator).

    A GENERIC WALK of the records rather than a list of known field names —
    the whole point of the axis. Anything a future record grows is covered
    without editing this file.
    """
    out = []
    base = load_all()

    # GPU sweeps: result.per_arm[arm][field], plus the result-level blocks.
    for mode in ("on", "off"):
        for arm in sorted(base[mode]["result"]["per_arm"]):
            for field in base[mode]["result"]["per_arm"][arm]:
                def mk(mode=mode, arm=arm, field=field):
                    def apply(r):
                        blk = r[mode]["result"]["per_arm"][arm]
                        blk[field] = poison(blk[field])
                    return apply
                out.append(
                    (f"gpu[{mode}].result.per_arm.{arm}.{field}", field, mk()))
        for field in ("findings", "inconclusive", "arms_checked"):
            if field not in base[mode]["result"]:
                continue

            def mk2(mode=mode, field=field):
                def apply(r):
                    r[mode]["result"][field] = poison(r[mode]["result"][field])
                return apply
            out.append((f"gpu[{mode}].result.{field}", field, mk2()))

    # Uniformity records: per_arm[arm][field]. No raw readings of their own,
    # so EVERY field here is a stored summary.
    for mode in ("uon", "uoff"):
        for arm in sorted(base[mode]["per_arm"]):
            for field in base[mode]["per_arm"][arm]:
                def mk3(mode=mode, arm=arm, field=field):
                    def apply(r):
                        blk = r[mode]["per_arm"][arm]
                        blk[field] = poison(blk[field])
                    return apply
                out.append(
                    (f"unif[{mode}].per_arm.{arm}.{field}", field, mk3()))

    # Readback records: the verdicts block is the run's account of itself.
    for key in ("rb", "rep", "repc"):
        for engine, vecs in (base[key].get("verdicts") or {}).items():
            for vec, blk in vecs.items():
                if not isinstance(blk, dict):
                    continue
                for field in blk:
                    def mk4(key=key, engine=engine, vec=vec, field=field):
                        def apply(r):
                            b = r[key]["verdicts"][engine][vec]
                            b[field] = poison(b[field])
                        return apply
                    out.append(
                        (f"rb[{key}].verdicts.{engine}.{vec}.{field}",
                         field, mk4()))
        if base[key].get("cross_engine_contrast") is not None:
            def mk5(key=key):
                def apply(r):
                    r[key]["cross_engine_contrast"] = poison(
                        r[key]["cross_engine_contrast"])
                return apply
            out.append(
                (f"rb[{key}].cross_engine_contrast",
                 "cross_engine_contrast", mk5()))
    return out


def run_axis2(base: "list[str]", quiet: bool) -> "list[str]":
    """Poison each stored field in turn. Return the labels that VIOLATE."""
    violations: "list[str]" = []
    fields = _summary_fields()
    print(f"\n{'=' * 76}\nAXIS 2 — poison the stored summary, readings INTACT")
    print(f"  {len(fields)} stored fields walked\n")

    for label, field, apply in fields:
        records = load_all()
        apply(records)
        after = render(records).split("\n")
        changed = [(b, a) for b, a in zip(base, after) if b != a]
        rows_moved = [(b, a) for b, a in changed
                      if b.startswith("|") or a.startswith("|")]

        if _is_disclosed(label, field):
            # Exempt — but PROVE the exemption rather than assuming it. The
            # disclosure must actually fire, and it must move PROSE ONLY: a
            # moving `|` row would be a published figure taken from the
            # summary, which is what this class ships.
            fired = any("stored summary disagrees" in a for _, a in changed)
            if not fired:
                violations.append(
                    f"{label} — exempt as DISCLOSED, but poisoning it did not "
                    "fire the drift disclosure")
                print(f"  ✗ {label}: exemption claimed, disclosure SILENT")
            elif rows_moved:
                violations.append(
                    f"{label} — disclosed field moved a TABLE ROW, not just "
                    "the disclosure prose")
                print(f"  ✗ {label}: moved a published row")
            elif not quiet:
                print(f"  ~ {label}: exempt, disclosure fired (prose only)")
            continue

        if _is_epoch_input(label, field):
            # Exempt as an INPUT, and asserted the OPPOSITE way to a disclosed
            # field: this one MUST move a published row. `k` is the pool the
            # identities were drawn from, so a score against it that does not
            # respond to it is not reading the epoch at all — the article
            # would be scoring the draw against something else, which is the
            # very defect the pin was added to fix.
            if not rows_moved:
                violations.append(
                    f"{label} — exempt as an EPOCH INPUT, but poisoning it "
                    "moved no published row, so nothing rendered depends on "
                    "the pool the draw came from")
                print(f"  ✗ {label}: exemption claimed, but INERT")
            elif not quiet:
                print(f"  ~ {label}: exempt as an epoch input "
                      f"({len(rows_moved)} row(s) respond to it)")
            continue

        if changed:
            violations.append(label)
            print(f"  ✗ {label}: {len(changed)} line(s) MOVED — rendered from "
                  "the stored summary, not the readings")
            if not quiet:
                for b, a in changed[:3]:
                    print(f"      - {b[:130]}")
                    print(f"      + {a[:130]}")
        elif not quiet:
            print(f"  ✓ {label}")
    return violations


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="report only the verdict per scenario")
    ap.add_argument("--axis", choices=("1", "2", "both"), default="both",
                    help="which mutation axis to run (default: both)")
    args = ap.parse_args(argv)

    base = render(load_all()).split("\n")
    inert: "list[str]" = []
    violations: "list[str]" = []

    if args.axis in ("1", "both"):
        inert = run_axis1(base, args.quiet)

    if args.axis in ("2", "both"):
        violations = run_axis2(base, args.quiet)

    print()
    if inert or violations:
        if inert:
            print("AXIS 1 — A RENDERED CLAIM IS FROZEN AGAINST ITS EVIDENCE:")
            for name in inert:
                print(f"  - {name}")
        if violations:
            print("AXIS 2 — RENDERED FIGURES DEPEND ON A STORED SUMMARY:")
            for name in violations:
                print(f"  - {name}")
        return 1
    print("Axis 1: every raw-reading field either moves the render, or is "
          "declared as feeding no rendered claim — and no value the article "
          "prints survives the destruction of the field it was read from.")
    print("Axis 2: no stored summary field moves the render, except the "
          "asserted drift disclosure. The class is closed on both axes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
