import logging

import pytest

from src.core.logging import SESSION_MARKER, setup_logging


def _reset_persona_logger():
    logger = logging.getLogger("persona")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


@pytest.fixture(autouse=True)
def _clean_persona_logger():
    _reset_persona_logger()
    yield
    _reset_persona_logger()


def _log_content(tmp_path):
    files = list(tmp_path.glob("persona_*.log"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


def test_setup_logging_writes_session_marker(tmp_path):
    setup_logging(str(tmp_path))
    assert SESSION_MARKER in _log_content(tmp_path)


def test_session_marker_written_once_per_launch(tmp_path):
    setup_logging(str(tmp_path))
    setup_logging(str(tmp_path))
    assert _log_content(tmp_path).count(SESSION_MARKER) == 1
