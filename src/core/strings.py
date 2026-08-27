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
    # PS-223 — a browser left running by a PREVIOUS persona. The wording names
    # the profile and says the browser is still open, because the alternative
    # the user meets today is chromium's own refusal ("Failed to create a
    # ProcessSingleton for your profile directory", exit 21) — engine-speak
    # that names no profile and offers no action.
    "already_running_survivor": (
        "{name} is already open in a browser from a previous persona session. "
        "Close that window, or use [ close ] on this card to end it."
    ),
    "survivors_found": (
        "{count} browser session(s) from a previous persona are still running: "
        "{names}. They were not closed when persona last exited."
    ),
    # The INDETERMINATE case. Said out loud rather than silently swallowed: the
    # launch is ALLOWED (a false "already running" would lock the user out of
    # their own profile with no way back), so the user is told that the check
    # could not be made rather than being left to think it passed.
    "survivors_unknown": (
        "Could not check whether {count} recorded session(s) are still running "
        "({names}). Launching is still allowed — check for an open browser "
        "window first."
    ),
    "survivor_closed": "Closed the leftover browser for {name}.",
    "survivor_close_failed": (
        "Could not confirm the leftover browser for {name} closed. "
        "Check for an open window before launching again."
    ),
    # The confirm-before-close dialog (PS-223 outcome 2).
    "confirm_exit_title": "{count} profile(s) still open",
    "confirm_exit_body": (
        "Closing persona will close the browser(s) for: {names}.\n\n"
        "Anything unsaved in those windows will be lost."
    ),
    "confirm_exit_close": "[ close them and exit ]",
    "confirm_exit_cancel": "[ cancel ]",
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
