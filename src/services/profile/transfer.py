import json
import os
import pathlib
import shutil
import tempfile
import zipfile
from datetime import datetime

from ...core.logging import get_logger
from ...models.profile import Profile
from ...utils.validation import validate_profile_name

logger = get_logger("profile.transfer")

# A shared profile zip is UNTRUSTED input. Cap the total uncompressed size and
# the entry count so a zip-bomb (a tiny archive that inflates to GBs / millions
# of files) can't exhaust the disk during import.
_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_MAX_ENTRIES = 50_000

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

            # Build from the intersection of the archive and Profile's own field
            # set (except name, validated above). Enumerating fields by hand kept
            # silently dropping fields on import — cookie_import_status was the
            # latest, a repeat of the same 12-field round-trip bug (audit5 LOW).
            # Deriving from dataclasses.fields means a new field round-trips
            # automatically; unknown keys in the archive are ignored.
            import dataclasses

            field_names = {f.name for f in dataclasses.fields(Profile)}
            known = {
                k: v for k, v in profile_data.items()
                if k in field_names and k != "name"
            }
            profile = Profile(name=name, **known)

            data_files = [f for f in zipf.namelist() if f.startswith("data/")]

            # Zip-bomb guard: reject before extracting a single byte if the
            # archive's declared uncompressed size or entry count is over the cap.
            total = sum(
                i.file_size for i in zipf.infolist() if i.filename.startswith("data/")
            )
            if total > _MAX_UNCOMPRESSED_BYTES:
                return False, "Profile archive is too large (possible zip bomb)"
            if len(data_files) > _MAX_ENTRIES:
                return False, "Profile archive has too many files (possible zip bomb)"

            if data_files:
                profile_data_dir = os.path.join(data_dir, name)
                # Extract into a sibling temp dir and move it into place only on
                # FULL success, so a partial/aborted import never leaves a
                # half-extracted, unregistered profile data dir behind.
                pathlib.Path(data_dir).mkdir(exist_ok=True, parents=True)
                staging = tempfile.mkdtemp(
                    dir=data_dir, prefix=f".import-{name}-"
                )
                try:
                    written = 0
                    for file in data_files:
                        if file.endswith("/"):
                            continue
                        member = os.path.relpath(file, "data")
                        target_path = os.path.join(staging, member)
                        # Zip-slip guard: a member like '../../…/Startup/evil.bat'
                        # (or a Windows backslash/drive-letter arcname) resolves
                        # outside the staging dir and writes attacker bytes
                        # anywhere. Reject any member that leaves staging.
                        if not _is_within(staging, target_path):
                            return False, f"Unsafe path in archive: {file}"
                        pathlib.Path(target_path).parent.mkdir(
                            exist_ok=True, parents=True,
                        )
                        # Bounded, chunked copy so a lying local header can't
                        # inflate past the cap during the read itself.
                        with zipf.open(file) as src, open(target_path, "wb") as dst:
                            while True:
                                chunk = src.read(1024 * 1024)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > _MAX_UNCOMPRESSED_BYTES:
                                    return False, "Profile archive is too large (possible zip bomb)"
                                dst.write(chunk)
                    # Atomic-ish swap: remove any existing dir, move staging in.
                    if os.path.exists(profile_data_dir):
                        shutil.rmtree(profile_data_dir, ignore_errors=True)
                    os.replace(staging, profile_data_dir)
                    staging = None
                finally:
                    if staging is not None:
                        shutil.rmtree(staging, ignore_errors=True)

            logger.info("Imported profile from zip: %s", name)
            return True, profile
    except zipfile.BadZipFile:
        return False, "Invalid zip file"
    except Exception as e:
        logger.exception("Error importing profile: %s", e)
        return False, str(e)
