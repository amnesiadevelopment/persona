"""The single-instance lock must live inside the CONFIGURED runtime home.

Every other runtime file (profiles, proxies, certs, bookmarks, settings, the
MCP token) resolves through ``config._under_home``, which honours
``PERSONA_HOME``. The lockfile hardcoded ``~/.persona/persona.lock``, so a
relocated home had two consequences, and both are pinned below:

* HOST RESIDUE — ``acquire()`` makedirs the lock's parent, so a portable
  install CREATED ``~/.persona`` on a machine that had none and wrote the
  operator's live pid into a directory it then never used.
* A FALSE STARTUP REFUSAL — two deliberately isolated ``PERSONA_HOME``
  installs resolved to the SAME lockfile, so the second was refused. The
  guard exists because two windows sharing ONE home corrupt each other's
  state; two distinct homes share none of that.

The refusal that the guard is actually FOR (two instances, one home) is pinned
here too. Note it could not have failed before the fix — every home resolved to
the one hardcoded path, so the bug satisfied it trivially. It is a real
regression guard only now that the paths diverge: it goes red if this change
over-corrected and made same-home installs stop contending.

``tests/test_single_instance.py`` is deliberately untouched: its fixture
monkeypatches ``PERSONA_LOCK_FILE``, so no existing test pinned the hardcoded
default and this file is purely additive.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from src.core import config
from src.core import single_instance as si

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def relocated_home(tmp_path, monkeypatch):
    """A configured home that is NOT ~/.persona, with no explicit lock override.

    ``config.PERSONA_HOME`` is resolved once at config IMPORT time (deliberate —
    see its module-level assignment, ``PERSONA_HOME = _ensure_home(_home())``),
    so relocation in-process is expressed by patching that attribute, exactly
    as tests/test_settings.py does for settings.json.
    """
    home = tmp_path / "portable-home"
    home.mkdir()
    monkeypatch.delenv("PERSONA_LOCK_FILE", raising=False)
    monkeypatch.setattr(config, "PERSONA_HOME", str(home))
    return home


def test_lock_path_is_inside_a_relocated_home(relocated_home):
    """AC1. Fails on the pre-fix tree: the lock resolved to ~/.persona."""
    path = si._lock_path()
    assert path == os.path.join(str(relocated_home), "persona.lock"), (
        "the lock must be derived from PERSONA_HOME like every other runtime "
        f"file; got {path!r}"
    )
    assert os.path.commonpath([path, str(relocated_home)]) == str(relocated_home)


def test_acquire_under_relocated_home_creates_no_dot_persona_on_the_host(tmp_path):
    """AC2. Assert on the FILESYSTEM, not on the returned path string.

    Run in a child process with a fake ``HOME``: the pre-fix module resolved
    ``~/.persona`` at IMPORT time, so this is the only way to observe the
    residue without touching the real host home.
    """
    fake_home = tmp_path / "fake-host-home"
    fake_home.mkdir()
    persona_home = tmp_path / "portable-home"

    code = textwrap.dedent(
        """
        from src.core import single_instance as si
        h = si.acquire()
        assert h is not None, "startup must not be blocked"
        print("LOCK", si._lock_path())
        """
    )
    env = dict(
        os.environ,
        HOME=str(fake_home),
        USERPROFILE=str(fake_home),
        PERSONA_HOME=str(persona_home),
        PYTHONPATH=REPO_ROOT,
    )
    env.pop("PERSONA_LOCK_FILE", None)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr

    residue = fake_home / ".persona"
    assert not residue.exists(), (
        "a relocated install must not create ~/.persona on the host; found "
        f"{sorted(p.name for p in residue.iterdir())} in {residue}"
    )
    assert (persona_home / "persona.lock").exists(), (
        "the lock must have been taken inside the configured home instead"
    )


def test_two_distinct_homes_both_acquire(tmp_path, monkeypatch):
    """AC3. The false refusal is gone: isolated installs share no state."""
    monkeypatch.delenv("PERSONA_LOCK_FILE", raising=False)
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    home_a.mkdir()
    home_b.mkdir()

    monkeypatch.setattr(config, "PERSONA_HOME", str(home_a))
    handle_a = si.acquire()
    assert handle_a is not None

    monkeypatch.setattr(config, "PERSONA_HOME", str(home_b))
    handle_b = si.acquire()
    try:
        assert handle_b is not None, (
            "two installs with DIFFERENT PERSONA_HOME values share no state, so "
            "the second must not be refused"
        )
        assert si._lock_path() != os.path.join(str(home_a), "persona.lock")
    finally:
        handle_a.release()
        if handle_b is not None:
            handle_b.release()


def test_two_instances_sharing_one_home_still_refuse_the_second(
    tmp_path, monkeypatch
):
    """AC4. The guard's ACTUAL purpose — must not regress.

    Vacuous before the fix (every home resolved to one path); meaningful now:
    it goes red if the lock stopped contending for same-home installs.
    """
    monkeypatch.delenv("PERSONA_LOCK_FILE", raising=False)
    home = tmp_path / "shared-home"
    home.mkdir()
    monkeypatch.setattr(config, "PERSONA_HOME", str(home))

    first = si.acquire()
    assert first is not None
    try:
        assert si.acquire() is None, (
            "two windows sharing ONE PERSONA_HOME race each other into corrupt "
            "state, so the second must still be refused"
        )
    finally:
        first.release()


def test_explicit_lock_file_override_still_wins(tmp_path, monkeypatch):
    """AC5. PERSONA_LOCK_FILE beats PERSONA_HOME, and is read at CALL time.

    The override is set AFTER this module was imported, which is what a
    module-level constant would have frozen out.
    """
    home = tmp_path / "home"
    home.mkdir()
    explicit = tmp_path / "elsewhere" / "custom.lock"
    monkeypatch.setattr(config, "PERSONA_HOME", str(home))
    monkeypatch.setenv("PERSONA_LOCK_FILE", str(explicit))

    assert si._lock_path() == str(explicit)

    handle = si.acquire()
    try:
        assert handle is not None
        assert explicit.exists()
        assert not (home / "persona.lock").exists()
    finally:
        handle.release()


def test_fail_open_when_the_lock_directory_cannot_be_made(tmp_path, monkeypatch):
    """AC6. Refusing to start at all is worse than a rare second window."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file", encoding="utf-8")
    # makedirs() on a path whose parent is a regular file raises OSError.
    monkeypatch.setenv("PERSONA_LOCK_FILE", str(blocker / "sub" / "persona.lock"))

    handle = si.acquire()
    assert handle is not None, (
        "an unmakeable lock directory must fail OPEN — returning a handle — "
        "rather than blocking startup"
    )
    handle.release()
