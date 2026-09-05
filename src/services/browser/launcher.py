import atexit
import contextlib
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

from ...core.logging import get_logger
from ...models.profile import Profile
from .automation_channel import opens_cdp_channel
from .process import effective_engine, spawn_browser, terminate, wait_for_exit
from .refusal import Refusal, classify_refusal
from .session_registry import (
    Liveness,
    SessionRecord,
    SessionRegistry,
    default_registry,
    liveness_of,
    make_record,
    terminate_record,
)

logger = get_logger("browser.launcher")

_CLOSE_REASON = re.compile(r"LIFECYCLE close=(\S+)")

# The Firefox mTLS CA import SOFT-FAILS by design: the launch proceeds with the
# certificate untrusted and the engine announces the outcome once on stdout.
# Without capturing it here the announcement degrades to a scrolling log line
# and nothing survives the session, so a profile launched untrusted is
# indistinguishable from one whose trust imported cleanly.
#
# These are the engine's emit strings (invisible_launch.py) — matched, never
# restated. Changing them here without changing them there silently stops the
# capture, which is why the mapping is a named function with its own test.
_MTLS_TRUSTED = "MTLS_CA_TRUSTED"
_MTLS_UNSUPPORTED = "MTLS_UNSUPPORTED:"
_MTLS_IMPORT_FAILED = "MTLS_CA_IMPORT_FAILED:"


def cert_trust_status_from(msg: str) -> str | None:
    """Map an engine mTLS message onto a status to persist on the profile.

    Returns None for every other line, so the caller can tell "not an mTLS
    message" from a real outcome.
    """
    if msg == _MTLS_TRUSTED:
        return "trusted"
    if msg.startswith(_MTLS_UNSUPPORTED):
        detail = msg[len(_MTLS_UNSUPPORTED):].strip()
        return f"not trusted (unsupported): {detail}" if detail else (
            "not trusted (unsupported)"
        )
    if msg.startswith(_MTLS_IMPORT_FAILED):
        detail = msg[len(_MTLS_IMPORT_FAILED):].strip()
        return f"NOT TRUSTED: {detail}" if detail else "NOT TRUSTED"
    return None

# Close reasons that need no extra diagnostics in the log: the user ended the
# session (stop/X-close), the browser exited on its own and said so, or the
# failure was already reported loudly (LAUNCH_FAILED). Anything else — the
# watch's give-up, or the engine dying with no close signal at all — is an
# unexpected end and must say why, or a real crash reads like a normal close
# in the Activity Log.
_QUIET_CLOSE_REASONS = {
    "stop-requested",
    "window-gone",
    # PS-204: renamed from "all-pids-exit", which claimed more than the watch
    # could observe. The watch quantifies over the pid set it captured ONCE, so
    # the old name asserted that EVERY process the session spawned had exited —
    # a claim it was structurally unable to falsify about a child that appeared
    # after the capture. The new name says exactly what was checked. Renamed
    # HERE in the same change as the emit (invisible_launch.py), or a normal
    # close falls through to the "ended unexpectedly" branch below.
    "tracked-pids-exit",
    "parent-pid-exit",
    "closed-event",
    "launch-failed",
}

_NOISY_PREFIXES = (
    "- [pid=",
    "console.error:",
    "Crash Annotation",
    "JavaScript error:",
    "WARNING: At least one completion condition",
)

# GTK/accessibility chatter the engine prints to stderr on a headless or
# a11y-less display. Harmless, but it drowns the activity log; these markers
# appear mid-line (e.g. "(chrome:123): dbind-WARNING **: ..."), so match as a
# substring rather than a prefix.
_NOISY_SUBSTRINGS = (
    "dbind-WARNING",
    "AT-SPI:",
    "Atk-CRITICAL",
    "Gtk-WARNING",
    "Gdk-Message",
    "Gtk-Message",
    "GLib-GObject",
    "org.a11y.Bus",
    "atk_socket_embed",
    "from the cursor theme",
)


# Chromium ERROR-level stderr that is provably benign: USB enumeration
# probing device properties the host doesn't expose, EV-cert OID metadata
# decode chatter, VA-API probing without a usable GPU, and WebRTC P2P/ICE
# gathering against unreachable STUN/TURN (a scanner's WebRTC test drives ICE
# with no real servers, so name-resolution and TURN-socket failures are
# expected probe chatter). Rendered red in the Activity Log they read like
# failures, so keep them file-log only. The WebRTC entries key on the P2P
# source-file markers, not on bare "Failed to …", so a real DNS/socket error
# elsewhere still surfaces.
_BENIGN_ENGINE_ERRORS = (
    "device_event_log_impl.cc",
    "usb_service_win.cc",
    "SetupDiGetDeviceProperty",
    "Failed to decode OID",
    "vaInitialize failed",
    "vaapi_wrapper.cc",
    "socket_manager.cc",
    "turn_port.cc",
    "stun_port.cc",
    # Shutdown chatter: the parent polls a content child that already exited, so
    # the zygote GetTerminationStatus send fails on close — not a failure.
    "zygote_communication_linux.cc",
    # Benign fontconfig chatter from chromium children when the host fontconfig
    # isn't ideal; the engine spoofs its own fonts and the page still renders.
    "Fontconfig error: Cannot load default config file",
    # No session D-Bus in a headless/VM session: chromium spams bus-connect and
    # NameHasOwner failures. The browser runs fine; on a real host with a bus
    # these don't appear.
    "dbus/bus.cc",
    "dbus/object_proxy.cc",
    # A spoofed/guest-type profile can't host the WebUI new-tab page, so chromium
    # logs an ERROR and opens a plain new tab instead. Expected, not a failure.
    "new_tab_ui.cc",
    # Mesa's VMware/virgl SVGA driver reports the guest has no host 3D
    # acceleration ("(0, Success)" — it's a status line, not an error). The
    # engine falls back to software GL and renders fine; it just spams this on
    # every launch inside a VM.
    "VMware: No 3D enabled",
)

# net_error -100 = ERR_CONNECTION_CLOSED: the connection was dropped
# mid-handshake (closed tab, cancelled prefetch) — not a TLS failure. Any
# other net_error on a handshake is a real problem and must stay visible.
_BENIGN_SSL_HANDSHAKE = re.compile(r"handshake failed.*net_error -100\b")


def _is_benign_engine_error(msg: str) -> bool:
    if any(s in msg for s in _BENIGN_ENGINE_ERRORS):
        return True
    return (
        "ssl_client_socket_impl.cc" in msg
        and _BENIGN_SSL_HANDSHAKE.search(msg) is not None
    )


def is_engine_noise(msg: str) -> bool:
    """True for engine stderr chatter that shouldn't reach the activity log."""
    return (
        msg.startswith(_NOISY_PREFIXES)
        or any(s in msg for s in _NOISY_SUBSTRINGS)
        or _is_benign_engine_error(msg)
    )


class BrowserLauncher:
    def __init__(self, registry: "SessionRegistry | None" = None) -> None:
        # A Condition (not a bare Lock) so stop_profile can WAIT for a
        # spawn-in-flight to leave _starting: the spawn thread notifies when it
        # registers or bails, and the stopper blocks until then. Without this a
        # delete/wipe would rmtree the data dir while spawn_browser is still
        # os.makedirs+seeding into it (audit5 #1).
        self._lock = threading.Condition()
        self._active_sessions: dict[str, subprocess.Popen] = {}
        self._stop_notifiers: dict[str, threading.Event] = {}
        # Wall-clock time each session was registered. read_cdp_port compares a
        # DevToolsActivePort's mtime against this so a reader never attaches to a
        # port file left behind by a previous run of the same profile.
        self._session_started_at: dict[str, float] = {}
        # Whether the LIVE session opened a remote-debugging (CDP) channel,
        # evaluated once from the profile actually launched (see _register_session).
        #
        # This is deliberately NOT re-derived from the stored record at read
        # time. ProfileManager.set_ai_control mutates and saves p.ai_control with
        # no reference to whether the profile is running, so the record and the
        # running session diverge the moment an operator flips the connect-page
        # checkbox mid-session. The port that chromium bound at launch does not
        # close because a boolean on disk changed; re-reading the record would
        # report a channel CLOSED while it is still listening — the falsely
        # reassuring direction. The launched fact is captured and kept.
        self._session_cdp_open: dict[str, bool] = {}
        # The (engine, build) the LIVE session is executing from, captured at
        # registration and dropped with the session (PS-221).
        #
        # WHY THIS IS NOT `Profile.last_launch_build` (which already exists).
        # That field is a PERSISTED, LAST-launch stamp, and it cannot answer
        # "which build is this session running from RIGHT NOW" for two reasons
        # that are both structural rather than incidental:
        #
        #   * It is written by a hook that fires AFTER registration (see
        #     start_thread), so between _register_session and that write the
        #     profile is reported as running while the record still names the
        #     PREVIOUS launch's build. Every launch passes through that window.
        #   * The write is best-effort and its failure is swallowed, which
        #     leaves the previous launch's build standing — an affirmative
        #     claim about a build the session is NOT on.
        #
        # Both produce the same shape: a running profile whose persisted stamp
        # names a DIFFERENT build than it is executing from. A prune that reads
        # it as authoritative deletes the live build and spares a dead one —
        # strictly worse than the disk it reclaims. Intersecting the stamp with
        # running_profile_names() does NOT fix it: the intersection is with a
        # value that is stale for a RUNNING profile, not only for a stopped one.
        #
        # So the live question is answered from the live object. This is
        # written INSIDE the same locked block that registers the session, so
        # "registered" and "build known" cannot come apart, and it dies with
        # the session via _forget_session_facts. A name in _starting has no
        # entry here at all, which is what makes an in-flight launch UNKNOWN by
        # construction rather than by a stamp that happens to be absent.
        #
        # NOT a second source of truth about what is INSTALLED — it records
        # what THIS process launched, and only for as long as it is running.
        self._session_build: dict[str, tuple[str, str | None]] = {}
        # The LAST REFUSED launch per profile — the fail-closed guards firing,
        # kept so the answer reaches the profile that refused instead of only a
        # log line that scrolls away.
        #
        # DELIBERATELY NOT A SESSION FACT, which is why it is not cleaned up by
        # _forget_session_facts beside the two dicts above. Those describe a LIVE
        # session and must die with it; this one describes a launch that never
        # became a session, and its whole purpose is to still be there an hour
        # later when the operator comes back to a card that did not open. Adding
        # it to the teardown helper would erase the marker at the very moment it
        # becomes the only remaining evidence.
        #
        # Superseded, never accumulated: each new launch ATTEMPT for a name drops
        # the previous entry (see start_thread), so the card shows the outcome of
        # the most recent attempt and a refusal that has since launched fine
        # leaves no stale badge behind.
        self._last_refusal: dict[str, Refusal] = {}
        # Profiles whose spawn is in flight but not yet in _active_sessions.
        # start_thread reserves the slot here synchronously so a second launch of
        # the same profile — fired while the slow spawn_browser() runs — is
        # rejected instead of starting a second browser on the same profile dir.
        self._starting: set[str] = set()
        # Profiles whose in-flight spawn has been asked to abort (a stop/delete
        # arrived mid-spawn). The spawn thread checks this after spawn_browser
        # returns: if set, it terminates the just-spawned process and never
        # registers a session, so the caller can safely remove the data dir.
        self._aborting: set[str] = set()
        # Called with the Profile right after a launch has really proceeded, to
        # record which engine build it ran under. Set by the composition root
        # (core/container.py) rather than passed per-call: there are three
        # launch lanes (UI, REST, MCP) and they all resolve THIS object from the
        # container, so wiring it here covers all three. A per-call callback —
        # the shape on_cert_trust uses — would have covered only the UI lane and
        # left an API/MCP launch silently unrecorded, which is the failure mode
        # that makes an absent stamp ambiguous: "never launched" and "launched
        # through a lane that forgot to record" would be indistinguishable.
        # No-op by default (headless/tests).
        self._launch_record_hook: "Callable[[Profile], None] | None" = None
        # THE SAME SESSION FACTS, WRITTEN WHERE THEY SURVIVE US. Every dict
        # above dies with the process, which is exactly why the launch guard
        # goes blind after an unclean exit and offers a second browser on a
        # profile dir that already has one (PS-223). This mirrors the
        # registrations onto disk so the guard can still ask the question.
        #
        # It is a MIRROR, never the authority: what it stores is a pid to probe,
        # and a record only ever refuses a launch after the process it names has
        # been shown to be alive. See session_registry.py for why that
        # distinction is the difference between a safety catch and a lockout.
        #
        # Injectable so tests can point it at a tmp path; resolved lazily
        # (default_registry reads config at CALL time) so a test that moves
        # PERSONA_HOME still gets the registry that goes with it.
        self._registry = registry if registry is not None else default_registry()
        # Survivors found on disk at STARTUP: browsers a previous persona
        # launched and did not get to tear down. Populated by scan_survivors()
        # and NEVER by a launch — a session we started this run is tracked in
        # _active_sessions, and conflating the two is what "silently adopting"
        # would mean. Held so the launch guard can answer without re-probing
        # psutil on every render.
        self._survivors: dict[str, SessionRecord] = {}
        # The INDETERMINATE half of the same scan: records whose liveness could
        # not be settled (no psutil, permission denied, no create time captured
        # at registration). These are deliberately NOT adopted as survivors and
        # never refuse a launch — an unanswerable question must not lock a user
        # out of their own profile. They are retained here for exactly one
        # reader: running_session_builds(), which must count them as UNKNOWN.
        #
        # ⚠️ AN INDETERMINATE IS A LIVE PROCESS WE CANNOT PROVE IS OURS, NOT A
        # PROBABLY-DEAD ONE. `liveness_of` answers GONE when it establishes the
        # process is gone, and a GONE record is dropped by the registry as it
        # reads; UNKNOWN means only that the question could not be answered.
        # Reading it as "nothing is running there" is the mistake
        # session_registry.py:185 and process_group.py:518 both record having
        # been bitten by — a psutil-less container measuring a teardown as
        # CLEAN precisely because the measuring code silently answered
        # "nothing there".
        self._indeterminate: dict[str, SessionRecord] = {}
        atexit.register(self.shutdown_all)

    def set_launch_record_hook(
        self, hook: "Callable[[Profile], None] | None"
    ) -> None:
        """Install the hook that records a launch's engine build.

        Optional by construction: a launcher with no hook launches exactly as
        it always did. The hook's own failures are swallowed at the call site —
        a profile that cannot record its build must still open.
        """
        self._launch_record_hook = hook

    def shutdown_all(self) -> None:
        """Tear down every session THIS process started. The atexit path.

        ⚠️ THIS REAPS ``_active_sessions`` AND NOTHING ELSE, and that asymmetry
        is why the record-keeping below is per-name rather than bulk. A
        SURVIVOR — a browser a PREVIOUS persona left running — is in neither
        ``_active_sessions`` nor ``_starting``, so this method does not kill it
        and cannot kill it. Wiping the whole registry here therefore erased the
        record of a browser that was still alive, and the next persona started
        with an empty registry, found no survivor, and offered a second launch
        on that profile directory. That is the user's original defect reached
        through the front door of an ordinary clean quit, so the guard is only
        as persistent as this method is careful.

        We forget exactly the sessions we terminated. A record we did not act
        on is LEFT ALONE, and leaving it is safe because nothing anywhere
        refuses a launch on a record's PRESENCE: ``scan_survivors`` probes
        every record it reads, a record whose process is gone is dropped as it
        is read, and pid reuse is discriminated by create time. A preserved
        record can therefore only ever refuse a launch against a browser that
        is genuinely still alive — which is the refusal this ticket exists to
        make.
        """
        with self._lock:
            terminated: list[str] = []
            for name, proc in list(self._active_sessions.items()):
                notifier = self._stop_notifiers.pop(name, None)
                if notifier:
                    notifier.set()
                terminate(proc, name)
                terminated.append(name)
            self._active_sessions.clear()
            self._forget_all_session_facts(terminated)
        logger.info("All browser sessions terminated")

    def start_thread(
        self,
        profile: Profile,
        log_callback: Callable[[str], None],
        on_start: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        *,
        on_cert_trust: "Callable[[str | None], None] | None" = None,
    ) -> None:
        with self._lock:
            if profile.name in self._active_sessions or profile.name in self._starting:
                # Already running/starting: refuse the duplicate, but tell the
                # caller so it clears its own is_loading flag — returning silently
                # left the card stuck "loading" and uncontrollable (audit6 LOW b).
                if on_stop:
                    on_stop()
                return

            # RESERVE IN THE SAME ACQUISITION THAT CHECKED. The check above and
            # this reservation are ONE atomic step on purpose: releasing the
            # lock between them lets two concurrent launches of one profile
            # both read "not running" and both reserve, and the survivor probe
            # below makes that window wide rather than instruction-sized
            # because it performs psutil IO. Two browsers on one profile
            # directory is the precise defect this ticket exists to remove, and
            # the UI's is_loading flag does not serialise the API and MCP
            # lanes. Clear any stale abort flag from a prior aborted launch.
            self._starting.add(profile.name)
            self._aborting.discard(profile.name)

        # THE SAME REFUSAL, FOR A BROWSER WE DID NOT LAUNCH. The check above can
        # only see sessions THIS process started; a browser left behind by a
        # previous persona is in neither dict, which is the whole defect
        # (PS-223). The UI asks survivor_for() before it ever gets here, but
        # start_thread is also the API and MCP lanes' entry point, and a guard
        # that lives only in the UI is a guard two lanes do not have.
        #
        # Grounded in a LIVENESS PROBE, never in the record's presence:
        # survivor_for re-probes and releases the block when the process is
        # gone or can no longer be established. So a stale record cannot refuse
        # a launch here either — that is the lockout this ticket forbids, and
        # it would be worse in these lanes, which have no card to click.
        #
        # PROBED OFF-LOCK, HOLDING THE SLOT. survivor_for takes this same
        # Condition, and it does file/psutil IO; holding the lock across it
        # would block every other holder behind a probe. The reservation above
        # is what keeps that safe — the slot is ours for the duration, so
        # nothing can slip past while we ask.
        try:
            survivor = self.survivor_for(profile.name)
        except Exception:
            # FAIL OPEN, AND LOUDLY. An unanswerable liveness question must not
            # refuse a launch (the module's standing rule), but here it must
            # also not leak the reservation we are holding: a name stuck in
            # _starting refuses every future launch for the life of the
            # process, which is the permanent lockout this ticket forbids in
            # its strongest form.
            logger.exception(
                "Could not establish whether %s has a surviving browser; "
                "allowing the launch rather than refusing on no evidence.",
                profile.name,
            )
            survivor = None
        if survivor is not None:
            # RELEASE THE SLOT WE RESERVED. notify_all because stop_profile
            # blocks on this Condition waiting for a spawn-in-flight to leave
            # _starting; without the wake it would sit out its full timeout on
            # a launch that never happened.
            with self._lock:
                self._starting.discard(profile.name)
                self._aborting.discard(profile.name)
                self._lock.notify_all()
            log_callback(
                f"{profile.name} already has a browser running (pid "
                f"{survivor.pid}) from a previous persona session — not "
                f"launching a second one."
            )
            logger.warning(
                "Refused a launch of %s: a browser from a previous session is "
                "still running (pid %s)", profile.name, survivor.pid,
            )
            if on_stop:
                on_stop()
            return

        with self._lock:
            # A NEW attempt supersedes the previous verdict, so the card reports
            # the most recent one and never a badge the profile has outgrown.
            # Dropped HERE — at the attempt, not at its outcome — because the
            # only honest states are "refused, at this time" and "no verdict
            # yet": leaving the old refusal up while a relaunch is in flight
            # would assert a refusal the product is at that moment disproving.
            # Placed AFTER BOTH refusals above on purpose: a click that gets
            # refused as a duplicate — or on account of a survivor — is not an
            # attempt and must not erase the verdict from the attempt that did
            # run. That is why this stayed here rather than moving up into the
            # reservation block: the reservation is about exclusion, this is
            # about what the card is entitled to claim.
            self._last_refusal.pop(profile.name, None)

        # THE SAME RULE, ONE VERDICT OVER — the trust verdict is superseded by
        # the ATTEMPT, exactly as the refusal above is, and for a sharper
        # reason: this one is PERSISTED. A dropped trust verdict would merely
        # leave the field None (an honest absence that renders as nothing), but
        # a verdict left STANDING is an affirmative "trusted" describing a
        # session that ran with its CA untrusted — and it survives a restart.
        # The engine announces the outcome once, and only when it reaches the
        # cert path; a launch that reaches it and emits NOTHING (or dies before
        # the line) must not leave the previous session's verdict on record.
        #
        # THE DISCRIMINATOR IS THE ATTEMPT, NOT THE MESSAGE — see
        # api/refusal_report.py, which owns this reasoning in full. Clearing at
        # the outcome instead would be unreachable in exactly the case that
        # matters: no message, no clear.
        #
        # Delivered through on_cert_trust rather than written here so the
        # launcher keeps knowing nothing about persistence, and — the load-
        # bearing half — so a lane cannot half-adopt this: the drop and the
        # record arrive through ONE wiring, and a lane that records a verdict
        # necessarily also drops the stale one.
        #
        # Gated on the certificate: a profile with no certificate assigned has
        # no verdict to invalidate and must stay byte-identical (no write, no
        # save_profiles()). Placed AFTER the duplicate-launch return, for the
        # reason stated above it — a click refused as a duplicate is not an
        # attempt and must not erase the verdict from the attempt that did run.
        if on_cert_trust is not None and profile.certificate:
            try:
                on_cert_trust(None)
            except Exception:
                # A profile that cannot drop its stale verdict must still open,
                # matching the outcome path in _monitor_process.
                logger.exception(
                    "Failed to clear the stale cert trust verdict for %s; the "
                    "launch proceeds", profile.name,
                )

        log_callback(f"Starting {profile.name} ({profile.os_type})...")
        logger.info(f"Starting browser for profile: {profile.name}")
        if on_start:
            on_start()

        stop_event = threading.Event()
        notify_lock = threading.Lock()
        close_reason: list[str | None] = [None]
        last_output: deque[str] = deque(maxlen=5)

        def notify_stopped() -> None:
            with notify_lock:
                if stop_event.is_set():
                    return
                stop_event.set()
                with self._lock:
                    # Evict by proc IDENTITY, not just by name: a delayed stale
                    # notifier from a previous crashed launch must not pop the NEW
                    # live session the user relaunched (which would leave the new
                    # browser untracked, stop_profile False for it, and a second
                    # engine launchable on the same dir) (audit6 #7).
                    if self._active_sessions.get(profile.name) is proc:
                        self._active_sessions.pop(profile.name, None)
                        self._stop_notifiers.pop(profile.name, None)
                        self._forget_session_facts(profile.name)
                reason = close_reason[0]
                if reason in _QUIET_CLOSE_REASONS:
                    log_callback(f"Session ended: {profile.name}")
                    logger.info(
                        "Session ended for profile: %s (close=%s)",
                        profile.name, reason,
                    )
                else:
                    detail = reason or "engine exited with no close signal"
                    exit_code = None
                    with contextlib.suppress(Exception):
                        exit_code = proc.poll()
                    log_callback(
                        f"Session ended unexpectedly: {profile.name} ({detail})"
                    )
                    logger.warning(
                        "Session ended unexpectedly for profile %s: %s "
                        "(exit code %s); last engine output: %s",
                        profile.name, detail, exit_code,
                        list(last_output) or "none",
                    )
                if on_stop:
                    on_stop()

        try:
            proc = spawn_browser(profile)
            # Evaluated HERE, from the profile that was actually just launched,
            # and evaluated OUTSIDE the lock (it is pure, but it resolves the
            # effective engine, so there is no reason to hold the lock for it).
            # This is the whole mechanism behind the card's channel indicator:
            # the answer is taken once, from the launch, and never re-derived
            # from a record that set_ai_control can flip while the session runs.
            cdp_open = opens_cdp_channel(profile)
            engine_name = "unknown"
            with contextlib.suppress(Exception):
                engine_name = effective_engine(profile)
            # WHICH BUILD this session is executing from, resolved here beside
            # the engine and for the same reasons: off-lock (it reads the
            # installed build), from the launch itself, once (PS-221).
            #
            # Best-effort in the SAME sense engine_name above is: a build that
            # cannot be read yields None, never a guess. None is honest —
            # "running, build not known" — and every consumer must treat it as
            # UNKNOWN rather than as "on no build". A wrong build here is worse
            # than no build, because it is an affirmative claim a prune acts on.
            session_build: "str | None" = None
            with contextlib.suppress(Exception):
                from .launch_provenance import engine_build_for

                session_build = engine_build_for(engine_name)
            with self._lock:
                aborted = profile.name in self._aborting
                if not aborted:
                    self._active_sessions[profile.name] = proc
                    self._stop_notifiers[profile.name] = stop_event
                    self._session_started_at[profile.name] = time.time()
                    self._session_cdp_open[profile.name] = cdp_open
                    # IN THE SAME ACQUISITION THAT REGISTERS THE SESSION, which
                    # is the entire point of this dict rather than a detail of
                    # where the line landed: "is it running" and "what is it
                    # running" become one atomic fact, so no reader can observe
                    # a registered session whose build is still the previous
                    # launch's. Recorded even when it is None — the KEY is what
                    # says "this session was accounted for", and its absence is
                    # what says an in-flight launch has not been.
                    self._session_build[profile.name] = (engine_name, session_build)
                    self._starting.discard(profile.name)
                    self._lock.notify_all()

            if not aborted:
                # THE SAME REGISTRATION, ONTO DISK, so the guard survives us.
                # Written OUTSIDE the lock: it does file IO, and every other
                # holder of this Condition (stop_profile, is_running, a
                # concurrent launch) would block behind an fsync for no reason.
                #
                # Its failure is swallowed inside the registry and never
                # raised — deliberately, and this is the one place it matters
                # most: everything here runs inside start_thread's outer
                # `except Exception`, which converts ANY raise into "the launch
                # failed" and calls on_stop WHILE THE BROWSER IS ALREADY
                # SPAWNED AND REGISTERED. A registry write that cannot happen
                # must cost us the guard, never the session.
                with contextlib.suppress(Exception):
                    self._registry.record(
                        make_record(profile.name, proc, engine_name)
                    )

            if aborted:
                # A stop/delete arrived while we were spawning. Terminate the
                # process we just started and only THEN leave _starting — the
                # waiting stopper (which will rmtree the data dir) blocks on the
                # name being in _starting, so keeping the reservation until the
                # browser is DEAD guarantees nothing is still writing the dir when
                # it proceeds. Discarding+notifying BEFORE terminate (audit6 #5)
                # woke the stopper while the browser was still alive → rmtree
                # under a live process, the same corruption class audit-5 #1 fixed.
                terminate(proc, profile.name, timeout=1)
                with self._lock:
                    self._starting.discard(profile.name)
                    self._aborting.discard(profile.name)
                    self._lock.notify_all()
                logger.info("Aborted in-flight spawn for profile: %s", profile.name)
                if on_stop:
                    on_stop()
                return

            # Record WHICH ENGINE BUILD this profile just launched under.
            #
            # Placed here, after the abort check, so only a launch that really
            # proceeded is stamped — an aborted spawn returns above and leaves
            # the previous stamp alone rather than claiming a session that was
            # torn down.
            #
            # ITS OWN try/except, and that is the whole point of this block
            # rather than an incidental precaution. Everything here runs inside
            # the outer `except Exception` that logs "Error starting process"
            # and calls on_stop — so an unguarded raise from the hook would be
            # reported to the operator as a FAILED launch and clear the card's
            # loading state, while the browser it just spawned is registered
            # and running. A profile that cannot record its build must still
            # open; provenance is strictly a by-product of launching, never a
            # precondition for it.
            if self._launch_record_hook is not None:
                try:
                    self._launch_record_hook(profile)
                except Exception:
                    logger.exception(
                        "Failed to record the engine build for profile %s; the "
                        "launch proceeds and the profile keeps its previous "
                        "stamp", profile.name,
                    )

            try:
                threading.Thread(
                    target=self._monitor_process,
                    args=(proc, profile.name, log_callback, on_ready,
                          notify_stopped, close_reason, last_output),
                    kwargs={"on_cert_trust": on_cert_trust},
                    daemon=True,
                ).start()
                threading.Thread(
                    target=wait_for_exit,
                    args=(proc, profile.name, notify_stopped),
                    daemon=True,
                ).start()
            except Exception:
                # The session is already REGISTERED but a monitor/wait thread
                # failed to start (rare: thread-table exhaustion). Without this
                # the process would run live but unmonitored while we log "Error
                # starting" — nobody would ever drain its stdout or reap it
                # (audit6 LOW a). Tear the registered session down cleanly.
                terminate(proc, profile.name, timeout=1)
                with self._lock:
                    if self._active_sessions.get(profile.name) is proc:
                        self._active_sessions.pop(profile.name, None)
                        self._stop_notifiers.pop(profile.name, None)
                        self._forget_session_facts(profile.name)
                    self._lock.notify_all()
                raise
        except Exception as e:
            # Classify BEFORE taking the lock. classify_refusal is pure and
            # allocation-only, so holding the lock across it is safe today —
            # but it is a classification function inside a critical section
            # other threads block on, and keeping it outside means the
            # invariant stays obvious if it ever grows. The timestamp is taken
            # here too, which is if anything more truthful: it stamps the
            # instant the failure was handled rather than the instant the lock
            # was won.
            #
            # Records a REFUSAL and only a refusal. classify_refusal returns
            # None for an ordinary failure, which stays in the log and off the
            # card — if every transient spawn error marked a card, the operator
            # would learn to skim past the one marker that means a guard fired.
            refusal = classify_refusal(e, time.time())
            with self._lock:
                self._starting.discard(profile.name)
                self._aborting.discard(profile.name)
                if refusal is not None:
                    self._last_refusal[profile.name] = refusal
                self._lock.notify_all()
            logger.exception(f"Error starting browser for {profile.name}: {e}")
            log_callback(f"Error starting process: {e}")
            if on_stop:
                on_stop()

    def stop_profile(self, profile_name: str, timeout: int = 2) -> bool:
        with self._lock:
            if profile_name in self._starting:
                # Spawn in flight: mark it to abort and WAIT until start_thread
                # resolves it (registers-then-we-stop, or bails). Only then is
                # nothing writing the data dir, so a caller (delete/wipe) may
                # rmtree it safely. Returns True: we DID act on a live launch.
                self._aborting.add(profile_name)
                while profile_name in self._starting:
                    self._lock.wait(timeout=10)
                # If the spawn registered a session before seeing the abort flag,
                # tear it down here.
                proc = self._active_sessions.pop(profile_name, None)
                notifier = self._stop_notifiers.pop(profile_name, None)
                self._forget_session_facts(profile_name)
                if notifier:
                    notifier.set()
                if proc is not None:
                    terminate(proc, profile_name, timeout)
                logger.info("Stopped in-flight browser for profile: %s", profile_name)
                return True
            if profile_name not in self._active_sessions:
                return False
            proc = self._active_sessions.pop(profile_name)
            notifier = self._stop_notifiers.pop(profile_name, None)
            self._forget_session_facts(profile_name)
        if notifier:
            notifier.set()
        terminate(proc, profile_name, timeout)
        logger.info("Stopped browser for profile: %s", profile_name)
        return True

    def _running_names_locked(self) -> set[str]:
        """The running names, assuming ``self._lock`` is ALREADY held.

        Factored out of ``running_profile_names`` so the names and the
        per-session builds can be taken in ONE acquisition (see
        ``running_session_builds``). ``self._lock`` is a Condition and is not
        reentrant, so a public method cannot call another public method that
        takes it — and duplicating the reaping logic in the second reader is
        how the two answers would drift apart.
        """
        stale = [
            n for n, p in self._active_sessions.items() if p.poll() is not None
        ]
        for n in stale:
            self._active_sessions.pop(n, None)
            self._stop_notifiers.pop(n, None)
            self._forget_session_facts(n)
        # A profile whose spawn is still in flight counts as running so the
        # UI shows it busy and a second launch is refused.
        return set(self._active_sessions.keys()) | set(self._starting)

    def running_profile_names(self) -> set[str]:
        with self._lock:
            return self._running_names_locked()

    def running_session_builds(self) -> "dict[str, tuple[str, str | None] | None]":
        """Every RUNNING profile mapped to the (engine, build) it is executing
        from, or to None when that is not established (PS-221).

        THE KEY SET IS DELIBERATELY WIDER THAN ``running_profile_names()``
        -----------------------------------------------------------------
        It is ``running_profile_names() | self._survivors |
        self._indeterminate``, and both extra terms are load-bearing rather
        than tidy. A SURVIVOR is a browser a PREVIOUS persona left running: a
        real, probed-alive process executing out of a real build directory,
        which ``is_running()`` reports as running and the UI paints as running.
        An INDETERMINATE is the same kind of thing one probe short — a real
        recorded process whose liveness could not be settled (no psutil,
        permission denied, no create time captured at registration). But
        ``scan_survivors`` populates both maps only with names that are in
        NEITHER ``_active_sessions`` NOR ``_starting`` (that exclusion is
        correct for its own purpose — never shadow a session this run owns), so
        neither can ever appear in ``_running_names_locked()``.

        The consumer of this map spares the builds it can see and RECLAIMS the
        rest, so a name that is missing entirely does not produce an UNKNOWN —
        it produces a confident claim that the process's build is free, and its
        live build is deleted out from under it. Omitting either bucket would
        therefore make this map answer a question about "running" over a name
        set narrower than running.

        ⚠️ ``Liveness.UNKNOWN`` IS NOT "PROBABLY DEAD". A record that probes
        GONE is dropped by the registry as it reads, so it is never in either
        bucket; UNKNOWN means the question could not be answered about a
        process that may well be running. The psutil-absent shape is the one to
        hold in mind, because it fires for EVERY record at once: with psutil
        unavailable ``alive`` is empty and ``_survivors`` is empty, so the
        survivor widening alone protects nothing and every browser a previous
        persona left running would be invisible here.

        ``running_profile_names()`` is NOT widened to match, and that asymmetry
        is intentional for both buckets. Its callers are the UI's running
        snapshot and the launch-refusal path: survivors have their own handling
        there (``survivor_for``/``close_survivor``) and would double-count, and
        an indeterminate deliberately fails OPEN for launch refusal — an
        unanswerable question must not lock a user out of their own profile
        (``test_an_indeterminate_record_does_not_block_a_launch`` pins that,
        and it is correct). The two directions are opposite over the same
        UNKNOWN because the costs are: refusing a launch on no evidence costs
        the user their session, whereas deferring a prune on no evidence costs
        one prune cycle. The widening belongs to THIS map, whose contract is
        "every live thing that could be executing a build".

        A survivor's and an indeterminate's value is ALWAYS None, and None is
        the honest answer rather than a placeholder: ``SessionRecord`` carries
        the engine but no build, so there is genuinely no record of which build
        it is on. Resolving one from ``active_build()`` at scan time would
        invent a confident wrong answer — such a process predates our startup
        and may well be on an older build than the one active now, which is
        exactly the case that deletes it.

        The whole map is taken in ONE lock acquisition on purpose. Asking for
        the names and then asking for the builds would let a session start or
        stop between the two reads, and a consumer that spares "the builds in
        use" would then be acting on a name set that no longer matches — the
        skew is small and the consequence (deleting a build a live session is
        on) is not.

        A value of None means UNKNOWN for that profile, and there are exactly
        four ways to get one, all ordinary:

        * the profile is in ``_starting`` — its spawn is in flight, so nothing
          has been registered for it yet. This is why an in-flight launch is
          UNKNOWN BY CONSTRUCTION rather than by a stamp that happens to be
          missing;
        * the profile is a SURVIVOR — this process never launched it, so there
          is no session record of any kind to read (see above);
        * the profile is INDETERMINATE — a recorded process this run did not
          launch and whose liveness could not be settled. Same absence of a
          build for the same reason, and the same rule: not provably alive is
          not evidence of dead;
        * the build could not be read at launch (``engine_build_for`` returns
          None on any read failure, deliberately), so the pair is
          ``(engine, None)``.

        Note the last is distinguishable from the first three and a caller may
        care: the first three are ``None`` for the whole entry, the last is a
        pair whose build is None. All must be treated as "cannot say", never as
        "on no build".

        This reads the LIVE session map, never ``Profile.last_launch_build``.
        That field is a persisted LAST-launch stamp written after registration
        and best-effort, so for a running profile it can name the PREVIOUS
        launch's build — see ``_session_build`` for why intersecting it with
        the running names does not make it safe.
        """
        with self._lock:
            names = self._running_names_locked()
            out: "dict[str, tuple[str, str | None] | None]" = {
                n: self._session_build.get(n) for n in names
            }
            # Survivors and indeterminates last and unconditionally None.
            # `setdefault` rather than an update so a name that is somehow in
            # both keeps its resolved session pair — a session THIS run owns is
            # the better answer, and neither bucket may overwrite one with an
            # UNKNOWN.
            for n in self._survivors:
                out.setdefault(n, None)
            for n in self._indeterminate:
                out.setdefault(n, None)
            return out

    def running_count(self) -> int:
        return len(self.running_profile_names())

    def is_running(self, profile_name: str) -> bool:
        with self._lock:
            if profile_name in self._starting:
                return True
            if profile_name not in self._active_sessions:
                return profile_name in self._survivors
            if self._active_sessions[profile_name].poll() is None:
                return True
            del self._active_sessions[profile_name]
            self._stop_notifiers.pop(profile_name, None)
            self._forget_session_facts(profile_name)
            return False

    def scan_survivors(self) -> tuple[list[SessionRecord], list[SessionRecord]]:
        """Find browsers a PREVIOUS persona left running. Call once, at startup.

        Returns ``(alive, indeterminate)``, and the split is the whole contract:

        * ``alive`` — the record's process was PROBED and is still the process
          the record named. These are real survivors, and only these may make
          the guard refuse a launch.
        * ``indeterminate`` — a record exists but liveness could not be
          established (no psutil, permission denied, no create time to rule out
          pid reuse). These are NOT adopted as survivors and never refuse
          anything. They are returned so the caller can SAY so, because "we
          think something may still be running and cannot tell" is information
          the user should have, and it is the honest alternative to inventing
          either answer.

          They are ALSO retained on ``self._indeterminate``, for one reader
          only: ``running_session_builds()``. Refusing a launch and deferring a
          prune are opposite duties over the same UNKNOWN — refusing on no
          evidence costs the user their session, whereas deferring on no
          evidence costs one prune cycle — so this bucket fails OPEN for the
          first and CLOSED for the second. See ``running_session_builds`` for
          why a name that is merely absent from that map is not an UNKNOWN at
          all but a licence to delete the build it is executing from.

        A record whose process probed GONE is dropped from the file by the
        registry as it reads — that is the stale-record case, and the correct
        handling of it is silence: nothing survived, nothing to report, and
        crucially nothing to block.

        Calling this is what populates the guard, so a persona that never calls
        it behaves exactly as it did before this ticket. That is deliberate:
        headless lanes (tests, API, MCP) do not inherit a UI's survivor state
        by accident.
        """
        alive, unknown = self._registry.live_records()
        with self._lock:
            # Never shadow a session THIS run owns. A profile we have launched
            # ourselves is already tracked and stoppable through the normal
            # path; treating it as a survivor would offer the user a second,
            # weaker way to kill their own live session.
            self._survivors = {
                r.profile: r
                for r in alive
                if r.profile not in self._active_sessions
                and r.profile not in self._starting
            }
            # The indeterminate half, retained under the SAME exclusion and for
            # a different reader. It refuses nothing (see survivor_for /
            # is_running, which do not consult it, and
            # test_an_indeterminate_record_does_not_block_a_launch, which pins
            # that); its only consumer is running_session_builds(), which must
            # report a live-but-unprovable process as UNKNOWN rather than as
            # absent. Absent is not neutral there — it is a positive claim that
            # the build the process is executing from is free.
            self._indeterminate = {
                r.profile: r
                for r in unknown
                if r.profile not in self._active_sessions
                and r.profile not in self._starting
            }
            survivors = list(self._survivors.values())
        if survivors:
            logger.warning(
                "Found %d browser session(s) still running from a previous "
                "persona: %s", len(survivors),
                ", ".join(sorted(r.profile for r in survivors)),
            )
        if unknown:
            logger.warning(
                "Could not establish whether %d recorded session(s) are still "
                "running (%s); their launches are NOT refused.",
                len(unknown), ", ".join(sorted(r.profile for r in unknown)),
            )
        return survivors, unknown

    def survivors(self) -> list[SessionRecord]:
        """The surviving sessions found at startup and not yet resolved."""
        with self._lock:
            return list(self._survivors.values())

    def survivor_for(self, profile_name: str) -> "SessionRecord | None":
        """The survivor record blocking ``profile_name``, if there is one.

        RE-PROBED on every call rather than trusted from the scan. The scan is
        a point in time, and the user may well have closed the browser by hand
        between then and now — exactly the gesture a person takes when told
        "this profile is already open". Answering from the cached scan would
        keep refusing a launch against a window that is no longer there, which
        is the lockout arriving a few minutes late instead of immediately.
        """
        with self._lock:
            rec = self._survivors.get(profile_name)
        if rec is None:
            return None
        state = liveness_of(rec)
        if state is Liveness.ALIVE:
            return rec
        # GONE, or no longer determinable: stop refusing on its account.
        # UNKNOWN releases the block too — see the module docstring on why an
        # unanswerable question must not hold a profile hostage.
        self.forget_survivor(profile_name)
        return None

    def forget_survivor(self, profile_name: str) -> None:
        """Stop treating ``profile_name`` as having a surviving browser.

        Called when the survivor has been closed, when it has been shown to be
        gone, or when the user has been asked and chose to leave it running —
        in the last case the block is released because the user has made an
        informed decision, which is the opposite of silently adopting it.

        Drops ``_survivors`` only, NOT ``_indeterminate`` (PS-221) — deliberate,
        and the direction it errs in is the safe one. A name left on
        ``_indeterminate`` after its process dies keeps reading as UNKNOWN to
        ``running_session_builds``, which costs a prune cycle (a lost reclaim),
        never a deletion under a live browser. It cannot accumulate either:
        ``scan_survivors`` is startup-only and REASSIGNS that dict wholesale
        rather than mutating it, so the map never outlives one scan.
        """
        with self._lock:
            self._survivors.pop(profile_name, None)
        with contextlib.suppress(Exception):
            self._registry.forget(profile_name)

    def close_survivor(self, profile_name: str) -> bool:
        """Tear down a surviving browser at the USER'S request. True if gone.

        The only path in this class that kills a process persona did not start,
        and it exists solely so the answer to "this profile is already open"
        can be "close it". Never called automatically.
        """
        with self._lock:
            rec = self._survivors.get(profile_name)
        if rec is None:
            return True
        ok = terminate_record(rec)
        if ok:
            self.forget_survivor(profile_name)
            logger.info("Closed surviving browser for profile: %s", profile_name)
        else:
            logger.warning(
                "Could not confirm the surviving browser for %s is gone",
                profile_name,
            )
        return ok

    def close_all_survivors(self) -> list[str]:
        """Close every surviving browser. Returns the names that did NOT close.

        The bulk counterpart of :meth:`close_survivor`, and it carries the same
        licence: it is called ONLY from the exit-confirmation dialog, after the
        user has been shown these profiles by name and has pressed "close them
        and exit". That gesture is the consent — the ticket forbids a SILENT
        kill, not a kill the user asked for.

        It exists because ``shutdown_all`` structurally cannot do this job:
        that method reaps ``_active_sessions``, and a survivor is by definition
        not in it. Without this, the dialog named survivors among the browsers
        it promised to close and then left them running — a sentence that was
        untrue for precisely the browsers this ticket is about.

        RETURNS THE FAILURES RATHER THAN RAISING, so the caller can say so. A
        survivor we could not kill keeps its registry record (nothing here
        forgets a name that did not close), so the guard still refuses a second
        launch against it on the next start. Failing to close is survivable;
        failing to close *and* forgetting is the defect.
        """
        # SNAPSHOT UNDER THE LOCK, then close off-lock. close_survivor takes
        # this same Condition and does process IO, so iterating the live dict
        # here would both mutate it while reading it (close_survivor forgets
        # the name it closed) and hold the lock across a teardown.
        with self._lock:
            names = sorted(self._survivors)
        stubborn: list[str] = []
        for name in names:
            try:
                if not self.close_survivor(name):
                    stubborn.append(name)
            except Exception:
                logger.exception(
                    "Error closing the surviving browser for %s", name
                )
                stubborn.append(name)
        return stubborn

    def started_at(self, profile_name: str) -> float | None:
        """Wall-clock time the session was registered, or None if not running.

        read_cdp_port uses it as ``not_before`` so a stale DevToolsActivePort
        from a previous run of the same profile is never trusted."""
        with self._lock:
            return self._session_started_at.get(profile_name)

    def cdp_channel_open(self, profile_name: str) -> bool:
        """True if the LIVE session for ``profile_name`` has a CDP port open.

        The fact is the one captured when the session was registered, from the
        profile that was actually launched — NOT a re-read of the stored record.
        ``ProfileManager.set_ai_control`` saves ``p.ai_control`` without any
        reference to whether the profile is running, so a mid-session toggle
        moves the record while the bound port does not move at all. Answering
        from the record would report a channel closed while chromium is still
        listening on it; that is the falsely reassuring direction, and it is the
        defect this accessor exists to avoid.

        False for a profile that is not running: the entry is dropped at every
        teardown path (see ``_forget_session_facts``), so an ended session
        reports no channel rather than leaving a stale claim behind.

        Pure and cheap by construction — a dict lookup under the lock, exactly
        like ``started_at`` above. It performs no IO, which is what makes it
        safe to call from a render path.
        """
        with self._lock:
            return self._session_cdp_open.get(profile_name, False)

    def last_refusal(self, profile_name: str) -> Refusal | None:
        """The most recent REFUSED launch for ``profile_name``, or None.

        None means "no refusal on record for the current attempt" — either the
        profile has never been launched this session, or its latest attempt was
        not refused. It never means "refused, but we forgot which kind": a
        refusal is only ever replaced by a newer ATTEMPT, never downgraded to a
        bare boolean, because the whole point is that the causes are
        distinguishable at the surface.

        NOT gated on the profile being stopped, and that asymmetry with
        ``cdp_channel_open`` above is deliberate. That accessor answers about a
        LIVE session and must go quiet when the session dies. This one answers
        about an attempt that never produced a session at all, so there is no
        session whose end could retire it — it stands until the next attempt
        supersedes it (``start_thread``). That is what lets the marker still be
        there an hour later, which is the property the log line lacks.

        Pure and cheap — a dict lookup under the lock, exactly like the
        accessors above, doing no IO so it is safe on a render path. The
        returned ``Refusal`` is frozen, so a caller cannot edit the record it
        is reading.
        """
        with self._lock:
            return self._last_refusal.get(profile_name)

    def forget_refusal(self, profile_name: str) -> None:
        """Drop the recorded refusal for ``profile_name`` — the profile IDENTITY
        under that key is gone (deleted, wiped, renamed away, or overwritten).

        DELIBERATELY NOT PART OF ``_forget_session_facts``, and the distinction
        is the whole point. That helper tears down facts about a LIVE SESSION,
        and a refusal must SURVIVE a teardown: a launch that never became a
        session has no session whose end could retire it, and being there an
        hour later is the property this ticket exists to deliver. Folding this
        in there would erase the marker at the moment it becomes the only
        remaining evidence.

        Destruction of the identity is a DIFFERENT EVENT from the end of a
        session, and it is the one event that must take the verdict with it.
        ``_last_refusal`` is keyed by profile NAME, and a name is not a stable
        identity in this codebase — ``ProfileManager`` deletes them, wipes them,
        re-keys them on rename, and overwrites them on import, after which the
        SAME STRING can name a different profile. Left in place, the verdict is
        then attributed to a profile that never attempted a launch: the card
        renders a red refusal chip, and because ``humanize_since`` re-derives the
        age against the current clock it reads "just now" — the most urgent form
        of the marker, on the least deserving profile. It would also point the
        operator at a proxy check for a proxy that may be perfectly healthy,
        which is the exact wasted trip the disproven/unknown wording split exists
        to prevent.

        So the survival rule is scoped to what it was always meant to mean: the
        verdict outlives the SESSION, never the SUBJECT.

        Idempotent, and safe for a name that has no verdict — every caller is a
        lifecycle path that cannot know whether a refusal was ever recorded, and
        making them ask first would be a race as well as a nuisance. Takes the
        lock itself (unlike ``_forget_session_facts``, which is called from
        inside existing critical sections) because its callers reach it from
        ``ProfileManager``, holding no lock of ours.
        """
        with self._lock:
            self._last_refusal.pop(profile_name, None)

    def forget_identity(self, profile_name: str) -> None:
        """Drop EVERYTHING this launcher remembers under ``profile_name`` because
        the identity behind that name is gone — deleted, wiped, renamed away, or
        overwritten by an import.

        THE ONE CONSUMER OF ``ProfileManager``'s identity event, and it exists so
        there is exactly one obvious place for a name-keyed fact to join. The
        hook in ``src/ui/app.py`` used to be ``forget_refusal`` itself, a single
        bare method, which meant the second name-keyed store — the DURABLE
        survivor registry — was never told. That omission was strictly worse than
        the refusal-chip orphan it sat beside:

        * ``_last_refusal`` dies with the process, bounding a stale verdict to
          one persona run. A survivor record is on disk and is re-adopted by
          ``scan_survivors()`` at every subsequent startup, so it outlives
          restarts indefinitely.
        * A stale verdict renders a wrong chip. A stale survivor record REFUSES
          THE LAUNCH: a profile created seconds ago and never opened is reported
          "already open".
        * The operator's only escape from that block is the card's ``[ close ]``
          button, which reaps the recorded process GROUP — a gesture aimed at a
          pid belonging to a profile that no longer exists, or after pid reuse at
          something unrelated. ``resolve_group``/``signallable_group`` bound the
          blast radius; they cannot make the gesture correct.

        The registry's own fail-open design does not cover this. Every read there
        is grounded in a liveness probe, which is exactly right for a record whose
        PROCESS died — but here the process is genuinely alive and ``liveness_of``
        correctly answers ALIVE. What changed is not the process but the SUBJECT
        of the name, and that is the axis no probe measures.

        DELIBERATELY NOT FOLDED INTO ``_forget_session_facts``. That helper tears
        down facts about a LIVE SESSION, and both of the facts dropped here must
        SURVIVE a teardown: a refusal outlives the session it describes (see
        ``forget_refusal``), and a survivor record must outlive persona's own exit
        or the PS-223 lockout comes straight back — ``shutdown_all`` cannot reap a
        survivor, so forgetting one on a teardown erases the guard for a browser
        that is still on screen.

        Idempotent, and safe for a name that has neither a verdict nor a record —
        every caller is a lifecycle path that cannot know whether either was ever
        written. Takes no lock of its own; both callees do their own locking.
        """
        self.forget_refusal(profile_name)
        self.forget_survivor(profile_name)

    def _forget_session_facts(self, profile_name: str) -> None:
        """Drop every per-session fact recorded for ``profile_name``.

        The caller MUST already hold ``self._lock`` — every teardown site does,
        and this is called from inside those critical sections rather than
        taking the lock itself (``self._lock`` is a Condition, not reentrant).

        One helper instead of parallel ``.pop()`` lines at each site, on purpose:
        the facts are popped at SEVEN places (six single-session teardowns plus
        the bulk clear in ``shutdown_all``), which use three different key
        expressions (``profile.name``, ``profile_name`` and a loop variable), so
        the key is taken as an argument rather than assumed. A dict that gets
        added here later is then cleaned up at all seven sites by construction —
        the failure mode this guards is a dead session still reporting an open
        CDP channel, which would light the indicator over a browser that is no
        longer running.
        """
        self._session_started_at.pop(profile_name, None)
        self._session_cdp_open.pop(profile_name, None)
        # PS-221: the live (engine, build) of this session. Dropped here for
        # exactly the reason this helper exists — a dead session must stop
        # asserting things about itself. Left behind, it would tell the engine
        # prune to SPARE a build nothing is running from, which is a disk leak
        # rather than a deletion, so it fails in the safe direction; it is
        # dropped anyway because "safe direction" is not the same as correct.
        self._session_build.pop(profile_name, None)
        # AND THE PERSISTED MIRROR. Dropped HERE, in the shared helper, rather
        # than at the six call sites: the whole reason this helper exists is
        # that a per-session fact added without visiting every teardown site
        # leaves a dead session still asserting something about itself. For the
        # in-memory dicts that mistake lights a stale CDP indicator; for this
        # one it REFUSES A LAUNCH on a browser that has already exited, which
        # is the lockout the ticket forbids. Putting it in the helper makes
        # covering all seven sites structural instead of remembered.
        #
        # Swallowed: a registry we cannot write must not break a teardown. The
        # cost of a failed forget is one stale record, and a stale record is
        # already handled — it probes GONE and is dropped at the next read.
        with contextlib.suppress(Exception):
            self._registry.forget(profile_name)

    def _forget_all_session_facts(self, terminated: "list[str] | None" = None) -> None:
        """Bulk counterpart of ``_forget_session_facts`` for ``shutdown_all``.

        The caller MUST already hold ``self._lock``. Kept beside its per-session
        twin so the two cannot drift: this is the site that tears down EVERY
        session at once, and it is the one most easily forgotten when a new
        per-session dict is introduced.

        ``terminated`` names the sessions ``shutdown_all`` ACTUALLY REAPED, and
        only their persisted records are dropped.
        """
        self._session_started_at.clear()
        self._session_cdp_open.clear()
        self._session_build.clear()  # PS-221 — see the per-session twin above
        # AND THE PERSISTED MIRROR — BUT ONLY FOR WHAT WE KILLED.
        #
        # This used to call registry.forget_all(), on the reasoning that
        # shutdown_all is the clean/atexit path, so an empty file at startup
        # would mean "persona exited cleanly" and any surviving record would
        # mean "it did not". That equivalence was WRONG IN THE ONE CASE THE
        # TICKET IS ABOUT: shutdown_all reaps only _active_sessions, and a
        # SURVIVOR inherited from a previous persona is in neither dict — so
        # the bulk wipe deleted the record of a browser this process had just
        # failed to kill, and the next start had no idea it was there. A clean
        # quit was enough to erase the guard and hand back the double launch.
        #
        # The equivalence it protected turns out to be DECORATIVE. Nothing
        # refuses a launch because a record EXISTS: live_records() probes every
        # record it reads and drops the dead ones as it goes, survivor_for()
        # re-probes before it blocks anything, and create-time comparison
        # settles pid reuse. Record presence is therefore only ever an
        # invitation to look, never a verdict — which is exactly what makes
        # keeping a survivor's record safe. A preserved record cannot resurrect
        # a stale block; it can only preserve a true one.
        #
        # Swallowed for the same reason as its per-session twin: a registry we
        # cannot write must not break a teardown.
        for name in terminated or []:
            with contextlib.suppress(Exception):
                self._registry.forget(name)

    def _monitor_process(
        self,
        proc: subprocess.Popen,
        name: str,
        log_callback: Callable[[str], None],
        on_ready: Callable[[], None] | None,
        notify_stopped: Callable[[], None],
        close_reason: "list[str | None]",
        last_output: "deque[str]",
        on_cert_trust: Callable[[str | None], None] | None = None,
    ) -> None:
        # The process being up is what makes the profile stoppable: report
        # ready NOW so the card shows a killable [stop] while the engine is
        # still coming up. Chromium never prints a readiness marker at all,
        # and gating Firefox on its BROWSER_STARTED left a wedged proxied
        # launch stuck "loading" with no stop button (#137).
        if on_ready is not None:
            on_ready()
        try:
            if proc.stdout is None:
                return
            for line in iter(proc.stdout.readline, ""):
                msg = line.strip()
                if not msg:
                    continue
                last_output.append(msg)
                m = _CLOSE_REASON.search(msg)
                if m:
                    close_reason[0] = m.group(1)
                if msg == "BROWSER_STARTED":
                    log_callback("Browser started!")
                    logger.info("Browser started for profile: %s", name)
                    continue
                if msg == "BROWSER_CLOSED":
                    logger.info("Browser close detected for profile: %s", name)
                    notify_stopped()
                    terminate(proc, name, timeout=1)
                    continue
                if msg.startswith("LAUNCH_FAILED:") or msg == "LAUNCH_CANCELLED":
                    log_callback(f"[{name}] {msg}")
                    logger.warning("Launch failed for profile %s: %s", name, msg)
                    close_reason[0] = "launch-failed"
                    notify_stopped()
                    terminate(proc, name, timeout=1)
                    break
                _trust = cert_trust_status_from(msg)
                if _trust is not None:
                    # Announced once by the engine and, before this, consumed by
                    # nobody. Record it so the outcome outlives the session; the
                    # message still falls through to the log below so the
                    # Activity Log reads exactly as it did.
                    if on_cert_trust is not None:
                        try:
                            on_cert_trust(_trust)
                        except Exception:
                            # Persisting the status must never take down the
                            # monitor thread — losing the monitor would leave a
                            # live browser unreaped.
                            logger.exception(
                                "Failed to record cert trust status for %s", name
                            )
                    logger.info(
                        "mTLS CA trust outcome for profile %s: %s", name, _trust
                    )
                if is_engine_noise(msg):
                    logger.debug("[%s] %s", name, msg)
                    continue
                if len(msg) > 400:
                    msg = msg[:400] + "..."
                log_callback(f"[{name}] {msg}")
                logger.debug("[%s] %s", name, msg)
        except Exception as e:
            logger.exception("Monitor error for profile %s: %s", name, e)
            log_callback(f"[{name}] Monitor error: {e}")
        finally:
            if proc.stdout is not None:
                with contextlib.suppress(Exception):
                    proc.stdout.close()
