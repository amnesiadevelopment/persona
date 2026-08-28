"""Serve persona's real ``App`` over flet web mode, isolated, in a subprocess.

The application is served EXACTLY as it ships: this calls the real
``src.ui.app.App._main`` that ``App.run()`` calls, changing only the flet view
(``WEB_BROWSER`` instead of the default desktop window). No production code is
modified, imported differently, or given a test-only branch.

Two constraints shape the design:

**A subprocess, not a thread.** ``ft.run`` owns an event loop and blocks for
the life of the app; persona additionally installs a single-instance lock and
process-wide logging. Running it in-process would contaminate the test
interpreter and make teardown unreliable.

**PERSONA_HOME is set before ``src.*`` is imported.** ``src/core/config.py``
resolves the runtime root at IMPORT time (``PERSONA_HOME = _ensure_home(...)``),
so an environment variable set after the import is simply ignored — the app
would write to the developer's real ``~/.persona``. The isolation is therefore
done in the child's environment, before the child imports anything.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .watchdog import child_pids, reap_process_tree

# The group-session kwargs come from the PRODUCT's own owner rather than being
# restated here, so the tooling path and the product paths cannot drift: a fix
# to one is a fix to both. `tests/` imports `src.*` throughout this suite.
from src.services.browser.process_group import (  # noqa: E402
    popen_in_new_session,
    recorded_group,
)

#: How long to wait for the served app to answer on its port. The real app
#: loads the container, purges trash, and paints a splash before serving, so
#: this is generous — a slow CI box must not read as "web mode is broken".
STARTUP_TIMEOUT = 120.0

_CHILD = textwrap.dedent(
    """
    import os, sys
    # BEFORE any src.* import: config.py resolves PERSONA_HOME at import time.
    os.environ["PERSONA_HOME"] = {home!r}
    sys.path.insert(0, {repo!r})

    import flet as ft
    from src.core.container import Container
    from src.ui.app import App

    {patch}

    gui = App(Container())
    # The real App._main — the same callable App.run() hands to ft.run().
    ft.run(gui._main, view=ft.AppView.WEB_BROWSER, port={port:d})
    """
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class ServedApp:
    """A running persona instance and the isolated home it writes to."""

    url: str
    home: str
    port: int
    process: subprocess.Popen

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def output(self) -> str:
        """Whatever the child wrote — the only diagnosis when startup fails."""
        try:
            with open(self._log_path, errors="replace", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    _log_path: str = ""

    def descendants(self) -> list[int]:
        """Every process this served app is responsible for, itself included.

        Exposed so a test can ASSERT the cleanup rather than trust it. "The
        finally block ran" is not evidence that nothing survived.
        """
        return [self.process.pid, *sorted(child_pids(self.process.pid))]

    def stop(self) -> None:
        """Stop the served app and everything it spawned.

        THE WHOLE TREE, not just the direct child. ``ft.run`` is not a leaf:
        the child this class holds is a python process that starts flet, which
        starts its own. Terminating only the pid we hold leaves those
        grandchildren reparented to init — still holding the port, still
        running — which is a cleanup that reads as successful while moving the
        hang rather than fixing it.

        The descendants are therefore collected BEFORE anything is signalled:
        once the parent dies they are no longer reachable from the pid we hold.

        PS-192 adds a GROUP backstop after the walk. The walk is still the
        primary reaper and is still correct, but it can only see what is
        reachable from the pid we hold AT THE MOMENT IT LOOKS — so a
        grandchild that is spawned, or reparented, in the window between the
        snapshot and the signal is invisible to it. The child now leads its own
        session, so one `killpg` covers exactly that residue. Belt and braces
        deliberately: this is the path that ran 12.5h on a user's workstation.
        """
        # Resolved BEFORE anything is signalled, for the same reason the
        # descendants are: once the leader is reaped and waited on, its pid can
        # be recycled and must not be re-resolved into somebody else's group.
        #
        # PREFERS THE VALUE RECORDED AT LAUNCH. `resolve_group` asks the kernel,
        # and the kernel stops answering (ESRCH) the moment the leader has been
        # waited on — while the GROUP is still alive and still killable. That
        # made the backstop return None on precisely the `else` branch below,
        # whose whole premise is that the parent has ALREADY EXITED and left a
        # serving grandchild. The backstop was therefore absent exactly where
        # the orphans are the problem. This is the TOOLING path — the class of
        # process observed at 361% CPU for 12.5h on a user's workstation.
        pgid = recorded_group(self.process)

        if self.process.poll() is None:
            family = self.descendants()
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                with contextlib.suppress(Exception):
                    self.process.wait(timeout=10)
            # Whatever outlived its parent gets reaped explicitly.
            for pid in family:
                reap_process_tree(pid, grace=2.0)
        else:
            # Even an exited parent can leave a serving grandchild behind.
            for pid in self.descendants():
                reap_process_tree(pid, grace=2.0)

        self._reap_group(pgid)

    def _reap_group(self, pgid: "int | None") -> bool:
        """SIGKILL whatever is left in the served app's process group.

        Runs AFTER the descendant walk, so by design it is normally a no-op
        that finds an empty group — it exists for the residue the walk cannot
        see. Never raises: a teardown that throws would mask the test failure
        that sent us here.
        """
        if pgid is None:
            return False
        import signal

        try:
            os.killpg(pgid, getattr(signal, "SIGKILL", 9))
            return True
        except ProcessLookupError:
            # An empty group is the SUCCESS case: the walk got everything.
            return True
        except Exception:
            return False


@contextlib.contextmanager
def serve_app(repo_root: str, home: str | None = None, patch: str = ""):
    """Serve persona in web mode against an isolated home; stop it on exit.

    ``patch`` is python executed in the child AFTER ``src`` is importable and
    BEFORE the app is constructed. It exists for the negative control — the
    deliberate break that proves a driven test can actually go red — and is
    empty for every ordinary use.
    """
    home = home or tempfile.mkdtemp(prefix="persona-ui-driver-")
    port = _free_port()
    script = _CHILD.format(home=home, repo=repo_root, port=port, patch=patch)

    # BOTH ENDS OF THIS FILE MUST NAME utf-8, and pinning only one is worse
    # than pinning neither (PS-184). `output()` reads this path back with
    # encoding="utf-8"; the actual WRITER is the child's stdout, which decodes
    # under the locale and is cp1252 on Windows. Two locale-dependent ends
    # agree by accident; one pinned end and one locale end disagree by
    # construction -- and `errors="replace"` on the read means the
    # disagreement surfaces as silent mojibake in the only diagnosis available
    # when startup fails, rather than as a crash.
    #
    # `mode="w", encoding="utf-8"` pins this wrapper. It is NOT the load-bearing
    # half -- `stdout=log` hands the child a raw fd and the wrapper's codec is
    # bypassed -- but naming it stops the next reader concluding the wrapper is
    # the writer. PYTHONIOENCODING below is what actually pins the child.
    log = tempfile.NamedTemporaryFile(
        prefix="persona-ui-serve-", suffix=".log", delete=False, mode="w",
        encoding="utf-8",
    )
    # The child's stdout/stderr codec. Without this the child encodes its
    # diagnostics under the platform locale while output() decodes utf-8.
    env = dict(os.environ, PERSONA_HOME=home, PYTHONIOENCODING="utf-8")
    # A served app must never inherit a stale display or the selftest gate.
    env.pop("PERSONA_SELFTEST", None)
    # Nor an explicit settings-file override from the PARENT pytest process.
    # PERSONA_SETTINGS_FILE deliberately OUTRANKS PERSONA_HOME (settings._path),
    # so inheriting one would pull the child's settings back OUT of the isolated
    # home set two lines up — every served app would share the parent's single
    # file, and one test's write would be another's starting state. The whole
    # point of `home` is that this child owns its data; let it derive its
    # settings path from that home like a real install does.
    env.pop("PERSONA_SETTINGS_FILE", None)

    proc = popen_in_new_session(
        [sys.executable, "-c", script],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=repo_root,
        # PS-192: its own process group. This child starts flet, which starts
        # its own children — and the driven tests additionally launch a
        # playwright node driver with a chromium behind it. The descendant
        # WALK below is the primary reaper and stays; the group is the
        # BACKSTOP for what the walk structurally cannot see, because a
        # descendant enumerated from the pid we hold is only visible while its
        # parent is still alive. This is the TOOLING path — the class of
        # process observed at 361% CPU for 12.5h on a user's workstation.
        #
        # ⚠️ VIA THE HELPER, NOT BY HAND. The session alone is only half the
        # fix: `popen_in_new_session` also RECORDS the group on the handle, and
        # `stop()` needs that recorded value because it tears down on a branch
        # where the parent has already exited and the kernel will no longer
        # answer `getpgid`.
    )
    app = ServedApp(
        url=f"http://127.0.0.1:{port}/",
        home=home,
        port=port,
        process=proc,
        _log_path=log.name,
    )
    try:
        _await_ready(app)
        yield app
    finally:
        app.stop()
        with contextlib.suppress(Exception):
            log.close()


def _await_ready(app: ServedApp) -> None:
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if not app.is_alive():
            raise RuntimeError(
                f"persona exited before serving (rc={app.process.returncode}). "
                f"Output:\n{app.output()[-4000:]}"
            )
        try:
            with urllib.request.urlopen(app.url, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1.0)
    raise RuntimeError(
        f"persona did not serve on {app.url} within {STARTUP_TIMEOUT:.0f}s. "
        f"Output:\n{app.output()[-4000:]}"
    )
