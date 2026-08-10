import io
import threading

import src.services.browser.launcher as launcher_mod
from src.models.profile import Profile
from src.services.browser.launcher import BrowserLauncher


class _Stdout:
    """Replays scripted engine lines; flags EOF so the fake process can report
    itself exited only after the monitor consumed everything (no race between
    the monitor and wait_for_exit)."""

    def __init__(self, lines, eof):
        self._io = io.StringIO("".join(line + "\n" for line in lines))
        self._eof = eof

    def readline(self, *a):
        line = self._io.readline()
        if not line:
            self._eof.set()
        return line

    def close(self):
        pass


class _Proc:
    def __init__(self, lines, returncode=0):
        self._exited = threading.Event()
        self.stdout = _Stdout(lines, self._exited)
        self.returncode = returncode

    def wait(self, timeout=None):
        self._exited.wait(timeout)
        return self.returncode

    def poll(self):
        return self.returncode if self._exited.is_set() else None

    def terminate(self):
        self._exited.set()

    def kill(self):
        self._exited.set()


def _run_session(monkeypatch, lines, returncode=0):
    proc = _Proc(lines, returncode)
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    stopped = threading.Event()
    messages = []
    bl = BrowserLauncher()
    bl.start_thread(
        Profile(name="fox", engine="firefox", os_type="windows"),
        messages.append,
        on_stop=stopped.set,
    )
    assert stopped.wait(5)
    return messages


def test_started_at_set_while_running_cleared_after_end(monkeypatch):
    # audit7 LOW: read_cdp_port needs the session's launch time as `not_before`
    # so a stale DevToolsActivePort from a previous run is rejected. The launcher
    # records started_at on registration and clears it when the session ends.
    import time

    proc = _Proc([])  # never exits on its own; poll() stays None until we stop it
    monkeypatch.setattr(launcher_mod, "spawn_browser", lambda p: proc)
    # Keep the session alive: replace the monitor with one that blocks until the
    # process is terminated, so registration persists for the assertions.
    registered = threading.Event()

    def fake_monitor(*a, **k):
        registered.set()
        proc._exited.wait()

    monkeypatch.setattr(BrowserLauncher, "_monitor_process", fake_monitor)

    bl = BrowserLauncher()
    before = time.time()
    bl.start_thread(
        Profile(name="fox", engine="firefox", os_type="windows"),
        lambda m: None,
    )
    assert registered.wait(5)
    ts = bl.started_at("fox")
    assert ts is not None and ts >= before
    assert bl.started_at("absent") is None

    assert bl.stop_profile("fox")
    assert bl.started_at("fox") is None


def test_user_close_logs_quiet_session_end(monkeypatch):
    # A clean user close (window-gone from the close-watch) must stay quiet:
    # the plain "Session ended" line, no "unexpectedly" noise.
    messages = _run_session(monkeypatch, [
        "BROWSER_STARTED",
        "LIFECYCLE close=window-gone pids=[10] streak=2",
        "BROWSER_CLOSED",
    ])
    assert "Session ended: fox" in messages
    assert not any("unexpectedly" in m for m in messages)


def test_watch_timeout_end_logs_reason(monkeypatch):
    # #204: a session torn down by the watch's give-up is NOT a user close —
    # the bare "Session ended" hid every such kill. The close reason from the
    # LIFECYCLE line must reach the activity log.
    messages = _run_session(monkeypatch, [
        "BROWSER_STARTED",
        "LIFECYCLE close=no-process-timeout (launch never resolved a pid)",
        "BROWSER_CLOSED",
    ])
    assert any(
        "unexpectedly" in m and "no-process-timeout" in m for m in messages
    )
    assert "Session ended: fox" not in messages


def test_benign_engine_errors_skip_activity_log_but_hit_file_log(
    monkeypatch, caplog,
):
    # Benign chromium stderr (USB enumeration, OID metadata, aborted-
    # connection handshakes) must not reach the activity log as red ERROR
    # lines, but must stay in the app log for diagnosis. A real engine error
    # on the same stream must still surface.
    benign_usb = (
        "[21324:9448:0715/120000.123:ERROR:components\\device_event_log\\"
        "device_event_log_impl.cc:202] USB: usb_service_win.cc:108 "
        "SetupDiGetDeviceProperty(...) failed: Элемент не найден. (0x490)"
    )
    benign_ssl = (
        "[1:2:0715/120000.123:ERROR:net/socket/ssl_client_socket_impl.cc:926]"
        " handshake failed; returned -1, SSL error code 1, net_error -100"
    )
    real_error = (
        "[1:1:0715/120000.123:ERROR:gpu_process_host.cc(999)] "
        "GPU process exited unexpectedly: exit_code=139"
    )
    with caplog.at_level("DEBUG", logger="persona.browser.launcher"):
        messages = _run_session(monkeypatch, [
            "BROWSER_STARTED",
            benign_usb,
            benign_ssl,
            real_error,
            "LIFECYCLE close=window-gone pids=[10] streak=2",
            "BROWSER_CLOSED",
        ])
    assert not any("device_event_log_impl" in m for m in messages)
    assert not any("handshake failed" in m for m in messages)
    assert any("gpu_process_host" in m for m in messages)
    logged = [r.getMessage() for r in caplog.records]
    assert any("device_event_log_impl" in m for m in logged)
    assert any("handshake failed" in m for m in logged)


def test_engine_death_without_close_signal_logs_unexpected(monkeypatch, caplog):
    # The engine process dying with no BROWSER_CLOSED (segfault, python-level
    # crash) used to end in the same bare "Session ended". It must be flagged
    # as unexpected, with the exit code and the last engine output preserved
    # in the app log for diagnosis.
    with caplog.at_level("WARNING", logger="persona.browser.launcher"):
        messages = _run_session(monkeypatch, [
            "BROWSER_STARTED",
            "Crash Annotation GraphicsCriticalError: |[0][GFX1]: oh no",
        ], returncode=11)
    assert any("unexpectedly" in m for m in messages)
    assert "Session ended: fox" not in messages
    warned = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("11" in r.getMessage() for r in warned)
    assert any("GraphicsCriticalError" in r.getMessage() for r in warned)
