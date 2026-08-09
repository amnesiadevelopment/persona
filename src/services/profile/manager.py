import json
import os
import pathlib
import shutil
import threading
import time
from collections.abc import Callable

from ...core.config import DATA_DIR, PROFILES_FILE
from ...core.logging import get_logger
from ...models.profile import Profile
from ...utils.atomic import atomic_write_json
from ...utils.validation import validate_profile_name
from .transfer import export_to_zip, import_from_zip, peek_profile_name

logger = get_logger("profile.manager")


class InvalidProfileName(ValueError):
    """A profile name that fails validation or would escape the data dir."""


class ProfileManager:
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
                            "tags": p_data.get("tags", []),
                            "notes": p_data.get("notes", ""),
                            "ai_control": p_data.get("ai_control", False),
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

    def _quarantine_profiles_file(self) -> None:
        # An unreadable profiles.json still holds every profile the user has;
        # move it aside so the next save_profiles() can't overwrite it with
        # the empty in-memory dict.
        backup = f"{PROFILES_FILE}.corrupt-{int(time.time())}"
        try:
            pathlib.Path(PROFILES_FILE).rename(backup)
            logger.warning("Moved unreadable profiles file to %s", backup)
        except OSError:
            self._save_blocked = True
            logger.exception(
                "Could not back up %s; profile saving disabled to avoid "
                "overwriting it",
                PROFILES_FILE,
            )

    def save_profiles(self) -> None:
        if self._save_blocked:
            logger.error(
                "Not saving: profiles file failed to load and could not be "
                "backed up"
            )
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
    ) -> bool:
        # Validate up front so an invalid/traversal name is rejected before it's
        # registered — import and the MCP tool used to reach here unchecked.
        valid, _ = validate_profile_name(name)
        if not valid:
            return False
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
                profile.certificate = new_certificate or None
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
        if name not in self.profiles:
            return False
        # Stop a live browser BEFORE rmtree (outside our lock — stop is blocking)
        # so we never delete the data dir out from under a running engine.
        self._stop_if_running(name)
        with self._lock:
            if name in self.profiles:
                del self.profiles[name]
                self.save_profiles()
                shutil.rmtree(self._data_path(name), ignore_errors=True)
                self._remove_window_entry(name)
                logger.info("Deleted profile: %s", name)
                return True
        return False

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
        instant clean-out. Irreversible, like delete_profile: each profile's data
        dir is rmtree'd and profiles.json is emptied. Returns how many profiles
        were removed. The UI gates this behind a typed confirmation."""
        for name in list(self.profiles.keys()):
            self._stop_if_running(name)
        with self._lock:
            names = list(self.profiles.keys())
            for name in names:
                shutil.rmtree(self._data_path(name), ignore_errors=True)
                self._remove_window_entry(name)
            self.profiles.clear()
            self.save_profiles()
        if names:
            logger.info("Wiped all %d profiles", len(names))
        return len(names)

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
            self.profiles[profile.name] = profile
            self.save_profiles()
        logger.info("Registered imported profile: %s", profile.name)
        return True, profile.name
