import pytest

from src.services.profile.bulk import bulk_create, parse_names
from src.services.profile.manager import ProfileManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


def test_parse_names_splits_newlines():
    assert parse_names("a\nb\nc") == ["a", "b", "c"]


def test_parse_names_splits_commas():
    assert parse_names("a,b,c") == ["a", "b", "c"]


def test_parse_names_mixed_separators():
    assert parse_names("a, b\nc,d\n") == ["a", "b", "c", "d"]


def test_parse_names_strips_whitespace():
    assert parse_names("  a  \n\t b \n") == ["a", "b"]


def test_parse_names_drops_blanks():
    assert parse_names("a\n\n\n,,\nb") == ["a", "b"]


def test_parse_names_dedups_preserving_order():
    assert parse_names("a\nb\na\nc\nb") == ["a", "b", "c"]


def test_parse_names_empty_string():
    assert parse_names("") == []


def test_bulk_create_all_new(mgr):
    result = bulk_create(mgr, ["alpha", "beta", "gamma"])
    assert result["created"] == ["alpha", "beta", "gamma"]
    assert result["skipped"] == []
    assert set(mgr.profiles) == {"alpha", "beta", "gamma"}


def test_bulk_create_skips_existing(mgr):
    mgr.add_profile("alpha", "", "windows")
    result = bulk_create(mgr, ["alpha", "beta"])
    assert result["created"] == ["beta"]
    assert result["skipped"] == ["alpha"]


def test_bulk_create_skips_duplicates_within_list(mgr):
    result = bulk_create(mgr, ["alpha", "alpha", "beta"])
    assert result["created"] == ["alpha", "beta"]
    assert result["skipped"] == []
    assert set(mgr.profiles) == {"alpha", "beta"}


def test_bulk_create_skips_invalid_names(mgr):
    result = bulk_create(mgr, ["good", "bad/name", "also:bad"])
    assert result["created"] == ["good"]
    assert set(result["skipped"]) == {"bad/name", "also:bad"}


def test_bulk_create_skips_blank_names(mgr):
    result = bulk_create(mgr, ["good", "", "   "])
    assert result["created"] == ["good"]
    assert result["skipped"] == []


def test_bulk_create_passes_attributes(mgr):
    bulk_create(
        mgr, ["x"], proxy="", os_type="macos",
        search_engine="brave", tags=["work"],
    )
    p = mgr.profiles["x"]
    assert p.os_type == "macos"
    assert p.search_engine == "brave"
    assert p.tags == ["work"]


def test_bulk_create_empty_list(mgr):
    result = bulk_create(mgr, [])
    assert result == {"created": [], "skipped": []}
