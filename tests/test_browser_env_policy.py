"""The launched browser must not inherit the operator's identity.

Covers both engines at their two different seams:

* Chromium builds an ``env`` copy and passes it to ``Popen(env=...)``, so the
  assertion is on the dict actually handed to the child.
* Firefox on Linux FORKS and the child scrubs its own ``os.environ``, so the
  assertion is on the environment *the forked child actually has* — read back
  out of a real fork over a pipe. Checking that a helper was called would pass
  on an inert implementation; this cannot.

The parent-process tests are the other half of the same story: persona's own
environment (and therefore every other concurrently-open profile's) must come
through a launch untouched.
"""

import json
import os
import threading

import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile
from src.services.browser.env_policy import OPERATOR_IDENTITY_VARS

# A parent polluted the way a real operator's shell is. The socket path is the
# shape ssh-agent actually produces, so a reader can see what is at stake: it
# is a live handle onto the agent, not a label.
POLLUTED_PARENT = {
    "SSH_AUTH_SOCK": "/tmp/ssh-XXXXcAgEnT/agent.1337",
    "USER": "operator",
    "LOGNAME": "operator",
    "HOSTNAME": "operator-laptop",
    "MAIL": "/var/mail/operator",
}


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _pollute(monkeypatch):
    for key, value in POLLUTED_PARENT.items():
        monkeypatch.setenv(key, value)


def _spawn_chromium(monkeypatch, tmp_path, profile, linux=False):
    """Mirrors tests/test_process.py::_spawn_chromium_args — returns the dict of
    kwargs the launcher handed to Popen, including the env under test."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(process._platform, "IS_LINUX", bool(linux))
    process.spawn_browser(profile)
    return captured


# --------------------------------------------------------------------------
# Chromium — the env dict actually handed to the child
# --------------------------------------------------------------------------


def test_chromium_child_env_carries_no_operator_identity(monkeypatch, tmp_path):
    # AC1. Asserted against a POLLUTED parent: a clean parent would pass on an
    # implementation that scrubs nothing at all.
    _pollute(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="ident-linux"), linux=True
    )
    for var in OPERATOR_IDENTITY_VARS:
        assert var not in captured["env"], f"{var} reached the chromium child"


def test_chromium_scrubs_identity_on_non_linux_too(monkeypatch, tmp_path):
    # Chromium passes env= on every platform, so unlike the firefox half this
    # guarantee is not Linux-only. Pinning it keeps a future refactor from
    # quietly folding the scrub into the IS_LINUX branch just below it.
    _pollute(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="ident-win"), linux=False
    )
    for var in OPERATOR_IDENTITY_VARS:
        assert var not in captured["env"]


def test_chromium_ssh_agent_socket_specifically_is_gone(monkeypatch, tmp_path):
    # The load-bearing one, named on its own: SSH_AUTH_SOCK is a capability
    # handle, not a passive identity string. Asserts on the VALUE's absence so
    # the test still bites if the key were re-added empty.
    _pollute(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="ident-ssh"), linux=True
    )
    assert "SSH_AUTH_SOCK" not in captured["env"]
    assert POLLUTED_PARENT["SSH_AUTH_SOCK"] not in captured["env"].values()


def test_chromium_launch_leaves_the_parent_environment_intact(monkeypatch, tmp_path):
    # AC4. persona's own process must survive a launch unchanged — the scrub
    # works on a COPY. If this ever fails, every other open profile (and the
    # app itself) just lost its environment.
    _pollute(monkeypatch)
    _spawn_chromium(monkeypatch, tmp_path, Profile(name="ident-parent"), linux=True)
    for var in OPERATOR_IDENTITY_VARS:
        assert os.environ.get(var) == POLLUTED_PARENT[var]


def test_chromium_scrub_leaves_unrelated_environment_alone(monkeypatch, tmp_path):
    # The scrub is a named list, not a purge: an unrelated variable the browser
    # legitimately needs must still reach it.
    _pollute(monkeypatch)
    monkeypatch.setenv("PERSONA_UNRELATED_CANARY", "keep-me")
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="ident-canary"), linux=True
    )
    assert captured["env"].get("PERSONA_UNRELATED_CANARY") == "keep-me"


# --------------------------------------------------------------------------
# Firefox — a REAL fork, asserting the child's own environment
# --------------------------------------------------------------------------


def _child_environ_after_fork(tmp_path, in_thread=False, is_linux=True):
    """Fork for real, run ``_child``, and report the child's OWN os.environ back
    over a pipe.

    The engine launch itself is replaced (in the child, after the fork) with a
    reporter, so what comes back is the environment ``_child`` had established
    by the time it would have handed control to the browser. Nothing is
    asserted about calls — only about the environment that actually exists in
    the child process.
    """
    read_fd, write_fd = os.pipe()
    report_r, report_w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            os.close(report_r)
            il._platform.IS_LINUX = is_linux

            def _report(cfg, profile_dir, emit, _finish, stop_event, _in_thread):
                payload = json.dumps(
                    {k: os.environ.get(k) for k in OPERATOR_IDENTITY_VARS}
                )
                with os.fdopen(report_w, "w") as fh:
                    fh.write(payload)
                _finish()

            il._launch_and_watch = _report
            il._child(
                {"profile_dir": str(tmp_path)},
                write_fd,
                stop_event=threading.Event() if in_thread else None,
            )
        except BaseException:
            pass
        os._exit(0)

    os.close(write_fd)
    os.close(report_w)
    with os.fdopen(report_r) as fh:
        payload = fh.read()
    os.fdopen(read_fd).close()
    os.waitpid(pid, 0)
    assert payload, "the forked child reported no environment"
    return json.loads(payload)


def test_forked_firefox_child_carries_no_operator_identity(monkeypatch, tmp_path):
    # AC2. The fork is real and the assertion is on the child's own environ,
    # not on the parent's and not on the shape of a call.
    _pollute(monkeypatch)
    child_env = _child_environ_after_fork(tmp_path)
    for var in OPERATOR_IDENTITY_VARS:
        assert child_env[var] is None, f"{var} reached the forked firefox child"


def test_forked_firefox_child_does_not_disturb_the_parent(monkeypatch, tmp_path):
    # AC4 for the firefox half. A fork has separate memory, so the child's
    # scrub of its own os.environ must be invisible here.
    _pollute(monkeypatch)
    _child_environ_after_fork(tmp_path)
    for var in OPERATOR_IDENTITY_VARS:
        assert os.environ.get(var) == POLLUTED_PARENT[var]


def test_thread_path_child_leaves_its_process_environment_alone(monkeypatch, tmp_path):
    # THE HAZARD, asserted rather than assumed. On Windows/macOS this same
    # _child runs as a THREAD of the manager process, where os.environ IS
    # persona's own. Scrubbing there would strip the app and every other open
    # profile. IS_LINUX is left TRUE here on purpose: it isolates the in_thread
    # half of the guard, so a scrub gated on platform alone fails this test.
    _pollute(monkeypatch)
    child_env = _child_environ_after_fork(tmp_path, in_thread=True, is_linux=True)
    for var in OPERATOR_IDENTITY_VARS:
        assert child_env[var] == POLLUTED_PARENT[var], (
            f"{var} was scrubbed on the thread path — that mutates persona's "
            "own environment and every concurrently-open profile's"
        )


# --------------------------------------------------------------------------
# The shared list itself
# --------------------------------------------------------------------------


def test_both_engines_share_one_scrub_list():
    # Two divergent literal tuples in two launchers is the predictable
    # regression here: a name added at one seam and forgotten at the other is a
    # leak that looks fixed. Both launchers must reach the same object.
    assert process.scrub_operator_identity.__module__ == (
        "src.services.browser.env_policy"
    )
    assert il.scrub_current_process_environ.__module__ == (
        "src.services.browser.env_policy"
    )
    assert set(OPERATOR_IDENTITY_VARS) == {
        "SSH_AUTH_SOCK",
        "USER",
        "LOGNAME",
        "HOSTNAME",
        "MAIL",
    }


def test_scrub_does_not_touch_the_deliberately_excluded_names():
    # XAUTHORITY / TZ / LANG / LC_ALL / TMPDIR are each excluded for a stated
    # reason (see env_policy's docstring). Widening the list is a decision, not
    # a tidy-up: this test makes that decision visible in review.
    env = {
        "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
        "TZ": "Europe/Berlin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
    }
    from src.services.browser.env_policy import scrub_operator_identity

    scrub_operator_identity(env)
    assert env == {
        "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
        "TZ": "Europe/Berlin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp",
    }
