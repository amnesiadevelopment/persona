"""persona's OWN masking layer, installed onto a verification engine.

Why this module exists
----------------------
The checker matrix launched the packaged engines and read the checkers, and it
installed **none** of persona's masking layer. ``browser_tier`` constructed
``InvisiblePlaywright(headless, humanize, proxy, extra_prefs, seed)`` and
entered it; ``chromium_tier`` ran the engine binary with ``--fingerprint`` flags
and loaded no extension. Across all 22 files of ``services/verify`` there was
not one ``build_*_extension``, ``_install_spoof``, ``add_init_script`` or
``firefox_webgl_init_script`` — against 27 and 18 hits for the same patterns in
``services/browser/process.py`` and ``services/browser/invisible_launch.py``.

So every checker reading ever taken described **the two upstream engines persona
ships, configured with a seed and some flags**, not what an operator's profile
presents.

HOW IT WAS CAUGHT, because the shape generalises. PS-97 fixed a stride aliasing
bug in ``webgl_ext`` after two profiles differing only by seed handed CreepJS one
byte-identical ``webgl_pixel_hash``. A live two-seed re-read on the fixed branch
returned every rendered vector byte-identical to the pre-fix run, ``51df3565``
included. The fix had not failed — **the fixed code had never run.** A reading
through that harness could not move when persona's masking changed, because
persona's masking was not in it. Level 3 of the project bar is this matrix as a
regression gate, and a gate that cannot observe a change in the code it gates
does not gate it.

``browser_tier``'s own docstring anticipates exactly one version of this mistake
and closes it: *"It must be persona's engine, not stock Chromium — a reading
taken under stock Chromium describes stock Chromium and answers nothing about
persona."* The reasoning is right and stops one step short. The packaged engine
without persona's layer is nearer the product than stock Chromium and is still
not the product.

The seam, and why it is narrow
------------------------------
``browser_tier`` deliberately does not import ``spawn_browser``, and some of
that is sound: the app's launcher seeds bookmarks, writes desktop entries,
resolves a search-engine override and starts a proxy bridge, none of which a
checker run wants. **The requirement is the masking layer, not the whole
launcher.** This module is that layer and nothing else.

Everything here is BUILT BY THE SHIPPED BUILDERS. Not one line of spoof source
is written in this file, and that is the load-bearing constraint rather than
tidiness: a second copy of the spoof set, drifting from the one the product
launches, would reproduce the very defect this module exists to close — a
harness measuring something that is not the product — in a subtler and much
harder-to-catch form. If a builder's output changes, what the harness installs
changes with it, in the same commit, with no edit here.

The two routes, because the layer does not arrive the same way on both engines
-------------------------------------------------------------------------------
This asymmetry is real, it is in the product, and flattening it would be a lie:

* **Chromium** gets the layer as unpacked MV3 extensions —
  ``process.py``'s 13 ``build_*_extension`` calls, loaded with
  ``--load-extension``.
* **Firefox** reaches none of those. ``spawn_browser`` returns on the Firefox arm
  at ``process.py:353-356``, roughly 150 lines before the extension list is even
  assembled, so ``build_webgl_extension`` and friends are Chromium-only *by
  construction*. Firefox receives its layer through ``invisible_launch``'s
  ``add_init_script`` installs instead — the ``_install_spoof`` registry, which
  is the engine's only supported route (it has no MV3 unpacked-extension
  mechanism).

Both routes were missing from the harness. Both are installed here, and each arm
reports WHICH VECTORS IT ACTUALLY GOT so the record can state its subject rather
than implying it — see :class:`LayerReport`.

What is deliberately NOT installed
----------------------------------
``build_search_extension`` (a settings override, not masking), bookmark seeding,
desktop entries and the proxy bridge. Those are launcher furniture; including
them would widen the seam past the thing being measured and pull the harness
toward being a second copy of ``spawn_browser``.

The PS-78 rule this module obeys
--------------------------------
``add_init_script`` reaches only documents created AFTER it is registered.
Measured under PS-78: on a restore launch Firefox has already rebuilt its tabs
by the time ``__enter__`` returns, so an init script alone covers a first launch
and misses every restored tab — the override was present on launch 1 and ABSENT
on every launch after. So :func:`install_firefox_layer` registers each spoof for
new documents *and* replays it into the tabs that already exist, which is what
``invisible_launch._install_spoof`` / ``_apply_spoofs_to_open_tabs`` do for the
product. Every script involved is idempotent by construction (each pins
accessors or guards on its own realm marker), so a tab that did get the init
script is unaffected by the second application.

**And an install is still not a reading.** That a builder was called and a script
registered is not evidence the spoof reached the page a checker actually reads.
That claim is only settled by observing a vector MOVE on a real page, which is
what :mod:`local_probe` exists to do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

# The vector labels. These are the names a record uses to state which parts of
# the layer were installed, so "the layer" is never an opaque boolean.
WEBGL = "webgl"
AUDIO = "audio"
LOCALE = "locale"
NATIVE = "native"
STEALTH = "stealth"
MEASURETEXT = "measuretext"
VOICE = "voice"
DEVICE = "device"
GPU = "gpu"
CANVAS_CTX = "canvas_ctx"

# The default locale a harness run declares. ``en-US`` matches what
# ``spawn_browser`` uses for a profile with no proxy country
# (``process.py``: ``lang = _locale_for(proxy.country_code) if proxy else
# "en-US"``), so the harness is not inventing a value the product would not use.
DEFAULT_LOCALE = "en-US"

# The OS a harness profile declares when nothing else says. Matches
# ``browser_tier.DEFAULT_DECLARED_MACHINE``; passed in explicitly by callers
# that know better.
DEFAULT_OS_TYPE = "windows"


@dataclass(frozen=True)
class LayerReport:
    """What the masking layer ACTUALLY did on one engine, for the record header.

    The point of this type is that a record must never claim a subject it did
    not measure. An install can partially fail — a builder can raise, a context
    can refuse a script — and a header reading "layer: installed" over a run
    where the WebGL spoof never landed would be exactly the class of wrong
    record this whole ticket is about.

    So there are three fields and they are not redundant:

    ``installed``
        Vectors that were built AND handed to the engine. A claim about
        delivery, not about effect: see the module docstring's last paragraph.
    ``failed``
        ``{vector: reason}`` for anything that was attempted and did not land.
        Present in the record so a partial layer is visible as a partial layer.
    ``route``
        How the layer arrived — ``"extensions"`` (Chromium's MV3 unpacked
        extensions) or ``"init_scripts"`` (Firefox's ``add_init_script``
        registry). Recorded because the two engines genuinely differ and a
        consumer comparing arms needs to know it is comparing two routes, not
        one mechanism behaving oddly.
    """

    route: str
    installed: "tuple[str, ...]" = ()
    failed: "dict[str, str]" = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """True when every attempted vector landed."""
        return not self.failed

    def as_record(self) -> dict:
        """The header form. Sorted so two records diff cleanly."""
        return {
            "route": self.route,
            "installed": sorted(self.installed),
            "failed": {k: self.failed[k] for k in sorted(self.failed)},
            "complete": self.complete,
        }


# A report for an arm that never ran at all, so a caller always has one to put
# in the header rather than a None that every consumer has to special-case.
def absent_layer(reason: str) -> LayerReport:
    """The layer was not installed, and the record says so with a reason."""
    return LayerReport(route="none", installed=(), failed={"layer": reason})


# --- Firefox: the init-script route -----------------------------------------


def firefox_layer_scripts(
    seed: int,
    *,
    locale: str = DEFAULT_LOCALE,
) -> "list[tuple[str, str]]":
    """The per-profile spoof scripts ``invisible_launch`` installs, as data.

    Returns ``[(vector, js), ...]`` built by the SHIPPED builders — the exact
    three ``invisible_launch`` registers for a product launch that has chosen no
    resolution override:

    * ``locale``  — ``_language_override_script`` (``invisible_launch.py:2877``)
    * ``webgl``   — ``firefox_webgl_init_script`` (``:2904``, added by PS-78)
    * ``audio``   — ``firefox_audio_init_script`` (``:2928``, added by PS-73)

    ``outer-size`` is the one product spoof deliberately not here, and its
    absence is a property of the configuration rather than a gap: the product
    installs it only when a resolution was explicitly chosen
    (``if _res_overrides is not None``), and a harness profile chooses none, so
    a launch of this shape would not install it either. Including it would make
    the harness present something the equivalent product profile does not.

    Imported inside the function, not at module import: ``invisible_launch``
    pulls the whole browser stack, and a caller reading the JSON tier or printing
    ``--help`` must not be made to import an engine.
    """
    from ..browser.audio_ext import firefox_audio_init_script
    from ..browser.invisible_launch import _language_override_script
    from ..browser.webgl_ext import firefox_webgl_init_script

    scripts: "list[tuple[str, str]]" = []
    # An empty locale makes _language_override_script a documented no-op; keep
    # the vector out of the list rather than registering an empty script, so
    # `installed` never names a vector that delivered nothing.
    locale_js = _language_override_script(locale)
    if locale_js:
        scripts.append((LOCALE, locale_js))
    scripts.append((WEBGL, firefox_webgl_init_script(seed)))
    scripts.append((AUDIO, firefox_audio_init_script(seed)))
    return [(vector, js) for vector, js in scripts if js]


def context_for(live: Any) -> "tuple[Any, str]":
    """Get a live BROWSER CONTEXT out of whatever the engine handed back.

    THIS FUNCTION EXISTS BECAUSE ITS ABSENCE WAS A REAL, MEASURED DEFECT, and
    the shape of that defect is the whole subject of this ticket arriving one
    level down.

    ``InvisiblePlaywright.__enter__`` returns a ``Browser`` when no
    ``profile_dir`` is set (the harness's case) and a ``BrowserContext`` when one
    is. **A playwright ``Browser`` has no ``add_init_script`` and no ``pages``.**
    So the first version of :func:`install_firefox_layer` registered nothing at
    all: every vector landed in ``failed`` and the layer was silently absent.

    It was caught only because the differential was run for real and reported
    ``unmoved`` with an empty ``installed`` list. Had the report claimed success
    on the strength of "we called the builder", this would have shipped as a
    harness that installs a layer it does not install — the exact failure class
    (PS-11) this subsystem exists to detect, reproduced inside the instrument
    built to detect it.

    A ``Browser``'s ``new_page()`` creates a throwaway context per call, so an
    init script has nowhere to live. Taking ONE explicit context and running
    every page in it is what gives the spoofs a realm that outlives a page —
    and it is the supported path: the engine patches its own context defaults
    before handing the browser back, so a context made this way carries them.

    Returns ``(context, note)``; the note names which case was taken, so a
    record can say it rather than a reader having to infer it.
    """
    if hasattr(live, "add_init_script"):
        return live, "the engine returned a BrowserContext; used directly"
    if hasattr(live, "new_context"):
        return (
            live.new_context(),
            "the engine returned a Browser (no add_init_script, no pages), so "
            "ONE explicit context was opened to carry the layer",
        )
    raise TypeError(
        "the engine returned an object that is neither a BrowserContext nor a "
        "Browser: persona's masking layer has nowhere to install."
    )


def install_firefox_layer(
    ctx: Any,
    seed: int,
    *,
    locale: str = DEFAULT_LOCALE,
    scripts: "list[tuple[str, str]] | None" = None,
) -> LayerReport:
    """Install persona's Firefox masking layer onto a live CONTEXT.

    ``ctx`` must be a ``BrowserContext`` — pass whatever the engine returned
    through :func:`context_for` first. A raw ``Browser`` has neither
    ``add_init_script`` nor ``pages``, and handing one here installs NOTHING;
    that was a measured defect, not a hypothetical, and it is why this function
    now refuses rather than reporting ten failures.

    TWO deliveries per spoof, and the second is not optional — see the PS-78 rule
    in the module docstring. ``add_init_script`` covers documents created after
    registration; ``page.evaluate`` covers the tabs that already exist. The
    engine opens a page before ``__enter__`` returns, so without the replay the
    very first tab a run might reuse would be unspoofed.

    A per-spoof failure is RECORDED, never raised. One vector failing to install
    must not take down a run that can still honestly report the rest — the
    record's job is to say what was measured, and "the audio spoof did not land"
    is a fact worth having rather than a reason to have no record at all.
    """
    if not hasattr(ctx, "add_init_script"):
        return absent_layer(
            "install_firefox_layer was handed an object with no "
            "add_init_script (most likely a playwright Browser rather than a "
            "BrowserContext), so NOTHING was installed. Route it through "
            "masking_layer.context_for first."
        )

    try:
        pairs = scripts if scripts is not None else firefox_layer_scripts(
            seed, locale=locale
        )
    except Exception as exc:
        return absent_layer(
            f"persona's Firefox spoof scripts could not be built: "
            f"{type(exc).__name__}: {exc}"
        )

    installed: "list[str]" = []
    failed: "dict[str, str]" = {}

    for vector, js in pairs:
        try:
            ctx.add_init_script(js)
        except Exception as exc:
            failed[vector] = (
                f"add_init_script refused it: {type(exc).__name__}: {exc}"
            )
            continue
        installed.append(vector)

    # ...and into the tabs that ALREADY EXIST. Nothing is navigated: a reload
    # would be the product-side mistake PS-73 encoded against (it would clobber
    # a restored session), and `evaluate` runs the same source in the live realm.
    pages = list(getattr(ctx, "pages", ()) or ())
    for page in pages:
        for vector, js in pairs:
            if vector not in installed:
                continue
            try:
                page.evaluate(js)
            except Exception as exc:
                # A tab with no browsingContext (the fx-19 dead default tab)
                # raises on any eval and has nothing to patch — the next document
                # in it comes from the init script. Recorded rather than
                # swallowed for every OTHER reason, because the symptom of a
                # silent swallow here is a tab that quietly keeps host values,
                # which is the defect this module exists to close.
                if "browsingContext" in str(exc):
                    continue
                failed.setdefault(
                    vector,
                    f"registered for new documents, but replay into an "
                    f"already-open tab failed: {type(exc).__name__}: {exc}",
                )

    return LayerReport(
        route="init_scripts",
        installed=tuple(installed),
        failed=failed,
    )


# --- Chromium: the unpacked-extension route ---------------------------------


def build_chromium_layer(
    profile_dir: str,
    seed: int,
    *,
    os_type: str = DEFAULT_OS_TYPE,
    locale: str = DEFAULT_LOCALE,
    generation: int = 0,
) -> "tuple[list[str], LayerReport]":
    """Build persona's Chromium masking extensions into ``profile_dir``.

    Returns ``(extension_dirs, report)``. The dirs go on ``--load-extension``;
    the report goes in the record header.

    The set mirrors ``spawn_browser``'s masking extensions and stops there. What
    is left out is left out on purpose and is named in the module docstring:
    ``build_search_extension`` is a settings override rather than masking,
    ``build_geo_extension`` needs proxy coordinates this harness does not carry,
    and ``build_mobile_extension`` belongs to a mobile profile which a checker
    run is not.

    ``generation`` is the hardware generation, defaulting to ``0`` — the value
    ``models.hardware_generation.normalize_generation`` gives a profile that
    predates the field, i.e. the visible pool as it originally shipped. A
    default rather than a guess at the newest: it is the one value that cannot
    silently move an existing reading.

    A builder that raises is recorded against its vector and the rest still
    build, for the same reason the Firefox arm records per-spoof failures.
    """
    from ..browser.audio_ext import build_audio_extension
    from ..browser.canvas_ctx_ext import build_canvas_ctx_extension
    from ..browser.device_ext import build_device_extension
    from ..browser.gpu_ext import build_gpu_extension
    from ..browser.locale_ext import build_locale_extension
    from ..browser.measuretext_ext import build_measuretext_extension
    from ..browser.native_ext import build_native_extension
    from ..browser.stealth_ext import build_stealth_extension
    from ..browser.voice_ext import build_voice_extension
    from ..browser.webgl_ext import build_webgl_extension

    def _dir(name: str) -> str:
        return os.path.join(profile_dir, name)

    # (vector, thunk). Ordered as ``process.py`` appends them, so a diff against
    # the product's list reads straight down.
    builders: "list[tuple[str, Callable[[], str]]]" = [
        (NATIVE, lambda: build_native_extension(_dir(".persona-native-ext"))),
        (LOCALE, lambda: build_locale_extension(
            locale, _dir(".persona-locale-ext"))),
        (VOICE, lambda: build_voice_extension(
            locale, _dir(".persona-voice-ext"), os_type=os_type)),
        (STEALTH, lambda: build_stealth_extension(_dir(".persona-stealth-ext"))),
        (MEASURETEXT, lambda: build_measuretext_extension(
            _dir(".persona-measuretext-ext"))),
        (AUDIO, lambda: build_audio_extension(
            seed, _dir(".persona-audio-ext"))),
        (DEVICE, lambda: build_device_extension(
            seed, _dir(".persona-device-ext"), generation, os_type=os_type)),
        (WEBGL, lambda: build_webgl_extension(
            seed, _dir(".persona-webgl-ext"))),
        (GPU, lambda: build_gpu_extension(
            seed, os_type, _dir(".persona-gpu-ext"), generation)),
        (CANVAS_CTX, lambda: build_canvas_ctx_extension(
            os_type, _dir(".persona-canvas-ctx-ext"))),
    ]

    dirs: "list[str]" = []
    installed: "list[str]" = []
    failed: "dict[str, str]" = {}
    for vector, build in builders:
        try:
            dirs.append(build())
        except Exception as exc:
            failed[vector] = f"{type(exc).__name__}: {exc}"
            continue
        installed.append(vector)

    return dirs, LayerReport(
        route="extensions",
        installed=tuple(installed),
        failed=failed,
    )


__all__ = [
    "AUDIO",
    "CANVAS_CTX",
    "DEFAULT_LOCALE",
    "DEFAULT_OS_TYPE",
    "DEVICE",
    "GPU",
    "LOCALE",
    "MEASURETEXT",
    "NATIVE",
    "STEALTH",
    "VOICE",
    "WEBGL",
    "LayerReport",
    "absent_layer",
    "context_for",
    "build_chromium_layer",
    "firefox_layer_scripts",
    "install_firefox_layer",
]
