#: OUR name for the Chromium engine, shown to the OPERATOR only.
#:
#: ⚠️ THIS NAME MUST NEVER REACH ANYTHING A WEB PAGE CAN READ (PS-224).
#:
#: To a site, the browser presents as Chrome and nothing else — that is what
#: ``--fingerprint-brand=Chrome`` is for (``services/browser/process.py`` and
#: ``services/verify/chromium_tier.py``). A product-specific string appearing in
#: a user agent, a brand list, or any JS-reachable surface is a UNIQUE MARKER
#: identifying every one of our users, which is the exact opposite of what this
#: product exists to do. So this constant is imported by the UI layer and by
#: nothing on the launch path: ``src/services/browser/`` and
#: ``src/services/engine/`` must never import ``core.strings``, and a test pins
#: that (``tests/test_ps224_engine_name.py``).
#:
#: IT IS A DISPLAY VALUE, NEVER A STORED ONE. The persisted engine key is
#: ``"chromium"`` (``models/profile.py:137``, ``verify/snapshot.py``,
#: ``browser/automation_channel.py``) and is untouched by this name, so an
#: existing installation's profiles keep resolving after an upgrade. The
#: dropdown option that displays this string still carries ``key="chromium"``.
#:
#: IT IS NOT THE ARTIFACT'S NAME EITHER. ``fpchrome.AppImage``
#: (``core/platform.py:31``) is the UPSTREAM build's filename, resolved by the
#: platform layer to find and launch the downloaded binary; renaming it breaks
#: the download and the launch. It becomes ours only once we ship a binary we
#: built ourselves.
CHROMIUM_ENGINE_NAME = "Personium"

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
    # PS-273 — bulk create used to report "created 45, skipped 5" and close the
    # dialog, so the operator could not find out WHICH five or why. These are
    # modelled on `delete_profile_failed`: the per-item line names the item and
    # explains the refusal, rather than contributing to an integer.
    #
    # NOTE ON SEVERITY, deliberately: the per-name line below carries no new
    # severity token, exactly as the aggregate line and "Created: {name}" carry
    # none — `log_console.severity()` classifies all three as SEV_IDLE and is
    # not touched here. The interpolated {reason} is data, not a token: it can
    # in principle contain "fail"/"error" and would then read as SEV_FAIL,
    # which is honest for a refusal and is not a change to the classifier.
    # The name LEADS, deliberately. `log_console.parse_event` HOISTS a known
    # profile name out of the prose into its own column, so "Not created:
    # {name} - {reason}" renders as "Not created: - {reason}" with a dangling
    # separator where the name was. (The shipped `delete_profile_failed` has
    # the same artifact — "Could not delete: its data ..." — so this is not a
    # deviation from the precedent, it is the precedent's rough edge avoided.)
    # Leading with the name reads correctly BOTH ways: hoisted it becomes the
    # profile column beside "not created: {reason}", and un-hoisted (an
    # invalid name is not in the roster) it reads "bad/name not created:
    # {reason}".
    "bulk_create_not_created": "{name} not created: {reason}",
    # The bulk lane's already-exists reason, deliberately NOT `profile_exists`.
    #
    # MEASURED, not stylistic: `log_console.severity()` substring-matches
    # "ready", and "already" CONTAINS it — so "Profile already exists!" on a
    # log line classifies as SEV_OK and paints the GREEN SUCCESS DOT next to a
    # refusal. `profile_exists` never hit this because the single-create lane
    # renders it into a dialog field, which no classifier reads; this is the
    # first time that string would reach the Activity Log.
    #
    # Changing severity() is explicitly out of scope for PS-273 (it would
    # re-classify every existing line carrying "already"), so the wording
    # avoids the token instead — and it is the better line anyway, on the
    # `delete_profile_failed` model: it says what happened to the EXISTING
    # profile, which is the operator's real question.
    "bulk_create_exists": (
        "a profile with that name exists - the existing one was left unchanged"
    ),
    # The dialog's own message. It leads with what ALREADY HAPPENED, because
    # the dialog now stays open on a partial success and the operator's first
    # question is whether the 45 that worked need re-submitting. They do not.
    "bulk_create_partial": (
        "Created {created} profile{plural} - already saved, no need to submit "
        "them again. {skipped} name{skipped_plural} not created:"
    ),
    "bulk_create_none": "No profiles created. {skipped} name{skipped_plural} refused:",
    "bulk_create_refusal_line": "  - {name}: {reason}",
    "bulk_create_more": "  ... and {count} more refusal{plural} - see the Activity Log",
    # The fallback reason, for a skipped name that arrived without one.
    #
    # Deliberately NOT `get_string("error")` ("Error"): this text is
    # interpolated into a line that reaches the Activity Log, and
    # `log_console.severity()` matches "error", so the bare word would paint
    # the RED FAILURE dot — a louder outcome than every other line this lane
    # writes, on the one case where the lane knows LESS than usual rather than
    # more. `bulk_create` writes `skipped` and `reasons` together through
    # `_refuse`, so this is unreachable today and pinned by
    # test_bulk_create_every_skipped_name_is_explained; it exists so a future
    # caller that skips a name without a reason still gets a line, and that
    # line reads as the idle record the rest of the batch does.
    "bulk_create_no_reason": "no reason was recorded",
    # Repeats inside the paste are dropped BEFORE the loop, so they appear in
    # neither list and `created + skipped` is fewer than the rows pasted. Said
    # out loud rather than left as an unexplained arithmetic gap.
    #
    # The DURABLE half, one line per repeated name, on the same model as the
    # refusal lines above and the bulk-DELETE lane: it is written whether or
    # not anything was refused, because a clean paste that silently made two
    # profiles out of three rows is exactly the unexplained arithmetic — and
    # it is what lets the inline line below be capped without losing anything.
    # No severity token: this is an ordinary batch record, not a failure.
    #
    # TWO STRINGS, NOT ONE, and the split is the point (PR #209 review round
    # 2). The repeat record is a property of the PASTE — it is computed from
    # the text before `bulk_create` runs — but "created once" is a claim about
    # the OUTCOME, and the two are independent: a name can be listed twice and
    # created zero times. Writing the creating wording for a REFUSED repeat
    # produced a durable line saying `bad/name` — a string
    # `validate_profile_name` refuses precisely because it can never be a
    # profile — had been created, sitting one row below the refusal line that
    # said it had not. In a wholly-refused batch (one bad os_type refuses
    # EVERY name, the case this ticket singles out) that was the majority of
    # what the log held: 200 false "created once" lines against zero profiles.
    #
    # The repeat is still worth recording when the name was refused — the
    # operator did type it twice — so the line is kept and the OUTCOME half of
    # the wording is corrected, rather than the line being dropped. Neither
    # wording carries a severity token ("refused" is a SEV_FAIL substring, so
    # the refused variant says "not created", matching `bulk_create_not_created`).
    "bulk_create_repeat_logged": "{name} was listed more than once - created once",
    "bulk_create_repeat_refused_logged": (
        "{name} was listed more than once - not created"
    ),
    # The INLINE half, capped like the refusal list for the same reason: this
    # renders into a 460px dialog above the controls the operator needs to
    # correct the paste with.
    #
    # "attempted once", not "entered once": this ONE line covers a mixed set —
    # some repeated names were created, some were refused (their reasons are
    # in the refusal lines directly above it) — so it must not assert an
    # outcome for any of them. What is true of every name in the list, whatever
    # happened next, is that the batch tried it once rather than twice. The
    # per-name Activity Log lines carry the outcome; this line carries the
    # arithmetic. Same correction as `bulk_create_repeat_logged` above, one
    # level softer, applied so the two surfaces cannot disagree.
    "bulk_create_repeats": (
        "  ({count} repeated name{plural} in the paste {was} attempted once: {names})"
    ),
    "bulk_create_repeats_more": (
        "  ... and {count} more repeated name{plural} - see the Activity Log"
    ),
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
