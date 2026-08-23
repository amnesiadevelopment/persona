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
    """One side of a differential: a label, a reading, and what produced it."""

    label: str
    reading: ProbeReading
    layer: LayerReport
    seed: int
    error: str = ""

    def as_record(self) -> dict:
        return {
            "label": self.label,
            "seed": self.seed,
            "layer": self.layer.as_record(),
            "reading": self.reading.as_record(),
            "error": self.error,
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
) -> Arm:
    """Launch ONE engine against the local page and read its vectors.

    Deliberately drives the SAME code path a real run uses — the engine is
    constructed by :mod:`browser_tier` and the layer installed by
    :mod:`masking_layer` — rather than a bespoke launch written for this
    demonstration. A differential proven through a path the real run does not
    have would prove nothing about the real run.

    The page is read through ``inner_text``, which is also how the browser tier
    reads a real checker (``page.evaluate`` is blocked by CSP on real checker
    pages — measured, and recorded in ``browser_tier``'s docstring). One reading
    path, so this cannot succeed by a route the harness lacks.

    An engine that will not start is recorded on the arm as an error, not
    raised: a differential that cannot run is a result worth reporting with its
    reason.
    """
    import time

    from .browser_tier import EngineUnavailable

    sleep = sleep or time.sleep
    label = f"{engine}/seed{seed}/layer={'on' if install_layer else 'off'}"

    captured: "list[LayerReport]" = []
    try:
        page_text = _drive_engine(
            url,
            seed=seed,
            engine=engine,
            install_layer=install_layer,
            settle_seconds=settle_seconds,
            sleep=sleep,
            layer_sink=captured.append,
        )
    except EngineUnavailable as exc:
        return Arm(
            label=label,
            reading=ProbeReading(vectors={}, note=str(exc)),
            layer=captured[0] if captured else absent_layer(str(exc)),
            seed=seed,
            error=str(exc),
        )

    return Arm(
        label=label,
        reading=parse_probe_text(page_text),
        layer=captured[0] if captured else absent_layer(
            "the engine ran but reported no layer"
        ),
        seed=seed,
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
) -> str:
    """Open ``url`` in a persona engine with/without the layer, return its text.

    A local page needs no proxy, and the engine is given none. That is the whole
    point of the venue: this runs in a container with no credential.
    """
    from .browser_tier import CHROMIUM, EngineUnavailable
    from .masking_layer import DEFAULT_LOCALE, context_for, install_firefox_layer

    if engine == CHROMIUM:
        from .chromium_tier import ChromiumSession, ChromiumUnavailable

        try:
            session = ChromiumSession(
                "",
                seed=seed,
                install_layer=install_layer,
            )
        except ChromiumUnavailable as exc:
            raise EngineUnavailable(str(exc)) from exc
        with session as live:
            layer_sink(session.layer_report)
            return _load_and_read(live, url, settle_seconds, sleep)

    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as exc:
        raise EngineUnavailable(
            f"persona's engine (invisible_playwright) is not importable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    kwargs: "dict[str, Any]" = {"headless": True, "humanize": False}
    if seed:
        kwargs["seed"] = seed
    try:
        eng = InvisiblePlaywright(**kwargs)
    except Exception as exc:
        raise EngineUnavailable(
            f"could not construct persona's engine: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        with eng as live:
            # See browser_tier: the engine returns a Browser here, which has no
            # add_init_script and whose new_page() opens a throwaway context.
            # The layer needs one explicit context to live in.
            live, _note = context_for(live)
            if install_layer:
                layer_sink(install_firefox_layer(live, seed, locale=DEFAULT_LOCALE))
            else:
                layer_sink(
                    absent_layer(
                        "install_layer=False: the packaged engine with NONE of "
                        "persona's masking layer — the control arm."
                    )
                )
            return _load_and_read(live, url, settle_seconds, sleep)
    except EngineUnavailable:
        raise
    except Exception as exc:
        raise EngineUnavailable(
            f"persona's engine failed during the differential: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


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
) -> dict:
    """Stand up the local page, read it twice varying ONE axis, and report.

    Returns the whole result as a dict — both arms with their layer reports,
    the diff, and a verdict. It is a document rather than a boolean because the
    interesting outcomes are not binary: see the module docstring on what an
    empty ``moved`` means, which depends on whether the page could compute the
    vectors at all.
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
            )
            after = read_probe_once(
                url, seed=seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
            )
        else:
            # ONE axis: layer on BOTH sides, only the seed moves.
            before = read_probe_once(
                url, seed=seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
            )
            after = read_probe_once(
                url, seed=control_seed, engine=engine, install_layer=True,
                settle_seconds=settle_seconds,
            )

    return build_differential_record(axis, engine, before, after)


def build_differential_record(
    axis: str, engine: str, before: Arm, after: Arm
) -> dict:
    """Assemble the differential document from two arms.

    Split out from :func:`run_differential` so the reporting logic — which is
    where a wrong verdict would actually come from — is testable without
    launching a browser.
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
