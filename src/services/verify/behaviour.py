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
        "every check on this list runs on FIREFOX only — WITH ONE EXCEPTION",
        "all but one of the scratch profiles here take Context.make_profile's "
        "defaults (os_type=windows, device_type=desktop), and windows+desktop "
        "is the ONE combination that resolves to firefox — every other OS "
        "launches on chromium whatever the stored engine says. The recorder "
        "can now read either engine, so this is a property of the CHECKS' "
        "fixtures, not of the instrument. THE EXCEPTION is "
        "no-process-survives-a-closed-session (PS-347), which launches "
        "os_type=linux and therefore CHROMIUM on purpose: it counts surviving "
        "processes, and the leak it guards is a property of the WRAPPER, "
        "multi-process launch — a direct single-process launch does not leak "
        "on terminate() at all, so the same measurement taken on the firefox "
        "fixtures would be vacuous. It observes ONE engine on ONE platform "
        "(chromium/Linux) and says nothing about the other arm; every other "
        "check below still says nothing about how a macos, linux or mobile "
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
        "SELECTED check has needs_launch=True (5 of the 8: restart-continuity, "
        "two-profile-unlinkability, benign-edit-stability, "
        "trash-restore-and-wipe, no-process-survives-a-closed-session), and "
        "that is decided before any profile's "
        "engine is resolved — the preflight is engine-BLIND by construction. "
        "The recorder's own gate now sits on the firefox arm, immediately "
        "before the launch, so baseline.record_snapshot reads an "
        "already-running chromium session on a headless host with no DISPLAY "
        "at all. This lane is deliberately NOT narrowed to match: four of "
        "those 5 launching checks use firefox fixtures (above), which really "
        "do launch, and the fifth is a CHROMIUM launch that needs a display "
        "just as much — it starts a real headful browser rather than "
        "attaching to one — so the preflight refuses nothing today that could "
        "have run, and refusing once up front gives an operator one actionable "
        "message instead of five identical ones. The consequence is stated "
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
    (
        "the LAUNCH-PERIMETER INVENTORY observes the SOURCE, never a running "
        "launch",
        "launch-perimeter-inventory (PS-355) walks a traced launch surface "
        "with an AST parser and compares what it finds against "
        "PERIMETER_ARTIFACTS. Three consequences it does NOT hide. (1) A write "
        "reached only DYNAMICALLY — through a built attribute name, an exec, "
        "or a third-party library's own file handling — is invisible to it, "
        "because a parser sees the shape of a call and not the calls that "
        "actually happen. (2) Its population is LAUNCH_SURFACE, the modules a "
        "real traced firefox launch entered plus env_policy (which the forked "
        "child reaches, where the tracer does not follow); a new "
        "out-of-perimeter write in a module NOT on that list is not seen at "
        "all, and widening the list is the fix rather than a reason to read "
        "the check more generously. (3) The INVENTORY ITSELF was measured on "
        "ONE platform — Linux, firefox, one launch — so its platform column is "
        "a claim about where each artifact is EXPECTED, verified against the "
        "guards in the source, and not a reading taken on Windows or macOS. "
        "What the check does guarantee is narrow and real: the tree cannot "
        "grow a statically-visible out-of-perimeter write, or lose an "
        "enumerated one, without this going red and naming it.",
    ),
)


# --- known positions: a red that has always been red, one vector at a time ---
#
# PS-380. A KNOWN POSITION is a single (realm, probe) pair whose collision is a
# RECORDED, PUBLISHED fact handed to another ticket as product work. It is
# EXCLUDED FROM THE COMPARISON AND REPORTED, never run-and-then-forgiven.
#
# ⭐ WHY THIS EXISTS AT ALL — IT SHRINKS AN EXCLUSION, IT DOES NOT CREATE ONE.
# `two-profile-unlinkability` is the only check that observes Level 2 of the
# bar (mutual unlinkability), and the launch lane's venue
# (`.github/scripts/run_launch_behaviour_checks.py`) excludes it WHOLE-CHECK,
# for a reason that is correct and must not be reversed: the check reports a
# FINDING on the firefox engine this project ships, because canvas 2D is not
# spoofed there, so requiring it would make that gate permanently red — "the
# same defect as permanently green wearing the other colour", in that file's
# own words.
#
# But the check compares FIVE must-differ pairs and only TWO of them collide.
# Measured live at PS-380 on the lane's own engine, firefox-20 (Personium,
# Firefox 151.0 build 20260817150018), two runs of two fresh profiles each:
#
#     child_frame/webgl.readback.childFrame   DIFFERS
#     window/audio.digest                     DIFFERS
#     window/canvas.readback                   COLLIDING  digest 4242351214
#     window/webgl.readback                    DIFFERS
#     worker/canvas.readback                   COLLIDING  digest 4242351214
#
# The three that differ are exactly the vectors persona ships Firefox spoofs
# for (`_install_spoof("webgl", ...)` and `_install_spoof("audio", ...)` in
# invisible_launch.py; canvas has no firefox arm). So a whole-check exclusion
# taken to avoid ONE known red also stops anyone watching the vectors the
# product actively defends: if either spoof silently stopped being installed,
# two profiles would share a WebGL readback and an audio digest and NOTHING in
# CI would say so. This structure is what lets the lane watch those three
# while the known canvas collision stays visible rather than silenced.
#
# ⛔ THE FOUR RULES, AND EACH ONE IS A GUARD AGAINST A SPECIFIC WAY THIS
# AFFORDANCE COULD ROT INTO A WAIVER LIST:
#
# 1. IT MAY ONLY EVER SHRINK AN EXISTING EXCLUSION, NEVER CREATE ONE. A vector
#    enters this set solely by moving OUT of a check-level omission that
#    already exists — never by moving out of a passing lane. A check that is
#    green today may not buy itself a known position tomorrow.
#    ⭐ ENFORCED PER-PAIR, because the rule is stated per-pair: the retired
#    omission records WHICH PAIRS its reason rested on
#    (`test_ps336_launch_behaviour_venue.RETIRED_OMISSION_PAIRS`, re-derived
#    from the committed corpus rather than trusted), and a pin outside that set
#    is refused. A check-granular version of this rule goes VACUOUS the moment
#    the check is retired — every future pin satisfies it for free — which is
#    what round 1 shipped and what `test_rule_1_REFUSES_a_pin_on_a_pair_the_
#    omission_never_covered` now keeps failing-capable.
# 2. EXCLUDED VISIBLY, NEVER FORGIVEN. The pair is removed from the comparison
#    BEFORE a verdict exists, and it is named in the outcome's detail and
#    evidence on every run. Nothing adjudicates a FINDING down to a PASS:
#    `run_behaviour_checks.py`'s asymmetry ("every correction here can only
#    ever move a verdict TOWARDS 2, never greener") is untouched, because no
#    correction happens at all. Excluding is visible in the report; forgiving
#    is invisible, which is why only the first is admissible.
# 3. THE PIN IS A READING, NOT A VECTOR NAME. An entry carries the DIGEST the
#    collision was recorded at, on the BUILD it was recorded on. A pair that
#    collides on a DIFFERENT value is NOT the known position: it rejoins the
#    live comparison and is reported as the finding it is.
#    ⚠️ A PAIR THAT HAS STOPPED COLLIDING IS THE OPPOSITE CASE AND IS NOT A
#    FINDING. Its premise expired because the PRODUCT GOT BETTER, so it
#    rejoins the live comparison and gates normally, the dead pin is REPORTED
#    so it gets deleted, and THE VERDICT IS UNAFFECTED — the lane stays green.
#    Failing on it would turn the day PS-2 ships its fix into a red, which is
#    "permanently red is as bad as permanently green" arriving by the back
#    door. The prompt to delete a dead pin is therefore a REPORT LINE ON A
#    GREEN RUN, plus one test that goes red on its own evidence
#    (`test_a_firefox_canvas_arm_is_what_PROMPTS_deleting_the_pins`) — never
#    this lane going red.
# 4. EVERY ENTRY CITES A FILE AND A VERBATIM QUOTE, on the model of
#    `tests/test_engine_masking_matrix.py`'s RECORDED_REASON_SOURCES. A
#    recorded decision whose record has been deleted or reworded is an
#    unexplained absence wearing a reason's clothes, so the quote is re-read at
#    its source by a test rather than trusted.
#
# ⚠️ THE ci.yml ANTI-ALLOWLIST STANCE IS NOT BREACHED, AND THE DISTINCTION IS
# THE OBJECT. That passage ("NO ALLOWLIST IS CONFIGURED, ON ANY PLATFORM, AND
# THAT IS DELIBERATE … a floor becomes permanent: the 22 would stop being a
# debt anyone can see and start being scenery") governs a TEST-SUITE FAILURE
# FLOOR: an unbounded count of anonymous failures, where the allowlist's job is
# to stop anyone having to look. This is the opposite shape — one named pair,
# one pinned digest, one cited reading, REPORTED ON EVERY RUN, which goes red
# the moment the reading changes in either direction. The thing ci.yml refuses
# is a mechanism that makes a debt invisible; this one is the mechanism that
# keeps this debt legible while un-blinding three vectors beside it.
#
# ⚠️ AND THE NOISE-SOURCE TEST IS ANSWERED RATHER THAN SIDESTEPPED. A tolerance
# mechanism transplanted from a gate whose noise is a third party we do not
# control, onto a gate whose finding is our own arithmetic over our own two
# profiles, stops absorbing drift and starts silencing a true leak. That rule
# is why this is NOT the checker matrix' waiver: nothing here tolerates
# variance, nothing here absorbs noise, and no reading is re-classified. The
# collision is still reported as a collision on every run; what changes is that
# it no longer takes FOUR OTHER PAIRS with it into a whole-check exclusion. A
# permission to HIDE is what that rule forbids, and this grants none — which is
# also why rule 1 exists, so the next carve-out cannot reach for it.


@dataclass(frozen=True)
class KnownPosition:
    """One (realm, probe) pair whose collision is already recorded elsewhere.

    ``digest`` is the reading the pair was recorded colliding AT, and
    ``build`` the engine build it was recorded ON — the pin is a measurement,
    not a vector name (rule 3 above). ``reason_path`` / ``reason_quote`` cite
    where the decision lives, verbatim (rule 4).
    """

    realm: str
    probe_id: str
    digest: object
    build: str
    owner: str
    reason_path: str
    reason_quote: str

    @property
    def pair(self) -> str:
        return f"{self.realm}/{self.probe_id}"


#: The known positions, one entry per pair. Listed rather than derived, because
#: a derived set would grow silently and each entry has to be argued.
#:
#: ⛔ DO NOT ADD AN ENTRY TO MAKE A RED LANE GREEN. Rule 1 above is enforced by
#: `tests/test_ps380_known_position.py`, which refuses a pin on any pair that
#: the check's own recorded omission did not rest on — PER PAIR, not per check
#: (`RETIRED_OMISSION_PAIRS`, itself re-derived from the committed corpus).
KNOWN_POSITIONS: tuple[KnownPosition, ...] = (
    KnownPosition(
        realm="window",
        probe_id="canvas.readback",
        digest=4242351214,
        build="firefox-20",
        owner="PS-2 (readings/ps135-2026-08-24/EVIDENCE.md §7.3 hands it over)",
        reason_path="readings/ps135-2026-08-24/EVIDENCE.md",
        reason_quote=(
            "two profiles agree, so the two-profile unlinkability check will"
        ),
    ),
    KnownPosition(
        realm="worker",
        probe_id="canvas.readback",
        digest=4242351214,
        build="firefox-20",
        owner="PS-2 (readings/ps135-2026-08-24/EVIDENCE.md §7.3 hands it over)",
        reason_path="readings/ps135-2026-08-24/EVIDENCE.md",
        reason_quote=(
            "report **COLLIDING** on `window` and `worker` and go to "
            "**FINDING** on that"
        ),
    ),
)


def known_positions_for(build: "str | None") -> tuple[KnownPosition, ...]:
    """The known positions recorded on ``build``, in inventory order.

    ⛔ THE BUILD AXIS IS HONOURED RATHER THAN STATED-AND-IGNORED. The corpus
    shows the collided digest MOVING with the engine: the same profile reads
    4242351214 on firefox-20 and 2735004646 on firefox-21/25/26
    (``readings/ps290-2026-09-03/artifacts/*/fingerprint-{before,after}.json``).
    So an entry pinned to one build says nothing about another, and on an
    unrecognised build this returns NOTHING — every pair is compared and a
    collision is reported as the finding it is. A baseline that followed the
    engine wherever it went would be a waiver, not a pin: the safe direction
    for an unknown build is to measure it, not to excuse it.
    """
    if not build:
        return ()
    return tuple(kp for kp in KNOWN_POSITIONS if kp.build == build)


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


# --- the chromium singleton socket budget -----------------------------------
#
# ⭐ MEASURED TO THE BYTE ON A REAL ENGINE, AND IT IS A HARD WALL RATHER THAN
# A STYLE RULE. Chromium's process singleton binds a UNIX socket, and a UNIX
# socket address is `sun_path[108]` — 107 usable bytes plus the NUL. Over that,
# the engine does not warn or degrade: it exits FATAL
# "chrome/browser/process_singleton_posix.cc:313] Socket path too long" a few
# seconds into the launch. From outside that reads as a tree that started (peak
# 5) and then vanished — a launch that never happened, which is exactly the
# reading a survivor-counting check must never take a zero from.
#
# persona pins the browser child's scratch directory INSIDE the profile
# (`env_policy.browser_child_tmpdir`, PS-129), so the socket lands at:
#
#     <PERSONA_HOME>/persona_data/<profile>/.persona-tmp/
#         org.chromium.Chromium.XXXXXX/SingletonSocket
#
# Everything except the home and the profile name is fixed, and that fixed
# part is what `_SINGLETON_SOCKET_FIXED_COST` counts.
#
# THE BOUNDARY, ISOLATED ON THIS ENGINE (personium-152.0.7977.75, one variable
# — the home path's length — moved by ONE byte between the two arms):
#
#     home len 25 -> socket 107 bytes -> launched, tree settled at 11
#     home len 26 -> socket 108 bytes -> FATAL, peak 5 then 0
#
# ⛔ THIS IS NOT AN OBSERVATION TO BE RE-DERIVED AT EACH CALL SITE. It lives
# here, once, because three consumers need the SAME number: the check that
# names a profile, the CLI that provisions a default home, and the test that
# refuses to let either drift. A budget restated in three places is a budget
# that is wrong in two of them.

#: Usable bytes in a UNIX socket address — ``sizeof(sun_path)`` is 108 and the
#: last byte is the terminator.
SUN_PATH_LIMIT = 107

#: Everything in the singleton socket path that is NOT the home or the profile
#: name: ``/persona_data/`` + ``/.persona-tmp`` + the engine's own
#: ``/org.chromium.Chromium.XXXXXX/SingletonSocket`` (45 bytes, its mkdtemp
#: suffix being a fixed six characters).
_SINGLETON_SOCKET_FIXED_COST = (
    len("/persona_data/")
    + len("/.persona-tmp")
    + len("/org.chromium.Chromium.XXXXXX/SingletonSocket")
)


def singleton_socket_length(home: str, profile_name: str) -> int:
    """How many bytes chromium's singleton socket path takes for this pair.

    The number the engine measures against ``sun_path``, computed rather than
    guessed — see this section's comment for the isolated boundary run.
    """
    return len(home) + len(profile_name) + _SINGLETON_SOCKET_FIXED_COST


def singleton_socket_is_bound() -> bool:
    """Whether this platform's engine binds a UNIX socket for its singleton.

    ⛔ THE LIMIT IS A POSIX FACT, NOT A UNIVERSAL ONE, and the caught mistake
    was applying it everywhere. The engine's own FATAL names the file that
    enforces it — ``chrome/browser/process_singleton_posix.cc:313`` — because
    ``sun_path`` is a property of the UNIX-domain socket that arm binds.
    Windows' process singleton is a NAMED MUTEX plus a hidden message window;
    it binds no socket, so there is no 107-byte wall to measure and MAX_PATH
    (260, or effectively unbounded with long paths enabled) is a different
    constraint entirely.

    Measured, not reasoned: refusing on the Windows CI runner rejected a
    perfectly launchable ``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp`` — a
    guard inventing a failure on a platform whose engine cannot have it, which
    is worse than the defect it was written for.
    """
    from ...core import platform as _platform

    return not _platform.IS_WINDOWS


def profile_name_budget(home: str) -> int:
    """The longest profile name that still fits under ``home``.

    Negative when the home ALONE has already spent the budget, which is a real
    answer rather than an error: no profile name, however short, launches under
    such a home, and a caller that clamps this at zero would hide exactly that.

    ⚠️ THIS IS THE POSIX ARITHMETIC AND IT IS ANSWERED ON EVERY PLATFORM, on
    purpose — it is the pure calculation, and the tests pin it as one. Whether
    the answer BINDS is :func:`singleton_socket_is_bound`'s question, asked by
    the callers that refuse.
    """
    return SUN_PATH_LIMIT - len(home) - _SINGLETON_SOCKET_FIXED_COST


#: The prefix a provisioned scratch home carries. DELIBERATELY TERSE, and the
#: terseness is load-bearing rather than a style preference: every byte here is
#: a byte taken off the profile-name budget above. The previous
#: ``persona-behaviour-`` spent 18 of the 35 bytes ``/tmp`` leaves, which put
#: the default invocation 6 bytes OVER the wall and made the launch-backed
#: checks structurally incapable of reaching green under it. Recognisable
#: enough for an operator to spot a stray directory; short enough to launch.
_SCRATCH_PREFIX = "pb-"


def default_scratch_home(min_name_budget: int = 0) -> str:
    """Provision a throwaway ``PERSONA_HOME`` that a browser can actually
    launch under.

    ⭐ THE LENGTH IS A CORRECTNESS PROPERTY OF THIS DIRECTORY, NOT A DETAIL OF
    WHERE IT LANDS. ``tempfile.mkdtemp()`` alone honours ``TMPDIR``, and the
    bases real runners hand it are long: a GitHub runner's
    ``/home/runner/work/_temp`` is 23 bytes and macOS's ``/var/folders/…`` is
    53, either of which spends the whole budget before a profile is named. So
    the base is CHOSEN — shortest writable candidate first — rather than
    accepted, and the result is then VERIFIED against the budget instead of
    assumed to fit.

    ``min_name_budget`` is what the caller needs left over: the longest profile
    name any check will create under this home. A home that cannot supply it is
    a REFUSAL naming ``--home``, never a silent return — the failure it
    replaces was chromium exiting FATAL several seconds into a launch, which
    every watcher reads as a browser that started and vanished.
    """
    import tempfile

    candidates = [tempfile.gettempdir()]
    if os.name == "posix":
        # Almost always the shortest thing available, and almost always
        # writable — but ASKED FOR rather than assumed, because a hardened
        # runner can have neither.
        candidates.append("/tmp")

    bases = sorted(
        {c for c in candidates if os.path.isdir(c) and os.access(c, os.W_OK)},
        key=len,
    )
    if not bases:
        raise UnsafeEnvironment(
            "refusing to run: no writable temporary directory was found, so "
            "no scratch PERSONA_HOME could be provisioned. Pass --home with a "
            "directory this user may create and destroy."
        )

    home = tempfile.mkdtemp(prefix=_SCRATCH_PREFIX, dir=bases[0])
    budget = profile_name_budget(home)
    # ⛔ ONLY WHERE THE ENGINE ACTUALLY BINDS A SOCKET. On Windows the
    # singleton is a named mutex and this arithmetic describes nothing, so
    # refusing there invents a failure on a platform that cannot have it — as
    # the Windows CI runner demonstrated, rejecting a launchable
    # C:\Users\RUNNER~1\AppData\Local\Temp at -13 bytes.
    if singleton_socket_is_bound() and budget < min_name_budget:
        # ⭐ THE REFUSAL TAKES ITS OWN DIRECTORY WITH IT. The home has to be
        # CREATED before it can be measured — mkdtemp's suffix is part of the
        # length, and re-deriving it here would duplicate a stdlib internal
        # that is free to change. So the measurement happens on the real path
        # and the refusal cleans up after itself, because this module's rule is
        # that a check must not leave the machine dirtier than it found it
        # (`_sweep_group`'s docstring is the argument). Unswept, every refusal
        # left an empty scratch directory behind — small in bytes, unbounded on
        # a self-hosted or cached runner, and precisely the shape this harness
        # exists to refuse in the product.
        import shutil

        shutil.rmtree(home, ignore_errors=True)
        raise UnsafeEnvironment(
            f"refusing to run: the scratch home {home!r} leaves only {budget} "
            f"byte(s) for a profile name, and the checks need {min_name_budget}"
            ". Chromium's process singleton binds a UNIX socket under the "
            f"profile, and its path may not exceed {SUN_PATH_LIMIT} bytes — "
            "over that the engine exits FATAL 'Socket path too long' seconds "
            "into the launch, which looks from outside like a browser that "
            "started and vanished. Pass --home with a shorter path (on this "
            f"machine the temporary directories available are {bases!r})."
        )
    return home


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

    # ⭐ KNOWN POSITIONS ARE IN THE REPORT, ALWAYS — that is what makes them an
    # EXCLUSION rather than a WAIVER (see KNOWN_POSITIONS, rule 2). A forgiven
    # pair would be invisible here; an excluded one is named with its pinned
    # digest, its build, the reading it rests on and the ticket that owns the
    # fix, so a reader can never mistake a green for "every pair differed".
    if KNOWN_POSITIONS:
        lines.append("")
        lines.append(
            "KNOWN POSITIONS — single (realm, probe) pairs whose collision is "
            "already RECORDED and owned elsewhere. Each is EXCLUDED from the "
            "cross-profile comparison and REPORTED here, never adjudicated "
            "down to a pass. A pin only ever removes an EXACTLY-MATCHING "
            "recorded collision; every other reading rejoins the comparison "
            "and gates normally. So a pair colliding at a DIFFERENT reading "
            "is a FINDING (the pin does not describe it), while a pair that "
            "has STOPPED colliding is NOT — that is the product improving: "
            "the pair gates normally again, the dead pin is reported here so "
            "it gets deleted, and the verdict is unaffected:"
        )
        for kp in KNOWN_POSITIONS:
            lines.append(
                f"  * {kp.pair} — collides at {kp.digest!r} on {kp.build}; "
                f"owned by {kp.owner}; recorded in {kp.reason_path}"
            )
    return "\n".join(lines)


__all__ = [
    "CANNOT_RUN",
    "EXIT_CANNOT_RUN",
    "EXIT_FINDING",
    "EXIT_OK",
    "FINDING",
    "KNOWN_POSITIONS",
    "PASS",
    "SUN_PATH_LIMIT",
    "UNCOVERED_SURFACES",
    "BehaviourCheckError",
    "Check",
    "Context",
    "KnownPosition",
    "Outcome",
    "UnsafeEnvironment",
    "default_scratch_home",
    "exit_code",
    "format_report",
    "known_positions_for",
    "profile_name_budget",
    "require_display",
    "require_scratch_home",
    "run_check",
    "run_checks",
    "singleton_socket_length",
]
