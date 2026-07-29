import json
import os
import pathlib
import shutil
import time

from ...core.config import DATA_DIR, PROFILES_FILE
from ...core.logging import get_logger
from ...models.profile import Profile
from .transfer import export_to_zip, import_from_zip

logger = get_logger("profile.manager")


class ProfileManager:
    def __init__(self) -> None:
        self.profiles: dict[str, Profile] = {}
        self._save_blocked = False
        self._load_profiles()
        pathlib.Path(DATA_DIR).mkdir(exist_ok=True, parents=True)

    def _data_path(self, name: str) -> str:
        return os.path.join(DATA_DIR, name)

    def _load_profiles(self) -> None:
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
            with pathlib.Path(PROFILES_FILE).open("w", encoding="utf-8") as f:
                json.dump(
                    {name: p.to_dict() for name, p in self.profiles.items()},
                    f,
                    indent=4,
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
    ) -> bool:
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
    ) -> bool:
        if original_name not in self.profiles:
            return False

        if new_name != original_name and new_name in self.profiles:
            return False

        profile = self.profiles[original_name]
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
        if new_ai_control is not None:
            profile.ai_control = new_ai_control

        if new_name != original_name:
            self.profiles = {
                (new_name if k == original_name else k): v
                for k, v in self.profiles.items()
            }

            old_dir = self._data_path(original_name)
            if pathlib.Path(old_dir).exists():
                pathlib.Path(old_dir).rename(self._data_path(new_name))

        self.save_profiles()
        logger.info("Updated profile: %s -> %s", original_name, new_name)
        return True

    def set_cookie_status(self, name: str, status: str) -> bool:
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
        """Remove a tag from every profile that has it. Returns count changed."""
        changed = 0
        for p in self.profiles.values():
            if tag in p.tags:
                p.tags = [x for x in p.tags if x != tag]
                changed += 1
        if changed:
            self.save_profiles()
        return changed

    def set_ai_control(self, name: str, enabled: bool) -> bool:
        p = self.profiles.get(name)
        if p is None:
            return False
        p.ai_control = enabled
        self.save_profiles()
        return True

    def delete_profile(self, name: str) -> bool:
        if name in self.profiles:
            del self.profiles[name]
            self.save_profiles()
            shutil.rmtree(self._data_path(name), ignore_errors=True)
            logger.info("Deleted profile: %s", name)
            return True
        return False

    def wipe_all_profiles(self) -> int:
        """Delete EVERY profile and its data in one pass — a panic wipe for an
        instant clean-out. Irreversible, like delete_profile: each profile's data
        dir is rmtree'd and profiles.json is emptied. Returns how many profiles
        were removed. The UI gates this behind a typed confirmation."""
        names = list(self.profiles.keys())
        for name in names:
            shutil.rmtree(self._data_path(name), ignore_errors=True)
        self.profiles.clear()
        self.save_profiles()
        if names:
            logger.info("Wiped all %d profiles", len(names))
        return len(names)

    def list_profiles(self) -> list[Profile]:
        return list(self.profiles.values())

    def export_profile(
        self,
        name: str,
        export_path: str,
        include_data: bool = True,
    ) -> tuple[bool, str]:
        if name not in self.profiles:
            return False, "Profile not found"
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
        success, result = import_from_zip(zip_path, DATA_DIR)
        if not success:
            return False, result

        profile = result
        if profile.name in self.profiles and not overwrite:
            return False, f"Profile '{profile.name}' already exists"

        self.profiles[profile.name] = profile
        self.save_profiles()
        logger.info("Registered imported profile: %s", profile.name)
        return True, profile.name
