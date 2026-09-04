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
reports WHICH VECTORS IT ACTUALLY GOT — measured against WHICH VECTORS THAT
CONFIGURATION SHOULD HAVE GOT, declared separately — so the record can state its
subject rather than implying it. See :class:`LayerReport`, and
:data:`CHROMIUM_VECTORS` for why that second set has to be its own authority.

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
GEO = "geo"

# The vectors persona's FIREFOX layer actually builds, in install order. Named
# as data so a subtraction arm can be checked against the real set rather than
# against a name someone typed — see ``firefox_layer_scripts(vectors=...)``.
# Firefox reaches none of the Chromium extension builders (``spawn_browser``
# returns on the Firefox arm ~150 lines before the extension list is assembled),
# so this is deliberately three names and not the full vector vocabulary above.
FIREFOX_VECTORS = (LOCALE, WEBGL, AUDIO)

# The vectors persona's CHROMIUM layer installs, in the order ``process.py``
# appends them. **THIS IS AN INDEPENDENT DECLARATION AND THAT INDEPENDENCE IS
# THE ENTIRE POINT.**
#
# It deliberately does NOT read the builder thunks in ``build_chromium_layer``,
# and must never be computed from them. A set derived from the same list it
# validates is tautological: ``expected == installed ∪ failed`` by construction,
# a builder dropped from the list drops out of ``expected`` in the same edit,
# and :attr:`LayerReport.complete` can never go false for a vector that was
# never attempted — which reproduces exactly the blindness this constant exists
# to close, behind a new field name.
#
# The drift class is not hypothetical: PS-103 and PS-150 were both a harness
# vector set that had quietly diverged from the product's, and both were caught
# by hand. Two authorities that must be edited SEPARATELY are what turns that
# into a record that says so.
#
# ``geo`` is NOT here. It is not an omission: ``build_chromium_layer``'s
# ``include_geo`` is off by default, so a default run genuinely should not
# install it and must not start reporting ``complete: false`` for not having
# been asked. See :func:`chromium_expected_vectors`, which adds it when — and
# only when — the configuration asked for it.
CHROMIUM_VECTORS = (
    NATIVE,
    LOCALE,
    VOICE,
    STEALTH,
    MEASURETEXT,
    AUDIO,
    DEVICE,
    WEBGL,
    GPU,
    CANVAS_CTX,
)


# The default locale a harness run declares. ``en-US`` matches what
# ``spawn_browser`` uses for a profile with NO PROXY (``process._profile_locale``
# returns it directly for ``proxy is None``, which is deliberate policy: persona
# forces en-US so it never leaks the host locale, #218). So the harness is not
# inventing a value the product would not use.
#
# ⚠️ It is NOT what a PROXIED profile gets. Since PS-240 that path derives the
# locale from the exit country and REFUSES rather than falling back here — a
# proxied harness profile is a different case and must not reuse this constant
# as if it were the product's answer.
DEFAULT_LOCALE = "en-US"

# The OS a harness profile declares when nothing else says. Matches
# ``browser_tier.DEFAULT_DECLARED_MACHINE``; passed in explicitly by callers
# that know better.
DEFAULT_OS_TYPE = "windows"


def chromium_expected_vectors(*, include_geo: bool = False) -> "tuple[str, ...]":
    """The vectors a Chromium layer of THIS CONFIGURATION should install.

    Declared from :data:`CHROMIUM_VECTORS` — never from the builder list — and
    parameterised by the one documented configuration switch, so it states "the
    set this configuration should install" rather than "the set the loop
    happened to enumerate".

    ``search`` and ``mobile`` are absent because the product's own set for a
    desktop checker run does not contain them (a settings override and a mobile
    profile respectively — see the module docstring). Their absence is a
    property of the configuration, not a gap, so they must never make a record
    incomplete.
    """
    return CHROMIUM_VECTORS + ((GEO,) if include_geo else ())


def firefox_expected_vectors(
    *,
    locale: str = DEFAULT_LOCALE,
    vectors: "tuple[str, ...] | None" = None,
) -> "tuple[str, ...]":
    """The vectors a Firefox layer of THIS CONFIGURATION should install.

    Declared from :data:`FIREFOX_VECTORS` — never from the script pairs
    :func:`firefox_layer_scripts` produced — and narrowed by the same two
    configuration inputs that narrow what is actually installed:

    ``vectors``
        A subtraction arm (PS-119) is **not an incomplete layer**; it is a
        complete layer of a deliberately smaller configuration. If ``expected``
        ignored the narrowing, every differential arm would start reporting
        ``complete: false`` and the differential's own records would become the
        thing that is wrong.
    ``locale``
        An EMPTY locale makes ``_language_override_script`` a documented no-op,
        and ``firefox_layer_scripts`` keeps the vector out of the pairs so
        ``installed`` never names a vector that delivered nothing. **DECIDED
        HERE, EXPLICITLY:** an empty locale is a configuration choice of exactly
        the same class as ``include_geo=False`` — the caller did not ask for a
        locale override — so ``expected`` narrows with it and such a run stays
        ``complete: true``.

        This is narrowed on the ARGUMENT, not on the produced script, and that
        distinction is what keeps it a tripwire rather than a rubber stamp: if
        ``_language_override_script`` ever returned empty for a NON-empty
        locale, ``expected`` would still name ``locale`` and the record would
        correctly read incomplete.
    """
    declared = tuple(v for v in FIREFOX_VECTORS if v != LOCALE or locale)
    if vectors is None:
        return declared
    keep = set(vectors)
    return tuple(v for v in declared if v in keep)


# The DECLARED set for each route, used when a report states no expectation of
# its own. Keyed by ``route`` because the route is what says which engine's
# layer the report is about, and each engine has its own declared set. A route
# not named here (``"none"``, the absent-layer arm) declares nothing — that
# report already carries its reason in ``failed``.
_ROUTE_EXPECTATIONS: "dict[str, tuple[str, ...]]" = {
    "extensions": CHROMIUM_VECTORS,
    "init_scripts": FIREFOX_VECTORS,
}


@dataclass(frozen=True)
class LayerReport:
    """What the masking layer ACTUALLY did on one engine, for the record header.

    The point of this type is that a record must never claim a subject it did
    not measure. An install can partially fail — a builder can raise, a context
    can refuse a script — and a header reading "layer: installed" over a run
    where the WebGL spoof never landed would be exactly the class of wrong
    record this whole ticket is about.

    So there are four fields and they are not redundant:

    ``installed``
        Vectors that were built AND handed to the engine. A claim about
        delivery, not about effect: see the module docstring's last paragraph.
    ``failed``
        ``{vector: reason}`` for anything that was attempted and did not land.
        Present in the record so a partial layer is visible as a partial layer.
    ``expected``
        The vectors this configuration SHOULD have installed, declared
        independently of the loop that installs them (see
        :data:`CHROMIUM_VECTORS` / :data:`FIREFOX_VECTORS` and their two
        configuration-aware helpers). ``installed`` and ``failed`` between them
        can only speak about vectors that were ATTEMPTED, so before this field
        existed a vector that was never attempted was in neither and could not
        lower ``complete``: a short builder list produced a record that never
        mentioned the missing vector and still read ``complete: true``. That is
        the drift class PS-103 and PS-150 both were, caught by hand both times.
        Empty means "this report makes no claim about a declared set" — the
        pre-existing behaviour, kept so a hand-built report is not forced to
        invent one.
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
    expected: "tuple[str, ...] | None" = None

    def __post_init__(self) -> None:
        # An UNSTATED expectation defaults to the DECLARED set for this route —
        # never to what this report happens to contain, which would be the
        # circularity the whole field exists to avoid. This is what closes the
        # degenerate case: ``LayerReport(route="extensions")`` with nothing
        # installed and nothing failed used to read ``complete: true``, which is
        # this defect at its limit. It now names ten missing vectors.
        #
        # An EXPLICIT ``expected=()`` is honoured and means "this report makes
        # no claim about a declared set" — that is why the sentinel is ``None``
        # rather than the empty tuple.
        if self.expected is None:
            object.__setattr__(
                self, "expected", _ROUTE_EXPECTATIONS.get(self.route, ())
            )

    @property
    def missing(self) -> "tuple[str, ...]":
        """Vectors ``expected`` names that were NEVER ATTEMPTED.

        Not in ``installed``, and not in ``failed`` either — so nothing in the
        record before this property could say a word about them. Sorted, so two
        records diff cleanly.

        The ``or ()`` is for the type checker, not for the runtime:
        ``__post_init__`` guarantees ``expected`` is never ``None`` after
        construction, but the DECLARED type is optional (``None`` is the
        sentinel meaning "unstated", distinct from an explicit empty tuple) and
        mypy reads the declaration. Written this way rather than with a
        ``cast`` so it reads beside the sentinel the docstring already explains.
        """
        attempted = set(self.installed) | set(self.failed)
        return tuple(sorted(v for v in (self.expected or ()) if v not in attempted))

    @property
    def complete(self) -> bool:
        """True when every expected vector was attempted AND landed.

        JOINS the ``failed`` check, never replaces it: an attempted-and-failed
        vector still reports incomplete for exactly the reason it always did.
        """
        return not self.failed and not self.missing

    def as_record(self) -> dict:
        """The header form. Sorted so two records diff cleanly."""
        return {
            "route": self.route,
            "installed": sorted(self.installed),
            "failed": {k: self.failed[k] for k in sorted(self.failed)},
            "expected": sorted(self.expected or ()),
            "missing": list(self.missing),
            "complete": self.complete,
        }


# A report for an arm that never ran at all, so a caller always has one to put
# in the header rather than a None that every consumer has to special-case.
def absent_layer(reason: str) -> LayerReport:
    """The layer was not installed, and the record says so with a reason."""
    return LayerReport(
        route="none", installed=(), failed={"layer": reason}, expected=()
    )


# --- Firefox: the init-script route -----------------------------------------


def firefox_layer_scripts(
    seed: int,
    *,
    locale: str = DEFAULT_LOCALE,
    vectors: "tuple[str, ...] | None" = None,
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

    ``vectors`` NARROWS the set to the named subset, and exists for exactly one
    caller: the subtraction arm of a differential (PS-119 step 3). When a live
    checker names the layer, the question "WHICH spoof did it see?" is answered
    by removing one vector at a time and re-reading — a measurement, rather than
    an argument about which line of generated source looks suspicious. ``None``
    means the full product set and is the default, because a reading of a subset
    does NOT describe the product and must never be reached by accident.

    A name that is not a vector this engine builds is refused rather than
    silently ignored: a typo that quietly produced the FULL layer would report a
    subtraction that never happened, and the arm would look like an exoneration
    of the vector the operator thought they had removed.

    Imported inside the function, not at module import: ``invisible_launch``
    pulls the whole browser stack, and a caller reading the JSON tier or printing
    ``--help`` must not be made to import an engine.
    """
    from ..browser.audio_ext import firefox_audio_init_script
    from ..browser.invisible_launch import _language_override_script
    from ..browser.webgl_ext import firefox_webgl_init_script

    if vectors is not None:
        unknown = sorted(set(vectors) - set(FIREFOX_VECTORS))
        if unknown:
            raise ValueError(
                f"not vector(s) persona's firefox layer builds: "
                f"{', '.join(unknown)}. This engine builds "
                f"{', '.join(FIREFOX_VECTORS)}. Refusing rather than ignoring "
                f"the name: a subtraction arm that silently installed the FULL "
                f"layer would read as an exoneration of the vector you meant "
                f"to remove."
            )

    scripts: "list[tuple[str, str]]" = []
    # An empty locale makes _language_override_script a documented no-op; keep
    # the vector out of the list rather than registering an empty script, so
    # `installed` never names a vector that delivered nothing.
    locale_js = _language_override_script(locale)
    if locale_js:
        scripts.append((LOCALE, locale_js))
    scripts.append((WEBGL, firefox_webgl_init_script(seed)))
    scripts.append((AUDIO, firefox_audio_init_script(seed)))
    if vectors is not None:
        keep = set(vectors)
        scripts = [(v, js) for v, js in scripts if v in keep]
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
    vectors: "tuple[str, ...] | None" = None,
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
            seed, locale=locale, vectors=vectors
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
        # Declared from FIREFOX_VECTORS, narrowed by the same two configuration
        # inputs that narrowed what was installed — never derived from `pairs`,
        # which is the list this is meant to catch drifting. A caller that
        # supplied `scripts=` explicitly has stated its own pair list and is
        # therefore not asking about the declared configuration at all, so it
        # gets no expectation rather than a fabricated one.
        expected=(
            firefox_expected_vectors(locale=locale, vectors=vectors)
            if scripts is None
            else ()
        ),
    )


# --- Chromium: the unpacked-extension route ---------------------------------


def build_chromium_layer(
    profile_dir: str,
    seed: int,
    *,
    os_type: str = DEFAULT_OS_TYPE,
    locale: str = DEFAULT_LOCALE,
    generation: int = 0,
    include_geo: bool = False,
) -> "tuple[list[str], LayerReport]":
    """Build persona's Chromium masking extensions into ``profile_dir``.

    Returns ``(extension_dirs, report)``. The dirs go on ``--load-extension``;
    the report goes in the record header.

    The set mirrors ``spawn_browser``'s masking extensions and stops there. What
    is left out is left out on purpose and is named in the module docstring:
    ``build_search_extension`` is a settings override rather than masking, and
    ``build_mobile_extension`` belongs to a mobile profile which a checker run
    is not.

    ``include_geo`` adds ``build_geo_extension`` in DENY mode, closing a
    TIER-VERSUS-PRODUCT gap rather than widening the seam. This exclusion used
    to be unconditional, on the stated ground that the builder "needs proxy
    coordinates this harness does not carry". That reason does not survive
    reading the product: ``process.py`` builds the extension for EVERY proxied
    profile and passes ``None, None`` when the exit has no usable coordinates,
    so coordinate-less is a case the builder is DESIGNED for and the product
    exercises — not a blocker. Every checker reading in this campaign is
    proxied, so the product surface always carries this extension and the tier
    never did.

    Off by default, because it changes what the harness installs and an
    existing reading must not move underneath a caller that did not ask. The
    record names the vector in ``installed`` either way, so which surface was
    read is never inferred.

    ``generation`` is the hardware generation, defaulting to ``0`` — the value
    ``models.hardware_generation.normalize_generation`` gives a profile that
    predates the field, i.e. the visible pool as it originally shipped. A
    default rather than a guess at the newest: it is the one value that cannot
    silently move an existing reading.

    A builder that raises is recorded against its vector and the rest still
    build, for the same reason the Firefox arm records per-spoof failures.
    """
    builders = _chromium_builders(
        profile_dir,
        seed,
        os_type=os_type,
        locale=locale,
        generation=generation,
        include_geo=include_geo,
    )

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
        # THE INDEPENDENT DECLARATION. Read from CHROMIUM_VECTORS, not from
        # `builders` — a set computed from the list above would equal
        # `installed | failed` by construction and could never catch a builder
        # that quietly went missing from it, which is the entire point.
        expected=chromium_expected_vectors(include_geo=include_geo),
    )


def _chromium_builders(
    profile_dir: str,
    seed: int,
    *,
    os_type: str,
    locale: str,
    generation: int,
    include_geo: bool,
) -> "list[tuple[str, Callable[[], str]]]":
    """The hand-maintained ``(vector, thunk)`` list, mirroring ``process.py``.

    Split out of :func:`build_chromium_layer` so THIS list — the thing that
    drifts — can be shortened in a test while the real build loop, the real
    record construction and the real ``expected`` declaration all still run.
    That is what makes the non-circularity test (PS-242 AC3) a test of the
    PRODUCTION path rather than of a hand-built report: a circular ``expected``
    computed from this list would shrink with it and the record would go on
    reading ``complete: true``.
    """
    from ..browser.audio_ext import build_audio_extension
    from ..browser.canvas_ctx_ext import build_canvas_ctx_extension
    from ..browser.device_ext import build_device_extension
    from ..browser.engine_platform import engine_platform_for
    from ..browser.geo_ext import build_geo_extension
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
        # ``engine_platform`` is computed by the SAME function ``process.py``
        # uses, from the same inputs, rather than being assumed equal to
        # ``os_type`` — the assumption that they are equal is what leaked in the
        # product. This harness is a DESKTOP checker run by construction (it
        # declares one of browser_tier.DECLARED_MACHINES and carries no mobile
        # preset), so ``device_type`` is "desktop" here as a stated fact, not a
        # default that hides a case: a mobile declared machine is not a thing
        # this tier can be asked for.
        (GPU, lambda: build_gpu_extension(
            seed, os_type, _dir(".persona-gpu-ext"), generation,
            engine_platform=engine_platform_for(os_type, "desktop"))),
        (CANVAS_CTX, lambda: build_canvas_ctx_extension(
            os_type, _dir(".persona-canvas-ctx-ext"))),
    ]
    if include_geo:
        # Mirrors ``process.py``'s LAST extension, appended here in the same
        # position so a diff against the product's list still reads straight
        # down. DENY mode (lat/lon = None) because this harness carries no
        # proxy coordinates — which is not a shortfall but the PRODUCT'S OWN
        # BEHAVIOUR for that case: ``process.py`` computes
        # ``has_coords = proxy.lat is not None and proxy.lon is not None`` and
        # passes ``None, None`` when it is false, precisely so
        # ``getCurrentPosition`` cannot fall through to the real host
        # coordinates while the locale and timezone already name the exit
        # country.
        builders.append(
            (GEO, lambda: build_geo_extension(
                None, None, _dir(".persona-geo-ext")))
        )

    return builders


__all__ = [
    "AUDIO",
    "CANVAS_CTX",
    "CHROMIUM_VECTORS",
    "DEFAULT_LOCALE",
    "DEFAULT_OS_TYPE",
    "DEVICE",
    "FIREFOX_VECTORS",
    "GEO",
    "GPU",
    "LOCALE",
    "MEASURETEXT",
    "NATIVE",
    "STEALTH",
    "VOICE",
    "WEBGL",
    "LayerReport",
    "absent_layer",
    "chromium_expected_vectors",
    "context_for",
    "build_chromium_layer",
    "firefox_expected_vectors",
    "firefox_layer_scripts",
    "install_firefox_layer",
]
