import atexit
import contextlib
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable

from ...core.logging import get_logger
from ...models.profile import Profile
from .process import spawn_browser, terminate, wait_for_exit

logger = get_logger("browser.launcher")

_CLOSE_REASON = re.compile(r"LIFECYCLE close=(\S+)")

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
        self._lock = threading.Lock()
        self._active_sessions: dict[str, subprocess.Popen] = {}
        self._stop_notifiers: dict[str, threading.Event] = {}
        atexit.register(self.shutdown_all)

    def shutdown_all(self) -> None:
        with self._lock:
            for name, proc in list(self._active_sessions.items()):
                notifier = self._stop_notifiers.pop(name, None)
                if notifier:
                    notifier.set()
                terminate(proc, name)
            self._active_sessions.clear()
        logger.info("All browser sessions terminated")

    def start_thread(
        self,
        profile: Profile,
        log_callback: Callable[[str], None],
        on_start: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            if profile.name in self._active_sessions:
                return

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
                    self._active_sessions.pop(profile.name, None)
                    self._stop_notifiers.pop(profile.name, None)
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
            with self._lock:
                self._active_sessions[profile.name] = proc
                self._stop_notifiers[profile.name] = stop_event

            threading.Thread(
                target=self._monitor_process,
                args=(proc, profile.name, log_callback, on_ready,
                      notify_stopped, close_reason, last_output),
                daemon=True,
            ).start()
            threading.Thread(
                target=wait_for_exit,
                args=(proc, profile.name, notify_stopped),
                daemon=True,
            ).start()
        except Exception as e:
            logger.exception(f"Error starting browser for {profile.name}: {e}")
            log_callback(f"Error starting process: {e}")
            if on_stop:
                on_stop()

    def stop_profile(self, profile_name: str, timeout: int = 2) -> bool:
        with self._lock:
            if profile_name not in self._active_sessions:
                return False
            proc = self._active_sessions.pop(profile_name)
            notifier = self._stop_notifiers.pop(profile_name, None)
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
            return set(self._active_sessions.keys())

    def running_count(self) -> int:
        return len(self.running_profile_names())

    def is_running(self, profile_name: str) -> bool:
        with self._lock:
            if profile_name not in self._active_sessions:
                return False
            if self._active_sessions[profile_name].poll() is None:
                return True
            del self._active_sessions[profile_name]
            self._stop_notifiers.pop(profile_name, None)
            return False

    def _monitor_process(
        self,
        proc: subprocess.Popen,
        name: str,
        log_callback: Callable[[str], None],
        on_ready: Callable[[], None] | None,
        notify_stopped: Callable[[], None],
        close_reason: "list[str | None]",
        last_output: "deque[str]",
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
