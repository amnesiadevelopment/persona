"""The per-install secret that salts newly minted identity material.

WHAT THIS EXISTS TO CLOSE. `mint_fingerprint_seed()` used to be
`zlib.crc32(name)` — a pure, public function of a string the operator typed.
Same name, same integer, on every install and every machine. And the seed is
not internal bookkeeping: it deterministically derives the PRESENTED machine
(resolution, device preset, touch points, `--fingerprint=`). So an adversary
who guessed a naming scheme — `acme-bank`, `shop1`, `client-alpha` — computed
that profile's presented hardware OFFLINE, with no access to the install at
all. Salting the mint with a value that never leaves this machine is what
turns that offline computation back into a guess.

WHY A FILE UNDER PERSONA_HOME, and not a derived value. Anything DERIVED from
the machine (hostname, MAC, a hardware id) is not a secret: it is another
public preimage, reachable by anyone who can see the host or who can make the
product report it. This is `secrets.token_bytes` — genuine entropy, generated
once, persisted 0600, and read back. `src/api/mcp_token.py` establishes the
shape for exactly this reason, and this module follows it deliberately rather
than inventing a second convention for persisted secret material.

THE PATH IS RESOLVED PER CALL, NOT AT IMPORT — like `TrashStore._path()` and
`mcp_token._path()`, and unlike the eight module-level constants in
`core/config.py`. An isolated instance (a test, a portable layout, a second
install pointed at its own PERSONA_HOME) must get its OWN secret rather than
reusing ~/.persona's; freezing the path at import would silently bind every
such instance to whichever home happened to exist when this module first
loaded.

WHAT HAPPENS WHEN THE SECRET CANNOT BE PERSISTED. It falls back to a
PROCESS-LIFETIME secret and says so, loudly. It does NOT fall back to "no
salt": an unwritable home must not silently restore the guessable, offline
computable seed this module exists to remove. The fallback is weaker in a
different direction — a seed minted under it is still unguessable, and it is
still frozen into `fingerprint_seed_value` at mint time, so the profile keeps
presenting that machine forever — but a LATER mint in a LATER process gets a
different secret. That costs nothing a profile can observe (each seed is
frozen at birth and never re-derived) and it never costs secrecy, which is the
trade this module is willing to make and the reason it is stated here.

THIS VALUE NEVER LEAVES THE MACHINE. It is not a `Profile` field, so it cannot
reach `to_dict()` or an export archive; it is never logged (the failure paths
below name the PATH, never the bytes); and nothing renders it on a page. That
is a property of the whole file: if you are about to return, log, serialise or
display this value, you are re-opening the defect.
"""

from __future__ import annotations

import logging
import os
import pathlib
import secrets
import threading

logger = logging.getLogger("install_secret")

#: Filename under PERSONA_HOME. Named here so `_path` and the module header
#: cannot drift.
_SECRET_FILENAME = "install_secret"

#: Bytes of entropy in a freshly generated secret. 32 bytes is the HMAC-SHA256
#: block-appropriate size and is far past the 32-bit space it protects.
_SECRET_BYTES = 32

#: Cache, KEYED BY RESOLVED PATH rather than a bare module global. The key is
#: what keeps two homes (a test's tmp_path, a portable install) genuinely
#: distinct in one process while still making repeated mints against ONE home
#: hit the same value without re-reading the file — i.e. it serves determinism
#: (AC6) and isolation (AC1) with the same structure. A plain global would
#: hand the second home the first home's secret and quietly make an
#: "independent install" test assert nothing.
_cache: dict[str, bytes] = {}
_lock = threading.Lock()


def _path() -> str:
    """Where the secret lives. Honours PERSONA_HOME like every other data file
    (an explicit PERSONA_INSTALL_SECRET_FILE still wins).

    THE HOME IS READ FROM THE ENVIRONMENT HERE, NOT TAKEN FROM
    ``config.PERSONA_HOME``, and that is the whole reason this is not a
    ``_under_home()`` call. ``_under_home`` joins the module-level
    ``PERSONA_HOME`` CONSTANT, which is bound ONCE at import — so it answers
    with whatever home existed when ``core.config`` first loaded, forever. For
    the eight constants in config.py that is exactly right (they are bound at
    import too, before any chdir). For this file it is wrong in a way that
    silently destroys the property the module exists to provide: a second
    install pointed at its own PERSONA_HOME would resolve to the FIRST home's
    path and read the FIRST install's secret, so "two installs" would mint
    identical seeds and every cross-install assertion would be vacuously
    comparing one secret with itself.

    Measured, which is how this was caught rather than reasoned about:

        PERSONA_HOME=/tmp/x  ->  _under_home(...) = /tmp/x/install_secret
        os.environ[...] = /tmp/y
                             ->  _under_home(...) = /tmp/x/install_secret

    Resolved per call, from the environment, for the same reason
    ``TrashStore.trash_file()`` and ``mcp_token._path()`` are: an isolated
    instance must get its OWN secret. The fallback is the config constant, so
    an install that sets nothing still lands where every other data file does.
    """
    override = os.getenv("PERSONA_INSTALL_SECRET_FILE")
    if override:
        return override
    home = os.getenv("PERSONA_HOME")
    if home:
        return os.path.join(os.path.expanduser(home), _SECRET_FILENAME)
    from .config import PERSONA_HOME

    return os.path.join(PERSONA_HOME, _SECRET_FILENAME)


def _read(path: str) -> bytes:
    """The stored secret, or b'' if there is not a usable one yet.

    A short/empty file is treated as ABSENT rather than used: a truncated write
    would otherwise pin every future mint to a few bytes of entropy, and the
    caller's next step is simply to generate a real one.
    """
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return b""
    return data if len(data) >= _SECRET_BYTES else b""


def _create(path: str) -> bytes:
    """Generate, persist 0600 (POSIX), and return a new secret.

    Written to a temp file in the same directory and `os.replace`d into place,
    so a crash mid-write cannot leave a HALF-LENGTH secret that `_read` would
    reject and a fresh `_create` would then replace — silently re-rolling the
    salt for every mint that followed. The chmod happens BEFORE the rename, so
    the bytes are never briefly readable at the final path under a wider mode.

    THE 0600 IS POSIX-ONLY, and that is a real bound rather than an oversight.
    Windows has no POSIX mode bits: `os.chmod` there can only flip the
    read-only attribute, so the file lands at whatever the parent directory's
    ACL grants (it reads back as 0o666). Confining it would mean an ACL call
    (icacls / pywin32), which this slice does not attempt. So on Windows the
    secret is protected by the ACL on PERSONA_HOME and not by this line — the
    unguessability property still holds against a REMOTE adversary, which is
    the threat this file exists for, but the local-unprivileged-reader half of
    it does not. `test_the_secret_file_is_not_world_readable` is skipped there
    for exactly this reason, matching the repo's convention for mode-bit
    assertions.
    """
    secret = secrets.token_bytes(_SECRET_BYTES)
    directory = os.path.dirname(path)
    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(secret)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return secret


def install_secret() -> bytes:
    """This install's secret, creating it on first use.

    Stable for the life of the install: the same bytes on every call, which is
    what makes the mint DETERMINISTIC given its inputs (no RNG at mint time —
    the entropy was spent once, here, when the file was created).
    """
    path = _path()
    with _lock:
        cached = _cache.get(path)
        if cached:
            return cached

        secret = _read(path)
        if not secret:
            try:
                secret = _create(path)
            except OSError:
                # Never fall back to "no salt" — see the module header. The
                # path is named; the bytes never are.
                logger.error(
                    "Could not persist the install secret at %r; using a "
                    "process-lifetime secret instead. Seeds minted now stay "
                    "unguessable and stay frozen on the profile, but a later "
                    "process will mint under a different secret.",
                    path,
                )
                secret = secrets.token_bytes(_SECRET_BYTES)

        _cache[path] = secret
        return secret


def reset_cache_for_tests() -> None:
    """Drop the in-process cache.

    Exists so a test can repoint PERSONA_HOME and genuinely model a SECOND
    INSTALL. Named for what it is rather than hidden behind an underscore: a
    test reaching for this is doing something legitimate, and a reader finding
    it in product code is looking at a bug.
    """
    with _lock:
        _cache.clear()
