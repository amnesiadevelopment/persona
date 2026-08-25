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

THE ENGINE IS NOT ONLY THE BINARY: THE STACK HAS TO MOVE IN LOCKSTEP
---------------------------------------------------------------------
``require_engine_moved`` watches ``engine_build``, which resolves to the
installed firefox BINARY. That leaves a false green one layer out from it, and
the layer it leaves open is the one CI is most likely to get wrong.

The engine ships as a PAIR: the ``firefox-NN`` binary and the driver that
speaks to it, ``invisible_playwright``, which pins ``invisible_core==NN``. The
two majors are the same number *by construction* — ``engine_autobump.plan()``
derives ``new_baseline`` as ``f"firefox-{core_major(latest_core)}"`` — and a
newer ``firefox-NN`` speaks a juggler contract only the newer core can drive.

So a provisioning step that installs the new binary and the new driver but
leaves ``invisible_core`` at the OLD major produces a recording of a stack
**nobody will ever run**. ``engine_build`` genuinely moved, so
``require_engine_moved`` is satisfied and the gate proceeds to certify — the
one guard built to catch "a comparison that never really happened" cannot see
this at all, because it is looking at the binary and the mismatch is in the
driver.

Both verdicts become unattributable when that happens:

* DRIFT — did the bump change what a site sees, or did an old core drive a new
  binary badly? The operator cannot tell, and this is exactly the false red
  that trains people to disbelieve the true ones.
* PASS — the tag is cut on evidence gathered from a stack no user runs.

:func:`require_stack_lockstep` closes it, and asserts the gate's premise about
the WHOLE engine stack rather than just the binary. It runs twice:

* at ``record`` time, BEFORE the browser is launched, so a misprovisioned
  runner fails fast and loudly instead of spending a browser launch producing a
  recording that cannot mean anything. The resolved core version is then
  stamped into the recording under ``engine_stack``;
* at ``compare`` time, re-derived from each artifact's OWN stamp against its
  OWN ``engine_build``. The artifact carries its evidence, so the check does
  not depend on the recording side having been trusted to run it.

A recording carrying no stamp is refused rather than compared, for the same
reason an unresolved ``engine_build`` is: a premise nobody checked is not a
premise that held.

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
import re
import sys

from .baseline import (
    BASELINE_ENGINE,
    BaselineUnavailable,
    count_errors,
    record_snapshot,
)
from .diff import (
    NotASnapshot,
    diff_snapshots,
    format_diff,
    inconclusive_count,
    require_snapshot,
)
from .snapshot import engine_build, load, quote_path, write

EXIT_PASS = 0
EXIT_DRIFT = 1
EXIT_CANNOT_RUN = 2

# An engine_build the resolver could not determine. `snapshot.engine_build`
# never raises and answers this string instead, so it arrives here as a value
# that LOOKS like data and is not one.
UNRESOLVED_BUILD = "unknown"

# Header field the recording carries its resolved driver-stack version under.
# Written by `record` (see `_cmd_record`) and re-checked by `compare`, so each
# artifact carries the evidence for its own premise rather than depending on
# the recording side having been trusted to check it.
STACK_FIELD = "engine_stack"

# The driver package whose major must track the firefox-NN binary. It is
# invisible_playwright's own pin, and engine_autobump derives the baseline tag
# from it (`firefox-{core_major(latest_core)}`), so the two majors are the same
# number by construction rather than by coincidence.
CORE_DISTRIBUTION = "invisible_core"


class GateCannotRun(RuntimeError):
    """A precondition failed, so no verdict about the identity is available.

    Always maps to EXIT_CANNOT_RUN and never to EXIT_DRIFT. A refusal is not a
    finding: reporting "the engine moved the fingerprint" because provisioning
    broke would be a false red on the most alarming signal this system has, and
    it trains an operator to disbelieve the true reds.
    """


# --- preconditions ----------------------------------------------------------


def major_of(version: str) -> int:
    """``"20.14.0"`` -> ``20``; ``-1`` when it cannot be read.

    Mirrors ``scripts/engine_autobump.core_major`` deliberately rather than
    importing it: ``scripts/`` is not an importable package from here, and this
    module must stay importable in a bare checkout.
    """
    match = re.match(r"^(\d+)(?:\.|$)", (version or "").strip())
    return int(match.group(1)) if match else -1


def build_major(build: str) -> int:
    """``"firefox-20"`` -> ``20``; ``-1`` when it cannot be read."""
    match = re.search(r"-(\d+)\s*$", (build or "").strip())
    return int(match.group(1)) if match else -1


def installed_core_version() -> str:
    """The ``invisible_core`` version actually installed in THIS environment.

    Read from installed package metadata rather than from ``pyproject.toml``:
    the pin file says what CI was *asked* to install, and the entire defect
    class this guards against is an install step that did not do what it was
    asked. Only the installed distribution can answer what the recording is
    really being taken against.

    Returns ``""`` when the package is not installed, which the caller reports
    as a refusal — never as a version.
    """
    try:
        from importlib import metadata

        return str(metadata.version(CORE_DISTRIBUTION))
    except Exception:
        return ""


def require_stack_lockstep(build: str, core_version: str, *, side: str) -> str:
    """Refuse a recording whose driver stack does not match its engine binary.

    THE POINT OF THIS FUNCTION, and why it is separate from
    :func:`require_engine_moved`. That guard compares ``engine_build``, which
    resolves to the installed firefox BINARY. A provisioning step that fetches
    the new binary and the new driver but leaves ``invisible_core`` at the old
    major therefore SATISFIES it: the build genuinely moved. The comparison then
    proceeds over a stack that will never ship, and both possible verdicts are
    unattributable — a DRIFT nobody can attribute to the engine rather than to
    the mismatch, or a PASS that certifies a tag on evidence from a stack no
    user runs.

    The two majors are equal by construction (``engine_autobump.plan`` derives
    ``firefox-NN`` from ``core_major(latest_core)``), so a mismatch is never
    incidental — it is always a provisioning failure.

    Returns the validated core version so the caller can stamp it.
    """
    if not core_version:
        raise GateCannotRun(
            f"the {side} side has no {CORE_DISTRIBUTION} installed, so the "
            "engine binary has no driver to be in lockstep with and this "
            "recording cannot be taken against a stack anyone ships. The "
            "engine is a PAIR — the firefox-NN binary and the driver that "
            f"pins {CORE_DISTRIBUTION}==NN — and only half of it is here. "
            "Check the provisioning step installed the driver stack, not just "
            "the binary."
        )

    want = build_major(build)
    got = major_of(core_version)
    if want < 0:
        raise GateCannotRun(
            f"the {side} side's engine build {build!r} does not name a major "
            "version, so it cannot be checked against the installed "
            f"{CORE_DISTRIBUTION} ({core_version}). Refusing to certify a "
            "comparison whose premise could not be established."
        )
    if got < 0:
        raise GateCannotRun(
            f"the {side} side's installed {CORE_DISTRIBUTION} version "
            f"{core_version!r} does not parse, so it cannot be checked against "
            f"engine build {build!r}. Refusing to certify a comparison whose "
            "premise could not be established."
        )
    if want != got:
        raise GateCannotRun(
            f"the {side} side is MISPROVISIONED: engine build {build!r} "
            f"(firefox-{want}) is installed alongside {CORE_DISTRIBUTION}=="
            f"{core_version} (major {got}). The engine and its driver MUST "
            "ship together — a firefox-NN binary speaks a juggler contract "
            "only the matching driver can drive — and these two majors are "
            "equal by construction, so this is a provisioning failure and "
            "never an incidental difference.\n\n"
            "A recording taken here would be of a stack NOBODY SHIPS, which "
            "makes both verdicts meaningless: a drift could not be attributed "
            "to the engine rather than to the mismatch, and a pass would "
            "certify a release on evidence from a stack no user runs. The "
            "engine-build guard cannot see this — the binary really did move. "
            "Refusing to certify. Install the driver stack at the pin the tree "
            "declares, not just the engine binary."
        )
    return core_version


def stack_of(snapshot: dict, *, side: str) -> str:
    """The driver-stack version a recording was taken under, refusing an absent one.

    A recording with no stamp is refused rather than compared. The stamp is the
    evidence that the lockstep premise was checked at all, and an unchecked
    premise is not a premise that held — the same reasoning that makes an
    unresolved ``engine_build`` a refusal rather than a value.
    """
    stack = snapshot.get(STACK_FIELD)
    if not isinstance(stack, str) or not stack:
        raise GateCannotRun(
            f"the {side} recording does not say which {CORE_DISTRIBUTION} it "
            f"was taken under ({STACK_FIELD}={stack!r}). Without it there is no "
            "evidence that the engine binary and its driver were in lockstep "
            "when this was recorded, and a recording of a mismatched stack is "
            "a recording of something nobody ships. Re-record it with a "
            "current gate build."
        )
    return stack


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

    # Re-derived from each artifact's OWN stamp against its OWN build, and
    # checked on BOTH sides. `record` already refused a mismatched stack before
    # launching a browser, so reaching here with one means the recording came
    # from elsewhere — but the artifact carries its own evidence precisely so
    # this does not rest on the recording side having been trusted to look.
    #
    # Note this cannot be folded into require_engine_moved: that guard is
    # satisfied by a mismatched stack (the binary really did move), which is
    # exactly why this defect class is invisible to it.
    before_stack = require_stack_lockstep(
        before_build, stack_of(before, side="before"), side="before"
    )
    after_stack = require_stack_lockstep(
        after_build, stack_of(after, side="after"), side="after"
    )
    lines.append(
        f"driver stack in lockstep: {CORE_DISTRIBUTION} {before_stack} -> "
        f"{after_stack} (majors track {before_build} -> {after_build})"
    )

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
    # Checked BEFORE the browser is launched, deliberately. A misprovisioned
    # runner cannot produce a recording that means anything, so failing here
    # costs one message instead of a browser launch plus a full probe sweep
    # whose output would have to be thrown away — and it names the fault at the
    # step that caused it rather than three steps later in the comparison.
    #
    # Resolved through the same accessor `build_snapshot` will use, so the
    # version checked here is the version the recording ends up claiming.
    build = engine_build_of(
        {"engine_build": engine_build(BASELINE_ENGINE)}, side="this"
    )
    core = require_stack_lockstep(build, installed_core_version(), side="this")

    snapshot = record_snapshot(fresh=True)
    errors = count_errors(snapshot)
    total = sum(len(realm) for realm in snapshot["probes"].values())

    # The pre-launch check was made against a build resolved BEFORE the session;
    # this is the build the artifact will actually claim. They agree in every
    # normal run, and a disagreement means the engine moved underneath the
    # recording — so the stamp about to be written would attest to a pairing
    # that was never true. Refuse rather than stamp it.
    recorded_build = engine_build_of(snapshot, side="this")
    if recorded_build != build:
        raise GateCannotRun(
            f"the engine build changed while recording: {build!r} was checked "
            f"against {CORE_DISTRIBUTION} {core} before launch, but the "
            f"recording claims {recorded_build!r}. The lockstep stamp would "
            "attest to a pairing that was never true, so nothing is written."
        )

    # Stamped so `compare` can re-derive the premise from the artifact itself
    # rather than trusting that this side ever checked it.
    snapshot[STACK_FIELD] = core

    try:
        write(snapshot, args.output)
    except OSError as exc:
        raise GateCannotRun(
            f"recorded a reading but could not write it to {quote_path(args.output)}: "
            f"{exc}. Nothing was written."
        ) from exc

    print(
        f"wrote {args.output}: {total} readings across "
        f"{len(snapshot['realms'])} realm(s) on engine build "
        f"{snapshot.get('engine_build')} with {CORE_DISTRIBUTION} {core}, "
        f"{errors} error(s)",
        file=sys.stderr,
    )
    # Errors are NOT fatal here, and that is deliberate: this side of the gate
    # is one of two recordings, and whether an unread probe matters is a
    # question only the COMPARISON can answer. A probe that errored on both
    # sides is reported as inconclusive by `compare` and never as agreement, so
    # refusing here would stop the gate before it could say that properly.
    return EXIT_PASS


def _load_recording(path: str, *, side: str) -> dict:
    """Read one of the two recordings, or refuse — never traceback.

    The guard is here at the CALL SITE and not in ``snapshot.load()`` because
    that is already the unanimous convention among its callers: ``cli.py`` and
    ``baseline.py`` both guard their own ``load()`` on exactly this pair, and
    this module was the only one of the three that did not.

    ``(OSError, ValueError)``, and the width is deliberate and load-bearing on
    BOTH sides:

      * ``ValueError`` covers the file that opened and would not parse — a
        truncated or half-written recording, an empty file, an ``<html>`` error
        page saved under a ``.json`` name, and non-UTF-8 bytes. That last one
        raises ``UnicodeDecodeError``, which IS a ``ValueError`` but is NOT a
        ``JSONDecodeError``, so catching the narrower type would still let a
        corrupt-bytes recording out.
      * ``OSError`` covers the read that never gets that far — a path that
        lands on a DIRECTORY, a permission denial, a broken link. Those are
        ``OSError`` and NOT ``ValueError``, so a guard written only to the
        corrupt-content story leaves the mistyped path tracebacking out.

    Either omission lands on exit 1, and exit 1 out of this module means the
    engine moved what a site sees — the loudest signal this subsystem has,
    announced for a file nobody managed to read. Whatever went wrong here,
    nothing was compared, so it is a refusal (exit 2) and never drift.

    ``FileNotFoundError`` is the one ``OSError`` this does NOT keep: it is
    re-raised below so ``main()``'s existing arm still answers the absent-file
    case in its own words. Both routes end on the same exit 2; this message
    additionally names WHICH of the two arguments was unreadable.
    """
    try:
        return load(path)
    except FileNotFoundError:
        # Deliberately re-raised, NOT folded into the guard below. A missing
        # file is an OSError and would otherwise be swallowed here, and
        # `main()` already refuses it with its own message on the same exit 2.
        # That arm is a control for this change rather than a target of it, so
        # it is left reachable and untouched: an absent recording and an
        # unreadable one are different operator problems, and the existing
        # wording for the first one is not this fix's to rewrite.
        raise
    except (OSError, ValueError) as exc:
        raise GateCannotRun(
            f"the {side} recording at {quote_path(path)} could not be read: "
            f"{exc}. Nothing was compared, so this is NOT drift — the "
            "recording itself is unusable. Re-record it, and check that the "
            "file was written completely."
        ) from exc


def _cmd_compare(args: argparse.Namespace) -> int:
    # Loaded OUTSIDE the gate() call, deliberately. The guard belongs on the
    # reads and nothing else: NotASnapshot subclasses ValueError, so widening
    # this to cover gate() would catch a file that read back perfectly well and
    # re-label it "could not be read" — and would swallow real comparison
    # errors, destroying the 0/1/2 distinction this refusal exists to protect.
    before = _load_recording(args.before, side="before")
    after = _load_recording(args.after, side="after")
    code, report = gate(before, after)
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
