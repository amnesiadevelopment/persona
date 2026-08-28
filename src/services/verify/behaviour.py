"""Behavioural checks: observe the PRODUCT doing its job, not what a site sees.

The checker matrix (``checkers.py``, PS-59/PS-69) answers one question well:
*what does a fingerprinting site see?* It answers nothing about the rest of the
product. A checker never launches two profiles and compares them, never restarts
one and checks it came back the same, never assigns a proxy and re-reads what an
unrelated edit did to it, never deletes something and asks whether it is
recoverable.

Every defect this project found by reading code this month had the same shape:
**do a thing, then do another thing, then observe.** A rename re-rolled the
presented machine under a live cookie jar; an edit silently cleared a proxy
assignment; unassigning a certificate stranded a decrypted private key on disk.
That shape is structurally invisible to a single-shot fingerprint reading, and
it is the shape almost every real defect has had.

This module is the fourth quadrant. It drives the instrument that already
exists — ``record_snapshot`` to observe a live profile, ``diff_snapshots`` to
ask "did it stay itself?", ``compare_profiles`` to ask "are these two genuinely
two?" — across sequences of real product operations. **It is not a second
recorder.** Every reading here comes from ``verify``'s own probe run; nothing in
this file evaluates JS, opens a socket, or invents a vector.

WHY EVERY CHECK CARRIES ITS OWN FALSIFICATION
----------------------------------------------
These checks are expensive: several of them launch a real browser two or three
times. Nobody re-reads an expensive check that has been green for a month, which
makes a permanently-green one worse than no check at all — it converts an
unmeasured surface into a surface everyone believes is measured.

So a check here does not merely assert. Immediately before its verdict is
trusted, it **plants a known defect of the class it exists to catch and requires
itself to go red**. A check whose falsification does not go red is reported
:data:`CANNOT_RUN` and NEVER :data:`PASS` — its green is withheld rather than
published. This is ``engine_gate.self_test``'s rule, applied per check: a gate is
only worth its exit code once it has been shown to fail.

The falsification is run on **this run's own world**, not on a fixture, because
the claim is about the check as configured on this machine, over these probes,
right now.

THREE VERDICTS, NEVER TWO
-------------------------
``pass`` / ``finding`` / ``cannot_run`` stay distinct all the way to the exit
code (0 / 1 / 2). Collapsing "could not run" into either direction is the
specific failure this subsystem exists to end: an unobtainable reading reported
as a pass certifies something nobody measured, and reported as a finding raises
a false alarm on the most alarming signal the product has.

SAFETY: THESE CHECKS MUTATE A REAL STORE, AND ONE OF THEM WIPES IT
-------------------------------------------------------------------
The trash check calls :meth:`ProfileManager.wipe_all_profiles`, which is
genuinely irreversible and purges the trash as part of its job. Run against an
operator's real ``~/.persona`` that would destroy every profile they own. So the
harness refuses to run unless ``PERSONA_HOME`` points at a scratch directory
(:func:`require_scratch_home`), and the CLI provisions a throwaway one by
default. The guard is a hard refusal rather than a warning, because the failure
it prevents is unrecoverable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

# --- verdicts ---------------------------------------------------------------

#: The check ran, its falsification went red, and the behaviour held.
PASS = "pass"
#: The check ran, its falsification went red, and the behaviour DID NOT hold.
FINDING = "finding"
#: Nothing was certified. Never a pass, never a finding.
CANNOT_RUN = "cannot_run"

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_RUN = 2

#: Surfaces this module does not observe. Reported in the summary rather than
#: left to the reader to notice, because the ticket's own standard is that an
#: admitted gap beats a surface marked "covered" by a check that could not fail.
UNCOVERED_SURFACES: tuple[tuple[str, str], ...] = (
    (
        "GPU-dependent vectors",
        "the sandbox has no GPU; webgl.unmasked and friends read through a "
        "software rasteriser here, so a difference in them is environmental "
        "rather than a product fact. Out of scope per the ticket.",
    ),
    (
        "proxy transport / exit behaviour",
        "whether a SOCKS handshake succeeds and what the exit IP is belongs to "
        "the network direction. Here a proxy is a thing that gets ASSIGNED and "
        "must stay assigned, never a thing whose handshake is under test.",
    ),
    (
        "certificate trust OUTCOME under a real mTLS launch",
        "cert_trust_status is written by a launch that reaches a real admin "
        "host. This module checks the key-material half ONLY (nothing "
        "decrypted outlives the session). It does not stand up an mTLS "
        "endpoint, so it never reads cert_trust_status and says nothing "
        "about whether that field reports what actually happened.",
    ),
    (
        "engine upgrade / downgrade continuity",
        "owned by engine_gate.py, which already records both sides of a bump "
        "on one runner. Not duplicated here.",
    ),
    (
        "every check on this list runs on FIREFOX only",
        "all 18 scratch profiles here take Context.make_profile's defaults "
        "(os_type=windows, device_type=desktop), and windows+desktop is the "
        "ONE combination that resolves to firefox — every other OS launches "
        "on chromium whatever the stored engine says. The recorder can now "
        "read either engine, so this is a property of the CHECKS' fixtures, "
        "not of the instrument: nothing below has been observed on chromium, "
        "and a pass here says nothing about how a macos, linux or mobile "
        "profile behaves. Widening the fixtures is separate work.",
    ),
    (
        "the chromium arm cannot be recorded on a machine with no automation "
        "session already running",
        "reading a chromium page needs a CDP debugging port, and that port "
        "only exists for a profile launched with ai_control — an "
        "unauthenticated control channel any same-user process can drive "
        "(cdp.py). Launching one so our own check can see better is precisely "
        "the isolation trade the charter refuses, so this module attaches to "
        "a session the operator already opened and REFUSES otherwise. The "
        "refusal is a raised BaselineUnavailable, never an empty reading: two "
        "unreadable recordings compare EQUAL, so a returned blank would be "
        "reported as agreement. Unobserved here reads as CANNOT_RUN (exit 2), "
        "never as a pass.",
    ),
    (
        "THIS lane still needs a display even for a chromium profile, though "
        "the recorder underneath it does not",
        "run_checks calls require_display() as a PREFLIGHT whenever any "
        "SELECTED check has needs_launch=True (4 of the 7: restart-continuity, "
        "two-profile-unlinkability, benign-edit-stability, "
        "trash-restore-and-wipe), and that is decided before any profile's "
        "engine is resolved — the preflight is engine-BLIND by construction. "
        "The recorder's own gate now sits on the firefox arm, immediately "
        "before the launch, so baseline.record_snapshot reads an "
        "already-running chromium session on a headless host with no DISPLAY "
        "at all. This lane is deliberately NOT narrowed to match: every one of "
        "those 4 launching checks uses firefox fixtures (above), which really "
        "do launch, so the preflight refuses nothing today that could have "
        "run, and refusing once up front gives an operator one actionable "
        "message instead of four identical ones. The consequence is stated "
        "rather than left to be discovered: chromium reachability is WIDER in "
        "baseline than in this module, and widening any launching check's "
        "fixtures to chromium means revisiting this preflight in the same "
        "change — left as it is, it would refuse a run that needs no display.",
    ),
    (
        "a FRESH (wipe-then-launch) recording of a chromium-effective profile",
        "fresh=True means 'remove the data directory, then launch from a "
        "known state', and the chromium arm is not allowed to launch (above). "
        "Wiping the directory of an already-running session is corruption, "
        "not a clean start, so it is refused rather than silently downgraded "
        "to a warm read — a document whose provenance claimed a freshness it "
        "did not have would be worse than no document. Chromium recordings "
        "are therefore warm reads of a live session only.",
    ),
)


class BehaviourCheckError(RuntimeError):
    """A check could not be run, with an actionable reason."""


class UnsafeEnvironment(BehaviourCheckError):
    """Refusing to mutate what looks like a real operator store."""


@dataclass
class Outcome:
    """One check's verdict, with the evidence behind it.

    ``falsification`` is not decoration: it is the sentence that says HOW this
    check was shown capable of failing on this run. An outcome carrying an empty
    falsification is never allowed to be a :data:`PASS` — see :func:`run_check`.
    """

    name: str
    surface: str
    status: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    falsification: str = ""
    launches: int = 0

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass(frozen=True)
class Check:
    """A behaviour, the procedure that observes it, and its falsification.

    ``run`` performs the real sequence and returns an :class:`Outcome`.
    ``falsify`` plants a defect of the class ``run`` exists to catch and returns
    a one-line description of the defect it PROVED the check catches. It raises
    :class:`BehaviourCheckError` when the check failed to notice the planted
    defect — which is the check reporting itself untrustworthy.
    """

    name: str
    surface: str
    needs_launch: bool
    run: Callable[["Context"], Outcome]
    falsify: Callable[["Context"], str]


# --- environment ------------------------------------------------------------


def require_scratch_home() -> str:
    """Refuse unless ``PERSONA_HOME`` is a scratch directory we may destroy.

    A hard refusal, not a warning. :meth:`ProfileManager.wipe_all_profiles`
    deletes every profile AND purges the trash, so a run against a real
    ``~/.persona`` is unrecoverable — the one class of mistake that cannot be
    apologised for afterwards.

    The check is on the CONFIGURED home rather than on the environment variable
    alone, because ``core.config`` resolves ``PERSONA_HOME`` at import time: a
    variable set after that import has no effect on where the stores actually
    live, and trusting it would be a guard that reads one path and protects
    another.
    """
    from ...core.config import PERSONA_HOME

    declared = os.environ.get("PERSONA_HOME", "")
    if not declared:
        raise UnsafeEnvironment(
            "refusing to run: PERSONA_HOME is not set, so these checks would "
            "mutate the default store at ~/.persona — and the trash check "
            "WIPES every profile and purges the trash, irreversibly. Run "
            "against a scratch home:\n"
            "    PERSONA_HOME=$(mktemp -d) xvfb-run -a python -m "
            "src.services.verify.behaviour_cli run"
        )
    real_declared = os.path.realpath(os.path.expanduser(declared))
    real_home = os.path.realpath(PERSONA_HOME)
    if real_declared != real_home:
        raise UnsafeEnvironment(
            f"refusing to run: PERSONA_HOME says {real_declared!r} but the "
            f"stores resolved to {real_home!r}. core.config reads the variable "
            "at IMPORT time, so it was set too late to take effect — the "
            "checks would mutate a store the guard is not looking at. Set it "
            "in the environment before starting python."
        )
    default_home = os.path.realpath(os.path.expanduser("~/.persona"))
    if real_home == default_home:
        raise UnsafeEnvironment(
            "refusing to run: PERSONA_HOME points at the DEFAULT store "
            f"({real_home!r}). The trash check wipes every profile and purges "
            "the trash. Point it at a throwaway directory."
        )
    return real_home


def require_display() -> None:
    """A launch check needs a real browser, which needs a display.

    Reuses ``baseline._require_display`` rather than restating the rule, so the
    Xvfb message an operator sees is the same one everywhere. This is the trap
    the ticket calls out: without a display the engine raises, and a run that
    records the failure "correctly" still exits 0 over a near-empty record.

    The message is reused; the EXCEPTION CLASS is not. ``baseline`` raises
    ``BaselineUnavailable``, which is not a :class:`BehaviourCheckError`, and
    this function is called from :func:`run_checks` OUTSIDE any per-check
    handler. Left untranslated it escaped the CLI's ``except`` entirely and
    Python's default unhandled-exception code — 1 — collided with
    :data:`EXIT_FINDING`, reporting "nothing could be measured" in the exact
    words reserved for "the product is broken". That is the one confusion the
    three-way exit split exists to prevent, so the translation happens HERE, at
    the single seam where this module reaches into ``baseline``, rather than by
    making the lower module import this one.
    """
    from .baseline import BaselineUnavailable, _require_display

    try:
        _require_display()
    except BaselineUnavailable as exc:
        # Message verbatim (the Xvfb install line is the actionable part);
        # only the class changes, so the CLI can see it.
        raise BehaviourCheckError(str(exc)) from exc


@dataclass
class Context:
    """The scratch world one run of the checks operates in."""

    home: str
    launches: int = 0
    _manager: object = None

    # -- profile helpers ----------------------------------------------------

    def manager(self):
        """ONE manager for the whole run, cached deliberately.

        ``ProfileManager`` loads the store into an in-memory dict at
        construction, so a second instance created before a profile is added
        never learns about it. Handing out a fresh instance per call made a
        check that did ``pm = ctx.manager()`` and *then* created a profile
        operate on a manager that had never seen it — ``update_profile``
        returned False and the check reported CANNOT RUN for a reason that had
        nothing to do with the product.

        One instance is also the more faithful model: the desktop app holds a
        single manager for its lifetime, so an edit landing on the same object
        that created the profile is the sequence an operator actually performs.
        """
        if self._manager is None:
            from ..profile.manager import ProfileManager

            self._manager = ProfileManager()
        return self._manager

    def make_profile(self, name: str, **kwargs):
        """Create a STORE-BACKED profile and hand back its record.

        Store-backed on purpose, rather than the plain dataclass
        ``baseline.baseline_profile`` builds: ``add_profile`` is what FREEZES
        ``fingerprint_seed_value``, and the frozen seed is precisely the
        property the rename check exists to observe. A hand-built Profile falls
        back to ``crc32(name)`` on every read, so a rename would move its
        identity by construction and the check would be measuring the test
        harness rather than the product.
        """
        from ..profile.proxy_assignment import PROXY_NONE

        pm = self.manager()
        # The proxy is POSITIONAL on add_profile and is a directive, not a
        # plain field, so it is pulled out of kwargs rather than splatted:
        # passing it through **opts collides with the positional argument.
        # A caller naming a proxy means that assignment; silence means DIRECT,
        # and DIRECT has to be SAID (PROXY_NONE) — an empty value reads as
        # UNCHANGED everywhere in this codebase, deliberately.
        proxy = kwargs.pop("proxy", None) or PROXY_NONE
        opts = {
            "os_type": "windows",
            "engine": "firefox",
            "resolution": "1920x1080",
            "device_type": "desktop",
            "search_engine": "duckduckgo",
            # [] is "explicitly cleared", never None ("use the store's
            # defaults") — the reading must not depend on a bookmark store.
            "bookmarks": [],
            "ai_control": False,
        }
        opts.update(kwargs)
        if not pm.add_profile(name, proxy, **opts):
            raise BehaviourCheckError(f"could not create scratch profile {name!r}")
        profile = pm.profiles.get(name)
        if profile is None:
            raise BehaviourCheckError(f"scratch profile {name!r} vanished after create")
        return profile

    def record(self, profile, *, fresh: bool, realms: "tuple[str, ...] | None" = None):
        """Observe a live profile through the EXISTING recorder.

        Every reading in this module comes through here, and what
        ``record_snapshot`` does depends on the engine the profile ACTUALLY
        launches on. On the FIREFOX arm it launches in-process (the firefox
        eval hook is published per-process), reads every probe in the requested
        realms, and tears the session down. On the CHROMIUM arm it does NOT
        launch at all: it attaches to a session the operator already opened in
        automation mode, or it refuses — launching there would mean opening an
        unauthenticated CDP control channel, which isolation forbids. So
        ``self.launches`` counts recordings, not browser starts.

        ``realms`` defaults to ``record_snapshot``'s own default
        (``BASELINE_REALMS`` — window and worker), which is what the continuity
        comparators want: they ask "did THIS profile move?", answered by
        :func:`~.diff.diff_snapshots` over whatever realms both recordings
        carry.

        PS-232. A caller that is going to compare with
        :func:`~.diff.compare_profiles` must pass
        :func:`~.probes.must_differ_realms` instead, because that comparator is
        INVENTORY-driven rather than intersection-driven: it walks every realm
        a must-differ vector declares, and a realm this recording skipped reads
        ABSENT -> unread -> INCONCLUSIVE on every run, which the unlinkability
        check reports as CANNOT_RUN. Recording narrower than the comparator
        walks does not lose a comparison quietly; it converts the whole verdict
        into a permanent refusal.

        DELIBERATELY OPT-IN rather than widened for everyone. Only the two
        cross-profile lanes use ``compare_profiles``; the other four
        (continuity, benign-edit, trash/restore) use ``diff_snapshots``, and
        for them an extra realm is extra surface to enter with no comparison
        depending on it — while :func:`_readings_or_refuse` REFUSES a recording
        carrying any unreadable probe, so a realm that failed to be entered
        would turn those checks' verdicts into refusals over a realm they never
        asked about. Narrow default, explicit widening, no lane changed except
        the one whose comparator changed.
        """
        from .baseline import record_snapshot

        self.launches += 1
        if realms is None:
            return record_snapshot(profile=profile, fresh=fresh)
        return record_snapshot(profile=profile, fresh=fresh, realms=realms)

    def data_dir(self, name: str) -> str:
        from ...core.config import DATA_DIR

        return os.path.join(DATA_DIR, name)


# --- shared evidence helpers ------------------------------------------------


def _readings_or_refuse(snapshot: dict, label: str) -> None:
    """Refuse a comparison built on a recording nobody could read.

    ``diff_snapshots`` compares entries verbatim, so two identically-FAILED
    readings compare EQUAL and are reported as agreement. A continuity check
    could therefore go green off two non-readings — which is the Xvfb trap
    wearing a different hat, and the exact reason this guard exists.
    """
    from .baseline import count_errors

    errors = count_errors(snapshot)
    total = sum(len(realm) for realm in snapshot.get("probes", {}).values())
    if not total:
        raise BehaviourCheckError(
            f"the {label} recording carries no probes at all — nothing was "
            "observed, so nothing can be compared."
        )
    if errors:
        raise BehaviourCheckError(
            f"the {label} recording has {errors} unreadable probe(s) of {total}. "
            "Two identical FAILURES compare equal, so a comparison over them "
            "would report agreement it never observed. Nothing was certified."
        )


def _first_readable(snapshot: dict) -> tuple[str, str]:
    from .engine_gate import _readable_probes

    candidates = _readable_probes(snapshot)
    if not candidates:
        raise BehaviourCheckError(
            "the recording carries no probe with an obtained reading, so this "
            "check cannot be shown to work and its verdict cannot be trusted."
        )
    return candidates[0]


def _summarise(entries: list[dict], limit: int = 6) -> list[str]:
    from .diff import format_diff

    if not entries:
        return []
    text = format_diff(entries)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more line(s)"]
    return lines


# --- the harness ------------------------------------------------------------


def run_check(check: "Check", ctx: Context) -> Outcome:
    """Run one check, but ONLY publish its verdict once it has been falsified.

    The order is deliberate and is the whole design: the falsification runs
    FIRST, and a check that fails to notice its planted defect never reaches its
    own verdict. Running it afterwards would let a check that has quietly
    stopped looking publish a green and have it retracted a line later; running
    it first means an inert check cannot emit a pass at all.

    A :data:`FINDING` is still reported when the falsification fails, because
    "this check is broken" and "this behaviour is broken" are different
    messages — the first is :data:`CANNOT_RUN` and certifies nothing.
    """
    try:
        proven = check.falsify(ctx)
    except BehaviourCheckError as exc:
        return Outcome(
            name=check.name,
            surface=check.surface,
            status=CANNOT_RUN,
            detail=(
                "SELF-TEST FAILED — this check did not catch a defect planted "
                f"on purpose, so nothing it says can be trusted: {exc}"
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return Outcome(
            name=check.name,
            surface=check.surface,
            status=CANNOT_RUN,
            detail=f"the falsification could not run: {type(exc).__name__}: {exc}",
        )

    try:
        outcome = check.run(ctx)
    except BehaviourCheckError as exc:
        return Outcome(
            name=check.name,
            surface=check.surface,
            status=CANNOT_RUN,
            detail=str(exc),
            falsification=proven,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return Outcome(
            name=check.name,
            surface=check.surface,
            status=CANNOT_RUN,
            detail=f"the check raised: {type(exc).__name__}: {exc}",
            falsification=proven,
        )

    outcome.falsification = proven
    # Belt and braces: a PASS with no falsification line is exactly the
    # permanently-green check this module exists to prevent, so it is downgraded
    # rather than trusted.
    if outcome.status == PASS and not outcome.falsification:
        outcome.status = CANNOT_RUN
        outcome.detail = (
            "refusing to report a pass: this check produced no evidence that it "
            "is capable of failing. " + outcome.detail
        )
    return outcome


def run_checks(names: "list[str] | None" = None, *, skip_launch: bool = False):
    """Run the registry (or a named subset) and return ``(outcomes, ctx)``."""
    from .behaviour_checks import CHECKS

    # Name validation comes BEFORE the environment guard, deliberately. A
    # typo'd --check is a mistake in the REQUEST and is worth reporting
    # whatever the environment looks like; refusing it only after the store
    # guard happens to pass would report the two problems in the order they
    # are cheapest to detect rather than the order the caller can act on. It
    # also keeps the "selects nothing, exits 0" hole closed on every path:
    # an unknown name can never silently reduce the selection to empty.
    selected = list(CHECKS)
    if names:
        by_name = {c.name: c for c in CHECKS}
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise BehaviourCheckError(
                f"unknown check(s): {', '.join(unknown)}. Known: "
                f"{', '.join(c.name for c in CHECKS)}"
            )
        selected = [by_name[n] for n in names]

    home = require_scratch_home()
    if skip_launch:
        selected = [c for c in selected if not c.needs_launch]
    elif any(c.needs_launch for c in selected):
        require_display()

    ctx = Context(home=home)
    return [run_check(c, ctx) for c in selected], ctx


def exit_code(outcomes: "list[Outcome]") -> int:
    """0 all passed / 1 a finding / 2 something could not run.

    CANNOT_RUN outranks FINDING: if any check could not be trusted, the run's
    headline must not be a confident finding count over a partial world.
    """
    if any(o.status == CANNOT_RUN for o in outcomes):
        return EXIT_CANNOT_RUN
    if any(o.status == FINDING for o in outcomes):
        return EXIT_FINDING
    return EXIT_OK


def format_report(outcomes: "list[Outcome]", ctx: "Context | None" = None) -> str:
    """The operator-facing report, including what was NOT covered."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("BEHAVIOURAL CHECKS — what the product does, not what a site sees")
    lines.append("=" * 72)
    for o in outcomes:
        badge = {PASS: "PASS", FINDING: "FINDING", CANNOT_RUN: "CANNOT RUN"}[o.status]
        lines.append("")
        lines.append(f"[{badge}] {o.name}")
        lines.append(f"  surface: {o.surface}")
        lines.append(f"  {o.detail}")
        for ev in o.evidence:
            lines.append(f"    | {ev}")
        if o.falsification:
            lines.append(f"  shown capable of failing: {o.falsification}")
        else:
            lines.append("  shown capable of failing: NO — verdict withheld")

    passed = sum(1 for o in outcomes if o.status == PASS)
    findings = [o for o in outcomes if o.status == FINDING]
    blocked = [o for o in outcomes if o.status == CANNOT_RUN]
    lines.append("")
    lines.append("-" * 72)
    lines.append(
        f"{passed} passed, {len(findings)} finding(s), {len(blocked)} could not run"
        + (f", {ctx.launches} browser launch(es)" if ctx else "")
    )
    if findings:
        lines.append("")
        lines.append("FINDINGS — hand off with the evidence above; this module")
        lines.append("reports, it does not fix:")
        for o in findings:
            lines.append(f"  * {o.name}: {o.surface}")

    lines.append("")
    lines.append("NOT COVERED BY THIS MODULE (stated, not implied):")
    for surface, why in UNCOVERED_SURFACES:
        lines.append(f"  * {surface} — {why}")
    return "\n".join(lines)


__all__ = [
    "CANNOT_RUN",
    "EXIT_CANNOT_RUN",
    "EXIT_FINDING",
    "EXIT_OK",
    "FINDING",
    "PASS",
    "UNCOVERED_SURFACES",
    "BehaviourCheckError",
    "Check",
    "Context",
    "Outcome",
    "UnsafeEnvironment",
    "exit_code",
    "format_report",
    "require_display",
    "require_scratch_home",
    "run_check",
    "run_checks",
]
