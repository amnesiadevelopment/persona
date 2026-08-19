import pytest

from src.services.browser.window_entry import _entry_dir, write_window_entry
from src.services.profile.manager import ProfileManager


@pytest.fixture(autouse=True)
def _isolate_entry_dir(tmp_path, monkeypatch):
    """Redirect the desktop-entry dir away from the real host.

    `_entry_dir()` resolves `~/.local/share/applications` at call time and is
    NOT under PROFILES_FILE/DATA_DIR, so the `mgr` fixture below does not cover
    it. update_profile now removes an entry, so without this every test in this
    file would unlink against the developer's real applications dir. Autouse so
    no test can opt out by forgetting.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _entries():
    """Every persona desktop entry currently on disk, as (filename, body)."""
    d = _entry_dir()
    if not d.exists():
        return []
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.desktop"))]


def _mentions(name):
    """Entries whose FILENAME or BODY mentions `name` — the cleartext residue."""
    return [(fn, body) for fn, body in _entries() if name in fn or name in body]


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    import src.core.config as cfg
    import src.services.profile.manager as mod

    pf, dd = tmp_path / "profiles.json", tmp_path / "data"
    for m in (cfg, mod):
        monkeypatch.setattr(m, "PROFILES_FILE", str(pf), raising=False)
        monkeypatch.setattr(m, "DATA_DIR", str(dd), raising=False)
    return ProfileManager()


def _names(mgr):
    return [p.name for p in mgr.list_profiles()]


def test_rename_keeps_list_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b2", "", "windows")

    assert _names(mgr) == ["a", "b2", "c"]


def test_rename_first_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")

    mgr.update_profile("a", "a2", "", "windows")

    assert _names(mgr) == ["a2", "b"]


def test_update_without_rename_keeps_position(mgr):
    mgr.add_profile("a", "", "windows")
    mgr.add_profile("b", "", "windows")
    mgr.add_profile("c", "", "windows")

    mgr.update_profile("b", "b", "1.2.3.4:8080", "linux")

    assert _names(mgr) == ["a", "b", "c"]


def test_failed_dir_rename_leaves_profile_unchanged(mgr, monkeypatch):
    # #15: a data-dir rename can fail (Windows lock while the browser runs). The
    # fields must be mutated AFTER the rename, so a failure leaves the profile
    # fully intact — no half-applied name/proxy/os and no memory/disk divergence.
    import pathlib

    mgr.add_profile("a", "1.1.1.1:1", "windows")

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    ok = mgr.update_profile("a", "a2", "2.2.2.2:2", "linux")

    assert ok is False
    # nothing changed: still keyed by "a", original name/proxy/os intact
    assert _names(mgr) == ["a"]
    p = mgr.profiles.get("a")
    assert p.name == "a"
    assert p.os_type == "windows"
    assert (p.proxy or "") == "1.1.1.1:1"


# --- PS-16: a rename must not strand the old name's desktop entry ----------
#
# The entry lives in ~/.local/share/applications (host-global, OUTSIDE
# PERSONA_HOME), is keyed by profile NAME, and carries `Name=<name>` in
# cleartext. delete_profile/wipe_all_profiles only ever remove the CURRENT
# name, so before this fix a rename left the OLD name on the host permanently —
# unreachable by both delete and the panic wipe. Invariant #0: nothing about a
# deleted profile may survive it.
#
# The launcher writes the entry on every launch (process.py); these tests call
# write_window_entry directly to stand in for that launch.


def test_rename_then_delete_leaves_no_trace_of_the_old_name(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")  # as a launch would
    assert _mentions("alpha")

    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")  # next launch re-creates it under the new name
    mgr.delete_profile("bravo")

    # the whole point: the operator deleted the profile, so the name they were
    # hiding must be gone from the host — not just the name it ended up with.
    assert _mentions("alpha") == []
    assert _mentions("bravo") == []


def test_rename_chain_leaves_no_entry_for_any_previous_name(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")
    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")
    mgr.update_profile("bravo", "charlie", "", "windows")
    write_window_entry("charlie")

    mgr.delete_profile("charlie")

    # every link in the chain, not just the last one
    for name in ("alpha", "bravo", "charlie"):
        assert _mentions(name) == [], f"{name} survived the delete"
    assert _entries() == []


def test_wipe_leaves_no_entry_for_a_renamed_profile(mgr):
    mgr.add_profile("keeper", "", "windows")
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("keeper")
    write_window_entry("alpha")
    mgr.update_profile("alpha", "bravo", "", "windows")
    write_window_entry("bravo")

    mgr.wipe_all_profiles()

    # a panic wipe is the strongest promise the app makes; a pre-rename name
    # surviving it is exactly the residue that made this a leak.
    for name in ("alpha", "bravo", "keeper"):
        assert _mentions(name) == [], f"{name} survived the panic wipe"
    assert _entries() == []


def test_rename_removes_only_the_old_name_not_a_bystander(mgr):
    mgr.add_profile("bystander", "", "windows")
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("bystander")
    write_window_entry("alpha")

    mgr.update_profile("alpha", "bravo", "", "windows")

    assert _mentions("alpha") == []
    # an unrelated profile's entry is untouched — the rename is not a sweep
    assert [fn for fn, _ in _mentions("bystander")] == ["persona-bystander.desktop"]


def test_update_without_rename_keeps_the_entry(mgr):
    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")

    mgr.update_profile("alpha", "alpha", "1.2.3.4:8080", "linux")

    # nothing was renamed, so nothing is stale: removing here would delete the
    # live profile's own entry and blank its taskbar label until the next launch
    assert [fn for fn, _ in _mentions("alpha")] == ["persona-alpha.desktop"]


def test_failed_dir_rename_keeps_the_old_entry(mgr, monkeypatch):
    # Companion to test_failed_dir_rename_leaves_profile_unchanged: a failed
    # rename returns early leaving the profile fully intact, so it must ALSO
    # leave the entry intact — the profile is still called "alpha" and still
    # needs its taskbar entry. This is why the removal sits AFTER the rename.
    import pathlib as _pathlib

    mgr.add_profile("alpha", "", "windows")
    write_window_entry("alpha")

    def boom(self, target):
        raise OSError("dir locked")

    monkeypatch.setattr(_pathlib.Path, "rename", boom)
    ok = mgr.update_profile("alpha", "bravo", "", "linux")

    assert ok is False
    assert [fn for fn, _ in _mentions("alpha")] == ["persona-alpha.desktop"]
