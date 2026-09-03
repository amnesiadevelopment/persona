"""The loader builds its keys from `dataclasses.fields(Profile)` (PS-269).

Every assertion here is bound to a LOADED `Profile`'s field values, never to
"the code calls dataclasses.fields": a test that asserted the call was present
would pass just as happily against an implementation that dropped both of the
migrations the derived build has to keep as explicit post-steps.

The round-trip test drives the property END TO END — save through a real
`ProfileManager`, then construct a SECOND `ProfileManager` against the same
PROFILES_FILE — because the whole failure mode this change removes is invisible
in memory: `to_dict()` saved the field and the hand-enumerated allow-list
dropped it on the next load, so an in-memory assertion could not see it and
every such test stayed green across a restart-only bug.
"""

import dataclasses
import json

import pytest

from src.models.profile import Profile
from src.services.profile.manager import ProfileManager


@pytest.fixture
def profiles_file(tmp_path, monkeypatch):
    """Point the manager at a tmp profiles.json (the test_profile_load.py
    pattern) and hand back the path so a test can write records directly or
    inspect what a save produced."""
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
    return pf


@pytest.fixture
def make_mgr(profiles_file):
    def _make(data):
        profiles_file.write_text(json.dumps(data), encoding="utf-8")
        return ProfileManager()

    return _make


def test_new_dataclass_field_survives_save_and_reload(
    profiles_file, monkeypatch
):
    """A field the loader was never told about round-trips through a REAL
    save and a REAL second manager — no edit to manager.py.

    The extra field is added by subclassing Profile and swapping the name the
    manager module resolves, which is exactly what a future field addition
    looks like from the loader's point of view: `dataclasses.fields()` reports
    one more name and the loader must carry it. Under the hand-enumerated
    allow-list this assertion went red on the SECOND manager while every
    in-memory check stayed green.
    """
    import src.services.profile.manager as mod

    @dataclasses.dataclass
    class ProfileWithNewField(Profile):
        # Stands in for "the next field somebody adds to Profile".
        experimental_flag: str | None = None

    monkeypatch.setattr(mod, "Profile", ProfileWithNewField)

    mgr = ProfileManager()
    assert mgr.add_profile("p1", None, "windows")
    mgr.profiles["p1"].experimental_flag = "kept"
    mgr.save_profiles()

    # The save half was already reflection-based, so the value reaches disk
    # either way; the load half is what this ticket changes.
    assert (
        json.loads(profiles_file.read_text(encoding="utf-8"))["p1"][
            "experimental_flag"
        ]
        == "kept"
    )

    reloaded = ProfileManager()
    assert reloaded.profiles["p1"].experimental_flag == "kept"


def test_legacy_nested_config_os_still_migrates(make_mgr):
    """MIGRATION 1: a pre-os_type record carries the OS at config.os, and
    "config" is not a Profile field — so a reflection-only build cannot see it
    and every such profile would silently become "windows"."""
    mgr = make_mgr(
        {"old": {"name": "old", "proxy": None, "config": {"os": "linux"}}}
    )
    assert mgr.profiles["old"].os_type == "linux"


def test_explicit_os_type_wins_over_legacy_config(make_mgr):
    """The legacy lookup is a FALLBACK, not an override: a record carrying
    both keeps its own os_type."""
    mgr = make_mgr(
        {
            "p": {
                "name": "p",
                "proxy": None,
                "os_type": "macos",
                "config": {"os": "linux"},
            }
        }
    )
    assert mgr.profiles["p"].os_type == "macos"


def test_absent_os_type_and_no_config_defaults_to_windows(make_mgr):
    mgr = make_mgr({"p": {"name": "p", "proxy": None}})
    assert mgr.profiles["p"].os_type == "windows"


def test_retired_camoufox_engine_loads_as_firefox(make_mgr):
    """MIGRATION 2: "camoufox" was the retired Firefox engine. Reflection
    would copy the stored value through verbatim and resurrect a value nothing
    else in the tree understands."""
    mgr = make_mgr(
        {"p": {"name": "p", "proxy": None, "engine": "camoufox"}}
    )
    assert mgr.profiles["p"].engine == "firefox"


def test_other_engine_values_pass_through_and_absent_defaults(make_mgr):
    mgr = make_mgr(
        {
            "ff": {"name": "ff", "proxy": None, "engine": "firefox"},
            "none": {"name": "none", "proxy": None},
        }
    )
    assert mgr.profiles["ff"].engine == "firefox"
    assert mgr.profiles["none"].engine == "chromium"


def test_unknown_keys_are_ignored(make_mgr):
    """A record carrying a key that is not a Profile field loads without
    error, and the junk key never reaches Profile(**...)."""
    mgr = make_mgr(
        {
            "p": {
                "name": "p",
                "proxy": None,
                "totally_made_up_key": "junk",
                "config": {"os": "windows"},
            }
        }
    )
    p = mgr.profiles["p"]
    assert p.name == "p"
    assert not hasattr(p, "totally_made_up_key")


def test_defaults_are_unchanged_by_the_derived_build(make_mgr):
    """The allow-list defaults all matched the dataclass defaults, so omitting
    an absent key and letting the dataclass supply the default is equivalent.
    Pinned here so a later "tidy-up" that reintroduces a default at the loader
    has to break a test."""
    mgr = make_mgr({"p": {"name": "p", "proxy": None}})
    p = mgr.profiles["p"]
    assert p.device_type == "desktop"
    assert p.resolution == "auto"
    assert p.search_engine == "duckduckgo"
    assert p.tags == []
    assert p.notes == ""
    assert p.ai_control is False
    assert p.bookmarks is None
    assert p.bookmark_pool is None
    assert p.certificate is None
    assert p.cookie_import_status is None
    assert p.cert_trust_status is None
    assert p.fingerprint_seed_value is None
    assert p.hardware_generation_value is None
    assert p.last_launch_engine is None
    assert p.last_launch_build is None


def test_tags_default_is_a_fresh_list_per_record(make_mgr):
    """`default_factory=list` per record, exactly like the literal's
    `p_data.get("tags", [])` — not one shared list handed to every profile."""
    mgr = make_mgr(
        {
            "a": {"name": "a", "proxy": None},
            "b": {"name": "b", "proxy": None},
        }
    )
    mgr.profiles["a"].tags.append("x")
    assert mgr.profiles["b"].tags == []
