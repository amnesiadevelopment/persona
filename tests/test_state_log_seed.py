import src.ui.state as state
from src.core.logging import SESSION_MARKER


def _write_log(dirpath, name, lines):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_seed_reads_from_log_dir_regardless_of_cwd(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    _write_log(
        log_dir,
        "persona_20260707.log",
        ["2026-07-07 10:00:01 - INFO - persona.api - hello"],
    )
    # decoy relative logs/ under CWD must NOT be picked up
    cwd = tmp_path / "elsewhere"
    _write_log(
        cwd / "logs",
        "persona_20260707.log",
        ["2026-07-07 09:00:00 - INFO - persona.api - decoy"],
    )
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))
    monkeypatch.chdir(cwd)

    assert state._load_recent_log_lines() == ["10:00:01  > hello"]


def test_seed_picks_newest_file_and_respects_limit(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    _write_log(
        log_dir,
        "persona_20260706.log",
        ["2026-07-06 08:00:00 - INFO - persona.api - old"],
    )
    _write_log(
        log_dir,
        "persona_20260707.log",
        [f"2026-07-07 10:00:{i:02d} - INFO - persona.api - msg{i}" for i in range(5)],
    )
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))

    lines = state._load_recent_log_lines(limit=3)
    assert lines == [f"10:00:0{i}  > msg{i}" for i in (2, 3, 4)]


def test_seed_empty_when_no_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    log_dir.mkdir()
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))
    assert state._load_recent_log_lines() == []


def test_seed_returns_only_current_session(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    _write_log(
        log_dir,
        "persona_20260708.log",
        [
            f"2026-07-08 09:00:00 - INFO - persona - {SESSION_MARKER} 2.3.9 ==========",
            "2026-07-08 09:00:01 - INFO - persona.api - first session line",
            f"2026-07-08 10:00:00 - INFO - persona - {SESSION_MARKER} 2.3.10 ==========",
            "2026-07-08 10:00:01 - INFO - persona.api - second session line",
        ],
    )
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))

    assert state._load_recent_log_lines() == [
        f"10:00:00  > {SESSION_MARKER} 2.3.10 ==========",
        "10:00:01  > second session line",
    ]


def test_seed_limit_applies_within_current_session(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    _write_log(
        log_dir,
        "persona_20260708.log",
        [
            "2026-07-08 09:00:00 - INFO - persona.api - previous session",
            f"2026-07-08 10:00:00 - INFO - persona - {SESSION_MARKER} 2.3.10 ==========",
            *(
                f"2026-07-08 10:00:{i:02d} - INFO - persona.api - msg{i}"
                for i in range(1, 6)
            ),
        ],
    )
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))

    assert state._load_recent_log_lines(limit=3) == [
        f"10:00:0{i}  > msg{i}" for i in (3, 4, 5)
    ]


def test_appstate_seeds_from_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "persona_logs"
    _write_log(
        log_dir,
        "persona_20260707.log",
        ["2026-07-07 10:00:01 - INFO - persona.api - hello"],
    )
    monkeypatch.setattr(state, "LOG_DIR", str(log_dir))

    st = state.AppState()
    assert st.get_all_log_lines() == ["10:00:01  > hello"]
