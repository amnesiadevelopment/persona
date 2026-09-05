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


def firefox_builds_in_use(session_builds) -> "set[str] | None":
    """Which firefox builds the RUNNING sessions are executing from, or None.

    This is the JOIN the engine prune consults (PS-221). ``session_builds`` is
    a zero-arg CALLABLE returning the launcher's ``running_session_builds()`` —
    ``{name: (engine, build) | None}`` over exactly the running names. A
    callable rather than the dict itself so the read happens HERE, inside this
    function's own guard, and a launcher that raises becomes a None rather than
    an exception in the prune path.

    ⚠️ IT DOES NOT READ ``Profile.last_launch_build``, AND THAT IS THE POINT
    ---------------------------------------------------------------------------
    An earlier version of this function resolved each running name through that
    persisted stamp, on the reasoning that the stamp is only stale for a
    STOPPED profile, so intersecting it with the running names yields the live
    fact. **That reasoning is false**, and it is false on the ordinary path
    rather than in an exotic corner:

    * the stamp is written by a hook that fires AFTER the session is registered
      (``launcher.start_thread``), so between those two points the profile is
      reported as running while the record still names the PREVIOUS launch's
      build. Every launch passes through that window;
    * that write is best-effort and its failure is swallowed, which LEAVES the
      previous launch's build standing rather than clearing it.

    Both yield a running profile whose stamp names a build it is not on, and
    sparing "the builds in use" then deletes the live build and spares a dead
    one. The intersection does not save it, because the value being intersected
    is stale for a RUNNING profile — not only for a stopped one.

    So the live question is asked of the live object. The launcher records the
    build in the same locked block that registers the session, which makes
    "running" and "what it is running" one atomic fact with no window between.

    RETURNS None FOR "UNKNOWN", AND THAT IS THE WHOLE POINT
    -------------------------------------------------------
    The caller spares every build in the returned set and reclaims the rest, so
    a set is an affirmative claim that every OTHER build is free. That claim
    may only be made when EVERY running session was resolved. If even one was
    not, the honest answer is None — "cannot say" — and the caller defers
    wholesale, exactly as it did before this join existed.

    ⚠️ "EVERY RUNNING SESSION" IS WIDER THAN "EVERY SESSION THIS PROCESS
    STARTED". The precondition above is a claim about what is RUNNING, so the
    input's key set must be too — see cases 3 and 4 below and
    ``BrowserLauncher.running_session_builds`` for why the map is keyed on
    ``running_profile_names() | _survivors | _indeterminate`` rather than on
    the running names alone. A live process this function cannot see does not
    make it cautious; it makes it confidently wrong. Note that every producer
    of None below is a property of a name that APPEARS in the map — none of
    them can fire for a name that never appears at all, which is why the key
    set, and not this function's branches, is where that class of defect lives.

    Six things produce None, and each is an ordinary state:

    1. Reading the launcher raised. Not evidence that nothing is running.
    2. A running name has no session entry: its spawn is in flight
       (``_starting``), so nothing has been registered for it yet. An in-flight
       launch is UNKNOWN BY CONSTRUCTION here — there is no stamp to be absent
       and no previous launch's value to mistake for this one's.
    3. A SURVIVOR — a browser a PREVIOUS persona left running, found at startup
       by ``scan_survivors``. It is a real live process executing a real build,
       but this process never launched it, so there is no session record of any
       kind to read. ``SessionRecord`` carries the engine and no build, so None
       is the honest answer rather than a gap; resolving one from
       ``active_build()`` would invent a confident wrong answer about a process
       that predates our startup. The launcher puts survivors in the map with a
       None value precisely so they land in this case: a name that were MISSING
       instead would not be an UNKNOWN at all — it would silently license the
       deletion of the build it is executing from, which is the one shape this
       whole guard exists to prevent.
    4. An INDETERMINATE — the other half of the same scan: a recorded session
       whose liveness could not be SETTLED (no psutil, permission denied, no
       create time captured at registration). ``Liveness.UNKNOWN`` means the
       question could not be answered, NOT "probably dead" — a record that
       probes GONE is dropped by the registry as it reads and is in neither
       bucket. So this is a real process that may well be running, and it is
       here for exactly the reason case 3 is. The psutil-absent shape is worth
       holding in mind because it fires for every record at once: with psutil
       unavailable NOTHING probes ALIVE, ``_survivors`` is empty, and case 3
       alone would protect nothing whatsoever.

       This does NOT make an indeterminate refuse a launch — that path
       deliberately fails OPEN, because refusing on no evidence costs the user
       their session while deferring a prune on no evidence costs one prune
       cycle. Nor does it wedge reclamation on a machine with no psutil: this
       function is only ever consulted once ``_engine_in_use`` has already said
       something IS running, and that gate reads the running NAMES, which an
       indeterminate is not in. An indeterminate ALONE therefore leaves the
       prune exactly as it was before this join existed.
    5. The session's build is None. ``engine_build_for`` returns None on ANY
       read failure, deliberately — a build that says the wrong thing is worse
       than no build. That None must not be read as "on no build".
    6. The session's engine is not firefox. A chromium build is a dotted
       version and is NOT comparable to ``firefox-NN`` (see this module's
       header) — it says nothing about which firefox build is free, so reading
       it as "no firefox build in use" would authorise exactly the deletion
       this guard exists to prevent.

    An EMPTY input maps to an empty set: nothing is running, so no firefox
    build is in use. The prune only asks once its own gate has said otherwise,
    and it treats that disagreement as UNKNOWN itself — see
    ``engine_install._in_use_build_numbers``.
    """
    try:
        sessions = session_builds()
    except Exception:
        logger.exception(
            "Could not read the running sessions while resolving which engine "
            "builds are in use; treating the answer as unknown"
        )
        return None

    builds: set[str] = set()
    for _name, entry in (sessions or {}).items():
        if not entry:
            # No session record for a name in the map: an in-flight spawn, a
            # survivor, or an indeterminate. UNKNOWN, never "free".
            return None
        engine, build = entry
        if engine != "firefox" or not build:
            # Not a firefox session, or its build could not be read at launch.
            # UNKNOWN, never "free".
            return None
        builds.add(build)
    return builds
