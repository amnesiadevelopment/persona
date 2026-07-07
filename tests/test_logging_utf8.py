"""The persisted log must be UTF-8 regardless of the interpreter's locale.

The frozen Windows build runs with the ANSI code page as its locale (cp1251 on
a Russian install, utf8_mode off), so any log sink that falls back to the
locale encoding writes bytes the UTF-8 readers (Activity Log seed, editors)
turn into mojibake ("—" -> "вЂ”") or U+FFFD ("аа4" -> "??4"). These tests run
the logger in a subprocess with UTF-8 mode forced OFF to reproduce that
environment even on UTF-8 hosts.
"""
import glob
import os
import pathlib
import subprocess
import sys

import src.ui.state as state
from src.core.logging import setup_logging

SAMPLE = "Firefox engine not found — downloading… профиль аа4"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[2])
from src.core.logging import setup_logging
logger = setup_logging(log_dir=sys.argv[1])
logger.info(sys.argv[3])
"""


def _log_in_locale_encoded_interpreter(log_dir: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", CHILD_SCRIPT,
         str(log_dir), ROOT, SAMPLE],
        check=True,
        env=env,
    )


def _log_file(log_dir) -> str:
    files = glob.glob(os.path.join(str(log_dir), "persona_*.log"))
    assert len(files) == 1
    return files[0]


def test_log_file_is_utf8_under_locale_encoded_interpreter(tmp_path):
    _log_in_locale_encoded_interpreter(str(tmp_path))
    raw = pathlib.Path(_log_file(tmp_path)).read_bytes()
    text = raw.decode("utf-8")  # raises if the record was locale-encoded
    assert SAMPLE in text
    assert "вЂ" not in text  # cp1251 view of UTF-8 em-dash bytes


def test_activity_log_seed_round_trips_utf8(tmp_path, monkeypatch):
    _log_in_locale_encoded_interpreter(str(tmp_path))
    monkeypatch.setattr(state, "LOG_DIR", str(tmp_path))
    lines = state._load_recent_log_lines()
    assert len(lines) == 1
    assert SAMPLE in lines[0]
    assert "�" not in lines[0]


def test_setup_logging_in_process_writes_utf8(tmp_path):
    import logging

    persona_logger = logging.getLogger("persona")
    saved = list(persona_logger.handlers)
    persona_logger.handlers.clear()
    logger = setup_logging(log_dir=str(tmp_path))
    try:
        logger.info(SAMPLE)
        raw = pathlib.Path(_log_file(tmp_path)).read_bytes()
        assert SAMPLE in raw.decode("utf-8")
    finally:
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        persona_logger.handlers.extend(saved)
