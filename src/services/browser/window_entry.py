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

    The legacy name is ambiguous BY CONSTRUCTION, and — the trap — the new
    scheme's own output is a well-formed legacy name for a DIFFERENT profile:
    `_safe_filename("a_b") == _legacy_safe_filename("a_b-28cb39ea")`, because
    the digest suffix is built from `-` and hex, all of which the sanitiser
    passes through untouched. Both names pass `validate_profile_name`, so the
    pair is creatable. Unlinking by filename alone therefore deletes another
    LIVE profile's CURRENT entry, which is the collision this ticket exists
    to close, reintroduced through the migration helper.

    Restricting this to "a name the caller is already acting on" does not
    help: the caller is acting on the ATTACKER's name, and it is the victim's
    file that shares the path. The safety property the first cut claimed —
    "can never delete a file this app did not write" — is true and is the
    wrong property. Every file here is one this app wrote; the hazard was
    never a foreign file, it was another profile's current one.

    So ownership is confirmed from CONTENT, not from the name: a legacy entry
    carries a `StartupWMClass` naming the profile it belongs to, and that token
    is unambiguous — that is the premise of this whole ticket — which makes it
    a reliable ownership token.

    BOTH eras of that token are accepted, because `app_id_for` has not always
    returned one shape. It gained its digest at 8c38017 (v2.1.8); before that
    it returned `persona-{raw name}`. `_safe_filename` was byte-identical from
    v1.0.0 through the merge-base, so a v2.1.7-era entry sits at EXACTLY the
    path computed here — recognising only the modern token would FIND that file
    and then refuse it, turning residue the pre-PS-209 code cleaned up (it
    unlinked by name, unguarded) into residue nothing can ever reach: the write
    path could not heal it and the delete path could not remove it, so a panic
    wipe would leave the profile's cleartext `Name=` on the host and
    `wipe_all_profiles`' "nothing survives it" would stop being true.

    Accepting the superseded token does not re-open the collision above, for
    two structural reasons. The old token embeds the raw name verbatim behind a
    fixed prefix and the match is anchored at BOTH ends of an LF-delimited
    record, so distinct names give distinct tokens. And the two token spaces
    cannot overlap: new is `persona_…`, old is `persona-…`, differing at
    character 8, so an old-token match can never be satisfied by a new-scheme
    file.
    """
    path = entry_dir / _legacy_safe_filename(profile_name)
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as e:
        logger.warning(
            "Could not read legacy desktop entry for %s: %s", profile_name, e
        )
        return

    owned = (
        f"StartupWMClass={app_id_for(profile_name)}",
        # Pre-v2.1.8 (8c38017) app_id_for returned f"persona-{profile_name}".
        f"StartupWMClass=persona-{profile_name}",
    )
    # NOT body.splitlines(): that splits on the Unicode line-boundary set, which
    # includes U+2028, U+2029 and U+0085 — all three sit ABOVE 0x20, so
    # validate_profile_name admits them (it bans only ord < 0x20). A profile
    # named "a\u2028b" would have its own old-era token torn in half
    # ("StartupWMClass=persona-a" + "b"), so its entry would be FOUND at the
    # legacy path and then refused as un-owned: the delete path could not reach
    # it and the write path could not heal it. A .desktop file is an
    # LF-delimited format, so split on the separator this format actually uses.
    if not any(line in owned for line in body.split("\n")):
        # Belongs to another profile — not ours to delete.
        return

    try:
        path.unlink()
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
