import pytest

from src.api import mcp_token


@pytest.fixture(autouse=True)
def tmp_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_MCP_TOKEN_FILE", str(tmp_path / "mcp_token"))


def test_read_token_empty_when_none():
    assert mcp_token.read_token() == ""


def test_get_or_create_generates_token():
    t = mcp_token.get_or_create_token()
    assert t
    assert len(t) >= 20


def test_get_or_create_is_stable():
    t1 = mcp_token.get_or_create_token()
    t2 = mcp_token.get_or_create_token()
    assert t1 == t2


def test_read_after_create(tmp_path):
    t = mcp_token.get_or_create_token()
    assert mcp_token.read_token() == t
