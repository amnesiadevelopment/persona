"""Per-version "what's new" notes shown once after an update (#215).

Keep the newest version first. Each entry is a short, user-facing bullet list —
not a git log. When cutting a release, add the new version's highlights here.
"""

CHANGELOG: dict[str, list[str]] = {
    "2.7.0": [
        "Google Sheets works fully through a proxy in Chrome profiles again — the "
        "date-cell calendar picker and custom-currency dialog open, and the sheet "
        "no longer sticks on \"Working\". A persona extension was clashing with "
        "Google's own scripts and stopping the sheet's calculator from loading.",
        "On Linux, persona reliably reopens after an auto-update instead of "
        "closing and never coming back.",
        "Firefox profiles open faster on the first launch — no more waiting out "
        "two dead retries.",
        "Windows code-only updates are quick again (seconds, not a full "
        "reinstall).",
    ],
    "2.6.5": [
        "Google Sheets overlays work in Chrome profiles: the date-cell calendar "
        "picker and the custom-currency dialog open again, even when persona's "
        "window isn't in front. They were frozen whenever another window covered "
        "the browser.",
        "The Firefox engine installs and launches on Windows again — a version "
        "mismatch in the last build left it stuck on \"not ready\" and "
        "re-downloading forever.",
        "After an update you reliably see the what's-new notes, never the "
        "first-run welcome screen again.",
        "After an update persona reopens once and stays open — no more "
        "close-reopen-close flicker.",
        "Fewer false-red lines in the Activity Log (a benign VM graphics "
        "message no longer shows up).",
    ],
    "2.6.4": [
        "The Firefox engine is recognised once it's downloaded — no more stuck "
        "on \"not ready\" or re-downloading the same build over and over.",
        "Google Sheets menus and overlays (the date calendar, custom currency) "
        "paint promptly again through a proxy — a rendering flag from the last "
        "release was making them stall.",
        "Clicking the engine row while it's already downloading no longer starts "
        "a second download from scratch.",
        "Fewer false-red GPU lines in the Activity Log.",
    ],
    "2.6.3": [
        "Google Sheets works through a proxy again — the Format menu, the date "
        "calendar and custom-currency all open, and \"Working\" no longer sticks. "
        "Chrome profiles paint their menus/overlays properly under the VM's "
        "software renderer.",
        "Firefox profiles launch reliably: the engine now re-downloads itself if "
        "an earlier download was interrupted, instead of getting stuck \"not "
        "ready\".",
        "After an update you see the what's-new notes, not the first-run welcome "
        "screen again.",
        "The startup scan fills the window even if you move or resize it while it "
        "loads.",
        "Engine and app downloads over a slow connection keep resuming and can be "
        "nudged by clicking the version line.",
        "Renaming a profile keeps it in place in the list.",
        "Fewer false-red lines in the Activity Log.",
    ],
    "2.6.2": [
        "Firefox no longer leaks the host system language — the browser's "
        "internal date/number formatting now matches the profile locale, so a "
        "scanner sees one consistent language.",
        "Google Sheets is snappier through a proxy — large documents load "
        "without stalling on \"Working\".",
        "The Firefox engine repairs itself: an interrupted first download no "
        "longer leaves it half-installed and unable to open — it re-downloads "
        "automatically.",
        "Renaming a profile keeps it in place in the list instead of moving it "
        "to the end.",
        "Windows updates that only change app code now install in seconds "
        "instead of running the full installer every time.",
        "The screen-resolution picker now notes how your monitor's display "
        "scale affects the reported resolution.",
        "Fewer false-red lines in the Activity Log.",
    ],
    "2.6.1": [
        "Google Sheets loads fully through a proxy — the calendar date-picker "
        "opens, custom currency and dates compute, no more permanent \"Working\".",
        "Firefox windows render at the display's real pixel ratio, so pages are "
        "sharp and nothing is shifted or over-zoomed.",
        "Fresh Firefox profiles open in a few seconds on Windows too, with their "
        "bookmarks and dark theme from the first launch.",
        "Closing a profile with its window's X reliably stops it on macOS.",
        "Profiles with a non-Latin name (e.g. Cyrillic) create without an error.",
        "Fewer false-red lines in the Activity Log.",
    ],
    "2.6.0": [
        "Google Sheets and other Google apps now work through a proxy — no more "
        "stuck on \"Working\".",
        "Firefox profiles report a browser language that matches the proxy's "
        "country, so a scanner no longer flags a location mismatch.",
        "Firefox windows render cleanly at any chosen resolution — no more "
        "skewed or shifted pages.",
        "Fresh Firefox profiles open much faster on the first launch.",
        "Windows updates are far quicker: only the changed app code is fetched "
        "and swapped in.",
        "Paste a proxy as a single line and it fills every field — on every OS.",
        "Closing a profile with its window's X now stops it on macOS too.",
    ],
    "2.5.2": [
        "Fresh Firefox profiles now open in seconds with their bookmarks on the "
        "first launch, on every OS.",
        "Google Sheets and other live sites stay responsive through a proxy "
        "(no more stuck on \"Working\").",
        "The engine keeps only the current build — old versions are cleaned up.",
        "Fewer false-red lines in the Activity Log.",
    ],
}


def notes_for(version: str) -> list[str]:
    """The what's-new bullets for a version, or [] if none are recorded."""
    return list(CHANGELOG.get(version, []))
