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


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The shipped wiring, pointed at a tmp dir: LOG_DIR is a SIBLING of
    DATA_DIR here exactly as it is in production (both under PERSONA_HOME), so
    the test cannot accidentally pass because the wipe's rmtree swept the logs
    away as collateral."""
    import src.core.config as cfg
    import src.services.profile.manager as mod

    log_dir = tmp_path / "logs"
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(tmp_path / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(tmp_path / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(tmp_path / "trash.json"))
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
        {"pm": pm, "log_dir": log_dir, "logger": logger, "tmp_path": tmp_path},
    )


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
    pathlib.Path(data_dir, "Cookies").write_text("logged-in")
    return data_dir


def _all_log_text(log_dir) -> str:
    return "\n".join(
        pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
        for p in glob.glob(os.path.join(log_dir, "persona_*.log"))
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
