"""Launch a browser into its OWN process group, and tear the GROUP down.

WHY THIS EXISTS
---------------
A browser is not a leaf process. Chromium forks ``--type=zygote``,
``--type=gpu-process`` and one renderer per tab, and a wrapper launch
(``fpchrome.AppImage``, ``xvfb-run``, a shell shim) adds another layer above
that. ``Popen(...).terminate()`` signals **only the pid we hold**. Every
descendant survives, is reparented to init, and is from that moment
**unreachable from any handle we ever had**.

That is not a theoretical leak. PS-192 recorded a chromium burning **361% CPU
for 12.5 hours** on a user's workstation, and PS-185 measured **~35 engine
processes surviving per launch** internally. The orphans accumulate for the
life of the container.

The failure mode is worse than the waste, because it does not announce itself.
An exhausted machine degrades a launch into a **contentless
``TargetClosedError``** — the error PS-133 records being misattributed to
fingerprint seed 4242. The leak stops looking like a leak and starts looking
like a property of the code under test.

TWO THINGS MUST BOTH BE TRUE
----------------------------
Neither half works alone, which is why they live in one module:

1. **The launch starts its own session** — ``start_new_session=True``, so the
   browser becomes a process-group leader (``pgid == pid``) and its whole
   descendant tree inherits that group. Without this there IS no group to
   signal: the children sit in *persona's own* group.
2. **The teardown signals the GROUP** — ``os.killpg``, not ``proc.kill()``.

``src/services/app_update/updater.py`` already did exactly this (``:496``,
``:954``, ``:476``). The technique was never in doubt here; it was simply never
applied to the paths that spawn process TREES. This module is that pattern,
factored into one place so a new launch site cannot get half of it.

⚠️ THE SELF-KILL HAZARD — READ BEFORE EDITING
----------------------------------------------
**A group kill aimed at a process that is NOT a group leader kills the caller.**
If a launch site forgets ``start_new_session=True``, the child sits in
*persona's* process group, and ``os.killpg(child_pid, SIGKILL)`` resolves to
persona's own group — terminating the app, the test runner, or the agent that
called it. The blast radius of the *fix* would exceed the leak.

So :func:`reap_process_group` **refuses to signal its own group** and falls
back to a single-process kill instead. That guard is what makes this safe to
call unconditionally, including on a mocked or legacy handle that never got a
session of its own.

This is the same class of self-inflicted wound the ticket flags for
``pkill -f`` — a pattern that matches its own command line — and it is the
reason this module keys on **process groups and recorded pids**, never on a
command-line substring.

PORTABILITY
-----------
``start_new_session`` is accepted on every platform: POSIX honours it,
Windows's ``_execute_child`` takes it as ``unused_start_new_session`` and
ignores it. ``os.killpg`` is POSIX-only, so the reaper degrades to
``proc.kill()`` where it does not exist. Passing the kwarg everywhere and
guarding only the kill keeps one code path for all three platforms.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

__all__ = [
    "NEW_SESSION_KWARGS",
    "group_of",
    "new_session_kwargs",
    "process_group_survivors",
    "reap_process_group",
    "record_group_by_construction",
    "recorded_group",
    "remember_group",
    "resolve_group",
    "signallable_group",
    "start_own_session",
    "terminate_process_group",
]


# Spread into a Popen call so every launch site reads identically and a reader
# can grep ONE token to find them all. A dict rather than a bare literal so the
# reason travels with the value.
NEW_SESSION_KWARGS = {"start_new_session": True}

#: Where `remember_group` stashes the pgid on a Popen handle. Underscored and
#: module-private: it is an implementation detail of this module's own
#: launch/teardown pair, not part of the handle's contract.
_GROUP_ATTR = "_persona_pgid"


def new_session_kwargs() -> dict:
    """Popen kwargs that make the child its own process-group leader.

    Returns a fresh dict each call so a caller may merge platform kwargs into
    it without mutating the shared constant.
    """
    return dict(NEW_SESSION_KWARGS)


def start_own_session() -> bool:
    """Make the CALLING process a session/group leader. For FORKED children.

    ``start_new_session=True`` is a ``Popen`` kwarg, so it is unavailable to a
    ``multiprocessing`` fork child (``invisible_launch``'s Linux path). Such a
    child calls this ITSELF, immediately after the fork, to reach the same
    state: ``pgid == pid``, with every engine process it goes on to spawn
    inheriting that group.

    ⚠️ FORKED CHILDREN ONLY — never call this from a thread. A session is
    PROCESS-global state: doing this on Windows/macOS, where the same ``_child``
    body runs as a thread of the manager process, would move persona's OWN
    session and orphan the app from its terminal.

    Never raises. ``setsid`` fails with ``EPERM`` when the caller is already a
    group leader, which is a state we can live with rather than a fault.
    """
    setsid = getattr(os, "setsid", None)
    if setsid is None:  # pragma: no cover - Windows
        return False
    try:
        setsid()
        return True
    except Exception:
        return False


def group_of(pid: int) -> "int | None":
    """The process group id of ``pid``, or ``None`` if it cannot be read.

    ``None`` means "do not attempt a group operation" — an exited process has
    no group to ask about, and guessing one is how a stale pid gets a signal
    meant for something else.
    """
    getpgid = getattr(os, "getpgid", None)
    if getpgid is None:  # pragma: no cover - Windows
        return None
    try:
        return getpgid(pid)
    except Exception:
        return None


def _own_group() -> "int | None":
    getpgrp = getattr(os, "getpgrp", None)
    if getpgrp is None:  # pragma: no cover - Windows
        return None
    try:
        return getpgrp()
    except Exception:  # pragma: no cover - defensive
        return None


def popen_in_new_session(args, **kwargs):
    """``subprocess.Popen`` + its own session + the group recorded. USE THIS.

    The single entry point every browser/tooling launch should use, because it
    makes the two halves of the fix INSEPARABLE. Passing
    ``start_new_session=True`` by hand works, but it leaves the caller free to
    forget :func:`remember_group` — and a teardown with no recorded group goes
    blind the moment the leader is waited on, which is precisely the failure
    path DoD #3 is about. PS-192's table lists seven launch sites; "a single
    missed site keeps the leak", so the correct thing is the EASY thing here.

    Returns the live ``Popen``. Raises whatever ``Popen`` raises — a launch
    that failed must not be silently swallowed into a fake handle.
    """
    # MERGED rather than spread alongside `kwargs`, so a caller that also
    # passes `start_new_session=True` (belt and braces, or a site part-way
    # through migration) gets the same launch instead of a TypeError about a
    # duplicate keyword. The session is never turned OFF here: an explicit
    # False would defeat the whole point, so it is ignored with the default.
    merged = dict(kwargs)
    merged.update(NEW_SESSION_KWARGS)
    proc = subprocess.Popen(args, **merged)

    # RECORDED BY CONSTRUCTION, NOT BY ASKING. `start_new_session=True` makes
    # the child call setsid() before exec, so its pgid IS its pid — that is
    # what the kwarg means. Querying the kernel for it instead (getpgid) loses
    # a race this ticket is specifically about: a wrapper that spawns its
    # children and exits IMMEDIATELY can be gone before the query runs, and a
    # reaped pid answers ESRCH, so the record comes back empty at exactly the
    # moment the orphans are the whole problem. Measured: it left 3 of 3
    # children alive in
    # `test_teardown_reaps_even_when_the_parent_already_exited`.
    try:
        setattr(proc, _GROUP_ATTR, proc.pid)
    except Exception:
        pass
    return proc


def remember_group(proc) -> "int | None":
    """Record the process group AT LAUNCH, while the leader is verifiably alive.

    Call this immediately after a ``Popen`` that passed
    :data:`NEW_SESSION_KWARGS`. It returns the pgid and stashes it on the
    handle for the teardown to reuse.

    ⚠️ WHY THE TEARDOWN CANNOT JUST ASK AGAIN. ``os.getpgid(pid)`` raises
    ESRCH once the leader has been **waited on** — but the GROUP outlives its
    leader: ``pgid == pid`` stays valid, and ``killpg`` keeps working, while
    ANY member survives. So a teardown that re-resolves gets ``None`` at
    exactly the moment the orphans it must kill are the whole problem, and
    refuses. That is the DoD #3 failure path — an exited parent whose children
    are the leak — and it is measured by
    ``test_teardown_reaps_even_when_the_parent_already_exited``, which FAILED
    against a re-resolving implementation.

    Recording at launch instead means the value was verified as a real leader
    (``pgid == pid``) at a moment we know it was ours, so the teardown never
    has to guess ``pgid = pid`` for a pid that may since have been recycled.

    PID REUSE, stated rather than hand-waved: the kernel keeps a pid allocated
    while it is in use as a process-group id, so the number cannot be recycled
    out from under us while any member of the group is still alive — which is
    exactly the window where the signal matters. Once the group is genuinely
    empty the signal is a no-op (ESRCH). This is the same guarantee
    ``updater.py`` relies on, made explicit and narrowed by recording the value
    only when the leader check passed.
    """
    pgid = resolve_group(getattr(proc, "pid", None))
    if pgid is None:
        return None
    try:
        setattr(proc, _GROUP_ATTR, pgid)
    except Exception:
        # A handle that refuses attributes (a mock, a __slots__ object) simply
        # falls back to live resolution; nothing breaks.
        pass
    return pgid


def record_group_by_construction(proc, pid: "int | None" = None) -> "int | None":
    """Record a FORKED child's group WITHOUT asking the kernel.

    The fork path (``invisible_launch``'s Linux launch) cannot use
    :func:`popen_in_new_session` — there is no ``Popen`` and no
    ``start_new_session`` kwarg — but it needs the identical guarantee, and for
    the identical reason. Its child calls :func:`start_own_session` as its first
    act, so ``pgid == pid`` holds there exactly as the Popen kwarg makes it hold
    elsewhere. This records that value.

    ⚠️ WHY NOT ``remember_group`` HERE. That one ASKS the kernel
    (``getpgid``), and the answer is unavailable at precisely the moment it
    matters: once the leader has been waited on, ``getpgid`` raises ESRCH and
    the record comes back empty *while the orphaned tree is still alive*. That
    is PS-192's DoD #3 failure path, measured on this very class: leader alive
    → 0 survivors, leader waited on first → ``resolve_group`` returns ``None``
    and 3 of 3 orphans survive ``terminate()`` + ``kill()``.

    ⚠️ THE RACE THIS DELIBERATELY TOLERATES. ``setsid()`` runs in the child a
    moment AFTER ``start()`` returns, so a signal sent in that window addresses
    a group that does not exist yet. That is a harmless ESRCH — **but it must
    never degrade into signalling persona's OWN group**, which is the self-kill
    hazard in the module docstring. It cannot: the value recorded is the
    CHILD's pid, never a group we belong to, and :func:`signallable_group`
    re-checks that on every use.
    """
    if pid is None:
        pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        setattr(proc, _GROUP_ATTR, pid)
    except Exception:
        # A handle that refuses attributes falls back to live resolution.
        return None
    return pid


def signallable_group(pgid: "int | None") -> "int | None":
    """The recorded ``pgid``, or ``None`` if signalling it is unsafe/impossible.

    :func:`recorded_group` returns what was stashed at launch WITHOUT consulting
    the platform or the caller's own group — that is what makes it survive the
    leader being reaped. This is the guard for the moment of USE, and it is the
    leader check the recorded path would otherwise skip:

    1. **No ``killpg``** (Windows) — the caller must fall back to a
       single-process kill rather than signal nothing at all.
    2. **Our own group** — the self-kill hazard. A group signal aimed at a
       group we belong to takes down persona, the test runner, or the agent.
    """
    if not isinstance(pgid, int) or pgid <= 0:
        return None
    if getattr(os, "killpg", None) is None:  # pragma: no cover - Windows
        return None
    mine = _own_group()
    if mine is not None and pgid == mine:  # pragma: no cover - defensive
        return None
    return pgid


def recorded_group(proc) -> "int | None":
    """The group recorded at launch, else a live resolution. None if neither.

    Prefers the recorded value precisely because live resolution goes blind the
    moment the leader is reaped — see :func:`remember_group`.
    """
    pgid = getattr(proc, _GROUP_ATTR, None)
    if isinstance(pgid, int) and pgid > 0:
        return pgid
    return resolve_group(getattr(proc, "pid", None))


def resolve_group(pid: "int | None") -> "int | None":
    """The process group this module is willing to SIGNAL for ``pid``.

    ``None`` means "no group may be signalled for this handle" and is the
    caller's cue to fall back to a single-process kill. Three refusals, each
    guarding a way a group kill can hit something we never launched:

    1. **Not a group LEADER.** Every launch site here passes
       ``start_new_session=True``, so a browser we started satisfies
       ``pgid == pid`` BY CONSTRUCTION. Anything else is a pid we did not put
       in a session of its own — a legacy handle, a mock, or a fabricated pid
       — and its group belongs to somebody else. (Two test fakes in this repo
       carry a hardcoded ``pid = 4242``; without this check a teardown would
       resolve whatever real process happens to hold 4242 and signal ITS
       group.)
    2. **Our own group.** See the self-kill hazard in the module docstring:
       this would take down persona, the test runner, or the agent.
    3. **Unreadable.** ``getpgid`` raising means the pid is gone. Guessing
       ``pgid = pid`` there is a PID-REUSE hazard — the recycled pid can name
       an unrelated process — so this refuses rather than guesses.
    """
    if not pid or pid <= 0:
        return None
    if getattr(os, "killpg", None) is None:  # pragma: no cover - Windows
        return None

    pgid = group_of(pid)
    if pgid is None or pgid != pid:
        return None

    mine = _own_group()
    if mine is not None and pgid == mine:  # pragma: no cover - defensive
        return None
    return pgid


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal an ALREADY-RESOLVED group. True if it was delivered.

    Takes a pgid rather than a pid deliberately: the caller resolves ONCE,
    before the escalation begins, and reuses that value. Re-resolving between
    the SIGTERM and the SIGKILL would read the pid after it may have been
    waited on and recycled.
    """
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        # Nothing left in the group — a successful teardown, not a failure to
        # be retried as a single-process kill.
        return True
    except Exception:
        return False


def terminate_process_group(proc, *, timeout: float = 10.0) -> bool:
    """SIGTERM the group, wait, then SIGKILL the group. True if a group was hit.

    The escalation is preserved deliberately: a browser asked politely first
    gets to flush its profile and release its lock, and only a browser that
    ignores that is killed outright. What changes versus ``proc.terminate()``
    is the AUDIENCE — the whole group rather than the one pid we hold.

    Never raises. Teardown runs from ``finally`` blocks and from failure paths,
    where masking the original error would hide the reason we are here at all.
    """
    sigterm = getattr(signal, "SIGTERM", 15)
    sigkill = getattr(signal, "SIGKILL", 9)

    # Resolve ONCE, before anything is signalled and before any wait() can
    # reap the pid and let it be recycled. Everything below reuses this value.
    #
    # PREFERS THE VALUE RECORDED AT LAUNCH. Live resolution goes blind the
    # moment the leader is waited on (getpgid raises ESRCH) — while the GROUP
    # is still alive and still killable. Re-resolving here therefore returns
    # None exactly when an exited parent's orphaned children are the whole
    # problem, which is the DoD #3 failure path. See `remember_group`.
    pgid = recorded_group(proc)

    # ⚠️ BRANCH ON DELIVERY, NOT ON THE EXISTENCE OF A pgid. A recorded group
    # is not the same as a group that can be SIGNALLED: `recorded_group`
    # returns the value stashed at launch without consulting `os.killpg`, which
    # does not exist on Windows. Keying the fallback on `pgid is not None`
    # therefore skipped the single-process branch on exactly the handles that
    # most needed it, `_signal_group` swallowed the AttributeError as False,
    # and the process was signalled NEITHER way — strictly worse than the code
    # this module replaced, and contrary to the PORTABILITY note above.
    # Measured by deleting `os.killpg`: terminate=False kill=False.
    #
    # `_signal_group` already reports delivery truthfully (ProcessLookupError
    # is a SUCCESS — an empty group is a completed teardown, not a failure to
    # retry), so its return value is the right thing to escalate on.
    group_hit = pgid is not None and _signal_group(pgid, sigterm)

    if not group_hit:
        # No group we could actually address (not a leader, a fabricated pid,
        # already gone, or no killpg on this platform). The single process this
        # handle names is the most we can safely reach.
        with contextlib.suppress(Exception):
            proc.terminate()

    try:
        proc.wait(timeout=timeout)
    except Exception:
        pass

    # Escalate REGARDLESS of the parent's exit status. The parent exiting says
    # nothing about its children — that gap is the entire defect this module
    # exists to close — so the group gets a SIGKILL either way. Members that
    # already left make this a no-op.
    #
    # Delivery-keyed for the same reason as the SIGTERM above: a recorded pgid
    # that cannot be signalled must still reach `proc.kill()`, or the handle
    # goes un-signalled by BOTH paths.
    kill_hit = pgid is not None and _signal_group(pgid, sigkill)

    if not kill_hit:
        with contextlib.suppress(Exception):
            proc.kill()

    with contextlib.suppress(Exception):
        proc.wait(timeout=5)
    # TRUE means "a group signal was actually DELIVERED", not merely "a pgid
    # was known". A caller measuring the fix needs to tell a group teardown
    # from a single-process fallback, and the old `pgid is not None` reported
    # success on the Windows path where nothing was signalled at all.
    return group_hit or kill_hit


def reap_process_group(proc, *, timeout: float = 10.0) -> bool:
    """Tear ``proc`` and its whole tree down. Safe to call more than once.

    The single entry point every teardown path should use — including the
    failure paths (timeout, exception, abnormal exit), which is where a leak of
    this size actually accumulates. A completed run must leave nothing behind,
    and a run that threw is still a completed run for that purpose.
    """
    if proc is None:
        return False
    # An already-reaped handle still gets the group signal: the parent's exit
    # is not evidence about its children.
    return terminate_process_group(proc, timeout=timeout)


def process_group_survivors(pgid: int) -> "list[int]":
    """Pids still alive in process group ``pgid`` — the measurement's evidence.

    Anchored on the GROUP, never on a command-line substring. PS-185's worker
    lost two cycles to a ``pkill -f chromium`` that matched its own command
    line; a pgid cannot match the process asking the question unless it truly
    is a member.

    A zombie is a corpse awaiting a ``wait()``, not a survivor — counting one
    would make a correct reap look like a failed one.

    ⚠️ RAISES WHEN IT CANNOT LOOK, RATHER THAN REPORTING AN EMPTY LIST. This
    is the MEASUREMENT'S OWN EVIDENCE FUNCTION, so "nothing survived" and "I
    was unable to check" must never render as the same value. Returning ``[]``
    on a missing ``psutil`` produced exactly that: PS-192's reviewer measured
    the product path as CLEAN in a container where psutil was absent, while
    ``ps`` showed 3 live processes — a green from a broken instrument, on the
    ticket about a leak that hides behind a green. PS-14's rule is to check the
    instrument before attributing anything to the product, and an instrument
    that cannot fail cannot be checked.
    """
    try:
        import psutil
    except Exception as exc:  # pragma: no cover - psutil is a declared dependency
        raise RuntimeError(
            "process_group_survivors cannot measure: psutil is unavailable, so "
            "'no survivors' would be indistinguishable from 'could not look'. "
            "psutil is a declared dependency (pyproject.toml); install it "
            "before drawing any conclusion from a survivor count."
        ) from exc

    alive: "list[int]" = []
    for proc in psutil.process_iter(["pid", "status"]):
        try:
            if os.getpgid(proc.info["pid"]) != pgid:
                continue
            if proc.info.get("status") == psutil.STATUS_ZOMBIE:
                continue
            alive.append(proc.info["pid"])
        except Exception:
            continue
    return sorted(alive)
