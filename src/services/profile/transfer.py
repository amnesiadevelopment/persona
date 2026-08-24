import json
import os
import pathlib
import shutil
import tempfile
import zipfile
from datetime import datetime

from ...core.logging import get_logger
from ...models.hardware_generation import CURRENT_HARDWARE_GENERATION
from ...models.profile import Profile
from ...utils.validation import validate_profile_name

logger = get_logger("profile.transfer")

# A shared profile zip is UNTRUSTED input. Cap the total uncompressed size and
# the entry count so a zip-bomb (a tiny archive that inflates to GBs / millions
# of files) can't exhaust the disk during import.
_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_MAX_ENTRIES = 50_000

# Directories that live inside the profile dir but must never ride along in an
# exported/shared profile zip. Two entries, for two different reasons:
#
# * ``.persona-mtls`` — the mTLS terminator drops the client cert + UNENCRYPTED
#   private key here. A secrecy exclusion.
# * ``.persona-tmp`` — the browser child's scratch directory (PS-129, see
#   env_policy.browser_child_tmpdir). A BULK exclusion, and it is load-bearing:
#   pinning the child's TMPDIR inside the profile is what makes a crash-stranded
#   temp file wipeable, but it also puts the engine's ~714MB AppImage
#   self-extraction under the profile dir — MEASURED, and it survives a CLEAN
#   exit, not just a crash. Without this line every profile export would grow by
#   that much. Scratch is per-launch state that no importer wants and the child
#   recreates on demand, so excluding it loses nothing.
_EXPORT_EXCLUDE_DIRS = {".persona-mtls", ".persona-tmp"}


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

            # That automatic round-trip is a feature, but it means an UNTRUSTED
            # archive also gets to choose the profile's fingerprint seed — the
            # integer the whole presented machine derives from. Two consequences,
            # both reachable with a hand-edited profile.json:
            #
            #  * a non-integer sails in and is stored verbatim, then hard-crashes
            #    the launch path the first time something does arithmetic on it
            #    (touch_points in process.py: "not all arguments converted during
            #    string formatting");
            #  * an attacker-chosen value pins the imported profile onto ANOTHER
            #    profile's exact seed, i.e. deliberate cross-profile linkage.
            #
            # So the seed is validated here exactly like the name above: keep it
            # only if it is a genuine in-range crc32 integer, else DROP the key.
            # Dropping is the right failure — it lands the profile on the
            # crc32(name) fallback rather than refusing an otherwise-importable
            # archive, and an operator recovering an old export is not punished
            # for a field their build never wrote. bool is excluded on purpose:
            # it is an int subclass, and True would silently become seed 1.
            #
            # This is the TYPE half only. Whether the (now valid) seed collides
            # with a profile already on this machine is the REGISTRY's question,
            # and import_profile answers it — this function has no view of the
            # live profiles.
            if "fingerprint_seed_value" in known:
                seed = known["fingerprint_seed_value"]
                if seed is not None and not (
                    isinstance(seed, int)
                    and not isinstance(seed, bool)
                    and 0 <= seed <= 0xFFFFFFFF
                ):
                    logger.warning(
                        "Profile archive %r carried an invalid fingerprint seed "
                        "(%r); dropping it, the profile will derive one from its "
                        "name.",
                        name, seed,
                    )
                    known.pop("fingerprint_seed_value")

            # Same automatic round-trip, same untrusted source, for the hardware
            # generation — and it needs the same TYPE guard, for a reason that is
            # NOT the seed's. This value is not an identity to be protected from
            # collision; it selects which slice of each hardware list the profile
            # may be picked from, so a malformed one has a different failure mode:
            # a non-integer (or a float, or a string "3") reaches the `since <=
            # generation` comparison and raises mid-launch on some Python paths
            # while silently comparing wrong in the baked JS, and an absurdly
            # LARGE value is not an error at all — it just pins the profile to
            # every future generation forever, which is a slow re-roll rather
            # than a crash.
            #
            # Model.hardware_generation already normalizes on READ (None,
            # malformed and negative all read as 0), so this guard is belt-and-
            # braces rather than the only line of defence — but dropping the key
            # at the door keeps a nonsense value from being PERSISTED back out in
            # the next save and travelling on to the next machine.
            #
            # Dropping lands the profile on generation 0 — the lists as they
            # shipped, the pool every pre-generations profile sees. That is the
            # conservative direction: it can only ever show an imported profile
            # OLDER hardware than it claimed, never re-roll it onto newer.
            if "hardware_generation_value" in known:
                gen = known["hardware_generation_value"]
                if gen is not None and not (
                    isinstance(gen, int)
                    and not isinstance(gen, bool)
                    and 0 <= gen <= CURRENT_HARDWARE_GENERATION
                ):
                    logger.warning(
                        "Profile archive %r carried an invalid hardware "
                        "generation (%r); dropping it, the profile will read as "
                        "generation 0 (the hardware lists as they shipped).",
                        name, gen,
                    )
                    known.pop("hardware_generation_value")

            # Same automatic round-trip, same untrusted source, for the launch
            # provenance pair. The TYPE half only, exactly like the seed above:
            # both fields are read as strings by every consumer, so a dict or a
            # number sailing in from a hand-edited archive would be stored
            # verbatim and blow up whichever surface first formats it. Drop a
            # malformed value rather than refusing the archive — that lands the
            # profile on None, which is the field's own honest "not known".
            #
            # A WELL-FORMED value is deliberately KEPT, and that choice is worth
            # stating because the opposite is tempting. The exported data dir
            # travels with the archive, so the imported profile continues the
            # SAME identity — the build that produced that identity is genuine
            # provenance and dropping it would discard a true fact. What this
            # cannot do is VERIFY the claim: an archive is free to assert any
            # build. That is a property of profile sharing as a whole (the same
            # is true of every other field here) and not something this field
            # can settle on its own; a consumer treating an imported stamp as
            # locally observed is the thing that would be wrong.
            for _prov_key in ("last_launch_engine", "last_launch_build"):
                if _prov_key in known:
                    _prov = known[_prov_key]
                    if _prov is not None and not isinstance(_prov, str):
                        logger.warning(
                            "Profile archive %r carried a malformed %s (%r); "
                            "dropping it, the profile will read as having no "
                            "recorded launch build.",
                            name, _prov_key, _prov,
                        )
                        known.pop(_prov_key)

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
