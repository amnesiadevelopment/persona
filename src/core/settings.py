"""Small persistent key-value store for app-level preferences (onboarding
seen, etc.). Lives under the user's config dir, separate from per-profile data.
"""

import json
import os
import pathlib

SETTINGS_DIR = os.path.expanduser("~/.persona")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

_ONBOARDING_KEY = "onboarding_done"
_SERVER_KEY = "server_enabled"
_AUTO_UPDATE_KEY = "auto_update"


def _path() -> str:
    return os.environ.get("PERSONA_SETTINGS_FILE", SETTINGS_FILE)


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    pathlib.Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get(key: str, default=None):
    return _load().get(key, default)


def set(key: str, value) -> None:
    data = _load()
    data[key] = value
    _save(data)


def is_onboarding_done() -> bool:
    return bool(get(_ONBOARDING_KEY, False))


def mark_onboarding_done() -> None:
    set(_ONBOARDING_KEY, True)


def is_server_enabled() -> bool:
    return bool(get(_SERVER_KEY, False))


def set_server_enabled(enabled: bool) -> None:
    set(_SERVER_KEY, bool(enabled))


def is_auto_update_enabled() -> bool:
    return bool(get(_AUTO_UPDATE_KEY, True))


def set_auto_update_enabled(enabled: bool) -> None:
    set(_AUTO_UPDATE_KEY, bool(enabled))
