"""HOW the local API bearer token reaches the disk — not merely what it says.

This token is the sole credential for `/mcp` and its `/api/v1` REST twin, so the
persist step has to hold three properties at once, and each one below is
asserted by OBSERVING THE FILESYSTEM DURING A REAL `get_or_create_token()` call
rather than by asserting that some helper was called. A test of the second kind
passes against any implementation that keeps calling the helper, including one
that has quietly stopped protecting anything.

The properties:

1. The token never exists at its FINAL path under a mode wider than 0600. The
   old shape — `open(path,"w")` then `os.chmod(path, 0o600)` — left the bytes
   sitting at the umask default for the interval between the two lines.
2. A truncated file is treated as ABSENT and re-minted, never used verbatim.
3. A mint interrupted partway leaves either the old token or the whole new one,
   never a fragment.

THE UMASK IS PINNED WIDE (0o022), deliberately. The window this file exists to
close is a width set by an ambient process-global, so on a host whose umask
already happens to be 0o077 the vulnerable condition never exists and the
falsification would pass against the BROKEN code — a vacuous green. Pinning the
umask more permissive is what makes the observation meaningful.
"""
import os
import sys

import pytest

from src.api import mcp_token


@pytest.fixture(autouse=True)
def tmp_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_MCP_TOKEN_FILE", str(tmp_path / "mcp_token"))
    return tmp_path / "mcp_token"


@pytest.fixture(autouse=True)
def wide_umask():
    """Pin the process umask wide so a late chmod is actually observable."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path) -> int:
    return os.stat(path).st_mode & 0o777


class _FinalPathWatcher:
    """Samples the FINAL token path's on-disk state at each mode-changing or
    path-publishing syscall, whichever module issues it.

    `os.chmod` / `os.replace` are patched on the `os` module itself rather than
    on any one caller, so this watches the real syscalls and stays honest
    against either implementation shape — the temp+replace one and the
    write-then-chmod one it replaced.
    """

    def __init__(self, final_path, monkeypatch):
        self.final_path = str(final_path)
        self.samples = []
        real_chmod, real_replace = os.chmod, os.replace

        def sample(when):
            try:
                self.samples.append({"when": when, "mode": _mode(self.final_path)})
            except OSError:
                self.samples.append({"when": when, "mode": None})  # not there yet

        def chmod_spy(path, mode, *a, **kw):
            sample("at chmod")
            out = real_chmod(path, mode, *a, **kw)
            sample("after chmod")
            return out

        def replace_spy(src, dst, *a, **kw):
            sample("at replace")
            out = real_replace(src, dst, *a, **kw)
            sample("after replace")
            return out

        monkeypatch.setattr(os, "chmod", chmod_spy)
        monkeypatch.setattr(os, "replace", replace_spy)

    @property
    def observed_modes(self):
        """Every mode the final path was actually seen holding."""
        return [s["mode"] for s in self.samples if s["mode"] is not None]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_token_never_exists_at_final_path_world_readable(tmp_token, monkeypatch):
    """AC1. Restoring write-then-chmod turns this red — see the falsification
    test below, which drives that exact ordering through the same watcher."""
    watcher = _FinalPathWatcher(tmp_token, monkeypatch)

    token = mcp_token.get_or_create_token()

    assert token
    # The file was seen on disk at some point, so the watcher is not vacuously
    # green because it never observed anything.
    assert watcher.observed_modes, f"watcher never saw the file: {watcher.samples}"
    assert all(m == 0o600 for m in watcher.observed_modes), (
        f"token was readable at its final path under a wider mode: {watcher.samples}"
    )
    assert _mode(tmp_token) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_mode_is_applied_before_the_bytes_reach_the_final_path(
    tmp_token, monkeypatch
):
    """AC1, stated as the ordering rule itself: at the instant the mode is set,
    the token must NOT yet be at its final path."""
    watcher = _FinalPathWatcher(tmp_token, monkeypatch)

    mcp_token.get_or_create_token()

    at_chmod = [s for s in watcher.samples if s["when"] == "at chmod"]
    assert at_chmod, "no chmod was issued during a mint"
    assert all(s["mode"] is None for s in at_chmod), (
        f"the mode was narrowed AFTER the bytes were already at the final "
        f"path: {watcher.samples}"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_falsification_write_then_chmod_is_caught(tmp_path, monkeypatch):
    """AC2, non-waivable. The watcher above is only worth anything if it FAILS
    on the ordering this ticket removed, so drive that ordering — the shipped
    body of `get_or_create_token` before this change, verbatim — through the
    same watcher and assert it is caught.

    This is what proves AC1 is a real observation rather than a test that would
    pass against anything.
    """
    import pathlib
    import secrets

    final = tmp_path / "broken_token"
    watcher = _FinalPathWatcher(final, monkeypatch)

    def broken_get_or_create_token() -> str:
        path = str(final)
        token = secrets.token_urlsafe(24)
        pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
        os.chmod(path, 0o600)
        return token

    broken_get_or_create_token()

    # The defect, observed: the file existed at the final path, world-readable,
    # at the moment the chmod was reached.
    at_chmod = [s for s in watcher.samples if s["when"] == "at chmod"]
    assert at_chmod and at_chmod[0]["mode"] == 0o644, (
        f"expected the old ordering to expose 0o644 at the final path, "
        f"saw {watcher.samples}"
    )
    # ...and the assertion AC1 makes would therefore have failed here.
    assert not all(m == 0o600 for m in watcher.observed_modes)


def test_short_token_file_is_treated_as_absent_and_reminted(tmp_token):
    """AC3."""
    tmp_token.write_text("abc", encoding="utf-8")

    assert mcp_token.read_token() == ""

    t = mcp_token.get_or_create_token()
    assert t != "abc"
    assert len(t) >= 20  # the assertion the existing suite makes
    assert mcp_token.read_token() == t
    assert tmp_token.read_text(encoding="utf-8") == t


def test_the_length_guard_is_derived_from_the_mint(tmp_token):
    """AC3, guarding against the guard and the mint drifting apart: a real
    freshly minted token must satisfy the guard exactly, not by luck."""
    t = mcp_token.get_or_create_token()
    assert len(t) == mcp_token._MIN_TOKEN_CHARS
    assert mcp_token._MIN_TOKEN_CHARS >= 20

    # one character short of a real mint still reads as absent
    tmp_token.write_text(t[:-1], encoding="utf-8")
    assert mcp_token.read_token() == ""


@pytest.fixture
def interrupt_publish():
    """Make the publish step (`os.replace`) fail, then restore it — WITHOUT
    `monkeypatch.undo()`.

    `undo()` reverts every patch made through that function's monkeypatch,
    including the autouse fixture's `PERSONA_MCP_TOKEN_FILE`. A test that called
    it mid-way would silently finish by resolving `_path()` against the
    developer's REAL ~/.persona — reading (or asserting about) their live token
    instead of the tmp one. Restoring only what this fixture patched keeps the
    isolation the autouse fixture established.
    """
    real_replace = os.replace

    def boom(src, dst, *a, **kw):
        raise OSError("simulated interruption mid-mint")

    def start():
        os.replace = boom

    def stop():
        os.replace = real_replace

    try:
        yield start, stop
    finally:
        os.replace = real_replace


def test_interrupted_mint_leaves_the_pre_existing_file_intact(
    tmp_token, interrupt_publish
):
    """AC4, with a file already AT the final path: the bytes that were there
    before the mint are still there afterwards, unmodified.

    The pre-existing file is a SHORT one, and that is the only way to reach
    this case rather than a convenience. `get_or_create_token()` returns early
    on a valid token, so an interrupted re-mint over a *whole* token is
    unreachable by construction — the re-mint is precisely what the AC3 length
    guard triggers. So this drives the one reachable overwrite path: a
    truncated file reads as absent, the mint runs, the publish fails, and the
    question AC4 asks is whether the original bytes survived.

    Deliberately NOT `unlink()`-ing first: that would delete the very thing
    whose survival is under test, leaving the empty-directory case that
    `test_interrupted_mint_publishes_nothing_readable` below already covers.
    """
    start, stop = interrupt_publish
    stale = "abc"
    tmp_token.write_text(stale, encoding="utf-8")

    start()
    with pytest.raises(OSError):
        mcp_token.get_or_create_token()
    stop()

    # The pre-existing bytes survived the interrupted mint untouched — no
    # fragment of the new token was published over them...
    assert tmp_token.exists()
    assert tmp_token.read_text(encoding="utf-8") == stale
    # ...and no temp debris was left beside them.
    assert [p.name for p in tmp_token.parent.iterdir()] == ["mcp_token"]

    # A reader still sees exactly what was there before: this file is short, so
    # the guard keeps reporting it absent rather than serving half a mint.
    assert mcp_token.read_token() == ""

    # And the interruption cost nothing permanent — the next mint succeeds and
    # publishes a whole token over the stale bytes.
    recovered = mcp_token.get_or_create_token()
    assert len(recovered) == mcp_token._MIN_TOKEN_CHARS
    assert mcp_token.read_token() == recovered


def test_interrupted_mint_publishes_nothing_readable(tmp_token, interrupt_publish):
    """AC4, from empty: a failed mint must not leave a fragment that the next
    read would accept as the credential."""
    start, stop = interrupt_publish

    start()
    with pytest.raises(OSError):
        mcp_token.get_or_create_token()
    stop()

    assert not tmp_token.exists()
    assert list(tmp_token.parent.iterdir()) == []
    assert mcp_token.read_token() == ""


def test_successful_mint_leaves_no_temp_files(tmp_token):
    """The temp is a sibling in the same directory, so prove it is cleaned up."""
    mcp_token.get_or_create_token()
    assert [p.name for p in tmp_token.parent.iterdir()] == ["mcp_token"]


def test_token_is_stable_and_readable_after_the_rewrite(tmp_token):
    """AC6. The existing suite asserts this too; restated here so this file
    fails loudly if the atomic persist ever breaks round-tripping."""
    t1 = mcp_token.get_or_create_token()
    t2 = mcp_token.get_or_create_token()
    assert t1 == t2
    assert mcp_token.read_token() == t1
    assert tmp_token.read_text(encoding="utf-8") == t1
