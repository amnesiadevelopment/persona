import os
from types import ModuleType

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _home() -> str:
    """Single root for all runtime data. Defaults to ~/.persona so the app
    never scatters files into the directory it happens to be launched from.
    Override with PERSONA_HOME (e.g. for a portable layout)."""
    return os.path.expanduser(os.getenv("PERSONA_HOME", "~/.persona"))


def _ensure_home(path: str) -> str:
    """Create the runtime root, falling back to ~/.persona if the configured
    PERSONA_HOME can't be made (unwritable, a UNC path that's offline, or a
    parent that's a file). An unguarded makedirs here aborted the import of
    core.config — loaded extremely early — with a bare traceback and no window
    (audit5 #7). Uses the stdlib logger directly to avoid importing core.logging
    at this depth. Returns the directory actually in use, so every path below is
    derived from the home that really exists."""
    import logging

    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        logging.getLogger("config").error(
            "PERSONA_HOME %r could not be created; falling back to ~/.persona",
            path,
        )
        fallback = os.path.expanduser("~/.persona")
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError:
            logging.getLogger("config").error(
                "fallback home %r also unavailable", fallback
            )
        return fallback


# Resolve AND create the home up front so every _under_home path below is built
# from the directory that actually exists (a fallback must apply to them too).
PERSONA_HOME = _ensure_home(_home())


def _is_already_absolute(val: str, _path: ModuleType = os.path) -> bool:
    """Is this override already anchored, such that _under_home must return it
    untouched? `os.path.isabs` alone is NOT enough, on two counts.

    1. On Windows under Python 3.13+, a ROOTED BUT DRIVELESS path ('/custom/x')
       reports isabs=False, where 3.12 reported True. Sending it to abspath then
       pins it to whatever drive the process is running from — relocating a path
       the operator spelled on purpose, and reintroducing exactly the
       cwd-dependence this function exists to remove. Measured:
           py3.12  ntpath.isabs('/custom/p.json') -> True
           py3.13  ntpath.isabs('/custom/p.json') -> False
       so we state the pre-3.13 semantics explicitly rather than depend on a
       stdlib predicate that has already shifted once.

    2. That rooted test MUST be gated to Windows path flavours. A backslash is a
       legal character in a POSIX filename, so '\\custom\\p.json' on Linux is a
       RELATIVE file whose name merely contains backslashes; treating it as
       anchored would return it verbatim and leave a cwd-dependent constant —
       the very defect being fixed. Hence the `sep` check: it is true for ntpath
       (and for os.path on Windows) and false for posixpath.

    `_path` is injectable so the Windows branch is testable from POSIX — this
    defect reached CI precisely because it is invisible to a POSIX-only run."""
    if _path.isabs(val):
        return True
    if _path.sep == "\\":  # Windows path flavour: rooted-but-driveless
        return _path.splitdrive(val)[1][:1] in ("/", "\\")
    return False


def _under_home(name: str, env: str) -> str:
    """Resolve a runtime path: an explicit env override wins; otherwise the
    name is placed under PERSONA_HOME. The result is never interpreted against
    the CALLER's cwd — see the caveat below for the one shape where "never
    interpreted against anything" is a stronger claim than this can make.

    A RELATIVE override is anchored to the current working directory here, once,
    at import. It used to be returned verbatim, which made every constant built
    from it — DATA_DIR, LOG_DIR, ENGINE_DIR — resolve against whatever cwd the
    process happened to hold at the moment each consumer joined onto it. That is
    not a hypothetical shape: `.env.example` ships `PERSONA_DATA_DIR=persona_data`
    (relative) and load_dotenv() above reads it, so an operator who copied the
    example is running on one. And persona's cwd is not fixed — main.py's
    _ensure_valid_cwd() exists because a self-update re-exec can strand the
    process in an unmounted directory and move it to ~ / $HOME / /tmp / /, which
    would silently re-point "where profiles and logs live" mid-installation.
    Anchoring at import (main.py runs _ensure_valid_cwd BEFORE anything imports
    this module) collapses that to a single, stable answer.

    An ABSOLUTE override is returned EXACTLY as given — deliberately not passed
    through abspath/normpath, which would rewrite a trailing slash or an
    embedded '..' and thereby relocate a path an operator spelled on purpose.
    This is a normalisation of relative values, not a rewrite of absolute ones.
    See _is_already_absolute for why that test is not a bare os.path.isabs.

    CAVEAT — stated because a consumer must not drop its OWN anchoring without
    it. On a Windows path flavour, a ROOTED-BUT-DRIVELESS override
    ('/custom/data') is returned verbatim, and that value is NOT absolute:

        py3.13  ntpath.isabs('/custom/data')            -> False
                resolved from cwd 'C:\\repo'      -> C:\\custom\\data
                resolved from cwd 'D:\\elsewhere' -> D:\\custom\\data

    so it still resolves against the process's current DRIVE. This is not a
    regression — the pre-normalisation function returned that shape verbatim
    too, byte for byte — and it is not fixable here: the two guarantees
    genuinely conflict for this one spelling, and "an absolute override is
    returned exactly as given" WINS, because rewriting it onto the current
    drive would relocate a path the operator spelled (that is the defect
    _is_already_absolute exists to prevent, and it turned Windows CI red once).

    What IS universally true, and what a consumer may rely on, is the weaker
    property this function actually delivers: THE VALUE DOES NOT MOVE WHEN THE
    PROCESS'S CWD MOVES. Every constant is fixed at import and is identical
    afterwards regardless of any later chdir. On POSIX — and on Windows for
    every other override shape — the result is additionally absolute.

    Do not read the paragraph above as licence to re-add a cwd join: joining
    os.getcwd() onto one of these constants is wrong under every shape, and
    removing those joins is the point of this function. A Windows consumer that
    needs a drive-anchored value from a rooted-driveless override should say so
    explicitly (os.path.abspath at that call site), not silently."""
    val = os.getenv(env)
    if val:
        return val if _is_already_absolute(val) else os.path.abspath(val)
    return os.path.join(PERSONA_HOME, name)


def _int_env(env: str, default: int) -> int:
    """Read an int-valued env override, falling back to the default when it's
    unset or non-numeric. A bad value (e.g. PERSONA_API_PORT=abc) must not crash
    the whole app at import with a raw traceback and no window."""
    val = os.getenv(env)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


PROFILES_FILE = _under_home("profiles.json", "PERSONA_PROFILES_FILE")
PROXIES_FILE = _under_home("proxies.json", "PERSONA_PROXIES_FILE")
CERTS_FILE = _under_home("certificates.json", "PERSONA_CERTS_FILE")
CERTS_DIR = _under_home("certificates", "PERSONA_CERTS_DIR")
BOOKMARKS_FILE = _under_home("bookmarks.json", "PERSONA_BOOKMARKS_FILE")
DATA_DIR = _under_home("persona_data", "PERSONA_DATA_DIR")
LOG_DIR = _under_home("logs", "PERSONA_LOG_DIR")
ENGINE_DIR = _under_home("engine", "PERSONA_ENGINE_DIR")

LOG_LEVEL = os.getenv("PERSONA_LOG_LEVEL", "INFO")
PROXY_CHECK_TIMEOUT = _int_env("PERSONA_PROXY_TIMEOUT", 10)

API_HOST = os.getenv("PERSONA_API_HOST", "127.0.0.1")


def _port_env(env: str, default: int) -> int:
    """A TCP port from an env override, clamped to the valid 1..65535 range.
    validation.py enforces this range for proxy ports; the API port parser did
    not, so PERSONA_API_PORT=0/-1/99999 sailed through to bind() and failed with
    an opaque error (audit5 LOW). Out-of-range or non-numeric → the default."""
    val = _int_env(env, default)
    if not 1 <= val <= 65535:
        return default
    return val


API_PORT = _port_env("PERSONA_API_PORT", 8000)

# The runtime root holds proxy/ssh creds and profile data; keep it owner-only on
# POSIX (chmod is a near-no-op on Windows, harmless). Never widen an existing dir.
# (The directory itself was created up top by _ensure_home.)
try:
    os.chmod(PERSONA_HOME, 0o700)
except OSError:
    pass
