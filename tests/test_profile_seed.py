import json

from src.services.browser.profile_seed import (
    DEFAULT_SEARCH_ENGINE,
    SEARCH_ENGINES,
    seed_profile_prefs,
)


def _prefs(tmp_path):
    return json.loads((tmp_path / "Default" / "Preferences").read_text())


def test_seed_writes_theme_for_fresh_profile(tmp_path):
    assert seed_profile_prefs(str(tmp_path)) is True
    prefs = _prefs(tmp_path)
    assert prefs["extensions"]["theme"]["system_theme"] == 0  # Classic
    assert prefs["browser"]["theme"]["color_scheme2"] == 2  # Dark


def test_seed_defaults_to_duckduckgo(tmp_path):
    seed_profile_prefs(str(tmp_path))
    prefs = _prefs(tmp_path)
    assert prefs["default_search_provider"]["enabled"] is True
    url = prefs["default_search_provider_data"]["template_url_data"]["url"]
    assert "duckduckgo.com" in url
    assert DEFAULT_SEARCH_ENGINE == "duckduckgo"


def test_seed_honours_chosen_search_engine(tmp_path):
    seed_profile_prefs(str(tmp_path), search_engine="google")
    url = _prefs(tmp_path)["default_search_provider_data"]["template_url_data"]["url"]
    assert "google.com" in url


def test_seed_unknown_engine_falls_back_to_default(tmp_path):
    seed_profile_prefs(str(tmp_path), search_engine="nonsense")
    url = _prefs(tmp_path)["default_search_provider_data"]["template_url_data"]["url"]
    assert "duckduckgo.com" in url


def test_all_engines_have_required_fields(tmp_path):
    for key, tmpl in SEARCH_ENGINES.items():
        assert "{searchTerms}" in tmpl["url"], key
        assert tmpl["short_name"], key
        assert tmpl["keyword"], key


def test_seed_skips_existing_profile(tmp_path):
    default = tmp_path / "Default"
    default.mkdir(parents=True)
    existing = default / "Preferences"
    existing.write_text('{"user":"customized"}')
    assert seed_profile_prefs(str(tmp_path)) is False
    assert json.loads(existing.read_text()) == {"user": "customized"}


def test_seed_creates_default_dir(tmp_path):
    target = tmp_path / "newprofile"
    target.mkdir()
    assert seed_profile_prefs(str(target)) is True
    assert (target / "Default" / "Preferences").exists()
