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

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ...models.profile import Profile
from .diff import diff_snapshots, format_diff
from .probes import WINDOW, WORKER
from .runner import run_probes
from .snapshot import build_snapshot, load

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

# Repo-relative location of the committed artifact. Beside the iOS WebGL
# reference (PS-12), so "what we expect a profile to look like" has one home.
BASELINE_ARTIFACT = os.path.join(
    "tests", "fixtures", "engine-fingerprint-baseline.firefox.json"
)

# How long to wait for the session to report BROWSER_STARTED. Generous: a first
# launch of a fresh profile dir can do a one-time headless init.
LAUNCH_TIMEOUT_S = 240.0


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
    )


def provenance(profile: Profile) -> dict:
    """How this recording was produced, as data.

    Recorded next to the readings so the next person can reproduce the artifact
    exactly instead of guessing which knobs were set.
    """
    return {
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
    }


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
    """Block until the session reports BROWSER_STARTED, or fail saying why."""
    deadline = time.time() + timeout
    tail: list[str] = []
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise BaselineUnavailable(
                "the browser session ended before it reported readiness; last "
                f"output: {tail[-5:] or 'none'}"
            )
        line = line.strip()
        if line:
            tail.append(line)
        if line == "BROWSER_STARTED":
            return
        if line in ("BROWSER_CLOSED", "LAUNCH_CANCELLED"):
            raise BaselineUnavailable(
                f"the browser session reported {line} instead of starting; "
                f"last output: {tail[-5:]}"
            )
    raise BaselineUnavailable(
        f"the browser did not start within {timeout:.0f}s; last output: "
        f"{tail[-5:] or 'none'}"
    )


def _teardown(proc: Any, name: str) -> None:
    from ..browser.invisible_launch import unregister_ff_eval

    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=30)
    except Exception:
        pass
    unregister_ff_eval(name)


def record_snapshot(
    *,
    profile: Profile | None = None,
    fresh: bool = True,
    realms: tuple[str, ...] = BASELINE_REALMS,
    timeout: float = LAUNCH_TIMEOUT_S,
) -> dict:
    """Launch the pinned profile, read every probe, tear the session down.

    Returns a canonical snapshot document. ``fresh`` (the default) removes the
    profile's data directory first, so the recording starts from a known state
    rather than from whatever a previous session left behind — reproducibility
    is the whole point of the artifact.
    """
    _require_display()
    profile = profile or baseline_profile()

    from ...core.config import DATA_DIR
    from ..browser.invisible_launch import get_ff_eval
    from ..browser.process import spawn_browser

    if fresh:
        shutil.rmtree(os.path.join(DATA_DIR, profile.name), ignore_errors=True)

    # in_process=True: the eval hook is published per-process, so a forked
    # session would register it somewhere this code cannot see.
    proc = spawn_browser(profile, in_process=True)
    try:
        _await_started(proc, timeout)
        hook = get_ff_eval(profile.name)
        if not hook or not callable(hook.get("eval")):
            raise BaselineUnavailable(
                f"the session for {profile.name!r} started but published no "
                "eval hook, so nothing can be read from it"
            )
        results = run_probes(hook["eval"], realms)
    finally:
        _teardown(proc, profile.name)

    snapshot = build_snapshot(
        results, engine=BASELINE_ENGINE, profile=profile.name, realms=realms
    )
    snapshot["provenance"] = provenance(profile)
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
    def drifted(self) -> bool:
        """A probe moved, appeared or vanished."""
        return bool(self.entries)

    @property
    def ok(self) -> bool:
        """PASS: every probe was read on both sides, and nothing moved.

        Deliberately NOT just ``not self.drifted``. Two identically-failed
        readings compare equal, so an error on either side means the comparison
        had nothing to say — that is not a pass.
        """
        return not self.drifted and self.baseline_errors == 0 and self.observed_errors == 0

    def report(self) -> str:
        """The verdict as an operator reads it: which probe, which realm, both
        values — legible without opening the JSON."""
        lines = [
            f"baseline engine build: {self.baseline_build}",
            f"observed engine build: {self.observed_build}",
            "",
        ]
        if self.drifted:
            lines.append(
                f"DRIFT: {len(self.entries)} probe(s) differ from the baseline."
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
    a browser; it defaults to a real launched reading."""
    baseline = load(baseline_path)
    observed = (recorder or record_snapshot)()
    return compare(baseline, observed)


__all__ = [
    "BASELINE_ARTIFACT",
    "BASELINE_ENGINE",
    "BASELINE_PROFILE_NAME",
    "BASELINE_REALMS",
    "BaselineResult",
    "BaselineUnavailable",
    "baseline_profile",
    "check",
    "compare",
    "count_errors",
    "provenance",
    "record_snapshot",
]
