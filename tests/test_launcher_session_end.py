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
