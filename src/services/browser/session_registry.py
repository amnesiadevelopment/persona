"""A record of which profiles have a browser running, that SURVIVES persona.

WHY THIS EXISTS. ``BrowserLauncher`` keeps its live sessions in a plain dict of
``Popen`` handles, and that dict dies with the process. So after persona
restarts, ``is_running()`` answers "not running" about a profile whose browser
is alive on screen — the launch guard is not broken, it is asking a question
whose only source of truth was destroyed. The user then launches a second
browser against the same profile directory (PS-223).

Clean shutdown already clears this file: ``shutdown_all`` is registered with
``atexit`` and forgets every record as it terminates the sessions. THE GAP IS
EVERY EXIT THAT IS NOT CLEAN — a crash, a kill from Task Manager, or the
``execv``/``os._exit`` restart path. On those the browsers survive and, until
this module, persona forgot them.

THE CENTRAL RULE: A RECORD IS NOT EVIDENCE. A record left behind by a crash
reads byte-identically to a record of a live session. Treating the file as the
answer would lock a user out of their own profile permanently — a worse bug
than the double launch it set out to prevent. So every read is GROUNDED IN A
LIVENESS PROBE of the recorded process, and the file only ever tells us WHICH
PID TO ASK ABOUT.

That rule is not a matter of taste here; it is what the engine itself does.
Measured against a real chromium (PS-223), ``--user-data-dir`` pointed at one
directory:

  * second launch while the first is ALIVE  -> refused, exit code 21,
    ``Failed to create .../SingletonLock: File exists (17)`` and
    "Aborting now to avoid profile corruption";
  * the owner SIGKILLed, its ``SingletonLock`` symlink still on disk ->
    chromium RECOVERS the stale lock and launches normally.

So the engine already distinguishes a live owner from a stale lock file. A
persona that refused on the presence of a RECORD would be STRICTER THAN THE
ENGINE IT LAUNCHES, refusing a profile the browser itself would happily open,
with no way out from the UI. Hence :class:`Liveness` is TRI-STATE and its
``UNKNOWN`` fails OPEN.

WHY THE PID ALONE IS NOT ENOUGH. Pids are reused. A record naming pid 4242 and
an unrelated process that has since inherited 4242 are indistinguishable by pid,
and blocking on that mistake is the same lockout in a different costume. Each
record therefore stores the process's CREATE TIME, taken at registration, and a
probe only answers ALIVE when the pid is live AND its start time still matches.
When the create time could not be captured, the record cannot discriminate
reuse at all, so the probe answers UNKNOWN rather than guessing ALIVE — the
fail-open direction, on purpose.
"""

from __future__ import annotations

import contextlib
import enum
import json
import os
import threading
import time
from dataclasses import asdict, dataclass

from ...core.logging import get_logger
from ...utils.atomic import atomic_write_json

logger = get_logger("browser.session_registry")

#: Tolerance when comparing a recorded create time against a live one.
#: psutil derives it from the kernel's clock-tick resolution, and a value
#: round-tripped through JSON is a float either way, so an exact == would be
#: fragile in the direction that matters (a false "different process" reads as
#: GONE, which unblocks a launch against a browser that IS running). A second
#: is far tighter than any real pid-reuse window and far looser than the noise.
_CREATE_TIME_TOLERANCE = 1.0

#: Bumped only if the on-disk shape changes incompatibly. A file whose version
#: we do not recognise is IGNORED rather than guessed at — an unreadable
#: registry must degrade to "no records", which fails open, never to a refusal
#: built on a shape we cannot parse.
_VERSION = 1


class Liveness(enum.Enum):
    """What a probe of a recorded process actually established.

    THREE STATES, NOT TWO, and the third is the whole point. ``UNKNOWN`` means
    "the question could not be answered" — psutil missing, permission denied,
    a record with no create time to check reuse against. It must never be
    collapsed into ALIVE (that refuses a launch on no evidence, the lockout) and
    must never be silently collapsed into GONE either (that would claim the
    browser is dead when nobody looked). Callers branch on it explicitly: a
    guard treats UNKNOWN as "allow, and say so".
    """

    ALIVE = "alive"
    GONE = "gone"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SessionRecord:
    """One profile's browser session, as it can be described ACROSS a restart.

    Frozen because a caller reads these to decide whether to refuse a launch;
    a mutable record handed out of the registry could be edited into a
    different verdict by the code doing the asking.
    """

    profile: str
    pid: int
    #: Process start time as psutil reports it, or None when it could not be
    #: read at registration. None is NOT "0" and is not a wildcard: it disables
    #: the pid-reuse discriminator, which downgrades the probe to UNKNOWN.
    create_time: float | None
    #: Process group recorded at launch, for an explicit teardown later. PS-185
    #: measured ~35 surviving processes per launch, so killing the recorded pid
    #: alone would leave most of a session running.
    pgid: int | None
    engine: str
    started_at: float
    #: The persona pid that wrote this record. Diagnostics only — survivorship
    #: is decided by "the record was on disk at startup", never by comparing
    #: pids, because our own pid may itself be a reused one.
    owner_pid: int

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(raw: dict) -> "SessionRecord | None":
        """Parse one record, or None if it is not usable.

        Returns None rather than raising: a single corrupt entry must not take
        the whole registry down with it, because an unreadable registry means
        "no records", and no records means every launch is allowed. Losing one
        entry costs a missed refusal; losing the file costs nothing worse.
        """
        try:
            pid = int(raw["pid"])
        except (KeyError, TypeError, ValueError):
            return None
        profile = raw.get("profile")
        if not isinstance(profile, str) or not profile:
            return None
        if pid <= 0:
            return None
        ct = raw.get("create_time")
        pgid = raw.get("pgid")
        return SessionRecord(
            profile=profile,
            pid=pid,
            create_time=float(ct) if isinstance(ct, (int, float)) else None,
            pgid=int(pgid) if isinstance(pgid, int) and pgid > 0 else None,
            engine=str(raw.get("engine") or "unknown"),
            started_at=float(raw.get("started_at") or 0.0),
            owner_pid=int(raw.get("owner_pid") or 0),
        )


def capture_create_time(pid: int) -> float | None:
    """The start time of ``pid``, or None if it cannot be read.

    Called at REGISTRATION, while we still hold a live handle, so the value
    recorded describes the process we actually launched. None is honest — it
    disables the reuse check and so makes later probes answer UNKNOWN, which
    fails open.
    """
    try:
        import psutil
    except Exception:  # pragma: no cover - psutil is a declared dependency
        return None
    try:
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def liveness_of(record: SessionRecord) -> Liveness:
    """Is the process this record names STILL THE PROCESS IT NAMED?

    This is the function the whole module exists to provide, and every refusal
    in the product must be grounded in it rather than in the record's presence.

    The order of the checks is deliberate:

    1. **No psutil** -> UNKNOWN. The dependency is declared, but an install
       that lost it must not thereby start refusing launches; it must stop
       being able to refuse them. (``process_group.py`` records the mirror of
       this trap: a psutil-less container once measured a teardown as CLEAN
       precisely because the measuring code silently answered "nothing there".)
    2. **No such process** -> GONE. The unambiguous case: nothing holds the pid.
    3. **Zombie** -> GONE. A zombie is an exited process awaiting a reap. Its
       pid is still resolvable, so without this it reads ALIVE and blocks a
       launch against a browser that has already died.
    4. **Create time disagrees** -> GONE. Something else wears this pid now.
       The record is stale, and stale must not block (the ticket's named
       failure mode: the record and the process disagree).
    5. **Create time unreadable, or never recorded** -> UNKNOWN. We can see
       *a* process on the pid but cannot show it is *ours*. Refusing here would
       be a refusal on no evidence.
    """
    try:
        import psutil
    except Exception:  # pragma: no cover - psutil is a declared dependency
        logger.warning(
            "Cannot establish whether %s's browser (pid %s) is alive: psutil is "
            "unavailable, so the launch guard fails OPEN for this profile.",
            record.profile, record.pid,
        )
        return Liveness.UNKNOWN

    try:
        proc = psutil.Process(record.pid)
    except psutil.NoSuchProcess:
        return Liveness.GONE
    except Exception:
        return Liveness.UNKNOWN

    try:
        if proc.status() == psutil.STATUS_ZOMBIE:
            return Liveness.GONE
    except psutil.NoSuchProcess:
        return Liveness.GONE
    except Exception:
        # A status we cannot read is not itself evidence of death; fall through
        # to the identity check, which is the stronger question anyway.
        pass

    if record.create_time is None:
        return Liveness.UNKNOWN

    try:
        live_create_time = proc.create_time()
    except psutil.NoSuchProcess:
        return Liveness.GONE
    except Exception:
        return Liveness.UNKNOWN

    if abs(live_create_time - record.create_time) > _CREATE_TIME_TOLERANCE:
        # Same pid, different process: the browser died and the number was
        # handed to something else. Treating this as ALIVE would refuse a
        # launch on behalf of an unrelated process.
        return Liveness.GONE
    return Liveness.ALIVE


def terminate_record(record: SessionRecord, *, timeout: float = 10.0) -> bool:
    """Tear down the browser a record names. Returns True if it is gone after.

    ONLY EVER CALLED FROM AN EXPLICIT USER GESTURE. The ticket forbids silently
    adopting or silently killing a surviving browser, and both directions lose
    the user's work in the same way; this function is the "yes, close it" half
    of a question the user was asked.

    It reaps the process GROUP, not the pid, for the reason PS-192 landed and
    PS-185 measured: roughly 35 processes survive a launch, so signalling the
    recorded pid alone leaves the session running while reporting success. The
    group is resolved through ``process_group.resolve_group``, which already
    refuses to signal a group we did not create and refuses to signal our own —
    without those guards a stale pid would hand a group kill to whatever now
    holds the number, up to and including persona itself.
    """
    from .process_group import _signal_group, resolve_group, signallable_group
    import signal as _signal

    if liveness_of(record) is Liveness.GONE:
        return True

    pgid = signallable_group(record.pgid or resolve_group(record.pid))
    deadline = time.time() + timeout

    if pgid is not None:
        _signal_group(pgid, _signal.SIGTERM)
    else:
        # No signallable group — fall back to the single process. This is the
        # weaker teardown and it is deliberately the fallback, not the default.
        with contextlib.suppress(Exception):
            import psutil

            psutil.Process(record.pid).terminate()

    while time.time() < deadline:
        if liveness_of(record) is Liveness.GONE:
            return True
        time.sleep(0.2)

    if pgid is not None:
        _signal_group(pgid, _signal.SIGKILL)
    else:
        with contextlib.suppress(Exception):
            import psutil

            psutil.Process(record.pid).kill()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if liveness_of(record) is Liveness.GONE:
            return True
        time.sleep(0.2)

    # Report what was OBSERVED, not what was attempted. PS-204 exists because a
    # teardown signal reported a success it had not achieved.
    logger.warning(
        "Survivor teardown for %s (pid %s) did not confirm the process is gone",
        record.profile, record.pid,
    )
    return False


class SessionRegistry:
    """The persisted set of running-session records.

    Keyed by profile NAME, matching the launcher's own dicts, because that is
    what the launch guard asks about.

    Every mutation rewrites the file atomically (``atomic_write_json``): a
    truncate-in-place that loses power mid-write would leave a half-written
    registry, and this file's whole job is to be readable after an unclean
    stop — the one situation where it is guaranteed to be read.

    A WRITE THAT FAILS IS SWALLOWED, LOUDLY LOGGED, AND NEVER RAISED. This
    registry is a safety catch on a launch path; a disk that cannot be written
    must not become a browser that cannot be launched.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> list[SessionRecord]:
        """Every record on disk. An unreadable registry is an EMPTY one.

        Failing open is the deliberate behaviour: a registry that cannot be
        read cannot justify refusing anything, and a corrupt file must not
        strand the user with an unlaunchable profile.
        """
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> list[SessionRecord]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.warning(
                "Running-session registry at %s is unreadable (%s); treating it "
                "as empty, so no launch is refused on its account.",
                self._path, exc,
            )
            return []
        if not isinstance(raw, dict) or raw.get("version") != _VERSION:
            return []
        entries = raw.get("sessions")
        if not isinstance(entries, list):
            return []
        out: list[SessionRecord] = []
        for entry in entries:
            if isinstance(entry, dict):
                rec = SessionRecord.from_json(entry)
                if rec is not None:
                    out.append(rec)
        return out

    def _save_locked(self, records: list[SessionRecord]) -> bool:
        """Write the file. **False if the write did not happen.**

        ⚠️ THE RETURN VALUE EXISTS SO A NO-OP CAN BE REPORTED (PS-353). The
        failure is SWALLOWED here on purpose — a registry that cannot be
        written must never cost the user a session, which is the same
        fail-open direction the whole module is built on — but swallowing the
        exception must not also swallow the FACT. Before this returned
        anything, `record()` answered True for a write this method had just
        warned it could not perform, and `launcher.py`'s monitor logged "the
        restart guard now covers this session" directly beneath that warning.
        A safety catch whose failure is silent is the defect this module
        exists to avoid; announcing a guard that is not there is worse still,
        because it is the sentence the operator reads as the outcome.
        """
        try:
            atomic_write_json(
                self._path,
                {"version": _VERSION, "sessions": [r.to_json() for r in records]},
            )
        except Exception as exc:
            logger.warning(
                "Could not write the running-session registry at %s (%s); the "
                "launch guard will not survive a restart for these sessions.",
                self._path, exc,
            )
            return False
        return True

    def record(self, rec: SessionRecord) -> bool:
        """Add or replace the record for ``rec.profile``. True if it was kept.

        ⚠️ THE RETURN VALUE IS NOT DECORATIVE (PS-353), AND IT ANSWERS FOR
        BOTH WAYS A WRITE CAN FAIL TO STICK — the record being unrepresentable,
        and the file being unwritable. It is False for either.

        **(1) A record whose pid is not probeable CANNOT SURVIVE ITS OWN
        FILE:** ``from_json`` drops it on the way back in, and — worse — the
        next ``record()`` of ANY profile rebuilds this file from
        ``_load_locked()``, so the unreadable row is physically ERASED and a
        post-mortem reader cannot even find the entry that would explain a
        missing guard. Writing one is a silent no-op, and a safety catch whose
        failure is silent is the defect this whole module exists to avoid.

        So it is REFUSED here and SAID OUT LOUD, rather than written and lost.
        This is a backstop, not the primary fix: :func:`make_record` already
        declines to build such a record, and callers are expected to notice
        there. It is kept because the constructor is public and a hand-built
        record must not be able to disappear either.

        **(2) A write ``_save_locked`` could not perform did not happen.** That
        failure is swallowed there (fail-open: an unwritable registry must not
        cost a session), and this method used to fall through to ``return
        True`` regardless — so a caller was told a row was kept that was
        never written, and ``launcher.py``'s monitor announced "the restart
        guard now covers this session" on the line after the warning saying it
        does not. That is this ticket's own defect class reproduced in the API
        added to fix it: a writer whose success signal cannot express its own
        no-op. The outcome is now threaded through, so False means the guard
        is genuinely absent for this session in BOTH cases and a caller that
        gates its claims on it cannot overstate them.
        """
        if rec.pid <= 0:
            logger.warning(
                "Refusing to record a running session for %s: the launch handle "
                "reported no probeable pid (%s), and such a record is dropped "
                "on the next read — so the launch guard will NOT survive a "
                "restart for this session.",
                rec.profile, rec.pid,
            )
            return False
        with self._lock:
            records = [r for r in self._load_locked() if r.profile != rec.profile]
            records.append(rec)
            return self._save_locked(records)

    def forget(self, profile: str) -> None:
        """Drop the record for ``profile``. Idempotent.

        Called from every teardown path. Idempotence matters because those
        paths cannot know whether a record was ever written (a launch that
        failed before registration has none), and making them ask first would
        be both a race and a nuisance.
        """
        with self._lock:
            records = self._load_locked()
            kept = [r for r in records if r.profile != profile]
            if len(kept) != len(records):
                self._save_locked(kept)

    def forget_all(self) -> None:
        """Drop every record — the clean-shutdown counterpart.

        This is what makes "a record was on disk at startup" mean "persona did
        not exit cleanly last time", which is the entire survivor signal.
        """
        with self._lock:
            if self._load_locked():
                self._save_locked([])

    def live_records(self) -> tuple[list[SessionRecord], list[SessionRecord]]:
        """``(alive, indeterminate)`` — probed, never merely read.

        Records that probe GONE are dropped from the file as a side effect:
        they describe processes that no longer exist, and leaving them would
        accumulate stale entries that each cost a probe on every launch.

        The two lists are returned SEPARATELY rather than summed because they
        license different actions. ``alive`` may justify refusing a launch;
        ``indeterminate`` may not — it is the "we could not tell" bucket, and
        the caller's duty there is to allow the launch and say why.
        """
        with self._lock:
            records = self._load_locked()
            alive: list[SessionRecord] = []
            unknown: list[SessionRecord] = []
            gone = False
            for rec in records:
                state = liveness_of(rec)
                if state is Liveness.ALIVE:
                    alive.append(rec)
                elif state is Liveness.UNKNOWN:
                    unknown.append(rec)
                else:
                    gone = True
            if gone:
                self._save_locked(alive + unknown)
        return alive, unknown


def default_registry() -> SessionRegistry:
    """The registry at the configured location.

    Resolved at CALL time, not import time, so a test (or a portable install)
    that points PERSONA_HOME somewhere else gets the registry that goes with it.
    """
    from ...core import config

    return SessionRegistry(config.SESSIONS_FILE)


def probeable_pid(proc) -> "int | None":
    """The pid on ``proc`` that a liveness probe could actually ask about, or
    ``None`` when the handle carries none.

    THE ONE HANDLE SHAPE THAT CARRIES NONE, and why this predicate exists
    (PS-353). ``InvisibleProcess`` runs the Firefox session as a forked CHILD
    on Linux and as a THREAD of persona on Windows/macOS, because
    ``needs_fork_launch()`` is ``IS_LINUX``. On the thread arm there is no child
    process, so the handle honestly sets ``pid = 0`` — and every line involved
    was individually right, which is why this survived review: ``pid = 0`` is
    honest, ``from_json``'s ``pid <= 0`` rejection is honest (a pid of 0 is not
    probeable, and the module's central rule is that a record is not evidence),
    and the old ``int(getattr(proc, "pid", 0) or 0)`` honestly recorded what the
    handle reported.

    THE GAP WAS AT THE SEAM: the writer accepted a handle shape the persistence
    format cannot represent, and NEITHER END KNEW. ``launcher.py`` wraps the
    write in ``contextlib.suppress(Exception)`` for a correct reason of its own
    ("a registry write that cannot happen must cost us the guard, never the
    session"), so there was no path on which the writer could report its own
    no-op. Measured: the pid-0 row reached disk, a fresh reader dropped it, and
    the NEXT ``record()`` of any profile rewrote the file without it — so a
    post-mortem reader could not even find the entry that explained the missing
    guard.

    This is the predicate that makes the no-op reportable. It is deliberately a
    question about the HANDLE and not about the platform: a caller keys off
    "can this be represented" rather than re-deriving ``needs_fork_launch()``,
    which would put a second copy of the launch-arm decision in a module whose
    whole job is to know nothing about how a browser was started.
    """
    try:
        pid = int(getattr(proc, "pid", 0) or 0)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def make_record_for_pid(
    profile_name: str, pid: int, engine: str, *, pgid: "int | None" = None
) -> "SessionRecord | None":
    """Build a record around a pid resolved OUTSIDE a launch handle.

    The thread arm has no child process at ``spawn`` time, but its browser IS a
    real set of OS processes — the close-watch resolves them from the profile's
    own ``-profile`` argument and kills each with its tree at teardown. Those
    pids arrive on the engine's pipe (``LIFECYCLE watch-pids``) a second or two
    after launch, which is the only honest moment to record one: at ``spawn``
    the engine has not started, and at TEARDOWN it is far too late for a guard
    that has to survive a restart.

    Returns ``None`` rather than a record with an unprobeable pid, for the same
    reason :func:`make_record` does — see :func:`probeable_pid`.

    ⚠️ ``pgid`` DEFAULTS TO None ON THIS PATH AND THAT IS CORRECT, not an
    omission. ``_child`` calls ``start_own_session()`` only on the FORK arm (a
    session is process-global state, so ``setsid`` in a thread would move
    persona's OWN session — a recorded platform gap, not an oversight). So the
    thread arm's engine parent sits in PERSONA'S group, and recording that
    number would hand a later ``terminate_record`` a group kill aimed at
    persona itself. ``signallable_group`` refuses our own group and would catch
    it, but the record should not carry the hazard in the first place; the
    single-process fallback is the honest teardown for this arm.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    return SessionRecord(
        profile=profile_name,
        pid=pid,
        create_time=capture_create_time(pid),
        pgid=pgid if isinstance(pgid, int) and pgid > 0 else None,
        engine=engine,
        started_at=time.time(),
        owner_pid=os.getpid(),
    )


def make_record(profile_name: str, proc, engine: str) -> "SessionRecord | None":
    """Build a record from a live ``Popen`` handle, or ``None`` if the handle
    carries no probeable identity.

    The create time is captured HERE, while the process is known to be the one
    just launched. Capturing it later — at probe time, from the record — would
    be circular: it would compare the process against itself and could never
    detect reuse.

    ⚠️ ``None`` IS A REAL RETURN VALUE AND MUST BE HANDLED (PS-353), not an
    error case to shrug at. It means "this handle cannot be written down", and
    the caller's duty is to SAY SO rather than write a row the reader will
    discard. See :func:`probeable_pid` for which handle shape produces it and
    why every line that produced the old silent no-op was individually correct.

    ⛔ THE FIX IS NOT TO RELAX ``from_json``'s ``pid <= 0`` GUARD. An
    unprobeable record would then load and reach ``liveness_of``, which fails
    open to UNKNOWN — but ``running_session_builds()`` reads the indeterminate
    bucket as "live, build unknown, do not prune", so a stale row would defer
    engine pruning forever. The guard is right; what was wrong is feeding it a
    value it cannot represent.
    """
    from .process_group import recorded_group

    pid = probeable_pid(proc)
    if pid is None:
        return None
    pgid = None
    with contextlib.suppress(Exception):
        pgid = recorded_group(proc)
    return make_record_for_pid(profile_name, pid, engine, pgid=pgid)
