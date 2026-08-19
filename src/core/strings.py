STRINGS = {
    "app_name": "persona",
    "app_subtitle": "persona@host:~$",
    "your_profiles": "Your Profiles",
    "no_profiles_yet": "No profiles yet",
    "create_profile_hint": "Create a new profile to get started",
    "new_profile": "+ NEW PROFILE",
    "total_profiles": "Total Profiles: {count}",
    "create_new_profile": "Create New Profile",
    "edit_profile": "Edit Profile",
    "add_profile": "Add Profile",
    "profile_name": "Profile Name",
    "proxy_placeholder": "Proxy (user:pass@ip:port) [Optional]",
    "operating_system": "Operating System",
    "create_profile_btn": "Create Profile",
    "save_changes": "Save Changes",
    "check_proxy": "Check Proxy",
    "launch": "LAUNCH",
    "close": "CLOSE",
    "loading": "LOADING...",
    "previous": "Previous",
    "next": "Next",
    "page_of": "Page {current} of {total}",
    "activity_log": "ACTIVITY LOG",
    "expand": "Expand",
    "minimize": "Minimize",
    "activity_log_fullscreen": "Activity Log - Full Screen",
    "proxy_active": "PROXY ACTIVE",
    "direct_connection": "DIRECT CONNECTION",
    "confirm_delete": "Confirm",
    "confirm_delete_msg": "Delete profile '{name}'?",
    "error": "Error",
    "profile_exists": "Profile already exists!",
    "update_failed": "Could not update profile (Name might exist)",
    "created_profile": "Created: {name}",
    "deleted_profile": "Deleted: {name}",
    "delete_profile_failed": "Could not delete {name}: its data could not be moved to the trash. The profile is unchanged.",
    "updated_profile": "Updated: {old} -> {new}",
    "launching_profile": "Launching {name}...",
    "stopping_profile": "Stopping {name}...",
    "starting_profile": "Starting {name} ({os})...",
    "browser_started": "Browser started!",
    "session_ended": "Session ended: {name}",
    "error_starting": "Error starting process: {error}",
    "export_profile": "Export Profile",
    "import_profile": "Import Profile",
    "export_success": "Profile exported successfully",
    "import_success": "Profile imported successfully",
    "export_error": "Error exporting profile: {error}",
    "import_error": "Error importing profile: {error}",
    "validation_empty_name": "Profile name cannot be empty",
    "validation_name_too_long": "Profile name must be 64 characters or less",
    "validation_invalid_chars": "Name contains invalid characters: {chars}",
    "validation_name_spaces": "Name cannot start or end with spaces",
    "validation_reserved_name": "'{name}' is a reserved system name",
    "validation_invalid_proxy": "Invalid proxy format. Use: [scheme://][user:pass@]host:port",
    "validation_invalid_port": "Port must be between 1 and 65535",
    "proxy_checking": "Checking proxy...",
    "proxy_check_success": "Proxy working.",
    "proxy_check_failed": "Proxy check failed: {reason}",
    "proxy_check_skipped": "Proxy check skipped (aiohttp not installed)",
}


def get_string(key: str, **kwargs: object) -> str:
    text = STRINGS.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text


def app_subtitle() -> str:
    """The terminal-style subtitle under the logo, tagged with the current OS so
    a shared screenshot instantly shows which persona (Windows/mac/Linux) it is:
    persona@windows / persona@mac / persona@linux."""
    from . import platform as _platform

    if _platform.IS_WINDOWS:
        host = "windows"
    elif _platform.IS_MACOS:
        host = "mac"
    elif _platform.IS_LINUX:
        host = "linux"
    else:
        host = "host"
    return f"persona@{host}:~$"
