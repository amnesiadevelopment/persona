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
            with open(self._log_path, errors="replace") as fh:
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
        """
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

    log = tempfile.NamedTemporaryFile(
        prefix="persona-ui-serve-", suffix=".log", delete=False, mode="w"
    )
    env = dict(os.environ, PERSONA_HOME=home)
    # A served app must never inherit a stale display or the selftest gate.
    env.pop("PERSONA_SELFTEST", None)

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=repo_root,
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
