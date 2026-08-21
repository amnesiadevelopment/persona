"""What engine build a profile is being launched under, resolved at launch.

WHY THIS EXISTS
---------------
A profile records what it presents, but not what PRODUCED what it presents.
Without the build, "this profile's identity has not moved" is unanswerable in
the general case: comparing what a profile exposes now against what it exposed
before is only interpretable if you know which build each reading came from.
Otherwise an engine update and a genuine masking regression look identical.

THE TWO SHAPES ARE NOT NORMALISED, ON PURPOSE
---------------------------------------------
The engines report their builds in genuinely different shapes:

* **firefox** — a ``firefox-NN`` release tag (``engine/firefox.py``
  ``current_version`` → ``invisible_launch.active_build()``), sometimes
  carrying the package's cache-dir suffix (``firefox-18_151.0_20260724001829``).
* **chromium** — a dotted release tag from ``ENGINE_DIR/version.txt``
  (``engine/updater.py`` ``current_version``).

These are not two renderings of one scale — ``firefox-18`` and
``151.0.8000.10`` cannot be compared, ordered, or folded into a common format
without inventing a relationship that does not exist. So each engine's own
string is recorded VERBATIM and returned ALONGSIDE the engine that reported it.
A build identifier that cannot say which engine produced it is not provenance:
it is a string that will eventually be compared against a string from the other
engine and yield a meaningless answer.

This is deliberately NOT parsed, ordered, or validated into a shape. Every
consumer needs the engine label to interpret the build anyway, and the pair is
what makes the reading honest.

NOT A SECOND SOURCE OF TRUTH ABOUT THE INSTALLED BUILD
------------------------------------------------------
``engine/updater.py`` owns "what is installed". This module answers a different
question — "what did THIS launch run under" — by READING that owner rather than
keeping its own record. The two can legitimately disagree once the engine
updates, and that disagreement is the entire point: a profile stamped with an
older build than the one installed is exactly the signal a drift comparison
needs. Nothing here writes a version file or decides which build to install.

FAILING TO RESOLVE IS A None, NEVER A GUESS
-------------------------------------------
Every failure path returns None rather than a fallback build. A stamp that says
the wrong build is worse than no stamp at all, because the comparison it enables
returns a confident false answer, whereas None reads as "not known" and the
comparison correctly declines. There is deliberately no default constant here.
"""

from ...core.logging import get_logger
from ...models.profile import Profile

logger = get_logger("browser.launch_provenance")


def engine_build_for(engine: str) -> str | None:
    """What the named engine reports as its installed build, or None.

    ``engine`` must already be the EFFECTIVE engine (see ``resolve``) — this
    function does not resolve a profile's stored engine, it only dispatches on
    a resolved one.

    Returns the engine's own string verbatim. An engine that reports '' (its
    own "not installed" answer) yields None, because an empty string is the
    absence of a build, not a build named "".
    """
    try:
        if engine == "firefox":
            from ..engine.firefox import current_version as firefox_version

            build = firefox_version()
        else:
            from ..engine.updater import current_version as chromium_version

            build = chromium_version()
    except Exception:
        # Reading the installed build is best-effort. Anything raising here
        # (missing engine package, unreadable version file, an import error in
        # a partially installed tree) means we do not KNOW the build — which is
        # precisely what None says.
        logger.exception(
            "Could not read the installed build for engine %r; the launch will "
            "be recorded with no build stamp", engine,
        )
        return None
    build = (build or "").strip()
    return build or None


def resolve(profile: Profile) -> tuple[str, str | None]:
    """The (engine, build) pair to stamp on ``profile`` for THIS launch.

    The engine is the EFFECTIVE engine, not ``profile.engine``, and the
    difference is load-bearing rather than pedantic: a mobile profile stored as
    ``firefox`` actually launches chromium (``browser/process.py``
    ``effective_engine``), and the stored value is editable after the fact. A
    consumer that later re-derived the engine from the record would attribute
    the build to the wrong engine — which is the one way a provenance pair can
    be actively misleading rather than merely absent.

    The engine is always returned (it is known from the launch itself); only
    the build can be None.
    """
    from .process import effective_engine

    try:
        engine = effective_engine(profile)
    except Exception:
        # effective_engine consults the coherence rules and can in principle
        # raise on a malformed record. Fall back to the stored engine rather
        # than dropping the stamp entirely: the stored value is the best thing
        # known about a record whose resolution failed, and it is still true
        # that SOME engine ran.
        logger.exception(
            "Could not resolve the effective engine for profile %r; falling "
            "back to its stored engine", profile.name,
        )
        engine = getattr(profile, "engine", "chromium") or "chromium"
    return engine, engine_build_for(engine)
