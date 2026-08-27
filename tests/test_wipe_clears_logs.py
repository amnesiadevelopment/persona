"""The panic wipe clears the profile names the file log holds in cleartext.

The wipe destroys every profile, rmtree's each data dir, empties profiles.json
and purges the trash — and then, before this ticket, logged that it had done so
into a file that still named everything it destroyed. LOG_DIR is a SIBLING of
DATA_DIR, so no part of the destruction reached it.

That is a residue defect with a second half that makes it operator-visible: the
Activity Log seed (src/ui/state.py `_load_recent_log_lines`) globs
`LOG_DIR/persona_*.log` and reads those names straight back out. The operator
performs the product's strongest destructive gesture and the UI still lists the
identities by name.

So these tests drive the SHIPPED path end to end — real `setup_logging`, a real
`ProfileManager` whose real log sites fire, a real `wipe_all_profiles()` — and
assert the operator-facing half through the real reader, not by inspecting the
file. A test that called a clearing helper directly could not have caught the
defect, because the defect was that nothing called it.
"""
import glob
import logging
import os
import pathlib

import pytest

import src.ui.state as state
from src.core.logging import SESSION_MARKER, setup_logging
from src.services.profile.manager import ProfileManager
from src.services.trash.store import TrashStore

NAME = "acme-bank-viktor"
OLD_NAME = "long-gone-persona"
OLD_LOG = "persona_20260101.log"


def _reset_persona_logger():
    logger = logging.getLogger("persona")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


@pytest.fixture(autouse=True)
def _clean_persona_logger():
    # setup_logging() returns early if handlers already exist, and a leaked
    # FileHandler would keep writing into another test's tmp dir.
    _reset_persona_logger()
    yield
    _reset_persona_logger()


def _point_the_layout_at(root, monkeypatch):
    """The shipped wiring, pointed at `root`: LOG_DIR is a SIBLING of DATA_DIR
    here exactly as it is in production (both under PERSONA_HOME), so the test
    cannot accidentally pass because the wipe's rmtree swept the logs away as
    collateral."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    root.mkdir(parents=True, exist_ok=True)
    log_dir = root / "logs"
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(root / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(root / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(root / "trash.json"))
    # LOG_DIR is read from the config MODULE at call time by the wipe, and bound
    # at import by the seed reader — so both have to be pointed at the tmp dir.
    monkeypatch.setattr(cfg, "LOG_DIR", str(log_dir), raising=False)
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir), raising=False)

    logger = setup_logging(str(log_dir))
    pm = ProfileManager()
    pm.set_trash(TrashStore())
    return type(
        "Env",
        (),
        {"pm": pm, "log_dir": log_dir, "logger": logger, "tmp_path": root},
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The ordinary case: a home whose path holds no glob metacharacters."""
    return _point_the_layout_at(tmp_path, monkeypatch)


@pytest.fixture
def bracketed_env(tmp_path, monkeypatch):
    """The SAME wiring under a home whose directory name contains `[`.

    PERSONA_HOME is operator-overridable (config.py, "e.g. for a portable
    layout") and LOG_DIR is derived from it (`_under_home("logs", ...)`), so a
    bracketed home propagates straight into the directory both glob sites join.
    glob interprets metacharacters across the WHOLE pattern — directory portion
    included. `[` and `]` are legal on both POSIX and Windows (unlike `*` and
    `?`, which Windows forbids), and a user-named portable/USB directory is
    exactly where an overridden home comes from."""
    return _point_the_layout_at(tmp_path / "Persona[1] portable", monkeypatch)


def _plant_old_day_file(log_dir) -> pathlib.Path:
    """A day-file from an earlier session, holding a name of its own. Nothing
    rotates or prunes LOG_DIR, so a long-lived install really does accumulate
    these — and this one is NOT the file the live handler holds open, so it
    exercises the unlink branch rather than the truncate branch."""
    old = pathlib.Path(log_dir, OLD_LOG)
    old.write_text(
        f"2026-01-01 09:00:00 - INFO - persona.profile.manager - "
        f"Created profile: {OLD_NAME}\n",
        encoding="utf-8",
    )
    return old


def _make_real_profile(pm, name=NAME):
    """Create through the REAL manager so the REAL log site fires
    (manager.py `logger.info("Created profile: %s", name)`). The name reaches
    the log because the shipped code put it there, not because the test wrote
    it into a file."""
    pm.add_profile(name, "", "windows")
    data_dir = pm._data_path(name)
    os.makedirs(data_dir, exist_ok=True)
    pathlib.Path(data_dir, "Cookies").write_text("logged-in", encoding="utf-8")
    return data_dir


def _log_files(log_dir) -> list[str]:
    """The OBSERVER, escaped independently of the code under test.

    This helper globs the same directory the product does, so under the
    bracketed fixture it would hit the identical bug and report an empty log —
    a RED caused by the instrument rather than by the defect, which would then
    stay RED after a correct fix. Escaping here keeps the reading a fact about
    the product: the observer can always see the directory, so whatever it
    reports missing is genuinely missing."""
    return sorted(glob.glob(os.path.join(glob.escape(str(log_dir)), "persona_*.log")))


def _all_log_text(log_dir) -> str:
    return "\n".join(
        pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
        for p in _log_files(log_dir)
    )


# --- the artifact itself ---


def test_the_name_really_reaches_the_log_before_the_wipe(env):
    # Guards the premise the rest of the file rests on: if the shipped log site
    # stopped naming the profile, every assertion below would pass vacuously.
    _make_real_profile(env.pm)
    assert NAME in _all_log_text(env.log_dir)


def test_no_log_file_names_a_wiped_profile(env):
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()
    assert NAME not in _all_log_text(env.log_dir)


def test_an_older_day_file_does_not_survive_the_wipe(env):
    # A planted old day-file survived a real wipe with its name intact, so
    # historical files are in scope, not just today's.
    old = _plant_old_day_file(env.log_dir)
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()
    assert not old.exists()
    assert OLD_NAME not in _all_log_text(env.log_dir)


# --- the operator-facing half: the shipped reader ---


def test_the_activity_log_seed_names_no_wiped_profile(env):
    # AC3, asserted through the REAL reader rather than by inspecting the file:
    # this is the surface the operator actually looks at.
    _make_real_profile(env.pm)
    seeded_before = state._load_recent_log_lines()
    assert any(NAME in ln for ln in seeded_before), (
        "premise: the Activity Log shows the name before the wipe"
    )

    env.pm.wipe_all_profiles()

    seeded_after = state._load_recent_log_lines()
    assert [ln for ln in seeded_after if NAME in ln] == [], seeded_after


def test_the_seed_still_has_its_session_anchor_after_a_wipe(env):
    # AC6: truncating today's file also destroys the SESSION_MARKER the seed
    # scans backwards for. We RE-EMIT it, so the seed keeps a well-defined
    # anchor instead of falling through to raw[-limit:] by accident.
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()

    assert SESSION_MARKER in _all_log_text(env.log_dir)
    assert any(SESSION_MARKER in ln for ln in state._load_recent_log_lines())


def test_the_wipe_itself_is_still_recorded_after_the_clear(env):
    # The wipe must not erase all evidence that it happened — the line naming no
    # profile is written AFTER the clear, into the same file.
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()
    assert "Wiped all 1 profiles" in _all_log_text(env.log_dir)


# --- logging survives the clear (AC2) ---


def test_logging_still_works_after_the_wipe(env):
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()

    logging.getLogger("persona.api").info("after the wipe")

    # ... and it lands in the file the handler ALREADY held open: no lost
    # handler, and no second file minted beside the first.
    files = sorted(glob.glob(os.path.join(env.log_dir, "persona_*.log")))
    assert len(files) == 1, files
    assert "after the wipe" in pathlib.Path(files[0]).read_text(encoding="utf-8")


def test_a_profile_created_after_the_wipe_still_logs_normally(env):
    _make_real_profile(env.pm)
    env.pm.wipe_all_profiles()
    _make_real_profile(env.pm, "fresh-start")
    assert "fresh-start" in _all_log_text(env.log_dir)


# --- the wipe's existing contract is unchanged (AC4, AC5) ---


def test_the_wipe_still_returns_its_count_and_destroys_the_data(env):
    data_dir = _make_real_profile(env.pm)
    assert env.pm.wipe_all_profiles() == 1
    assert not os.path.exists(data_dir)
    assert env.pm.list_profiles() == []


def test_a_log_clearing_failure_does_not_break_the_wipe(env, monkeypatch):
    # AC5: a wipe that raised because a log file was locked (Windows) would be
    # worse than the residue. The wipe still completes and still returns.
    data_dir = _make_real_profile(env.pm)

    def _boom(*a, **kw):
        raise OSError("log file is locked by another process")

    monkeypatch.setattr(os, "truncate", _boom)
    monkeypatch.setattr(os, "remove", _boom)

    assert env.pm.wipe_all_profiles() == 1
    assert not os.path.exists(data_dir)
    assert env.pm.list_profiles() == []


# --- the log dir is a PATH, not a glob pattern (PS-227) ---


def test_the_name_really_reaches_the_log_under_a_bracketed_home(bracketed_env):
    # Guards the premise the bracketed tests below rest on, mirroring
    # test_the_name_really_reaches_the_log_before_the_wipe: if the shipped log
    # site stopped naming the profile under this fixture, every assertion below
    # would pass vacuously.
    _make_real_profile(bracketed_env.pm)
    assert NAME in _all_log_text(bracketed_env.log_dir)


def test_no_log_file_names_a_wiped_profile_under_a_bracketed_home(bracketed_env):
    """The wipe's log clear must treat LOG_DIR as a PATH, not as a pattern.

    glob interprets metacharacters across the whole pattern, so joining an
    unescaped log dir produces `/tmp/.../Persona[1] portable/logs/persona_*.log`,
    in which `[1]` is a character class matching the single character `1` — a
    directory that does not exist. glob.glob then returns [], the loop body
    never runs, and the wipe completes and reports success: no exception, no
    empty-result branch, no signal anywhere that the clear did nothing. The
    operator types the "this cannot be undone" confirmation and the log file
    still names every profile just destroyed — silently re-opening the exact
    defect this file exists to keep closed.

    Bound to BYTES ON DISK, so it falsifies cleanly: drop the glob.escape and
    this goes RED with the wiped profile's name still readable in LOG_DIR."""
    _make_real_profile(bracketed_env.pm)
    assert NAME in _all_log_text(bracketed_env.log_dir), (
        "premise: the name reaches the log before the wipe"
    )

    bracketed_env.pm.wipe_all_profiles()

    surviving = _all_log_text(bracketed_env.log_dir)
    assert NAME not in surviving, (
        "the log clear no-opped on a LOG_DIR containing a glob metacharacter; "
        f"the wiped profile's name survives in cleartext: {surviving!r}"
    )


def test_an_older_day_file_does_not_survive_a_wipe_under_a_bracketed_home(
    bracketed_env,
):
    # The unlink branch, not just the truncate branch: a planted old day-file is
    # not the file the live handler holds open, and it is reached only through
    # the same glob.
    old = _plant_old_day_file(bracketed_env.log_dir)
    _make_real_profile(bracketed_env.pm)
    bracketed_env.pm.wipe_all_profiles()
    assert not old.exists()
    assert OLD_NAME not in _all_log_text(bracketed_env.log_dir)


def test_the_activity_log_seed_reads_the_log_under_a_bracketed_home(bracketed_env):
    """SITE 2 (src/ui/state.py `_load_recent_log_lines`), bound to the READER.

    Same one-word bug, different consequence: this one is NOT a leak. Under a
    bracketed home the seed's glob matches nothing, `candidates` is empty and it
    returns [] — the operator gets a BLANK Activity Log panel instead of the
    session's history. Asserted through the real reader, since a blank panel is
    exactly what the operator sees."""
    _make_real_profile(bracketed_env.pm)

    seeded = state._load_recent_log_lines()

    assert seeded != [], (
        "the Activity Log seed no-opped on a LOG_DIR containing a glob "
        "metacharacter: the panel would be blank"
    )
    assert any(NAME in ln for ln in seeded), seeded


def test_the_activity_log_seed_names_no_wiped_profile_under_a_bracketed_home(
    bracketed_env,
):
    # Both sites at once, through the surface the operator actually looks at.
    _make_real_profile(bracketed_env.pm)
    assert any(NAME in ln for ln in state._load_recent_log_lines()), (
        "premise: the Activity Log shows the name before the wipe"
    )

    bracketed_env.pm.wipe_all_profiles()

    seeded_after = state._load_recent_log_lines()
    assert [ln for ln in seeded_after if NAME in ln] == [], seeded_after


def test_a_log_clearing_failure_does_not_break_the_wipe_under_a_bracketed_home(
    bracketed_env, monkeypatch
):
    # AC5: widening what the glob can SEE must not change what happens when
    # something fails. Now that the glob actually matches files under a
    # bracketed home, the per-file OSError handling is reached here for the
    # first time — and the wipe must still complete and still return its count.
    data_dir = _make_real_profile(bracketed_env.pm)

    def _boom(*a, **kw):
        raise OSError("log file is locked by another process")

    monkeypatch.setattr(os, "truncate", _boom)
    monkeypatch.setattr(os, "remove", _boom)

    assert bracketed_env.pm.wipe_all_profiles() == 1
    assert not os.path.exists(data_dir)
    assert bracketed_env.pm.list_profiles() == []


def test_the_persona_glob_half_still_only_matches_persona_day_files(bracketed_env):
    """CONTROL on the OTHER half of the pattern: escaping the DIRECTORY must not
    escape `persona_*.log` too. If the `*` were escaped along with the dir, the
    sweep would match only a literal file named `persona_*.log` and clear
    nothing — a different silent no-op wearing the same clothes.

    So: a real day-file IS cleared (the `*` still expands), and an unrelated
    neighbour in the same directory is NOT (the sweep stayed scoped)."""
    unrelated = pathlib.Path(bracketed_env.log_dir, "notes.txt")
    unrelated.write_text("operator scratch", encoding="utf-8")
    old = _plant_old_day_file(bracketed_env.log_dir)
    _make_real_profile(bracketed_env.pm)

    bracketed_env.pm.wipe_all_profiles()

    assert not old.exists(), "the `*` in persona_*.log must still expand"
    assert unrelated.exists(), "the sweep must stay scoped to persona_*.log"
    assert unrelated.read_text(encoding="utf-8") == "operator scratch"
