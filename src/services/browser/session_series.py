"""Record a running session's cpu and context-switch series. NOTHING READS IT.

⛔ READ THIS FIRST, BECAUSE THE NAME OF THIS FILE INVITES THE WRONG CLAIM.

This module RECORDS. It does not JUDGE, and there is no threshold, no verdict,
no watchdog and no termination path anywhere in it or downstream of it. That is
not an omission to be filled in later — it is the deliberate boundary the
reading that authorised this module drew, and the reason is measured rather
than cautious (``readings/ps349-2026-09-09/PROBE.md``, Recommendation 1 and the
table at :228-239):

    arm          truth       cpu med   ctxt min
    busymax      HEALTHY     103.9     9      <- healthy, answering every ping
    jugwedge     DEGRADED      0.5     18     <- WEDGED, HIGHER than the healthy one
    sigstop      DEGRADED      0.0     0
    spin         DEGRADED    110.2     202    <- WEDGED, higher than several healthy ones

The populations OVERLAP. A healthy arm went BELOW a wedged arm's
context-switch floor. Any threshold written today is a guess with a number on
it, and a killer acting on it would terminate a live session — with account
sessions open — on a signal a busy page reproduces.

⭐ AND THE HONEST FRAMING, WHICH A FUTURE READER MUST NOT BE ABLE TO SOFTEN.
PS-8 DoD #4 asks that a degrading session be OBSERVABLE. This makes it
observable AFTERWARDS, BY A PERSON READING THE RECORD. It does not make it
DETECTED, and it does not close DoD #4 — distinguishing a working run from a
stopped one is a calibration this project does not have, on real pages and real
hardware (Recommendation 4). What this ships is the substrate such a
calibration would be built from, and the before-and-after the NEXT stall
arrives carrying instead of being reconstructed by a person afterwards.

If you find yourself adding a comparison against a constant here, you have left
the slice. Go and read PROBE.md:464-477 — it authorises this recording and
forbids that judging, one paragraph apart.

────────────────────────────────────────────────────────────────────────────

THE FOUR DECISIONS THIS MODULE MAKES, IN WRITING (they are decisions, not
defaults, and each one is asserted by a test).

1. PLATFORM SCOPE — LINUX ALONE, AND THE PORTABLE LIBRARY DOES NOT WIDEN IT.

   The reader is ``/proc``. persona ships on three platforms and this series
   exists on one of them; that narrowing is NAMED here rather than papered
   over, and ``series_capability()`` is the machine-readable form of it.

   ⛔ THE TEMPTING WIDENING IS A LIE, NOT A COST. ``psutil`` IS a declared
   runtime dependency of this app (``psutil>=6.0`` in both requirements.txt
   and pyproject.toml, a security floor authored in core/peerauth.py), so
   ``Process.num_ctx_switches()`` is free to call and would appear to make
   this portable. Read what it returns off the two non-Linux arms:

       # psutil/_pswindows.py
       def num_ctx_switches(self):
           ctx_switches = self._proc_info()[pinfo_map['ctx_switches']]
           # only voluntary ctx switches are supported
           return ntp.pctxsw(ctx_switches, 0)        # <- involuntary is a LITERAL 0

       # psutil/_psosx.py
       def num_ctx_switches(self):
           # Unvoluntary value seems not to be available; ...
           vol = self._get_pidtaskinfo()[pidtaskinfo_map['volctxsw']]
           return ntp.pctxsw(vol, 0)                 # <- involuntary is a LITERAL 0

   On Windows and macOS the nonvoluntary leg is NOT MEASURED — it is a
   constant zero returned in the shape of a reading, indistinguishable from a
   genuine zero. Recording that would forge the exact ``sigstop`` signature
   (``ctxt 0``) on the two platforms where it is least checkable. The Linux
   arm of psutil genuinely parses both legs, which is what makes this a
   per-platform asymmetry rather than a library-wide convention.

   So: ``/proc`` on Linux, and on the other two platforms this module records
   NOTHING and says why (see ``series_capability``). An honest narrow delivery
   beats a portable one that reports a constant as a measurement.

2. DESTINATION AND IDENTIFYING KEY — INSIDE THE PROFILE'S OWN DATA DIR, AND
   THE PROFILE IS NAMED BY THE PATH, NEVER BY THE BYTES.

   The record lives at ``DATA_DIR/<profile>/.persona-session-series/series.jsonl``,
   beside the ``.persona-*-ext`` directories the launch already writes there.
   That is the containment property ``env_policy`` chose this location for: it
   is reached by ``delete_profile`` (the dir is MOVED to the trash with the
   profile) and destroyed outright by ``wipe_all_profiles``'s rmtree. The
   panic wipe therefore covers it for free, with no glob to extend and no
   second site to keep in step.

   ⛔ THE ALTERNATIVE WAS REJECTED FOR A SECURITY REASON, NOT A TIDINESS ONE.
   ``LOG_DIR`` is reached by the wipe only through ``_clear_logs_for_wipe``,
   whose glob is ``persona_*.log`` and nothing else — a series file at any
   other name or extension in that directory would SURVIVE the panic wipe
   carrying the operator's profile names, while the wipe reported success.

   PS-330's sharing convention, answered explicitly: *"Labels are
   user-identifying and are deliberately NOT recorded into a file the operator
   may share."* A series keyed by profile NAME is the operator's private
   label, so no profile name is written into this file's CONTENT at all — the
   profile is identified by WHERE the file is, which is a fact the filesystem
   already holds and which the wipe already destroys. A record lifted out of
   its directory names nobody.

3. LIFECYCLE — PER-SESSION TRUNCATION, PLUS A BYTE CAP.

   The file is TRUNCATED at each session start, so exactly the LAST session's
   series exists, and it stops growing at ``MAX_BYTES``.

   Truncation rather than rotation, deliberately: an operator diagnosing a
   stall looks at the session that stalled — the running one, or the one that
   just died. A retained history of every session is a dated record of the
   operator's browsing cadence sitting on their disk, which buys the
   diagnostician nothing and is exactly the kind of durable by-product this
   product exists to avoid. A record that grows forever on the operator's
   machine is not a neutral addition.

   The cap is a second, independent bound for the session that never ends: at
   the 2 s cadence a line is ~140 bytes, so ~250 KB/hour, and ``MAX_BYTES``
   (4 MiB) is reached after roughly 17 hours of continuous session. On reaching
   it the sampler writes ONE final ``capped`` line and stops writing — it does
   not wrap, because a wrapped file whose beginning is missing reads as a
   session that started later than it did.

4. FAILURE IS CONTAINED, AND IT COSTS THE SERIES, NEVER THE SESSION.

   Every path here is best-effort and swallowing, on the shape the launcher
   already states twice in its own words: *"A registry write that cannot
   happen must cost us the guard, never the session"* and *"Losing the record
   costs the guard; losing the monitor costs the session."* This is a
   RECORDING thread: it holds no session handle, opens no automation channel,
   speaks to no engine, and there is nothing it can do to the session it
   observes. Its worst outcome is an absent or truncated series.
"""

import contextlib
import json
import os
import threading
import time

from ...core.logging import get_logger
from ...core.platform import IS_LINUX

logger = get_logger("browser.session_series")

#: The directory the series lives in, INSIDE the profile's own data dir. A
#: dot-prefixed sibling of the ``.persona-*-ext`` dirs the launch already
#: writes there, so it inherits the same containment (delete → trash, wipe →
#: rmtree) with no second cleanup site to keep in step.
SERIES_DIRNAME = ".persona-session-series"

#: One file, truncated per session. See decision 3 above for why this is not a
#: rotation.
SERIES_FILENAME = "series.jsonl"

#: Seconds between samples. The cadence PS-349's own observer ran at, and the
#: cadence its cost figure ("two /proc reads per sample") was measured for.
PERIOD_S = 2.0

#: The hard bound on the file. See decision 3 — roughly 17 hours at the 2 s
#: cadence, after which one `capped` line is written and sampling stops
#: WRITING (the thread still exits with the session).
MAX_BYTES = 4 * 1024 * 1024

#: The schema version carried on the header line, so a file found later can be
#: read without guessing which build wrote it.
SCHEMA = 1


def series_path(profile_dir: str) -> str:
    """Where this profile's series file lives, given its data dir.

    A pure join, so a caller (a test, a future reader, the wipe assertion) can
    name the file without importing the writer's internals.
    """
    return os.path.join(profile_dir, SERIES_DIRNAME, SERIES_FILENAME)


def series_capability() -> dict:
    """WHAT THIS PLATFORM CAN MEASURE — stated per platform, never assumed.

    The machine-readable form of decision 1, and the reason it is a function
    with a test rather than a comment: the failure this guards against is a
    leg that is NOT measured being recorded as a zero, which is unfalsifiable
    once it is in the file. A platform that cannot measure a leg says
    ``"unmeasured"`` here and writes no sample at all, so the absence is
    legible in the record instead of being disguised as a reading of 0.

    ``cpu`` and ``ctxt_voluntary`` / ``ctxt_nonvoluntary`` are named
    separately because they are genuinely separate capabilities: the portable
    library can answer the first two everywhere and the third on Linux alone.
    """
    if IS_LINUX:
        return {
            "platform": "linux",
            "source": "/proc",
            "recorded": True,
            "cpu": "measured",
            "ctxt_voluntary": "measured",
            "ctxt_nonvoluntary": "measured",
            "why": (
                "/proc/<pid>/stat carries utime+stime and /proc/<pid>/status "
                "carries both ctxt-switch legs, so every leg here is a real "
                "reading."
            ),
        }
    # ⛔ NOT "unsupported" as a shrug: each leg says what is true of it, and
    # the nonvoluntary leg says the specific thing that keeps this module off
    # psutil. A future widening must change THIS table first, which is where a
    # reviewer will see the claim being made.
    return {
        "platform": "windows" if os.name == "nt" else "other",
        "source": None,
        "recorded": False,
        "cpu": "unmeasured",
        "ctxt_voluntary": "unmeasured",
        "ctxt_nonvoluntary": "unmeasured",
        "why": (
            "no /proc on this platform. psutil is available and would answer "
            "cpu and the VOLUNTARY leg, but its Windows and macOS arms return "
            "a hard-coded 0 for the NONVOLUNTARY leg "
            "(_pswindows.py / _psosx.py: ntp.pctxsw(vol, 0)) — an unmeasured "
            "value shaped like a reading, which would forge the sigstop "
            "wedged signature. Recording nothing is honest; recording that "
            "zero is not."
        ),
    }


def engine_pids_for(profile_dir: str, proc_root: str = "/proc") -> "list[int]":
    """The session's process tree, matched on the PROFILE DIR PATH in cmdline.

    ⚠️ THE MATCHER DOCTRINE IS INHERITED AND MUST NOT BE SOFTENED
    (``readings/ps349-2026-09-09/observe.py:54-60``): the tree is matched on a
    PATH inside ``/proc/<pid>/cmdline``, NEVER on ``ps comm``. PS-171 arm H
    measured the ``comm`` matcher finding 1 process where the path matcher
    finds 11, because ``comm`` is capped at 15 characters and every Firefox
    child is named ``Web Content`` / ``Socket Process`` / ``RDD Process`` —
    none of which contains the substring "firefox". A matcher that sees one
    process of an eleven-process tree reports a busy session as idle.

    ONE ADAPTATION, deliberate: observe.py matched the ENGINE DIR, which finds
    every engine process on the box. This matches the PROFILE'S OWN DATA DIR,
    which finds the processes of THIS session and no other — the engine passes
    it as ``--user-data-dir=`` (chromium) and as the profile path (firefox), so
    it appears in the argv of every child of the tree. Two profiles running at
    once therefore record two separate series instead of two copies of their
    sum. Measured on this box against a real chromium: 11 processes.

    ⛔ IT DOES NOT MATCH PERSONA ITSELF, and that is correct on both launch
    arms. On the Linux fork arm the forked shim carries persona's own cmdline,
    not the profile's; on the in-process thread arm there is no separate
    process at all. In both cases the processes that DO carry the profile path
    are the real engine processes, which is the tree being asked about. This is
    therefore the one matcher that works on both arms without being told which
    arm it is on.

    ⛔ AND IT EXCLUDES THE OBSERVER'S OWN PID EXPLICITLY, which is not
    paranoia — it FIRED. Building this module's own falsification harness, the
    harness took the profile dir as ``argv[1]``, so its cmdline contained the
    path, so this matcher returned the harness's pid as a member of the tree it
    was observing. That is PS-185's lesson one level down (a worker lost two
    cycles to a ``pkill -f chromium`` that matched its own command line), and
    for a RECORDER the damage is quieter than a kill and no less wrong: every
    sample would carry the observer's own cpu folded into the session's, and
    the record would attribute persona's work to the engine. A path matcher is
    a substring test and a substring test sees everyone; the one process it may
    never count is the one asking.

    Returns a sorted list; an unreadable ``/proc`` entry is SKIPPED here (the
    process exited between listdir and open, which is ordinary) and the
    caller's per-pid read is where a DENIAL is counted — see ``_sample``.
    """
    found: "list[int]" = []
    self_pid = os.getpid()
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        if int(entry) == self_pid:
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            # Gone between listdir and open, or not ours to read. Not a denial
            # of a KNOWN member — we do not yet know it is one.
            continue
        if profile_dir not in raw.decode("utf-8", "replace"):
            continue
        with contextlib.suppress(ValueError):
            found.append(int(entry))
    return sorted(found)


class _Readings:
    """The previous sample's raw counters, per pid, so a delta can be taken.

    ⚠️ THE DELTA IS THE POINT, AND A LIFETIME AVERAGE WOULD DESTROY IT.
    ``ps pcpu`` is cpu-since-process-start, which makes a session that spun for
    an hour and then wedged indistinguishable from one that is spinning right
    now — the one distinction this whole record turns on. Both series here are
    instantaneous: ``(counter_now - counter_then) / elapsed``.
    """

    def __init__(self) -> None:
        self.cpu: "dict[int, tuple[int, float]]" = {}
        self.ctxt: "dict[int, tuple[int, int, float]]" = {}

    def forget_absent(self, pids: "set[int]") -> None:
        for book in (self.cpu, self.ctxt):
            for pid in [p for p in book if p not in pids]:
                book.pop(pid, None)


def _clock_ticks() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK")) or 100.0
    except (OSError, ValueError, AttributeError):
        return 100.0


def _sample(
    profile_dir: str,
    prev: _Readings,
    started_at: float,
    clk: float,
    now: "float | None" = None,
    proc_root: str = "/proc",
) -> dict:
    """One sample of the tree: cpu percent and both context-switch legs.

    ⛔ AN UNREADABLE SAMPLE IS RECORDED AS UNREAD, NEVER AS A ZERO. This is the
    single most important property in the file and it is why every leg is
    ``None`` rather than ``0`` when nothing could be read.

    It is not hypothetical: PS-349's venue note records ``/proc/<pid>/syscall``
    and ``/proc/<pid>/stack`` answering ``Operation not permitted`` at its own
    privilege level, so "the reader is denied" is a state that HAPPENS. A
    silently-zeroed unreadable sample forges the ``sigstop`` signature exactly
    — cpu 0.0, ctxt 0 — which is the wedged reading. ``denied`` counts the
    members whose read was refused, so a reader can tell "this session did
    nothing" (a real 0 over readable pids) from "I was unable to look"
    (``null`` with ``denied`` > 0). Those are different facts and they must
    never render as the same value.

    ``gone`` is the third state and is ordinary rather than alarming: the pid
    was in the tree at match time and exited before the read — a content
    process closing a tab.
    """
    now = time.time() if now is None else now
    pids = engine_pids_for(profile_dir, proc_root=proc_root)
    prev.forget_absent(set(pids))

    cpu_total = 0.0
    cpu_from = 0
    ctxt_v = 0
    ctxt_nv = 0
    ctxt_from = 0
    denied = 0
    gone = 0

    for pid in pids:
        try:
            with open(os.path.join(proc_root, str(pid), "stat")) as fh:
                raw = fh.read()
        except PermissionError:
            denied += 1
            continue
        except OSError:
            gone += 1
            continue
        try:
            # comm is parenthesised and may itself contain spaces and
            # parentheses, so the fields are taken after the LAST ')'.
            fields = raw[raw.rindex(")") + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
        except (ValueError, IndexError):
            denied += 1
            continue
        before = prev.cpu.get(pid)
        prev.cpu[pid] = (ticks, now)
        if before is not None and now > before[1]:
            cpu_total += (ticks - before[0]) / clk / (now - before[1]) * 100.0
            cpu_from += 1

        try:
            vol = nonvol = None
            with open(os.path.join(proc_root, str(pid), "status")) as fh:
                for line in fh:
                    if line.startswith("voluntary_ctxt_switches:"):
                        vol = int(line.split()[1])
                    elif line.startswith("nonvoluntary_ctxt_switches:"):
                        nonvol = int(line.split()[1])
        except PermissionError:
            denied += 1
            continue
        except (OSError, ValueError, IndexError):
            gone += 1
            continue
        if vol is None or nonvol is None:
            # The lines were absent. NOT a zero — the kernel did not answer
            # this question, which is the same class of fact as a denial.
            denied += 1
            continue
        seen = prev.ctxt.get(pid)
        prev.ctxt[pid] = (vol, nonvol, now)
        if seen is not None and now > seen[2]:
            # max(0, ...) guards pid reuse only; a counter never falls.
            ctxt_v += max(0, vol - seen[0])
            ctxt_nv += max(0, nonvol - seen[1])
            ctxt_from += 1

    return {
        "t": round(now - started_at, 1),
        "nproc": len(pids),
        # ⛔ null, never 0, when no member yielded a delta. The FIRST sample
        # of every session lands here by construction (there is no previous
        # counter to subtract), which is the cheapest possible reminder that
        # the two are different values.
        "cpu": round(cpu_total, 1) if cpu_from else None,
        "cpu_from": cpu_from,
        "ctxt_v": ctxt_v if ctxt_from else None,
        "ctxt_nv": ctxt_nv if ctxt_from else None,
        "ctxt_from": ctxt_from,
        "denied": denied,
        "gone": gone,
    }


class SessionSeriesRecorder:
    """Writes one session's series. Owns no session handle and reads nothing.

    Constructed with the PROFILE DIR rather than the profile — deliberately:
    this class never learns the profile's name, so it cannot write one into the
    file even by accident (decision 2, the PS-330 convention).
    """

    def __init__(
        self,
        profile_dir: str,
        *,
        period_s: float = PERIOD_S,
        max_bytes: int = MAX_BYTES,
        engine: "str | None" = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._period = period_s
        self._max_bytes = max_bytes
        self._engine = engine
        self._written = 0
        self._capped = False

    @property
    def path(self) -> str:
        return series_path(self._profile_dir)

    def _open_truncated(self):
        """Create the dir and TRUNCATE the file — decision 3.

        0600/0700 for the same reason ``setup_logging`` chmods the log dir: a
        series names the moments an operator's browser was busy, and that is
        not a fact for other local accounts.
        """
        directory = os.path.join(self._profile_dir, SERIES_DIRNAME)
        os.makedirs(directory, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)
        path = self.path
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")

    def _emit(self, handle, obj: dict) -> None:
        if self._capped:
            return
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        if self._written + len(line) > self._max_bytes:
            self._capped = True
            # ⛔ The cap SAYS it capped. A file that simply stops has no way to
            # tell a reader whether the session ended or the record did.
            final = json.dumps(
                {"meta": "capped", "bytes": self._written,
                 "why": "series reached max_bytes; sampling continues, "
                        "writing stops. The record does not wrap."},
                separators=(",", ":"),
            ) + "\n"
            with contextlib.suppress(OSError, ValueError):
                handle.write(final)
                handle.flush()
            logger.info(
                "Session series reached its %d-byte cap; the rest of this "
                "session is not recorded.", self._max_bytes,
            )
            return
        handle.write(line)
        handle.flush()
        self._written += len(line)

    def run(self, stop_event: threading.Event) -> None:
        """Sample until ``stop_event`` is set. Never raises at the caller.

        ⚠️ THE SWALLOW IS THE CONTRACT, not sloppiness (decision 4). This runs
        on a daemon thread of a process whose live browser must not care
        whether a recording succeeded. It holds no handle on the session, so
        there is nothing it could break even if it wanted to; the only thing
        its failure can cost is the series.
        """
        try:
            self._run(stop_event)
        except Exception:
            logger.exception(
                "Session series recording stopped on an error; the session is "
                "unaffected and continues without a series."
            )

    def _run(self, stop_event: threading.Event) -> None:
        capability = series_capability()
        if not capability["recorded"]:
            # Decision 1. Nothing is written at all — not an empty file, which
            # would read as a session that produced no samples.
            logger.info(
                "Session series not recorded on this platform: %s",
                capability["why"],
            )
            return

        started_at = time.time()
        clk = _clock_ticks()
        prev = _Readings()
        handle = None
        try:
            handle = self._open_truncated()
            self._emit(handle, {
                "meta": "header",
                "schema": SCHEMA,
                "period_s": self._period,
                "max_bytes": self._max_bytes,
                "engine": self._engine,
                "capability": capability,
                # ⛔ Said in the file itself, because the file is what outlives
                # this module and a reader of it will not have this docstring.
                "note": (
                    "RECORDING, NOT DETECTION. Nothing reads this file to "
                    "decide anything: there is no threshold, no verdict and no "
                    "watchdog. cpu/ctxt_v/ctxt_nv are null when NOTHING COULD "
                    "BE READ (see `denied`) — null is not zero, and a healthy "
                    "session can read lower than a wedged one on both series."
                ),
            })
            while not stop_event.is_set():
                try:
                    self._emit(handle, _sample(
                        self._profile_dir, prev, started_at, clk,
                    ))
                except Exception as exc:
                    # ⛔ A sample that failed SAYS SO in the file. Skipping it
                    # silently would leave a gap indistinguishable from a
                    # session that was quiet for that interval.
                    with contextlib.suppress(Exception):
                        self._emit(handle, {
                            "meta": "sample-error",
                            "t": round(time.time() - started_at, 1),
                            "err": f"{type(exc).__name__}: {exc}",
                        })
                stop_event.wait(self._period)
            with contextlib.suppress(Exception):
                self._emit(handle, {
                    "meta": "end",
                    "t": round(time.time() - started_at, 1),
                })
        finally:
            if handle is not None:
                with contextlib.suppress(Exception):
                    handle.close()


def start_recording(
    profile_dir: str,
    stop_event: threading.Event,
    *,
    engine: "str | None" = None,
) -> "threading.Thread | None":
    """Start the recorder on its own daemon thread. Returns it, or None.

    ⛔ THE CALLER MUST GUARD THIS SEPARATELY FROM ITS MONITOR THREADS, and the
    reason is specific rather than stylistic. ``BrowserLauncher.start_thread``
    starts its monitor and wait threads inside one ``try``, whose ``except``
    calls ``terminate(proc, ...)`` and unregisters the session — CORRECT for
    those two (nobody would drain the engine's stdout or reap it), and
    CATASTROPHIC here. A thread-table exhaustion that stopped a RECORDING
    thread from starting must not tear down a live browser with account
    sessions open. That is why this function swallows and returns None instead
    of raising: the session goes on with no series.
    """
    try:
        recorder = SessionSeriesRecorder(profile_dir, engine=engine)
        thread = threading.Thread(
            target=recorder.run, args=(stop_event,), daemon=True,
        )
        thread.start()
        return thread
    except Exception:
        logger.exception(
            "Could not start the session series recorder; the session runs "
            "normally and simply records no series."
        )
        return None
