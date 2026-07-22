"""Per-version "what's new" notes shown once after an update (#215).

Keep the newest version first. Each entry is a short, user-facing bullet list —
not a git log. When cutting a release, add the new version's highlights here.
"""

CHANGELOG: dict[str, list[str]] = {
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
