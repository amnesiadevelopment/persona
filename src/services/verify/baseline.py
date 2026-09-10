"""The engine-continuity baseline: a pinned profile, recorded and compared.

`verify` can already observe a live profile and diff two observations. What was
missing was a *reference* — a recorded "before" that lives in the repository —
and something that produces the "after" and compares them. This module is both.

The question it answers is narrow and operational:

    does a profile still look exactly as it did on the previous engine?

The engine moves on its own (``.github/workflows/engine-autoupdate.yml`` runs
daily and commits a new pin to ``main`` unattended), and an engine change
replaces the layer half the masking lives in. Comparing a recorded reading
against a committed one is how that becomes visible as a diff rather than as a
ban.

WHY THIS LAUNCHES THE BROWSER ITSELF
------------------------------------
``cli record`` deliberately attaches to an ALREADY-RUNNING profile. For a
baseline that is not enough, and the reason is mechanical rather than stylistic:
``register_ff_eval`` publishes a firefox session's eval hook into an in-memory,
**per-process** dict, and on Linux a launch FORKS. A session launched by the
desktop app therefore publishes its hook inside a process this code cannot
reach. So the baseline command launches the profile in-process (a thread — see
``InvisibleProcess(in_process=True)``), records through the hook it can now see,
and tears the session down. Launch and record must be one process; they are.

THE CHROMIUM ARM DOES NOT LAUNCH
--------------------------------
The paragraph above is the FIREFOX arm's reason and it does not generalise. A
profile that launches on chromium is read by ATTACHING to a session the
operator already started in automation mode (``transport``'s CDP adapter), and
is refused otherwise. It is never launched here.

That asymmetry is a security decision, not an oversight. Reading a chromium
page needs a CDP debugging port, and that port only exists for a profile
launched with ``ai_control`` — which ``cdp.py`` describes in its own words as
an **unauthenticated control channel any process running as this user can
drive**. Launching our own port so that our own check can see better is the
canonical instance of the thing the project's isolation invariant refuses, and
that invariant is not a trade to be weighed. So nothing here launches a debug
port, sets ``ai_control``, or persists it on a stored record.

The consequence is stated rather than worked around: on a machine with no such
session running, the chromium arm CANNOT RUN. That is the honest answer and it
is deliberately not a pass — an arm that returned an empty or errored reading
instead would compare EQUAL against another empty reading and be reported as
agreement, manufacturing exactly the green these checks exist to withhold.
Every failure on this arm is therefore a raised ``BaselineUnavailable``, never
a returned document. ``behaviour._readings_or_refuse`` is the second net under
the same trap.

WHY THE PROFILE IS BUILT IN CODE AND NOT LOOKED UP
--------------------------------------------------
A snapshot is only a reference if the ONLY thing that can differ between two
recordings is the engine. Every input that feeds the derived identity is pinned
here as a literal, not inherited from whatever the operator happens to have in
their store:

* the **name** — because ``Profile.fingerprint_seed`` is ``crc32(name)``, so
  pinning the name is what pins the seed, and with it the whole derived identity;
* ``os_type`` / ``device_type`` / ``engine`` / ``resolution`` — explicit, so a
  preset is never picked from the seed behind our back;
* **no proxy** — a proxy would make locale and timezone follow a geo lookup, and
  a variance the network introduced would read exactly like a variance the
  engine introduced. With no proxy the launcher pins ``en-US`` +
  ``America/New_York`` (see ``launch_policy``), which is deterministic;
* **no bookmarks and no certificate** — ``bookmarks=[]`` is "explicitly cleared"
  rather than ``None`` ("give me the store's defaults"), which would make the
  reading depend on the operator's bookmark store.

The profile is a plain dataclass and is never written to the profile store: the
baseline identity cannot be edited by a human out from under the artifact.

BOTH REALMS, ALWAYS
-------------------
``window`` and ``worker`` are both recorded and neither is optional. A spoof
that lands on the page but not inside a Web Worker is the historically
load-bearing leak, and it is invisible unless the worker realm is read.

WHAT A CLEAN DIFF DOES NOT MEAN
-------------------------------
``diff_snapshots`` compares entries verbatim, so two identically-FAILED readings
(``{"error": X}`` on both sides) compare equal and are reported as agreement. A
comparison could therefore go green off two non-readings. :func:`check` refuses
that: it counts errors on both sides and fails when either side has any, so a
pass means "every probe was read AND nothing moved", never "nothing could be
read on either side". See :class:`BaselineResult.ok`.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ...models.profile import Profile
from .diff import diff_snapshots, format_diff, inconclusive_count
from .probes import WINDOW, WORKER
from .runner import run_probes
from .snapshot import build_snapshot, load, quote_path

# --- the pinned baseline profile -------------------------------------------

# Pinning the NAME is what pins the fingerprint seed: Profile.fingerprint_seed
# is crc32(name). Changing this string silently changes the whole derived
# identity and invalidates the committed artifact, so it is a constant and the
# artifact records it.
BASELINE_PROFILE_NAME = "persona-fingerprint-baseline"

BASELINE_OS_TYPE = "windows"
BASELINE_DEVICE_TYPE = "desktop"
BASELINE_ENGINE = "firefox"
# Explicit, never "auto": "auto" would pick a preset from the seed, which is
# reproducible but leaves the recorded geometry implicit in a lookup table.
BASELINE_RESOLUTION = "1920x1080"
BASELINE_SEARCH_ENGINE = "duckduckgo"
BASELINE_REALMS: tuple[str, ...] = (WINDOW, WORKER)

# The main-window geometry the RECORDER pins before every launch, in DEVICE px
# (xulstore.json's own unit — see `_seed_window_size`'s docstring for the live
# proof of that unit).
#
# WHY THIS EXISTS. `window.innerSize` is a WINDOW_ONLY probe that is neither
# seed-derived nor an identity vector — its own note in `probes.py` says
# "Window chrome geometry; differs with a resized window, not a spoof change."
# Nothing in the recording path used to fix it, so the recorded value was
# whatever default the ENGINE happened to choose. `_seed_window_size` looks like
# it would, but it cannot: `_work_area()` returns (0, 0) on everything that is
# not Windows and the helper bails on `if not (aw and ah)`, so on the
# `ubuntu-24.04` runner the gate records under `xvfb-run` with no seeding at all.
#
# firefox-21 changed that default (1152 -> 1280 CSS px) and every later build
# kept the new value, so `engine_gate compare` reported a moved probe on every
# bump from firefox-20 onwards — permanently, for no security reason, beside two
# genuine `canvas.readback` findings. `engine_gate.py` states the cost in its own
# words: "a gate that is always red is a gate people learn to ignore, which is
# worse than no gate." Pinning makes the geometry an INPUT to the recording
# rather than an observation OF it, so the gate stops comparing two engines'
# private defaults and the two canvas lines stand alone.
#
# ⛔ THIS DOES NOT BLIND ANYTHING. The probe still reports what it reads, still
# diffs, and a genuine re-roll of window geometry still moves it — which is
# exactly the property `behaviour_checks.py:20-65` protects when it refuses to
# solve the same transient with an ignore list. What changes is that both sides
# of an engine comparison are now sized by US, so a difference between them is
# attributable to the engine.
#
# ⚠️ AND THE PIN IS SCOPED TO RECORDINGS THAT ASK FOR IT — read that claim with
# this bound, because round 1 made it while the pin was unconditional and the
# two sentences could not both be true. `_pin_recording_window` DEFERS to an
# existing `xulstore.json`, so:
#   * a FRESH recording (the gate's, always) is pinned — the rmtree removed the
#     file, so the pin always writes, and both sides of a comparison are ours;
#   * a WARM recording (`--reuse-profile`, and the launch-backed behavioural
#     checks) keeps the profile's own persisted geometry.
# That second line is what makes the first claim honest: `behaviour_checks.py`'s
# restart-continuity model is a question ABOUT a profile's persisted state, so a
# recorder that rewrote it on every launch would be writing over the thing being
# observed — blinding, in that lane, exactly what this comment says it does not.
#
# WHY THESE NUMBERS. Two constraints, both from code:
#   * It must stay inside the baseline profile's spoofed screen
#     (BASELINE_RESOLUTION, 1920x1080 CSS px). A CSS innerWidth larger than
#     `screen.width` is the #216 impossibility `_seed_window_size` caps against
#     — no real un-maximized window is wider than its own screen. 1280x800
#     device px at dpr 1.0 leaves the content area far below 1920x1080 with room
#     to spare for window chrome at any plausible dpr the runner reports.
#
#     ⚠️ 800 IS THE OUTER WINDOW, NOT THE CONTENT AREA — stated because the AC7
#     test asserts `800 < 1080` on the DEVICE figure while the invariant it
#     stands for is about the CSS `innerHeight` a page reads. Live on the runner,
#     an 800px window records `innerHeight: 687`: window chrome takes ~113px. So
#     the real headroom is larger than the test's arithmetic suggests, and the
#     assertion holds comfortably either way — but anyone RAISING this constant
#     must reason from 687-per-800, not from 800, or they will size the window
#     against the wrong number.
#   * It must be an ORDINARY desktop window, because the recorded reading is a
#     reference an operator's profile is compared against. 1280x800 is the
#     engine's own post-firefox-21 default width beside a common laptop height,
#     not a synthetic number that would itself read as a tell.
#
# The value is named HERE, once, rather than written as a literal at the call
# site, so what the gate pins is readable in one place (AC1).
BASELINE_WINDOW_SIZE: tuple[int, int] = (1280, 800)

# The two channels the recorder can speak, canonical and lowercase.
#
# DELIBERATELY NOT `BASELINE_ENGINE`, which names a different thing: that is the
# pinned baseline PROFILE's stored engine, and reusing it for routing would
# conflate "what the artifact's profile is" with "which channel did the read".
# Those came apart in exactly the way this ticket exists to fix.
#
# These are what a snapshot's `engine` header is stamped with, because they name
# THE CHANNEL THAT DID THE READING and not the profile's stored claim. A stored
# record is an ASSERTION; a transport is an OBSERVATION. Pinned against
# `_FirefoxTransport.engine` / `_ChromiumTransport.engine` by
# `test_both_arms_report_the_engine_their_own_transport_reports`, so these
# spellings cannot drift from the adapters they stand for.
_FIREFOX_CHANNEL = "firefox"
_CHROMIUM_CHANNEL = "chromium"

# Probes whose value reads through a HOST facility (GPU/driver stack, installed
# font metrics) rather than being derived from the seed. Pinning the profile
# pins everything else; these can still differ between two machines running the
# SAME engine, so a red run on unfamiliar hardware may be host variance rather
# than engine drift.
#
# Recorded as NAMES only, and deliberately so: the point is to tell whoever is
# reading a diff which lines to distrust, and an actual host identifier (GPU
# string, driver version) would make the artifact's bytes depend on the machine
# that recorded it — the opposite of what a byte-stable reference needs.
ENV_SENSITIVE_PROBES: tuple[str, ...] = (
    # PS-135. Listed for the SAME reason `fonts.measureText` below is, only
    # more so: that probe reads glyph ADVANCE WIDTHS, while this one rasterises
    # glyphs and a stroked arc and hashes the resulting PIXELS. Everything that
    # moves a text width between two machines (which fonts are installed, which
    # rasteriser, which hinting) moves these bytes too, and antialiasing — which
    # a width measurement never sees at all — moves them as well. A vector
    # strictly downstream of an already-listed one cannot be less host-dependent
    # than it.
    #
    # THE HONEST BOUND on that: it is an argument from what the draw DOES, not a
    # two-machine measurement. Only one host was available on PS-135, so
    # "differs across machines" was not observed directly. What WAS observed is
    # the weaker neighbouring fact — the same seed reads 4242351214 on the
    # packaged firefox and 2838771797 on chromium — which shows the digest
    # tracks the renderer rather than the seed alone.
    #
    # Listed rather than omitted because the two errors are not symmetric. A
    # probe wrongly listed costs a line of caveat on a real drift. A probe
    # wrongly OMITTED reds the baseline on a different machine for a reason
    # nobody can see in the artifact, which is how an operator is trained to
    # ignore the command.
    "canvas.readback",
    "fonts.measureText",
    "masking.webglGetParameter",
    "webgl.extensions",
    "webgl.parameters",
    "webgl.readback",
    "webgl.unmasked",
    # PS-314. WebSerial is gated on the HOST PLATFORM, not on anything persona
    # does: `stealth_ext` never touches `Serial` or `navigator.serial` (grep it
    # — zero hits), and the two keys are the ONLY movement in a probe that
    # reports 41 of them. Firefox ships WebSerial behind a platform gate, so a
    # re-record on a different host flips exactly this pair:
    #
    #     window + worker   Serial:           undefined -> function
    #                       navigator.serial: undefined -> object
    #
    # Measured on this project's own artifact when the reference was re-recorded
    # under PS-314: `engine_build` identical (firefox-20), all 39 other keys
    # byte-identical, the seven probes already listed here byte-identical.
    #
    # ⚠️ WHAT THIS LIST DOES AND DOES NOT DO — read before assuming either way.
    # It is DOCUMENTATION, not a filter. `compare()` diffs every probe and
    # consults this tuple NOWHERE; its only consumer is `provenance()`, which
    # copies it into the artifact so a reader can tell "this row moved because
    # the machine changed" from "this row moved because the product changed".
    # Adding an entry therefore SUPPRESSES NOTHING: a future movement in
    # `stealth.apiPresence` still reds `baseline.check` exactly as it would
    # have before, and still has to be explained.
    #
    # That is the right property for this particular probe, and it is why the
    # entry is safe to add. `probes.py` says of it that "an UNEXPECTED PRESENCE
    # is as much a finding as an absence" — so a vector that is ALSO a real
    # stealth signal must not become tolerated by being named here, and it does
    # not.
    #
    # ⛔ SCOPE, for the reader this note exists to inform: this entry explains
    # the WebSerial PAIR under a platform-gate difference. It says nothing about
    # any other key of this probe, and a re-record that moves one is a finding
    # to be stated rather than a row that was already excused.
    "stealth.apiPresence",
)

# Repo-relative location of the committed artifact. Beside the iOS WebGL
# reference (PS-12), so "what we expect a profile to look like" has one home.
BASELINE_ARTIFACT = os.path.join(
    "tests", "fixtures", "engine-fingerprint-baseline.firefox.json"
)

# How long to wait for the session to report BROWSER_STARTED. Generous: a first
# launch of a fresh profile dir can do a one-time headless init.
LAUNCH_TIMEOUT_S = 240.0

# How long to wait for the eval hook AFTER the session has already reported
# BROWSER_STARTED. A SECOND, MUCH SMALLER BUDGET, and the size is the argument.
#
# `_launch_and_watch` emits BROWSER_STARTED the moment the window is on screen
# and registers the hook afterwards; between the two it only defines three
# closures (`_live_page`, `_ff_eval`, `_ff_goto`) and does no I/O. So the gap
# this budget covers is closure-definition time — sub-millisecond — not work.
# It is a real gap rather than a theoretical one because `in_process=True` puts
# the session on a THREAD of this interpreter and `emit`'s flush is a write()
# that releases the GIL, handing the reader the interpreter mid-gap.
#
# 5s is ~2% of LAUNCH_TIMEOUT_S and four orders of magnitude larger than the
# gap it exists to absorb — deliberately over-provisioned against a loaded CI
# runner, and still small enough that a session which publishes NOTHING is
# refused in seconds rather than minutes. It is NOT sized for slow work,
# because there is no work here to be slow: a hook that has not appeared in 5s
# is not late, it is absent. Widening it would only delay a true refusal, which
# `_await_started`'s docstring already explains is the outcome to avoid.
HOOK_PUBLISH_TIMEOUT_S = 5.0

# Poll interval while waiting for the hook. Fine enough that the ordinary case
# (hook already there, or a few microseconds away) costs essentially nothing.
HOOK_POLL_INTERVAL_S = 0.02


class BaselineUnavailable(RuntimeError):
    """The baseline could not be recorded, with an actionable reason.

    Always says which precondition is missing and what to do about it — a
    baseline that fails for an unexplained reason is worse than no baseline,
    because it trains the operator to ignore the command.
    """


def baseline_profile(name: str = BASELINE_PROFILE_NAME) -> Profile:
    """The pinned profile the baseline is recorded from.

    Constructed, never looked up. See the module docstring for why every field
    here is a literal.
    """
    return Profile(
        name=name,
        proxy=None,
        os_type=BASELINE_OS_TYPE,
        device_type=BASELINE_DEVICE_TYPE,
        engine=BASELINE_ENGINE,
        resolution=BASELINE_RESOLUTION,
        search_engine=BASELINE_SEARCH_ENGINE,
        # [] is "explicitly cleared", NOT None ("use the store's defaults") —
        # the reading must not depend on the operator's bookmark store.
        bookmarks=[],
        certificate=None,
        ai_control=False,
        # Pinned like every other identity input above, and pinned to 0
        # SPECIFICALLY — never to CURRENT_HARDWARE_GENERATION. The hardware
        # generation selects which slice of each hardware list the seed indexes
        # into, so it feeds the derived identity exactly like os_type does. If
        # this tracked the current constant, bumping that constant (i.e. adding
        # hardware) would move the baseline's own screen and GPU, and the next
        # recording would diff red for a reason that has nothing to do with the
        # engine — destroying the one property this module exists to provide.
        # 0 is also what a None would fall back to; stating it makes the
        # invariant survive someone later changing that fallback.
        hardware_generation_value=0,
    )


def _effective_window_size(profile: Profile) -> list[int] | None:
    """The main-window geometry the recording ACTUALLY ran with, read back off
    disk, or ``None`` when there is nothing to read.

    WHY THIS IS READ RATHER THAN ASSUMED. :func:`provenance` reports the pinned
    geometry as an INPUT, and that was honest while :func:`_pin_recording_window`
    wrote unconditionally. It is not honest now that the pin DEFERS to a
    profile's own persisted state: a warm (``fresh=False``) recording keeps
    whatever the previous session left behind, so a provenance field naming
    ``BASELINE_WINDOW_SIZE`` would claim an input that recording did not use.
    An artifact that misreports its own inputs is worse than one that omits
    them — it is the reference every later comparison is read against.

    So this reports what the file says, whoever wrote it: the pin on a fresh
    recording, the profile's own history on a warm one. ``None`` when the file
    is absent or unreadable (the chromium arm never launches and never pins, and
    a malformed file is not a geometry) — the field is then omitted rather than
    guessed at.
    """
    from ...core.config import DATA_DIR
    from ..browser.invisible_launch import _INVISIBLE_SUBDIR

    path = os.path.join(
        DATA_DIR, profile.name, _INVISIBLE_SUBDIR, "xulstore.json"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        main = stored["chrome://browser/content/browser.xhtml"]["main-window"]
        return [int(main["width"]), int(main["height"])]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def provenance(profile: Profile, *, window_size: list[int] | None = None) -> dict:
    """How this recording was produced, as data.

    Recorded next to the readings so the next person can reproduce the artifact
    exactly instead of guessing which knobs were set.

    ``window_size`` is the geometry the recording ACTUALLY ran with, supplied by
    the caller that did the recording — see :func:`_effective_window_size` for
    why it is measured rather than restated from ``BASELINE_WINDOW_SIZE``. The
    key is OMITTED when it is unknown, which is the honest answer for a
    recording that never launched a window; a caller reading this field can
    therefore trust it, and a missing field says "not recorded" rather than
    standing in for a value nobody observed.
    """
    out = {
        "profile_name": profile.name,
        "fingerprint_seed": profile.fingerprint_seed,
        "os_type": profile.os_type,
        "device_type": profile.device_type,
        "engine": profile.engine,
        "resolution": profile.resolution,
        "search_engine": profile.search_engine,
        "proxy": "none",
        "bookmarks": "none (explicitly cleared)",
        "certificate": "none",
        "realms": list(BASELINE_REALMS),
        # Which readings are host-dependent, stated IN the artifact rather than
        # only in the accompanying note — so whoever is looking at a red diff
        # sees the caveat in the same file as the values it applies to.
        "env_sensitive_probes": list(ENV_SENSITIVE_PROBES),
    }
    # The window geometry the recording ACTUALLY ran with, stated as data so a
    # reader of a red `window.innerSize` diff can tell "the pin moved" from "the
    # engine moved" without reading the recorder's source. Before the pin existed
    # this field could not have been written honestly: the geometry was the
    # engine's own default, i.e. an OBSERVATION, and provenance records INPUTS.
    #
    # ⚠️ MEASURED, NOT RESTATED. It is what the caller read back off disk, not
    # `BASELINE_WINDOW_SIZE` — a warm recording defers to the profile's own
    # persisted geometry, so naming the constant here would claim an input that
    # recording did not use. Omitted entirely when unknown, rather than guessed.
    if window_size is not None:
        out["window_size"] = list(window_size)
    return out


# --- recording --------------------------------------------------------------


def _require_display() -> None:
    """A real reading needs a real browser, which needs a display.

    Refused loudly rather than worked around. Comparing anything that is not a
    live reading — generated source text, a headless approximation — is exactly
    the defect the verify service was built to end, so the honest failure is to
    say what is missing.
    """
    from ...core import platform as _platform

    if _platform.IS_LINUX and not os.environ.get("DISPLAY"):
        raise BaselineUnavailable(
            "no DISPLAY: recording a baseline launches a real browser and there "
            "is no display server to launch it on. Run the command under a "
            "virtual display, e.g.\n"
            "    xvfb-run -a python -m src.services.verify.baseline_cli record\n"
            "(install with: sudo apt-get install -y xvfb)"
        )


def _await_started(proc: Any, timeout: float) -> None:
    """Block until the session reports BROWSER_STARTED, or fail saying why.

    The read is on a pump THREAD rather than inline, and that is the whole
    point: ``proc.stdout.readline()`` blocks unboundedly, so a deadline tested
    only *between* reads can never fire against a session that starts and then
    says nothing — which is precisely the wedge a launch timeout exists to
    catch. Draining into a queue and waiting with the remaining budget makes the
    bound real: the deadline is enforced while we are waiting, not only after a
    line happens to arrive.

    A hang here is worse than a failure. The ``finally: _teardown(...)`` in
    :func:`record_snapshot` never runs while we are parked, so the session leaks
    and the eval-hook registry keeps a stale entry; and on the ``in_process``
    path the session is a daemon thread of THIS process whose only stop signal
    is ``proc.terminate()``, unreachable from inside a blocking read. This is
    also destined for CI, where a hang burns the job's wall clock and gets
    misdiagnosed as flaky infrastructure, while a failure is a red build
    somebody reads.

    A thread plus ``queue.Queue.get(timeout=...)`` rather than ``select``:
    ``select`` on pipes is not available on Windows, and ``InvisibleProcess``
    explicitly supports a Windows/macOS path.
    """
    import queue
    import threading

    lines: "queue.Queue[str | None]" = queue.Queue()

    def _pump() -> None:
        # readline(), not `for raw in proc.stdout`: iterating a file object
        # reads ahead into an internal buffer and can withhold a complete line
        # until the buffer fills, which on a pipe means BROWSER_STARTED can sit
        # unseen. readline returns each line as it lands.
        try:
            while True:
                raw = proc.stdout.readline()
                if not raw:
                    break
                lines.put(raw)
        except Exception:
            pass
        finally:
            lines.put(None)  # EOF sentinel

    # Daemon: if we abandon this read on timeout, the pump must never keep the
    # interpreter alive waiting on a pipe nobody will write to again.
    threading.Thread(target=_pump, daemon=True).start()

    deadline = time.time() + timeout
    tail: list[str] = []
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise BaselineUnavailable(
                f"the browser did not start within {timeout:.0f}s; last output: "
                f"{tail[-5:] or 'none'}"
            )
        try:
            raw = lines.get(timeout=remaining)
        except queue.Empty:
            # Nothing arrived inside the budget; loop re-checks and raises.
            continue
        if raw is None:
            raise BaselineUnavailable(
                "the browser session ended before it reported readiness; last "
                f"output: {tail[-5:] or 'none'}"
            )
        line = raw.strip()
        if line:
            tail.append(line)
        if line == "BROWSER_STARTED":
            return
        if line in ("BROWSER_CLOSED", "LAUNCH_CANCELLED"):
            raise BaselineUnavailable(
                f"the browser session reported {line} instead of starting; "
                f"last output: {tail[-5:]}"
            )


def _await_ff_eval_hook(
    proc: Any,
    name: str,
    timeout: float | None = None,
) -> dict | None:
    """Wait, BOUNDED, for a started session to publish its firefox eval hook.

    Returns the hook, or ``None`` when the budget is spent or the session is
    already dead. Returning ``None`` rather than raising is deliberate: the
    caller owns the refusal, so the message that names the profile stays at the
    one site that has always produced it and cannot drift.

    WHY A WAIT EXISTS AT ALL. ``_launch_and_watch`` announces readiness
    (``emit("BROWSER_STARTED")``) BEFORE it publishes the hook
    (``register_ff_eval``); in between it only defines three closures.
    ``_await_started`` returns on the announcement, so a reader can legally
    arrive inside that gap. With ``in_process=True`` the session is a THREAD of
    this interpreter and the registry a plain dict, so the two are threads of
    one process and ``emit``'s flush — a ``write()`` — releases the GIL exactly
    there. A single unretried read therefore converts a LATE hook into a
    refusal, and on the behaviour lane one such refusal reddens all 16 launches
    as ``CANNOT_RUN``. Four other harnesses in this tree (``readings/
    ps349-.../subject.py``, ``scripts/ps350_stealth_control.py``,
    ``scripts/ps330_ff_devices_reading.py``, ``scripts/ps175_cycles.py``) each
    learned this by hand; the production recorder was the one that never did.

    ⛔ THE REFUSAL IS NOT SOFTENED, ONLY DELAYED. A session that publishes NO
    hook still reaches the caller's ``BaselineUnavailable`` — this returns
    ``None`` for exactly that case. Trading an over-firing gate for a silent
    one would be strictly worse than the defect being fixed.

    ⚠️ BOUNDED, because ``_await_started``'s docstring already argues the case
    one call higher and it is not re-litigated here: while we are parked the
    ``finally: _teardown(...)`` never runs, the session leaks and the registry
    keeps a stale entry; and in CI a hang burns the job's wall clock and is
    misdiagnosed as flaky infrastructure, where a failure is a red build
    somebody reads. See ``HOOK_PUBLISH_TIMEOUT_S`` for why that number.

    ``proc.poll()`` IS USED, and this is the explicit decision the ticket asks
    for. It is implemented on both arms (``None`` while the session lives, an
    exit code once it does not), so it distinguishes "not yet" from "never":
    a session that has already ENDED can never publish a hook, and waiting the
    remaining budget on a corpse would delay a true refusal for no reading.
    It is consulted only AFTER a hook lookup, so a session that publishes and
    exits immediately is still read rather than lost to a race with itself.
    A ``proc`` that cannot answer (no ``poll``, or one that raises — test
    doubles, and any future handle) is treated as ALIVE: an unknown liveness
    must not shorten the budget, because that would re-create the very
    single-read refusal this function exists to remove.
    """
    from ..browser import invisible_launch as il

    # Resolved at CALL time, not bound as a default: the module constant is the
    # single home of the number, so shortening it (a test, or a future caller)
    # must not require knowing that a default argument froze it at import.
    if timeout is None:
        timeout = HOOK_PUBLISH_TIMEOUT_S

    deadline = time.monotonic() + timeout
    while True:
        # Looked up through the module, not a bound name, so the registry is
        # re-read on every pass — the point of the loop.
        hook = il.get_ff_eval(name)
        if hook and callable(hook.get("eval")):
            return hook

        try:
            ended = proc.poll() is not None
        except Exception:
            ended = False
        if ended:
            # "Never", not "not yet". Refuse now; the caller says why.
            return None

        if time.monotonic() >= deadline:
            return None
        time.sleep(HOOK_POLL_INTERVAL_S)


def _teardown(proc: Any, name: str) -> None:
    """Stop the session and drop its eval hook, never raising.

    Teardown runs from a ``finally`` and must not mask the real error that sent
    us here, so every step is swallowed — but not SILENTLY. On the in_process
    path ``terminate()`` only sets a stop event, so a session that ignores it
    leaves a live browser thread behind while the registry entry is wiped. A
    leaked session should be diagnosable rather than invisible, so say so.
    """
    import sys

    from ..browser.invisible_launch import unregister_ff_eval
    from ..browser.process_group import reap_process_group

    # PS-192: tear down the process GROUP, not the single handle. On the fork
    # path the child made itself a session leader, so this reaches the whole
    # Firefox tree; on the in_process path pid is 0, the group is refused, and
    # this degrades to exactly the terminate() this used to do (which there
    # only sets a stop event — the case the warning below exists for).
    #
    # The terminate -> wait -> kill escalation lives inside the reaper, and it
    # never raises: this runs from a `finally` and must not mask the real error
    # that sent us here.
    try:
        reap_process_group(proc, timeout=30)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"warning: could not terminate the session: {exc}", file=sys.stderr)
    try:
        # Asked AFTER the reap, so it reports what actually survived rather
        # than what was merely asked to stop. Still not silent: a session that
        # outlives a group kill is a real leak and must stay diagnosable.
        if proc.poll() is None:
            print(
                f"warning: the browser session for {name!r} did not stop within "
                "30s and may still be running; its eval hook is being dropped "
                "regardless.",
                file=sys.stderr,
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"warning: could not wait for the session to stop: {exc}", file=sys.stderr)
    unregister_ff_eval(name)


def _pin_recording_window(profile: Profile) -> str:
    """Write the recorder's fixed main-window geometry, and say where.

    Returns the path written, so the caller (and the tests) can assert on the
    exact file the LAUNCH will read rather than on a directory that merely
    looks right.

    THREE THINGS ABOUT THE PATH, each found in code and each easy to get wrong:

    1. It is the ENGINE's inner profile dir, not the outer data dir.
       ``process.spawn_browser`` makes ``DATA_DIR/<name>/`` and then hands the
       child ``profile_dir = os.path.join(profile_dir, ".invisible-profile")``
       (``process.py:493``). ``_seed_window_size`` is called with THAT inner
       path, so the file the launch consults is
       ``DATA_DIR/<name>/.invisible-profile/xulstore.json``. A pin written one
       level up is never read by anything.

    2. The directory does not exist yet at this point, so it is created. On a
       ``fresh`` recording the whole tree was just removed; on a warm one the
       inner dir exists only if a previous launch made it.

    3. ``_seed_window_size`` DEFERS to it. That helper early-returns when
       ``xulstore.json`` is already present (``invisible_launch.py:914-916``),
       and that early return happens before it writes anything — so the file we
       leave here reaches the engine byte-for-byte. This is why the pin needs no
       change to the seeding helper at all: it is not fighting it, it is using
       the deferral the helper already promises to a user's own manual resize.

    ⭐ AND THIS FUNCTION HONOURS THAT SAME DEFERRAL, which is the point of the
    ``os.path.exists`` guard below. Round 1 wrote unconditionally and so was the
    one write in this path that OVERRODE a persisted resize — the exact contract
    ``_seed_window_size`` states ("a manual resize must survive relaunches") and
    the exact contract this docstring claimed to be using. Executed against a
    profile carrying real persisted state, the unconditional ``json.dump``
    destroyed ``PersonalToolbar`` and ``sidebar-box`` and rewrote ``main-window``
    — a comment asserting a property the code beneath it does not have.

    ⚠️ SO NOT EVERY RECORDING IS PINNED, and a reader of a red diff needs to
    know which:

    * ``fresh=True`` — ALWAYS pinned. The rmtree in :func:`_record_on_firefox`
      has just removed the whole tree, so the file cannot exist and the write
      always happens. This is the GATE's path (``engine_gate.py`` records with
      ``fresh=True`` unconditionally), so the gate is pinned on both sides and
      ACs 1-7 are untouched by the guard.
    * ``fresh=False`` — pinned only on the FIRST such recording, and thereafter
      the profile's own persisted geometry wins. That is what ``--reuse-profile``
      and the launch-backed behavioural checks want: ``fresh=False`` means "do
      not wipe the data dir, start from what the previous session left behind",
      and ``restart-continuity`` is asking a question ABOUT that state. A pin
      that rewrote it every launch would be writing over the thing being
      observed.

    :func:`provenance` reports the pinned geometry, so it must be read with the
    same scope: it states what the recorder pins, which is what the artifact it
    accompanies was recorded with (that artifact is recorded ``fresh``).

    ⚠️ THE CALL SITE IS PART OF THE FIX. This must run AFTER the ``fresh``
    rmtree in :func:`_record_on_firefox` and BEFORE ``spawn_browser``. Written
    before the rmtree it is deleted, and the recording silently falls back to
    the engine's default — which is the exact defect, restored, and looking
    green. ``test_the_window_pin_survives_a_fresh_recording`` holds that
    ordering.

    ONE PATH COULD STILL UNDO IT, and it is stated rather than assumed:
    ``_init_places_db`` (``invisible_launch.py:1650-1666``) deletes
    ``xulstore.json`` after its throwaway headless run. It is unreachable for
    THIS profile — ``baseline_profile()`` sets ``bookmarks=[]`` (explicitly
    cleared), and ``_seed_firefox_bookmarks`` returns immediately when places is
    not ready and there are no bookmarks, so no init run happens. That is a
    property of the pinned profile, not a guarantee of the launcher: a future
    change that gives the baseline profile bookmarks would reintroduce the
    unpinned reading, and the live check in AC4 is what would catch it.
    """
    from ...core.config import DATA_DIR
    from ..browser.invisible_launch import _INVISIBLE_SUBDIR

    width, height = BASELINE_WINDOW_SIZE
    inner_dir = os.path.join(DATA_DIR, profile.name, _INVISIBLE_SUBDIR)
    os.makedirs(inner_dir, exist_ok=True)
    path = os.path.join(inner_dir, "xulstore.json")
    if os.path.exists(path):
        # Defer, exactly as `_seed_window_size` does to a user's manual resize.
        # See the docstring: a warm recording's own persisted state is the thing
        # the behavioural checks are observing, and must not be written over.
        return path
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "chrome://browser/content/browser.xhtml": {
                    "main-window": {
                        "width": str(width),
                        "height": str(height),
                        "screenX": "0",
                        "screenY": "0",
                        "sizemode": "normal",
                    }
                }
            },
            fh,
        )
    return path


def _record_on_firefox(
    profile: Profile, realms: tuple[str, ...], timeout: float, fresh: bool
) -> tuple[dict, str]:
    """Launch the profile in-process, read every probe, tear the session down.

    This is the ORIGINAL recording path, moved behind the engine split with no
    behavioural change: same launch, same hook lookup, same failure message,
    same ``finally`` teardown. See :func:`record_snapshot` for why launch and
    record must happen in one process.

    Returns ``(readings, engine_observed)``. The second element is the engine
    of the CHANNEL that did the reading, not of the profile record — this arm
    reads through the firefox eval hook, so reaching the return at all means
    firefox answered. Pinned against ``_FirefoxTransport.engine`` by
    ``test_both_arms_report_the_engine_their_own_transport_reports`` so the two
    spellings of "firefox" cannot drift apart.

    THE DISPLAY GATE LIVES HERE, on the arm that launches, rather than at the
    top of :func:`record_snapshot` where it used to sit. Before the engine
    split there was one path and it ALWAYS launched, so an unconditional gate
    was correct and its message ("recording a baseline launches a real
    browser ... run it under xvfb") was true. Adding a non-launching arm broke
    that reference: the chromium arm attaches to a session someone else
    started and needs no display at all, so gating it on one refused the exact
    deployment this ticket exists to reach — a headless host running chromium
    under automation, where a chromium-effective profile would STILL have been
    unobservable to Levels 1 and 2. That is the "the instrument cannot be
    pointed at chromium" defect again with a different gate substituted for the
    hardcoded ``get_ff_eval``, so the gate belongs to the launch, not to the
    recorder.
    """
    from ...core.config import DATA_DIR
    from ..browser.process import spawn_browser

    # Immediately before the launch, so the refusal and the thing it refuses
    # are the same event and the message cannot drift out of truth again.
    _require_display()

    if fresh:
        shutil.rmtree(os.path.join(DATA_DIR, profile.name), ignore_errors=True)

    # AFTER the wipe, BEFORE the launch — the only seam where the pin both
    # survives `fresh` and is in place when the engine reads it. See
    # `_pin_recording_window` for why the order is load-bearing.
    _pin_recording_window(profile)

    # in_process=True: the eval hook is published per-process, so a forked
    # session would register it somewhere this code cannot see.
    proc = spawn_browser(profile, in_process=True)
    try:
        _await_started(proc, timeout)
        # NOT a single read: BROWSER_STARTED is announced before the hook is
        # published, so the one-shot read turned a LATE hook into a refusal.
        # The refusal below is unchanged and still fires for a session that
        # publishes none — see `_await_ff_eval_hook`.
        hook = _await_ff_eval_hook(proc, profile.name)
        if not hook or not callable(hook.get("eval")):
            raise BaselineUnavailable(
                f"the session for {profile.name!r} started but published no "
                "eval hook, so nothing can be read from it"
            )
        return run_probes(hook["eval"], realms), _FIREFOX_CHANNEL
    finally:
        _teardown(proc, profile.name)


def _record_on_chromium(
    profile: Profile, realms: tuple[str, ...], fresh: bool
) -> tuple[dict, str]:
    """Read an ALREADY-RUNNING chromium profile over its existing CDP channel.

    THIS PATH DELIBERATELY DOES NOT LAUNCH, and that is a security decision
    rather than an omission — see the "THE CHROMIUM ARM DOES NOT LAUNCH"
    section of the module docstring. It attaches to a channel the operator
    already opened, or it refuses. It never sets, persists or infers
    ``ai_control``, and it never passes a debugging-port flag to anything.

    ``fresh`` is REFUSED here rather than honoured. Wiping the data directory
    of a session that is currently running is not a "clean start", it is
    corruption of a live profile — and the caller asking for one is asking for
    a launch-from-clean this path is not allowed to perform. Refusing says so;
    silently downgrading to a warm read would return a document whose
    provenance claims a freshness it does not have.
    """
    from .transport import TransportUnavailable, transport_for

    if fresh:
        raise BaselineUnavailable(
            f"cannot take a FRESH recording of {profile.name!r}: it launches on "
            "chromium, and the chromium arm attaches to an already-running "
            "session rather than launching one (launching it here would mean "
            "opening an unauthenticated CDP control channel, which isolation "
            "forbids). A fresh recording would require wiping the data "
            "directory of a live session. Record it with fresh=False against a "
            "profile already running in automation mode, or record the "
            "firefox-effective baseline instead."
        )

    try:
        transport = transport_for(profile.name, _CHROMIUM_CHANNEL)
    except TransportUnavailable as exc:
        # Not a reading, so not a verdict. Raised — never returned as an empty
        # or errored snapshot — because an unreadable arm that reaches the
        # comparators compares EQUAL against another unreadable arm and is
        # reported as agreement. See `behaviour._readings_or_refuse`.
        raise BaselineUnavailable(
            f"the chromium arm cannot read {profile.name!r}: {exc} Nothing was "
            "observed, so nothing is certified."
        ) from exc

    with transport:
        # `transport.engine`, NOT the constant just passed in and NOT the
        # profile's stored engine: the header must report the channel that did
        # the reading. Round-2 review caught the earlier spelling stamping
        # `effective_engine(profile)` — a profile stored as 'Chromium' was read
        # over the chromium channel and stamped 'Chromium', an engine nothing
        # ever observed, which also degraded `engine_build` to "unknown".
        return run_probes(transport.evaluate, realms), transport.engine


def record_snapshot(
    *,
    profile: Profile | None = None,
    fresh: bool = True,
    realms: tuple[str, ...] = BASELINE_REALMS,
    timeout: float = LAUNCH_TIMEOUT_S,
) -> dict:
    """Read every probe from a live profile, on the engine it ACTUALLY runs on.

    Returns a canonical snapshot document. ``fresh`` (the default) removes the
    profile's data directory first, so the recording starts from a known state
    rather than from whatever a previous session left behind — reproducibility
    is the whole point of the artifact.

    THE ENGINE IS OBSERVED, NEVER ASSUMED — and those are two separate claims
    about two separate steps, which is what round-2 review caught this docstring
    conflating.

    ROUTING is chosen by ``effective_engine`` — the same function
    ``spawn_browser`` routes on — so the read and the launch cannot answer the
    same question differently. This used to be an unconditional ``get_ff_eval``,
    which meant every profile that launches on chromium (every
    non-Windows-desktop profile, whatever its stored ``engine`` says) started
    fine, published no firefox hook, and died unread.

    STAMPING is taken from the TRANSPORT THAT DID THE READING — each arm returns
    its own channel's ``engine`` and that is what reaches ``build_snapshot`` —
    mirroring ``cli.py:126`` (``engine = transport.engine``), which is the one
    lane that already had this right. It is deliberately NOT
    ``effective_engine(profile)``: that reads the profile RECORD, and a record
    is an ASSERTION while a transport is an OBSERVATION. The two agree only
    when the stored engine happens to be spelled exactly like the channel.
    ``coherent_engine`` returns a coherent value UNCHANGED and there is no
    engine whitelist anywhere, so a profile stored as ``'Chromium'`` was read
    over the chromium channel and then stamped ``'Chromium'`` — an engine
    nothing ever observed — which additionally degraded ``engine_build`` to
    ``"unknown"`` (``snapshot.py`` maps any unrecognised family that way) and
    fed ``diff._META_FIELDS`` a field the recorder no longer guaranteed. That
    was the same assume-don't-observe defect this function exists to close,
    with a different constant substituted.

    AN UNRECOGNISED ENGINE IS REFUSED, not read. Routing used to be a bare
    ``else``, so anything that was not ``firefox`` went to the chromium adapter
    unconditionally — a profile stored ``'webkit'`` (which passes coherence, as
    only firefox pairs are constrained) would be driven over CDP and then
    stamped with its own unknown name. Refusing matches this module's posture:
    an engine nobody can name is precisely an inconclusive, and inconclusive is
    never a pass. Normalising instead would silently record one engine's
    readings under another engine's name, which is the defect above wearing a
    correction's clothes.

    THE DISPLAY GATE BELONGS TO THE LAUNCH, NOT TO THIS FUNCTION. It used to
    run unconditionally here, which was correct while there was one path and it
    always launched. The chromium arm does not launch — it attaches to a
    session the operator already opened — so gating it on a display refused the
    precise deployment this function exists to reach: a headless host running
    chromium under automation, where there is no X display and none is needed.
    It now sits inside :func:`_record_on_firefox`, immediately before the
    launch. Pinned by
    ``test_the_chromium_arm_records_with_no_display_because_it_does_not_launch``,
    which unsets DISPLAY for real and does NOT stub the gate.
    """
    profile = profile or baseline_profile()

    from ..browser.process import effective_engine

    routed = effective_engine(profile)
    if routed == _FIREFOX_CHANNEL:
        results, observed = _record_on_firefox(profile, realms, timeout, fresh)
    elif routed == _CHROMIUM_CHANNEL:
        results, observed = _record_on_chromium(profile, realms, fresh)
    else:
        raise BaselineUnavailable(
            f"{profile.name!r} resolves to engine {routed!r}, which this "
            f"recorder cannot speak (it knows {_FIREFOX_CHANNEL!r} and "
            f"{_CHROMIUM_CHANNEL!r}). Nothing was observed, so nothing is "
            "certified. Note the stored engine is not normalised on the way "
            "in: an unexpected spelling such as 'Chromium' lands here rather "
            "than being read over a channel it does not name."
        )

    snapshot = build_snapshot(
        results, engine=observed, profile=profile.name, realms=realms
    )
    # Read back AFTER the recording, so provenance states the geometry the
    # launch actually ran with rather than the one the pin would have written.
    # On the firefox arm this is the pin (fresh) or the profile's own persisted
    # state (warm); on the chromium arm nothing launched and it reads None, so
    # the field is omitted rather than fabricated.
    snapshot["provenance"] = provenance(
        profile, window_size=_effective_window_size(profile)
    )
    return snapshot


# --- comparison -------------------------------------------------------------


def count_errors(snapshot: dict) -> int:
    """How many probes in ``snapshot`` are an ERROR rather than a reading.

    An unobtainable reading is inconclusive, and inconclusive is never a pass —
    this is what stops a comparison going green off two non-readings.
    """
    probes = snapshot.get("probes")
    if not isinstance(probes, dict):
        return 0
    return sum(
        1
        for realm in probes.values()
        if isinstance(realm, dict)
        for entry in realm.values()
        if isinstance(entry, dict) and "error" in entry
    )


@dataclass
class BaselineResult:
    """The verdict of a baseline comparison."""

    entries: list[dict] = field(default_factory=list)
    baseline_errors: int = 0
    observed_errors: int = 0
    baseline_build: str = "unknown"
    observed_build: str = "unknown"

    @property
    def inconclusive(self) -> int:
        """How many reported entries rest on readings nobody obtained.

        Delegated to ``diff.inconclusive_count`` rather than recomputed, so this
        verdict and the ``diff`` CLI's cannot drift apart about what counts as
        "no evidence".
        """
        return inconclusive_count(self.entries)

    @property
    def drifted(self) -> bool:
        """A probe genuinely moved, appeared or vanished.

        NOT simply ``bool(self.entries)``. ``diff_snapshots`` reports an entry
        for a probe that FAILED on both sides too — deliberately, since a
        silently dropped one would be worse — but it labels it INCONCLUSIVE
        because neither side carries a reading. Counting those as drift would
        make the headline announce "N probe(s) differ" about probes that were
        never read, which is a confident claim derived from a non-reading: the
        same defect on the drift axis that ``ok`` closes on the pass axis.

        Those entries are still REPORTED, and they still deny the pass through
        ``ok`` — they are just not called drift.
        """
        return self.inconclusive < len(self.entries)

    @property
    def ok(self) -> bool:
        """PASS: every probe was read on both sides, and nothing moved.

        Deliberately NOT just ``not self.drifted``. Two identically-failed
        readings compare equal, so an error on either side means the comparison
        had nothing to say — that is not a pass. Since ``drifted`` now excludes
        inconclusive entries, the error terms are what keeps an all-inconclusive
        comparison from passing, and they are load-bearing for exactly that.
        """
        return not self.entries and self.baseline_errors == 0 and self.observed_errors == 0

    def report(self) -> str:
        """The verdict as an operator reads it: which probe, which realm, both
        values — legible without opening the JSON."""
        lines = [
            f"baseline engine build: {self.baseline_build}",
            f"observed engine build: {self.observed_build}",
            "",
        ]
        if self.drifted:
            # Counts the probes that ACTUALLY moved, not every reported entry:
            # an inconclusive one is listed below but is not a difference, and
            # folding it into this number would overstate the finding.
            lines.append(
                f"DRIFT: {len(self.entries) - self.inconclusive} probe(s) "
                "differ from the baseline."
            )
            lines.append("")
            lines.append(format_diff(self.entries))
        elif self.entries:
            # Nothing moved, but the comparison was not clean either: every
            # entry here rests on a reading nobody obtained. Saying "every
            # reading matches" would be a false reassurance about probes that
            # were never read — the pass-side twin of announcing DRIFT over a
            # non-reading. They are LISTED, because a probe that quietly
            # disappears from the report is exactly what this tool exists to
            # prevent.
            lines.append(
                f"NOT DRIFT, but INCONCLUSIVE: {len(self.entries)} probe(s) "
                "could not be read on either side, so nothing was established "
                "about them. This is not agreement."
            )
            lines.append("")
            lines.append(format_diff(self.entries))
        else:
            lines.append("no probe moved: every reading matches the baseline.")

        if self.baseline_errors or self.observed_errors:
            lines.append("")
            lines.append(
                f"INCONCLUSIVE: {self.baseline_errors} error(s) in the baseline "
                f"and {self.observed_errors} in the new reading. Two probes that "
                "FAIL identically compare equal, so a clean diff over errored "
                "probes is not evidence they agree. Fix the errors and re-run."
            )

        lines.append("")
        lines.append("PASS" if self.ok else "FAIL")
        return "\n".join(lines)


def compare(baseline: dict, observed: dict) -> BaselineResult:
    """Compare a recorded reading against the committed baseline.

    ``include_meta`` is left at its default (off) on purpose: an engine BUILD
    change is exactly what we EXPECT between the two sides — it is the reason
    the comparison is being run. It is the probe evidence that must not move.
    """
    return BaselineResult(
        entries=diff_snapshots(baseline, observed),
        baseline_errors=count_errors(baseline),
        observed_errors=count_errors(observed),
        baseline_build=str(baseline.get("engine_build", "unknown")),
        observed_build=str(observed.get("engine_build", "unknown")),
    )


def check(
    baseline_path: str = BASELINE_ARTIFACT,
    *,
    recorder: Callable[[], dict] | None = None,
) -> BaselineResult:
    """Record the pinned profile now and compare it against the committed
    baseline. ``recorder`` is injectable so the comparison is testable without
    a browser; it defaults to a real launched reading.

    Raises ``BaselineUnavailable`` — which the CLI reports as exit 2, "the
    check could not run" — when there is nothing to compare against. That
    separation is the whole point of the guard below: exit 1 is the DRIFT
    signal, so a caller (and the CI job this is headed for) would otherwise
    read a missing or corrupt artifact as an identity change that never
    happened. It is the inverse of the trap ``BaselineResult.ok`` closes:
    there, a green derived from two non-readings; here, a red derived from no
    reading at all.
    """
    if not os.path.isfile(baseline_path):
        raise BaselineUnavailable(
            f"no baseline to compare against at {quote_path(baseline_path)}. "
            "Nothing was compared, so this is NOT drift. "
            f"{BASELINE_ARTIFACT} is repo-relative, so if you are running from "
            "outside the repository root, pass --baseline with a full path. If "
            "the artifact is genuinely absent, record one with:\n"
            "    xvfb-run -a python -m src.services.verify.baseline_cli record"
        )
    try:
        baseline = load(baseline_path)
    except (OSError, ValueError) as exc:
        # ValueError, NOT json.JSONDecodeError, and the width is deliberate: a
        # baseline that is not valid UTF-8 raises UnicodeDecodeError, which is a
        # ValueError but NOT a JSONDecodeError, so catching the narrower type
        # would let a corrupt-bytes artifact traceback out to exit 1 — the drift
        # signal — which is the exact confusion this guard exists to end.
        # Verified: opening a non-UTF-8 file under json.load raises
        # UnicodeDecodeError (isinstance ValueError=True, JSONDecodeError=False).
        raise BaselineUnavailable(
            f"the baseline at {quote_path(baseline_path)} could not be read: {exc}. "
            "Nothing was compared, so this is NOT drift — the reference "
            "itself is unusable. Restore it from git, or re-record it after "
            "an ACCEPTED bump."
        ) from exc
    # PARSING IS NOT THE SAME QUESTION AS BEING A BASELINE, and the two guards
    # above only answer the first. Everything below here is about a file that
    # read back perfectly well and simply is not a snapshot — a JSON list, a
    # bare `null`, or some other object in the tree (`--baseline site/package.json`
    # is a one-character-plausible typo). Without this check those reach
    # `compare` and fail in two different ways, BOTH of them exit 1:
    #
    #   * a non-dict raises AttributeError out of diff.py — an uncaught
    #     traceback surfacing on the DRIFT code;
    #   * a dict with no probes compares cleanly against nothing and reports
    #     every observed probe as "added" — `DRIFT: 78 probe(s)`, a confident
    #     maximum-alarm FAIL produced by a reading that never happened. That
    #     one is the more dangerous of the two precisely because it does not
    #     look like a malfunction, so it gets believed.
    #
    # Both are the same defect as a corrupt file and get the same answer: the
    # comparison did not happen, so it cannot be drift. Exit 2.
    if not isinstance(baseline, dict) or not isinstance(baseline.get("probes"), dict):
        raise BaselineUnavailable(
            f"the file at {quote_path(baseline_path)} parsed as JSON but is not a "
            "baseline snapshot — it has no 'probes' object. Nothing was "
            "compared, so this is NOT drift. --baseline must point at an "
            "artifact produced by `record` (the committed one is "
            f"{BASELINE_ARTIFACT})."
        )
    if not any(
        isinstance(realm, dict) and realm for realm in baseline["probes"].values()
    ):
        # A structurally valid snapshot carrying ZERO readings is the same
        # false red by a narrower door: every observed probe diffs as "added".
        # Separated from the check above so the message can name the real
        # cause — this shape is what a REFUSED or truncated recording leaves
        # behind, not a typo'd path.
        raise BaselineUnavailable(
            f"the baseline at {quote_path(baseline_path)} is a snapshot but "
            "contains no probe readings at all, so there is nothing to compare "
            "against — this is NOT drift. Every probe would be reported as "
            "'added'. "
            "Re-record it, and check that the recording was not refused for "
            "unread probes."
        )
    observed = (recorder or record_snapshot)()
    return compare(baseline, observed)


__all__ = [
    "BASELINE_ARTIFACT",
    "BASELINE_ENGINE",
    "BASELINE_PROFILE_NAME",
    "BASELINE_REALMS",
    "BASELINE_WINDOW_SIZE",
    "BaselineResult",
    "BaselineUnavailable",
    "baseline_profile",
    "check",
    "compare",
    "count_errors",
    "provenance",
    "record_snapshot",
]
