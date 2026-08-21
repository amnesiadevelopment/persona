import json
import os
import pathlib
import shutil
import threading
from collections.abc import Callable

from ...core.config import DATA_DIR, PROFILES_FILE
from ...core.logging import get_logger
from ...models.profile import Profile, mint_fingerprint_seed
from ...utils.atomic import atomic_write_json
from ...utils.store_guard import StoreGuardMixin
from ...utils.trashable import TrashableMixin
from ...utils.validation import validate_profile_name
from .coherence import (
    assert_coherent,
    coherence_error,
    coherent_engine,
    normalize_engine,
)
from .transfer import export_to_zip, import_from_zip, peek_profile_name

logger = get_logger("profile.manager")

#: Directory name of the park area where trashed profile data dirs live. It sits
#: BESIDE DATA_DIR, never inside it.
TRASH_DIR_NAME = "trash_data"


def trash_data_root() -> str:
    """The park area for trashed profile data dirs: a SIBLING of DATA_DIR, never
    a subdirectory of it.

    An in-DATA_DIR park area was addressable as a profile. validate_profile_name
    (".trash") is valid, so _data_path(".trash") resolved to the park area
    ITSELF: every other profile's trashed cookies were parked inside a live,
    launchable profile's data dir, and deleting that profile failed outright
    (renaming a directory into itself). Outside DATA_DIR no profile name can
    name the park area at all, so "nothing in the trash is reachable as a live
    record" holds by CONSTRUCTION rather than by reserving a name a validator
    might later accept again.

    Derived from DATA_DIR's parent at call time, not frozen at import, for two
    reasons: it keeps the park area on the SAME filesystem as the data dir, so
    the move-not-copy rename cannot fail with EXDEV; and DATA_DIR is monkey-
    patched per-test, so a derived root follows it instead of leaking trashed
    profile data into the real PERSONA_HOME during a test run.
    """
    return os.path.join(os.path.dirname(os.path.normpath(DATA_DIR)), TRASH_DIR_NAME)


class InvalidProfileName(ValueError):
    """A profile name that fails validation or would escape the data dir."""


class InvalidTrashToken(ValueError):
    """A trash token that isn't an opaque hex id or would escape the data dir."""


class ProfileManager(StoreGuardMixin, TrashableMixin):
    _guard_logger = logger
    _guard_noun_plural = "profiles"
    _guard_noun_singular = "profile"

    def __init__(self) -> None:
        self.profiles: dict[str, Profile] = {}
        self._save_blocked = False
        # Set by the app once the launcher exists: called with a profile name
        # right before its data dir is rmtree'd, so delete/wipe never rmtree a
        # profile dir out from under a live browser (corrupt cache / Windows
        # rmtree failure). No-op by default (headless/tests).
        self._stop_hook: "Callable[[str], None] | None" = None
        # Serializes load+save so a concurrent read during a bulk import/tagging
        # flow can't see a half-written file and a concurrent add can't be lost.
        self._lock = threading.RLock()
        self._load_profiles()
        pathlib.Path(DATA_DIR).mkdir(exist_ok=True, parents=True)

    def _data_path(self, name: str) -> str:
        # The single choke point for every profile filesystem op (mkdir, rmtree,
        # rename). Validate here so no caller — import, the MCP tool, a future
        # entry point — can slip a traversal name through and turn these into
        # arbitrary-path primitives. Belt AND braces: reject names that fail
        # validation, then confirm the resolved path stays inside DATA_DIR.
        valid, msg = validate_profile_name(name)
        if not valid:
            raise InvalidProfileName(msg)
        base = os.path.realpath(DATA_DIR)
        target = os.path.realpath(os.path.join(DATA_DIR, name))
        if os.path.commonpath([base, target]) != base:
            raise InvalidProfileName(f"profile name escapes the data dir: {name!r}")
        return os.path.join(DATA_DIR, name)

    def _trash_data_path(self, token: str) -> str:
        """Where a trashed profile's data dir lives, keyed by its opaque trash
        token. The trash must not become a NEW arbitrary-path primitive beside
        _data_path, so it gets the same treatment: the token is constrained to
        hex (no separators, no dots, so no traversal is even expressible), and
        the resolved path is then confirmed to stay inside the park area — belt
        AND braces, exactly as for a profile name. The token, not the profile
        name, names the directory: the trash area then carries no profile name in
        cleartext, matching why the desktop entry is removed on delete.

        The park area is trash_data_root(), OUTSIDE DATA_DIR — see its docstring:
        no profile name can address a directory that isn't under the profile data
        dir, so a trashed data dir can never land inside a live profile's own
        directory."""
        if not token or not all(c in "0123456789abcdefABCDEF" for c in token):
            raise InvalidTrashToken(f"not an opaque trash token: {token!r}")
        root = trash_data_root()
        base = os.path.realpath(root)
        target_dir = os.path.join(root, token)
        # realpath of a not-yet-existing leaf is still its lexical parent + leaf,
        # so this check is meaningful before the move as well as after it.
        target = os.path.realpath(target_dir)
        if os.path.commonpath([base, target]) != base or target == base:
            raise InvalidTrashToken(f"trash token escapes the trash area: {token!r}")
        return target_dir

    def _load_profiles(self) -> None:
        with self._lock:
            self._load_profiles_locked()

    def _load_profiles_locked(self) -> None:
        if pathlib.Path(PROFILES_FILE).exists():
            try:
                with pathlib.Path(PROFILES_FILE).open(encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise TypeError(
                        f"profiles root is {type(data).__name__}, not dict"
                    )
                skipped = 0
                for name, p_data in data.items():
                    # One malformed record must not abort the whole load — the
                    # next save would overwrite profiles.json with only what
                    # loaded, silently losing every good profile.
                    try:
                        clean_data = {
                            "name": p_data.get("name"),
                            "proxy": p_data.get("proxy"),
                            "os_type": p_data.get(
                                "os_type",
                                p_data.get("config", {}).get("os", "windows"),
                            ),
                            "device_type": p_data.get(
                                "device_type", "desktop"
                            ),
                            # "camoufox" was the retired Firefox engine; map any
                            # saved profile onto the current "firefox" engine.
                            "engine": (
                                "firefox"
                                if p_data.get("engine") == "camoufox"
                                else p_data.get("engine", "chromium")
                            ),
                            "resolution": p_data.get("resolution", "auto"),
                            "search_engine": p_data.get(
                                "search_engine", "duckduckgo"
                            ),
                            "bookmark_pool": p_data.get("bookmark_pool"),
                            # Absent key = a pre-this-field profile → None so it
                            # keeps getting the default bookmarks. A saved [] is
                            # an intentional empty selection and is preserved.
                            "bookmarks": p_data.get("bookmarks"),
                            "certificate": p_data.get("certificate"),
                            "cookie_import_status": p_data.get(
                                "cookie_import_status"
                            ),
                            # This allow-list is hand-enumerated, so a field
                            # missing HERE is silently dropped on reload even
                            # though to_dict() saved it (see transfer.py:117 —
                            # cookie_import_status was the last field to hit
                            # this exact bug).
                            "cert_trust_status": p_data.get(
                                "cert_trust_status"
                            ),
                            "tags": p_data.get("tags", []),
                            "notes": p_data.get("notes", ""),
                            "ai_control": p_data.get("ai_control", False),
                            # Absent key = a profile written before the seed
                            # was persisted → None, which makes
                            # fingerprint_seed fall back to crc32(name) and
                            # present exactly what that profile has always
                            # presented. That fallback is the entire migration
                            # for existing profiles; do NOT default this to
                            # crc32(name) here, which would silently freeze a
                            # derived value and make a later rename of an OLD
                            # profile behave differently depending on whether
                            # it had been reloaded since. And per the
                            # allow-list note above, omitting this line is not
                            # an inert oversight: the seed would be saved by
                            # to_dict() and dropped on the next load, so every
                            # profile would quietly re-derive from its name at
                            # restart and the freeze would evaporate while the
                            # in-memory tests still passed.
                            "fingerprint_seed_value": p_data.get(
                                "fingerprint_seed_value"
                            ),
                        }
                        self.profiles[name] = Profile(**clean_data)
                    except Exception:
                        skipped += 1
                        logger.exception("Skipping malformed profile %r", name)
                if skipped:
                    logger.warning(
                        "Skipped %d malformed profile record(s)", skipped
                    )
                logger.info("Loaded %d profiles", len(self.profiles))
            except Exception as e:
                logger.exception("Error loading profiles: %s", e)
                self._quarantine_profiles_file()

    def _store_path(self) -> str:
        # Read the module global on every call: the profile specs monkeypatch
        # manager.PROFILES_FILE, so a value bound at import time elsewhere
        # would bypass the patch and touch the real ~/.persona/profiles.json.
        return PROFILES_FILE

    def _quarantine_profiles_file(self) -> None:
        # An unreadable profiles.json still holds every profile the user has;
        # move it aside so the next save_profiles() can't overwrite it with
        # the empty in-memory dict.
        self._quarantine_store_file()

    def save_profiles(self) -> None:
        if self._save_is_blocked():
            return
        try:
            with self._lock:
                atomic_write_json(
                    PROFILES_FILE,
                    {name: p.to_dict() for name, p in self.profiles.items()},
                )
            logger.debug("Profiles saved")
        except Exception as e:
            logger.exception("Error saving profiles: %s", e)

    def add_profile(
        self,
        name: str,
        proxy: str,
        os_type: str,
        search_engine: str = "duckduckgo",
        bookmark_pool: str | None = None,
        bookmarks: list[str] | None = None,
        tags: list[str] | None = None,
        device_type: str = "desktop",
        notes: str = "",
        engine: str = "chromium",
        resolution: str = "auto",
        certificate: str | None = None,
        ai_control: bool = False,
    ) -> bool:
        # Validate up front so an invalid/traversal name is rejected before it's
        # registered — import and the MCP tool used to reach here unchecked.
        valid, _ = validate_profile_name(name)
        if not valid:
            return False
        # The os_type/engine coherence rules live below every door (see
        # coherence.py). They used to live only in the profile dialog, so the
        # REST lane composed profiles the dialog exists to prevent — a macOS
        # record on the Firefox engine, which launches presenting Windows.
        # Raised rather than returned False: False here means "already exists"
        # (409), while an incoherent pair is a different refusal with a reason
        # the caller can act on, and a door that forgets to handle it fails
        # loudly instead of silently storing a lie.
        assert_coherent(os_type, engine)
        # Hold the lock across the check-then-insert so two concurrent adds of
        # the same name can't both pass the `name in self.profiles` check and one
        # silently overwrite the other (RLock: save_profiles below re-enters it).
        with self._lock:
            if name in self.profiles:
                return False
            self.profiles[name] = Profile(
                name=name,
                proxy=proxy or None,
                os_type=os_type,
                device_type=device_type,
                engine=engine,
                resolution=resolution,
                search_engine=search_engine,
                bookmark_pool=bookmark_pool or None,
                bookmarks=bookmarks,
                certificate=certificate or None,
                tags=tags or [],
                notes=notes,
                ai_control=ai_control,
                # Freeze the seed HERE, at the one moment the profile's
                # identity is born. Minted from the creation name, so a new
                # profile presents exactly what it would have presented when
                # the seed was re-derived on every read — but from now on a
                # rename moves the name and the data dir while the presented
                # machine stays put. Mint only on CREATE: writing this on any
                # edit path would re-roll the very thing it exists to pin.
                fingerprint_seed_value=mint_fingerprint_seed(name),
            )
            self.save_profiles()
            pathlib.Path(self._data_path(name)).mkdir(exist_ok=True, parents=True)
        logger.info("Created profile: %s", name)
        return True

    def update_profile(
        self,
        original_name: str,
        new_name: str,
        new_proxy: str,
        new_os: str,
        new_search_engine: str | None = None,
        new_bookmark_pool: str | None = None,
        new_bookmarks: list[str] | None = None,
        new_tags: list[str] | None = None,
        new_ai_control: bool | None = None,
        new_device_type: str | None = None,
        new_notes: str | None = None,
        new_engine: str | None = None,
        new_resolution: str | None = None,
        new_certificate: str | None = None,
    ) -> bool:
        with self._lock:
            if original_name not in self.profiles:
                return False

            if new_name != original_name and new_name in self.profiles:
                return False

            # Coherence (see coherence.py) is checked on the pair this edit would
            # RESULT IN, not on the fields it happens to supply: a PATCH carrying
            # only os_type must be judged against the engine already stored, or
            # `PATCH {"os_type": "macos"}` would sail through on a profile that is
            # already firefox. Both values are in hand here and only here, which
            # is the second reason the rule belongs in the model rather than at a
            # route.
            #
            # Refused only when the edit INTRODUCES the incoherence. A record
            # written before these rules existed (or through the unguarded REST
            # lane) stays editable: blocking it would make an ordinary edit to a
            # note or a tag fail on a conflict that edit did not create, and
            # leave the profile permanently uneditable — including the edit that
            # would FIX the pair.
            _current = self.profiles[original_name]
            _resulting_engine = (
                new_engine if new_engine is not None
                else getattr(_current, "engine", "chromium")
            )
            _pair_changed = (
                new_os != _current.os_type
                or _resulting_engine != getattr(_current, "engine", "chromium")
            )
            if _pair_changed:
                assert_coherent(new_os, _resulting_engine)

            # Rename the data dir BEFORE touching any in-memory field, so a
            # locked/failed dir-rename (routine on Windows when the browser is
            # running) leaves the profile completely unchanged — no half-applied
            # fields and no memory/disk divergence.
            if new_name != original_name:
                old_dir = self._data_path(original_name)
                if pathlib.Path(old_dir).exists():
                    try:
                        pathlib.Path(old_dir).rename(self._data_path(new_name))
                    except OSError as e:
                        logger.warning(
                            "Rename of profile data dir %r failed, keeping %r: %s",
                            original_name, original_name, e,
                        )
                        return False
                self.profiles = {
                    (new_name if k == original_name else k): v
                    for k, v in self.profiles.items()
                }
                # The desktop entry is keyed by NAME and embeds it in cleartext,
                # but delete/wipe only ever remove the CURRENT name (audit6 LOW
                # c). Drop the old name's entry here or it is stranded on the
                # host forever, outside PERSONA_HOME and unreachable by both.
                # Nothing is written back: the next launch rewrites it under the
                # new name (process.py). Must come AFTER the dir rename, whose
                # failure returns early and must leave everything untouched.
                self._remove_window_entry(original_name)

            profile = self.profiles[new_name]
            profile.name = new_name
            profile.proxy = new_proxy or None
            profile.os_type = new_os
            if new_device_type is not None:
                profile.device_type = new_device_type
            if new_engine is not None:
                profile.engine = new_engine
            if new_resolution is not None:
                profile.resolution = new_resolution
            if new_search_engine is not None:
                profile.search_engine = new_search_engine
            profile.bookmark_pool = new_bookmark_pool or None
            if new_bookmarks is not None:
                profile.bookmarks = new_bookmarks
            if new_tags is not None:
                profile.tags = new_tags
            if new_notes is not None:
                profile.notes = new_notes
            if new_certificate is not None:
                _new_cert = new_certificate or None
                if _new_cert != profile.certificate:
                    # cert_trust_status records the outcome of the LAST CA trust
                    # attempt, which was made for the certificate being replaced
                    # here. Carried over it would render a verdict describing a
                    # different CA — including a stale "trusted", an affirmative
                    # clean bill of health for a certificate whose trust was
                    # never attempted. None is the field's own "never attempted".
                    # Conditional on purpose: update_profile runs on EVERY field
                    # edit, so an unconditional clear would discard a real
                    # verdict on a rename or a notes edit.
                    profile.cert_trust_status = None
                profile.certificate = _new_cert
            if new_ai_control is not None:
                profile.ai_control = new_ai_control

            self.save_profiles()
        logger.info("Updated profile: %s -> %s", original_name, new_name)
        return True

    def set_cookie_status(self, name: str, status: str) -> bool:
        with self._lock:
            if name not in self.profiles:
                return False
            self.profiles[name].cookie_import_status = status
            self.save_profiles()
        return True

    def set_cert_trust_status(self, name: str, status: str) -> bool:
        """Record the outcome of the last mTLS CA trust attempt for a profile.

        The Firefox CA import soft-fails (the launch proceeds untrusted), so
        this is the only thing that survives the session to tell the operator
        the assigned certificate is not actually trusted.
        """
        with self._lock:
            if name not in self.profiles:
                return False
            self.profiles[name].cert_trust_status = status
            self.save_profiles()
        return True

    def assign_tag(self, names: list[str], tag: str) -> int:
        """Add a tag to each named profile (no duplicates). Returns count changed."""
        tag = tag.strip()
        if not tag:
            return 0
        with self._lock:
            changed = 0
            for name in names:
                p = self.profiles.get(name)
                if p is not None and tag not in p.tags:
                    p.tags.append(tag)
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def remove_tag(self, tag: str) -> int:
        """Remove a tag from every profile that has it, case-insensitively, so a
        "Work"/"work" mix doesn't leave a variant behind after a chip ✕ — the
        chip cloud and the tag filter both treat tags case-insensitively (audit5
        LOW). Returns count changed."""
        t = tag.lower()
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                kept = [x for x in p.tags if x.lower() != t]
                if len(kept) != len(p.tags):
                    p.tags = kept
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def clear_proxy(self, proxy_name: str) -> int:
        """Drop a proxy reference from every profile that uses it, so a deleted
        proxy leaves no dangling name behind (which stranded the profile page).
        Returns count changed."""
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.proxy == proxy_name:
                    p.proxy = None
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def clear_bookmark_pool(self, pool_name: str) -> int:
        """Drop a bookmark-pool reference from every profile that uses it, so a
        deleted pool leaves no dangling name behind (which made the profile launch
        with an empty toolbar). Mirror of clear_proxy. Returns count changed."""
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.bookmark_pool == pool_name:
                    p.bookmark_pool = None
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def rename_bookmark_pool(self, old_name: str, new_name: str) -> int:
        """Propagate a pool rename to every profile referencing the old name, so
        the reference stays valid. Returns count changed."""
        if not new_name or new_name == old_name:
            return 0
        with self._lock:
            changed = 0
            for p in self.profiles.values():
                if p.bookmark_pool == old_name:
                    p.bookmark_pool = new_name
                    changed += 1
            if changed:
                self.save_profiles()
        return changed

    def set_ai_control(self, name: str, enabled: bool) -> bool:
        with self._lock:
            p = self.profiles.get(name)
            if p is None:
                return False
            p.ai_control = enabled
            self.save_profiles()
        return True

    def set_notes(self, name: str, notes: str) -> bool:
        """Save a profile's notes under the manager lock. The inline-edit path
        used to mutate the Profile and save_profiles() directly off the lock —
        the one write escaping the lock discipline, which could lose notes or
        clobber a concurrent rename (audit5 LOW). Returns True when changed."""
        with self._lock:
            p = self.profiles.get(name)
            if p is None or p.notes == notes:
                return False
            p.notes = notes
            self.save_profiles()
        return True

    def set_stop_hook(self, hook: "Callable[[str], None] | None") -> None:
        self._stop_hook = hook

    def _stop_if_running(self, name: str) -> None:
        if self._stop_hook is not None:
            try:
                self._stop_hook(name)
            except Exception as e:
                logger.warning("stop hook failed for %s: %s", name, e)

    def delete_profile(self, name: str) -> bool:
        """Move a profile to the trash: it leaves the profile list and its data
        dir leaves the launchable area, but both are recoverable via
        restore_profile until retention expires or the operator deletes it
        permanently.

        The data dir is MOVED, never copied — a browser profile is large, and two
        copies of one identity would diverge. The move goes through the same
        validated choke point as every other profile filesystem op.
        """
        if name not in self.profiles:
            return False
        # Stop a live browser BEFORE moving its data dir (outside our lock — stop
        # is blocking): a data dir cannot be moved out from under a live engine
        # any more than it could be removed.
        self._stop_if_running(name)
        with self._lock:
            if name not in self.profiles:
                return False
            profile = self.profiles[name]
            token = self._trash().new_id()
            live_dir = self._data_path(name)
            parked = ""
            if pathlib.Path(live_dir).exists():
                dest = self._trash_data_path(token)
                try:
                    pathlib.Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    pathlib.Path(live_dir).rename(dest)
                    parked = dest
                except OSError as e:
                    # Keep the profile completely unchanged rather than delete a
                    # data dir we could not park — a failed trash must not become
                    # the irreversible delete the trash exists to replace.
                    logger.warning(
                        "Could not move profile data dir for %r to the trash: %s",
                        name, e,
                    )
                    return False
            del self.profiles[name]
            self.save_profiles()
            self._trash().add(
                "profile",
                name,
                profile.to_dict(),
                entry_id=token,
                material_path=parked,
            )
            # The desktop entry carries the profile name in cleartext, so it goes
            # the moment the profile is trashed (audit6 LOW c) and comes back only
            # on restore — a trashed profile leaves no more trace than a deleted
            # one did.
            self._remove_window_entry(name)
            logger.info("Moved profile to trash: %s", name)
            return True

    def restore_profile(self, entry) -> tuple[bool, str]:
        """Put a trashed profile back exactly as it was, under its own name.

        Refused when the name is taken — and refused rather than renamed on
        purpose. A profile's entire presented machine derives from crc32(name)
        (Profile.fingerprint_seed), so restoring under a different name would
        hand back the cookie jar attached to a DIFFERENT fingerprint: a silently
        changed identity. Free the name and restore again.
        """
        name = entry.name
        with self._lock:
            if name in self.profiles:
                return False, (
                    f"A profile named '{name}' already exists. A profile's "
                    "fingerprint is derived from its name, so restoring under a "
                    "different name would return its cookies under a different "
                    "identity. Rename or delete the existing profile, then "
                    "restore again."
                )
            live_dir = self._data_path(name)
            parked = entry.material_path
            if parked and pathlib.Path(parked).exists():
                if pathlib.Path(live_dir).exists():
                    return False, (
                        f"A data directory for '{name}' is already in place; "
                        "move it aside before restoring."
                    )
                try:
                    pathlib.Path(parked).rename(live_dir)
                except OSError as e:
                    return False, f"Could not restore the profile's data: {e}"
            try:
                # Intentionally EXEMPT from the coherence rules (coherence.py).
                # Restore replays a record that already existed, so it cannot
                # introduce incoherence — and refusing (or rewriting) here would
                # strand a trashed profile behind a conflict it did not create,
                # which is exactly what the "already-stored records are not
                # stranded" policy forbids. Do not "fix" this into a guard.
                self.profiles[name] = Profile(**entry.payload)
            except Exception as e:
                # Put the data dir back in the trash area so a failed restore
                # leaves the profile still recoverable, not half-moved.
                if parked and pathlib.Path(live_dir).exists():
                    try:
                        pathlib.Path(live_dir).rename(parked)
                    except OSError:
                        logger.exception(
                            "Could not re-park profile data for %r", name
                        )
                logger.exception("Could not rebuild trashed profile %r", name)
                return False, f"Could not restore the profile record: {e}"
            self.save_profiles()
        logger.info("Restored profile from trash: %s", name)
        return True, ""

    @staticmethod
    def destroy_trashed_material(material_path: str) -> None:
        """Destroy a trashed profile's parked data dir, for good. Called on
        permanent deletion, retention expiry and the panic wipe — the three
        paths that are genuinely irreversible."""
        if not material_path:
            return
        base = os.path.realpath(trash_data_root())
        target = os.path.realpath(material_path)
        # Never rmtree a path outside the park area, whatever a hand-edited
        # trash.json claims. The trash must not become a path by which a delete
        # escapes into arbitrary filesystem locations. Parked dirs live under
        # trash_data_root() only, so that — not DATA_DIR — is the containment
        # root; a LIVE profile's data dir is deliberately NOT deletable here.
        if os.path.commonpath([base, target]) != base or target == base:
            logger.warning(
                "Refusing to delete trashed material outside the trash area: %s",
                material_path,
            )
            return
        shutil.rmtree(material_path, ignore_errors=True)

    @staticmethod
    def _remove_window_entry(name: str) -> None:
        # Drop the Linux desktop entry (embeds the profile name in cleartext) so
        # a delete/wipe leaves no forensic trace (audit6 LOW c). No-op elsewhere.
        try:
            from ..browser.window_entry import remove_window_entry

            remove_window_entry(name)
        except Exception:
            pass

    def wipe_all_profiles(self) -> int:
        """Delete EVERY profile and its data in one pass — a panic wipe for an
        instant clean-out. Genuinely irreversible, unlike delete_profile: each
        profile's data dir is rmtree'd, profiles.json is emptied, and the trash
        is PURGED — the wipe bypasses the trash entirely and destroys whatever is
        already in it, so nothing survives it in a recoverable form. Returns how
        many profiles were removed. The UI gates this behind a typed
        confirmation whose "this cannot be undone" is therefore true."""
        for name in list(self.profiles.keys()):
            self._stop_if_running(name)
        with self._lock:
            names = list(self.profiles.keys())
            for name in names:
                shutil.rmtree(self._data_path(name), ignore_errors=True)
                self._remove_window_entry(name)
            self.profiles.clear()
            self.save_profiles()
        # Purge the trash as part of the wipe. A wipe that quietly parked fifty
        # logged-in profiles in a recoverable store would be the interface
        # claiming a protection the code does not deliver.
        self._purge_trash_for_wipe()
        if names:
            logger.info("Wiped all %d profiles", len(names))
        return len(names)

    def _purge_trash_for_wipe(self) -> None:
        try:
            from ..trash.service import destroy_entry

            for entry in self._trash().clear():
                destroy_entry(entry, self)
        except Exception:
            logger.exception("Could not purge the trash during the wipe")

    def list_profiles(self) -> list[Profile]:
        with self._lock:
            return list(self.profiles.values())

    def export_profile(
        self,
        name: str,
        export_path: str,
        include_data: bool = True,
    ) -> tuple[bool, str]:
        if name not in self.profiles:
            return False, "Profile not found"
        # Stop a live browser before copying its data dir — the Cookies SQLite
        # (+ -wal/-journal) is copied live otherwise, giving a torn/corrupt copy
        # in the export. delete/update already stop first (audit5 LOW).
        if include_data:
            self._stop_if_running(name)
        return export_to_zip(
            self.profiles[name],
            self._data_path(name),
            export_path,
            include_data,
        )

    def import_profile(
        self,
        zip_path: str,
        overwrite: bool = False,
    ) -> tuple[bool, str]:
        # Check the name collision BEFORE extracting — import_from_zip writes the
        # archive's data over DATA_DIR/<name>, so doing it first (as before) let a
        # non-overwrite import clobber an existing profile's data and only THEN
        # report "already exists". Peek the name, gate under the lock, then extract.
        peek_ok, peeked = peek_profile_name(zip_path)
        if not peek_ok:
            return False, peeked
        with self._lock:
            if peeked in self.profiles and not overwrite:
                return False, f"Profile '{peeked}' already exists"

            success, result = import_from_zip(zip_path, DATA_DIR)
            if not success:
                return False, result

            profile = result
            # Import is a door into the model, so it crosses the coherence rules
            # (coherence.py) like every other one — but it crosses them by
            # NORMALISING, not by refusing.
            #
            # The choice, stated deliberately: an archive is closer to an
            # already-stored legacy record than to a fresh REST request. It was
            # written by an older build, possibly before these rules existed, and
            # the operator importing it is recovering a profile rather than
            # composing one. Refusing would make those archives permanently
            # unimportable — the stranding the ticket forbids — and it would
            # refuse at the one moment the operator has no way to edit the record
            # into shape. So the incoherent pair is reconciled the same way a
            # stored one is at launch: fall back to the engine that HONORS
            # os_type, which makes the imported record match the machine it will
            # actually present. The record lands coherent; nothing is lost but a
            # claim that was never true.
            resolved = coherent_engine(profile.os_type, profile.engine)
            if resolved != normalize_engine(profile.engine):
                logger.warning(
                    "Imported profile %r carried an incoherent os_type/engine "
                    "pair (%s/%s); stored as %s. Reason: %s",
                    profile.name,
                    profile.os_type,
                    profile.engine,
                    resolved,
                    coherence_error(profile.os_type, profile.engine),
                )
            profile.engine = resolved
            self.profiles[profile.name] = profile
            self.save_profiles()
        logger.info("Registered imported profile: %s", profile.name)
        return True, profile.name
