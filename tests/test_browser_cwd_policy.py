"""Both engines must give the browser child the SAME working directory, and
both must take it from one authority rather than from a path written at a seam.

This is ``test_browser_env_policy.py`` one axis over, and it is deliberately
built the same way. Before PS-123 the chromium launcher pinned
``cwd=os.path.expanduser("~")`` inline and the firefox seam set nothing at all,
so its child inherited whatever directory persona itself was sitting in —
``grep -rnE 'cwd=|chdir' src/services/browser/`` returned exactly one hit in
the whole package.

The seams differ in shape, so the assertions do too:

* Chromium passes ``cwd=`` to ``Popen``, so the assertion is on the kwarg
  actually handed to the child.
* Firefox on Linux FORKS and the child ``os.chdir``s itself, so the assertion
  is on the directory *the forked child actually stands in* — read back out of
  a real fork over a pipe. Checking that a helper was called would pass on an
  inert implementation; this cannot.

WHY THE TESTS PATCH THE AUTHORITY TO A SENTINEL rather than comparing both
seams against ``os.path.expanduser("~")``: the value is unchanged by this
ticket, so a test that asserts "both seams == ~" stays GREEN when a seam
re-inlines the path and stops consulting the authority at all — it would be
asserting the authority exists, not that it is used. Pointing the authority at
a directory that is nobody's home makes a re-inlined seam land somewhere else
and go red. That is the falsification this file is built around.

The parent-process tests are the other half of the same story: persona's own
working directory (and therefore every other concurrently-open profile's) must
come through a launch unmoved.
"""

import ast
import json
import os
import threading

import pytest

import src.services.browser.env_policy as env_policy
import src.services.browser.invisible_launch as il
import src.services.browser.process as process
from src.models.profile import Profile
from src.services.browser.env_policy import browser_child_cwd


class _Store:
    def resolve(self, name):
        return ""

    def get(self, name):
        return None


class _Bookmarks:
    def resolve_selection(self, pool, names):
        return []


def _sentinel_dir(tmp_path):
    """A real directory that is nobody's home, so a seam that re-inlines
    ``expanduser("~")`` instead of consulting the authority lands elsewhere and
    the test goes red. realpath'd because ``getcwd()`` reports a resolved path
    and the tmp root is a symlink on macOS (``/var`` → ``/private/var``)."""
    target = tmp_path / "sentinel-cwd"
    target.mkdir()
    return os.path.realpath(str(target))


def _spawn_chromium_cwd(monkeypatch, tmp_path, profile, linux=False):
    """Returns the ``cwd`` kwarg the chromium launcher handed to Popen."""
    captured = {}

    class _FakePopen:
        def __init__(self, args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            self.pid = os.getpid()

    monkeypatch.setattr(process, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(process, "ProxyStore", _Store)
    monkeypatch.setattr(process, "BookmarkStore", _Bookmarks)
    monkeypatch.setattr(process, "write_window_entry", lambda name: None)
    monkeypatch.setattr(process.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(process._platform, "IS_LINUX", bool(linux))
    process.spawn_browser(profile)
    return captured["cwd"]


# --------------------------------------------------------------------------
# The authority itself
# --------------------------------------------------------------------------


def test_the_authority_returns_the_home_directory_unchanged():
    # PS-123 CENTRALIZES this value, it does not change it. The pin was
    # os.path.expanduser("~") before and is os.path.expanduser("~") after;
    # changing it is a product decision held elsewhere (see the docstring on
    # browser_child_cwd). This test exists so that a change of VALUE cannot
    # ride along inside a change of STRUCTURE unnoticed.
    assert browser_child_cwd() == os.path.expanduser("~")


def test_the_authority_recomputes_per_call_rather_than_freezing_the_value(
    monkeypatch, tmp_path
):
    # The claim in browser_child_cwd's docstring: the home directory is read on
    # every call, not captured once into a module constant at import time.
    #
    # Asserted THROUGH expanduser itself rather than by setting an environment
    # variable. WHICH variable drives expanduser is platform-specific and is
    # NOT what this pins: posixpath.expanduser reads HOME, while
    # ntpath.expanduser prefers USERPROFILE (then HOMEDRIVE+HOMEPATH) and
    # consults HOME only if none of those are set. An earlier version of this
    # test did monkeypatch.setenv("HOME", ...) and asserted the result equalled
    # that path; it was green on Linux/macOS and RED on windows-latest, where
    # USERPROFILE is always set and the setenv was simply ignored. The property
    # worth pinning survives without naming any variable.
    first = tmp_path / "first-home"
    second = tmp_path / "second-home"
    first.mkdir()
    second.mkdir()

    monkeypatch.setattr(os.path, "expanduser", lambda p: str(first))
    assert browser_child_cwd() == str(first)

    # Called again after the answer changes: a value memoized at import (or
    # cached on the first call) would still report `first` here.
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(second))
    assert browser_child_cwd() == str(second), (
        "browser_child_cwd froze its value instead of recomputing it per call"
    )


# --------------------------------------------------------------------------
# Chromium — the cwd actually handed to the child
# --------------------------------------------------------------------------


def test_chromium_child_cwd_comes_from_the_authority(monkeypatch, tmp_path):
    # AC2. The authority is pointed at a sentinel, so this fails if the seam
    # goes back to writing the path inline — which is the whole point, since
    # the inline value and the authority's value are otherwise identical.
    sentinel = _sentinel_dir(tmp_path)
    monkeypatch.setattr(process, "browser_child_cwd", lambda: sentinel)
    cwd = _spawn_chromium_cwd(
        monkeypatch, tmp_path, Profile(name="cwd-chromium"), linux=True
    )
    assert cwd == sentinel, "the chromium seam did not consult the authority"


def test_chromium_pins_the_cwd_on_non_linux_too(monkeypatch, tmp_path):
    # Chromium passes cwd= on every platform — unlike the firefox half, this
    # guarantee is not Linux-only. Pinning it keeps a future refactor from
    # quietly folding the pin into an IS_LINUX branch.
    sentinel = _sentinel_dir(tmp_path)
    monkeypatch.setattr(process, "browser_child_cwd", lambda: sentinel)
    cwd = _spawn_chromium_cwd(
        monkeypatch, tmp_path, Profile(name="cwd-chromium-win"), linux=False
    )
    assert cwd == sentinel


def test_chromium_launch_leaves_the_parent_working_directory_alone(
    monkeypatch, tmp_path
):
    # Popen(cwd=) sets the directory in the CHILD only. If this ever fails,
    # persona itself just moved — and so did every other open profile.
    sentinel = _sentinel_dir(tmp_path)
    monkeypatch.setattr(process, "browser_child_cwd", lambda: sentinel)
    before = os.getcwd()
    _spawn_chromium_cwd(monkeypatch, tmp_path, Profile(name="cwd-parent"), linux=True)
    assert os.getcwd() == before


# --------------------------------------------------------------------------
# Firefox — a REAL fork, asserting the directory the child stands in
# --------------------------------------------------------------------------


# Same guard, and same reasoning, as tests/test_browser_env_policy.py: the
# harness below IS a fork, and on Windows `os.fork` does not exist, so without
# this these tests would not skip — they would CRASH with AttributeError. An
# undeclared absence is noise in the column that is supposed to carry signal.
#
# THIS SKIPS THE HARNESS, NOT THE COVERAGE. The behaviour pinned here is
# Linux-only BY DESIGN: `invisible_launch._child` guards the chdir on
# `not in_thread and _platform.IS_LINUX`, because on Windows/macOS that same
# _child runs as a THREAD whose working directory is persona's own. That
# recorded, deliberate absence is asserted on Linux by
# test_thread_path_child_leaves_its_process_working_directory_alone, which
# holds IS_LINUX true to isolate the in_thread half of the guard.
requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")


def _child_cwd_after_fork(tmp_path, sentinel=None, in_thread=False, is_linux=True):
    """Fork for real, run ``_child``, and report the directory the child is
    actually standing in back over a pipe.

    The engine launch itself is replaced (in the child, after the fork) with a
    reporter, so what comes back is the working directory ``_child`` had
    established by the time it would have handed control to the browser.
    Nothing is asserted about calls — only about where the child really is.

    ``sentinel`` repoints the authority INSIDE the forked child, so a seam that
    writes the path inline instead of consulting it is caught. Patched on BOTH
    modules on purpose: ``invisible_launch`` binds the helper into its own
    namespace at import (``from .env_policy import browser_child_cwd``), so
    patching only ``env_policy`` misses the seam's own binding — which is
    exactly how this harness silently stopped repointing anything and let the
    child run to the real home directory. Patching both is the analogue of what
    the chromium tests do with ``process.browser_child_cwd``.
    """
    read_fd, write_fd = os.pipe()
    report_r, report_w = os.pipe()
    started_in = os.getcwd()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            os.close(report_r)
            il._platform.IS_LINUX = is_linux
            if sentinel is not None:
                env_policy.browser_child_cwd = lambda: sentinel
                il.browser_child_cwd = lambda: sentinel

            def _report(cfg, profile_dir, emit, _finish, stop_event, _in_thread):
                payload = json.dumps(
                    {"cwd": os.getcwd(), "started_in": started_in}
                )
                with os.fdopen(report_w, "w") as fh:
                    fh.write(payload)
                _finish()

            il._launch_and_watch = _report
            il._child(
                # Both keys, the way process._spawn_invisible builds them:
                # profile_dir is the engine's inner .invisible-profile and
                # profile_data_dir is the profile data dir one level up. This
                # file is about the WORKING directory, but _child refuses a cfg
                # with no profile_data_dir (it could not pin scratch, and
                # launching unpinned silently is the residue PS-129 closes), so
                # a fixture omitting it would exercise that refusal instead of
                # the cwd behaviour under test here.
                {
                    "profile_dir": os.path.join(str(tmp_path), ".invisible-profile"),
                    "profile_data_dir": str(tmp_path),
                },
                write_fd,
                stop_event=threading.Event() if in_thread else None,
            )
        except BaseException as e:  # noqa: BLE001 - reported, see below
            # Report the failure rather than dying mute. A bare `pass` here
            # meant a child that died for an unrelated reason sent nothing, and
            # the parent's assert below then said only "reported no working
            # directory" — true, uninformative, and a guess to debug. The
            # exception text costs one write and turns that into a read.
            try:
                with os.fdopen(report_w, "w") as fh:
                    fh.write(
                        json.dumps(
                            {"error": f"{type(e).__name__}: {e}"}
                        )
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
    assert payload, "the forked child reported no working directory"
    report = json.loads(payload)
    # The child reports its own exception rather than dying mute (see above),
    # so a failure inside the fork surfaces as itself instead of as a missing
    # key three lines later.
    assert "error" not in report, (
        f"the forked child died before reporting: {report['error']}"
    )
    return report


@requires_fork
def test_forked_firefox_child_moves_to_the_authoritys_directory(tmp_path):
    # AC3, and THE defect this ticket closes. Before PS-123 this seam set
    # nothing at all — no cwd=, no chdir — so the child simply inherited
    # persona's own directory. Asserted on the child's real getcwd() after a
    # real fork, not on whether a helper was called.
    sentinel = _sentinel_dir(tmp_path)
    report = _child_cwd_after_fork(tmp_path, sentinel=sentinel)
    assert report["cwd"] == sentinel, (
        "the forked firefox child did not take the shared working directory — "
        f"it is standing in {report['cwd']!r}"
    )
    # And it genuinely MOVED, rather than the test having started there.
    assert report["started_in"] != sentinel


@requires_fork
def test_forked_firefox_child_does_not_disturb_the_parent(tmp_path):
    # A fork has separate memory, so the child moving itself must be invisible
    # here. persona's own cwd, and every concurrently-open profile's, is the
    # same process-global state.
    before = os.getcwd()
    _child_cwd_after_fork(tmp_path, sentinel=_sentinel_dir(tmp_path))
    assert os.getcwd() == before


@requires_fork
def test_an_unreachable_directory_is_reported_on_the_pipe_not_died_on(tmp_path):
    # Pinning a directory INTRODUCES a failure mode this seam did not have:
    # before PS-123 it set nothing and inherited a directory that by
    # construction worked, whereas os.chdir raises if the target is gone (an
    # unmounted home — the fault case src/main.py:_ensure_valid_cwd exists
    # for). Applied before the pipe was open, that exception killed the child
    # with NO BROWSER_STARTED and NO BROWSER_CLOSED: the parent's monitor saw a
    # bare EOF and a launch that failed without saying why.
    #
    # Deliberately NOT a fallback chain — falling back means choosing a second
    # directory, which is the product decision this ticket does not take. The
    # requirement is that it is AUDIBLE.
    missing = os.path.join(str(tmp_path), "definitely-not-mounted")
    assert not os.path.exists(missing)

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            il._platform.IS_LINUX = True
            # Both bindings, for the reason given on _child_cwd_after_fork.
            env_policy.browser_child_cwd = lambda: missing
            il.browser_child_cwd = lambda: missing
            il._launch_and_watch = lambda *a, **k: None
            # profile_data_dir is set so the scratch pin SUCCEEDS and this test
            # reaches the cwd failure it is about. The scratch guard runs first
            # (deliberately — a failure to pin scratch is the perimeter
            # guarantee failing), so a cfg without it would report the missing
            # key here instead of the unreachable directory.
            il._child(
                {
                    "profile_dir": os.path.join(str(tmp_path), ".invisible-profile"),
                    "profile_data_dir": str(tmp_path),
                },
                write_fd,
            )
        except BaseException:
            pass
        os._exit(0)

    os.close(write_fd)
    with os.fdopen(read_fd) as fh:
        said = fh.read()
    os.waitpid(pid, 0)

    assert "LAUNCH_FAILED" in said, (
        "the child died without reporting the unreachable working directory — "
        f"the parent saw only {said!r}"
    )
    # Names the directory, so the log says WHICH path was unreachable.
    assert "definitely-not-mounted" in said
    # And it closes the session properly: the parent's monitor keys the card's
    # stop button off BROWSER_CLOSED, so a launch that fails must still say it
    # is over rather than leaving the profile stuck "loading".
    assert "BROWSER_CLOSED" in said


@requires_fork
def test_thread_path_child_leaves_its_process_working_directory_alone(tmp_path):
    # AC4 — THE HAZARD, asserted rather than assumed, and the direct mirror of
    # test_thread_path_child_leaves_its_process_environment_alone.
    #
    # On Windows/macOS this same _child runs as a THREAD of the manager
    # process, where the working directory IS persona's own — process-global
    # state exactly like os.environ. An os.chdir there would move the app and
    # every other open profile, which is strictly worse than the divergence
    # this ticket fixes. IS_LINUX is left TRUE here on purpose: it isolates the
    # in_thread half of the guard, so a chdir gated on platform alone fails
    # this test.
    sentinel = _sentinel_dir(tmp_path)
    report = _child_cwd_after_fork(
        tmp_path, sentinel=sentinel, in_thread=True, is_linux=True
    )
    assert report["cwd"] == report["started_in"], (
        "the thread path moved its process's working directory — that moves "
        "persona's own cwd and every concurrently-open profile's"
    )
    assert report["cwd"] != sentinel


# --------------------------------------------------------------------------
# The shared authority itself
# --------------------------------------------------------------------------


@requires_fork
def test_both_engines_agree_on_the_browser_child_directory(monkeypatch, tmp_path):
    # AC1, AND THE FALSIFICATION THE TICKET ASKS FOR: re-inline the path at
    # EITHER seam with the rest of the diff in place and this goes red, because
    # the authority is pointed at a directory that is nobody's home. A version
    # of this test that compared both seams against expanduser("~") would stay
    # green under that mutation — it would be asserting that the authority
    # exists, not that both seams use it.
    #
    # Both halves are measured the way that seam really works: the kwarg handed
    # to Popen for chromium, and a real forked child's actual getcwd() for
    # firefox.
    sentinel = _sentinel_dir(tmp_path)
    monkeypatch.setattr(process, "browser_child_cwd", lambda: sentinel)

    chromium_cwd = _spawn_chromium_cwd(
        monkeypatch, tmp_path, Profile(name="cwd-agree"), linux=True
    )
    firefox_cwd = _child_cwd_after_fork(tmp_path, sentinel=sentinel)["cwd"]

    assert chromium_cwd == firefox_cwd == sentinel, (
        "the two engine seams do not agree on the browser child's working "
        f"directory: chromium={chromium_cwd!r} firefox={firefox_cwd!r}"
    )


def test_both_engines_route_through_one_authority():
    # The structural half of AC1, mirroring test_both_engines_share_one_scrub_list.
    # Two divergent literal paths in two launchers is the predictable
    # regression here: a directory changed at one seam and forgotten at the
    # other is a fix that looks done. Both launchers must reach env_policy.
    assert process.browser_child_cwd.__module__ == "src.services.browser.env_policy"
    assert (
        il.chdir_current_process.__module__ == "src.services.browser.env_policy"
    )
    # The value lives in ONE place: neither launcher may CALL expanduser to
    # re-acquire its own copy. This is the grep from the ticket, asserted — it
    # is what stops a seam quietly getting a second home-directory pin.
    #
    # Matched on the AST rather than on the text. A line-based version of this
    # check has two failure modes, and the dangerous one is the false NEGATIVE:
    # it looked for the substring 'expanduser("~")', so a re-inlined
    # expanduser('~') in SINGLE quotes would sail straight past the one
    # assertion standing between the codebase and a re-acquired second copy.
    # (It also had to strip whole-line comments to avoid tripping on the
    # explanatory prose at both seams, and would still have tripped on a
    # trailing inline comment or a docstring that named the expression.) The
    # AST sees calls and nothing but calls, so prose can say "expanduser"
    # freely — as the comments at both seams now do — and only real code fails.
    seam_sources = (
        os.path.join(os.path.dirname(process.__file__), "process.py"),
        os.path.join(os.path.dirname(il.__file__), "invisible_launch.py"),
    )
    for path in seam_sources:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "attr", None) == "expanduser"
                or getattr(node.func, "id", None) == "expanduser"
            )
        ]
        assert not calls, (
            f"{os.path.basename(path)} calls expanduser at line(s) {calls} — it "
            "pins a home directory inline again; the browser child's cwd "
            "belongs to env_policy.browser_child_cwd"
        )
