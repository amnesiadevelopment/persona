"""The DIFFERENTIAL: show that persona's masking layer reaches the page.

What this proves, and why nothing else does
-------------------------------------------
:mod:`masking_layer` installs persona's layer onto a verification engine. That
it was *installed* is a claim about delivery. The claim that matters is that the
layer REACHES THE PAGE A CHECKER ACTUALLY READS — and those are different, as
PS-78 measured on the product itself: ``add_init_script`` registered a spoof
that was present on a fresh launch and ABSENT on every restored tab. An
assertion that a builder was called would have read green through that entire
defect.

So the bar is a differential, and it is deliberately the same bar the record
must meet: **change something in the extension layer that the page observes,
take a reading before and after, and show the record moves.** A harness that
cannot produce that pair has not demonstrated anything.

It runs against a LOCAL page (:mod:`local_probe`), on loopback, with no
credential, no proxy and no exit. That venue is a deliberate choice with a
precedent: PS-69 hit exactly this wall, was re-scoped mid-flight to prove
engine, machine and seed each move a reading without needing the exit, and PS-10
records an explicit instruction not to re-introduce the dependency
(``/workspace/_secrets/`` is per-container and routinely absent; only a human can
restore it). The substance of the differential is unchanged; only the venue is.

The two arms, and why the axis is varied ONE at a time
------------------------------------------------------
``LAYER`` — the arm that answers this ticket's question. Same engine, same seed,
same page; persona's masking layer installed on one side and not the other. A
vector that moves is a vector the layer REACHES. This is the pair that would
have caught the original defect: on the un-widened harness both sides are the
packaged engine, so every vector reads identical and the "fix" looks like it
failed — which is exactly what PS-97 saw.

``SEED`` — the control. Same engine, layer installed on BOTH sides, only the
seed differs. It answers the question the LAYER arm cannot: that the vectors are
seed-derived and the probe is actually reading persona's per-profile
perturbation rather than some constant of the environment.

One axis at a time, always. A difference observed while two axes moved is
attributable to neither, and that is not a stylistic preference — it is the
method QA had to impose on PS-69 for the same reason.

What a result MEANS — read this before interpreting an empty ``moved``
----------------------------------------------------------------------
``moved`` non-empty on the LAYER arm is the demonstration: the layer reached the
page.

``moved`` EMPTY is not automatically a failure of this instrument, and the
report says which of the two it is rather than collapsing them:

* every vector reading ``unavailable:`` / ``error:`` means the PAGE could not
  compute them here (no WebGL in this build, no OfflineAudioContext). That is a
  gap in the venue and it is reported as such — never as "the layer does not
  reach the page", which would be a false and much more alarming claim.
* vectors that computed real values and did not move IS the finding, and it is
  the PS-97 shape: the code the layer was supposed to change did not change what
  the page sees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .local_probe import (
    ProbeReading,
    ProbeServer,
    differential,
    parse_probe_text,
)
from .masking_layer import LayerReport, absent_layer

# How long to let the local page settle. Tiny next to a real checker's 45-60s:
# the page does its work synchronously at load and renders the result, and there
# is no third party deciding when to publish a verdict.
PROBE_SETTLE_SECONDS = 2.0

# The axes a differential can vary. Exactly one moves per run — see the module
# docstring on why a two-axis difference is attributable to neither.
AXIS_LAYER = "layer"
AXIS_SEED = "seed"

DEFAULT_SEED = 4242
DEFAULT_CONTROL_SEED = 1337


@dataclass(frozen=True)
class Arm:
    """One side of a differential: a label, a reading, and what produced it.

    ``sandbox_waived`` is what the launch REALLY did, not what was asked for.
    It is read back off the chromium command line (see
    ``chromium_tier.ChromiumSession._start``) and it stays False on firefox,
    which ignores the flag entirely — a record that echoed the REQUEST would
    tag a firefox arm with a waiver that never applied to it, which is this
    ticket's own defect in miniature.

    It is per-ARM rather than per-record because the arms are separate
    launches: an arm that refused before launching never ran anything to
    disclose, and collapsing the pair into one flag would attribute a
    condition to a reading that was never taken under it.
    """

    label: str
    reading: ProbeReading
    layer: LayerReport
    seed: int
    error: str = ""
    sandbox_waived: bool = False

    def as_record(self) -> dict:
        return {
            "label": self.label,
            "seed": self.seed,
            "layer": self.layer.as_record(),
            "reading": self.reading.as_record(),
            "error": self.error,
            "sandbox_waived": self.sandbox_waived,
        }


def _computed_vectors(reading: ProbeReading) -> "dict[str, str]":
    """The vectors the page really computed — dropping the ones it could not.

    A value beginning ``unavailable:`` or ``error:`` is the page saying it could
    not read that vector at all. Those must not be compared: two sides agreeing
    on ``unavailable:no-webgl-context`` is not evidence that a spoof failed to
    land, and counting it as an unmoved vector would manufacture exactly the
    false negative this module exists to make impossible.
    """
    return {
        k: v
        for k, v in reading.vectors.items()
        if not (v.startswith("unavailable:") or v.startswith("error:"))
    }


def read_probe_once(
    url: str,
    *,
    seed: int,
    engine: str = "firefox",
    install_layer: bool = True,
    settle_seconds: float = PROBE_SETTLE_SECONDS,
    sleep: "Callable[[float], None] | None" = None,
    allow_unsandboxed: bool = False,
) -> Arm:
    """Launch ONE engine against the local page and read its vectors.

    WHAT IS SHARED WITH A REAL RUN, AND WHAT IS NOT. Stated plainly, because
    the value of this differential rests entirely on the first list and an
    earlier version of this docstring claimed the whole of it.

    SHARED — the parts that could carry the defect:

    * the LAUNCH and the LAYER INSTALL, both engines. Firefox goes through
      ``browser_tier.firefox_session`` and chromium through
      ``chromium_tier.ChromiumSession``, which are the same functions the real
      checker run uses — not a second copy written for this demonstration. That
      matters because the install wiring is exactly where the original defect
      lived: a differential with its own launch could keep passing while the
      harness's own path lost the layer.
    * the layer itself, built by the shipped builders (:mod:`masking_layer`).
    * the READING PATH: ``inner_text``, which is how the browser tier reads a
      real checker (``page.evaluate`` is blocked by CSP on real checker pages —
      measured, and recorded in ``browser_tier``'s docstring). So this cannot
      succeed by a route the harness lacks.

    NOT SHARED — and each difference is a property of the VENUE, not of the
    layer under test:

    * ``_read_open_session``, the checker loop, is not used. It cannot be: its
      first act is to prove the engine's own exit and it blanks every row when
      that fails (``ExitNotProvenInEngine``). A loopback page has no exit to
      prove. So this function drives one page directly, through the same
      ``goto``/``inner_text`` calls that loop makes.
    * NO PROXY and, on firefox, no ``extra_prefs``. Both are the credential's
      browser-side half (remote DNS, no direct failover) and there is no
      resolution to leak to a loopback address and nothing to fail over to.
    * no ``settle_seconds`` of 45-60s: the page computes its vectors
      synchronously at load, with no third party deciding when to publish.

    None of those three touches whether a spoof reaches the document, which is
    the single claim this instrument makes.

    An engine that will not start is recorded on the arm as an error, not
    raised: a differential that cannot run is a result worth reporting with its
    reason.
    """
    import time

    from .browser_tier import EngineUnavailable

    sleep = sleep or time.sleep
    label = f"{engine}/seed{seed}/layer={'on' if install_layer else 'off'}"

    captured: "list[LayerReport]" = []
    # What the launch REALLY did about the sandbox, reported by the session
    # itself. Stays empty on firefox and on a launch that never happened, so
    # the arm cannot claim a condition no process ran under.
    waived: "list[bool]" = []
    try:
        page_text = _drive_engine(
            url,
            seed=seed,
            engine=engine,
            install_layer=install_layer,
            settle_seconds=settle_seconds,
            sleep=sleep,
            layer_sink=captured.append,
            allow_unsandboxed=allow_unsandboxed,
            waiver_sink=waived.append,
        )
    except EngineUnavailable as exc:
        return Arm(
            label=label,
            reading=ProbeReading(vectors={}, note=str(exc)),
            layer=captured[0] if captured else absent_layer(str(exc)),
            seed=seed,
            error=str(exc),
            sandbox_waived=bool(waived and waived[0]),
        )

    return Arm(
        label=label,
        reading=parse_probe_text(page_text),
        layer=captured[0] if captured else absent_layer(
            "the engine ran but reported no layer"
        ),
        seed=seed,
        sandbox_waived=bool(waived and waived[0]),
    )


def _drive_engine(
    url: str,
    *,
    seed: int,
    engine: str,
    install_layer: bool,
    settle_seconds: float,
    sleep: "Callable[[float], None]",
    layer_sink: "Callable[[LayerReport], None]",
    allow_unsandboxed: bool = False,
    waiver_sink: "Callable[[bool], None] | None" = None,
) -> str:
    """Open ``url`` in a persona engine with/without the layer, return its text.

    A local page needs no proxy, and the engine is given none. That is the whole
    point of the venue: this runs in a container with no credential. Both
    engines are told so EXPLICITLY (``allow_no_proxy`` / an empty credential)
    rather than by omission — a launch that merely left the flag off would fall
    back to the system proxy, which is neither "no proxy" nor a proxy this run
    chose.

    ``waiver_sink`` receives what the launch REALLY did about chromium's
    sandbox, taken from the session that ran rather than from this function's
    own argument. It is never called on the firefox arm, because firefox has no
    sandbox flag to waive and a report there would be a claim about a condition
    that does not exist on that engine.
    """
    from .browser_tier import CHROMIUM, EngineUnavailable, firefox_session

    if engine == CHROMIUM:
        from .chromium_tier import ChromiumSession, ChromiumUnavailable

        try:
            session = ChromiumSession(
                "",
                seed=seed,
                install_layer=install_layer,
                # There is no exit at this venue, and the session must be told
                # so: without this it refuses the empty credential, which is
                # the right default for a checker run and wrong for loopback.
                allow_no_proxy=True,
                allow_unsandboxed=allow_unsandboxed,
            )
        except ChromiumUnavailable as exc:
            raise EngineUnavailable(str(exc)) from exc
        with session as live:
            layer_sink(session.layer_report)
            if waiver_sink is not None:
                # What the command line REALLY carried, not what was requested.
                waiver_sink(session.sandbox_waived)
            return _load_and_read(live, url, settle_seconds, sleep)

    with firefox_session(
        # Empty: no exit at this venue. See the note above on why that is said
        # rather than defaulted.
        "",
        seed=seed,
        install_layer=install_layer,
        layer_sink=layer_sink,
    ) as live:
        return _load_and_read(live, url, settle_seconds, sleep)


def _load_and_read(live, url: str, settle_seconds: float, sleep) -> str:
    page = live.new_page()
    try:
        page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        sleep(settle_seconds)
        return page.inner_text("body")
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_differential(
    *,
    axis: str = AXIS_LAYER,
    engine: str = "firefox",
    seed: int = DEFAULT_SEED,
    control_seed: int = DEFAULT_CONTROL_SEED,
    settle_seconds: float = PROBE_SETTLE_SECONDS,
    allow_unsandboxed: bool = False,
) -> dict:
    """Stand up the local page, read it twice varying ONE axis, and report.

    Returns the whole result as a dict — both arms with their layer reports,
    the diff, and a verdict. It is a document rather than a boolean because the
    interesting outcomes are not binary: see the module docstring on what an
    empty ``moved`` means, which depends on whether the page could compute the
    vectors at all.

    ``allow_unsandboxed`` is the chromium-only waiver for a host that forbids
    the user namespace chromium's sandbox needs — which is the state of the
    container this is usually run in. Off by default and never inferred, for
    the reason ``chromium_tier._launch_args`` records: persona's own launch
    path passes ``--no-sandbox`` nowhere, so a reading taken under it is not
    the product's surface and the operator asks for it explicitly.
    """
    if axis not in (AXIS_LAYER, AXIS_SEED):
        raise ValueError(f"unknown axis {axis!r}: vary {AXIS_LAYER} or {AXIS_SEED}")

    with ProbeServer() as server:
        url = server.url
        if axis == AXIS_LAYER:
            # ONE axis: same engine, same seed, same page. Only the layer moves.
            before = read_probe_once(
                url, seed=seed, engine=engine, install_layer=False,
                settle_seconds=settle_seconds,
                allow_unsandboxed=allow_unsandboxed,
            )
            after = read_probe_once(
                url, seed=seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
                allow_unsandboxed=allow_unsandboxed,
            )
        else:
            # ONE axis: layer on BOTH sides, only the seed moves.
            before = read_probe_once(
                url, seed=seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
                allow_unsandboxed=allow_unsandboxed,
            )
            after = read_probe_once(
                url, seed=control_seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
                allow_unsandboxed=allow_unsandboxed,
            )

    return build_differential_record(axis, engine, before, after)


def _sandbox_notes(before: Arm, after: Arm) -> "list[str]":
    """The prose disclosure for a reading taken without chromium's sandbox.

    Mirrors ``checker_cli._notes_for``, which already tags the sibling ``read``
    path's record with the same caveat. Two surfaces, one meaning: a consumer
    who learns to read the note on a checker record reads the same note here.

    Derived from what the LAUNCHES DID (``Arm.sandbox_waived``, read back off
    the command line) rather than from what the run requested, and empty when
    neither arm waived anything — so the note appears exactly when it is true.
    A firefox record never carries it, because firefox has no such flag.
    """
    waived = [arm for arm in (before, after) if arm.sandbox_waived]
    if not waived:
        return []
    which = " and ".join(arm.label for arm in waived)
    both = len(waived) == 2
    return [
        f"THIS DIFFERENTIAL WAS TAKEN WITH --no-sandbox ({which}). The host "
        "forbids the unprivileged user namespace chromium's sandbox needs, and "
        "the operator waived it explicitly (--allow-unsandboxed-chromium). "
        "persona's own launch path passes that flag NOWHERE, so this is NOT "
        "the surface the product presents to a checker: treat any difference "
        "against a sandboxed reading as possibly environmental until it is "
        "reproduced on a host where the sandbox works."
        + (
            ""
            if both
            else " Only ONE arm waived it, so the sandbox is a SECOND axis "
            "that moved alongside the one under test — and a difference seen "
            "while two axes moved is attributable to neither. Read this "
            "record's verdict as inconclusive about the axis it names."
        )
    ]


def build_differential_record(
    axis: str, engine: str, before: Arm, after: Arm
) -> dict:
    """Assemble the differential document from two arms.

    Split out from :func:`run_differential` so the reporting logic — which is
    where a wrong verdict would actually come from — is testable without
    launching a browser.

    THE RECORD DISCLOSES THE CONDITIONS IT WAS TAKEN UNDER, and that is not
    decoration: this whole ticket exists because a record described one subject
    while a consumer read it as another. A differential taken under
    ``--no-sandbox`` describes an engine surface persona never ships, so
    ``sandbox_waived`` and the matching ``notes`` entry ride the document
    itself — the durable thing that outlives the terminal it was printed in —
    rather than only the help text of the flag that caused it.
    """
    # Compared on the vectors the page really COMPUTED. See _computed_vectors:
    # two sides agreeing on "unavailable:no-webgl-context" is not a spoof
    # failing to land, and must never be counted as an unmoved vector.
    diff = differential(
        ProbeReading(vectors=_computed_vectors(before.reading)),
        ProbeReading(vectors=_computed_vectors(after.reading)),
    )
    comparable = sorted(
        set(_computed_vectors(before.reading)) & set(_computed_vectors(after.reading))
    )

    if before.error or after.error:
        verdict = "inconclusive"
        detail = (
            f"an arm did not run, so nothing was compared: "
            f"{before.error or after.error}"
        )
    elif not comparable:
        verdict = "inconclusive"
        detail = (
            "the local page computed NO vector on both sides (every reading was "
            "unavailable/error), so there was nothing to compare. This is a gap "
            "in what this venue can read here — NOT evidence that the layer "
            "fails to reach the page."
        )
    elif diff["any_moved"]:
        verdict = "moved"
        detail = (
            f"{len(diff['moved'])} of {len(comparable)} comparable vectors "
            f"moved when only {axis} changed: "
            f"{', '.join(sorted(diff['moved']))}. The harness can OBSERVE "
            "persona's masking."
        )
    else:
        verdict = "unmoved"
        detail = (
            f"all {len(comparable)} comparable vectors read IDENTICALLY when "
            f"{axis} changed. On the layer axis this is the PS-97 shape: the "
            "code the layer was supposed to change did not change what the "
            "page sees."
        )

    return {
        "axis": axis,
        "engine": engine,
        "verdict": verdict,
        "detail": detail,
        # What the LAUNCHES did about chromium's sandbox. A plain boolean at
        # the top of the document so a consumer scanning records cannot miss
        # it, with the per-arm truth on each arm and the prose in `notes`.
        "sandbox_waived": bool(before.sandbox_waived or after.sandbox_waived),
        "notes": _sandbox_notes(before, after),
        "comparable_vectors": comparable,
        "diff": diff,
        "before": before.as_record(),
        "after": after.as_record(),
    }


def dumps(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


__all__ = [
    "AXIS_LAYER",
    "AXIS_SEED",
    "Arm",
    "DEFAULT_CONTROL_SEED",
    "DEFAULT_SEED",
    "PROBE_SETTLE_SECONDS",
    "build_differential_record",
    "dumps",
    "read_probe_once",
    "run_differential",
]
