import json
import os
import pathlib
import zipfile
from datetime import datetime

from ...core.logging import get_logger
from ...models.profile import Profile
from ...utils.validation import validate_profile_name

logger = get_logger("profile.transfer")

# The mTLS terminator drops the client cert + UNENCRYPTED private key here inside
# the profile dir; never let it ride along in an exported/shared profile zip.
_EXPORT_EXCLUDE_DIRS = {".persona-mtls"}


def _is_within(base: str, target: str) -> bool:
    base_r = os.path.realpath(base)
    target_r = os.path.realpath(target)
    return os.path.commonpath([base_r, target_r]) == base_r


def peek_profile_name(zip_path: str) -> tuple[bool, str]:
    """Read the archive's profile name WITHOUT extracting any data, so a caller
    can gate on a name collision before import_from_zip writes over an existing
    profile's dir. Returns (True, name) or (False, error)."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            if "profile.json" not in zipf.namelist():
                return False, "Invalid profile archive (missing profile.json)"
            name = json.loads(zipf.read("profile.json")).get("name")
        if not name:
            return False, "Invalid profile data (missing name)"
        valid, msg = validate_profile_name(name)
        if not valid:
            return False, f"Invalid profile name in archive: {msg}"
        return True, name
    except zipfile.BadZipFile:
        return False, "Invalid zip file"
    except Exception as e:
        return False, str(e)


def export_to_zip(
    profile: Profile,
    profile_data_dir: str,
    export_path: str,
    include_data: bool = True,
) -> tuple[bool, str]:
    """Create a ZIP archive of a profile and optionally its browser data."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"{profile.name}_{timestamp}.zip"
        zip_path = os.path.join(export_path, zip_filename)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr("profile.json", json.dumps(profile.to_dict(), indent=2))

            if include_data and pathlib.Path(profile_data_dir).exists():
                for root, dirs, files in os.walk(profile_data_dir):
                    # Prune the mTLS dir so the cleartext client key never leaves
                    # in a shared profile.
                    dirs[:] = [d for d in dirs if d not in _EXPORT_EXCLUDE_DIRS]
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.join(
                            "data",
                            os.path.relpath(file_path, profile_data_dir),
                        )
                        zipf.write(file_path, arcname)

        logger.info("Exported profile %s to %s", profile.name, zip_path)
        return True, zip_path
    except Exception as e:
        logger.exception("Error exporting profile %s: %s", profile.name, e)
        return False, str(e)


def import_from_zip(
    zip_path: str,
    data_dir: str,
) -> tuple[bool, Profile | str]:
    """Extract a profile from a ZIP archive.

    Returns (True, Profile) on success or (False, error_message) on failure.
    The caller is responsible for registering the profile in the manager.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            if "profile.json" not in zipf.namelist():
                return False, "Invalid profile archive (missing profile.json)"

            profile_data = json.loads(zipf.read("profile.json"))
            name = profile_data.get("name")

            if not name:
                return False, "Invalid profile data (missing name)"

            # The archive is UNTRUSTED input (profile sharing is a feature). Its
            # name becomes a filesystem path, so validate it exactly like the UI/
            # API create path — an absolute or '../' name would otherwise escape
            # DATA_DIR into e.g. the Startup folder.
            valid, msg = validate_profile_name(name)
            if not valid:
                return False, f"Invalid profile name in archive: {msg}"

            profile = Profile(
                name=name,
                proxy=profile_data.get("proxy"),
                os_type=profile_data.get("os_type", "windows"),
                device_type=profile_data.get("device_type", "desktop"),
                engine=profile_data.get("engine", "chromium"),
                resolution=profile_data.get("resolution", "auto"),
                search_engine=profile_data.get("search_engine", "duckduckgo"),
                bookmark_pool=profile_data.get("bookmark_pool"),
                bookmarks=profile_data.get("bookmarks"),
                certificate=profile_data.get("certificate"),
                tags=profile_data.get("tags", []),
                notes=profile_data.get("notes", ""),
                ai_control=profile_data.get("ai_control", False),
            )

            data_files = [f for f in zipf.namelist() if f.startswith("data/")]
            if data_files:
                profile_data_dir = os.path.join(data_dir, name)
                pathlib.Path(profile_data_dir).mkdir(exist_ok=True, parents=True)

                for file in data_files:
                    if file.endswith("/"):
                        continue
                    member = os.path.relpath(file, "data")
                    target_path = os.path.join(profile_data_dir, member)
                    # Zip-slip guard: a member like '../../.../Startup/evil.bat'
                    # (or a Windows backslash/drive-letter arcname) would resolve
                    # outside the profile dir and write attacker bytes anywhere.
                    # Reject any member whose resolved path leaves profile_data_dir.
                    if not _is_within(profile_data_dir, target_path):
                        return False, f"Unsafe path in archive: {file}"
                    pathlib.Path(target_path).parent.mkdir(
                        exist_ok=True,
                        parents=True,
                    )
                    with zipf.open(file) as src, open(target_path, "wb") as dst:
                        dst.write(src.read())

            logger.info("Imported profile from zip: %s", name)
            return True, profile
    except zipfile.BadZipFile:
        return False, "Invalid zip file"
    except Exception as e:
        logger.exception("Error importing profile: %s", e)
        return False, str(e)
