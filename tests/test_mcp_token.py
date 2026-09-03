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


# --- PS-280: an undecodable token file --------------------------------------
#
# `read_token`'s `except OSError` arm did not cover `UnicodeDecodeError`, which
# inherits from `ValueError` — NOT from `OSError`. So an undecodable token file
# (a torn write, disk corruption, an external edit) escaped the guard and broke
# the docstring's "'' if there is not a usable one yet" promise. Both callers
# are unguarded: `api/app.py` binds this at startup, so persona's entire local
# management API (`/mcp` plus its `/api/v1` REST twin) did not start; the UI's
# Connect page raised instead of rendering.
#
# Driven against REAL bytes at the REAL token path — which exception a real
# decoding read raises IS the defect, so a mocked `open` would prove nothing.

_UNDECODABLE_TOKENS = {
    # a lone 0xff mid-string: never a valid UTF-8 start byte. Long enough that
    # the length guard cannot be what rejects it — the DECODE has to.
    "raw-0xff": b"abcdefghij\xffklmnopqrstuvwxyz0123456789",
    # a real encoding persona does not read
    "utf-16": ("t" * 40).encode("utf-16"),
}


@pytest.mark.parametrize("label", sorted(_UNDECODABLE_TOKENS))
def test_an_undecodable_token_file_reads_as_absent(tmp_path, label):
    """The documented answer for an unusable token file is '' — asserted on the
    RETURN VALUE of the real function. An escaping `UnicodeDecodeError` fails
    this as an ERROR, which is exactly the regression."""
    (tmp_path / "mcp_token").write_bytes(_UNDECODABLE_TOKENS[label])

    assert mcp_token.read_token() == ""


@pytest.mark.parametrize("label", sorted(_UNDECODABLE_TOKENS))
def test_an_undecodable_token_file_is_re_minted_over(tmp_path, label):
    """The only caller's documented response to '' is to mint a real token over
    it, so the API comes up with a working credential instead of not coming up
    at all."""
    path = tmp_path / "mcp_token"
    path.write_bytes(_UNDECODABLE_TOKENS[label])

    token = mcp_token.get_or_create_token()

    # A real, freshly minted token: not '', and long enough to pass the module's
    # own length guard (so the next read accepts it rather than re-minting).
    assert token
    assert len(token) >= mcp_token._MIN_TOKEN_CHARS
    # It is decodable ASCII, i.e. none of the undecodable bytes survived.
    token.encode("ascii")
    # And it STABILISES: the file on disk now holds exactly this token.
    assert mcp_token.read_token() == token
    assert mcp_token.get_or_create_token() == token


def test_a_decodable_token_file_is_still_read_verbatim(tmp_path):
    """CONTROL. The widened arm must not have turned every read into '' — a
    perfectly good token file still reads, and is NOT re-minted over."""
    good = "x" * mcp_token._MIN_TOKEN_CHARS
    (tmp_path / "mcp_token").write_text(good, encoding="utf-8")

    assert mcp_token.read_token() == good
    assert mcp_token.get_or_create_token() == good


def test_a_short_decodable_token_file_still_reads_as_absent(tmp_path):
    """CONTROL for the OTHER rejection cause. A truncated-but-decodable file was
    always treated as absent, and widening the catch must not have disturbed
    that — the two rejection reasons stay independent."""
    (tmp_path / "mcp_token").write_text("short", encoding="utf-8")

    assert mcp_token.read_token() == ""
