"""The engine-bump gate: does the daily engine swap move what a site sees?

    # on ONE runner, in ONE job:
    python -m src.services.verify.engine_gate record --output /tmp/before.json
    # ... engine pins are bumped, the new engine is installed ...
    python -m src.services.verify.engine_gate record --output /tmp/after.json
    python -m src.services.verify.engine_gate compare /tmp/before.json /tmp/after.json

``.github/workflows/engine-autoupdate.yml`` runs daily at 06:00 UTC, rewrites the
engine pins, commits to ``main`` and pushes a tag that triggers a full release
build with nobody in the loop. An engine swap replaces the layer half the masking
lives in — the single event most likely to move what a site sees about a profile.
This module is what makes that event checkable, and it is wired into that job
BEFORE it tags (see the workflow, and the PR that introduced this file).

WHY THIS RECORDS BOTH SIDES INSTEAD OF USING THE COMMITTED REFERENCE
--------------------------------------------------------------------
``tests/fixtures/engine-fingerprint-baseline.firefox.json`` is a real reference
*for the machine that recorded it*. Its own ``provenance.env_sensitive_probes``
declares five host-dependent probes: ``webgl.unmasked`` renders through the
host's real GL stack and ``fonts.measureText`` measures the host's installed
fonts. Three recordings on one machine came out byte-identical, so the instrument
is sound — but machine-independence was never shown, and the window and worker
realms already disagree on three fonts *inside a single snapshot*.

Reuse that artifact as this gate's reference on a CI runner and it is permanently
red for reasons having nothing to do with the engine. A gate that is always red
is a gate people learn to ignore, which is worse than no gate.

So this gate records **both** of its sides itself, on **one runner inside one
job**: the old pins, then the bumped pins. Same host, so the host-dependent
probes cancel and the engine is the only variable. That is also why it must not
be split across two jobs — two jobs are two runners, and every env-sensitive
probe would read as engine drift.

WHY THERE IS NO ``accept`` AND NO DEFAULT ``--output``
------------------------------------------------------
The obvious cure for a red gate is to re-record until it goes green, and that
destroys the reference: it manufactures evidence of continuity across exactly
the event the reference exists to police. So accepting a move stays a deliberate,
reviewable human act — run ``baseline_cli record`` and commit the artifact in a
diff someone reads.

Structurally, not by convention:

* there is no accept/bless path in this module;
* ``record`` has **no default** ``--output`` and requires it. ``baseline_cli``'s
  default *is* the committed artifact, so inheriting that default would let a
  stray CI invocation silently overwrite the reference;
* this module never imports ``BASELINE_ARTIFACT`` — there is a test asserting
  that, so the path cannot creep back in;
* the workflow's commit step stages an explicit file list.

THE FAILURE THIS IS REALLY BUILT AGAINST IS THE FALSE GREEN
------------------------------------------------------------
A false red gets investigated. A false **green** does not — that is its whole
danger. If engine provisioning silently fails, both recordings are taken on the
*same* engine build, every probe agrees by construction, the diff is empty, and
the gate prints a confident pass **over a comparison that never happened**, at
precisely the moment it was supposed to be looking.

:func:`require_engine_moved` refuses that: identical (or unresolved)
``engine_build`` on the two sides is EXIT_CANNOT_RUN, never a pass and never
drift. It is load-bearing — with it neutered, this gate prints PASS over two
recordings of the same engine.

Exit codes — three outcomes, and only one of them is a pass:

    0   the comparison RAN over two genuinely different engine builds, every
        probe was read on both sides, and nothing moved.
    1   a probe genuinely moved. This is the finding: the engine changed what a
        site sees about the pinned profile.
    2   the gate COULD NOT RUN, so nothing was certified — no display, no
        readable recording, the two sides are the same engine build, or the
        comparison rested on readings nobody obtained.

1 and 2 are both non-zero and both stop the bump, so a caller that only checks
``if rc != 0`` is never wrong about whether to proceed. They are kept distinct
because "the identity moved" and "we failed to look" need different human
responses, and collapsing them is how a provisioning failure gets triaged as
engine drift (or, far worse, the reverse).
"""

from __future__ import annotations

import argparse
import copy
import sys

from .baseline import BaselineUnavailable, count_errors, record_snapshot
from .diff import (
    NotASnapshot,
    diff_snapshots,
    format_diff,
    inconclusive_count,
    require_snapshot,
)
from .snapshot import load, write

EXIT_PASS = 0
EXIT_DRIFT = 1
EXIT_CANNOT_RUN = 2

# An engine_build the resolver could not determine. `snapshot.engine_build`
# never raises and answers this string instead, so it arrives here as a value
# that LOOKS like data and is not one.
UNRESOLVED_BUILD = "unknown"


class GateCannotRun(RuntimeError):
    """A precondition failed, so no verdict about the identity is available.

    Always maps to EXIT_CANNOT_RUN and never to EXIT_DRIFT. A refusal is not a
    finding: reporting "the engine moved the fingerprint" because provisioning
    broke would be a false red on the most alarming signal this system has, and
    it trains an operator to disbelieve the true reds.
    """


# --- preconditions ----------------------------------------------------------


def engine_build_of(snapshot: dict, *, side: str) -> str:
    """The engine build a recording was taken under, refusing an unusable one.

    ``side`` names which of the two recordings this is, so a failure message
    can say which one to go and look at.
    """
    build = snapshot.get("engine_build")
    if not isinstance(build, str) or not build or build == UNRESOLVED_BUILD:
        raise GateCannotRun(
            f"the {side} recording does not say which engine build it was taken "
            f"under (engine_build={build!r}). Without that this gate cannot "
            "establish that the two sides are different engines, and a "
            "comparison of two recordings of the SAME engine agrees by "
            "construction — a pass that means nothing. Check that the engine "
            "was actually installed before the recording was taken."
        )
    return build


def require_engine_moved(before: dict, after: dict) -> tuple[str, str]:
    """Refuse to certify a comparison whose two sides are the same engine.

    THE POINT OF THIS FUNCTION. The gate's premise is that the engine is the
    only variable between the two recordings. If engine provisioning silently
    fails — the download 404s, the install step is skipped, the second pip
    install resolves to the same pinned build — then both recordings are taken
    on the same engine, every probe agrees *by construction*, the diff is empty,
    and the gate reports a confident PASS over a comparison that never happened.

    That is the false green, and it fires at exactly the moment the gate was
    supposed to be looking. A false red gets investigated by somebody; a false
    green does not.

    So: same build on both sides, or a build that could not be resolved at all,
    is EXIT_CANNOT_RUN. Deliberately NOT drift — nothing was observed to move,
    and saying otherwise would be a lie in the other direction.
    """
    before_build = engine_build_of(before, side="before")
    after_build = engine_build_of(after, side="after")
    if before_build == after_build:
        raise GateCannotRun(
            f"both recordings were taken on engine build {before_build!r}, so "
            "the engine did NOT move between them and this comparison proves "
            "nothing: two readings of the same engine agree by construction. "
            "This is almost always a provisioning failure — the bumped engine "
            "was never installed before the second recording — and NOT evidence "
            "that the bump is safe. Refusing to report a pass."
        )
    return before_build, after_build


def _readable_probes(snapshot: dict) -> list[tuple[str, str]]:
    """``(realm, probe_id)`` for every probe carrying an obtained reading.

    Only readings, never errors: the self-test below perturbs one of these, and
    perturbing an entry that NEITHER side could read produces an ``inconclusive``
    entry rather than a ``changed`` one — the self-test would then fail for a
    reason that has nothing to do with the comparator working.
    """
    found: list[tuple[str, str]] = []
    probes = snapshot.get("probes")
    if not isinstance(probes, dict):
        return found
    for realm in sorted(probes):
        entries = probes[realm]
        if not isinstance(entries, dict):
            continue
        for probe_id in sorted(entries):
            entry = entries[probe_id]
            if isinstance(entry, dict) and "value" in entry:
                found.append((realm, probe_id))
    return found


# --- falsification: prove the comparator is awake, on every single run -------


def plant_moved_reading(snapshot: dict, realm: str, probe_id: str) -> dict:
    """A copy of ``snapshot`` with one probe's reading deliberately perturbed.

    The straightforward defect: a reading that moved. A comparator that reports
    nothing here is inert.
    """
    perturbed = copy.deepcopy(snapshot)
    entry = perturbed["probes"][realm][probe_id]
    entry["value"] = {"__perturbed_by_self_test__": True, "was": entry.get("value")}
    return perturbed


def plant_absent_probe(snapshot: dict, realm: str, probe_id: str) -> dict:
    """A copy of ``snapshot`` with one probe REMOVED entirely.

    The sharper of the two defects, and the reason this self-test exists rather
    than a single perturbation check. A comparator that loops over the
    INTERSECTION of the two probe sets cannot see an absent probe at all: it
    silently drops out of the iteration, contributes no entry, and the run goes
    green having quietly stopped checking it. The guard can look complete and
    still never run on the case that matters, because the iteration set — not
    the predicate — is what is wrong.

    The ticket names this case explicitly: a probe present on one side and
    missing on the other must be REPORTED, not passed over.
    """
    perturbed = copy.deepcopy(snapshot)
    del perturbed["probes"][realm][probe_id]
    return perturbed


def self_test(snapshot: dict) -> list[str]:
    """Plant two known defects in THIS RUN's own recording; refuse unless both
    are caught. Returns one human-readable line per defect proven caught.

    Run on every invocation of ``compare``, against the real recording rather
    than a fixture, because the claim being made is about the comparator as it
    is configured *on this runner, over these probes, right now*. A gate is only
    worth its exit code if it has been shown to fail; this shows it, every time,
    immediately before it is trusted.
    """
    candidates = _readable_probes(snapshot)
    if not candidates:
        raise GateCannotRun(
            "the recording carries no probe with an obtained reading, so the "
            "comparator cannot be shown to work and its verdict cannot be "
            "trusted. Nothing was certified."
        )

    realm, probe_id = candidates[0]
    proven: list[str] = []

    moved = diff_snapshots(snapshot, plant_moved_reading(snapshot, realm, probe_id))
    if not any(
        e.get("probe_id") == probe_id
        and e.get("realm") == realm
        and e.get("status") == "changed"
        for e in moved
    ):
        raise GateCannotRun(
            "SELF-TEST FAILED: a deliberately perturbed reading for "
            f"{realm}/{probe_id} was NOT reported as changed. The comparator is "
            "not detecting movement, so a green verdict from it would be "
            "meaningless. Refusing to certify this bump."
        )
    proven.append(f"a moved reading ({realm}/{probe_id}) is reported as changed")

    absent = diff_snapshots(snapshot, plant_absent_probe(snapshot, realm, probe_id))
    if not any(
        e.get("probe_id") == probe_id
        and e.get("realm") == realm
        and e.get("status") in ("removed", "changed")
        for e in absent
    ):
        raise GateCannotRun(
            f"SELF-TEST FAILED: probe {realm}/{probe_id} was present on one "
            "side and absent on the other, and was NOT reported. A comparator "
            "that iterates the intersection of the two probe sets silently "
            "stops checking a probe that disappears — which is how a gate goes "
            "green while no longer looking. Refusing to certify this bump."
        )
    proven.append(f"an absent probe ({realm}/{probe_id}) is reported, not skipped")

    return proven


# --- the gate ---------------------------------------------------------------


def gate(before: dict, after: dict) -> tuple[int, str]:
    """Compare two recordings and return ``(exit_code, report)``.

    Order of operations is deliberate. The preconditions are checked BEFORE the
    diff is believed, because an empty diff is this subsystem's agreement signal
    and every way of producing one without looking has to be excluded first.
    """
    lines: list[str] = []

    before = require_snapshot(before, source="the before recording")
    after = require_snapshot(after, source="the after recording")

    before_build, after_build = require_engine_moved(before, after)
    lines.append(f"engine moved: {before_build} -> {after_build}")

    for proven in self_test(after):
        lines.append(f"self-test: {proven}")

    entries = diff_snapshots(before, after)
    unread = inconclusive_count(entries)

    if not entries:
        lines.append("")
        lines.append(
            f"PASS — every probe was read on both {before_build} and "
            f"{after_build}, and none of them moved."
        )
        return EXIT_PASS, "\n".join(lines)

    lines.append("")
    lines.append(format_diff(entries))
    lines.append("")

    if unread == len(entries):
        # Nothing was observed to MOVE, but the comparison rests entirely on
        # readings nobody obtained. Never a pass (PS-29: inconclusive is never
        # a match) and not drift either — we failed to look, which is a
        # different fact needing a different human response.
        lines.append(
            f"COULD NOT RUN — {unread} probe(s) could not be read on either "
            "side, and nothing else differs. An unobtained reading is "
            "inconclusive, never agreement, so this is NOT a pass. The bump is "
            "stopped, but this is a failure to observe, not evidence that the "
            "engine changed anything."
        )
        return EXIT_CANNOT_RUN, "\n".join(lines)

    moved = len(entries) - unread
    lines.append(
        f"DRIFT — {moved} probe(s) moved between {before_build} and "
        f"{after_build}"
        + (f" ({unread} further probe(s) could not be read)" if unread else "")
        + ". The engine bump changes what a site sees about the pinned profile. "
        "Each line above names the probe, the realm, and expected versus "
        "observed.\n\n"
        "If this movement is reviewed and ACCEPTED, re-record the committed "
        "reference deliberately (python -m src.services.verify.baseline_cli "
        "record) and commit it in a reviewable diff. This job will not do that "
        "for you, by design: a gate that re-records itself until it goes green "
        "destroys the very reference it exists to defend."
    )
    return EXIT_DRIFT, "\n".join(lines)


# --- CLI --------------------------------------------------------------------


def _cmd_record(args: argparse.Namespace) -> int:
    snapshot = record_snapshot(fresh=True)
    errors = count_errors(snapshot)
    total = sum(len(realm) for realm in snapshot["probes"].values())

    try:
        write(snapshot, args.output)
    except OSError as exc:
        raise GateCannotRun(
            f"recorded a reading but could not write it to {args.output!r}: "
            f"{exc}. Nothing was written."
        ) from exc

    print(
        f"wrote {args.output}: {total} readings across "
        f"{len(snapshot['realms'])} realm(s) on engine build "
        f"{snapshot.get('engine_build')}, {errors} error(s)",
        file=sys.stderr,
    )
    # Errors are NOT fatal here, and that is deliberate: this side of the gate
    # is one of two recordings, and whether an unread probe matters is a
    # question only the COMPARISON can answer. A probe that errored on both
    # sides is reported as inconclusive by `compare` and never as agreement, so
    # refusing here would stop the gate before it could say that properly.
    return EXIT_PASS


def _cmd_compare(args: argparse.Namespace) -> int:
    code, report = gate(load(args.before), load(args.after))
    print(report)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.services.verify.engine_gate",
        description=(
            "Record the pinned profile on either side of an engine bump and "
            "refuse the bump if the engine moved what a site sees."
        ),
        epilog=(
            "Both recordings MUST be taken on the same machine in the same job: "
            "several probes read through the host's GL stack and installed "
            "fonts, so two runners would report host variance as engine drift. "
            "Needs a real browser, so it needs a display: prefix with "
            "'xvfb-run -a' on a headless runner."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record",
        help="record the pinned profile into a throwaway gate artifact",
        description=(
            "Record one side of the gate's comparison. --output is REQUIRED and "
            "has no default: the committed reference is baseline_cli's default "
            "output, and a default here could let a CI invocation overwrite it."
        ),
    )
    # No default, deliberately. See the module docstring: inheriting
    # baseline_cli's default would point CI at the committed reference.
    rec.add_argument(
        "-o",
        "--output",
        required=True,
        help="where to write this side's recording (required, no default)",
    )
    rec.set_defaults(func=_cmd_record)

    cmp_ = sub.add_parser(
        "compare",
        help="compare the two recordings and gate the bump on the result",
        description=(
            "Exits 0 only when the comparison ran over two genuinely different "
            "engine builds and nothing moved. Exits 1 on drift, 2 when it could "
            "not run (including when both recordings are of the same engine)."
        ),
    )
    cmp_.add_argument("before", help="recording taken on the OLD engine")
    cmp_.add_argument("after", help="recording taken on the BUMPED engine")
    cmp_.set_defaults(func=_cmd_compare)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (GateCannotRun, BaselineUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    except NotASnapshot as exc:
        # A file that parsed but is not a snapshot. Refusal, not drift: with no
        # probes on one side every probe diffs as added/removed and the gate
        # would print a confident maximum-alarm DRIFT for a comparison that
        # never happened.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    except FileNotFoundError as exc:
        print(
            f"error: {exc}. Nothing was compared, so nothing is certified.",
            file=sys.stderr,
        )
        return EXIT_CANNOT_RUN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
