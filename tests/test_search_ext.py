import json

import pytest

from src.services.browser.profile_seed import SEARCH_ENGINES
from src.services.browser.search_ext import build_search_extension


def _manifest(path):
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def test_builds_search_provider_override_for_chosen_engine(tmp_path):
    build_search_extension("duckduckgo", str(tmp_path))
    sp = _manifest(tmp_path)["chrome_settings_overrides"]["search_provider"]
    assert sp["is_default"] is True
    assert "duckduckgo.com" in sp["search_url"]
    assert "{searchTerms}" in sp["search_url"]
    assert sp["name"] == "DuckDuckGo"


def test_honours_google(tmp_path):
    build_search_extension("google", str(tmp_path))
    sp = _manifest(tmp_path)["chrome_settings_overrides"]["search_provider"]
    assert "google.com" in sp["search_url"]
    assert sp["name"] == "Google"


def test_unknown_engine_falls_back_to_default(tmp_path):
    build_search_extension("nonsense", str(tmp_path))
    sp = _manifest(tmp_path)["chrome_settings_overrides"]["search_provider"]
    assert "duckduckgo.com" in sp["search_url"]


@pytest.mark.parametrize("engine", list(SEARCH_ENGINES))
def test_every_engine_produces_a_valid_manifest(engine, tmp_path):
    build_search_extension(engine, str(tmp_path))
    manifest = _manifest(tmp_path)
    assert manifest["manifest_version"] == 3
    sp = manifest["chrome_settings_overrides"]["search_provider"]
    for key in ("name", "keyword", "search_url", "favicon_url", "encoding"):
        assert sp[key], key
    assert sp["encoding"] == "UTF-8"
