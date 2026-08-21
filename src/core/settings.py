"""Small persistent key-value store for app-level preferences (onboarding
seen, etc.). Lives under the user's config dir, separate from per-profile data.
"""

import json
import os
import threading
import time

from ..core import config
from ..utils.atomic import atomic_write_json

# set() is a read-modify-write of the whole file, called from the UI thread and
# the API/server background thread; serialize it so two concurrent sets can't
# each read the old file and one clobber the other's key.
_set_lock = threading.Lock()

_ONBOARDING_KEY = "onboarding_done"
_SERVER_KEY = "server_enabled"
_AUTO_UPDATE_KEY = "auto_update"
_LAST_VERSION_KEY = "last_seen_version"
# The firefox-NN build the operator deliberately went BACK to. Empty (the
# normal state) means "launch the newest installed build" — the pin exists
# only after an explicit revert, and clearing it resumes normal updating.
_ENGINE_PIN_KEY = "engine_build_pin"


def _path() -> str:
    # Derive from PERSONA_HOME like every other data file (profiles, proxies,
    # certs, bookmarks, mcp token). settings.json was the lone exception that
    # hardcoded ~/.persona, so a portable/isolated PERSONA_HOME layout split
    # onboarding_done/last_seen_version off from the rest of the data — the
    # instance re-ran onboarding and re-showed the changelog every launch, and
    # two isolated instances clobbered each other (audit5 #3). An explicit
    # PERSONA_SETTINGS_FILE still wins.
    return config._under_home("settings.json", "PERSONA_SETTINGS_FILE")


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
    # atomic_write_json gives a unique temp per call + fsync, so concurrent
    # writers can't interleave into a corrupt settings.json.
    atomic_write_json(_path(), data)


def get(key: str, default=None):
    return _load().get(key, default)


def set(key: str, value) -> None:
    # Merge into whatever is on disk. If the existing file can't be read right
    # now, DON'T persist a partial — writing {key: value} alone would drop every
    # other preference (onboarding_done among them, which re-triggered onboarding
    # after an update, #214). Retry through a transient lock first (#226); only
    # skip the write if it stays unreadable, and that self-heals on the next set.
    with _set_lock:
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


def engine_build_pin() -> str:
    """The firefox-NN build an operator deliberately reverted to, or "" when
    they never did. "" is the normal state and means "launch the newest
    installed build" — the pin is written only by an explicit revert.

    A pin is a STANDING instruction, not a one-off launch flag: it survives
    restarts, it makes the build prune-immune, and it holds the automatic
    update off. Otherwise the unattended updater would put the operator back
    on the build they just rejected, which is the whole failure this exists to
    end."""
    v = get(_ENGINE_PIN_KEY, "")
    return v if isinstance(v, str) else ""


def set_engine_build_pin(build: str) -> None:
    """Pin launches to `build`, or pass "" to clear the pin and resume normal
    updating (the operator saying "go forward again")."""
    set(_ENGINE_PIN_KEY, str(build or ""))
