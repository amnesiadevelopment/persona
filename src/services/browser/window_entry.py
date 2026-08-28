import os
import pathlib
import re
import zlib

from ...core.logging import get_logger

logger = get_logger("browser.window_entry")


def app_id_for(profile_name: str) -> str:
    """The window app_id / WM_CLASS for a profile, shared by the .desktop
    StartupWMClass, the browser's --class, and (on Firefox) MOZ_APP_REMOTINGNAME.

    Must be a valid DBus path segment ([A-Za-z0-9_]): Firefox uses the app_id as
    its DBus remoting name, and a dash or space makes it reject the name and fall
    back to a shared default, so two profiles collide and the second won't open.
    A crc of the original name keeps profiles whose names collapse to the same
    sanitised form (e.g. "a-b" and "a b") distinct."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", profile_name)
    digest = format(zlib.crc32(profile_name.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return f"persona_{safe}_{digest}"


def _entry_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~/.local/share/applications"))


def _legacy_safe_filename(profile_name: str) -> str:
    """The pre-PS-209 filename: sanitise with NO digest.

    Kept only so entries already written under it can be found and removed.
    Never used to WRITE. It collides for names that collapse to the same
    sanitised form, which is the whole reason it was replaced.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    return f"persona-{safe}.desktop"


def _safe_filename(profile_name: str) -> str:
    """The .desktop filename for a profile.

    Carries the same crc of the original name that `app_id_for` does, and for
    the same reason: profiles whose names collapse to one sanitised form (e.g.
    "a-b" and "a b") must not share one file. Without the digest they did, and
    a single file cannot label two windows — its StartupWMClass matches only
    one of the pair, so the other fell back to the generic label this module
    exists to prevent. Worse, `remove_window_entry` unlinks by this name, so
    deleting either profile removed the entry belonging to the LIVE one and
    stranded that profile's cleartext `Name=` on the host (PS-209).
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    digest = format(zlib.crc32(profile_name.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return f"persona-{safe}-{digest}.desktop"


def _unlink_legacy_entry(entry_dir: pathlib.Path, profile_name: str) -> None:
    """Best-effort removal of this profile's pre-PS-209 (digest-less) entry.

    The legacy name is ambiguous by construction, so this can only be reached
    for a name the caller is already acting on — never as a sweep.
    """
    try:
        (entry_dir / _legacy_safe_filename(profile_name)).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(
            "Could not remove legacy desktop entry for %s: %s", profile_name, e
        )


def write_window_entry(profile_name: str, icon: str = "chromium") -> str:
    """Write a desktop entry so the Wayland taskbar shows the browser window
    with the engine icon and the profile name instead of a generic fallback.

    labwc/lxqt-panel matches a toplevel's app_id against StartupWMClass to pick
    the icon and label; the browser is launched with --class=<app_id>.
    `icon` is the engine's icon-theme name (chromium / firefox).
    """
    entry_dir = _entry_dir()
    entry_dir.mkdir(parents=True, exist_ok=True)
    path = entry_dir / _safe_filename(profile_name)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={profile_name}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        f"StartupWMClass={app_id_for(profile_name)}\n"
    )
    path.write_text(content, encoding="utf-8")
    # PS-209 migration: converge this profile off the old digest-less filename.
    # The scheme change would otherwise orphan the entry already on disk under
    # the legacy name, because remove_window_entry no longer computes that path
    # and nothing else would ever reach it. Unlinking it HERE — for this one
    # name, after its replacement is safely written — is self-healing on the
    # next launch and needs no profile list and no directory sweep, so it can
    # never delete a file this app did not write.
    _unlink_legacy_entry(entry_dir, profile_name)
    return str(path)


def remove_window_entry(profile_name: str) -> None:
    """Delete a profile's desktop entry. The entry embeds the profile NAME in
    cleartext (Name=, and the filename); leaving it after a delete or a panic
    wipe was a forensic trace that survived the wipe (audit6 LOW c). Best-effort;
    absent file is fine.

    Removes BOTH the current filename and this profile's pre-PS-209 legacy one:
    a delete/wipe is the last chance to reach that residue, and after it the
    profile is gone, so no later launch would ever converge the old file away.
    """
    entry_dir = _entry_dir()
    try:
        (entry_dir / _safe_filename(profile_name)).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove desktop entry for %s: %s", profile_name, e)
    _unlink_legacy_entry(entry_dir, profile_name)
