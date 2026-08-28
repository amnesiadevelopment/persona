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
# The Chromium TAG the operator deliberately went BACK to. Same idea as the key
# above and deliberately NOT the same key, which is worth stating because
# sharing one looks free and is not.
#
# This store is a single flat JSON dict — one key, one value, no namespace — so
# a shared key is a shared VALUE SPACE, and these two hold different vocabularies:
# _ENGINE_PIN_KEY holds a "firefox-NN" build directory name, this one holds an
# upstream Chromium release tag like "148.0.7559.132". Four live readers of
# _ENGINE_PIN_KEY would have silently mis-read a Chromium tag — engine_install's
# active_build() (membership test fails, so the pin is IGNORED and the revert
# does not hold), its prune-immunity number at :708, and the two UI sites that
# suppress the FIREFOX update row and offer the FIREFOX resume gesture. Sharing
# would have made "go back on Chromium" mute Firefox's updates, and Firefox's
# "resume updates" silently clear Chromium's revert.
#
# Two engines, two pins. They are independent gestures and nothing should make
# one engine's rollback observable in the other's UI.
_CHROMIUM_PIN_KEY = "chromium_build_pin"
# The persona RELEASE an operator deliberately went BACK from — the app-update
# counterpart of the two engine pins above, and deliberately a THIRD key rather
# than a reuse of either.
#
# The same flat-dict argument the _CHROMIUM_PIN_KEY comment makes applies again,
# and the vocabulary differs a third time: the engine keys hold a "firefox-NN"
# build dir and a Chromium tag, this one holds a PERSONA release like "3.0.2".
# Sharing would make "go back on persona" mute an engine's update row, and an
# engine's "resume updates" silently un-hold a rejected persona build.
#
# NOTE THE INVERTED SENSE, which is why it is named a hold and not a pin: the
# engine keys name the build to STAY ON, this one names the release to STAY
# AWAY FROM. That difference is the whole design choice recorded on PS-208 —
# holding the rejected release rather than pausing updates outright means a
# LATER release (the one that probably carries the fix) still installs normally,
# so the hold cannot silently become permanent. Empty (the normal state) means
# nothing was ever rejected.
_APP_HOLD_KEY = "app_update_hold"
# How PERSONA'S OWN requests should leave the machine (the release-metadata
# polls it makes unattended at startup) — NOT a profile's proxy, which lives
# per-profile in the proxy store. Empty means DIRECT, which is what every
# existing install has and what this key must keep meaning: see
# services/egress.py for why the default is deliberately not fail-closed.
_APP_EGRESS_KEY = "app_egress_proxy"


def _path() -> str:
    # Derive from PERSONA_HOME like every other data file (profiles, proxies,
    # certs, bookmarks, mcp token). settings.json hardcoded ~/.persona, so a
    # portable/isolated PERSONA_HOME layout split onboarding_done/
    # last_seen_version off from the rest of the data — the instance re-ran
    # onboarding and re-showed the changelog every launch, and two isolated
    # instances clobbered each other (audit5 #3). An explicit
    # PERSONA_SETTINGS_FILE still wins.
    #
    # THIS WAS NOT THE LONE EXCEPTION, though this comment claimed it was, and
    # that claim is what kept the second one invisible: single_instance.py's
    # lockfile hardcoded ~/.persona/persona.lock the same way, with the same two
    # consequences (host residue under a relocated home, and two isolated
    # installs falsely refusing to run together). Fixed in PS-86 through this
    # same _under_home helper. If a third turns up, prefer grepping for
    # expanduser("~/.persona") over trusting a comment like this one.
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


def chromium_build_pin() -> str:
    """The Chromium release TAG an operator deliberately reverted to, or "" when
    they never did. "" is the normal state and means "install the newest
    acceptable build" — the pin is written only by an explicit revert.

    A pin is a STANDING instruction, not a one-off flag: it survives restarts
    and it holds the hourly unattended check off. Without it a revert lasts
    under an hour — the next check sees the build the operator just rejected as
    "newer than installed" and puts them straight back on it, which is the whole
    failure this exists to end.

    Deliberately a DIFFERENT key from engine_build_pin(), which is the Firefox
    engine's pin. See _CHROMIUM_PIN_KEY for why sharing one would have made a
    Chromium revert mute Firefox's update row."""
    v = get(_CHROMIUM_PIN_KEY, "")
    return v if isinstance(v, str) else ""


def set_chromium_build_pin(tag: str) -> None:
    """Pin the Chromium engine to `tag`, or pass "" to clear the pin and resume
    normal updating (the operator saying "go forward again")."""
    set(_CHROMIUM_PIN_KEY, str(tag or ""))


def app_update_hold() -> str:
    """The persona release an operator deliberately went BACK from, or "" when
    they never did. "" is the normal state and means "offer the newest
    release" — the hold is written only by an explicit revert.

    A hold is a STANDING instruction, not a one-off flag: it survives the
    restart the revert itself demands, which is the entire point. Without it a
    revert lasts under a MINUTE — the 60s update poll sees the release the
    operator just rejected as newer than what is now installed, and on Linux
    with auto-update on (the default) installs it again with nobody present.

    NOTE THE INVERTED SENSE versus engine_build_pin()/chromium_build_pin(): a
    pin names the build to STAY ON, this names the release to STAY AWAY FROM.
    So it holds back that release AND everything at or below it, while a LATER
    release — the one that probably carries the fix — is offered normally. That
    is deliberate (PS-208): a hold that swallowed every future release would be
    a worse defect than the loop it closes."""
    v = get(_APP_HOLD_KEY, "")
    return v if isinstance(v, str) else ""


def set_app_update_hold(version: str) -> None:
    """Hold back `version` (and anything not newer than it), or pass "" to
    clear the hold and resume normal updating (the operator saying "go forward
    again")."""
    set(_APP_HOLD_KEY, str(version or ""))


def app_egress_proxy() -> str:
    """The proxy persona's OWN unattended requests must leave through, or "" to
    send them directly.

    "" is the default AND the behaviour every existing install already has, and
    that is deliberate rather than lax. The charter's fail-closed rule governs a
    PROFILE's declared geography, where refusing a launch costs one launch; here
    a fail-closed default would mean no persona could check for updates until
    its operator configured a proxy — bricking the update path, security updates
    included, for every install that predates this key. Fail-closed applies ONCE
    a value is set, and there it is genuine: see services/egress.py, where a
    configured-but-unusable transport means the request is NOT SENT rather than
    silently falling back to the real IP.
    """
    v = get(_APP_EGRESS_KEY, "")
    return v.strip() if isinstance(v, str) else ""


def set_app_egress_proxy(proxy: str) -> None:
    """Set (or, with "", clear) the app-egress proxy. Stored stripped so a
    stray-whitespace paste cannot become a value that is truthy here but
    unparseable at the transport — the difference between "direct" and "refuse
    to send", which must never turn on invisible characters."""
    set(_APP_EGRESS_KEY, str(proxy).strip())
