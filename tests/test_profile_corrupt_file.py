import json
import pathlib

import pytest

from src.models.profile import Profile
from src.services.profile.manager import ProfileManager


@pytest.fixture
def paths(tmp_path, monkeypatch):
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
    return pf, tmp_path


def test_invalid_json_is_quarantined_not_lost(paths):
    pf, tmp_path = paths
    raw = "{this is not json"
    pf.write_text(raw, encoding="utf-8")

    mgr = ProfileManager()

    assert mgr.profiles == {}
    backups = list(tmp_path.glob("profiles.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_non_dict_json_is_quarantined(paths):
    pf, tmp_path = paths
    raw = json.dumps(["not", "a", "dict"])
    pf.write_text(raw, encoding="utf-8")

    mgr = ProfileManager()

    assert mgr.profiles == {}
    backups = list(tmp_path.glob("profiles.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw


def test_save_allowed_after_successful_quarantine(paths):
    pf, tmp_path = paths
    pf.write_text("{broken", encoding="utf-8")

    mgr = ProfileManager()
    mgr.add_profile("fresh", "", "windows")

    data = json.loads(pf.read_text(encoding="utf-8"))
    assert "fresh" in data
    # the corrupt original is still preserved alongside
    assert list(tmp_path.glob("profiles.json.corrupt-*"))


def test_save_refused_when_quarantine_fails(paths, monkeypatch):
    pf, _ = paths
    raw = "{broken"
    pf.write_text(raw, encoding="utf-8")

    def _fail_rename(self, target):
        raise OSError("disk says no")

    # NOTE: never monkeypatch.undo() here — it would also revert the path
    # patches from the fixture and point save_profiles at the real
    # ~/.persona/profiles.json.
    monkeypatch.setattr(pathlib.Path, "rename", _fail_rename)
    mgr = ProfileManager()

    mgr.profiles["x"] = Profile(name="x", proxy=None)
    mgr.save_profiles()

    # the unreadable original must survive untouched
    assert pf.read_text(encoding="utf-8") == raw


def test_valid_file_loads_and_saves_normally(paths):
    pf, tmp_path = paths
    pf.write_text(
        json.dumps({"a": {"name": "a", "proxy": None}}), encoding="utf-8"
    )

    mgr = ProfileManager()

    assert set(mgr.profiles) == {"a"}
    assert not list(tmp_path.glob("profiles.json.corrupt-*"))
    mgr.save_profiles()
    assert "a" in json.loads(pf.read_text(encoding="utf-8"))
