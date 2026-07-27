"""Small persistent key-value store for app-level preferences (onboarding
seen, etc.). Lives under the user's config dir, separate from per-profile data.
"""

import json
import os
import pathlib
import time

SETTINGS_DIR = os.path.expanduser("~/.persona")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

_ONBOARDING_KEY = "onboarding_done"
_SERVER_KEY = "server_enabled"
_AUTO_UPDATE_KEY = "auto_update"
_LAST_VERSION_KEY = "last_seen_version"


def _path() -> str:
    return os.environ.get("PERSONA_SETTINGS_FILE", SETTINGS_FILE)


def _quarantine(path: str) -> None:
    # An unreadable settings file may still hold real preferences; move it
    # aside so the next _save() can't silently overwrite it.
    try:
        os.replace(path, f"{path}.corrupt-{int(time.time())}")
    except OSError:
        pass


class _UnreadableSettings(Exception):
    """The settings file exists on disk but couldn't be read this instant (a
    permission blip, a race with the atomic replace at relaunch). Distinct from
    a genuinely absent file — the caller must NOT overwrite it with a partial."""


def _read(path: str) -> dict:
    """Parse the settings file. Returns {} when the file is genuinely absent;
    raises _UnreadableSettings when a file exists but this read failed, so a
    write can back off instead of clobbering real preferences (#214)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except OSError as e:
        # The file is there (or we can't even tell) but we couldn't read it —
        # a transient failure, never treat it as "empty settings".
        raise _UnreadableSettings(str(e)) from e
    except ValueError:
        _quarantine(path)
        return {}
    if isinstance(data, dict):
        return data
    _quarantine(path)
    return {}


def _load() -> dict:
    # A transient read failure is NOT "no settings" (#226): right after a Windows
    # update the relaunched persona can hit a momentary lock on settings.json (the
    # installer's /CLOSEAPPLICATIONS, an AV sweep, or the relaunch purge still
    # holding a handle). Treating that first failed read as {} made
    # is_onboarding_done() False and re-ran the whole onboarding after every
    # update. Retry a few times before falling back so the real value survives a
    # brief lock; a genuinely absent file returns {} on the first try (no raise).
    for attempt in range(5):
        try:
            return _read(_path())
        except _UnreadableSettings:
            if attempt < 4:
                time.sleep(0.1)
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
    # Merge into whatever is on disk. If the existing file can't be read right
    # now, DON'T persist a partial — writing {key: value} alone would drop every
    # other preference (onboarding_done among them, which re-triggered onboarding
    # after an update, #214). Retry through a transient lock first (#226); only
    # skip the write if it stays unreadable, and that self-heals on the next set.
    data = None
    for attempt in range(5):
        try:
            data = _read(_path())
            break
        except _UnreadableSettings:
            if attempt < 4:
                time.sleep(0.1)
    if data is None:
        return
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


def last_seen_version() -> str:
    """The APP_VERSION the last session recorded, or "" if never recorded (a
    first run, or a pre-changelog install). Drives the after-update changelog
    (#215): "" + not onboarded = first install → onboarding; a value that
    differs from the current APP_VERSION = just updated → changelog."""
    v = get(_LAST_VERSION_KEY, "")
    return v if isinstance(v, str) else ""


def set_last_seen_version(version: str) -> None:
    set(_LAST_VERSION_KEY, str(version))
