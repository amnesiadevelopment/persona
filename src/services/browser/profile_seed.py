import json
import pathlib

from ...core.logging import get_logger

logger = get_logger("browser.profile_seed")

# Search engines offered when creating a profile. Each maps to the
# template_url_data shape Chromium stores in Default/Preferences. Seeded only
# into a fresh profile (before first run) so Chromium adopts it as default.
# prepopulate_id is 0 for every engine: a non-zero id makes Chromium merge the
# entry with its built-in prepopulated engine, and in ungoogled-chromium the
# built-in Google entry is stripped, leaving a broken "http://{searchTerms}".
# id 0 = fully custom engine, so Chromium uses our url verbatim.
SEARCH_ENGINES: dict[str, dict] = {
    "duckduckgo": {
        "short_name": "DuckDuckGo",
        "keyword": "duckduckgo.com",
        "url": "https://duckduckgo.com/?q={searchTerms}",
        "suggestions_url": "https://duckduckgo.com/ac/?q={searchTerms}&type=list",
        "favicon_url": "https://duckduckgo.com/favicon.ico",
        "prepopulate_id": 0,
    },
    "google": {
        "short_name": "Google",
        "keyword": "google.com",
        "url": "https://www.google.com/search?q={searchTerms}",
        "suggestions_url": "https://www.google.com/complete/search?client=chrome&q={searchTerms}",
        "favicon_url": "https://www.google.com/favicon.ico",
        "prepopulate_id": 0,
    },
    "brave": {
        "short_name": "Brave",
        "keyword": "search.brave.com",
        "url": "https://search.brave.com/search?q={searchTerms}",
        "suggestions_url": "https://search.brave.com/api/suggest?q={searchTerms}",
        "favicon_url": "https://search.brave.com/favicon.ico",
        "prepopulate_id": 0,
    },
}

DEFAULT_SEARCH_ENGINE = "duckduckgo"

SEARCH_ENGINE_LABELS = {
    "duckduckgo": "DuckDuckGo",
    "google": "Google",
    "brave": "Brave",
}


def _search_template(search_engine: str) -> dict:
    return dict(SEARCH_ENGINES.get(search_engine, SEARCH_ENGINES[DEFAULT_SEARCH_ENGINE]))


def _default_prefs(search_engine: str) -> dict:
    template = _search_template(search_engine)
    template["safe_for_autoreplace"] = False
    return {
        "extensions": {"theme": {"id": "", "system_theme": 0}},  # Classic theme
        "browser": {"theme": {"color_scheme2": 2}},  # 2 = Dark
        "default_search_provider": {"enabled": True},
        "default_search_provider_data": {"template_url_data": template},
    }


def seed_profile_prefs(
    profile_dir: str, search_engine: str = DEFAULT_SEARCH_ENGINE
) -> bool:
    """Seed a fresh profile's preferences with GTK theme, dark mode and the
    chosen default search engine. No-op once the profile has been launched
    (Preferences exists), so it never overrides settings changed later.

    Returns True when a seed file was written, False when skipped.
    """
    default_dir = pathlib.Path(profile_dir) / "Default"
    prefs_path = default_dir / "Preferences"
    if prefs_path.exists():
        return False
    default_dir.mkdir(parents=True, exist_ok=True)
    try:
        with prefs_path.open("w", encoding="utf-8") as f:
            json.dump(_default_prefs(search_engine), f)
        logger.info(
            "Seeded preferences for new profile at %s (search=%s)",
            profile_dir,
            search_engine,
        )
        return True
    except Exception as e:
        logger.exception("Error seeding profile preferences: %s", e)
        return False
