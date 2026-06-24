from src.services.profile.filter import filter_profiles
from src.models.profile import Profile


def _p(name, proxy=None, os_type="windows"):
    return Profile(name=name, proxy=proxy, os_type=os_type)


PROFILES = [
    _p("amazon-us", proxy="us-proxy", os_type="windows"),
    _p("amazon-eu", proxy="de-proxy", os_type="macos"),
    _p("google-acc", proxy=None, os_type="linux"),
]


def test_empty_query_returns_all():
    assert filter_profiles(PROFILES, "") == PROFILES
    assert filter_profiles(PROFILES, "   ") == PROFILES


def test_matches_name_substring():
    out = filter_profiles(PROFILES, "amazon")
    assert [p.name for p in out] == ["amazon-us", "amazon-eu"]


def test_case_insensitive():
    out = filter_profiles(PROFILES, "GOOGLE")
    assert [p.name for p in out] == ["google-acc"]


def test_matches_proxy_name():
    out = filter_profiles(PROFILES, "de-proxy")
    assert [p.name for p in out] == ["amazon-eu"]


def test_matches_os_type():
    out = filter_profiles(PROFILES, "linux")
    assert [p.name for p in out] == ["google-acc"]


def test_no_match_returns_empty():
    assert filter_profiles(PROFILES, "zzz") == []


def test_query_is_trimmed():
    out = filter_profiles(PROFILES, "  amazon-us  ")
    assert [p.name for p in out] == ["amazon-us"]
