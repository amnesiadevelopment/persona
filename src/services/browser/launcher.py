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
from .process import spawn_browser, terminate, wait_for_exit
from .refusal import Refusal, classify_refusal

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
    "all-pids-exit",
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
    def __init__(self) -> None:
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
        atexit.register(self.shutdown_all)

    def shutdown_all(self) -> None:
        with self._lock:
            for name, proc in list(self._active_sessions.items()):
                notifier = self._stop_notifiers.pop(name, None)
                if notifier:
                    notifier.set()
                terminate(proc, name)
            self._active_sessions.clear()
            self._forget_all_session_facts()
        logger.info("All browser sessions terminated")

    def start_thread(
        self,
        profile: Profile,
        log_callback: Callable[[str], None],
        on_start: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        *,
        on_cert_trust: Callable[[str], None] | None = None,
    ) -> None:
        with self._lock:
            if profile.name in self._active_sessions or profile.name in self._starting:
                # Already running/starting: refuse the duplicate, but tell the
                # caller so it clears its own is_loading flag — returning silently
                # left the card stuck "loading" and uncontrollable (audit6 LOW b).
                if on_stop:
                    on_stop()
                return
            # Reserve the slot BEFORE releasing the lock so a concurrent launch
            # of this profile can't slip past while spawn_browser() runs. Clear
            # any stale abort flag from a prior aborted launch of this name.
            self._starting.add(profile.name)
            self._aborting.discard(profile.name)
            # A NEW attempt supersedes the previous verdict, so the card reports
            # the most recent one and never a badge the profile has outgrown.
            # Dropped HERE — at the attempt, not at its outcome — because the
            # only honest states are "refused, at this time" and "no verdict
            # yet": leaving the old refusal up while a relaunch is in flight
            # would assert a refusal the product is at that moment disproving.
            # Placed AFTER the duplicate-launch return above on purpose: a click
            # that gets refused as a duplicate is not an attempt and must not
            # erase the verdict from the attempt that did run.
            self._last_refusal.pop(profile.name, None)

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
            with self._lock:
                aborted = profile.name in self._aborting
                if not aborted:
                    self._active_sessions[profile.name] = proc
                    self._stop_notifiers[profile.name] = stop_event
                    self._session_started_at[profile.name] = time.time()
                    self._session_cdp_open[profile.name] = cdp_open
                    self._starting.discard(profile.name)
                    self._lock.notify_all()

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
            with self._lock:
                self._starting.discard(profile.name)
                self._aborting.discard(profile.name)
                # Record a REFUSAL (and only a refusal) on the profile that
                # refused. classify_refusal returns None for an ordinary
                # failure, which stays in the log and off the card — if every
                # transient spawn error marked a card, the operator would learn
                # to skim past the one marker that means a guard fired.
                refusal = classify_refusal(e, time.time())
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

    def running_profile_names(self) -> set[str]:
        with self._lock:
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

    def running_count(self) -> int:
        return len(self.running_profile_names())

    def is_running(self, profile_name: str) -> bool:
        with self._lock:
            if profile_name in self._starting:
                return True
            if profile_name not in self._active_sessions:
                return False
            if self._active_sessions[profile_name].poll() is None:
                return True
            del self._active_sessions[profile_name]
            self._stop_notifiers.pop(profile_name, None)
            self._forget_session_facts(profile_name)
            return False

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
        bare boolean, because the whole point is that the three causes are
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

    def _forget_all_session_facts(self) -> None:
        """Bulk counterpart of ``_forget_session_facts`` for ``shutdown_all``.

        The caller MUST already hold ``self._lock``. Kept beside its per-session
        twin so the two cannot drift: this is the site that tears down EVERY
        session at once, and it is the one most easily forgotten when a new
        per-session dict is introduced.
        """
        self._session_started_at.clear()
        self._session_cdp_open.clear()

    def _monitor_process(
        self,
        proc: subprocess.Popen,
        name: str,
        log_callback: Callable[[str], None],
        on_ready: Callable[[], None] | None,
        notify_stopped: Callable[[], None],
        close_reason: "list[str | None]",
        last_output: "deque[str]",
        on_cert_trust: Callable[[str], None] | None = None,
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
