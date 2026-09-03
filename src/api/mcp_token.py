"""Local MCP bearer token: generated once, stored under the user's config dir,
shown in the UI so the operator can paste it into their MCP client.

THIS FILE IS THE SOLE CREDENTIAL for persona's entire local management API —
`/mcp` and its functionally identical `/api/v1` REST twin (profile CRUD, browser
launch, proxy CRUD, import/export). `src/api/app.py` binds this value and
compares it with `hmac.compare_digest`. So how the bytes reach the disk matters
as much as their entropy, and the persist step below deliberately follows the
same shape as the install secret (`src/core/install_secret.py:_create`) and the
shared writer it now shares with every other 0600 file in the tree.
"""

import secrets

from ..core.config import _under_home
from ..utils.atomic import atomic_write_bytes

#: Bytes of entropy handed to `secrets.token_urlsafe` when minting. Named here
#: so the mint and the length guard below cannot drift apart.
_TOKEN_BYTES = 24

#: Shortest string `read_token` will accept as a real token. DERIVED FROM THE
#: MINT, not chosen: `token_urlsafe(n)` is unpadded base64url, so it is exactly
#: ceil(4n/3) characters — 32 for the 24 bytes above. (NOT ceil(n/3)*4: that is
#: the PADDED length, and the two coincide only when n % 3 == 0. At n=32 a real
#: token is 43 chars while the padded rule says 44, so a maintainer who raised
#: _TOKEN_BYTES and reconciled this constant against the padded rule would make
#: every freshly minted token fail its own guard — read_token would return ""
#: forever and the API credential would never stabilise.) Anything shorter did
#: not come from a complete mint, so it is a truncated write and must be treated
#: as ABSENT rather than used: accepting it would pin the entire management API
#: to a few bytes of entropy, and the caller's next step is simply to mint a
#: real one. (Raising `_TOKEN_BYTES` therefore re-mints once on the next read, which
#: is the correct behaviour for a credential whose strength just changed — the
#: operator re-pastes it, exactly as they would after any rotation.)
_MIN_TOKEN_CHARS = (_TOKEN_BYTES * 4 + 2) // 3


def _path() -> str:
    # Honour PERSONA_HOME like every other data file (an explicit
    # PERSONA_MCP_TOKEN_FILE still wins), so an isolated instance gets its OWN
    # token instead of reusing ~/.persona's.
    return _under_home("mcp_token", "PERSONA_MCP_TOKEN_FILE")


def get_or_create_token() -> str:
    """Return the persistent local MCP token, creating it on first use."""
    path = _path()
    existing = read_token()
    if existing:
        return existing
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    # Temp file in the same directory -> fsync -> chmod 0600 -> os.replace, via
    # the shared writer. The ordering is the point: the mode is applied to the
    # TEMP, so the token never exists at its final path under a wider mode, not
    # even for the instant a write-then-chmod would leave open. It also makes
    # the swap atomic, so a mint interrupted partway cannot leave a half-written
    # credential where a reader (or the next `read_token`) would find one.
    #
    # THE 0600 IS POSIX-ONLY. On Windows `os.chmod` can only flip the read-only
    # attribute, so the file lands at whatever the parent directory's ACL grants
    # — the same bound `install_secret.py` records for its own file. Fixing the
    # ordering does not fix Windows and does not claim to.
    atomic_write_bytes(path, token.encode("utf-8"), private=True)
    return token


def read_token() -> str:
    """Return the stored token, or '' if there is not a usable one yet.

    A short file reads as ABSENT — see `_MIN_TOKEN_CHARS`. It is not an error:
    the only caller's response is to mint a real token over it.
    """
    try:
        with open(_path(), encoding="utf-8") as f:
            token = f.read().strip()
    except (OSError, UnicodeDecodeError):
        # `UnicodeDecodeError` is here because it is NOT an `OSError` — it
        # inherits from `ValueError`, so an undecodable token file (a torn
        # write, disk corruption, an external edit) walked straight through an
        # `OSError`-only arm and out of this function, breaking the docstring's
        # "'' if there is not a usable one yet" promise. Both callers are
        # unguarded: `api/app.py` binds this at startup, so the entire local
        # management API did not start; the UI's Connect page (`ui/app.py`)
        # raised instead of rendering. Returning '' is this function's own
        # documented answer, and the only caller's response is to mint a real
        # token over it — which is exactly right for bytes that cannot be a
        # token persona wrote.
        #
        # Named and narrow, mirroring `_from_file` in `verify/exit_guard.py`:
        # NOT bare `Exception`, and NOT `errors="replace"` — mangling
        # undecodable bytes into a string could hand `hmac.compare_digest` a
        # credential nobody minted.
        return ""
    return token if len(token) >= _MIN_TOKEN_CHARS else ""
