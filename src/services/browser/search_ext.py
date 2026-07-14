import json
import pathlib

from .profile_seed import DEFAULT_SEARCH_ENGINE, SEARCH_ENGINES


def _search_provider(search_engine: str) -> dict:
    template = SEARCH_ENGINES.get(
        search_engine, SEARCH_ENGINES[DEFAULT_SEARCH_ENGINE]
    )
    return {
        "name": template["short_name"],
        "keyword": template["keyword"],
        "search_url": template["url"],
        "suggest_url": template["suggestions_url"],
        "favicon_url": template["favicon_url"],
        "encoding": "UTF-8",
        "is_default": True,
    }


def build_search_extension(search_engine: str, base_dir: str) -> str:
    """Generate an unpacked extension that makes the chosen engine the default
    search provider via chrome_settings_overrides.search_provider (is_default).

    On Windows and macOS Chromium enforces the default-search-engine pref as a
    tracked ("protected") preference: a value seeded into Default/Preferences
    without a matching HMAC in Secure Preferences is reset on first run, so the
    profile falls back to the prepopulated default. A settings-override
    extension is the per-profile mechanism that survives — Chromium re-applies
    the override from the loaded extension every launch, the same way a
    user-installed search extension sets the default.
    """
    ext_dir = pathlib.Path(base_dir)
    ext_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "manifest_version": 3,
        "name": "persona-search",
        "version": "1.0",
        "chrome_settings_overrides": {
            "search_provider": _search_provider(search_engine),
        },
    }
    (ext_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return str(ext_dir)
