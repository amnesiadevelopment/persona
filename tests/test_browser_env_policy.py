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

import pytest

import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile
from src.services.browser.env_policy import (
    CHILD_TMPDIR_NAME,
    OPERATOR_IDENTITY_VARS,
    STALE_RUNTIME_PATH_VARS,
    TEMP_DIR_VARS,
)

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


STALE_PARENT = {
    "FONTCONFIG_FILE": "/tmp/.mount_personaXYZ/etc/fonts/fonts.conf",
    "FONTCONFIG_PATH": "/tmp/.mount_personaXYZ/etc/fonts",
    "FONTCONFIG_SYSROOT": "/tmp/.mount_personaXYZ",
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


# Every test below drives `_child_environ_after_fork`, and that harness IS a
# fork: it asserts on the environment a real forked child ends up owning. On
# Windows `os.fork` does not exist, so without this guard these tests do not
# skip — they CRASH with `AttributeError`. That distinction is the whole point
# of the guard rather than a tidy-up: conftest.py exists to report every skip
# and the reason it gave, so an absence is DECLARED and an operator can read
# it. A crash is an absence too, but an undeclared one, and it is noise in the
# same column that is supposed to carry signal.
#
# Guarded on the CAPABILITY (`hasattr(os, "fork")`) rather than on
# `sys.platform == "win32"`, because fork is the thing the harness actually
# needs; any future interpreter without it is then covered by construction.
#
# THIS SKIPS THE HARNESS, NOT THE COVERAGE IT PROVIDES. The behaviour these
# tests pin is Linux-only BY DESIGN — `invisible_launch.py:2352` guards the
# scrub on `not in_thread and _platform.IS_LINUX`, so on Windows/macOS the
# same `_child` runs as a THREAD whose `os.environ` is persona's own and must
# not be touched. That recorded, deliberate absence is asserted on Linux by
# the two `test_thread_path_child_*` tests below, which hold IS_LINUX true to
# isolate the `in_thread` half of the guard. Skipping here removes no gate
# from any platform that could have run one.
requires_fork = pytest.mark.skipif(
    not hasattr(os, "fork"), reason="POSIX fork only"
)


def _child_environ_after_fork(tmp_path, in_thread=False, is_linux=True, cfg=None):
    """Fork for real, run ``_child``, and report the child's OWN os.environ back
    over a pipe.

    The engine launch itself is replaced (in the child, after the fork) with a
    reporter, so what comes back is the environment ``_child`` had established
    by the time it would have handed control to the browser. Nothing is
    asserted about calls — only about the environment that actually exists in
    the child process.

    ``cfg`` overrides what the child is handed. The default models a
    WELL-FORMED launch, carrying both keys the way ``process._spawn_invisible``
    builds them: ``profile_dir`` is the engine's inner ``.invisible-profile``
    and ``profile_data_dir`` is the profile data dir one level up, which is
    what the scratch pin hangs off. It used to omit the second key, which
    quietly made every caller of this helper a malformed-cfg launch — the
    child now refuses those (correctly), so a default that omitted it would
    test the refusal path in tests that are about something else entirely.
    Pass ``cfg`` explicitly to construct the malformed shape on purpose.
    """
    read_fd, write_fd = os.pipe()
    report_r, report_w = os.pipe()
    default_cfg = {
        "profile_dir": str(tmp_path / ".invisible-profile"),
        "profile_data_dir": str(tmp_path),
    }
    child_cfg = default_cfg if cfg is None else cfg
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            os.close(report_r)
            il._platform.IS_LINUX = is_linux

            def _report(cfg, profile_dir, emit, _finish, stop_event, _in_thread):
                payload = json.dumps(
                    {
                        k: os.environ.get(k)
                        for k in (
                            *OPERATOR_IDENTITY_VARS,
                            *STALE_RUNTIME_PATH_VARS,
                            *TEMP_DIR_VARS,
                        )
                    }
                )
                with os.fdopen(report_w, "w") as fh:
                    fh.write(payload)
                _finish()

            il._launch_and_watch = _report
            il._child(
                child_cfg,
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


@requires_fork
def test_forked_firefox_child_carries_no_operator_identity(monkeypatch, tmp_path):
    # AC2. The fork is real and the assertion is on the child's own environ,
    # not on the parent's and not on the shape of a call.
    _pollute(monkeypatch)
    child_env = _child_environ_after_fork(tmp_path)
    for var in OPERATOR_IDENTITY_VARS:
        assert child_env[var] is None, f"{var} reached the forked firefox child"


@requires_fork
def test_forked_firefox_child_does_not_disturb_the_parent(monkeypatch, tmp_path):
    # AC4 for the firefox half. A fork has separate memory, so the child's
    # scrub of its own os.environ must be invisible here.
    _pollute(monkeypatch)
    _child_environ_after_fork(tmp_path)
    for var in OPERATOR_IDENTITY_VARS:
        assert os.environ.get(var) == POLLUTED_PARENT[var]


@requires_fork
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


@requires_fork
def test_forked_firefox_child_carries_no_stale_runtime_paths(monkeypatch, tmp_path):
    # PS-85, THE DEFECT. The chromium seam scrubbed FONTCONFIG_* via an inline
    # tuple that the firefox seam never reached, so the forked child inherited
    # paths into an AppImage mount that is GONE after a self-update.
    #
    # This test fails against the pre-PS-85 tree: all three names survive into
    # the child with their stale values. The assertion is on the child's OWN
    # environ after a real fork, not on whether a helper was called.
    _pollute(monkeypatch)
    for key, value in STALE_PARENT.items():
        monkeypatch.setenv(key, value)

    child_env = _child_environ_after_fork(tmp_path)

    for var in STALE_RUNTIME_PATH_VARS:
        assert child_env[var] is None, (
            f"{var} reached the forked firefox child as "
            f"{child_env[var]!r} — a path into a mount that no longer exists"
        )


@requires_fork
def test_forked_firefox_child_stale_path_scrub_does_not_disturb_parent(
    monkeypatch, tmp_path
):
    # The firefox half of the parent-safety guarantee, for the second list. A
    # fork has separate memory, so the child scrubbing its own os.environ must
    # be invisible here — persona's own runtime still needs these to resolve
    # its bundled fonts.
    _pollute(monkeypatch)
    for key, value in STALE_PARENT.items():
        monkeypatch.setenv(key, value)

    _child_environ_after_fork(tmp_path)

    for var, value in STALE_PARENT.items():
        assert os.environ.get(var) == value


@requires_fork
def test_thread_path_child_leaves_stale_runtime_paths_alone(monkeypatch, tmp_path):
    # THE HAZARD for the second list, asserted rather than assumed. On
    # Windows/macOS this same _child runs as a THREAD of the manager process,
    # where os.environ IS persona's own. Scrubbing FONTCONFIG_* there would
    # strip the running app's own font configuration. IS_LINUX stays TRUE on
    # purpose so a scrub gated on platform alone fails this test.
    _pollute(monkeypatch)
    for key, value in STALE_PARENT.items():
        monkeypatch.setenv(key, value)

    child_env = _child_environ_after_fork(tmp_path, in_thread=True, is_linux=True)

    for var, value in STALE_PARENT.items():
        assert child_env[var] == value, (
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
    #
    # PS-85 changed WHICH symbol the chromium seam reaches — from
    # `scrub_operator_identity` to `scrub_inherited_environment`, the single
    # entry point that applies every list — because enumerating the scrubs at
    # each seam is what left FONTCONFIG_* scrubbed for chromium and inherited
    # by the forked firefox child. The assertion below is the same claim about
    # a renamed seam, not a weakened one: it still pins that both launchers
    # reach env_policy rather than a local literal.
    assert process.scrub_inherited_environment.__module__ == (
        "src.services.browser.env_policy"
    )
    assert il.scrub_current_process_environ.__module__ == (
        "src.services.browser.env_policy"
    )
    # The chromium seam must NOT have gone back to enumerating lists itself.
    assert not hasattr(process, "scrub_operator_identity")
    assert not hasattr(process, "scrub_stale_runtime_paths")
    assert set(OPERATOR_IDENTITY_VARS) == {
        "SSH_AUTH_SOCK",
        "USER",
        "LOGNAME",
        "HOSTNAME",
        "MAIL",
    }
    # The second list is pinned the same way, and for the same reason:
    # widening it is a decision, not a tidy-up.
    assert set(STALE_RUNTIME_PATH_VARS) == {
        "FONTCONFIG_FILE",
        "FONTCONFIG_PATH",
        "FONTCONFIG_SYSROOT",
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


# --------------------------------------------------------------------------
# The child's scratch directory (PS-129)
#
# Same shape as everything above, one axis over: not what the child must not
# LEARN or FOLLOW, but where its temp files LAND. Asserted against a POLLUTED
# parent throughout — a parent whose TMPDIR already points at the host's shared
# temp dir, which is what a real operator's shell hands persona. A clean parent
# would let an implementation that pins nothing pass.
# --------------------------------------------------------------------------

# The host's shared temp dir, the way an operator's environment actually
# presents it. This is the value the child must NOT keep.
HOST_TEMP_PARENT = {
    "TMPDIR": "/tmp",
    "TMP": "/tmp",
    "TEMP": "/tmp",
}


def _pollute_temp(monkeypatch):
    for key, value in HOST_TEMP_PARENT.items():
        monkeypatch.setenv(key, value)


def test_chromium_child_scratch_is_pinned_inside_the_profile(monkeypatch, tmp_path):
    # AC3/AC4 for the chromium seam. The assertion is on the env dict actually
    # handed to Popen, and on the PATH being under the profile's data dir —
    # which is the whole perimeter claim, since delete_profile renames that
    # directory into the trash and wipe_all_profiles rmtrees it.
    _pollute(monkeypatch)
    _pollute_temp(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="scratch-linux"), linux=True
    )
    profile_dir = os.path.join(str(tmp_path), "scratch-linux")
    expected = os.path.join(profile_dir, CHILD_TMPDIR_NAME)
    for var in TEMP_DIR_VARS:
        assert captured["env"][var] == expected, (
            f"{var} was left at {captured['env'].get(var)!r} — engine scratch "
            "outside the profile is reached by neither delete_profile nor "
            "wipe_all_profiles"
        )


def test_chromium_scratch_directory_exists_before_the_launch(monkeypatch, tmp_path):
    # An unwritable or missing TMPDIR can stop the engine starting, which would
    # turn a residue fix into a launch bug. The directory must therefore exist
    # by the time Popen is reached, not on first use by the child.
    _pollute_temp(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="scratch-exists"), linux=True
    )
    assert os.path.isdir(captured["env"]["TMPDIR"])


def test_chromium_pins_scratch_on_non_linux_too(monkeypatch, tmp_path):
    # Chromium hands Popen an env= copy on every platform, so — exactly like
    # the scrub — this half is NOT Linux-only. Pins that a future refactor
    # cannot quietly fold the pin into the IS_LINUX branch beside it.
    _pollute_temp(monkeypatch)
    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="scratch-win"), linux=False
    )
    expected = os.path.join(str(tmp_path), "scratch-win", CHILD_TMPDIR_NAME)
    for var in TEMP_DIR_VARS:
        assert captured["env"][var] == expected


def test_chromium_scratch_pin_does_not_move_personas_own_temp_dir(
    monkeypatch, tmp_path
):
    # The parent-safety half. `env` is a COPY, so pinning the child's scratch
    # must be invisible in this process — persona's own temp dir, and every
    # concurrently-open profile's, stay where they were.
    _pollute_temp(monkeypatch)
    _spawn_chromium(monkeypatch, tmp_path, Profile(name="scratch-parent"), linux=True)
    for var, value in HOST_TEMP_PARENT.items():
        assert os.environ.get(var) == value


@requires_fork
def test_forked_firefox_child_scratch_is_pinned_inside_the_profile(
    monkeypatch, tmp_path
):
    # AC3 for the firefox fork seam, asserted on the child's OWN environ after
    # a real fork rather than on whether a helper was called — an inert
    # implementation cannot pass this.
    _pollute(monkeypatch)
    _pollute_temp(monkeypatch)
    child_env = _child_environ_after_fork(
        tmp_path,
        cfg={
            "profile_dir": os.path.join(str(tmp_path), ".invisible-profile"),
            "profile_data_dir": str(tmp_path),
        },
    )
    expected = os.path.join(str(tmp_path), CHILD_TMPDIR_NAME)
    for var in TEMP_DIR_VARS:
        assert child_env[var] == expected, (
            f"{var} reached the forked firefox child as {child_env[var]!r}"
        )


@requires_fork
def test_both_engines_pin_the_same_scratch_directory(monkeypatch, tmp_path):
    # THE AGREE TEST (AC5). Two seams, one value: the regression this whole
    # module exists to prevent is a value decided at one engine and forgotten
    # at the other.
    #
    # FALSIFICATION, run before shipping: re-inlining the value at either seam
    # — e.g. `env["TMPDIR"] = os.path.join(profile_dir, ".persona-tmp")` in
    # process.py instead of the shared call — must turn this RED. It does: an
    # inline pin at the chromium seam sets TMPDIR but not TMP/TEMP, and the
    # per-var loop below catches the divergence.
    _pollute(monkeypatch)
    _pollute_temp(monkeypatch)

    profile_root = tmp_path / "agree"
    profile_root.mkdir()

    captured = _spawn_chromium(
        monkeypatch, tmp_path, Profile(name="agree"), linux=True
    )
    child_env = _child_environ_after_fork(
        profile_root,
        cfg={
            "profile_dir": str(profile_root / ".invisible-profile"),
            "profile_data_dir": str(profile_root),
        },
    )

    for var in TEMP_DIR_VARS:
        assert captured["env"][var] == child_env[var], (
            f"the two engines disagree on {var}: chromium pinned "
            f"{captured['env'][var]!r}, the forked firefox child "
            f"{child_env[var]!r}"
        )


@requires_fork
def test_thread_path_child_leaves_its_process_temp_dir_alone(monkeypatch, tmp_path):
    # THE HAZARD, asserted rather than assumed — and it is SHARPER here than
    # for the scrub. On Windows/macOS this same _child runs as a THREAD of the
    # manager process, where os.environ IS persona's own. Pinning there would
    # move persona's temp dir AND every concurrently-open profile's, pointing
    # them all at ONE profile's scratch directory: worse than leaving them in
    # /tmp, because it makes profiles share scratch.
    #
    # IS_LINUX is held TRUE on purpose, mirroring :255 — it isolates the
    # in_thread half of the guard, so a pin gated on platform alone fails here.
    _pollute(monkeypatch)
    _pollute_temp(monkeypatch)
    child_env = _child_environ_after_fork(
        tmp_path,
        in_thread=True,
        is_linux=True,
        cfg={
            "profile_dir": os.path.join(str(tmp_path), ".invisible-profile"),
            "profile_data_dir": str(tmp_path),
        },
    )
    for var, value in HOST_TEMP_PARENT.items():
        assert child_env[var] == value, (
            f"{var} was pinned on the thread path — that moves persona's own "
            "temp dir and every concurrently-open profile's onto one "
            "profile's scratch directory"
        )


@requires_fork
def test_forked_firefox_scratch_pin_does_not_disturb_the_parent(monkeypatch, tmp_path):
    # A fork has separate memory, so the child pinning its own os.environ must
    # be invisible in this process.
    _pollute_temp(monkeypatch)
    _child_environ_after_fork(
        tmp_path,
        cfg={
            "profile_dir": os.path.join(str(tmp_path), ".invisible-profile"),
            "profile_data_dir": str(tmp_path),
        },
    )
    for var, value in HOST_TEMP_PARENT.items():
        assert os.environ.get(var) == value


def test_both_engines_share_one_scratch_authority():
    # AC2, mirroring test_both_engines_share_one_scrub_list: both launchers
    # must reach env_policy rather than a local literal. Neither seam may
    # rebuild the path itself — that is precisely how FONTCONFIG_* ended up
    # scrubbed for chromium and inherited by the forked firefox child.
    assert process.pin_child_tmpdir.__module__ == "src.services.browser.env_policy"
    assert il.pin_current_process_tmpdir.__module__ == (
        "src.services.browser.env_policy"
    )
    # All three names, pinned as a set: TMPDIR is POSIX, TMP/TEMP are what
    # Windows resolves, and tempfile checks all three. Setting one and leaving
    # the others is how a value looks pinned on one platform and is not on
    # another. Widening or narrowing this is a decision, not a tidy-up.
    assert set(TEMP_DIR_VARS) == {"TMPDIR", "TMP", "TEMP"}


def test_scratch_directory_is_inside_the_wipeable_perimeter(tmp_path):
    # AC4 as a unit-level guard on the PATH (the live wipe proof is in the PR).
    # delete_profile renames the profile's data dir into the trash and
    # wipe_all_profiles rmtrees it, so a scratch path that is not UNDER that
    # directory is reached by neither.
    from src.services.browser.env_policy import browser_child_tmpdir

    profile_dir = str(tmp_path / "victim")
    scratch = browser_child_tmpdir(profile_dir)
    assert os.path.commonpath([profile_dir, scratch]) == profile_dir
    assert scratch != profile_dir


def test_the_scratch_directory_is_swept_so_only_one_session_lands_on_disk(tmp_path):
    # THE BOUND, and the one test here that a no-op CANNOT pass. Pinning
    # scratch inside the profile only decides WHERE it lands; without a sweep
    # it accumulates forever, so what used to be one shared copy in /tmp (which
    # the OS clears on reboot) becomes one PERMANENT copy per profile that
    # nothing in the tree ever reclaims — and a deleted profile parks it in the
    # trash for the full 30-day retention.
    #
    # Measured on the real engine: the AppImage extraction is ~714MB and
    # survives a clean exit, so this is the difference between bounded and
    # unbounded growth, not a tidy-up.
    #
    # FALSIFICATION: drop the rmtree from prepare_child_tmpdir and this goes
    # red on the planted file — the pin still "works" by every other test in
    # this module, which is exactly why the bound needs its own assertion.
    from src.services.browser.env_policy import prepare_child_tmpdir

    profile_dir = str(tmp_path / "recurring")
    os.makedirs(profile_dir)

    first = prepare_child_tmpdir(profile_dir)
    # Stand in for last session's residue: the engine's extraction, and a
    # nested directory, since rmtree vs unlink is part of what is under test.
    planted = os.path.join(first, "appimage_extracted_deadbeef")
    os.makedirs(planted)
    leftover = os.path.join(planted, "chrome")
    with open(leftover, "w") as fh:
        fh.write("last session")
    assert os.path.exists(leftover)

    second = prepare_child_tmpdir(profile_dir)

    assert second == first, "the scratch path must be stable across sessions"
    assert os.path.isdir(second), (
        "the sweep must leave the directory EXISTING and writable — an absent "
        "or unwritable TMPDIR can stop the engine starting"
    )
    assert not os.path.exists(leftover), (
        "last session's scratch survived into this one — scratch is unbounded "
        "and grows one copy per profile, forever"
    )
    assert os.listdir(second) == [], "the swept directory must start empty"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write bits")
def test_a_sweep_that_cannot_clear_does_not_fail_the_launch(tmp_path):
    # The degradation, stated in prepare_child_tmpdir's docstring and asserted
    # here so it stays true: a directory that cannot be CLEARED is a stale-cache
    # problem and must not fail the launch, unlike one that cannot be CREATED
    # (which means the pin did not happen, and the caller refuses over it).
    #
    # The unsweepable directory is built for REAL — a scratch dir with its
    # write bit dropped, so unlinking the file inside genuinely fails. An
    # earlier version of this test monkeypatched shutil.rmtree to raise, which
    # asserted on the mock rather than on the behaviour: it bypassed the very
    # ignore_errors= that is under test, so it would have passed against code
    # with no error handling at all.
    #
    # Mirrors sweep_key_material's "unreadable is not clean" degradation — the
    # precedent this sweep is modelled on.
    from src.services.browser.env_policy import prepare_child_tmpdir

    profile_dir = str(tmp_path / "unsweepable")
    os.makedirs(profile_dir)

    target = prepare_child_tmpdir(profile_dir)
    stuck = os.path.join(target, "wont-go")
    with open(stuck, "w") as fh:
        fh.write("residue")
    os.chmod(target, 0o500)  # r-x: entries cannot be unlinked

    try:
        again = prepare_child_tmpdir(profile_dir)
        assert again == target
        assert os.path.isdir(again), (
            "an unsweepable scratch directory must not fail the launch — "
            "a stale cache is not a perimeter failure"
        )
        # The honest outcome, asserted rather than glossed: the sweep did NOT
        # clear it. Unreadable is not clean, and the docstring says so.
        assert os.path.exists(stuck)
    finally:
        os.chmod(target, 0o700)


@requires_fork
def test_a_cfg_without_the_profile_data_dir_is_refused_not_launched_unpinned(tmp_path):
    # A cfg with no profile_data_dir reaches the SAME outcome as a failed
    # makedirs — the engine runs on the host's shared temp dir — so it must be
    # refused the same way rather than launched in silence.
    #
    # This is NOT the platform gap. The thread/non-Linux paths are deliberate,
    # recorded absences; a missing cfg key is a CALLER BUG, and folding the two
    # into one boolean let the bug wear the absence's clothes. _child_main
    # builds cfg from an unvalidated PERSONA_INVISIBLE_CFG blob, so the next
    # caller that omits the key has to hear about it.
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            il._platform.IS_LINUX = True
            il._launch_and_watch = lambda *a, **k: None
            # profile_dir present, profile_data_dir absent — the shape a caller
            # that only knows about the engine's inner profile would build.
            il._child({"profile_dir": str(tmp_path / ".invisible-profile")}, write_fd)
        except BaseException:
            pass
        os._exit(0)

    os.close(write_fd)
    with os.fdopen(read_fd) as fh:
        said = fh.read()
    os.waitpid(pid, 0)

    assert "LAUNCH_FAILED" in said, (
        "the child launched with no scratch pin and said nothing — the engine "
        f"would run on the host's shared temp dir; the parent saw {said!r}"
    )
    # Names the missing key, so the log says WHAT was wrong rather than only
    # that something was.
    assert "profile_data_dir" in said
    # And it closes the session properly: the parent's monitor keys the card's
    # stop button off BROWSER_CLOSED, so a refused launch must still say it is
    # over rather than leaving the profile stuck "loading".
    assert "BROWSER_CLOSED" in said
