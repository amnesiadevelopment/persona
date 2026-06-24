import os

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


PERSONA_HOME = _home()


def _under_home(name: str, env: str) -> str:
    """Resolve a runtime path: an explicit env override wins; otherwise the
    name is placed under PERSONA_HOME. Absolute overrides are used as-is."""
    val = os.getenv(env)
    if val:
        return val
    return os.path.join(PERSONA_HOME, name)


PROFILES_FILE = _under_home("profiles.json", "PERSONA_PROFILES_FILE")
PROXIES_FILE = _under_home("proxies.json", "PERSONA_PROXIES_FILE")
BOOKMARKS_FILE = _under_home("bookmarks.json", "PERSONA_BOOKMARKS_FILE")
DATA_DIR = _under_home("persona_data", "PERSONA_DATA_DIR")
LOG_DIR = _under_home("logs", "PERSONA_LOG_DIR")
ENGINE_DIR = _under_home("engine", "PERSONA_ENGINE_DIR")

LOG_LEVEL = os.getenv("PERSONA_LOG_LEVEL", "INFO")
PROXY_CHECK_TIMEOUT = int(os.getenv("PERSONA_PROXY_TIMEOUT", "10"))

API_HOST = os.getenv("PERSONA_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("PERSONA_API_PORT", "8000"))

os.makedirs(PERSONA_HOME, exist_ok=True)
